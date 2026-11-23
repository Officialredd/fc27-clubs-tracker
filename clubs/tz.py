"""The club's time zone, so the online copy shows match times the way the PC (and the club) sees them.

Streamlit's servers run on UTC. The PC describes its own time zone as a POSIX TZ string, worked out from how
this machine converts times (so it needs no time zone database and works the same on Windows), sends it in
club.json, and the online app uses it for all local times. Denmark, for example: <+01>-1<+02>-2,M3.5.0/2,M10.5.0/3
"""
import calendar
import datetime as dt
import functools
import os
import time


def _offset(t: int) -> int:
    """Seconds this machine's local time is ahead of UTC at moment t."""
    return calendar.timegm(time.localtime(t)) - t


def _name(off: int) -> str:
    sign, a = ("+" if off >= 0 else "-"), abs(off) // 60
    h, m = divmod(a, 60)
    return f"<{sign}{h:02d}{m:02d}>" if m else f"<{sign}{h:02d}>"


def _clock(sec: int) -> str:
    h, rest = divmod(abs(sec), 3600)
    m, s = divmod(rest, 60)
    return f"{h}" + (f":{m:02d}" if m or s else "") + (f":{s:02d}" if s else "")


def _posix_offset(off: int) -> str:
    """POSIX counts the other way round: what to add to local time to get UTC (Denmark, UTC+1, is -1)."""
    return ("-" if off > 0 else "") + _clock(off)


def _rule(local: dt.datetime) -> str:
    """'Mmonth.week.weekday/time' for a change at this local time (week 5 = the last one in the month)."""
    last = calendar.monthrange(local.year, local.month)[1]
    week = 5 if local.day + 7 > last else (local.day - 1) // 7 + 1
    day = (local.weekday() + 1) % 7   # POSIX: Sunday is 0
    return f"M{local.month}.{week}.{day}/{_clock(local.hour * 3600 + local.minute * 60 + local.second)}"


@functools.lru_cache(maxsize=4)
def _describe(year: int) -> str:
    t0 = calendar.timegm((year, 1, 1, 0, 0, 0, 0, 0, 0))
    t1 = calendar.timegm((year + 1, 1, 1, 0, 0, 0, 0, 0, 0))
    changes, prev, t = [], _offset(t0), t0
    while t < t1:   # hour by hour, then to the second
        nxt = _offset(t + 3600)
        if nxt != prev:
            lo, hi = t, t + 3600
            while hi - lo > 1:
                mid = (lo + hi) // 2
                lo, hi = (mid, hi) if _offset(mid) == prev else (lo, mid)
            changes.append((hi, prev, nxt))
            prev = nxt
        t += 3600
    if not changes:
        return _name(prev) + _posix_offset(prev)
    offsets = {c[1] for c in changes} | {c[2] for c in changes}
    if len(changes) != 2 or len(offsets) != 2:   # an unusual year: keep today's offset
        now = _offset(int(time.time()))
        return _name(now) + _posix_offset(now)
    std, dst = min(offsets), max(offsets)
    start = next(c[0] for c in changes if c[2] == dst)
    end = next(c[0] for c in changes if c[2] == std)
    epoch = dt.datetime(1970, 1, 1)
    start_local = epoch + dt.timedelta(seconds=start + std)   # POSIX: the clock time just before the change
    end_local = epoch + dt.timedelta(seconds=end + dst)
    return f"{_name(std)}{_posix_offset(std)}{_name(dst)}{_posix_offset(dst)},{_rule(start_local)},{_rule(end_local)}"


def describe(year: int | None = None) -> str:
    """This machine's time zone as a POSIX TZ string ('' if it can't be worked out)."""
    try:
        return _describe(year or dt.date.today().year)
    except Exception:
        return ""


def apply(tz: str | None) -> bool:
    """Use this time zone for every local time in this process (the online copy). True when it changed."""
    tz = (tz or "").strip()
    if not tz or not hasattr(time, "tzset") or os.environ.get("TZ") == tz:
        return False
    os.environ["TZ"] = tz
    time.tzset()
    return True
