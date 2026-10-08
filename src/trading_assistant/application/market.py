"""Market-data boundary and centralized instrument symbol mapping."""

import re
from typing import Protocol

from trading_assistant.domain.market import MarketDataError, Quote


class MarketDataProvider(Protocol):
    async def quote(self, symbol: str) -> Quote: ...

    async def history(self, symbol: str) -> object: ...

    async def fundamentals(self, symbol: str) -> object: ...


class SymbolResolver:
    """Initial explicit US mapping; other markets need verified mappings."""

    def finnhub_us(self, symbol: str) -> str:
        normalized = symbol.strip().upper()
        if normalized.endswith(".US"):
            normalized = normalized[:-3]
        if not re.fullmatch(r"[A-Z][A-Z0-9-]{0,14}", normalized):
            raise MarketDataError(
                "Unsupported symbol: use a canonical US ticker or XTB .US symbol."
            )
        return normalized

    def twelve_data_europe(self, symbol: str) -> str:
        """Exchange-qualified candidates, validated against provider response identity."""
        ticker, separator, suffix = symbol.strip().upper().rpartition(".")
        exchanges = {"PL": "XWAR", "DE": "XETR", "UK": "XLON"}
        if not separator or suffix not in exchanges or not re.fullmatch(r"[A-Z0-9]+", ticker):
            raise MarketDataError("European market mapping unavailable.")
        return f"{ticker}:{exchanges[suffix]}"

    def yahoo_europe(self, identifier: str) -> str:
        """Explicit verified listings; no automatic cross-exchange substitution."""
        verified = {
            "AE5A:XETR": "AE5A.DE",
            "COPA:XLON": "COPA.L",
            "EHLT:XETR": "EHLT.DE",
            "ETFPZUW20M40:XWAR": "ETFPZUW20M40.WA",
            "GRID:XETR": "GRID.DE",
            "IPLT:XLON": "IPLT.L",
            "IUHC:XLON": "IUHC.L",
            "PKO:XWAR": "PKO.WA",
            "QDVE:XETR": "QDVE.DE",
            "RHM:XETR": "RHM.DE",
            "V9N:XETR": "V9N.DE",
            "XDPU:XETR": "XDPU.DE",
            "XTB:XWAR": "XTB.WA",
        }
        try:
            return verified[identifier]
        except KeyError:
            raise MarketDataError(
                "Yahoo listing has not been verified for this instrument."
            ) from None


class EuropeanQuoteProvider:
    """Keep Twelve Data where available; use verified Yahoo listings on failure."""

    def __init__(self, primary: MarketDataProvider | None, fallback: MarketDataProvider):
        self.primary = primary
        self.fallback = fallback
        self.resolver = SymbolResolver()

    async def quote(self, symbol: str) -> Quote:
        if self.primary is not None:
            try:
                return await self.primary.quote(symbol)
            except MarketDataError:
                pass
        return await self.fallback.quote(self.resolver.yahoo_europe(symbol))

    async def history(self, symbol: str) -> object:
        raise MarketDataError("European history is not implemented yet.")

    async def fundamentals(self, symbol: str) -> object:
        raise MarketDataError("European fundamentals are not implemented yet.")
