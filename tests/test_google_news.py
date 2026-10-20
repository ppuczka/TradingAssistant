import httpx
import pytest

from trading_assistant.adapters.google_news import FallbackNewsProvider, GoogleNewsRssProvider
from trading_assistant.domain.news import NewsUnavailableError

RSS = """<rss><channel>
<item><title>PKO BP earnings - Test</title><link>https://example.com/pko</link>
<pubDate>Fri, 09 Oct 2026 08:00:00 GMT</pubDate><source>Test</source></item>
<item><title>Unrelated football match</title><link>https://example.com/sport</link>
<pubDate>Fri, 09 Oct 2026 08:00:00 GMT</pubDate></item>
<item><title>PKO invalid date</title><link>https://example.com/bad</link><pubDate>bad</pubDate></item>
</channel></rss>"""


async def test_rss_metadata_relevance_and_query():
    def handler(request):
        assert "PKO BP" in request.url.params["q"]
        assert request.url.params["hl"] == "pl"
        return httpx.Response(200, text=RSS)

    provider = GoogleNewsRssProvider(transport=httpx.MockTransport(handler))
    articles = await provider.news("PKO.PL")
    assert len(articles) == 1
    assert articles[0].publisher == "Test"
    assert articles[0].timestamp_kind == "published"
    assert articles[0].discovery_source == "Google News RSS"
    assert await provider.news("UNSUPPORTED.PL") == []


@pytest.mark.parametrize(
    "response",
    [httpx.Response(429), httpx.Response(200, text="bad xml"), httpx.Response(200, text="<html/>")],
)
async def test_rss_failures_are_explicit(response):
    provider = GoogleNewsRssProvider(transport=httpx.MockTransport(lambda request: response))
    with pytest.raises(NewsUnavailableError):
        await provider.news("XTB.PL")


@pytest.mark.parametrize("primary_result", ["error", "empty", "articles"])
async def test_fallback_handles_empty_and_failed_primary(primary_result):
    class Primary:
        async def news(self, symbol):
            if primary_result == "error":
                raise NewsUnavailableError("Rate limited")
            if primary_result == "empty":
                return []
            return ["primary article"]

    class Fallback:
        calls = 0

        async def news(self, symbol):
            self.calls += 1
            return ["fallback article"]

    fallback = Fallback()
    result = await FallbackNewsProvider(Primary(), fallback).news("PKO.PL")
    assert result == ["primary article" if primary_result == "articles" else "fallback article"]
    assert fallback.calls == (0 if primary_result == "articles" else 1)


@pytest.mark.parametrize(
    "title,expected",
    [
        ("PKO BP: wyniki kwartalne i wzrost zysku", True),
        ("XTB: akcje spadają po raporcie", True),
        ("Rheinmetall otrzymał nowe zamówienia", True),
        ("PKO BP: nowe odpisy na kredyty frankowe", True),
        ("XTB shares fall despite positive earnings", True),
        ("PKO Ekstraklasa: wyniki meczów", False),
        ("PKO BP organizuje dyktando", False),
        ("XTB: webinar o rynku akcji", False),
        ("PKO BP: promocja na konto osobiste", False),
        ("XTB nowym sponsorem drużyny", False),
        ("PKO BP zaprasza na wydarzenie - StockWatch", False),
    ],
)
def test_financial_headline_relevance(title, expected):
    from trading_assistant.application.news_relevance import financially_relevant

    assert financially_relevant(title, "StockWatch") is expected


async def test_rss_excludes_company_mentions_without_market_relevance():
    rss = RSS.replace("Unrelated football match", "PKO Ekstraklasa: wyniki meczów")
    provider = GoogleNewsRssProvider(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=rss))
    )
    articles = await provider.news("PKO.PL")
    assert len(articles) == 1 and "earnings" in articles[0].title
