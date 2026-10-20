"""Terminal candle bars supplied by the application history service."""

from decimal import Decimal

from rich.text import Text
from textual.widgets import Static

from trading_assistant.application.dashboard import DashboardSnapshot


def render_day_chart(snapshot: DashboardSnapshot, width: int, *, percentage: bool = False) -> Text:
    history = snapshot.day_change_percent_history if percentage else snapshot.day_change_history
    candles = history[-max(1, (width - 15) // 3) :]
    if not candles:
        return Text(
            "Waiting for usable quotes • Observed session history collects while the app runs.",
            style="#96aaba",
        )
    result = Text()
    covered = candles[-1].covered
    total = candles[-1].total
    covered = snapshot.today_pnl_covered if covered is None else covered
    total = snapshot.today_pnl_total if total is None else total
    breaks = [
        index > 0
        and (
            candle.segment != candles[index - 1].segment
            or (candle.started_at - candles[index - 1].started_at).total_seconds() > 60
        )
        for index, candle in enumerate(candles)
    ]
    result.append(
        f"{'Partial estimate' if covered < total else 'Estimated'} • "
        f"{'%' if percentage else 'PLN'} • Latest coverage {covered}/{total} holdings • "
        "1-minute candles • observed session estimates"
        f"{' • ┆ coverage/time break' if any(breaks) else ''}\n",
        style="#96aaba",
    )
    high = max(c.high for c in candles)
    low = min(c.low for c in candles)
    if high == low:
        padding = max(abs(high) * Decimal("0.01"), Decimal("0.01") if percentage else Decimal("1"))
        high += padding
        low -= padding
    rows = 7

    def level(value: Decimal) -> int:
        return int((high - value) / (high - low) * (rows - 1))

    for y in range(rows):
        axis = high - (high - low) * Decimal(y) / Decimal(rows - 1)
        result.append(f"{axis:>11,.2f} │", style="#96aaba")
        for index, candle in enumerate(candles):
            top, bottom = level(candle.high), level(candle.low)
            body_top = level(max(candle.open, candle.close))
            body_bottom = level(min(candle.open, candle.close))
            char = "│" if top <= y <= bottom else " "
            if body_top <= y <= body_bottom:
                char = "━" if candle.open == candle.close else "█"
            color = "green" if candle.close >= candle.open else "red"
            result.append("┆" if breaks[index] else " ", style="#96aaba")
            result.append(f"{char} ", style=color)
        result.append("\n")
    result.append(
        f"             {candles[0].started_at:%m-%d %H:%M} – "
        f"{candles[-1].started_at:%m-%d %H:%M} UTC • observed history",
        style="#96aaba",
    )
    return result


class DayChangeChart(Static):
    def __init__(self, *, percentage: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.snapshot = DashboardSnapshot()
        self.percentage = percentage

    def set_snapshot(self, snapshot: DashboardSnapshot) -> None:
        self.snapshot = snapshot
        self.refresh(layout=True)

    def render(self) -> Text:
        return render_day_chart(self.snapshot, self.size.width or 80, percentage=self.percentage)
