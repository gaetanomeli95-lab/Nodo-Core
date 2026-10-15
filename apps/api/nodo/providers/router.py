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

import asyncio
import re
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

from nodo.config import NodoMode
from nodo.db.models import SENSITIVITY_RANK, Sensitivity
from nodo.providers.base import (
    ChatChunk,
    ChatMessage,
    FunctionSpec,
    ModelDescriptor,
    ModelProvider,
    ProviderError,
    ToolTurn,
)


@dataclass
class RouteRequest:
    purpose: str = "synthesis"
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    min_reasoning: int = 0
    min_coding: int = 0
    needs_vision: bool = False
    needs_tools: bool = False
    prefer_local: bool = False
    prefer_fast: bool = False   # voice turns: a quick answer matters more than the smartest one
    background: bool = False    # memory/distillation: never start a CLI program (Claude/ChatGPT) on the small PC
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


# Models that hit their usage limit (subscription weekly limit, free-tier quota, 429) are skipped for a while,
# so NODO does not waste seconds on every message retrying a provider that cannot answer. Process-wide.
_LIMIT_RE = re.compile(r"limit|quota|rate.?limit|\b429\b|too many requests|usage|resets? (at|on|in)|esaurit",
                       re.I)
# dead-end errors (wrong key, model removed, no permission): they will not heal in half an hour, so the
# provider rests much longer instead of wasting a timeout on every message
_DEAD_RE = re.compile(r"not.?found|does not exist|no such model|invalid api key|unauthoriz|forbidden|"
                      r"permission denied|\b401\b|\b403\b|\b404\b|non JSON", re.I)
# transient trouble (timeout, overload, 5xx): may heal in minutes, so the pause is short
_TRANSIENT_RE = re.compile(r"timed?\s*out|timeout|non ha risposto|unavailable|overloaded|\b50[0-9]\b|deadline|"
                           r"transport error|getaddrinfo|high demand", re.I)
_cooldown: dict[str, float] = {}
# providers that run a whole program on Gaetano's PC for every call (seconds of CPU and hundreds of MB each)
LOCAL_PROGRAMS = {"claude-subscription", "chatgpt-subscription"}

# A voice turn cannot wait minutes while a dead provider burns its own timeout: `prefer_fast`
# bounds each attempt to this many seconds (per chunk for streams), then the router moves on.
FAST_ATTEMPT_TIMEOUT = 30.0


def cooldowns() -> dict[str, float]:
    """provider name -> seconds left in the pause (only providers currently paused)."""
    now = time.monotonic()
    return {k: round(v - now) for k, v in _cooldown.items() if v > now}


def clear_cooldowns() -> None:
    _cooldown.clear()


