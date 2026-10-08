# Implementation plan

## Current status and agreed delivery order

The first read-only Textual dashboard is implemented. `uv run trader` opens it;
`uv run trader portfolio` shows the CLI overview. Both consume an application
service backed by a SQLite ledger. The initial M3/M4/M5 slice is implemented:
SQLAlchemy models, Alembic migration, exact decimal storage, My Trades-only XLSX
import, source-event deduplication, atomic commits, and ledger-to-report quantity
reconciliation. The supplied report has been imported and repeat import verified.
The importer defaults missing currency to PLN by owner policy, rejects explicit
non-PLN or conflicting currency evidence, includes My Trades and IKZE, and checks newly added trades
against all stored snapshots for the same account before commit, including snapshots
before and after the incoming report's as-of date.
Market-data integration and the AnalysisEngine adapter remain unimplemented.
Unavailable AI/navigation features show availability notices rather than running
placeholder analyses. Synthetic tests cover CLI/dashboard behavior, imports,
overlapping reports, rollback, account separation, precision, and database constraints.
Report-dated holdings value and unrealized P/L are displayed in PLN and USD.
The NBP table A adapter, typed FX boundary, Decimal conversion, one-hour cache,
publication provenance, timeout, and explicit stale-cache fallback are implemented.
Manual My Trades PLN cash confirmations are supported through import options and
`trader set-cash`, with timezone-aware timestamps and an additive migration. Cash
observations remain separate from the ledger and are not automatically advanced.
A cash total requires a confirmation for every account. Live stock prices and
daily/total performance remain unavailable. Lot-ID discrepancies are flagged,
not resolved by invented trades.

The project has a Python 3.13 uv environment, installed dependencies, and a
`uv.lock`. TradingAgents 0.6.0 is installed as an external dependency pinned to
upstream commit `1394a3f72aa4393e1a98f51b382434c4b4c2d972`. No paid analysis has
been run from TradingAssistant; its analysis integration remains to be verified.

The owner has prioritized real portfolio data and intraday valuations before AI
integration. Retain the original milestone names for reference, but deliver in
this order:

1. Inspect anonymized XTB report samples.
2. M3/M4: persistence, accounts, instruments, and portfolio services.
3. M5: validated, repeatable XTB import and reconciliation.
4. M6: symbol resolution, brought forward before fetching market quotes.
5. Connect the existing M8 dashboard to holdings and cash, then add independent
   market data and intraday valuations.
6. Complete M1/M2 analysis contracts and TradingAgents adapter, then M7
   portfolio-aware review using the stored portfolio and valuation context.
7. M9 performance history, M10 deterministic risk engine, and M11 paper broker later.

Next: investigate remaining My Trades lot-ID mappings and opening-cash evidence;
then implement M6 symbol/currency resolution and independent quote/FX providers.
The importer intentionally rejects incomplete quantity history; explicit opening
balances and automatic correction/replacement of changed source events are not
implemented. Dry-run currently parses/previews only; reconciliation against the
stored ledger runs in the real import transaction.

This changes delivery order, not the architectural boundaries. Implement each
slice independently; do not build later capabilities just to fill dashboard panels.

## Next slice: report inspection and database design

Inspect anonymized examples of Closed Positions, Cash Operations, and Open
Positions before fixing the importer schema. Confirm formats, sheets/columns,
identifiers, account currency, timestamp/timezone semantics, fractional quantities,
fees, dividends, deposits/withdrawals, and the meaning of reported prices and P/L.
Check how sections relate and whether reports include complete transaction history.

Reports may represent a bounded period or current state rather than a full ledger.
Do not invent missing trades or treat closed-position summaries as complete fills.
When history is incomplete, design an explicit opening-balance mechanism with an
as-of date and provenance. Open-position records are reconciliation evidence,
not a second source of trades to count alongside history.

Use SQLite with SQLAlchemy 2 and Alembic. Enable foreign keys on every connection
and configure WAL. Keep database access behind application-facing interfaces and
use Decimal-aware storage for quantities and money. Preserve currency per amount;
do not sum PLN and USD directly.

Start with accounts, instruments, broker instrument identities, transactions,
cash operations, and minimal import provenance needed for duplicate detection.
Choose exact columns after inspecting reports. Transactions and explicit opening
balances establish the ledger; derive positions and cash through portfolio services.
Defer recommendation/order/snapshot tables to the milestones that need them.

Acceptance: migrations create an empty database, multiple accounts stay distinct,
and synthetic ledger records produce reproducible holdings and cash balances.

