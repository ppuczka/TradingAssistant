# Architectural rules

## Purpose and scope

Build a local-first, single-user personal portfolio and investment assistant.
The human makes every investment decision. This is not an autonomous trading bot.
Support multiple XTB accounts incrementally. Prefer clean architecture, explicit
interfaces, meaningful tests, and small implementations over speculative abstractions.
Ask the owner before making an architectural change that contradicts these rules.

## Analysis boundary

- Wrap TradingAgents as a dependency. Do not fork or modify its source initially.
- Define an application-owned `AnalysisEngine` protocol with
  `async analyze(request: AnalysisRequest) -> Recommendation`.
- Only the `TradingAgentsEngine` adapter may import TradingAgents internals.
  Domain, application services, CLI, and TUI must not depend on them.
- Keep request and recommendation DTOs in our domain, using Pydantic.
- Owned-position actions: ADD, HOLD, REDUCE, SELL.
  Opportunity actions: BUY, WATCH, IGNORE.
- Never invent confidence, prices, or risk metadata missing from engine output.
  Invalid or ambiguous engine output must produce an explicit analysis failure.
- Existing-position analysis must account for holdings, strategy, and horizon.
  A short-term technical signal alone must not automatically sell a long-term holding.
- Start with LONG_TERM, POSITION, and TRADE; avoid a complex strategy taxonomy.
- Keep quick/deep model identifiers configurable through YAML and environment
  variables, never hardcoded into application logic.

## Domain and data boundaries

- Transactions are ultimately the source of truth; positions must be derivable.
- Keep domain logic independent of SQLAlchemy and database-specific behavior.
- Initially use SQLite, SQLAlchemy 2, and Alembic. Enable WAL and foreign keys
  on connections. Permit a future PostgreSQL implementation without rewriting domain logic.
- Preserve account identity, instrument identity, monetary currency, and precision.
- Preserve broker and canonical symbols separately. Centralize conversion in a
  `SymbolResolver`: broker symbol -> canonical instrument -> provider symbol.
  Do not blindly pass XTB symbols to providers or scatter suffix-stripping rules.
- Define a provider-independent `MarketDataProvider` with async quote, history,
  and fundamentals operations when implementing the market-data milestone.
- Store recommendation history and periodic portfolio snapshots in their milestones.
  Keep future performance/benchmark evaluation possible without implementing it early.
- CLI and Textual dashboard call application services; neither contains business logic.
- Current XTB import scope is My Trades only. Exclude Investment Plan/Investment
  Plans rows from the ledger, valuations, dashboard, and AI context. ETFs held
  in My Trades remain in scope. Keep My Trades-side transfers to/from excluded
  plans as cash movements, not investment profit. Do not import the excluded leg.

## Execution and security

- No LLM may execute broker orders, receive broker credentials, or run arbitrary shell commands.
- Future execution must follow recommendation -> deterministic Risk Engine ->
  human approval -> restricted typed BrokerAdapter -> paper trading first.
- Real-money execution is a later, explicit decision; consider double confirmation.
- Risk rules belong to this application, not to the LLM or TradingAgents risk debate.
  Future rules include concentration, per-trade risk, daily loss, cash reserves,
  disabled leverage by default, and explicit permission for averaging down.
- Secrets belong in environment variables, an ignored .env, or an external secret manager.
  Never store API keys in SQLite or log secrets.
- Never commit private XTB reports, portfolio databases, analysis artifacts with
  private portfolio context, or credentials. Use synthetic test fixtures only.

## Stack and delivery

- Python 3.12/3.13, uv, pyproject.toml; Typer CLI, Rich, Textual TUI.
- Pydantic, SQLAlchemy 2, Alembic, httpx, pytest, structlog; YAML plus env configuration.
- Use Pandas for report/data processing; NumPy only where needed.
- Docker/Compose and GitHub Actions come later.
- Implement incrementally: M0 environment/integration verification; M1 skeleton/DTOs;
  M2 AnalysisEngine/TradingAgents adapter; M3 persistence; M4 accounts/portfolio;
  M5 XTB importer; M6 SymbolResolver; M7 portfolio-aware review; M8 dashboard;
  M9 snapshots/performance; M10 risk engine; M11 paper broker.
- First runnable goal: `trader analyze GE` returns our normalized Recommendation.
  Do not implement importing, execution, or later milestones prematurely.
- Test DTO validation, rating normalization and failures, adapter boundaries,
  and CLI behavior with fakes. Real network/paid-model tests must be opt-in.
