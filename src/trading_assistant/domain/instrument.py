"""Verified listing identity for manually selected analysis targets."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field


class AnalysisInstrument(BaseModel):
    symbol: str
    name: str
    asset_type: Literal["STOCK", "ETF"]
    exchange: str
    currency: Literal["USD", "PLN", "EUR", "GBP", "GBp", "GBX"]
    verified_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
