from datetime import UTC, datetime, timedelta

import httpx
import pytest

from trading_assistant.adapters.gdelt_news import GdeltNewsProvider
from trading_assistant.application.news import CombinedPortfolioNewsService, PortfolioNewsService
from trading_assistant.domain.news import NewsArticle, NewsUnavailableError


async def test_gdelt_query_metadata_and_title_relevance():
    now = datetime.now(UTC)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")

    def handler(request):
        assert "PKO" in request.url.params["query"]
        assert request.url.params["mode"] == "ArtList"
        assert request.url.params["maxrecords"] == "250"
        assert "startdatetime" in request.url.params
        return httpx.Response(
            200,
            json={
                "articles": [
                    {
                        "title": "PKO BP results",
                        "url": "https://example.com/pko",
                        "domain": "example.com",
                        "seendate": stamp,
                    },
                    {
                        "title": "Unrelated bank",
                        "url": "https://example.com/other",
                        "domain": "example.com",
                        "seendate": stamp,
                    },
                    {
                        "title": "PKO old",
                        "url": "https://example.com/old",
                        "domain": "example.com",
                        "seendate": (now - timedelta(days=4)).strftime("%Y%m%dT%H%M%SZ"),
                    },
                    {
                        "title": "PKO malformed",
                        "url": "javascript:bad",
                        "domain": "example.com",
                        "seendate": stamp,
                    },
                ]
            },
        )

    provider = GdeltNewsProvider(transport=httpx.MockTransport(handler))
    articles = await provider.news("PKO.PL")
    assert len(articles) == 1
    assert articles[0].timestamp_kind == "indexed"
    assert articles[0].discovery_source == "GDELT"
    assert await provider.news("UNKNOWN.PL") == []


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503),
        httpx.Response(200, text="Not JSON"),
        httpx.Response(200, json={"wrong": []}),
    ],
)
async def test_gdelt_errors_are_explicit(response):
    provider = GdeltNewsProvider(transport=httpx.MockTransport(lambda request: response))
    with pytest.raises(NewsUnavailableError):
        await provider.news("XTB.PL")


async def test_gdelt_rate_limit_stops_additional_requests():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429)

    provider = GdeltNewsProvider(transport=httpx.MockTransport(handler))
    for symbol in ["XTB.PL", "RHM.DE"]:
        with pytest.raises(NewsUnavailableError):
            await provider.news(symbol)
    assert len(calls) == 1


async def test_combined_news_merges_urls_and_keeps_yahoo_publication_metadata():
    class Provider:
        def __init__(self, indexed=False):
            self.indexed = indexed
            self.calls = []

        async def news(self, symbol):
            self.calls.append(symbol)
            return [
                NewsArticle(
                    title="Shared headline",
                    url="https://example.com/shared",
                    published_at=datetime.now(UTC),
                    publisher="Test",
                    timestamp_kind="indexed" if self.indexed else "published",
                    discovery_source="GDELT" if self.indexed else "Yahoo",
                )
            ]

    yahoo_provider, gdelt_provider = Provider(), Provider(True)
    yahoo = PortfolioNewsService(yahoo_provider)
    gdelt = PortfolioNewsService(
        gdelt_provider, mapper=lambda s: s, label="GDELT company searches", refresh_seconds=1800
    )
    service = CombinedPortfolioNewsService(yahoo, gdelt, ("PKO.PL", "XTB.PL", "RHM.DE"))
    service.snapshot(["GE.US", "PKO.PL"])
    await yahoo.task
    await gdelt.task
    articles, status = service.snapshot(["GE.US", "PKO.PL"])
    assert len(articles) == 1 and articles[0].timestamp_kind == "published"
    assert set(articles[0].symbols) == {"GE.US", "PKO.PL"}
    assert gdelt_provider.calls == ["PKO.PL"]
    assert "Refresh 30 minutes" in status
