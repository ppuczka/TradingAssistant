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


@pytest.mark.parametrize(
    "amount,as_of",
    [
        ("NaN", "2026-01-01T00:00:00Z"),
        ("Infinity", "2026-01-01T00:00:00Z"),
        ("-1", "2026-01-01T00:00:00Z"),
        ("100", "2026-01-01T00:00:00"),
        ("100", "2099-01-01T00:00:00Z"),
        ("100", "invalid"),
    ],
)
def test_set_cash_rejects_invalid_input(amount, as_of):
    result = CliRunner().invoke(app, ["set-cash", amount, "--account", "test", "--as-of", as_of])
    assert result.exit_code != 0


def test_set_cash_unknown_account():
    result = CliRunner().invoke(app, ["set-cash", "100", "--account", "missing"])
    assert result.exit_code == 1
    assert "Unknown account" in result.output
