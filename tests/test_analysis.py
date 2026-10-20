import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from trading_assistant.adapters.recommendations import JsonRecommendationStore
from trading_assistant.adapters.tradingagents import TradingAgentsEngine, normalize
from trading_assistant.analysis_config import AnalysisSettings, load_analysis_settings
from trading_assistant.application.analysis import AnalysisService
from trading_assistant.cli import app
from trading_assistant.domain.analysis import (
    Action,
    AnalysisFailure,
    AnalysisRequest,
    PortfolioContext,
    PositionContext,
    Recommendation,
)
from trading_assistant.domain.portfolio import Holding, PortfolioState


def request(owned=True):
    positions = (
        [PositionContext(broker_symbol="GE.US", canonical_symbol="GE", quantity="0.8014")]
        if owned
        else []
    )
    return AnalysisRequest(
        symbol="GE",
        time_horizon="3-5 years",
        portfolio=PortfolioContext(positions=positions, cash_pln=None),
    )


@pytest.mark.parametrize(
    "owned,signal,action",
    [
        (True, "Buy", "ADD"),
        (True, "Overweight", "ADD"),
        (True, "Hold", "HOLD"),
        (True, "Underweight", "REDUCE"),
        (True, "Sell", "SELL"),
        (False, "Buy", "BUY"),
        (False, "Overweight", "BUY"),
        (False, "Hold", "WATCH"),
        (False, "Underweight", "IGNORE"),
        (False, "Sell", "IGNORE"),
    ],
)
def test_rating_normalization_without_invented_metadata(owned, signal, action):
    result = normalize(
        request(owned),
        {"final_rating": signal, "final_trade_decision": f"Rating: {signal}\nReasoned decision."},
        signal,
    )
    assert result.action == action
    assert result.confidence is None and result.entry is None and result.risk is None
    assert result.timeframe is None and result.stop_loss is None and result.target is None


@pytest.mark.parametrize(
    "state,signal",
    [
        ({"final_trade_decision": "Reason"}, "REVIEW"),
        ({"final_trade_decision": ""}, "Hold"),
        ({"final_rating": "Buy", "final_trade_decision": "Rating: Sell"}, "Buy"),
        ({"final_rating": "Sell", "final_trade_decision": "Reason"}, "Hold"),
        ({"final_trade_decision": "Rating: Buy\nRating: Sell"}, "Buy"),
        ({"final_trade_decision": "Possibly buy but consider selling"}, "Buy"),
    ],
)
def test_ambiguous_or_invalid_result_fails(state, signal):
    with pytest.raises(AnalysisFailure):
        normalize(request(), state, signal)


def test_domain_rejects_action_wrong_for_ownership_and_invalid_quantity():
    with pytest.raises(ValidationError):
        Recommendation(
            symbol="GE", action=Action.BUY, owned=True, engine_rating="Buy", reasoning=["Reason"]
        )
    with pytest.raises(ValidationError):
        PositionContext(broker_symbol="GE.US", quantity="NaN")


async def test_adapter_uses_inspected_api_context_and_config_with_fake_graph(tmp_path):
    seen = {}

    class Graph:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def propagate(self, ticker, date, *, portfolio):
            seen["ticker"] = ticker
            seen["context"] = portfolio.render(ticker)
            seen["fingerprint"] = portfolio.fingerprint()
            return {
                "final_rating": "Hold",
                "final_trade_decision": "Rating: Hold\nLong-term case.",
            }, "Hold"

    settings = AnalysisSettings(quick_model="test-quick", deep_model="test-deep")
    result = await TradingAgentsEngine(settings, tmp_path, graph_factory=Graph).analyze(request())
    assert result.action == Action.HOLD
    assert seen["ticker"] == "GE"
    assert (
        "0.8014" in seen["context"]
        and "LONG_TERM" in seen["context"]
        and "3-5 years" in seen["context"]
    )
    assert '"cash_pln":null' in seen["context"]
    assert "short-term technical signal alone" in seen["context"]
    config = seen["config"]
    assert config["quick_think_llm"] == "test-quick"
    assert config["deep_think_llm"] == "test-deep"
    assert config["max_tool_rounds"] == 20 and config["max_tokens"] == 8192
    assert config["openai_reasoning_effort"] == "medium"
    assert config["max_debate_rounds"] == 1 and config["max_risk_discuss_rounds"] == 1
    assert Path(config["results_dir"]).is_relative_to(tmp_path)
    assert Path(config["memory_log_path"]).is_relative_to(tmp_path)


async def test_engine_errors_do_not_expose_raw_provider_exception(tmp_path):
    def broken(**kwargs):
        raise RuntimeError("private-key-value")

    engine = TradingAgentsEngine(
        AnalysisSettings(quick_model="quick", deep_model="deep"), tmp_path, broken
    )
    with pytest.raises(AnalysisFailure) as exc:
        await engine.analyze(request())
    assert "private-key-value" not in str(exc.value)


