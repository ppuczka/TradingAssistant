"""Read explicit report price labels; never infer levels from narrative or quotes."""

import re
from decimal import Decimal

from trading_assistant.domain.analysis import AnalysisRequest, Recommendation


def with_report_levels(request: AnalysisRequest, result: Recommendation) -> Recommendation:
    report = result.reports.get("trading_plan", "")
    known_currency = (
        request.candidate.currency
        if request.candidate
        else request.instrument.currency
        if request.instrument
        else None
    )
    if known_currency in {"GBp", "GBX"}:
        return result
    updates = {}
    for label, field, currency_field in [
        ("Entry Price", "entry", "entry_currency"),
        ("Stop Loss", "stop_loss", "stop_loss_currency"),
    ]:
        if getattr(result, field) is not None:
            continue
        values = []
        for line in report.splitlines():
            plain = line.replace("**", "").replace("`", "").strip()
            plain = re.sub(r"^[-*]\s+", "", plain)
            match = re.fullmatch(re.escape(label) + r"\s*:\s*(.*?)", plain, re.IGNORECASE)
            if match:
                values.append(match[1])
        if len(values) != 1:
            continue
        match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(?:\s+(USD|PLN|EUR|GBP))?", values[0])
        if not match or Decimal(match[1]) <= 0:
            continue
        currency = match[2] or known_currency
        if known_currency and currency != known_currency:
            continue
        updates[field] = Decimal(match[1])
        updates[currency_field] = currency
    return result.model_copy(update=updates)
