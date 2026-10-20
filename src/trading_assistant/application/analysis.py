"""Analysis orchestration without TradingAgents dependencies."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Protocol

from trading_assistant.application.balance import portfolio_balance
from trading_assistant.application.market import SymbolResolver
from trading_assistant.application.portfolio import PortfolioRepository
from trading_assistant.application.progress import ProgressCallback
from trading_assistant.application.recommendations import RecommendationReader
from trading_assistant.domain.analysis import (
    AnalysisFailure,
    AnalysisRequest,
    PortfolioContext,
    PositionContext,
    Recommendation,
    Strategy,
)
from trading_assistant.domain.candidates import StockCandidate
from trading_assistant.domain.instrument import AnalysisInstrument
from trading_assistant.domain.market import MarketDataError
from trading_assistant.domain.review import ReviewPlan


class AnalysisEngine(Protocol):
    async def analyze(self, request: AnalysisRequest) -> Recommendation: ...


class RecommendationStore(Protocol):
    def save(self, request: AnalysisRequest, result: Recommendation) -> None: ...


class InstrumentProvider(Protocol):
    async def resolve(self, symbol: str) -> AnalysisInstrument: ...


class AnalysisService:
    def __init__(
        self,
        repository: PortfolioRepository,
        engine: AnalysisEngine,
        store: RecommendationStore | None = None,
        reader: RecommendationReader | None = None,
        instrument_provider: InstrumentProvider | None = None,
    ):
        self.repository, self.engine, self.store = repository, engine, store
        self.reader = reader
        self.instrument_provider = instrument_provider

    def saved(self, symbol: str):
        canonical = SymbolResolver().analysis_symbol(symbol)
        return self.reader.latest().get(canonical) if self.reader else None

    def request(self, symbol: str, strategy: Strategy, horizon: str) -> AnalysisRequest:
        return self._request(symbol, strategy, horizon, self.repository.portfolio())

    async def prepare_request(
        self, symbol: str, strategy: Strategy, horizon: str
    ) -> AnalysisRequest:
        if self.instrument_provider is None:
            raise AnalysisFailure("Symbol lookup is not configured.")
        try:
            canonical = SymbolResolver().lookup_symbol(symbol)
        except MarketDataError as exc:
            raise AnalysisFailure(str(exc)) from None
        instrument = await self.instrument_provider.resolve(canonical)
        if instrument.symbol != canonical:
            raise AnalysisFailure("Lookup returned a different instrument.")
        state = await asyncio.to_thread(self.repository.portfolio)
        return self._request(canonical, strategy, horizon, state, instrument=instrument)

    def review_plan(self, strategy: Strategy, horizon: str, market: str = "US") -> ReviewPlan:
        if market not in {"US", "EU", "PL"}:
            raise AnalysisFailure("Review market must be US, EU (excluding Poland), or PL.")
        state = self.repository.portfolio()
        requests, skipped = [], []
        seen = set()
        for holding in state.holdings:
            suffixes = {"US": (".US",), "EU": (".DE", ".UK"), "PL": (".PL",)}
            if not holding.symbol.upper().endswith(suffixes[market]):
                skipped.append(holding.symbol)
                continue
            try:
                req = self._request(holding.symbol, strategy, horizon, state)
            except AnalysisFailure:
                skipped.append(holding.symbol)
                continue
            if req.symbol not in seen:
                requests.append(req)
                seen.add(req.symbol)
        return ReviewPlan(requests=requests, skipped=skipped)

    def opportunity_plan(
        self, candidates: list[StockCandidate], strategy: Strategy, horizon: str, limit: int = 3
    ) -> ReviewPlan:
        if not 1 <= limit <= 10:
            raise AnalysisFailure("Analyze between 1 and 10 candidates per batch.")
        state = self.repository.portfolio()
        requests, skipped, seen = [], [], set()
        now = datetime.now(UTC)
        for candidate in candidates:
            if candidate.symbol in seen:
                continue
            seen.add(candidate.symbol)
            if not now - timedelta(hours=24) <= candidate.fetched_at <= now:
                skipped.append(candidate.symbol)
                continue
            try:
                request = self._request(candidate.symbol, strategy, horizon, state, candidate)
            except (AnalysisFailure, ValueError):
                skipped.append(candidate.symbol)
                continue
            if request.owned:
                skipped.append(candidate.symbol)
                continue
            requests.append(request)
            if len(requests) == limit:
                break
        return ReviewPlan(requests=requests, skipped=skipped)

    def _request(
        self,
        symbol: str,
        strategy: Strategy,
        horizon: str,
        state,
        candidate: StockCandidate | None = None,
        instrument: AnalysisInstrument | None = None,
    ) -> AnalysisRequest:
        resolver = SymbolResolver()
        try:
            canonical = (
                resolver.verified_instrument_symbol(instrument)
                if instrument
                else (
                    resolver.candidate_symbol(candidate)
                    if candidate
                    else resolver.analysis_symbol(symbol)
                )
            )
        except MarketDataError as exc:
            raise AnalysisFailure(str(exc)) from None
        positions = []
        for holding in state.holdings:
            try:
                mapped = resolver.analysis_symbol(holding.symbol)
            except MarketDataError:
                mapped = None
                if candidate is not None:
                    try:
                        if resolver.candidate_matches_holding(candidate, holding.symbol):
                            mapped = canonical
                    except MarketDataError:
                        pass
                if instrument is not None:
                    try:
                        if resolver.instrument_matches_holding(instrument, holding.symbol):
                            mapped = canonical
                    except MarketDataError:
                        pass
            positions.append(
                PositionContext(
                    broker_symbol=holding.symbol,
                    canonical_symbol=mapped,
                    quantity=holding.quantity,
                    report_value_pln=holding.report_value,
                    report_pnl_pln=holding.report_pnl_amount,
                    report_pnl_percent=holding.report_pnl_percent,
                    asset_type=holding.asset_type,
                    name=holding.name,
                )
            )
        target = next((p for p in positions if p.canonical_symbol == canonical), None)
        if (
            candidate is None
            and instrument is None
            and target is None
            and canonical.endswith((".DE", ".L", ".WA"))
        ):
            raise AnalysisFailure(
                "Non-US analysis requires an imported holding to identify its instrument type."
            )
        asset_type = (
            target.asset_type.upper()
            if target
            else (instrument.asset_type if instrument else "STOCK")
        )
        if instrument and target and asset_type != instrument.asset_type:
            if asset_type == "UNKNOWN":
                asset_type = instrument.asset_type
            elif {asset_type, instrument.asset_type} != {"ETF", "ETC"}:
                raise AnalysisFailure("Broker and provider instrument types conflict.")
        if asset_type not in {"STOCK", "ETF", "ETC"}:
            raise AnalysisFailure("Instrument type is not supported for analysis.")
        return AnalysisRequest(
            symbol=canonical,
            candidate=candidate,
            instrument=instrument,
            asset_type=asset_type,
            strategy=strategy,
            time_horizon=horizon,
            portfolio=PortfolioContext(
                positions=positions,
                cash_pln=portfolio_balance(state).reserve,
                account_value_pln=state.account_value,
                report_dates=state.report_dates,
                cash_dates=state.account_value_dates,
            ),
        )

    async def analyze(self, request: AnalysisRequest) -> Recommendation:
        return await self._analyze(request)

    async def analyze_with_progress(
        self, request: AnalysisRequest, progress: ProgressCallback
    ) -> Recommendation:
        return await self._analyze(request, progress)

    async def _analyze(
        self, request: AnalysisRequest, progress: ProgressCallback | None = None
    ) -> Recommendation:
        from trading_assistant.application.progress import analyze_with_progress
        from trading_assistant.domain.progress import AnalysisProgress

        result = (
            await analyze_with_progress(self.engine, request, progress)
            if progress
            else await self.engine.analyze(request)
        )
        if result.symbol != request.symbol or result.owned != request.owned:
            raise AnalysisFailure("Engine recommendation does not match the request.")
        if self.store:
            if progress:
                progress(AnalysisProgress(stage="Saving recommendation"))
            await asyncio.to_thread(self.store.save, request, result)
        return result