async def test_service_resolves_owned_symbol_and_saves_exact_context(tmp_path):
    class Repository:
        def portfolio(self):
            return PortfolioState(
                holdings=[
                    Holding(
                        symbol="GE.US", name="GE", asset_type="STOCK", quantity=Decimal("0.8014")
                    )
                ],
                report_dates=[datetime.now(UTC)],
            )

    class Engine:
        async def analyze(self, req):
            return normalize(
                req, {"final_rating": "Hold", "final_trade_decision": "Reason"}, "Hold"
            )

    store = JsonRecommendationStore(
        tmp_path, AnalysisSettings(quick_model="quick", deep_model="deep")
    )
    service = AnalysisService(Repository(), Engine(), store)
    from trading_assistant.domain.analysis import Strategy

    req = service.request("GE.US", Strategy.LONG_TERM, "3-5 years")
    assert req.symbol == "GE" and req.owned
    await service.analyze(req)
    saved = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert saved["request"]["portfolio"]["positions"][0]["quantity"] == "0.8014"
    assert saved["recommendation"]["action"] == "HOLD"
    assert "api_key" not in json.dumps(saved)
    with pytest.raises(AnalysisFailure, match="imported holding"):
        service.request("PKO.PL", Strategy.LONG_TERM, "3-5 years")


