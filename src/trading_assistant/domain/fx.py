from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class FxRate(BaseModel):
    base: Literal["USD", "EUR", "GBP"] = "USD"
    quote: Literal["PLN"] = "PLN"
    rate: Decimal = Field(gt=0, allow_inf_nan=False)
    effective_date: date
    retrieved_at: datetime
    source: Literal["NBP table A"] = "NBP table A"
    source_reference: str
    stale: bool = False


class FxUnavailableError(ValueError):
    """No validated currency rate is available."""
