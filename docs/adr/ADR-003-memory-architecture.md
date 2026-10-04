# ADR-003 — Memory architecture: explicit facts with provenance, relational graph

**Status:** accepted · **Date:** 2026-10-04

## Context
NODO must "know what is going on": which projects exist, what was decided, what a client prefers, what changed
and when. The dominant pattern in AI products (store chat transcripts, retrieve similar chunks by embedding) is
cheap to build but fails the requirements that matter here: it cannot say *where* a fact came from, *how sure*
it is, *since when* it holds, or that two facts *contradict* each other. It also makes the system impossible to
audit and leaks everything into a single undifferentiated blob regardless of sensitivity.

## Decision
1. **Memory is explicit and typed.** Domain entities (`Client`, `Project`, `Task`, `Decision`, `Repository`,
   `Document`, `ContentItem`) are first-class relational rows. Free-form knowledge is a `MemoryFact` attached to
   a subject (`subject_type`, `subject_id`), never a floating text chunk.
2. **Every fact carries provenance and epistemic metadata:** `source_type/source_id`, `confidence`, `status`
   (`CONFIRMED INFERRED UNVERIFIED CONFLICTING STALE SUPERSEDED ARCHIVED`), `sensitivity`, `recorded_at`,
   `valid_from/valid_until`.
3. **Facts are never overwritten.** `MemoryService.supersede()` closes `valid_until`, links `superseded_by` and
   writes a new row. The history is always reconstructible (`facts_for(subject, at=...)`).
4. **Conflicts are detected, not silently resolved.** Two active facts with the same `subject+predicate` and
   different content are both marked `CONFLICTING`, linked via `conflicts_with`, and a
   `memory.conflict_detected` event is emitted. Resolution is a human (or, later, MEMORY agent) action.
5. **The graph is relational.** `Relationship(from, kind, to, meta)` rows plus foreign keys. No graph database.
6. **Layers are a column, not separate stores.** `MemoryFact.layer ∈ {semantic, episodic, preference, decision,
   relationship, document, agent}`; working memory is `Conversation.active_context` + `Message` rows.
7. **Entity resolution is part of memory.** `EntityResolver` matches spoken/typed names against names + `aliases`
   with fuzzy scoring; hierarchy (client → project) is not ambiguity; true ambiguity triggers a clarification.
8. **Retrieval is intent-driven.** The Context Engine assembles a bounded `ContextPackage` with only what the
   intent needs, records `sources`, and raises `sensitivity` to the max of the included entities.

## Alternatives considered
- **Vector store of chat history** (LangChain-style memory). Rejected as the primary store: no provenance, no
  temporal validity, no conflict detection, no sensitivity boundary. Kept as a *future additional index*.
- **Graph database (Neo4j).** Rejected for V0.1: operational cost for a team of one, no query pattern yet that
  relational joins cannot serve. `Relationship` rows keep the door open.
- **Event-sourced memory.** Partially adopted: the append-only `event_log` records what happened, but facts are
  materialised rows because reads dominate.

## Consequences
+ Every answer can be traced to rows with sources; "known vs inferred" is data, not prose.
+ Sensitivity travels with the fact → the Model Router can refuse a provider before any network call.
+ Deterministic tests without any model.
− Fact extraction from conversations is manual today; the system knows only what is entered or synced.
− No semantic similarity search; "find anything about pricing" needs a predicate or exact words.
− Supersession and conflicts create row growth; a staleness/archival job will be needed.

## Current V0.1 implementation (implemented)
`nodo/db/models.py` (`MemoryFact`, `Relationship`, `Conversation.active_context`), `nodo/memory/service.py`
(`remember`, `supersede`, `facts_for`, `relate`, conflict detection), `nodo/memory/resolver.py`
(`EntityResolver`, RapidFuzz), `nodo/core/context.py` (`ContextEngine`, `ContextPackage`). Tests:
`test_memory_conflict_and_supersession`, `test_entity_resolver_aliases_and_fuzzy`, `test_conversational_reference`.

## Architecturally prepared (seams exist, no implementation)
- `layer="agent"` and `layer="document"` values; `Document` table for ingestion metadata.
- `source_type="conversation"` for extracted facts; `Message.request_id` for linking.
- `sensitivity` on facts for a future `storage_location` partition (HIGHLY_SENSITIVE never leaves a local Node).

## Future evolution (planned)
- MEMORY agent: fact extraction from conversations, dedupe, conflict proposals.
- Embeddings (pgvector) as an *additional* retrieval index over `MemoryFact.content` and documents.
- Staleness job (`STALE` after N days without confirmation for episodic facts).
- Structured session state beyond `{entity, last_intent}` (multi-entity references; see ADR-005 for voice).
