"""CLI entry point. With no command, open the terminal dashboard."""

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table
from rich.text import Text

from trading_assistant.adapters.xtb import XtbReportReader
from trading_assistant.application.portfolio import ImportService
from trading_assistant.bootstrap import dashboard_service, repository
from trading_assistant.domain.portfolio import (
    AccountAliasInput,
    AccountValueInput,
    CashDepositInput,
    ImportValidationError,
)

app = typer.Typer(no_args_is_help=False, help="Personal AI portfolio and trading assistant.")
DEFAULT_DATABASE_PATH = Path("data/portfolio.sqlite3")


@app.command()
def quote(symbol: str) -> None:
    """Fetch a Finnhub US quote (canonical ticker or XTB .US symbol)."""
    from trading_assistant.adapters.finnhub import FinnhubMarketDataProvider
    from trading_assistant.application.market import SymbolResolver
    from trading_assistant.domain.market import MarketDataError

    load_dotenv(Path(".env"), override=False)
    try:
        resolved = SymbolResolver().finnhub_us(symbol)
        provider = FinnhubMarketDataProvider(os.environ.get("FINNHUB_API_KEY", ""))
        result = asyncio.run(provider.quote(resolved))
    except MarketDataError as exc:
        Console().print(Text(str(exc)))
        raise typer.Exit(1) from None
    Console().print(
        Text(
            f"{result.symbol}: {result.price} {result.currency} • "
            f"Previous close: {result.previous_close} • Quote as of {result.as_of.isoformat()} "
            f"• Retrieved {result.retrieved_at.isoformat()} • {result.source}"
        )
    )


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    database: Annotated[
        Path, typer.Option("--database", envvar="TRADER_DATABASE_PATH", help="Local database path.")
    ] = DEFAULT_DATABASE_PATH,
) -> None:
    ctx.obj = database
    if ctx.invoked_subcommand is None:
        from trading_assistant.bootstrap import (
            candidate_service,
            dashboard_analysis,
            opportunity_history_service,
            sentiment_service,
        )
        from trading_assistant.tui.dashboard import DashboardApp

        dashboard = dashboard_service(database)
        DashboardApp(
            dashboard,
            analysis_factory=lambda: dashboard_analysis(database),
            candidate_factory=lambda: candidate_service(database),
            opportunity_history_factory=lambda: opportunity_history_service(database),
            preferences_path=database.parent / "ui-preferences.json",
            sentiment_factory=lambda paid: sentiment_service(
                database, paid=paid, dashboard=dashboard
            ),
        ).run()


