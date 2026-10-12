"""OpenAI-compatible chat provider. Covers OpenAI, OpenRouter, Groq and any `/v1/chat/completions` server."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from nodo.db.models import Sensitivity
from nodo.providers.base import (
    ChatChunk,
    ChatMessage,
    FunctionSpec,
    ModelDescriptor,
    ProviderError,
    ToolCall,
    ToolTurn,
    estimate_tokens,
)


class OpenAICompatProvider:
    def __init__(self, name: str, base_url: str, api_key: str, descriptors: list[ModelDescriptor],
                 timeout: float = 60.0, extra_headers: dict | None = None,
                 transport: httpx.AsyncBaseTransport | None = None, reasoning_effort: str | None = None):
        self.name = name
        self._transport = transport
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._descriptors = descriptors
        self._timeout = timeout
        self._headers = {"Authorization": f"Bearer {api_key}", **(extra_headers or {})}
        # Thinking models (Gemini 3.x) bill their hidden reasoning inside `max_tokens`: with the default budget
        # a 900-token request can come back EMPTY, finish_reason=length. "none" disables it; "low|medium|high" keep it.
        self._reasoning_effort = reasoning_effort
        self._signatures: dict[str, dict] = {}   # tool_call id -> extra_content to echo back (Gemini thought_signature)

    def _body(self, model: str, messages: list[dict], temperature: float, max_tokens: int) -> dict:
        body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        if self._reasoning_effort:
            body["reasoning_effort"] = self._reasoning_effort
        return body

    def models(self) -> list[ModelDescriptor]:
        return self._descriptors

    async def available(self) -> bool:
        return bool(self.api_key)

    async def stream(self, model: str, messages: list[ChatMessage], *, temperature: float = 0.3,
                     max_tokens: int = 800) -> AsyncIterator[ChatChunk]:
        body = {**self._body(model, [{"role": m.role, "content": m.content} for m in messages], temperature,
                             max_tokens), "stream": True, "stream_options": {"include_usage": True}}
        tin = sum(estimate_tokens(m.content) for m in messages)
        tout = 0
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
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


    async def complete_tools(self, model: str, messages: list[dict], tools: list[FunctionSpec], *,
                             temperature: float = 0.4, max_tokens: int = 1200) -> ToolTurn:
        """Non-streaming chat with function calling (OpenAI-compatible: OpenAI, Groq, OpenRouter, Gemini)."""
        wire = []
        for m in messages:
            if m["role"] == "assistant" and m.get("tool_calls"):
                wire.append({"role": "assistant", "content": m.get("content") or None, "tool_calls": [
                    {"id": c["id"], "type": "function",
                     "function": {"name": c["name"], "arguments": json.dumps(c["arguments"], ensure_ascii=False)},
                     # Gemini 3.x rejects a follow-up turn whose earlier function calls lack their thought_signature
                     **({"extra_content": self._signatures[c["id"]]} if c["id"] in self._signatures else {})}
                    for c in m["tool_calls"]]})
            elif m["role"] == "tool":
                wire.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
            else:
                wire.append({"role": m["role"], "content": m["content"]})
        body = self._body(model, wire, temperature, max_tokens)
        if tools:
            body["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                               "parameters": t.parameters}} for t in tools]
            body["tool_choice"] = "auto"
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=getattr(self, "_transport", None)) as client:
                r = await client.post(f"{self.base_url}/chat/completions", json=body, headers=self._headers)
        except httpx.HTTPError as e:
            raise ProviderError(f"{self.name} transport error: {e}") from e
        if r.status_code >= 400:
            raise ProviderError(f"{self.name} HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        msg = ((data.get("choices") or [{}])[0].get("message") or {})
        calls = []
        for i, c in enumerate(msg.get("tool_calls") or []):
            fn = c.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_raw": fn.get("arguments")}
            call_id = c.get("id") or f"call_{i}"
            if c.get("extra_content"):
                if len(self._signatures) >= 512:
                    self._signatures.pop(next(iter(self._signatures)))
                self._signatures[call_id] = c["extra_content"]
            calls.append(ToolCall(call_id, fn.get("name", ""), args if isinstance(args, dict) else {}))
        usage = data.get("usage") or {}
        return ToolTurn(msg.get("content") or "", calls, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))


def openai_descriptors(model: str) -> list[ModelDescriptor]:
    return [ModelDescriptor(provider="openai", model=model, reasoning=4, coding=4, vision=True, tool_calling=True,
                            context_window=128_000, cost_in_per_1k=0.00015, cost_out_per_1k=0.0006, tier="paid",
                            max_sensitivity=Sensitivity.CONFIDENTIAL)]


def openrouter_descriptors(model: str) -> list[ModelDescriptor]:
    free = model.endswith(":free")
    # paid OpenRouter models are typically frontier models (e.g. anthropic/..., openai/...): good enough for code fixes
    return [ModelDescriptor(provider="openrouter", model=model, reasoning=3 if free else 4, coding=3 if free else 4,
                            tool_calling=True, context_window=32_000 if free else 128_000, tier="free" if free else "paid",
                            cost_in_per_1k=0 if free else 0.0005, cost_out_per_1k=0 if free else 0.0015,
                            max_sensitivity=Sensitivity.INTERNAL)]


def groq_descriptors(model: str) -> list[ModelDescriptor]:
    return [ModelDescriptor(provider="groq", model=model, reasoning=3, coding=3, tool_calling=True,
                            context_window=32_000, latency_class="low", tier="free",
                            max_sensitivity=Sensitivity.INTERNAL, daily_quota=1000)]


def gemini_descriptors(model: str) -> list[ModelDescriptor]:
    """Google Gemini through its OpenAI-compatible endpoint. The free tier covers Flash models (with daily limits)."""
    flash = "flash" in model
    return [ModelDescriptor(provider="gemini", model=model, reasoning=4, coding=4 if not flash else 3, vision=True,
                            tool_calling=True, context_window=1_000_000, latency_class="low",
                            tier="free" if flash else "paid", cost_in_per_1k=0 if flash else 0.00125,
                            cost_out_per_1k=0 if flash else 0.01, max_sensitivity=Sensitivity.INTERNAL,
                            daily_quota=None)]
