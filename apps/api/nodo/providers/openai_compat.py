"""OpenAI-compatible chat provider. Covers OpenAI, OpenRouter, Groq and any `/v1/chat/completions` server."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from nodo.db.models import Sensitivity
from nodo.providers.base import ChatChunk, ChatMessage, ModelDescriptor, ProviderError, estimate_tokens


class OpenAICompatProvider:
    def __init__(self, name: str, base_url: str, api_key: str, descriptors: list[ModelDescriptor],
                 timeout: float = 60.0, extra_headers: dict | None = None):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._descriptors = descriptors
        self._timeout = timeout
        self._headers = {"Authorization": f"Bearer {api_key}", **(extra_headers or {})}

    def models(self) -> list[ModelDescriptor]:
        return self._descriptors

    async def available(self) -> bool:
        return bool(self.api_key)

    async def stream(self, model: str, messages: list[ChatMessage], *, temperature: float = 0.3,
                     max_tokens: int = 800) -> AsyncIterator[ChatChunk]:
        body = {"model": model, "messages": [{"role": m.role, "content": m.content} for m in messages],
                "temperature": temperature, "max_tokens": max_tokens, "stream": True,
                "stream_options": {"include_usage": True}}
        tin = sum(estimate_tokens(m.content) for m in messages)
        tout = 0
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                async with client.stream("POST", f"{self.base_url}/chat/completions", json=body,
                                         headers=self._headers) as resp:
                    if resp.status_code >= 400:
                        raise ProviderError(f"{self.name} HTTP {resp.status_code}: {(await resp.aread())[:300]!r}")
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        evt = json.loads(data)
                        if usage := evt.get("usage"):
                            tin, tout = usage.get("prompt_tokens", tin), usage.get("completion_tokens", tout)
                        for choice in evt.get("choices", []):
                            delta = (choice.get("delta") or {}).get("content")
                            if delta:
                                tout += estimate_tokens(delta) if not usage else 0
                                yield ChatChunk(delta=delta)
        except httpx.HTTPError as e:
            raise ProviderError(f"{self.name} transport error: {e}") from e
        yield ChatChunk(done=True, tokens_in=tin, tokens_out=tout)


def openai_descriptors(model: str) -> list[ModelDescriptor]:
    return [ModelDescriptor(provider="openai", model=model, reasoning=4, coding=4, vision=True, tool_calling=True,
                            context_window=128_000, cost_in_per_1k=0.00015, cost_out_per_1k=0.0006, tier="paid",
                            max_sensitivity=Sensitivity.CONFIDENTIAL)]


def openrouter_descriptors(model: str) -> list[ModelDescriptor]:
    free = model.endswith(":free")
    return [ModelDescriptor(provider="openrouter", model=model, reasoning=3, coding=3, tool_calling=True,
                            context_window=32_000, tier="free" if free else "paid",
                            cost_in_per_1k=0 if free else 0.0005, cost_out_per_1k=0 if free else 0.0015,
                            max_sensitivity=Sensitivity.INTERNAL)]


def groq_descriptors(model: str) -> list[ModelDescriptor]:
    return [ModelDescriptor(provider="groq", model=model, reasoning=3, coding=3, tool_calling=True,
                            context_window=32_000, latency_class="low", tier="free",
                            max_sensitivity=Sensitivity.INTERNAL, daily_quota=1000)]
