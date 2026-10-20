"""Fresh listing metadata lookup, without screener liquidity restrictions."""

import asyncio

import yfinance as yf

from trading_assistant.application.market import SymbolResolver
from trading_assistant.domain.analysis import AnalysisFailure
from trading_assistant.domain.instrument import AnalysisInstrument


class YahooInstrumentProvider:
    def __init__(self, fetch=None):
        self.fetch = fetch or (lambda symbol: yf.Ticker(symbol).get_info())

    async def resolve(self, symbol: str) -> AnalysisInstrument:
        try:
            canonical = SymbolResolver().lookup_symbol(symbol)
            info = await asyncio.to_thread(self.fetch, canonical)
            if info.get("symbol") != canonical:
                raise ValueError
            kind = {"EQUITY": "STOCK", "ETF": "ETF"}.get(info.get("quoteType"))
            if kind is None:
                raise ValueError
            instrument = AnalysisInstrument(
                symbol=canonical,
                name=info.get("longName") or info.get("shortName") or canonical,
                asset_type=kind,
                exchange=info["exchange"],
                currency=info["currency"],
            )
            SymbolResolver().verified_instrument_symbol(instrument)
            return instrument
        except Exception:
            raise AnalysisFailure(
                "Unable to verify symbol, exchange, currency or stock/ETF type with Yahoo."
            ) from None
