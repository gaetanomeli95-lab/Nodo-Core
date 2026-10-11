"""Offline support report. No .env, transcripts, raw log lines or network calls.

Run from the repository with diagnosi-nodo.bat, or python -m nodo.voice.diagnostics.
"""
from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                                text=True, timeout=3, check=True)
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def collect(root: Path) -> dict:
    packages = {}
    for name in ("google-genai", "fastapi", "websockets"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = "non installato"
    web = root / "apps/web"
    dist = web / "dist/index.html"
    inputs = [p for folder in ("src", "public") for p in (web / folder).rglob("*") if p.is_file()]
    inputs += [p for p in (web / "package.json", web / "package-lock.json", web / "index.html") if p.exists()]
    stale = dist.exists() and any(p.stat().st_mtime > dist.stat().st_mtime for p in inputs)
    log = root / "apps/api/nodo.log"
    data = ""
    if log.exists():
        with log.open("rb") as stream:
            stream.seek(max(0, log.stat().st_size - 512_000))
            data = stream.read().decode("utf-8", errors="replace")
    # Only categorical counts leave the log. No excerpt can expose a secret or a conversation.
    counts = {label: sum(bool(re.search(pattern, line, re.I)) for line in data.splitlines())
              for label, pattern in {
                  "quota_esplicita": r"quota|resource_exhausted|rate.limit|\b429\b",
                  "codice_1011_non_diagnostico": r"\b1011\b",
                  "configurazione_1007": r"\b1007\b|CONTENT_TYPE_AUDIO",
                  "vecchia_chiusura_sessione_terminata": r"sessione terminata",
                  "tentativi_riconnessione": r"reconnect|riconness",
                  "eccezioni_python": r"Traceback \(most recent call last\)",
              }.items()}
    commit = _git(root, "rev-parse", "--short=12", "HEAD")
    status = _git(root, "status", "--porcelain", "--untracked-files=no")
    return {
        "schema": 1,
        "creato_utc": datetime.now(UTC).isoformat(),
        "sistema": platform.system(),
        "sistema_versione": platform.release(),
        "python": platform.python_version(),
        "pacchetti": packages,
        "commit_locale": commit if commit and re.fullmatch(r"[0-9a-f]{7,40}", commit) else "non disponibile",
        "file_tracciati_modificati": len(status.splitlines()) if status is not None else None,
        "pannello": "mancante" if not dist.exists() else "da ricostruire" if stale else "costruito",
        "log_presente": log.exists(),
        "conteggi_ultimi_512kb_log": counts,
        "interpretazione": [
            "Il commit identifica il codice su disco, non garantisce che il processo aperto sia stato riavviato.",
            "1011 da solo non significa quota esaurita. I conteggi possono sovrapporsi.",
            "Questo controllo non verifica microfono, altoparlanti, quota disponibile o qualità della conversazione.",
            "Per una diagnosi precisa servono anche orario del problema e cosa è successo a voce.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnosi locale di NODO, senza inviare dati.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[4]
    report = json.dumps(collect(root), ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(report + "\n", encoding="utf-8")
        print("Diagnosi salvata. Il file contiene versioni e conteggi, senza chiavi o conversazioni.")
    else:
        print(report)


if __name__ == "__main__":
    main()
