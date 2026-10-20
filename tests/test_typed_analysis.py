from datetime import date

import pytest
from textual.widgets import Button, Input, Select

from trading_assistant.adapters.yahoo_instruments import YahooInstrumentProvider
from trading_assistant.analysis_config import AnalysisSettings
from trading_assistant.application.analysis import AnalysisService
from trading_assistant.application.dashboard import EmptyDashboardService
from trading_assistant.domain.analysis import AnalysisFailure, Strategy
from trading_assistant.domain.portfolio import Holding, PortfolioState
from trading_assistant.tui.analysis_modal import AnalysisModal
from trading_assistant.tui.dashboard import DashboardApp


@pytest.mark.parametrize(
    "symbol,exchange,currency,kind",
    [
        ("GE", "NYQ", "USD", "EQUITY"),
        ("PCO.WA", "WSE", "PLN", "EQUITY"),
        ("SPY", "PCX", "USD", "ETF"),
    ],
)
async def test_lookup_without_screener_requirements(symbol, exchange, currency, kind):
    provider = YahooInstrumentProvider(
        lambda _: dict(symbol=symbol, exchange=exchange, currency=currency, quoteType=kind)
    )
    result = await provider.resolve(symbol)
    assert result.symbol == symbol
    assert result.asset_type == ("ETF" if kind == "ETF" else "STOCK")


@pytest.mark.parametrize(
    "change",
    [
        {"symbol": "OTHER"},
        {"exchange": "NYQ"},
        {"currency": "EUR"},
        {"quoteType": "CRYPTOCURRENCY"},
    ],
)
async def test_lookup_rejects_mismatched_listing(change):
    info = dict(symbol="PCO.WA", exchange="WSE", currency="PLN", quoteType="EQUITY")
    info.update(change)
    with pytest.raises(AnalysisFailure):
        await YahooInstrumentProvider(lambda _: info).resolve("PCO.PL")


async def test_typed_request_uses_current_holdings_and_validates_before_engine():
    class Repository:
        def portfolio(self):
            return PortfolioState(
                holdings=[
                    Holding(symbol="PCO.PL", name="Synthetic", asset_type="STOCK", quantity="2")
                ]
            )

    provider = YahooInstrumentProvider(
        lambda _: dict(symbol="PCO.WA", exchange="WSE", currency="PLN", quoteType="EQUITY")
    )
    service = AnalysisService(Repository(), object(), instrument_provider=provider)
    request = await service.prepare_request("PCO.PL", Strategy.POSITION, "6 months")
    assert request.owned
    assert request.as_of == date.today()
    assert request.symbol == "PCO.WA"
    assert request.portfolio.positions[0].quantity == 2
    with pytest.raises(AnalysisFailure):
        await service.prepare_request("../bad", Strategy.TRADE, "1 day")


async def test_typed_dialog_is_explicit_and_retry_preserves_settings(tmp_path):
    from trading_assistant.adapters.recommendations import (
        JsonRecommendationReader,
        JsonRecommendationStore,
    )
    from trading_assistant.application.recommendations import OpportunityHistoryService
    from trading_assistant.domain.analysis import Recommendation

    calls = []

    class Repository:
        def portfolio(self):
            return PortfolioState()

    class Engine:
        async def analyze(self, request):
            calls.append(request)
            return Recommendation(
                symbol=request.symbol,
                action="WATCH",
                owned=False,
                engine_rating="Hold",
                reasoning=["Synthetic"],
            )

    settings = AnalysisSettings(quick_model="quick", deep_model="deep")
    provider = YahooInstrumentProvider(
        lambda symbol: dict(symbol=symbol, exchange="NYQ", currency="USD", quoteType="EQUITY")
    )
    reader = JsonRecommendationReader(tmp_path)
    service = AnalysisService(
        Repository(), Engine(), JsonRecommendationStore(tmp_path, settings), reader, provider
    )
    app = DashboardApp(
        EmptyDashboardService(),
        analysis_factory=lambda: (service, settings),
        opportunity_history_factory=lambda: OpportunityHistoryService(reader),
    )
    async with app.run_test(size=(120, 45)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.press("t")
        assert isinstance(app.screen, AnalysisModal)
        assert not calls
        app.screen.query_one("#analysis-symbol", Input).value = "GE.US"
        app.screen.query_one("#analysis-strategy", Select).value = "POSITION"
        app.screen.query_one("#analysis-horizon", Input).value = "6 months"
        app.screen.query_one("#analysis-run", Button).press()
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert len(calls) == 1
        await pilot.press("escape", "w")
        await app.workers.wait_for_complete()
        app.screen.query_one("#opportunity-history-retry", Button).press()
        await pilot.pause()
        assert isinstance(app.screen, AnalysisModal)
        assert app.screen.query_one("#analysis-strategy", Select).value == "POSITION"
        assert app.screen.query_one("#analysis-horizon", Input).value == "6 months"
        assert len(calls) == 1
        app.screen.query_one("#analysis-run", Button).press()
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert len(calls) == 2
        assert len(list(tmp_path.glob("*.json"))) == 2


def test_cli_lookup_dry_run_verifies_without_paid_analysis(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from trading_assistant import bootstrap
    from trading_assistant.cli import app
    from trading_assistant.domain.analysis import AnalysisRequest, PortfolioContext

    calls = []

    class Service:
        async def prepare_request(self, symbol, strategy, horizon):
            calls.append(symbol)
            return AnalysisRequest(
                symbol="PCO.WA",
                strategy=strategy,
                time_horizon=horizon,
                portfolio=PortfolioContext(),
            )

        async def analyze(self, request):
            raise AssertionError("Dry run must not call paid engine")

    config = tmp_path / "analysis.yaml"
    config.write_text("quick_model: quick\ndeep_model: deep\n")
    monkeypatch.setattr(bootstrap, "analysis_service", lambda *args: Service())
    result = CliRunner().invoke(
        app, ["analyze", "PCO.PL", "--lookup", "--dry-run", "--config", str(config)]
    )
    assert result.exit_code == 0, result.output
    assert calls == ["PCO.PL"]
    assert "PCO.WA" in result.output
