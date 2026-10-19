"""GET /api/v1/work/live: the long jobs and the end of the file each one is writing (the panel shows it live)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from nodo.api.deps import auth, container
from nodo.app import Container
from nodo.db.models import InferenceRun
from nodo.db.session import get_db

router = APIRouter(prefix="/api/v1", dependencies=[Depends(auth)])


@router.get("/work/live")
def live():
    from nodo.brain import live_work
    jobs = live_work.snapshot()
    return {"jobs": jobs, "running": sum(1 for j in jobs if j["status"] == "in corso")}


@router.get("/hud")
def hud(c: Container = Depends(container), db: Session = Depends(get_db)):
    """The cockpit readouts (HoloHud.tsx). Only measured facts: the last model that really answered and how long
    it took, today's runs and how many went wrong, how long NODO has been on, the jobs running now."""
    from nodo.brain import live_work
    since = datetime.now(UTC) - timedelta(hours=24)
    last = db.scalars(select(InferenceRun).where(InferenceRun.provider != "deterministic")
                      .order_by(InferenceRun.created_at.desc()).limit(1)).first()
    runs = db.execute(select(func.count(), func.sum(case((InferenceRun.status != "ok", 1), else_=0)),
                             func.avg(InferenceRun.latency_ms))
                      .where(InferenceRun.created_at >= since)).one()
    started = getattr(c, "started_at", None)
    return {
        "brain": None if last is None else {"provider": last.provider, "model": last.model, "ms": last.latency_ms,
                                            "ok": last.status == "ok", "at": last.created_at.isoformat()},
        "runs": int(runs[0] or 0), "errors": int(runs[1] or 0), "avg_ms": int(runs[2] or 0),
        "uptime_s": int((datetime.now(UTC) - started).total_seconds()) if started else None,
        "jobs": len(live_work.running()),
    }
