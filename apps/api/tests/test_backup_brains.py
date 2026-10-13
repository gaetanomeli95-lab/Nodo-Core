"""Free backup brains: each key/URL adds a provider that can talk and use tools; local Ollama only when it answers."""
from __future__ import annotations

import httpx

from nodo.config import Settings
from nodo.providers.openai_compat import LocalOpenAIProvider
from nodo.providers.registry import build_providers


def _names(**kw) -> list[str]:
    return [p.name for p in build_providers(Settings(**kw))]


def test_every_key_adds_a_tool_capable_free_brain():
    names = _names(cerebras_api_key="c", mistral_api_key="m", groq_api_key="g", github_token="t", env="DEV")
    assert {"cerebras", "mistral", "groq", "github-models", "deterministic"} <= set(names)
    for p in build_providers(Settings(cerebras_api_key="c", mistral_api_key="m")):
        if p.name in ("cerebras", "mistral"):
            d = p.models()[0]
            assert d.tool_calling and d.tier == "free"


def test_no_keys_means_only_the_rule_based_fallback_or_the_subscriptions():
    assert "cerebras" not in _names(env="TEST") and "github-models" not in _names(env="TEST", github_token="t")


def test_ollama_also_becomes_a_tool_brain_and_can_be_turned_off():
    assert "ollama-tools" in _names(ollama_base_url="http://localhost:11434")
    assert "ollama-tools" not in _names(ollama_base_url="http://localhost:11434", ollama_tools=False)


async def test_local_brain_is_available_only_when_the_server_answers():
    up = LocalOpenAIProvider("ollama-tools", "http://x/v1", "llama3.2",
                             transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"data": []})))
    assert await up.available() is True

    def boom(request):
        raise httpx.ConnectError("closed")
    down = LocalOpenAIProvider("ollama-tools", "http://x/v1", "llama3.2", transport=httpx.MockTransport(boom))
    assert await down.available() is False
