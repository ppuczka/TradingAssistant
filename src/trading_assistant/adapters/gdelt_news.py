"""GDELT discovery metadata; seendate is an index time, not publication evidence."""

import asyncio
import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from trading_assistant.application.news_relevance import financially_relevant
from trading_assistant.domain.news import NewsArticle, NewsUnavailableError

COMPANIES = {
    "PKO.PL": ('("PKO BP" OR "PKO Bank Polski")', r"\bpko\b"),
    "XTB.PL": ('"XTB"', r"\bxtb\b"),
    "RHM.DE": ('"Rheinmetall"', r"\brheinmetall\b"),
}


class GdeltNewsProvider:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport
        self.lock = asyncio.Lock()
        self.last_request = 0.0
        self.blocked_until = 0.0

    async def news(self, symbol: str) -> list[NewsArticle]:
        if symbol not in COMPANIES:
            return []
        query, pattern = COMPANIES[symbol]
        now = datetime.now(UTC)
        local = now.astimezone(ZoneInfo("Europe/Warsaw"))
        start = (local - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        params = {
            "query": query,
            "mode": "ArtList",
            "format": "json",
            "sort": "DateDesc",
            "maxrecords": 250,
            "startdatetime": start.astimezone(UTC).strftime("%Y%m%d%H%M%S"),
        }
        async with self.lock:
            loop = asyncio.get_running_loop()
            if loop.time() < self.blocked_until:
                raise NewsUnavailableError("GDELT rate limit cooldown.")
            # Keep public API requests sequential and conservatively spaced.
            await asyncio.sleep(max(0, self.last_request + 10 - loop.time()))
            self.last_request = loop.time()
            try:
                async with httpx.AsyncClient(timeout=20, transport=self.transport) as client:
                    response = await client.get(
                        "https://api.gdeltproject.org/api/v2/doc/doc", params=params
                    )
                    if response.status_code == 429:
                        self.blocked_until = loop.time() + 1800
                        raise NewsUnavailableError("GDELT rate limit cooldown.")
                    response.raise_for_status()
                    data = response.json()
                raw = data["articles"]
                if not isinstance(raw, list):
                    raise ValueError("Invalid feed")
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                raise NewsUnavailableError("GDELT news unavailable.") from None
        articles = []
        for item in raw:
            try:
                if not re.search(pattern, item["title"], re.IGNORECASE):
                    continue
                if not financially_relevant(item["title"]):
                    continue
                seen = datetime.strptime(item["seendate"], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
                if not start.astimezone(UTC) <= seen <= now:
                    continue
                articles.append(
                    NewsArticle(
                        title=item["title"],
                        url=item["url"],
                        published_at=seen,
                        publisher=item["domain"],
                        timestamp_kind="indexed",
                        discovery_source="GDELT",
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return articles
