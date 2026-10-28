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
import time
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
_FOREIGN_CHARS = re.compile(r"[\u3040-\u30ff\uac00-\ud7af\u4e00-\u9fff\u0400-\u04ff\u0600-\u06ff\u0900-\u097f]")
_EN_ES = re.compile(r"\b(the|you(r|rs)?|i'?m|i'?ll|here'?s|sure|please|thank(s| you)|let me|hola|gracias|"
                    r"est[aá]|usted|porque|entonces|aqu[ií]|vamos|claro)\b", re.I)


def _foreign_reply(text: str) -> bool:
    """Maria must speak Italian. A reply with CJK/Cyrillic/Arabic script, or mostly English/Spanish function
    words, is a drifted answer: the loop makes the model say it again in Italian instead of airing it."""
    t = (text or "").strip()
    if len(t) < 15:
        return False
    if _FOREIGN_CHARS.search(t):
        return True
    words = len(re.findall(r"[a-zà-ü']+", t, re.I))
    return words >= 8 and len(_EN_ES.findall(t)) >= max(2, int(words * 0.22))

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
    def __init__(self, core, conv, request_id: str, channel: str = "text", *, directed: bool = False,
                 intent_name: str | None = None):
        self.core, self.conv, self.request_id, self.channel = core, conv, request_id, channel
        self.intent_name = intent_name
        self.s, self.org, self.settings = core.s, core.org, core.settings
        self.provider: str | None = None
        self.model: str | None = None
        self.tools_used: list[str] = []
        self.directed = directed

    # ------------------------------------------------------------------ context
    def snapshot(self, query: str = "", *, max_chars: int = 10000) -> str:
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
            from nodo.ledger import Ledger, due, sentence
            led = Ledger(self.s, self.org, self.settings.ledger_business, today=now.date())
            todo, open_pay = led.pending_work(), led.open_payments()
            if todo:
                lines.append(f"Registro di {self.settings.ledger_business}, da fare: "
                             + "; ".join(f"{sentence(r)} ({r.day})" for r in todo[:6]) + ".")
            if open_pay:
                # the whole list, with the total: «quali sono i sospesi?» is answered straight from here, without a
                # tool round (on the Claude program that round alone is 15 seconds)
                total = sum(due(r) for r in open_pay)
                full = len(open_pay) <= 15
                lines.append(f"Pagamenti sospesi ({len(open_pay)}, in tutto {total:.2f} €"
                             + ("; elenco COMPLETO, rispondi da qui senza strumenti" if full else
                                "; i primi 15, per gli altri usa ledger_open") + "): "
                             + "; ".join(sentence(r) for r in open_pay[:15]) + ".")
            else:
                lines.append(f"Pagamenti sospesi nel registro di {self.settings.ledger_business}: nessuno.")
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
        from nodo.pcscan import context_line
        if pc := context_line():
            lines.append(pc)
        from nodo.knowledge.service import KnowledgeService
        k = KnowledgeService(self.s, self.org).overview()
        if k["conversations"]:
            lines.append(f"Hai accesso a {k['conversations']} vecchie conversazioni di Gaetano con ChatGPT/Claude "
                         f"(search_knowledge) e a {k['facts']} fatti distillati da esse.")
        priority = [mem] if mem else []
        priority += [line for line in lines if line != "\n" + mem]
        selected, used = [], 0
        for line in priority:
            if used + len(line) + 1 <= max_chars:
                selected.append(line)
                used += len(line) + 1
        return "\n".join(selected)

    # ------------------------------------------------------------------ the loop
    async def run(self, text: str) -> AsyncIterator[dict]:
        """Yields UI events (step, approval.requested, token, sentence). Raises BrainUnavailable if no tool-capable
        model produced anything (nothing has been said yet, so the caller can fall back)."""
        rid = self.request_id
        box = Toolbox(self.core, self.conv, rid, text, self.channel)
        from nodo.brain.quick_read import action_answer, answer, match_action, match_read
        context = " ".join(str(m.get("content") or "")[:400]
                           for m in recent_history(self.s, self.conv.id, 6, exclude_request=rid))
        if act := match_action(text, context):
            name, args = act
            yield _ev("step", rid, step=f"tool:{name}", status="running", arguments=args)
            result = await box.call(name, args)
            yield _ev("step", rid, step=f"tool:{name}", status="done", ok="error" not in result)
            if response := action_answer(name, result):
                self.tools_used.append(name)
                self.provider, self.model, self.text = "nodo", "direct-action", response
                from nodo.brain.surfaces import surface_for
                if card := surface_for(name, result):
                    yield _ev("ui.present", rid, **card)
                for sent in sentences(response):
                    yield _ev("token", rid, delta=sent + " ")
                    yield _ev("sentence", rid, text=sent)
                return
        if quick := match_read(text):
            name, args = quick
            yield _ev("step", rid, step=f"tool:{name}", status="running", arguments=args)
            result = await box.call(name, args)
            yield _ev("step", rid, step=f"tool:{name}", status="done", ok="error" not in result)
            if response := answer(name, result):
                self.tools_used.append(name)
                self.provider, self.model, self.text = "nodo", "register-read", response
                from nodo.brain.surfaces import surface_for
                if card := surface_for(name, result):
                    yield _ev("ui.present", rid, **card)
                for sent in sentences(response):
                    yield _ev("token", rid, delta=sent + " ")
                    yield _ev("sentence", rid, text=sent)
                return
        messages: list[dict] = [{"role": "system", "content": build_persona(self.settings, self.snapshot(text))}]
        from nodo.brain.quick_read import recall_query
        if q := recall_query(text):
            # «ti ricordi…», «trova il progetto che si chiama…»: look in the old chats and in what was said BEFORE
            # answering, so «non lo trovo» is never said without looking
            found = []
            try:
                for name, args in (("search_knowledge", {"query": q}), ("search_conversations", {"query": q})):
                    res = await box.call(name, args)
                    for h in (res.get("results") or [])[:4]:
                        found.append(f"- {h.get('title') or h.get('who') or ''} ({h.get('when', '')}): "
                                     f"{str(h.get('snippet') or h.get('text') or '')[:260]}")
            except Exception:  # noqa: BLE001 - a failed search must not stop the answer
                found = []
            messages[0]["content"] += (
                f"\n\nRICERCA GIÀ FATTA PER TE su «{q}» (vecchie chat di ChatGPT e conversazioni con Gaetano):\n"
                + ("\n".join(found) if found else "niente trovato con queste parole: prova altre parole con "
                   "search_knowledge prima di dire che non c'è")
                + "\nUsala per rispondere: nomi e progetti possono avere un nome nuovo (es. un progetto rinominato).")
        if self.directed:
            messages[0]["content"] += ("\nQuesta richiesta è già stata rivolta a te. Rispondi o chiedi un "
                                       "chiarimento breve: non usare il segnale di silenzio.")
        history = recent_history(self.s, self.conv.id, self.settings.brain_history, exclude_request=rid)
        messages += history
        messages.append({"role": "user", "content": text})
        from nodo.brain.toolsets import pick
        recent = " ".join(str(m.get("content") or "")[:300] for m in history[-4:])
        wanted = pick([sp.name for sp in box.specs()], f"{text} {recent}")
        specs = [sp for sp in box.specs() if sp.name in wanted]     # ~half the tokens: answers come sooner
        started = time.monotonic()
        said: list[str] = []
        guarded = False
        lang_guarded = False
        bound_sent: set[str] = set()
        max_steps = max(1, self.settings.brain_max_steps)
        for step in range(max_steps + 1):
            # never keep Gaetano waiting for minutes: after the deadline, answer with what is known
            last = step == max_steps or time.monotonic() - started > self.settings.brain_deadline_s
            # release the database while the model thinks (seconds): background jobs and the night shift can write
            self.s.commit()
            # «voce precisa» (the default) wants the BEST brain even on voice: speed is not worth wrong answers
            # the classified intent shapes the route: data-backed intents favour the fast model, analytical
            # ones raise the reasoning floor; telemetry sees it in purpose (`brain:<intent>`)
            from nodo.providers.router import intent_route
            shape = intent_route(self.intent_name)
            req = RouteRequest(purpose=f"brain:{self.intent_name or 'conversation'}",
                               sensitivity=Sensitivity.INTERNAL,
                               min_reasoning=max(3, int(shape.get("min_reasoning") or 0)),
                               min_coding=int(shape.get("min_coding") or 0),
                               prefer_fast=bool(shape.get("prefer_fast"))
                                   or (self.channel == "voice" and self.settings.voice_engine == "auto"))
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
            if not calls and interim and not lang_guarded and _foreign_reply(interim):
                # English/Spanish slipped in (it happens when the transcript was garbled): say it again in Italian
                lang_guarded = True
                messages.append({"role": "assistant", "content": interim})
                messages.append({"role": "user", "content": "Riscrivi la stessa risposta SOLO in italiano, "
                                                            "stesso significato, una o due frasi."})
                continue
            if interim and _SILENCE.fullmatch(interim):
                interim = "Non ho capito bene la richiesta. Me la ripeti?" if self.directed else ""
                self.silent = not self.directed
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
