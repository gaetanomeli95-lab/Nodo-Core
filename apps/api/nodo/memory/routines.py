"""Predictive routines: NODO learns WHEN Gaetano asks for what.

Pure data mining, no model needed: group his messages by normalized signature and time-of-day band,
keep the ones that repeat (>= `MIN_HITS` on >= `MIN_DAYS` different days), and persist them as
`routine` facts — so the working memory can surface «di solito a quest'ora chiedi il riepilogo».

Nothing here invents habits: a routine exists only when the history really repeats.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher

from sqlalchemy import select

from nodo.db.models import FactStatus, MemoryFact, Message

log = logging.getLogger("nodo.routines")

WINDOW_DAYS = 45          # how far back we look for habits
MIN_HITS = 3              # a routine needs at least this many occurrences
MIN_DAYS = 2              # ... on at least this many distinct days (same-day repeats are one event)
BAND_H = 3                # time-of-day band width in hours
MAX_ROUTINES = 12         # cap on stored routine facts

_VOCATIVE = re.compile(r"^\s*(?:maria|nodo|ehi|senti|scusa|dimmi|dai)[,!. ]+", re.I)
_FILLER = re.compile(r"\b(allora|ecco|dai|per[oò]|cio[eè]|tipo|diciamo|praticamente|insomma|vedi|"
                     r"comunque|adesso|ora|ok|okay|s[iì]|no|eh|mah|boh|una cosa|questa cosa|"
                     r"maria|nodo)\b", re.I)              # the assistant's name is a vocative, not content
_NONWORD = re.compile(r"[^a-zà-ù0-9' ]+", re.I)
_SPACES = re.compile(r"\s+")


def signature(text: str) -> str | None:
    """A stable key for 'the same request': lowercase, no vocatives/fillers/punctuation, first words.
    Returns None when the message isn't a real request (too short, noise)."""
    from nodo.memory.mind import is_noise
    t = (text or "").strip()
    if len(t) < 6 or is_noise(t):
        return None
    t = _VOCATIVE.sub("", t)
    t = _NONWORD.sub(" ", t.lower())
    t = _FILLER.sub(" ", t)
    t = _SPACES.sub(" ", t).strip()
    words = t.split()
    if len(words) < 2 or sum(len(w) for w in words) < 7:
        return None
    return " ".join(words[:8])[:80]


def _band(hour: int) -> int:
    return (hour // BAND_H) * BAND_H


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def mine(db, org: str, tz, days: int = WINDOW_DAYS) -> list[dict]:
    """Scan user messages and return recurring (signature, band) patterns, strongest first."""
    since = datetime.now(UTC) - timedelta(days=days)
    rows = db.scalars(select(Message).where(Message.role == "user", Message.created_at >= since)
                      .order_by(Message.created_at)).all()
    hits: dict[str, dict] = defaultdict(lambda: {"n": 0, "days": set(), "bands": defaultdict(int),
                                               "sample": "", "last": None})
    for m in rows:
        sig = signature(m.content or "")
        if sig is None:
            continue
        local = _aware(m.created_at).astimezone(tz)
        h = hits[sig]
        h["n"] += 1
        h["days"].add(local.date())
        h["bands"][_band(local.hour)] += 1
        if not h["sample"] or len(m.content) < len(h["sample"]):
            h["sample"] = (m.content or "").strip().replace("\n", " ")[:160]
        h["last"] = m.created_at
    out = []
    for sig, h in hits.items():
        if h["n"] < MIN_HITS or len(h["days"]) < MIN_DAYS:
            continue
        band, band_n = max(h["bands"].items(), key=lambda kv: kv[1])
        out.append({"signature": sig, "count": h["n"], "days": len(h["days"]), "band": band,
                    "band_count": band_n, "sample": h["sample"], "last": h["last"]})
    out.sort(key=lambda r: (-r["band_count"], -r["count"]))
    return out[:MAX_ROUTINES]


def _similar(a: str, b: str) -> bool:
    return SequenceMatcher(None, a, b).ratio() >= 0.8


def learn(db, org: str, tz, subject_id: str) -> int:
    """Persist freshly mined routines as `routine` facts; supersedes the previous mining pass.
    Returns how many routine facts are active after the pass."""
    now = datetime.now(UTC)
    found = mine(db, org, tz)
    old = list(db.scalars(select(MemoryFact).where(MemoryFact.organization_id == org,
                                                   MemoryFact.layer == "routine",
                                                   MemoryFact.valid_until.is_(None))))
    for fact in old:
        fact.valid_until = now            # superseded: routine facts are re-derived every pass
    saved = 0
    for r in found:
        band_end = (r["band"] + BAND_H) % 24
        predicate = f"routine:{r['band']:02d}-{band_end:02d}:{r['signature'][:80]}"
        content = (f"Di solito tra le {r['band']:02d} e le {band_end:02d} chiede «{r['sample']}» "
                   f"({r['count']} volte su {r['days']} giorni diversi)")
        # same signature seen before → keep that row alive instead of a new id churn
        match = next((f for f in old if _similar((f.predicate or "")[15:], r["signature"])), None)
        if match is not None:
            match.valid_until = None
            match.content = content
            match.predicate = predicate
            match.confidence = min(1.0, 0.4 + r["band_count"] * 0.1)
            match.recorded_at = now
        else:
            db.add(MemoryFact(organization_id=org, subject_type="user", subject_id=subject_id,
                              layer="routine", predicate=predicate, content=content,
                              source_type="agent", confidence=min(1.0, 0.4 + r["band_count"] * 0.1),
                              status=FactStatus.INFERRED))
        saved += 1
    db.flush()
    return saved


def _covers(predicate: str, hour: int, grace: int = 1) -> bool:
    """Is `hour` inside the routine's band (+-`grace` h, bands may wrap midnight)."""
    m = re.match(r"routine:(\d{2})-(\d{2})", predicate or "")
    if not m:
        return False
    lo, hi = (int(m.group(1)) - grace) % 24, (int(m.group(2)) + grace) % 24
    hour %= 24
    return lo <= hour < hi if lo <= hi else (hour >= lo or hour < hi)


def anticipations(facts: list[MemoryFact], hour: int, limit: int = 3) -> list[str]:
    """Routine facts active around `hour` — injected into the working memory as gentle predictions."""
    return [f.content for f in facts if f.layer == "routine" and _covers(f.predicate, hour)][:limit]
