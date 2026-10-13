"""Read-only calendar from a secret iCal (.ics) link - Google Calendar, Apple, Outlook all give one.

Self-contained (no extra packages): line unfolding, DTSTART with TZID/UTC/all-day, EXDATE, RECURRENCE-ID overrides
and the recurrence rules people actually use (DAILY/WEEKLY+BYDAY/MONTHLY/YEARLY with INTERVAL, COUNT, UNTIL).
Nothing is ever written back: the link is a window, not a key to the account.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

_DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


@dataclass
class Event:
    title: str
    start: datetime            # aware; all-day events start at 00:00 local
    end: datetime
    all_day: bool = False
    location: str = ""

    def as_dict(self, tz) -> dict:
        s = self.start.astimezone(tz)
        d = {"title": self.title, "day": s.date().isoformat(), "all_day": self.all_day}
        if not self.all_day:
            d["from"], d["to"] = s.strftime("%H:%M"), self.end.astimezone(tz).strftime("%H:%M")
        if self.location:
            d["where"] = self.location
        return d


# ------------------------------------------------------------------------------------------------ parsing
def _unfold(text: str) -> list[str]:
    out: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and out:
            out[-1] += raw[1:]
        elif raw:
            out.append(raw)
    return out


def _unescape(v: str) -> str:
    return v.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def _prop(line: str) -> tuple[str, dict[str, str], str]:
    head, _, value = line.partition(":")
    name, *params = head.split(";")
    return name.upper(), {k.upper(): v.strip('"') for k, _, v in (p.partition("=") for p in params)}, value


def _when(value: str, params: dict[str, str], default_tz) -> tuple[datetime, bool]:
    """-> (aware datetime, is_all_day)."""
    v = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", v):
        d = datetime.strptime(v[:8], "%Y%m%d")
        return d.replace(tzinfo=default_tz), True
    utc = v.endswith("Z")
    d = datetime.strptime(v.rstrip("Z"), "%Y%m%dT%H%M%S")
    if utc:
        return d.replace(tzinfo=UTC), False
    tzid = params.get("TZID")
    try:
        tz = ZoneInfo(tzid) if tzid else default_tz
    except Exception:  # noqa: BLE001 - unknown Windows-style zone names: use the calendar owner's zone
        tz = default_tz
    return d.replace(tzinfo=tz), False


def _duration(v: str) -> timedelta:
    m = re.fullmatch(r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", v.strip())
    if not m:
        return timedelta(0)
    sign = -1 if m.group(1) == "-" else 1
    w, d, h, mi, s = (int(x or 0) for x in m.groups()[1:])
    return sign * timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)


def _rule(v: str) -> dict[str, str]:
    return {k.upper(): val for k, _, val in (p.partition("=") for p in v.split(";") if "=" in p)}


# ------------------------------------------------------------------------------------------------ recurrence
def _nth_weekday(year: int, month: int, wd: int, n: int) -> date | None:
    first = date(year, month, 1)
    if n > 0:
        d = first + timedelta(days=(wd - first.weekday()) % 7 + 7 * (n - 1))
    else:
        nxt = date(year + (month == 12), month % 12 + 1, 1)
        last = nxt - timedelta(days=1)
        d = last - timedelta(days=(last.weekday() - wd) % 7 + 7 * (-n - 1))
    return d if d.month == month else None


def _byday(rule: dict[str, str]) -> list[tuple[int, int]]:
    out = []
    for tok in rule.get("BYDAY", "").split(","):
        m = re.fullmatch(r"([+-]?\d+)?(MO|TU|WE|TH|FR|SA|SU)", tok.strip())
        if m:
            out.append((int(m.group(1) or 0), _DAYS.index(m.group(2))))
    return out


def _candidates(start: datetime, rule: dict[str, str]):
    """Naive local start times of every occurrence, in order, from DTSTART on (generator)."""
    base = start.replace(tzinfo=None)
    freq = rule.get("FREQ", "")
    step = max(1, int(rule.get("INTERVAL", "1") or 1))
    days = _byday(rule)
    t = base.time()
    if freq == "DAILY":
        k = 0
        while True:
            yield base + timedelta(days=k * step)
            k += 1
    elif freq == "WEEKLY":
        wanted = sorted({wd for _, wd in days}) or [base.weekday()]
        monday = base.date() - timedelta(days=base.weekday())
        k = 0
        while True:
            for wd in wanted:
                d = datetime.combine(monday + timedelta(days=7 * step * k + wd), t)
                if d >= base:
                    yield d
            k += 1
    elif freq == "MONTHLY":
        k = 0
        while True:
            y, m = divmod(base.month - 1 + k * step, 12)
            y, m = base.year + y, m + 1
            found: list[date] = []
            if days:
                for n, wd in days:
                    if n:
                        if (d := _nth_weekday(y, m, wd, n)):
                            found.append(d)
                    else:
                        d0 = date(y, m, 1)
                        found += [d0 + timedelta(days=i) for i in range(31)
                                  if (d0 + timedelta(days=i)).month == m and (d0 + timedelta(days=i)).weekday() == wd]
            else:
                for md in (int(x) for x in rule.get("BYMONTHDAY", str(base.day)).split(",")):
                    try:
                        found.append(date(y, m, md) if md > 0 else _nth_day_from_end(y, m, -md))
                    except ValueError:
                        pass
            for d in sorted(set(found)):
                dt = datetime.combine(d, t)
                if dt >= base:
                    yield dt
            k += 1
    elif freq == "YEARLY":
        k = 0
        while True:
            y = base.year + k * step
            months = [int(x) for x in rule.get("BYMONTH", str(base.month)).split(",")]
            for m in months:
                if days and any(n for n, _ in days):
                    for n, wd in days:
                        if n and (d := _nth_weekday(y, m, wd, n)):
                            dt = datetime.combine(d, t)
                            if dt >= base:
                                yield dt
                else:
                    try:
                        dt = datetime(y, m, int(rule.get("BYMONTHDAY", base.day)), t.hour, t.minute, t.second)
                    except ValueError:
                        continue
                    if dt >= base:
                        yield dt
            k += 1
    else:
        yield base


def _nth_day_from_end(y: int, m: int, n: int) -> date:
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return nxt - timedelta(days=n)


def _expand(start: datetime, rule: dict[str, str], lo: datetime, hi: datetime, exdates: set[datetime]) -> list[datetime]:
    until = None
    if rule.get("UNTIL"):
        u, _ = _when(rule["UNTIL"], {}, start.tzinfo)
        until = u if rule["UNTIL"].endswith("Z") or "T" in rule["UNTIL"] else u + timedelta(days=1)
    count = int(rule["COUNT"]) if rule.get("COUNT", "").isdigit() else None
    out, n = [], 0
    for naive in _candidates(start, rule):
        n += 1
        occ = naive.replace(tzinfo=start.tzinfo)
        if (until and occ > until) or (count and n > count) or occ > hi or n > 20000:
            break
        if occ >= lo and occ.astimezone(UTC) not in exdates:
            out.append(occ)
    return out


# ------------------------------------------------------------------------------------------------ public
def parse_events(ics: str, lo: datetime, hi: datetime, tz) -> list[Event]:
    """Events (recurring ones expanded) that touch [lo, hi], sorted by start."""
    lines = _unfold(ics)
    blocks: list[list[str]] = []
    cur: list[str] | None = None
    for ln in lines:
        if ln.upper() == "BEGIN:VEVENT":
            cur = []
        elif ln.upper() == "END:VEVENT":
            if cur is not None:
                blocks.append(cur)
            cur = None
        elif cur is not None:
            cur.append(ln)
    overridden: set[tuple[str, datetime]] = set()
    parsed: list[dict] = []
    for blk in blocks:
        ev: dict = {"exdates": set()}
        for ln in blk:
            name, params, value = _prop(ln)
            if name == "SUMMARY":
                ev["title"] = _unescape(value)
            elif name == "LOCATION":
                ev["where"] = _unescape(value).replace("\n", ", ")
            elif name == "UID":
                ev["uid"] = value
            elif name == "STATUS":
                ev["status"] = value.upper()
            elif name == "DTSTART":
                try:
                    ev["start"], ev["all_day"] = _when(value, params, tz)
                except ValueError:
                    pass
            elif name == "DTEND":
                try:
                    ev["end"], _ = _when(value, params, tz)
                except ValueError:
                    pass
            elif name == "DURATION":
                ev["duration"] = _duration(value)
            elif name == "RRULE":
                ev["rule"] = _rule(value)
            elif name == "EXDATE":
                for v in value.split(","):
                    try:
                        ev["exdates"].add(_when(v, params, tz)[0].astimezone(UTC))
                    except ValueError:
                        pass
            elif name == "RECURRENCE-ID":
                try:
                    ev["recurrence_id"] = _when(value, params, tz)[0].astimezone(UTC)
                except ValueError:
                    pass
        if "start" not in ev or ev.get("status") == "CANCELLED":
            if ev.get("recurrence_id") and ev.get("uid"):
                overridden.add((ev["uid"], ev["recurrence_id"]))
            continue
        if ev.get("recurrence_id") and ev.get("uid"):
            overridden.add((ev["uid"], ev["recurrence_id"]))
        parsed.append(ev)

    out: list[Event] = []
    for ev in parsed:
        start: datetime = ev["start"]
        length = (ev["end"] - start) if "end" in ev else ev.get("duration") or (
            timedelta(days=1) if ev["all_day"] else timedelta(0))
        if ev["all_day"] and length <= timedelta(0):
            length = timedelta(days=1)
        starts = [start] if not ev.get("rule") or ev.get("recurrence_id") else \
            _expand(start, ev["rule"], lo - length, hi, ev["exdates"])
        for s in starts:
            if ev.get("rule") and not ev.get("recurrence_id") and ev.get("uid") \
                    and (ev["uid"], s.astimezone(UTC)) in overridden:
                continue
            e = s + length
            if e < lo or s > hi:
                continue
            out.append(Event(ev.get("title") or "(senza titolo)", s, e, ev["all_day"], ev.get("where", "")))
    return sorted(out, key=lambda x: x.start)


async def fetch_events(url: str, days: int, tz, *, start: datetime | None = None, client=None) -> list[Event]:
    """Downloads the calendar and returns the events of the next `days` days (today included)."""
    import httpx
    local = (start or datetime.now(tz)).astimezone(tz)
    lo = datetime.combine(local.date(), datetime.min.time(), tz)
    hi = lo + timedelta(days=max(1, min(days, 90))) - timedelta(seconds=1)
    if client is None:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
            r = await c.get(url.replace("webcal://", "https://"))
    else:
        r = await client.get(url.replace("webcal://", "https://"))
    r.raise_for_status()
    return parse_events(r.text, lo, hi, tz)
