import httpx
import pytest

from trading_assistant.adapters.finnhub import FinnhubMarketDataProvider
from trading_assistant.application.market import SymbolResolver
from trading_assistant.domain.market import MarketDataError


def test_symbol_resolution():
    resolver = SymbolResolver()
    assert resolver.finnhub_us("GE.US") == "GE"
    assert resolver.finnhub_us("aapl") == "AAPL"
    with pytest.raises(MarketDataError):
        resolver.finnhub_us("PKO.PL")


async def test_quote_header_and_precision():
    def handle(request):
        assert request.headers["X-Finnhub-Token"] == "test-secret"
        assert "test-secret" not in str(request.url)
        assert request.url.params["symbol"] == "GE"
        return httpx.Response(200, text='{"c":123.456789,"pc":122,"t":1700000000}')

    provider = FinnhubMarketDataProvider("test-secret", httpx.MockTransport(handle))
    quote = await provider.quote("GE")
    assert str(quote.price) == "123.456789"
    assert quote.currency == "USD"


@pytest.mark.parametrize(
    "status,body",
    [
        (429, "{}"),
        (403, '{"error":"test-secret"}'),
        (500, "{}"),
        (200, '{"c":0,"pc":0,"t":0}'),
        (200, "bad json"),
        (200, '{"c":123,"pc":122,"t":9999999999}'),
    ],
)
async def test_failures_are_sanitized(status, body):
    provider = FinnhubMarketDataProvider(
        "test-secret", httpx.MockTransport(lambda request: httpx.Response(status, text=body))
    )
    with pytest.raises(MarketDataError) as error:
        await provider.quote("GE")
    assert "test-secret" not in str(error.value)


async def test_dashboard_quote_values_and_partial_coverage():
    from datetime import UTC, datetime
    from decimal import Decimal

    from trading_assistant.application.portfolio import PortfolioDashboardService
    from trading_assistant.domain.market import Quote
    from trading_assistant.domain.portfolio import Holding, PortfolioState

    class Repository:
        def portfolio(self):
            return PortfolioState(
                holdings=[
                    Holding(
                        symbol="GE.US",
                        name="GE",
                        asset_type="STOCK",
                        quantity="2",
                        report_value="1000",
                    ),
                    Holding(
                        symbol="PKO.PL",
                        name="PKO",
                        asset_type="STOCK",
                        quantity="3",
                        report_value="200",
                    ),
                ],
                report_dates=[datetime.now(UTC)],
            )

    class Provider:
        calls = []

        async def quote(self, symbol):
            self.calls.append(symbol)
            return Quote(
                symbol=symbol,
                currency="USD",
                price="300",
                previous_close="290",
                as_of=datetime.now(UTC),
                retrieved_at=datetime.now(UTC),
                source="Finnhub",
            )

    provider = Provider()
    service = PortfolioDashboardService(Repository(), market_provider=provider)
    result = await service.snapshot()
    assert provider.calls == ["GE"]
    assert result.positions[0].quote_value_usd == Decimal("600")
    assert result.positions[0].quote_daily_change_percent == Decimal(10) / Decimal(290) * 100
    assert result.positions[1].quote_value_usd is None
    assert result.holdings_value == Decimal("1200")
    assert result.today_pnl is None
    assert "1/2" in result.market_status


async def test_dashboard_quote_failure_preserves_report_values():
    from decimal import Decimal

    from trading_assistant.application.portfolio import PortfolioDashboardService
    from trading_assistant.domain.portfolio import Holding, PortfolioState

    class Repository:
        def portfolio(self):
            return PortfolioState(
                holdings=[
                    Holding(
                        symbol="GE.US",
                        name="GE",
                        asset_type="STOCK",
                        quantity="2",
                        report_value="1000",
                    )
                ]
            )

    class Provider:
        async def quote(self, symbol):
            raise MarketDataError("Finnhub rate limit reached; try again later.")

    result = await PortfolioDashboardService(Repository(), market_provider=Provider()).snapshot()
    assert result.positions[0].market_value == Decimal("1000")
    assert result.positions[0].quote_price_usd is None
    assert "rate limit" in result.positions[0].quote_status


async def test_initial_snapshot_does_not_wait_for_external_data():
    from datetime import UTC, datetime
    from decimal import Decimal

    from trading_assistant.application.portfolio import PortfolioDashboardService
    from trading_assistant.domain.portfolio import Holding, PortfolioState

    class Repository:
        def portfolio(self):
            return PortfolioState(
                holdings=[
                    Holding(
                        symbol="GE.US",
                        name="GE",
                        asset_type="STOCK",
                        quantity="2",
                        report_value="1000",
                    )
                ],
                report_dates=[datetime.now(UTC)],
                cash_balance=Decimal("50"),
            )

    class SlowProvider:
        async def quote(self, symbol):
            pytest.fail("Initial local load must not call Finnhub")

        async def usd_pln(self):
            pytest.fail("Initial local load must not call NBP")

    provider = SlowProvider()
    result = await PortfolioDashboardService(Repository(), provider, provider).initial_snapshot()
    assert result.holdings_value == Decimal("1000")
    assert result.cash == Decimal("50")
    assert result.positions[0].symbol == "GE.US"
    assert "Loading" in result.market_status
