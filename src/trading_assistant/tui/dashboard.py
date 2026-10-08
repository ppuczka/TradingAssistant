"""Presentation only: render the snapshot supplied by an application service."""

import asyncio
from decimal import Decimal

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from trading_assistant.application.dashboard import DashboardService, DashboardSnapshot


def money(value: Decimal | None, currency: str | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f}{' ' + currency if currency else ''}"


def percent(value: Decimal | None) -> Text:
    if value is None:
        return Text("—")
    return Text(f"{value:+.2f}%", style="green" if value >= 0 else "red")


PORTFOLIO_COLUMNS = (
    "SYMBOL",
    "NAME",
    "QUANTITY",
    "REPORT VALUE",
    "REPORT USD",
    "REPORT P/L",
    "QUOTE PRICE",
    "QUOTE VALUE",
    "DAY CHANGE %",
    "AI",
)


def populate_portfolio(table: DataTable, snapshot: DashboardSnapshot) -> None:
    table.clear()
    for position in snapshot.positions:
        table.add_row(
            Text(position.symbol),
            Text(position.name),
            str(position.quantity) if position.quantity is not None else "—",
            money(position.market_value, position.currency),
            money(position.market_value_usd, "USD"),
            percent(position.pnl_percent),
            money(position.quote_price, position.quote_currency),
            money(position.quote_value, position.quote_currency),
            percent(position.quote_daily_change_percent),
            Text(position.recommendation or "—"),
        )


