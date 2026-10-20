from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_assistant.application.portfolio import PortfolioDashboardService
from trading_assistant.domain.analysis import (
    AnalysisRequest,
    PortfolioContext,
    PositionContext,
    Recommendation,
    SavedAnalysis,
)
from trading_assistant.domain.portfolio import Holding, PortfolioState


@pytest.mark.parametrize("cash", [Decimal("1250"), None])
async def test_allocation_disjoint_report_values_with_known_or_unknown_cash(cash):
    class Repository:
        def portfolio(self):
            return PortfolioState(
                holdings=[
                    Holding(
                        symbol=symbol,
                        name=symbol,
                        asset_type=kind,
                        quantity="1",
                        report_value=value,
                    )
                    for symbol, kind, value in [
                        ("GE.US", "STOCK", "400"),
                        ("PKO.PL", "STOCK", "200"),
                        ("ETF.US", "ETF", "400"),
                    ]
                ],
                report_dates=[datetime(2026, 1, 2, tzinfo=UTC)],
                account_value=cash,
            )

    snapshot = await PortfolioDashboardService(Repository()).initial_snapshot()
    allocation = {row.label: row.weight_percent for row in snapshot.allocation}
    assert sum(allocation.values()) == 100
    assert allocation["US stocks"] == (32 if cash is not None else 40)
    assert allocation["ETF / ETC"] == allocation["US stocks"]
    assert ("Cash reserve" in allocation) == (cash is not None)
    assert (
        "unconfirmed" in snapshot.allocation_status
        if cash is None
        else "Confirmed account total" in snapshot.allocation_status
    )


async def test_missing_value_does_not_show_misleading_partial_allocation():
    class Repository:
        def portfolio(self):
            return PortfolioState(
                holdings=[Holding(symbol="GE.US", name="GE", asset_type="STOCK", quantity="1")],
                report_dates=[datetime(2026, 1, 2, tzinfo=UTC)],
                account_value="250",
            )

    snapshot = await PortfolioDashboardService(Repository()).initial_snapshot()
    assert snapshot.allocation == []
    assert "incomplete" in snapshot.allocation_status


async def test_ai_status_uses_owned_and_opportunity_history_without_inventing_risk():
    owned_date = datetime(2026, 1, 2, tzinfo=UTC)
    opportunity_date = datetime(2026, 1, 3, tzinfo=UTC)
    owned = SavedAnalysis(
        request=AnalysisRequest(
            symbol="GE",
            time_horizon="3 years",
            portfolio=PortfolioContext(
                positions=[
                    PositionContext(broker_symbol="GE.US", canonical_symbol="GE", quantity="1")
                ]
            ),
        ),
        recommendation=Recommendation(
            symbol="GE",
            owned=True,
            action="HOLD",
            engine_rating="Hold",
            analyzed_at=owned_date,
            reasoning=["Synthetic"],
        ),
    )
    opportunity = SavedAnalysis(
        request=AnalysisRequest(
            symbol="TEST", time_horizon="3 years", portfolio=PortfolioContext()
        ),
        recommendation=Recommendation(
            symbol="TEST",
            owned=False,
            action="WATCH",
            engine_rating="Hold",
            analyzed_at=opportunity_date,
            reasoning=["Synthetic"],
        ),
    )

    class Reader:
        def latest(self):
            return {"GE": owned}

        def opportunities(self):
            return [opportunity]

    class Repository:
        def portfolio(self):
            return PortfolioState(
                holdings=[Holding(symbol="GE.US", name="GE", asset_type="STOCK", quantity="1")]
            )

    snapshot = await PortfolioDashboardService(
        Repository(), recommendation_reader=Reader()
    ).initial_snapshot()
    assert snapshot.last_analysis == opportunity_date.isoformat()
    assert snapshot.analyzed_positions == 1
    assert snapshot.saved_opportunity_count == 1
    assert "Not assessed" in snapshot.market_sentiment
    assert "Unavailable" in snapshot.portfolio_risk
    assert "Manual" in snapshot.next_scan
