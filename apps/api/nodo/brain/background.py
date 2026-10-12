"""Work the brain starts and leaves running while you keep talking (e.g. preparing a site change).
When it finishes: a message in the same conversation, a NightAction in the log and, if Telegram is set up,
the decision with the Pubblica/Rifiuta buttons on your phone."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from nodo.db.models import Message, Site

log = logging.getLogger("nodo.brain")

_running: dict[str, asyncio.Task] = {}      # site_id -> task (one change at a time per site)
_background: set[asyncio.Task] = set()


def change_running(site_id: str) -> bool:
    t = _running.get(site_id)
    return t is not None and not t.done()


def start_change(c, org: str, site_id: str, instruction: str, conversation_id: str | None,
                 channel: str = "text") -> asyncio.Task:
    task = asyncio.create_task(run_change(c, org, site_id, instruction, conversation_id, channel,
                                          requested_at=datetime.now(UTC)))
    _running[site_id] = task
    _background.add(task)
    task.add_done_callback(_background.discard)
    return task


def outcome_message(site_name: str, prop, note: str) -> str:
    if prop is None:
        return f"Non ho preparato la modifica per {site_name}: {note}"
    if prop.status == "failed":
        return f"Ho preparato la modifica per {site_name} ma non sono riuscito ad aprire la pull request."
    preview = {"ok": "l'anteprima funziona", "failed": "attenzione: l'anteprima non funziona",
               "protected": "l'anteprima è protetta, aprila tu", "pending": "l'anteprima è ancora in costruzione",
               "unknown": "l'anteprima non è verificata"}.get(prop.preview_status, "")
    where = f" Anteprima: {prop.preview_url}" if prop.preview_url else ""
    pr = f" (PR #{prop.pr_number})" if prop.pr_number else ""
    return (f"La modifica per {site_name} è pronta{pr}: {note.rstrip('.')}. {preview.capitalize()}.{where} "
            "La pubblico? Dimmi sì o no.")


async def run_change(c, org: str, site_id: str, instruction: str, conversation_id: str | None,
                     channel: str = "text", requested_at: datetime | None = None) -> str:
    from nodo.channels.telegram import send_decisions
    from nodo.db.session import session_scope
    text = ""
    started = requested_at or datetime.now(UTC)
    try:
        with session_scope() as db:
            site = db.get(Site, site_id)
            if site is None:
                return ""
            shift = c.night_shift(db)
            prop, note = await shift.propose_change(site, instruction, trigger=channel)
            text = outcome_message(site.name, prop, note)
            if conversation_id:
                await _after_current_turn(db, conversation_id, started)
                db.add(Message(conversation_id=conversation_id, role="nodo", content=text))
                if prop is not None and prop.approval_id:  # "sì" in this conversation may now publish it
                    from nodo.db.models import Conversation
                    conv = db.get(Conversation, conversation_id)
                    if conv is not None:
                        ctx = dict(conv.active_context or {})
                        ctx["presented_approvals"] = [prop.approval_id] + [
                            i for i in ctx.get("presented_approvals", []) if i != prop.approval_id][:7]
                        conv.active_context = ctx
            db.commit()
            notifier = c.notifier
            if notifier is not None and notifier.enabled:
                sent = 0
                if prop is not None and prop.approval_id:
                    sent = await send_decisions(notifier, db, org, site_id)
                if not sent:
                    await notifier.send(f"NODO · {text}")
            if c.telegram is not None and prop is not None and prop.approval_id:
                import time
                c.telegram.last_presented = (prop.approval_id, time.monotonic())
    except Exception:
        log.exception("site change failed")
    finally:
        _running.pop(site_id, None)
    return text


async def _after_current_turn(db, conversation_id: str, started: datetime, timeout: float = 120) -> None:
    """Write the result AFTER the answer of the turn that asked for it, so the conversation reads in order."""
    from sqlalchemy import select
    for _ in range(int(timeout)):
        last = db.scalars(select(Message).where(Message.conversation_id == conversation_id)
                          .order_by(Message.created_at.desc()).limit(1)).first()
        at = last.created_at if last and last.created_at.tzinfo else (last.created_at.replace(tzinfo=UTC)
                                                                       if last else None)
        if last is None or (last.role == "nodo" and at >= started):
            return
        db.expire_all()
        await asyncio.sleep(1)


async def drain() -> None:
    """Tests: wait for every background job (and what it started) to finish."""
    from nodo.night import approvals as A
    while _background or A._background:
        await asyncio.gather(*list(_background), *list(A._background))
