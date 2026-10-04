"""Context Engine: assembles ONLY the information relevant to an intent into an inspectable ContextPackage."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from nodo.core.intent import Intent
from nodo.db.models import (
    SENSITIVITY_RANK,
    Client,
    ContentItem,
    Decision,
    Document,
    Project,
    Repository,
    Sensitivity,
    Task,
    TaskStatus,
)
from nodo.memory.service import MemoryService


@dataclass
class ContextPackage:
    intent: str
    organization_id: str
    projects: list[dict] = field(default_factory=list)
    clients: list[dict] = field(default_factory=list)
    tasks: list[dict] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    repositories: list[dict] = field(default_factory=list)
    documents: list[dict] = field(default_factory=list)
    content_history: list[dict] = field(default_factory=list)
    facts: list[dict] = field(default_factory=list)
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    sources: list[str] = field(default_factory=list)
    focus: dict | None = None  # the resolved entity, if any
    now: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict:
        d = asdict(self)
        d["sensitivity"] = str(self.sensitivity)
        d["size"] = {k: len(v) for k, v in d.items() if isinstance(v, list)}
        return d

    def raise_sensitivity(self, s: str | Sensitivity) -> None:
        s = Sensitivity(s)
        if SENSITIVITY_RANK[s] > SENSITIVITY_RANK[self.sensitivity]:
            self.sensitivity = s


class ContextEngine:
    def __init__(self, session: Session, organization_id: str, memory: MemoryService):
        self.s, self.org, self.memory = session, organization_id, memory

    def build(self, intent: Intent) -> ContextPackage:
        pkg = ContextPackage(intent.name, self.org)
        if intent.name == "list_projects":
            self._all_projects(pkg)
        elif intent.name == "today":
            self._all_projects(pkg)
            self._tasks(pkg, project_ids=None)
        elif intent.name == "compare":
            for e in intent.entities or ([intent.entity] if intent.entity else []):
                pkg.focus = pkg.focus or asdict(e)
                if e.entity_type == "project":
                    self._project(pkg, e.entity_id, deep=True)
                else:
                    self._client(pkg, e.entity_id, with_projects=True)
        elif intent.entity:
            pkg.focus = asdict(intent.entity)
            if intent.entity.entity_type == "project":
                self._project(pkg, intent.entity.entity_id, deep=True)
            else:
                self._client(pkg, intent.entity.entity_id, with_projects=intent.name != "prepare_content")
        return pkg

    # ------------------------------------------------------------------ loaders
    def _all_projects(self, pkg: ContextPackage) -> None:
        for p in self.s.scalars(select(Project).where(Project.organization_id == self.org,
                                                      Project.status != "archived").order_by(Project.priority)):
            pkg.projects.append(_project_dict(p))
            pkg.raise_sensitivity(p.sensitivity)
        pkg.sources.append("projects")

    def _project(self, pkg: ContextPackage, project_id: str, deep: bool) -> None:
        p = self.s.get(Project, project_id)
        if not p:
            return
        pkg.projects.append(_project_dict(p))
        pkg.raise_sensitivity(p.sensitivity)
        pkg.sources.append(f"project:{p.id}")
        if p.client_id and (c := self.s.get(Client, p.client_id)):
            pkg.clients.append({"id": c.id, "name": c.name, "sector": c.sector})
            pkg.raise_sensitivity(c.sensitivity)
        if not deep:
            return
        self._tasks(pkg, [p.id])
        for d in self.s.scalars(select(Decision).where(Decision.project_id == p.id).order_by(Decision.created_at.desc()).limit(10)):
            pkg.decisions.append({"id": d.id, "project_id": p.id, "title": d.title, "status": d.status,
                                  "rationale": d.rationale})
        for r in self.s.scalars(select(Repository).where(Repository.project_id == p.id)):
            pkg.repositories.append({"id": r.id, "owner": r.owner, "name": r.name, "default_branch": r.default_branch,
                                     "last_synced_at": r.last_synced_at.isoformat() if r.last_synced_at else None,
                                     "tech_state": r.tech_state or {}})
        for doc in self.s.scalars(select(Document).where(Document.project_id == p.id).limit(10)):
            pkg.documents.append({"id": doc.id, "title": doc.title, "kind": doc.kind, "summary": doc.summary})
            pkg.raise_sensitivity(doc.sensitivity)
        self._facts(pkg, "project", p.id)

    def _client(self, pkg: ContextPackage, client_id: str, with_projects: bool) -> None:
        c = self.s.get(Client, client_id)
        if not c:
            return
        pkg.clients.append({"id": c.id, "name": c.name, "sector": c.sector, "notes": c.notes})
        pkg.raise_sensitivity(c.sensitivity)
        pkg.sources.append(f"client:{c.id}")
        for ci in self.s.scalars(select(ContentItem).where(ContentItem.client_id == c.id)
                                 .order_by(ContentItem.created_at.desc()).limit(15)):
            pkg.content_history.append({"id": ci.id, "title": ci.title, "kind": ci.kind, "angle": ci.angle,
                                        "status": ci.status, "channel": ci.channel,
                                        "published_at": ci.published_at.isoformat() if ci.published_at else None})
        pkg.sources.append("content_history")
        self._facts(pkg, "client", c.id)
        if with_projects:
            for p in self.s.scalars(select(Project).where(Project.client_id == c.id)):
                self._project(pkg, p.id, deep=True)

    def _tasks(self, pkg: ContextPackage, project_ids: list[str] | None) -> None:
        q = select(Task).where(Task.organization_id == self.org, Task.status != TaskStatus.DONE)
        if project_ids:
            q = q.where(Task.project_id.in_(project_ids))
        now = datetime.now(UTC)
        for t in self.s.scalars(q.order_by(Task.priority, Task.due_date)):
            due = t.due_date.replace(tzinfo=UTC) if t.due_date and not t.due_date.tzinfo else t.due_date
            pkg.tasks.append({"id": t.id, "project_id": t.project_id, "title": t.title, "status": t.status,
                              "priority": t.priority, "due_date": due.isoformat() if due else None,
                              "overdue": bool(due and due < now), "blocked_reason": t.blocked_reason})
        pkg.sources.append("tasks")

    def _facts(self, pkg: ContextPackage, etype: str, eid: str) -> None:
        for f in self.memory.facts_for(etype, eid):
            pkg.facts.append({"id": f.id, "content": f.content, "layer": f.layer, "status": f.status,
                              "confidence": f.confidence, "source": f"{f.source_type}:{f.source_id or '-'}",
                              "recorded_at": f.recorded_at.isoformat()})
            pkg.raise_sensitivity(f.sensitivity)
        pkg.sources.append(f"memory:{etype}:{eid}")


def _project_dict(p: Project) -> dict:
    return {"id": p.id, "name": p.name, "kind": p.kind, "status": p.status, "priority": p.priority,
            "client_id": p.client_id, "description": p.description,
            "due_date": p.due_date.isoformat() if p.due_date else None,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None}
