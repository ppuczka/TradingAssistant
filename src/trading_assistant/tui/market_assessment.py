"""Explicit sentiment generation alongside deterministic portfolio risk."""

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, LoadingIndicator, Static

from trading_assistant.domain.sentiment import SentimentFailure


class MarketAssessmentView(ModalScreen):
    DEFAULT_CSS = """
    MarketAssessmentView { align: center middle; background: $background 70%; }
    #market-assessment-dialog { width: 95%; height: 90%; border: round #35556e;
                               background: #101923; padding: 1 2; }
    #market-assessment-dialog Static { height: auto; margin-bottom: 1; }
    #market-assessment-buttons { height: 3; }
    #market-assessment-loading { height: 1; }
    """
    BINDINGS = [Binding("escape", "close", "Close", priority=True)]

    def __init__(self, service, paid_factory):
        super().__init__()
        self.service, self.paid_factory = service, paid_factory
        self.request = None
        self.running = False

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="market-assessment-dialog"):
            yield Static("MARKET SENTIMENT & PORTFOLIO RISK", classes="heading")
            yield Static(
                "US / PL • Portfolio-linked quotes and headlines, not broad-market coverage. "
                "Risk checks are deterministic and report-based. Refresh evidence uses data "
                "providers. Generate sentiment makes a paid call with the configured quick "
                "model; only the supplied evidence is sent, no account balances or credentials.",
                markup=False,
            )
            with Horizontal(id="market-assessment-buttons"):
                yield Button("Refresh evidence / risk", id="market-evidence")
                yield Button("Generate sentiment • paid", id="market-generate", disabled=True)
                yield Button("Close", id="market-close")
            yield LoadingIndicator(id="market-assessment-loading")
            yield Static("Loading local summary…", id="market-status", markup=False)
            yield Static("", id="market-risk", markup=False)
            yield Static(
                "Refresh evidence before generating sentiment.", id="market-coverage", markup=False
            )
            yield Static("", id="market-result", markup=False)

    @work()
    async def on_mount(self):
        self.query_one(LoadingIndicator).display = False
        try:
            snapshot = await self.service.dashboard.initial_snapshot()
            self.render_risk(snapshot)
            saved = await self.service.saved()
            if saved:
                self.render_sentiment(saved)
            self.query_one("#market-status", Static).update("Ready. Local summary and risk loaded.")
        except Exception:
            self.query_one("#market-status", Static).update(
                "Unable to load local summary; refresh to retry."
            )

    def render_risk(self, snapshot):
        risk = snapshot.risk_summary
        lines = ["REPORT-BASED PORTFOLIO RISK", snapshot.portfolio_risk]
        if risk:
            lines.append(risk.basis)
            if risk.largest_percent is not None:
                lines.append(f"Largest: {risk.largest_symbol} • {risk.largest_percent:.1f}%")
            if risk.top_five_percent is not None:
                lines.append(f"Top five: {risk.top_five_percent:.1f}%")
            lines.append(
                f"Reserve: {risk.cash_percent:.1f}%"
                if risk.cash_percent is not None
                else "Reserve: unknown"
            )
            lines.extend(risk.warnings)
        lines.append(
            "Concentration/cash checks only; not an overall risk score or execution approval."
        )
        self.query_one("#market-risk", Static).update("\n".join(lines))

    def render_sentiment(self, saved):
        lines = [
            f"SAVED SENTIMENT • {saved.analyzed_at.isoformat()} • {saved.model}",
            saved.request.scope,
        ]
        for market in saved.result.markets:
            covered = len({q.symbol for q in saved.request.quotes if q.market == market.market})
            total = len(saved.request.expected_symbols.get(market.market, []))
            lines.extend(
                [
                    f"\n{market.market}: {market.outlook} • quotes {covered}/{total}",
                    *market.reasoning,
                    *market.limitations,
                ]
            )
            lines.extend(f"Source: {url}" for url in market.news_urls)
        lines.append("\nEVIDENCE SNAPSHOT")
        for quote in saved.request.quotes:
            lines.append(
                f"{quote.symbol}: {quote.price} {quote.currency} • "
                f"{quote.daily_change_percent}% • {quote.as_of.isoformat()} • {quote.source}"
            )
        lines.extend(saved.request.limitations)
        self.query_one("#market-result", Static).update("\n".join(lines))

    def busy(self, running):
        self.running = running
        self.query_one(LoadingIndicator).display = running
        self.query_one("#market-evidence", Button).disabled = running
        self.query_one("#market-generate", Button).disabled = running or self.request is None

    @on(Button.Pressed, "#market-evidence")
    @work()
    async def refresh_evidence(self):
        if self.running:
            return
        self.request = None
        self.busy(True)
        status = self.query_one("#market-status", Static)
        status.update("Refreshing quotes, news and report risk…")
        try:
            self.request = await self.service.request()
            snapshot = await self.service.dashboard.initial_snapshot()
            self.render_risk(snapshot)
            self.query_one("#market-coverage", Static).update(
                f"Evidence captured {self.request.captured_at.isoformat()} • "
                f"{len(self.request.quotes)} fresh quotes • "
                f"{len(self.request.news)} recent headlines"
            )
            status.update("Evidence ready. Generate sentiment starts a paid model call.")
        except Exception:
            status.update("Evidence refresh failed; previous summary may be stale. Retry.")
        finally:
            self.busy(False)

    @on(Button.Pressed, "#market-generate")
    @work()
    async def generate(self):
        if self.running or self.request is None:
            return
        self.busy(True)
        status = self.query_one("#market-status", Static)
        status.update("Generating sentiment… Paid model call may take a few minutes.")
        try:
            service = self.paid_factory()
            saved = await service.assess(self.request)
            self.render_sentiment(saved)
            status.update("Sentiment saved. Close to refresh the dashboard summary.")
        except SentimentFailure as exc:
            status.update(str(exc))
        except Exception:
            status.update(
                "Assessment failed; check configuration/API access. Saved results retained."
            )
        finally:
            self.busy(False)

    @on(Button.Pressed, "#market-close")
    def action_close(self):
        if self.running:
            self.notify("Please wait for the assessment to finish.")
            return
        self.app.pop_screen()
        self.app.refresh_active_screen()
