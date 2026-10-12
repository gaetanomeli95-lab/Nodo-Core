"""personal knowledge: imported ChatGPT/Claude conversations, searchable chunks (docs/CONOSCENZA.md)

Revision ID: d9a1c4e7b2f3
Revises: c7d4e2f1a9b0
Create Date: 2026-10-12

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d9a1c4e7b2f3"
down_revision: str | Sequence[str] | None = "c7d4e2f1a9b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_sources",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("organization_id", sa.String(length=36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="imported"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("stats", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("sensitivity", sa.String(length=32), nullable=False, server_default="CONFIDENTIAL"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_knowledge_sources_organization_id", "knowledge_sources", ["organization_id"])
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("organization_id", sa.String(length=36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("source_id", sa.String(length=36), sa.ForeignKey("knowledge_sources.id"), nullable=False),
        sa.Column("document_id", sa.String(length=36), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("happened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for col in ("organization_id", "source_id", "document_id"):
        op.create_index(f"ix_knowledge_chunks_{col}", "knowledge_chunks", [col])
    # The FTS5 index is created lazily by nodo.knowledge.service.ensure_index (SQLite only).


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS knowledge_fts")
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_sources")
