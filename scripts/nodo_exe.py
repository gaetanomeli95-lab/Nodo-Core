"""NODO.exe - the small launcher that becomes the NODO app (built by costruisci-exe.bat).

It does not contain NODO: it finds the installed NODO next to it (apps\\api\\.venv) and starts the tray app there,
so updating NODO never needs a new exe. Keep NODO.exe in the NODO folder (the one with avvia-nodo.bat).
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path


def home() -> Path:
    here = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
    for cand in (here, here.parent):
        if (cand / "apps" / "api").is_dir():
            return cand
    return here


def message(text: str) -> None:
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(0, text, "NODO", 0x40)
    else:
        print(text)


def main() -> int:
    root = home()
    api = root / "apps" / "api"
    pyw = api / ".venv" / "Scripts" / "pythonw.exe"
    if not pyw.exists():
        message("NODO non e' ancora installato qui.\nFai doppio click su crea-app-nodo.bat nella cartella di NODO.")
        return 1
    flags = 0x00000008 | 0x08000000 if os.name == "nt" else 0       # DETACHED_PROCESS | CREATE_NO_WINDOW
    subprocess.Popen([str(pyw), "-m", "nodo.tray", *sys.argv[1:]], cwd=api, creationflags=flags, close_fds=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
