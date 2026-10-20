"""Persist observed intraday estimates and daily summaries."""

import sqlalchemy as sa
from alembic import op

revision = "0005_change_history"
down_revision = "0004_account_aliases"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "intraday_estimates",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("minute", sa.String(40), nullable=False),
        sa.Column("observed_at", sa.String(40), nullable=False),
        sa.Column("segment", sa.String(40), nullable=False),
        sa.Column("context", sa.String(64), nullable=False),
        sa.Column("covered", sa.Integer, nullable=False),
        sa.Column("total", sa.Integer, nullable=False),
        sa.Column("pnl", sa.JSON, nullable=False),
        sa.Column("percent", sa.JSON, nullable=True),
        sa.UniqueConstraint("minute", "segment"),
    )
    op.create_index("ix_intraday_estimates_minute", "intraday_estimates", ["minute"])
    op.create_table(
        "daily_estimates",
        sa.Column("day", sa.String(10), primary_key=True),
        sa.Column("payload", sa.JSON, nullable=False),
    )


def downgrade():
    op.drop_table("daily_estimates")
    op.drop_table("intraday_estimates")
