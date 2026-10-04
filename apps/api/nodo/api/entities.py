"""REST routes for domain entities (projects, clients, tasks, repositories, decisions, memory facts, content)."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from nodo.api import schemas as S
from nodo.api.deps import auth, org_id, slugify
from nodo.db.models import (
    Client,
    ContentItem,
    Decision,
    FactStatus,
    MemoryFact,
    Project,
    Relationship,
    Repository,
    Sensitivity,
    Task,
)
from nodo.db.session import get_db
from nodo.memory.service import MemoryService

router = APIRouter(prefix="/api/v1", dependencies=[Depends(auth)])


@router.get("/clients", response_model=list[S.ClientOut])
def list_clients(db: Session = Depends(get_db), org: str = Depends(org_id)):
    return db.scalars(select(Client).where(Client.organization_id == org).order_by(Client.name)).all()


@router.post("/clients", response_model=S.ClientOut, status_code=201)
def create_client(body: S.ClientIn, db: Session = Depends(get_db), org: str = Depends(org_id)):
    c = Client(organization_id=org, slug=slugify(body.name), **body.model_dump())
    db.add(c)
    db.flush()
    return c


@router.get("/projects", response_model=list[S.ProjectOut])
def list_projects(db: Session = Depends(get_db), org: str = Depends(org_id)):
    return db.scalars(select(Project).where(Project.organization_id == org).order_by(Project.priority, Project.name)).all()


@router.post("/projects", response_model=S.ProjectOut, status_code=201)
def create_project(body: S.ProjectIn, db: Session = Depends(get_db), org: str = Depends(org_id)):
    if body.client_id and not db.get(Client, body.client_id):
        raise HTTPException(404, "client not found")
    p = Project(organization_id=org, slug=slugify(body.name), **body.model_dump())
    db.add(p)
    db.flush()
    if body.client_id:
        MemoryService(db, org).relate("client", body.client_id, "has_project", "project", p.id)
    return p


@router.get("/projects/{project_id}", response_model=S.ProjectOut)
def get_project(project_id: str, db: Session = Depends(get_db)):
    if p := db.get(Project, project_id):
        return p
    raise HTTPException(404, "project not found")


@router.get("/repositories", response_model=list[S.RepositoryOut])
def list_repositories(project_id: str | None = None, db: Session = Depends(get_db)):
    q = select(Repository)
    if project_id:
        q = q.where(Repository.project_id == project_id)
    return db.scalars(q).all()


@router.post("/repositories", response_model=S.RepositoryOut, status_code=201)
def create_repository(body: S.RepositoryIn, db: Session = Depends(get_db), org: str = Depends(org_id)):
    if not db.get(Project, body.project_id):
        raise HTTPException(404, "project not found")
    r = Repository(**body.model_dump())
    db.add(r)
    db.flush()
    MemoryService(db, org).relate("project", body.project_id, "stored_in", "repository", r.id)
    return r


@router.get("/tasks", response_model=list[S.TaskOut])
def list_tasks(project_id: str | None = None, status: str | None = None, db: Session = Depends(get_db),
               org: str = Depends(org_id)):
    q = select(Task).where(Task.organization_id == org)
    if project_id:
        q = q.where(Task.project_id == project_id)
    if status:
        q = q.where(Task.status == status)
    return db.scalars(q.order_by(Task.priority, Task.due_date)).all()


@router.post("/tasks", response_model=S.TaskOut, status_code=201)
def create_task(body: S.TaskIn, db: Session = Depends(get_db), org: str = Depends(org_id)):
    t = Task(organization_id=org, **body.model_dump())
    db.add(t)
    db.flush()
    return t


@router.patch("/tasks/{task_id}", response_model=S.TaskOut)
def update_task(task_id: str, body: dict, db: Session = Depends(get_db)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    for k in ("title", "status", "priority", "blocked_reason", "due_date"):
        if k in body:
            setattr(t, k, body[k])
    if body.get("status") == "DONE" and not t.completed_at:
        t.completed_at = datetime.now(UTC)
    db.flush()
    return t


@router.get("/decisions", response_model=list[S.DecisionOut])
def list_decisions(project_id: str | None = None, db: Session = Depends(get_db)):
    q = select(Decision)
    if project_id:
        q = q.where(Decision.project_id == project_id)
    return db.scalars(q.order_by(Decision.created_at.desc())).all()


@router.post("/decisions", response_model=S.DecisionOut, status_code=201)
def create_decision(body: S.DecisionIn, db: Session = Depends(get_db), org: str = Depends(org_id)):
    d = Decision(**body.model_dump())
    if d.status == "decided":
        d.decided_at = datetime.now(UTC)
    db.add(d)
    db.flush()
    MemoryService(db, org).remember("project", body.project_id, f"Decisione: {body.title}", layer="decision",
                                    source_type="manual", source_id=d.id)
    return d


@router.get("/memory/facts", response_model=list[S.FactOut])
def list_facts(subject_type: str | None = None, subject_id: str | None = None, status: str | None = None,
               db: Session = Depends(get_db), org: str = Depends(org_id)):
    q = select(MemoryFact).where(MemoryFact.organization_id == org)
    if subject_type:
        q = q.where(MemoryFact.subject_type == subject_type)
    if subject_id:
        q = q.where(MemoryFact.subject_id == subject_id)
    if status:
        q = q.where(MemoryFact.status == status)
    return db.scalars(q.order_by(MemoryFact.recorded_at.desc()).limit(200)).all()


@router.post("/memory/facts", response_model=S.FactOut, status_code=201)
def create_fact(body: S.FactIn, db: Session = Depends(get_db), org: str = Depends(org_id)):
    d = body.model_dump()
    return MemoryService(db, org).remember(d.pop("subject_type"), d.pop("subject_id"), d.pop("content"),
                                           status=FactStatus(d.pop("status")), sensitivity=Sensitivity(d.pop("sensitivity")), **d)


@router.get("/graph/{entity_type}/{entity_id}")
def graph_neighbors(entity_type: str, entity_id: str, db: Session = Depends(get_db), org: str = Depends(org_id)):
    rels: list[Relationship] = MemoryService(db, org).neighbors(entity_type, entity_id)
    return [{"id": r.id, "from": f"{r.from_type}:{r.from_id}", "kind": r.kind, "to": f"{r.to_type}:{r.to_id}"} for r in rels]


@router.get("/content", response_model=list[S.ContentOut])
def list_content(client_id: str | None = None, db: Session = Depends(get_db)):
    q = select(ContentItem)
    if client_id:
        q = q.where(ContentItem.client_id == client_id)
    return db.scalars(q.order_by(ContentItem.created_at.desc()).limit(100)).all()
