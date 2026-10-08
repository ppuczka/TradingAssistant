"""Finnhub HTTP adapter. Credentials only travel in the authentication header."""

import asyncio
import json
import time
from datetime import UTC, datetime
from decimal import Decimal

import httpx
from pydantic import ValidationError

from trading_assistant.domain.market import MarketDataError, Quote


class FinnhubMarketDataProvider:
    def __init__(self, api_key: str, transport: httpx.AsyncBaseTransport | None = None):
        if not api_key.strip():
            raise MarketDataError("Set FINNHUB_API_KEY in your environment or .env.")
        self.api_key = api_key.strip()
        self.transport = transport
        self._request_lock = asyncio.Lock()
        self._next_request = 0.0
        self._retry_after = 0.0

    async def quote(self, symbol: str) -> Quote:
        async with self._request_lock:
            if time.monotonic() < self._retry_after:
                raise MarketDataError("Finnhub rate limit reached; retry paused for one minute.")
            delay = self._next_request - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._next_request = time.monotonic() + 1.1
            return await self._quote(symbol)

    async def _quote(self, symbol: str) -> Quote:
        try:
            async with httpx.AsyncClient(timeout=10, transport=self.transport) as client:
                response = await client.get(
                    "https://finnhub.io/api/v1/quote",
                    params={"symbol": symbol},
                    headers={"X-Finnhub-Token": self.api_key},
                )
            if response.status_code == 429:
                self._retry_after = time.monotonic() + 60
                raise MarketDataError("Finnhub rate limit reached; try again later.")
            if response.status_code in (401, 403):
                raise MarketDataError(
                    "Finnhub rejected access; check the key and market entitlement."
                )
            response.raise_for_status()
            data = json.loads(response.text, parse_float=Decimal)
            timestamp = data["t"]
            if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp <= 0:
                raise ValueError("Invalid quote timestamp")
            now = datetime.now(UTC)
            as_of = datetime.fromtimestamp(timestamp, UTC)
            if as_of > now:
                raise ValueError("Future quote timestamp")
            return Quote(
                symbol=symbol,
                currency="USD",
                price=data["c"],
                previous_close=data["pc"],
                as_of=as_of,
                retrieved_at=now,
                source="Finnhub",
            )
        except MarketDataError:
            raise
        except (httpx.HTTPError, ValueError, KeyError, TypeError, OverflowError, ValidationError):
            raise MarketDataError(
                "Finnhub quote unavailable or invalid; no price substituted."
            ) from None

    async def history(self, symbol: str) -> object:
        raise MarketDataError("Finnhub history is not implemented yet.")

    async def fundamentals(self, symbol: str) -> object:
        raise MarketDataError("Finnhub fundamentals are not implemented yet.")