@app.command()
def portfolio(ctx: typer.Context) -> None:
    """Show the portfolio overview without opening the dashboard."""
    snapshot = asyncio.run(dashboard_service(ctx.obj).snapshot())
    console = Console()
    if snapshot.portfolio_value is not None:
        console.print(f"Total account value: {snapshot.portfolio_value:,.2f} PLN")
    if snapshot.cash is not None:
        console.print(
            f"Derived cash reserve: {snapshot.cash:,.2f} PLN • As of {snapshot.cash_as_of}"
        )
    if not snapshot.positions:
        console.print("No positions available. No portfolio is connected yet.")
        for alert in snapshot.alerts:
            console.print(Text(alert))
        return
    table = Table(title="Portfolio")
    for name in (
        "Symbol",
        "Name",
        "Quantity",
        "Value",
        "Currency",
        "Value USD",
        "Report P/L %",
        "Est. P/L PLN",
        "Est. P/L %",
        "AI",
    ):
        table.add_column(name)
    for row in snapshot.positions:
        table.add_row(
            Text(row.symbol),
            Text(row.name),
            str(row.quantity) if row.quantity is not None else "—",
            str(row.market_value) if row.market_value is not None else "—",
            Text(row.currency),
            f"{row.market_value_usd:,.2f}" if row.market_value_usd is not None else "—",
            str(row.pnl_percent) if row.pnl_percent is not None else "—",
            f"{row.estimated_pnl_pln:+,.2f}" if row.estimated_pnl_pln is not None else "—",
            f"{row.estimated_pnl_percent:+.2f}%" if row.estimated_pnl_percent is not None else "—",
            Text(row.recommendation or "—"),
        )
    console.print(table)
    if snapshot.holdings_value is not None:
        console.print(f"Holdings value: {snapshot.holdings_value:,.2f} PLN")
    if snapshot.holdings_value_usd is not None:
        console.print(f"Holdings value USD: {snapshot.holdings_value_usd:,.2f} USD")
    if snapshot.unrealized_pnl is not None:
        console.print(f"Report unrealized P/L: {snapshot.unrealized_pnl:,.2f} PLN")
    if snapshot.estimated_unrealized_pnl is not None:
        label = (
            "Partial estimate"
            if snapshot.estimated_pnl_covered < snapshot.estimated_pnl_total
            else "Estimated"
        )
        console.print(
            f"{label} unrealized P/L: {snapshot.estimated_unrealized_pnl:+,.2f} PLN "
            f"• {snapshot.estimated_pnl_covered}/{snapshot.estimated_pnl_total} holdings"
        )
    if snapshot.today_pnl is not None:
        label = (
            "partial estimate"
            if snapshot.today_pnl_covered < snapshot.today_pnl_total
            else "estimated"
        )
        console.print(
            f"Today's P/L ({label}): {snapshot.today_pnl:,.2f} PLN • "
            f"{snapshot.today_pnl_covered}/{snapshot.today_pnl_total} holdings"
        )
    for alert in snapshot.alerts:
        console.print(Text(alert))


@app.command("import-xtb")
def import_xtb(
    ctx: typer.Context,
    report: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    account: Annotated[
        str | None,
        typer.Option("--account", help="Account ID or existing alias if report metadata is blank."),
    ] = None,
    account_value: Annotated[
        str | None,
        typer.Option(
            "--account-value",
            "--cash-balance",
            help="Total account value including holdings, in PLN.",
        ),
    ] = None,
    cash_as_of: Annotated[
        str | None,
        typer.Option(
            "--value-as-of", "--cash-as-of", help="ISO timestamp with timezone; defaults to now."
        ),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Parse and preview only; database is unchanged.")
    ] = False,
) -> None:
    """Import My Trades and IKZE; validate and reconcile before atomic commit."""
    console = Console()
    if account is not None:
        from trading_assistant.adapters.portfolio_repository import resolve_import_account

        account = resolve_import_account(ctx.obj, account)
    reader = XtbReportReader(account)
    balance = parse_account_value(account_value, cash_as_of)
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
                    f"Account value preview: {balance.amount} PLN "
                    f"as of {balance.as_of.isoformat()}."
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
                f"Total account value saved: {balance.amount:,.2f} PLN "
                f"as of {balance.as_of.isoformat()}."
            )
        )
    for warning in result.warnings:
        console.print(Text(warning))


def parse_account_value(amount: str | None, as_of: str | None) -> AccountValueInput | None:
    if amount is None:
        if as_of is not None:
            raise typer.BadParameter("--value-as-of requires --account-value.")
        return None
    try:
        return AccountValueInput(
            amount=amount,
            as_of=as_of if as_of is not None else datetime.now(UTC),
        )
    except ValidationError as exc:
        raise typer.BadParameter(
            "Account value must be a finite nonnegative PLN amount; timestamp must be ISO "
            "format with a timezone and cannot be in the future."
        ) from exc


@app.command("set-cash", hidden=True)
@app.command("set-account-value")
def set_cash(
    ctx: typer.Context,
    amount: Annotated[str, typer.Argument(help="Total account value including holdings, in PLN.")],
    account: Annotated[str, typer.Option("--account", help="Imported XTB account ID or alias.")],
    as_of: Annotated[
        str | None, typer.Option("--as-of", help="ISO timestamp with timezone; defaults to now.")
    ] = None,
) -> None:
    """Record total account value including holdings without importing a report."""
    balance = parse_account_value(amount, as_of)
    try:
        repository(ctx.obj).set_account_value(account, balance)
    except ImportValidationError as exc:
        Console().print(Text(f"Account value update failed: {exc}"))
        raise typer.Exit(1) from exc
    Console().print(
        Text(f"Total account value: {balance.amount:,.2f} PLN as of {balance.as_of.isoformat()}.")
    )


