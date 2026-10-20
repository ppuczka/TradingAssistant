from datetime import UTC, datetime
from decimal import Decimal

from trading_assistant.application.analysis import AnalysisService
from trading_assistant.application.balance import portfolio_balance
from trading_assistant.application.portfolio import PortfolioDashboardService
from trading_assistant.application.risk import assess_portfolio
from trading_assistant.domain.analysis import Strategy
from trading_assistant.domain.portfolio import Holding, PortfolioState
from trading_assistant.domain.risk import RiskSettings


def state(total="20729.28"):
    return PortfolioState(
        account_value=total,
        report_dates=[datetime(2026, 1, 2, tzinfo=UTC)],
        holdings=[
            Holding(
                symbol="GE.US", name="GE", quantity="1", asset_type="STOCK", report_value="19086.71"
            )
        ],
    )


async def test_total_is_not_added_twice_and_ai_receives_only_reserve_as_cash():
    portfolio = state()
    balance = portfolio_balance(portfolio)
    assert balance.reserve == Decimal("1642.57")
    risk = assess_portfolio(portfolio, RiskSettings())
    assert risk.cash_percent == Decimal("1642.57") / Decimal("20729.28") * 100
    assert risk.largest_percent == Decimal("19086.71") / Decimal("20729.28") * 100

    class Repository:
        def portfolio(self):
            return portfolio

    snapshot = await PortfolioDashboardService(Repository()).initial_snapshot()
    assert snapshot.portfolio_value == Decimal("20729.28")
    assert snapshot.cash == Decimal("1642.57")
    weights = {row.label: row.weight_percent for row in snapshot.allocation}
    assert weights["Cash reserve"] == risk.cash_percent
    context = (
        AnalysisService(Repository(), None).request("GE", Strategy.LONG_TERM, "3 years").portfolio
    )
    assert context.cash_pln == Decimal("1642.57")
    assert context.account_value_pln == Decimal("20729.28")


def test_negative_or_unknown_reserve_is_not_zero_and_account_mismatch_cannot_offset():
    assert portfolio_balance(state("100")).reserve is None
    assert portfolio_balance(state(None)).reserve is None
    partial = state()
    partial.holdings[0].report_value = None
    assert portfolio_balance(partial).reserve is None
    mismatched = state("100000")
    mismatched.account_value_mismatches = ["synthetic-account"]
    assert portfolio_balance(mismatched).reserve is None
    assert assess_portfolio(mismatched, RiskSettings()).status == "Unavailable"
