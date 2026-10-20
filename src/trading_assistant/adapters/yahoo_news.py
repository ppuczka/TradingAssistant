"""Yahoo ticker-feed metadata using the installed yfinance API."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

import yfinance as yf

from trading_assistant.domain.news import NewsArticle, NewsUnavailableError


class YahooNewsProvider:
    def __init__(self, fetch: Callable[[str], list] | None = None):
        self.fetch = fetch or self._fetch
        self.semaphore = asyncio.Semaphore(2)

    @staticmethod
    def _fetch(symbol: str) -> list:
        articles = yf.Ticker(symbol).get_news(count=100, tab="all")
        return articles or yf.Search(symbol, news_count=100).news

    async def news(self, symbol: str) -> list[NewsArticle]:
        async with self.semaphore:
            try:
                raw = await asyncio.to_thread(self.fetch, symbol)
                if not isinstance(raw, list):
                    raise ValueError("Invalid feed")
            except Exception:
                raise NewsUnavailableError("Yahoo news feed unavailable.") from None
        articles = []
        for item in raw:
            try:
                if "content" not in item:
                    # Search results can include unrelated companies; require an exact tag.
                    if symbol not in item.get("relatedTickers", []):
                        continue
                    articles.append(
                        NewsArticle(
                            title=item["title"],
                            url=item["link"],
                            published_at=datetime.fromtimestamp(item["providerPublishTime"], UTC),
                            publisher=item.get("publisher") or "Yahoo Finance",
                        )
                    )
                    continue
                content = item["content"]
                if item.get("ad") or not content:
                    continue
                url = content.get("canonicalUrl") or content.get("clickThroughUrl") or {}
                articles.append(
                    NewsArticle(
                        title=content["title"],
                        url=url["url"],
                        published_at=content["pubDate"],
                        publisher=(content.get("provider") or {}).get("displayName")
                        or "Yahoo Finance",
                    )
                )
            except (KeyError, TypeError, ValueError, OverflowError, OSError):
                continue
        if raw and not articles:
            raise NewsUnavailableError("Yahoo news feed returned no valid article metadata.")
        return articles
