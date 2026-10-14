from __future__ import annotations

import asyncio
import os
import subprocess
import sys

import pytest

from nodo.providers.claude_cli import ClaudeCodeProvider
from nodo.providers.cli_process import run_cli
from nodo.providers.codex_cli import CodexCliProvider


async def test_cli_keeps_input_output_and_exit_code_across_polling(tmp_path):
    code = ("import sys,time; text=sys.stdin.buffer.read(); time.sleep(.2); "
            "sys.stdout.buffer.write(text); sys.stderr.write('diagnostic'); sys.exit(7)")
    result = await run_cli([sys.executable, "-c", code], "Ciao, è una prova".encode(), 5,
                           env=dict(os.environ), cwd=tmp_path)
    assert result.returncode == 7
    assert result.stdout.decode() == "Ciao, è una prova"
    assert result.stderr == b"diagnostic"


@pytest.mark.parametrize("provider_cls", [ClaudeCodeProvider, CodexCliProvider])
async def test_cancellation_stops_only_the_owned_process_tree(tmp_path, provider_cls):
    ready, late = tmp_path / "ready", tmp_path / "should-not-exist"
    child = f"import time; from pathlib import Path; time.sleep(.8); Path({str(late)!r}).write_text('leaked')"
    parent = (f"import subprocess,sys,time; from pathlib import Path; "
              f"subprocess.Popen([sys.executable,'-c',{child!r}]); "
              f"Path({str(ready)!r}).write_text('ready'); time.sleep(30)")
    provider = provider_cls(executable=sys.executable)
    task = asyncio.create_task(provider._exec([sys.executable, "-c", parent], None, 30))
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        async with asyncio.timeout(4):
            while not ready.exists():
                await asyncio.sleep(.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 7)
        await asyncio.sleep(.9)
        assert not late.exists(), "the CLI child survived cancellation"
        assert unrelated.poll() is None, "an unrelated process was killed"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        unrelated.terminate()
        unrelated.wait(timeout=5)


async def test_timeout_stops_the_cli(tmp_path):
    late = tmp_path / "late"
    code = f"import time; from pathlib import Path; time.sleep(.5); Path({str(late)!r}).write_text('leaked')"
    with pytest.raises(subprocess.TimeoutExpired):
        await run_cli([sys.executable, "-c", code], None, .1, env=dict(os.environ), cwd=tmp_path)
    await asyncio.sleep(.6)
    assert not late.exists()
