"""Relational domain model. Stable UUID ids, explicit ownership (organization_id), UTC timestamps.

The relationship graph is relational for now (see ADR-003/ADR-007): `Relationship` rows plus foreign keys.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class Sensitivity(StrEnum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    CLIENT_PRIVATE = "CLIENT_PRIVATE"
    HIGHLY_SENSITIVE = "HIGHLY_SENSITIVE"


SENSITIVITY_RANK = {s: i for i, s in enumerate(Sensitivity)}


class FactStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    INFERRED = "INFERRED"
    UNVERIFIED = "UNVERIFIED"
    CONFLICTING = "CONFLICTING"
    STALE = "STALE"
    SUPERSEDED = "SUPERSEDED"
    ARCHIVED = "ARCHIVED"


class TaskStatus(StrEnum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    DONE = "DONE"


class Base(DeclarativeBase):
    type_annotation_map = {dict: JSON, list: JSON}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True)


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(200))
    preferences: Mapped[dict] = mapped_column(JSON, default=dict)


class Client(Base, TimestampMixin):
    __tablename__ = "clients"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    slug: Mapped[str] = mapped_column(String(100))
    sector: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)
    sensitivity: Mapped[str] = mapped_column(String(32), default=Sensitivity.CLIENT_PRIVATE)
    aliases: Mapped[list] = mapped_column(JSON, default=list)


class Project(Base, TimestampMixin):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    client_id: Mapped[str | None] = mapped_column(ForeignKey("clients.id"), index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    slug: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(32), default="software")  # software | content | business | mixed
    status: Mapped[str] = mapped_column(String(32), default="active")   # active | paused | done | archived
    priority: Mapped[int] = mapped_column(Integer, default=3)           # 1 (highest) .. 5
    description: Mapped[str | None] = mapped_column(Text)
    sensitivity: Mapped[str] = mapped_column(String(32), default=Sensitivity.INTERNAL)
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Repository(Base, TimestampMixin):
    __tablename__ = "repositories"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32), default="github")
    owner: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(200))
    default_branch: Mapped[str] = mapped_column(String(100), default="main")
    url: Mapped[str | None] = mapped_column(String(500))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tech_state: Mapped[dict] = mapped_column(JSON, default=dict)  # normalized snapshot from the connector


class Task(Base, TimestampMixin):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(32), default=TaskStatus.TODO)
    priority: Mapped[int] = mapped_column(Integer, default=3)
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    blocked_reason: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Decision(Base, TimestampMixin):
    __tablename__ = "decisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    rationale: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="open")  # open | decided | superseded
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Document(Base, TimestampMixin):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(50), default="document")
    uri: Mapped[str | None] = mapped_column(String(1000))
    summary: Mapped[str | None] = mapped_column(Text)
    sensitivity: Mapped[str] = mapped_column(String(32), default=Sensitivity.INTERNAL)


class KnowledgeSource(Base, TimestampMixin):
    """One import of personal knowledge (a ChatGPT/Claude export, later mail or files). Distillation into
    `MemoryFact` rows runs in the background; `stats` tracks its progress honestly."""
    __tablename__ = "knowledge_sources"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))                 # chatgpt | claude | file
    label: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="imported")  # imported | distilling | done | partial | failed | no_model
    note: Mapped[str | None] = mapped_column(Text)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)      # conversations, chunks, distilled, facts, skipped
    sensitivity: Mapped[str] = mapped_column(String(32), default=Sensitivity.CONFIDENTIAL)


class KnowledgeChunk(Base):
    """A searchable slice of an imported conversation/document (full text; the FTS index mirrors it)."""
    __tablename__ = "knowledge_chunks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("knowledge_sources.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    happened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ContentItem(Base, TimestampMixin):
    __tablename__ = "content_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"))
    title: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(50), default="video")  # video | post | story | article
    angle: Mapped[str | None] = mapped_column(String(50))             # commercial | narrative | light | educational
    channel: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(32), default="idea")  # idea | draft | approved | published
    body: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generated_by_run_id: Mapped[str | None] = mapped_column(String(36))


class MemoryFact(Base):
    """A fact with provenance and temporal validity. Never overwritten: superseded instead."""
    __tablename__ = "memory_facts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    subject_type: Mapped[str] = mapped_column(String(50), index=True)   # project | client | person | user ...
    subject_id: Mapped[str] = mapped_column(String(36), index=True)
    layer: Mapped[str] = mapped_column(String(32), default="semantic")  # semantic | episodic | preference | decision | relationship | document | agent
    predicate: Mapped[str | None] = mapped_column(String(100))
    content: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(50), default="manual")  # conversation | document | tool | agent | manual
    source_id: Mapped[str | None] = mapped_column(String(36))
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    status: Mapped[str] = mapped_column(String(32), default=FactStatus.CONFIRMED)
    sensitivity: Mapped[str] = mapped_column(String(32), default=Sensitivity.INTERNAL)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by: Mapped[str | None] = mapped_column(String(36))
    conflicts_with: Mapped[str | None] = mapped_column(String(36))


class Relationship(Base):
    __tablename__ = "relationships"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    from_type: Mapped[str] = mapped_column(String(50))
    from_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(50))  # owns | serves | has_project | stored_in | works_on | depends_on
    to_type: Mapped[str] = mapped_column(String(50))
    to_id: Mapped[str] = mapped_column(String(36), index=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str | None] = mapped_column(String(300))
    channel: Mapped[str] = mapped_column(String(20), default="text")  # text | voice
    active_context: Mapped[dict] = mapped_column(JSON, default=dict)  # {"entity_type","entity_id","last_intent"}


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # user | nodo | system
    content: Mapped[str] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------- execution / telemetry

class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    request_id: Mapped[str] = mapped_column(String(36), index=True)
    parent_run_id: Mapped[str | None] = mapped_column(String(36))
    agent_name: Mapped[str] = mapped_column(String(50), index=True)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running | ok | failed | budget_exceeded
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    budget: Mapped[dict] = mapped_column(JSON, default=dict)
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ToolExecution(Base):
    __tablename__ = "tool_executions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    request_id: Mapped[str] = mapped_column(String(36), index=True)
    agent_run_id: Mapped[str | None] = mapped_column(String(36))
    tool_name: Mapped[str] = mapped_column(String(50))
    action: Mapped[str] = mapped_column(String(50))
    level: Mapped[int] = mapped_column(Integer)
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="ok")  # ok | failed | denied | pending_approval
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InferenceRun(Base):
    __tablename__ = "inference_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    request_id: Mapped[str] = mapped_column(String(36), index=True)
    agent_run_id: Mapped[str | None] = mapped_column(String(36))
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(100))
    purpose: Mapped[str] = mapped_column(String(50))  # synthesis | classification | agent
    is_local: Mapped[bool] = mapped_column(Boolean, default=False)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="ok")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(36), index=True)
    tool_name: Mapped[str] = mapped_column(String(50))
    action: Mapped[str] = mapped_column(String(50))
    level: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | rejected
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EventLog(Base):
    """Append-only. Never updated or deleted by application code."""
    __tablename__ = "event_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    request_id: Mapped[str | None] = mapped_column(String(36), index=True)
    organization_id: Mapped[str | None] = mapped_column(String(36))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Reminder(Base):
    """«Ricordamelo domani alle 9»: delivered by the scheduler (Telegram + the panel) when it is due."""
    __tablename__ = "reminders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    text: Mapped[str] = mapped_column(String(500))
    remind_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")   # pending | sent | cancelled
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VoiceTurnMetric(Base):
    """One row per completed voice turn (Phase 2M): per-stage timestamps plus computed latencies (ms).
    Timestamps are nullable — an interrupted/error turn may never reach later stages."""
    __tablename__ = "voice_turn_metrics"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(36), index=True)
    request_id: Mapped[str | None] = mapped_column(String(36), index=True)
    turn_number: Mapped[int] = mapped_column(Integer, default=0)
    interrupted: Mapped[bool] = mapped_column(Boolean, default=False)
    turn_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    speech_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    speech_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    transcript_partial_first_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    transcript_final_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    intent_resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    context_built_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    model_first_token_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    model_done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tts_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tts_first_audio_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tts_done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    turn_done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[dict] = mapped_column(JSON, default=dict)  # stt | context | model_ttft | model_total | tts_tfa | tts_total | total
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------- night shift (docs/NIGHT_SHIFT.md)

class Site(Base, TimestampMixin):
    """A website NODO watches: a Vercel project, its production URL and (optionally) its GitHub repository."""
    __tablename__ = "sites"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    url: Mapped[str] = mapped_column(String(500))
    vercel_project_id: Mapped[str | None] = mapped_column(String(100), index=True)
    vercel_project_name: Mapped[str | None] = mapped_column(String(200))
    repo_owner: Mapped[str | None] = mapped_column(String(200))
    repo_name: Mapped[str | None] = mapped_column(String(200))
    default_branch: Mapped[str] = mapped_column(String(100), default="main")
    key_pages: Mapped[list] = mapped_column(JSON, default=list)      # [{"path": "/contatti", "expect": "Contattaci"}]
    policy: Mapped[dict] = mapped_column(JSON, default=dict)         # per-site overrides: rollback/redeploy/fix_prs
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sensitivity: Mapped[str] = mapped_column(String(32), default=Sensitivity.INTERNAL)
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="unknown")  # ok | warning | critical | unknown
    status_note: Mapped[str | None] = mapped_column(Text)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_good_deployment_id: Mapped[str | None] = mapped_column(String(100))  # verified healthy in production
    last_good_commit_sha: Mapped[str | None] = mapped_column(String(64))
    current_deployment_id: Mapped[str | None] = mapped_column(String(100))


class NightRun(Base):
    """One pass of the night shift (full night run, light periodic check, manual run, or morning briefing)."""
    __tablename__ = "night_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(36), index=True)  # telemetry correlation (tools/inference)
    kind: Mapped[str] = mapped_column(String(20), default="manual")   # night | light | manual | briefing
    status: Mapped[str] = mapped_column(String(20), default="running")  # running | done | failed
    trigger: Mapped[str] = mapped_column(String(20), default="manual")  # schedule | manual | api | voice
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    briefing: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SiteCheck(Base):
    __tablename__ = "site_checks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("night_runs.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), index=True)
    check: Mapped[str] = mapped_column(String(30))     # deploy | uptime | pages | runtime | ssl | security | repo
    status: Mapped[str] = mapped_column(String(20))    # ok | warning | critical | error | skipped
    summary: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Incident(Base):
    """A problem NODO noticed. Deduplicated by fingerprint; closes itself when the check is healthy again."""
    __tablename__ = "incidents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(200), index=True)
    kind: Mapped[str] = mapped_column(String(30))       # site_down | page_broken | deploy_failed | runtime_errors | ...
    severity: Mapped[str] = mapped_column(String(20))   # critical | warning | info
    title: Mapped[str] = mapped_column(String(300))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | mitigated | resolved | ignored
    resolution: Mapped[str | None] = mapped_column(Text)
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NightAction(Base):
    """What NODO did (or tried, or proposed) about an incident: the 'I did this for you' record."""
    __tablename__ = "night_actions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), index=True)
    site_id: Mapped[str | None] = mapped_column(String(36), index=True)
    incident_id: Mapped[str | None] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(30))   # rollback | redeploy | fix_pr | merge | close_pr | draft | notify
    status: Mapped[str] = mapped_column(String(20))   # done | failed | pending_approval | skipped | verified
    summary: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    approval_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class FixProposal(Base, TimestampMixin):
    """A code fix NODO prepared on a branch + PR. Reaches production only after your approval (merge)."""
    __tablename__ = "fix_proposals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), index=True)
    incident_id: Mapped[str | None] = mapped_column(String(36), index=True)
    run_id: Mapped[str | None] = mapped_column(String(36))
    title: Mapped[str] = mapped_column(String(300))
    diagnosis: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[str] = mapped_column(String(20), default="medium")  # low | medium | high
    files: Mapped[list] = mapped_column(JSON, default=list)   # [{"path", "added", "removed"}]
    diff: Mapped[str] = mapped_column(Text, default="")       # unified diff for review in the UI
    branch: Mapped[str | None] = mapped_column(String(200))
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    pr_number: Mapped[int | None] = mapped_column(Integer)
    pr_url: Mapped[str | None] = mapped_column(String(500))
    preview_url: Mapped[str | None] = mapped_column(String(500))
    preview_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | ok | failed | protected | unknown
    preview_note: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="proposed")  # proposed | merged | rejected | failed
    approval_id: Mapped[str | None] = mapped_column(String(36))
