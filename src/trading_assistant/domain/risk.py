"""Report-based concentration observations, not order authorization."""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class RiskSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_position_percent: Decimal = Field(default=Decimal("20"), ge=0, le=100)
    max_top_five_percent: Decimal = Field(default=Decimal("60"), ge=0, le=100)
    min_cash_percent: Decimal = Field(default=Decimal("10"), ge=0, le=100)


class PortfolioRiskSummary(BaseModel):
    status: str
    basis: str
    largest_symbol: str | None = None
    largest_percent: Decimal | None = None
    top_five_percent: Decimal | None = None
    cash_percent: Decimal | None = None
    warnings: list[str] = Field(default_factory=list)
