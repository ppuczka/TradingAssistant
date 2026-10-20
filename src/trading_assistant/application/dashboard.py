"""Read-only dashboard contract, independent of terminal widgets and persistence."""

from datetime import datetime
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, Field

from trading_assistant.domain.history import DailyChangeSummary, DayChangeCandle
from trading_assistant.domain.news import NewsArticle
from trading_assistant.domain.risk import PortfolioRiskSummary


class PortfolioRow(BaseModel):
    symbol: str
    name: str = ""
    quantity: Decimal | None = None
    market_value: Decimal | None = None
    market_value_usd: Decimal | None = None
    currency: str
    pnl_percent: Decimal | None = None
    recommendation: str | None = None
    recommendation_as_of: datetime | None = None
    quote_price_usd: Decimal | None = None
    quote_value_usd: Decimal | None = None
    quote_price: Decimal | None = None
    quote_value: Decimal | None = None
    quote_value_pln: Decimal | None = None
    estimated_pnl_pln: Decimal | None = None
    estimated_pnl_percent: Decimal | None = None
    quote_currency: str | None = None
    quote_daily_change_percent: Decimal | None = None
    quote_daily_change: Decimal | None = None
    quote_status: str = "Report only"
    quote_as_of: datetime | None = None
    quote_source: str | None = None


class AllocationRow(BaseModel):
    label: str
    weight_percent: Decimal = Field(ge=0, le=100)


class OpportunityRow(BaseModel):
    symbol: str
    action: str
    engine_rating: str | None = None
    entry: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    entry_currency: str | None = None
    stop_loss: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    stop_loss_currency: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class DashboardSnapshot(BaseModel):
    currency: str | None = None
    portfolio_value: Decimal | None = None
    cash: Decimal | None = None
    cash_as_of: str | None = None
    today_pnl: Decimal | None = None
    today_pnl_label: str = "Today's P/L"
    today_pnl_status: str | None = None
    today_pnl_covered: int = Field(default=0, ge=0)
    today_pnl_total: int = Field(default=0, ge=0)
    day_change_history: list[DayChangeCandle] = Field(default_factory=list)
    today_change_percent: Decimal | None = None
    day_change_percent_history: list[DayChangeCandle] = Field(default_factory=list)
    daily_change_history: list[DailyChangeSummary] = Field(default_factory=list)
    total_pnl: Decimal | None = None
    valuation_as_of: str | None = None
    holdings_value: Decimal | None = None
    holdings_value_usd: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    unrealized_pnl_usd: Decimal | None = None
    estimated_unrealized_pnl: Decimal | None = None
    estimated_unrealized_pnl_usd: Decimal | None = None
    estimated_pnl_covered: int = 0
    estimated_pnl_total: int = 0
    fx_status: str | None = None
    market_status: str | None = None
    positions: list[PortfolioRow] = Field(default_factory=list)
    allocation: list[AllocationRow] = Field(default_factory=list)
    allocation_status: str = "No report valuations available."
    analyzed_positions: int = 0
    saved_opportunity_count: int = 0
    opportunities: list[OpportunityRow] = Field(default_factory=list)
    market_sentiment: str = "Portfolio-wide summary not implemented"
    portfolio_risk: str = "Not assessed"
    risk_summary: PortfolioRiskSummary | None = None
    last_analysis: str = "Never"
    next_scan: str = "Manual • S to discover candidates"
    alerts: list[str] = Field(default_factory=list)
    news: list[NewsArticle] = Field(default_factory=list)
    news_status: str = "News provider not configured."


class DashboardService(Protocol):
    async def snapshot(self) -> DashboardSnapshot: ...


class EmptyDashboardService:
    """Initial state until portfolio and analysis services are implemented."""

    async def snapshot(self) -> DashboardSnapshot:
        return DashboardSnapshot()
