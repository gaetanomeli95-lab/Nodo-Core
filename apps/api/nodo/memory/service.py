"""Memory service: facts with provenance, temporal validity, supersession and basic conflict detection."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from nodo.core.events import EventBus
from nodo.db.models import FactStatus, MemoryFact, Relationship, Sensitivity

ACTIVE = (FactStatus.CONFIRMED, FactStatus.INFERRED, FactStatus.UNVERIFIED, FactStatus.CONFLICTING)


class MemoryService:
    def __init__(self, session: Session, organization_id: str, events: EventBus | None = None):
        self.s, self.org, self.events = session, organization_id, events

    def facts_for(self, subject_type: str, subject_id: str, *, at: datetime | None = None,
                  layers: tuple[str, ...] | None = None, limit: int = 30) -> list[MemoryFact]:
        at = at or datetime.now(UTC)
        q = select(MemoryFact).where(MemoryFact.organization_id == self.org, MemoryFact.subject_type == subject_type,
                                     MemoryFact.subject_id == subject_id, MemoryFact.status.in_(ACTIVE))
        if layers:
            q = q.where(MemoryFact.layer.in_(layers))
        rows = self.s.scalars(q.order_by(MemoryFact.recorded_at.desc()).limit(limit * 2)).all()
        return [f for f in rows if (f.valid_from is None or _aware(f.valid_from) <= at)
                and (f.valid_until is None or _aware(f.valid_until) > at)][:limit]

    def remember(self, subject_type: str, subject_id: str, content: str, *, predicate: str | None = None,
                 layer: str = "semantic", source_type: str = "manual", source_id: str | None = None,
                 confidence: float = 1.0, status: FactStatus = FactStatus.CONFIRMED,
                 sensitivity: Sensitivity = Sensitivity.INTERNAL, valid_from: datetime | None = None,
                 request_id: str | None = None) -> MemoryFact:
        fact = MemoryFact(organization_id=self.org, subject_type=subject_type, subject_id=subject_id,
                          predicate=predicate, content=content, layer=layer, source_type=source_type,
                          source_id=source_id, confidence=confidence, status=status, sensitivity=sensitivity,
                          valid_from=valid_from)
        self.s.add(fact)
        self.s.flush()
        if predicate:
            self._check_predicate_conflict(fact, request_id)
        if self.events:
            self.events.emit("memory.fact_recorded", {"fact_id": fact.id, "subject": f"{subject_type}:{subject_id}",
                                                      "layer": layer}, request_id=request_id, organization_id=self.org)
        return fact

    def supersede(self, old: MemoryFact, new_content: str, **kw) -> MemoryFact:
        """Temporal update: history is preserved, the old fact becomes SUPERSEDED with valid_until=now."""
        now = datetime.now(UTC)
        new = self.remember(old.subject_type, old.subject_id, new_content, predicate=old.predicate, layer=old.layer,
                            sensitivity=Sensitivity(old.sensitivity), valid_from=now, **kw)
        old.status, old.valid_until, old.superseded_by = FactStatus.SUPERSEDED, now, new.id
        self.s.flush()
        return new

    def _check_predicate_conflict(self, fact: MemoryFact, request_id: str | None) -> None:
        """Two active facts with the same subject+predicate but different content => CONFLICTING (never overwritten)."""
        others = self.s.scalars(select(MemoryFact).where(
            MemoryFact.id != fact.id, MemoryFact.subject_type == fact.subject_type,
            MemoryFact.subject_id == fact.subject_id, MemoryFact.predicate == fact.predicate,
            MemoryFact.status.in_(ACTIVE), MemoryFact.valid_until.is_(None))).all()
        for o in others:
            if _norm(o.content) != _norm(fact.content):
                o.status = fact.status = FactStatus.CONFLICTING
                o.conflicts_with, fact.conflicts_with = fact.id, o.id
                if self.events:
                    self.events.emit("memory.conflict_detected", {"fact_id": fact.id, "conflicts_with": o.id,
                                                                  "predicate": fact.predicate},
                                     request_id=request_id, organization_id=self.org)

    def conflicts(self) -> list[MemoryFact]:
        return self.s.scalars(select(MemoryFact).where(MemoryFact.organization_id == self.org,
                                                       MemoryFact.status == FactStatus.CONFLICTING)).all()

    # ------------------------------------------------------------- relationships (graph)
    def relate(self, from_type: str, from_id: str, kind: str, to_type: str, to_id: str, **meta) -> Relationship:
        rel = Relationship(organization_id=self.org, from_type=from_type, from_id=from_id, kind=kind,
                           to_type=to_type, to_id=to_id, meta=meta)
        self.s.add(rel)
        self.s.flush()
        return rel

    def neighbors(self, entity_type: str, entity_id: str) -> list[Relationship]:
        return self.s.scalars(select(Relationship).where(
            Relationship.organization_id == self.org,
            ((Relationship.from_type == entity_type) & (Relationship.from_id == entity_id))
            | ((Relationship.to_type == entity_type) & (Relationship.to_id == entity_id)))).all()


def _norm(text: str) -> str:
    return " ".join(text.casefold().split()).rstrip(".")


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
