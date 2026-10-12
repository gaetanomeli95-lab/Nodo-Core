"""Files NODO creates for you (documents, images, exports): stored under `files/` next to the database,
registered as Document rows, downloadable from /api/v1/files/<name>."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from nodo.db.models import Document

TEXT_KINDS = {"md", "txt", "csv", "json", "html", "svg", "xml", "py", "css", "js", "yaml", "log"}
BINARY_KINDS = {"png", "jpg", "webp"}

_SAFE = re.compile(r"[^a-z0-9._-]+")


def _safe_name(name: str) -> str:
    stem = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    stem = _SAFE.sub("-", stem.lower().replace("\\", "/").split("/")[-1]).strip("-.")
    return stem or "file"


class FileStore:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        p = (self.root / _safe_name(name)).resolve()
        if p.parent != self.root.resolve():
            raise ValueError("nome file non valido")
        return p

    def save_text(self, name: str, content: str) -> Path:
        p = self._path(name)
        if p.suffix.lstrip(".").lower() not in TEXT_KINDS:
            p = p.with_suffix(".md")
        p.write_text(content, encoding="utf-8")
        return p

    def save_bytes(self, name: str, content: bytes) -> Path:
        p = self._path(name)
        if p.suffix.lstrip(".").lower() not in BINARY_KINDS | TEXT_KINDS:
            p = p.with_suffix(".bin")
        p.write_bytes(content)
        return p

    def get(self, name: str) -> Path | None:
        p = self._path(name)
        return p if p.is_file() else None

    def list(self, session: Session, org: str) -> list[dict]:
        docs = session.scalars(select(Document).where(Document.organization_id == org,
                                                      Document.kind == "file")
                               .order_by(Document.created_at.desc()).limit(50)).all()
        return [{"title": d.title, "uri": d.uri, "created_at": d.created_at.isoformat()} for d in docs]

    def register(self, session: Session, org: str, name: str, title: str, summary: str | None = None) -> Document:
        doc = Document(organization_id=org, title=title or name, kind="file",
                       uri=f"/api/v1/files/{Path(name).name}", summary=summary)
        session.add(doc)
        session.flush()
        return doc