### First report inspection: schema implications (2026-10-08)

The first provided workbook contains Export Summary, Closed Positions, Cash
Operations, and Open Positions. It was re-exported through Apple Numbers, so
support this inspected layout explicitly and validate future broker-native exports
separately. The original report is private and must remain unchanged and ignored.
Detailed inspection results are kept in ignored `broker_reports/IMPORT_NOTES.md`.
Its account-number cells are blank, so imports require explicit `--account`.
For the owner's first import, the account identifier from the filename was
supplied explicitly; the parser does not infer account identity from filenames.

Owner-confirmed scope: this report is for one PLN-base-currency account. Import
only rows whose Product is `My Trades`. Exclude `Investment Plan` and
`Investment Plans` from ledger events, holdings, valuation summaries, dashboard
totals, and AI context. ETFs in My Trades remain included; scope is determined
by Product, not instrument category. Individual investment plans are out of scope.

Normalize Product labels consistently in all three broker sheets before applying
the filter. The dry-run summary should report excluded row counts without
persisting excluded holdings. Blank/unknown Product labels require review rather
than automatic inclusion.

Model instrument type independently from where it is held:

- `instruments.asset_type`: STOCK, ETF, ETC, or UNKNOWN for unverified categories.
  Preserve the original broker category. Do not silently turn an unfamiliar asset
  into a stock. ETF and ETC must remain distinct.
- Preserve the MY_TRADES product label on imported events. Recognize
  INVESTMENT_PLANS only to exclude its rows; do not build plan-management tables.
- Ledger events and resulting holdings retain their account and portfolio group.
  The same instrument can appear in several groups; aggregate only for a deliberate
  consolidated view. Product groups do not create additional instrument identities.
- Individual named-plan identity and assignment are excluded from the current scope.
- Strategy and requested horizon belong to the user's holding/group context,
  not the shared instrument. Do not infer them solely from ETF classification.

Import ETFs and ETCs into the same quantity/cash ledger as stocks, retaining
fractional units. Quote and value the listed instrument without expanding it into
constituent trades. Future ETF/ETC analysis must use appropriate fund/underlying
exposure context; do not assume TradingAgents' company-stock pipeline is suitable
for every category. Unsupported analysis must be explicit. Listing exchange is
not a proxy for geographic investment exposure.

The concrete import sequence for this layout is:

1. Detect sections by normalized header names, not fixed row numbers. Extract
   account/date metadata and honor the workbook's Excel date system. Ignore
   Export Summary as transaction data. Exclude totals and blank/separator rows.
   Apply the My Trades Product filter before staging any ledger or holding data.
2. Stage Cash Operations as source events keyed by account plus operation ID.
   Preserve source row, product label, position ID, amount, and comment privately.
   Account base currency is confirmed as PLN for the first account. Each report
   must independently establish PLN denomination through explicit account/base
   currency metadata or My Trades valuation summaries; missing values default to PLN by owner policy; conflicting or
   non-PLN denominations are rejected. Explicit cash-row currencies must agree.
   Verify broker cash-field conventions
   before assigning units to imported amounts; foreign instrument execution and
   quote prices must retain their own currency rather than being labeled PLN.
3. Parse both `OPEN BUY q @ p` and `OPEN BUY q/total @ p`, and equivalent CLOSE
   shapes, through a strict, tested broker-comment parser. For observed slash
   forms, the first quantity is the individual cash event's executed units;
   the second is contextual total volume, not another execution. Keep original
   text and mark unsupported formats for review rather than guessing.
4. Create one trade transaction for each supported purchase/sale cash event,
   linking its cash effect to the same source operation. Store each cash effect
   once; do not add another debit/credit when deriving a trade transaction.
   Preserve distinct operation IDs even when events share a time, ticker, price,
   or position ID.
5. Use Closed Positions to enrich/reconcile realized results and lot links, not
   to create a second set of trades. Repeated position IDs represent partial
   closes; position ID is not a unique close key. Timestamp-only or first-match
   joins are insufficient when several candidates exist. Quantity reconciliation
   currently passes by product/instrument, but some position-level links remain
   unresolved and must not be silently repaired by synthetic trades.
6. Parse Open Positions into separate instrument summary rows and detailed lot
   rows. Inherit verified category/name from each summary into its lots. Use
   only one level for holdings totals; reconcile lot sums against summary rows.
   Retain report prices/values as as-of reconciliation evidence, not live quotes
   or additional transactions.
