# Trading Assistant

A local-first personal investment assistant with a read-only Textual dashboard,
SQLite portfolio ledger, XTB XLSX import, live market quotes, and portfolio-aware
TradingAgents analysis through the CLI.

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
see all panels on smaller terminals. Select a US holding and press **A** to open
the analysis dialog. Scan, History, Orders and Config shortcuts currently
show an explicit availability notice.

The UI reads an injected `DashboardService` snapshot. It does not calculate
portfolio performance, call models, or execute orders.

## Import XTB reports

```sh
uv run trader import-xtb broker_reports/report.xlsx --account YOUR_ACCOUNT_ID --dry-run
uv run trader import-xtb broker_reports/report.xlsx --account YOUR_ACCOUNT_ID
uv run trader portfolio
uv run trader
```

When account-number cells are blank, `--account` is required; a conflicting account
selection is rejected. Only PLN-denominated reports are currently supported.
Explicit account/basemetadata and included-product valuation summaries must agree on PLN.
Missing or blank currency defaults to PLN by owner policy; conflicting and non-PLN evidence
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

The dashboard shows report-dated PLN holdings and unrealized P/L, plus a manually
confirmed **total account value including holdings** and a derived **cash reserve**.
Imported net cash movements remain separate from these dated observations.

### Confirm total account value

Provide the total value of the included account portfolio in PLN, including holdings
and uninvested reserve, excluding Investment Plans. Save it with an import or later:

```sh
uv run trader import-xtb broker_reports/report.xlsx --account YOUR_ACCOUNT_ID --account-value 20729.28
uv run trader set-account-value 20729.28 --account YOUR_ACCOUNT_ID
```

**Reserve = account total − report holdings.** For 20,729.28 PLN total and
19,086.71 PLN holdings, reserve is 1,642.57 PLN. Reserve percentage divides this
reserve by the account total; holdings are never added to that total a second time.

Timestamp defaults to now. Use `--value-as-of` on import or `--as-of` on
`set-account-value` for a dated observation. Amounts must be finite/nonnegative and
timestamps timezone-aware and not future-dated. `--dry-run` only previews.
Legacy `--cash-balance`, `--cash-as-of` and `set-cash` remain compatible aliases
with the corrected total-value meaning. Existing stored amounts are preserved and
interpreted as totals by explicit owner policy. Legacy database table names remain
for compatibility; no database rewrite or monetary conversion is required.

Totals are dated observations, not transaction events. Imports do not update them.
`add-money` increases the confirmed total by the deposit amount. With multiple
accounts, combined total/reserve requires a confirmation for every account.
Reserve is unknown when holding values are incomplete or any account total is
below its reported holdings; check balance and snapshot dates. Different report
and balance dates mean this reserve is a report-based estimate, not live cash.
New AI requests receive the total separately from the derived spendable reserve.
Previously saved AI contexts are preserved; rerun analyses to use corrected context.

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
GBP pence quotes are divided by 100 before conversion. Only holdings with a quote
dated today (UTC) and a usable currency rate contribute. Incomplete coverage shows
a labelled partial estimate with the included/total holding count; zero coverage
remains unavailable. Older quotes are excluded. This estimate excludes intraday trades,
realized gains, fees and daily FX movement. Cached FX publication dates and stale
status remain explicit in alerts; it is not audited daily account performance.

Record money already deposited into an imported account:

```sh
trader add-money 500 --account YOUR_ACCOUNT_ID
```

The amount must be positive and is denominated in PLN. This records a local
 deposit and increases the latest total account value; it does not transfer money at
 the broker. Set an unknown balance first with `trader set-cash`. Each invocation
 records a new deposit, so run it once per deposit. A later confirmed balance
 replaces the calculated balance. Imported cash operations remain separate and
 are not added to confirmed balances, preventing report reimports from adding
 the same deposit again. The dashboard reads the updated balance on its next refresh.

Assign a unique alias to an imported account:

```sh
trader set-account-alias ikze --account YOUR_ACCOUNT_ID
trader add-money 500 --account ikze
trader set-cash 1500 --account ikze
trader import-xtb broker_reports/report.xlsx --account ikze
```

Aliases are case insensitive and stored in lowercase. They start with a letter,
contain only letters, digits, underscores or hyphens, and have at most 64 characters.
An alias cannot identify another account or collide with another account's broker ID.
To rename an alias, run `set-account-alias` again using the ID or current alias.
The original broker account ID continues to work. Report imports retain their
broker account identity and validate report metadata against the resolved ID.

