"""Builds the provider list from configuration. Adding a provider = add a branch here + a module."""
from __future__ import annotations

from nodo.config import Settings
from nodo.providers.anthropic import AnthropicProvider
from nodo.providers.base import ModelProvider
from nodo.providers.claude_cli import ClaudeCodeProvider
from nodo.providers.codex_cli import CodexCliProvider
from nodo.providers.deterministic import DeterministicProvider
from nodo.providers.ollama import OllamaProvider
from nodo.providers.openai_compat import (
    OpenAICompatProvider,
    gemini_descriptors,
    groq_descriptors,
    openai_descriptors,
    openrouter_descriptors,
)


def build_providers(s: Settings) -> list[ModelProvider]:
    providers: list[ModelProvider] = []
    if s.ollama_base_url:
        providers.append(OllamaProvider(s.ollama_base_url, s.ollama_model))
    if s.groq_api_key:
        providers.append(OpenAICompatProvider("groq", "https://api.groq.com/openai/v1", s.groq_api_key,
                                              groq_descriptors(s.groq_model)))
    if s.openrouter_api_key:
        providers.append(OpenAICompatProvider("openrouter", "https://openrouter.ai/api/v1", s.openrouter_api_key,
                                              openrouter_descriptors(s.openrouter_model),
                                              extra_headers={"X-Title": "NODO CORE"}))
    if s.gemini_api_key:
        providers.append(OpenAICompatProvider("gemini", "https://generativelanguage.googleapis.com/v1beta/openai",
                                              s.gemini_api_key, gemini_descriptors(s.gemini_model),
                                              reasoning_effort=s.gemini_reasoning or None))
    if s.anthropic_api_key:
        providers.append(AnthropicProvider(s.anthropic_api_key, s.anthropic_model, s.anthropic_base_url))
    if s.claude_subscription and s.env not in ("TEST", "DEMO"):
        cc = ClaudeCodeProvider(s.claude_cli_model, s.claude_cli_path, effort=s.claude_cli_effort or None)
        if cc.executable:  # Claude Code installed: your subscription becomes NODO's best brain at no extra cost
            providers.append(cc)
    if s.chatgpt_subscription and s.env not in ("TEST", "DEMO"):
        cx = CodexCliProvider(s.codex_cli_model or None, s.codex_cli_path)
        if cx.executable:  # registered after Claude: same scores, so Claude leads and ChatGPT is the backup
            providers.append(cx)
    if s.openai_api_key:
        providers.append(OpenAICompatProvider("openai", s.openai_base_url, s.openai_api_key,
                                              openai_descriptors(s.openai_model)))
    providers.append(DeterministicProvider())  # always-available grounded fallback
    return providers
