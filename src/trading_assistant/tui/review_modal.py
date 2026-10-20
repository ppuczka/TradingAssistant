"""Explicitly started portfolio review with progress and per-holding reports."""

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, ProgressBar, Select, Static

from trading_assistant.analysis_config import AnalysisSettings
from trading_assistant.application.analysis import AnalysisService
from trading_assistant.application.review import PortfolioReviewService
from trading_assistant.domain.analysis import AnalysisFailure, Strategy
from trading_assistant.domain.review import ReviewItem
from trading_assistant.tui.analysis_modal import STRATEGY_DESCRIPTIONS
from trading_assistant.tui.analysis_progress import AnalysisActivity
from trading_assistant.tui.full_analysis import FullAnalysisView
from trading_assistant.tui.rating_legend import RatingLegend


class ReviewModal(ModalScreen):
    DEFAULT_CSS = """
    ReviewModal { align: center middle; background: $background 70%; }
    #review-dialog { width: 95%; height: 90%; border: round #35556e;
                     background: #101923; padding: 1 2; }
    #review-dialog Static { height: auto; margin-bottom: 1; }
    #review-dialog Input, #review-dialog Select { margin-bottom: 1; }
    #review-buttons { height: 3; }
    #review-table { height: auto; min-height: 4; max-height: 12; margin-top: 1; }
    """
    BINDINGS = [Binding("escape", "close", "Close", priority=True)]

    def __init__(self, analysis: AnalysisService, settings: AnalysisSettings):
        super().__init__()
        self.analysis, self.settings = analysis, settings
        self.plan = analysis.review_plan(Strategy.LONG_TERM, settings.default_horizon)
        self.items: dict[str, ReviewItem] = {}
        self.running = False
        self.stop_requested = False

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="review-dialog"):
            yield Static("PORTFOLIO REVIEW", classes="heading")
            yield RatingLegend()
            yield Select(
                [
                    ("US", "US"),
                    ("EU • Germany / UK • excludes Poland", "EU"),
                    ("PL • Poland", "PL"),
                ],
                value="US",
                allow_blank=False,
                id="review-market",
            )
            yield Static(
                f"Quick: {self.settings.quick_model} • "
                f"Deep: {self.settings.deep_model}\n"
                "Runs sequentially. OpenAI API calls are billed. "
                "Uses one portfolio snapshot for the batch; no orders are executed.",
                markup=False,
            )
            yield Select(
                [(s.value, s.value) for s in Strategy],
                value="LONG_TERM",
                allow_blank=False,
                id="review-strategy",
            )
            yield Static(
                STRATEGY_DESCRIPTIONS["LONG_TERM"], id="review-strategy-description", markup=False
            )
            yield Input(
                value=self.settings.default_horizon,
                placeholder="Investment horizon",
                id="review-horizon",
            )
            with Horizontal(id="review-buttons"):
                yield Button("Start review", id="review-run", variant="primary")
                yield Button("Stop after current", id="review-stop", disabled=True)
                yield Button("Full analysis", id="review-report", disabled=True)
                yield Button("Close", id="review-close")
            yield Static(
                "Ready. Start review to make paid model calls.", id="review-status", markup=False
            )
            yield ProgressBar(total=1, show_eta=False, id="review-batch-progress")
            yield AnalysisActivity(id="review-activity")
            yield DataTable(id="review-table", cursor_type="row")
            yield Static(
                f"Skipped outside market / unmapped: {', '.join(self.plan.skipped) or 'none'}",
                id="review-skipped",
                markup=False,
            )

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("SYMBOL", "STATUS", "AI", "DETAIL")
        self.render_items()

    def render_items(self) -> None:
        table = self.query_one(DataTable)
        cursor = table.cursor_row
        table.clear()
        for request in self.plan.requests:
            item = self.items.get(request.symbol)
            table.add_row(
                request.symbol,
                item.status if item else "QUEUED",
                item.recommendation.action.value if item and item.recommendation else "—",
                item.error or "" if item else "",
            )
        if table.row_count:
            table.move_cursor(row=min(cursor, table.row_count - 1))
        self.update_report_button()

    def update_report_button(self) -> None:
        table = self.query_one(DataTable)
        item = (
            self.items.get(str(table.get_row_at(table.cursor_row)[0])) if table.row_count else None
        )
        self.query_one("#review-report", Button).disabled = not (item and item.recommendation)

    @on(DataTable.RowHighlighted, "#review-table")
    def highlighted(self) -> None:
        self.update_report_button()

    @on(Select.Changed, "#review-strategy")
    def strategy_changed(self, event: Select.Changed) -> None:
        if event.value in STRATEGY_DESCRIPTIONS:
            self.query_one("#review-strategy-description", Static).update(
                STRATEGY_DESCRIPTIONS[event.value]
            )

    @on(Select.Changed, "#review-market")
    def market_changed(self, event: Select.Changed) -> None:
        if event.value in {"US", "EU", "PL"} and not self.running:
            self.plan = self.analysis.review_plan(
                Strategy(self.query_one("#review-strategy", Select).value),
                self.query_one("#review-horizon", Input).value.strip()
                or self.settings.default_horizon,
                market=event.value,
            )
            self.items = {}
            self.render_items()
            self.query_one("#review-status", Static).update(
                f"Ready: {len(self.plan.requests)} {event.value} holdings. "
                "Start review to make paid calls."
            )
            self.query_one("#review-skipped", Static).update(
                f"Skipped outside market / unmapped: {', '.join(self.plan.skipped) or 'none'}"
            )

    @on(Button.Pressed, "#review-run")
    @work()
    async def run_review(self) -> None:
        if self.running:
            return
        horizon = self.query_one("#review-horizon", Input).value.strip()
        status = self.query_one("#review-status", Static)
        if not horizon:
            status.update("Provide an investment horizon.")
            return
        try:
            self.plan = self.analysis.review_plan(
                Strategy(self.query_one("#review-strategy", Select).value),
                horizon,
                market=self.query_one("#review-market", Select).value,
            )
        except (AnalysisFailure, ValueError):
            status.update("Unable to prepare review; check strategy/horizon and portfolio.")
            return
        if not self.plan.requests:
            status.update("No supported holdings in the selected market.")
            return
        self.items = {}
        self.running, self.stop_requested = True, False
        self.query_one("#review-run", Button).disabled = True
        self.query_one("#review-stop", Button).disabled = False
        self.query_one("#review-market", Select).disabled = True
        self.query_one("#review-strategy", Select).disabled = True
        self.query_one("#review-horizon", Input).disabled = True
        self.query_one("#review-skipped", Static).update(
            f"Skipped outside market / unmapped: {', '.join(self.plan.skipped) or 'none'}"
        )

        activity = self.query_one(AnalysisActivity)
        batch_bar = self.query_one("#review-batch-progress", ProgressBar)
        batch_bar.update(total=len(self.plan.requests), progress=0)

        def progress(index, total, item):
            if item.status == "RUNNING":
                activity.start()
            elif item.status != "STOPPED":
                batch_bar.update(progress=index)
                activity.finish(item.status == "COMPLETED")
            self.items[item.symbol] = item
            status.update(f"{index}/{total} • {item.symbol} • {item.status}")
            self.render_items()

        try:
            result = await PortfolioReviewService(self.analysis).run(
                self.plan,
                progress=progress,
                stop_requested=lambda: self.stop_requested,
                stage_progress=activity.observe,
            )
            completed = sum(i.status == "COMPLETED" for i in result.items)
            failed = sum(i.status == "FAILED" for i in result.items)
            stopped = sum(i.status == "STOPPED" for i in result.items)
            status.update(
                f"Review finished: {completed} saved, {failed} failed, {stopped} stopped. "
                "Select a completed row for Full analysis. Close to refresh AI columns."
            )
        finally:
            self.running = False
            self.query_one("#review-run", Button).disabled = False
            self.query_one("#review-stop", Button).disabled = True
            self.query_one("#review-market", Select).disabled = False
            self.query_one("#review-strategy", Select).disabled = False
            self.query_one("#review-horizon", Input).disabled = False

    @on(Button.Pressed, "#review-stop")
    def stop_review(self) -> None:
        self.stop_requested = True
        self.query_one("#review-stop", Button).disabled = True
        self.query_one("#review-status", Static).update(
            "Stop requested: current analysis will finish and save."
        )

    @on(Button.Pressed, "#review-report")
    def report(self) -> None:
        table = self.query_one(DataTable)
        if table.row_count:
            item = self.items.get(str(table.get_row_at(table.cursor_row)[0]))
            if item and item.recommendation:
                self.app.push_screen(FullAnalysisView(item.recommendation))

    @on(Button.Pressed, "#review-close")
    def close_button(self) -> None:
        self.action_close()

    def action_close(self) -> None:
        if self.running:
            self.notify("Use Stop after current to finish safely before closing.")
            return
        self.dismiss()
        self.app.refresh_active_screen()
