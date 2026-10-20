"""Application-owned analysis requests and normalized recommendations."""

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator

from trading_assistant.domain.candidates import StockCandidate
from trading_assistant.domain.instrument import AnalysisInstrument


class Strategy(StrEnum):
    LONG_TERM = "LONG_TERM"
    POSITION = "POSITION"
    TRADE = "TRADE"


class Action(StrEnum):
    ADD = "ADD"
    HOLD = "HOLD"
    REDUCE = "REDUCE"
    SELL = "SELL"
    BUY = "BUY"
    WATCH = "WATCH"
    IGNORE = "IGNORE"


class PositionContext(BaseModel):
    broker_symbol: str
    canonical_symbol: str | None = None
    quantity: Decimal = Field(gt=0, allow_inf_nan=False)
    report_value_pln: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    report_pnl_pln: Decimal | None = Field(default=None, allow_inf_nan=False)
    report_pnl_percent: Decimal | None = Field(default=None, allow_inf_nan=False)
    asset_type: str | None = None
    name: str | None = None


class PortfolioContext(BaseModel):
    positions: list[PositionContext] = Field(default_factory=list)
    cash_pln: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    account_value_pln: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    report_dates: list[AwareDatetime] = Field(default_factory=list)
    cash_dates: list[AwareDatetime] = Field(default_factory=list)


class AnalysisRequest(BaseModel):
    symbol: str = Field(
        pattern=r"^(?:[A-Z][A-Z0-9-]{0,14}(?:\.(?:DE|L))?|[A-Z0-9][A-Z0-9]{0,14}\.WA)$"
    )
    asset_type: Literal["STOCK", "ETF", "ETC"] = "STOCK"
    as_of: date = Field(default_factory=lambda: datetime.now(UTC).date())
    strategy: Strategy = Strategy.LONG_TERM
    time_horizon: str = Field(min_length=1, max_length=100)
    portfolio: PortfolioContext
    candidate: StockCandidate | None = None
    instrument: AnalysisInstrument | None = None

    @field_validator("time_horizon")
    @classmethod
    def validate_horizon(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Time horizon cannot be blank.")
        return value.strip()

    @property
    def owned(self) -> bool:
        return any(p.canonical_symbol == self.symbol for p in self.portfolio.positions)

    @model_validator(mode="after")
    def validate_date(self):
        if self.instrument is not None and self.instrument.symbol != self.symbol:
            raise ValueError("Instrument identity must match the analysis target.")
        if self.candidate is not None and (self.candidate.symbol != self.symbol or self.owned):
            raise ValueError("Candidate identity must match an unowned analysis target.")
        if self.as_of > datetime.now(UTC).date():
            raise ValueError("Analysis date cannot be in the future.")
        return self


class Recommendation(BaseModel):
    symbol: str
    action: Action
    owned: bool
    engine: str = "TradingAgents"
    engine_rating: str
    analyzed_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    reasoning: list[str] = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    entry: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    entry_currency: Literal["USD", "PLN", "EUR", "GBP"] | None = None
    stop_loss: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    stop_loss_currency: Literal["USD", "PLN", "EUR", "GBP"] | None = None
    target: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    timeframe: str | None = None
    risk: str | None = None
    reports: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_action(self):
        allowed = (
            {Action.ADD, Action.HOLD, Action.REDUCE, Action.SELL}
            if self.owned
            else {
                Action.BUY,
                Action.WATCH,
                Action.IGNORE,
            }
        )
        if self.action not in allowed or not all(reason.strip() for reason in self.reasoning):
            raise ValueError("Action or reasoning is invalid for this position.")
        return self


class AnalysisFailure(ValueError):
    """Configuration, engine execution, or normalization failed."""


class SavedAnalysis(BaseModel):
    request: AnalysisRequest
    recommendation: Recommendation

    @model_validator(mode="after")
    def validate_identity(self):
        if (
            self.request.symbol != self.recommendation.symbol
            or self.request.owned != self.recommendation.owned
        ):
            raise ValueError("Saved recommendation does not match its input.")
        return self
