"""Explicit sentiment generation from observed evidence; no broker or TA dependencies."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Protocol

from trading_assistant.application.dashboard import DashboardService
from trading_assistant.application.market import SymbolResolver
from trading_assistant.domain.sentiment import (
    SavedSentiment,
    SentimentFailure,
    SentimentQuote,
    SentimentRequest,
    SentimentResult,
)


class SentimentEngine(Protocol):
    async def assess(self, request: SentimentRequest) -> SentimentResult: ...


class SentimentStore(Protocol):
    def save(self, saved: SavedSentiment) -> None: ...
    def latest(self) -> SavedSentiment | None: ...


def validate_sentiment(request: SentimentRequest, result: SentimentResult) -> None:
    if sorted(m.market for m in result.markets) != ["PL", "US"]:
        raise SentimentFailure("The summary must contain exactly US and PL assessments.")
    for market in result.markets:
        quotes = {q.symbol for q in request.quotes if q.market == market.market}
        news = {
            str(a.url)
            for a in request.news
            if market.market in request.news_markets.get(str(a.url), [])
        }
        if not set(market.quote_symbols) <= quotes or not set(market.news_urls) <= news:
            raise SentimentFailure(
                "The summary cites evidence outside the supplied market context."
            )
        if not quotes and not news and market.outlook != "UNKNOWN":
            raise SentimentFailure("Missing market evidence requires UNKNOWN sentiment.")
        if market.outlook != "UNKNOWN" and not (market.quote_symbols or market.news_urls):
            raise SentimentFailure("A sentiment opinion must cite supplied evidence.")
        if not all(reason.strip() for reason in market.reasoning):
            raise SentimentFailure("Sentiment reasoning cannot be blank.")


class SentimentService:
    def __init__(
        self,
        dashboard: DashboardService,
        store: SentimentStore,
        engine: SentimentEngine | None = None,
        model: str = "",
    ):
        self.dashboard, self.store, self.engine, self.model = dashboard, store, engine, model
        self.lock = asyncio.Lock()

    async def saved(self):
        return await asyncio.to_thread(self.store.latest)

    async def request(self) -> SentimentRequest:
        snapshot = await getattr(self.dashboard, "sentiment_snapshot", self.dashboard.snapshot)()
        now = datetime.now(UTC)
        resolver = SymbolResolver()
        expected = {"US": [], "PL": []}
        quotes, markets = [], {}
        limitations = [
            "Only portfolio-linked US/PL listings; "
            "no broad-market index, sector or macro coverage.",
            "News headlines only, not full article verification.",
        ]
        for row in snapshot.positions:
            market = resolver.listing_market(row.symbol)
            if market not in expected:
                continue
            expected[market].append(row.symbol)
            markets[row.symbol] = market
            if (
                row.quote_price is not None
                and row.quote_currency is not None
                and row.quote_daily_change_percent is not None
                and row.quote_as_of is not None
                and now - timedelta(hours=24) <= row.quote_as_of <= now
            ):
                quotes.append(
                    SentimentQuote(
                        symbol=row.symbol,
                        market=market,
                        currency=row.quote_currency,
                        price=row.quote_price,
                        daily_change_percent=row.quote_daily_change_percent,
                        as_of=row.quote_as_of,
                        source=row.quote_source or "Unknown",
                    )
                )
        news, news_markets = [], {}
        for article in snapshot.news:
            article_markets = sorted({markets[s] for s in article.symbols if s in markets})
            if article_markets and now - timedelta(hours=48) <= article.published_at <= now:
                news.append(article)
                news_markets[str(article.url)] = article_markets
            if len(news) == 30:
                break
        limitations.append(snapshot.news_status)
        return SentimentRequest(
            captured_at=now,
            expected_symbols=expected,
            quotes=quotes,
            news=news,
            news_markets=news_markets,
            limitations=limitations,
        )

    async def assess(self, request: SentimentRequest) -> SavedSentiment:
        if self.engine is None:
            raise SentimentFailure("Configure an AI engine before starting sentiment analysis.")
        if not request.quotes and not request.news:
            raise SentimentFailure(
                "No fresh US/PL evidence. Refresh quotes/news and retry; no model call made."
            )
        if not timedelta(0) <= datetime.now(UTC) - request.captured_at <= timedelta(minutes=5):
            raise SentimentFailure("Evidence snapshot expired; prepare a fresh request.")
        async with self.lock:
            result = await self.engine.assess(request)
            validate_sentiment(request, result)
            saved = SavedSentiment(request=request, result=result, model=self.model)
            await asyncio.to_thread(self.store.save, saved)
            return saved
