import pytest
from textual.widgets import Select

from trading_assistant.adapters.tradingagents import TradingAgentsEngine
from trading_assistant.analysis_config import AnalysisSettings
from trading_assistant.application.analysis import AnalysisService
from trading_assistant.application.dashboard import DashboardSnapshot
from trading_assistant.application.market import SymbolResolver
from trading_assistant.domain.analysis import Strategy
from trading_assistant.domain.market import MarketDataError
from trading_assistant.domain.portfolio import Holding, PortfolioState
from trading_assistant.tui.dashboard import DashboardApp
from trading_assistant.tui.review_modal import ReviewModal


class Portfolio:
    def portfolio(self):
        return PortfolioState(
            holdings=[
                Holding(symbol=s, name="Fund or company", asset_type=kind, quantity="1")
                for s, kind in [
                    ("GE.US", "STOCK"),
                    ("RHM.DE", "STOCK"),
                    ("AE5A.DE", "ETF"),
                    ("COPA.UK", "ETC"),
                    ("PKO.PL", "STOCK"),
                    ("XTB.PL", "STOCK"),
                ]
            ]
        )


def test_verified_eu_mappings_and_polish_exclusion():
    resolver = SymbolResolver()
    assert resolver.analysis_symbol("RHM.DE") == "RHM.DE"
    assert resolver.analysis_symbol("COPA.UK") == "COPA.L"
    assert resolver.analysis_symbol("COPA.L") == "COPA.L"
    assert resolver.analysis_symbol("PKO.PL") == "PKO.WA"
    assert resolver.analysis_symbol("PKO.WA") == "PKO.WA"
    for symbol in ["UNKNOWN.PL", "UNKNOWN.WA", "UNKNOWN.DE"]:
        with pytest.raises(MarketDataError):
            resolver.analysis_symbol(symbol)


def test_eu_review_includes_funds_excludes_pl_and_keeps_full_book():
    service = AnalysisService(Portfolio(), None)
    plan = service.review_plan(Strategy.LONG_TERM, "3-5 years", market="EU")
    assert [r.symbol for r in plan.requests] == ["RHM.DE", "AE5A.DE", "COPA.L"]
    assert [r.asset_type for r in plan.requests] == ["STOCK", "ETF", "ETC"]
    assert all(r.owned and len(r.portfolio.positions) == 6 for r in plan.requests)
    assert set(plan.skipped) == {"GE.US", "PKO.PL", "XTB.PL"}
    assert [r.symbol for r in service.review_plan(Strategy.LONG_TERM, "3-5 years").requests] == [
        "GE"
    ]


@pytest.mark.parametrize("symbol", ["AE5A.DE", "COPA.UK"])
async def test_fund_analysis_uses_market_and_news_without_corporate_fundamentals(symbol, tmp_path):
    seen = {}

    class Graph:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def propagate(self, ticker, date, *, portfolio):
            seen["context"] = portfolio.render(ticker)
            return {
                "final_rating": "Hold",
                "final_trade_decision": "Rating: Hold\nFund reasoning",
            }, "Hold"

    req = AnalysisService(Portfolio(), None).request(symbol, Strategy.LONG_TERM, "3-5 years")
    engine = TradingAgentsEngine(
        AnalysisSettings(quick_model="quick", deep_model="deep"), tmp_path, Graph
    )
    result = await engine.analyze(req)
    assert result.action == "HOLD"
    assert seen["selected_analysts"] == ("market", "news")
    assert "do not invent corporate earnings" in seen["context"]


async def test_review_market_selector_previews_eu_without_paid_calls():
    class Dashboard:
        async def snapshot(self):
            return DashboardSnapshot()

    analysis = AnalysisService(Portfolio(), None)
    app = DashboardApp(
        Dashboard(),
        analysis_factory=lambda: (
            analysis,
            AnalysisSettings(quick_model="quick", deep_model="deep"),
        ),
    )
    async with app.run_test(size=(130, 55)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.press("r")
        assert isinstance(app.screen, ReviewModal)
        app.screen.query_one("#review-market", Select).value = "EU"
        await pilot.pause()
        assert [r.symbol for r in app.screen.plan.requests] == ["RHM.DE", "AE5A.DE", "COPA.L"]
        assert not app.screen.running
