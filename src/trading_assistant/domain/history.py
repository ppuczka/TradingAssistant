"""Observed portfolio estimates, not audited account returns."""

from datetime import date
from decimal import Decimal

from pydantic import AwareDatetime, BaseModel, Field


class DayChangeCandle(BaseModel):
    started_at: AwareDatetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    segment: str | None = None
    covered: int | None = None
    total: int | None = None


class DailyChangeSummary(BaseModel):
    day: date
    observed_at: AwareDatetime
    pnl: Decimal
    change_percent: Decimal | None
    covered: int = Field(ge=0)
    total: int = Field(ge=0)
