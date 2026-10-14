"""«Mostrami…»: find what Gaetano wants to see — on his PC or on the web — and hand it to the screen.

NODO runs on his own computer, so a photo on the Desktop, a video in Video, a PDF in Download can be shown at once,
in a viewer made for it (image, video with its sound, PDF, Word/Excel read as text, YouTube). Only the usual
personal folders are searched, never system folders; each file found gets a random one-time address valid for a
few hours, so nothing else on the disk can be reached through NODO.
"""
from __future__ import annotations

import os
import re
import secrets
import time
from pathlib import Path

from rapidfuzz import fuzz

KIND_BY_EXT = {
    **dict.fromkeys(("png", "jpg", "jpeg", "webp", "gif", "bmp", "avif"), "image"),
    **dict.fromkeys(("mp4", "webm", "mov", "m4v", "ogv"), "video"),
    **dict.fromkeys(("mp3", "wav", "ogg", "m4a", "aac", "flac"), "audio"),
    "pdf": "pdf", "docx": "doc", "doc": "doc", "xlsx": "sheet", "csv": "sheet", "pptx": "slides",
    **dict.fromkeys(("txt", "md", "json", "html", "log"), "text"),
}
WORD_KIND = [(re.compile(r"\bvideo|filmat|clip\b|registrazion", re.I), "video"),
             (re.compile(r"\bfoto|immagin|picture|screenshot|logo\b", re.I), "image"),
             (re.compile(r"\bpdf\b", re.I), "pdf"),
             (re.compile(r"\bword\b|\bdocumento\b|lettera|contratto", re.I), "doc"),
             (re.compile(r"\bexcel\b|tabella|foglio", re.I), "sheet"),
             (re.compile(r"\bmusica|canzone|audio|vocale\b", re.I), "audio"),
             (re.compile(r"presentazion|\bslide", re.I), "slides")]
_STOP = re.compile(r"\b(mostrami|fammi|vedere|apri|aprimi|il|lo|la|i|gli|le|un|una|uno|di|del|della|dei|delle|"
                   r"sul|sulla|nel|nella|che|ho|mio|mia|miei|mie|file|video|foto|immagine|immagini|pdf|documento|"
                   r"ultimo|ultima|ultimi|recente|desktop|scrivania|cartella|download|documenti|pc|computer)\b", re.I)
_TOKENS: dict[str, tuple[Path, float]] = {}
TOKEN_TTL_S = 6 * 3600
MAX_FILES = 25000
SCAN_BUDGET_S = 4.0


def roots() -> list[Path]:
    """His personal folders (Italian or English names, OneDrive copies included)."""
    home = Path.home()
    names = ["Desktop", "Scrivania", "Documents", "Documenti", "Pictures", "Immagini", "Videos", "Video",
             "Downloads", "Download", "Music", "Musica"]
    out: list[Path] = []
    for base in (home, home / "OneDrive"):
        for n in names:
            p = base / n
            if p.is_dir() and p.resolve() not in [q.resolve() for q in out]:
                out.append(p)
    extra = os.environ.get("NODO_MEDIA_DIRS", "")
    out += [Path(x) for x in extra.split(os.pathsep) if x and Path(x).is_dir()]
    return out


def kind_of(path: Path | str) -> str:
    return KIND_BY_EXT.get(str(path).rsplit(".", 1)[-1].lower(), "other")


def wanted_kind(text: str) -> str | None:
    for rx, k in WORD_KIND:
        if rx.search(text or ""):
            return k
    return None


def _scan(base_roots: list[Path], kind: str | None) -> list[Path]:
    found: list[Path] = []
    started = time.monotonic()
    for root in base_roots:
        for dirpath, dirnames, filenames in os.walk(root):
            depth = len(Path(dirpath).relative_to(root).parts)
            dirnames[:] = [d for d in dirnames if not d.startswith((".", "$")) and d.lower() not in
                           ("node_modules", "appdata", "venv", ".venv", "__pycache__")] if depth < 4 else []
            for f in filenames:
                k = KIND_BY_EXT.get(f.rsplit(".", 1)[-1].lower())
                if k and (kind is None or k == kind):
                    found.append(Path(dirpath) / f)
            if len(found) > MAX_FILES or time.monotonic() - started > SCAN_BUDGET_S:
                return found
    return found


def find_local(what: str, base_roots: list[Path] | None = None) -> tuple[Path | None, list[Path]]:
    """Best file for «l'ultimo video», «la foto del materasso», «preventivo Rossi pdf». (hit, other candidates)."""
    kind = wanted_kind(what)
    files = _scan(base_roots if base_roots is not None else roots(), kind)
    if not files:
        return None, []
    words = _STOP.sub(" ", what or "").strip()
    words = re.sub(r"\s+", " ", words)
    newest = sorted(files, key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    if not words or re.search(r"\bultim|\brecent|\bappena\b", what or "", re.I) and len(words) < 3:
        return (newest[0], newest[1:5])
    q = words.lower()

    def score(p: Path) -> float:
        name = re.sub(r"[_\-.]+", " ", p.stem.lower())
        return max(fuzz.token_set_ratio(q, name), fuzz.partial_ratio(q, name) if len(q) >= 4 else 0)
    scored = sorted(((score(p), p) for p in newest), key=lambda x: -x[0])
    good = [p for s, p in scored if s >= 70]
    if not good:
        return None, [p for _, p in scored[:5]]
    return good[0], good[1:5]


def share(path: Path) -> str:
    """A random, temporary address for one file (the token IS the permission)."""
    now = time.time()
    for t, (_, exp) in list(_TOKENS.items()):
        if exp < now:
            _TOKENS.pop(t, None)
    tok = secrets.token_urlsafe(18)
    _TOKENS[tok] = (path, now + TOKEN_TTL_S)
    return f"/api/v1/view/{tok}/{_safe_name(path.name)}"


def resolve(token: str) -> Path | None:
    hit = _TOKENS.get(token)
    if not hit or hit[1] < time.time() or not hit[0].is_file():
        return None
    return hit[0]


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", name)[:120] or "file"


_YT = re.compile(r"(?:youtube\.com/(?:watch\?v=|shorts/|embed/)|youtu\.be/)([\w-]{11})")


def from_url(text: str) -> dict | None:
    """A link said or pasted: YouTube becomes a player, a picture or a video is shown as it is, a page opens."""
    m = re.search(r"https?://\S+", text or "")
    if not m:
        return None
    url = m.group(0).rstrip(".,;)»\"'")
    if yt := _YT.search(url):
        return {"kind": "youtube", "url": f"https://www.youtube-nocookie.com/embed/{yt.group(1)}?autoplay=1&rel=0",
                "title": "Video da YouTube"}
    k = kind_of(url.split("?", 1)[0])
    return {"kind": k if k in ("image", "video", "audio", "pdf") else "web", "url": url, "title": url[:80]}
