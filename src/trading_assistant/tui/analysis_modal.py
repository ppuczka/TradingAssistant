"""Human-triggered analysis of a captured selected symbol."""

import asyncio

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static

from trading_assistant.analysis_config import AnalysisSettings
from trading_assistant.application.analysis import AnalysisService
from trading_assistant.application.progress import analyze_with_progress
from trading_assistant.domain.analysis import AnalysisFailure, Strategy
from trading_assistant.tui.analysis_progress import AnalysisActivity
from trading_assistant.tui.full_analysis import FullAnalysisView
from trading_assistant.tui.rating_legend import RatingLegend

STRATEGY_DESCRIPTIONS = {
    Strategy.LONG_TERM.value: (
        "LONG_TERM • Years: business quality, earnings, valuation and long-term prospects. "
        "Short-term weakness alone should not justify selling."
    ),
    Strategy.POSITION.value: (
        "POSITION • Weeks to months: fundamentals, medium-term trends and catalysts."
    ),
    Strategy.TRADE.value: (
        "TRADE • Short term: technical signals, momentum, timing and nearer-term risks."
    ),
}


class AnalysisModal(ModalScreen):
    DEFAULT_CSS = """
    AnalysisModal { align: center middle; background: $background 70%; }
    #analysis-dialog { width: 90%; max-width: 110; height: 85%; border: round #35556e;
                       background: #101923; padding: 1 2; }
    #analysis-dialog Static { height: auto; margin-bottom: 1; }
    #analysis-dialog Input, #analysis-dialog Select { margin-bottom: 1; }
    #analysis-buttons { height: 3; }
    #analysis-result { margin-top: 1; }
    """
    BINDINGS = [Binding("escape", "close", "Close", priority=True)]

    def __init__(
        self,
        symbol: str,
        service: AnalysisService,
        settings: AnalysisSettings,
        *,
        typed: bool = False,
        initial_strategy: Strategy = Strategy.LONG_TERM,
        initial_horizon: str | None = None,
        saved_result=None,
    ):
        super().__init__()
        self.symbol, self.service, self.settings = symbol, service, settings
        self.running = False
        self.typed = typed
        self.initial_strategy = initial_strategy
        self.initial_horizon = initial_horizon or settings.default_horizon
        self.report_result = saved_result

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="analysis-dialog"):
            yield Static(f"ANALYZE {self.symbol or 'SYMBOL'}", classes="heading", markup=False)
            if self.typed:
                yield Input(
                    value=self.symbol,
                    placeholder="GE, AAPL.US, PCO.PL, SPY, RHM.DE…",
                    id="analysis-symbol",
                )
                yield Static(
                    "Yahoo listing lookup verifies stock/ETF identity before analysis. "
                    "Current portfolio context and today's date are used; broker availability "
                    "is unverified.",
                    markup=False,
                )
            yield Static(
                f"Quick: {self.settings.quick_model} • Deep: {self.settings.deep_model}\n"
                "Uses the combined imported portfolio. OpenAI API calls are billed. "
                "No orders are executed.",
                markup=False,
            )
            yield RatingLegend()
            yield Static("Strategy", markup=False)
            yield Select(
                [(s.value, s.value) for s in Strategy],
                value=self.initial_strategy.value,
                allow_blank=False,
                id="analysis-strategy",
            )
            yield Static(
                STRATEGY_DESCRIPTIONS[self.initial_strategy.value],
                id="analysis-strategy-description",
                markup=False,
            )
            yield Static(
                "Set the horizon to your intended duration, e.g. 3–5 years or 3–6 months. "
                "Strategy guides the same AI workflow; it does not automatically change "
                "agents, models or data lookback, and is not a deterministic risk rule.",
                markup=False,
            )
            yield Static("Investment horizon", markup=False)
            yield Input(value=self.initial_horizon, id="analysis-horizon")
            with Horizontal(id="analysis-buttons"):
                yield Button("Run analysis", id="analysis-run", variant="primary")
                yield Button(
                    "Full analysis", id="analysis-full", disabled=self.report_result is None
                )
                yield Button("Close", id="analysis-close")
            yield AnalysisActivity(id="analysis-activity")
            yield Static(
                "Ready. Select strategy/horizon, then run analysis.",
                id="analysis-result",
                markup=False,
            )

    @work()
    async def on_mount(self) -> None:
        loader = getattr(self.service, "saved", None)
        if loader and self.symbol and self.report_result is None:
            try:
                saved = await asyncio.to_thread(loader, self.symbol)
                if saved and not self.running and self.report_result is None:
                    self.report_result = saved.recommendation
                    self.query_one("#analysis-full", Button).disabled = False
                    self.query_one("#analysis-result", Static).update(
                        Text(
                            f"Saved {saved.recommendation.action.value} • "
                            f"{saved.recommendation.analyzed_at.isoformat()}\n"
                            "Open Full analysis to read the saved reports without rerunning."
                        )
                    )
            except Exception:
                pass

    @on(Button.Pressed, "#analysis-full")
    def full_analysis(self) -> None:
        if self.report_result and not self.running:
            self.app.push_screen(FullAnalysisView(self.report_result))

    @on(Select.Changed, "#analysis-strategy")
    def strategy_changed(self, event: Select.Changed) -> None:
        if event.value in STRATEGY_DESCRIPTIONS:
            self.query_one("#analysis-strategy-description", Static).update(
                STRATEGY_DESCRIPTIONS[event.value]
            )

    @on(Button.Pressed, "#analysis-run")
    @work()
    async def run_analysis(self) -> None:
        if self.running:
            return
        horizon = self.query_one("#analysis-horizon", Input).value.strip()
        if not horizon:
            self.query_one("#analysis-result", Static).update("Provide an investment horizon.")
            return
        symbol = (
            self.query_one("#analysis-symbol", Input).value.strip() if self.typed else self.symbol
        )
        if not symbol:
            self.query_one("#analysis-result", Static).update("Provide a ticker symbol.")
            return
        self.running = True
        if self.typed:
            self.query_one("#analysis-symbol", Input).disabled = True
        self.query_one("#analysis-full", Button).disabled = True
        self.query_one("#analysis-run", Button).disabled = True
        self.query_one("#analysis-strategy", Select).disabled = True
        self.query_one("#analysis-horizon", Input).disabled = True
        result_widget = self.query_one("#analysis-result", Static)
        result_widget.update("TradingAgents is analyzing… This can take several minutes.")
        activity = self.query_one(AnalysisActivity)
        activity.start()
        success = False
        try:
            strategy = Strategy(self.query_one("#analysis-strategy", Select).value)
            request = (
                await self.service.prepare_request(symbol, strategy, horizon)
                if self.typed
                else self.service.request(symbol, strategy, horizon)
            )
            result = await analyze_with_progress(self.service, request, activity.observe)
            success = True
            self.report_result = result
            result_widget.update(
                Text(
                    f"{result.symbol}: {result.action.value}\n"
                    f"Analyzed {result.analyzed_at.isoformat()} • {strategy.value} • {horizon}\n\n"
                    + "\n\n".join(result.reasoning)
                    + "\n\nSaved locally. Close to see the updated AI column. "
                    "The final investment decision belongs to you."
                )
            )
        except AnalysisFailure as exc:
            result_widget.update(Text(f"Analysis failed: {exc}"))
        except Exception:
            result_widget.update(
                "Analysis failed. Check configuration and API access; no result substituted."
            )
        finally:
            activity.finish(success)
            self.running = False
            if self.typed:
                self.query_one("#analysis-symbol", Input).disabled = False
            self.query_one("#analysis-full", Button).disabled = self.report_result is None
            self.query_one("#analysis-run", Button).disabled = False
            self.query_one("#analysis-strategy", Select).disabled = False
            self.query_one("#analysis-horizon", Input).disabled = False

    @on(Button.Pressed, "#analysis-close")
    def close_button(self) -> None:
        self.action_close()

    def action_close(self) -> None:
        if self.running:
            self.notify("Analysis is still running.")
            return
        self.dismiss()
        self.app.refresh_active_screen()
