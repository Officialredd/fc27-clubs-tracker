"""Clubs playoffs: the final week of each league season.

The playoffs open on a Thursday and last a week, and the season ends when they close (the next one starts in
Division 5 for everyone). A club can play only so many playoff matches, set by the division it's in when they open:
promotions stop then. League matches can still be played during the week. The final placing (a percentage of all
clubs) decides how many fans the club gets, so it's about winning as many as possible.
"""
import json

import pandas as pd

from . import seasons

# Playoff matches allowed, by EA's division code (6 = Division 5 ... 2 = Division 1, 1 = Elite)
LIMITS = {"6": 10, "5": 10, "4": 15, "3": 15, "2": 20, "1": 25}
WEEK = pd.Timedelta(days=7)
SHOW_AFTER = pd.Timedelta(days=4)   # the playoffs card stays this long after they close
SOON = pd.Timedelta(days=3)         # the season card mentions the playoffs this long before they open
THURSDAY = 3


def _opening(when) -> pd.Timestamp:
    """The Thursday the playoff week containing this moment opened (at the hour EA's seasons change)."""
    cal = seasons.calendar_starts()
    hour = cal[-1][1] if cal else pd.Timestamp("2026-12-03 08:00")
    when = pd.Timestamp(when)
    start = (when.normalize() - pd.Timedelta(days=(when.weekday() - THURSDAY) % 7)
             + pd.Timedelta(hours=hour.hour, minutes=hour.minute))
    return start - WEEK if start > when else start


def windows(matches: pd.DataFrame | None = None) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Playoff weeks (opens, closes) in local time: the last week of each season in EA's calendar, plus the
    Thursday-to-Thursday week around any playoff match the calendar doesn't cover yet."""
    out = [(t - WEEK, t) for _, t in seasons.calendar_starts()]
    if matches is not None and not matches.empty and "match_type" in matches:
        for w in matches.loc[matches["match_type"] == "playoffMatch", "when"]:
            if not any(a <= w < b for a, b in out):
                start = _opening(w)
                out.append((start, start + WEEK))
    return sorted(set(out))


def next_window(now, matches: pd.DataFrame | None = None):
    """The next playoff week that hasn't opened yet (or None)."""
    later = [(a, b) for a, b in windows(matches) if a > pd.Timestamp(now)]
    return later[0] if later else None


def _division_at(snaps: pd.DataFrame | None, t) -> str | None:
    if snaps is None or snaps.empty or "division" not in snaps:
        return None
    s = snaps.dropna(subset=["division"]).sort_values("at")
    if s.empty:
        return None
    before = s[s["at"] <= t]
    row = before.iloc[-1] if not before.empty else s.iloc[0]
    try:
        return str(int(float(row["division"])))
    except (TypeError, ValueError):
        return None


def _season_playoff_games(raw):
    """EA's count of this season's playoff matches, from a club snapshot's raw data."""
    try:
        v = (json.loads(raw) if isinstance(raw, str) else (raw or {})).get("season", {}).get("gamesPlayedPlayoff")
        return None if v is None else int(v)
    except (TypeError, ValueError, AttributeError):
        return None


def ea_count(snaps: pd.DataFrame | None, start, end) -> int | None:
    """How many playoff matches EA counted in the week: its season count from the last check before the playoffs
    closed, or else the rise in its all-time count over the week."""
    if snaps is None or snaps.empty:
        return None
    s = snaps.sort_values("at")
    week = s[(s["at"] >= start) & (s["at"] < end)]
    if week.empty:
        return None
    if "raw" in week:
        vals = [v for v in (_season_playoff_games(r) for r in week["raw"]) if v is not None]
        if vals:
            return vals[-1]
    if "playoff_games" in s:
        before = s[s["at"] < start].dropna(subset=["playoff_games"])
        after = week.dropna(subset=["playoff_games"])
        if not before.empty and not after.empty:
            return max(int(after["playoff_games"].iloc[-1]) - int(before["playoff_games"].iloc[-1]), 0)
    return None


def status(matches: pd.DataFrame, snaps: pd.DataFrame | None, now) -> dict | None:
    """The playoff week to show now (open, or closed less than SHOW_AFTER ago) and how it's going; None otherwise."""
    now = pd.Timestamp(now)
    for start, end in reversed(windows(matches)):
        if not start <= now < end + SHOW_AFTER:
            continue
        m = matches if matches is not None else pd.DataFrame()
        inside = m[(m["when"] >= start) & (m["when"] < end)] if not m.empty else m
        po = inside[inside["match_type"] == "playoffMatch"] if not inside.empty else inside
        div = _division_at(snaps, start)
        ea_played = ea_count(snaps, start, end)
        w = int((po["result"] == "W").sum()) if not po.empty else 0
        d_ = int((po["result"] == "D").sum()) if not po.empty else 0
        lo = int((po["result"] == "L").sum()) if not po.empty else 0
        played = max(len(po), ea_played or 0)
        limit = LIMITS.get(div) if div else None
        season_no = next((no - 1 for no, t in seasons.calendar_starts() if t == end), None)
        return {"start": start, "end": end, "open": now < end, "season": season_no, "division": div,
                "limit": limit, "played": played, "tracked": len(po), "left": (max(limit - played, 0) if limit else None),
                "w": w, "d": d_, "l": lo, "points": 3 * w + d_, "gf": int(po["goals_for"].sum()) if not po.empty else 0,
                "ga": int(po["goals_against"].sum()) if not po.empty else 0,
                "league_during": int((inside["match_type"] == "leagueMatch").sum()) if not inside.empty else 0,
                "matches": po.sort_values("when", ascending=False) if not po.empty else po}
    return None
