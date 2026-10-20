from textual.widgets import Select

from trading_assistant.analysis_config import AnalysisSettings
from trading_assistant.application.analysis import AnalysisService
from trading_assistant.application.dashboard import DashboardSnapshot
from trading_assistant.application.market import SymbolResolver
from trading_assistant.domain.analysis import Strategy
from trading_assistant.domain.portfolio import Holding, PortfolioState
from trading_assistant.tui.dashboard import DashboardApp
from trading_assistant.tui.review_modal import ReviewModal


class Portfolio:
    def portfolio(self):
        return PortfolioState(
            holdings=[
                Holding(symbol=s, name=s, asset_type=kind, quantity="1")
                for s, kind in [
                    ("GE.US", "STOCK"),
                    ("RHM.DE", "STOCK"),
                    ("PKO.PL", "STOCK"),
                    ("XTB.PL", "STOCK"),
                    ("ETFPZUW20M40.PL", "ETF"),
                ]
            ]
        )


def test_polish_stock_and_fund_mappings_and_market_separation():
    resolver = SymbolResolver()
    assert resolver.analysis_symbol("PKO.PL") == "PKO.WA"
    assert resolver.analysis_symbol("XTB.WA") == "XTB.WA"
    assert resolver.analysis_symbol("ETFPZUW20M40.PL") == "ETFPZUW20M40.WA"
    service = AnalysisService(Portfolio(), None)
    plan = service.review_plan(Strategy.LONG_TERM, "3-5 years", market="PL")
    assert [r.symbol for r in plan.requests] == ["PKO.WA", "XTB.WA", "ETFPZUW20M40.WA"]
    assert [r.asset_type for r in plan.requests] == ["STOCK", "STOCK", "ETF"]
    assert all(r.owned and len(r.portfolio.positions) == 5 for r in plan.requests)
    assert set(plan.skipped) == {"GE.US", "RHM.DE"}
    assert [
        r.symbol for r in service.review_plan(Strategy.LONG_TERM, "3-5 years", market="EU").requests
    ] == ["RHM.DE"]


async def test_review_dialog_previews_pl_without_engine_calls():
    class Dashboard:
        async def snapshot(self):
            return DashboardSnapshot()

    service = AnalysisService(Portfolio(), None)
    app = DashboardApp(
        Dashboard(),
        analysis_factory=lambda: (
            service,
            AnalysisSettings(quick_model="quick", deep_model="deep"),
        ),
    )
    async with app.run_test(size=(130, 55)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.press("r")
        assert isinstance(app.screen, ReviewModal)
        app.screen.query_one("#review-market", Select).value = "PL"
        await pilot.pause()
        assert len(app.screen.plan.requests) == 3 and not app.screen.running
