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

from nodo.brain.persona import build_persona
from nodo.brain.tools import Toolbox, recent_history, result_text
from nodo.db.models import Project, Sensitivity, Site
from nodo.night import briefing as B
from nodo.providers.router import NoEligibleModel, RouteRequest

_SENT_END = re.compile(r"(?<=[.!?…])\s+|\n+")
_DAYS = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
_MONTHS = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre",
           "novembre", "dicembre"]
_CHANNEL = {"voice": "voce (la risposta viene letta ad alta voce: niente elenchi, frasi brevi)",
            "telegram": "Telegram dal telefono (risposte brevi)", "text": "chat testuale nel pannello"}


_EARLY_DONE = re.compile(r"\s*(ok|okay|va bene|fatto|segnat\w*|registrat\w*|me lo ricordo|ricevuto|capito|perfetto|certo)\b",
                         re.I)
_SILENCE = re.compile(r"\s*\[?\(?\s*silenzio\s*\]?\)?\.?\s*", re.I)

_CLAIMS = [
    (re.compile(r"\b(ho|l'ho|li ho|le ho)\s+(segnat|registrat|annotat|aggiunt|corrett|aggiornat)\w*.{0,40}"
                r"\b(registro|vendit|spes|consegn|credit|sospes)", re.I | re.S),
     ("ledger_add", "ledger_update"), "segnato/corretto nel registro"),
    (re.compile(r"\b(ho|l'ho)\s+(creat|preparat|fatt|salvat|generat)\w*\s+(il|un|la|una|lo)?\s*"
                r"(file|pdf|documento|excel|word|presentazion|immagin|foto)", re.I),
     ("create_file", "create_document", "generate_image"), "creato il file"),
    (re.compile(r"\b(sul|nel)\s+desktop\b", re.I), ("save_to_desktop",), "messo sul desktop"),
    (re.compile(r"\b(ho|l'ho)\s+(appuntat|mess|scritt)\w*.{0,30}\blavagna\b", re.I | re.S), ("board_pin",),
     "appuntato sulla lavagna"),
    (re.compile(r"\b(ho|l'ho)\s+(impostat|programmat|messo)\w*.{0,30}\bpromemoria\b", re.I | re.S),
     ("set_reminder",), "impostato il promemoria"),
]


