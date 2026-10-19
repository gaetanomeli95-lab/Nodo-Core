"""NODO builds a website draft (or any written deliverable) by itself, and you watch it being written.

18/10, HostFacility: the voice promised a site for two hours; what reached the Desktop was a box with one line,
and a pull request replaced the home page of Arredo Chef, a site already online. Now a NEW site is always a
separate file in NODO's own folder (never GitHub, never another site), written by the best model available
right now — it streams into the file, so the panel shows the code growing — and opened on screen when ready.
Also the fallback when the Claude subscription has hit its limit: the work is done anyway by another model.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path

from nodo.brain import live_work

log = logging.getLogger("nodo.builder")
_tasks: set[asyncio.Task] = set()

SITE_PROMPT = """Sei un web designer senior. Scrivi UNA pagina web completa e autonoma per: {brief}

Requisiti:
- Un solo file HTML: CSS dentro <style>, eventuale JavaScript dentro <script>. Nessuna libreria esterna, solo
  Google Fonts se serve. Testi in italiano, veri e convincenti (niente lorem ipsum), nome: {name}.
- Sezioni: barra di navigazione, hero grande con titolo, sottotitolo e invito all'azione; servizi (con icone SVG
  disegnate da te); come funziona (passi); perché sceglierci; numeri o garanzie; contatti con un modulo (che non
  invia nulla); footer.
- Design moderno e curato: palette coerente, gradienti leggeri, angoli morbidi, ombre, animazioni discrete allo
  scorrimento, responsive per telefono.
- Rispondi SOLO con il codice, da <!DOCTYPE html> a </html>. Nessun commento prima o dopo."""

DOC_PROMPT = """Svolgi questo lavoro per Gaetano e scrivi il risultato completo in italiano, in Markdown pulito
(titoli, elenchi, tabelle quando servono). Non inventare dati che non hai: se mancano, scrivilo.

Lavoro: {task}"""


def is_site(text: str) -> bool:
    return bool(re.search(r"\b(sito|website|landing|pagina web|home ?page|html|bozza del sito)\b", text or "", re.I))


def start_site(c, org: str, name: str, brief: str, conversation_id: str | None) -> str:
    job_id = f"sito-{datetime.now():%Y%m%d-%H%M%S}"
    workdir = Path(c.files.root) / "lavori" / job_id
    workdir.mkdir(parents=True, exist_ok=True)
    live_work.start(job_id, f"Sito {name}", workdir, kind="sito")
    task = asyncio.create_task(_run_site(c, org, job_id, workdir, name, brief, conversation_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return job_id


async def stream_to_file(c, prompt: str, target: Path, job_id: str, max_tokens: int = 9000) -> tuple[str, str]:
    """The best model available writes into `target` as it thinks (flushed every half second). (text, model)."""
    from nodo.db.models import Sensitivity
    from nodo.db.session import session_scope
    from nodo.providers.base import ChatMessage
    from nodo.providers.router import NoEligibleModel, RouteRequest
    text, last = "", 0.0
    loop = asyncio.get_running_loop()
    for reasoning in (4, 3):
        try:
            with session_scope() as db:
                core = c.core(db)
                # background=True: never wake the Claude program on the PC for this (it answers only at the end,
                # so nothing could be watched, and its weekly limit is what broke 18/10)
                req = RouteRequest(purpose="work", sensitivity=Sensitivity.INTERNAL, min_reasoning=reasoning,
                                   background=True)
                live_work.step(job_id, "scrivo il codice")
                async for ch in core.router.stream([ChatMessage("user", prompt)], req, request_id=job_id,
                                                   temperature=0.5, max_tokens=max_tokens):
                    text += ch.delta or ""
                    if loop.time() - last > 0.5:
                        last = loop.time()
                        target.write_text(text, encoding="utf-8")
                d = core.router.last_decision
                model = f"{d.descriptor.provider}:{d.descriptor.model}" if d else "?"
                if d and d.descriptor.provider == "deterministic":
                    raise NoEligibleModel("solo il modello di riserva senza intelligenza è disponibile")
            target.write_text(text, encoding="utf-8")
            return text, model
        except NoEligibleModel:
            if reasoning == 3:
                raise
    raise NoEligibleModel("nessun modello")


def clean_html(text: str) -> str:
    t = text.strip()
    t = re.sub(r"^```(?:html)?\s*", "", t)
    t = re.sub(r"\s*```\s*$", "", t)
    start = t.lower().find("<!doctype")
    if start < 0:
        start = t.lower().find("<html")
    end = t.lower().rfind("</html>")
    if start >= 0 and end > start:
        t = t[start:end + 7]
    return t


async def _run_site(c, org: str, job_id: str, workdir: Path, name: str, brief: str,
                    conversation_id: str | None) -> None:
    target = workdir / "index.html"
    try:
        text, model = await stream_to_file(c, SITE_PROMPT.format(name=name, brief=brief), target, job_id)
        html = clean_html(text)
        if "<body" not in html.lower() or len(html) < 1500:
            raise RuntimeError("il modello ha scritto una pagina incompleta")
        target.write_text(html, encoding="utf-8")
        url = _publish(c, org, html, name)
        live_work.finish(job_id, True, f"pagina completa ({len(html) // 1024} KB, {html.count(chr(10))} righe)",
                         url=url, view="web")
        await _tell(c, conversation_id, f"La bozza del sito {name} è pronta: è sullo schermo e nella scheda File.")
    except Exception as e:  # noqa: BLE001 - the job must end with the truth, never «ci sto lavorando» forever
        log.warning("site %s failed: %s", job_id, e)
        live_work.finish(job_id, False, f"non riuscito: {str(e)[:200]}")
        await _tell(c, conversation_id, f"Non sono riuscita a fare la bozza del sito {name}: {str(e)[:200]}")


def _publish(c, org: str, html: str, name: str) -> str:
    from nodo.db.session import session_scope
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "sito"
    with session_scope() as db:
        p = c.files.save_bytes(f"{slug}-{datetime.now():%d%m-%H%M}.html", html.encode("utf-8"))
        doc = c.files.register(db, org, p.name, f"Sito {name} (bozza)", summary="bozza di sito creata da NODO")
        db.commit()
        return doc.uri


async def _tell(c, conversation_id: str | None, text: str) -> None:
    from nodo.db.models import Message
    from nodo.db.session import session_scope
    if not conversation_id:
        return
    try:
        with session_scope() as db:
            db.add(Message(conversation_id=conversation_id, role="nodo", content=text))
            db.commit()
    except Exception:  # noqa: BLE001
        log.warning("could not write the job message", exc_info=True)


async def quick_build(c, org: str, job_id: str, task: str, workdir: Path) -> list[dict]:
    """start_work without the Claude program (weekly limit, not installed): another model does it, visibly."""
    from nodo.brain.workbench import _collect
    from nodo.db.session import session_scope
    workdir.mkdir(parents=True, exist_ok=True)
    if is_site(task):
        target = workdir / "index.html"
        text, _ = await stream_to_file(c, SITE_PROMPT.format(name="il progetto", brief=task), target, job_id)
        target.write_text(clean_html(text), encoding="utf-8")
    else:
        target = workdir / "risultato.md"
        text, _ = await stream_to_file(c, DOC_PROMPT.format(task=task), target, job_id, max_tokens=6000)
    with session_scope() as db:
        made = _collect(workdir, c.files, db, org, job_id)
        db.commit()
    return made
