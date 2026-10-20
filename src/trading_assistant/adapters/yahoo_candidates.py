"""Bounded Yahoo equity screening with explicit listing and liquidity validation."""

import asyncio
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import yfinance as yf

from trading_assistant.domain.candidates import (
    CandidatePage,
    CandidateUnavailableError,
    StockCandidate,
)

EXCHANGES = {"US": ("NYQ", "NMS", "NGM", "NCM", "ASE"), "PL": ("WSE",)}
MIN_CAP = {"US": Decimal("1000000000"), "PL": Decimal("100000000")}
MIN_VOLUME = {"US": Decimal("100000"), "PL": Decimal("10000")}


class YahooCandidateProvider:
    def __init__(self, fetch: Callable | None = None):
        self.fetch = fetch or self._fetch
        self.cache: dict[tuple, tuple[float, CandidatePage]] = {}
        self.lock = asyncio.Lock()

    @staticmethod
    def _fetch(market: str, offset: int, size: int) -> dict:
        query = yf.EquityQuery(
            "and",
            [
                yf.EquityQuery("eq", ["region", market.lower()]),
                yf.EquityQuery("is-in", ["exchange", *EXCHANGES[market]]),
                yf.EquityQuery("gte", ["intradaymarketcap", float(MIN_CAP[market])]),
                yf.EquityQuery("gte", ["avgdailyvol3m", float(MIN_VOLUME[market])]),
            ],
        )
        return yf.screen(
            query, offset=offset, size=size, sortField="intradaymarketcap", sortAsc=False
        )

    async def page(self, market: str, offset: int, size: int) -> CandidatePage:
        if market not in EXCHANGES:
            raise CandidateUnavailableError("Candidate market must be US or PL.")
        key = (market, offset, size)
        async with self.lock:
            cached = self.cache.get(key)
            if cached and time.monotonic() < cached[0]:
                return cached[1].model_copy(deep=True)
            try:
                data = await asyncio.to_thread(self.fetch, market, offset, size)
                raw = data["quotes"]
                total = data["total"]
                if (
                    not isinstance(raw, list)
                    or isinstance(total, bool)
                    or not isinstance(total, int)
                    or total < 0
                ):
                    raise ValueError("Invalid screener response")
            except Exception:
                raise CandidateUnavailableError("Yahoo screener unavailable or invalid.") from None
            now = datetime.now(UTC)
            candidates, rejected = [], 0
            for quote in raw:
                try:
                    if quote["quoteType"] != "EQUITY" or quote["exchange"] not in EXCHANGES[market]:
                        raise ValueError("Wrong listing")
                    symbol = quote["symbol"]
                    pattern = (
                        r"[A-Z][A-Z0-9-]{0,14}" if market == "US" else r"[A-Z0-9][A-Z0-9]{0,14}\.WA"
                    )
                    if not re.fullmatch(pattern, symbol) or quote["currency"] != (
                        "USD" if market == "US" else "PLN"
                    ):
                        raise ValueError("Wrong symbol/currency")
                    timestamp = quote["regularMarketTime"]
                    if (
                        isinstance(timestamp, bool)
                        or not isinstance(timestamp, int)
                        or timestamp <= 0
                    ):
                        raise ValueError("Invalid quote time")
                    as_of = datetime.fromtimestamp(timestamp, UTC)
                    if as_of > now:
                        raise ValueError("Future quote")
                    numbers = [
                        quote["regularMarketPrice"],
                        quote["marketCap"],
                        quote["averageDailyVolume3Month"],
                    ]
                    if any(isinstance(v, bool) for v in numbers):
                        raise ValueError("Invalid numeric field")
                    price, cap, volume = [Decimal(str(v)) for v in numbers]
                    if cap < MIN_CAP[market] or volume < MIN_VOLUME[market]:
                        raise ValueError("Liquidity filter failed")
                    candidates.append(
                        StockCandidate(
                            symbol=symbol,
                            name=quote.get("longName") or quote["shortName"],
                            market=market,
                            exchange=quote["exchange"],
                            currency=quote["currency"],
                            price=price,
                            market_cap=cap,
                            average_volume=volume,
                            quote_as_of=as_of,
                            fetched_at=now,
                        )
                    )
                except (ValueError, KeyError, TypeError, OverflowError, OSError, InvalidOperation):
                    rejected += 1
            result = CandidatePage(
                candidates=candidates, received=len(raw), total=total, rejected=rejected
            )
            self.cache[key] = (time.monotonic() + 900, result)
            return result.model_copy(deep=True)
