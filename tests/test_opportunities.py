from datetime import UTC, datetime, timedelta

import pytest
from textual.widgets import Button, DataTable, Input, ProgressBar, Static
from typer.testing import CliRunner

from trading_assistant.analysis_config import AnalysisSettings
from trading_assistant.application.analysis import AnalysisService
from trading_assistant.application.dashboard import EmptyDashboardService
from trading_assistant.application.review import PortfolioReviewService, ordered_opportunities
from trading_assistant.cli import app as cli
from trading_assistant.domain.analysis import AnalysisFailure, Recommendation, Strategy
from trading_assistant.domain.candidates import CandidateBatch, StockCandidate
from trading_assistant.domain.portfolio import Holding, PortfolioState
from trading_assistant.tui.dashboard import DashboardApp
from trading_assistant.tui.full_analysis import FullAnalysisView
from trading_assistant.tui.opportunity_modal import OpportunityModal


def candidate(symbol="TEST", market="US", **overrides):
    now = datetime.now(UTC)
    values = dict(
        symbol=symbol,
        name="Synthetic stock",
        market=market,
        exchange="NMS" if market == "US" else "WSE",
        currency="USD" if market == "US" else "PLN",
        price="25",
        market_cap="1000000000",
        average_volume="100000",
        quote_as_of=now,
        fetched_at=now,
    )
    values.update(overrides)
    return StockCandidate.model_validate(values)


class Repository:
    def __init__(self):
        self.calls = 0

    def portfolio(self):
        self.calls += 1
        return PortfolioState(
            holdings=[
                Holding(symbol="GE.US", name="GE", asset_type="STOCK", quantity="2"),
                Holding(
                    symbol="11B.PL", name="Synthetic PL holding", asset_type="STOCK", quantity="1"
                ),
            ],
            account_value="1000",
        )


class Engine:
    def __init__(self):
        self.calls = []

    async def analyze(self, request):
        self.calls.append(request)
        if request.symbol == "FAIL":
            raise AnalysisFailure("Synthetic analysis failure")
        return Recommendation(
            symbol=request.symbol,
            owned=False,
            action="BUY" if request.symbol.endswith(".WA") else "WATCH",
            engine_rating="Buy" if request.symbol.endswith(".WA") else "Hold",
            reasoning=["Synthetic portfolio-fit reasoning"],
            reports={"market": "Synthetic full report"},
        )


class Store:
    def __init__(self):
        self.saved = []

    def save(self, request, result):
        self.saved.append((request, result))


def test_opportunity_plan_identity_freshness_owned_exclusion_and_frozen_portfolio():
    repository = Repository()
    service = AnalysisService(repository, Engine())
    pool = [
        candidate("GE"),
        candidate("11B.WA", "PL"),
        candidate("OLD", fetched_at=datetime.now(UTC) - timedelta(days=2)),
        candidate("BAD", currency="PLN"),
        candidate("TEST"),
        candidate("TEST"),
        candidate("NEW.WA", "PL"),
    ]
    plan = service.opportunity_plan(pool, Strategy.POSITION, "6 months", 3)
    assert [r.symbol for r in plan.requests] == ["TEST", "NEW.WA"]
    assert set(plan.skipped) == {"GE", "11B.WA", "OLD", "BAD"}
    assert repository.calls == 1
    assert all(not r.owned and len(r.portfolio.positions) == 2 for r in plan.requests)
    assert all(
        r.portfolio.account_value_pln == 1000 and r.portfolio.cash_pln is None and r.candidate
        for r in plan.requests
    )
    assert all(r.strategy == Strategy.POSITION for r in plan.requests)
    with pytest.raises(AnalysisFailure):
        service.opportunity_plan(pool, Strategy.LONG_TERM, "3 years", 11)
    numeric = service.opportunity_plan([candidate("123.WA", "PL")], Strategy.TRADE, "1 week")
    assert numeric.requests[0].symbol == "123.WA"


async def test_batch_failure_continues_saves_and_groups_actions_without_confidence():
    engine, store = Engine(), Store()
    service = AnalysisService(Repository(), engine, store)
    plan = service.opportunity_plan(
        [candidate("TEST"), candidate("FAIL"), candidate("NEW.WA", "PL")],
        Strategy.LONG_TERM,
        "3 years",
    )
    result = await PortfolioReviewService(service).run(plan)
    assert [i.symbol for i in ordered_opportunities(result.items)] == ["NEW.WA", "TEST", "FAIL"]
    assert result.items[1].status == "FAILED"
    assert len(store.saved) == 2
    assert all(r.confidence is None for _, r in store.saved)
    assert all(req.candidate is not None for req, _ in store.saved)