@app.command("add-money")
def add_money(
    ctx: typer.Context,
    amount: Annotated[str, typer.Argument(help="Amount already deposited at the broker, in PLN.")],
    account: Annotated[str, typer.Option("--account", help="Imported XTB account ID or alias.")],
) -> None:
    """Record a deposit and increase the account's existing total account value."""
    try:
        deposit = CashDepositInput(amount=amount)
    except ValidationError as exc:
        raise typer.BadParameter("Deposit must be a finite positive PLN amount.") from exc
    try:
        balance = repository(ctx.obj).add_money(account, deposit)
    except ImportValidationError as exc:
        Console().print(Text(f"Deposit failed: {exc}"))
        raise typer.Exit(1) from exc
    Console().print(
        Text(
            f"Recorded deposit: {deposit.amount:,.2f} PLN. "
            f"Account {account} total value: {balance.amount:,.2f} PLN."
        )
    )


@app.command("set-account-alias")
def set_account_alias(
    ctx: typer.Context,
    alias: Annotated[str, typer.Argument(help="Unique account alias, e.g. ikze.")],
    account: Annotated[
        str, typer.Option("--account", help="Imported account ID or current alias.")
    ],
) -> None:
    """Assign or replace an account alias (case insensitive)."""
    try:
        value = AccountAliasInput(alias=alias)
    except ValidationError as exc:
        raise typer.BadParameter(
            "Alias must start with a letter and contain up to 64 letters, digits, "
            "underscores or hyphens."
        ) from exc
    try:
        repository(ctx.obj).set_account_alias(account, value)
    except ImportValidationError as exc:
        Console().print(Text(f"Alias update failed: {exc}"))
        raise typer.Exit(1) from exc
    Console().print(Text(f"Account alias set: {value.alias}."))


@app.command("analyze")
def analyze(
    ctx: typer.Context,
    symbol: Annotated[
        str, typer.Argument(help="US ticker or verified XTB .US/.DE/.UK/.PL symbol.")
    ],
    config: Annotated[Path, typer.Option("--config", help="Analysis YAML configuration.")] = Path(
        "analysis.yaml"
    ),
    strategy: Annotated[str, typer.Option(help="LONG_TERM, POSITION, or TRADE.")] = "LONG_TERM",
    horizon: Annotated[
        str | None, typer.Option(help="Investment horizon; defaults to configured value.")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show request/config without paid analysis.")
    ] = False,
    lookup: Annotated[
        bool,
        typer.Option(
            "--lookup", help="Verify typed listing with Yahoo (network, even with --dry-run)."
        ),
    ] = False,
) -> None:
    """Analyze a US or verified German/UK/Polish holding with portfolio context."""
    from trading_assistant.analysis_config import load_analysis_settings
    from trading_assistant.bootstrap import analysis_service
    from trading_assistant.domain.analysis import AnalysisFailure, Strategy

    load_dotenv(Path(".env"), override=False)
    console = Console()
    try:
        settings = load_analysis_settings(config)
        service = analysis_service(ctx.obj, settings)
        arguments = (symbol, Strategy(strategy.upper()), horizon or settings.default_horizon)
        request = (
            asyncio.run(service.prepare_request(*arguments))
            if lookup
            else service.request(*arguments)
        )
        if dry_run:
            console.print_json(
                data={
                    "request": request.model_dump(mode="json"),
                    "settings": settings.model_dump(mode="json"),
                }
            )
            return
        if not os.environ.get("OPENAI_API_KEY", "").strip():
            raise AnalysisFailure(
                "OPENAI_API_KEY is missing; set it in the environment or ignored .env."
            )
        console.print(
            Text(
                f"Analyzing {request.symbol} • "
                f"{'Owned position' if request.owned else 'Opportunity'} • "
                f"{request.strategy.value} • {request.time_horizon}\n"
                f"Quick: {settings.quick_model} • Deep: {settings.deep_model} • "
                "OpenAI API calls are billed."
            )
        )
        with console.status("TradingAgents is analyzing…"):
            result = asyncio.run(service.analyze(request))
        console.print_json(result.model_dump_json(exclude={"reports"}))
        console.print(
            Text("Recommendation saved locally. Final investment decision belongs to you.")
        )
    except (AnalysisFailure, ValueError) as exc:
        message = (
            str(exc)
            if isinstance(exc, AnalysisFailure)
            else "Invalid strategy, horizon, or analysis input."
        )
        console.print(Text(f"Analysis failed: {message}"))
        raise typer.Exit(1) from exc


