import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from trading_assistant.application.balance import portfolio_balance
from trading_assistant.application.currency import FxRateProvider, pln_to_usd
from trading_assistant.application.dashboard import (
    AllocationRow,
    DashboardSnapshot,
    OpportunityRow,
    PortfolioRow,
)
from trading_assistant.application.day_history import DayChangeHistory
from trading_assistant.application.history import ChangeHistoryRepository
from trading_assistant.application.live_pnl import estimate_pnl
from trading_assistant.application.market import (
    MarketDataProvider,
    MarketSessionProvider,
    SymbolResolver,
)
from trading_assistant.application.news import PortfolioNewsSource
from trading_assistant.application.recommendations import (
    OpportunityHistoryService,
    RecommendationReader,
)
from trading_assistant.application.risk import assess_portfolio
from trading_assistant.domain.fx import FxUnavailableError
from trading_assistant.domain.market import MarketDataError
from trading_assistant.domain.portfolio import (
    AccountAliasInput,
    AccountValueInput,
    BrokerReport,
    CashDepositInput,
    ImportResult,
    PortfolioState,
)
from trading_assistant.domain.risk import RiskSettings


class ReportReader(Protocol):
    def read(self, path: Path) -> BrokerReport: ...


class PortfolioRepository(Protocol):
    def resolve_account(self, reference: str) -> str: ...

    def set_account_alias(self, reference: str, alias: AccountAliasInput) -> None: ...

    def import_report(
        self, report: BrokerReport, account_value: AccountValueInput | None = None
    ) -> ImportResult: ...

    def set_account_value(self, account_number: str, balance: AccountValueInput) -> None: ...

    def add_money(self, account_number: str, deposit: CashDepositInput) -> AccountValueInput: ...

    def portfolio(self) -> PortfolioState: ...


class ImportService:
    def __init__(self, reader: ReportReader, repository: PortfolioRepository):
        self.reader = reader
        self.repository = repository

    def import_file(
        self, path: Path, account_value: AccountValueInput | None = None
    ) -> ImportResult:
        return self.repository.import_report(self.reader.read(path), account_value)


