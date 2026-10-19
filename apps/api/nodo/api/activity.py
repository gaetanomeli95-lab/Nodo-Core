"""The activity feed: what NODO is actually doing, for the living interface.

The panel polls this every few seconds and turns real events — a tool call, a memory written, a model
turn finished, a night job, an approval waiting — into light on the MindGraph and lines in the cockpit
log. Everything comes from the event log and the metrics tables: nothing is simulated."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from nodo.api.deps import auth, org_id
from nodo.db.models import EventLog, VoiceTurnMetric
from nodo.db.session import get_db

router = APIRouter(prefix="/api/v1", dependencies=[Depends(auth)])

_KIND = {
    "agent.work": "tool", "memory.fact_recorded": "memory", "memory.conflict_detected": "memory",
    "request.received": "voice", "request.completed": "voice", "request.failed": "voice",
    "model.failed": "voice", "approval.requested": "decision", "approval.approved": "decision",
    "approval.rejected": "decision", "night.run.started": "night", "night.run.finished": "night",
    "night.incident.opened": "night", "nodo.selfcare": "night", "social.draft": "social",
    "agent.started": "agent", "agent.finished": "agent", "client.created": "memory",
    "project.created": "memory", "task.created": "memory",
}
_WHAT = {
    "agent.work": lambda p: f"{p.get('tool', '?')} · "
                            f"{'al lavoro' if p.get('running') else 'ok' if p.get('ok') else 'errore'}",
    "memory.fact_recorded": lambda p: f"nuovo ricordo ({p.get('layer', '?')})",
    "memory.conflict_detected": lambda p: "un ricordo in conflitto, li sto sistemando",
    "request.completed": lambda p: f"risposta via {p.get('provider') or '?'}",
    "model.failed": lambda p: "un modello non ha risposto, ho cambiato strada",
    "approval.requested": lambda p: f"ti chiedo: {p.get('label', 'una decisione')}",
    "approval.approved": lambda p: "hai detto sì, eseguo",
    "approval.rejected": lambda p: "hai detto no, archivio",
    "night.run.finished": lambda p: f"notte: {p.get('kind', 'giro')} {p.get('status', '')}".strip(),
    "social.draft": lambda p: f"bozza social pronta: {p.get('title', '')[:60]}",
    "agent.finished": lambda p: f"agente {p.get('agent', '?')} · {p.get('status', '')}".strip(),
}


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=UTC)


@router.get("/activity")
def activity(since: str | None = None, db: Session = Depends(get_db), org: str = Depends(org_id)):
    """Recent real events, newest first. `since` (ISO) lets the panel ask only what is new."""
    cutoff = None
    if since:
        try:
            cutoff = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            cutoff = None
    q = select(EventLog).where(EventLog.organization_id == org)
    if cutoff:
        q = q.where(EventLog.created_at > cutoff)
    rows = db.scalars(q.order_by(desc(EventLog.created_at)).limit(25)).all()
    out = []
    for e in rows:
        p = e.payload or {}
        what = _WHAT.get(e.event_type, lambda x: "")(p)
        out.append({"id": e.id, "kind": _KIND.get(e.event_type, "other"), "type": e.event_type,
                    "what": what or e.event_type, "at": _aware(e.created_at).isoformat()})
    # the last finished voice turn says how fast the brain actually answered — its pulse
    turn = db.scalars(select(VoiceTurnMetric).order_by(desc(VoiceTurnMetric.created_at)).limit(1)).first()
    if turn and (cutoff is None or _aware(turn.created_at) > cutoff):
        ms = int((turn.latency_ms or {}).get("total") or 0)
        out.append({"id": f"turn-{turn.id}", "kind": "voice", "type": "voice.turn",
                    "what": f"turno vocale · {ms} ms" + (" · interrotto" if turn.interrupted else ""),
                    "at": _aware(turn.created_at).isoformat()})
    out.sort(key=lambda x: x["at"], reverse=True)
    return {"events": out[:20], "now": datetime.now(UTC).isoformat()}