@app.command("analysis-report")
def analysis_report(
    ctx: typer.Context,
    symbol: str,
    section: Annotated[str, typer.Option(help="Report section or all.")] = "all",
) -> None:
    """Read a saved full analysis without model calls or an API key."""
    from rich.markdown import Markdown

    service = dashboard_service(ctx.obj)
    console = Console()
    try:
        saved = asyncio.run(service.saved_analysis(symbol))
    except ValueError as exc:
        console.print(Text(f"Report unavailable: {exc}"))
        raise typer.Exit(1) from exc
    if saved is None:
        console.print("No saved analysis for this symbol.")
        raise typer.Exit(1)
    result = saved.recommendation
    console.print(
        Text(f"{result.symbol} • {result.action.value} • {result.analyzed_at.isoformat()}")
    )
    reports = {"decision": "\n\n".join(result.reasoning), **result.reports}
    if section != "all" and section not in reports:
        console.print("Section unavailable in this saved run.")
        raise typer.Exit(1)
    for name, content in reports.items():
        if section in {"all", name}:
            console.print(Text(name.replace("_", " ").title(), style="bold"))
            console.print(Markdown(content))


@app.command("review")
def review(
    ctx: typer.Context,
    market: Annotated[str, typer.Option(help="Review market: US, EU (Germany/UK), or PL.")] = "US",
    strategy: Annotated[str, typer.Option(help="LONG_TERM, POSITION, or TRADE.")] = "LONG_TERM",
    horizon: Annotated[str | None, typer.Option(help="Investment horizon.")] = None,
    config: Annotated[Path, typer.Option(help="Analysis YAML configuration.")] = Path(
        "analysis.yaml"
    ),
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Preview holdings/settings without paid calls.")
    ] = False,
) -> None:
    """Review existing holdings sequentially with a frozen portfolio context."""
    from trading_assistant.analysis_config import load_analysis_settings
    from trading_assistant.application.review import PortfolioReviewService
    from trading_assistant.bootstrap import analysis_service
    from trading_assistant.domain.analysis import AnalysisFailure, Strategy

    if market.upper() not in {"US", "EU", "PL"}:
        raise typer.BadParameter("Use --market US, EU (excluding Poland), or PL.")
    load_dotenv(Path(".env"), override=False)
    console = Console()
    try:
        settings = load_analysis_settings(config)
        analysis = analysis_service(ctx.obj, settings)
        plan = analysis.review_plan(
            Strategy(strategy.upper()), horizon or settings.default_horizon, market=market.upper()
        )
        if dry_run:
            console.print_json(
                data={
                    "market": market.upper(),
                    "symbols": [r.symbol for r in plan.requests],
                    "skipped": plan.skipped,
                    "strategy": strategy.upper(),
                    "horizon": horizon or settings.default_horizon,
                    "settings": settings.model_dump(mode="json"),
                }
            )
            return
        if not plan.requests:
            console.print(f"No supported {market.upper()} holdings to review.")
            return
        if not os.environ.get("OPENAI_API_KEY", "").strip():
            raise AnalysisFailure("Set OPENAI_API_KEY in the environment or ignored .env first.")
        console.print(
            Text(
                f"Reviewing {len(plan.requests)} {market.upper()} holdings sequentially • "
                "OpenAI API calls are billed."
            )
        )

        def progress(index, total, item):
            console.print(
                Text(
                    f"{index}/{total} {item.symbol}: {item.status}"
                    + (f" • {item.recommendation.action.value}" if item.recommendation else "")
                    + (f" • {item.error}" if item.error else "")
                )
            )

        result = asyncio.run(PortfolioReviewService(analysis).run(plan, progress=progress))
        console.print(Text(f"Skipped unsupported holdings: {', '.join(result.skipped) or 'none'}"))
        if any(item.status == "FAILED" for item in result.items):
            raise typer.Exit(1)
    except (AnalysisFailure, ValueError) as exc:
        message = (
            str(exc)
            if isinstance(exc, AnalysisFailure)
            else "Invalid strategy, horizon or configuration."
        )
        console.print(Text(f"Review failed: {message}"))
        raise typer.Exit(1) from exc


