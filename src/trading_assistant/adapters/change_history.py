"""Atomic minute aggregates and latest daily estimates with bounded retention."""

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from trading_assistant.adapters.database import DailyEstimate, IntradayEstimate
from trading_assistant.domain.history import DailyChangeSummary, DayChangeCandle


def stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def candle_payload(previous: dict | None, now: datetime, value: Decimal) -> dict:
    candle = (
        DayChangeCandle.model_validate(previous)
        if previous
        else DayChangeCandle(
            started_at=now,
            open=value,
            high=value,
            low=value,
            close=value,
        )
    )
    candle.high = max(candle.high, value)
    candle.low = min(candle.low, value)
    candle.close = value
    return candle.model_dump(mode="json")


class SqlAlchemyChangeHistoryRepository:
    def __init__(self, engine):
        self.engine = engine

    @staticmethod
    def _prune(session, now):
        session.execute(
            delete(IntradayEstimate).where(
                IntradayEstimate.minute
                < stamp((now - timedelta(hours=24)).replace(second=0, microsecond=0))
            )
        )
        session.execute(
            delete(DailyEstimate).where(
                DailyEstimate.day < (now.date() - timedelta(days=89)).isoformat()
            )
        )

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
    ):
        now = now.astimezone(UTC)
        minute = now.replace(second=0, microsecond=0)
        fingerprint = hashlib.sha256(repr(context).encode()).hexdigest()
        with Session(self.engine) as session, session.begin():
            self._prune(session, now)
            last = session.scalar(
                select(IntradayEstimate)
                .order_by(IntradayEstimate.observed_at.desc(), IntradayEstimate.id.desc())
                .limit(1)
            )
            # Coverage/holdings changes form a new segment even if they later revert.
            segment = (
                last.segment
                if last
                and last.context == fingerprint
                and datetime.fromisoformat(last.minute).date() == now.date()
                else stamp(now)
            )
            row = session.scalar(
                select(IntradayEstimate).where(
                    IntradayEstimate.minute == stamp(minute), IntradayEstimate.segment == segment
                )
            )
            if row is None:
                row = IntradayEstimate(
                    minute=stamp(minute),
                    segment=segment,
                    context=fingerprint,
                    observed_at=stamp(now),
                    covered=covered,
                    total=total,
                )
                session.add(row)
            row.pnl = candle_payload(row.pnl, minute, pnl)
            row.percent = (
                candle_payload(row.percent, minute, percent) if percent is not None else None
            )
            row.observed_at = stamp(now)
            if update_daily:
                summary = session.get(DailyEstimate, now.date().isoformat())
                if summary is None:
                    summary = DailyEstimate(day=now.date().isoformat())
                    session.add(summary)
                summary.payload = DailyChangeSummary(
                    day=now.date(),
                    observed_at=now,
                    pnl=pnl,
                    change_percent=percent,
                    covered=covered,
                    total=total,
                ).model_dump(mode="json")
            session.flush()
            return self._read_intraday(session)

    @staticmethod
    def _read_intraday(session):
        rows = session.scalars(
            select(IntradayEstimate).order_by(
                IntradayEstimate.minute, IntradayEstimate.observed_at, IntradayEstimate.id
            )
        ).all()

        def candle(row, payload):
            return DayChangeCandle.model_validate(payload).model_copy(
                update={
                    "segment": row.segment,
                    "covered": row.covered,
                    "total": row.total,
                }
            )

        return ([candle(r, r.pnl) for r in rows], [candle(r, r.percent) for r in rows if r.percent])

    def intraday(self, now: datetime):
        with Session(self.engine) as session, session.begin():
            self._prune(session, now.astimezone(UTC))
            return self._read_intraday(session)

    def daily(self, now: datetime) -> list[DailyChangeSummary]:
        now = now.astimezone(UTC)
        with Session(self.engine) as session, session.begin():
            self._prune(session, now)
            return [
                DailyChangeSummary.model_validate(row.payload)
                for row in session.scalars(select(DailyEstimate).order_by(DailyEstimate.day))
            ]
