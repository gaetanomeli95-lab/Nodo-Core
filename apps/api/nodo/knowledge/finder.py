"""Finds the ChatGPT/Claude data exports Gaetano downloaded (Desktop, Downloads, OneDrive Desktop), so Maria can
learn them without anyone typing a path. Only the top level of those folders is looked at, only .zip files, and a
zip counts only if it really contains conversations (conversations.json / conversations-000.json …)."""
from __future__ import annotations

import os
import zipfile
from pathlib import Path

from sqlalchemy import select

from nodo.db.models import KnowledgeSource


def _places() -> list[Path]:
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    out = [home / "Desktop", home / "Scrivania", home / "Downloads", home / "Download"]
    od = os.environ.get("OneDrive")
    out += [home / "OneDrive" / "Desktop", home / "OneDrive" / "Scrivania"] + ([Path(od) / "Desktop"] if od else [])
    return [p for p in dict.fromkeys(out) if p.is_dir()]


def is_export(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as z:
            return any(Path(n).name.lower().startswith("conversations") and n.lower().endswith(".json")
                       for n in z.namelist())
    except (zipfile.BadZipFile, OSError):
        return False


def find_exports(places: list[Path] | None = None) -> list[Path]:
    """Newest first."""
    found = []
    for folder in places if places is not None else _places():
        try:
            for p in folder.iterdir():
                if p.suffix.lower() == ".zip" and p.is_file() and is_export(p):
                    found.append(p)
        except OSError:
            continue
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def not_imported(db, org: str, places: list[Path] | None = None) -> list[Path]:
    done = set(db.scalars(select(KnowledgeSource.label).where(KnowledgeSource.organization_id == org)))
    return [p for p in find_exports(places) if p.name not in done]


async def import_found(c, db, org: str, places: list[Path] | None = None) -> dict:
    """Imports every export found and not yet imported; distillation (facts) starts in the background."""
    import asyncio

    from nodo.knowledge import distill
    from nodo.knowledge.importers import UnknownExport, read_export
    from nodo.knowledge.service import KnowledgeService
    todo = not_imported(db, org, places)
    if not todo:
        seen = find_exports(places)
        return {"found": len(seen), "imported": [], "message": (
            "Le chat che ho trovato le conosco già." if seen else
            "Non trovo nessun export di ChatGPT o Claude sul Desktop o in Download.")}
    def work(path: Path):
        # reading a 4 GB zip and writing thousands of chunks takes a while on a small PC: do it in a worker thread
        # with its own session, so the voice keeps running meanwhile
        from nodo.db.session import session_scope
        convs = read_export(path, path.name)
        if not convs:
            return None, None
        with session_scope() as s2:
            src, st = KnowledgeService(s2, org).import_conversations(convs, kind=convs[0].kind, label=path.name)
            s2.commit()
            return src.id, st

    db.commit()
    out = []
    for path in todo:
        try:
            src_id, st = await asyncio.to_thread(work, path)
        except (UnknownExport, OSError) as e:
            out.append({"file": path.name, "error": str(e)[:200]})
            continue
        if st is None:
            continue
        if c.settings.knowledge_distill and (st.new or st.updated):
            distill.start(c, src_id)
        out.append({"file": path.name, **st.as_dict()})
    n = sum(x.get("conversations", 0) for x in out)
    return {"found": len(todo), "imported": out,
            "message": f"Ho importato {n} conversazioni: ora le leggo e ne ricavo i ricordi, piano piano."
            if n else "Ho trovato il file ma non ci sono conversazioni leggibili."}
