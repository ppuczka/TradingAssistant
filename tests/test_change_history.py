from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_assistant.adapters.change_history import SqlAlchemyChangeHistoryRepository
from trading_assistant.adapters.database import (
    DailyEstimate,
    IntradayEstimate,
    database_engine,
    migrate,
)


def store(path):
    engine = database_engine(path)
    migrate(engine)
    return SqlAlchemyChangeHistoryRepository(engine)


def test_history_survives_restart_and_aggregates_exact_decimals(tmp_path):
    path = tmp_path / "history.sqlite3"
    now = datetime(2026, 10, 9, 10, tzinfo=UTC)
    repository = store(path)
    context = (("GE.US", Decimal("0.8014")),)
    repository.record(now, Decimal("10.123456789"), Decimal("1.5"), context, 1, 2)
    repository.engine.dispose()
    restarted = store(path)
    pnl, percent = restarted.record(
        now + timedelta(seconds=5), Decimal("8"), Decimal("1"), context, 1, 2
    )
    assert len(pnl) == 1
    assert pnl[0].open == Decimal("10.123456789")
    assert pnl[0].high == Decimal("10.123456789")
    assert pnl[0].low == 8 and pnl[0].close == 8
    assert percent[0].open == Decimal("1.5") and percent[0].close == 1
    pnl, _ = restarted.record(
        now + timedelta(minutes=5), Decimal("12"), Decimal("2"), context, 1, 2
    )
    assert len(pnl) == 2  # Gaps remain gaps.
    daily = restarted.daily(now + timedelta(minutes=5))
    assert len(daily) == 1 and daily[0].pnl == 12
    assert daily[0].covered == 1 and daily[0].total == 2
    with Session(restarted.engine) as session:
        assert session.scalar(select(func.count()).select_from(IntradayEstimate)) == 2


def test_retention_keeps_24_hours_and_90_calendar_days(tmp_path):
    repository = store(tmp_path / "retention.sqlite3")
    now = datetime(2026, 10, 9, 10, tzinfo=UTC)
    for days_ago in range(90, -1, -1):
        repository.record(now - timedelta(days=days_ago), Decimal(days_ago), None, (), 1, 1)
    summaries = repository.daily(now)
    assert len(summaries) == 90
    assert summaries[0].day == (now - timedelta(days=89)).date()
    with Session(repository.engine) as session:
        assert session.scalar(select(func.count()).select_from(IntradayEstimate)) == 2
        assert session.scalar(select(func.count()).select_from(DailyEstimate)) == 90
    # Retention also runs when no fresh quotes are recorded.
    repository.daily(now + timedelta(days=2))
    with Session(repository.engine) as session:
        assert session.scalar(select(func.count()).select_from(IntradayEstimate)) == 0


def test_coverage_changes_segment_history_without_erasing_saved_observations(tmp_path):
    repository = store(tmp_path / "segments.sqlite3")
    now = datetime(2026, 10, 9, 10, tzinfo=UTC)
    repository.record(now, Decimal("1"), Decimal("1"), ("A",), 1, 2)
    changed, _ = repository.record(
        now + timedelta(seconds=5), Decimal("10"), Decimal("2"), ("A", "B"), 2, 2
    )
    assert len(changed) == 2 and changed[-1].open == 10
    assert changed[0].segment != changed[-1].segment
    reverted, _ = repository.record(
        now + timedelta(seconds=10), Decimal("2"), Decimal("1"), ("A",), 1, 2
    )
    assert len(reverted) == 3 and reverted[-1].open == 2
    with Session(repository.engine) as session:
        assert session.scalar(select(func.count()).select_from(IntradayEstimate)) == 3


