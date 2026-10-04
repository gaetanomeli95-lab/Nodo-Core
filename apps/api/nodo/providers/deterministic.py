"""Deterministic (non-LLM) provider.

Always available, free, local, privacy-safe. It performs NO language generation: it returns the grounded
summary block that the Core already computed from stored data. This keeps NODO honest in FREE mode when no
inference provider is configured: nothing is invented, and telemetry labels it as `deterministic`.

Also used as the test double (`FakeModelProvider` alias) with optional scripted responses.
"""
from __future__ import annotations

import re
from collections.abc import AsyncIterator

from nodo.db.models import Sensitivity
from nodo.providers.base import ChatChunk, ChatMessage, ModelDescriptor, estimate_tokens

GROUNDED_RE = re.compile(r"\[GROUNDED_SUMMARY\]\n?(.*?)\n?\[/GROUNDED_SUMMARY\]", re.DOTALL)


class DeterministicProvider:
    name = "deterministic"

    def __init__(self, scripted: list[str] | None = None, fail: bool = False):
        self._scripted = list(scripted or [])
        self._fail = fail
        self.calls: list[list[ChatMessage]] = []

    def models(self) -> list[ModelDescriptor]:
        return [ModelDescriptor(provider=self.name, model="grounded-v1", reasoning=0, coding=0,
                                is_local=True, tier="local", latency_class="low",
                                max_sensitivity=Sensitivity.HIGHLY_SENSITIVE, context_window=1_000_000)]

    async def available(self) -> bool:
        return True

    async def stream(self, model: str, messages: list[ChatMessage], *, temperature: float = 0.3,
                     max_tokens: int = 800) -> AsyncIterator[ChatChunk]:
        self.calls.append(messages)
        if self._fail:
            from nodo.providers.base import ProviderError
            raise ProviderError("scripted failure")
        joined = "\n".join(m.content for m in messages)
        if self._scripted:
            text = self._scripted.pop(0)
        else:
            m = GROUNDED_RE.search(joined)
            text = m.group(1).strip() if m else "Non ho abbastanza informazioni per rispondere."
        tin = estimate_tokens(joined)
        # stream in word chunks so the UI path is exercised exactly like a real provider
        words = text.split(" ")
        for i, w in enumerate(words):
            yield ChatChunk(delta=w + (" " if i < len(words) - 1 else ""))
        yield ChatChunk(done=True, tokens_in=tin, tokens_out=estimate_tokens(text))


FakeModelProvider = DeterministicProvider