class ModelRouter:
    def __init__(self, providers: list[ModelProvider], mode: NodoMode = NodoMode.FREE,
                 allow_paid_in_free: bool = False,
                 recorder: Callable[[InferenceRecord], None] | None = None, cooldown_minutes: float = 30):
        self.cooldown_seconds = cooldown_minutes * 60
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
            if d.id in req.exclude or (req.background and d.provider in LOCAL_PROGRAMS):
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
        if req.purpose == "memory" and d.provider == "gemini":
            # Gemini Flash's free quota is small and it is the best fast brain for talking with Gaetano: studying
            # old chats and tidying memory ate it (15/10: «429 quota» all day, and the conversation fell back to
            # Flash-Lite, which says «done» without doing). Memory work uses the other models first.
            s -= 40
        if req.prefer_fast:  # a spoken answer that takes 30s feels broken: fast models jump ahead
            s += {"low": 15, "medium": 6, "high": 0}[d.latency_class]
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

    def _paused(self, provider: str) -> bool:
        until = _cooldown.get(provider)
        return until is not None and until > time.monotonic()

    def _maybe_pause(self, provider: str, error: str) -> None:
        if self.cooldown_seconds <= 0 or provider == "deterministic":
            return
        if _LIMIT_RE.search(error):
            _cooldown[provider] = time.monotonic() + self.cooldown_seconds
        elif _DEAD_RE.search(error):
            _cooldown[provider] = time.monotonic() + self.cooldown_seconds * 12  # 6h with the default
        elif _TRANSIENT_RE.search(error):
            _cooldown[provider] = time.monotonic() + self.cooldown_seconds / 6  # ~5 min: worth retrying soon

    # ------------------------------------------------------------------ execution with fallback
    async def stream(self, messages: list[ChatMessage], req: RouteRequest, *, request_id: str | None = None,
                     agent_run_id: str | None = None, temperature: float = 0.3,
                     max_tokens: int = 800) -> AsyncIterator[ChatChunk]:
        errors: list[str] = []
        for cand in self.eligible(req):
            d = cand.descriptor
            provider = self.providers[d.provider]
            if self._paused(d.provider):
                errors.append(f"{d.id}: in pausa (limite d'uso)")
                continue
            if not await provider.available():
                errors.append(f"{d.id}: unavailable")
                continue
            self.last_decision = cand
            t0 = time.perf_counter()
            tin = tout = 0
            produced = False
            try:
                ait = provider.stream(d.model, messages, temperature=temperature, max_tokens=max_tokens)
                while True:
                    try:
                        ch = await (asyncio.wait_for(ait.__anext__(), FAST_ATTEMPT_TIMEOUT)
                                    if req.prefer_fast else ait.__anext__())
                    except StopAsyncIteration:
                        break
                    except TimeoutError as e:
                        raise ProviderError(f"{d.provider}: timed out after "
                                            f"{int(FAST_ATTEMPT_TIMEOUT)}s") from e
                    if ch.done:
                        tin, tout = ch.tokens_in, ch.tokens_out
                    else:
                        produced = True
                    yield ch
                self._record(request_id, agent_run_id, d, req, tin, tout, t0, "ok")
                return
            except Exception as e:  # noqa: BLE001 - ANY provider failure (bad JSON, bug) -> next model
                self._record(request_id, agent_run_id, d, req, tin, tout, t0, "failed", str(e))
                self._maybe_pause(d.provider, str(e))
                errors.append(f"{d.id}: {e}")
                if produced:  # cannot transparently switch provider mid-stream
                    raise
        raise NoEligibleModel("All candidates failed: " + "; ".join(errors))

    async def complete_tools(self, messages: list[dict], tools: list[FunctionSpec], req: RouteRequest, *,
                             request_id: str | None = None, temperature: float = 0.4,
                             max_tokens: int = 1200) -> ToolTurn:
        """One function-calling step on the best eligible tool-capable model, with fallback."""
        req.needs_tools = True
        errors: list[str] = []
        cands = self.eligible(req)
        # resting providers go LAST instead of never: if every good one is resting, a slow answer beats none
        cands = [c for c in cands if not self._paused(c.descriptor.provider)] + \
                [c for c in cands if self._paused(c.descriptor.provider)]
        for cand in cands:
            d = cand.descriptor
            provider = self.providers[d.provider]
            if not hasattr(provider, "complete_tools") or not await provider.available():
                continue
            self.last_decision = cand
            t0 = time.perf_counter()
            try:
                try:
                    turn = await (asyncio.wait_for(
                        provider.complete_tools(d.model, messages, tools, temperature=temperature,
                                                max_tokens=max_tokens), FAST_ATTEMPT_TIMEOUT)
                        if req.prefer_fast else
                        provider.complete_tools(d.model, messages, tools, temperature=temperature,
                                                max_tokens=max_tokens))
                except TimeoutError as e:
                    raise ProviderError(f"{d.provider}: timed out after "
                                        f"{int(FAST_ATTEMPT_TIMEOUT)}s") from e
                self._record(request_id, None, d, req, turn.tokens_in, turn.tokens_out, t0, "ok")
                return turn
            except Exception as e:  # noqa: BLE001 - ANY provider failure (bad JSON, bug) -> next model
                self._record(request_id, None, d, req, 0, 0, t0, "failed", str(e))
                self._maybe_pause(d.provider, str(e))
                errors.append(f"{d.id}: {e}")
        raise NoEligibleModel("No tool-capable model available: " + ("; ".join(errors) or "none configured"))

    def has_tool_model(self, req: RouteRequest) -> bool:
        req.needs_tools = True
        return any(hasattr(self.providers[c.descriptor.provider], "complete_tools") for c in self.eligible(req))

    def _record(self, request_id, agent_run_id, d: ModelDescriptor, req: RouteRequest, tin, tout, t0, status,
                error: str | None = None) -> None:
        if not self.recorder:
            return
        cost = (tin / 1000) * d.cost_in_per_1k + (tout / 1000) * d.cost_out_per_1k
        self.recorder(InferenceRecord(request_id, d.provider, d.model, req.purpose, d.is_local, tin, tout,
                                      int((time.perf_counter() - t0) * 1000), round(cost, 6), status, error,
                                      agent_run_id))
