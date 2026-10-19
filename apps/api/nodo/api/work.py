"""GET /api/v1/work/live: the long jobs and the end of the file each one is writing (the panel shows it live)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from nodo.api.deps import auth

router = APIRouter(prefix="/api/v1", dependencies=[Depends(auth)])


@router.get("/work/live")
def live():
    from nodo.brain import live_work
    jobs = live_work.snapshot()
    return {"jobs": jobs, "running": sum(1 for j in jobs if j["status"] == "in corso")}
