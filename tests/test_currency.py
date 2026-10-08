from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from trading_assistant.adapters.nbp import NbpFxRateProvider
from trading_assistant.application.currency import pln_to_usd
from trading_assistant.application.portfolio import PortfolioDashboardService
from trading_assistant.domain.fx import FxRate, FxUnavailableError
from trading_assistant.domain.portfolio import Holding, PortfolioState


def rate(**overrides):
    return FxRate(
        **{
            "rate": Decimal("4"),
            "effective_date": datetime.now(UTC).date(),
            "retrieved_at": datetime.now(UTC),
            "source_reference": "test/A/NBP",
            **overrides,
        }
    )


def response(**overrides):
    return {
        "table": "A",
        "code": "USD",
        "rates": [
            {"mid": 4, "effectiveDate": datetime.now(UTC).date().isoformat(), "no": "test/A/NBP"}
        ],
        **overrides,
    }


def test_conversion_direction_precision_and_missing_values():
    assert pln_to_usd(Decimal("400"), rate()) == Decimal("100")
    assert pln_to_usd(Decimal("-40"), rate()) == Decimal("-10")
    assert pln_to_usd(Decimal("0"), rate()) == Decimal("0")
    assert pln_to_usd(Decimal("1.001"), rate()) == Decimal("0.25025")
    assert pln_to_usd(None, rate()) is None
    assert pln_to_usd(Decimal("100"), None) is None


async def test_nbp_http_decimal_and_cache_reuse(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        data = response()
        data["rates"][0]["mid"] = 4.1234
        return httpx.Response(200, json=data)

    path = tmp_path / "fx.json"
    provider = NbpFxRateProvider(path, transport=httpx.MockTransport(handler))
    actual = await provider.usd_pln()
    assert actual.rate == Decimal("4.1234")
    assert actual.base == "USD" and actual.quote == "PLN"
    assert not actual.stale
    assert (await provider.usd_pln()).rate == actual.rate
    assert len(calls) == 1
    assert calls[0].url.scheme == "https"
    assert str(calls[0].url).endswith("/a/usd/?format=json")
    restored = NbpFxRateProvider(path, transport=httpx.MockTransport(handler))
    assert (await restored.usd_pln()).rate == actual.rate
    assert len(calls) == 1


@pytest.mark.parametrize(
    "data",
    [
        response(code="EUR"),
        response(table="C"),
        response(rates=[]),
        response(rates=[{"mid": 0, "effectiveDate": "2020-01-01", "no": "test"}]),
        response(rates=[{"mid": -1, "effectiveDate": "2020-01-01", "no": "test"}]),
        response(rates=[{"mid": "NaN", "effectiveDate": "2020-01-01", "no": "test"}]),
        response(rates=[{"mid": 4, "effectiveDate": "2999-01-01", "no": "test"}]),
        {},
    ],
)
async def test_invalid_nbp_data_is_not_used(data):
    provider = NbpFxRateProvider(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))
    )
    with pytest.raises(FxUnavailableError):
        await provider.usd_pln()


async def test_network_failure_uses_explicitly_stale_cache(tmp_path):
    path = tmp_path / "fx.json"
    path.write_text(rate(retrieved_at=datetime.now(UTC) - timedelta(hours=2)).model_dump_json())

    def offline(request):
        raise httpx.ConnectError("offline", request=request)

    provider = NbpFxRateProvider(path, transport=httpx.MockTransport(offline))
    result = await provider.usd_pln()
    assert result.stale
    assert result.rate == Decimal("4")
    assert result.retrieved_at < datetime.now(UTC) - timedelta(hours=1)
    assert not FxRate.model_validate_json(path.read_text()).stale


async def test_corrupted_cache_does_not_replace_missing_rate(tmp_path):
    path = tmp_path / "fx.json"
    path.write_text("invalid JSON")
    provider = NbpFxRateProvider(path, transport=httpx.MockTransport(lambda _: httpx.Response(503)))
    with pytest.raises(FxUnavailableError):
        await provider.usd_pln()


