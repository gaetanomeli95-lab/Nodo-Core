"""Gemini Live voice engine: real-time speech-to-speech, the "human" conversation.

Audio flows straight between the browser and Gemini's Live API (one WebSocket, ~1s):
  mic PCM 16kHz -> `send_realtime_input` -> Gemini hears, thinks, calls NODO's tools -> audio PCM 24kHz back.

Same safety as the text brain: tools run through nodo/brain/tools.py where the guards live in code
(an approval is executed only if it was presented and the user just said a clear yes/no).

Activation: `voice_engine` = auto|live|browser; with "auto" the engine is used when a Gemini key and the
google-genai package are present, otherwise the browser chain keeps working untouched.

The model "ladder" (idea seen in JARVIS Mark LV, reimplemented from scratch): several Live model names are
tried in order, because model names change and a key may not have them all; the first that connects wins.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

log = logging.getLogger("nodo.voice.live")

IN_MIME = "audio/pcm;rate=16000"    # what the browser microphone sends us (16-bit PCM, mono)
OUT_MIME = "audio/pcm;rate=24000"   # what Gemini Live sends back (16-bit PCM, mono)

# tried in order until one accepts the connection (names change; a key may not have them all).
# The *-preview-12/09-2025 snapshots are known to drop sessions mid-call (server-side 1007),
# so the newer live models come first and a dropped model is skipped on reconnect.
LIVE_MODELS = ("gemini-3.8-live", "gemini-3.1-flash-live-preview", "gemini-2.5-flash-native-audio-latest",
               "gemini-2.5-flash-native-audio-preview-12-2025",
               "gemini-2.5-flash-native-audio-preview-09-2025")

# returns an async context manager yielding a live session (duck-typed: send_realtime_input,
# send_client_content, send_tool_response, receive()). In tests a fake factory replaces this.
ConnectFactory = Callable[[str, str, dict], Any]
OnEvent = Callable[[dict], Awaitable[None]]

# Quotas are enforced by Google per *project*, not per API key.
# This is a process-local safeguard against repeatedly retrying a known exhausted
# credential. It is NOT a quota reset timer or a way to bypass project limits.
_QUOTA_COOLDOWN_S = 3600
_quota = {"key": None, "until": 0.0}


class LiveQuotaExceeded(RuntimeError):
    """Live service explicitly reported a quota or rate-limit failure."""


def _key_fingerprint(key: str) -> str:
    """Avoid keeping an extra copy of the raw API key in the cooldown cache."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _is_quota_error(e: Exception | str) -> bool:
    s = str(e).lower()
    # 1011 alone is a generic WebSocket internal-error status, NOT a quota verdict.
    return any(marker in s for marker in ("quota", "resource_exhausted", "rate limit",
                                          "rate_limit_exceeded", "too many requests", "429"))


def quota_note(settings) -> str | None:
    """Temporary local backoff; not a guarantee about Google's reset schedule."""
    key = settings.gemini_api_key
    if key and _quota["key"] == _key_fingerprint(key) and time.monotonic() < _quota["until"]:
        return ("Gemini Live ha segnalato un limite di quota. Voce browser attiva; "
                "controlla le quote del progetto in Google AI Studio e riprova più tardi.")
    return None


def _mark_quota(settings) -> None:
    key = settings.gemini_api_key
    if key:
        _quota["key"], _quota["until"] = _key_fingerprint(key), time.monotonic() + _QUOTA_COOLDOWN_S


class LiveDropped(Exception):
    """The live socket is gone (closed mid-send): the runtime treats it like a 'closed' event."""


def missing_reason(settings, container=None) -> str | None:
    """None = the live engine can run; otherwise the plain-Italian reason it cannot."""
    if getattr(container, "live_connect", None) is not None:
        return None                                     # test hook: a fake engine was injected
    if (settings.voice_engine or "auto") == "browser":
        return "disattivata (NODO_VOICE_ENGINE=browser)"
    if settings.env in ("TEST", "DEMO"):
        return "disattivata in test/demo"
    if not settings.gemini_api_key:
        return "manca NODO_GEMINI_API_KEY (chiave gratuita su aistudio.google.com)"
    if note := quota_note(settings):
        return note
    if importlib.util.find_spec("google.genai") is None:
        return "manca la libreria google-genai (la installa avvia-nodo)"
    return None


def _real_connect(api_key: str, model: str, config: dict):
    from google import genai  # imported only when actually used
    client = genai.Client(api_key=api_key)
    return client.aio.live.connect(model=model, config=config)


