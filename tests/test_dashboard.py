from decimal import Decimal

import pytest
from textual.widgets import DataTable, Static

from trading_assistant.application.dashboard import (
    AllocationRow,
    DashboardSnapshot,
    EmptyDashboardService,
    OpportunityRow,
    PortfolioRow,
)
from trading_assistant.tui.dashboard import DashboardApp, PortfolioScreen


class FakeService:
    def __init__(self) -> None:
        self.calls = 0
        self.fail = False

    async def snapshot(self) -> DashboardSnapshot:
        self.calls += 1
        if self.fail:
            raise RuntimeError("private provider detail")
        return DashboardSnapshot(
            currency="PLN",
            portfolio_value=Decimal("1250"),
            holdings_value=Decimal("1000"),
            holdings_value_usd=Decimal("250"),
            unrealized_pnl=Decimal("-30"),
            unrealized_pnl_usd=Decimal("-7.50"),
            estimated_unrealized_pnl=Decimal("-20"),
            estimated_unrealized_pnl_usd=Decimal("-5"),
            estimated_pnl_covered=1,
            estimated_pnl_total=1,
            fx_status="NBP reference FX: 1 USD = 4 PLN",
            cash=Decimal("250"),
            today_pnl=Decimal("-12.50"),
            positions=[
                PortfolioRow(
                    symbol="GE",
                    name="GE Aerospace",
                    market_value=Decimal("1000"),
                    market_value_usd=Decimal("250"),
                    currency="PLN",
                    pnl_percent=Decimal("-3.3"),
                    recommendation="HOLD",
                )
            ],
            allocation=[AllocationRow(label="USA", weight_percent=Decimal("80"))],
            opportunities=[OpportunityRow(symbol="TEST", action="WATCH")],
            alerts=["Synthetic concentration warning"],
        )


@pytest.mark.parametrize("size", [(80, 24), (140, 45)])
async def test_empty_dashboard_and_keyboard_navigation(size: tuple[int, int]) -> None:
    app = DashboardApp(EmptyDashboardService())
    async with app.run_test(size=size) as pilot:
        await app.workers.wait_for_complete()
        assert app.query_one("#portfolio-table", DataTable).row_count == 0
        assert "No portfolio" in str(app.query_one("#intro", Static).render())
        assert "—" in str(app.query_one("#metric-value", Static).render())
        await pilot.press("p")
        await app.workers.wait_for_complete()
        assert isinstance(app.screen, PortfolioScreen)
        assert app.focused is app.screen.query_one("#portfolio-view-table")
        assert "No open positions" in str(
            app.screen.query_one("#portfolio-view-notice", Static).render()
        )
        await pilot.press("escape")
        assert not isinstance(app.screen, PortfolioScreen)
        for key in ("a", "s", "r", "h", "o", "c"):
            await pilot.press(key)
        await pilot.press("q")
        assert not app.is_running


async def test_snapshot_render_refresh_and_failure_keep_previous_data() -> None:
    service = FakeService()
    app = DashboardApp(service)
    async with app.run_test(size=(120, 42)) as pilot:
        await app.workers.wait_for_complete()
        assert service.calls == 1
        table = app.query_one("#portfolio-table", DataTable)
        assert table.row_count == 1
        assert str(table.get_row_at(0)[0]) == "GE"
        assert str(table.get_row_at(0)[1]) == "GE Aerospace"
        assert table.get_row_at(0)[4] == "250.00 USD"
        assert "1,000.00 PLN" in str(app.query_one("#metric-value", Static).render())
        assert "250.00 USD" in str(app.query_one("#metric-value", Static).render())
        assert "-12.50 PLN" in str(app.query_one("#metric-today", Static).render())
        assert "Estimated" in str(app.query_one("#metric-today", Static).render())
        assert "-5.00 USD" in str(app.query_one("#metric-total", Static).render())
        assert "NBP reference" in str(app.query_one("#fx-status", Static).render())
        assert "80.0%" in str(app.query_one("#allocation", Static).render())
        assert app.query_one("#opportunities-table", DataTable).get_row_at(0)[2] == "—"
        await pilot.press("f5")
        await app.workers.wait_for_complete()
        assert service.calls == 2
        assert table.row_count == 1
        service.fail = True
        await pilot.press("f5")
        await app.workers.wait_for_complete()
        message = str(app.query_one("#intro", Static).render())
        assert "stale" in message
        assert "private provider detail" not in message
        assert table.row_count == 1


