"""Public Google News RSS fallback; no API key or article-body fetching."""

import asyncio
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import httpx

from trading_assistant.application.news import NewsProvider
from trading_assistant.application.news_relevance import financially_relevant
from trading_assistant.domain.news import NewsArticle, NewsUnavailableError

SEARCHES = {
    "PKO.PL": ('("PKO BP" OR "PKO Bank Polski") when:2d', r"\bpko\b"),
    "XTB.PL": ('"XTB" when:2d', r"\bxtb\b"),
    "RHM.DE": ('"Rheinmetall" when:2d', r"\brheinmetall\b"),
}


class GoogleNewsRssProvider:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport
        self.semaphore = asyncio.Semaphore(2)

    async def news(self, symbol: str) -> list[NewsArticle]:
        if symbol not in SEARCHES:
            return []
        query, pattern = SEARCHES[symbol]
        async with self.semaphore:
            try:
                async with httpx.AsyncClient(timeout=15, transport=self.transport) as client:
                    response = await client.get(
                        "https://news.google.com/rss/search",
                        params={
                            "q": query,
                            "hl": "pl",
                            "gl": "PL",
                            "ceid": "PL:pl",
                        },
                    )
                    response.raise_for_status()
                root = ET.fromstring(response.content)
                if root.tag != "rss" or root.find("channel") is None:
                    raise ValueError("Invalid RSS feed")
            except (httpx.HTTPError, ET.ParseError, ValueError):
                raise NewsUnavailableError("Google News RSS unavailable.") from None
        articles = []
        for item in root.findall("./channel/item"):
            try:
                title = item.findtext("title") or ""
                if not re.search(pattern, title, re.IGNORECASE):
                    continue
                publisher = item.findtext("source") or "Google News"
                if not financially_relevant(title, publisher):
                    continue
                articles.append(
                    NewsArticle(
                        title=title,
                        url=item.findtext("link"),
                        published_at=parsedate_to_datetime(item.findtext("pubDate")),
                        publisher=publisher,
                        discovery_source="Google News RSS",
                    )
                )
            except (TypeError, ValueError, AttributeError, OverflowError):
                continue
        return articles


class FallbackNewsProvider:
    def __init__(self, primary: NewsProvider, fallback: NewsProvider):
        self.primary = primary
        self.fallback = fallback

    async def news(self, symbol: str) -> list[NewsArticle]:
        try:
            articles = await self.primary.news(symbol)
            if articles:
                return articles
        except NewsUnavailableError:
            pass
        return await self.fallback.news(symbol)
