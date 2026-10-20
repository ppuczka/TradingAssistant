"""Unique optional account aliases."""

import sqlalchemy as sa
from alembic import op

revision = "0004_account_aliases"
down_revision = "0003_cash_deposits"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("accounts", sa.Column("alias", sa.String(64), nullable=True))
    op.create_index("ix_accounts_alias", "accounts", ["alias"], unique=True)


def downgrade():
    op.drop_index("ix_accounts_alias", table_name="accounts")
    op.drop_column("accounts", "alias")
