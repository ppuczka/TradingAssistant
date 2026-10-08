"""CLI entry point. With no command, open the terminal dashboard."""

import asyncio
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from trading_assistant.adapters.xtb import XtbReportReader
from trading_assistant.application.portfolio import ImportService
from trading_assistant.bootstrap import dashboard_service, repository
from trading_assistant.domain.portfolio import ImportValidationError

app = typer.Typer(no_args_is_help=False, help="Personal AI portfolio and trading assistant.")
DEFAULT_DATABASE_PATH = Path("data/portfolio.sqlite3")


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    database: Annotated[
        Path, typer.Option("--database", envvar="TRADER_DATABASE_PATH", help="Local database path.")
    ] = DEFAULT_DATABASE_PATH,
) -> None:
    ctx.obj = database
    if ctx.invoked_subcommand is None:
        from trading_assistant.tui.dashboard import DashboardApp

        DashboardApp(dashboard_service(database)).run()


@app.command()
def portfolio(ctx: typer.Context) -> None:
    """Show the portfolio overview without opening the dashboard."""
    snapshot = asyncio.run(dashboard_service(ctx.obj).snapshot())
    console = Console()
    if not snapshot.positions:
        console.print("No positions available. No portfolio is connected yet.")
        return
    table = Table(title="Portfolio")
    for name in ("Symbol", "Quantity", "Value", "Currency", "Value USD", "P/L %", "AI"):
        table.add_column(name)
    for row in snapshot.positions:
        table.add_row(
            Text(row.symbol),
            str(row.quantity) if row.quantity is not None else "—",
            str(row.market_value) if row.market_value is not None else "—",
            Text(row.currency),
            f"{row.market_value_usd:,.2f}" if row.market_value_usd is not None else "—",
            str(row.pnl_percent) if row.pnl_percent is not None else "—",
            Text(row.recommendation or "—"),
        )
    console.print(table)
    for alert in snapshot.alerts:
        console.print(Text(alert))


@app.command("import-xtb")
def import_xtb(
    ctx: typer.Context,
    report: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    account: Annotated[
        str | None,
        typer.Option("--account", help="Explicit account ID if report metadata is blank."),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Parse and preview only; database is unchanged.")
    ] = False,
) -> None:
    """Import My Trades only; validate and reconcile before atomic commit."""
    console = Console()
    reader = XtbReportReader(account)
    try:
        if dry_run:
            parsed = reader.read(report)
            console.print(
                f"My Trades preview: {len(parsed.cash_events)} cash operations, "
                f"{sum(e.action is not None for e in parsed.cash_events)} trades, "
                f"{len(parsed.positions)} open instruments. "
                f"Excluded plan rows: {parsed.excluded_rows}."
            )
            console.print("Database unchanged. Ledger reconciliation runs during import.")
            return
        result = ImportService(reader, repository(ctx.obj)).import_file(report)
    except ImportValidationError as exc:
        console.print(Text(f"Import failed: {exc}"))
        raise typer.Exit(1) from exc
    console.print(
        f"{'Already imported' if result.already_imported else 'Import complete'}: "
        f"{result.cash_added} cash operations added, {result.trades_added} trades added, "
        f"{result.cash_duplicates} duplicates skipped, {result.positions} open instruments. "
        f"Excluded plan rows: {result.excluded_rows}."
    )
    for warning in result.warnings:
        console.print(Text(warning))