7. Handle dividends, withholding/transaction taxes, interest, interest tax,
   deposits, transfers, and subaccount transfers as distinct cash event types.
   Preserve unrecognized types for explicit review. Keep only the My Trades-side
   leg of transfers involving excluded plans. These are boundary cash flows for
   the tracked portfolio, not investment income or loss; do not cancel them with
   the excluded leg. Preserve their internal-transfer classification for possible
   future consolidated reporting.
   Never invent the missing counterpart of an inter-account transfer before the
   other account's records arrive.
8. Reconcile quantities by account/product/instrument and inspect lot exceptions.
   Reconcile cash to a verified opening/closing balance when available. A Cash
   Operations Total is an activity sum, not proof of current cash balance.

Before implementation, verify original report monetary-field conventions and cash
opening-balance evidence. Recheck lot-ID reconciliation using only the included
My Trades rows: prior inspection counts covered the entire workbook, including
excluded plans. A wide report date range alone does not prove complete history.

The next implementation remains M3/M4: migration plus account, instrument,
ledger, and provenance interfaces, preserving Product without implementing plan
management. Build a dry-run parser next,
with synthetic fixtures covering slash quantities, partial sales, hierarchical
open rows, singular/plural product labels, transfers, mixed quote/account
currencies, unknown categories, and repeated/overlapping imports. Assert that no
investment-plan holding or event enters the imported portfolio or AI context.

## XTB import and reconciliation

- Parse reports into validated DTOs before committing database changes.
- Associate every import with an explicit account; do not infer account identity
  from a ticker or merge separate accounts accidentally.
- Show an import preview/summary with additions, duplicates, validation errors,
  and reconciliation differences. Commit each accepted import atomically.
- Use stable broker identifiers when available, supplemented by inspected record
  identity rules. Track source provenance and report fingerprints. Reimporting an
  identical or overlapping report must not duplicate ledger events.
- Reconcile derived holdings against Open Positions and cash against available
  report balances. Surface discrepancies; do not silently overwrite the ledger.
- Keep reports, database files, and private artifacts in ignored local storage.
  Tests use synthetic/anonymized fixtures.

Acceptance: a report imports into the selected account, a repeat import adds no
duplicates, invalid imports leave no partial records, and discrepancies are visible.

## Symbol resolution before market data

Implement the application-owned SymbolResolver boundary:

```text
XTB symbol -> canonical instrument -> provider-specific symbol
```

Preserve broker symbol, canonical identity, exchange, currency, and name separately.
Validate GE.US -> GE against the selected provider; inspect and verify mappings for
PKO.PL and ETFPZU20M40.PL rather than blindly stripping suffixes. Reject or flag
unresolved/ambiguous symbols. Keep provider-specific mappings out of the importer,
dashboard, portfolio calculations, and AI prompts.

## Database-backed dashboard and intraday valuations

First connect DashboardService to persisted holdings and cash. Show unknown values
as unavailable until pricing exists. Quote monitoring updates valuations of known
holdings; it does not discover new broker trades or cash movements. Those still
require new XTB imports until a separate account-sync feature is implemented.

Add a provider-independent MarketDataProvider protocol with async quote, history,
and fundamentals operations. Implement quote fetching first; do not build unused
history/fundamentals workflows. Define a typed quote including instrument identity,
price, quote currency, market timestamp, retrieval timestamp, source, and known
delay/session metadata. Preserve unknown metadata rather than claiming real-time data.

Choose the initial provider only after verifying symbol coverage and usable quote
access for GE, PKO BP, and ETFPZU20M40, plus relevant FX pairs. Check API quotas,
exchange delay/entitlements, and actual responses. A free API must not be assumed
to provide real-time data for every exchange. Twelve Data is a candidate, not a
selected provider; Alpha Vantage's standard free quota was too limited for frequent
polling at discussion time. Recheck provider terms when implementing this slice.

Run a background quote refresh while the dashboard is open. Start with a
configurable 1–5 minute interval, adjusted to provider limits and market sessions;
support manual refresh. Use bounded requests, rate-limit handling, and cached
last-known values. Keep network calls off the UI loop. On failure, preserve cached
prices with a stale indicator rather than displaying zero or presenting them as fresh.

The valuation service, not the dashboard, combines holdings, quotes, and FX rates
to calculate position market values, unrealized P/L, allocation, and portfolio
totals in a configurable base currency. Missing prices or FX rates must leave totals
unavailable or explicitly partial. Per-instrument today's price change can use a
verified previous close; do not call it full portfolio daily performance until
cash flows, session boundaries, and currency effects are handled correctly.

