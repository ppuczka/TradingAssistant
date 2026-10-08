"""Provider-independent market quote contracts."""

from datetime import datetime
from decimal import Decimal

from pydantic import AwareDatetime, BaseModel, Field


class Quote(BaseModel):
    symbol: str
    name: str | None = None
    currency: str
    price: Decimal = Field(gt=0, allow_inf_nan=False)
    previous_close: Decimal = Field(gt=0, allow_inf_nan=False)
    as_of: AwareDatetime
    retrieved_at: datetime
    source: str


class MarketDataError(Exception):
    """Sanitized provider failure safe to display to the user."""
