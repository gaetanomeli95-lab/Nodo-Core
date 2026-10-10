"""The brain: a real conversation with tools.

One message from you -> up to N rounds of "think, use tools, look at the results" on the best tool-capable model
(your Claude subscription, Gemini, Groq...) -> one natural answer. It remembers the conversation, knows the
current state (sites, projects, pending decisions, what it has learned about you) and acts through
nodo/brain/tools.py, where the safety rules are enforced in code.

If no model with tool calling is available (or it fails before answering), NodoCore falls back to the
rule-based pipeline, so NODO always answers.
"""
from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from sqlalchemy import select

from nodo.brain.persona import PERSONA
from nodo.brain.tools import Toolbox, recent_history, result_text
from nodo.db.models import FactStatus, MemoryFact, Project, Sensitivity, Site
from nodo.night import briefing as B
from nodo.providers.router import NoEligibleModel, RouteRequest

_SENT_END = re.compile(r"(?<=[.!?…])\s+|\n+")
_DAYS = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
_MONTHS = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre",
           "novembre", "dicembre"]
_CHANNEL = {"voice": "voce (la risposta viene letta ad alta voce: niente elenchi, frasi brevi)",
            "telegram": "Telegram dal telefono (risposte brevi)", "text": "chat testuale nel pannello"}


class BrainUnavailable(RuntimeError):
    """No tool-capable model answered: the caller should use the rule-based pipeline."""


def _ev(type: str, request_id: str, **payload) -> dict:
    return {"type": type, "request_id": request_id, "at": datetime.now(UTC).isoformat(), **payload}


def sentences(text: str) -> list[str]:
    out, pending = [], text
    while m := _SENT_END.search(pending):
        sent, pending = pending[: m.end()].strip(), pending[m.end():]
        if sent:
            out.append(sent)
    if pending.strip():
        out.append(pending.strip())
    return out


class Brain:
    def __init__(self, core, conv, request_id: str, channel: str = "text"):
        self.core, self.conv, self.request_id, self.channel = core, conv, request_id, channel
        self.s, self.org, self.settings = core.s, core.org, core.settings
        self.provider: str | None = None
        self.model: str | None = None
        self.tools_used: list[str] = []

    # ------------------------------------------------------------------ context
    def snapshot(self) -> str:
        tz = B.tzinfo(self.settings.timezone)
        now = datetime.now(tz)
        lines = [f"Adesso: {_DAYS[now.weekday()]} {now.day} {_MONTHS[now.month - 1]} {now.year}, ore {now:%H:%M}.",
                 f"Canale: {_CHANNEL.get(self.channel, self.channel)}."]
        sites = self.s.scalars(select(Site).where(Site.organization_id == self.org, Site.active.is_(True))
                               .order_by(Site.name)).all()
        if sites:
            st = {"ok": "ok", "warning": "attenzione", "critical": "PROBLEMA", "unknown": "non controllato"}
            lines.append(f"Siti seguiti ({len(sites)}): " + ", ".join(f"{x.name} [{st.get(x.status, x.status)}]"
                                                                        for x in sites[:25]) + ".")
        else:
            lines.append("Siti seguiti: nessuno ancora (manca il collegamento a Vercel o non sono stati aggiunti).")
        projects = self.s.scalars(select(Project).where(Project.organization_id == self.org,
                                                        Project.status == "active").order_by(Project.priority)).all()
        if projects:
            lines.append("Progetti attivi: " + ", ".join(p.name for p in projects[:20]) + ".")
        pending = B.pending_decisions(self.s, self.org)
        if pending:
            lines.append(f"Decisioni in attesa del suo ok: {len(pending)} (usa pending_decisions per i dettagli).")
        presented = set((self.conv.active_context or {}).get("presented_approvals", []))
        shown = [p for p in pending if p["approval_id"] in presented]
        if shown:
            lines.append("Già presentate in questa conversazione (approval_id: cosa): "
                         + "; ".join(f"{p['approval_id']}: {p['label']}" for p in shown) + ".")
        from nodo.brain.background import _running
        busy = [x.name for x in sites if _running.get(x.id) and not _running[x.id].done()]
        if busy:
            lines.append("Modifiche che stai preparando adesso in background: " + ", ".join(busy) + ".")
        facts = self.s.scalars(select(MemoryFact).where(
            MemoryFact.organization_id == self.org, MemoryFact.subject_type == "user",
            MemoryFact.status.in_((FactStatus.CONFIRMED, FactStatus.INFERRED)), MemoryFact.valid_until.is_(None))
            .order_by(MemoryFact.recorded_at.desc()).limit(15)).all()
        if facts:
            lines.append("Cosa sai di Gaetano: " + " | ".join(f.content for f in facts) + ".")
        return "\n".join(lines)

    # ------------------------------------------------------------------ the loop
    async def run(self, text: str) -> AsyncIterator[dict]:
        """Yields UI events (step, approval.requested, token, sentence). Raises BrainUnavailable if no tool-capable
        model produced anything (nothing has been said yet, so the caller can fall back)."""
        rid = self.request_id
        box = Toolbox(self.core, self.conv, rid, text, self.channel)
        messages: list[dict] = [{"role": "system", "content": PERSONA.format(context=self.snapshot())}]
        messages += recent_history(self.s, self.conv.id, self.settings.brain_history, exclude_request=rid)
        messages.append({"role": "user", "content": text})
        specs = box.specs()
        said: list[str] = []
        bound_sent: set[str] = set()
        max_steps = max(1, self.settings.brain_max_steps)
        for step in range(max_steps + 1):
            last = step == max_steps
            # release the database while the model thinks (seconds): background jobs and the night shift can write
            self.s.commit()
            req = RouteRequest(purpose="brain", sensitivity=Sensitivity.INTERNAL, min_reasoning=3)
            try:
                turn = await self.core.router.complete_tools(messages, [] if last else specs, req, request_id=rid,
                                                             temperature=0.4, max_tokens=1500)
            except NoEligibleModel as e:
                if not said and not self.tools_used:
                    raise BrainUnavailable(str(e)) from e
                said.append("Scusa, ho perso il filo per un problema con il modello: riprova tra un attimo.")
                break
            d = self.core.router.last_decision
            if d:
                self.provider, self.model = d.descriptor.provider, d.descriptor.model
            calls = [] if last else turn.tool_calls
            interim = turn.text.strip()
            if calls and len(interim) > 120:
                interim = ""   # long text next to tool calls is usually repeated in the final answer: keep that one
            if interim:
                # a short "un attimo, controllo" before tools is spoken right away; the final answer closes the turn
                said.append(interim)
                for sent in sentences(interim):
                    yield _ev("token", rid, delta=sent + " ")
                    yield _ev("sentence", rid, text=sent)
            if not calls:
                break
            messages.append({"role": "assistant", "content": interim,
                             "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in calls]})
            for c in calls:
                self.tools_used.append(c.name)
                yield _ev("step", rid, step=f"tool:{c.name}", status="running", arguments=c.arguments)
                result = await box.call(c.name, c.arguments)
                yield _ev("step", rid, step=f"tool:{c.name}", status="done",
                          ok="error" not in result)
                messages.append({"role": "tool", "tool_call_id": c.id, "name": c.name,
                                 "content": result_text(result)})
                for b in box.bindings:
                    if b["approval_id"] not in bound_sent:
                        bound_sent.add(b["approval_id"])
                        self.core.events.emit("approval.requested", b, request_id=rid, organization_id=self.org)
                        yield _ev("approval.requested", rid, **b)
        if not said:
            said.append("Fatto.")
        self.text = " ".join(said).strip()
