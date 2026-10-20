from datetime import UTC, datetime, timedelta

from textual.widgets import Button, DataTable, Select, Static
from typer.testing import CliRunner

from trading_assistant.adapters.recommendations import (
    JsonRecommendationReader,
    JsonRecommendationStore,
)
from trading_assistant.analysis_config import AnalysisSettings
from trading_assistant.application.dashboard import EmptyDashboardService
from trading_assistant.application.portfolio import PortfolioDashboardService
from trading_assistant.application.recommendations import OpportunityHistoryService
from trading_assistant.cli import app as cli
from trading_assistant.domain.analysis import (
    AnalysisRequest,
    PortfolioContext,
    PositionContext,
    Recommendation,
)
from trading_assistant.domain.portfolio import Holding, PortfolioState
from trading_assistant.tui.dashboard import DashboardApp
from trading_assistant.tui.full_analysis import FullAnalysisView
from trading_assistant.tui.opportunity_history import OpportunityHistoryView


def save(directory, symbol, action, when, owned=False):
    request = AnalysisRequest(
        symbol=symbol,
        time_horizon="3 years",
        portfolio=PortfolioContext(
            positions=[PositionContext(broker_symbol=symbol, canonical_symbol=symbol, quantity="1")]
            if owned
            else []
        ),
    )
    result = Recommendation(
        symbol=symbol,
        action=action,
        owned=owned,
        engine_rating="Hold",
        reasoning=["Synthetic reasoning"],
        analyzed_at=when,
        reports={"market": "Synthetic market report"},
    )
    JsonRecommendationStore(
        directory, AnalysisSettings(quick_model="quick", deep_model="deep")
    ).save(request, result)


async def test_saved_history_latest_and_all_preserve_runs_and_exclude_invalid(tmp_path):
    now = datetime.now(UTC)
    save(tmp_path, "TEST", "BUY", now - timedelta(hours=2))
    save(tmp_path, "TEST", "IGNORE", now - timedelta(hours=1))
    save(tmp_path, "OTHER", "WATCH", now - timedelta(minutes=30))
    save(tmp_path, "GE", "HOLD", now, owned=True)
    save(tmp_path, "FUTURE", "BUY", now + timedelta(days=1))
    (tmp_path / "bad.json").write_text('{"private": "malformed"}')
    reader = JsonRecommendationReader(tmp_path)
    service = OpportunityHistoryService(reader)
    latest = await service.records()
    assert [(s.recommendation.symbol, s.recommendation.action) for s in latest] == [
        ("OTHER", "WATCH"),
        ("TEST", "IGNORE"),
    ]
    history = await service.records(history=True)
    assert [s.recommendation.symbol for s in history] == ["OTHER", "TEST", "TEST"]
    assert list(reader.latest()) == ["GE"]
    assert all(s.recommendation.reports["market"] == "Synthetic market report" for s in history)


async def test_w_and_candidate_button_open_history_without_keys_and_full_report(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    now = datetime.now(UTC)
    save(tmp_path, "TEST", "WATCH", now - timedelta(hours=1))
    save(tmp_path, "TEST", "BUY", now)
    history = OpportunityHistoryService(JsonRecommendationReader(tmp_path))

    def forbidden():
        raise AssertionError("Must not construct analysis engine or fetch candidates")

    app = DashboardApp(
        EmptyDashboardService(),
        analysis_factory=forbidden,
        candidate_factory=lambda: object(),
        opportunity_history_factory=lambda: history,
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.press("w")
        await app.workers.wait_for_complete()
        screen = app.screen
        assert isinstance(screen, OpportunityHistoryView)
        table = screen.query_one(DataTable)
        assert table.row_count == 1
        assert table.get_row_at(0)[1] == "BUY"
        table.focus()
        await pilot.press("enter")
        assert isinstance(app.screen, FullAnalysisView)
        await pilot.press("escape")
        screen.query_one(Select).value = "history"
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert table.row_count == 2
        await pilot.press("escape", "s")
        app.screen.query_one("#candidate-saved", Button).press()
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert isinstance(app.screen, OpportunityHistoryView)
        assert app.screen.query_one(DataTable).row_count == 1


async def test_empty_history_and_read_failure_are_explicit():
    class History:
        fail = False

        async def records(self, *, history=False):
            if self.fail:
                raise RuntimeError("private detail")
            return []

    history = History()
    app = DashboardApp(EmptyDashboardService(), opportunity_history_factory=lambda: history)
    async with app.run_test(size=(80, 24)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.press("w")
        await app.workers.wait_for_complete()
        screen = app.screen
        assert "No saved" in str(screen.query_one("#opportunity-history-status", Static).render())
        assert screen.query_one("#opportunity-history-report", Button).disabled
        history.fail = True
        screen.reload()
        await app.workers.wait_for_complete()
        status = str(screen.query_one("#opportunity-history-status", Static).render())
        assert "retry" in status and "private detail" not in status


async def test_dashboard_displays_saved_opportunities_but_not_currently_owned(tmp_path):
    now = datetime.now(UTC)
    save(tmp_path, "TEST", "BUY", now)
    save(tmp_path, "GE", "BUY", now)

    class Repository:
        def portfolio(self):
            return PortfolioState(
                holdings=[Holding(symbol="GE.US", name="GE", asset_type="STOCK", quantity="1")]
            )

    service = PortfolioDashboardService(
        Repository(), recommendation_reader=JsonRecommendationReader(tmp_path)
    )
    snapshot = await service.initial_snapshot()
    assert [(r.symbol, r.action) for r in snapshot.opportunities] == [("TEST", "BUY")]


def test_cli_saved_reviews_no_key_or_network(tmp_path, monkeypatch):
    from trading_assistant import bootstrap

    save(tmp_path, "TEST", "BUY", datetime.now(UTC))
    service = OpportunityHistoryService(JsonRecommendationReader(tmp_path))
    monkeypatch.setattr(bootstrap, "opportunity_history_service", lambda path: service)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = CliRunner().invoke(cli, ["opportunity-reviews", "--symbol", "TEST"])
    assert result.exit_code == 0, result.output
    assert "BUY" in result.output and "Synthetic market report" in result.output
