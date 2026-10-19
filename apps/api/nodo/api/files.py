"""Files NODO created for you: list and download."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from nodo.api.deps import auth, container, get_db, org_id
from nodo.app import Container

router = APIRouter(prefix="/api/v1/files", dependencies=[Depends(auth)])


@router.get("")
def list_files(c: Container = Depends(container), db: Session = Depends(get_db), org: str = Depends(org_id)):
    return {"files": c.files.list(db, org)}


_UPLOAD_KINDS = {"png", "jpg", "jpeg", "webp", "pdf", "docx", "xlsx", "pptx", "csv", "txt", "md"}


@router.post("/upload")
async def upload(file: UploadFile = File(...), c: Container = Depends(container), db: Session = Depends(get_db),
                 org: str = Depends(org_id)):
    """A photo or a document dropped on the panel: kept among NODO's files so the brain can read or show it."""
    from datetime import datetime
    raw = (file.filename or "file").rsplit("/", 1)[-1]
    ext = raw.rsplit(".", 1)[-1].lower() if "." in raw else ""
    if ext not in _UPLOAD_KINDS:
        raise HTTPException(400, "tipo di file non supportato: foto, PDF, Word, Excel, PowerPoint o testo")
    data = await file.read(15_000_001)
    if len(data) > 15_000_000:
        raise HTTPException(413, "file troppo grande (massimo 15 MB)")
    if ext in ("png", "jpg", "jpeg", "webp") and not (data[:4] == b"\x89PNG" or data[:3] == b"\xff\xd8\xff"
                                                      or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")):
        raise HTTPException(400, "questa non è una foto valida (file vuoto o danneggiato)")
    stem = raw.rsplit(".", 1)[0][:60] or "file"
    p = c.files.save_bytes(f"{stem}-{datetime.now():%H%M%S}.{'jpg' if ext == 'jpeg' else ext}", data)
    doc = c.files.register(db, org, p.name, raw[:200], summary="caricato dal pannello")
    db.commit()
    return {"name": p.name, "title": doc.title, "url": doc.uri}


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
    # pages, PDFs and pictures open INSIDE the viewer (as an attachment the site draft was downloaded, not shown)
    inline = p.suffix.lower() in (".html", ".htm", ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".txt", ".md")
    return FileResponse(p, filename=p.name, content_disposition_type="inline" if inline else "attachment")
