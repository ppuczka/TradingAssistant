import json

from textual.widgets import TabbedContent

from trading_assistant.adapters.tradingagents import (
    TradingAgentsSavedReportReader,
    extract_reports,
    normalize,
)
from trading_assistant.application.dashboard import DashboardSnapshot, PortfolioRow
from trading_assistant.domain.analysis import (
    AnalysisRequest,
    PortfolioContext,
    Recommendation,
    SavedAnalysis,
)
from trading_assistant.tui.dashboard import DashboardApp
from trading_assistant.tui.full_analysis import FullAnalysisView


def test_engine_reports_are_preserved_and_invalid_sections_not_invented():
    state = {
        "market_report": "Market analysis",
        "fundamentals_report": "Fundamental analysis",
        "news_report": None,
        "sentiment_report": 12,
        "investment_debate_state": {"history": "Bull and bear debate"},
        "risk_debate_state": {
            "aggressive_history": "Aggressive view",
            "conservative_history": "Conservative view",
        },
        "final_rating": "Hold",
        "final_trade_decision": "Rating: Hold\nReason",
    }
    req = AnalysisRequest(symbol="GE", time_horizon="3-5 years", portfolio=PortfolioContext())
    result = normalize(req, state, "Hold")
    assert result.reports["market"] == "Market analysis"
    assert result.reports["investment_debate"] == "Bull and bear debate"
    assert result.reports["risk_debate"] == "Aggressive view\n\nConservative view"
    assert "news" not in result.reports and "sentiment" not in result.reports
    assert extract_reports({}) == {}


def test_legacy_reports_require_exact_decision_symbol_date_and_unique_match(tmp_path):
    req = AnalysisRequest(symbol="GE", time_horizon="3-5 years", portfolio=PortfolioContext())
    result = Recommendation(
        symbol="GE", owned=False, action="WATCH", engine_rating="Hold", reasoning=["Decision"]
    )
    saved = SavedAnalysis(request=req, recommendation=result)
    path = (
        tmp_path
        / "run"
        / "logs"
        / "GE"
        / "TradingAgentsStrategy_logs"
        / "full_states_log_test.json"
    )
    path.parent.mkdir(parents=True)
    state = {
        "company_of_interest": "GE",
        "trade_date": req.as_of.isoformat(),
        "final_rating": "Hold",
        "final_trade_decision": "Different decision",
        "market_report": "Old report",
    }
    path.write_text(json.dumps(state))
    reader = TradingAgentsSavedReportReader(tmp_path)
    assert reader.reports_for(saved) == {}
    state["final_trade_decision"] = "Decision"
    path.write_text(json.dumps(state))
    assert reader.reports_for(saved) == {"market": "Old report"}
    (path.parent / "full_states_log_duplicate.json").write_text(json.dumps(state))
    assert reader.reports_for(saved) == {}


async def test_full_analysis_is_read_only_tabbed_and_can_open_from_selected_holding():
    result = Recommendation(
        symbol="GE",
        action="HOLD",
        owned=True,
        engine_rating="Hold",
        reasoning=["Decision"],
        reports={"market": "# Market\nTechnical findings", "risk_debate": "Risk discussion"},
    )

    class Dashboard:
        async def snapshot(self):
            return DashboardSnapshot(positions=[PortfolioRow(symbol="GE.US", currency="PLN")])

        async def saved_analysis(self, symbol):
            assert symbol == "GE.US"

            class Saved:
                recommendation = result

            return Saved()

    app = DashboardApp(Dashboard())  # No analysis factory and no API key.
    async with app.run_test(size=(120, 45)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.press("v")
        await app.workers.wait_for_complete()
        assert isinstance(app.screen, FullAnalysisView)
        tabs = app.screen.query_one(TabbedContent)
        tabs.active = "report-market"
        await pilot.pause()
        assert app.screen.result.reports["market"].endswith("Technical findings")
        tabs.active = "report-news"
        await pilot.pause()
        assert len(app.screen.query("TabPane")) == 9
        await pilot.press("escape")
        assert app.screen is app.home_screen
