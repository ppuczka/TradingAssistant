"""Reopen saved opportunity recommendations and reports without engine calls."""

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Select, Static

from trading_assistant.application.recommendations import OpportunityHistoryService
from trading_assistant.tui.analysis_modal import AnalysisModal
from trading_assistant.tui.full_analysis import FullAnalysisView
from trading_assistant.tui.rating_legend import RatingLegend


class OpportunityHistoryView(ModalScreen):
    DEFAULT_CSS = """
    OpportunityHistoryView { align: center middle; background: $background 70%; }
    #opportunity-history-dialog { width: 95%; height: 90%; border: round #35556e;
                                 background: #101923; padding: 1 2; }
    #opportunity-history-dialog Static { height: auto; margin-bottom: 1; }
    #opportunity-history-buttons { height: 3; }
    #opportunity-history-buttons Button { min-width: 10; width: 1fr; padding: 0; }
    #opportunity-history-table { height: 1fr; }
    """
    BINDINGS = [Binding("escape", "close", "Close", priority=True)]

    def __init__(self, service: OpportunityHistoryService, analysis_factory=None):
        super().__init__()
        self.service = service
        self.analysis_factory = analysis_factory
        self.records = []

    def compose(self) -> ComposeResult:
        with Vertical(id="opportunity-history-dialog"):
            yield Static("SAVED OPPORTUNITY REVIEWS", classes="heading")
            yield RatingLegend()
            yield Static(
                "Historical recommendations, not current trading instructions. "
                "Engine rating: Buy > Overweight > Hold > Underweight > Sell. "
                "This is recommendation strength, not a probability of success. "
                "Select a row for its full report. No API calls or key required.",
                markup=False,
            )
            yield Select(
                [
                    ("Latest per symbol • strongest rating first", "latest"),
                    ("All analyses • newest first", "history"),
                ],
                value="latest",
                allow_blank=False,
                id="opportunity-history-mode",
            )
            with Horizontal(id="opportunity-history-buttons"):
                yield Button("Full report", id="opportunity-history-report", disabled=True)
                yield Button("Retry analysis", id="opportunity-history-retry", disabled=True)
                yield Button("Analyze symbol", id="opportunity-history-symbol")
                yield Button("Refresh", id="opportunity-history-refresh")
                yield Button("Close", id="opportunity-history-close")
            yield Static("Loading saved reviews…", id="opportunity-history-status", markup=False)
            yield DataTable(id="opportunity-history-table", cursor_type="row")

    def on_mount(self):
        self.query_one(DataTable).add_columns(
            "SYMBOL", "AI", "ENGINE RATING", "ANALYZED UTC", "STRATEGY", "HORIZON"
        )
        self.reload()

    @on(Select.Changed, "#opportunity-history-mode")
    @on(Button.Pressed, "#opportunity-history-refresh")
    def controls_changed(self):
        self.reload()

    @work(exclusive=True)
    async def reload(self):
        try:
            records = await self.service.records(history=self.query_one(Select).value == "history")
        except Exception:
            self.query_one("#opportunity-history-status", Static).update(
                "Unable to read saved reviews. Previous results may be stale. Refresh to retry."
            )
            return
        self.records = records
        table = self.query_one(DataTable)
        table.clear()
        for saved in records:
            result = saved.recommendation
            table.add_row(
                result.symbol,
                result.action.value,
                result.engine_rating,
                result.analyzed_at.strftime("%Y-%m-%d %H:%M:%S"),
                saved.request.strategy.value,
                saved.request.time_horizon,
            )
        self.query_one("#opportunity-history-status", Static).update(
            f"{len(records)} saved reviews. Original portfolio context is preserved."
            if records
            else "No saved opportunity reviews yet. Use S → Fetch → AI review."
        )
        self.highlighted()

    @on(DataTable.RowHighlighted, "#opportunity-history-table")
    def highlighted(self):
        self.query_one("#opportunity-history-report", Button).disabled = not self.records
        self.query_one("#opportunity-history-retry", Button).disabled = not self.records

    @on(Button.Pressed, "#opportunity-history-report")
    @on(DataTable.RowSelected, "#opportunity-history-table")
    def report(self):
        table = self.query_one(DataTable)
        if self.records:
            self.app.push_screen(FullAnalysisView(self.records[table.cursor_row].recommendation))

    @on(Button.Pressed, "#opportunity-history-retry")
    def retry_analysis(self):
        if not self.records:
            return
        saved = self.records[self.query_one(DataTable).cursor_row]
        self.open_analysis(saved)

    @on(Button.Pressed, "#opportunity-history-symbol")
    def analyze_symbol(self):
        self.open_analysis()

    def open_analysis(self, saved=None):
        if self.analysis_factory is None:
            self.notify("AI analysis is not configured.", severity="warning")
            return
        try:
            analysis, settings = self.analysis_factory()
            options = (
                {
                    "initial_strategy": saved.request.strategy,
                    "initial_horizon": saved.request.time_horizon,
                    "saved_result": saved.recommendation,
                }
                if saved
                else {}
            )
            self.app.push_screen(
                AnalysisModal(
                    saved.request.symbol if saved else "", analysis, settings, typed=True, **options
                ),
                lambda _: self.reload(),
            )
        except Exception:
            self.notify("Check analysis configuration and OPENAI_API_KEY.", severity="error")

    @on(Button.Pressed, "#opportunity-history-close")
    def action_close(self):
        self.app.pop_screen()
