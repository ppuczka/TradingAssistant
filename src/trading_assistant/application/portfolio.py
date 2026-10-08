import asyncio
from pathlib import Path
from typing import Protocol

from trading_assistant.application.dashboard import DashboardSnapshot, PortfolioRow
from trading_assistant.domain.portfolio import BrokerReport, ImportResult, PortfolioState


class ReportReader(Protocol):
    def read(self, path: Path) -> BrokerReport: ...


class PortfolioRepository(Protocol):
    def import_report(self, report: BrokerReport) -> ImportResult: ...

    def portfolio(self) -> PortfolioState: ...


class ImportService:
    def __init__(self, reader: ReportReader, repository: PortfolioRepository):
        self.reader = reader
        self.repository = repository

    def import_file(self, path: Path) -> ImportResult:
        return self.repository.import_report(self.reader.read(path))


class PortfolioDashboardService:
    def __init__(self, repository: PortfolioRepository):
        self.repository = repository

    async def snapshot(self) -> DashboardSnapshot:
        state = await asyncio.to_thread(self.repository.portfolio)
        alerts = list(state.warnings)
        if state.report_dates:
            dates = ", ".join(sorted({d.isoformat() for d in state.report_dates}))
            alerts.append(
                f"Values and P/L are broker-report snapshots as of {dates}; not live quotes."
            )
            alerts.append(
                "Cash balance is unverified: no opening balance supplied. "
                f"Imported net cash movements: {state.cash_movement:,.2f} PLN."
            )
        return DashboardSnapshot(
            currency="PLN",
            valuation_as_of=", ".join(sorted({d.isoformat() for d in state.report_dates})) or None,
            # Cash, daily performance, and full portfolio total remain unknown.
            positions=[
                PortfolioRow(
                    symbol=h.symbol,
                    quantity=h.quantity,
                    market_value=h.report_value,
                    currency="PLN",
                    pnl_percent=h.report_pnl_percent,
                )
                for h in state.holdings
            ],
            alerts=alerts,
        )