@app.command("candidates")
def candidates(
    ctx: typer.Context,
    market: Annotated[
        str, typer.Option(help="US, PL, or US,PL; other markets are excluded.")
    ] = "US,PL",
    limit: Annotated[int, typer.Option(min=1, max=100, help="Maximum candidates per market.")] = 20,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print structured candidate data.")
    ] = False,
) -> None:
    """Fetch Yahoo stock candidates without AI calls or investment recommendations."""
    from trading_assistant.bootstrap import candidate_service
    from trading_assistant.domain.candidates import CandidateUnavailableError

    markets = list(dict.fromkeys(m.strip().upper() for m in market.split(",")))
    if not markets or any(m not in {"US", "PL"} for m in markets):
        raise typer.BadParameter("Use --market US, PL, or US,PL.")
    console = Console()
    try:
        if as_json:
            batch = asyncio.run(candidate_service(ctx.obj).fetch(markets, limit))
        else:
            with console.status("Fetching Yahoo screener candidates…"):
                batch = asyncio.run(candidate_service(ctx.obj).fetch(markets, limit))
    except CandidateUnavailableError as exc:
        console.print(Text(f"Candidate fetch failed: {exc}"))
        raise typer.Exit(1) from exc
    if as_json:
        console.print_json(batch.model_dump_json())
    else:
        table = Table(title="Yahoo candidates • Not AI recommendations")
        for name in [
            "Market",
            "Symbol",
            "Name",
            "Price",
            "Market cap",
            "Avg volume",
            "Quote time UTC",
        ]:
            table.add_column(name)
        for candidate in batch.candidates:
            table.add_row(
                candidate.market,
                Text(candidate.symbol),
                Text(candidate.name),
                f"{candidate.price:,.2f} {candidate.currency}",
                f"{candidate.market_cap:,.0f} {candidate.currency}",
                f"{candidate.average_volume:,.0f}",
                candidate.quote_as_of.strftime("%m-%d %H:%M"),
            )
        console.print(table)
        console.print(
            Text(
                f"{len(batch.candidates)} candidates • {batch.excluded_owned} owned excluded • "
                f"{batch.rejected} invalid/out-of-filter rows rejected."
            )
        )
        console.print(
            "Ranked by market cap within each market; minimum average volume filters apply. "
            "Warsaw listings can include foreign issuers. XTB availability is unverified. "
            "No AI calls made. Snapshot saved beside the database in candidates/latest.json."
        )
        for warning in batch.warnings:
            console.print(Text(warning))
        for name, error in batch.errors.items():
            console.print(Text(f"{name}: {error}"))
    if batch.errors:
        raise typer.Exit(1)