The dashboard's Day Change History panel shows the original one-minute candle bars
of estimated PLN daily P/L. Bodies show the first/last observed estimates in each
minute; wicks show highs/lows. Rising candles are green and falling candles are red.
It labels partial coverage and marks holdings, coverage, reference FX, or date changes.
The chart shows the most recent candles that fit its width. No earlier
history or missing samples are invented. Minute candles persist in SQLite for at
least 24 hours and load immediately after restart, before live quotes arrive.
Coverage changes start separate chart segments without deleting retained observations;
charts mark coverage changes/time gaps with `┆`.

Press **D** to switch to the Portfolio Daily Change dashboard, showing the daily
percentage and PLN estimates with separate candle charts. **D** or **Esc** returns
to the main dashboard; F5 and automatic refresh work in both views. Daily percent
is the estimated PLN change divided by the previous-close PLN value of the same
covered holdings. It excludes cash and labels partial coverage. Both charts use
observed portfolio estimates, not historical market candles. The daily dashboard
also lists the latest observed estimate for each of the last 90 UTC calendar days,
with observation time and coverage. These are not guaranteed end-of-day returns:
the app must be running to collect data, and partial coverage stays explicitly labelled.

Portfolio News appears between Opportunities and Alerts. It combines Yahoo Finance
feeds for mapped portfolio tickers (US and verified European listings), displays
publication time, publisher and associated holdings, and links to the original
article. Headlines are filtered to today/yesterday in Europe/Warsaw and duplicate
URLs are merged. Fetching runs in the background, at most two requests at once,
with a five-minute refresh; news does not block quote updates. Yahoo returns up to
100 recent items per ticker and may have incomplete or no coverage, particularly
for ETFs and European listings. This is available ticker-feed coverage, not an
exhaustive search or independently verified article relevance. No article bodies
or AI summaries are downloaded or stored.
When Yahoo's ticker feed is empty, its search feed is used instead; search items
must explicitly tag the exact Yahoo ticker to be included.

GDELT supplements Yahoo for the initial European/Polish pilot holdings: PKO.PL
(PKO BP / PKO Bank Polski), XTB.PL, and RHM.DE (Rheinmetall). Searches run only for
holdings present in the portfolio, refresh every 30 minutes in the background,
and require the company name in the headline. Requests are sequential with at
least ten seconds between starts; a rate-limit response triggers a 30-minute
cooldown. Available results are merged with Yahoo and deduplicated by URL.

GDELT provides an indexing timestamp (`seendate`), not verified publication time.
Its entries are therefore labelled **Indexed**, and the today/yesterday window
applies to indexing time in Europe/Warsaw. Older articles newly indexed can appear.
The pilot is limited to 250 results per company search and does not guarantee
exhaustive coverage. No API key is required; outages or rate limits are displayed
as unavailable feeds and do not block quotes or Yahoo news.

If GDELT fails or returns no matching headlines, the EU/PL pilot uses Google News's
public Polish RSS search feed for PKO BP, XTB, and Rheinmetall. Items must mention
the company in the headline and have valid publication metadata. Google results
are labelled **Google News RSS / Published** and link through Google News to the
publisher. This needs no API key and shares the 30-minute background news refresh.
Google RSS is an unofficial interface with incomplete coverage. The panel shows
recent-headline counts separately for Yahoo and the EU/PL source.

Company-name search feeds (Google RSS and GDELT) also require financial/business
headline terms, such as earnings, shares, dividends, management, contracts or
litigation. Sports, competitions, training/webinars and promotional headlines are
excluded. Publisher names are not evidence of headline relevance. This conservative
Polish/English keyword filter can miss useful news; it does not use an LLM or claim
perfect relevance. Yahoo's ticker-tagged feed remains separate.


## TradingAgents analysis

The installed external TradingAgents 0.6.0 dependency is pinned to commit
`1394a3f72aa4393e1a98f51b382434c4b4c2d972`; its source is not modified. Its inspected
API is `TradingAgentsGraph.propagate(ticker, date, portfolio=...)`, returning a final
state and five-tier rating. Its portfolio input calls `render(ticker)` and
`fingerprint()`. Our adapter supplies those methods with exact Decimal holdings,
confirmed PLN cash (or unknown), report dates, strategy and investment horizon.

