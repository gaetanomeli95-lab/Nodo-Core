"""Central configuration. Everything comes from environment variables (see .env.example)."""
from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class NodoMode(StrEnum):
    FREE = "FREE"            # never incur paid usage
    BALANCED = "BALANCED"    # prefer free, allow paid when capability requires it
    PERFORMANCE = "PERFORMANCE"
    PRIVATE = "PRIVATE"      # local-only inference
    CUSTOM = "CUSTOM"


class Environment(StrEnum):
    DEV = "DEV"
    TEST = "TEST"
    LOCAL = "LOCAL"
    PRODUCTION = "PRODUCTION"
    DEMO = "DEMO"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="NODO_", extra="ignore")

    env: Environment = Environment.DEV
    mode: NodoMode = NodoMode.FREE
    database_url: str = "sqlite:///./nodo.db"
    api_token: str | None = None          # single-user dev auth; None disables auth (DEV/TEST only)
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    default_language: str = "it"
    seed_demo_data: bool = False

    # --- Model providers (all optional; absence simply removes the provider) ---
    ollama_base_url: str | None = None
    ollama_model: str = "llama3.2"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com/v1"
    openrouter_api_key: str | None = None
    openrouter_model: str = "meta-llama/llama-3.3-70b-instruct:free"
    groq_api_key: str | None = None
    groq_model: str = "llama-3.3-70b-versatile"
    allow_paid_in_free_mode: bool = False  # explicit override; default is NEVER charge in FREE

    # --- Voice ---
    stt_provider: str = "browser"   # browser | openai | fake
    tts_provider: str = "browser"   # browser | openai | fake
    tts_voice: str = "alloy"

    # --- Tools ---
    github_token: str | None = None
    github_connector: str = "auto"  # auto | http | fake

    # --- Budgets ---
    agent_max_model_calls: int = 4
    agent_max_tool_calls: int = 8
    agent_max_seconds: int = 60
    agent_max_depth: int = 2


@lru_cache
def get_settings() -> Settings:
    return Settings()