@app.command("opportunities")
def opportunities(
    ctx: typer.Context,
    market: Annotated[str, typer.Option(help="Yahoo markets: US, PL, or US,PL.")] = "US",
    limit: Annotated[int, typer.Option(min=1, max=10, help="Maximum paid analyses.")] = 3,
    strategy: Annotated[str, typer.Option(help="LONG_TERM, POSITION, or TRADE.")] = "LONG_TERM",
    horizon: Annotated[str | None, typer.Option(help="Investment horizon.")] = None,
    config: Annotated[Path, typer.Option(help="Analysis YAML.")] = Path("analysis.yaml"),
    dry_run: Annotated[bool, typer.Option(help="Fetch and preview without model calls.")] = False,
) -> None:
    """Analyze a bounded Yahoo candidate batch with portfolio-aware TradingAgents."""
    from trading_assistant.analysis_config import load_analysis_settings
    from trading_assistant.application.review import PortfolioReviewService, ordered_opportunities
    from trading_assistant.bootstrap import analysis_service, candidate_service
    from trading_assistant.domain.analysis import AnalysisFailure, Strategy
    from trading_assistant.domain.candidates import CandidateUnavailableError

    markets = list(dict.fromkeys(m.strip().upper() for m in market.split(",")))
    if any(m not in {"US", "PL"} for m in markets):
        raise typer.BadParameter("Use US, PL, or US,PL.")
    console = Console()
    load_dotenv(Path(".env"), override=False)
    try:
        settings = load_analysis_settings(config)
        selected_strategy = Strategy(strategy.upper())
        analysis = analysis_service(ctx.obj, settings)
        batch = asyncio.run(candidate_service(ctx.obj).fetch(markets, 20))
        for warning in batch.warnings:
            console.print(Text(warning))
        for name, error in batch.errors.items():
            console.print(Text(f"{name}: {error}"))
        plan = analysis.opportunity_plan(
            batch.candidates, selected_strategy, horizon or settings.default_horizon, limit
        )
        if dry_run:
            console.print_json(data=plan.model_dump(mode="json"))
            return
        if not plan.requests:
            console.print("No eligible candidates; no model calls made.")
            raise typer.Exit(1)
        if not os.environ.get("OPENAI_API_KEY", "").strip():
            raise AnalysisFailure("Set OPENAI_API_KEY in the environment or ignored .env first.")
        console.print(
            Text(
                f"Analyzing {len(plan.requests)} candidates in Yahoo order • Paid OpenAI calls. "
                "XTB availability unverified. Results group BUY / WATCH / IGNORE."
            )
        )

        def progress(index, total, item):
            console.print(Text(f"{index}/{total} {item.symbol}: {item.status}"))

        result = asyncio.run(PortfolioReviewService(analysis).run(plan, progress=progress))
        for item in ordered_opportunities(result.items):
            recommendation = item.recommendation
            console.print(
                Text(
                    f"{item.symbol}: "
                    f"{recommendation.action.value if recommendation else item.status}"
                    + (
                        f" • Engine rating: {recommendation.engine_rating}"
                        if recommendation
                        else ""
                    )
                )
            )
            console.print(
                Text("\n".join(recommendation.reasoning) if recommendation else item.error or "")
            )
        if batch.errors or any(i.status == "FAILED" for i in result.items):
            raise typer.Exit(1)
    except (AnalysisFailure, CandidateUnavailableError, ValueError):
        console.print(
            "Opportunity review failed. Check configuration, portfolio and candidate data."
        )
        raise typer.Exit(1) from None


