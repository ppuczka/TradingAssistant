"""Regular exchange sessions, including holidays, DST and scheduled early closes."""

from datetime import datetime
from functools import lru_cache

import exchange_calendars as xcals
import pandas as pd

from trading_assistant.application.market import SymbolResolver
from trading_assistant.domain.market import MarketSession


@lru_cache(maxsize=4)
def calendar(name):
    return xcals.get_calendar(name)


class ExchangeSessionProvider:
    def state(self, symbol: str, now: datetime) -> MarketSession | None:
        name = SymbolResolver().calendar_name(symbol)
        if name is None:
            return None
        exchange = calendar(name)
        timestamp = pd.Timestamp(now)
        local_date = timestamp.tz_convert(exchange.tz).date()
        session = exchange.date_to_session(local_date, direction="previous")
        if timestamp < exchange.session_open(session):
            session = exchange.previous_session(session)
        close = exchange.session_close(session)
        return MarketSession(
            session_date=session.date(),
            timezone=str(exchange.tz),
            closed=timestamp >= close,
            open_at=exchange.session_open(session).to_pydatetime(),
            close_at=close.to_pydatetime(),
        )
