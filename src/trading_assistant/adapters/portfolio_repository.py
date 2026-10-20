"""Atomic imports and portfolio read model over an event ledger."""

from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from trading_assistant.adapters.database import (
    Account,
    CashBalance,
    CashDeposit,
    CashOperation,
    ImportBatch,
    Instrument,
    ReportHolding,
    Transaction,
)
from trading_assistant.domain.portfolio import (
    AccountAliasInput,
    AccountValueInput,
    BrokerReport,
    CashDepositInput,
    Holding,
    ImportResult,
    ImportValidationError,
    PortfolioState,
)

TOLERANCE = Decimal("0.00000001")


def stamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds")


def ledger_quantities(session, account_id: int, as_of: str):
    quantities = defaultdict(Decimal)
    lots = defaultdict(Decimal)
    rows = session.execute(
        select(Transaction, CashOperation)
        .join(CashOperation, Transaction.cash_operation_id == CashOperation.id)
        .where(Transaction.account_id == account_id, CashOperation.occurred_at <= as_of)
    )
    for trade, cash in rows:
        signed = trade.quantity if trade.action == "BUY" else -trade.quantity
        quantities[trade.instrument_id] += signed
        lots[cash.position_id] += signed
    return quantities, lots


def quantities_match(derived, observed) -> bool:
    return all(
        abs(derived.get(key, Decimal(0)) - observed.get(key, Decimal(0))) <= TOLERANCE
        for key in set(derived) | set(observed)
    )


