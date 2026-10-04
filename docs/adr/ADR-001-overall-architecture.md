# ADR-001 — Overall architecture: modular monolith, API-first, SSE streaming

**Status:** accepted · **Date:** 2026-10-04

## Context
NODO must serve voice, text and UI from one intelligence, stay runnable on a laptop with no keys, and evolve
into a distributed system (cloud Core + local Nodes). A founding team of one cannot operate microservices.

## Decision
- **Modular monolith** in Python/FastAPI: packages `core`, `providers`, `memory`, `agents`, `tools`, `voice`,
  `api` with one-directional dependencies (see ARCHITECTURE.md §4). No module imports `api`.
- **API-first**: every capability is an HTTP endpoint; the web app is one client. `/command/sync` exists for
  constrained clients (mobile, CLI).
- **Realtime = SSE** for command streaming: plain HTTP, proxy/CDN friendly, trivial to consume with `fetch`,
  enough for unidirectional token/event streams. **WebSocket** is reserved for duplex voice (Phase 2).
- **Frontend = React + TypeScript + Vite** instead of Next.js: no SSR/SEO need, smaller surface, faster builds,
  natural PWA path for mobile. Revisit only if server rendering becomes necessary.
- **Rule-based Intent Engine** for V0.1 behind a stable `classify()` interface; a model-backed classifier is a
  drop-in.

## Consequences
+ One process to run/test/deploy; deterministic tests; clear seams for later extraction (connectors, voice).
− SSE cannot receive mid-stream client input (interruptions are a separate POST); acceptable for V0.1.
− Rule-based intents will miss phrasing; mitigated by conversational context and clarification questions.
