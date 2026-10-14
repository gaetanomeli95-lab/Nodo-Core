"""ledger: part payments (acconti) on a sale

Revision ID: b8e1f2a3c4d5
Revises: a7d2e9c4b1f8
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8e1f2a3c4d5"
down_revision: str | Sequence[str] | None = "a7d2e9c4b1f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("ledger_entries")}
    if "paid_amount" not in cols:
        op.add_column("ledger_entries", sa.Column("paid_amount", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("ledger_entries", "paid_amount")
