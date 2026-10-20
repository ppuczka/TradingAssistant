"""Scrollable article links supplied by the application news service."""

from zoneinfo import ZoneInfo

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Link, Static

from trading_assistant.application.dashboard import DashboardSnapshot


class NewsPanel(VerticalScroll):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.snapshot = DashboardSnapshot()

    def compose(self) -> ComposeResult:
        yield Static(self.snapshot.news_status, markup=False, classes="message")
        if not self.snapshot.news:
            yield Static("No recent headlines available from the retrieved feeds.", markup=False)
        for article in self.snapshot.news:
            timestamp = article.published_at.astimezone(ZoneInfo("Europe/Warsaw"))
            yield Static(
                f"{article.timestamp_kind.title()} {timestamp:%Y-%m-%d %H:%M} • "
                f"{article.publisher} • {article.discovery_source} • {', '.join(article.symbols)}",
                markup=False,
                classes="message",
            )
            yield Link(article.title, url=str(article.url))

    def set_snapshot(self, snapshot: DashboardSnapshot) -> None:
        if (
            self.snapshot.news == snapshot.news
            and self.snapshot.news_status == snapshot.news_status
        ):
            return
        self.snapshot = snapshot
        self.refresh(recompose=True)
