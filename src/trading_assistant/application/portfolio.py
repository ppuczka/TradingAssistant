import asyncio
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from trading_assistant.application.currency import FxRateProvider, pln_to_usd
from trading_assistant.application.dashboard import DashboardSnapshot, PortfolioRow
from trading_assistant.domain.fx import FxUnavailableError
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
    def __init__(self, repository: PortfolioRepository, fx_provider: FxRateProvider | None = None):
        self.repository = repository
        self.fx_provider = fx_provider

    async def snapshot(self) -> DashboardSnapshot:
        state = await asyncio.to_thread(self.repository.portfolio)
        alerts = list(state.warnings)
        rate = None
        fx_status = None
        if self.fx_provider and state.report_dates:
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
        return DashboardSnapshot(
            currency="PLN",
            cash=state.cash_balance,
            cash_as_of=", ".join(sorted({d.isoformat() for d in state.cash_balance_dates}))
            if state.cash_balance is not None
            else None,
            holdings_value=holdings_value,
            holdings_value_usd=pln_to_usd(holdings_value, rate),
            unrealized_pnl=unrealized_pnl,
            unrealized_pnl_usd=pln_to_usd(unrealized_pnl, rate),
            fx_status=fx_status,
            valuation_as_of=", ".join(sorted({d.isoformat() for d in state.report_dates})) or None,
            # Daily performance and full portfolio total remain unknown.
            positions=[
                PortfolioRow(
                    symbol=h.symbol,
                    quantity=h.quantity,
                    market_value=h.report_value,
                    market_value_usd=pln_to_usd(h.report_value, rate),
                    currency="PLN",
                    pnl_percent=h.report_pnl_percent,
                )
                for h in state.holdings
            ],
            alerts=alerts,
        )
