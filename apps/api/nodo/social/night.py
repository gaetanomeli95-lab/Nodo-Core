"""Overnight social prep: once a day, if Gaetano has an open task about social content and no draft is
already waiting for his ok, NODO writes one — ready to approve in the morning. Needs a model; without
one it quietly does nothing (the queue simply stays empty)."""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from sqlalchemy import select

from nodo.db.models import Task, TaskStatus

log = logging.getLogger("nodo.social.night")

_SOCIAL_TASK = re.compile(r"social|tiktok|instagram|facebook|post\b|video", re.I)

PROMPT = """Sei Maria, l'assistente di {owner}. Devi preparare UNA bozza di post social da questa attività:
«{task}». Scrivi in italiano, tono naturale e diretto, adatto a {channel}: hook nelle prime parole,
2-4 righe di testo, poi una riga di hashtag (5-10, pertinenti). Niente emoji eccessive, niente testo inventato
su fatti che non conosci. Rispondi SOLO con JSON: {{"title": "titolo breve", "body": "caption completa"}}."""


async def maybe_draft(c, db, org: str, now: datetime | None = None) -> dict | None:
    """Draft one post for the first open social task, once per day. Returns the draft or None."""
    import json

    from nodo.social import service as soc
    now = now or datetime.now(UTC)
    if soc.pending(db, org):
        return None                                # a draft already waits for the ok: do not pile up
    task = db.scalars(select(Task).where(Task.organization_id == org, Task.status != TaskStatus.DONE)
                      .order_by(Task.priority)).all()
    task = next((t for t in task if _SOCIAL_TASK.search(t.title or "")), None)
    if task is None:
        return None
    from nodo.providers.base import ChatMessage
    from nodo.providers.router import RouteRequest
    reply = ""
    try:
        core = c.core(db)
        async for ch in core.router.stream(
                [ChatMessage("user", PROMPT.format(owner=c.settings.owner_name, task=task.title,
                                                   channel="TikTok/Instagram"))],
                RouteRequest(purpose="memory", min_reasoning=2), request_id="social-draft"):
            reply += ch.delta or ""
    except Exception as e:  # noqa: BLE001 - quota or network: try tomorrow, silently
        log.info("social night draft skipped: %s", type(e).__name__)
        return None
    m = re.search(r"\{.*\}", reply, re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        data = {}
    if not data.get("body"):
        return None
    item, a = soc.draft(db, org, title=(data.get("title") or task.title)[:300], body=data["body"],
                        channels=["tiktok", "instagram"], request_id="social-night")
    from nodo.db.models import EventLog
    db.add(EventLog(event_type="social.draft", request_id="social-night", organization_id=org,
                    payload={"item_id": item.id, "task": task.title}))
    return {"item_id": item.id, "approval_id": a.id, "title": item.title, "for_task": task.title}
