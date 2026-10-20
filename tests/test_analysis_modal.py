from decimal import Decimal

import pytest
from textual.widgets import DataTable, Input, Select, Static

from trading_assistant.analysis_config import AnalysisSettings
from trading_assistant.application.dashboard import DashboardSnapshot, PortfolioRow
from trading_assistant.domain.analysis import (
    AnalysisFailure,
    AnalysisRequest,
    PortfolioContext,
    Recommendation,
)
from trading_assistant.tui.analysis_modal import AnalysisModal
from trading_assistant.tui.dashboard import DashboardApp


@pytest.mark.parametrize("portfolio_view", [False, True])
async def test_selected_holding_analysis_is_explicit_and_selection_survives_refresh(portfolio_view):
    calls = []

    class Dashboard:
        async def snapshot(self):
            return DashboardSnapshot(
                positions=[
                    PortfolioRow(symbol=s, currency="PLN", quantity=Decimal("1"))
                    for s in ["GE.US", "AAPL.US"]
                ]
            )

    class Analysis:
        def request(self, symbol, strategy, horizon):
            return AnalysisRequest(
                symbol=symbol.removesuffix(".US"),
                strategy=strategy,
                time_horizon=horizon,
                portfolio=PortfolioContext(),
            )

        async def analyze(self, request):
            calls.append(request)
            return Recommendation(
                symbol=request.symbol,
                action="WATCH",
                owned=False,
                engine_rating="Hold",
                reasoning=["Synthetic reason"],
            )

    settings = AnalysisSettings(quick_model="quick", deep_model="deep")
    app = DashboardApp(Dashboard(), analysis_factory=lambda: (Analysis(), settings))
    async with app.run_test(size=(120, 45)) as pilot:
        await app.workers.wait_for_complete()
        if portfolio_view:
            await pilot.press("p")
            await app.workers.wait_for_complete()
        table = app.screen.query_one(DataTable)
        table.move_cursor(row=1)
        app.refresh_active_screen()
        await app.workers.wait_for_complete()
        assert table.cursor_row == 1
        await pilot.press("a")
        assert isinstance(app.screen, AnalysisModal)
        assert app.screen.symbol == "AAPL.US"
        assert calls == []  # Opening the dialog never calls the paid engine.
        description = app.screen.query_one("#analysis-strategy-description", Static)
        assert "Short-term weakness" in str(description.render())
        selector = app.screen.query_one("#analysis-strategy", Select)
        selector.value = "POSITION"
        await pilot.pause()
        assert "medium-term trends" in str(description.render())
        selector.value = "TRADE"
        await pilot.pause()
        assert "technical signals" in str(description.render())
        selector.value = "LONG_TERM"
        field = app.screen.query_one("#analysis-horizon", Input)
        field.focus()
        field.value = ""
        await pilot.press("a", "d", "q")
        assert field.value == "adq" and isinstance(app.screen, AnalysisModal)
        field.value = "3-5 years"
        await pilot.click("#analysis-run")
        await app.workers.wait_for_complete()
        assert len(calls) == 1 and calls[0].symbol == "AAPL"
        assert "WATCH" in str(app.screen.query_one("#analysis-result", Static).render())
        await pilot.press("escape")
        await app.workers.wait_for_complete()
        assert not isinstance(app.screen, AnalysisModal)


async def test_analysis_failure_is_visible_and_retry_enabled():
    class Analysis:
        def request(self, symbol, strategy, horizon):
            return AnalysisRequest(symbol="GE", time_horizon=horizon, portfolio=PortfolioContext())

        async def analyze(self, request):
            raise AnalysisFailure("Provider unavailable")

    class Dashboard:
        async def snapshot(self):
            return DashboardSnapshot(positions=[PortfolioRow(symbol="GE.US", currency="PLN")])

    app = DashboardApp(
        Dashboard(),
        analysis_factory=lambda: (
            Analysis(),
            AnalysisSettings(quick_model="quick", deep_model="deep"),
        ),
    )
    async with app.run_test(size=(120, 45)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.press("a")
        await pilot.click("#analysis-run")
        await app.workers.wait_for_complete()
        assert "Provider unavailable" in str(
            app.screen.query_one("#analysis-result", Static).render()
        )
        assert not app.screen.query_one("#analysis-run").disabled


async def test_completed_dashboard_analysis_updates_ai_column(tmp_path):
    from trading_assistant.adapters.recommendations import (
        JsonRecommendationReader,
        JsonRecommendationStore,
    )
    from trading_assistant.application.analysis import AnalysisService
    from trading_assistant.application.portfolio import PortfolioDashboardService
    from trading_assistant.domain.portfolio import Holding, PortfolioState

    class Repository:
        def portfolio(self):
            return PortfolioState(
                holdings=[Holding(symbol="GE.US", name="GE", asset_type="STOCK", quantity="1")]
            )

    class Engine:
        async def analyze(self, request):
            return Recommendation(
                symbol=request.symbol,
                action="HOLD",
                owned=True,
                engine_rating="Hold",
                reasoning=["Synthetic long-term reasoning"],
            )

    settings = AnalysisSettings(quick_model="quick", deep_model="deep")
    repository = Repository()
    analysis = AnalysisService(repository, Engine(), JsonRecommendationStore(tmp_path, settings))
    dashboard = PortfolioDashboardService(
        repository, recommendation_reader=JsonRecommendationReader(tmp_path)
    )
    app = DashboardApp(dashboard, analysis_factory=lambda: (analysis, settings))
    async with app.run_test(size=(120, 45)) as pilot:
        await app.workers.wait_for_complete()
        assert str(app.query_one(DataTable).get_row_at(0)[11]) == "—"
        await pilot.press("a")
        await pilot.click("#analysis-run")
        await app.workers.wait_for_complete()
        await pilot.press("escape")
        await app.workers.wait_for_complete()
        assert str(app.query_one(DataTable).get_row_at(0)[11]) == "HOLD"
