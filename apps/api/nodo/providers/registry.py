"""Builds the provider list from configuration. Adding a provider = add a branch here + a module."""
from __future__ import annotations

from nodo.config import Settings
from nodo.providers.base import ModelProvider
from nodo.providers.deterministic import DeterministicProvider
from nodo.providers.ollama import OllamaProvider
from nodo.providers.openai_compat import (
    OpenAICompatProvider,
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
    if s.openai_api_key:
        providers.append(OpenAICompatProvider("openai", s.openai_base_url, s.openai_api_key,
                                              openai_descriptors(s.openai_model)))
    providers.append(DeterministicProvider())  # always-available grounded fallback
    return providers
