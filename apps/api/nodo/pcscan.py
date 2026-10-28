"""«Analizza il PC»: what is on the computer NODO runs on, so it knows what Gaetano works on.

Only the SYSTEM disk (C:), never other disks, USB sticks or network drives. Only names, kinds, sizes and dates:
no file is opened or read. System folders, AppData and program internals are skipped. The result is a profile
(apps/api/pc-profilo.json) that the brain sees in its context from then on, so it is learned once, not every turn.
"""
from __future__ import annotations

import json
import os
import string
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from nodo.media import KIND_BY_EXT

PROFILE = Path(__file__).resolve().parents[1] / "pc-profilo.json"
SCAN_BUDGET_S = 90.0
MAX_FILES = 300_000

_KIND_IT = {"image": "foto e immagini", "video": "video", "audio": "audio", "pdf": "PDF", "doc": "documenti Word",
            "sheet": "fogli Excel/CSV", "slides": "presentazioni", "text": "testi", "other": "altri file"}
_EXTRA_KINDS = {**dict.fromkeys(("py", "js", "ts", "tsx", "jsx", "php", "java", "kt", "cs", "cpp", "c", "go", "rs",
                                 "css", "scss", "sql", "sh", "vue", "dart", "swift"), "code"),
                **dict.fromkeys(("psd", "ai", "eps", "fig", "xd", "indd", "blend", "dwg", "skp", "kra"), "design"),
                **dict.fromkeys(("zip", "rar", "7z", "tar", "gz", "iso"), "archive")}
_KIND_IT.update(code="codice sorgente", design="grafica e progettazione", archive="archivi compressi")
_SKIP = {"appdata", "node_modules", "venv", ".venv", "__pycache__", "site-packages", "dist", "build", "target",
         "vendor", "bower_components", "cache", "temp", "tmp", ".git", "$recycle.bin", "system volume information"}
_SYSTEM_TOP = {"windows", "program files", "program files (x86)", "programdata", "users", "perflogs", "recovery",
               "$recycle.bin", "system volume information", "$windows.~bt", "$windows.~ws", "msocache", "intel",
               "amd", "nvidia", "drivers", "config.msi", "documents and settings", "onedrivetemp", "$sysreset"}
# folders at the root of C: that installers and drivers leave behind: they say nothing about Gaetano's work
_SYSTEM_TOP_WORDS = ("driver", "swsetup", "setup", "inetpub", "mssql", "temp", "programmi", "winreagent",
                     "node_modules", "node-modules", "tempsave", "windows.old", "esd", "system.sav")
# Start menu folders that are Windows' own tools, not programs he chose
_SYSTEM_MENUS = {"administrative tools", "windows tools", "system tools", "accessibility", "accessories",
                 "windows powershell", "maintenance", "startup", "windows administrative tools",
                 "strumenti di amministrazione windows", "accessori windows", "accessibilità", "esecuzione automatica",
                 "utilità di sistema windows", "manutenzione"}
_PROJECT_MARKS = {"package.json", "pyproject.toml", "setup.py", "cargo.toml", "go.mod", "pom.xml", "composer.json",
                  "requirements.txt", "build.gradle", "pubspec.yaml", "next.config.js", "next.config.mjs"}
_NOISE_PROGRAMS = {"common files", "windowsapps", "microsoft.net", "windows defender", "windows mail",
                   "windows media player", "windows nt", "windows photo viewer", "windows portable devices",
                   "windows security", "windows sidebar", "windowspowershell", "internet explorer", "reference assemblies",
                   "modifiablewindowsapps", "msbuild", "uninstall information", "microsoft update health tools",
                   "package cache", "dotnet", "microsoft"}


def system_drive() -> str:
    """'C:' on Windows (from %SystemDrive%), '/' elsewhere."""
    return (os.environ.get("SystemDrive") or "C:") if os.name == "nt" else "/"


def on_system_drive(path: Path) -> bool:
    """True only for paths on the system disk: other disks, USB sticks and network drives are never scanned."""
    if os.name != "nt":
        return True
    try:
        drive = os.path.splitdrive(str(path.resolve()))[0]
    except OSError:
        return False
    return drive.upper() == system_drive().upper()


def kind(name: str) -> str | None:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return _EXTRA_KINDS.get(ext) or KIND_BY_EXT.get(ext)


