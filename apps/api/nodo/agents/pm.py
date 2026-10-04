"""NODO PM: project status, priorities, blockers, deadlines, open decisions. Pure data reasoning."""
from __future__ import annotations

from datetime import UTC, datetime

from nodo.agents.base import AgentContext, AgentReport

STALE_DAYS = 10


class PMAgent:
    name = "pm"
    description = "Project status, priorities, blockers, deadlines, open decisions"

    async def run(self, ctx: AgentContext) -> AgentReport:
        pkg = ctx.package
        rep = AgentReport(self.name)
        if not pkg.projects:
            rep.confidence = "unknown"
            rep.summary_lines.append("Non ho progetti registrati su cui fare il punto.")
            return rep
        now = datetime.now(UTC)
        for p in pkg.projects:
            tasks = [t for t in pkg.tasks if t["project_id"] == p["id"]]
            open_t = [t for t in tasks if t["status"] != "DONE"]
            blocked = [t for t in open_t if t["status"] == "BLOCKED"]
            overdue = [t for t in open_t if t["overdue"]]
            in_prog = [t for t in open_t if t["status"] == "IN_PROGRESS"]
            open_dec = [d for d in pkg.decisions if d["status"] == "open" and d.get("project_id") in (None, p["id"])]
            stale = False
            if p.get("updated_at"):
                upd = datetime.fromisoformat(p["updated_at"])
                upd = upd if upd.tzinfo else upd.replace(tzinfo=UTC)
                stale = (now - upd).days >= STALE_DAYS and not in_prog
            rep.findings[p["id"]] = {"name": p["name"], "open_tasks": len(open_t), "in_progress": len(in_prog),
                                     "blocked": len(blocked), "overdue": len(overdue), "open_decisions": len(open_dec),
                                     "stale": stale, "priority": p["priority"]}
            line = f"{p['name']}: {len(open_t)} attività aperte"
            if in_prog:
                line += f", {len(in_prog)} in corso"
            if blocked:
                line += f", {len(blocked)} bloccate"
                rep.risks.append(f"{p['name']} bloccato: {blocked[0]['title']}"
                                 + (f" ({blocked[0]['blocked_reason']})" if blocked[0].get("blocked_reason") else ""))
            if overdue:
                line += f", {len(overdue)} in ritardo"
                rep.risks.append(f"{p['name']}: '{overdue[0]['title']}' è oltre la scadenza")
            if open_dec and len(pkg.projects) == 1:
                line += f"; {len(open_dec)} decision{'e' if len(open_dec) == 1 else 'i'} apert{'a' if len(open_dec) == 1 else 'e'}"
                rep.recommendations += [f"Decidere: {d['title']}" for d in open_dec[:3]]
            if stale:
                rep.risks.append(f"{p['name']} fermo da più di {STALE_DAYS} giorni")
            rep.summary_lines.append(line + ".")
            if len(pkg.projects) == 1:
                nxt = sorted(open_t, key=lambda t: (t["priority"], t["due_date"] or "9"))[:3]
                if nxt:
                    rep.recommendations.append("Prossime azioni: " + "; ".join(t["title"] for t in nxt))
                if not tasks and not open_dec:
                    rep.confidence = "inferred"
                    rep.freshness_notes.append("Nessuna attività registrata: lo stato potrebbe essere incompleto.")
        return rep
