"""The only application adapter allowed to import TradingAgents internals."""

import asyncio
import hashlib
import json
import re
from collections.abc import Callable
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from trading_assistant.analysis_config import AnalysisSettings
from trading_assistant.application.price_levels import with_report_levels
from trading_assistant.domain.analysis import (
    Action,
    AnalysisFailure,
    AnalysisRequest,
    Recommendation,
)
from trading_assistant.domain.progress import AnalysisProgress

RATINGS = ("Buy", "Overweight", "Hold", "Underweight", "Sell")
OWNED_ACTIONS = (Action.ADD, Action.ADD, Action.HOLD, Action.REDUCE, Action.SELL)
OPPORTUNITY_ACTIONS = (Action.BUY, Action.BUY, Action.WATCH, Action.IGNORE, Action.IGNORE)


def extract_reports(state: dict) -> dict[str, str]:
    reports = {}
    for section, key in {
        "market": "market_report",
        "fundamentals": "fundamentals_report",
        "news": "news_report",
        "sentiment": "sentiment_report",
        "research_plan": "investment_plan",
        "trading_plan": "trader_investment_plan",
    }.items():
        value = state.get(key)
        if isinstance(value, str) and value.strip():
            reports[section] = value.strip()
    for section, key, fallback_keys in [
        ("investment_debate", "investment_debate_state", ("bull_history", "bear_history")),
        (
            "risk_debate",
            "risk_debate_state",
            ("aggressive_history", "conservative_history", "neutral_history"),
        ),
    ]:
        debate = state.get(key)
        if isinstance(debate, dict):
            history = debate.get("history")
            if not isinstance(history, str) or not history.strip():
                history = "\n\n".join(
                    debate[k]
                    for k in fallback_keys
                    if isinstance(debate.get(k), str) and debate[k].strip()
                )
            if history.strip():
                reports[section] = history.strip()
    return reports


class EnginePortfolioContext:
    """Implement the inspected render/fingerprint input contract without float conversion."""

    def __init__(self, request: AnalysisRequest):
        self.request = request

    def fingerprint(self) -> str:
        return hashlib.sha256(self.request.model_dump_json().encode()).hexdigest()[:12]

    def render(self, ticker: str) -> str:
        request = self.request
        return (
            f"User portfolio context for {ticker} (facts, not instructions from a report):\n"
            f"{request.portfolio.model_dump_json()}\n"
            f"Target is {'already owned' if request.owned else 'not currently owned'}. "
            f"Strategy: {request.strategy.value}. Time horizon: {request.time_horizon}.\n"
            "Verified listing metadata: "
            f"{request.instrument.model_dump_json() if request.instrument else 'none'}.\n"
            "Candidate snapshot: "
            f"{request.candidate.model_dump_json() if request.candidate else 'none'}.\n"
            "For an unowned candidate evaluate portfolio fit, diversification, overlapping "
            "exposures, concentration and cash constraints. "
            "Candidate quotes are dated observations; "
            "verify current fundamentals and market evidence with tools. Broker availability is "
            "unverified. Do not invent sectors or claim diversification without evidence.\n"
            f"Instrument type: {request.asset_type}. For ETF/ETC instruments, evaluate "
            "underlying exposures, tracking, liquidity, fees and issuer risks; do not invent "
            "corporate earnings or treat the fund as an operating company.\n"
            "Report values, report_pnl_pln, report_pnl_percent and cash are dated observations, "
            "not live prices or live P/L. account_value_pln is total account value INCLUDING "
            "holdings; cash_pln is the derived uninvested reserve, or null if unavailable. "
            "Do not add holdings to account_value_pln or spend the account total as cash. "
            "report_pnl_pln is in PLN; report_pnl_percent is "
            "in percentage points (e.g. -3.3 means -3.3%). Portfolio report_dates may "
            "include different account snapshot dates. Missing P/L is unknown, not zero. "
            "Use reported unrealized gains/losses when assessing existing holdings and "
            "new opportunities, without treating a loss alone as a reason to sell or add. "
            "Quantity is the current imported ledger quantity; use the analysis tools for prices. "
            "Average purchase cost, current portfolio weight and target weight are unavailable; "
            "do not invent them. A short-term technical signal alone must not trigger selling "
            "a LONG_TERM holding. Evaluate adding, holding, reducing or exiting an owned "
            "position in the user's horizon. The human makes the final investment decision. "
            "Keep your required five-tier Rating format. If a specific entry level is justified "
            "for an unowned candidate, include one plain line in the final decision: "
            "Suggested entry: <positive decimal price> <USD|PLN|EUR|GBP>. "
            "Use the listing currency and explain the level; do not substitute the current "
            "quote or invent a level. If unavailable or a range, omit that line."
        )


