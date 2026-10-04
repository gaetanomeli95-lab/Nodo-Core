"""Model Router: chooses which provider/model performs a reasoning step.

Policy inputs: NODO mode (FREE/BALANCED/PERFORMANCE/PRIVATE), data sensitivity, required capabilities.
Hard rules:
  * FREE mode never selects a paid model unless `allow_paid_in_free_mode` is explicitly on.
  * A model never receives data above its `max_sensitivity`.
  * PRIVATE mode selects local models only.
Soft ranking: prefer local, then free, then cheaper; prefer capability match; prefer low latency.
Fallback: on ProviderError the next candidate is tried. Every attempt is recorded in telemetry.
"""
from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

from nodo.config import NodoMode
from nodo.db.models import SENSITIVITY_RANK, Sensitivity
from nodo.providers.base import ChatChunk, ChatMessage, ModelDescriptor, ModelProvider, ProviderError


@dataclass
class RouteRequest:
    purpose: str = "synthesis"
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    min_reasoning: int = 0
    min_coding: int = 0
    needs_vision: bool = False
    needs_tools: bool = False
    prefer_local: bool = False
    exclude: set[str] = field(default_factory=set)


@dataclass
class RouteDecision:
    descriptor: ModelDescriptor
    reason: str


@dataclass
class InferenceRecord:
    request_id: str | None
    provider: str
    model: str
    purpose: str
    is_local: bool
    tokens_in: int
    tokens_out: int
    latency_ms: int
    estimated_cost: float
    status: str
    error: str | None = None
    agent_run_id: str | None = None


class NoEligibleModel(RuntimeError):
    pass


class ModelRouter:
    def __init__(self, providers: list[ModelProvider], mode: NodoMode = NodoMode.FREE,
                 allow_paid_in_free: bool = False,
                 recorder: Callable[[InferenceRecord], None] | None = None):
        self.providers = {p.name: p for p in providers}
        self.mode = mode
        self.allow_paid_in_free = allow_paid_in_free
        self.recorder = recorder
        self.last_decision: RouteDecision | None = None

    # ------------------------------------------------------------------ selection
    def catalog(self) -> list[ModelDescriptor]:
        return [d for p in self.providers.values() for d in p.models()]

    def eligible(self, req: RouteRequest) -> list[RouteDecision]:
        out: list[RouteDecision] = []
        for d in self.catalog():
            if d.id in req.exclude:
                continue
            if SENSITIVITY_RANK[Sensitivity(d.max_sensitivity)] < SENSITIVITY_RANK[req.sensitivity]:
                continue
            if self.mode == NodoMode.PRIVATE and not d.is_local:
                continue
            if self.mode == NodoMode.FREE and d.is_paid and not self.allow_paid_in_free:
                continue
            if d.reasoning < req.min_reasoning or d.coding < req.min_coding:
                continue
            if req.needs_vision and not d.vision or req.needs_tools and not d.tool_calling:
                continue
            out.append(RouteDecision(d, self._reason(d, req)))
        out.sort(key=lambda r: self._score(r.descriptor, req), reverse=True)
        return out

    def _score(self, d: ModelDescriptor, req: RouteRequest) -> float:
        s = 0.0
        if self.mode in (NodoMode.FREE, NodoMode.PRIVATE) or req.prefer_local:
            s += 30 if d.is_local else 0
            s += 20 if d.tier == "free" else 0
        if self.mode == NodoMode.PERFORMANCE:
            s += 10 * (d.reasoning + d.coding)
        else:
            s += 2 * (d.reasoning + d.coding)
        # the deterministic grounded provider is a last resort when real language models exist
        if d.provider == "deterministic":
            s -= 100
        s -= 50 * (d.cost_in_per_1k + d.cost_out_per_1k)
        s += {"low": 3, "medium": 1, "high": 0}[d.latency_class]
        return s

    def _reason(self, d: ModelDescriptor, req: RouteRequest) -> str:
        bits = [f"mode={self.mode}", f"sensitivity={req.sensitivity}", f"tier={d.tier}"]
        if d.is_local:
            bits.append("local")
        return ", ".join(bits)

    def select(self, req: RouteRequest) -> RouteDecision:
        cands = self.eligible(req)
        if not cands:
            raise NoEligibleModel(f"No model satisfies {req} in mode {self.mode}")
        self.last_decision = cands[0]
        return cands[0]

    # ------------------------------------------------------------------ execution with fallback
    async def stream(self, messages: list[ChatMessage], req: RouteRequest, *, request_id: str | None = None,
                     agent_run_id: str | None = None, temperature: float = 0.3,
                     max_tokens: int = 800) -> AsyncIterator[ChatChunk]:
        errors: list[str] = []
        for cand in self.eligible(req):
            d = cand.descriptor
            provider = self.providers[d.provider]
            if not await provider.available():
                errors.append(f"{d.id}: unavailable")
                continue
            self.last_decision = cand
            t0 = time.perf_counter()
            tin = tout = 0
            produced = False
            try:
                async for ch in provider.stream(d.model, messages, temperature=temperature, max_tokens=max_tokens):
                    if ch.done:
                        tin, tout = ch.tokens_in, ch.tokens_out
                    else:
                        produced = True
                    yield ch
                self._record(request_id, agent_run_id, d, req, tin, tout, t0, "ok")
                return
            except ProviderError as e:
                self._record(request_id, agent_run_id, d, req, tin, tout, t0, "failed", str(e))
                errors.append(f"{d.id}: {e}")
                if produced:  # cannot transparently switch provider mid-stream
                    raise
        raise NoEligibleModel("All candidates failed: " + "; ".join(errors))

    def _record(self, request_id, agent_run_id, d: ModelDescriptor, req: RouteRequest, tin, tout, t0, status,
                error: str | None = None) -> None:
        if not self.recorder:
            return
        cost = (tin / 1000) * d.cost_in_per_1k + (tout / 1000) * d.cost_out_per_1k
        self.recorder(InferenceRecord(request_id, d.provider, d.model, req.purpose, d.is_local, tin, tout,
                                      int((time.perf_counter() - t0) * 1000), round(cost, 6), status, error,
                                      agent_run_id))
