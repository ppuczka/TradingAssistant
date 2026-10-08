"""Twelve Data European quotes, cached for 60 seconds including failures."""

import asyncio
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime

import httpx

from trading_assistant.domain.market import MarketDataError, Quote


class TwelveDataMarketDataProvider:
    def __init__(
        self,
        api_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        if not api_key.strip():
            raise MarketDataError("Set TWELVEDATA_API_KEY in your environment or .env.")
        self.api_key = api_key.strip()
        self.transport = transport
        self.lock = asyncio.Lock()
        self.cache: dict[str, tuple[float, Quote | MarketDataError]] = {}
        self.requests: deque[float] = deque()
        self.cooldown = 0.0
        self.clock = clock
        # Insertion order preserves turns across fixed-order dashboard refreshes.
        self.waiting: dict[str, float] = {}

    async def quote(self, symbol: str) -> Quote:
        async with self.lock:
            now = self.clock()
            cached = self.cache.get(symbol)
            if cached and now < cached[0]:
                if isinstance(cached[1], MarketDataError):
                    raise MarketDataError(str(cached[1]))
                return cached[1]
            # Forget symbols no longer polled, so removed holdings cannot block the queue.
            self.waiting = {s: seen for s, seen in self.waiting.items() if now - seen < 60}
            self.waiting[symbol] = now
            while self.requests and now - self.requests[0] >= 60:
                self.requests.popleft()
            if now < self.cooldown or len(self.requests) >= 8:
                raise MarketDataError("Twelve Data request budget reached; retry next minute.")
            if next(iter(self.waiting)) != symbol:
                raise MarketDataError("Twelve Data quote queued; waiting for earlier holdings.")
            del self.waiting[symbol]
            self.requests.append(now)
            try:
                result = await self._quote(symbol)
            except MarketDataError as exc:
                self.cache[symbol] = (self.clock() + 60, exc)
                raise
            self.cache[symbol] = (self.clock() + 60, result)
            return result

    async def _quote(self, identifier: str) -> Quote:
        try:
            symbol, mic = identifier.split(":")
            async with httpx.AsyncClient(timeout=10, transport=self.transport) as client:
                response = await client.get(
                    "https://api.twelvedata.com/quote",
                    params={"symbol": symbol, "mic_code": mic},
                    headers={"Authorization": f"apikey {self.api_key}"},
                )
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("Unexpected response shape")
            code = data.get("code", response.status_code)
            if code == 429:
                self.cooldown = self.clock() + 60
                raise MarketDataError("Twelve Data rate limit reached; retry next minute.")
            if code in (401, 403):
                raise MarketDataError(
                    "Twelve Data access denied; check key and market entitlement."
                )
            if (
                data.get("status") == "error"
                and "available starting with" in str(data.get("message", "")).lower()
            ):
                raise MarketDataError("Twelve Data subscription does not include this instrument.")
            response.raise_for_status()
            if data.get("status") == "error":
                raise MarketDataError("Twelve Data symbol unavailable for this market or plan.")
            if data["symbol"] != symbol or data["mic_code"] != mic:
                raise ValueError("Unexpected instrument")
            currency = data["currency"]
            if currency not in {"PLN", "EUR", "GBP", "GBX", "GBp", "USD"}:
                raise ValueError("Unsupported quote currency")
            timestamp = data["timestamp"]
            if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp <= 0:
                raise ValueError("Invalid timestamp")
            as_of = datetime.fromtimestamp(timestamp, UTC)
            now = datetime.now(UTC)
            if as_of > now:
                raise ValueError("Future quote")
            return Quote(
                symbol=identifier,
                name=data.get("name"),
                currency=currency,
                price=data["close"],
                previous_close=data["previous_close"],
                as_of=as_of,
                retrieved_at=now,
                source="Twelve Data",
            )
        except MarketDataError:
            raise
        except (httpx.HTTPError, ValueError, KeyError, TypeError, OverflowError):
            raise MarketDataError("Twelve Data quote unavailable or invalid.") from None

    async def history(self, symbol: str) -> object:
        raise MarketDataError("Twelve Data history is not implemented yet.")

    async def fundamentals(self, symbol: str) -> object:
        raise MarketDataError("Twelve Data fundamentals are not implemented yet.")
