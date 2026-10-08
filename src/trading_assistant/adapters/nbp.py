"""NBP reference rates with bounded HTTP requests and an optional local cache."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
from pydantic import ValidationError

from trading_assistant.domain.fx import FxRate, FxUnavailableError


class NbpFxRateProvider:
    def __init__(
        self,
        cache_path: Path | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        currency: str = "USD",
    ):
        self.cache_path = cache_path
        self.transport = transport
        self.cached: FxRate | None = None
        self.lock = asyncio.Lock()
        if currency not in {"USD", "EUR", "GBP"}:
            raise ValueError("Unsupported NBP currency")
        self.currency = currency
        self.providers: dict[str, NbpFxRateProvider] = {}

    async def currency_pln(self, currency: str) -> FxRate:
        if currency == self.currency:
            return await self._rate()
        if currency not in {"USD", "EUR", "GBP"}:
            raise FxUnavailableError("Unsupported reference FX currency")
        if currency not in self.providers:
            path = (
                self.cache_path.with_name(f"nbp-{currency.lower()}-pln.json")
                if self.cache_path
                else None
            )
            self.providers[currency] = NbpFxRateProvider(
                path, transport=self.transport, currency=currency
            )
        return await self.providers[currency]._rate()

    def _load_cache(self) -> FxRate | None:
        if self.cache_path is None:
            return None
        try:
            rate = FxRate.model_validate_json(self.cache_path.read_text())
            if rate.base != self.currency:
                return None
            if rate.retrieved_at.tzinfo is None or rate.retrieved_at > datetime.now(UTC):
                return None
            if rate.effective_date > datetime.now(UTC).date():
                return None
            return rate
        except (OSError, ValueError, ValidationError):
            return None

    async def usd_pln(self) -> FxRate:
        return await self.currency_pln("USD")

    async def _rate(self) -> FxRate:
        async with self.lock:
            now = datetime.now(UTC)
            if self.cached is None:
                self.cached = await asyncio.to_thread(self._load_cache)
            if self.cached and now - self.cached.retrieved_at < timedelta(hours=1):
                return self.cached
            try:
                async with httpx.AsyncClient(timeout=5, transport=self.transport) as client:
                    response = await client.get(
                        f"https://api.nbp.pl/api/exchangerates/rates/a/{self.currency.lower()}/",
                        params={"format": "json"},
                    )
                    response.raise_for_status()
                data = json.loads(response.text, parse_float=Decimal)
                if data["table"] != "A" or data["code"] != self.currency or len(data["rates"]) != 1:
                    raise ValueError("Unexpected NBP response.")
                item = data["rates"][0]
                rate = FxRate(
                    base=self.currency,
                    rate=item["mid"],
                    effective_date=item["effectiveDate"],
                    retrieved_at=now,
                    source_reference=item["no"],
                )
                if rate.effective_date > now.date():
                    raise ValueError("Future publication date.")
                self.cached = rate
                if self.cache_path:
                    await asyncio.to_thread(self._save_cache, rate)
                return rate
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                if self.cached:
                    return self.cached.model_copy(update={"stale": True})
                raise FxUnavailableError("NBP exchange rate is unavailable.") from None

    def _save_cache(self, rate: FxRate) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.cache_path.with_suffix(".tmp")
            temporary.write_text(rate.model_dump_json())
            temporary.replace(self.cache_path)
        except OSError:
            pass  # A validated in-memory rate remains usable if disk caching fails.
