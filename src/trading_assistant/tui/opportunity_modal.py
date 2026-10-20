"""Explicitly started, bounded TradingAgents review of Yahoo candidates."""

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, ProgressBar, Select, Static

from trading_assistant.application.review import PortfolioReviewService, ordered_opportunities
from trading_assistant.domain.analysis import Strategy
from trading_assistant.tui.analysis_modal import STRATEGY_DESCRIPTIONS
from trading_assistant.tui.analysis_progress import AnalysisActivity
from trading_assistant.tui.full_analysis import FullAnalysisView
from trading_assistant.tui.rating_legend import RatingLegend


class OpportunityModal(ModalScreen):
    DEFAULT_CSS = """
    OpportunityModal { align: center middle; background: $background 70%; }
    #opportunity-dialog { width: 95%; height: 90%; border: round #35556e;
                          background: #101923; padding: 1 2; }
    #opportunity-dialog Static { height: auto; margin-bottom: 1; }
    #opportunity-dialog Input, #opportunity-dialog Select { margin-bottom: 1; }
    #opportunity-buttons { height: 3; }
    #opportunity-results { height: 12; min-height: 4; }
    """
    BINDINGS = [Binding("escape", "close", "Close", priority=True)]

    def __init__(self, candidates, analysis, settings, *, selected: bool = False):
        super().__init__()
        self.candidates, self.analysis, self.settings = candidates, analysis, settings
        self.selected = selected
        self.running = False
        self.stop_requested = False
        self.items = {}

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="opportunity-dialog"):
            yield Static("AI OPPORTUNITY REVIEW", classes="heading")
            yield RatingLegend()
            yield Static(
                (
                    f"Selected candidates: {', '.join(c.symbol for c in self.candidates)}. "
                    "All eligible selected candidates will be analyzed. "
                    if self.selected
                    else f"{len(self.candidates)} fetched candidates. "
                    "Analyzes the first eligible N "
                    "in Yahoo market-cap order (US before PL for a combined fetch). "
                )
                + "Each candidate gets a full TradingAgents analysis with your portfolio context. "
                "Results sort by action and engine rating: Buy > Overweight > Hold > "
                "Underweight > Sell. Rating is recommendation strength, not confidence. "
                "XTB availability remains unverified.\n"
                f"Paid OpenAI calls • Quick: {self.settings.quick_model} • "
                f"Deep: {self.settings.deep_model}. No orders are executed.",
                markup=False,
            )
            yield Static("Selected analyses" if self.selected else "Maximum analyses (1–10)")
            yield Input(
                value=str(len(self.candidates)) if self.selected else "3",
                type="integer",
                id="opportunity-limit",
                disabled=self.selected,
            )
            yield Select(
                [(s.value, s.value) for s in Strategy],
                value="LONG_TERM",
                allow_blank=False,
                id="opportunity-strategy",
            )
            yield Static(
                STRATEGY_DESCRIPTIONS["LONG_TERM"],
                id="opportunity-strategy-description",
                markup=False,
            )
            yield Input(value=self.settings.default_horizon, id="opportunity-horizon")
            with Horizontal(id="opportunity-buttons"):
                yield Button("Start AI review", id="opportunity-run", variant="primary")
                yield Button("Stop after current", id="opportunity-stop", disabled=True)
                yield Button("Full report", id="opportunity-report", disabled=True)
                yield Button("Close", id="opportunity-close")
            yield Static(
                "Ready. No model calls until Start AI review.",
                id="opportunity-status",
                markup=False,
            )
            yield ProgressBar(total=1, show_eta=False, id="opportunity-batch-progress")
            yield AnalysisActivity(id="opportunity-activity")
            yield DataTable(id="opportunity-results", cursor_type="row")

    def on_mount(self):
        self.query_one(DataTable).add_columns(
            "SYMBOL", "STATUS", "AI", "ENGINE RATING", "REASON / ERROR"
        )

    @on(Select.Changed, "#opportunity-strategy")
    def strategy_changed(self, event):
        if event.value in STRATEGY_DESCRIPTIONS:
            self.query_one("#opportunity-strategy-description", Static).update(
                STRATEGY_DESCRIPTIONS[event.value]
            )

    def render_items(self):
        table = self.query_one(DataTable)
        selected = str(table.get_row_at(table.cursor_row)[0]) if table.row_count else None
        table.clear()
        items = ordered_opportunities(self.items.values())
        for index, item in enumerate(items):
            result = item.recommendation
            table.add_row(
                item.symbol,
                item.status,
                result.action.value if result else "—",
                result.engine_rating if result else "—",
                " ".join(result.reasoning) if result else item.error or "",
            )
            if item.symbol == selected:
                table.move_cursor(row=index)
        self.highlighted()

    @on(DataTable.RowHighlighted, "#opportunity-results")
    def highlighted(self):
        table = self.query_one(DataTable)
        item = (
            self.items.get(str(table.get_row_at(table.cursor_row)[0])) if table.row_count else None
        )
        self.query_one("#opportunity-report", Button).disabled = not (item and item.recommendation)

    @on(Button.Pressed, "#opportunity-run")
    @work()
    async def run_review(self):
        if self.running:
            return
        status = self.query_one("#opportunity-status", Static)
        try:
            limit = (
                len(self.candidates)
                if self.selected
                else int(self.query_one("#opportunity-limit", Input).value)
            )
            plan = self.analysis.opportunity_plan(
                self.candidates,
                Strategy(self.query_one("#opportunity-strategy", Select).value),
                self.query_one("#opportunity-horizon", Input).value,
                limit,
            )
        except ValueError:
            status.update("Check count (1–10), strategy and a nonempty horizon.")
            return
        except Exception:
            status.update("Unable to read portfolio and prepare candidate analysis.")
            return
        if not plan.requests:
            status.update("No eligible candidates. Fetch fresh candidates and retry.")
            return
        controls = [
            self.query_one("#opportunity-run", Button),
            self.query_one("#opportunity-limit", Input),
            self.query_one("#opportunity-strategy", Select),
            self.query_one("#opportunity-horizon", Input),
        ]
        self.running, self.stop_requested = True, False
        self.items = {}
        self.render_items()
        for control in controls:
            control.disabled = True
        self.query_one("#opportunity-stop", Button).disabled = False

        activity = self.query_one(AnalysisActivity)
        batch_bar = self.query_one("#opportunity-batch-progress", ProgressBar)
        batch_bar.update(total=len(plan.requests), progress=0)

        def progress(index, total, item):
            if item.status == "RUNNING":
                activity.start()
            elif item.status != "STOPPED":
                batch_bar.update(progress=index)
                activity.finish(item.status == "COMPLETED")
            self.items[item.symbol] = item
            self.render_items()
            status.update(f"{index}/{total} • {item.symbol} • {item.status}")

        try:
            result = await PortfolioReviewService(self.analysis).run(
                plan,
                progress=progress,
                stop_requested=lambda: self.stop_requested,
                stage_progress=activity.observe,
            )
            completed = sum(i.status == "COMPLETED" for i in result.items)
            failed = sum(i.status == "FAILED" for i in result.items)
            stopped = sum(i.status == "STOPPED" for i in result.items)
            status.update(
                f"Finished: {completed} saved, {failed} failed, {stopped} stopped. "
                f"Skipped stale / owned / invalid: {', '.join(plan.skipped) or 'none'}. "
                "Select a completed row for its full report."
            )
        except Exception:
            status.update("Review interrupted. Completed results remain saved.")
        finally:
            self.running = False
            for control in controls:
                control.disabled = False
            self.query_one("#opportunity-limit", Input).disabled = self.selected
            self.query_one("#opportunity-stop", Button).disabled = True

    @on(Button.Pressed, "#opportunity-stop")
    def stop(self):
        self.stop_requested = True
        self.query_one("#opportunity-stop", Button).disabled = True
        self.query_one("#opportunity-status", Static).update(
            "Stop requested. Current analysis will finish and save."
        )

    @on(Button.Pressed, "#opportunity-report")
    def report(self):
        table = self.query_one(DataTable)
        item = (
            self.items.get(str(table.get_row_at(table.cursor_row)[0])) if table.row_count else None
        )
        if item and item.recommendation:
            self.app.push_screen(FullAnalysisView(item.recommendation))

    @on(Button.Pressed, "#opportunity-close")
    def close_button(self):
        self.action_close()

    def action_close(self):
        if self.running:
            self.notify("Stop after current analysis, then close when it finishes.")
            return
        self.app.pop_screen()
