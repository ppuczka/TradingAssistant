from datetime import UTC, datetime, timedelta

import pytest

from trading_assistant.adapters.yahoo_news import YahooNewsProvider
from trading_assistant.application.market import SymbolResolver
from trading_assistant.application.news import PortfolioNewsService
from trading_assistant.domain.news import NewsArticle, NewsUnavailableError


def article(title="Example", url="https://example.com/news", published=None):
    return NewsArticle(
        title=title, url=url, published_at=published or datetime.now(UTC), publisher="Test"
    )


async def test_yahoo_metadata_and_invalid_feed():
    raw = [
        {
            "content": {
                "title": "Headline [safe]",
                "pubDate": "2026-10-09T08:00:00Z",
                "canonicalUrl": {"url": "https://example.com/story"},
                "provider": {"displayName": "Publisher"},
            }
        },
        {"content": {"title": "invalid"}},
    ]
    provider = YahooNewsProvider(lambda symbol: raw)
    news = await provider.news("GE")
    assert len(news) == 1 and news[0].publisher == "Publisher"
    with pytest.raises(NewsUnavailableError):
        await YahooNewsProvider(lambda symbol: [{"bad": "data"}]).news("GE")
    assert await YahooNewsProvider(lambda symbol: []).news("GE") == []


async def test_news_background_cache_dates_deduplication_and_failure():
    calls = []
    now = datetime.now(UTC)

    class Provider:
        async def news(self, symbol):
            calls.append(symbol)
            if symbol == "BAD":
                raise NewsUnavailableError("unavailable")
            return [
                article(),
                article("Yesterday", "https://example.com/yesterday", now - timedelta(days=1)),
                article("Old", "https://example.com/old", now - timedelta(days=3)),
                article("Future", "https://example.com/future", now + timedelta(days=1)),
            ]

    service = PortfolioNewsService(Provider())
    articles, status = service.snapshot(["GE.US", "PKO.PL", "BAD.US"])
    assert articles == [] and "Loading" in status
    await service.task
    articles, status = service.snapshot(["GE.US", "PKO.PL", "BAD.US"])
    assert len(articles) == 2
    assert set(articles[0].symbols) == {"GE.US", "PKO.PL"}
    assert "1/3 feeds unavailable" in status
    assert sorted(calls) == ["BAD", "GE", "PKO.WA"]
    service.snapshot(["GE.US", "PKO.PL", "BAD.US"])
    assert len(calls) == 3
    service.snapshot(["GE.US"])
    await service.task
    assert service.snapshot(["GE.US"])[0][0].symbols == ["GE.US"]


def test_news_symbol_boundary():
    resolver = SymbolResolver()
    assert resolver.yahoo_news("GE.US") == "GE"
    assert resolver.yahoo_news("COPA.UK") == "COPA.L"


async def test_news_panel_position_and_links():
    from textual.widgets import Link, Static

    from trading_assistant.application.dashboard import DashboardSnapshot
    from trading_assistant.tui.dashboard import DashboardApp
    from trading_assistant.tui.news_panel import NewsPanel

    class Service:
        async def snapshot(self):
            return DashboardSnapshot(news=[article()], news_status="Yahoo ticker feeds")

    app = DashboardApp(Service())
    async with app.run_test(size=(140, 60)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        panel = app.query_one(NewsPanel)
        assert str(panel.query_one(Link).url) == "https://example.com/news"
        assert app.query_one("#opportunities-table").region.y < panel.region.y
        assert panel.region.y < app.query_one("#alerts", Static).region.y


async def test_yahoo_search_fallback_metadata_requires_exact_ticker():
    raw = [
        {
            "title": "GE headline",
            "link": "https://example.com/ge",
            "providerPublishTime": 1791532800,
            "publisher": "Test",
            "relatedTickers": ["GE"],
        },
        {
            "title": "Unrelated",
            "link": "https://example.com/other",
            "providerPublishTime": 1791532800,
            "relatedTickers": ["GEO"],
        },
    ]
    articles = await YahooNewsProvider(lambda symbol: raw).news("GE")
    assert len(articles) == 1 and articles[0].title == "GE headline"
