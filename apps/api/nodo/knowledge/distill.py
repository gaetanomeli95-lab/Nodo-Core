"""Distillation: imported conversations -> durable facts about Gaetano, his clients and projects.

Runs in the background after an import, newest conversations first, one model call per conversation, with an
honest progress counter on the `KnowledgeSource`. Facts are saved as `MemoryFact(status=INFERRED,
source_type="document", source_id=<Document.id>)`: NODO can always say where it learned something. Near
duplicates of facts already in memory are skipped; nothing already known is overwritten.

The model is the same router the brain uses. The deterministic fallback cannot extract facts: in that case the
job stops with status `no_model` instead of pretending.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime

from rapidfuzz import fuzz
from sqlalchemy import select

from nodo.db.models import Document, FactStatus, KnowledgeChunk, KnowledgeSource, MemoryFact, Sensitivity
from nodo.memory.service import ACTIVE
from nodo.providers.base import ChatMessage
from nodo.providers.router import NoEligibleModel, RouteRequest

log = logging.getLogger("nodo.knowledge")

_jobs: dict[str, asyncio.Task] = {}
_waiting: set[str] = set()       # running but paused: Gaetano is talking (old chats are read when it is quiet)
MAX_CHARS = 9000          # per conversation sent to the model (head + tail when longer)
MAX_FACTS = 8             # per conversation
_KINDS = {"preferenza": "preference", "fatto": "semantic", "progetto": "semantic", "decisione": "decision",
          "persona": "semantic"}

PROMPT = """Leggi questa conversazione tra Gaetano (freelance siciliano che gestisce siti web, progetti e contenuti
per i suoi clienti) e un assistente AI. Estrai SOLO ciò che vale la pena ricordare in modo duraturo su Gaetano,
sui suoi clienti, progetti, strumenti, abitudini e decisioni. Niente di generico, niente contenuto dell'assistente
spacciato per fatto, niente dettagli effimeri (errori di codice risolti, saluti, richieste una tantum).

Rispondi SOLO con un array JSON, al massimo {max_facts} elementi, anche vuoto []. Ogni elemento:
{{"about": "Gaetano" oppure il nome del cliente/progetto, "fact": "frase breve in italiano, al presente, autonoma",
"kind": "preferenza" | "fatto" | "progetto" | "decisione" | "persona", "confidence": numero tra 0 e 1}}

Conversazione «{title}» del {when}:
{body}"""


def running(source_id: str) -> bool:
    t = _jobs.get(source_id)
    return t is not None and not t.done()


def _quiet(settings) -> bool:
    """Reading old chats is night work, or work for when Gaetano has not spoken for 15 minutes."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import func, select

    from nodo.db.models import Message
    from nodo.db.session import session_scope
    from nodo.night.briefing import tzinfo
    now = datetime.now(UTC)
    if 1 <= now.astimezone(tzinfo(settings.timezone)).hour < 7:
        return True
    with session_scope() as db:
        last = db.scalar(select(func.max(Message.created_at)))
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return now - last > timedelta(minutes=15)


def resume_pending(c) -> list[str]:
    """Studying must survive a restart: a reading left half done (NODO closed, PC asleep, quota finished) starts
    again by itself. Before, the job lived only in memory: NODO restarted at 14:18 and for a whole night the panel
    said «distilling» while nothing was read, and Maria told Gaetano it was going on."""
    from nodo.db.session import session_scope
    started = []
    with session_scope() as db:
        rows = db.scalars(select(KnowledgeSource).where(KnowledgeSource.status.in_(("distilling", "partial")))).all()
        ids = [r.id for r in rows if not running(r.id) and _pending_documents(db, r.id, 1)]
    for sid in ids:
        start(c, sid)
        started.append(sid)
    return started


_progress_cache: dict[str, tuple[float, dict | None]] = {}


def progress(db, org: str, max_age_s: float = 300) -> dict | None:
    """Honest numbers about the old chats: read, to read, facts learned, last reading, running now or paused.
    Counting what is left reads every chunk: cached for a few minutes (the lavagna asks every minute)."""
    import time
    hit = _progress_cache.get(org)
    if hit and time.monotonic() - hit[0] < max_age_s:
        p = hit[1]
        if p is not None:          # running / paused may change at any moment: those are cheap, always fresh
            ids = db.scalars(select(KnowledgeSource.id).where(KnowledgeSource.organization_id == org)).all()
            run = [i for i in ids if running(i)]
            p = {**p, "running": bool(run), "waiting": bool(run) and all(i in _waiting for i in run)}
        return p
    p = _progress(db, org)
    _progress_cache[org] = (time.monotonic(), p)
    return p


