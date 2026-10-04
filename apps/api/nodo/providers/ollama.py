"""Ollama provider: local inference (privacy-safe, free)."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from nodo.db.models import Sensitivity
from nodo.providers.base import ChatChunk, ChatMessage, ModelDescriptor, ProviderError


class OllamaProvider:
    name = "ollama"

    def __init__(self, base_url: str, model: str, timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._timeout = timeout

    def models(self) -> list[ModelDescriptor]:
        return [ModelDescriptor(provider=self.name, model=self.model, reasoning=2, coding=2, is_local=True,
                                tier="local", context_window=8_000, latency_class="medium",
                                max_sensitivity=Sensitivity.HIGHLY_SENSITIVE)]

    async def available(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as c:
                r = await c.get(f"{self.base_url}/api/tags")
                return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def stream(self, model: str, messages: list[ChatMessage], *, temperature: float = 0.3,
                     max_tokens: int = 800) -> AsyncIterator[ChatChunk]:
        body = {"model": model, "stream": True, "options": {"temperature": temperature, "num_predict": max_tokens},
                "messages": [{"role": m.role, "content": m.content} for m in messages]}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                async with client.stream("POST", f"{self.base_url}/api/chat", json=body) as resp:
                    if resp.status_code >= 400:
                        raise ProviderError(f"ollama HTTP {resp.status_code}")
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        evt = json.loads(line)
                        if delta := (evt.get("message") or {}).get("content"):
                            yield ChatChunk(delta=delta)
                        if evt.get("done"):
                            yield ChatChunk(done=True, tokens_in=evt.get("prompt_eval_count", 0),
                                            tokens_out=evt.get("eval_count", 0))
                            return
        except httpx.HTTPError as e:
            raise ProviderError(f"ollama transport error: {e}") from e
        yield ChatChunk(done=True)
