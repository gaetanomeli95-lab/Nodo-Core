"""Anthropic (Claude) provider over the native Messages API with streaming. Strong at code: used by the night
shift's fix agent when configured. Paid tier: never selected in FREE mode unless explicitly allowed."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from nodo.db.models import Sensitivity
from nodo.providers.base import ChatChunk, ChatMessage, ModelDescriptor, ProviderError, estimate_tokens


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str, base_url: str = "https://api.anthropic.com/v1",
                 timeout: float = 120.0, transport: httpx.AsyncBaseTransport | None = None):
        self.api_key, self.model, self.base_url = api_key, model, base_url.rstrip("/")
        self._timeout, self._transport = timeout, transport

    def models(self) -> list[ModelDescriptor]:
        return anthropic_descriptors(self.model)

    async def available(self) -> bool:
        return bool(self.api_key)

    async def stream(self, model: str, messages: list[ChatMessage], *, temperature: float = 0.3,
                     max_tokens: int = 800) -> AsyncIterator[ChatChunk]:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        turns: list[dict] = []
        for m in messages:
            if m.role == "system":
                continue
            role = "assistant" if m.role == "assistant" else "user"
            if turns and turns[-1]["role"] == role:  # the API requires alternating roles
                turns[-1]["content"] += "\n\n" + m.content
            else:
                turns.append({"role": role, "content": m.content})
        body = {"model": model, "max_tokens": max_tokens, "temperature": temperature, "stream": True,
                "messages": turns or [{"role": "user", "content": "."}]}
        if system:
            body["system"] = system
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        tin = sum(estimate_tokens(m.content) for m in messages)
        tout = 0
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                async with client.stream("POST", f"{self.base_url}/messages", json=body, headers=headers) as resp:
                    if resp.status_code >= 400:
                        raise ProviderError(f"anthropic HTTP {resp.status_code}: {(await resp.aread())[:300]!r}")
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        evt = json.loads(line[5:].strip() or "{}")
                        kind = evt.get("type")
                        if kind == "message_start":
                            tin = ((evt.get("message") or {}).get("usage") or {}).get("input_tokens", tin)
                        elif kind == "content_block_delta":
                            delta = evt.get("delta") or {}
                            if delta.get("type") == "text_delta" and delta.get("text"):
                                yield ChatChunk(delta=delta["text"])
                        elif kind == "message_delta":
                            tout = (evt.get("usage") or {}).get("output_tokens", tout)
                        elif kind == "error":
                            raise ProviderError(f"anthropic stream error: {evt.get('error')}")
        except httpx.HTTPError as e:
            raise ProviderError(f"anthropic transport error: {e}") from e
        yield ChatChunk(done=True, tokens_in=tin, tokens_out=tout)


def anthropic_descriptors(model: str) -> list[ModelDescriptor]:
    # pricing is an estimate for the cost meter (USD per 1k tokens); adjust NODO_ANTHROPIC_MODEL as you prefer
    cheap = "haiku" in model
    return [ModelDescriptor(provider="anthropic", model=model, reasoning=4 if cheap else 5, coding=4 if cheap else 5,
                            vision=True, tool_calling=True, context_window=200_000, tier="paid",
                            cost_in_per_1k=0.001 if cheap else 0.003, cost_out_per_1k=0.005 if cheap else 0.015,
                            max_sensitivity=Sensitivity.CONFIDENTIAL)]