Model identifiers and limits are in `analysis.yaml`, with environment overrides:

```dotenv
OPENAI_API_KEY=your-key
TRADINGAGENTS_LLM_PROVIDER=openai
TRADINGAGENTS_DEEP_THINK_LLM=gpt-6-sol
TRADINGAGENTS_QUICK_THINK_LLM=gpt-6-luna
TRADINGAGENTS_MAX_DEBATE_ROUNDS=1
TRADINGAGENTS_MAX_RISK_ROUNDS=1
TRADINGAGENTS_MAX_TOOL_ROUNDS=20
TRADINGAGENTS_OPENAI_REASONING_EFFORT=medium
TRADINGAGENTS_MAX_TOKENS=8192
```

Keep the key in the ignored `.env` or your shell environment. `TRADER_QUICK_MODEL`
and `TRADER_DEEP_MODEL` take precedence over the matching TradingAgents model env
variables. `TRADER_TIME_HORIZON` overrides the YAML horizon.

```sh
uv run trader analyze GE --dry-run
uv run trader analyze GE
uv run trader analyze GE.US --strategy LONG_TERM --horizon "3-5 years"
```

`--dry-run` prints our request and effective settings without invoking the engine
or requiring an API key. It reads/initializes the local portfolio database. The
integration supports US symbols and verified German/UK mappings; verified Polish holdings are also supported.
The request uses the combined imported portfolio. Default strategy is LONG_TERM;
default horizon is 3–5 years and is printed before analysis. Override both to match
your actual intent. A normal invocation makes billed OpenAI API calls; the token
cap applies per model response, not to the full run. It can take several minutes.

| Engine rating | Owned position | Unowned opportunity |
|---|---|---|
| Buy / Overweight | ADD | BUY |
| Hold | HOLD | WATCH |
| Underweight | REDUCE | IGNORE |
| Sell | SELL | IGNORE |

Unparseable/REVIEW or inconsistent ratings fail explicitly. The final decision
text is preserved as reasoning; missing structured confidence, risk, prices and
timeframe remain null. Report valuations are explicitly dated PLN observations,
not live portfolio weights or purchase cost. TradingAgents fetches its own analysis
data; the dashboard quote/news feeds are not yet injected into its agents.

Normalized recommendation, input context and non-secret settings are saved under
`data/analysis/recommendations/` (beside a custom database under `analysis/`). Engine
logs/cache stay beneath the same ignored analysis directory. Each run uses separate
memory to avoid silently reusing another portfolio's lessons. SQLite recommendation
history is follow-up work. The dashboard AI column reads the latest valid saved
owned-position recommendation for each supported symbol, including in the initial
offline snapshot. Analysis time, strategy and horizon appear in Alerts; quantity
changes since analysis are flagged. Dashboard refresh never invokes the engine. The TUI **A** shortcut opens an analysis dialog for the selected holding in the
main dashboard or **P** Portfolio view. It shows configured models and lets you
set strategy/horizon. **Run analysis** starts the billed call; opening the dialog
or refreshing the dashboard does not. Results/reasoning stay visible in the dialog
and are saved through the same application service as the CLI. Close the completed
dialog to refresh the AI column. US and verified German/UK/Polish holdings are supported. Analysis setup
loads `analysis.yaml` (override with `TRADER_ANALYSIS_CONFIG`) and requires an
OpenAI key. No orders are created.

Tests use fake graphs/engines and do not call paid models or network providers.

## Full analysis view

Select a holding and press **V** in the main dashboard or Portfolio view to open
its saved full analysis without model calls or an API key. The **Full analysis**
button in the A dialog opens the same view before/after a run when a saved result
is available. Tabs contain the final decision, market, fundamentals, news,
sentiment, investment debate, research/trading plans and risk discussion. Each
section scrolls independently; Esc/Back returns to the prior view. Missing reports
are marked unavailable, never generated on demand.

New normalized records include these reports. Legacy records can display reports
from the ignored TradingAgents log only when symbol, analysis date, engine rating
and exact final reasoning uniquely match; ambiguous matches remain unavailable.
The adapter alone understands TradingAgents report/state keys. Opening or refreshing
reports never calls OpenAI. CLI equivalents:

```sh
trader analysis-report GE
trader analysis-report GE --section market
trader analysis-report GE --section investment_debate
```

`trader analyze` keeps its printed recommendation compact; full reports are saved
alongside it and read through `analysis-report` or the separate TUI view.


