"""board: notes NODO pins on the screen

Revision ID: a7d2e9c4b1f8
Revises: f4c9a6b3d7e2
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7d2e9c4b1f8"
down_revision: str | Sequence[str] | None = "f4c9a6b3d7e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if "board_notes" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "board_notes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("organization_id", sa.String(length=36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("color", sa.String(length=16), nullable=False, server_default="mint"),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_board_notes_organization_id", "board_notes", ["organization_id"])


def downgrade() -> None:
    op.drop_table("board_notes")
