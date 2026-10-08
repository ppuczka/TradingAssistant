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
            currency="USD",
            portfolio_value=Decimal("1250"),
            cash=Decimal("250"),
            positions=[
                PortfolioRow(
                    symbol="GE",
                    market_value=Decimal("1000"),
                    market_value_usd=Decimal("1000"),
                    currency="USD",
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
        assert table.get_row_at(0)[3] == "1,000.00 USD"
        assert "1,250.00 USD" in str(app.query_one("#metric-value", Static).render())
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
