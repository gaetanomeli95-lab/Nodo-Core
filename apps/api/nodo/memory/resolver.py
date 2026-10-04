"""Entity resolution: maps a spoken/typed name ("prosperya", "PB Care", "softcomfort") to stored entities."""
from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz, process
from sqlalchemy import select
from sqlalchemy.orm import Session

from nodo.db.models import Client, Project


@dataclass
class ResolvedEntity:
    entity_type: str  # project | client
    entity_id: str
    name: str
    score: float
    matched_on: str


class EntityResolver:
    def __init__(self, session: Session, organization_id: str, threshold: float = 72.0):
        self.session, self.org, self.threshold = session, organization_id, threshold

    def _candidates(self) -> list[tuple[str, str, str, str]]:
        out = []
        for p in self.session.scalars(select(Project).where(Project.organization_id == self.org)):
            out.append(("project", p.id, p.name, p.name))
            out += [("project", p.id, p.name, a) for a in (p.aliases or [])]
        for c in self.session.scalars(select(Client).where(Client.organization_id == self.org)):
            out.append(("client", c.id, c.name, c.name))
            out += [("client", c.id, c.name, a) for a in (c.aliases or [])]
        return out

    def resolve(self, text: str, prefer: str | None = None) -> list[ResolvedEntity]:
        """Return matches above threshold, best first. `prefer` boosts one entity type."""
        cands = self._candidates()
        if not cands or not text.strip():
            return []
        names = [c[3] for c in cands]
        results: dict[tuple[str, str], ResolvedEntity] = {}
        for _, score, idx in process.extract(text, names, scorer=fuzz.token_set_ratio, limit=10):
            etype, eid, name, alias = cands[idx]
            # partial_ratio catches "fammi il punto su prosperya" vs "Prosperya"; require the alias to appear-ish
            partial = fuzz.partial_ratio(alias.lower(), text.lower())
            s = max(score, partial if len(alias) >= 4 else 0)
            if prefer == etype:
                s += 5
            if s >= self.threshold and (etype, eid) not in results or s > results.get((etype, eid), ResolvedEntity("", "", "", 0, "")).score:
                if s >= self.threshold:
                    results[(etype, eid)] = ResolvedEntity(etype, eid, name, s, alias)
        # tie-break on specificity: "Arredo Chef Sito" beats "Arredo Chef" when both match fully
        return sorted(results.values(), key=lambda r: (r.score, len(r.matched_on)), reverse=True)

    def best(self, text: str, prefer: str | None = None) -> ResolvedEntity | None:
        r = self.resolve(text, prefer)
        return r[0] if r else None
