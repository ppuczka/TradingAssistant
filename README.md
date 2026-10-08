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
Positions include a separate Value USD column, populated through the public NBP
table A API. Holdings value and unrealized P/L cards show PLN and USD. The original
report values are preserved; USD amounts use the latest published reference rate,
not a live FX quote or historical performance rate. The source and publication date
appear in both views. No API key is required.

Rates are cached for one hour in memory and beside the database as
`nbp-usd-pln.json`. Requests time out after five seconds. On API failure, an older
cached rate is explicitly marked stale; without a usable rate, USD values remain
unavailable while PLN values remain visible.

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

Import scope is **My Trades and IKZE**. Investment Plan/Investment Plans records are
excluded, while ETFs under My Trades remain included. Transfers on the My Trades
side remain cash movements. When account-number cells are blank, `--account` is
required; a conflicting account selection is rejected.
Only PLN-denominated reports are currently supported. Explicit account/base
metadata and included-product valuation summaries must agree on PLN. Missing or
blank currency defaults to PLN by owner policy; conflicting and non-PLN evidence
is rejected. Explicit cash-row currencies must agree. Trade comments may include
a ticker, which must match the row ticker. Multiple detailed lots per ticker are
preserved and reconciled beneath the instrument summary. Foreign instrument execution prices are not
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
not live quotes. The summary shows holdings value and reported unrealized P/L,
rather than claiming a complete portfolio value or total investment return. Cash
balance is available after manual confirmation. Daily performance and full portfolio
total remain unavailable until their required inputs are established.
Imported net cash movements are shown separately from verified cash balance.

### Confirm cash balance

Provide the **My Trades cash balance in PLN**, excluding investment plans. You can
save it atomically with an import or set it later for an already imported account:

```sh
uv run trader import-xtb broker_reports/report.xlsx --account YOUR_ACCOUNT_ID --cash-balance 250.12
uv run trader set-cash 250.12 --account YOUR_ACCOUNT_ID
```

The timestamp defaults to the current time. For an earlier observation, use
`--cash-as-of "2026-10-08T16:00:00+02:00"` during import or
`--as-of "2026-10-08T16:00:00+02:00"` with `set-cash`.
Amounts must be finite and nonnegative; timestamps require a timezone and cannot
be in the future. Import `--dry-run` previews the balance without writing anything.

Cash confirmations are retained as dated observations, separate from transactions.
The latest observation per account is displayed, with its timestamp in alerts and
CLI output. Imports do not automatically change that balance: complete cash history
is not yet established. Refresh the confirmation after activity. With multiple
accounts, the cash total is shown only when all accounts have confirmations.
Holdings values and cash may have different timestamps, so a full portfolio total
is still unavailable.

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

Finnhub quote check:

Save `FINNHUB_API_KEY=your-key` in the ignored `.env` in the working directory,
then run `trader quote GE` (or `trader quote GE.US`). Existing environment values
 take precedence over `.env`. The command shows the quote timestamp and previous
close in USD; a retrieved quote is not necessarily a current trading-session quote.
This initial adapter supports US symbols only. Polish, German and UK holdings
need verified provider mappings and market access before dashboard valuation.
Dashboard values remain broker-report snapshots; this command does not change them.
History and fundamentals are explicit unimplemented operations for now.

The dashboard and P Portfolio screen now refresh automatically every five seconds.
Finnhub-backed US holdings have separate quote price, quote position value and day
change percentage columns, colored green for gains and red for losses. Report valuation and P/L
columns and headline totals remain report snapshots. Unsupported markets and failed
quotes show an explicit status instead of substituting a price. Day change is (quote price / previous close - 1) × 100%, not actual daily account
performance. No quotes are persisted and cash remains a confirmed manual snapshot.
Refreshes do not overlap; requests are paced to at most one per 1.1 seconds, and
HTTP 429 pauses Finnhub requests for a minute. A large portfolio or slow provider
can therefore take longer than the five-second refresh interval.

European quote integration uses `TWELVEDATA_API_KEY` from `.env` or the environment.
US holdings use Finnhub on the five-second dashboard cycle; European quotes and
failures are cached in memory for 60 seconds, including across the home and Portfolio
screens. The first request starts immediately. Twelve Data has a conservative local
budget of eight quote requests per rolling minute and pauses after rate limiting;
this does not remove provider daily credit limits. More than eight European symbols
may require additional refresh cycles.

Centralized exchange-qualified mapping covers XTB `.PL` -> XWAR, `.DE` -> XETR,
and `.UK` -> XLON. Each response must match the requested ticker and MIC; unsupported
or differently named instruments remain unavailable rather than falling back to
another exchange. Quote price and quantity-based position value now show the
provider's currency explicitly, including GBX/GBp (pence), without relabeling as USD.
Report USD conversion still uses NBP; EUR/GBP quote conversion is not implemented.
Daily percentage change uses provider previous close. European prices may be delayed
or end-of-day depending on market entitlement. Timestamps and failures appear in
alerts; report totals remain separate from quotes.

Yahoo Finance fallback is now enabled for the 13 verified European listings when
Twelve Data returns an unavailable quote, missing entitlement or request-limit error.
It also works without a Twelve Data key. Yahoo uses `yfinance` (an unofficial
personal-use integration), with successful quotes and failures cached for 60 seconds.
The initial fetch starts immediately and runs in a background thread. Returned
symbol, exchange, currency, positive prices and quote timestamp are validated;
missing fields are not filled from report data. Verified symbols are centralized
in SymbolResolver: `.DE` listings, the three London `.L` listings and the three
Warsaw `.WA` listings. New holdings need explicit verification before Yahoo fallback.
No Yahoo API key is required. Yahoo cookies/timezone cache stays under
`data/yahoo-cache`, separate from the portfolio database. Quotes may be delayed;
USD London listings keep their actual USD currency.

Portfolio tables include the full instrument name next to the ticker. The imported
name appears immediately; a provider's full listing name replaces it when available.
Today's P/L now shows a colored PLN estimate: sum of current quantity multiplied by
(quote price minus previous close), converted using NBP USD/EUR/GBP reference rates.
GBP pence quotes are divided by 100 before conversion. It requires every holding to
have a quote dated today (UTC) and a usable currency rate; otherwise the total remains
unavailable with a coverage explanation. This estimate excludes intraday trades,
realized gains, fees and daily FX movement. Cached FX publication dates and stale
status remain explicit in alerts; it is not audited daily account performance.
