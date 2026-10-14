"""NODO as a real app: an icon next to the clock that keeps the server running.

    pythonw -m nodo.tray            (no console window; this is what the desktop shortcut and autostart run)
    python  -m nodo.tray --no-browser --port 8000

What it does
- starts NODO's server in the background (hidden window) and restarts it a few seconds after any crash;
- puts an icon in the tray: green = NODO answers, amber = starting, red = down;
- menu: Apri NODO (opens the panel in an app-style window), Mostra il registro, Riavvia, Esci;
- a second launch does not start a second server: it just opens the panel of the one already running.

`Supervisor` has no GUI dependency (it is what the tests exercise); the icon needs `pip install pystray pillow`.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from collections.abc import Callable
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def healthy(port: int, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001 - any failure just means "not (yet) answering"
        return False


def port_busy(port: int, timeout: float = 1.0) -> bool:
    """Something already holds the port (a NODO still starting included): a second supervisor would fight it."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


class Supervisor:
    """Runs the server as a child process and keeps it alive."""

    def __init__(self, cmd: list[str], cwd: Path, log_path: Path, port: int, *, backoff: float = 10.0,
                 health: Callable[[int], bool] = healthy):
        self.cmd, self.cwd, self.log_path, self.port = cmd, cwd, log_path, port
        self.backoff = backoff
        self._health = health
        self._proc: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.restarts = 0
        self._fast = False

    # -- lifecycle
    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="nodo-supervisor", daemon=True)
        self._thread.start()

    def _spawn(self) -> subprocess.Popen:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log = open(self.log_path, "ab")  # noqa: SIM115 - handed to the child for its whole life
        log.write(f"\n--- avvio {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n".encode())
        log.flush()
        return subprocess.Popen(self.cmd, cwd=self.cwd, stdout=log, stderr=subprocess.STDOUT,
                                creationflags=NO_WINDOW)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._proc = self._spawn()
            if self._stop.is_set():              # stop() can land while we were spawning: kill what we just made
                self._proc.terminate()
                break
            while self._proc.poll() is None and not self._stop.is_set():
                self._stop.wait(0.5)
            if self._stop.is_set():
                break
            self.restarts += 1
            if self._fast:                          # asked to restart: no pause
                self._fast = False
            else:
                self._stop.wait(self.backoff)       # crashed: breathe, then start again

    def stop(self) -> None:
        self._stop.set()
        p = self._proc
        if p is not None and p.poll() is None:
            p.terminate()
            try:
                p.wait(8)
            except subprocess.TimeoutExpired:
                p.kill()
        if self._thread is not None:
            self._thread.join(timeout=3)
        p = self._proc                           # the loop may have spawned once more while we were killing
        if p is not None and p.poll() is None:
            p.kill()

    def restart(self) -> None:
        p = self._proc
        if p is not None and p.poll() is None:
            self._fast = True
            p.terminate()                           # the loop notices and starts a fresh one right away

    # -- status
    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def status(self) -> str:
        """'up' (answers), 'starting' (process alive, not answering yet) or 'down'."""
        if not self.running:
            return "down"
        return "up" if self._health(self.port) else "starting"


# ------------------------------------------------------------------------------------------------ helpers
def server_command(port: int, python: str | None = None) -> list[str]:
    exe = python or sys.executable
    return [exe, "-m", "uvicorn", "nodo.main:app", "--host", "127.0.0.1", "--port", str(port)]


def open_panel(port: int) -> None:
    """Opens NODO like an app (own window, no tabs) with Edge/Chrome when found, else the default browser."""
    url = f"http://127.0.0.1:{port}"
    for name in ("msedge", "chrome", "google-chrome", "chromium"):
        exe = shutil.which(name) or _windows_browser(name)
        if exe:
            try:
                # the app window may speak at once: NODO greets you while it appears
                subprocess.Popen([exe, f"--app={url}", "--autoplay-policy=no-user-gesture-required"],
                                 creationflags=NO_WINDOW)
                return
            except OSError:
                break
    webbrowser.open(url)


