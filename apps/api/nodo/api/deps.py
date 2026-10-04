from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from nodo.app import Container, ensure_workspace
from nodo.db.session import get_db


def container(request: Request) -> Container:
    return request.app.state.container


def auth(request: Request, authorization: str | None = Header(default=None)) -> None:
    """Single-user dev auth: if NODO_API_TOKEN is set, require `Authorization: Bearer <token>`."""
    token = request.app.state.settings.api_token
    if token and authorization != f"Bearer {token}":
        raise HTTPException(401, "invalid or missing token")


def org_id(db: Session = Depends(get_db)) -> str:
    return ensure_workspace(db)[0].id


def slugify(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:100]