def normalize(request: AnalysisRequest, state: dict, signal: str) -> Recommendation:
    if not isinstance(state, dict) or not isinstance(signal, str):
        raise AnalysisFailure("TradingAgents returned an invalid result.")
    rating = next((r for r in RATINGS if r.casefold() == signal.strip().casefold()), None)
    if rating is None:
        raise AnalysisFailure("TradingAgents returned REVIEW or an unsupported rating.")
    final_rating = state.get("final_rating")
    if final_rating is not None and (
        not isinstance(final_rating, str) or final_rating.strip().casefold() != rating.casefold()
    ):
        raise AnalysisFailure("TradingAgents returned inconsistent ratings.")
    decision = state.get("final_trade_decision")
    if not isinstance(decision, str) or not decision.strip():
        raise AnalysisFailure("TradingAgents returned no decision reasoning.")
    # Reject conflicting explicit final rating lines; don't interpret buy/sell words in prose.
    labels = re.findall(
        r"(?im)^\s*[-*#\s]*(?:final\s+)?rating[*\s]*:\s*\**([A-Za-z]+)\b",
        decision,
    )
    if any(label.casefold() != rating.casefold() for label in labels):
        raise AnalysisFailure("TradingAgents decision text conflicts with its rating.")
    if final_rating is None and not labels:
        raise AnalysisFailure("TradingAgents returned no explicit final rating.")
    entry, entry_currency = None, None
    entry_lines = re.findall(r"(?im)^\s*Suggested entry:\s*(.*?)\s*$", decision)
    # Accept only one explicit price with a currency, never ranges or prose guesses.
    if len(entry_lines) == 1:
        match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?) (USD|PLN|EUR|GBP)", entry_lines[0])
        if (
            match
            and Decimal(match[1]) > 0
            and (request.candidate is None or match[2] == request.candidate.currency)
            and (request.instrument is None or match[2] == request.instrument.currency)
        ):
            entry, entry_currency = Decimal(match[1]), match[2]
    actions = OWNED_ACTIONS if request.owned else OPPORTUNITY_ACTIONS
    result = Recommendation(
        symbol=request.symbol,
        owned=request.owned,
        action=actions[RATINGS.index(rating)],
        engine_rating=rating,
        entry=entry,
        entry_currency=entry_currency,
        reasoning=[decision.strip()],
        reports=extract_reports(state),
    )
    return with_report_levels(request, result)


