"""The app wrapper: keeps the server alive, reports its state, never starts twice."""
from __future__ import annotations

import sys
import time

from nodo import tray


def _wait(cond, secs=10.0):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_supervisor_restarts_a_server_that_dies_and_stops_cleanly(tmp_path):
    log = tmp_path / "nodo.log"
    sup = tray.Supervisor([sys.executable, "-c", "print('ciao', flush=True)"], tmp_path, log, 9, backoff=0.1,
                          health=lambda p: True)
    sup.start()
    assert _wait(lambda: sup.restarts >= 2)          # it exits at once, so it must keep being started again
    sup.stop()
    assert not sup.running
    assert log.read_text().count("--- avvio") >= 2 and "ciao" in log.read_text()


def test_status_reflects_process_and_health(tmp_path):
    answering = {"v": False}
    sup = tray.Supervisor([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, tmp_path / "l.log", 9,
                          health=lambda p: answering["v"])
    assert sup.status() == "down"
    sup.start()
    assert _wait(lambda: sup.running)
    assert sup.status() == "starting"
    answering["v"] = True
    assert sup.status() == "up"
    sup.restart()
    assert _wait(lambda: sup.restarts >= 1 or sup.running)
    sup.stop()
    assert sup.status() == "down"


def test_command_and_icon():
    assert tray.server_command(8123, "py")[-2:] == ["--port", "8123"]
    for state in ("up", "starting", "down"):
        assert tray.make_icon(state).size == (64, 64)


def test_icon_file_for_shortcuts(tmp_path):
    out = tmp_path / "nodo.ico"
    assert tray.main(["--make-icon", str(out)]) == 0
    assert out.read_bytes()[:4] == b"\x00\x00\x01\x00"


def test_a_second_launch_only_opens_the_panel(monkeypatch):
    opened = []
    monkeypatch.setattr(tray, "healthy", lambda port, timeout=1.5: True)
    monkeypatch.setattr(tray, "open_panel", lambda port: opened.append(port))
    monkeypatch.setattr(tray, "Supervisor", lambda *a, **k: (_ for _ in ()).throw(AssertionError("second server")))
    assert tray.main(["--port", "8123"]) == 0 and opened == [8123]
    assert tray.main(["--port", "8123", "--no-browser"]) == 0 and opened == [8123]
