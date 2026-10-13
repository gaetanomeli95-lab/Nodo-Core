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


@router.get("/{name}/preview")
def preview(name: str, c: Container = Depends(container)):
    """Text of a Word / Excel / PowerPoint / text file, to show it on screen without Office."""
    p = c.files.get(name)
    if p is None:
        raise HTTPException(404, "file non trovato")
    from nodo.services.documents import extract_text
    ext = p.suffix.lower()
    if ext in (".docx", ".xlsx", ".pptx", ".pdf"):
        text = extract_text(p)
    elif ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bin", ".zip"):
        text = ""
    else:
        text = p.read_text(encoding="utf-8", errors="replace")
    return {"name": p.name, "text": text[:60000], "truncated": len(text) > 60000}


@router.get("/{name}")
def download(name: str, c: Container = Depends(container)):
    p = c.files.get(name)
    if p is None:
        raise HTTPException(404, "file non trovato")
    return FileResponse(p, filename=p.name)
