"""Long jobs NODO takes on while you keep talking: «preparami un piccolo sito di prova», «analizza questo testo»,
«fai una ricerca approfondita su…». Claude Code (your subscription) works in a folder of its own with file and web
tools only: no shell, nothing outside that folder. What it produces is copied into your files and announced in the
conversation (and on Telegram). Nothing is published anywhere."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import UTC, datetime
from pathlib import Path

from nodo.db.models import Message

log = logging.getLogger("nodo.brain")

_jobs: dict[str, asyncio.Task] = {}
_background: set[asyncio.Task] = set()
MAX_FILES, MAX_BYTES = 25, 2_000_000

SYSTEM = """Sei il collaboratore di Gaetano e svolgi un lavoro per lui. Lavori SOLO dentro la cartella corrente: crea qui
i file richiesti (usa nomi chiari). Puoi leggere i file della cartella e cercare sul web; non hai una shell.
Non inventare dati: se non li trovi, scrivilo. Rispondi in italiano.
Quando hai finito, rispondi con un resoconto breve e parlato (massimo 6 frasi, niente elenchi o markdown):
cosa hai fatto, quali file hai creato, cosa resta da decidere a lui."""


def provider_for_work(c):
    for p in c.providers:
        if hasattr(p, "run_agent"):
            return p
    return None


def work_running() -> int:
    return sum(1 for t in _jobs.values() if not t.done())


def start_work(c, org: str, task: str, conversation_id: str | None) -> str:
    job_id = f"{datetime.now():%Y%m%d-%H%M%S}"
    t = asyncio.create_task(run_work(c, org, job_id, task, conversation_id, requested_at=datetime.now(UTC)))
    _jobs[job_id] = t
    _background.add(t)
    t.add_done_callback(_background.discard)
    return job_id


def _collect(workdir: Path, store, session, org: str, job_id: str) -> list[dict]:
    out = []
    files = sorted(p for p in workdir.rglob("*") if p.is_file() and not any(part.startswith(".") for part in p.parts))
    for p in files[:MAX_FILES]:
        if p.stat().st_size > MAX_BYTES:
            continue
        rel = p.relative_to(workdir).as_posix()
        name = f"lavoro-{job_id}-{re.sub(r'[^A-Za-z0-9._-]+', '-', rel)}"
        try:
            dest = store.save_bytes(name, p.read_bytes())
            doc = store.register(session, org, dest.name, rel, summary=f"creato da NODO nel lavoro {job_id}")
            out.append({"title": rel, "url": doc.uri})
        except ValueError:
            continue
    return out


async def run_work(c, org: str, job_id: str, task: str, conversation_id: str | None,
                   requested_at: datetime | None = None) -> str:
    from nodo.brain.background import _after_current_turn
    from nodo.db.session import session_scope
    started = requested_at or datetime.now(UTC)
    workdir = Path(c.files.root) / "lavori" / job_id
    t0 = time.monotonic()
    try:
        agent = provider_for_work(c)
        if agent is None:
            text = "Non ho potuto fare il lavoro: serve l'abbonamento Claude collegato (attiva-cervello.bat)."
        else:
            try:
                report = await agent.run_agent(task, workdir, SYSTEM)
                with session_scope() as db:
                    made = _collect(workdir, c.files, db, org, job_id)
                    db.commit()
                links = "".join(f" {m['title']}: {m['url']}" for m in made[:6])
                text = (f"Ho finito il lavoro ({int(time.monotonic() - t0)} secondi). {report}"
                        + (f"\nFile pronti nella scheda File:{links}" if made else ""))
            except Exception as e:  # a failed job must tell you, not vanish
                log.warning("work %s failed: %s", job_id, e)
                text = f"Il lavoro non è riuscito: {str(e)[:300]}"
        with session_scope() as db:
            if conversation_id:
                await _after_current_turn(db, conversation_id, started)
                db.add(Message(conversation_id=conversation_id, role="nodo", content=text))
            db.commit()
        n = c.notifier
        if n is not None and getattr(n, "enabled", False):
            await n.send(f"NODO · {text[:3000]}")
        return text
    except Exception:
        log.exception("work job crashed")
        return ""
    finally:
        _jobs.pop(job_id, None)


async def drain() -> None:
    while _background:
        await asyncio.gather(*list(_background))
