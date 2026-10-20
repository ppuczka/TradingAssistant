from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_assistant.application.dashboard import DashboardSnapshot
from trading_assistant.application.day_history import DayChangeHistory
from trading_assistant.tui.day_chart import render_day_chart


def test_candles_aggregate_observed_values_and_return_independent_snapshots():
    history = DayChangeHistory()
    start = datetime(2026, 10, 9, 10, tzinfo=UTC)
    first = history.record(start, Decimal("10"), ("same holdings",))
    history.record(start + timedelta(seconds=5), Decimal("15"), ("same holdings",))
    candles = history.record(start + timedelta(seconds=10), Decimal("8"), ("same holdings",))
    assert (candles[0].open, candles[0].high, candles[0].low, candles[0].close) == (10, 15, 8, 8)
    assert first[0].close == 10
    candles = history.record(start + timedelta(minutes=3), Decimal("12"), ("same holdings",))
    assert len(candles) == 2  # No invented candles in the gap.


def test_history_resets_on_coverage_day_or_missing_data():
    history = DayChangeHistory()
    start = datetime(2026, 10, 9, 10, tzinfo=UTC)
    history.record(start, Decimal("10"), ("AAA",))
    changed = history.record(start + timedelta(minutes=1), Decimal("100"), ("AAA", "BBB"))
    assert len(changed) == 1 and changed[0].open == 100
    next_day = history.record(start + timedelta(days=1), Decimal("5"), ("AAA", "BBB"))
    assert len(next_day) == 1 and next_day[0].open == 5
    assert history.record(start + timedelta(days=1, minutes=1), None, ("AAA", "BBB")) == []


def test_chart_renders_flat_negative_values_and_partial_coverage():
    history = DayChangeHistory()
    start = datetime(2026, 10, 9, 10, tzinfo=UTC)
    candles = history.record(start, Decimal("-12"), ())
    snapshot = DashboardSnapshot(
        day_change_history=candles, today_pnl_covered=12, today_pnl_total=20
    )
    rendered = render_day_chart(snapshot, 80)
    assert "Partial estimate" in rendered.plain
    assert "12/20 holdings" in rendered.plain
    assert "10:00" in rendered.plain
    assert "━" in rendered.plain
    history.record(start + timedelta(seconds=5), Decimal("-15"), ())
    snapshot.day_change_history = history.record(start + timedelta(seconds=10), Decimal("-10"), ())
    rendered = render_day_chart(snapshot, 80)
    assert "█" in rendered.plain
    assert "│" in rendered.plain
    assert any(span.style == "green" for span in rendered.spans)
    assert "Waiting" in render_day_chart(DashboardSnapshot(), 40).plain


async def test_chart_is_between_summary_and_portfolio_at_dashboard_sizes():
    from textual.widgets import DataTable, Static

    from trading_assistant.tui.dashboard import DashboardApp
    from trading_assistant.tui.day_chart import DayChangeChart

    history = DayChangeHistory()
    start = datetime(2026, 10, 9, 10, tzinfo=UTC)
    for minute, value in enumerate([10, 20, 15]):
        history.record(start + timedelta(minutes=minute), Decimal(value), ())
    snapshot = DashboardSnapshot(
        currency="PLN",
        today_pnl=Decimal("15"),
        today_pnl_covered=12,
        today_pnl_total=20,
        day_change_history=history.candles,
    )

    class Service:
        async def snapshot(self):
            return snapshot

    for size in [(80, 24), (140, 45)]:
        app = DashboardApp(Service())
        async with app.run_test(size=size) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            chart = app.query_one(DayChangeChart)
            assert "12/20 holdings" in chart.render().plain
            assert app.query_one("#metric-today", Static).region.y < chart.region.y
            assert chart.region.y < app.query_one("#portfolio-table", DataTable).region.y


def test_saved_segments_show_breaks_and_saved_coverage():
    from trading_assistant.domain.history import DayChangeCandle

    now = datetime(2026, 10, 9, 10, tzinfo=UTC)
    snapshot = DashboardSnapshot(
        day_change_history=[
            DayChangeCandle(
                started_at=now,
                open=10,
                high=10,
                low=10,
                close=10,
                segment="first",
                covered=20,
                total=20,
            ),
            DayChangeCandle(
                started_at=now + timedelta(minutes=1),
                open=2,
                high=2,
                low=2,
                close=2,
                segment="second",
                covered=12,
                total=20,
            ),
        ],
    )
    chart = render_day_chart(snapshot, 140).plain
    assert "Latest coverage 12/20" in chart
    assert "┆ coverage/time break" in chart
    assert "10-09 10:00" in chart


def test_bars_select_percentage_history_and_show_recent_observations():
    from trading_assistant.domain.history import DayChangeCandle

    now = datetime(2026, 10, 9, tzinfo=UTC)
    points = [
        DayChangeCandle(
            started_at=now + timedelta(minutes=i), open=i, high=i, low=i, close=i, segment="same"
        )
        for i in range(120)
    ]
    snapshot = DashboardSnapshot(day_change_percent_history=points)
    chart = render_day_chart(snapshot, 80, percentage=True)
    assert "• % •" in chart.plain
    assert "10-09 01:39" in chart.plain and "10-09 01:59" in chart.plain
    assert "█" not in chart.plain
    assert "1-minute candles" in chart.plain
    assert "Waiting" in render_day_chart(snapshot, 80).plain
