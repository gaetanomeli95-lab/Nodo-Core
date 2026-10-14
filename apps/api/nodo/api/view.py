"""Files from Gaetano's own folders, shown on screen (see nodo/media.py). The random token in the address IS the
permission: it is created only when Maria shows that file, and it expires."""
from __future__ import annotations

import mimetypes

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from nodo.media import resolve

router = APIRouter(prefix="/api/v1/view")


@router.get("/{token}/{name}/preview")
def preview(token: str, name: str):
    p = resolve(token)
    if p is None:
        raise HTTPException(404, "il file non è più disponibile: chiedi di nuovo di mostrarlo")
    from nodo.services.documents import extract_text
    if p.suffix.lower() in (".docx", ".xlsx", ".pptx", ".pdf"):
        text = extract_text(p)
    else:
        text = p.read_text(encoding="utf-8", errors="replace")
    return {"name": p.name, "text": text[:60000], "truncated": len(text) > 60000}


@router.get("/{token}/{name}")
def view(token: str, name: str):
    p = resolve(token)
    if p is None:
        raise HTTPException(404, "il file non è più disponibile: chiedi di nuovo di mostrarlo")
    media = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    return FileResponse(p, media_type=media, headers={"Cache-Control": "private, max-age=3600"})
