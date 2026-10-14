"""Reopen an unchanged NODO instead of killing and rebuilding it."""
from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path


def fingerprint(api: Path | None = None) -> str:
    api = api or Path(__file__).resolve().parents[1]
    web = api.parent / "web"
    files = [api / "pyproject.toml", api / ".env"]
    files += sorted((api / "nodo").rglob("*.py"))
    files += sorted((api / "alembic").rglob("*.py"))
    for folder in ("src", "public", "dist"):
        files += sorted(p for p in (web / folder).rglob("*") if p.is_file())
    files += [web / "package.json", web / "package-lock.json", web / "index.html"]
    digest = hashlib.sha256()
    for path in files:
        if path.is_file():
            digest.update(str(path.relative_to(api.parent)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def reusable(port: int) -> bool:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/health", timeout=0.6) as response:
            state = json.load(response)
        return (isinstance(state, dict) and state.get("status") == "ok" and state.get("env") != "DEMO"
                and state.get("service") == "nodo-core" and state.get("build_id") == fingerprint())
    except (OSError, ValueError, TypeError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not reusable(args.port):
        return 3
    if not args.no_browser:
        from nodo.tray import open_panel
        open_panel(args.port)
    print("[NODO] Gia' pronto: apro la finestra senza riavviare il server.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
