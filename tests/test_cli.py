import pytest
from typer.testing import CliRunner

from trading_assistant.cli import app


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADER_DATABASE_PATH", str(tmp_path / "test.sqlite3"))


def test_help_does_not_launch_dashboard() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "portfolio" in result.output


def test_portfolio_empty_state() -> None:
    result = CliRunner().invoke(app, ["portfolio"])
    assert result.exit_code == 0
    assert "No portfolio is connected" in result.output


def test_no_command_launches_dashboard(monkeypatch) -> None:
    launched = []
    monkeypatch.setattr(
        "trading_assistant.tui.dashboard.DashboardApp.run",
        lambda self: launched.append(self),
    )
    result = CliRunner().invoke(app, [])
    assert result.exit_code == 0
    assert len(launched) == 1
