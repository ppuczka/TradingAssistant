import asyncio
import threading

from textual.widgets import ProgressBar, Static

from trading_assistant.adapters.tradingagents import TradingAgentsEngine
from trading_assistant.analysis_config import AnalysisSettings
from trading_assistant.application.analysis import AnalysisService
from trading_assistant.application.dashboard import EmptyDashboardService
from trading_assistant.domain.analysis import AnalysisRequest, PortfolioContext
from trading_assistant.domain.progress import AnalysisProgress
from trading_assistant.tui.analysis_progress import AnalysisActivity
from trading_assistant.tui.dashboard import DashboardApp


def request():
    return AnalysisRequest(symbol="GE", time_horizon="3 years", portfolio=PortfolioContext())


async def test_real_adapter_callback_bridge_stages_counts_and_main_loop_delivery(tmp_path):
    thread_id = threading.get_ident()
    events, callback_threads = [], []

    class Graph:
        def __init__(self, **kwargs):
            self.handler = kwargs["callbacks"][0]

        def propagate(self, ticker, date, *, portfolio):
            self.handler.on_chat_model_start(
                {},
                [],
                metadata={
                    "langgraph_node": "agent",
                    "langgraph_checkpoint_ns": "News Analyst:123|agent:456",
                },
            )
            self.handler.on_llm_end(None)
            self.handler.on_chat_model_start({}, [], metadata={"langgraph_node": "Trader"})
            self.handler.on_llm_end(None)
            self.handler.on_chat_model_start({}, [], metadata={"langgraph_node": "private-token"})
            return {"final_rating": "Buy", "final_trade_decision": "Rating: Buy\nReason"}, "Buy"

    def progress(event):
        events.append(event)
        callback_threads.append(threading.get_ident())

    engine = TradingAgentsEngine(
        AnalysisSettings(quick_model="quick", deep_model="deep"), tmp_path, graph_factory=Graph
    )
    result = await engine.analyze_with_progress(request(), progress)
    await asyncio.sleep(0)
    assert result.action == "BUY"
    assert "News Analyst" in [e.stage for e in events]
    assert "Trader" in [e.stage for e in events]
    assert max(e.completed_calls for e in events) == 2
    assert set(callback_threads) == {thread_id}
    assert "private-token" not in str(events)
    assert events[-1].stage == "Normalizing recommendation"

    def broken_progress(event):
        raise RuntimeError("UI observer failed")

    assert (await engine.analyze_with_progress(request(), broken_progress)).action == "BUY"


async def test_activity_indeterminate_during_analysis_and_exact_finish_reset():
    app = DashboardApp(EmptyDashboardService())
    async with app.run_test():
        await app.workers.wait_for_complete()
        activity = AnalysisActivity()
        await app.home_screen.mount(activity)
        activity.start()
        bar = activity.query_one(ProgressBar)
        assert bar.total is None
        activity.observe(AnalysisProgress(stage="Trader", completed_calls=2))
        label = str(activity.query_one(Static).render())
        assert "Trader" in label and "2 model calls completed" in label and "elapsed" in label
        activity.observe(AnalysisProgress(stage="Saving recommendation"))
        assert activity.completed_calls == 2
        activity.finish()
        assert bar.total == 1 and bar.progress == 1
        assert "Finished" in str(activity.query_one(Static).render())
        activity.start()
        assert activity.completed_calls == 0 and bar.total is None
        activity.finish(False)
        assert bar.progress == 0
        assert "Failed" in str(activity.query_one(Static).render())


async def test_service_progress_fallback_saves_without_requiring_engine_extension():
    events, saved = [], []

    class Engine:
        async def analyze(self, req):
            from trading_assistant.domain.analysis import Recommendation

            return Recommendation(
                symbol=req.symbol,
                owned=False,
                action="WATCH",
                engine_rating="Hold",
                reasoning=["Synthetic"],
            )

    class Store:
        def save(self, req, result):
            saved.append(result)

    service = AnalysisService(None, Engine(), Store())
    result = await service.analyze_with_progress(request(), events.append)
    assert result.action == "WATCH" and saved == [result]
    assert [e.stage for e in events] == ["Analyzing", "Saving recommendation"]
