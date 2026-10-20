from datetime import UTC, datetime

import pytest

from trading_assistant.adapters.recommendations import (
    JsonRecommendationReader,
    JsonRecommendationStore,
)
from trading_assistant.adapters.tradingagents import normalize
from trading_assistant.analysis_config import AnalysisSettings
from trading_assistant.domain.analysis import AnalysisRequest, PortfolioContext
from trading_assistant.domain.candidates import StockCandidate


def request():
    now = datetime.now(UTC)
    return AnalysisRequest(
        symbol="TEST.WA",
        time_horizon="3 years",
        portfolio=PortfolioContext(),
        candidate=StockCandidate(
            symbol="TEST.WA",
            name="Synthetic",
            market="PL",
            exchange="WSE",
            currency="PLN",
            price="45.10",
            market_cap="100000000",
            average_volume="10000",
            fetched_at=now,
            quote_as_of=now,
        ),
    )


def recommendation(report):
    return normalize(
        request(),
        {
            "final_rating": "Overweight",
            "final_trade_decision": "Rating: Overweight\nSynthetic",
            "trader_investment_plan": report,
        },
        "Overweight",
    )


def test_markdown_plan_levels_normalize_and_saved_legacy_levels_load_without_rewrite(tmp_path):
    result = recommendation("**Entry Price:** 44.0\n**Stop Loss:** 42.61")
    assert str(result.entry) == "44.0"
    assert str(result.stop_loss) == "42.61"
    assert result.entry_currency == result.stop_loss_currency == "PLN"
    legacy = result.model_copy(
        update={
            "entry": None,
            "entry_currency": None,
            "stop_loss": None,
            "stop_loss_currency": None,
        }
    )
    JsonRecommendationStore(
        tmp_path, AnalysisSettings(quick_model="quick", deep_model="deep")
    ).save(request(), legacy)
    path = next(tmp_path.glob("*.json"))
    original = path.read_bytes()
    saved = JsonRecommendationReader(tmp_path).opportunities()[0].recommendation
    assert saved.entry == result.entry and saved.stop_loss == result.stop_loss
    assert saved.entry_currency == saved.stop_loss_currency == "PLN"
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "report",
    [
        "Entry Price: 40–44\nStop Loss: 42 or 43",
        "Entry Price: 44\nEntry Price: 45\nStop Loss: 42\nStop Loss: 41",
        "Entry Price: 44 USD\nStop Loss: 42 USD",
        "Entry Price: 0\nStop Loss: -2",
        "Price now: 44\nConsider a stop near 42",
    ],
)
def test_ambiguous_conflicting_or_missing_levels_are_unknown(report):
    result = recommendation(report)
    assert result.entry is None and result.stop_loss is None
