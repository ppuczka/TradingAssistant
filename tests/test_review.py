import json

from textual.widgets import DataTable, Static
from typer.testing import CliRunner

from trading_assistant.adapters.recommendations import (
    JsonRecommendationReader,
    JsonRecommendationStore,
)
from trading_assistant.analysis_config import AnalysisSettings
from trading_assistant.application.analysis import AnalysisService
from trading_assistant.application.dashboard import DashboardSnapshot
from trading_assistant.application.review import PortfolioReviewService
from trading_assistant.cli import app
from trading_assistant.domain.analysis import AnalysisFailure, Recommendation, Strategy
from trading_assistant.domain.portfolio import Holding, PortfolioState
from trading_assistant.tui.dashboard import DashboardApp
from trading_assistant.tui.review_modal import ReviewModal


class Repository:
    calls = 0

    def portfolio(self):
        self.calls += 1
        return PortfolioState(
            holdings=[
                Holding(symbol=s, name=s, asset_type="STOCK", quantity="1")
                for s in ["GE.US", "AAPL.US", "PKO.PL"]
            ]
        )


class Engine:
    def __init__(self):
        self.calls = []

    async def analyze(self, req):
        self.calls.append(req)
        if req.symbol == "AAPL":
            raise AnalysisFailure("Data unavailable")
        return Recommendation(
            symbol=req.symbol, owned=True, action="HOLD", engine_rating="Hold", reasoning=["Reason"]
        )


def test_review_plan_uses_one_snapshot_and_excludes_non_us_holdings():
    repo = Repository()
    plan = AnalysisService(repo, Engine()).review_plan(Strategy.LONG_TERM, "3-5 years")
    assert repo.calls == 1
    assert [r.symbol for r in plan.requests] == ["GE", "AAPL"]
    assert plan.skipped == ["PKO.PL"]
    assert all(r.owned and len(r.portfolio.positions) == 3 for r in plan.requests)
    assert plan.requests[0].portfolio == plan.requests[1].portfolio


async def test_review_saves_successes_continues_after_failure_and_reports_progress(tmp_path):
    engine = Engine()
    settings = AnalysisSettings(quick_model="quick", deep_model="deep")
    analysis = AnalysisService(Repository(), engine, JsonRecommendationStore(tmp_path, settings))
    plan = analysis.review_plan(Strategy.POSITION, "3-6 months")
    updates = []
    result = await PortfolioReviewService(analysis).run(
        plan, progress=lambda i, total, item: updates.append(item.status)
    )
    assert [r.status for r in result.items] == ["COMPLETED", "FAILED"]
    assert updates == ["RUNNING", "COMPLETED", "RUNNING", "FAILED"]
    assert result.items[1].error == "Data unavailable"
    assert JsonRecommendationReader(tmp_path).latest()["GE"].recommendation.action == "HOLD"
    assert [r.symbol for r in engine.calls] == ["GE", "AAPL"]


async def test_review_stop_starts_no_more_engine_calls():
    engine = Engine()
    analysis = AnalysisService(Repository(), engine)
    plan = analysis.review_plan(Strategy.LONG_TERM, "3-5 years")
    stop = False

    def progress(index, total, item):
        nonlocal stop
        if item.status == "COMPLETED":
            stop = True

    result = await PortfolioReviewService(analysis).run(
        plan, progress=progress, stop_requested=lambda: stop
    )
    assert [i.status for i in result.items] == ["COMPLETED", "STOPPED"]
    assert len(engine.calls) == 1


def test_review_cli_dry_run_and_partial_failure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "analysis.yaml"
    config.write_text("quick_model: quick\ndeep_model: deep\n")
    engine = Engine()
    analysis = AnalysisService(Repository(), engine)
    monkeypatch.setattr("trading_assistant.bootstrap.analysis_service", lambda *args: analysis)
    runner = CliRunner()
    preview = runner.invoke(app, ["review", "--dry-run"])
    assert preview.exit_code == 0, preview.output
    assert json.loads(preview.output)["symbols"] == ["GE", "AAPL"]
    assert engine.calls == []
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key")
    reviewed = runner.invoke(app, ["review"])
    assert reviewed.exit_code == 1 and "GE: COMPLETED" in reviewed.output
    assert "AAPL: FAILED" in reviewed.output and "PKO.PL" in reviewed.output
    assert runner.invoke(app, ["review", "--market", "INVALID"]).exit_code != 0


async def test_review_modal_waits_for_start_and_displays_partial_results():
    engine = Engine()
    analysis = AnalysisService(Repository(), engine)

    class Dashboard:
        async def snapshot(self):
            return DashboardSnapshot()

    app = DashboardApp(
        Dashboard(),
        analysis_factory=lambda: (
            analysis,
            AnalysisSettings(quick_model="quick", deep_model="deep"),
        ),
    )
    async with app.run_test(size=(130, 50)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.press("r")
        assert isinstance(app.screen, ReviewModal)
        assert engine.calls == []
        await pilot.click("#review-run")
        await app.workers.wait_for_complete()
        table = app.screen.query_one(DataTable)
        assert table.get_row_at(0)[2] == "HOLD"
        assert table.get_row_at(1)[1] == "FAILED"
        assert "1 saved, 1 failed" in str(app.screen.query_one("#review-status", Static).render())
        await pilot.press("escape")
        await app.workers.wait_for_complete()
        assert app.screen is app.home_screen
