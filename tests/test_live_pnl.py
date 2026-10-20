from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from trading_assistant.application.live_pnl import estimate_pnl
from trading_assistant.application.portfolio import PortfolioDashboardService
from trading_assistant.domain.fx import FxRate
from trading_assistant.domain.market import Quote
from trading_assistant.domain.portfolio import Holding, PortfolioState


@pytest.mark.parametrize(
    "value,pnl,percent,quote,expected_amount,expected_percent",
    [
        ("90", "-10", None, "95", "-5", "-5"),
        ("90", None, "-10", "95", "-5", "-5"),
        ("110", "10", None, "120", "20", "20"),
        ("100", "0", None, "100", "0", "0"),
        ("0", None, "-100", "10", None, None),
        ("90", None, None, "95", None, None),
        ("90", "NaN", None, "95", None, None),
    ],
)
def test_report_anchored_pnl_not_just_movement_since_report(
    value, pnl, percent, quote, expected_amount, expected_percent
):
    def dec(value):
        return Decimal(value) if value is not None else None

    amount, percentage = estimate_pnl(dec(value), dec(pnl), dec(percent), dec(quote))
    assert amount == dec(expected_amount)
    assert percentage == dec(expected_percent)


@pytest.mark.parametrize(
    "currency,price,rate,expected",
    [
        ("USD", "23.75", "4", "-5"),
        ("EUR", "19", "5", "-5"),
        ("GBp", "2000", "6", "20"),
        ("PLN", "95", None, "-5"),
    ],
)
async def test_quote_currency_converted_before_report_comparison(currency, price, rate, expected):
    class Repository:
        def portfolio(self):
            return PortfolioState(
                holdings=[
                    Holding(
                        symbol="PKO.PL",
                        name="Synthetic",
                        quantity="1",
                        asset_type="STOCK",
                        report_value="90",
                        report_pnl_amount="-10",
                        report_pnl_percent="-10",
                    )
                ],
                report_dates=[datetime.now(UTC)],
            )

    class Provider:
        async def quote(self, symbol):
            return Quote(
                symbol=symbol,
                price=price,
                previous_close=price,
                currency=currency,
                as_of=datetime.now(UTC),
                retrieved_at=datetime.now(UTC),
                source="Synthetic",
            )

    class Fx:
        async def usd_pln(self):
            return FxRate(
                base="USD",
                quote="PLN",
                rate="4",
                effective_date=date.today(),
                retrieved_at=datetime.now(UTC),
                source_reference="synthetic/A/NBP",
            )

        async def currency_pln(self, code):
            return FxRate(
                base=code,
                quote="PLN",
                rate=rate,
                effective_date=date.today(),
                retrieved_at=datetime.now(UTC),
                source_reference="synthetic/A/NBP",
            )

    snapshot = await PortfolioDashboardService(
        Repository(), Fx(), european_provider=Provider()
    ).snapshot()
    row = snapshot.positions[0]
    assert row.estimated_pnl_pln == Decimal(expected)
    assert row.estimated_pnl_percent == Decimal(expected)
    assert row.pnl_percent == -10 and row.market_value == 90
    assert snapshot.estimated_unrealized_pnl == Decimal(expected)
    assert snapshot.estimated_unrealized_pnl_usd == Decimal(expected) / 4
    assert snapshot.estimated_pnl_covered == snapshot.estimated_pnl_total == 1


async def test_live_total_reports_partial_coverage_and_never_falls_back_to_report_total():
    from trading_assistant.domain.market import MarketDataError

    class Repository:
        def portfolio(self):
            return PortfolioState(
                holdings=[
                    Holding(
                        symbol=symbol,
                        name=symbol,
                        quantity="1",
                        asset_type="STOCK",
                        report_value="90",
                        report_pnl_amount="-10",
                    )
                    for symbol in ["PKO.PL", "XTB.PL"]
                ],
                report_dates=[datetime.now(UTC)],
            )

    class Provider:
        unavailable = False

        async def quote(self, symbol):
            if self.unavailable or symbol.startswith("XTB"):
                raise MarketDataError("Synthetic unavailable quote")
            return Quote(
                symbol=symbol,
                price="95",
                previous_close="94",
                currency="PLN",
                as_of=datetime.now(UTC),
                retrieved_at=datetime.now(UTC),
                source="Synthetic",
            )

    provider = Provider()
    service = PortfolioDashboardService(Repository(), european_provider=provider)
    snapshot = await service.snapshot()
    assert snapshot.unrealized_pnl == -20
    assert snapshot.estimated_unrealized_pnl == -5
    assert (snapshot.estimated_pnl_covered, snapshot.estimated_pnl_total) == (1, 2)
    provider.unavailable = True
    snapshot = await service.snapshot()
    assert snapshot.estimated_unrealized_pnl is None
    assert snapshot.estimated_pnl_covered == 0
