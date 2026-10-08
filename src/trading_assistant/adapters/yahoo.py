"""Unofficial Yahoo quotes through yfinance, off the UI loop and cached for a minute."""

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import yfinance as yf

from trading_assistant.domain.market import MarketDataError, Quote


class YahooMarketDataProvider:
    def __init__(self, cache_path: Path, fetch_info: Callable[[str], dict] | None = None):
        if fetch_info is None:
            yf.set_tz_cache_location(str(cache_path))
        self.fetch_info = fetch_info or (lambda symbol: yf.Ticker(symbol).get_info())
        self.cache: dict[str, tuple[float, Quote | MarketDataError]] = {}
        self.lock = asyncio.Lock()

    async def quote(self, symbol: str) -> Quote:
        async with self.lock:
            cached = self.cache.get(symbol)
            if cached and time.monotonic() < cached[0]:
                if isinstance(cached[1], MarketDataError):
                    raise MarketDataError(str(cached[1]))
                return cached[1]
            try:
                info = await asyncio.to_thread(self.fetch_info, symbol)
                result = self._normalize(symbol, info)
            except Exception:
                error = MarketDataError("Yahoo quote unavailable or invalid; no price substituted.")
                self.cache[symbol] = (time.monotonic() + 60, error)
                raise error from None
            self.cache[symbol] = (time.monotonic() + 60, result)
            return result

    def _normalize(self, symbol: str, info: dict) -> Quote:
        suffix = symbol.rsplit(".", 1)[-1]
        expected_exchange = {"DE": "GER", "L": "LSE", "WA": "WSE"}[suffix]
        if info["symbol"] != symbol or info["exchange"] != expected_exchange:
            raise ValueError("Unexpected listing")
        currency = info["currency"]
        if currency not in {"EUR", "PLN", "GBP", "GBp", "GBX", "USD"}:
            raise ValueError("Unsupported quote currency")
        timestamp = info["regularMarketTime"]
        if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp <= 0:
            raise ValueError("Invalid timestamp")
        as_of = datetime.fromtimestamp(timestamp, UTC)
        now = datetime.now(UTC)
        if as_of > now:
            raise ValueError("Future quote")
        return Quote(
            symbol=symbol,
            name=info.get("longName") or info.get("shortName"),
            currency=currency,
            price=Decimal(str(info["regularMarketPrice"])),
            previous_close=Decimal(str(info["regularMarketPreviousClose"])),
            as_of=as_of,
            retrieved_at=now,
            source="Yahoo Finance (unofficial)",
        )

    async def history(self, symbol: str) -> object:
        raise MarketDataError("Yahoo history is not implemented yet.")

    async def fundamentals(self, symbol: str) -> object:
        raise MarketDataError("Yahoo fundamentals are not implemented yet.")
