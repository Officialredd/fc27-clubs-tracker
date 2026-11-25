"""Clubs league seasons.

EA's match data has no season number, but every club snapshot carries the current season's record (league games
played, points, promotions) and how many seasons the club has finished. When that record starts again from zero,
or the finished count goes up, a new season began, and the new season's game count tells which of our league
matches already belong to it. So seasons show up as soon as the collector has seen one change.
"""
import json

import pandas as pd


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


def number(matches: pd.DataFrame, season_starts: list) -> pd.Series:
    """Season number of each match: 1 for the first season seen, 2 for the next..."""
    if matches.empty:
        return pd.Series(dtype=int)
    n = pd.Series(1, index=matches.index)
    for t in season_starts:
        n += (matches["when"] >= t).astype(int)
    return n


def label(first, last, current: bool) -> str:
    if current:
        return f"This season (since {pd.Timestamp(first):%d %b})" if pd.notna(first) else "This season"
    return f"{pd.Timestamp(first):%d %b} - {pd.Timestamp(last):%d %b %Y}"


def table(matches: pd.DataFrame, snaps: pd.DataFrame, season_starts: list) -> pd.DataFrame:
    """One row per season, newest first: record, goals, points and EA's numbers at its end."""
    if matches.empty:
        return pd.DataFrame()
    m = matches.assign(season=number(matches, season_starts))
    last_no = int(m["season"].max())
    rows = []
    for no, g in m.groupby("season"):
        w, d, lo = (g["result"] == "W").sum(), (g["result"] == "D").sum(), (g["result"] == "L").sum()
        n = len(g)
        first, last = g["when"].min(), g["when"].max()
        row = {"season": int(no), "label": label(first, last, no == last_no), "first": first, "last": last,
               "matches": n, "w": int(w), "d": int(d), "l": int(lo), "points": int(3 * w + d), "ppg": (3 * w + d) / n,
               "gf": int(g["goals_for"].sum()), "ga": int(g["goals_against"].sum()),
               "win_pct": 100.0 * w / n, "clean_sheets": int((g["goals_against"] == 0).sum())}
        if "team_grade_score" in g:
            row["team_grade"] = g["team_grade_score"].mean()
        if snaps is not None and not snaps.empty:
            nxt = season_starts[no - 1] if no - 1 < len(season_starts) else None
            sn = snaps[(snaps["at"] >= (season_starts[no - 2] if no >= 2 else snaps["at"].min()))
                       & ((snaps["at"] < nxt) if nxt is not None else True)].sort_values("at")
            if not sn.empty:
                end = sn.iloc[-1]
                row.update({"sr_start": sn["skill_rating"].iloc[0], "sr_end": end["skill_rating"],
                            "division": end.get("division"), "ea_points": end.get("season_points"),
                            "promotions": end.get("promotions")})
        rows.append(row)
    return pd.DataFrame(rows).sort_values("season", ascending=False).reset_index(drop=True)
