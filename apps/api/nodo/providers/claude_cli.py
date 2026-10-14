"""Claude through your Claude subscription (Pro/Max): runs the locally installed Claude Code CLI in print mode
(`claude -p`), which Anthropic allows to use with subscription limits. No API key, no extra bill.

* Text: `claude -p --output-format json` with NODO's own system prompt (passed as a file) and ALL Claude Code
  tools disabled (`--tools ""`, no MCP, no skills): it is used purely as a language model.
* Function calling: prompt-based. The tool catalog goes in the system prompt and Claude answers with one JSON
  object {"say": "...", "calls": [{"name": ..., "arguments": {...}}]} that NODO parses and executes.
* Runs in an empty working directory so no project settings/CLAUDE.md are picked up, and without
  ANTHROPIC_API_KEY in the environment so the subscription login is what gets used.
* The process runs in a worker thread (works with any asyncio loop on Windows) without opening a console window.
  Optional flags that an older Claude Code does not know are dropped automatically.

Each call starts the CLI (a few seconds): fine for the brain and the night shift, slower than an API for voice.
"""
from __future__ import annotations

import asyncio
import json
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
from nodo.providers.text_tools import TOOL_PROTOCOL, _parse, _render_transcript  # noqa: F401

OPTIONAL_FLAGS = ["--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence", "--effort"]


def _find_claude() -> str | None:
    found = shutil.which("claude") or shutil.which("claude.exe") or shutil.which("claude.cmd")
    if found:
        return found
    home = Path.home()   # native installer location, in case NODO started before PATH was refreshed
    for p in (home / ".local" / "bin" / "claude.exe", home / ".local" / "bin" / "claude",
              home / "AppData" / "Roaming" / "npm" / "claude.cmd"):
        if p.exists():
            return str(p)
    return None