class PortfolioDashboardService:
    def __init__(
        self,
        repository: PortfolioRepository,
        fx_provider: FxRateProvider | None = None,
        market_provider: MarketDataProvider | None = None,
        european_provider: MarketDataProvider | None = None,
        news_service: PortfolioNewsSource | None = None,
        history_repository: ChangeHistoryRepository | None = None,
        recommendation_reader: RecommendationReader | None = None,
        risk_settings: RiskSettings | None = None,
        sentiment_store=None,
        session_provider: MarketSessionProvider | None = None,
    ):
        self.repository = repository
        self.risk_settings = risk_settings or RiskSettings()
        self.sentiment_store = sentiment_store
        self.session_provider = session_provider
        self.fx_provider = fx_provider
        self.market_provider = market_provider
        self.european_provider = european_provider
        self.news_service = news_service
        self.history_repository = history_repository
        self.recommendation_reader = recommendation_reader
        self._snapshot_lock = asyncio.Lock()
        self._day_history = DayChangeHistory()
        self._percent_history = DayChangeHistory()

    async def snapshot(self) -> DashboardSnapshot:
        async with self._snapshot_lock:
            return await self._snapshot()

    async def saved_analysis(self, symbol: str):
        canonical = SymbolResolver().analysis_symbol(symbol)
        if self.recommendation_reader:
            records = await asyncio.to_thread(self.recommendation_reader.latest)
            return records.get(canonical)
        return None

    async def sentiment_snapshot(self) -> DashboardSnapshot:
        snapshot = await self.snapshot()
        if self.news_service and hasattr(self.news_service, "ready"):
            await self.news_service.ready()
            snapshot = await self.snapshot()
        return snapshot

    async def initial_snapshot(self) -> DashboardSnapshot:
        """Local data first, without waiting for external providers."""
        return await self._snapshot(include_network=False)

    async def _snapshot(self, *, include_network: bool = True) -> DashboardSnapshot:
        state = await asyncio.to_thread(self.repository.portfolio)
        balance = portfolio_balance(state)
        alerts = list(state.warnings)
        if balance.warning:
            alerts.append(balance.warning)
        risk = assess_portfolio(state, self.risk_settings)
        alerts.extend("Report risk • " + warning for warning in risk.warnings)
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
            if state.account_value is None:
                alerts.append(
                    "Total account value is unverified: not all accounts have a confirmed balance. "
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
        allocation = []
        allocation_status = "Report valuations incomplete; allocation unavailable."
        if balance.holdings is not None and (balance.total is None or balance.reserve is not None):
            values = {}
            resolver = SymbolResolver()
            for holding in state.holdings:
                label = (
                    "ETF / ETC"
                    if holding.asset_type.upper() in {"ETF", "ETC"}
                    else {"US": "US stocks", "PL": "Polish stocks", "EU": "EU stocks"}.get(
                        resolver.listing_market(holding.symbol), "Other"
                    )
                )
                values[label] = values.get(label, Decimal(0)) + holding.report_value
            if balance.reserve is not None:
                values["Cash reserve"] = balance.reserve
            total = sum(values.values(), Decimal(0))
            if total > 0:
                allocation = [
                    AllocationRow(label=label, weight_percent=value / total * 100)
                    for label, value in values.items()
                    if value > 0
                ]
                allocation_status = (
                    "Confirmed account total • report-derived reserve"
                    if state.account_value is not None
                    else "Report holdings only • account total unconfirmed"
                )
                allocation_status += " • stock groups use listing markets, not underlying exposure"
            else:
                allocation_status = "No positive portfolio value to allocate."
        analysis_dates = []
        opportunities = []
        if self.recommendation_reader:
            if hasattr(self.recommendation_reader, "opportunities"):
                saved_opportunities = await OpportunityHistoryService(
                    self.recommendation_reader
                ).records()
                analysis_dates.extend(
                    saved.recommendation.analyzed_at for saved in saved_opportunities
                )
                for saved in saved_opportunities:
                    result = saved.recommendation
                    owned = False
                    resolver = SymbolResolver()
                    for holding in state.holdings:
                        try:
                            owned = (
                                resolver.candidate_matches_holding(
                                    saved.request.candidate, holding.symbol
                                )
                                if saved.request.candidate
                                else resolver.analysis_symbol(holding.symbol) == result.symbol
                            )
                        except MarketDataError:
                            continue
                        if owned:
                            break
                    if not owned:
                        opportunities.append(
                            OpportunityRow(
                                symbol=result.symbol,
                                action=result.action.value,
                                engine_rating=result.engine_rating,
                                entry=result.entry,
                                entry_currency=result.entry_currency,
                                stop_loss=result.stop_loss,
                                stop_loss_currency=result.stop_loss_currency,
                                confidence=result.confidence,
                            )
                        )
            saved_results = await asyncio.to_thread(self.recommendation_reader.latest)
            analysis_dates.extend(
                saved.recommendation.analyzed_at for saved in saved_results.values()
            )
            for row in positions:
                try:
                    canonical = SymbolResolver().analysis_symbol(row.symbol)
                except MarketDataError:
                    continue
                saved = saved_results.get(canonical)
                if saved:
                    result = saved.recommendation
                    row.recommendation = result.action.value
                    row.recommendation_as_of = result.analyzed_at
                    alerts.append(
                        f"{row.symbol}: saved AI {result.action.value} • "
                        f"{result.analyzed_at.isoformat()} • {saved.request.strategy.value} • "
                        f"{saved.request.time_horizon}."
                    )
                    analyzed_quantity = sum(
                        (
                            p.quantity
                            for p in saved.request.portfolio.positions
                            if p.canonical_symbol == canonical
                        ),
                        Decimal(0),
                    )
                    if analyzed_quantity != row.quantity:
                        alerts.append(
                            f"{row.symbol}: holding quantity changed since AI analysis; reanalyze."
                        )
        covered = 0
        daily_changes = []
        session_dates = set()
        symbol_sessions = {}
        prior_session = False
        daily_symbols = []
        previous_values = []
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
                row.quote_as_of = quote.as_of
                row.quote_source = quote.source
                row.quote_price = quote.price
                row.quote_currency = quote.currency
                row.quote_value = quote.price * row.quantity
                if quote.currency == "USD":
                    row.quote_price_usd = quote.price
                    row.quote_value_usd = row.quote_value
                elif quote.currency == "PLN":
                    row.quote_price_usd = pln_to_usd(quote.price, rate)
                    row.quote_value_usd = pln_to_usd(row.quote_value, rate)
                age = (datetime.now(UTC) - quote.as_of).total_seconds()
                row.quote_status = f"{quote.source} {quote.as_of.isoformat()}" + (
                    " • older than 60s / market may be closed" if age > 60 else ""
                )
                covered += 1
                now = datetime.now(UTC)
                session = (
                    await asyncio.to_thread(self.session_provider.state, row.symbol, now)
                    if self.session_provider
                    else None
                )
                if session is not None:
                    quote_date = quote.as_of.astimezone(ZoneInfo(session.timezone)).date()
                    # The calendar session label is a trading date, independent of UTC day rollover.
                    # For the supported exchanges the session label and local closing date agree.
                    expected_date = session.session_date
                    if (
                        quote_date != expected_date
                        or quote.as_of > now
                        or quote.as_of < session.open_at
                    ):
                        continue
                    if session.closed and not (
                        session.close_at - timedelta(minutes=15)
                        <= quote.as_of
                        <= session.close_at + timedelta(minutes=15)
                    ):
                        row.quote_status += " • closing quote unavailable"
                        continue
                    session_dates.add(expected_date.isoformat())
                    symbol_sessions[row.symbol] = expected_date.isoformat()
                    prior_session = prior_session or expected_date != now.date()
                    if session.closed:
                        row.quote_status += f" • completed session {expected_date}"
                elif quote.as_of.date() != now.date():
                    continue
                else:
                    symbol_sessions[row.symbol] = quote.as_of.date().isoformat()
                row.quote_daily_change = (quote.price - quote.previous_close) * row.quantity
                row.quote_daily_change_percent = (
                    (quote.price - quote.previous_close) / quote.previous_close * Decimal(100)
                )
                currency = quote.currency
                change = row.quote_daily_change
                if currency in {"GBp", "GBX"}:
                    currency = "GBP"
                    change /= Decimal(100)
                if currency == "PLN":
                    daily_changes.append(change)
                    daily_symbols.append(row.symbol)
                    previous_values.append(quote.previous_close * row.quantity)
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
                        daily_symbols.append(row.symbol)
                        previous_value = quote.previous_close * row.quantity
                        if quote.currency in {"GBp", "GBX"}:
                            previous_value /= Decimal(100)
                        previous_values.append(previous_value * fx.rate)
            except MarketDataError as exc:
                row.quote_status = str(exc)
        for holding, row in zip(state.holdings, positions, strict=True):
            if row.quote_value is None:
                continue
            currency, value = row.quote_currency, row.quote_value
            if currency in {"GBp", "GBX"}:
                currency, value = "GBP", value / Decimal(100)
            if currency == "PLN":
                row.quote_value_pln = value
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
                    row.quote_value_pln = value * fx.rate
            if row.quote_value_pln is not None:
                row.estimated_pnl_pln, row.estimated_pnl_percent = estimate_pnl(
                    holding.report_value,
                    holding.report_pnl_amount,
                    holding.report_pnl_percent,
                    row.quote_value_pln,
                )
        estimated_values = [
            row.estimated_pnl_pln for row in positions if row.estimated_pnl_pln is not None
        ]
        estimated_unrealized = sum(estimated_values, Decimal(0)) if estimated_values else None
        market_status = (
            (
                f"Quotes: {covered}/{len(positions)} holdings • US 5s / Europe 60s • "
                "Report P/L is historical; estimated P/L uses quote values and reference FX, "
                "assuming unchanged holdings/costs since the report. "
                "Day change % uses the current session or latest completed session "
                "outside trading hours."
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
        today_pnl = sum(daily_changes, Decimal(0)) if daily_changes else None
        previous_total = sum(previous_values, Decimal(0))
        today_percent = today_pnl / previous_total * Decimal(100) if previous_total else None
        history = []
        percent_history = []
        if self.history_repository:
            history, percent_history = await asyncio.to_thread(
                self.history_repository.intraday, datetime.now(UTC)
            )
        if include_network:
            context = (
                tuple((row.symbol, row.quantity) for row in positions),
                tuple(daily_symbols),
                tuple(sorted((code, fx.rate) for code, fx in conversion_rates.items() if fx)),
                tuple((symbol, symbol_sessions.get(symbol)) for symbol in daily_symbols),
            )
            now = datetime.now(UTC)
            if self.history_repository and today_pnl is not None:
                history, percent_history = await asyncio.to_thread(
                    self.history_repository.record,
                    now,
                    today_pnl,
                    today_percent,
                    context,
                    len(daily_changes),
                    len(positions),
                    update_daily=not prior_session,
                )
            elif not self.history_repository:
                history = self._day_history.record(now, today_pnl, context)
                percent_history = self._percent_history.record(now, today_percent, context)
        daily_history = (
            await asyncio.to_thread(self.history_repository.daily, datetime.now(UTC))
            if self.history_repository
            else []
        )
        coverage = f"{len(daily_changes)}/{len(positions)} holdings"
        estimate_label = "Partial estimate" if len(daily_changes) < len(positions) else "Estimated"
        today_status = (
            (
                f"{estimate_label} • {coverage} • current holdings × change since previous close; "
                "excludes trades and FX P/L."
                if today_pnl is not None
                else f"Daily estimate unavailable: today's quotes and FX cover "
                f"{len(daily_changes)}/{len(positions)} holdings."
            )
            if positions
            else None
        )
        if session_dates and today_pnl is not None:
            today_status += " Sessions: " + ", ".join(sorted(session_dates)) + "."
            if prior_session:
                today_status += " Includes latest completed sessions; not current-calendar-day P/L."
        if include_network and positions:
            alerts.append(today_status)
            for fx in conversion_rates.values():
                if fx:
                    alerts.append(
                        f"Daily estimate FX: 1 {fx.base} = {fx.rate} PLN • NBP {fx.effective_date}"
                        + (" • stale cached rate" if fx.stale else "")
                    )
        news, news_status = (
            [],
            "Loading news…" if self.news_service else "News provider not configured.",
        )
        if include_network and self.news_service:
            news, news_status = self.news_service.snapshot([row.symbol for row in positions])
        sentiment_status = "Not assessed • M to assess markets and risk"
        if self.sentiment_store:
            saved_sentiment = await asyncio.to_thread(self.sentiment_store.latest)
            if saved_sentiment:
                age = datetime.now(UTC) - saved_sentiment.analyzed_at
                sentiment_status = " / ".join(
                    f"{market.market}: {market.outlook}"
                    for market in saved_sentiment.result.markets
                )
                sentiment_status += f" • {saved_sentiment.analyzed_at:%Y-%m-%d %H:%M} UTC"
                sentiment_status += " • portfolio-linked sample"
                if age.total_seconds() > 86400:
                    sentiment_status += " • stale"
        return DashboardSnapshot(
            market_sentiment=sentiment_status,
            news=news,
            news_status=news_status,
            currency="PLN",
            portfolio_risk="Report-based • " + risk.status,
            risk_summary=risk,
            cash=balance.reserve,
            portfolio_value=balance.total,
            today_pnl=today_pnl,
            today_pnl_label="Session P/L" if session_dates else "Today's P/L",
            today_pnl_status=today_status,
            today_pnl_covered=len(daily_changes),
            today_pnl_total=len(positions),
            day_change_history=history,
            today_change_percent=today_percent,
            day_change_percent_history=percent_history,
            daily_change_history=daily_history,
            cash_as_of=", ".join(sorted({d.isoformat() for d in state.account_value_dates}))
            if balance.reserve is not None
            else None,
            holdings_value=holdings_value,
            holdings_value_usd=pln_to_usd(holdings_value, rate),
            unrealized_pnl=unrealized_pnl,
            unrealized_pnl_usd=pln_to_usd(unrealized_pnl, rate),
            estimated_unrealized_pnl=estimated_unrealized,
            estimated_unrealized_pnl_usd=pln_to_usd(estimated_unrealized, rate),
            estimated_pnl_covered=len(estimated_values),
            estimated_pnl_total=len(positions),
            fx_status=fx_status,
            market_status=market_status,
            valuation_as_of=", ".join(sorted({d.isoformat() for d in state.report_dates})) or None,
            # Daily performance and full portfolio total remain unknown.
            positions=positions,
            opportunities=opportunities,
            allocation=allocation,
            allocation_status=allocation_status,
            analyzed_positions=sum(row.recommendation is not None for row in positions),
            saved_opportunity_count=len(opportunities),
            last_analysis=max(analysis_dates).isoformat()
            if analysis_dates
            else "No saved analyses",
            alerts=alerts,
        )
