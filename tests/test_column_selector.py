import json

from textual.widgets import Button, DataTable, SelectionList

from trading_assistant.application.dashboard import DashboardSnapshot, PortfolioRow
from trading_assistant.tui.column_selector import ColumnSelector
from trading_assistant.tui.dashboard import PORTFOLIO_COLUMNS, DashboardApp, PortfolioScreen


class Dashboard:
    async def snapshot(self):
        return DashboardSnapshot(
            positions=[PortfolioRow(symbol="GE.US", name="GE", currency="PLN")]
        )


async def test_hide_columns_persist_and_apply_to_both_views(tmp_path):
    preferences = tmp_path / "ui.json"
    app = DashboardApp(Dashboard(), preferences_path=preferences)
    async with app.run_test(size=(120, 40)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.press("ctrl+l")
        assert isinstance(app.screen, ColumnSelector)
        choices = app.screen.query_one(SelectionList)
        choices.deselect("REPORT USD")
        choices.deselect("REPORT P/L")
        app.screen.query_one("#columns-apply", Button).press()
        await pilot.pause()
        table = app.query_one("#portfolio-table", DataTable)
        assert len(table.columns) == len(PORTFOLIO_COLUMNS) - 2
        assert str(table.get_row_at(0)[0]) == "GE.US"
        assert table.row_count == 1
        await pilot.press("p")
        await app.workers.wait_for_complete()
        assert isinstance(app.screen, PortfolioScreen)
        assert len(app.screen.query_one(DataTable).columns) == len(PORTFOLIO_COLUMNS) - 2
        await pilot.press("f5")
        await app.workers.wait_for_complete()
        assert len(app.screen.query_one(DataTable).columns) == len(PORTFOLIO_COLUMNS) - 2
    saved = json.loads(preferences.read_text())
    assert saved["hidden_portfolio_columns"] == ["REPORT P/L", "REPORT USD"]
    restarted = DashboardApp(Dashboard(), preferences_path=preferences)
    assert restarted.hidden_portfolio_columns == {"REPORT USD", "REPORT P/L"}


async def test_cancel_show_all_and_symbol_always_visible(tmp_path):
    path = tmp_path / "ui.json"
    path.write_text(json.dumps({"hidden_portfolio_columns": ["SYMBOL", "NAME", "obsolete"]}))
    app = DashboardApp(Dashboard(), preferences_path=path)
    async with app.run_test(size=(120, 40)) as pilot:
        await app.workers.wait_for_complete()
        assert app.hidden_portfolio_columns == {"NAME"}
        await pilot.press("ctrl+l")
        app.screen.query_one(SelectionList).deselect_all()
        await pilot.press("escape")
        assert app.hidden_portfolio_columns == {"NAME"}
        await pilot.press("ctrl+l")
        app.screen.query_one(SelectionList).deselect_all()
        app.screen.query_one("#columns-apply", Button).press()
        await pilot.pause()
        table = app.query_one("#portfolio-table", DataTable)
        assert len(table.columns) == 1
        assert str(table.get_row_at(0)[0]) == "GE.US"
        await pilot.press("ctrl+l")
        app.screen.query_one("#columns-all", Button).press()
        await pilot.pause()
        app.screen.query_one("#columns-apply", Button).press()
        await pilot.pause()
        assert len(table.columns) == len(PORTFOLIO_COLUMNS)
