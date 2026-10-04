"""Command surface (SSE streaming), conversations, approvals, telemetry, timeline, system info."""
from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from nodo.api import schemas as S
from nodo.api.deps import auth, container, org_id
from nodo.app import Container
from nodo.core.telemetry import Telemetry
from nodo.db.models import Approval, Conversation, EventLog, Message
from nodo.db.session import get_db, session_scope

router = APIRouter(prefix="/api/v1", dependencies=[Depends(auth)])


@router.post("/command")
async def command(body: S.CommandIn, c: Container = Depends(container)):
    """Streams execution events as SSE. Each event: {type, request_id, ...}. Final event type is `done`."""
    async def gen():
        with session_scope() as db:   # one unit of work per request; committed when the stream completes
            core = c.core(db)
            async for ev in core.handle(body.text, body.conversation_id, body.channel, body.language):
                yield {"event": ev["type"], "data": json.dumps(ev, ensure_ascii=False, default=str)}
    return EventSourceResponse(gen())


@router.post("/command/sync")
async def command_sync(body: S.CommandIn, c: Container = Depends(container), db: Session = Depends(get_db)):
    """Non-streaming variant (mobile/CLI friendly)."""
    out = await c.core(db).run_once(body.text, body.conversation_id, channel=body.channel, language=body.language)
    return out.__dict__ | {"reports": None}


@router.get("/conversations/{conversation_id}/messages", response_model=list[S.MessageOut])
def messages(conversation_id: str, db: Session = Depends(get_db)):
    if not db.get(Conversation, conversation_id):
        raise HTTPException(404, "conversation not found")
    return db.scalars(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at)).all()


@router.get("/conversations/{conversation_id}/context")
def conversation_context(conversation_id: str, db: Session = Depends(get_db)):
    conv = db.get(Conversation, conversation_id)
    if not conv:
        raise HTTPException(404, "conversation not found")
    return conv.active_context


@router.get("/approvals", response_model=list[S.ApprovalOut])
def approvals(status: str | None = "pending", db: Session = Depends(get_db), org: str = Depends(org_id)):
    q = select(Approval).where(Approval.organization_id == org)
    if status:
        q = q.where(Approval.status == status)
    return db.scalars(q.order_by(Approval.created_at.desc())).all()


@router.post("/approvals/{approval_id}/{decision}", response_model=S.ApprovalOut)
def resolve_approval(approval_id: str, decision: str, db: Session = Depends(get_db), c: Container = Depends(container)):
    if decision not in ("approve", "reject"):
        raise HTTPException(400, "decision must be approve|reject")
    a = db.get(Approval, approval_id)
    if not a or a.status != "pending":
        raise HTTPException(404, "pending approval not found")
    a.status, a.resolved_at = ("approved" if decision == "approve" else "rejected"), datetime.now(UTC)
    db.add(EventLog(event_type=f"approval.{a.status}", request_id=a.request_id, organization_id=a.organization_id,
                    payload={"approval_id": a.id, "tool": a.tool_name, "action": a.action}))
    # NOTE: V0.1 records the decision; execution of approved actions lands with the first write connector (Phase 4).
    return a


@router.get("/telemetry/usage")
def usage(db: Session = Depends(get_db)):
    return Telemetry(db).usage_today()


@router.get("/telemetry/trace/{request_id}")
def trace(request_id: str, db: Session = Depends(get_db)):
    return Telemetry(db).trace(request_id)


@router.get("/timeline")
def timeline(limit: int = 50, db: Session = Depends(get_db)):
    rows = db.scalars(select(EventLog).order_by(EventLog.created_at.desc()).limit(min(limit, 200))).all()
    return [{"id": e.id, "type": e.event_type, "request_id": e.request_id, "at": e.created_at.isoformat(),
             "payload": e.payload} for e in rows]


@router.get("/system")
async def system(c: Container = Depends(container), db: Session = Depends(get_db)):
    s = c.settings
    models = []
    for p in c.providers:
        avail = await p.available()
        models += [{"id": d.id, "provider": d.provider, "model": d.model, "tier": d.tier, "is_local": d.is_local,
                    "max_sensitivity": str(d.max_sensitivity), "available": avail} for d in p.models()]
    return {"name": "NODO CORE", "version": "0.1.0", "env": s.env, "mode": s.mode, "language": s.default_language,
            "models": models, "voice": {"stt": {"name": c.stt.name, "location": c.stt.location},
                                        "tts": {"name": c.tts.name, "location": c.tts.location}},
            "github": type(c.github).__name__, "usage": Telemetry(db).usage_today()}
