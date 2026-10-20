"""Observed analysis activity and elapsed time; no guessed percentages or ETA."""

from time import monotonic

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import ProgressBar, Static

from trading_assistant.domain.progress import AnalysisProgress


class AnalysisActivity(Vertical):
    DEFAULT_CSS = """
    AnalysisActivity { height: auto; margin: 1 0; }
    AnalysisActivity Static { height: auto; }
    AnalysisActivity ProgressBar { height: 1; }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.started = None
        self.stage = "Ready"
        self.completed_calls = 0

    def compose(self) -> ComposeResult:
        yield Static("Ready", classes="activity-label", markup=False)
        yield ProgressBar(total=1, show_eta=False, show_percentage=False)

    def on_mount(self):
        self.set_interval(1, self.update_label)

    def start(self):
        self.started = monotonic()
        self.stage, self.completed_calls = "Starting analysis", 0
        self.query_one(ProgressBar).update(total=None, progress=0)
        self.update_label()

    def observe(self, event: AnalysisProgress):
        self.stage = event.stage
        self.completed_calls = max(self.completed_calls, event.completed_calls)
        self.update_label()

    def update_label(self):
        elapsed = f" • {int(monotonic() - self.started)}s elapsed" if self.started else ""
        self.query_one(Static).update(
            f"{self.stage} • {self.completed_calls} model calls completed{elapsed}"
        )

    def finish(self, success=True):
        elapsed = int(monotonic() - self.started) if self.started else 0
        self.started = None
        self.stage = "Finished" if success else "Failed"
        self.query_one(ProgressBar).update(total=1, progress=1 if success else 0)
        self.query_one(Static).update(
            f"{self.stage} • {self.completed_calls} model calls completed • {elapsed}s elapsed"
        )