def _progress(db, org: str) -> dict | None:
    rows = db.scalars(select(KnowledgeSource).where(KnowledgeSource.organization_id == org,
                                                    KnowledgeSource.kind.in_(("chatgpt", "claude")))).all()
    if not rows:
        return None
    total = done = facts = 0
    last = None
    live: str | None = None
    paused_reason = None
    for r in rows:
        st = r.stats or {}
        if not st.get("chunks") and not st.get("distilled"):
            continue                                   # a second look at the same file that found nothing new
        left = len(_pending_documents(db, r.id, 100_000))
        d = int(st.get("distilled", 0))
        total += d + left
        done += d
        facts += int(st.get("facts", 0))
        at = r.updated_at
        if at is not None and (last is None or at > last):
            last = at
        if running(r.id):
            live = "attesa" if r.id in _waiting and live is None else "lettura"
        elif left and r.status in ("partial", "no_model", "failed"):
            paused_reason = r.note
    if not total:
        return None
    return {"read": done, "total": total, "left": total - done, "facts": facts, "last_at": last,
            "running": live is not None, "waiting": live == "attesa", "note": paused_reason}


def start(c, source_id: str) -> asyncio.Task:
    if running(source_id):
        return _jobs[source_id]
    task = asyncio.create_task(run(c, source_id))
    _jobs[source_id] = task
    return task


async def run(c, source_id: str) -> dict:
    from nodo.db.session import session_scope
    settings = c.settings
    stats: dict = {}
    with session_scope() as db:
        src = db.get(KnowledgeSource, source_id)
        if src is None:
            return {}
        src.status, stats = "distilling", dict(src.stats or {})
        docs = _pending_documents(db, source_id, int(getattr(settings, "knowledge_distill_max", 300)))
    status, note = "done", None
    try:
        pause = 0.0 if settings.env == "TEST" else float(getattr(settings, "knowledge_distill_pause", 20.0))
        for n, doc_id in enumerate(docs):
            if n and pause:
                # slow and steady: hundreds of chats must not eat the free quota Maria needs to talk to Gaetano
                # (today they did: Gemini hit 429 and every answer fell back to the slow brain)
                await asyncio.sleep(pause)
                while settings.env != "TEST" and not _quiet(settings):
                    _waiting.add(source_id)
                    await asyncio.sleep(120)
                _waiting.discard(source_id)
            with session_scope() as db:
                src = db.get(KnowledgeSource, source_id)
                if src is None or src.status == "stopped":
                    return stats
                core = c.core(db)
                doc = db.get(Document, doc_id)
                if doc is None:
                    continue
                body = _body(db, doc_id)
                try:
                    facts = await extract(core, doc, body, max_facts=MAX_FACTS)
                except NoModel as e:
                    status, note = "no_model", str(e)
                    break
                except NoEligibleModel as e:
                    status = "partial" if stats.get("distilled") else "no_model"
                    note = ("nessun modello disponibile per leggere le conversazioni: collega Claude, ChatGPT, "
                            f"Gemini o Groq ({str(e)[:160]})")
                    break
                saved, skipped = save_facts(core, doc, facts)
                stats["distilled"] = stats.get("distilled", 0) + 1
                stats["facts"] = stats.get("facts", 0) + saved
                stats["skipped"] = stats.get("skipped", 0) + skipped
                doc.summary = _mark_distilled(doc.summary)
                src.stats = dict(stats)
                src.updated_at = datetime.now(UTC)
    except Exception as e:  # the job must leave an honest status behind
        log.exception("knowledge distillation failed")
        status, note = "failed", f"{type(e).__name__}: {str(e)[:200]}"
    with session_scope() as db:
        src = db.get(KnowledgeSource, source_id)
        if src is not None:
            left = len(_pending_documents(db, source_id, 10_000))
            src.status = status if not (status == "done" and left) else "partial"
            src.note = note or (f"{left} conversazioni ancora da distillare" if left else None)
            src.stats = {**stats, "remaining": left}
    return stats


class NoModel(RuntimeError):
    pass


