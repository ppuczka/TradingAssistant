"""Nonblocking cached ticker feeds, filtered to two local calendar days."""

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from trading_assistant.application.market import SymbolResolver
from trading_assistant.domain.market import MarketDataError
from trading_assistant.domain.news import NewsArticle, NewsUnavailableError


class NewsProvider(Protocol):
    async def news(self, symbol: str) -> list[NewsArticle]: ...


class PortfolioNewsSource(Protocol):
    def snapshot(self, symbols: list[str]) -> tuple[list[NewsArticle], str]: ...


class PortfolioNewsService:
    def __init__(
        self,
        provider: NewsProvider,
        *,
        mapper: Callable[[str], str] | None = None,
        label: str = "Yahoo ticker feeds",
        refresh_seconds: int = 300,
    ):
        self.provider = provider
        self.mapper = mapper or SymbolResolver().yahoo_news
        self.label = label
        self.refresh_seconds = refresh_seconds
        self.task: asyncio.Task | None = None
        self.symbols: tuple[str, ...] = ()
        self.articles: list[NewsArticle] = []
        self.errors = 0
        self.fetched_at: datetime | None = None
        self.next_refresh = 0.0

    def snapshot(self, symbols: list[str]) -> tuple[list[NewsArticle], str]:
        key = tuple(sorted(set(symbols)))
        if self.task is None or self.task.done():
            if key != self.symbols or time.monotonic() >= self.next_refresh:
                if key != self.symbols:
                    self.articles = []
                    self.fetched_at = None
                self.symbols = key
                self.task = asyncio.create_task(self._refresh(key))
                self.next_refresh = time.monotonic() + self.refresh_seconds
        now = datetime.now(UTC)
        today = now.astimezone(ZoneInfo("Europe/Warsaw")).date()
        allowed = {today, today - timedelta(days=1)}
        articles = [
            a
            for a in self.articles
            if a.published_at <= now
            and a.published_at.astimezone(ZoneInfo("Europe/Warsaw")).date() in allowed
        ]
        if key != self.symbols:
            articles = []  # An earlier portfolio's background request is still finishing.
        articles.sort(key=lambda a: a.published_at, reverse=True)
        status = (
            f"{self.label} • Today / yesterday (Europe/Warsaw) • "
            f"Refresh {self.refresh_seconds // 60} minutes"
        )
        if self.task and not self.task.done():
            status += " • Loading…"
        elif self.errors:
            status += f" • {self.errors}/{len(key)} feeds unavailable or unmapped"
        if self.fetched_at:
            status += f" • Checked {self.fetched_at:%H:%M} UTC"
        status += f" • {len(articles)} recent headlines"
        return articles, status

    async def ready(self, timeout: float = 30) -> None:
        if self.task:
            try:
                await asyncio.wait_for(asyncio.shield(self.task), timeout=timeout)
            except Exception:
                return

    async def _refresh(self, symbols: tuple[str, ...]) -> None:
        async def fetch(symbol):
            try:
                identifier = self.mapper(symbol)
                return symbol, await self.provider.news(identifier)
            except (MarketDataError, NewsUnavailableError):
                return symbol, None

        results = await asyncio.gather(*(fetch(symbol) for symbol in symbols))
        unique: dict[str, NewsArticle] = {}
        errors = 0
        for symbol, articles in results:
            if articles is None:
                errors += 1
                continue
            for article in articles:
                key = str(article.url)
                if key not in unique:
                    unique[key] = article.model_copy(update={"symbols": []}, deep=True)
                if symbol not in unique[key].symbols:
                    unique[key].symbols.append(symbol)
        self.articles = list(unique.values())
        self.errors = errors
        self.fetched_at = datetime.now(UTC)


class CombinedPortfolioNewsService:
    def __init__(
        self,
        yahoo: PortfolioNewsService,
        gdelt: PortfolioNewsService,
        supported_symbols: tuple[str, ...],
    ):
        self.yahoo = yahoo
        self.gdelt = gdelt
        self.supported_symbols = supported_symbols

    async def ready(self, timeout: float = 30) -> None:
        await asyncio.gather(self.yahoo.ready(timeout), self.gdelt.ready(timeout))

    def snapshot(self, symbols: list[str]) -> tuple[list[NewsArticle], str]:
        yahoo, yahoo_status = self.yahoo.snapshot(symbols)
        gdelt, gdelt_status = self.gdelt.snapshot(
            [s for s in symbols if s in self.supported_symbols]
        )
        unique = {str(a.url): a.model_copy(deep=True) for a in yahoo}
        for article in gdelt:
            key = str(article.url)
            if key not in unique:
                unique[key] = article.model_copy(deep=True)
            else:
                unique[key].symbols = sorted(set(unique[key].symbols + article.symbols))
        articles = sorted(unique.values(), key=lambda a: a.published_at, reverse=True)
        return (
            articles,
            f"{yahoo_status}\n{gdelt_status} • GDELT times are indexed, not publication times",
        )
