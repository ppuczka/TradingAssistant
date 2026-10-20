"""Interpret confirmed balances as total account value, including investments."""

from decimal import Decimal

from pydantic import BaseModel

from trading_assistant.domain.portfolio import PortfolioState


class PortfolioBalance(BaseModel):
    total: Decimal | None
    holdings: Decimal | None
    reserve: Decimal | None = None
    warning: str | None = None


def portfolio_balance(state: PortfolioState) -> PortfolioBalance:
    total = state.account_value
    if total is not None and (not total.is_finite() or total < 0):
        return PortfolioBalance(total=None, holdings=None, warning="Invalid total account value.")
    if state.holdings and (
        not state.report_dates
        or any(
            h.report_value is None or not h.report_value.is_finite() or h.report_value < 0
            for h in state.holdings
        )
    ):
        return PortfolioBalance(
            total=total,
            holdings=None,
            warning="Report holdings incomplete; reserve cannot be calculated.",
        )
    holdings = sum((h.report_value for h in state.holdings), Decimal(0))
    if state.account_value_mismatches:
        return PortfolioBalance(
            total=total,
            holdings=holdings,
            warning="Account total below report holdings for: "
            + ", ".join(state.account_value_mismatches),
        )
    if total is None:
        return PortfolioBalance(
            total=None,
            holdings=holdings,
            warning="Total account value unconfirmed; reserve unknown.",
        )
    if total < holdings:
        return PortfolioBalance(
            total=total,
            holdings=holdings,
            warning="Total account value is below report holdings; "
            "check balance and snapshot dates.",
        )
    return PortfolioBalance(total=total, holdings=holdings, reserve=total - holdings)
