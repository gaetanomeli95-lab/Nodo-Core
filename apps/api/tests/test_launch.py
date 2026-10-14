from __future__ import annotations

import io
import json

import pytest

from nodo import launch


@pytest.mark.parametrize("relative", ["api/.env", "api/nodo/main.py", "api/pyproject.toml",
                                      "api/alembic/versions/new.py", "web/dist/index.html", "web/src/App.tsx"])
def test_fingerprint_changes_when_running_code_or_config_changes(tmp_path, relative):
    api = tmp_path / "api"
    api.mkdir()
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("before")
    before = launch.fingerprint(api)
    assert launch.fingerprint(api) == before
    path.write_text("after")
    assert launch.fingerprint(api) != before


@pytest.mark.parametrize(("state", "expected"), [
    ({"status": "ok", "service": "nodo-core", "build_id": "current", "env": "LOCAL"}, True),
    ({"status": "ok", "service": "nodo-core", "build_id": "current", "env": "DEMO"}, False),
    ({"status": "ok", "service": "other-app", "build_id": "current", "env": "LOCAL"}, False),
    ({"status": "ok", "service": "nodo-core", "build_id": "old", "env": "LOCAL"}, False),
    ({"status": "ok"}, False), ([], False),
])
def test_reuse_only_the_same_running_nodo(monkeypatch, state, expected):
    class Opener:
        def open(self, url, *, timeout):
            assert url == "http://127.0.0.1:8010/health" and timeout < 1
            return io.BytesIO(json.dumps(state).encode())
    monkeypatch.setattr(launch.urllib.request, "build_opener", lambda *a: Opener())
    monkeypatch.setattr(launch, "fingerprint", lambda: "current")
    assert launch.reusable(8010) is expected


def test_offline_instance_is_not_reused(monkeypatch):
    class Opener:
        def open(self, *a, **kw):
            raise OSError("offline")
    monkeypatch.setattr(launch.urllib.request, "build_opener", lambda *a: Opener())
    assert not launch.reusable(8000)
