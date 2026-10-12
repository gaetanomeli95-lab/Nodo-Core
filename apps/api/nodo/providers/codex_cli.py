"""ChatGPT through your ChatGPT subscription (Plus/Pro): runs the locally installed Codex CLI in non-interactive
mode (`codex exec`), logged in with "Sign in with ChatGPT". No API key, it uses your plan's Codex limits.

* Used purely as a language model: read-only sandbox, empty working directory, no git checks, the session is not
  saved, and the prompt tells it not to run commands. The final message is read from `--output-last-message`.
* Function calling: the same prompt-based JSON protocol as the Claude subscription (nodo/providers/text_tools.py).
* OPENAI_API_KEY is removed from the environment so the ChatGPT login is what gets used.
* Runs in a worker thread without a console window on Windows; optional flags an older Codex does not know are
  dropped automatically.

OpenAI documents the ChatGPT sign-in for local use of Codex; NODO runs on your own PC for you only. If the
subscription stops answering (limits, login expired) NODO simply moves on to the next model.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

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
from nodo.providers.text_tools import TOOL_PROTOCOL, _parse, _render_transcript, catalog_text

OPTIONAL_FLAGS = ["--ephemeral", "--color"]
NO_TOOLS = ("Rispondi direttamente con il testo richiesto. Non eseguire comandi, non leggere o scrivere file, "
            "non usare strumenti di Codex: sei usato solo per ragionare e scrivere.")


def _find_codex() -> str | None:
    found = shutil.which("codex") or shutil.which("codex.cmd") or shutil.which("codex.exe")
    if found:
        return found
    home = Path.home()
    for p in (home / "AppData" / "Roaming" / "npm" / "codex.cmd", home / ".local" / "bin" / "codex.exe",
              home / ".local" / "bin" / "codex", home / ".codex" / "bin" / "codex.exe"):
        if p.exists():
            return str(p)
    return None


class CodexCliProvider:
    name = "chatgpt-subscription"

    def __init__(self, model: str | None = None, executable: str | None = None, timeout: float = 240.0):
        self.model = model or ""                       # empty = the default model of your ChatGPT plan
        self.executable = executable or _find_codex()
        self.timeout = timeout
        self._auth_checked_at: float | None = None   # never checked yet
        self._auth_ok = False
        self._dropped: set[str] = set()
        self._cwd = Path(tempfile.gettempdir()) / "nodo-codex-cwd"
        self._cwd.mkdir(parents=True, exist_ok=True)

    def models(self) -> list[ModelDescriptor]:
        return [ModelDescriptor(provider=self.name, model=self.model or "chatgpt", reasoning=5, coding=5,
                                tool_calling=True, context_window=200_000, latency_class="high", tier="free",
                                is_local=False, max_sensitivity=Sensitivity.CLIENT_PRIVATE)]  # your own account: OK for client data

    def _env(self) -> dict:
        env = dict(os.environ)
        env.pop("OPENAI_API_KEY", None)        # make sure the ChatGPT login is used
        env.pop("CODEX_API_KEY", None)
        env["NO_COLOR"] = "1"
        return env

    def _exec(self, args: list[str], stdin: bytes | None, timeout: float) -> subprocess.CompletedProcess:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        return subprocess.run(args, input=stdin, capture_output=True, env=self._env(), cwd=self._cwd,
                              timeout=timeout, creationflags=flags)

    async def available(self) -> bool:
        if not self.executable:
            return False
        if self._auth_checked_at is not None and time.monotonic() - self._auth_checked_at < 600:
            return self._auth_ok
        self._auth_checked_at = time.monotonic()
        try:
            proc = await asyncio.to_thread(self._exec, [self.executable, "login", "status"], None, 30)
            out = (proc.stdout + proc.stderr).decode("utf-8", "replace").lower()
            self._auth_ok = proc.returncode == 0 and "not logged" not in out
        except (OSError, subprocess.TimeoutExpired):
            self._auth_ok = False
        return self._auth_ok

    async def _run(self, prompt: str, model: str) -> str:
        if not self.executable:
            raise ProviderError("Codex CLI non installato (comando 'codex' non trovato)")
        fd, out_path = tempfile.mkstemp(prefix="nodo-codex-", suffix=".txt", dir=self._cwd)
        os.close(fd)
        try:
            for _ in range(len(OPTIONAL_FLAGS) + 1):
                args = [self.executable, "exec", "--sandbox", "read-only", "--skip-git-repo-check",
                        "--output-last-message", out_path]
                if "--ephemeral" not in self._dropped:
                    args.append("--ephemeral")
                if "--color" not in self._dropped:
                    args += ["--color", "never"]
                if model and model != "chatgpt":
                    args += ["--model", model]
                args.append("-")                       # prompt from stdin
                try:
                    proc = await asyncio.to_thread(self._exec, args, prompt.encode("utf-8"), self.timeout)
                except subprocess.TimeoutExpired as e:
                    raise ProviderError("ChatGPT (abbonamento) non ha risposto in tempo") from e
                except OSError as e:
                    raise ProviderError(f"impossibile avviare Codex: {e}") from e
                err = proc.stderr.decode("utf-8", "replace")
                bad = re.search(r"(?:unexpected argument|unknown option|unrecognized)[^']*'(--[\w-]+)", err)
                if proc.returncode != 0 and bad and bad.group(1) in OPTIONAL_FLAGS:
                    self._dropped.add(bad.group(1))   # older Codex: retry without that flag
                    continue
                break
            text = Path(out_path).read_text(encoding="utf-8", errors="replace").strip() \
                if Path(out_path).exists() else ""
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass
        if proc.returncode != 0 or not text:
            detail = (err or proc.stdout.decode("utf-8", "replace"))[-300:].strip()
            if "login" in detail.lower() or "auth" in detail.lower():
                self._auth_ok, self._auth_checked_at = False, time.monotonic()
            raise ProviderError(f"ChatGPT (abbonamento) errore: {detail or 'nessuna risposta'}")
        return text

    async def stream(self, model: str, messages: list[ChatMessage], *, temperature: float = 0.3,
                     max_tokens: int = 800) -> AsyncIterator[ChatChunk]:
        system = "\n\n".join(m.content for m in messages if m.role == "system") or "Rispondi in italiano."
        body = "\n\n".join(m.content for m in messages if m.role != "system")
        prompt = f"{system}\n\n{NO_TOOLS}\n\n{body}"
        text = await self._run(prompt, model)
        yield ChatChunk(delta=text)
        yield ChatChunk(done=True, tokens_in=estimate_tokens(prompt), tokens_out=estimate_tokens(text))

    async def complete_tools(self, model: str, messages: list[dict], tools: list[FunctionSpec], *,
                             temperature: float = 0.4, max_tokens: int = 1200) -> ToolTurn:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        if tools:
            system += TOOL_PROTOCOL.format(catalog=catalog_text(tools))
        prompt = f"{system}\n\n{NO_TOOLS}\n\n## Conversazione\n{_render_transcript(messages)}"
        text = await self._run(prompt, model)
        say, calls = _parse(text)
        known = {t.name for t in tools}
        return ToolTurn(say, [ToolCall(f"call_{uuid.uuid4().hex[:8]}", n, a) for n, a in calls if n in known],
                        estimate_tokens(prompt), estimate_tokens(text))
