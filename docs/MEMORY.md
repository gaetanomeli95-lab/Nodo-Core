# Memory

Memory is explicit, relational and provenance-aware (ADR-003). Not "store chats, retrieve similar chunks".

## Layers → where they live
| Layer | V0.1 storage |
|---|---|
| Working | `Conversation.active_context` + `Message` rows |
| Episodic | `MemoryFact.layer="episodic"` |
| Semantic | `MemoryFact.layer="semantic"` (+ predicate as a single-valued slot) |
| Project | `Project`, `Task`, `Decision`, `Repository.tech_state`, facts on `project` |
| Relationship | `Relationship` rows (`serves`, `has_project`, `stored_in`, …) |
| Preference | `MemoryFact.layer="preference"` on `user`/`client` |
| Decision | `Decision` + mirrored fact `layer="decision"` |
| Document | `Document` metadata (ingestion in Phase 4) |
| Agent | `AgentRun` output (facts with `layer="agent"` later) |

## `MemoryFact`
`subject_type/subject_id`, `predicate?`, `content`, `source_type/source_id`, `confidence`, `status`
(`CONFIRMED INFERRED UNVERIFIED CONFLICTING STALE SUPERSEDED ARCHIVED`), `sensitivity`, `recorded_at`,
`valid_from/valid_until`, `superseded_by`, `conflicts_with`.

- **Never overwrite:** `MemoryService.supersede(old, new)` closes `valid_until`, links `superseded_by`.
- **Conflict detection:** two active facts with the same subject+predicate and different content are both
  marked `CONFLICTING` and an `memory.conflict_detected` event is emitted. Humans (or the future MEMORY agent) resolve.
- **Temporal reads:** `facts_for(subject, at=...)` returns truth at a point in time.

## Entity resolution
`EntityResolver` matches spoken/typed names against project/client names + `aliases` with RapidFuzz
(`token_set_ratio` + `partial_ratio`), tie-breaking on specificity. Parent/child pairs ("Prosperya" vs
"Prosperya CRM") are a hierarchy, not an ambiguity; genuinely ambiguous matches trigger a clarification question.

## Context Engine
Builds a `ContextPackage` with only what the intent needs, records `sources`, and raises `sensitivity` to the
max of the included entities. Exposed in the SSE stream (`context` event) and therefore inspectable.

## Next
Embeddings (pgvector) as an additional retrieval index over `MemoryFact.content` and documents; fact extraction
from conversations; staleness job; graph UI fed by `/graph/{type}/{id}`.
