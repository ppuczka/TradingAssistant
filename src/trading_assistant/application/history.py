"""Persistence boundary for portfolio estimate history."""

from datetime import datetime
from decimal import Decimal
from typing import Protocol

from trading_assistant.domain.history import DailyChangeSummary, DayChangeCandle


class ChangeHistoryRepository(Protocol):
    def record(
        self,
        now: datetime,
        pnl: Decimal,
        percent: Decimal | None,
        context: tuple,
        covered: int,
        total: int,
        *,
        update_daily: bool = True,
    ) -> tuple[list[DayChangeCandle], list[DayChangeCandle]]: ...

    def daily(self, now: datetime) -> list[DailyChangeSummary]: ...

    def intraday(self, now: datetime) -> tuple[list[DayChangeCandle], list[DayChangeCandle]]: ...
