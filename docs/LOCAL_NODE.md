# NODO LOCAL & the Node protocol (design boundary)

NODO CORE is the intelligence layer; a **Node** is a trusted execution environment registered with the Core
(HOME-PC, OFFICE-SERVER, MACBOOK, MOBILE, CLOUD-WORKER). A dedicated machine is *a Node*, not NODO.

## What already points there
- Providers carry `is_local` and `max_sensitivity`; `PRIVATE` mode routes local-only. A Node's Ollama is just
  another `ModelProvider` with a different base URL.
- Voice providers carry `location` (`browser | local | cloud`); `WakeWordEngine` is a protocol.
- Tools declare `auth` and levels; a Node can host filesystem/browser tools behind the same `ToolSpec`.
- All interactive capabilities are HTTP/SSE APIs, so a Node can be a client of the Core and vice versa.

## Node protocol (to design in Phase 5, not implemented)
```
Node → Core   register {node_id, name, capabilities: {gpu, local_models[], filesystem, microphone, wake_word, browser}}
Node → Core   heartbeat {load, availability}
Core → Node   execute {kind: inference|tool|voice, payload, budget, sensitivity}
Node → Core   result / stream
```
Placement decision inputs: sensitivity (prefer local for ≥ CLIENT_PRIVATE), availability, hardware, latency, cost.
Transport candidates: outbound WebSocket from the Node (NAT-friendly) with mTLS or device tokens.

## Privacy stance
Always-on wake word runs on the Node; only post-wake audio (or its local transcript) travels. Private memory can
be partitioned by `sensitivity` so HIGHLY_SENSITIVE facts never leave the Node (future `storage_location`).
