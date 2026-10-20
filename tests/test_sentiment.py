from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from textual.widgets import Button, Static
from typer.testing import CliRunner

from trading_assistant.adapters.openai_sentiment import OpenAISentimentEngine
from trading_assistant.adapters.sentiment_store import JsonSentimentStore
from trading_assistant.analysis_config import AnalysisSettings
from trading_assistant.application.dashboard import DashboardSnapshot, PortfolioRow
from trading_assistant.application.sentiment import SentimentService, validate_sentiment
from trading_assistant.cli import app as cli
from trading_assistant.domain.sentiment import MarketSentiment, SentimentFailure, SentimentResult
from trading_assistant.tui.dashboard import DashboardApp
from trading_assistant.tui.market_assessment import MarketAssessmentView


class Dashboard:
    async def initial_snapshot(self):
        return DashboardSnapshot(portfolio_risk="Report-based • synthetic warning")

    async def snapshot(self):
        now = datetime.now(UTC)
        return DashboardSnapshot(
            positions=[
                PortfolioRow(
                    symbol="GE.US",
                    currency="PLN",
                    quote_currency="USD",
                    quote_price="50",
                    quote_daily_change_percent="1",
                    quote_as_of=now,
                    quote_source="Synthetic provider",
                ),
                PortfolioRow(
                    symbol="PKO.PL",
                    currency="PLN",
                    quote_currency="PLN",
                    quote_price="50",
                    quote_daily_change_percent="-1",
                    quote_as_of=now - timedelta(days=2),
                ),
            ]
        )


def result():
    return SentimentResult(
        markets=[
            MarketSentiment(
                market="US",
                outlook="BULLISH",
                reasoning=["Observed positive change"],
                limitations=["One listing, not broad market"],
                quote_symbols=["GE.US"],
                news_urls=[],
            ),
            MarketSentiment(
                market="PL",
                outlook="UNKNOWN",
                reasoning=["No fresh evidence"],
                limitations=["Missing evidence"],
                quote_symbols=[],
                news_urls=[],
            ),
        ]
    )


class Engine:
    def __init__(self):
        self.calls = 0

    async def assess(self, request):
        self.calls += 1
        return result()


async def test_evidence_stale_quote_exclusion_persistence_and_no_fabricated_pl_sentiment(tmp_path):
    engine = Engine()
    store = JsonSentimentStore(tmp_path)
    service = SentimentService(Dashboard(), store, engine, "configured-quick")
    request = await service.request()
    assert [q.symbol for q in request.quotes] == ["GE.US"]
    assert request.expected_symbols == {"US": ["GE.US"], "PL": ["PKO.PL"]}
    saved = await service.assess(request)
    assert engine.calls == 1
    assert (await service.saved()).model == "configured-quick"
    assert store.latest().result == saved.result
    (tmp_path / "bad.json").write_text("private malformed record")
    assert store.latest() is not None
    invalid = result()
    invalid.markets[1].outlook = "BULLISH"
    with pytest.raises(SentimentFailure, match="Missing market evidence"):
        validate_sentiment(request, invalid)
    invalid = result()
    invalid.markets[0].quote_symbols = ["INVENTED.US"]
    with pytest.raises(SentimentFailure, match="outside"):
        validate_sentiment(request, invalid)
    request.quotes = []
    with pytest.raises(SentimentFailure, match="No fresh"):
        await service.assess(request)
    assert engine.calls == 1


