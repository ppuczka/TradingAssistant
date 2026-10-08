from decimal import Decimal

import httpx
import pytest

from trading_assistant.adapters.twelvedata import TwelveDataMarketDataProvider
from trading_assistant.application.market import SymbolResolver
from trading_assistant.domain.market import MarketDataError


def payload(**overrides):
    return dict(
        symbol="PKO",
        mic_code="XWAR",
        currency="PLN",
        close="114.92",
        previous_close="118.88",
        timestamp=1700000000,
        **overrides,
    )


async def test_identity_currency_and_minute_cache():
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["Authorization"] == "apikey secret"
        assert "secret" not in str(request.url)
        assert request.url.params["mic_code"] == "XWAR"
        return httpx.Response(200, json=payload())

    provider = TwelveDataMarketDataProvider("secret", httpx.MockTransport(handler))
    first = await provider.quote("PKO:XWAR")
    assert first.currency == "PLN"
    assert first.price == Decimal("114.92")
    assert await provider.quote("PKO:XWAR") == first
    assert len(calls) == 1
    expiry, cached = provider.cache["PKO:XWAR"]
    provider.cache["PKO:XWAR"] = (0, cached)
    await provider.quote("PKO:XWAR")
    assert len(calls) == 2


@pytest.mark.parametrize(
    "data",
    [
        {"status": "error", "code": 403, "message": "secret"},
        {"status": "error", "code": 429, "message": "secret"},
        {"status": "error", "code": 400, "message": "secret"},
        {**payload(), "mic_code": "XNAS"},
        {**payload(), "close": "0"},
        {**payload(), "currency": "UNKNOWN"},
        {**payload(), "timestamp": 9999999999},
    ],
)
async def test_errors_cached_and_sanitized(data):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=data)

    provider = TwelveDataMarketDataProvider("secret", httpx.MockTransport(handler))
    for _ in range(2):
        with pytest.raises(MarketDataError) as error:
            await provider.quote("PKO:XWAR")
        assert "secret" not in str(error.value)
    assert len(calls) == 1


def test_european_mapping():
    resolver = SymbolResolver()
    assert resolver.twelve_data_europe("PKO.PL") == "PKO:XWAR"
    assert resolver.twelve_data_europe("QDVE.DE") == "QDVE:XETR"
    assert resolver.twelve_data_europe("IPLT.UK") == "IPLT:XLON"
    with pytest.raises(MarketDataError):
        resolver.twelve_data_europe("GE.US")


async def test_european_dashboard_keeps_currency():
    from datetime import UTC, datetime

    from trading_assistant.application.portfolio import PortfolioDashboardService
    from trading_assistant.domain.market import Quote
    from trading_assistant.domain.portfolio import Holding, PortfolioState

    class Repository:
        def portfolio(self):
            return PortfolioState(
                holdings=[
                    Holding(
                        symbol="QDVE.DE",
                        name="ETF",
                        asset_type="ETF",
                        quantity="2",
                        report_value="500",
                    )
                ],
                report_dates=[datetime.now(UTC)],
            )

    class Provider:
        async def quote(self, symbol):
            assert symbol == "QDVE:XETR"
            return Quote(
                symbol=symbol,
                currency="EUR",
                price="50",
                previous_close="40",
                as_of=datetime.now(UTC),
                retrieved_at=datetime.now(UTC),
                source="Twelve Data",
            )

    result = await PortfolioDashboardService(Repository(), european_provider=Provider()).snapshot()
    row = result.positions[0]
    assert row.quote_currency == "EUR"
    assert row.quote_value == Decimal("100")
    assert row.quote_value_usd is None
    assert row.quote_daily_change_percent == Decimal("25")
    assert result.holdings_value == Decimal("500")


async def test_subscription_restriction_is_distinct_from_missing_symbol():
    provider = TwelveDataMarketDataProvider(
        "secret",
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "status": "error",
                    "code": 404,
                    "message": "This symbol is available starting with the Grow or Venture plan.",
                },
            )
        ),
    )
    with pytest.raises(MarketDataError, match="subscription does not include"):
        await provider.quote("AE5A:XETR")


@pytest.mark.parametrize("count", [9, 17])
@pytest.mark.parametrize("fail", [False, True])
async def test_fixed_order_refreshes_share_budget_fairly(count, fail):
    now = 0.0
    calls = []

    def handler(request):
        symbol = request.url.params["symbol"]
        calls.append((now, symbol))
        data = {"status": "error", "code": 403} if fail else {**payload(), "symbol": symbol}
        return httpx.Response(200, json=data)

    provider = TwelveDataMarketDataProvider(
        "secret", httpx.MockTransport(handler), clock=lambda: now
    )
    symbols = [f"TEST{i}" for i in range(count)]
    for second in range(0, 301, 5):
        now = float(second)
        for symbol in symbols:
            try:
                await provider.quote(f"{symbol}:XWAR")
            except MarketDataError:
                pass
    # Every holding gets both initial coverage and subsequent updates, even on errors.
    assert all(sum(s == symbol for _, s in calls) >= 2 for symbol in symbols)
    assert calls[8][1] == symbols[8]
    for timestamp, _ in calls:
        assert sum(timestamp - 60 < t <= timestamp for t, _ in calls) <= 8
    for symbol in symbols:
        times = [t for t, s in calls if s == symbol]
        assert all(b - a >= 60 for a, b in zip(times, times[1:], strict=False))


async def test_removed_waiting_holding_does_not_block_refreshes():
    now = 0.0
    calls = []

    def handler(request):
        symbol = request.url.params["symbol"]
        calls.append(symbol)
        return httpx.Response(200, json={**payload(), "symbol": symbol})

    provider = TwelveDataMarketDataProvider(
        "secret", httpx.MockTransport(handler), clock=lambda: now
    )
    for i in range(8):
        await provider.quote(f"TEST{i}:XWAR")
    with pytest.raises(MarketDataError):
        await provider.quote("REMOVED:XWAR")
    now = 60.0
    await provider.quote("TEST0:XWAR")
    assert calls[-1] == "TEST0"
    assert "REMOVED" not in calls
