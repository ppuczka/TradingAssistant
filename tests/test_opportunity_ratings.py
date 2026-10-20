from datetime import UTC, datetime, timedelta

from textual.widgets import DataTable

from trading_assistant.application.dashboard import (
    DashboardSnapshot,
    EmptyDashboardService,
    OpportunityRow,
)
from trading_assistant.application.recommendations import OpportunityHistoryService
from trading_assistant.application.review import ordered_opportunities
from trading_assistant.domain.analysis import (
    AnalysisRequest,
    PortfolioContext,
    Recommendation,
    SavedAnalysis,
)
from trading_assistant.domain.review import ReviewItem
from trading_assistant.tui.dashboard import DashboardApp
from trading_assistant.tui.opportunity_history import OpportunityHistoryView


def result(symbol, action, rating, when=None):
    return Recommendation(
        symbol=symbol,
        action=action,
        owned=False,
        engine_rating=rating,
        analyzed_at=when or datetime.now(UTC),
        reasoning=["Synthetic reasoning"],
    )


def saved(recommendation):
    return SavedAnalysis(
        request=AnalysisRequest(
            symbol=recommendation.symbol, time_horizon="3 years", portfolio=PortfolioContext()
        ),
        recommendation=recommendation,
    )


class Reader:
    def opportunities(self):
        now = datetime.now(UTC)
        return [
            saved(result("OVER", "BUY", "Overweight")),
            saved(result("STRONG", "BUY", "Buy")),
            saved(result("WAIT", "WATCH", "Hold")),
            saved(result("STRONG", "IGNORE", "Sell", now - timedelta(days=1))),
        ]


async def test_latest_strength_sorts_buy_before_overweight_without_inventing_confidence():
    service = OpportunityHistoryService(Reader())
    latest = await service.records()
    assert [s.recommendation.symbol for s in latest] == ["STRONG", "OVER", "WAIT"]
    assert all(s.recommendation.confidence is None for s in latest)
    history = await service.records(history=True)
    assert history[-1].recommendation.action == "IGNORE"
    items = [
        ReviewItem(
            symbol=s.recommendation.symbol, status="COMPLETED", recommendation=s.recommendation
        )
        for s in reversed(latest)
    ]
    assert [i.symbol for i in ordered_opportunities(items)] == ["STRONG", "OVER", "WAIT"]


async def test_rating_visible_in_history_and_dashboard_without_new_analysis():
    service = OpportunityHistoryService(Reader())
    app = DashboardApp(EmptyDashboardService(), opportunity_history_factory=lambda: service)
    async with app.run_test(size=(120, 40)) as pilot:
        await app.workers.wait_for_complete()
        app.render_snapshot(
            DashboardSnapshot(
                opportunities=[OpportunityRow(symbol="STRONG", action="BUY", engine_rating="Buy")]
            )
        )
        assert str(app.query_one("#opportunities-table", DataTable).get_row_at(0)[2]) == "Buy"
        await pilot.press("w")
        await app.workers.wait_for_complete()
        assert isinstance(app.screen, OpportunityHistoryView)
        row = app.screen.query_one(DataTable).get_row_at(0)
        assert row[0] == "STRONG" and row[2] == "Buy"
