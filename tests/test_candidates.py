import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from typer.testing import CliRunner

from trading_assistant.adapters.yahoo_candidates import YahooCandidateProvider
from trading_assistant.application.candidates import CandidateService
from trading_assistant.cli import app
from trading_assistant.domain.candidates import CandidatePage
from trading_assistant.domain.portfolio import Holding, PortfolioState


def quote(symbol="AAPL", market="US", **overrides):
    return {
        "symbol": symbol,
        "longName": symbol + " company",
        "quoteType": "EQUITY",
        "exchange": "NMS" if market == "US" else "WSE",
        "currency": "USD" if market == "US" else "PLN",
        "regularMarketPrice": 123.456789,
        "marketCap": 10000000000,
        "averageDailyVolume3Month": 1000000,
        "regularMarketTime": int((datetime.now(UTC) - timedelta(minutes=1)).timestamp()),
        **overrides,
    }


async def test_candidate_provider_validates_identity_filters_and_caches():
    calls = []
    raw = [
        quote(),
        quote("GE"),
        quote("BAD", exchange="GER"),
        quote("ETF", quoteType="ETF"),
        quote("ILL", averageDailyVolume3Month=1),
        quote("NAN", regularMarketPrice=float("nan")),
        quote("BADNUM", marketCap="bad"),
        quote("FUTURE", regularMarketTime=9999999999),
    ]

    def fetch(market, offset, size):
        calls.append((market, offset, size))
        return {"total": len(raw), "quotes": raw}

    provider = YahooCandidateProvider(fetch)
    page = await provider.page("US", 0, 100)
    assert [c.symbol for c in page.candidates] == ["AAPL", "GE"]
    assert page.candidates[0].price == Decimal("123.456789")
    assert page.rejected == 6 and page.received == 8
    assert page.candidates[0].broker_available is None
    await provider.page("US", 0, 100)
    assert len(calls) == 1


async def test_polish_candidate_listing_and_numeric_ticker():
    provider = YahooCandidateProvider(
        lambda *args: {"total": 2, "quotes": [quote("11B.WA", "PL"), quote("PKO.WA", "PL")]}
    )
    page = await provider.page("PL", 0, 100)
    assert [c.symbol for c in page.candidates] == ["11B.WA", "PKO.WA"]
    assert all(c.market == "PL" and c.currency == "PLN" for c in page.candidates)


async def test_candidates_exclude_holdings_and_keep_success_when_market_fails():
    class Repository:
        def portfolio(self):
            return PortfolioState(
                holdings=[Holding(symbol="GE.US", name="GE", asset_type="STOCK", quantity="1")]
            )

    def fetch(market, offset, size):
        if market == "PL":
            raise RuntimeError("private provider details")
        return {"total": 3, "quotes": [quote("GE"), quote("AAPL"), quote("AAPL")]}

    batch = await CandidateService(Repository(), YahooCandidateProvider(fetch)).fetch(
        ["US", "PL"], 20
    )
    assert [c.symbol for c in batch.candidates] == ["AAPL"]
    assert batch.excluded_owned == 1
    assert batch.errors == {"PL": "Yahoo screener unavailable or invalid."}
    assert "only 1 candidates" in batch.warnings[0]


async def test_candidate_pagination_refills_after_owned_exclusions_and_is_bounded():
    class Repository:
        def portfolio(self):
            return PortfolioState()

    provider = YahooCandidateProvider(lambda *args: {"total": 1, "quotes": [quote("AAPL")]})
    candidate = (await provider.page("US", 0, 100)).candidates[0]
    calls = []

    class Pages:
        async def page(self, market, offset, size):
            calls.append(offset)
            return CandidatePage(candidates=[candidate], received=100, total=1000)

    batch = await CandidateService(Repository(), Pages()).fetch(["US"], 5)
    assert calls == [0, 100, 200] and len(batch.candidates) == 1


def test_candidates_cli_uses_service_and_makes_no_ai_calls(tmp_path, monkeypatch):
    from trading_assistant.domain.candidates import CandidateBatch

    class Service:
        async def fetch(self, markets, limit):
            assert markets == ["US", "PL"] and limit == 5
            return CandidateBatch(excluded_owned=2)

    monkeypatch.setattr("trading_assistant.bootstrap.candidate_service", lambda path: Service())
    result = CliRunner().invoke(app, ["candidates", "--limit", "5", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["excluded_owned"] == 2
    assert CliRunner().invoke(app, ["candidates", "--market", "EU"]).exit_code != 0
    assert CliRunner().invoke(app, ["candidates", "--limit", "0"]).exit_code != 0
