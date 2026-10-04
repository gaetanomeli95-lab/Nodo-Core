# ADR-008 — Local / cloud strategy: one Core, placement by privacy, cost and latency

**Status:** accepted · **Date:** 2026-10-04

## Context
NODO handles client-private data and should remain usable with no recurring API cost, yet the best models and
speech services are often cloud-hosted. The user also envisions dedicated machines ("a NODO box"). The risk is
either leaking everything to the cloud for convenience, or building a hardware product before the software
knows what to place where.

## Decision
1. **A dedicated machine is a Node, not NODO.** NODO CORE is the intelligence layer; a **Node** is a trusted
   execution environment (laptop, office server, future appliance) that can host local models, local speech,
   filesystem/browser tools and a microphone.
2. **Placement is a routing decision driven by metadata, not by code paths.** Providers carry `is_local`,
   `tier ∈ {local, free, paid}`, `max_sensitivity`, cost and latency class; voice providers carry `location`.
   Modes map to hard rules: `FREE` = never paid; `PRIVATE` = local only; `BALANCED/PERFORMANCE` relax cost.
3. **Sensitivity decides egress.** ≥ `CONFIDENTIAL` never goes to free cloud tiers; ≥ `CLIENT_PRIVATE` never
   goes to paid cloud by default; `HIGHLY_SENSITIVE` is local-only. Enforced by the Model Router (ADR-006).
4. **Local-first defaults.** Browser speech, deterministic provider, SQLite, and Ollama when configured mean a
   fully functional NODO with no credentials.
5. **Always-on listening belongs on a Node.** Wake word detection must run locally; only post-wake audio or
   its local transcript travels. Continuous cloud upload of microphone audio is rejected.
6. **API-first makes topology flexible.** Every capability is HTTP/SSE/WebSocket, so a Node can be a client of
   the Core and the Core can dispatch work to a Node over the same contracts.

## Alternatives considered
- **Cloud-only SaaS.** Rejected: privacy, cost, and the explicit vision of local operation.
- **Local-only appliance.** Rejected: the best models are remote; users move between devices.
- **Hardware first.** Rejected: placement logic and the Node protocol must exist in software before any box.
- **Federated per-tenant deployments.** Deferred; `organization_id` keeps it possible.

## Consequences
+ Users can choose privacy/cost/quality per deployment with configuration, not forks.
+ The Node concept gives wake word and HIGHLY_SENSITIVE memory a legitimate home.
− Two execution locations mean two failure domains; health/availability must be tracked (`available()`).
− Quality in FREE mode is bounded by local/free models; the deterministic floor is honest but terse.

## Current V0.1 implementation (implemented)
Provider descriptors with `is_local/tier/max_sensitivity` (`nodo/providers/*`), `ModelRouter` hard rules and
ranking, `NodoMode` in `nodo/config.py`, voice `location` and FREE gate (`nodo/voice/providers.py`), browser
speech defaults, `/voice/config` exposing processing location. Tests: `tests/test_router.py` (FREE excludes
paid, PRIVATE local-only, sensitivity gate, fallback chain).

## Architecturally prepared (seams exist, no implementation)
- `WakeWordEngine` protocol; voice provider `location="local"`.
- `ToolSpec.auth` and levels for Node-hosted tools.
- `docs/LOCAL_NODE.md` describes the Node protocol boundary (register / heartbeat / execute / result).

## Future evolution (planned)
Phase 5: Node registry and capability advertisement, outbound WebSocket from Node (NAT-friendly) with device
tokens/mTLS, workload placement (sensitivity, availability, hardware, latency, cost), local STT/TTS providers
(Whisper/Piper) on a Node, local wake word "Node." opening a `VoiceSession` (ADR-005), `storage_location` for
HIGHLY_SENSITIVE facts. Phase 7: multi-Node distribution and mobile client over the same API.
