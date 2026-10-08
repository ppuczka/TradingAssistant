"""CLI entry point. With no command, open the terminal dashboard."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table
from rich.text import Text

from trading_assistant.adapters.xtb import XtbReportReader
from trading_assistant.application.portfolio import ImportService
from trading_assistant.bootstrap import dashboard_service, repository
from trading_assistant.domain.portfolio import CashBalanceInput, ImportValidationError

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
    if snapshot.cash is not None:
        console.print(f"Confirmed cash: {snapshot.cash:,.2f} PLN • As of {snapshot.cash_as_of}")
    if not snapshot.positions:
        console.print("No positions available. No portfolio is connected yet.")
        for alert in snapshot.alerts:
            console.print(Text(alert))
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
    if snapshot.holdings_value is not None:
        console.print(f"Holdings value: {snapshot.holdings_value:,.2f} PLN")
    if snapshot.holdings_value_usd is not None:
        console.print(f"Holdings value USD: {snapshot.holdings_value_usd:,.2f} USD")
    if snapshot.unrealized_pnl is not None:
        console.print(f"Unrealized P/L: {snapshot.unrealized_pnl:,.2f} PLN")
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
    cash_balance: Annotated[
        str | None, typer.Option("--cash-balance", help="Confirmed My Trades cash in PLN.")
    ] = None,
    cash_as_of: Annotated[
        str | None,
        typer.Option("--cash-as-of", help="ISO timestamp with timezone; defaults to now."),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Parse and preview only; database is unchanged.")
    ] = False,
) -> None:
    """Import My Trades and IKZE; validate and reconcile before atomic commit."""
    console = Console()
    reader = XtbReportReader(account)
    balance = parse_cash_balance(cash_balance, cash_as_of)
    try:
        if dry_run:
            parsed = reader.read(report)
            console.print(
                f"Included portfolio preview: {len(parsed.cash_events)} cash operations, "
                f"{sum(e.action is not None for e in parsed.cash_events)} trades, "
                f"{len(parsed.positions)} open instruments. "
                f"Excluded plan rows: {parsed.excluded_rows}."
            )
            if balance is not None:
                console.print(
                    f"Cash preview: {balance.amount} PLN as of {balance.as_of.isoformat()}."
                )
            console.print("Database unchanged. Ledger reconciliation runs during import.")
            return
        result = ImportService(reader, repository(ctx.obj)).import_file(report, balance)
    except ImportValidationError as exc:
        console.print(Text(f"Import failed: {exc}"))
        raise typer.Exit(1) from exc
    console.print(
        f"{'Already imported' if result.already_imported else 'Import complete'}: "
        f"{result.cash_added} cash operations added, {result.trades_added} trades added, "
        f"{result.cash_duplicates} duplicates skipped, {result.positions} open instruments. "
        f"Excluded plan rows: {result.excluded_rows}."
    )
    if balance is not None:
        console.print(
            Text(
                f"Confirmed cash saved: {balance.amount:,.2f} PLN "
                f"as of {balance.as_of.isoformat()}."
            )
        )
    for warning in result.warnings:
        console.print(Text(warning))


def parse_cash_balance(amount: str | None, as_of: str | None) -> CashBalanceInput | None:
    if amount is None:
        if as_of is not None:
            raise typer.BadParameter("--cash-as-of requires --cash-balance.")
        return None
    try:
        return CashBalanceInput(
            amount=amount,
            as_of=as_of if as_of is not None else datetime.now(UTC),
        )
    except ValidationError as exc:
        raise typer.BadParameter(
            "Cash must be a finite nonnegative PLN amount; timestamp must be ISO "
            "format with a timezone and cannot be in the future."
        ) from exc


@app.command("set-cash")
def set_cash(
    ctx: typer.Context,
    amount: Annotated[str, typer.Argument(help="Confirmed My Trades cash balance in PLN.")],
    account: Annotated[str, typer.Option("--account", help="Previously imported XTB account ID.")],
    as_of: Annotated[
        str | None, typer.Option("--as-of", help="ISO timestamp with timezone; defaults to now.")
    ] = None,
) -> None:
    """Record a confirmed cash snapshot without importing a report."""
    balance = parse_cash_balance(amount, as_of)
    try:
        repository(ctx.obj).set_cash_balance(account, balance)
    except ImportValidationError as exc:
        Console().print(Text(f"Cash update failed: {exc}"))
        raise typer.Exit(1) from exc
    Console().print(
        Text(
            f"Confirmed My Trades cash: {balance.amount:,.2f} PLN "
            f"as of {balance.as_of.isoformat()}."
        )
    )
