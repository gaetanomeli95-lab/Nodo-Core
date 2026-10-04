from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ClientIn(BaseModel):
    name: str
    sector: str | None = None
    notes: str | None = None
    aliases: list[str] = []
    sensitivity: str = "CLIENT_PRIVATE"


class ClientOut(ORM):
    id: str
    name: str
    slug: str
    sector: str | None
    notes: str | None
    aliases: list
    sensitivity: str


class ProjectIn(BaseModel):
    name: str
    client_id: str | None = None
    kind: str = "software"
    status: str = "active"
    priority: int = Field(3, ge=1, le=5)
    description: str | None = None
    aliases: list[str] = []
    sensitivity: str = "INTERNAL"
    due_date: datetime | None = None


class ProjectOut(ORM):
    id: str
    name: str
    slug: str
    client_id: str | None
    kind: str
    status: str
    priority: int
    description: str | None
    aliases: list
    sensitivity: str
    due_date: datetime | None
    updated_at: datetime


class RepositoryIn(BaseModel):
    project_id: str
    owner: str
    name: str
    default_branch: str = "main"
    url: str | None = None


class RepositoryOut(ORM):
    id: str
    project_id: str
    provider: str
    owner: str
    name: str
    default_branch: str
    url: str | None
    last_synced_at: datetime | None
    tech_state: dict


class TaskIn(BaseModel):
    title: str
    project_id: str | None = None
    status: str = "TODO"
    priority: int = Field(3, ge=1, le=5)
    due_date: datetime | None = None
    blocked_reason: str | None = None


class TaskOut(ORM):
    id: str
    title: str
    project_id: str | None
    status: str
    priority: int
    due_date: datetime | None
    blocked_reason: str | None
    completed_at: datetime | None


class DecisionIn(BaseModel):
    project_id: str
    title: str
    rationale: str | None = None
    status: str = "open"


class DecisionOut(ORM):
    id: str
    project_id: str
    title: str
    rationale: str | None
    status: str
    decided_at: datetime | None


class FactIn(BaseModel):
    subject_type: str
    subject_id: str
    content: str
    predicate: str | None = None
    layer: str = "semantic"
    confidence: float = 1.0
    status: str = "CONFIRMED"
    sensitivity: str = "INTERNAL"
    source_type: str = "manual"
    source_id: str | None = None


class FactOut(ORM):
    id: str
    subject_type: str
    subject_id: str
    layer: str
    predicate: str | None
    content: str
    source_type: str
    source_id: str | None
    confidence: float
    status: str
    sensitivity: str
    recorded_at: datetime
    valid_from: datetime | None
    valid_until: datetime | None
    superseded_by: str | None
    conflicts_with: str | None


class ContentOut(ORM):
    id: str
    client_id: str
    title: str
    kind: str
    angle: str | None
    status: str
    body: str | None
    published_at: datetime | None


class CommandIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None
    channel: str = "text"
    language: str | None = None


class ApprovalOut(ORM):
    id: str
    request_id: str
    tool_name: str
    action: str
    level: int
    payload: dict
    reason: str | None
    status: str
    created_at: datetime
    resolved_at: datetime | None


class MessageOut(ORM):
    id: str
    role: str
    content: str
    request_id: str | None
    created_at: datetime