def _roots(home: Path) -> list[tuple[str, Path]]:
    """(label, folder): the home folder (minus AppData) and the folders Gaetano created at the root of C:."""
    out = [("cartella utente", home)] if home.is_dir() and on_system_drive(home) else []
    if os.name == "nt":
        top = Path(system_drive() + "\\")
        try:
            for p in sorted(top.iterdir()):
                low = p.name.lower()
                if p.is_dir() and low not in _SYSTEM_TOP and not p.name.startswith(("$", ".")) \
                        and not any(w in low for w in _SYSTEM_TOP_WORDS):
                    out.append((f"{system_drive()}\\{p.name}", p))
        except OSError:
            pass
    return out


def _programs() -> list[str]:
    """Installed programs (Start menu entries and Program Files folders), the most recently installed first.
    Windows' own tools (Prompt dei comandi, Pulizia disco...) are left out."""
    seen: dict[str, tuple[float, str]] = {}
    if os.name != "nt":
        return []

    def add(name: str, path: Path) -> None:
        try:
            when = path.stat().st_mtime
        except OSError:
            return
        key = name.lower()
        if key not in _NOISE_PROGRAMS and when > seen.get(key, (0, ""))[0]:
            seen[key] = (when, name)
    for var in ("APPDATA", "ProgramData"):
        base = os.environ.get(var)
        if not base:
            continue
        start = Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        try:
            for lnk in start.rglob("*.lnk"):
                parts = {x.lower() for x in lnk.relative_to(start).parts[:-1]}
                if parts & _SYSTEM_MENUS or any(w in lnk.stem.lower() for w in
                                                ("uninstall", "disinstall", "readme", "help", "guida", "manual")):
                    continue
                rel = lnk.relative_to(start).parts
                add(rel[0] if len(rel) > 1 else lnk.stem, lnk)    # a program's folder of shortcuts = one program
        except OSError:
            continue
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if not base:
            continue
        base = Path(base) / ("Programs" if var == "LOCALAPPDATA" else "")
        try:
            for p in base.iterdir():
                if p.is_dir() and on_system_drive(p) and not p.name.lower().startswith(("windows", "microsoft")):
                    add(p.name, p)
        except OSError:
            continue
    return [name for _, name in sorted(seen.values(), reverse=True)]


