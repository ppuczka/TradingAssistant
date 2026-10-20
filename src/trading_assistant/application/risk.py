"""Deterministic report concentration and cash checks; no execution decisions."""

from decimal import Decimal

from trading_assistant.application.balance import portfolio_balance
from trading_assistant.domain.portfolio import PortfolioState
from trading_assistant.domain.risk import PortfolioRiskSummary, RiskSettings


def assess_portfolio(state: PortfolioState, settings: RiskSettings) -> PortfolioRiskSummary:
    balance = portfolio_balance(state)
    if balance.holdings is None or (balance.total is not None and balance.reserve is None):
        return PortfolioRiskSummary(
            status="Unavailable", basis=balance.warning or "Invalid valuations"
        )
    values = {}
    for holding in state.holdings:
        values[holding.symbol] = values.get(holding.symbol, Decimal(0)) + holding.report_value
    cash = balance.reserve
    total = balance.total if balance.total is not None else balance.holdings
    if total <= 0:
        return PortfolioRiskSummary(status="Unavailable", basis="No positive portfolio value")
    ordered = sorted(values.items(), key=lambda item: item[1], reverse=True)
    summary = PortfolioRiskSummary(
        status="No threshold breaches"
        if cash is not None
        else "Incomplete • account total unconfirmed",
        basis="Confirmed account total • report-derived reserve"
        if cash is not None
        else "Report holdings only",
        largest_symbol=ordered[0][0] if ordered else None,
        largest_percent=ordered[0][1] / total * 100 if ordered else None,
        top_five_percent=sum((value for _, value in ordered[:5]), Decimal(0)) / total * 100,
        cash_percent=cash / total * 100 if cash is not None else None,
    )
    for symbol, value in ordered:
        weight = value / total * 100
        if weight > settings.max_position_percent:
            summary.warnings.append(
                f"{symbol} concentration: {weight:.1f}% exceeds {settings.max_position_percent}% "
                f"({summary.basis.lower()})."
            )
    if summary.top_five_percent > settings.max_top_five_percent:
        summary.warnings.append(
            f"Top-five concentration: {summary.top_five_percent:.1f}% exceeds "
            f"{settings.max_top_five_percent}% ({summary.basis.lower()})."
        )
    if summary.cash_percent is not None and summary.cash_percent < settings.min_cash_percent:
        summary.warnings.append(
            f"Cash reserve: {summary.cash_percent:.1f}% below {settings.min_cash_percent}%."
        )
    if summary.warnings and cash is not None:
        summary.status = "Threshold warnings"
    return summary
