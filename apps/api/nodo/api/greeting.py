"""«Buongiorno»: the short, spoken-friendly opening of the day."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from nodo.api.deps import auth, container, org_id
from nodo.app import Container
from nodo.db.models import Message
from nodo.db.session import get_db
from nodo.greeting import build_greeting

router = APIRouter(prefix="/api/v1", dependencies=[Depends(auth)])


@router.get("/greeting")
def get_greeting(c: Container = Depends(container), db: Session = Depends(get_db), org: str = Depends(org_id)):
    return build_greeting(db, org, c.settings)


class GreetBody(BaseModel):
    conversation_id: str | None = None


@router.post("/greeting")
def say_greeting(body: GreetBody, c: Container = Depends(container), db: Session = Depends(get_db),
                 org: str = Depends(org_id)):
    """The greeting as the first message of the conversation, so «sì, parliamone» has something to refer to."""
    g = build_greeting(db, org, c.settings)
    conv = c.core(db)._conversation(body.conversation_id, "text")
    db.add(Message(conversation_id=conv.id, role="nodo", content=g["text"], request_id="greeting"))
    db.commit()
    return {**g, "conversation_id": conv.id}
