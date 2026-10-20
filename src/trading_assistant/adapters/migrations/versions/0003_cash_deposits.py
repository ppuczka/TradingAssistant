"""Manual deposits, kept separately from imported broker operations."""

import sqlalchemy as sa
from alembic import op

revision = "0003_cash_deposits"
down_revision = "0002_cash_balances"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cash_deposits",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("account_id", sa.Integer, sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("amount", sa.Text, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("occurred_at", sa.String(40), nullable=False),
    )
    op.create_index("ix_cash_deposits_account_id", "cash_deposits", ["account_id"])


def downgrade():
    op.drop_table("cash_deposits")
