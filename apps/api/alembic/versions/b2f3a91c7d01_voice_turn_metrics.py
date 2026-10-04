"""voice turn metrics (Phase 2M latency telemetry)

Revision ID: b2f3a91c7d01
Revises: 51ea0000e744
Create Date: 2026-10-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2f3a91c7d01"
down_revision: Union[str, Sequence[str], None] = "51ea0000e744"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STAMPS = ("turn_started", "speech_started", "speech_end", "transcript_partial_first", "transcript_final",
           "intent_resolved", "context_built", "model_first_token", "model_done", "tts_started",
           "tts_first_audio", "tts_done", "turn_done")


def upgrade() -> None:
    op.create_table(
        "voice_turn_metrics",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=True),
        sa.Column("request_id", sa.String(length=36), nullable=True),
        sa.Column("turn_number", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("interrupted", sa.Boolean(), nullable=False, server_default=sa.false()),
        *[sa.Column(f"{k}_at", sa.DateTime(timezone=True), nullable=True) for k in _STAMPS],
        sa.Column("latency_ms", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_voice_turn_metrics_session_id", "voice_turn_metrics", ["session_id"])
    op.create_index("ix_voice_turn_metrics_conversation_id", "voice_turn_metrics", ["conversation_id"])
    op.create_index("ix_voice_turn_metrics_request_id", "voice_turn_metrics", ["request_id"])


def downgrade() -> None:
    op.drop_table("voice_turn_metrics")
