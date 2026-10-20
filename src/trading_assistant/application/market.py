"""Market-data boundary and centralized instrument symbol mapping."""

import re
from datetime import datetime
from typing import Protocol

from trading_assistant.domain.candidates import StockCandidate
from trading_assistant.domain.market import MarketDataError, MarketSession, Quote


class MarketDataProvider(Protocol):
    async def quote(self, symbol: str) -> Quote: ...

    async def history(self, symbol: str) -> object: ...

    async def fundamentals(self, symbol: str) -> object: ...


class MarketSessionProvider(Protocol):
    def state(self, symbol: str, now: datetime) -> MarketSession | None: ...


class SymbolResolver:
    """Initial explicit US mapping; other markets need verified mappings."""

    def lookup_symbol(self, symbol: str) -> str:
        value = symbol.strip().upper()
        for broker, provider in [(".US", ""), (".PL", ".WA"), (".UK", ".L")]:
            if value.endswith(broker):
                value = value[: -len(broker)] + provider
                break
        if not re.fullmatch(r"(?:[A-Z][A-Z0-9-]{0,14}|[A-Z0-9][A-Z0-9]{0,14}\.(?:WA|DE|L))", value):
            raise MarketDataError("Use a US ticker or a Warsaw, Xetra or London listing.")
        return value

    def verified_instrument_symbol(self, instrument) -> str:
        symbol = self.lookup_symbol(instrument.symbol)
        if symbol.endswith(".WA"):
            exchanges, currencies = {"WSE"}, {"PLN"}
        elif symbol.endswith(".DE"):
            exchanges, currencies = {"GER"}, {"EUR"}
        elif symbol.endswith(".L"):
            exchanges, currencies = {"LSE"}, {"GBP", "GBp", "GBX", "USD", "EUR"}
        else:
            exchanges, currencies = {"NYQ", "NMS", "NGM", "NCM", "ASE", "PCX", "BTS"}, {"USD"}
        if instrument.exchange not in exchanges or instrument.currency not in currencies:
            raise MarketDataError("Listing exchange/currency does not match its symbol.")
        return symbol

    def instrument_matches_holding(self, instrument, broker_symbol: str) -> bool:
        return self.lookup_symbol(broker_symbol) == self.verified_instrument_symbol(instrument)

    def calendar_name(self, broker_symbol: str) -> str | None:
        suffix = broker_symbol.strip().upper().rpartition(".")[2]
        return {
            "US": "XNYS",
            "PL": "XWAR",
            "WA": "XWAR",
            "DE": "XETR",
            "UK": "XLON",
            "L": "XLON",
        }.get(suffix)

    def listing_market(self, broker_symbol: str) -> str | None:
        symbol = broker_symbol.strip().upper()
        for market, suffixes in {
            "US": (".US",),
            "PL": (".PL", ".WA"),
            "EU": (".DE", ".UK", ".L"),
        }.items():
            if symbol.endswith(suffixes):
                return market
        return None

    def candidate_symbol(self, candidate: StockCandidate) -> str:
        """Validate a Yahoo screener listing without claiming a broker mapping."""
        valid = (
            candidate.market == "US"
            and candidate.currency == "USD"
            and candidate.exchange in {"NYQ", "NMS", "NGM", "NCM", "ASE"}
            and re.fullmatch(r"[A-Z][A-Z0-9-]{0,14}", candidate.symbol)
        ) or (
            candidate.market == "PL"
            and candidate.currency == "PLN"
            and candidate.exchange == "WSE"
            and re.fullmatch(r"[A-Z0-9][A-Z0-9]{0,14}\.WA", candidate.symbol)
        )
        if not valid:
            raise MarketDataError("Candidate listing identity is not supported.")
        return candidate.symbol

    def candidate_matches_holding(self, candidate: StockCandidate, broker_symbol: str) -> bool:
        canonical = self.candidate_symbol(candidate)
        if candidate.market == "PL":
            return self.twelve_data_europe(broker_symbol) == f"{canonical[:-3]}:XWAR"
        return self.finnhub_us(broker_symbol) == canonical

    def analysis_symbol(self, symbol: str) -> str:
        normalized = symbol.strip().upper()
        if normalized.endswith((".DE", ".UK", ".PL")):
            return self.yahoo_europe(self.twelve_data_europe(normalized))
        if normalized.endswith(".WA"):
            expected = self.yahoo_europe(self.twelve_data_europe(normalized[:-3] + ".PL"))
            if expected == normalized:
                return expected
        if normalized.endswith(".L"):
            expected = self.yahoo_europe(self.twelve_data_europe(normalized[:-2] + ".UK"))
            if expected == normalized:
                return expected
        return self.finnhub_us(normalized)

    def yahoo_news(self, symbol: str) -> str:
        if symbol.strip().upper().endswith(".US"):
            return self.finnhub_us(symbol)
        return self.yahoo_europe(self.twelve_data_europe(symbol))

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