## US portfolio review

```sh
trader review --market US --dry-run
trader review --market US
trader review --strategy POSITION --horizon "3-6 months"
```

The preview lists supported US holdings, skipped instruments and effective model
settings without paid calls. A normal run analyzes each supported holding sequentially
with the same imported portfolio snapshot, strategy and horizon. Each successful
recommendation and its full reports are saved using the single-holding analysis
service. Errors remain explicit per symbol; other holdings continue. The CLI exits
with status 1 when any symbol fails, while successful records remain available.
Holdings outside the selected market and unmapped symbols are listed as skipped; no candidate discovery, position
sizing, orders or automated portfolio rebalancing is performed.

Press **R** to open the US Portfolio Review dialog. Review strategy/horizon and the
holding count, then choose **Start review** to start billed OpenAI calls. The table
shows QUEUED/RUNNING/COMPLETED/FAILED states and normalized actions. **Full analysis**
opens a completed row's reports. **Stop after current** lets the current analysis
finish and save, then marks remaining holdings STOPPED without starting more calls.
Close the finished review to refresh AI columns. Opening the dialog never invokes
the engine, and dashboard refresh never starts a review automatically.


## EU review excluding Poland

```sh
trader review --market EU --dry-run
trader review --market EU
trader analyze RHM.DE
trader analyze COPA.UK
```

In the **R** dialog, choose **EU • Germany / UK • excludes Poland** before starting.
EU currently means the verified German and UK listings in SymbolResolver, not all
European exchanges. It includes stocks, ETFs and ETC commodity products. Poland
is excluded from analysis targets; Polish and US holdings remain in the full
portfolio context. Broker symbols are resolved to provider listings centrally:
for example COPA.UK -> COPA.L, while RHM.DE stays RHM.DE. Unknown mappings fail
explicitly rather than choosing another listing. Saved EU actions and reports are
read by the AI column and V view, just like US results.

Stocks use the existing analyst pipeline. ETFs/ETCs use market and news analysts
with explicit instrument-type context and instructions to consider underlying
exposure, tracking, liquidity, fees and issuer risk without inventing company
earnings. Fund fundamentals/sentiment report tabs may therefore be unavailable.
This is not yet a dedicated fund-data provider: fees/holdings not supplied by
analysis tools remain unknown. Market-data availability can vary by listing;
per-symbol errors remain visible. Paid EU analyses are user-triggered.


## Polish review

```sh
trader review --market PL --dry-run
trader review --market PL
trader analyze PKO.PL
trader analyze XTB.PL
```

Choose **PL • Poland** in the R dialog. Initial verified mappings are PKO.PL ->
PKO.WA, XTB.PL -> XTB.WA, and ETFPZUW20M40.PL -> ETFPZUW20M40.WA. Stocks use the
standard analyst pipeline; the ETF uses market/news analysts with fund context.
Unknown Polish listings are rejected rather than blindly replacing suffixes.
Polish targets remain separate from the EU (Germany/UK) review. All markets stay
in the portfolio context, while only selected-market holdings are analyzed.
Provider coverage may be incomplete; failures remain explicit per symbol. Saved
recommendations appear in the AI column and full reports can be opened with V.

## Yahoo candidate discovery (no AI calls)

```sh
trader candidates
trader candidates --market US --limit 10
trader candidates --market PL --limit 20
trader candidates --market US,PL --limit 20 --json
```

Fetches up to the requested number per market from Yahoo's equity screener. Initial
filters select NYSE/Nasdaq/NYSE American or Warsaw listings, ordered by market cap:
US market cap at least USD 1 billion and 3-month average daily volume at least
100,000 shares; PL market cap at least PLN 100 million and average volume at least
10,000 shares. Returned equity identity, exchange, currency, finite numeric values
and quote timestamps are validated. Invalid/unsupported rows are rejected.

The application excludes mapped current holdings and duplicate provider symbols,
then requests additional pages when needed, with a maximum of three 100-row pages
per market. Unmapped holdings, insufficient results and failed markets are explicit.
A failed market does not discard successful results from the other market; CLI exit
status is 1 for partial failures. In-process pages are cached for 15 minutes.