def _windows_browser(name: str) -> str | None:
    if os.name != "nt":
        return None
    roots = [os.environ.get(k, "") for k in ("ProgramFiles(x86)", "ProgramFiles", "LocalAppData")]
    rel = {"msedge": r"Microsoft\Edge\Application\msedge.exe", "chrome": r"Google\Chrome\Application\chrome.exe"}
    p = rel.get(name)
    if not p:
        return None
    for r in roots:
        if r and (Path(r) / p).exists():
            return str(Path(r) / p)
    return None


def make_icon(state: str = "up", size: int = 64):
    """The tray icon: a dark rounded square with an N, and a coloured dot for the state."""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, size - 3, size - 3), radius=size // 5, fill=(17, 24, 39, 255))
    try:
        font = ImageFont.truetype("arialbd.ttf", int(size * 0.62))
    except OSError:
        try:
            font = ImageFont.truetype("DejaVuSans-Bold.ttf", int(size * 0.62))
        except OSError:
            font = ImageFont.load_default()
    d.text((size / 2, size / 2 - 1), "N", fill=(255, 255, 255, 255), font=font, anchor="mm")
    colour = {"up": (34, 197, 94), "starting": (245, 158, 11), "down": (239, 68, 68)}.get(state, (156, 163, 175))
    r = size // 6
    d.ellipse((size - 3 * r, size - 3 * r, size - r // 2, size - r // 2), fill=colour + (255,),
              outline=(17, 24, 39, 255), width=2)
    return img


def save_ico(path: Path) -> None:
    img = make_icon("up", 256)
    img.save(path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def run_tray(sup: Supervisor, port: int, open_first: bool) -> None:
    import pystray
    log = sup.log_path

    def show_log(*_):
        if os.name == "nt":
            os.startfile(str(log))  # type: ignore[attr-defined]  # noqa: S606
        else:
            webbrowser.open(log.as_uri())

    def on_quit(icon, _item):
        icon.visible = False
        sup.stop()
        icon.stop()

    def on_restart(*_):
        sup.restart()

    menu = pystray.Menu(
        pystray.MenuItem("Apri NODO", lambda *_: open_panel(port), default=True),
        pystray.MenuItem("Mostra il registro", show_log),
        pystray.MenuItem("Riavvia NODO", on_restart),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Esci (NODO si spegne)", on_quit),
    )
    icon = pystray.Icon("nodo", make_icon("starting"), "NODO — si sta accendendo", menu)

    def watch():
        last, opened = "", False
        while icon.visible is not False and not sup._stop.is_set():
            st = sup.status()
            if st != last:
                icon.icon = make_icon(st)
                icon.title = {"up": "NODO — acceso", "starting": "NODO — si sta accendendo",
                              "down": "NODO — fermo, riparto"}[st]
                last = st
            if st == "up" and open_first and not opened:
                opened = True
                open_panel(port)
            time.sleep(2)

    def setup(ic):
        ic.visible = True
        threading.Thread(target=watch, daemon=True).start()

    icon.run(setup)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nodo.tray")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true", help="non aprire il pannello all'avvio")
    ap.add_argument("--make-icon", metavar="FILE", help="scrive l'icona .ico e termina")
    args = ap.parse_args(argv)
    if args.make_icon:
        save_ico(Path(args.make_icon))
        return 0
    if healthy(args.port):                     # already running: just show it
        if not args.no_browser:
            open_panel(args.port)
        return 0
    if port_busy(args.port):                   # starting up but not answering yet: never spawn a second server
        print(f"La porta {args.port} è occupata: NODO sta probabilmente ancora accendendosi (oppure lo occupa "
              "un altro programma).")
        if not args.no_browser:
            open_panel(args.port)
        return 0
    py = sys.executable
    if py.lower().endswith("pythonw.exe") and (Path(py).with_name("python.exe")).exists():
        py = str(Path(py).with_name("python.exe"))   # the child runs hidden through NO_WINDOW; pythonw has no stdout
    sup = Supervisor(server_command(args.port, py), API_DIR, API_DIR / "nodo.log", args.port)
    sup.start()
    try:
        run_tray(sup, args.port, open_first=not args.no_browser)
    except ImportError:
        print("Manca l'icona: pip install pystray pillow. NODO resta acceso senza icona (Ctrl+C per fermarlo).")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    finally:
        sup.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
