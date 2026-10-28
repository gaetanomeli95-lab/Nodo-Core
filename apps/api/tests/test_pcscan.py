"""«Analizza il PC»: what is on the system disk, names only, never other disks; learned once, known afterwards."""
from __future__ import annotations

import json

from nodo import pcscan
from nodo.brain.toolsets import pick
from nodo.core.orchestrator import brain_down_reply
from tests.test_brain_extra import box  # noqa: F401  (fixture)


def _pc(tmp_path):
    home = tmp_path / "Utente"
    (home / "Desktop").mkdir(parents=True)
    (home / "Documenti" / "Clienti").mkdir(parents=True)
    (home / "AppData" / "Local").mkdir(parents=True)
    (home / "Desktop" / "listino.xlsx").write_bytes(b"x" * 10)
    (home / "Desktop" / "foto materasso.jpg").write_bytes(b"x")
    (home / "Documenti" / "Clienti" / "preventivo rossi.pdf").write_bytes(b"x")
    (home / "AppData" / "Local" / "segreto.db").write_bytes(b"x")         # program internals: never counted
    shop = home / "Documenti" / "sito-negozio"
    (shop / "src").mkdir(parents=True)
    (shop / "package.json").write_text("{}")
    (shop / "src" / "app.ts").write_text("x")                              # inside a project: not counted
    return home


def test_scan_counts_names_kinds_and_projects_but_skips_appdata(tmp_path, monkeypatch):
    monkeypatch.setattr(pcscan, "PROFILE", tmp_path / "pc-profilo.json")
    home = _pc(tmp_path)
    p = pcscan.scan(home, roots=[("cartella utente", home)], programs=False)
    assert p["files"] == 3 and p["complete"]
    assert p["by_kind"] == {"fogli Excel/CSV": 1, "foto e immagini": 1, "PDF": 1}
    assert {f["folder"] for f in p["folders"]} == {"Desktop", "Documenti"}
    assert [x["name"] for x in p["software_projects"]] == ["sito-negozio"]
    assert "segreto.db" not in json.dumps(p) and "app.ts" not in json.dumps(p)
    assert json.loads((tmp_path / "pc-profilo.json").read_text(encoding="utf-8"))["files"] == 3


def test_other_disks_are_never_scanned(tmp_path, monkeypatch):
    monkeypatch.setattr(pcscan, "PROFILE", tmp_path / "pc-profilo.json")
    home = _pc(tmp_path)
    monkeypatch.setattr(pcscan, "on_system_drive", lambda path: False)   # as if the folder were on a USB stick
    p = pcscan.scan(home, roots=[("cartella utente", home)], programs=False)
    assert p["files"] == 0 and p["folders"] == []


def test_the_profile_reaches_the_brain_context(tmp_path, monkeypatch):
    monkeypatch.setattr(pcscan, "PROFILE", tmp_path / "pc-profilo.json")
    home = _pc(tmp_path)
    pcscan.scan(home, roots=[("cartella utente", home)], programs=False)
    line = pcscan.context_line()
    assert "3 file personali" in line and "sito-negozio" in line and "pc_overview" in line
    monkeypatch.setattr(pcscan, "PROFILE", tmp_path / "nessuno.json")
    assert pcscan.context_line() == ""


def test_only_a_pc_whose_user_said_yes(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPUTERNAME", "NEGOZIO")
    monkeypatch.setenv("USERNAME", "Gaetano")
    allowed = tmp_path / "pc-autorizzati.txt"
    assert pcscan.allowed_here(allowed)                       # no list: NODO installed by the PC's owner
    allowed.write_text("ALTRO-PC\\Mario\n", encoding="utf-8")
    assert not pcscan.allowed_here(allowed)
    allowed.write_text("ALTRO-PC\\Mario\nNEGOZIO\\Gaetano\n", encoding="utf-8")
    assert pcscan.allowed_here(allowed)


async def test_pc_overview_tool_scans_once_then_uses_the_saved_profile(box, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(pcscan, "PROFILE", tmp_path / "pc-profilo.json")
    monkeypatch.setattr(pcscan, "allowed_here", lambda: True)
    monkeypatch.setenv("COMPUTERNAME", "NEGOZIO")
    home = _pc(tmp_path)
    calls = []
    real = pcscan.scan

    def fake_scan(*a, **k):
        calls.append(1)
        return real(home, roots=[("cartella utente", home)], programs=False)
    monkeypatch.setattr(pcscan, "scan", fake_scan)
    first = await box.call("pc_overview", {})
    assert first["analysed"] == "adesso" and first["files"] == 3 and "nessun file è stato aperto" in first["note"]
    again = await box.call("pc_overview", {})
    assert again["analysed"].startswith("il ") and len(calls) == 1
    await box.call("pc_overview", {"refresh": True})
    assert len(calls) == 2
    monkeypatch.setattr(pcscan, "allowed_here", lambda: False)
    assert "permesso" in (await box.call("pc_overview", {}))["error"]


def test_talking_about_the_pc_offers_the_tool():
    names = ["pc_overview", "show_on_screen", "ledger_add"]
    assert "pc_overview" in pick(names, "controlla la cartella desktop e tutte le cartelle")
    assert "pc_overview" in pick(names, "puoi fare un'analisi completa del pc?")
    assert "pc_overview" not in pick(names, "ciao come stai")


def test_no_model_says_why_instead_of_the_menu():
    net = brain_down_reply("No tool-capable model available: groq transport error: [Errno 11001] getaddrinfo failed")
    busy = brain_down_reply("gemini-lite HTTP 503: This model is currently experiencing high demand")
    assert "internet" in net and "occupati" in busy
    assert "Posso aiutarti con" not in net + busy