Results include name, native-currency price and market cap, average daily volume,
quote timestamp and retrieval time. They are saved in ignored
`data/candidates/latest.json` (beside a custom database under `candidates/`). These
are provider listings, not verified XTB instruments or AI recommendations. Warsaw
listings can include foreign issuers; US listings can include foreign-company ADRs.
Different share classes/dual listings are not yet deduplicated at issuer level.
Yahoo screening is unofficial and coverage may be incomplete. Press **S** in the dashboard to open candidate discovery. Select US, PL, or both,
set the maximum results per market, and press **Fetch candidates**. Opening the
view makes no requests; fetching uses the same service and saved snapshot as the
CLI. Partial failures and warnings appear below the results.

After fetching, focus the candidate table and press **Space** or **Enter** to
mark/unmark rows (maximum 10). Press **Analyze selected**, choose a strategy and
horizon, then **Start AI review**. The dialog lists your selected symbols and
analyzes all eligible selections. Opening the dialog makes no model calls.
**Start AI review** makes paid OpenAI
calls through TradingAgents, sequentially, with one portfolio snapshot for the
batch. **Stop after current** finishes and saves the active analysis, then skips
remaining candidates. Completed rows provide a **Full report** view.

Dashboard reviews use only your marked candidates. Selection is preserved when
you return from the review dialog and cleared when candidates are fetched again.
The CLI uses the first eligible N candidates in Yahoo market-cap order, with US
before PL in a combined fetch; the AI does not compare the entire fetched universe first.
Results group **BUY**, **WATCH**, and **IGNORE**, with reasoning and no invented
confidence. This is an initial portfolio-aware assessment, not an objective score
or broker availability check. Stale candidate snapshots (over 24 hours), invalid
listing identities, and currently owned positions are skipped. Records include
candidate metadata, strategy, horizon, portfolio context, and full reports in the
ignored analysis directory.

The CLI provides the same bounded analysis:

```bash
trader opportunities --market US --limit 3 --dry-run
trader opportunities --market PL --limit 3 --strategy LONG_TERM --horizon "3-5 years"
```

`--dry-run` fetches Yahoo candidates and previews requests without model calls.
Without it, the command starts paid analyses. Successful results remain saved if
another candidate fails.

AI portfolio context includes each holding's reported unrealized P/L amount in PLN
and reported P/L percentage when available, for both holding and opportunity
analysis. These retain Decimal precision and are labeled as report snapshots,
with the portfolio's report dates; they are not live P/L. Missing values remain
unknown. Re-run analysis to include them in previously saved recommendations.

Analysis dialogs show an animated activity bar, elapsed time, observed workflow
stage and completed model-call count. Single analyses have no guessed percentage
or ETA; TradingAgents tool/debate rounds vary. Portfolio and opportunity reviews
also show a batch bar for finished attempts (including failures); candidates skipped
by Stop after current remain unfinished. Telemetry is optional for alternate engines
and never includes model messages, tool arguments or credentials.

Saved opportunity reviews are available with **W** from the dashboard or
**S → Saved reviews**. The default view shows the latest result per symbol, grouped
BUY/WATCH/IGNORE. Select **All analyses** to browse earlier runs, then select a row
and press **Full report** or Enter. Dates, strategy and horizon identify each run.
The dashboard Opportunities panel also loads latest saved recommendations for
currently unowned instruments. These are historical opinions, not live signals.
Opening saved reviews uses local files only and requires no API key.

```bash
trader opportunity-reviews
trader opportunity-reviews --history
trader opportunity-reviews --symbol GE
trader opportunity-reviews --symbol GE --history
```

`--symbol` accepts the exact canonical analysis symbol (e.g. `PKO.WA`) and prints
full reports. Existing records remain preserved when analysis is rerun.

Opportunity tables show TradingAgents' original **engine rating**: Buy, Overweight,
Hold, Underweight or Sell. Latest saved reviews and active review results sort by
action and this rating (Buy before Overweight within BUY). The dashboard and CLI
also display it. This is recommendation strength, not confidence or probability
of profit. Existing saved runs already contain the rating; no rerun is required.
All-history mode keeps newest-first ordering. Numeric confidence remains unknown
unless an engine explicitly provides it.

Analysis and review views include an expandable **Rating legend** with short
definitions of Buy, Overweight, Hold, Underweight and Sell, plus their mappings
to owned-position and opportunity actions. Ratings are recommendation strength,
not confidence estimates.