@pytest.mark.parametrize("size", [(80, 24), (140, 45)])
async def test_p_opens_populated_portfolio_and_refreshes(size: tuple[int, int]) -> None:
    service = FakeService()
    app = DashboardApp(service)
    async with app.run_test(size=size) as pilot:
        await app.workers.wait_for_complete()
        await pilot.press("p")
        await app.workers.wait_for_complete()
        screen = app.screen
        assert isinstance(screen, PortfolioScreen)
        table = screen.query_one("#portfolio-view-table", DataTable)
        assert table.row_count == 1
        assert str(table.get_row_at(0)[0]) == "GE"
        assert str(table.get_row_at(0)[1]) == "GE Aerospace"
        await pilot.press("p")
        assert app.screen is screen
        calls = service.calls
        await pilot.press("f5")
        await app.workers.wait_for_complete()
        assert service.calls == calls + 1
        service.fail = True
        await pilot.press("f5")
        await app.workers.wait_for_complete()
        assert table.row_count == 1
        assert "stale" in str(screen.query_one("#portfolio-view-notice", Static).render())
        await pilot.press("escape")
        assert app.screen is app.home_screen
        await pilot.press("q")
        assert not app.is_running


async def test_periodic_refresh_routes_to_active_screen():
    service = FakeService()
    app = DashboardApp(service)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        calls = service.calls
        app.refresh_active_screen()
        await app.workers.wait_for_complete()
        assert service.calls == calls + 1
        await pilot.press("p")
        await app.workers.wait_for_complete()
        calls = service.calls
        app.refresh_active_screen()
        await app.workers.wait_for_complete()
        assert service.calls == calls + 1
        assert app.screen.query_one(DataTable).row_count == 1


@pytest.mark.parametrize(
    "change,expected,color",
    [
        ("2.5", "+2.50%", "green"),
        ("-1.25", "-1.25%", "red"),
    ],
)
async def test_daily_percentage_color_and_no_status_column(change, expected, color):
    service = FakeService()
    original_snapshot = service.snapshot

    async def snapshot():
        result = await original_snapshot()
        result.positions[0].quote_daily_change_percent = Decimal(change)
        return result

    service.snapshot = snapshot
    app = DashboardApp(service)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        table = app.query_one("#portfolio-table", DataTable)
        cell = table.get_row_at(0)[8]
        assert str(cell) == expected
        assert cell.style == color
        assert len(table.columns) == 12
        await pilot.press("p")
        await app.workers.wait_for_complete()
        assert str(app.screen.query_one(DataTable).get_row_at(0)[8]) == expected


async def test_partial_daily_estimate_displays_coverage_and_sign():
    service = FakeService()
    app = DashboardApp(service)
    async with app.run_test(size=(140, 45)):
        await app.workers.wait_for_complete()
        snapshot = await service.snapshot()
        snapshot.today_pnl_covered = 1
        snapshot.today_pnl_total = 2
        for amount, color in [(Decimal("-12.50"), "red"), (Decimal("0"), "green")]:
            snapshot.today_pnl = amount
            app.render_snapshot(snapshot)
            widget = app.query_one("#metric-today", Static)
            assert "Partial estimate" in str(widget.render())
            assert "1/2 holdings" in str(widget.render())
            assert any(color in str(span.style) for span in widget.render().spans)


