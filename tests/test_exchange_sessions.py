from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_assistant.adapters.exchange_sessions import ExchangeSessionProvider
from trading_assistant.application.portfolio import PortfolioDashboardService
from trading_assistant.domain.market import Quote
from trading_assistant.domain.portfolio import Holding, PortfolioState


def at(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@pytest.mark.parametrize(
    "symbol,now,session,closed,close",
    [
        ("GE.US", "2026-10-16T11:00Z", "2026-10-15", True, "2026-10-15T20:00Z"),
        ("GE.US", "2026-10-16T15:00Z", "2026-10-16", False, "2026-10-16T20:00Z"),
        ("GE.US", "2026-10-17T10:00Z", "2026-10-16", True, "2026-10-16T20:00Z"),
        ("GE.US", "2026-11-27T19:00Z", "2026-11-27", True, "2026-11-27T18:00Z"),
        ("GE.US", "2026-11-26T15:00Z", "2026-11-25", True, "2026-11-25T21:00Z"),
        ("PKO.PL", "2026-10-16T06:00Z", "2026-10-15", True, "2026-10-15T15:00Z"),
        ("PKO.PL", "2026-10-16T16:00Z", "2026-10-16", True, "2026-10-16T15:00Z"),
        ("RHM.DE", "2026-10-16T16:00Z", "2026-10-16", True, "2026-10-16T15:30Z"),
        ("COPA.UK", "2026-10-16T16:00Z", "2026-10-16", True, "2026-10-16T15:30Z"),
    ],
)
def test_exchange_holidays_weekends_dst_and_early_close(symbol, now, session, closed, close):
    result = ExchangeSessionProvider().state(symbol, at(now))
    assert result.session_date.isoformat() == session
    assert result.closed == closed
    assert result.close_at == at(close)


@pytest.mark.parametrize(
    "now,quote_time,expected",
    [
        ("2026-10-16T06:00Z", "2026-10-15T15:00Z", True),
        ("2026-10-18T06:00Z", "2026-10-16T15:00Z", True),
        ("2026-10-16T08:00Z", "2026-10-15T15:00Z", False),
        ("2026-10-16T08:00Z", "2026-10-16T06:00Z", False),
        ("2026-10-16T06:00Z", "2026-10-15T11:00Z", False),
        ("2026-10-16T06:00Z", "2026-10-14T15:00Z", False),
    ],
)
async def test_off_hours_closing_change_without_rewriting_history(
    now, quote_time, expected, monkeypatch
):
    from trading_assistant.application import portfolio

    real_datetime = datetime

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return at(now).astimezone(tz or UTC)

    monkeypatch.setattr(portfolio, "datetime", Clock)

    class Repository:
        def portfolio(self):
            return PortfolioState(
                holdings=[Holding(symbol="PKO.PL", name="PKO", asset_type="STOCK", quantity="2")]
            )

    class Provider:
        async def quote(self, symbol):
            return Quote(
                symbol=symbol,
                currency="PLN",
                price="110",
                previous_close="100",
                as_of=at(quote_time),
                retrieved_at=real_datetime.now(UTC),
                source="Synthetic",
            )

    class History:
        writes = 0

        def intraday(self, now):
            return [], []

        def daily(self, now):
            return []

        daily_updates = []

        def record(self, *args, **kwargs):
            self.daily_updates.append(kwargs.get("update_daily", True))
            self.writes += 1
            return [], []

    history = History()
    snapshot = await PortfolioDashboardService(
        Repository(),
        european_provider=Provider(),
        session_provider=ExchangeSessionProvider(),
        history_repository=history,
    ).snapshot()
    assert snapshot.positions[0].quote_daily_change_percent == (Decimal("10") if expected else None)
    assert snapshot.today_pnl == (Decimal("20") if expected else None)
    if expected:
        assert snapshot.today_pnl_label == "Session P/L"
        assert "latest completed sessions" in snapshot.today_pnl_status
    assert history.writes == (1 if expected else 0)
    assert not any(history.daily_updates)


async def test_europe_trading_us_previous_close_does_not_freeze_graph(tmp_path, monkeypatch):
    from trading_assistant.adapters.change_history import SqlAlchemyChangeHistoryRepository
    from trading_assistant.adapters.database import database_engine, migrate
    from trading_assistant.application import portfolio
    from trading_assistant.domain.fx import FxRate

    now = at("2026-10-16T10:00Z")

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz or UTC)

    monkeypatch.setattr(portfolio, "datetime", Clock)

    class Repository:
        def portfolio(self):
            return PortfolioState(
                holdings=[
                    Holding(symbol=s, name=s, asset_type="STOCK", quantity="1")
                    for s in ["GE.US", "PKO.PL"]
                ],
                report_dates=[now],
            )

    class Provider:
        price = "110"

        async def quote(self, symbol):
            us = symbol == "GE"
            return Quote(
                symbol=symbol,
                currency="USD" if us else "PLN",
                price="110" if us else self.price,
                previous_close="100",
                as_of=at("2026-10-15T20:00Z") if us else now,
                retrieved_at=now,
                source="Synthetic",
            )

    class Fx:
        async def usd_pln(self):
            return FxRate(
                rate="4", effective_date=now.date(), retrieved_at=now, source_reference="synthetic"
            )

    engine = database_engine(tmp_path / "mixed.sqlite3")
    migrate(engine)
    history = SqlAlchemyChangeHistoryRepository(engine)
    provider = Provider()
    service = PortfolioDashboardService(
        Repository(),
        Fx(),
        provider,
        provider,
        history_repository=history,
        session_provider=ExchangeSessionProvider(),
    )
    first = await service.snapshot()
    assert first.today_pnl == 50 and len(first.day_change_history) == 1
    now = at("2026-10-16T10:01Z")
    provider.price = "111"
    second = await service.snapshot()
    assert second.today_pnl == 51 and len(second.day_change_history) == 2
    assert second.day_change_history[-1].close == 51
    assert history.daily(now) == []  # mixed session estimates are not today's daily return