@app.command("opportunity-reviews")
def opportunity_reviews(
    ctx: typer.Context,
    history: Annotated[
        bool, typer.Option(help="Show all runs, instead of latest per symbol.")
    ] = False,
    symbol: Annotated[
        str | None, typer.Option(help="Show full saved reports for this exact symbol.")
    ] = None,
):
    """Read saved opportunity reviews without fetching quotes or calling models."""
    from rich.markdown import Markdown

    from trading_assistant.bootstrap import opportunity_history_service

    console = Console()
    try:
        records = asyncio.run(opportunity_history_service(ctx.obj).records(history=history))
    except Exception:
        console.print("Unable to read saved opportunity reviews.")
        raise typer.Exit(1) from None
    if symbol:
        records = [
            saved for saved in records if saved.recommendation.symbol == symbol.strip().upper()
        ]
    if not records:
        console.print("No saved opportunity reviews found.")
        return
    for saved in records:
        result = saved.recommendation
        console.print(
            Text(
                f"{result.symbol} • {result.action.value} • Engine rating: {result.engine_rating} "
                f"• {result.analyzed_at.isoformat()} "
                f"• {saved.request.strategy.value} • {saved.request.time_horizon}"
            )
        )
        if symbol:
            for name, content in {
                "decision": "\n\n".join(result.reasoning),
                **result.reports,
            }.items():
                console.print(Text(name.replace("_", " ").title(), style="bold"))
                console.print(Markdown(content))


@app.command("risk")
def risk_summary(
    ctx: typer.Context,
    config: Annotated[Path | None, typer.Option(help="Risk thresholds YAML.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print structured summary.")] = False,
):
    """Assess report concentration and derived cash reserve locally, without AI or quotes."""
    from trading_assistant.application.risk import assess_portfolio
    from trading_assistant.bootstrap import repository
    from trading_assistant.risk_config import load_risk_settings

    load_dotenv(Path(".env"), override=False)
    console = Console()
    try:
        settings = load_risk_settings(
            config or Path(os.environ.get("TRADER_RISK_CONFIG", "risk.yaml"))
        )
        state = repository(ctx.obj).portfolio()
        result = assess_portfolio(state, settings)
    except ValueError:
        console.print("Unable to assess portfolio risk; check database and risk configuration.")
        raise typer.Exit(1) from None
    if json_output:
        console.print_json(
            data={
                "summary": result.model_dump(mode="json"),
                "thresholds": settings.model_dump(mode="json"),
                "report_dates": [d.isoformat() for d in state.report_dates],
                "cash_dates": [d.isoformat() for d in state.account_value_dates],
            }
        )
        return
    console.print(Text(f"Report-based portfolio risk • {result.status}\n{result.basis}"))
    if result.largest_percent is not None:
        console.print(
            Text(f"Largest position: {result.largest_symbol} • {result.largest_percent:.1f}%")
        )
    if result.top_five_percent is not None:
        console.print(f"Top five: {result.top_five_percent:.1f}%")
    console.print(
        f"Cash reserve: {result.cash_percent:.1f}%"
        if result.cash_percent is not None
        else "Cash reserve: unknown"
    )
    for warning in result.warnings:
        console.print(Text(warning))
    console.print(
        "Concentration and cash checks only; not an overall risk score or order approval."
    )


@app.command("sentiment")
def sentiment(
    ctx: typer.Context,
    generate: Annotated[
        bool, typer.Option(help="Refresh evidence and make a paid model call.")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option(help="Refresh evidence and preview, without AI calls.")
    ] = False,
):
    """Show saved US/PL sentiment; generation is explicitly opt-in."""
    from trading_assistant.bootstrap import sentiment_service
    from trading_assistant.domain.sentiment import SentimentFailure

    console = Console()
    if generate and dry_run:
        raise typer.BadParameter("Use --generate or --dry-run, not both.")

    async def run():
        service = sentiment_service(ctx.obj, paid=generate)
        if generate or dry_run:
            request = await service.request()
            if dry_run:
                console.print_json(request.model_dump_json())
                return
            console.print("Generating portfolio-linked sentiment • Paid OpenAI call.")
            saved = await service.assess(request)
        else:
            saved = await service.saved()
        if saved is None:
            console.print(
                "No saved sentiment. Use --dry-run to inspect evidence or --generate to analyze."
            )
        else:
            console.print_json(saved.model_dump_json())

    try:
        asyncio.run(run())
    except (SentimentFailure, ValueError):
        console.print("Sentiment unavailable; check evidence, configuration and API access.")
        raise typer.Exit(1) from None