async def extract(core, doc: Document, body: str, *, max_facts: int = MAX_FACTS) -> list[dict]:
    when = (doc.summary or "").split("] ", 1)[-1].split(" — ", 1)[0] if doc.summary else "data sconosciuta"
    prompt = PROMPT.format(max_facts=max_facts, title=doc.title, when=when, body=body)
    req = RouteRequest(purpose="memory", sensitivity=Sensitivity.INTERNAL, min_reasoning=2,
                       prefer_fast=True, background=True)   # an API model: hundreds of chats must not run Claude on the small PC
    text = ""
    async for ch in core.router.stream([ChatMessage("user", prompt)], req, request_id=f"knowledge-{doc.id[:8]}",
                                       temperature=0.1, max_tokens=900):
        text += ch.delta
    d = core.router.last_decision
    if d and d.descriptor.provider == "deterministic":
        raise NoModel("nessun modello configurato per leggere le conversazioni: collega Claude, ChatGPT, Gemini o Groq")
    return parse_facts(text)[:max_facts]


def parse_facts(text: str) -> list[dict]:
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    out = []
    for it in data if isinstance(data, list) else []:
        if not isinstance(it, dict) or not str(it.get("fact", "")).strip():
            continue
        try:
            conf = max(0.0, min(1.0, float(it.get("confidence", 0.7))))
        except (TypeError, ValueError):
            conf = 0.7
        out.append({"about": str(it.get("about") or "Gaetano").strip()[:200], "fact": str(it["fact"]).strip()[:600],
                    "kind": str(it.get("kind") or "fatto").lower().strip(), "confidence": conf})
    return out


def save_facts(core, doc: Document, facts: list[dict]) -> tuple[int, int]:
    """Writes new facts with provenance; skips near-duplicates (>= 90 token-set similarity) and low confidence."""
    saved = skipped = 0
    for f in facts:
        if f["confidence"] < 0.45:
            skipped += 1
            continue
        subject_type, subject_id = "user", core.user_id or core.org
        about = f["about"]
        if about and about.lower() not in ("gaetano", "utente", "user", "io", "me"):
            hit = core.resolver.best(about)
            if hit is not None:
                subject_type, subject_id = hit.entity_type, hit.entity_id
            else:
                f["fact"] = f"{about}: {f['fact']}" if about.lower() not in f["fact"].lower() else f["fact"]
        if _duplicate(core.s, core.org, subject_type, subject_id, f["fact"]):
            skipped += 1
            continue
        core.memory.remember(subject_type, subject_id, f["fact"], layer=_KINDS.get(f["kind"], "semantic"),
                             source_type="document", source_id=doc.id, confidence=f["confidence"],
                             status=FactStatus.INFERRED, sensitivity=Sensitivity(doc.sensitivity))
        saved += 1
    return saved, skipped


def _duplicate(s, org: str, subject_type: str, subject_id: str, content: str) -> bool:
    rows = s.scalars(select(MemoryFact.content).where(
        MemoryFact.organization_id == org, MemoryFact.subject_type == subject_type,
        MemoryFact.subject_id == subject_id, MemoryFact.status.in_(ACTIVE))).all()
    low = content.lower()
    return any(fuzz.token_set_ratio(low, r.lower()) >= 90 for r in rows)


def _pending_documents(db, source_id: str, limit: int) -> list[str]:
    from sqlalchemy import func
    q = (select(KnowledgeChunk.document_id, Document.summary, func.max(KnowledgeChunk.happened_at).label("at"))
         .join(Document, Document.id == KnowledgeChunk.document_id)
         .where(KnowledgeChunk.source_id == source_id).group_by(KnowledgeChunk.document_id, Document.summary)
         .order_by(func.max(KnowledgeChunk.happened_at).desc()))   # newest conversations first
    return [doc_id for doc_id, summary, _ in db.execute(q) if "✓" not in (summary or "")][:limit]


def _body(db, doc_id: str) -> str:
    parts = db.scalars(select(KnowledgeChunk.text).where(KnowledgeChunk.document_id == doc_id)
                       .order_by(KnowledgeChunk.seq)).all()
    text = "\n".join(parts)
    if len(text) <= MAX_CHARS:
        return text
    head = MAX_CHARS * 2 // 3
    return text[:head] + "\n[…]\n" + text[-(MAX_CHARS - head):]


def _mark_distilled(summary: str | None) -> str:
    s = summary or ""
    return s if "✓" in s else s + " ✓"
