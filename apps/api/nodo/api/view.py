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
    elif p.suffix.lower() == ".csv":
        import csv
        import io
        raw = p.read_text(encoding="utf-8-sig", errors="replace")
        dialect = csv.Sniffer().sniff(raw[:4000], delimiters=",;\t") if raw.strip() else csv.excel
        text = "\n".join(" | ".join(row) for row in csv.reader(io.StringIO(raw), dialect))
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


@router.post("/{token}/{name}/open")
def open_on_pc(token: str, name: str):
    """«Aprilo sul PC»: the file opens with its own program (a .mkv with the video player, a .doc with Word)."""
    import os
    import subprocess
    import sys

    from nodo.media import NEVER
    p = resolve(token)
    if p is None:
        raise HTTPException(404, "il file non è più disponibile: chiedi di nuovo di mostrarlo")
    if p.suffix.lower().lstrip(".") in NEVER:
        raise HTTPException(403, "i programmi non si aprono da qui")
    try:
        if os.name == "nt":
            os.startfile(str(p))  # type: ignore[attr-defined]  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        raise HTTPException(500, f"non riesco ad aprirlo: {e}") from e
    return {"opened": p.name}
