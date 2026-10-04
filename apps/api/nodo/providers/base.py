"""Model provider abstraction (ADR-002). Providers are computational resources, not NODO."""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

from nodo.db.models import Sensitivity

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str


@dataclass(frozen=True)
class ModelDescriptor:
    """Capability metadata used by the router."""
    provider: str
    model: str
    reasoning: int = 2          # 0..5 rough capability scores
    coding: int = 2
    vision: bool = False
    tool_calling: bool = False
    context_window: int = 8_000
    latency_class: Literal["low", "medium", "high"] = "medium"
    cost_in_per_1k: float = 0.0   # USD; 0 = free
    cost_out_per_1k: float = 0.0
    is_local: bool = False
    max_sensitivity: Sensitivity = Sensitivity.INTERNAL  # highest class this model may receive
    tier: Literal["local", "free", "paid"] = "free"
    daily_quota: int | None = None

    @property
    def id(self) -> str:
        return f"{self.provider}:{self.model}"

    @property
    def is_paid(self) -> bool:
        return self.tier == "paid" or self.cost_in_per_1k > 0 or self.cost_out_per_1k > 0


@dataclass
class ChatChunk:
    delta: str = ""
    done: bool = False
    tokens_in: int = 0
    tokens_out: int = 0


@dataclass
class ChatResult:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    raw: dict = field(default_factory=dict)


class ProviderError(RuntimeError):
    """Raised by providers on failure; the router may fall back to the next candidate."""


@runtime_checkable
class ModelProvider(Protocol):
    name: str

    def models(self) -> list[ModelDescriptor]: ...

    async def available(self) -> bool: ...

    def stream(self, model: str, messages: list[ChatMessage], *, temperature: float = 0.3,
               max_tokens: int = 800) -> AsyncIterator[ChatChunk]: ...


async def collect(stream: AsyncIterator[ChatChunk]) -> ChatResult:
    parts: list[str] = []
    tin = tout = 0
    async for ch in stream:
        parts.append(ch.delta)
        tin = ch.tokens_in or tin
        tout = ch.tokens_out or tout
    return ChatResult("".join(parts), tin, tout)


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)
