"""Is this brain really working? One tiny question to each cloud model («rispondi solo: ok»).

Used when a key is saved in Accessi (the panel says «funziona» or the real error, e.g. «chiave non valida»)
and a couple of minutes after NODO starts (written in nodo.log), so a wrong or expired key is found at once
instead of the day the brain is needed. The CLI programs (Claude, ChatGPT subscriptions) are not woken up.
"""
from __future__ import annotations

import asyncio
import logging

from nodo.providers.base import ChatMessage

log = logging.getLogger("nodo.brains")
SKIP = {"deterministic", "claude-subscription", "chatgpt-subscription", "ollama"}
LABEL = {"gemini": "Gemini Flash", "gemini-lite": "Gemini Flash-Lite", "groq": "Groq", "nvidia": "NVIDIA",
         "openrouter": "OpenRouter", "github-models": "GitHub Models", "cerebras": "Cerebras", "mistral": "Mistral",
         "openai": "OpenAI", "anthropic": "Anthropic"}


async def test(provider, timeout: float = 25.0) -> tuple[bool, str]:
    try:
        model = provider.models()[0].model
    except Exception as e:  # noqa: BLE001
        return False, f"nessun modello ({e})"
    text = ""
    try:
        async with asyncio.timeout(timeout):
            async for ch in provider.stream(model, [ChatMessage("user", "Rispondi solo con la parola: ok")],
                                            temperature=0, max_tokens=12):
                text += ch.delta or ""
    except TimeoutError:
        return False, "non risponde (più di 25 secondi)"
    except Exception as e:  # noqa: BLE001 - the message is what Gaetano needs to read
        msg = str(e)
        for code, say in (("401", "chiave non valida"), ("403", "chiave senza permessi"),
                          ("404", f"modello {model} non trovato"), ("429", "quota finita per adesso")):
            if code in msg:
                return False, say
        return False, msg[:160]
    return (True, "funziona") if text.strip() else (False, "risposta vuota")


async def test_all(providers) -> dict[str, str]:
    out = {}
    for p in providers:
        if p.name in SKIP:
            continue
        ok, msg = await test(p)
        out[p.name] = msg
        (log.info if ok else log.warning)("cervello %s: %s", LABEL.get(p.name, p.name), msg)
    return out