async def test_daily_dashboard_switching_and_refresh():
    from trading_assistant.tui.daily_dashboard import DailyDashboard
    from trading_assistant.tui.day_chart import DayChangeChart

    class Service(FakeService):
        async def snapshot(self):
            snapshot = await super().snapshot()
            snapshot.today_change_percent = Decimal("-1.25")
            snapshot.today_pnl_covered = 12
            snapshot.today_pnl_total = 20
            return snapshot

    service = Service()
    app = DashboardApp(service)
    async with app.run_test(size=(140, 45)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.press("d")
        await app.workers.wait_for_complete()
        assert isinstance(app.screen, DailyDashboard)
        summary = app.screen.query_one("#daily-summary", Static).render()
        assert "-1.25%" in str(summary)
        assert "12/20 holdings" in str(summary)
        assert "Partial estimate" in str(summary)
        assert app.screen.query_one("#daily-percent-chart", DayChangeChart).percentage
        calls = service.calls
        app.refresh_active_screen()
        await app.workers.wait_for_complete()
        assert service.calls == calls + 1
        await pilot.press("d")
        assert app.screen is app.home_screen
        await pilot.press("d")
        await app.workers.wait_for_complete()
        await pilot.press("escape")
        assert app.screen is app.home_screen


async def test_opportunity_suggested_entry_displays_price_currency_or_dash():
    service = FakeService()
    app = DashboardApp(service)
    async with app.run_test():
        await app.workers.wait_for_complete()
        table = app.query_one("#opportunities-table", DataTable)
        assert table.get_row_at(0)[3] == "—"
        assert table.get_row_at(0)[4] == "—"
        snapshot = await service.snapshot()
        snapshot.opportunities[0].entry = Decimal("123.45")
        snapshot.opportunities[0].entry_currency = "USD"
        snapshot.opportunities[0].stop_loss = Decimal("120.10")
        snapshot.opportunities[0].stop_loss_currency = "USD"
        app.render_snapshot(snapshot)
        assert table.get_row_at(0)[3] == "123.45 USD"
        assert table.get_row_at(0)[4] == "120.10 USD"


async def test_ctrl_p_returns_from_nested_views_and_focused_input():
    from textual.widgets import Input

    from trading_assistant.domain.analysis import Recommendation
    from trading_assistant.tui.candidate_modal import CandidateModal
    from trading_assistant.tui.full_analysis import FullAnalysisView

    app = DashboardApp(FakeService(), candidate_factory=lambda: object())
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.press("p")
        await app.workers.wait_for_complete()
        portfolio = app.screen
        await pilot.press("s")
        assert isinstance(app.screen, CandidateModal)
        app.screen.query_one(Input).focus()
        await pilot.press("ctrl+p")
        await app.workers.wait_for_complete()
        assert app.screen is portfolio
        await pilot.press("s")
        app.push_screen(
            FullAnalysisView(
                Recommendation(
                    symbol="TEST",
                    action="BUY",
                    owned=False,
                    engine_rating="Buy",
                    reasoning=["Synthetic"],
                )
            )
        )
        await pilot.pause()
        await pilot.press("ctrl+p")
        await app.workers.wait_for_complete()
        assert app.screen is portfolio
        await pilot.press("ctrl+p")
        assert app.screen is portfolio


async def test_ctrl_p_keeps_running_work_and_returns_after_finished():
    from trading_assistant.tui.candidate_modal import CandidateModal

    app = DashboardApp(FakeService(), candidate_factory=lambda: object())
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.press("s")
        screen = app.screen
        assert isinstance(screen, CandidateModal)
        screen.running = True
        await pilot.press("ctrl+p")
        assert app.screen is screen
        screen.running = False
        await pilot.press("ctrl+p")
        await app.workers.wait_for_complete()
        assert isinstance(app.screen, PortfolioScreen)


async def test_estimated_summary_label_partial_coverage_and_loss_color():
    app = DashboardApp(FakeService())
    async with app.run_test():
        await app.workers.wait_for_complete()
        snapshot = await app.service.snapshot()
        snapshot.estimated_pnl_total = 2
        app.render_snapshot(snapshot)
        box = app.query_one("#metric-total", Static)
        assert "Est. unrealized P/L" in str(box.border_title)
        text = box.render()
        assert "-20.00 PLN" in str(text) and "Partial estimate" in str(text)
        assert "1/2 holdings" in str(text)
        assert any("red" in str(span.style) for span in text.spans)
        snapshot.estimated_unrealized_pnl = None
        snapshot.estimated_unrealized_pnl_usd = None
        app.render_snapshot(snapshot)
        assert "-30" not in str(box.render())
