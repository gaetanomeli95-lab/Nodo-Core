"""Intent Engine.

V0.1 is deliberately rule-based (deterministic, free, testable). The interface is stable so a model-backed
classifier can be plugged behind `IntentEngine.classify` later (ADR-001). Handles Italian + English.

Conversational references: if the message carries no entity but requires one, the conversation's active
context is used. If the message is *only* an entity name ("PB CARe."), the previous intent is reapplied.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from nodo.memory.resolver import EntityResolver, ResolvedEntity

INTENT_PATTERNS: list[tuple[str, list[str], bool]] = [
    # (intent, regex list, needs_entity)
    ("stop", [r"^\s*(no[, ]+)?(stop|ferma(ti)?|basta|annulla|cancel)\b"], False),
    # bare confirmations/denials — become approvals ONLY when bound to a pending action (security)
    ("approve", [r"^\s*(sì|si|yes|ok|okay|va bene|d'accordo|confermo?|conferma|procedi|vai pure|approvo)[\.\!\s]*$"], False),
    ("reject", [r"^\s*(no|nope|non procedere|non confermo|rifiuto|lascia stare|non farlo)[\.\!\s]*$"], False),
    ("list_projects", [r"\b(mostrami|elenca|quali sono|lista|show|list)\b.*\bprogett", r"\bmy projects\b",
                       r"^\s*progetti\s*\??$"], False),
    ("tech_status", [r"\bstato tecnic", r"\btechnical (status|state)", r"\b(repo|repository|github|ci|build|deploy)\b",
                     r"\bcodice\b"], True),
    ("prepare_content", [r"\b(prepara|proponi|idea|scrivi|genera)\b.*\b(contenut|post|video|script|reel)",
                         r"\b(content|post|video)\b.*\bfor\b", r"\bcontenut\w*\s+per\b"], True),
    ("today", [r"\b(cosa|che cosa|che)\b.*\b(oggi|stamattina)\b", r"\boggi\b.*\?", r"\bbuongiorno\b", r"\btoday\b",
               r"\bgood morning\b", r"\bpriorit"], False),
    # multi-entity comparison: relies on explicit entities or the conversation's entity history
    ("compare", [r"\b(quale|qual è|chi)\b.*\b(delle due|dei due|tra loro|tra i due|tra le due|di più|"
                 r"più urgent|più attenzion|più important)\b",
                 r"\b(metti a confronto|confronta|compara|a confronto|quale delle|which of|compare)\b",
                 r"\bentramb[ie]\b"], False),
    ("project_status", [r"\b(punto|stato|situazione|aggiornament|come va|come procede|a che punto)\b",
                        r"\b(status|update|state) (of|on)\b", r"\bbrief", r"\bapri\b",
                        # follow-ups that rely on the active context: "cosa manca?", "cosa resta?", "è bloccato?"
                        r"\b(cosa|che cosa) (manca|resta|rimane)\b", r"\bblocc", r"\bprossim[oi] pass",
                        r"\bwhat('s| is) (missing|left|blocking)\b"], True),
]

_ENTITY_ONLY = re.compile(r"^[\w\s\.\-']{2,40}$")


@dataclass
class Intent:
    name: str
    text: str
    entity: ResolvedEntity | None = None
    confidence: float = 1.0
    needs_entity: bool = False
    from_context: bool = False      # entity came from conversation context
    reference_reapplied: bool = False  # bare entity name -> previous intent reused
    candidates: list[ResolvedEntity] = field(default_factory=list)
    entities: list[ResolvedEntity] = field(default_factory=list)  # compare: every entity in scope

    @property
    def unresolved(self) -> bool:
        return self.needs_entity and self.entity is None

    @property
    def ambiguous(self) -> bool:
        if self.name == "compare":
            return False  # multiple entities are the point, not ambiguity
        if len(self.candidates) < 2:
            return False
        top, second = self.candidates[0], self.candidates[1]
        # a parent/child pair ("Prosperya" vs "Prosperya CRM") is a hierarchy, not an ambiguity
        if second.matched_on.lower() in top.matched_on.lower() or top.matched_on.lower() in second.matched_on.lower():
            return False
        return top.score - second.score < 8


class IntentEngine:
    def __init__(self, resolver: EntityResolver):
        self.resolver = resolver

    def classify(self, text: str, active_context: dict | None = None) -> Intent:
        active_context = active_context or {}
        low = text.lower().strip()
        candidates = self.resolver.resolve(text)
        entity = candidates[0] if candidates else None

        name, needs_entity, confidence = "general", False, 0.5
        for intent_name, patterns, ne in INTENT_PATTERNS:
            if any(re.search(p, low) for p in patterns):
                name, needs_entity, confidence = intent_name, ne, 0.9
                break

        # bare entity reference: "PB CARe." / "e PB CARe?" after "fammi il punto su Prosperya"
        reapplied = False
        bare = re.sub(r"^(e|and)\s+", "", low).rstrip("?.! ")
        if name == "general" and entity and _ENTITY_ONLY.match(bare) and active_context.get("last_intent"):
            prev = active_context["last_intent"]
            if prev not in ("stop", "general"):
                name, needs_entity, confidence, reapplied = prev, prev not in ("list_projects", "today"), 0.8, True

        from_context = False
        if needs_entity and entity is None and active_context.get("entity_id"):
            entity = ResolvedEntity(active_context["entity_type"], active_context["entity_id"],
                                    active_context.get("entity_name", ""), 100.0, "context")
            from_context = True

        entities = [entity] if entity else []
        if name == "compare":
            entities = list(candidates[:4])                     # every entity named in the sentence
            if len(entities) < 2:
                for e in active_context.get("entities") or []:  # "le due" = the last entities discussed
                    entities.append(ResolvedEntity(e["entity_type"], e["entity_id"],
                                                   e.get("entity_name", ""), 100.0, "context"))
                entities = entities[:4]
            if entities:
                entity = entities[0]
        if name == "project_status" and entity and entity.entity_type == "client":
            pass  # the context engine will expand a client into its projects
        return Intent(name, text, entity, confidence, needs_entity, from_context, reapplied, candidates,
                      entities)
