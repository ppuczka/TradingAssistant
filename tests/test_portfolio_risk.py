import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from typer.testing import CliRunner

from trading_assistant.application.risk import assess_portfolio
from trading_assistant.cli import app
from trading_assistant.domain.portfolio import Holding, PortfolioState
from trading_assistant.domain.risk import RiskSettings
from trading_assistant.risk_config import load_risk_settings


def state(cash="1000"):
    return PortfolioState(
        holdings=[
            Holding(
                symbol=symbol, name=symbol, asset_type="STOCK", quantity="1", report_value="300"
            )
            for symbol in ["GE.US", "PKO.PL"]
        ],
        account_value=cash,
        report_dates=[datetime(2026, 1, 2, tzinfo=UTC)],
    )


def test_exact_thresholds_no_breach_and_default_concentration_warnings():
    result = assess_portfolio(state(), RiskSettings(max_position_percent="30"))
    assert result.largest_percent == 30
    assert result.top_five_percent == 60
    assert result.cash_percent == 40
    assert result.warnings == []
    assert result.status == "No threshold breaches"
    warned = assess_portfolio(state(), RiskSettings())
    assert len(warned.warnings) == 2
    assert warned.status == "Threshold warnings"


def test_unknown_cash_uses_labeled_holdings_basis_and_does_not_assume_zero_cash():
    result = assess_portfolio(state(None), RiskSettings())
    assert result.cash_percent is None
    assert result.largest_percent == 50
    assert result.top_five_percent == 100
    assert "Incomplete" in result.status
    assert result.basis == "Report holdings only"
    assert not any("Cash reserve:" in w for w in result.warnings)


def test_low_cash_missing_valuations_and_zero_value():
    result = assess_portfolio(state("600"), RiskSettings())
    assert result.cash_percent == 0
    assert any("Cash reserve:" in w for w in result.warnings)
    missing = state()
    missing.holdings[0].report_value = None
    assert assess_portfolio(missing, RiskSettings()).status == "Unavailable"
    assert (
        assess_portfolio(PortfolioState(account_value="0"), RiskSettings()).largest_percent is None
    )
    only_cash = assess_portfolio(PortfolioState(account_value="100"), RiskSettings())
    assert only_cash.cash_percent == 100 and only_cash.top_five_percent == 0


def test_same_instrument_aggregated_before_concentration_check():
    portfolio = state()
    portfolio.holdings.append(portfolio.holdings[0].model_copy())
    portfolio.account_value = Decimal("1300")
    result = assess_portfolio(portfolio, RiskSettings())
    assert result.largest_symbol == "GE.US"
    assert result.largest_percent == Decimal(600) / 1300 * 100
    assert len([w for w in result.warnings if "GE.US concentration" in w]) == 1


def test_risk_yaml_env_validation_and_defaults(tmp_path, monkeypatch):
    path = tmp_path / "risk.yaml"
    assert load_risk_settings(path).max_position_percent == 20
    path.write_text("max_position_percent: 25\nmin_cash_percent: 5\n")
    monkeypatch.setenv("TRADER_RISK_MAX_POSITION_PERCENT", "30")
    settings = load_risk_settings(path)
    assert settings.max_position_percent == 30 and settings.min_cash_percent == 5
    monkeypatch.setenv("TRADER_RISK_MIN_CASH_PERCENT", "101")
    with pytest.raises(ValueError, match="Invalid risk configuration"):
        load_risk_settings(path)


def test_cli_risk_local_json_and_threshold_configuration(tmp_path, monkeypatch):
    from trading_assistant import bootstrap

    class Repository:
        def portfolio(self):
            return state()

    monkeypatch.setattr(bootstrap, "repository", lambda path: Repository())
    config = tmp_path / "risk.yaml"
    config.write_text("max_position_percent: 30\n")
    response = CliRunner().invoke(app, ["risk", "--config", str(config), "--json"])
    assert response.exit_code == 0, response.output
    result = json.loads(response.output)
    assert result["summary"]["status"] == "No threshold breaches"
    assert result["summary"]["cash_percent"] == "40.0"
