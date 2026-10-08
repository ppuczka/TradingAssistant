"""Composition root: connect application services to local adapters."""

import os
from pathlib import Path

from dotenv import load_dotenv

from trading_assistant.adapters.database import database_engine, migrate
from trading_assistant.adapters.finnhub import FinnhubMarketDataProvider
from trading_assistant.adapters.nbp import NbpFxRateProvider
from trading_assistant.adapters.portfolio_repository import SqlAlchemyPortfolioRepository
from trading_assistant.adapters.twelvedata import TwelveDataMarketDataProvider
from trading_assistant.adapters.yahoo import YahooMarketDataProvider
from trading_assistant.application.market import EuropeanQuoteProvider
from trading_assistant.application.portfolio import PortfolioDashboardService


def repository(path: Path) -> SqlAlchemyPortfolioRepository:
    engine = database_engine(path)
    migrate(engine)
    return SqlAlchemyPortfolioRepository(engine)


def dashboard_service(path: Path) -> PortfolioDashboardService:
    load_dotenv(Path(".env"), override=False)
    key = os.environ.get("FINNHUB_API_KEY", "").strip()
    european_key = os.environ.get("TWELVEDATA_API_KEY", "").strip()
    return PortfolioDashboardService(
        repository(path),
        NbpFxRateProvider(path.parent / "nbp-usd-pln.json"),
        FinnhubMarketDataProvider(key) if key else None,
        EuropeanQuoteProvider(
            TwelveDataMarketDataProvider(european_key) if european_key else None,
            YahooMarketDataProvider(path.parent / "yahoo-cache"),
        ),
    )
