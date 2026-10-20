"""Verified market-data candidates, not AI recommendations or broker instruments."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field

Market = Literal["US", "PL"]


class StockCandidate(BaseModel):
    symbol: str
    name: str = Field(min_length=1)
    market: Market
    exchange: str
    currency: Literal["USD", "PLN"]
    price: Decimal = Field(gt=0, allow_inf_nan=False)
    market_cap: Decimal = Field(gt=0, allow_inf_nan=False)
    average_volume: Decimal = Field(ge=0, allow_inf_nan=False)
    quote_as_of: AwareDatetime
    fetched_at: AwareDatetime
    source: str = "Yahoo screener (unofficial)"
    broker_available: bool | None = None


class CandidatePage(BaseModel):
    candidates: list[StockCandidate]
    received: int
    total: int
    rejected: int = 0


class CandidateBatch(BaseModel):
    candidates: list[StockCandidate] = Field(default_factory=list)
    fetched_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    excluded_owned: int = 0
    rejected: int = 0
    warnings: list[str] = Field(default_factory=list)
    errors: dict[str, str] = Field(default_factory=dict)


class CandidateUnavailableError(ValueError):
    pass
