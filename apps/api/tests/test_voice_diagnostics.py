from __future__ import annotations

import json
import os

from nodo.voice.diagnostics import collect


def test_offline_report_does_not_export_secrets_or_transcripts(tmp_path):
    api = tmp_path / "apps/api"
    api.mkdir(parents=True)
    (api / ".env").write_text("NODO_GEMINI_API_KEY=private-key-do-not-export")
    (api / "nodo.log").write_text("1011 quota: key=another-private-key\nuser said: private-conversation\n")
    report = collect(tmp_path)
    encoded = json.dumps(report)
    assert "private-key" not in encoded and "private-conversation" not in encoded
    assert report["conteggi_ultimi_512kb_log"]["quota_esplicita"] == 1
    assert report["conteggi_ultimi_512kb_log"]["codice_1011_non_diagnostico"] == 1
    assert report["pannello"] == "mancante"


def test_offline_report_detects_outdated_audio_worklet(tmp_path):
    web = tmp_path / "apps/web"
    (web / "dist").mkdir(parents=True)
    (web / "public").mkdir()
    dist, worklet = web / "dist/index.html", web / "public/pcm-worklet.js"
    dist.write_text("build")
    worklet.write_text("new capture code")
    os.utime(dist, (10, 10))
    os.utime(worklet, (20, 20))
    assert collect(tmp_path)["pannello"] == "da ricostruire"
