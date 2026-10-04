# ADR-002 — AI provider abstraction and Model Router

**Status:** accepted · **Date:** 2026-10-04

## Context
NODO must be model-agnostic, run in FREE mode without spending, respect data sensitivity, and survive provider
failures. Commercial plans change often.

## Decision
- `ModelProvider` protocol (`models()`, `available()`, `stream()`) and `ModelDescriptor` capability metadata.
- One `OpenAICompatProvider` class for every `/v1/chat/completions` vendor (OpenAI, OpenRouter, Groq, …);
  `OllamaProvider` for local; `DeterministicProvider` always present.
- `ModelRouter` enforces hard rules (sensitivity gate, FREE = no paid, PRIVATE = local only, capability
  minimums), ranks softly, and falls back across candidates on `ProviderError`, recording every attempt.
- Limits, costs and privacy classes live in descriptors/config, not in logic.
- **Deterministic provider** returns the grounded summary block computed by the Core; it never generates.
  It is the honest floor of FREE mode and the test double (`FakeModelProvider`).

## Consequences
+ Adding a provider is additive; switching vendors is configuration.
+ Privacy is enforced by code before any network call.
− Mid-stream provider failure cannot be transparently retried (partial output already shown); surfaced as error.
− Token counts for streaming vendors without usage events are estimated (`len/4`).
