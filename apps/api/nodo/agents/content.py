"""NODO CONTENT: proposes the next content for a client from real content history and client memory.

Level 2 (PREPARE): it creates a draft ContentItem (internal mutation, no external publication).
"""
from __future__ import annotations

from collections import Counter

from nodo.agents.base import AgentContext, AgentReport
from nodo.db.models import ContentItem

ANGLES = ["commercial", "narrative", "light", "educational"]
ANGLE_IT = {"commercial": "commerciale", "narrative": "narrativo", "light": "leggero", "educational": "educativo"}


class ContentAgent:
    name = "content"
    description = "Content strategy, next content proposals, drafts"

    async def run(self, ctx: AgentContext) -> AgentReport:
        rep = AgentReport(self.name)
        pkg = ctx.package
        if not pkg.clients:
            rep.confidence = "unknown"
            rep.summary_lines.append("Non so per quale cliente preparare il contenuto.")
            return rep
        client = pkg.clients[0]
        history = pkg.content_history
        published = [c for c in history if c["status"] == "published"]
        counts = Counter(c["angle"] for c in published if c.get("angle"))
        # pick the least-used angle, prefer narrative on ties (brand-building default)
        angle = min(ANGLES, key=lambda a: (counts.get(a, 0), ANGLES.index(a) if a != "narrative" else -1))
        recent_titles = [c["title"] for c in history[:3]]
        facts = [f["content"] for f in pkg.facts][:5]
        title = f"{client['name']} - contenuto {ANGLE_IT[angle]}"
        body_lines = [f"Angolo: {ANGLE_IT[angle]}", "Basato su:"]
        body_lines += [f"- memoria: {f}" for f in facts] or ["- nessun fatto in memoria per questo cliente"]
        body_lines += [f"- da non ripetere: {t}" for t in recent_titles]
        item = ContentItem(client_id=client["id"], title=title, kind="video", angle=angle, status="draft",
                           body="\n".join(body_lines), generated_by_run_id=ctx.run_id)
        ctx.session.add(item)
        ctx.session.flush()
        ctx.events.emit("content.draft_created", {"content_id": item.id, "client": client["name"], "angle": angle},
                        request_id=ctx.request_id)
        rep.findings = {"content_id": item.id, "angle": angle, "published_by_angle": dict(counts),
                        "history_size": len(history), "facts_used": len(facts)}
        rep.summary_lines.append(
            f"Per {client['name']} propongo un contenuto {ANGLE_IT[angle]}: "
            + (f"negli ultimi {len(published)} pubblicati l'angolo {ANGLE_IT[angle]} è il meno coperto"
               if published else "non risultano contenuti pubblicati, quindi parto da un formato narrativo")
            + ". Ho salvato una bozza, da sviluppare.")
        if facts:
            rep.summary_lines.append("Spunti dalla memoria: " + "; ".join(facts[:2]) + ".")
        else:
            rep.confidence = "inferred"
            rep.freshness_notes.append("Nessun fatto in memoria sul cliente: la proposta è generica.")
        rep.recommendations.append("Vuoi che sviluppi lo script della bozza?")
        return rep