async def test_openai_adapter_structured_configuration_and_sanitized_failure(tmp_path):
    seen = {}

    async def parse(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(status="completed", output_parsed=result())

    client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    engine = OpenAISentimentEngine(
        AnalysisSettings(quick_model="configured-quick", deep_model="deep"), client
    )
    request = await SentimentService(Dashboard(), JsonSentimentStore(tmp_path)).request()
    assert (await engine.assess(request)) == result()
    assert seen["model"] == "configured-quick" and seen["store"] is False
    assert seen["text_format"] is SentimentResult
    assert "tools" not in seen
    assert "account_value" not in seen["input"]

    async def fail(**kwargs):
        raise RuntimeError("private-api-secret")

    client.responses.parse = fail
    with pytest.raises(SentimentFailure) as exc:
        await engine.assess(request)
    assert "private-api-secret" not in str(exc.value)


async def test_market_view_saved_only_open_and_explicit_paid_generation(tmp_path):
    dashboard = Dashboard()
    store = JsonSentimentStore(tmp_path)
    engine = Engine()
    readonly = SentimentService(dashboard, store)
    paid = SentimentService(dashboard, store, engine, "quick")
    factories = []

    def factory(is_paid):
        factories.append(is_paid)
        return paid if is_paid else readonly

    app = DashboardApp(dashboard, sentiment_factory=factory)
    async with app.run_test(size=(140, 50)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.press("m")
        await app.workers.wait_for_complete()
        screen = app.screen
        assert isinstance(screen, MarketAssessmentView)
        assert engine.calls == 0 and factories == [False]
        screen.query_one("#market-evidence", Button).press()
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert engine.calls == 0
        screen.query_one("#market-generate", Button).press()
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert engine.calls == 1 and factories == [False, True]
        text = str(screen.query_one("#market-result", Static).render())
        assert "US: BULLISH" in text and "PL: UNKNOWN" in text
        await pilot.press("escape", "m")
        await app.workers.wait_for_complete()
        assert "US: BULLISH" in str(app.screen.query_one("#market-result", Static).render())
        assert engine.calls == 1


def test_cli_saved_summary_does_not_create_paid_engine(tmp_path, monkeypatch):
    from trading_assistant import bootstrap

    calls = []

    def factory(path, *, paid=False):
        calls.append(paid)
        return SentimentService(Dashboard(), JsonSentimentStore(tmp_path))

    monkeypatch.setattr(bootstrap, "sentiment_service", factory)
    response = CliRunner().invoke(cli, ["sentiment"])
    assert response.exit_code == 0 and "No saved sentiment" in response.output
    assert calls == [False]
    response = CliRunner().invoke(cli, ["sentiment", "--dry-run"])
    assert response.exit_code == 0 and "GE.US" in response.output
    assert calls == [False, False]


async def test_news_market_citations_and_expired_snapshot(tmp_path):
    from trading_assistant.domain.news import NewsArticle

    class NewsDashboard(Dashboard):
        async def snapshot(self):
            snapshot = await super().snapshot()
            snapshot.news = [
                NewsArticle(
                    title="Synthetic earnings headline",
                    url="https://example.com/pko",
                    publisher="Synthetic",
                    published_at=datetime.now(UTC),
                    symbols=["PKO.PL"],
                )
            ]
            return snapshot

    service = SentimentService(NewsDashboard(), JsonSentimentStore(tmp_path), Engine(), "quick")
    request = await service.request()
    assert request.news_markets["https://example.com/pko"] == ["PL"]
    output = result()
    output.markets[1].outlook = "NEUTRAL"
    output.markets[1].news_urls = ["https://example.com/pko"]
    validate_sentiment(request, output)
    output.markets[0].news_urls = ["https://example.com/pko"]
    with pytest.raises(SentimentFailure, match="outside"):
        validate_sentiment(request, output)
    request.captured_at = datetime.now(UTC) - timedelta(minutes=6)
    with pytest.raises(SentimentFailure, match="expired"):
        await service.assess(request)


async def test_dashboard_loads_stale_saved_summary_without_network_or_engine(tmp_path):
    from trading_assistant.application.portfolio import PortfolioDashboardService
    from trading_assistant.domain.portfolio import PortfolioState
    from trading_assistant.domain.sentiment import SavedSentiment

    store = JsonSentimentStore(tmp_path)
    request = await SentimentService(Dashboard(), store).request()
    request.captured_at -= timedelta(days=2)
    for quote in request.quotes:
        quote.as_of -= timedelta(days=2)
    store.save(
        SavedSentiment(
            request=request,
            result=result(),
            model="quick",
            analyzed_at=datetime.now(UTC) - timedelta(days=2),
        )
    )

    class Repository:
        def portfolio(self):
            return PortfolioState()

    snapshot = await PortfolioDashboardService(
        Repository(), sentiment_store=store
    ).initial_snapshot()
    assert "US: BULLISH" in snapshot.market_sentiment
    assert "PL: UNKNOWN" in snapshot.market_sentiment
    assert "stale" in snapshot.market_sentiment
