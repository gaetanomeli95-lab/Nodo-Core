"""Read-only operating radar: one API for the UI and conversational brain."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from nodo.api.deps import auth, container, org_id
from nodo.app import Container
from nodo.db.session import get_db
from nodo.pulse import build_pulse

router = APIRouter(prefix="/api/v1", dependencies=[Depends(auth)])


@router.get("/pulse")
def get_pulse(c: Container = Depends(container), db: Session = Depends(get_db),
              org: str = Depends(org_id)):
    return build_pulse(db, org, c.settings)