class SqlAlchemyPortfolioRepository:
    def __init__(self, engine):
        self.engine = engine

    @staticmethod
    def _account(session, reference: str):
        matches = session.scalars(
            select(Account).where(
                Account.broker == "XTB",
                or_(
                    Account.broker_account_id == reference,
                    Account.alias == reference.strip().lower(),
                ),
            )
        ).all()
        if not matches:
            raise ImportValidationError("Unknown account; import its report first.")
        if len(matches) != 1:
            raise ImportValidationError(
                "Unknown or ambiguous account; use an imported account ID or alias."
            )
        return matches[0]

    def resolve_account(self, reference: str) -> str:
        with Session(self.engine) as session:
            return self._account(session, reference).broker_account_id

    def set_account_alias(self, reference: str, alias: AccountAliasInput) -> None:
        try:
            with Session(self.engine) as session, session.begin():
                account = self._account(session, reference)
                conflict = session.scalar(
                    select(Account.id).where(
                        or_(
                            Account.alias == alias.alias,
                            func.lower(Account.broker_account_id) == alias.alias,
                        ),
                        Account.id != account.id,
                    )
                )
                if conflict is not None:
                    raise ImportValidationError(
                        "Alias is already used by another account or account ID."
                    )
                account.alias = alias.alias
        except IntegrityError as exc:
            raise ImportValidationError("Alias is already used by another account.") from exc

    def set_account_value(self, account_number: str, balance: AccountValueInput) -> None:
        with Session(self.engine) as session, session.begin():
            account = self._account(session, account_number)
            self._save_account_value(session, account, balance)

    def add_money(self, account_number: str, deposit: CashDepositInput) -> AccountValueInput:
        with Session(self.engine) as session, session.begin():
            account = self._account(session, account_number)
            previous = session.scalar(
                select(CashBalance)
                .where(CashBalance.account_id == account.id)
                .order_by(CashBalance.as_of.desc(), CashBalance.id.desc())
                .limit(1)
            )
            if previous is None:
                raise ImportValidationError(
                    "Total account value is unknown; use set-account-value first."
                )
            now = datetime.now(UTC)
            balance = AccountValueInput(amount=previous.amount + deposit.amount, as_of=now)
            self._save_account_value(session, account, balance)
            session.add(
                CashDeposit(
                    account_id=account.id,
                    amount=deposit.amount,
                    currency=account.currency,
                    occurred_at=stamp(now),
                )
            )
            return balance

    @staticmethod
    def _save_account_value(session, account, balance: AccountValueInput) -> None:
        if account.currency != "PLN":
            raise ImportValidationError("Only PLN cash balances are supported.")
        session.add(
            CashBalance(
                account_id=account.id,
                amount=balance.amount,
                currency=account.currency,
                as_of=stamp(balance.as_of),
                recorded_at=stamp(datetime.now(UTC)),
            )
        )

    def import_report(
        self, report: BrokerReport, account_value: AccountValueInput | None = None
    ) -> ImportResult:
        with Session(self.engine) as session, session.begin():
            account = session.scalar(
                select(Account).where(
                    Account.broker == "XTB",
                    Account.broker_account_id == report.account_number,
                )
            )
            if account is None:
                if (
                    session.scalar(
                        select(Account.id).where(Account.alias == report.account_number.lower())
                    )
                    is not None
                ):
                    raise ImportValidationError(
                        "Report account ID conflicts with an existing alias."
                    )
                account = Account(
                    broker="XTB", broker_account_id=report.account_number, currency=report.currency
                )
                session.add(account)
                session.flush()
            if account.currency != report.currency:
                raise ImportValidationError("Account currency conflicts with existing records.")
            if account_value is not None:
                self._save_account_value(session, account, account_value)
            previous = session.scalar(
                select(ImportBatch).where(
                    ImportBatch.account_id == account.id,
                    ImportBatch.fingerprint == report.fingerprint,
                )
            )
            if previous:
                return ImportResult(
                    already_imported=True,
                    cash_duplicates=len(report.cash_events),
                    positions=len(report.positions),
                    excluded_rows=report.excluded_rows,
                    warnings=previous.warnings,
                )
            batch = ImportBatch(
                account_id=account.id,
                fingerprint=report.fingerprint,
                as_of=stamp(report.as_of),
                scope=report.scope,
                excluded_rows=report.excluded_rows,
                warnings=list(report.warnings),
                closed_records=report.closed_records,
            )
            session.add(batch)
            session.flush()
            instrument_ids = {}
            for item in report.instruments:
                instrument = session.scalar(
                    select(Instrument).where(
                        Instrument.broker == "XTB",
                        Instrument.broker_symbol == item.broker_symbol,
                    )
                )
                if instrument is None:
                    instrument = Instrument(
                        broker="XTB",
                        broker_symbol=item.broker_symbol,
                        name=item.name,
                        asset_type=item.asset_type,
                    )
                    session.add(instrument)
                    session.flush()
                elif instrument.asset_type != item.asset_type:
                    raise ImportValidationError(
                        "Instrument category conflicts with stored identity."
                    )
                instrument_ids[item.broker_symbol] = instrument.id
            result = ImportResult(
                positions=len(report.positions), excluded_rows=report.excluded_rows
            )
            for source in report.cash_events:
                payload = source.model_dump(mode="json", exclude={"source_row"})
                # Preserve payload compatibility with previously imported My Trades events.
                if source.product == "MY_TRADES":
                    payload.pop("product")
                for key in ("amount", "quantity", "execution_price"):
                    if payload.get(key) is not None:
                        payload[key] = format(Decimal(payload[key]).normalize(), "f")
                existing = session.scalar(
                    select(CashOperation).where(
                        CashOperation.account_id == account.id,
                        CashOperation.broker_operation_id == source.operation_id,
                    )
                )
                if existing:
                    if existing.payload["event"] != payload:
                        raise ImportValidationError(
                            "A broker operation ID conflicts with a previously imported event."
                        )
                    result.cash_duplicates += 1
                    continue
                cash = CashOperation(
                    account_id=account.id,
                    import_id=batch.id,
                    instrument_id=instrument_ids.get(source.symbol),
                    broker_operation_id=source.operation_id,
                    occurred_at=stamp(source.occurred_at),
                    kind=source.kind,
                    amount=source.amount,
                    currency=report.currency,
                    product=source.product,
                    position_id=source.position_id,
                    payload={"event": payload, "source_row": source.source_row},
                )
                session.add(cash)
                session.flush()
                result.cash_added += 1
                if source.action:
                    session.add(
                        Transaction(
                            account_id=account.id,
                            instrument_id=instrument_ids[source.symbol],
                            cash_operation_id=cash.id,
                            action=source.action,
                            quantity=source.quantity,
                            execution_price=source.execution_price,
                        )
                    )
                    result.trades_added += 1
            session.flush()
            derived, lots = ledger_quantities(session, account.id, batch.as_of)
            observed = {instrument_ids[p.symbol]: p.quantity for p in report.positions}
            if not quantities_match(derived, observed):
                raise ImportValidationError(
                    "Ledger quantities do not reconcile to the included open-position snapshot. "
                    "Import complete history or establish explicit opening balances; "
                    "no rows were saved."
                )
            if result.trades_added:
                # A newly discovered historical event can affect any saved snapshot,
                # including one before this report's as-of date. Check the whole
                # account inside this transaction so a failure rolls back every row.
                for saved in session.scalars(
                    select(ImportBatch).where(
                        ImportBatch.account_id == account.id, ImportBatch.id != batch.id
                    )
                ):
                    saved_derived, _ = ledger_quantities(session, account.id, saved.as_of)
                    saved_observed = {
                        row.instrument_id: row.quantity
                        for row in session.scalars(
                            select(ReportHolding).where(ReportHolding.import_id == saved.id)
                        )
                    }
                    if not quantities_match(saved_derived, saved_observed):
                        raise ImportValidationError(
                            "New trades invalidate a stored portfolio snapshot; "
                            "historical additions cannot be committed. No rows were saved."
                        )
            reported_lots = {}
            for position in report.positions:
                for lot in position.lots:
                    key = lot.get("Instrument/Position")
                    if key in reported_lots:
                        raise ImportValidationError("Repeated open lot identifier.")
                    reported_lots[key] = Decimal(lot["Volume"])
                session.add(
                    ReportHolding(
                        import_id=batch.id,
                        instrument_id=instrument_ids[position.symbol],
                        quantity=position.quantity,
                        market_value=position.market_value,
                        currency=report.currency,
                        pnl_percent=position.pnl_percent,
                        source_row=position.source_row,
                        lots=position.lots,
                    )
                )
            if any(
                abs(lots.get(k, Decimal(0)) - reported_lots.get(k, Decimal(0))) > TOLERANCE
                for k in set(lots) | set(reported_lots)
            ):
                batch.warnings = [
                    *batch.warnings,
                    "Instrument quantities reconcile; some broker lot IDs remain unresolved. "
                    "Lot-level cost basis is not certified.",
                ]
            result.warnings = batch.warnings
            return result

    def portfolio(self) -> PortfolioState:
        with Session(self.engine) as session:
            holdings: dict[int, Holding] = {}
            state = PortfolioState()
            accounts = list(session.scalars(select(Account).order_by(Account.id)))
            confirmed_total = Decimal(0)
            all_confirmed = bool(accounts)
            for account in accounts:
                balance = session.scalar(
                    select(CashBalance)
                    .where(CashBalance.account_id == account.id)
                    .order_by(CashBalance.as_of.desc(), CashBalance.id.desc())
                )
                if balance is None:
                    all_confirmed = False
                    state.warnings.append(
                        f"Account {account.broker_account_id}: total account value not confirmed."
                    )
                else:
                    confirmed_total += balance.amount
                    state.account_value_dates.append(datetime.fromisoformat(balance.as_of))
                    state.warnings.append(
                        f"Account {account.broker_account_id}: confirmed total account value "
                        f"{balance.amount:,.2f} PLN as of {balance.as_of}; "
                        "a manual snapshot, not automatically updated by imports."
                    )
                batch = session.scalar(
                    select(ImportBatch)
                    .where(
                        ImportBatch.account_id == account.id,
                    )
                    .order_by(ImportBatch.as_of.desc(), ImportBatch.id.desc())
                )
                if batch is None:
                    continue
                state.report_dates.append(datetime.fromisoformat(batch.as_of))
                state.warnings.extend(batch.warnings)
                quantities, _ = ledger_quantities(session, account.id, batch.as_of)
                observations = {
                    r.instrument_id: r
                    for r in session.scalars(
                        select(ReportHolding).where(ReportHolding.import_id == batch.id)
                    )
                }
                account_holdings_total = Decimal(0)
                account_values_known = True
                for instrument_id, quantity in quantities.items():
                    if abs(quantity) <= TOLERANCE:
                        continue
                    instrument = session.get(Instrument, instrument_id)
                    report = observations.get(instrument_id)
                    if report is None:
                        account_values_known = False
                    else:
                        account_holdings_total += report.market_value
                    pnl = None
                    if report and report.lots and all(lot.get("Net Profit") for lot in report.lots):
                        values = [Decimal(lot["Net Profit"]) for lot in report.lots]
                        if all(value.is_finite() for value in values):
                            pnl = sum(values, Decimal(0))
                    if instrument_id not in holdings:
                        holdings[instrument_id] = Holding(
                            symbol=instrument.broker_symbol,
                            name=instrument.name,
                            asset_type=instrument.asset_type,
                            quantity=quantity,
                            report_value=report.market_value if report else None,
                            report_pnl_percent=report.pnl_percent if report else None,
                            report_pnl_amount=pnl,
                        )
                    else:
                        existing = holdings[instrument_id]
                        existing.quantity += quantity
                        existing.report_value = (
                            existing.report_value + report.market_value
                            if existing.report_value is not None and report
                            else None
                        )
                        existing.report_pnl_percent = None
                        existing.report_pnl_amount = (
                            existing.report_pnl_amount + pnl
                            if existing.report_pnl_amount is not None and pnl is not None
                            else None
                        )
                if (
                    balance is not None
                    and account_values_known
                    and balance.amount < account_holdings_total
                ):
                    state.account_value_mismatches.append(account.broker_account_id)
                    state.warnings.append(
                        f"Account {account.broker_account_id}: "
                        "total account value below report holdings; "
                        "reserve cannot be calculated. Check snapshot dates and balance."
                    )
                for cash in session.scalars(
                    select(CashOperation).where(
                        CashOperation.account_id == account.id,
                        CashOperation.occurred_at <= batch.as_of,
                    )
                ):
                    state.cash_movement += cash.amount
            state.account_value = confirmed_total if all_confirmed else None
            state.holdings = sorted(holdings.values(), key=lambda h: h.symbol)
            state.warnings = sorted(set(state.warnings))
            return state


def resolve_import_account(path: Path, reference: str) -> str:
    """Resolve known aliases without creating a database or migrating during dry runs."""
    if not path.exists():
        return reference
    engine = create_engine(f"sqlite:///{path.resolve()}")
    try:
        with engine.connect() as connection:
            columns = connection.execute(text("PRAGMA table_info(accounts)")).all()
            if not any(column[1] == "alias" for column in columns):
                return reference
            resolved = connection.execute(
                text(
                    "SELECT broker_account_id FROM accounts WHERE broker = 'XTB' AND alias = :alias"
                ),
                {"alias": reference.strip().lower()},
            ).scalar()
            return resolved if resolved is not None else reference
    finally:
        engine.dispose()
