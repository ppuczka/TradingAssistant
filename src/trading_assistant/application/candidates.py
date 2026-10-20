"""Candidate discovery with bounded pagination and local holding exclusions."""

import asyncio
from typing import Protocol

from trading_assistant.application.market import SymbolResolver
from trading_assistant.application.portfolio import PortfolioRepository
from trading_assistant.domain.candidates import (
    CandidateBatch,
    CandidatePage,
    CandidateUnavailableError,
)
from trading_assistant.domain.market import MarketDataError


class CandidateProvider(Protocol):
    async def page(self, market: str, offset: int, size: int) -> CandidatePage: ...


class CandidateStore(Protocol):
    def save(self, batch: CandidateBatch) -> None: ...


class CandidateService:
    def __init__(
        self,
        repository: PortfolioRepository,
        provider: CandidateProvider,
        store: CandidateStore | None = None,
    ):
        self.repository, self.provider, self.store = repository, provider, store

    async def fetch(self, markets: list[str], limit: int = 20) -> CandidateBatch:
        if not markets or any(m not in {"US", "PL"} for m in markets) or not 1 <= limit <= 100:
            raise CandidateUnavailableError("Use US and/or PL with a limit between 1 and 100.")
        state = await asyncio.to_thread(self.repository.portfolio)
        held = set()
        batch = CandidateBatch()
        resolver = SymbolResolver()
        for holding in state.holdings:
            try:
                held.add(resolver.analysis_symbol(holding.symbol))
            except MarketDataError:
                batch.warnings.append(
                    f"Unable to map holding {holding.symbol}; owned exclusion is incomplete."
                )
        seen = set()
        for market in dict.fromkeys(markets):
            selected = []
            offset = 0
            for _ in range(3):
                try:
                    page = await self.provider.page(market, offset, 100)
                except CandidateUnavailableError as exc:
                    batch.errors[market] = str(exc)
                    break
                batch.rejected += page.rejected
                for candidate in page.candidates:
                    if candidate.market != market:
                        batch.rejected += 1
                        continue
                    if candidate.symbol in seen:
                        continue
                    seen.add(candidate.symbol)
                    if candidate.symbol in held:
                        batch.excluded_owned += 1
                        continue
                    selected.append(candidate)
                    if len(selected) >= limit:
                        break
                offset += page.received
                if len(selected) >= limit or not page.received or offset >= page.total:
                    break
            if len(selected) < limit and market not in batch.errors:
                batch.warnings.append(
                    f"{market}: only {len(selected)} candidates after filters "
                    "within the bounded search."
                )
            batch.candidates.extend(selected)
        if self.store:
            await asyncio.to_thread(self.store.save, batch)
        return batch
