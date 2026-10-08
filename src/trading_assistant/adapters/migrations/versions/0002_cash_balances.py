"""Human-confirmed cash observations, separate from cash movements."""

import sqlalchemy as sa
from alembic import op

revision = "0002_cash_balances"
down_revision = "0001_portfolio"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cash_balances",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("account_id", sa.Integer, sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("amount", sa.Text, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("as_of", sa.String(40), nullable=False),
        sa.Column("recorded_at", sa.String(40), nullable=False),
    )
    op.create_index("ix_cash_balances_account_id", "cash_balances", ["account_id"])


def downgrade():
    op.drop_table("cash_balances")
