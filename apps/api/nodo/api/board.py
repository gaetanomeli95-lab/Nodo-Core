"""The board («la lavagna»): notes NODO keeps on screen. The panel reads it and can tick notes off."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from nodo.api.deps import auth, get_db, org_id
from nodo.db.models import BoardNote

router = APIRouter(prefix="/api/v1/board", dependencies=[Depends(auth)])


def _out(n: BoardNote) -> dict:
    return {"id": n.id, "text": n.text, "color": n.color, "created_at": n.created_at.isoformat()}


@router.get("")
def board(db: Session = Depends(get_db), org: str = Depends(org_id)):
    rows = db.scalars(select(BoardNote).where(BoardNote.organization_id == org, BoardNote.done_at.is_(None))
                      .order_by(BoardNote.created_at)).all()
    return {"notes": [_out(n) for n in rows]}


class NoteIn(BaseModel):
    text: str
    color: str = "mint"


@router.post("")
def pin(body: NoteIn, db: Session = Depends(get_db), org: str = Depends(org_id)):
    text = body.text.strip()[:400]
    if not text:
        raise HTTPException(400, "nota vuota")
    n = BoardNote(organization_id=org, text=text, color=body.color if body.color in
                  ("mint", "gold", "violet", "rose", "blue") else "mint")
    db.add(n)
    db.commit()
    return _out(n)


@router.post("/{note_id}/done")
def done(note_id: str, db: Session = Depends(get_db), org: str = Depends(org_id)):
    n = db.get(BoardNote, note_id)
    if n is None or n.organization_id != org:
        raise HTTPException(404, "nota non trovata")
    n.done_at = datetime.now(UTC)
    db.commit()
    return {"ok": True}
