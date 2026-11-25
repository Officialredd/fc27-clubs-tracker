"""Clubs league seasons.

EA's match data has no season number, but every club snapshot carries the current season's record (league games
played, points, promotions) and how many seasons the club has finished. When that record starts again from zero,
or the finished count goes up, a new season began, and the new season's game count tells which of our league
matches already belong to it. So seasons show up as soon as the collector has seen one change.

EA's season calendar names the seasons (Season 2, Season 3...): the Clubs league season ends with the game's
season, after a final week of playoffs. A date from the calendar also splits the matches when the collector saw
nothing around it; when EA's record carried on past such a date, the record wins.
"""
import calendar as _cal
import datetime as dt
import json
import time

import pandas as pd

# When each season starts (UTC), from EA's calendar: add new ones when EA announces them.
CALENDAR = [
    (2, "2026-10-22 06:59"),   # Season 1 ended 22 October, 06:59 UTC
    (3, "2026-12-03 07:00"),   # EA: Season 2 runs to 3 December (the hour is a guess: Season 1's)
]
NEAR = pd.Timedelta(days=7)    # a change EA's record shows within a week of a calendar date is that season change


def _finished(raw) -> int | None:
    """How many seasons the club has finished, as EA counts them (finishes per division, added up)."""
    try:
        data = json.loads(raw) if isinstance(raw, str) else (raw or {})
        overall = data.get("overall") or {}
    except (TypeError, ValueError, AttributeError):
        return None
    vals = [v for k, v in overall.items() if k.startswith("finishesInDivision")]
    return sum(int(v or 0) for v in vals) if vals else None


def _games(v) -> int | None:
    return None if v is None or pd.isna(v) else int(v)


def starts(snaps: pd.DataFrame, matches: pd.DataFrame) -> list[pd.Timestamp]:
    """When each season after the first one began (local time, oldest first)."""
    if snaps is None or snaps.empty or matches is None or matches.empty or "season_games" not in snaps:
        return []
    s = snaps.sort_values("at").reset_index(drop=True)
    games = [_games(v) for v in s["season_games"]]
    fins = [_finished(v) for v in (s["raw"] if "raw" in s else [None] * len(s))]
    league = matches[matches["match_type"] == "leagueMatch"].sort_values("when")
    out, prev_g, prev_f, prev_at = [], None, None, None
    for i in range(len(s)):
        g, f, at = games[i], fins[i], pd.Timestamp(s["at"].iloc[i])
        dropped = prev_g is not None and g is not None and g < prev_g
        finished = prev_f is not None and f is not None and f > prev_f
        if dropped and not finished and i + 1 < len(s) and games[i + 1] is not None and games[i + 1] >= prev_g:
            dropped = False   # EA answered with an odd record once: the next check has the old one again
        if dropped or finished:
            after = league[league["when"] > prev_at] if prev_at is not None else league
            mine = after[after["when"] <= at]
            if g and len(mine):   # the new season's g league games are the last g up to this snapshot
                start = mine["when"].iloc[-min(g, len(mine))]
            else:
                start = at
            if not out or start > out[-1]:
                out.append(start)
        if g is not None:
            prev_g = g
        if f is not None:
            prev_f = f
        prev_at = at
    return out


def calendar_starts() -> list[tuple[int, pd.Timestamp]]:
    """(season number, when it starts in local time) from EA's calendar."""
    out = []
    for no, utc in CALENDAR:
        t = _cal.timegm(time.strptime(utc, "%Y-%m-%d %H:%M"))
        out.append((no, pd.Timestamp(dt.datetime.fromtimestamp(t))))   # local time, like the matches' "when"
    return out


def breaks(snaps: pd.DataFrame, matches: pd.DataFrame) -> tuple[list[pd.Timestamp], int]:
    """Where one season ends and the next begins among our matches (oldest first), and the number of the season
    the first match is in. EA's record decides; a calendar date counts when the collector saw nothing around it."""
    if matches is None or matches.empty:
        return [], 1
    seen = starts(snaps, matches)
    first, last = matches["when"].min(), matches["when"].max()
    cal = calendar_starts()
    first_no = max([no for no, t in cal if t <= first], default=1)
    checked = []
    if snaps is not None and not snaps.empty and "season_games" in snaps:
        checked = list(snaps.loc[snaps["season_games"].notna(), "at"])
    out = list(seen)
    for no, t in cal:
        if not first < t <= last or any(abs(x - t) <= NEAR for x in seen):
            continue
        if any(a < t for a in checked) and any(a >= t for a in checked):
            continue   # EA's record was checked on both sides of the date and carried on: no new season then
        out.append(t)
    return sorted(out), first_no


def number(matches: pd.DataFrame, season_breaks: list, first_no: int = 1) -> pd.Series:
    """Season number of each match."""
    if matches.empty:
        return pd.Series(dtype=int)
    n = pd.Series(first_no, index=matches.index)
    for t in season_breaks:
        n += (matches["when"] >= t).astype(int)
    return n


def span(season_breaks: list, first_no: int, no: int) -> tuple:
    """(from, until) of season no: None where it's open-ended."""
    i = no - first_no
    return (season_breaks[i - 1] if 1 <= i <= len(season_breaks) else None,
            season_breaks[i] if 0 <= i < len(season_breaks) else None)


def label(no: int, first, last, current: bool) -> str:
    if current:
        return f"Season {no} (this season)"
    return f"Season {no} ({pd.Timestamp(first):%d %b} - {pd.Timestamp(last):%d %b})"


def table(matches: pd.DataFrame, snaps: pd.DataFrame, season_breaks: list, first_no: int = 1) -> pd.DataFrame:
    """One row per season, newest first: record, goals, points and EA's numbers at its end."""
    if matches.empty:
        return pd.DataFrame()
    m = matches.assign(season=number(matches, season_breaks, first_no))
    last_no = int(m["season"].max())
    rows = []
    for no, g in m.groupby("season"):
        w, d, lo = (g["result"] == "W").sum(), (g["result"] == "D").sum(), (g["result"] == "L").sum()
        n = len(g)
        first, last = g["when"].min(), g["when"].max()
        row = {"season": int(no), "label": label(int(no), first, last, no == last_no), "first": first, "last": last,
               "matches": n, "w": int(w), "d": int(d), "l": int(lo), "points": int(3 * w + d), "ppg": (3 * w + d) / n,
               "gf": int(g["goals_for"].sum()), "ga": int(g["goals_against"].sum()),
               "win_pct": 100.0 * w / n, "clean_sheets": int((g["goals_against"] == 0).sum()),
               "playoffs": int((g["match_type"] == "playoffMatch").sum()) if "match_type" in g else 0}
        if "team_grade_score" in g:
            row["team_grade"] = g["team_grade_score"].mean()
        if snaps is not None and not snaps.empty:
            lo_t, hi_t = span(season_breaks, first_no, int(no))
            keep = pd.Series(True, index=snaps.index)
            if lo_t is not None:
                keep &= snaps["at"] >= lo_t
            if hi_t is not None:
                keep &= snaps["at"] < hi_t
            sn = snaps[keep].sort_values("at")
            if not sn.empty:
                end = sn.iloc[-1]
                row.update({"sr_start": sn["skill_rating"].iloc[0], "sr_end": end["skill_rating"],
                            "division": end.get("division"), "ea_points": end.get("season_points"),
                            "promotions": end.get("promotions")})
        rows.append(row)
    return pd.DataFrame(rows).sort_values("season", ascending=False).reset_index(drop=True)
