from decimal import Decimal

import pytest

from trading_assistant.adapters.yahoo import YahooMarketDataProvider
from trading_assistant.application.market import EuropeanQuoteProvider, SymbolResolver
from trading_assistant.domain.market import MarketDataError


def info(**overrides):
    return {
        "symbol": "XTB.WA",
        "exchange": "WSE",
        "currency": "PLN",
        "regularMarketPrice": 137.0,
        "regularMarketPreviousClose": 135.0,
        "regularMarketTime": 1700000000,
        **overrides,
    }


async def test_yahoo_cache_currency_and_refresh(tmp_path):
    calls = []

    def fetch(symbol):
        calls.append(symbol)
        return info()

    provider = YahooMarketDataProvider(tmp_path, fetch)
    quote = await provider.quote("XTB.WA")
    assert quote.price == Decimal("137.0")
    assert quote.currency == "PLN"
    assert quote.source == "Yahoo Finance (unofficial)"
    assert await provider.quote("XTB.WA") == quote
    assert calls == ["XTB.WA"]
    provider.cache["XTB.WA"] = (0, quote)
    await provider.quote("XTB.WA")
    assert len(calls) == 2


@pytest.mark.parametrize(
    "overrides",
    [
        {"symbol": "XTB.US"},
        {"exchange": "LSE"},
        {"currency": "UNKNOWN"},
        {"regularMarketPrice": 0},
        {"regularMarketPrice": float("nan")},
        {"regularMarketPreviousClose": None},
        {"regularMarketTime": True},
        {"regularMarketTime": 9999999999},
    ],
)
async def test_invalid_quote_rejected_and_failure_cached(tmp_path, overrides):
    calls = []

    def fetch(symbol):
        calls.append(symbol)
        return info(**overrides)

    provider = YahooMarketDataProvider(tmp_path, fetch)
    for _ in range(2):
        with pytest.raises(MarketDataError):
            await provider.quote("XTB.WA")
    assert len(calls) == 1


async def test_london_currency_is_not_assumed_gbp(tmp_path):
    provider = YahooMarketDataProvider(
        tmp_path, lambda symbol: info(symbol="COPA.L", exchange="LSE", currency="USD")
    )
    assert (await provider.quote("COPA.L")).currency == "USD"


async def test_fallback_mapping_and_primary_success(tmp_path):
    fallback_calls = []

    def fetch(symbol):
        fallback_calls.append(symbol)
        return info()

    fallback = YahooMarketDataProvider(tmp_path, fetch)
    quote = await fallback.quote("XTB.WA")
    fallback_calls.clear()

    class Primary:
        fail = False

        async def quote(self, symbol):
            if self.fail:
                raise MarketDataError("Subscription restriction")
            return quote

    primary = Primary()
    routed = EuropeanQuoteProvider(primary, fallback)
    assert await routed.quote("XTB:XWAR") == quote
    assert fallback_calls == []
    primary.fail = True
    fallback.cache.clear()
    result = await routed.quote("XTB:XWAR")
    assert result.price == quote.price
    assert result.symbol == "XTB.WA"
    assert fallback_calls == ["XTB.WA"]
    assert (await EuropeanQuoteProvider(None, fallback).quote("XTB:XWAR")).price == quote.price


def test_mapping_only_verified_listings():
    resolver = SymbolResolver()
    assert resolver.yahoo_europe("ETFPZUW20M40:XWAR") == "ETFPZUW20M40.WA"
    assert resolver.yahoo_europe("IPLT:XLON") == "IPLT.L"
    assert resolver.yahoo_europe("GRID:XETR") == "GRID.DE"
    with pytest.raises(MarketDataError):
        resolver.yahoo_europe("UNVERIFIED:XWAR")
