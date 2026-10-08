import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from trading_assistant.application.currency import FxRateProvider, pln_to_usd
from trading_assistant.application.dashboard import DashboardSnapshot, PortfolioRow
from trading_assistant.application.market import MarketDataProvider, SymbolResolver
from trading_assistant.domain.fx import FxUnavailableError
from trading_assistant.domain.market import MarketDataError
from trading_assistant.domain.portfolio import (
    BrokerReport,
    CashBalanceInput,
    ImportResult,
    PortfolioState,
)


class ReportReader(Protocol):
    def read(self, path: Path) -> BrokerReport: ...


class PortfolioRepository(Protocol):
    def import_report(
        self, report: BrokerReport, cash_balance: CashBalanceInput | None = None
    ) -> ImportResult: ...

    def set_cash_balance(self, account_number: str, balance: CashBalanceInput) -> None: ...

    def portfolio(self) -> PortfolioState: ...


class ImportService:
    def __init__(self, reader: ReportReader, repository: PortfolioRepository):
        self.reader = reader
        self.repository = repository

    def import_file(self, path: Path, cash_balance: CashBalanceInput | None = None) -> ImportResult:
        return self.repository.import_report(self.reader.read(path), cash_balance)


class PortfolioDashboardService:
    def __init__(
        self,
        repository: PortfolioRepository,
        fx_provider: FxRateProvider | None = None,
        market_provider: MarketDataProvider | None = None,
        european_provider: MarketDataProvider | None = None,
    ):
        self.repository = repository
        self.fx_provider = fx_provider
        self.market_provider = market_provider
        self.european_provider = european_provider
        self._snapshot_lock = asyncio.Lock()

    async def snapshot(self) -> DashboardSnapshot:
        async with self._snapshot_lock:
            return await self._snapshot()

    async def initial_snapshot(self) -> DashboardSnapshot:
        """Local data first, without waiting for external providers."""
        return await self._snapshot(include_network=False)

    async def _snapshot(self, *, include_network: bool = True) -> DashboardSnapshot:
        state = await asyncio.to_thread(self.repository.portfolio)
        alerts = list(state.warnings)
        rate = None
        fx_status = None
        if include_network and self.fx_provider and state.report_dates:
            try:
                rate = await self.fx_provider.usd_pln()
                fx_status = (
                    f"NBP reference FX: 1 USD = {rate.rate} PLN • Published {rate.effective_date}"
                    f"{' • Stale cached rate (refresh failed)' if rate.stale else ''}"
                )
            except FxUnavailableError:
                fx_status = "USD conversion unavailable: NBP could not be reached or validated."
            alerts.append(fx_status)
        holdings_value = None
        unrealized_pnl = None
        if state.report_dates and all(h.report_value is not None for h in state.holdings):
            holdings_value = sum((h.report_value for h in state.holdings), Decimal(0))
        if state.report_dates and all(h.report_pnl_amount is not None for h in state.holdings):
            unrealized_pnl = sum((h.report_pnl_amount for h in state.holdings), Decimal(0))
        if state.report_dates:
            dates = ", ".join(sorted({d.isoformat() for d in state.report_dates}))
            alerts.append(
                f"Values and P/L are broker-report snapshots as of {dates}; not live quotes."
            )
            if state.cash_balance is None:
                alerts.append(
                    "Cash balance is unverified: not all accounts have a confirmed balance. "
                    f"Imported net cash movements: {state.cash_movement:,.2f} PLN."
                )
        positions = [
            PortfolioRow(
                symbol=h.symbol,
                name=h.name,
                quantity=h.quantity,
                market_value=h.report_value,
                market_value_usd=pln_to_usd(h.report_value, rate),
                currency="PLN",
                pnl_percent=h.report_pnl_percent,
            )
            for h in state.holdings
        ]
        covered = 0
        daily_changes = []
        conversion_rates = {"USD": rate} if rate else {}
        resolver = SymbolResolver()
        for row in positions:
            if not include_network:
                row.quote_status = "Loading quotes…"
                continue
            try:
                is_us = row.symbol.upper().endswith(".US")
                provider = self.market_provider if is_us else self.european_provider
                if provider is None:
                    raise MarketDataError("Market data key or mapping unavailable")
                symbol = (
                    resolver.finnhub_us(row.symbol)
                    if is_us
                    else resolver.twelve_data_europe(row.symbol)
                )
                quote = await provider.quote(symbol)
                if quote.name:
                    row.name = quote.name
                if is_us and quote.currency != "USD":
                    raise MarketDataError("Unexpected quote currency")
                row.quote_price = quote.price
                row.quote_currency = quote.currency
                row.quote_value = quote.price * row.quantity
                row.quote_daily_change = (quote.price - quote.previous_close) * row.quantity
                if quote.currency == "USD":
                    row.quote_price_usd = quote.price
                    row.quote_value_usd = row.quote_value
                elif quote.currency == "PLN":
                    row.quote_price_usd = pln_to_usd(quote.price, rate)
                    row.quote_value_usd = pln_to_usd(row.quote_value, rate)
                row.quote_daily_change_percent = (
                    (quote.price - quote.previous_close) / quote.previous_close * Decimal(100)
                )
                age = (datetime.now(UTC) - quote.as_of).total_seconds()
                row.quote_status = f"{quote.source} {quote.as_of.isoformat()}" + (
                    " • older than 60s / market may be closed" if age > 60 else ""
                )
                covered += 1
                if quote.as_of.date() != datetime.now(UTC).date():
                    continue
                currency = quote.currency
                change = row.quote_daily_change
                if currency in {"GBp", "GBX"}:
                    currency = "GBP"
                    change /= Decimal(100)
                if currency == "PLN":
                    daily_changes.append(change)
                else:
                    if currency not in conversion_rates and self.fx_provider:
                        converter = getattr(self.fx_provider, "currency_pln", None)
                        if converter:
                            try:
                                conversion_rates[currency] = await converter(currency)
                            except FxUnavailableError:
                                conversion_rates[currency] = None
                    fx = conversion_rates.get(currency)
                    if fx:
                        daily_changes.append(change * fx.rate)
            except MarketDataError as exc:
                row.quote_status = str(exc)
        market_status = (
            (
                f"Quotes: {covered}/{len(positions)} holdings • US 5s / Europe 60s • "
                "Report totals and P/L remain report snapshots. "
                "Day change % compares quote price with previous close."
            )
            if positions
            else None
        )
        if not include_network and positions:
            market_status = "Loading market quotes and currency conversion…"
        if include_network:
            alerts.extend(
                f"{row.symbol}: {row.quote_status}"
                for row in positions
                if row.quote_price is None or "older than" in row.quote_status
            )
        today_pnl = (
            sum(daily_changes, Decimal(0))
            if positions and len(daily_changes) == len(positions)
            else None
        )
        today_status = (
            (
                "Estimated • current holdings × change since previous close; "
                "excludes trades and FX P/L."
                if today_pnl is not None
                else f"Daily estimate unavailable: today's quotes and FX cover "
                f"{len(daily_changes)}/{len(positions)} holdings."
            )
            if positions
            else None
        )
        if include_network and positions:
            alerts.append(today_status)
            for fx in conversion_rates.values():
                if fx:
                    alerts.append(
                        f"Daily estimate FX: 1 {fx.base} = {fx.rate} PLN • NBP {fx.effective_date}"
                        + (" • stale cached rate" if fx.stale else "")
                    )
        return DashboardSnapshot(
            currency="PLN",
            cash=state.cash_balance,
            today_pnl=today_pnl,
            today_pnl_status=today_status,
            cash_as_of=", ".join(sorted({d.isoformat() for d in state.cash_balance_dates}))
            if state.cash_balance is not None
            else None,
            holdings_value=holdings_value,
            holdings_value_usd=pln_to_usd(holdings_value, rate),
            unrealized_pnl=unrealized_pnl,
            unrealized_pnl_usd=pln_to_usd(unrealized_pnl, rate),
            fx_status=fx_status,
            market_status=market_status,
            valuation_as_of=", ".join(sorted({d.isoformat() for d in state.report_dates})) or None,
            # Daily performance and full portfolio total remain unknown.
            positions=positions,
            alerts=alerts,
        )
