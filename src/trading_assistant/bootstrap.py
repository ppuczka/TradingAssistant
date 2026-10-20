"""Composition root: connect application services to local adapters."""

import os
from pathlib import Path

from dotenv import load_dotenv

from trading_assistant.adapters.change_history import SqlAlchemyChangeHistoryRepository
from trading_assistant.adapters.database import database_engine, migrate
from trading_assistant.adapters.exchange_sessions import ExchangeSessionProvider
from trading_assistant.adapters.finnhub import FinnhubMarketDataProvider
from trading_assistant.adapters.gdelt_news import COMPANIES, GdeltNewsProvider
from trading_assistant.adapters.google_news import FallbackNewsProvider, GoogleNewsRssProvider
from trading_assistant.adapters.nbp import NbpFxRateProvider
from trading_assistant.adapters.portfolio_repository import SqlAlchemyPortfolioRepository
from trading_assistant.adapters.recommendations import JsonRecommendationReader
from trading_assistant.adapters.twelvedata import TwelveDataMarketDataProvider
from trading_assistant.adapters.yahoo import YahooMarketDataProvider
from trading_assistant.adapters.yahoo_news import YahooNewsProvider
from trading_assistant.application.market import EuropeanQuoteProvider
from trading_assistant.application.news import CombinedPortfolioNewsService, PortfolioNewsService
from trading_assistant.application.portfolio import PortfolioDashboardService


def repository(path: Path) -> SqlAlchemyPortfolioRepository:
    engine = database_engine(path)
    migrate(engine)
    return SqlAlchemyPortfolioRepository(engine)


def dashboard_service(path: Path) -> PortfolioDashboardService:
    load_dotenv(Path(".env"), override=False)
    key = os.environ.get("FINNHUB_API_KEY", "").strip()
    european_key = os.environ.get("TWELVEDATA_API_KEY", "").strip()
    from trading_assistant.risk_config import load_risk_settings

    risk_settings = load_risk_settings(Path(os.environ.get("TRADER_RISK_CONFIG", "risk.yaml")))
    portfolio_repository = repository(path)
    return PortfolioDashboardService(
        portfolio_repository,
        NbpFxRateProvider(path.parent / "nbp-usd-pln.json"),
        FinnhubMarketDataProvider(key) if key else None,
        EuropeanQuoteProvider(
            TwelveDataMarketDataProvider(european_key) if european_key else None,
            YahooMarketDataProvider(path.parent / "yahoo-cache"),
        ),
        CombinedPortfolioNewsService(
            PortfolioNewsService(YahooNewsProvider()),
            PortfolioNewsService(
                FallbackNewsProvider(GdeltNewsProvider(), GoogleNewsRssProvider()),
                mapper=lambda symbol: symbol,
                label="EU / PL company news • GDELT / Google News RSS",
                refresh_seconds=1800,
            ),
            tuple(COMPANIES),
        ),
        SqlAlchemyChangeHistoryRepository(portfolio_repository.engine),
        recommendation_reader(path),
        risk_settings=risk_settings,
        sentiment_store=sentiment_store(path),
        session_provider=ExchangeSessionProvider(),
    )


def analysis_service(path: Path, settings):
    from trading_assistant.adapters.recommendations import JsonRecommendationStore
    from trading_assistant.adapters.tradingagents import TradingAgentsEngine
    from trading_assistant.adapters.yahoo_instruments import YahooInstrumentProvider
    from trading_assistant.application.analysis import AnalysisService

    root = path.parent / "analysis"
    return AnalysisService(
        repository(path),
        TradingAgentsEngine(settings, root / "tradingagents"),
        JsonRecommendationStore(root / "recommendations", settings),
        recommendation_reader(path),
        instrument_provider=YahooInstrumentProvider(),
    )


def recommendation_reader(path: Path):
    from trading_assistant.adapters.tradingagents import TradingAgentsSavedReportReader

    root = path.parent / "analysis"
    return JsonRecommendationReader(
        root / "recommendations", TradingAgentsSavedReportReader(root / "tradingagents")
    )


def dashboard_analysis(path: Path):
    from trading_assistant.analysis_config import load_analysis_settings
    from trading_assistant.domain.analysis import AnalysisFailure

    load_dotenv(Path(".env"), override=False)
    settings = load_analysis_settings(
        Path(os.environ.get("TRADER_ANALYSIS_CONFIG", "analysis.yaml"))
    )
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        raise AnalysisFailure("Set OPENAI_API_KEY in the environment or ignored .env first.")
    return analysis_service(path, settings), settings


def candidate_service(path: Path):
    from trading_assistant.adapters.candidate_store import JsonCandidateStore
    from trading_assistant.adapters.yahoo_candidates import YahooCandidateProvider
    from trading_assistant.application.candidates import CandidateService

    return CandidateService(
        repository(path),
        YahooCandidateProvider(),
        JsonCandidateStore(path.parent / "candidates" / "latest.json"),
    )


def opportunity_history_service(path: Path):
    from trading_assistant.application.recommendations import OpportunityHistoryService

    return OpportunityHistoryService(recommendation_reader(path))


def sentiment_store(path: Path):
    from trading_assistant.adapters.sentiment_store import JsonSentimentStore

    return JsonSentimentStore(path.parent / "sentiment")


def sentiment_service(path: Path, *, paid: bool = False, dashboard=None):
    from trading_assistant.application.sentiment import SentimentService

    settings, engine = None, None
    if paid:
        from trading_assistant.adapters.openai_sentiment import OpenAISentimentEngine
        from trading_assistant.analysis_config import load_analysis_settings
        from trading_assistant.domain.sentiment import SentimentFailure

        load_dotenv(Path(".env"), override=False)
        settings = load_analysis_settings(
            Path(os.environ.get("TRADER_ANALYSIS_CONFIG", "analysis.yaml"))
        )
        if not os.environ.get("OPENAI_API_KEY", "").strip():
            raise SentimentFailure("Set OPENAI_API_KEY before starting sentiment analysis.")
        engine = OpenAISentimentEngine(settings)
    return SentimentService(
        dashboard or dashboard_service(path),
        sentiment_store(path),
        engine,
        settings.quick_model if settings else "",
    )
