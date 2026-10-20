"""Saved reports in a separate read-only view; never invokes an engine."""

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Markdown, Static, TabbedContent, TabPane

from trading_assistant.domain.analysis import Recommendation
from trading_assistant.tui.rating_legend import RatingLegend

REPORT_TITLES = {
    "market": "Market",
    "fundamentals": "Fundamentals",
    "news": "News",
    "sentiment": "Sentiment",
    "investment_debate": "Investment debate",
    "research_plan": "Research plan",
    "trading_plan": "Trading plan",
    "risk_debate": "Risk discussion",
}


class FullAnalysisView(ModalScreen):
    DEFAULT_CSS = """
    FullAnalysisView { align: center middle; background: $background 70%; }
    #full-analysis-dialog { width: 95%; height: 95%; border: round #35556e;
                            background: #101923; padding: 1 2; }
    #full-analysis-header { height: auto; margin-bottom: 1; }
    #full-analysis-tabs { height: 1fr; }
    #full-analysis-tabs TabPane { height: 1fr; }
    #full-analysis-tabs VerticalScroll { height: 1fr; }
    #full-analysis-close { margin-top: 1; }
    """
    BINDINGS = [Binding("escape", "close", "Back", priority=True)]

    def __init__(self, result: Recommendation):
        super().__init__()
        self.result = result

    def compose(self) -> ComposeResult:
        with Vertical(id="full-analysis-dialog"):
            yield Static(
                f"FULL ANALYSIS • {self.result.symbol} • {self.result.action.value}\n"
                f"Engine rating: {self.result.engine_rating} • "
                f"Saved {self.result.analyzed_at.isoformat()}",
                id="full-analysis-header",
                markup=False,
            )
            yield RatingLegend()
            with TabbedContent(id="full-analysis-tabs"):
                with TabPane("Decision", id="report-decision"):
                    with VerticalScroll():
                        yield Markdown("\n\n".join(self.result.reasoning))
                for section, title in REPORT_TITLES.items():
                    with TabPane(title, id=f"report-{section}"):
                        with VerticalScroll():
                            yield Markdown(
                                self.result.reports.get(section)
                                or "Report unavailable for this saved run."
                            )
            yield Button("Back", id="full-analysis-close")

    @on(Button.Pressed, "#full-analysis-close")
    def close_button(self) -> None:
        self.action_close()

    def action_close(self) -> None:
        self.dismiss()
