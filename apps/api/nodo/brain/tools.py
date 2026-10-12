"""The brain's hands: what NODO can look up and do while talking with you.

Every tool returns plain JSON-able data. Guard rails live HERE, in code, not in the prompt:
  * `decide` executes a pending approval only if NODO presented it in this conversation AND your current message
    is an explicit yes (or no). With several decisions on the table it must be clear which one you mean.
  * `request_site_change` never publishes: it prepares a branch + PR + preview in the background; publishing is a
    level-4 approval that goes through `decide` (or the Telegram buttons / the panel).
  * Tool output is data. Nothing a tool returns can grant permissions.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from rapidfuzz import fuzz
from sqlalchemy import select

from nodo.brain.extra import ExtraTools
from nodo.db.models import (
    Approval,
    Client,
    ContentItem,
    Decision,
    Document,
    FactStatus,
    FixProposal,
    Incident,
    MemoryFact,
    Message,
    NightRun,
    Project,
    Site,
    Task,
    TaskStatus,
)
from nodo.providers.base import FunctionSpec

log = logging.getLogger("nodo.brain")

_YES = re.compile(r"\b(s[iì]|ok|okay|vai|vada|fallo|falla|fai pure|procedi|applica\w*|approv\w*|pubblic\w*|"
                  r"conferm\w*|certo|esegui\w*|unisci\w*|merg\w*|d'accordo|va bene|perfetto|manda\w*)\b", re.I)
_REJECT = re.compile(r"\b(no|nope|rifiut\w*|scart\w*|annull\w*|lascia (stare|perdere)|chiudi\w*|cestin\w*|"
                     r"non (la |lo |li |le )?(pubblicare|fare|farlo|applicare|unire|procedere))\b", re.I)
_HOLD = re.compile(r"\b(aspetta|fermo|ferma|dopo|più tardi|non ancora|domani)\b", re.I)


def is_yes(text: str) -> bool:
    t = text.strip()
    return bool(_YES.search(t)) and not _REJECT.search(t) and not _HOLD.search(t) \
        and not re.search(r"\bnon\b", t, re.I) and not t.endswith("?")


def is_no(text: str) -> bool:
    return bool(_REJECT.search(text)) and not text.strip().endswith("?")


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


_STR = {"type": "string"}


@dataclass
class BrainTool:
    spec: FunctionSpec
    handler: Callable[..., Awaitable[dict]]


class Toolbox(ExtraTools):
    def __init__(self, core, conv, request_id: str, user_text: str, channel: str = "text"):
        self.core, self.conv, self.request_id, self.user_text, self.channel = core, conv, request_id, user_text, channel
        self.s, self.org, self.settings, self.c = core.s, core.org, core.settings, core.container
        self.bindings: list[dict] = []   # approval.requested events to emit (lets voice/Telegram bind a bare "sì")
        self.tools: dict[str, BrainTool] = {}
        self._register()

    # ------------------------------------------------------------------ registry
    def _add(self, name: str, description: str, params: dict, handler) -> None:
        self.tools[name] = BrainTool(FunctionSpec(name, description, params), handler)

    def _register(self) -> None:
        site_arg = {"site": {**_STR, "description": "nome del sito (opzionale: vuoto = tutti)"}}
        self._add("night_report", "Cosa è successo durante il turno di notte (briefing) e cosa aspetta una decisione.",
                  _obj({}), self.night_report)
        self._add("sites_status", "Stato attuale dei siti seguiti: risposta, problemi aperti, ultimo controllo, "
                                  "repository.", _obj(site_arg), self.sites_status)
        self._add("check_sites", "Fa SUBITO un giro di controllo rapido dei siti (o di uno) e dice com'è andata. "
                                 "Richiede fino a un paio di minuti.", _obj(site_arg), self.check_sites)
        self._add("sync_sites", "Importa o aggiorna i siti seguiti dai progetti Vercel collegati (come il pulsante "
                                "«Importa da Vercel»). Usala quando chiede di importare/sincronizzare i siti o "
                                "quali progetti hai su Vercel.", _obj({}), self.sync_sites)
        self._add("pending_decisions", "Le decisioni che aspettano l'ok di Gaetano (pubblicare correzioni o modifiche, "
                                       "rollback...), con anteprime.", _obj({}), self.pending_decisions)
        self._add("decide", "Esegue (approve=true) o rifiuta (approve=false) UNA decisione in attesa che hai già "
                            "presentato, solo se Gaetano ha appena detto chiaramente sì o no.",
                  _obj({"approval_id": _STR, "approve": {"type": "boolean"}}, ["approval_id", "approve"]), self.decide)
        self._add("request_site_change", "Prepara una modifica a un sito chiesta da Gaetano (testi, contatti, stile, "
                                         "piccole funzioni): branch + pull request + anteprima Vercel. Lavora in "
                                         "background e NON pubblica: la pubblicazione aspetta il suo ok.",
                  _obj({"site": _STR, "instruction": {**_STR, "description": "la modifica, chiara e completa, con i "
                                                                            "testi esatti se li ha detti"}},
                       ["site", "instruction"]), self.request_site_change)
        self._add("projects", "Elenco dei progetti, oppure il punto completo su uno (attività, decisioni, note, siti).",
                  _obj({"name": {**_STR, "description": "progetto o cliente (opzionale)"}}), self.projects)
        self._add("today_agenda", "Priorità di oggi: attività aperte ordinate per urgenza più il riassunto della "
                                  "notte.", _obj({}), self.today_agenda)
        self._add("create_task", "Crea un'attività (promemoria di lavoro).",
                  _obj({"title": _STR, "project": _STR, "due_date": {**_STR, "description": "AAAA-MM-GG"},
                        "priority": {"type": "integer", "description": "1 (massima) .. 5"}}, ["title"]),
                  self.create_task)
        self._add("complete_task", "Segna come fatta un'attività aperta (cercata per titolo).",
                  _obj({"task": _STR}, ["task"]), self.complete_task)
        self._add("remember", "Salva nella memoria di NODO un fatto o una preferenza da ricordare.",
                  _obj({"fact": _STR, "about": {**_STR, "description": "progetto o cliente a cui si riferisce "
                                                                        "(vuoto = Gaetano)"}}, ["fact"]),
                  self.remember)
        self._add("recall", "Cerca nella memoria di NODO (fatti, preferenze, note su clienti e progetti).",
                  _obj({"query": _STR}, ["query"]), self.recall)
        self._add("search_knowledge", "Cerca nelle vecchie conversazioni di Gaetano con ChatGPT e Claude che ha "
                                      "importato in NODO (idee, progetti, decisioni, come ha fatto una cosa). "
                                      "Usalo quando chiede «ricordi quando...», «cosa avevo deciso su...», o per "
                                      "un contesto che non hai in memoria.",
                  _obj({"query": {**_STR, "description": "parole chiave, non una frase intera"}},
                       ["query"]), self.search_knowledge)
        self._add("read_knowledge", "Legge per intero il passaggio di una vecchia conversazione trovato con "
                                    "search_knowledge (chunk_id).", _obj({"chunk_id": _STR}, ["chunk_id"]),
                  self.read_knowledge)
        self._add("save_draft", "Salva una bozza di contenuto (post, articolo, email...) per un cliente o progetto.",
                  _obj({"title": _STR, "body": _STR, "client": _STR, "project": _STR, "kind": _STR},
                       ["title", "body"]), self.save_draft)
        self._add("web_search", "Cerca su internet (Google) e risponde con le fonti. Usalo per notizie, prezzi, "
                                "documentazione, «cercami...», o qualunque cosa non sia già in memoria.",
                  _obj({"query": {**_STR, "description": "cosa cercare, con parole chiave precise"}},
                       ["query"]), self.web_search)
        self._add("create_file", "Crea un file per Gaetano (testo, markdown, csv, html, svg, json...) e lo mette a "
                                 "disposizione per scaricarlo. Usala quando chiede un documento, un riepilogo "
                                 "scritto, una tabella, una pagina.",
                  _obj({"name": {**_STR, "description": "nome file con estensione, es. report.md"},
                        "content": {**_STR, "description": "contenuto completo del file"},
                        "title": {**_STR, "description": "titolo breve (opzionale)"}}, ["name", "content"]),
                  self.create_file)
        self._add("generate_image", "Genera un'immagine da una descrizione (logo, illustrazione, bozza grafica) e "
                                    "la salva tra i file. Usala quando chiede «crea/disegna/fammi un'immagine».",
                  _obj({"description": {**_STR, "description": "descrizione dettagliata dell'immagine"}},
                       ["description"]), self.generate_image)
        self._register_extra()

    def specs(self) -> list[FunctionSpec]:
        return [t.spec for t in self.tools.values()]

    async def call(self, name: str, args: dict) -> dict:
        tool = self.tools.get(name)
        if tool is None:
            return {"error": f"strumento sconosciuto: {name}"}
        try:
            clean = {k: v for k, v in (args or {}).items() if v not in (None, "")}
            return await tool.handler(**clean)
        except TypeError as e:
            return {"error": f"parametri non validi per {name}: {e}"}
        except Exception as e:  # a failing tool must never break the conversation
            log.exception("brain tool %s failed", name)
            return {"error": f"{type(e).__name__}: {str(e)[:300]}"}

    # ------------------------------------------------------------------ helpers
    def _tz(self):
        from nodo.night.briefing import tzinfo
        return tzinfo(self.settings.timezone)

    def _clock(self, d: datetime | None) -> str | None:
        if d is None:
            return None
        d = d if d.tzinfo else d.replace(tzinfo=UTC)
        local = d.astimezone(self._tz())
        today = datetime.now(self._tz()).date()
        if local.date() == today:
            return f"oggi {local:%H:%M}"
        if local.date() == today - timedelta(days=1):
            return f"ieri {local:%H:%M}"
        return f"{local:%d/%m %H:%M}"

    def _sites(self) -> list[Site]:
        return list(self.s.scalars(select(Site).where(Site.organization_id == self.org, Site.active.is_(True))
                                   .order_by(Site.name)))

    def find_sites(self, name: str | None) -> list[Site]:
        sites = self._sites()
        if not name:
            return sites
        q = name.lower().strip()
        scored = []
        for x in sites:
            keys = [x.name, x.vercel_project_name or "", x.url.split("//")[-1].split("/")[0], *(x.aliases or [])]
            if x.project_id and (p := self.s.get(Project, x.project_id)):
                keys += [p.name, *(p.aliases or [])]
            best = max((max(fuzz.token_set_ratio(q, k.lower()), fuzz.partial_ratio(q, k.lower()) if len(q) >= 4 else 0)
                        for k in keys if k), default=0)
            if best >= 75:
                scored.append((best, x))
        scored.sort(key=lambda t: -t[0])
        if scored and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 8):
            return [scored[0][1]]
        return [x for _, x in scored]

    def _presented(self) -> list[str]:
        return list((self.conv.active_context or {}).get("presented_approvals", []))

    def _present(self, pending: list[dict]) -> None:
        """Record that these decisions were shown in this conversation and bind the first one for a bare 'sì'."""
        if not pending:
            return
        ids = [p["approval_id"] for p in pending]
        ctx = dict(self.conv.active_context or {})
        ctx["presented_approvals"] = (ids + [i for i in ctx.get("presented_approvals", []) if i not in ids])[:8]
        self.conv.active_context = ctx
        self.s.flush()
        from nodo.night.assistant import _bind
        if (b := _bind(pending)) and b not in self.bindings:
            self.bindings.append(b)

    def _decision_view(self, p: dict) -> dict:
        view = {k: p.get(k) for k in ("approval_id", "label", "site", "preview_status", "preview_url", "pr_url")
                if p.get(k) is not None}
        if p.get("fix_id") and (fix := self.s.get(FixProposal, p["fix_id"])):
            view["what_changes"] = fix.diagnosis
            view["files"] = [f"{f['path']} (+{f['added']}/-{f['removed']})" for f in fix.files or []]
            view["diff"] = (fix.diff or "")[:1500]
            view["model_confidence"] = fix.confidence
        return view

    # ------------------------------------------------------------------ night shift / sites
    async def sync_sites(self) -> dict:
        """Import Vercel projects as monitored sites (same as the Siti page button)."""
        if self.c is None or self.c.vercel is None:
            return {"error": "Vercel non collegato: inserisci il token nella scheda Accessi."}
        created = await self.c.night_shift(self.s).discover()
        return {"new_sites": [x.name for x in created], "sites": [x.name for x in self._sites()],
                "note": "importati i nuovi progetti da Vercel" if created
                        else "nessun nuovo progetto: i siti erano già sincronizzati"}

    async def night_report(self) -> dict:
        from nodo.night import briefing as B
        b = B.latest_or_live(self.s, self.org, self.settings)
        self._present(b.pending)
        return {"briefing": b.text, "pending_decisions": [self._decision_view(p) for p in b.pending]}

    async def sites_status(self, site: str | None = None) -> dict:
        sites = self.find_sites(site)
        if site and not sites:
            return {"error": f"nessun sito corrisponde a «{site}»", "sites": [x.name for x in self._sites()]}
        out = []
        for x in sites:
            incs = self.s.scalars(select(Incident).where(Incident.site_id == x.id,
                                                         Incident.status.in_(("open", "mitigated")))).all()
            fixes = self.s.scalars(select(FixProposal).where(FixProposal.site_id == x.id,
                                                             FixProposal.status == "proposed")).all()
            out.append({"name": x.name, "url": x.url, "status": x.status, "note": x.status_note,
                        "ready_to_publish": [f"{'correzione' if f.incident_id else 'modifica'}: {f.title}"
                                             + (f" (PR #{f.pr_number}, in attesa del tuo ok)" if f.pr_number else "")
                                             for f in fixes],
                        "last_checked": self._clock(x.last_checked_at),
                        "repo": f"{x.repo_owner}/{x.repo_name}" if x.repo_name else None,
                        "open_problems": [{"title": i.title, "severity": i.severity, "status": i.status} for i in incs]})
        if not out:
            return {"sites": [], "note": "Nessun sito ancora: serve il token Vercel (pannello Accessi) oppure "
                                         "aggiungerli dal pannello Siti."}
        return {"sites": out}

    async def check_sites(self, site: str | None = None) -> dict:
        from nodo.night import briefing as B
        from nodo.night.assistant import night_lines
        if site and not self.find_sites(site):
            return {"error": f"nessun sito corrisponde a «{site}»", "sites": [x.name for x in self._sites()]}
        focus = self.find_sites(site)
        text = " ".join(x.name for x in focus) if site and len(focus) == 1 else ""
        intent = SimpleNamespace(name="run_checks", text=text, entity=None)
        lines, _ = await night_lines(self.core, intent, self.request_id, B)
        return {"result": "\n".join(lines)}

    async def pending_decisions(self) -> dict:
        from nodo.night import briefing as B
        pending = B.pending_decisions(self.s, self.org)
        self._present(pending)
        return {"pending_decisions": [self._decision_view(p) for p in pending]}

    async def decide(self, approval_id: str, approve: bool) -> dict:
        from nodo.night import approvals as A
        from nodo.night import briefing as B
        approve = approve if isinstance(approve, bool) else str(approve).lower() in ("true", "1", "sì", "si", "yes")
        a = self.s.get(Approval, approval_id)
        if a is None or a.organization_id != self.org or a.status != "pending":
            return {"done": False, "error": "questa decisione non esiste o è già stata presa"}
        presented = self._presented()
        if approval_id not in presented:
            return {"done": False, "error": "non l'hai ancora presentata a Gaetano in questa conversazione: prima "
                                            "mostragliela (pending_decisions) e aspetta la sua risposta"}
        if approve and not is_yes(self.user_text):
            return {"done": False, "error": "Gaetano non ha detto un sì esplicito in questo messaggio: chiediglielo"}
        if not approve and not is_no(self.user_text):
            return {"done": False, "error": "Gaetano non ha detto chiaramente di no in questo messaggio: chiediglielo"}
        pending = {p["approval_id"]: p for p in B.pending_decisions(self.s, self.org)}
        on_table = [i for i in presented if i in pending]
        if len(on_table) > 1:
            p = pending.get(approval_id) or {}
            words = [w for w in re.split(r"[\s\-_.]+", (p.get("site") or "").lower()) if len(w) >= 4]
            text = self.user_text.lower()
            if not words or not any(w in text or fuzz.partial_ratio(w, text) >= 88 for w in words):
                return {"done": False, "error": "ci sono più decisioni sul tavolo e non è chiaro a quale si riferisce: "
                                                "chiedi a Gaetano quale (nominando il sito)",
                        "on_table": [self._decision_view(pending[i]) for i in on_table]}
        if self.c is None:
            return {"done": False, "error": "esecuzione non disponibile in questa istanza"}
        label = (pending.get(approval_id) or {}).get("label", f"{a.tool_name}.{a.action}")
        out = await A.resolve(self.s, self.c, approval_id, approve, via=f"brain:{self.channel}")
        ctx = dict(self.conv.active_context or {})
        ctx["presented_approvals"] = [i for i in ctx.get("presented_approvals", []) if i != approval_id]
        self.conv.active_context = ctx
        self.s.flush()
        return {"done": True, "decision": "approvata" if approve else "rifiutata", "what": label,
                "ok": out.get("ok"), "message": out.get("message")}

    async def request_site_change(self, site: str, instruction: str) -> dict:
        from nodo.brain.background import change_running, start_change
        matches = self.find_sites(site)
        if not matches:
            return {"started": False, "error": f"nessun sito corrisponde a «{site}»",
                    "sites": [x.name for x in self._sites()]}
        if len(matches) > 1:
            return {"started": False, "error": "più siti corrispondono: chiedi quale",
                    "candidates": [x.name for x in matches[:5]]}
        x = matches[0]
        if self.c is None or self.c.repo is None:
            return {"started": False, "error": "manca l'accesso a GitHub (NODO_GITHUB_TOKEN): senza non posso "
                                               "preparare modifiche al codice"}
        if not x.repo_name:
            return {"started": False, "error": f"{x.name} non ha un repository GitHub collegato"}
        if change_running(x.id):
            return {"started": False, "error": f"sto già preparando una modifica per {x.name}: aspetta che finisca"}
        since = datetime.now(UTC) - timedelta(hours=3)
        for run in self.s.scalars(select(NightRun).where(NightRun.organization_id == self.org, NightRun.kind == "change",
                                                         NightRun.started_at >= since)):
            prev = (run.summary or {}).get("instruction") or ""
            if (run.summary or {}).get("site") == x.name and fuzz.token_set_ratio(prev.lower(),
                                                                                   instruction.lower()) >= 85:
                fix = self.s.get(FixProposal, run.summary.get("fix_id")) if run.summary.get("fix_id") else None
                if fix is not None and fix.status == "proposed":
                    from nodo.night.briefing import pending_decisions
                    pend = [p for p in pending_decisions(self.s, self.org) if p["approval_id"] == fix.approval_id]
                    self._present(pend)
                    return {"started": False, "already_prepared": True,
                            "decision": self._decision_view(pend[0]) if pend else None,
                            "note": "questa modifica è già pronta e aspetta il suo ok: non rifarla"}
        start_change(self.c, self.org, x.id, instruction.strip(), self.conv.id, self.channel)
        return {"started": True, "site": x.name, "repo": f"{x.repo_owner}/{x.repo_name}",
                "next": "Preparo la modifica su un branch separato con anteprima (di solito 2-6 minuti). Quando è "
                        "pronta la trovi tra le decisioni (e su Telegram, se attivo): pubblico solo con il tuo ok."}

    # ------------------------------------------------------------------ projects / tasks / memory
    def _project(self, name: str | None) -> Project | None:
        if not name:
            return None
        hit = self.core.resolver.best(name, prefer="project")
        if hit is None:
            return None
        if hit.entity_type == "project":
            return self.s.get(Project, hit.entity_id)
        return self.s.scalars(select(Project).where(Project.client_id == hit.entity_id)
                              .order_by(Project.priority)).first()

    def _client(self, name: str | None) -> Client | None:
        if not name:
            return None
        hit = self.core.resolver.best(name, prefer="client")
        if hit and hit.entity_type == "client":
            return self.s.get(Client, hit.entity_id)
        if hit and hit.entity_type == "project" and (p := self.s.get(Project, hit.entity_id)) and p.client_id:
            return self.s.get(Client, p.client_id)
        return None

    def _task_view(self, t: Task, names: dict) -> dict:
        today = datetime.now(self._tz()).date()
        due = t.due_date.date() if t.due_date else None
        return {"title": t.title, "project": names.get(t.project_id), "status": t.status, "priority": t.priority,
                "due": due.isoformat() if due else None, "overdue": bool(due and due < today),
                "blocked_reason": t.blocked_reason}

    async def projects(self, name: str | None = None) -> dict:
        names = {p.id: p.name for p in self.s.scalars(select(Project).where(Project.organization_id == self.org))}
        if name:
            p = self._project(name)
            if p is None:
                return {"error": f"non trovo un progetto o cliente «{name}»", "projects": sorted(names.values())}
            tasks = self.s.scalars(select(Task).where(Task.project_id == p.id, Task.status != TaskStatus.DONE)
                                   .order_by(Task.priority)).all()
            decisions = self.s.scalars(select(Decision).where(Decision.project_id == p.id,
                                                              Decision.status == "open")).all()
            facts = self.core.memory.facts_for("project", p.id, limit=12)
            client = self.s.get(Client, p.client_id) if p.client_id else None
            sites = self.s.scalars(select(Site).where(Site.project_id == p.id)).all()
            return {"project": p.name, "client": client.name if client else None, "kind": p.kind, "status": p.status,
                    "priority": p.priority, "description": p.description,
                    "due": p.due_date.date().isoformat() if p.due_date else None,
                    "open_tasks": [self._task_view(t, names) for t in tasks[:15]],
                    "open_decisions": [d.title for d in decisions],
                    "notes": [f.content for f in facts],
                    "sites": [{"name": x.name, "status": x.status, "url": x.url} for x in sites]}
        rows = self.s.scalars(select(Project).where(Project.organization_id == self.org)
                              .order_by(Project.priority, Project.name)).all()
        out = []
        for p in rows:
            n_open = len(self.s.scalars(select(Task.id).where(Task.project_id == p.id,
                                                              Task.status != TaskStatus.DONE)).all())
            out.append({"name": p.name, "kind": p.kind, "status": p.status, "priority": p.priority,
                        "open_tasks": n_open})
        return {"projects": out}

    async def today_agenda(self) -> dict:
        from nodo.night import briefing as B
        names = {p.id: p.name for p in self.s.scalars(select(Project).where(Project.organization_id == self.org))}
        tasks = self.s.scalars(select(Task).where(Task.organization_id == self.org,
                                                  Task.status != TaskStatus.DONE)).all()
        views = [self._task_view(t, names) for t in tasks]
        today = datetime.now(self._tz()).date().isoformat()
        views.sort(key=lambda v: -((3 if v["overdue"] else 0) + (2 if v["status"] == TaskStatus.BLOCKED else 0)
                                   + (2 if v["due"] == today else 0) + (5 - v["priority"]) * 0.5))
        b = B.latest_or_live(self.s, self.org, self.settings)
        open_items = [i for i in b.items if i["status"] in ("open", "mitigated")]
        if b.pending:
            self._present(b.pending)
        from nodo.pulse import build_pulse
        pulse = build_pulse(self.s, self.org, self.settings)
        from nodo.db.models import Reminder
        rems = self.s.scalars(select(Reminder).where(Reminder.organization_id == self.org,
                                                     Reminder.status == "pending")
                              .order_by(Reminder.remind_at).limit(5)).all()
        return {"today": today, "tasks": views[:10], "open_tasks_total": len(views),
                "reminders": [{"text": r.text, "when": self._clock(r.remind_at)} for r in rems],
                "night": {"sites_checked": b.sites_checked, "open_problems": len(open_items),
                          "pending_decisions": [self._decision_view(p) for p in b.pending]},
                "pulse": pulse}  # shared grounded observations used by the presence UI

    async def create_task(self, title: str, project: str | None = None, due_date: str | None = None,
                          priority: int | None = None) -> dict:
        p = self._project(project) if project else None
        if project and p is None:
            return {"created": False, "error": f"non trovo il progetto «{project}»"}
        due = None
        if due_date:
            try:
                due = datetime.fromisoformat(due_date[:10]).replace(hour=18, tzinfo=self._tz()).astimezone(UTC)
            except ValueError:
                return {"created": False, "error": "data non valida: usa AAAA-MM-GG"}
        t = Task(organization_id=self.org, project_id=p.id if p else None, title=title.strip()[:300],
                 priority=max(1, min(5, int(priority or 3))), due_date=due)
        self.s.add(t)
        self.s.flush()
        self.core.events.emit("task.created", {"task_id": t.id, "title": t.title, "via": "brain"},
                              request_id=self.request_id, organization_id=self.org)
        return {"created": True, "title": t.title, "project": p.name if p else None,
                "due": due_date[:10] if due_date else None, "priority": t.priority}

    async def complete_task(self, task: str) -> dict:
        tasks = self.s.scalars(select(Task).where(Task.organization_id == self.org,
                                                  Task.status != TaskStatus.DONE)).all()
        scored = sorted(((fuzz.token_set_ratio(task.lower(), t.title.lower()), t) for t in tasks),
                        key=lambda x: -x[0])
        if not scored or scored[0][0] < 70:
            return {"done": False, "error": f"non trovo un'attività aperta simile a «{task}»"}
        if len(scored) > 1 and scored[1][0] >= scored[0][0] - 3:
            return {"done": False, "error": "più attività simili: chiedi quale",
                    "candidates": [t.title for _, t in scored[:4]]}
        t = scored[0][1]
        t.status, t.completed_at = TaskStatus.DONE, datetime.now(UTC)
        self.s.flush()
        return {"done": True, "title": t.title}

    async def remember(self, fact: str, about: str | None = None) -> dict:
        subject_type, subject_id, label = "user", self.core.user_id or self.org, "Gaetano"
        if about:
            if p := self._project(about):
                subject_type, subject_id, label = "project", p.id, p.name
            elif cl := self._client(about):
                subject_type, subject_id, label = "client", cl.id, cl.name
        f = self.core.memory.remember(subject_type, subject_id, fact.strip()[:1000], layer="semantic",
                                      source_type="conversation", source_id=self.conv.id,
                                      request_id=self.request_id)
        return {"saved": True, "about": label, "fact_id": f.id}

    async def recall(self, query: str) -> dict:
        facts = self.s.scalars(select(MemoryFact).where(MemoryFact.organization_id == self.org,
                                                        MemoryFact.status.in_((FactStatus.CONFIRMED,
                                                                               FactStatus.INFERRED,
                                                                               FactStatus.UNVERIFIED)),
                                                        MemoryFact.valid_until.is_(None))).all()
        q = query.lower()
        scored = sorted(((max(fuzz.token_set_ratio(q, f.content.lower()), fuzz.partial_ratio(q, f.content.lower())),
                          f) for f in facts), key=lambda x: -x[0])
        hits = [{"fact": f.content, "about": f.subject_type, "recorded": self._clock(f.recorded_at),
                 "source": "vecchie conversazioni importate" if f.source_type == "document" else f.source_type}
                for s, f in scored if s >= 60][:10]
        if hits:
            return {"results": hits}
        from nodo.knowledge.service import KnowledgeService
        if KnowledgeService(self.s, self.org).overview()["chunks"]:
            return {"results": [], "note": "nessun fatto in memoria: prova search_knowledge nelle vecchie conversazioni"}
        return {"results": [], "note": "non ho niente in memoria su questo"}

    async def search_knowledge(self, query: str) -> dict:
        from nodo.knowledge.service import KnowledgeService
        svc = KnowledgeService(self.s, self.org)
        hits = svc.search(query, limit=6)
        if hits:
            return {"results": hits, "note": "estratti: con read_knowledge leggi il passaggio intero"}
        if not svc.overview()["chunks"]:
            return {"results": [], "note": "Gaetano non ha ancora importato conversazioni: può farlo dalla scheda "
                                           "Memoria del pannello (export di ChatGPT o Claude)"}
        return {"results": [], "note": "niente di pertinente nelle conversazioni importate: prova altre parole chiave"}

    async def read_knowledge(self, chunk_id: str) -> dict:
        from nodo.knowledge.service import KnowledgeService
        text = KnowledgeService(self.s, self.org).chunk_text(chunk_id)
        return {"text": text[:6000]} if text else {"error": "passaggio non trovato"}

    async def save_draft(self, title: str, body: str, client: str | None = None, project: str | None = None,
                         kind: str | None = None) -> dict:
        p = self._project(project) if project else None
        cl = self._client(client) if client else (self.s.get(Client, p.client_id) if p and p.client_id else None)
        if cl is not None:
            item = ContentItem(client_id=cl.id, project_id=p.id if p else None, title=title[:300],
                               kind=(kind or "post")[:50], status="draft", body=body)
            self.s.add(item)
            self.s.flush()
            return {"saved": True, "where": "contenuti", "client": cl.name, "title": item.title}
        doc = Document(organization_id=self.org, project_id=p.id if p else None, title=title[:300], kind="draft",
                       summary=body)
        self.s.add(doc)
        self.s.flush()
        return {"saved": True, "where": "documenti (bozza)", "project": p.name if p else None, "title": doc.title}

    # ------------------------------------------------------------------ web & files
    async def web_search(self, query: str) -> dict:
        """Grounded Google search through Gemini; without a key or quota it falls back to DuckDuckGo."""
        from nodo.services.gemini import web_search as gs
        res = await gs(self.settings.gemini_api_key or "", self.settings.gemini_model, query,
                       self.settings.default_language)
        if not res["answer"]:
            return {"answer": None, "sources": res["sources"],
                    "note": res.get("note") or "nessuna risposta: riformula la domanda"}
        return {"answer": res["answer"], "sources": res["sources"],
                "note": "risposta cercata sul web adesso: cita le fonti se le usi"}

    async def create_file(self, name: str, content: str, title: str | None = None) -> dict:
        """Write a text file into the user's files and register it for download."""
        if self.c is None or self.c.files is None:
            return {"error": "archivio file non disponibile"}
        try:
            p = self.c.files.save_text(name, content)
        except ValueError as e:
            return {"error": str(e)}
        doc = self.c.files.register(self.s, self.org, p.name, title or p.name,
                                    summary=f"file creato da NODO ({len(content)} caratteri)")
        return {"created": doc.title, "url": doc.uri,
                "note": "file pronto: il link si apre dal pannello o incollandolo nel browser"}

    async def generate_image(self, description: str) -> dict:
        """Generate an image from a description and save it among the user's files.
        Gemini first; when the quota is gone (or no key) it uses free keyless pollinations.ai."""
        if self.c is None or self.c.files is None:
            return {"error": "archivio file non disponibile"}
        from nodo.services.gemini import generate_image as gi
        data, engine = await gi(self.settings.gemini_api_key or "",
                                self.settings.gemini_image_model, description)
        ext = ".png" if data[:4] == b"\x89PNG" else (".jpg" if data[:2] == b"\xff\xd8" else ".bin")
        from datetime import datetime as _dt
        p = self.c.files.save_bytes(f"immagine-{_dt.now():%Y%m%d-%H%M%S}{ext}", data)
        doc = self.c.files.register(self.s, self.org, p.name, description[:200],
                                    summary=f"immagine generata ({engine}): {description[:400]}")
        return {"created": doc.title, "url": doc.uri, "kind": "image",
                "note": f"immagine salvata ({engine}): il link si apre dal pannello o incollandolo nel browser"}


def recent_history(s, conv_id: str, limit: int, exclude_request: str | None = None) -> list[dict]:
    rows = s.scalars(select(Message).where(Message.conversation_id == conv_id).order_by(Message.created_at.desc())
                     .limit(limit + 1)).all()
    out = [{"role": "user" if m.role == "user" else "assistant", "content": m.content}
           for m in reversed(rows) if m.request_id != exclude_request and m.content and m.role in ("user", "nodo")]
    while out and out[0]["role"] != "user":  # start the transcript on a user turn
        out.pop(0)
    return out[-limit:]


def result_text(data: dict, limit: int = 6000) -> str:
    s = json.dumps(data, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit] + "…(troncato)"
