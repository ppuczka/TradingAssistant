"""Presentation preferences for portfolio table visibility."""

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, SelectionList, Static


class ColumnSelector(ModalScreen):
    DEFAULT_CSS = """
    ColumnSelector { align: center middle; background: $background 70%; }
    #column-dialog { width: 65; max-width: 95%; height: 85%; border: round #35556e;
                     background: #101923; padding: 1 2; }
    #column-dialog Static { height: auto; margin-bottom: 1; }
    #column-list { height: auto; }
    #column-buttons { height: 3; }
    """
    BINDINGS = [Binding("escape", "cancel", "Cancel", priority=True)]

    def __init__(self, columns, hidden):
        super().__init__()
        self.columns, self.hidden = columns, hidden

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="column-dialog"):
            yield Static("PORTFOLIO COLUMNS", classes="heading")
            yield Static(
                "Space toggles a column. Symbol is always visible. "
                "Applies to the dashboard and Portfolio view.",
                markup=False,
            )
            yield SelectionList(
                *[
                    (name, name, name not in self.hidden)
                    for name in self.columns
                    if name != "SYMBOL"
                ],
                id="column-list",
            )
            with Horizontal(id="column-buttons"):
                yield Button("Apply", id="columns-apply", variant="primary")
                yield Button("Show all", id="columns-all")
                yield Button("Cancel", id="columns-cancel")

    @on(Button.Pressed, "#columns-apply")
    def apply(self):
        selected = set(self.query_one(SelectionList).selected) | {"SYMBOL"}
        self.dismiss(set(self.columns) - selected)

    @on(Button.Pressed, "#columns-all")
    def show_all(self):
        self.query_one(SelectionList).select_all()

    @on(Button.Pressed, "#columns-cancel")
    def action_cancel(self):
        self.dismiss(None)