class ClaudeCodeProvider:
    name = "claude-subscription"

    def __init__(self, model: str = "sonnet", executable: str | None = None, timeout: float = 180.0,
                 effort: str | None = "low"):
        self.model, self.effort = model, effort
        self.executable = executable or _find_claude()
        self.timeout = timeout
        self._auth_checked_at: float | None = None   # never checked yet
        self._auth_ok = False
        self._dropped: set[str] = set()
        self._cwd = Path(tempfile.gettempdir()) / "nodo-claude-cwd"
        self._cwd.mkdir(parents=True, exist_ok=True)
        # leftovers of earlier runs (crash, Windows delete-lock). NEVER touch fresh files: the temp dir is
        # shared by every provider instance, so a peer could be writing its prompt file right now.
        for stale in self._cwd.glob("nodo-sp-*.txt"):
            try:
                if time.time() - stale.stat().st_mtime > 600:
                    stale.unlink()
            except OSError:
                pass

    @staticmethod
    async def _remove(path: str) -> None:
        """Windows keeps a just-finished child's file handles for a beat: deleting right away can raise
        PermissionError and leak the system prompt. Retry briefly instead of leaving nodo-sp-*.txt behind."""
        for _ in range(100):
            try:
                os.unlink(path)
                return
            except FileNotFoundError:
                return
            except OSError:
                await asyncio.sleep(0.05)

    def models(self) -> list[ModelDescriptor]:
        # covered by the subscription: no per-call cost for NODO's meter; Claude-grade reasoning and coding
        return [ModelDescriptor(provider=self.name, model=self.model, reasoning=5, coding=5, tool_calling=True,
                                context_window=200_000, latency_class="high", tier="free", is_local=False,
                                max_sensitivity=Sensitivity.CLIENT_PRIVATE)]  # your own account: OK for client data

    def _env(self) -> dict:
        env = dict(os.environ)
        env.pop("ANTHROPIC_API_KEY", None)        # make sure the subscription login is used
        return env

    def _exec(self, args: list[str], stdin: bytes | None, timeout: float,
              cwd: Path | None = None) -> subprocess.CompletedProcess:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        return subprocess.run(args, input=stdin, capture_output=True, env=self._env(), cwd=cwd or self._cwd,
                              timeout=timeout, creationflags=flags)

    async def available(self) -> bool:
        if not self.executable:
            return False
        if self._auth_checked_at is not None and time.monotonic() - self._auth_checked_at < 600:
            return self._auth_ok
        self._auth_checked_at = time.monotonic()
        try:
            proc = await asyncio.to_thread(self._exec, [self.executable, "auth", "status"], None, 30)
            out = proc.stdout.decode("utf-8", "replace")
            info = json.loads(out[out.index("{"):]) if "{" in out else {}
            self._auth_ok = proc.returncode == 0 and bool(info.get("loggedIn", True))
        except (OSError, subprocess.TimeoutExpired, ValueError):
            self._auth_ok = False
        return self._auth_ok

    async def _run(self, system: str, prompt: str, model: str, effort: str | None = None) -> dict:
        if not self.executable:
            raise ProviderError("Claude Code non installato (comando 'claude' non trovato)")
        fd, sp_path = tempfile.mkstemp(prefix="nodo-sp-", suffix=".txt", dir=self._cwd)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(system)
            for _ in range(len(OPTIONAL_FLAGS) + 1):
                args = [self.executable, "-p", "--output-format", "json", "--model", model,
                        "--system-prompt-file", sp_path, "--tools", ""]
                args += [f for f in OPTIONAL_FLAGS[:3] if f not in self._dropped]
                if effort and "--effort" not in self._dropped:
                    args += ["--effort", effort]   # conversation: answer fast; code (stream) keeps the default
                try:
                    proc = await asyncio.to_thread(self._exec, args, prompt.encode("utf-8"), self.timeout)
                except subprocess.TimeoutExpired as e:
                    raise ProviderError("Claude (abbonamento) non ha risposto in tempo") from e
                except OSError as e:
                    raise ProviderError(f"impossibile avviare Claude Code: {e}") from e
                err = proc.stderr.decode("utf-8", "replace")
                unknown = re.search(r"unknown option '(--[\w-]+)'", err)
                if proc.returncode != 0 and unknown and unknown.group(1) in OPTIONAL_FLAGS:
                    self._dropped.add(unknown.group(1))   # older Claude Code: retry without that flag
                    continue
                break
        finally:
            await self._remove(sp_path)
        text = proc.stdout.decode("utf-8", "replace").strip()
        try:
            data = json.loads(text[text.index("{"):]) if text else {}
        except (ValueError, json.JSONDecodeError):
            data = {}
        if proc.returncode != 0 or data.get("is_error") or not data:
            detail = str(data.get("result") or "") or err[-300:] or text[-300:]
            if "login" in detail.lower() or "auth" in detail.lower():
                self._auth_ok, self._auth_checked_at = False, time.monotonic()
            raise ProviderError(f"Claude (abbonamento) errore: {detail.strip()}")
        return data

    AGENT_TOOLS = "Read,Write,Edit,Glob,Grep,WebSearch,WebFetch"   # no shell: it can read, write and search, not run

    async def run_agent(self, task: str, workdir: Path, system: str, timeout: float = 900.0) -> str:
        """A longer job: Claude Code with file and web tools, confined to `workdir` (no shell, no MCP). Returns its
        final report. Used for «prepara un progetto», «analizza questo file», «fai una ricerca approfondita»."""
        if not self.executable:
            raise ProviderError("Claude Code non installato (comando 'claude' non trovato)")
        workdir.mkdir(parents=True, exist_ok=True)
        fd, sp_path = tempfile.mkstemp(prefix="nodo-sp-", suffix=".txt", dir=self._cwd)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(system)
            args = [self.executable, "-p", "--output-format", "json", "--model", self.model,
                    "--system-prompt-file", sp_path, "--tools", self.AGENT_TOOLS, "--permission-mode", "acceptEdits",
                    "--allowedTools", "WebSearch,WebFetch,Read,Glob,Grep",   # web and reading need no confirmation
                    "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"]
            for _ in range(len(OPTIONAL_FLAGS) + 1):
                try:
                    proc = await asyncio.to_thread(self._exec, args, task.encode("utf-8"), timeout, workdir)
                except subprocess.TimeoutExpired as e:
                    raise ProviderError("il lavoro ha richiesto troppo tempo e l'ho interrotto") from e
                except OSError as e:
                    raise ProviderError(f"impossibile avviare Claude Code: {e}") from e
                err = proc.stderr.decode("utf-8", "replace")
                unknown = re.search(r"unknown option '(--[\w-]+)'", err)
                if proc.returncode != 0 and unknown and unknown.group(1) in args:
                    args.remove(unknown.group(1))     # older Claude Code: retry without that flag
                    continue
                break
        finally:
            await self._remove(sp_path)
        text = proc.stdout.decode("utf-8", "replace").strip()
        try:
            data = json.loads(text[text.index("{"):]) if text else {}
        except (ValueError, json.JSONDecodeError):
            data = {}
        if proc.returncode != 0 or data.get("is_error") or not data:
            raise ProviderError(f"Claude (abbonamento) errore: {(str(data.get('result') or '') or err[-300:] or text[-300:]).strip()}")
        return str(data.get("result") or "").strip()

    @staticmethod
    def _usage(data: dict, fallback_in: str, fallback_out: str) -> tuple[int, int]:
        u = data.get("usage") or {}
        tin = (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0) \
            + (u.get("cache_creation_input_tokens") or 0)
        return tin or estimate_tokens(fallback_in), u.get("output_tokens") or estimate_tokens(fallback_out)

    async def stream(self, model: str, messages: list[ChatMessage], *, temperature: float = 0.3,
                     max_tokens: int = 800) -> AsyncIterator[ChatChunk]:
        system = "\n\n".join(m.content for m in messages if m.role == "system") or "Rispondi in italiano."
        prompt = "\n\n".join(m.content for m in messages if m.role != "system")
        data = await self._run(system, prompt, model)
        result = str(data.get("result") or "")
        tin, tout = self._usage(data, prompt, result)
        yield ChatChunk(delta=result)
        yield ChatChunk(done=True, tokens_in=tin, tokens_out=tout)

    async def complete_tools(self, model: str, messages: list[dict], tools: list[FunctionSpec], *,
                             temperature: float = 0.4, max_tokens: int = 1200) -> ToolTurn:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        if tools:
            catalog = "\n".join(f"- {t.name}: {t.description} | {json.dumps(t.parameters, ensure_ascii=False)}"
                                for t in tools)
            system += TOOL_PROTOCOL.format(catalog=catalog)
        prompt = _render_transcript(messages)
        data = await self._run(system, prompt, model, effort=self.effort)
        say, calls = _parse(str(data.get("result") or ""))
        known = {t.name for t in tools}
        tin, tout = self._usage(data, prompt + system, say)
        return ToolTurn(say, [ToolCall(f"call_{uuid.uuid4().hex[:8]}", n, a) for n, a in calls if n in known],
                        tin, tout)
