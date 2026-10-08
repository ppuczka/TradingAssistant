"""Composition root: connect application services to local adapters."""

from pathlib import Path

from trading_assistant.adapters.database import database_engine, migrate
from trading_assistant.adapters.portfolio_repository import SqlAlchemyPortfolioRepository
from trading_assistant.application.portfolio import PortfolioDashboardService


def repository(path: Path) -> SqlAlchemyPortfolioRepository:
    engine = database_engine(path)
    migrate(engine)
    return SqlAlchemyPortfolioRepository(engine)


def dashboard_service(path: Path) -> PortfolioDashboardService:
    return PortfolioDashboardService(repository(path))