The dashboard Opportunities table includes **Suggested entry** with the analysis
price and currency when explicitly provided. New analyses request an optional
entry level; only a single explicit positive price is accepted. Currency comes
from the report or verified candidate metadata.
Ranges, ambiguous values and missing levels display a dash. Candidate-currency
conflicts are rejected for that optional field. Older records without a structured
entry are enriched on read from labeled Entry Price / Stop Loss fields in the
saved trading plan, without rewriting history. Missing or ambiguous levels remain blank.
Entry is an AI suggestion, not a live quote or broker order.

Press **Ctrl+P** from any view to return directly to Portfolio, including nested
report/review dialogs and focused text fields. **Escape** still goes back one view.
While fetching or analyzing, Ctrl+P leaves the running work intact and asks you
to wait; batch reviews can use Stop after current first.

Allocation uses complete broker-report holding values in PLN plus the derived reserve.
Stock groups follow listing markets (US, Poland, other Europe); ETF/ETC holdings
form a separate group, without counting them again under a market. Unknown reserve
is excluded and labeled holdings-only; incomplete holding values suppress allocation
rather than showing misleading partial weights. This is report allocation, not live
or underlying geographic exposure. AI Status loads saved holding/opportunity counts
and the latest saved analysis time; portfolio-wide sentiment and order-approval
risk rules remain explicitly unimplemented, and scans are manual.

Opportunities also shows **Stop loss**, parsed from explicit trading-plan labels.
Markdown labels such as `**Entry Price:** 44.0` and `**Stop Loss:** 42.61` are
supported for new and existing saved analyses. Prices without a currency use the
verified candidate listing currency when available; otherwise the display says
"currency unspecified". Ranges, duplicate labels, nonpositive values, and conflicts
with candidate currency are left unknown. Stop loss is an AI suggestion, not an order.

Report-based concentration and cash assessment now appears in AI Status, with
threshold breaches in Alerts. It shows the largest position, top-five share, cash
reserve percentage and the valuation basis. Confirmed total account value is the denominator; reserve is total minus holdings. Unknown total/reserve is not treated as zero: concentration
uses holdings-only weights and the assessment is explicitly incomplete. Missing
holding valuations suppress the assessment. Multiple accounts' same broker symbol
are combined before checking position concentration. Allocation remains available
by listing market and instrument type in the Allocation panel.

Configure informational thresholds in `risk.yaml` (defaults: position 20%, top five
60%, minimum cash 10%). Environment overrides:
`TRADER_RISK_MAX_POSITION_PERCENT`, `TRADER_RISK_MAX_TOP_FIVE_PERCENT`,
`TRADER_RISK_MIN_CASH_PERCENT`; `TRADER_RISK_CONFIG` selects a YAML path.
Threshold equality is allowed; warnings are generated strictly above maxima or
below minimum cash. Invalid configuration fails explicitly. These checks do not
measure volatility, sector overlap or overall investment risk and do not authorize
or block orders. Report and cash snapshot dates remain visible in portfolio data.

```bash
trader risk
trader risk --json
trader risk --config risk.yaml
```

Risk assessment is local and makes no AI or quote-provider calls. Market sentiment
is a separately generated AI assessment, not an input to trade authorization.

Press **M** for **Market sentiment & Portfolio risk**. Opening the view reads saved
sentiment and report-based risk locally. **Refresh evidence / risk** fetches current
portfolio quotes and recent ticker news; **Generate sentiment • paid** makes one
OpenAI Responses structured-output call using the configured `quick_model`,
reasoning effort and token limit from analysis settings. No periodic paid refresh.

US and PL receive separate BULLISH / NEUTRAL / BEARISH / MIXED / UNKNOWN summaries
with reasoning, limitations, source URLs, quote timestamps and coverage counts.
This first slice assesses only portfolio-linked listings, not whole-market indices,
market breadth, macro conditions or sector exposure. Quotes must be within 24 hours
and have a valid daily change; news must be within 48 hours. Missing market evidence
requires UNKNOWN, and an entirely empty request makes no model call. A captured
request expires after five minutes. The model sees quotes/headlines, not balances,
account IDs or broker credentials; no tools or orders are available. Risk remains
deterministic concentration/reserve checks, independent of the AI opinion.

Dated evidence and results are stored in ignored `data/sentiment/` beside the database.
Dashboard refresh loads the latest saved sentiment without model calls and labels
summaries older than 24 hours stale. Invalid/refused/incomplete model output fails
explicitly; unsupported citations and fabricated assessments of empty markets are
rejected. Existing summaries remain preserved on failure.

