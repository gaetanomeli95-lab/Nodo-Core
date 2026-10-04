# Model Providers & Router

## Abstraction (`nodo/providers/base.py`, ADR-002)
```python
class ModelProvider(Protocol):
    name: str
    def models(self) -> list[ModelDescriptor]
    async def available(self) -> bool
    def stream(self, model, messages, *, temperature, max_tokens) -> AsyncIterator[ChatChunk]
```
`ModelDescriptor` carries the capability metadata the router reasons about: `reasoning`, `coding`, `vision`,
`tool_calling`, `context_window`, `latency_class`, `cost_in/out_per_1k`, `is_local`, `max_sensitivity`,
`tier (local|free|paid)`, `daily_quota`.

## Implementations
| Module | Covers | Notes |
|---|---|---|
| `deterministic.py` | always-on grounded fallback / test double | no LLM; returns the `[GROUNDED_SUMMARY]` block |
| `ollama.py` | local models | `/api/chat` streaming; `HIGHLY_SENSITIVE` allowed |
| `openai_compat.py` | OpenAI, OpenRouter, Groq, any `/v1/chat/completions` | one class, per-provider descriptors |

Adding a provider = one module implementing the protocol + one branch in `registry.build_providers`.
Anthropic/Gemini native APIs are planned the same way (or via OpenRouter today).

## Router (`router.py`)
`RouteRequest{purpose, sensitivity, min_reasoning, min_coding, needs_vision, needs_tools, prefer_local}` →
`eligible()` filters by hard rules, `_score()` ranks, `stream()` executes with fallback on `ProviderError` and
records an `InferenceRun` per attempt.

Hard rules:
1. `max_sensitivity` ≥ request sensitivity.
2. `FREE`: no paid model unless `NODO_ALLOW_PAID_IN_FREE_MODE=true`.
3. `PRIVATE`: local only.
4. Capability minimums.

Ranking: FREE/PRIVATE prefer local then free; PERFORMANCE weights capability; cost penalised; low latency
preferred; deterministic always last among eligible real models. Quotas (`daily_quota`) are metadata today and
will be enforced from telemetry counts.

## Privacy matrix
| Data class | deterministic | ollama | groq / openrouter free | openai |
|---|---|---|---|---|
| PUBLIC / INTERNAL | ✓ | ✓ | ✓ | ✓ |
| CONFIDENTIAL | ✓ | ✓ | ✗ | ✓ |
| CLIENT_PRIVATE | ✓ | ✓ | ✗ | ✗ |
| HIGHLY_SENSITIVE | ✓ | ✓ | ✗ | ✗ |

Adjust per deployment by editing the descriptors (future: configuration file / UI).

## Telemetry
`GET /api/v1/telemetry/usage` → local/cloud/premium requests, agent runs, tool executions, tokens, estimated
spend, per provider. `GET /api/v1/telemetry/trace/{request_id}` → everything one request did.