class LiveBridge:
    """One Gemini Live session bound to a voice session: audio in, normalized events out.

    Normalized events passed to `on_event`:
      {"kind": "audio", "data": bytes}         speech synthesized by the model (PCM 24kHz)
      {"kind": "input", "text": str}           piece of what the user said (input transcription)
      {"kind": "output", "text": str}          piece of what the model is saying (output transcription)
      {"kind": "tool_call", "id", "name", "args": dict}
      {"kind": "interrupted"}                  the user barged in: the model stopped talking
      {"kind": "turn_complete"}                the model finished its turn
      {"kind": "closed", "error": str}         the Live session ended (quota, timeout, network)
    """

    def __init__(self, settings, on_event: OnEvent, connect: ConnectFactory | None = None):
        self.settings = settings
        self._on_event = on_event
        self._connect = connect or _real_connect
        self.model: str | None = None
        self._session = None
        self._cm = None
        self._pump_task: asyncio.Task | None = None
        self._dead = False
        self._send_lock = asyncio.Lock()

    async def start(self, system: str, tool_decls: list[dict], language: str,
                    exclude: set[str] | None = None) -> None:
        lang = language if "-" in language else f"{language}-{language.upper()}"
        config = {
            "response_modalities": ["AUDIO"],
            "system_instruction": system,
            "speech_config": {"voice_config": {"prebuilt_voice_config":
                                               {"voice_name": self.settings.live_voice or "Kore"}},
                              "language_code": lang},
            "input_audio_transcription": {},        # server transcribes the user for the panel + logs
            "output_audio_transcription": {},       # and the model's own words for the chat
        }
        if tool_decls:
            config["tools"] = [{"function_declarations": tool_decls}]
        # sessions die after ~10 min without these: compression keeps the conversation alive,
        # resumption lets a reconnect continue the same session
        config["context_window_compression"] = {"sliding_window": {}}
        config["session_resumption"] = {}
        models = [self.settings.gemini_live_model] if self.settings.gemini_live_model else LIVE_MODELS
        models = [m for m in models if not exclude or m not in exclude]   # skip models that just dropped us
        last: Exception | None = None
        for model in models:
            try:
                self._cm = self._connect(self.settings.gemini_api_key or "test", model, config)
                self._session = await self._cm.__aenter__()
                self.model = model
                break
            except Exception as e:                  # this key has no access to this model: next rung
                last = e
                log.info("live model %s refused the connection: %s", model, str(e)[:160])
                self._cm = self._session = None
                if _is_quota_error(e):
                    _mark_quota(settings=self.settings)
                    raise LiveQuotaExceeded(
                        "Google AI Studio ha rifiutato Gemini Live per quota o limiti del progetto"
                    ) from e  # stop here: trying the other models won't resolve project quota
        if self._session is None:
            raise RuntimeError(f"nessun modello Live disponibile ({last})")
        self._pump_task = asyncio.create_task(self._pump())

    async def send_audio(self, pcm: bytes) -> None:
        if self._session is None or self._dead:
            return
        async with self._send_lock:
            try:
                await self._session.send_realtime_input(audio={"data": pcm, "mime_type": IN_MIME})
            except Exception as e:
                self._dead = True                       # the socket is gone: tell the runtime to drop us
                raise LiveDropped(f"{type(e).__name__}: {e}") from e

    async def inject(self, text: str, respond: bool) -> None:
        """Text into the conversation: context-only (respond=False) or a turn the model answers."""
        if self._session is None or self._dead:
            return
        async with self._send_lock:
            try:
                await self._session.send_client_content(
                    turns=[{"role": "user", "parts": [{"text": text}]}], turn_complete=respond)
            except Exception as e:
                self._dead = True
                raise LiveDropped(f"{type(e).__name__}: {e}") from e

    async def tool_response(self, calls: list[tuple[str, str, dict]]) -> None:
        """(call_id, name, result_dict) back to the model so it can comment on the outcome."""
        if self._session is None or self._dead:
            return
        async with self._send_lock:
            try:
                await self._session.send_tool_response(function_responses=[
                    {"id": cid, "name": name, "response": {"result": result}}
                    for cid, name, result in calls])
            except Exception as e:
                self._dead = True
                raise LiveDropped(f"{type(e).__name__}: {e}") from e

    async def close(self) -> None:
        self._dead = True
        if self._pump_task and self._pump_task is not asyncio.current_task():
            self._pump_task.cancel()                       # never await our own pump: it may be the caller
            with suppress(asyncio.CancelledError, Exception):
                await self._pump_task
        if self._cm is not None:
            with suppress(Exception):
                await self._cm.__aexit__(None, None, None)

    # ------------------------------------------------------------------ receive side
    async def _pump(self) -> None:
        try:
            async for msg in self._session.receive():
                if self._dead:
                    return
                for ev in self._normalize(msg):
                    try:
                        await self._on_event(ev)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        log.exception("live event handler failed (%s)", ev.get("kind"))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if not self._dead:
                await self._on_event({"kind": "closed", "error": f"{type(e).__name__}: {e}"})
        if not self._dead:
            await self._on_event({"kind": "closed", "error": "sessione terminata"})

    def _normalize(self, msg) -> list[dict]:
        out: list[dict] = []
        sc = getattr(msg, "server_content", None)
        if sc is not None:
            if getattr(sc, "interrupted", False):
                out.append({"kind": "interrupted"})
            for part in getattr(getattr(sc, "model_turn", None), "parts", None) or []:
                blob = getattr(part, "inline_data", None)
                if blob is not None and getattr(blob, "data", None):
                    out.append({"kind": "audio", "data": blob.data})
                if getattr(part, "text", None):
                    out.append({"kind": "output", "text": part.text})
            if t := getattr(getattr(sc, "input_transcription", None), "text", None):
                out.append({"kind": "input", "text": t})
            if t := getattr(getattr(sc, "output_transcription", None), "text", None):
                out.append({"kind": "output", "text": t})
            if getattr(sc, "turn_complete", False):
                out.append({"kind": "turn_complete"})
        for fc in getattr(getattr(msg, "tool_call", None), "function_calls", None) or []:
            out.append({"kind": "tool_call", "id": getattr(fc, "id", "") or "",
                        "name": getattr(fc, "name", ""), "args": dict(getattr(fc, "args", None) or {})})
        if getattr(msg, "go_away", None):
            out.append({"kind": "closed", "error": "la sessione Live sta per scadere"})
        return out
