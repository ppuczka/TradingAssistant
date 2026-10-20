"""Estimated P/L from dated report observations and a quote valued in PLN."""

from decimal import Decimal


def estimate_pnl(
    report_value: Decimal | None,
    report_pnl: Decimal | None,
    report_percent: Decimal | None,
    quote_value_pln: Decimal,
) -> tuple[Decimal | None, Decimal | None]:
    if any(
        not value.is_finite()
        for value in (report_value, report_pnl, report_percent, quote_value_pln)
        if value is not None
    ):
        return None, None
    if report_value is None or report_value < 0:
        return None, None
    if report_pnl is not None:
        cost = report_value - report_pnl
    elif report_percent is not None and report_percent > -100:
        denominator = 1 + report_percent / 100
        if denominator <= 0:
            return None, None
        cost = report_value / denominator
        report_pnl = report_value - cost
    else:
        return None, None
    amount = report_pnl + quote_value_pln - report_value
    return amount, amount / cost * 100 if cost > 0 else None
