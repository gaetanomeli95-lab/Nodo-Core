"""reminders

Revision ID: e3b8f5a2c6d1
Revises: d9a1c4e7b2f3
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e3b8f5a2c6d1"
down_revision: str | Sequence[str] | None = "d9a1c4e7b2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if "reminders" in sa.inspect(op.get_bind()).get_table_names():   # already made by the dev start-up create_all
        return
    op.create_table(
        "reminders",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("organization_id", sa.String(length=36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("text", sa.String(length=500), nullable=False),
        sa.Column("remind_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_reminders_organization_id", "reminders", ["organization_id"])
    op.create_index("ix_reminders_remind_at", "reminders", ["remind_at"])


def downgrade() -> None:
    op.drop_table("reminders")
