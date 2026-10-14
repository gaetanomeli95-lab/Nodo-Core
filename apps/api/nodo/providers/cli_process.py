"""Cancellable CLI calls on Windows and POSIX, including their owned child processes."""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import threading
import time
from contextlib import suppress
from pathlib import Path


def _stop_owned_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        if proc.poll() is not None:
            return
        with suppress(OSError, subprocess.TimeoutExpired):
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
                           creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        with suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
    if proc.poll() is None:
        with suppress(ProcessLookupError):
            proc.kill()


async def run_cli(args: list[str], stdin: bytes | None, timeout: float, *,
                  env: dict, cwd: Path) -> subprocess.CompletedProcess:
    # Cancelling to_thread(subprocess.run) alone never stops the real CLI.
    stop = threading.Event()

    def worker():
        if stop.is_set():
            return subprocess.CompletedProcess(args, -1, b"", b"")
        flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0
        started = time.monotonic()
        with subprocess.Popen(args, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=cwd,
                              creationflags=flags, start_new_session=os.name != "nt") as proc:
            incoming = stdin
            try:
                while True:
                    remaining = timeout - (time.monotonic() - started)
                    if stop.is_set():
                        _stop_owned_tree(proc)
                        out, err = proc.communicate(timeout=5)
                        return subprocess.CompletedProcess(args, proc.returncode, out, err)
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(args, timeout)
                    try:
                        out, err = proc.communicate(input=incoming, timeout=min(.15, remaining))
                        return subprocess.CompletedProcess(args, proc.returncode, out, err)
                    except subprocess.TimeoutExpired:
                        incoming = None
            except BaseException:
                _stop_owned_tree(proc)
                with suppress(subprocess.TimeoutExpired):
                    proc.communicate(timeout=5)
                raise

    task = asyncio.create_task(asyncio.to_thread(worker))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        stop.set()
        with suppress(Exception):
            await asyncio.shield(task)
        raise