def test_config_yaml_env_precedence_and_invalid_limits(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("quick_model: yaml-quick\ndeep_model: yaml-deep\n")
    monkeypatch.setenv("TRADINGAGENTS_QUICK_THINK_LLM", "env-quick")
    assert load_analysis_settings(path).quick_model == "env-quick"
    monkeypatch.setenv("TRADINGAGENTS_MAX_TOOL_ROUNDS", "bad")
    with pytest.raises(AnalysisFailure):
        load_analysis_settings(path)


def test_cli_dry_run_uses_configuration_without_invoking_engine(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "config.yaml"
    path.write_text("quick_model: quick\ndeep_model: deep\n")
    monkeypatch.setenv("TRADER_DATABASE_PATH", str(tmp_path / "portfolio.sqlite3"))

    def forbidden(*args):
        raise AssertionError("Paid engine called")

    monkeypatch.setattr(TradingAgentsEngine, "analyze", forbidden)
    result = CliRunner().invoke(app, ["analyze", "GE.US", "--config", str(path), "--dry-run"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["request"]["symbol"] == "GE"
    assert data["settings"]["max_tokens"] == 8192


def test_cli_fake_engine_and_missing_key(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "config.yaml"
    config.write_text("quick_model: quick\ndeep_model: deep\n")
    monkeypatch.setenv("TRADER_DATABASE_PATH", str(tmp_path / "portfolio.sqlite3"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = CliRunner().invoke(app, ["analyze", "GE", "--config", str(config)])
    assert result.exit_code == 1 and "OPENAI_API_KEY" in result.output
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key")

    async def fake(self, req):
        return normalize(
            req, {"final_rating": "Hold", "final_trade_decision": "Rating: Hold\nReason"}, "Hold"
        )

    monkeypatch.setattr(TradingAgentsEngine, "analyze", fake)
    result = CliRunner().invoke(app, ["analyze", "GE", "--config", str(config)])
    assert result.exit_code == 0, result.output
    assert "WATCH" in result.output
    assert len(list((tmp_path / "analysis" / "recommendations").glob("*.json"))) == 1


def test_only_adapter_imports_tradingagents_internals():
    import ast

    root = Path(__file__).parents[1] / "src" / "trading_assistant"
    for path in root.rglob("*.py"):
        if path == root / "adapters" / "tradingagents.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            modules = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else ([node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            )
            assert not any(
                name == "tradingagents" or name.startswith("tradingagents.") for name in modules
            ), path


def test_saved_reader_uses_analysis_timestamp_and_skips_invalid_or_unowned_records(tmp_path):
    from datetime import timedelta

    from trading_assistant.adapters.recommendations import JsonRecommendationReader

    now = datetime.now(UTC)

    def save(name, req, action, analyzed_at):
        result = Recommendation(
            symbol=req.symbol,
            owned=req.owned,
            action=action,
            engine_rating="Hold",
            reasoning=["Reason"],
            analyzed_at=analyzed_at,
        )
        (tmp_path / name).write_text(
            json.dumps(
                {
                    "request": req.model_dump(mode="json"),
                    "recommendation": result.model_dump(mode="json"),
                }
            )
        )

    save("older.json", request(), Action.ADD, now - timedelta(hours=2))
    save("newer.json", request(), Action.HOLD, now - timedelta(hours=1))
    save("opportunity.json", request(False), Action.BUY, now)
    (tmp_path / "corrupted.json").write_text("not json")
    latest = JsonRecommendationReader(tmp_path).latest()
    assert latest["GE"].recommendation.action == Action.HOLD
    assert latest["GE"].recommendation.analyzed_at == now - timedelta(hours=1)


async def test_dashboard_loads_saved_ai_without_analysis_or_network(tmp_path):
    from trading_assistant.adapters.recommendations import JsonRecommendationReader
    from trading_assistant.application.portfolio import PortfolioDashboardService

    class Repository:
        def portfolio(self):
            return PortfolioState(
                holdings=[
                    Holding(symbol="GE.US", name="GE", asset_type="STOCK", quantity="1"),
                    Holding(symbol="AAPL.US", name="Apple", asset_type="STOCK", quantity="1"),
                ]
            )

    settings = AnalysisSettings(quick_model="quick", deep_model="deep")
    result = normalize(
        request(), {"final_rating": "Hold", "final_trade_decision": "Reason"}, "Hold"
    )
    JsonRecommendationStore(tmp_path, settings).save(request(), result)
    service = PortfolioDashboardService(
        Repository(), recommendation_reader=JsonRecommendationReader(tmp_path)
    )
    snapshot = await service.initial_snapshot()
    assert snapshot.positions[0].recommendation == "HOLD"
    assert snapshot.positions[0].recommendation_as_of == result.analyzed_at
    assert snapshot.positions[1].recommendation is None
    assert any("quantity changed" in alert for alert in snapshot.alerts)
    assert any("LONG_TERM" in alert and "3-5 years" in alert for alert in snapshot.alerts)


@pytest.mark.parametrize(
    "amount,percent", [("-175.9012", "-3.30"), ("210.1234", "5.25"), (None, None)]
)
def test_report_gain_loss_passed_to_owned_and_opportunity_context(amount, percent):
    from trading_assistant.adapters.tradingagents import EnginePortfolioContext
    from trading_assistant.domain.analysis import Strategy
    from trading_assistant.domain.candidates import StockCandidate

    report_date = datetime(2026, 1, 2, tzinfo=UTC)

    class Portfolio:
        def portfolio(self):
            return PortfolioState(
                holdings=[
                    Holding(
                        symbol="GE.US",
                        name="GE",
                        asset_type="STOCK",
                        quantity="2",
                        report_value="1000",
                        report_pnl_amount=amount,
                        report_pnl_percent=percent,
                    )
                ],
                report_dates=[report_date],
            )

    service = AnalysisService(Portfolio(), None)
    owned = service.request("GE", Strategy.LONG_TERM, "3 years")
    now = datetime.now(UTC)
    candidate = StockCandidate(
        symbol="TEST",
        name="Synthetic",
        market="US",
        exchange="NMS",
        currency="USD",
        price="50",
        market_cap="1000000000",
        average_volume="100000",
        quote_as_of=now,
        fetched_at=now,
    )
    opportunity = service.opportunity_plan([candidate], Strategy.LONG_TERM, "3 years").requests[0]
    for analysis in [owned, opportunity]:
        position = analysis.portfolio.positions[0]
        assert position.report_pnl_pln == (Decimal(amount) if amount is not None else None)
        assert position.report_pnl_percent == (Decimal(percent) if percent is not None else None)
        assert analysis.portfolio.report_dates == [report_date]
        prompt = EnginePortfolioContext(analysis).render(analysis.symbol)
        assert "not live prices or live P/L" in prompt
        assert "unknown, not zero" in prompt
        assert "2026-01-02" in prompt
        assert amount in prompt if amount is not None else '"report_pnl_pln":null' in prompt


def test_position_context_rejects_nonfinite_report_pnl():
    for field in ("report_pnl_pln", "report_pnl_percent"):
        with pytest.raises(ValidationError):
            PositionContext(broker_symbol="GE.US", quantity="1", **{field: "NaN"})


@pytest.mark.parametrize(
    "line,price,currency",
    [
        ("Suggested entry: 123.4567 USD", "123.4567", "USD"),
        ("Suggested entry: 42 PLN", "42", "PLN"),
        ("Suggested entry: 100-120 USD", None, None),
        ("Suggested entry: 0 USD", None, None),
        ("Suggested entry: 123", None, None),
        ("Suggested entry: 123 USD\nSuggested entry: 124 USD", None, None),
        ("Price now: 123 USD", None, None),
    ],
)
def test_explicit_suggested_entry_preserves_precision_and_currency(line, price, currency):
    decision = "Rating: Buy\nReasoning.\n" + line
    result = normalize(
        request(False), {"final_rating": "Buy", "final_trade_decision": decision}, "Buy"
    )
    assert result.entry == (Decimal(price) if price else None)
    assert result.entry_currency == currency
