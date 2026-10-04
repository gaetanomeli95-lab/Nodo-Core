"""NODO DEV: technical state of repositories via the GitHub tool. Marks freshness honestly."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from nodo.agents.base import AgentContext, AgentReport
from nodo.db.models import Repository


class DevAgent:
    name = "dev"
    description = "Repositories, commits, PRs, issues, CI, deployments"

    async def run(self, ctx: AgentContext) -> AgentReport:
        rep = AgentReport(self.name)
        repos = ctx.package.repositories
        if not repos:
            rep.confidence = "unknown"
            rep.summary_lines.append("Nessun repository collegato a questo progetto: non posso valutarne lo stato tecnico.")
            return rep
        for r in repos:
            res = await ctx.tool("github", "snapshot", owner=r["owner"], name=r["name"], default_branch=r["default_branch"])
            snap = res.output if res.ok else {}
            if res.ok and snap.get("freshness") == "live":
                self._persist(ctx.session, r["id"], snap)
                rep.findings[r["id"]] = snap
                line = (f"{r['owner']}/{r['name']}: {snap['commits_last_7d']} commit negli ultimi 7 giorni, "
                        f"{snap['open_prs']} PR aperte, {snap['open_issues']} issue aperte, CI {snap['ci_status'] or 'sconosciuta'}.")
                if snap.get("last_commit_message"):
                    line += f" Ultimo commit: \"{snap['last_commit_message']}\"."
                rep.summary_lines.append(line)
                if snap.get("ci_status") == "failure":
                    rep.risks.append(f"CI in errore su {r['name']} ({r['default_branch']})")
                if snap["commits_last_7d"] == 0:
                    rep.risks.append(f"{r['name']}: nessun commit negli ultimi 7 giorni")
                if snap["open_prs"] > 0:
                    rep.recommendations.append(f"Rivedere le {snap['open_prs']} PR aperte su {r['name']}")
            else:
                cached = r.get("tech_state") or {}
                err = (snap.get("error") if snap else res.error) or "connettore non disponibile"
                rep.confidence = "inferred" if cached else "unknown"
                if cached:
                    rep.freshness_notes.append(f"{r['name']}: GitHub non raggiungibile, uso l'ultimo snapshot del "
                                               f"{(r.get('last_synced_at') or '?')[:10]}.")
                    rep.summary_lines.append(f"{r['owner']}/{r['name']} (dati non aggiornati): {cached.get('commits_last_7d', '?')} "
                                             f"commit/7gg, {cached.get('open_prs', '?')} PR, CI {cached.get('ci_status', '?')}.")
                else:
                    rep.summary_lines.append(f"{r['owner']}/{r['name']}: stato tecnico non disponibile ({err}).")
        return rep

    @staticmethod
    def _persist(session: Session, repo_id: str, snap: dict) -> None:
        if repo := session.get(Repository, repo_id):
            repo.tech_state, repo.last_synced_at = snap, datetime.now(UTC)
            session.flush()