class FakeRepository:
    def portfolio(self):
        return PortfolioState(
            holdings=[
                Holding(
                    symbol="TEST",
                    name="Synthetic",
                    asset_type="STOCK",
                    quantity=Decimal("1"),
                    report_value=Decimal("400"),
                    report_pnl_amount=Decimal("-40"),
                )
            ],
            report_dates=[datetime(2026, 1, 1, tzinfo=UTC)],
        )


async def test_summary_conversions_are_service_calculations():
    provider = NbpFxRateProvider(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response()))
    )
    snapshot = await PortfolioDashboardService(FakeRepository(), provider).snapshot()
    assert snapshot.holdings_value == Decimal("400")
    assert snapshot.holdings_value_usd == Decimal("100")
    assert snapshot.positions[0].market_value_usd == Decimal("100")
    assert snapshot.unrealized_pnl == Decimal("-40")
    assert snapshot.unrealized_pnl_usd == Decimal("-10")
    assert snapshot.cash is None and snapshot.today_pnl is None
    assert snapshot.portfolio_value is None and snapshot.total_pnl is None
    assert "NBP reference FX" in snapshot.fx_status


async def test_fx_failure_does_not_hide_pln_holdings():
    provider = NbpFxRateProvider(transport=httpx.MockTransport(lambda _: httpx.Response(503)))
    snapshot = await PortfolioDashboardService(FakeRepository(), provider).snapshot()
    assert snapshot.holdings_value == Decimal("400")
    assert snapshot.holdings_value_usd is None
    assert snapshot.positions[0].market_value_usd is None
    assert "unavailable" in snapshot.fx_status


async def test_nbp_eur_gbp_cache_and_identity(tmp_path):
    calls = []

    def handler(request):
        currency = request.url.path.split("/")[-2].upper()
        calls.append(currency)
        return httpx.Response(200, json=response(code=currency))

    provider = NbpFxRateProvider(
        tmp_path / "nbp-usd-pln.json", transport=httpx.MockTransport(handler)
    )
    for currency in ("EUR", "GBP"):
        actual = await provider.currency_pln(currency)
        assert actual.base == currency
        assert actual.rate == Decimal(4)
        assert (await provider.currency_pln(currency)).base == currency
    assert calls == ["EUR", "GBP"]


@pytest.mark.parametrize("missing,old_quote", [(False, False), (True, False), (False, True)])
async def test_daily_summary_multi_currency_and_missing_coverage(missing, old_quote):
    from trading_assistant.domain.market import MarketDataError, Quote

    holdings = [
        Holding(symbol=s, name="Full instrument name", asset_type="ETF", quantity="2")
        for s in ("AAA.US", "BBB.DE", "CCC.UK", "DDD.PL")
    ]

    class Repository:
        def portfolio(self):
            return PortfolioState(holdings=holdings, report_dates=[datetime.now(UTC)])

    class Provider:
        async def quote(self, symbol):
            if missing and symbol.startswith("BBB"):
                raise MarketDataError("unavailable")
            currency = {"AAA": "USD", "BBB": "EUR", "CCC": "GBp", "DDD": "PLN"}[symbol[:3]]
            as_of = datetime.now(UTC) - timedelta(days=1 if old_quote else 0)
            return Quote(
                symbol=symbol,
                name="Provider full name",
                currency=currency,
                price="110",
                previous_close="100",
                as_of=as_of,
                retrieved_at=datetime.now(UTC),
                source="Test",
            )

    class Fx:
        async def usd_pln(self):
            return rate()

        async def currency_pln(self, currency):
            return rate(base=currency, rate=Decimal("5") if currency == "EUR" else Decimal("6"))

    snapshot = await PortfolioDashboardService(
        Repository(), Fx(), Provider(), Provider()
    ).snapshot()
    assert snapshot.positions[0].name == "Provider full name"
    if missing or old_quote:
        assert snapshot.today_pnl is None
        assert "unavailable" in snapshot.today_pnl_status
    else:
        # 20 USD * 4 + 20 EUR * 5 + 20 pence / 100 * 6 + 20 PLN
        assert snapshot.today_pnl == Decimal("201.2")
        assert "excludes trades" in snapshot.today_pnl_status