```bash
trader sentiment             # Saved summary only, no key/network/model calls
trader sentiment --dry-run   # Fresh evidence preview, no AI call
trader sentiment --generate  # Fresh evidence + one paid summary
trader risk                  # Deterministic report-based risk, no AI
```

TradingAgents remains the stock-analysis engine. The new application-owned
SentimentEngine boundary is for aggregate evidence summaries and imports no
TradingAgents internals. Broad-market benchmark coverage can follow separately.

Outside regular trading hours, day change uses the latest completed exchange
session's near-close provider quote against its previous close, including before
opening, weekends and holidays. Exchange calendars cover NYSE (US listings), Warsaw,
Xetra and London, respecting DST and scheduled early closes. While a session is
open, old-session quotes remain blank until a current-session quote arrives.
Closing snapshots must match the expected session and fall within 15 minutes of
its scheduled close (including short auction/reporting delays); older intraday
quotes or later extended-hours prices are not substituted. This remains provider
quote data, not a verified official auction close. Missing close data stays unknown.

The summary is labeled **Session P/L**, with session dates in its explanation.
Different exchanges can refer to different sessions. Previous-session values are
not recorded as new-calendar-day performance observations. No new paid AI calls
or extra market-data quote requests are introduced.

Holdings tables and `trader portfolio` now show **Est. P/L PLN** and **Est. P/L %**
separately from unchanged **Report P/L**. Estimated amount = reported P/L + current
quote valuation in PLN − report valuation in PLN. This is total reported-position
P/L updated for valuation movement, not merely change since the report. Percentage
uses the report's implied cost (report value − reported P/L). When amount is missing,
reported percentage can imply cost as report value / (1 + reported percentage / 100);
missing or unusable cost information stays unknown. GBP pence quotes are normalized
and foreign values converted with NBP reference rates. Estimates refresh with quotes,
including closing-session quotes outside trading hours.

These are estimates assuming imported quantities and report costs remain applicable;
new trades, fees, corporate actions or different broker FX can cause differences.
They are not a replacement for a fresh broker report or a transaction-level cost-basis
ledger. Quote dates/staleness and reference FX remain visible. Report values and the
report-based summary/risk assessment are preserved. Missing quotes or FX show a dash.

Press **Ctrl+L** in the dashboard or Portfolio view to choose visible portfolio
columns. Use arrows and **Space** to toggle, then **Apply**; **Show all** restores
every column, and **Escape** cancels. Symbol is always visible so selecting holdings
for analysis keeps working. Both portfolio tables share the selection, including
after quote refreshes. Preferences persist beside the database in ignored
`data/ui-preferences.json` by default; malformed preferences fall back to all columns.

The **Est. unrealized P/L** summary box now sums **Est. P/L PLN** across holdings
and converts the estimated total to USD using the reference FX rate. It refreshes
with quotes. Missing estimates produce a clearly labeled partial total and coverage
count; zero coverage shows a dash, never the historical report total. Report P/L
remains available in the table and CLI, separately labeled.

Intraday charts record observed session estimates even when some markets are closed
(e.g. Europe trading while US values refer to yesterday's close). Session-date changes
start new segments, preserving the break instead of connecting incompatible baselines.
These observations remain in the 24-hour history across restarts. Mixed/prior-session
observations do not create or overwrite today's 90-day daily-performance summary.
No missing observations are backfilled or fabricated; a closed market can produce
a flat chart until new quotes arrive.

Analyze a typed symbol with **T** in the dashboard, or **W → Analyze symbol**.
Use US tickers or XTB/Yahoo listing symbols such as `GE.US`, `PCO.PL`,
`PCO.WA`, `RHM.DE`, or a supported London `.L` listing. Yahoo verifies the
listing, currency, and stock/ETF type; this does not verify broker availability.
Choose strategy and horizon, then press **Run analysis** to start paid analysis.
If you already own the instrument, current holdings are included automatically.

In **W → Saved opportunity reviews**, select any saved analysis and choose
**Retry analysis**. Strategy and horizon are prefilled and editable. The retry
uses today's date and current portfolio context, and saves a new result while
preserving the previous report. Symbol lookup failures leave saved results intact.
CLI equivalent: `trader analyze PCO.WA --lookup --strategy POSITION --horizon "6 months"`.
`--lookup --dry-run` performs Yahoo metadata lookup without paid AI analysis.
