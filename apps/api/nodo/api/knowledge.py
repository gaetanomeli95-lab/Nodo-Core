"""Personal knowledge (docs/CONOSCENZA.md): import ChatGPT/Claude exports, follow distillation, search."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from nodo.api.deps import auth, container, org_id
from nodo.app import Container
from nodo.db.models import FactStatus, KnowledgeSource, MemoryFact
from nodo.db.session import get_db
from nodo.knowledge import distill
from nodo.knowledge.importers import UnknownExport, read_export
from nodo.knowledge.service import KnowledgeService

log = logging.getLogger("nodo.knowledge")
router = APIRouter(prefix="/api/v1/knowledge", dependencies=[Depends(auth)])
MAX_UPLOAD = 1_500_000_000   # bytes; the official exports are big but bounded


def _source_out(s: KnowledgeSource) -> dict:
    return {"id": s.id, "kind": s.kind, "label": s.label, "status": s.status, "note": s.note,
            "stats": s.stats or {}, "running": distill.running(s.id),
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "updated_at": s.updated_at.isoformat() if s.updated_at else None}


@router.get("")
def overview(db: Session = Depends(get_db), org: str = Depends(org_id)):
    svc = KnowledgeService(db, org)
    facts = db.scalars(select(MemoryFact).where(
        MemoryFact.organization_id == org, MemoryFact.source_type == "document",
        MemoryFact.status.in_((FactStatus.CONFIRMED, FactStatus.INFERRED, FactStatus.UNVERIFIED)))
        .order_by(MemoryFact.recorded_at.desc()).limit(30)).all()
    return {**svc.overview(), "sources": [_source_out(s) for s in svc.sources()],
            "recent_facts": [{"id": f.id, "fact": f.content, "about": f.subject_type, "layer": f.layer,
                              "confidence": f.confidence, "status": f.status} for f in facts]}


class PathIn(BaseModel):
    path: str
    distill: bool = True


def _ingest(data: bytes | Path, filename: str, do_distill: bool, db: Session, org: str, c: Container) -> dict:
    try:
        convs = read_export(data, filename)
    except UnknownExport as e:
        raise HTTPException(400, str(e)) from e
    if not convs:
        raise HTTPException(400, "nel file non ci sono conversazioni leggibili")
    kind = convs[0].kind
    svc = KnowledgeService(db, org)
    src, st = svc.import_conversations(convs, kind=kind, label=filename or f"export {kind}")
    db.commit()
    started = False
    if do_distill and c.settings.knowledge_distill and (st.new or st.updated):
        distill.start(c, src.id)
        started = True
    return {"source": _source_out(db.get(KnowledgeSource, src.id)), "import": st.as_dict(), "distilling": started}


@router.post("/import")
async def import_upload(file: UploadFile = File(...), distill_now: bool = Form(True),
                        db: Session = Depends(get_db), org: str = Depends(org_id), c: Container = Depends(container)):
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "file troppo grande: usa l'import dal percorso sul disco")
    return _ingest(data, file.filename or "", distill_now, db, org, c)


@router.post("/import-path")
async def import_path(body: PathIn, db: Session = Depends(get_db), org: str = Depends(org_id),
                      c: Container = Depends(container)):
    p = Path(body.path.strip().strip('"'))
    if not p.is_file():
        raise HTTPException(404, f"file non trovato: {p}")
    return _ingest(p, p.name, body.distill, db, org, c)   # opened in place: exports can be several GB


@router.get("/sources")
def sources(db: Session = Depends(get_db), org: str = Depends(org_id)):
    return [_source_out(s) for s in KnowledgeService(db, org).sources()]


@router.post("/sources/{source_id}/distill")
async def distill_source(source_id: str, db: Session = Depends(get_db), org: str = Depends(org_id),
                         c: Container = Depends(container)):
    s = db.get(KnowledgeSource, source_id)
    if s is None or s.organization_id != org:
        raise HTTPException(404, "import non trovato")
    if distill.running(source_id):
        return {"started": False, "note": "distillazione già in corso", "source": _source_out(s)}
    if s.status == "stopped":
        s.status = "imported"
        db.commit()
    distill.start(c, source_id)
    return {"started": True, "source": _source_out(s)}


@router.post("/sources/{source_id}/stop")
def stop_source(source_id: str, db: Session = Depends(get_db), org: str = Depends(org_id)):
    s = db.get(KnowledgeSource, source_id)
    if s is None or s.organization_id != org:
        raise HTTPException(404, "import non trovato")
    s.status = "stopped"
    db.commit()
    return {"stopped": True}


@router.delete("/sources/{source_id}")
def delete_source(source_id: str, db: Session = Depends(get_db), org: str = Depends(org_id)):
    if distill.running(source_id):
        raise HTTPException(409, "ferma prima la distillazione")
    if not KnowledgeService(db, org).remove_source(source_id):
        raise HTTPException(404, "import non trovato")
    db.commit()
    return {"deleted": True}


@router.get("/search")
def search(q: str, limit: int = 8, db: Session = Depends(get_db), org: str = Depends(org_id)):
    return {"query": q, "results": KnowledgeService(db, org).search(q, limit=max(1, min(limit, 20)))}
