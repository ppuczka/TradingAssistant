"""Bounded session history of comparable portfolio daily estimates."""

from datetime import datetime
from decimal import Decimal

from trading_assistant.application.dashboard import DayChangeCandle


class DayChangeHistory:
    def __init__(self) -> None:
        self.candles: list[DayChangeCandle] = []
        self.context: tuple | None = None

    def record(self, now: datetime, value: Decimal | None, context: tuple) -> list[DayChangeCandle]:
        identity = (now.date(), context)
        if identity != self.context or value is None:
            self.candles = []
            self.context = identity
        if value is not None:
            minute = now.replace(second=0, microsecond=0)
            if self.candles and self.candles[-1].started_at == minute:
                candle = self.candles[-1]
                candle.high = max(candle.high, value)
                candle.low = min(candle.low, value)
                candle.close = value
            else:
                self.candles.append(
                    DayChangeCandle(
                        started_at=minute, open=value, high=value, low=value, close=value
                    )
                )
                self.candles = self.candles[-1440:]
        return [c.model_copy(deep=True) for c in self.candles]
