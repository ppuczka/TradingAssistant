# Trading Assistant

A local-first personal investment assistant with a read-only Textual dashboard,
SQLite portfolio ledger, and XTB XLSX import. AI analysis and live prices are not
connected yet.

## Run

Requires Python 3.12 or 3.13 and uv.

```sh
uv sync
uv run trader
uv run trader portfolio
uv run trader --help
```

The dashboard shows portfolio totals, positions, allocation, AI status,
opportunities, and alerts. Missing data is shown as unavailable, never as a
fabricated balance or recommendation. No API key is needed to open the dashboard.
Positions include a separate Value USD column. Currency conversion is planned
through an application service using NBP reference rates; until connected, USD
values remain unavailable. The original value and its currency are preserved.

Press **P** to open Portfolio, **Esc** to return to the dashboard, **F5** to
refresh the current view, and **Q** to quit. Scroll to
see all panels on smaller terminals. Analyze, Scan, Review, History, Orders,
and Config shortcuts currently show an explicit availability notice.

The UI reads an injected `DashboardService` snapshot. It does not calculate
portfolio performance, call models, or execute orders.

## Import XTB reports

```sh
uv run trader import-xtb broker_reports/report.xlsx --account YOUR_ACCOUNT_ID --dry-run
uv run trader import-xtb broker_reports/report.xlsx --account YOUR_ACCOUNT_ID
uv run trader portfolio
uv run trader
```

Import scope is **My Trades only**. Investment Plan/Investment Plans records are
excluded, while ETFs under My Trades remain included. Transfers on the My Trades
side remain cash movements. When account-number cells are blank, `--account` is
required; a conflicting account selection is rejected.
Only verified PLN-denominated reports are currently supported. Currency must be
identified by explicit account/base-currency metadata or the My Trades valuation
summary. Missing, conflicting, or non-PLN currency evidence is rejected; explicit
cash-row currencies must agree. Foreign instrument execution prices are not
relabeled PLN.

`--dry-run` parses and previews without opening the database. The actual import
deduplicates cash operation IDs, derives quantities from buy/sell events, and
reconciles them against the open-position summary before committing atomically.
Incomplete history and conflicting imported events fail without saving portfolio
records. Individual broker lot links can remain unresolved and are flagged.
New trades are also reconciled against every stored snapshot for the same account.
Historical additions that invalidate any snapshot are rejected and rolled back,
preserving the previously displayed quantities and report valuations.
Closed-position and open-lot rows are preserved as evidence, not additional trades.

The dashboard shows report-dated PLN position values and unrealized P/L percentages,
not live quotes. Cash balance, daily performance, full portfolio total, and USD
conversions remain unavailable until their required inputs are established.
Imported net cash movements are shown separately from verified cash balance.

The database defaults to ignored `data/portfolio.sqlite3`; reports belong in
ignored `broker_reports/`. Select another database with a global option or env var:

```sh
uv run trader --database data/another.sqlite3 portfolio
TRADER_DATABASE_PATH=data/another.sqlite3 uv run trader portfolio
```

SQLite uses WAL and foreign keys. Monetary values and quantities are stored without
float conversion. Alembic migrations are applied automatically when opening the
repository; manual migration commands are available:

```sh
uv run alembic current
uv run alembic upgrade head
```

## Development

```sh
uv run pytest
uv run ruff check .
```

TradingAgents is an external dependency pinned to an upstream commit. The
dashboard does not import or initialize it. See `AGENTS.md` for architectural rules.