def unbacked_claim(text: str, used: list[str]) -> str | None:
    """«Ho segnato nel registro», «ho creato il PDF»… without the tool that does it: a promise not kept."""
    for rx, tools, what in _CLAIMS:
        if rx.search(text or "") and not any(t in used for t in tools):
            return (f"[Controllo automatico] Hai scritto di aver {what}, ma non hai usato lo strumento "
                    f"({' / '.join(tools)}). Fallo adesso con lo strumento giusto; se non puoi, dillo chiaramente "
                    "senza far finta di averlo fatto.")
    return None


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
    def snapshot(self, query: str = "") -> str:
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
            lines.append("Siti seguiti: nessuno ancora." + (" Vercel è collegato: puoi importarli con sync_sites."
                         if getattr(self.core.container, "vercel", None) is not None else
                         " Manca il collegamento a Vercel (token nella scheda Accessi)."))
        from nodo.tools.github import HttpGitHubConnector
        access = [name for name, ok in
                  (("GitHub", bool(self.settings.github_token)
                    and isinstance(getattr(self.core.container, "github", None), HttpGitHubConnector)),
                   ("Vercel", getattr(self.core.container, "vercel", None) is not None)) if ok]
        if access:
            lines.append("Accessi collegati e verificati: " + " e ".join(access) + ".")
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
        try:
            from nodo.ledger import Ledger, sentence
            led = Ledger(self.s, self.org, self.settings.ledger_business, today=now.date())
            todo, open_pay = led.pending_work(), led.open_payments()
            if todo:
                lines.append(f"Registro di {self.settings.ledger_business}, da fare: "
                             + "; ".join(f"{sentence(r)} ({r.day})" for r in todo[:6]) + ".")
            if open_pay:
                lines.append("Pagamenti sospesi: " + "; ".join(sentence(r) for r in open_pay[:6]) + ".")
        except Exception:  # noqa: BLE001 - the snapshot must never fail because of the register
            pass
        from nodo.brain.background import _running
        busy = [x.name for x in sites if _running.get(x.id) and not _running[x.id].done()]
        if busy:
            lines.append("Modifiche che stai preparando adesso in background: " + ", ".join(busy) + ".")
        from nodo.memory.mind import memory_context
        mem = memory_context(self.s, self.org, tz, query, self.settings.owner_name, self.settings.assistant_name)
        if mem:
            lines.append("\n" + mem)
        from nodo.knowledge.service import KnowledgeService
        k = KnowledgeService(self.s, self.org).overview()
        if k["conversations"]:
            lines.append(f"Hai accesso a {k['conversations']} vecchie conversazioni di Gaetano con ChatGPT/Claude "
                         f"(search_knowledge) e a {k['facts']} fatti distillati da esse.")
        return "\n".join(lines)

    # ------------------------------------------------------------------ the loop
    async def run(self, text: str) -> AsyncIterator[dict]:
        """Yields UI events (step, approval.requested, token, sentence). Raises BrainUnavailable if no tool-capable
        model produced anything (nothing has been said yet, so the caller can fall back)."""
        rid = self.request_id
        box = Toolbox(self.core, self.conv, rid, text, self.channel)
        messages: list[dict] = [{"role": "system", "content": build_persona(self.settings, self.snapshot(text))}]
        messages += recent_history(self.s, self.conv.id, self.settings.brain_history, exclude_request=rid)
        messages.append({"role": "user", "content": text})
        specs = box.specs()
        said: list[str] = []
        guarded = False
        bound_sent: set[str] = set()
        max_steps = max(1, self.settings.brain_max_steps)
        for step in range(max_steps + 1):
            last = step == max_steps
            # release the database while the model thinks (seconds): background jobs and the night shift can write
            self.s.commit()
            # «voce precisa» (the default) wants the BEST brain even on voice: speed is not worth wrong answers
            req = RouteRequest(purpose="brain", sensitivity=Sensitivity.INTERNAL, min_reasoning=3,
                               prefer_fast=self.channel == "voice" and self.settings.voice_engine == "auto")
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
            if calls and (len(interim) > 120 or _EARLY_DONE.match(interim)):
                # long text next to tool calls is repeated in the final answer, and «ok, fatto» said BEFORE the tool
                # ran is both a double answer («ok… Ok, me lo ricordo») and a claim not yet true: keep the final one
                interim = ""
            if not calls and not last and not guarded and (fix := unbacked_claim(interim, self.tools_used)):
                # it says «fatto» but did not do it: do not let it say so, make it act (or tell the truth)
                guarded = True
                messages.append({"role": "assistant", "content": interim})
                messages.append({"role": "user", "content": fix})
                continue
            if interim and _SILENCE.fullmatch(interim):
                interim = ""                     # it chose to stay quiet (Gaetano is talking to someone else)
                self.silent = True
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
                from nodo.brain.surfaces import surface_for
                if (card := surface_for(c.name, result)) is not None:
                    yield _ev("ui.present", rid, **card)
                messages.append({"role": "tool", "tool_call_id": c.id, "name": c.name,
                                 "content": result_text(result)})
                for b in box.bindings:
                    if b["approval_id"] not in bound_sent:
                        bound_sent.add(b["approval_id"])
                        self.core.events.emit("approval.requested", b, request_id=rid, organization_id=self.org)
                        yield _ev("approval.requested", rid, **b)
        if not said and not getattr(self, "silent", False):
            said.append("Fatto.")
        self.text = " ".join(said).strip()
