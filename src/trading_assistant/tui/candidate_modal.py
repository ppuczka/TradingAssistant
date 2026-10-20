"""Yahoo candidate discovery through the application service; no model calls."""

from collections.abc import Callable

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.coordinate import Coordinate
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Select, Static

from trading_assistant.application.candidates import CandidateService
from trading_assistant.tui.opportunity_history import OpportunityHistoryView
from trading_assistant.tui.opportunity_modal import OpportunityModal


class CandidateModal(ModalScreen):
    DEFAULT_CSS = """
    CandidateModal { align: center middle; background: $background 70%; }
    #candidate-dialog { width: 95%; height: 90%; border: round #35556e;
                        background: #101923; padding: 1 2; }
    #candidate-dialog Static { height: auto; margin-bottom: 1; }
    #candidate-dialog Input, #candidate-dialog Select { margin-bottom: 1; }
    #candidate-buttons { height: 3; }
    #candidate-table { height: 15; min-height: 4; margin-top: 1; }
    """
    BINDINGS = [
        Binding("escape", "close", "Close", priority=True),
        Binding("space", "toggle_candidate", "Select candidate"),
    ]

    def __init__(
        self,
        service: CandidateService,
        analysis_factory: Callable | None = None,
        history_factory: Callable | None = None,
    ):
        super().__init__()
        self.service = service
        self.analysis_factory = analysis_factory
        self.history_factory = history_factory
        self.candidates = []
        self.selected_symbols: set[str] = set()
        self.running = False

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="candidate-dialog"):
            yield Static("STOCK CANDIDATES • Yahoo", classes="heading")
            yield Static(
                "Market-cap ranking with liquidity filters; mapped owned holdings excluded. "
                "PL means Warsaw-listed stocks. XTB availability is unverified. "
                "These are candidates, not AI recommendations. No model calls.",
                markup=False,
            )
            yield Select(
                [("US + PL", "US,PL"), ("US", "US"), ("PL • Warsaw", "PL")],
                value="US,PL",
                allow_blank=False,
                id="candidate-market",
            )
            yield Static("Maximum results per market (1–100)", markup=False)
            yield Input(value="20", id="candidate-limit", type="integer")
            with Horizontal(id="candidate-buttons"):
                yield Button("Fetch candidates", id="candidate-fetch", variant="primary")
                yield Button("Analyze selected", id="candidate-ai", disabled=True)
                yield Button("Close", id="candidate-close")
            yield Button("Saved reviews", id="candidate-saved")
            yield Static(
                "Ready. Fetch candidates to query Yahoo.", id="candidate-status", markup=False
            )
            yield Static(
                "0 selected • Highlight a row and press Space or Enter to select (maximum 10).",
                id="candidate-selection",
                markup=False,
            )
            yield DataTable(id="candidate-table", cursor_type="row")
            yield Static("", id="candidate-details", markup=False)

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns(
            "SELECT",
            "MARKET",
            "SYMBOL",
            "NAME",
            "PRICE",
            "MARKET CAP",
            "AVG VOLUME",
            "QUOTE TIME UTC",
        )

    @on(Button.Pressed, "#candidate-fetch")
    @work()
    async def fetch_candidates(self) -> None:
        if self.running:
            return
        status = self.query_one("#candidate-status", Static)
        try:
            limit = int(self.query_one("#candidate-limit", Input).value)
            if not 1 <= limit <= 100:
                raise ValueError
        except ValueError:
            status.update("Provide a result count between 1 and 100.")
            return
        markets = str(self.query_one("#candidate-market", Select).value).split(",")
        self.running = True
        controls = [
            self.query_one("#candidate-fetch", Button),
            self.query_one("#candidate-ai", Button),
            self.query_one("#candidate-market", Select),
            self.query_one("#candidate-limit", Input),
        ]
        for control in controls:
            control.disabled = True
        self.candidates = []
        self.selected_symbols.clear()
        self.query_one(DataTable).clear()
        self.update_selection()
        status.update("Fetching Yahoo candidates…")
        try:
            batch = await self.service.fetch(markets, limit)
            self.candidates = batch.candidates
            table = self.query_one(DataTable)
            table.clear()
            for candidate in batch.candidates:
                table.add_row(
                    Text("[ ]"),
                    Text(candidate.market),
                    Text(candidate.symbol),
                    Text(candidate.name),
                    f"{candidate.price:,.2f} {candidate.currency}",
                    f"{candidate.market_cap:,.0f} {candidate.currency}",
                    f"{candidate.average_volume:,.0f}",
                    candidate.quote_as_of.strftime("%Y-%m-%d %H:%M"),
                )
            status.update(
                f"{len(batch.candidates)} candidates • {batch.excluded_owned} owned excluded • "
                f"{batch.rejected} rejected • Fetched {batch.fetched_at:%Y-%m-%d %H:%M} UTC"
            )
            self.query_one("#candidate-details", Static).update(
                "\n".join(
                    [
                        *batch.warnings,
                        *(f"{market}: {error}" for market, error in batch.errors.items()),
                    ]
                )
            )
        except Exception:
            status.update(
                "Unable to fetch or save candidates. Previous results may be stale. Retry."
            )
        finally:
            self.running = False
            for control in controls:
                control.disabled = False
            self.update_selection()

    def update_selection(self) -> None:
        count = len(self.selected_symbols)
        self.query_one("#candidate-selection", Static).update(
            f"{count} selected • Highlight a row and press Space or Enter to select (maximum 10)."
        )
        self.query_one("#candidate-ai", Button).disabled = self.running or count == 0

    @on(DataTable.RowSelected, "#candidate-table")
    def row_selected(self) -> None:
        self.action_toggle_candidate()

    def action_toggle_candidate(self) -> None:
        table = self.query_one(DataTable)
        if self.running or self.focused is not table or not self.candidates:
            return
        index = table.cursor_row
        symbol = self.candidates[index].symbol
        if symbol in self.selected_symbols:
            self.selected_symbols.remove(symbol)
        else:
            if len(self.selected_symbols) == 10:
                self.notify("Select up to 10 candidates per review.", severity="warning")
                return
            self.selected_symbols.add(symbol)
        table.update_cell_at(
            Coordinate(index, 0), Text("[x]" if symbol in self.selected_symbols else "[ ]")
        )
        self.update_selection()

    @on(Button.Pressed, "#candidate-ai")
    def ai_review(self) -> None:
        if self.running or not self.selected_symbols:
            return
        if self.analysis_factory is None:
            self.notify("AI analysis is not configured.", severity="warning")
            return
        try:
            analysis, settings = self.analysis_factory()
            selected = [c for c in self.candidates if c.symbol in self.selected_symbols]
            self.app.push_screen(OpportunityModal(selected, analysis, settings, selected=True))
        except Exception:
            self.notify("Check analysis configuration and OPENAI_API_KEY.", severity="error")

    @on(Button.Pressed, "#candidate-saved")
    def saved_reviews(self) -> None:
        if self.history_factory is None:
            self.notify("Saved reviews are not configured.", severity="warning")
            return
        self.app.push_screen(OpportunityHistoryView(self.history_factory(), self.analysis_factory))

    @on(Button.Pressed, "#candidate-close")
    def close_button(self) -> None:
        self.action_close()

    def action_close(self) -> None:
        if self.running:
            self.notify("Please wait for candidate fetching to finish.")
            return
        self.app.pop_screen()
