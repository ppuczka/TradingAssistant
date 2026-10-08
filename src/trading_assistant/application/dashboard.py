"""Read-only dashboard contract, independent of terminal widgets and persistence."""

from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, Field


class PortfolioRow(BaseModel):
    symbol: str
    name: str = ""
    quantity: Decimal | None = None
    market_value: Decimal | None = None
    market_value_usd: Decimal | None = None
    currency: str
    pnl_percent: Decimal | None = None
    recommendation: str | None = None
    quote_price_usd: Decimal | None = None
    quote_value_usd: Decimal | None = None
    quote_price: Decimal | None = None
    quote_value: Decimal | None = None
    quote_currency: str | None = None
    quote_daily_change_percent: Decimal | None = None
    quote_daily_change: Decimal | None = None
    quote_status: str = "Report only"


class AllocationRow(BaseModel):
    label: str
    weight_percent: Decimal = Field(ge=0, le=100)


class OpportunityRow(BaseModel):
    symbol: str
    action: str
    confidence: float | None = Field(default=None, ge=0, le=1)


class DashboardSnapshot(BaseModel):
    currency: str | None = None
    portfolio_value: Decimal | None = None
    cash: Decimal | None = None
    cash_as_of: str | None = None
    today_pnl: Decimal | None = None
    today_pnl_status: str | None = None
    total_pnl: Decimal | None = None
    valuation_as_of: str | None = None
    holdings_value: Decimal | None = None
    holdings_value_usd: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    unrealized_pnl_usd: Decimal | None = None
    fx_status: str | None = None
    market_status: str | None = None
    positions: list[PortfolioRow] = Field(default_factory=list)
    allocation: list[AllocationRow] = Field(default_factory=list)
    opportunities: list[OpportunityRow] = Field(default_factory=list)
    market_sentiment: str = "Not analyzed"
    portfolio_risk: str = "Not assessed"
    last_analysis: str = "Never"
    next_scan: str = "Not scheduled"
    alerts: list[str] = Field(default_factory=list)


class DashboardService(Protocol):
    async def snapshot(self) -> DashboardSnapshot: ...


class EmptyDashboardService:
    """Initial state until portfolio and analysis services are implemented."""

    async def snapshot(self) -> DashboardSnapshot:
        return DashboardSnapshot()
