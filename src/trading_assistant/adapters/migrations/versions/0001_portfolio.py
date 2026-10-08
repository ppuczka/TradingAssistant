"""Initial scoped XTB ledger. SQLite monetary values are exact decimal text."""

import sqlalchemy as sa
from alembic import op

revision = "0001_portfolio"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("broker", sa.String(32), nullable=False),
        sa.Column("broker_account_id", sa.String(128), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.UniqueConstraint("broker", "broker_account_id"),
    )
    op.create_table(
        "instruments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("broker", sa.String(32), nullable=False),
        sa.Column("broker_symbol", sa.String(128), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("asset_type", sa.String(32), nullable=False),
        sa.Column("canonical_symbol", sa.String(128)),
        sa.Column("exchange", sa.String(64)),
        sa.Column("quote_currency", sa.String(3)),
        sa.UniqueConstraint("broker", "broker_symbol"),
    )
    op.create_table(
        "import_batches",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("account_id", sa.Integer, sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("as_of", sa.String(40), nullable=False),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("excluded_rows", sa.Integer, nullable=False),
        sa.Column("warnings", sa.JSON, nullable=False),
        sa.Column("closed_records", sa.JSON, nullable=False),
        sa.UniqueConstraint("account_id", "fingerprint"),
    )
    op.create_index("ix_import_batches_account_id", "import_batches", ["account_id"])
    op.create_table(
        "cash_operations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("account_id", sa.Integer, sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("import_id", sa.Integer, sa.ForeignKey("import_batches.id"), nullable=False),
        sa.Column("instrument_id", sa.Integer, sa.ForeignKey("instruments.id")),
        sa.Column("broker_operation_id", sa.String(128), nullable=False),
        sa.Column("occurred_at", sa.String(40), nullable=False),
        sa.Column("kind", sa.String(128), nullable=False),
        sa.Column("amount", sa.Text, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("product", sa.String(32), nullable=False),
        sa.Column("position_id", sa.String(128)),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.UniqueConstraint("account_id", "broker_operation_id"),
    )
    op.create_index("ix_cash_operations_account_id", "cash_operations", ["account_id"])
    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("account_id", sa.Integer, sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("instrument_id", sa.Integer, sa.ForeignKey("instruments.id"), nullable=False),
        sa.Column(
            "cash_operation_id", sa.Integer, sa.ForeignKey("cash_operations.id"), nullable=False
        ),
        sa.Column("action", sa.String(8), nullable=False),
        sa.Column("quantity", sa.Text, nullable=False),
        sa.Column("execution_price", sa.Text, nullable=False),
        sa.Column("quote_currency", sa.String(3)),
        sa.UniqueConstraint("cash_operation_id"),
    )
    op.create_index("ix_transactions_account_id", "transactions", ["account_id"])
    op.create_index("ix_transactions_instrument_id", "transactions", ["instrument_id"])
    op.create_table(
        "report_holdings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("import_id", sa.Integer, sa.ForeignKey("import_batches.id"), nullable=False),
        sa.Column("instrument_id", sa.Integer, sa.ForeignKey("instruments.id"), nullable=False),
        sa.Column("quantity", sa.Text, nullable=False),
        sa.Column("market_value", sa.Text, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("pnl_percent", sa.Text),
        sa.Column("source_row", sa.Integer, nullable=False),
        sa.Column("lots", sa.JSON, nullable=False),
        sa.UniqueConstraint("import_id", "instrument_id"),
    )
    op.create_index("ix_report_holdings_import_id", "report_holdings", ["import_id"])


def downgrade():
    for table in (
        "report_holdings",
        "transactions",
        "cash_operations",
        "import_batches",
        "instruments",
        "accounts",
    ):
        op.drop_table(table)