async def test_dashboard_restores_history_and_loads_daily_summaries_without_network(tmp_path):
    from trading_assistant.application.portfolio import PortfolioDashboardService
    from trading_assistant.domain.market import Quote
    from trading_assistant.domain.portfolio import Holding, PortfolioState

    class Portfolio:
        def portfolio(self):
            return PortfolioState(
                holdings=[
                    Holding(symbol="PKO.PL", name="Test bank", asset_type="STOCK", quantity="2")
                ],
                report_dates=[datetime.now(UTC)],
            )

    class Quotes:
        def __init__(self, price):
            self.price = price
            self.calls = 0

        async def quote(self, symbol):
            self.calls += 1
            return Quote(
                symbol=symbol,
                currency="PLN",
                price=self.price,
                previous_close="100",
                as_of=datetime.now(UTC),
                retrieved_at=datetime.now(UTC),
                source="Test",
            )

    path = tmp_path / "dashboard.sqlite3"
    first = PortfolioDashboardService(
        Portfolio(), european_provider=Quotes("110"), history_repository=store(path)
    )
    snapshot = await first.snapshot()
    assert snapshot.day_change_history[0].open == 20
    assert snapshot.daily_change_history[-1].pnl == 20
    quotes = Quotes("115")
    restarted = PortfolioDashboardService(
        Portfolio(), european_provider=quotes, history_repository=store(path)
    )
    initial = await restarted.initial_snapshot()
    assert quotes.calls == 0
    assert initial.day_change_history[0].open == 20
    assert initial.day_change_percent_history[0].open == 10
    assert initial.daily_change_history[-1].pnl == 20
    resumed = await restarted.snapshot()
    assert resumed.day_change_history[0].open == 20
    assert resumed.day_change_history[-1].close == 30
    assert resumed.daily_change_history[-1].change_percent == 15


async def test_saved_charts_remain_available_when_current_quotes_fail(tmp_path):
    from trading_assistant.application.portfolio import PortfolioDashboardService
    from trading_assistant.domain.market import MarketDataError
    from trading_assistant.domain.portfolio import Holding, PortfolioState

    saved = store(tmp_path / "offline.sqlite3")
    now = datetime.now(UTC)
    saved.record(now, Decimal("123"), Decimal("2.5"), ("full coverage",), 20, 20)

    class Portfolio:
        def portfolio(self):
            return PortfolioState(
                holdings=[Holding(symbol="PKO.PL", name="Test", asset_type="STOCK", quantity="2")],
                report_dates=[now],
            )

    class Unavailable:
        async def quote(self, symbol):
            raise MarketDataError("Unavailable")

    snapshot = await PortfolioDashboardService(
        Portfolio(), european_provider=Unavailable(), history_repository=saved
    ).snapshot()
    assert snapshot.today_pnl is None
    assert snapshot.day_change_history[0].close == 123
    assert snapshot.day_change_percent_history[0].close == Decimal("2.5")
    assert snapshot.day_change_history[0].covered == 20


def test_previous_session_observations_persist_without_creating_daily_performance(tmp_path):
    repository = store(tmp_path / "offhours.sqlite3")
    friday = datetime(2026, 10, 16, 20, tzinfo=UTC)
    repository.record(friday, Decimal("15"), Decimal("1.5"), ("Friday",), 2, 2)
    saturday = friday + timedelta(hours=10)
    history, _ = repository.record(
        saturday,
        Decimal("15"),
        Decimal("1.5"),
        ("Friday session displayed Saturday",),
        2,
        2,
        update_daily=False,
    )
    assert len(history) == 2
    daily = repository.daily(saturday)
    assert len(daily) == 1 and daily[0].day == friday.date()
    # Restart retains the newly observed close, without fabricating Saturday's return.
    repository.engine.dispose()
    restarted = store(tmp_path / "offhours.sqlite3")
    intraday, _ = restarted.intraday(saturday)
    assert intraday[-1].started_at == saturday
    assert intraday[-1].close == 15


def test_same_minute_session_transition_starts_new_segment(tmp_path):
    repository = store(tmp_path / "session-transition.sqlite3")
    now = datetime(2026, 10, 16, 13, 30, tzinfo=UTC)
    portfolio = (("GE.US", "1"), ("PKO.PL", "1"))
    before = (portfolio, (("GE.US", "2026-10-15"), ("PKO.PL", "2026-10-16")))
    after = (portfolio, (("GE.US", "2026-10-16"), ("PKO.PL", "2026-10-16")))
    repository.record(now, Decimal("20"), Decimal("2"), before, 2, 2, update_daily=False)
    candles, _ = repository.record(
        now + timedelta(seconds=5), Decimal("5"), Decimal("0.5"), after, 2, 2
    )
    assert len(candles) == 2
    assert candles[0].segment != candles[1].segment
    assert candles[1].open == candles[1].close == 5
    assert repository.daily(now)[0].pnl == 5