Display current price, position value, unrealized P/L, and available daily price
change. Show quote age/source and real-time, delayed, stale, or market-closed status
where supported. A recent poll does not make a delayed market timestamp real-time.

```text
Database holdings + market quotes + FX rates
                    |
            Valuation service
                    |
             DashboardService
                /       \
          Textual UI    CLI
```

TradingAgents remains the analysis engine. Its internal data fetching is not the
dashboard quote service, and refreshing prices must not trigger model calls.
No streaming infrastructure or continuous quote-history persistence is required
for this first polling implementation; portfolio snapshots remain M9.

Acceptance: synthetic quotes/FX produce correct valuations, unavailable/stale data
is clearly represented, refreshing never blocks the TUI or calls an LLM, and the
CLI can read the same valuation service. Use provider fakes for routine tests and
opt-in live checks for verified symbol coverage.

### USD display and FX conversion implementation

The portfolio table now has a separate Value USD column in the TUI and CLI.
`PortfolioRow.market_value_usd` is supplied by the application service; the UI
does not calculate FX conversions. Keep the existing value and currency alongside
it. The application service now populates it through the NBP adapter. Account
base currency remains PLN; USD is an additional reporting currency.

Start with the public [NBP API](https://api.nbp.pl/en.html), table A mid-rates,
over HTTPS using httpx. Fetch the latest USD rate from
`https://api.nbp.pl/api/exchangerates/rates/a/usd/?format=json`, or the complete
table A for several currencies. NBP provides published reference rates; this
initial USD display must be labeled as a reference conversion, not live FX or
a broker execution rate. Intraday FX may be added behind the same provider
interface later if needed.

The initial PLN-to-USD slice is implemented, including summary cards, cached rates,
provenance, and mocked HTTP tests. Cross-currency conversion and historical
reference-rate selection remain future work. The following describes the wider
FX design as currencies and historical reporting are added:

1. Define application-owned `FxRate` and `FxRateProvider` contracts. Each rate
   includes base/quote currency, positive Decimal rate, provider, publication
   date, retrieval timestamp, and source table identifier. Define rate direction
   explicitly: NBP USD `mid` is PLN per 1 USD.
2. Implement NbpFxRateProvider behind this interface. Validate responses and parse
   numeric JSON directly to Decimal; reject missing, nonpositive, or malformed
   rates. A currency's same-currency conversion is identity and needs no request.
3. Convert using a CurrencyConversionService, invoked by the valuation service:
   `value_usd = value_pln / pln_per_usd`. A synthetic rate of 4 PLN per USD converts
   400 PLN to 100 USD. For an NBP-supported currency C, cross conversion is
   `value_C * pln_per_C / pln_per_usd`; existing USD values need no conversion.
   Use one consistent rate set for a valuation refresh. Preserve instrument quote
   currency, account currency, and reporting currency separately.
4. Cache rates and bound retries/timeouts. Reuse the latest publication across
   weekends/holidays; display its actual publication date. Show reference/stale
   status explicitly on provider failures. With no usable rate, leave USD values
   unavailable; never substitute zero, 1:1 FX, or a guessed rate.
5. Keep full Decimal precision during calculations; round only displayed values
   to two decimals. Compute totals from unrounded components and leave totals
   unavailable or explicitly partial if any required conversion is missing.
6. Preserve original imported monetary values and broker conversion rates.
   Current NBP rates must not rewrite historical transaction costs or broker P/L.
   Historical reporting later uses an explicit as-of reference-rate policy,
   selecting a publication on or before the requested date, never a future rate.
7. Add synthetic conversion tests for direction, cross rates, identity, precision,
   missing/stale rates, weekends, and provider errors; mock HTTP for adapter tests.
   Connect `market_value_usd` in DashboardService and expose FX source/date/status
   in the UI and CLI before presenting converted values as usable valuations.

Acceptance: known PLN amounts produce correctly directed USD conversions, all
displayed conversions carry rate provenance, and FX requests never invoke an LLM
or block the UI loop. Current refreshes request FX only when an imported report
exists and reuse a cached rate for one hour. Cash is now available from manual current-balance confirmations; today's P/L requires market quotes and a session baseline.

## Portfolio-aware TradingAgents integration


Build portfolio context from application-owned DTOs in the TradingAgentsEngine
adapter. Convert it in memory to TradingAgents' typed portfolio object; a separate
JSON/file export pipeline is not required for analysis. An optional user export
can be added later if useful.

Preserve account selection and quote currencies. TradingAgents' single currency
field must not imply that mixed-currency position prices share one currency.
Before M7 is accepted, verify an adapter-only context mechanism for strategy,
requested horizon, valuations, weights, and other unsupported fields. Do not
silently drop them or modify upstream source to make the initial integration work.

```text
XTB reports -> validated ledger -> portfolio/valuation services
                                        |               |
                                   dashboard     TradingAgentsEngine
                                                        |
                                               Recommendation
```

`trader review` should return ADD/HOLD/REDUCE/SELL for owned positions. Human
decisions remain separate; no broker execution is included in this work.

## Initial inspection (2026-10-07; historical baseline)

- TradingAssistant has no tracked implementation or commits. Its .venv is
  CPython 3.13.14 with no installed distributions.
- uv is available; the environment metadata records uv 0.11.29.
- Sibling checkout: `/Users/ppuczka/repos/TradingAgents`, clean working tree,
  tag v0.6.0, commit `1394a3f72aa4393e1a98f51b382434c4b4c2d972`.
- TradingAgents is installed as a non-editable local-source package in global
  Python 3.11.6 under `/Library/Frameworks/Python.framework/Versions/3.11/lib/python3.11/site-packages`.
  Distribution metadata and imported `tradingagents.__version__` both report 0.6.0.
  Install provenance points to the sibling checkout. Its own .venv is also empty.
- Imported the installed graph, portfolio types, and decision schema successfully.
  No network analysis or paid model call was run. The owner's successful GE run
  remains the existing end-to-end evidence; Python 3.13 integration is still to verify.
- Did not read secrets or modify TradingAgents.

## Installed API, inspected directly

```python
TradingAgentsGraph(
    selected_analysts=("market", "social", "news", "fundamentals"),
    debug=False,
    config=None,
    callbacks=None,
)
graph.propagate(company_name, trade_date, asset_type="stock", portfolio=None)
# synchronous; returns (final_state, rating)
```

Ratings: Buy, Overweight, Hold, Underweight, Sell, or REVIEW when unparseable.
`final_state` exposes `final_rating` and markdown `final_trade_decision`.
The internal structured PortfolioDecision contains rating, executive_summary,
investment_thesis, optional price_target, and optional time_horizon, but the
manager returns rendered text rather than that typed object. It does not expose
numeric recommendation confidence, structured stop loss, or a risk category.

`tradingagents.portfolio.PortfolioContext` accepts cash, currency, and positions;
each Position accepts ticker, signed quantity, and optional average_price.
The graph injects `portfolio.render(ticker)` into state. Trader, risk debaters,
and portfolio manager consume it. No context and an explicitly empty book are
different. This API does not directly represent strategy, requested horizon,
weights, current prices, P/L, target weights, or multi-currency accounts.
M7 must explicitly bridge those gaps inside the adapter, using inspected extension
points and tests, without a fork or silently dropping strategy/horizon.

Construction creates cache/results directories and configures upstream global
state. Runs write reports and memory records, with optional SQLite checkpoints.
Override all artifact paths to ignored application-local storage. Start with
one analysis at a time; serialize construction plus execution in the adapter
rather than assuming concurrent graphs are isolated.

## Planned analysis slice structure

```text
pyproject.toml
uv.lock
.python-version
.env.example
config.example.yaml
src/trading_assistant/
    __init__.py
    config.py
    cli.py
    domain/
        __init__.py
        analysis.py
    application/
        __init__.py
        ports.py
        analysis_service.py
    adapters/
        __init__.py
        tradingagents_engine.py
tests/
    test_analysis_models.py
    test_tradingagents_engine.py
    test_cli.py
```

The existing application also has `application/dashboard.py`, `tui/dashboard.py`,
and dashboard tests. Add persistence, importer, market-data, and domain modules
only when their slices are implemented; do not create empty future modules.

## Smallest M1/M2 analysis implementation (after portfolio/valuation slices)

1. **M1:** Create a Python 3.13 uv project with src layout, Typer entry point
   `trader`, validated YAML/env settings, Pydantic AnalysisRequest and Recommendation,
   action enum, and async AnalysisEngine protocol. Request contains canonical
   symbol, analysis date, and explicit analysis intent; M2 uses opportunity intent.
   Recommendation includes symbol, action, nullable confidence/entry/stop_loss/
   target/timeframe/risk, reasoning, engine/version, timestamp, and source rating.
   Preserve raw decision separately for audit; use Decimal for monetary values.
2. **M0 completion/M2 setup:** TradingAgents is already installed and locked from
   the verified upstream revision in this project's Python 3.13 environment.
   Keep it external; do not copy its code or rely on the global Python 3.11 runtime.
   Verify adapter imports on Python 3.13 without network before a paid smoke run.
   A separate fork/checkout can be used later if changes prove necessary, with a
   uv local-path source for development and a pinned fork commit for stable use.
3. **M2:** Implement TradingAgentsEngine. Lazy-load TradingAgents inside the adapter,
   deep-copy defaults, apply validated model/provider settings and local artifact
   paths, and run synchronous construction/propagate via `asyncio.to_thread`.
   Serialize upstream execution and translate errors into application-owned exceptions.
   Supply an explicitly empty upstream portfolio for opportunity intent, rather
   than implying knowledge of the real portfolio. M2 does not support portfolio review.
4. Normalize opportunity ratings deterministically: Buy/Overweight -> BUY,
   Hold -> WATCH, Underweight/Sell -> IGNORE. Preserve the original rating and
   decision text. REVIEW/unknown/missing output -> explicit AnalysisOutputError;
   never silently map failure to HOLD or WATCH. For future owned-position review:
   Buy/Overweight -> ADD, Hold -> HOLD, Underweight -> REDUCE, Sell -> SELL.
5. Use final decision text as reasoning without another extraction LLM call.
   Initially leave optional metrics null unless a clearly labeled, validated value
   can be recovered reliably; numeric confidence remains null. Do not scrape arbitrary
   price numbers from prose or misrepresent upstream debate as deterministic risk approval.
6. Wire `trader analyze GE` through a small injected AnalysisService; display
   normalized output with Rich and support `--json`. Surface actionable failures
   with nonzero exit codes. Require canonical symbols at M2; automated broker-symbol
   resolution comes from M6, delivered earlier in the updated sequence. Connect
   analysis results to the existing dashboard through application services later.
7. Test all rating mappings, REVIEW/unknown/missing output, optional values,
   DTO validation, settings precedence, and CLI success/error paths using fakes.
   Inject the graph factory to test adapter orchestration without network or credentials.
   Separately run one opt-in live GE smoke analysis after configuration is ready.

Acceptance: `uv run trader analyze GE --json` invokes the configured engine and
returns valid application-owned Recommendation JSON. This analysis slice does
not add importing, broker execution, an opportunity scanner, or portfolio review;
portfolio-aware review is the subsequent M7 increment.

IKZE extension implemented: included IKZE rows preserve their product identity in
cash events and import scope. IKZE deposits are recognized; optional tickers in
OPEN/CLOSE BUY comments must match row tickers. Multiple detailed lots per ticker
remain preserved, summed, and reconciled. Account metadata must still agree across
all sheets. Investment Plan/Investment Plans remain excluded.

### Implemented: separate European quote refresh

- Twelve Data adapter authenticated with the ignored `TWELVEDATA_API_KEY`.
- Immediate first request, 60-second per-symbol cache (successes and failures);
  US Finnhub quotes continue on the five-second dashboard cycle.
- Central SymbolResolver candidates for Warsaw/XWAR, Xetra/XETR and London/XLON;
  validate returned ticker and MIC, with no alternative-exchange fallback.
- Native quote currency in dashboard, colored daily percentage change, AI at right.
  Preserve report totals and report FX calculations; no invented EUR/GBP conversion.
- Conservative rolling request budget and 429 cooldown, explicit unavailable/stale
  alerts. Market coverage and timeliness depend on Twelve Data entitlement.
- Verified a real PKO/XWAR PLN quote; synthetic tests cover caching, errors,
  identity/currency validation and European dashboard calculations.

### Implemented: verified Yahoo European fallback

- Tested all 13 current European holdings against Yahoo: matching listing names,
  exchange identities, native currencies, prices, previous closes and timestamps.
- YahooMarketDataProvider through explicit yfinance dependency; blocking library
  calls run off the UI event loop; cache success and failure for 60 seconds.
- EuropeanQuoteProvider preserves Twelve Data as primary and routes failures to
  explicitly verified Yahoo mappings. Finnhub US routing remains unchanged.
- Validate returned identity and currency and reject missing/invalid quote fields.
  No quote persistence, broker execution or new performance calculation.
