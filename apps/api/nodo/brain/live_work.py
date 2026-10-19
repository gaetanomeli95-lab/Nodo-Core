"""Long jobs you can WATCH, and that Maria cannot lie about.

18/10: «fammi la bozza del sito HostFacility». For two hours Maria said «ci sto lavorando», «siamo all'80%», «la
vedi adesso» — while nothing was running (the Claude program had hit its weekly limit at 20:51 and the job had
failed). And Gaetano asked twice to SEE the work: «voglio vedere quando scrivi i codici».

So every long job registers here: what it is, when it started, its folder, its state. The panel shows the file
growing line by line (/api/v1/work/live), the lavagna tells Maria the true state, and the voice is stopped when it
promises progress that does not exist.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

KEEP_S = 3 * 3600            # finished jobs stay visible for a while (the result, or the reason it failed)
TAIL_CHARS = 3500


@dataclass
class Job:
    id: str
    title: str
    kind: str                                   # sito | lavoro | documento
    workdir: Path
    started: datetime = field(default_factory=lambda: datetime.now(UTC))
    status: str = "in corso"                    # in corso | finito | fallito
    note: str = ""
    url: str | None = None
    view: str | None = None                     # how the result opens: web | pdf | text…
    finished: datetime | None = None
    step: str = "inizio"
    mono: float = field(default_factory=time.monotonic)


_jobs: dict[str, Job] = {}


def start(job_id: str, title: str, workdir: Path, kind: str = "lavoro") -> Job:
    _prune()
    job = Job(id=job_id, title=title.strip()[:120] or "Lavoro", kind=kind, workdir=Path(workdir))
    _jobs[job_id] = job
    return job


def step(job_id: str, text: str) -> None:
    if (j := _jobs.get(job_id)) is not None:
        j.step = text[:120]


def finish(job_id: str, ok: bool, note: str = "", url: str | None = None, view: str | None = None) -> None:
    if (j := _jobs.get(job_id)) is not None:
        j.status, j.note, j.url, j.view = ("finito" if ok else "fallito"), note[:400], url, view
        j.finished = datetime.now(UTC)


def running() -> list[Job]:
    return [j for j in _jobs.values() if j.status == "in corso"]


def latest() -> Job | None:
    return max(_jobs.values(), key=lambda j: j.mono, default=None)


def _prune() -> None:
    now = time.monotonic()
    for k, j in list(_jobs.items()):
        if j.status != "in corso" and now - j.mono > KEEP_S:
            _jobs.pop(k, None)


def _newest_file(workdir: Path) -> Path | None:
    try:
        files = [p for p in workdir.rglob("*") if p.is_file() and not any(x.startswith(".") for x in p.parts)]
    except OSError:
        return None
    return max(files, key=lambda p: p.stat().st_mtime, default=None)


def snapshot() -> list[dict]:
    """What the panel shows: each recent job, and the end of the file it is writing right now."""
    _prune()
    out = []
    now = datetime.now(UTC)
    for j in sorted(_jobs.values(), key=lambda j: j.mono, reverse=True)[:3]:
        f = _newest_file(j.workdir) if j.workdir.exists() else None
        tail, size, name = "", 0, None
        if f is not None:
            try:
                size = f.stat().st_size
                with f.open("rb") as fh:
                    fh.seek(max(0, size - TAIL_CHARS))
                    tail = fh.read().decode("utf-8", "replace")
                name = f.name
            except OSError:
                pass
        end = j.finished or now
        out.append({"id": j.id, "title": j.title, "kind": j.kind, "status": j.status, "step": j.step,
                    "note": j.note, "url": j.url, "view": j.view, "file": name, "bytes": size,
                    "lines": tail.count("\n") if tail else 0, "tail": tail,
                    "seconds": int((end - j.started).total_seconds()), "started": j.started.isoformat()})
    return out


def board_line(tz) -> str | None:
    """One line for the lavagna (the true state, without the clock ticking: it changes only when the state does)."""
    _prune()
    jobs = sorted(_jobs.values(), key=lambda j: j.mono, reverse=True)[:3]
    if not jobs:
        return ("LAVORI IN BACKGROUND: nessuno in corso. Non dire mai «ci sto lavorando», «ti avviso appena è "
                "pronto» o percentuali: non c'è niente che sta andando avanti.")
    bits = []
    for j in jobs:
        at = j.started.astimezone(tz).strftime("%H:%M")
        if j.status == "in corso":
            bits.append(f"«{j.title}» IN CORSO dalle {at} (Gaetano lo vede scrivere nel riquadro sullo schermo)")
        elif j.status == "finito":
            bits.append(f"«{j.title}» FINITO alle {j.finished.astimezone(tz):%H:%M}" + (f": {j.note}" if j.note else ""))
        else:
            bits.append(f"«{j.title}» NON RIUSCITO alle {j.finished.astimezone(tz):%H:%M}: {j.note}")
    return ("LAVORI IN BACKGROUND (stato vero, l'unico da dire): " + "; ".join(bits) + ". Niente percentuali "
            "inventate; se nessuno è in corso, non promettere avvisi.")