async def test_ui_candidate_to_ai_explicit_start_and_full_report():
    engine, store = Engine(), Store()
    analysis = AnalysisService(Repository(), engine, store)
    settings = AnalysisSettings(quick_model="quick", deep_model="deep")

    class Candidates:
        async def fetch(self, markets, limit):
            return CandidateBatch(candidates=[candidate("TEST"), candidate("NEW.WA", "PL")])

    app = DashboardApp(
        EmptyDashboardService(),
        candidate_factory=Candidates,
        analysis_factory=lambda: (analysis, settings),
    )
    async with app.run_test(size=(140, 55)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.press("s")
        app.screen.query_one("#candidate-fetch", Button).press()
        await pilot.pause()
        await app.workers.wait_for_complete()
        candidates_table = app.screen.query_one(DataTable)
        candidates_table.focus()
        await pilot.press("space", "down", "space")
        app.screen.query_one("#candidate-ai", Button).press()
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, OpportunityModal)
        assert not engine.calls
        assert screen.query_one("#opportunity-limit", Input).value == "2"
        assert screen.query_one("#opportunity-limit", Input).disabled
        screen.query_one("#opportunity-run", Button).press()
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert len(engine.calls) == 2
        table = screen.query_one(DataTable)
        assert table.get_row_at(0)[0] == "NEW.WA"
        assert table.get_row_at(0)[2] == "BUY"
        assert "2 saved" in str(screen.query_one("#opportunity-status", Static).render())
        screen.query_one("#opportunity-report", Button).press()
        await pilot.pause()
        assert isinstance(app.screen, FullAnalysisView)
        await pilot.press("escape", "escape", "escape")
        assert app.screen is app.home_screen


def test_cli_opportunities_dry_run_never_calls_engine(monkeypatch):
    from trading_assistant import bootstrap

    engine = Engine()
    analysis = AnalysisService(Repository(), engine)

    class Candidates:
        async def fetch(self, markets, limit):
            return CandidateBatch(candidates=[candidate("NEW.WA", "PL")])

    monkeypatch.setattr(bootstrap, "analysis_service", lambda *args: analysis)
    monkeypatch.setattr(bootstrap, "candidate_service", lambda *args: Candidates())
    result = CliRunner().invoke(cli, ["opportunities", "--market", "PL", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "NEW.WA" in result.output
    assert '"candidate"' in result.output
    assert not engine.calls


async def test_opportunity_stop_finishes_current_and_skips_remaining():
    import asyncio

    started, release = asyncio.Event(), asyncio.Event()

    class SlowEngine(Engine):
        async def analyze(self, request):
            started.set()
            await release.wait()
            return await super().analyze(request)

    engine = SlowEngine()
    service = AnalysisService(Repository(), engine, Store())
    app = DashboardApp(EmptyDashboardService())
    async with app.run_test(size=(140, 55)) as pilot:
        await app.workers.wait_for_complete()
        screen = OpportunityModal(
            [candidate("ONE"), candidate("TWO")],
            service,
            AnalysisSettings(quick_model="quick", deep_model="deep"),
        )
        app.push_screen(screen)
        await pilot.pause()
        screen.query_one("#opportunity-run", Button).press()
        await started.wait()
        screen.action_close()
        assert app.screen is screen
        screen.query_one("#opportunity-stop", Button).press()
        await pilot.pause()
        release.set()
        await app.workers.wait_for_complete()
        assert len(engine.calls) == 1
        assert screen.items["ONE"].status == "COMPLETED"
        assert screen.items["TWO"].status == "STOPPED"
        assert not screen.running
        assert screen.query_one("#opportunity-batch-progress", ProgressBar).progress == 1


def test_candidate_context_reaches_engine_prompt_and_preserves_symbol():
    from trading_assistant.adapters.tradingagents import EnginePortfolioContext

    request = (
        AnalysisService(Repository(), Engine())
        .opportunity_plan([candidate("NEW.WA", "PL")], Strategy.LONG_TERM, "3-5 years")
        .requests[0]
    )
    prompt = EnginePortfolioContext(request).render(request.symbol)
    assert "NEW.WA" in prompt
    assert "portfolio fit" in prompt
    assert "cash constraints" in prompt
    assert "3-5 years" in prompt
    assert "Synthetic stock" in prompt
    assert "unverified" in prompt


async def test_selected_review_includes_more_than_default_three_without_truncation():
    engine = Engine()
    analysis = AnalysisService(Repository(), engine, Store())
    selected = [candidate(symbol) for symbol in ("ONE", "TWO", "THREE", "FOUR")]
    app = DashboardApp(EmptyDashboardService())
    async with app.run_test(size=(140, 55)) as pilot:
        await app.workers.wait_for_complete()
        screen = OpportunityModal(
            selected,
            analysis,
            AnalysisSettings(quick_model="quick", deep_model="deep"),
            selected=True,
        )
        app.push_screen(screen)
        await pilot.pause()
        assert screen.query_one("#opportunity-limit", Input).value == "4"
        assert not engine.calls
        screen.query_one("#opportunity-run", Button).press()
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert [r.symbol for r in engine.calls] == ["ONE", "TWO", "THREE", "FOUR"]
        assert screen.query_one("#opportunity-limit", Input).disabled
        assert "4 saved" in str(screen.query_one("#opportunity-status", Static).render())
