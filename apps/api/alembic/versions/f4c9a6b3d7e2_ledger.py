"""ledger: the shop register (sales, replacements, expenses, payments)

Revision ID: f4c9a6b3d7e2
Revises: e3b8f5a2c6d1
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f4c9a6b3d7e2"
down_revision: str | Sequence[str] | None = "e3b8f5a2c6d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if "ledger_entries" in sa.inspect(op.get_bind()).get_table_names():   # made by the dev start-up create_all
        return
    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("organization_id", sa.String(length=36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("business", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("day", sa.String(length=10), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("item", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("size", sa.String(length=40), nullable=True),
        sa.Column("qty", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("unit_price", sa.Float(), nullable=True),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("counterpart", sa.String(length=200), nullable=True),
        sa.Column("payment", sa.String(length=16), nullable=False, server_default="pagato"),
        sa.Column("paid_on", sa.String(length=10), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="fatto"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ledger_entries_organization_id", "ledger_entries", ["organization_id"])
    op.create_index("ix_ledger_entries_day", "ledger_entries", ["day"])
    op.create_index("ix_ledger_entries_kind", "ledger_entries", ["kind"])


def downgrade() -> None:
    op.drop_table("ledger_entries")
