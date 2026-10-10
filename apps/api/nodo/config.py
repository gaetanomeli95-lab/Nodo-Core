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
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8-sig", env_prefix="NODO_",
                                      extra="ignore")

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
    gemini_api_key: str | None = None            # Google AI Studio key: free tier on Flash models
    gemini_model: str = "gemini-2.5-flash"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-5"
    anthropic_base_url: str = "https://api.anthropic.com/v1"
    allow_paid_in_free_mode: bool = False  # explicit override; default is NEVER charge in FREE
    # Claude through your Claude Pro/Max subscription: uses the Claude Code CLI installed and logged in on this PC
    # ("claude" command). No API key and no extra cost: it uses your plan's limits.
    claude_subscription: bool = True            # used automatically when the "claude" command is found
    claude_cli_model: str = "sonnet"            # sonnet | opus | haiku (or a full model name)
    claude_cli_path: str | None = None          # full path to claude(.exe/.cmd) if it is not on PATH
    claude_cli_effort: str = "low"              # conversation speed: low (fast) | medium | high (code fixes: default)

    # --- Brain: conversation with tools (nodo/brain) ---
    brain: str = "auto"                         # auto (on when a model with tool calling is available) | on | off
    brain_max_steps: int = 6                    # tool rounds per message
    brain_history: int = 16                     # previous messages of the conversation it remembers

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

    # --- Vercel ---
    vercel_token: str | None = None
    vercel_team_id: str | None = None           # team_xxx; empty = personal account
    vercel_connector: str = "auto"              # auto (http, fake in TEST/DEMO) | http | fake
    vercel_protection_bypass: str | None = None  # "Protection Bypass for Automation" secret to test protected previews

    # --- Night shift (docs/NIGHT_SHIFT.md) ---
    night_enabled: bool = True                  # background scheduler inside the API process
    timezone: str = "Europe/Rome"
    night_start: str = "01:00"                  # full night run (all checks + fixes + drafts)
    night_end: str = "07:00"
    night_interval_minutes: int = 60            # light re-checks inside the night window
    day_interval_minutes: int = 0               # light checks during the day (0 = off)
    briefing_time: str = "07:30"                # morning briefing composed (and sent, if a notifier is set)
    night_auto_rollback: bool = True            # production down after a deploy -> instant rollback (reversible)
    night_auto_redeploy: bool = True            # failed build with a transient error -> one redeploy
    night_fix_prs: bool = True                  # prepare fix pull requests (merge ALWAYS needs your approval)
    night_fix_allow_paid: bool = True           # fixes may use a paid model even in FREE mode (capped below)
    night_max_cost_usd: float = 1.0             # hard cap on model spend per night
    night_max_fixes: int = 3                    # max fix proposals per night
    night_drafts: bool = True                   # prepare content drafts for planned ideas (needs a language model)
    night_max_drafts: int = 3
    night_preview_wait_seconds: int = 360       # how long to wait for a Vercel preview before deferring the check
    night_browser_checks: bool = True           # use Playwright (if installed) to catch JS errors on key pages
    night_slow_ms: int = 3000                   # a page slower than this is reported as slow
    night_auto_discover: bool = True            # import Vercel projects as monitored sites automatically
    keep_awake: bool = True                     # Windows: keep the PC from sleeping during the night window

    # --- Notifications ---
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    notify_critical: bool = True                # send an alert immediately for critical incidents (if a notifier is set)
    telegram_commands: bool = True              # two-way: answer your messages, buttons and "sì"/"no" from the phone


@lru_cache
def get_settings() -> Settings:
    return Settings()
