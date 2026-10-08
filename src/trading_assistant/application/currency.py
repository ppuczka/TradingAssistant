from decimal import Decimal
from typing import Protocol

from trading_assistant.domain.fx import FxRate


class FxRateProvider(Protocol):
    async def usd_pln(self) -> FxRate: ...

    async def currency_pln(self, currency: str) -> FxRate: ...


def pln_to_usd(amount: Decimal | None, rate: FxRate | None) -> Decimal | None:
    if amount is None or rate is None:
        return None
    return amount / rate.rate
