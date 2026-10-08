"""SQLAlchemy persistence setup and programmatic Alembic migrations."""

from decimal import Decimal
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import (
    JSON,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class ExactDecimal(TypeDecorator):
    """Avoid SQLite float coercion; other databases can use native NUMERIC."""

    impl = Numeric(38, 16)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(Text() if dialect.name == "sqlite" else self.impl)

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return str(value) if dialect.name == "sqlite" else value

    def process_result_value(self, value, dialect):
        return Decimal(str(value)) if value is not None else None


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("broker", "broker_account_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    broker: Mapped[str] = mapped_column(String(32), default="XTB")
    broker_account_id: Mapped[str] = mapped_column(String(128))
    currency: Mapped[str] = mapped_column(String(3))


class Instrument(Base):
    __tablename__ = "instruments"
    __table_args__ = (UniqueConstraint("broker", "broker_symbol"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    broker: Mapped[str] = mapped_column(String(32), default="XTB")
    broker_symbol: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(Text)
    asset_type: Mapped[str] = mapped_column(String(32))
    canonical_symbol: Mapped[str | None] = mapped_column(String(128))
    exchange: Mapped[str | None] = mapped_column(String(64))
    quote_currency: Mapped[str | None] = mapped_column(String(3))


class ImportBatch(Base):
    __tablename__ = "import_batches"
    __table_args__ = (UniqueConstraint("account_id", "fingerprint"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    as_of: Mapped[str] = mapped_column(String(40))
    scope: Mapped[str] = mapped_column(String(32), default="MY_TRADES")
    excluded_rows: Mapped[int]
    warnings: Mapped[list] = mapped_column(JSON)
    closed_records: Mapped[list] = mapped_column(JSON)


class CashOperation(Base):
    __tablename__ = "cash_operations"
    __table_args__ = (UniqueConstraint("account_id", "broker_operation_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    import_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"))
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id"))
    broker_operation_id: Mapped[str] = mapped_column(String(128))
    occurred_at: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(128))
    amount: Mapped[Decimal] = mapped_column(ExactDecimal())
    currency: Mapped[str] = mapped_column(String(3))
    product: Mapped[str] = mapped_column(String(32), default="MY_TRADES")
    position_id: Mapped[str | None] = mapped_column(String(128))
    payload: Mapped[dict] = mapped_column(JSON)


class Transaction(Base):
    __tablename__ = "transactions"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    cash_operation_id: Mapped[int] = mapped_column(ForeignKey("cash_operations.id"), unique=True)
    action: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[Decimal] = mapped_column(ExactDecimal())
    execution_price: Mapped[Decimal] = mapped_column(ExactDecimal())
    # Deliberately unknown until currency resolution; the account amount is PLN.
    quote_currency: Mapped[str | None] = mapped_column(String(3))


class ReportHolding(Base):
    """Report evidence, never another set of ledger trades."""

    __tablename__ = "report_holdings"
    __table_args__ = (UniqueConstraint("import_id", "instrument_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    import_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), index=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    quantity: Mapped[Decimal] = mapped_column(ExactDecimal())
    market_value: Mapped[Decimal] = mapped_column(ExactDecimal())
    currency: Mapped[str] = mapped_column(String(3))
    pnl_percent: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    source_row: Mapped[int]
    lots: Mapped[list] = mapped_column(JSON)


def database_engine(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{path.resolve()}", connect_args={"timeout": 15, "autocommit": False}
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite(connection, _):
        # Use SQLite's modern transaction behavior after connection setup.
        original = connection.autocommit
        connection.autocommit = True
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()
        connection.autocommit = original

    return engine


def migrate(engine) -> None:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
