"""Portfolio daily-change view using application-owned estimates and history."""

import asyncio

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from trading_assistant.application.dashboard import DashboardService, DashboardSnapshot
from trading_assistant.tui.day_chart import DayChangeChart


class DailyDashboard(Screen):
    BINDINGS = [
        Binding("d", "back", "Main dashboard", priority=True),
        Binding("escape", "back", "Back", priority=True),
        Binding("f5", "refresh", "Refresh", priority=True),
    ]

    def __init__(self, service: DashboardService):
        super().__init__()
        self.service = service
        self.refresh_lock = asyncio.Lock()
        self.initial_load = True

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="daily-dashboard-body"):
            yield Static("PORTFOLIO DAILY CHANGE", classes="heading")
            yield Static("Loading today's estimates…", id="daily-summary", markup=False)
            with Vertical(classes="panel"):
                yield Static("DAY CHANGE % HISTORY", classes="heading")
                yield DayChangeChart(id="daily-percent-chart", percentage=True)
            with Vertical(classes="panel"):
                yield Static("DAY CHANGE PLN HISTORY", classes="heading")
                yield DayChangeChart(id="daily-pln-chart")
            yield Static("", id="daily-explanation", classes="message", markup=False)
            with Vertical(classes="panel"):
                yield Static("DAILY HISTORY • LAST 90 DAYS • UTC", classes="heading")
                yield DataTable(id="daily-history-table", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#daily-history-table", DataTable).add_columns(
            "DATE", "LAST OBSERVED", "P/L PLN", "CHANGE %", "COVERAGE"
        )
        self.action_refresh()

    @work()
    async def action_refresh(self) -> None:
        if self.refresh_lock.locked():
            return
        async with self.refresh_lock:
            try:
                initial = getattr(self.service, "initial_snapshot", None)
                if self.initial_load and initial is not None:
                    self.initial_load = False
                    self.render_snapshot(await initial())
                snapshot = await self.service.snapshot()
            except Exception:
                self.query_one("#daily-explanation", Static).update(
                    "Refresh failed; displayed history may be stale. F5 retries."
                )
                return
            self.render_snapshot(snapshot)

    def render_snapshot(self, snapshot: DashboardSnapshot) -> None:
        value = snapshot.today_change_percent
        summary = (
            f"{value:+.2f}% • {snapshot.today_pnl:+,.2f} PLN"
            if value is not None and snapshot.today_pnl is not None
            else "Daily change unavailable"
        )
        label = (
            "Partial estimate"
            if snapshot.today_pnl_covered < snapshot.today_pnl_total
            else "Estimated"
        )
        summary += f" • {label} • {snapshot.today_pnl_covered}/{snapshot.today_pnl_total} holdings"
        self.query_one("#daily-summary", Static).update(
            Text(
                summary,
                style="green"
                if value is not None and value >= 0
                else "red"
                if value is not None
                else "",
            )
        )
        self.query_one("#daily-percent-chart", DayChangeChart).set_snapshot(snapshot)
        self.query_one("#daily-pln-chart", DayChangeChart).set_snapshot(snapshot)
        table = self.query_one("#daily-history-table", DataTable)
        table.clear()
        for day in reversed(snapshot.daily_change_history):
            color = "green" if day.pnl >= 0 else "red"
            table.add_row(
                str(day.day),
                day.observed_at.strftime("%H:%M UTC"),
                Text(f"{day.pnl:+,.2f}", style=color),
                Text(f"{day.change_percent:+.2f}%", style=color)
                if day.change_percent is not None
                else "—",
                f"{day.covered}/{day.total} • "
                + ("Partial estimate" if day.covered < day.total else "Estimated"),
            )
        self.query_one("#daily-explanation", Static).update(
            "Daily % = covered holdings' estimated PLN change / their previous-close "
            "value × 100. Cash is excluded. NBP reference FX; excludes trades, fees and "
            "FX P/L. Today-dated quotes only. Observations persist for 24h; daily "
            "summaries for 90 days. Charts mark coverage/time breaks with ┆; "
            "gaps are not filled. "
            "Daily summaries are the last observed estimates, not closing returns. "
            "D or Esc returns to the main dashboard."
        )

    def action_back(self) -> None:
        self.app.pop_screen()