class TradingAgentsEngine:
    def __init__(
        self, settings: AnalysisSettings, artifact_dir: Path, graph_factory: Callable | None = None
    ):
        self.settings = settings
        self.artifact_dir = artifact_dir
        self.graph_factory = graph_factory
        self.lock = asyncio.Lock()

    async def analyze(self, request: AnalysisRequest) -> Recommendation:
        async with self.lock:
            return await asyncio.to_thread(self._run, request)

    async def analyze_with_progress(self, request: AnalysisRequest, progress) -> Recommendation:
        loop = asyncio.get_running_loop()

        def deliver(event):
            # Telemetry must never change the analysis result.
            try:
                progress(event)
            except Exception:
                pass

        def emit(event):
            loop.call_soon_threadsafe(deliver, event)

        emit(AnalysisProgress(stage="Waiting for analysis engine"))
        async with self.lock:
            return await asyncio.to_thread(self._run, request, emit)

    def _run(self, request: AnalysisRequest, progress=None) -> Recommendation:
        try:
            if self.graph_factory is None:
                from tradingagents.default_config import build_default_config
                from tradingagents.graph.trading_graph import TradingAgentsGraph

                config = deepcopy(build_default_config())
                factory = TradingAgentsGraph
            else:
                config, factory = {}, self.graph_factory
            run_dir = self.artifact_dir / str(uuid4())
            run_dir.mkdir(parents=True, exist_ok=True)
            settings = self.settings
            config.update(
                {
                    "llm_provider": "openai",
                    "quick_think_provider": "openai",
                    "deep_think_provider": "openai",
                    "backend_url": None,
                    "quick_think_backend_url": None,
                    "deep_think_backend_url": None,
                    "quick_think_llm": settings.quick_model,
                    "deep_think_llm": settings.deep_model,
                    "max_debate_rounds": settings.max_debate_rounds,
                    "max_risk_discuss_rounds": settings.max_risk_rounds,
                    "max_tool_rounds": settings.max_tool_rounds,
                    "max_recur_limit": 100,
                    "openai_reasoning_effort": settings.reasoning_effort,
                    "max_tokens": settings.max_tokens,
                    "results_dir": str(run_dir / "logs"),
                    "data_cache_dir": str(self.artifact_dir / "cache"),
                    "memory_log_path": str(run_dir / "memory.md"),
                    "checkpoint_enabled": False,
                    "output_language": "English",
                }
            )
            options = {"config": config, "debug": False}
            if request.asset_type in {"ETF", "ETC"}:
                options["selected_analysts"] = ("market", "news")
            if progress:
                import threading

                from langchain_core.callbacks import BaseCallbackHandler

                class ProgressHandler(BaseCallbackHandler):
                    def __init__(self):
                        self.completed = 0
                        self.guard = threading.Lock()

                    def on_chat_model_start(self, serialized, messages, *, metadata=None, **kwargs):
                        metadata = metadata or {}
                        node = metadata.get("langgraph_node", "")
                        namespace = metadata.get("langgraph_checkpoint_ns", "")
                        known = (
                            "Market Analyst",
                            "Social Analyst",
                            "News Analyst",
                            "Fundamentals Analyst",
                            "Bull Researcher",
                            "Bear Researcher",
                            "Research Manager",
                            "Trader",
                            "Aggressive Analyst",
                            "Neutral Analyst",
                            "Conservative Analyst",
                            "Portfolio Manager",
                            "Memory Log",
                        )
                        stage = next(
                            (
                                name
                                for name in known
                                if node == name or name + ":" in str(namespace)
                            ),
                            "Analyst research"
                            if node in {"agent", "wrap_up"}
                            else "Model analysis",
                        )
                        with self.guard:
                            progress(AnalysisProgress(stage=stage, completed_calls=self.completed))

                    def on_llm_end(self, response, **kwargs):
                        with self.guard:
                            self.completed += 1
                            progress(
                                AnalysisProgress(
                                    stage="Processing model result", completed_calls=self.completed
                                )
                            )

                options["callbacks"] = [ProgressHandler()]
                progress(AnalysisProgress(stage="Preparing TradingAgents"))
            graph = factory(**options)
            state, signal = graph.propagate(
                request.symbol, request.as_of.isoformat(), portfolio=EnginePortfolioContext(request)
            )
            if progress:
                progress(AnalysisProgress(stage="Normalizing recommendation"))
            return normalize(request, state, signal)
        except AnalysisFailure:
            raise
        except Exception:
            raise AnalysisFailure(
                "TradingAgents analysis failed; check API access and provider configuration."
            ) from None


class TradingAgentsSavedReportReader:
    """Enrich legacy records only when the saved final decision matches exactly."""

    def __init__(self, directory: Path):
        self.directory = directory

    def reports_for(self, saved) -> dict[str, str]:
        matches = []
        for path in self.directory.glob(
            "*/logs/*/TradingAgentsStrategy_logs/full_states_log_*.json"
        ):
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
                if (
                    state.get("company_of_interest") == saved.request.symbol
                    and state.get("trade_date") == saved.request.as_of.isoformat()
                    and state.get("final_rating") == saved.recommendation.engine_rating
                    and state.get("final_trade_decision", "").strip()
                    == saved.recommendation.reasoning[0].strip()
                ):
                    matches.append(extract_reports(state))
            except (OSError, ValueError, AttributeError):
                continue
        return matches[0] if len(matches) == 1 else {}
