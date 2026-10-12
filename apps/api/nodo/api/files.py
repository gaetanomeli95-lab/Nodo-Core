"""Files NODO created for you: list and download."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from nodo.api.deps import auth, container, get_db, org_id
from nodo.app import Container

router = APIRouter(prefix="/api/v1/files", dependencies=[Depends(auth)])


@router.get("")
def list_files(c: Container = Depends(container), db: Session = Depends(get_db), org: str = Depends(org_id)):
    return {"files": c.files.list(db, org)}


@router.get("/{name}")
def download(name: str, c: Container = Depends(container)):
    p = c.files.get(name)
    if p is None:
        raise HTTPException(404, "file non trovato")
    return FileResponse(p, filename=p.name)
