from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, field_validator


class InstrumentData(BaseModel):
    broker_symbol: str
    name: str
    asset_type: Literal["STOCK", "ETF", "ETC", "UNKNOWN"]


class CashEvent(BaseModel):
    product: Literal["MY_TRADES", "IKZE"] = "MY_TRADES"
    operation_id: str
    kind: str
    occurred_at: datetime
    amount: Decimal = Field(allow_inf_nan=False)
    symbol: str | None = None
    position_id: str | None = None
    comment: str
    source_row: int
    action: Literal["BUY", "SELL"] | None = None
    quantity: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    execution_price: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)


class ReportPosition(BaseModel):
    symbol: str
    quantity: Decimal = Field(ge=0, allow_inf_nan=False)
    market_value: Decimal = Field(ge=0, allow_inf_nan=False)
    pnl_percent: Decimal | None = Field(default=None, allow_inf_nan=False)
    source_row: int
    lots: list[dict] = Field(default_factory=list)


class BrokerReport(BaseModel):
    scope: Literal["MY_TRADES", "IKZE", "MY_TRADES_IKZE"] = "MY_TRADES"
    account_number: str
    currency: Literal["PLN"]
    fingerprint: str
    as_of: datetime
    instruments: list[InstrumentData]
    cash_events: list[CashEvent]
    positions: list[ReportPosition]
    closed_records: list[dict]
    excluded_rows: int
    warnings: list[str] = Field(default_factory=list)


class ImportResult(BaseModel):
    cash_added: int = 0
    cash_duplicates: int = 0
    trades_added: int = 0
    positions: int = 0
    excluded_rows: int = 0
    already_imported: bool = False
    warnings: list[str] = Field(default_factory=list)


class Holding(BaseModel):
    symbol: str
    name: str
    asset_type: str
    quantity: Decimal
    report_value: Decimal | None = None
    report_pnl_percent: Decimal | None = None
    report_pnl_amount: Decimal | None = None


class PortfolioState(BaseModel):
    holdings: list[Holding] = Field(default_factory=list)
    report_dates: list[datetime] = Field(default_factory=list)
    cash_movement: Decimal = Decimal("0")
    cash_balance: Decimal | None = None
    cash_balance_dates: list[datetime] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ImportValidationError(ValueError):
    """Invalid report, conflict, or failed ledger reconciliation."""


class CashBalanceInput(BaseModel):
    """A human-confirmed My Trades cash observation, not a ledger event."""

    amount: Decimal = Field(ge=0, allow_inf_nan=False)
    as_of: AwareDatetime

    @field_validator("as_of")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        value = value.astimezone(UTC)
        if value > datetime.now(UTC):
            raise ValueError("Cash balance timestamp cannot be in the future.")
        return value