def _recent(home: Path, limit: int = 25) -> list[str]:
    """Names of the files opened most recently (Windows «Recent» list: names only, the files are not opened)."""
    rec = home / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Recent"
    try:
        items = sorted((p for p in rec.glob("*.lnk")), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []
    import re
    junk = re.compile(r"^(ms-|[a-z]$|[0-9a-f]{8}-[0-9a-f-]{27}$)", re.I)    # Windows internals, GUIDs
    return [p.stem for p in items if not junk.search(p.stem)][:limit]


def scan(home: Path | None = None, *, budget_s: float = SCAN_BUDGET_S, roots: list[tuple[str, Path]] | None = None,
         programs: bool = True) -> dict:
    """Walks the system disk and returns the profile (also written to PROFILE)."""
    home = home or Path(os.environ.get("USERPROFILE") or Path.home())
    started = time.monotonic()
    by_kind: Counter = Counter()
    by_ext: Counter = Counter()
    folders: dict[str, dict] = {}
    projects: list[dict] = []
    newest: list[tuple[float, str]] = []
    total = size = 0
    stopped = False
    for label, root in (roots if roots is not None else _roots(home)):
        if not on_system_drive(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            here = Path(dirpath)
            rel = here.relative_to(root).parts
            dirnames[:] = [d for d in dirnames if d.lower() not in _SKIP and not d.startswith((".", "$"))
                           and on_system_drive(here / d)]
            top = rel[0] if rel else ("(file sciolti nella cartella utente)" if label == "cartella utente" else
                                      "(principale)")
            key = f"{label}\\{top}" if label != "cartella utente" else top
            if {f.lower() for f in filenames} & _PROJECT_MARKS or (here / ".git").is_dir():
                try:
                    projects.append({"name": here.name, "path": str(here),
                                     "modified": datetime.fromtimestamp(here.stat().st_mtime).date().isoformat()})
                except OSError:
                    pass
                dirnames[:] = []                     # a software project: its own files say nothing more
                continue
            f_info = folders.setdefault(key, {"files": 0, "bytes": 0, "last": 0.0, "kinds": Counter()})
            for f in filenames:
                k = kind(f)
                try:
                    st = os.stat(here / f)
                except OSError:
                    continue
                total += 1
                size += st.st_size
                f_info["files"] += 1
                f_info["bytes"] += st.st_size
                f_info["last"] = max(f_info["last"], st.st_mtime)
                if k:
                    by_kind[k] += 1
                    f_info["kinds"][k] += 1
                    by_ext[f.rsplit(".", 1)[-1].lower()] += 1
                    newest.append((st.st_mtime, str(here / f)))
            if len(newest) > 4000:
                newest = sorted(newest, reverse=True)[:200]
            if total > MAX_FILES or time.monotonic() - started > budget_s:
                stopped = True
                break
        if stopped:
            break
    top_folders = sorted(((k, v) for k, v in folders.items() if v["files"]), key=lambda kv: -kv[1]["files"])[:25]
    profile = {
        "scanned_at": datetime.now().isoformat(timespec="minutes"),
        "computer": os.environ.get("COMPUTERNAME", ""),
        "user": os.environ.get("USERNAME", ""),
        "disk": system_drive(),
        "complete": not stopped,
        "seconds": round(time.monotonic() - started, 1),
        "files": total,
        "gigabytes": round(size / 1e9, 1),
        "by_kind": {_KIND_IT.get(k, k): n for k, n in by_kind.most_common()},
        "top_extensions": dict(by_ext.most_common(15)),
        "folders": [{"folder": name, "files": v["files"], "megabytes": round(v["bytes"] / 1e6),
                     "last_change": datetime.fromtimestamp(v["last"]).date().isoformat() if v["last"] else None,
                     "mostly": [_KIND_IT.get(k, k) for k, _ in v["kinds"].most_common(3)]}
                    for name, v in top_folders],
        "software_projects": sorted(projects, key=lambda p: p["modified"], reverse=True)[:40],
        "recent_files": [Path(p).name for _, p in sorted(newest, reverse=True)[:30]],
        "recently_opened": _recent(home) if programs else [],
        "programs": _programs() if programs else [],
    }
    try:
        PROFILE.write_text(json.dumps(profile, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass
    return profile


def load() -> dict | None:
    try:
        return json.loads(PROFILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def context_line(profile: dict | None = None, limit: int = 900) -> str:
    """One compact paragraph for the brain's context: what this PC says about Gaetano's work."""
    p = profile if profile is not None else load()
    if not p:
        return ""
    when = (p.get("scanned_at") or "")[:10]
    parts = [f"PC su cui giri ({p.get('computer') or 'questo PC'}, disco {p.get('disk')}, analizzato il {when}): "
             f"{p.get('files', 0)} file personali"]
    kinds = list((p.get("by_kind") or {}).items())[:5]
    if kinds:
        parts.append("soprattutto " + ", ".join(f"{n} {k}" for k, n in kinds))
    folders = [f["folder"] for f in (p.get("folders") or [])[:6]]
    if folders:
        parts.append("cartelle principali: " + ", ".join(folders))
    projects = [x["name"] for x in (p.get("software_projects") or [])[:6]]
    if projects:
        parts.append("progetti software: " + ", ".join(projects))
    progs = [x for x in (p.get("programs") or []) if len(x) < 40][:12]
    if progs:
        parts.append("programmi installati: " + ", ".join(progs))
    line = "; ".join(parts) + ". Per i dettagli usa pc_overview."
    return line if len(line) <= limit else line[: limit - 3] + "..."


def allowed_here(allowed_file: Path | None = None) -> bool:
    """Started with AVVIA NODO.bat, a PC is used only after its user said yes (runtime/pc-autorizzati.txt).
    Without that file NODO was installed on this PC by its owner: allowed."""
    f = allowed_file or Path(__file__).resolve().parents[3] / "runtime" / "pc-autorizzati.txt"
    try:
        lines = {x.strip().lower() for x in f.read_text(encoding="utf-8", errors="replace").splitlines()}
    except OSError:
        return True
    me = f"{os.environ.get('COMPUTERNAME', '')}\\{os.environ.get('USERNAME', '')}".lower()
    return me in lines


def drives_skipped() -> list[str]:
    """Other disks present (they are never scanned): said to Gaetano so he knows what was left out."""
    if os.name != "nt":
        return []
    sys_d = system_drive().upper()
    return [f"{d}:" for d in string.ascii_uppercase if f"{d}:" != sys_d and os.path.exists(f"{d}:\\")]