class PortfolioScreen(Screen):
    """Dedicated holdings view, reading the same service as the dashboard."""

    BINDINGS = [
        Binding("escape", "back", "Dashboard", priority=True),
        Binding("f5", "refresh", "Refresh", priority=True),
    ]

    def __init__(self, service: DashboardService) -> None:
        super().__init__()
        self.service = service
        self.refresh_lock = asyncio.Lock()
        self.initial_load = True

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="portfolio-view-body"):
            yield Static("PORTFOLIO • My Trades / IKZE", classes="heading")
            yield Static("Loading holdings…", id="portfolio-view-notice", markup=False)
            yield Static("", id="portfolio-view-fx", classes="message", markup=False)
            yield DataTable(id="portfolio-view-table", cursor_type="row")
            with VerticalScroll(id="portfolio-view-alerts"):
                yield Static("", id="portfolio-view-status", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns(*PORTFOLIO_COLUMNS)
        self.query_one(DataTable).focus()
        self.action_refresh()

    @work()
    async def action_refresh(self) -> None:
        if self.refresh_lock.locked():
            return
        async with self.refresh_lock:
            await self.refresh_snapshot()

    async def refresh_snapshot(self) -> None:
        notice = self.query_one("#portfolio-view-notice", Static)
        try:
            initial_snapshot = getattr(self.service, "initial_snapshot", None)
            if self.initial_load and initial_snapshot is not None:
                self.initial_load = False
                initial = await initial_snapshot()
                populate_portfolio(self.query_one(DataTable), initial)
                notice.update("Read-only portfolio • Loading market data…")
                self.query_one("#portfolio-view-fx", Static).update(initial.market_status or "")
            snapshot = await self.service.snapshot()
        except Exception:
            notice.update("Unable to refresh holdings. Displayed values may be stale. F5 retries.")
            return
        populate_portfolio(self.query_one(DataTable), snapshot)
        notice.update(
            f"Report values as of {snapshot.valuation_as_of} • Read-only"
            if snapshot.positions and snapshot.valuation_as_of
            else "Read-only portfolio overview"
            if snapshot.positions
            else "No open positions available. Import a report with trader import-xtb."
        )
        self.query_one("#portfolio-view-status", Static).update("\n".join(snapshot.alerts))
        self.query_one("#portfolio-view-fx", Static).update(
            "\n".join(filter(None, (snapshot.fx_status, snapshot.market_status)))
        )

    def action_back(self) -> None:
        self.app.pop_screen()


class DashboardApp(App[None]):
    TITLE = "AI PORTFOLIO"
    SUB_TITLE = "Personal investment assistant"
    CSS = """
    Screen { background: #101923; }
    Header { background: #182a3a; }
    #content { padding: 1 2; }
    #intro { height: auto; margin-bottom: 1; color: #96aaba; }
    #fx-status { height: auto; color: #96aaba; margin-bottom: 1; }
    #metrics { grid-size: 4; grid-gutter: 1; height: 5; }
    .metric { border: round #35556e; padding: 0 1; }
    .panel { border: round #35556e; padding: 0 1; height: auto; margin-top: 1; }
    .heading { color: #75c6ed; text-style: bold; height: 1; margin-bottom: 1; }
    .message { height: auto; color: #96aaba; }
    #portfolio-table { height: auto; max-height: 14; min-height: 3; }
    #details { grid-size: 2; grid-gutter: 1; height: auto; }
    #details > .panel { min-height: 9; }
    #opportunities-table { height: auto; max-height: 9; min-height: 3; }
    #allocation, #ai-status, #alerts { height: auto; }
    #portfolio-view-body { padding: 1 2; }
    #portfolio-view-notice { height: auto; margin-bottom: 1; color: #96aaba; }
    #portfolio-view-table { height: 1fr; }
    #portfolio-view-alerts { height: auto; max-height: 7; margin-top: 1; }
    #portfolio-view-status { height: auto; }
    .compact #metrics { grid-size: 2; height: 10; }
    .compact #details { grid-size: 1; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit", priority=True),
        Binding("f5", "refresh", "Refresh"),
        Binding("p", "portfolio", "Portfolio", priority=True),
        Binding("a", "unavailable('Analysis')", "Analyze"),
        Binding("s", "unavailable('Opportunity scan')", "Scan"),
        Binding("r", "unavailable('Portfolio review')", "Review"),
        Binding("h", "unavailable('Recommendation history')", "History"),
        Binding("o", "unavailable('Orders')", "Orders"),
        Binding("c", "unavailable('Configuration')", "Config"),
    ]

    def __init__(self, service: DashboardService) -> None:
        super().__init__()
        self.service = service
        self.refresh_lock = asyncio.Lock()
        self.initial_load = True

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="content"):
            yield Static("Loading portfolio overview…", id="intro", markup=False)
            yield Static("", id="fx-status", markup=False)
            with Grid(id="metrics"):
                for widget_id in ("value", "cash", "today", "total"):
                    yield Static("—", id=f"metric-{widget_id}", classes="metric")
            with Vertical(classes="panel"):
                yield Static("PORTFOLIO", classes="heading")
                yield DataTable(id="portfolio-table", cursor_type="row")
                yield Static("", id="portfolio-empty", classes="message", markup=False)
            with Grid(id="details"):
                with Vertical(classes="panel"):
                    yield Static("ALLOCATION", classes="heading")
                    yield Static("", id="allocation", markup=False)
                with Vertical(classes="panel"):
                    yield Static("AI STATUS", classes="heading")
                    yield Static("", id="ai-status", markup=False)
            with Vertical(classes="panel"):
                yield Static("OPPORTUNITIES", classes="heading")
                yield DataTable(id="opportunities-table", cursor_type="row")
                yield Static("", id="opportunities-empty", classes="message", markup=False)
            with Vertical(classes="panel"):
                yield Static("ALERTS", classes="heading")
                yield Static("", id="alerts", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.home_screen = self.screen
        self.query_one("#portfolio-table", DataTable).add_columns(*PORTFOLIO_COLUMNS)
        self.query_one("#opportunities-table", DataTable).add_columns(
            "SYMBOL", "ACTION", "CONFIDENCE"
        )
        self.set_class(self.size.width < 100, "compact")
        self.action_refresh()
        self.set_interval(5, self.refresh_active_screen)

    def refresh_active_screen(self) -> None:
        if isinstance(self.screen, PortfolioScreen):
            self.screen.action_refresh()
        else:
            self.action_refresh()

    def on_resize(self) -> None:
        self.set_class(self.size.width < 100, "compact")

    @work()
    async def action_refresh(self) -> None:
        if self.refresh_lock.locked():
            return
        async with self.refresh_lock:
            await self.refresh_snapshot()

    async def refresh_snapshot(self) -> None:
        try:
            initial_snapshot = getattr(self.service, "initial_snapshot", None)
            if self.initial_load and initial_snapshot is not None:
                self.initial_load = False
                self.render_snapshot(await initial_snapshot())
            snapshot = await self.service.snapshot()
        except Exception:
            self.home_screen.query_one("#intro", Static).update(
                "Unable to refresh the dashboard. Any displayed values may be stale. "
                "Press F5 to retry."
            )
            self.notify("Dashboard data is unavailable.", severity="error")
            return
        self.render_snapshot(snapshot)

    def render_snapshot(self, snapshot: DashboardSnapshot) -> None:
        self.home_screen.query_one("#intro", Static).update(
            f"Portfolio overview • Read-only • Report values as of {snapshot.valuation_as_of}"
            if snapshot.positions and snapshot.valuation_as_of
            else "Portfolio overview • Read-only"
            if snapshot.positions
            else "Welcome. No portfolio is connected yet. Values appear when data is available."
        )
        for widget_id, title, value in (
            ("value", "Holdings value", snapshot.holdings_value),
            ("cash", "Cash", snapshot.cash),
            ("today", "Today's P/L", snapshot.today_pnl),
            ("total", "Unrealized P/L", snapshot.unrealized_pnl),
        ):
            widget = self.home_screen.query_one(f"#metric-{widget_id}", Static)
            widget.border_title = title
            content = money(value, snapshot.currency)
            usd = (
                snapshot.holdings_value_usd
                if widget_id == "value"
                else snapshot.unrealized_pnl_usd
                if widget_id == "total"
                else None
            )
            if widget_id == "cash" and value is not None:
                content += "\nConfirmed snapshot"
            if widget_id == "today" and value is not None:
                content += "\nEstimated • reference FX"
            if usd is not None:
                content += f"\n{money(usd, 'USD')}"
            if value is None and snapshot.positions:
                reason = {
                    "cash": "Confirmed balance needed",
                    "today": "Quotes / FX incomplete",
                    "total": "Report P/L unavailable",
                    "value": "Valuation unavailable",
                }[widget_id]
                content += f"\n{reason}"
            style = "bold"
            if widget_id == "today" and value is not None:
                style += " green" if value >= 0 else " red"
            widget.update(Text(content, style=style))
        self.home_screen.query_one("#fx-status", Static).update(
            "\n".join(filter(None, (snapshot.fx_status, snapshot.market_status)))
        )

        populate_portfolio(self.home_screen.query_one("#portfolio-table", DataTable), snapshot)
        self.home_screen.query_one("#portfolio-empty", Static).update(
            "No open positions available. Import an XTB report using trader import-xtb."
            if not snapshot.positions
            else ""
        )
        self.home_screen.query_one("#allocation", Static).update(
            "\n".join(f"{row.label:<16} {row.weight_percent:.1f}%" for row in snapshot.allocation)
            or "Allocation is unavailable until portfolio valuations are connected."
        )
        self.home_screen.query_one("#ai-status", Static).update(
            f"Market sentiment   {snapshot.market_sentiment}\n"
            f"Portfolio risk     {snapshot.portfolio_risk}\n"
            f"Last analysis      {snapshot.last_analysis}\n"
            f"Next scan          {snapshot.next_scan}"
        )
        opportunities = self.home_screen.query_one("#opportunities-table", DataTable)
        opportunities.clear()
        for row in snapshot.opportunities:
            opportunities.add_row(
                Text(row.symbol),
                Text(row.action),
                f"{row.confidence:.0%}" if row.confidence is not None else "—",
            )
        self.home_screen.query_one("#opportunities-empty", Static).update(
            "No opportunities analyzed yet." if not snapshot.opportunities else ""
        )
        self.home_screen.query_one("#alerts", Static).update(
            "\n".join(snapshot.alerts) or "No alerts available. Risk checks have not run."
        )

    def action_portfolio(self) -> None:
        if isinstance(self.screen, PortfolioScreen):
            self.screen.query_one(DataTable).focus()
            return
        self.push_screen(PortfolioScreen(self.service))

    def action_unavailable(self, feature: str) -> None:
        self.notify(f"{feature} is not implemented yet.", title="Coming later")
