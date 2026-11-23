"""Opponent scouting: right after a match, pull the other club's last 10 games and EA's numbers for it
(skill rating, record), then work out how strong they were and how they had been playing.

- Strength comes from EA's skill rating (SR), the rating EA uses to pair clubs: their SR minus yours, measured
  when they were scouted (a couple of minutes after the match while the collector runs). It also moves the
  match grades a little (clubs/grades.py): a stronger opponent adds, a weaker one takes off.
- Their form, goals, humans, usual team grade and players come from their last 10 games BEFORE your match.
  EA only keeps a club's last 10 games of each type, so for matches scouted later (older matches, or the
  collector was off) their games after the match are used instead, and the report says so.
"""
import datetime as dt
import time
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import api, db, grades, parse

TYPES = ("leagueMatch", "playoffMatch")
LAST_N = 10           # games per profile
MIN_BEFORE = 3        # fewer games from before the match: use their later games instead
MAX_TRIES = 3         # give up on a club after this many failed tries
EVEN = 50             # SR difference that still counts as an even match
BIG = 150             # ... and from here on it's much stronger / weaker
BUCKETS = ["Much stronger", "Stronger", "Even", "Weaker", "Much weaker"]


# ---------------------------------------------------------------- labels
def bucket(diff) -> str | None:
    """How strong the other club was, from their SR minus yours."""
    if diff is None or pd.isna(diff):
        return None
    return ("Much stronger" if diff >= BIG else "Stronger" if diff >= EVEN else "Even" if diff > -EVEN
            else "Weaker" if diff > -BIG else "Much weaker")


def expectation(diff) -> str | None:
    if diff is None or pd.isna(diff):
        return None
    return "Underdogs" if diff >= EVEN else "Favourites" if diff <= -EVEN else "Even match"


def verdict(diff, result) -> str | None:
    """'Upset win' when you beat a stronger club, 'Upset loss' when a weaker one beat you."""
    if diff is None or pd.isna(diff):
        return None
    if diff >= EVEN and result == "W":
        return "Upset win"
    if diff <= -EVEN and result == "L":
        return "Upset loss"
    return None


# ---------------------------------------------------------------- pulling from EA
def scout_match(cfg: dict, match_id, opp_id) -> dict:
    """Pull the other club's last 10 games of each type (league, playoffs) and EA's club numbers, and store them
    for this match of ours. Returns {"ok", "n", "errors"}."""
    plat = cfg["platform"]
    got, errors = [], []
    for mt in TYPES:
        try:
            got += [(mt, m) for m in api.matches(opp_id, plat, mt, LAST_N)]
        except api.ApiError as e:
            errors.append(f"{mt}: {e}")
    stats = None
    try:
        stats = api.overall_stats(opp_id, plat)
    except api.ApiError as e:
        errors.append(f"club numbers: {e}")
    if not got and stats is None:
        db.scout_failed(opp_id, match_id, "; ".join(errors) or "EA sent nothing")
        return {"ok": False, "n": 0, "errors": errors}
    parsed = []
    for mt, raw in got:
        try:
            m, players, clubs = parse.parse_match(raw, opp_id, plat, mt)
            parsed.append((m, players, clubs, raw))
        except Exception as e:   # one odd game shouldn't stop the rest
            errors.append(f"couldn't read game {raw.get('matchId')}: {e!r}")
    our_sr = db.latest_club_snapshot(cfg["club_id"]).get("skill_rating")
    db.save_scouting(opp_id, match_id, parsed, stats, our_sr, ("; ".join(errors)[:400] or None))
    return {"ok": True, "n": len(parsed), "errors": errors}


REUSE_HOURS = 6       # an older match against a club scouted this recently reuses that scouting


def _reusable(row: dict) -> dict | None:
    """An earlier scouting of the same club, made after this match and recently, that can stand in for it."""
    prev = db.latest_scout(row["opp_id"])
    if not prev or not prev.get("at"):
        return None
    try:
        at = dt.datetime.fromisoformat(prev["at"]).timestamp()
    except ValueError:
        return None
    if at >= (row.get("ts") or 0) + 60 and time.time() - at < REUSE_HOURS * 3600:
        return prev
    return None


def run(cfg: dict, log=print, limit: int = 4) -> dict:
    """Scout the opponents of matches that weren't scouted yet, newest first (new matches come first by
    themselves): at most `limit` clubs are asked from EA per call, so a long history fills in over a few checks.
    An older match against a club that was just scouted reuses that scouting."""
    done, failed, asked = [], 0, 0
    for row in db.scout_pending(cfg["club_id"], limit=limit * 10, max_tries=MAX_TRIES):
        prev = _reusable(row)
        if prev:
            db.copy_scout(row["opp_id"], row["match_id"], prev)
            done.append(row)
            continue
        if asked >= limit:
            continue
        asked += 1
        try:
            res = scout_match(cfg, row["match_id"], row["opp_id"])
        except Exception as e:   # never stop the collector over scouting
            db.scout_failed(row["opp_id"], row["match_id"], f"{type(e).__name__}: {e}")
            res = {"ok": False, "errors": [str(e)]}
        if res["ok"]:
            done.append(row)
            log(f"Scouted {row.get('opp_name') or row['opp_id']}: {res['n']} of their games.")
        else:
            failed += 1
            log(f"Couldn't scout {row.get('opp_name') or row['opp_id']}: " + "; ".join(res["errors"][-2:]))
    return {"done": done, "failed": failed}


# ---------------------------------------------------------------- working it out
@dataclass
class Scouting:
    fetches: pd.DataFrame    # one row per (scouted club, our match): when, SR, ours, EA's record
    games: pd.DataFrame      # every scouted game from the scouted club's side ("persp"), with both team grades
    lines: pd.DataFrame      # the scouted clubs' own player lines in those games, with grades

    @property
    def empty(self) -> bool:
        return self.fetches.empty


def load() -> Scouting:
    f = db.scout_fetches_df()
    g = db.scout_matches_df()
    if g.empty:
        return Scouting(f, g, pd.DataFrame())
    g, own = _frames(g, db.scout_players_df())
    return Scouting(f, g, own)


def _frames(g: pd.DataFrame, p: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """g: games (club_id = the club whose side they're seen from), p: player lines of both clubs. Returns the games
    with both team grades and that club's own player lines with their grades."""
    g = g.rename(columns={"club_id": "persp"})
    g["key"] = g["persp"].astype(str) + ":" + g["match_id"].astype(str)
    # every player line, seen from each scouted club that played that game
    lines = p.drop(columns=["side"]).merge(g[["match_id", "persp", "key", "goals_for", "goals_against", "result", "ts"]],
                                          on="match_id")
    lines["side"] = np.where(lines["club_id"].astype(str) == lines["persp"].astype(str), "us", "opp")
    # team grades (no strength part: their opponents' SR isn't known)
    tg = grades.grade_teams(lines.drop(columns=["match_id"]).rename(columns={"key": "match_id"}),
                            g.drop(columns=["match_id"]).rename(columns={"key": "match_id"}))
    if not tg.empty:
        g = g.merge(tg[["team_grade_score", "opp_team_grade_score"]], left_on="key", right_index=True, how="left")
    else:
        g["team_grade_score"] = g["opp_team_grade_score"] = np.nan
    own = lines[lines["side"] == "us"].copy()
    if not own.empty:
        gl = grades.grade_lines(own)
        own["grade_score"] = gl["grade_score"]
    return g, own


# ---------------------------------------------------------------- any club (the Search page)
def pull(club_id, platform: str, with_info: bool = False) -> dict:
    """A club's last 10 games of each type and EA's numbers for it, straight from EA (and stored for later).
    Returns {"parsed": [(match, players, clubs, raw), ...], "stats", "info", "errors"}."""
    out = {"parsed": [], "stats": None, "info": None, "errors": []}
    for mt in TYPES:
        try:
            for raw in api.matches(club_id, platform, mt, LAST_N):
                try:
                    m, players, clubs = parse.parse_match(raw, club_id, platform, mt)
                    out["parsed"].append((m, players, clubs, raw))
                except Exception as e:
                    out["errors"].append(f"couldn't read game {raw.get('matchId')}: {e!r}")
        except api.ApiError as e:
            out["errors"].append(f"{mt}: {e}")
    try:
        out["stats"] = api.overall_stats(club_id, platform)
    except api.ApiError as e:
        out["errors"].append(f"club numbers: {e}")
    if with_info or not out["parsed"]:
        try:
            out["info"] = api.club_info(club_id, platform)
        except api.ApiError as e:
            out["errors"].append(f"club info: {e}")
    if out["parsed"]:
        try:
            db.save_scouting(club_id, None, out["parsed"], out["stats"])
        except Exception as e:   # storing is a bonus: the page works without it
            out["errors"].append(f"couldn't store their games: {e!r}")
    return out


def from_parsed(parsed: list) -> Scouting:
    """Scouting worked out from games pulled just now (not from the database)."""
    if not parsed:
        return Scouting(pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
    g = pd.DataFrame([m for m, _, _, _ in parsed]).drop_duplicates("match_id")
    p = pd.DataFrame([r for _, players, _, _ in parsed for r in players]).drop_duplicates(["match_id", "club_id", "player_id"])
    g, own = _frames(g, p)
    return Scouting(pd.DataFrame(), g, own)


def _shrunk(scores: pd.Series, k: float = 2) -> float:
    s = pd.to_numeric(scores, errors="coerce").dropna()
    return float((s.sum() + k * 50) / (len(s) + k)) if len(s) else np.nan


def games_for(sc: Scouting, opp_id, match_id, ts) -> tuple[pd.DataFrame, str]:
    """Their last 10 games before our match ("before"), or their latest games if EA no longer had enough of
    those when they were scouted ("later")."""
    if sc.games.empty:
        return sc.games, "none"
    g = sc.games[(sc.games["persp"].astype(str) == str(opp_id)) & (sc.games["match_id"].astype(str) != str(match_id))]
    g = g.sort_values("ts")
    before = g[g["ts"] < (ts or 0)].tail(LAST_N)
    if len(before) >= MIN_BEFORE or len(g) <= len(before):
        return before, "before"
    return g.tail(LAST_N), "later"


def summary(games: pd.DataFrame) -> dict:
    """Form and numbers of a set of their games."""
    n = len(games)
    if not n:
        return {"n": 0}
    r = games["result"]
    w, d, l_ = int((r == "W").sum()), int((r == "D").sum()), int((r == "L").sum())
    return {"n": n, "w": w, "d": d, "l": l_, "ppg": (3 * w + d) / n,
            "gf": games["goals_for"].mean(), "ga": games["goals_against"].mean(),
            "humans": games["our_players"].mean(), "opp_humans": games["opp_players"].mean(),
            "level": _shrunk(games["team_grade_score"]) if "team_grade_score" in games else np.nan,
            "form": "".join(r.fillna("-").tolist())}


def their_players(sc: Scouting, games: pd.DataFrame, opp_id) -> pd.DataFrame:
    """Their humans across those games: matches, main position, goals, assists, rating, grade, MOTM."""
    if sc.lines.empty or games.empty:
        return pd.DataFrame()
    ln = sc.lines[sc.lines["key"].isin(games["key"])]
    if ln.empty:
        return pd.DataFrame()
    g = ln.groupby("player_id")
    out = pd.DataFrame({
        "name": g["name"].last(), "matches": g.size(),
        "pos": g["pos"].agg(lambda s: s.mode().iat[0] if not s.mode().empty else None),
        "goals": g["goals"].sum(), "assists": g["assists"].sum(), "rating": g["rating"].mean(),
        "grade": g["grade_score"].mean() if "grade_score" in ln else np.nan, "mom": g["mom"].sum(),
    })
    return out.sort_values(["matches", "grade"], ascending=False)


def per_match(sc: Scouting, matches: pd.DataFrame, our_sr_now=None) -> pd.DataFrame:
    """For each of our matches: what scouting found about the other club (one row per match_id)."""
    cols = ["scouted", "scout_at", "opp_sr", "our_sr", "sr_diff", "strength", "expectation", "verdict",
            "opp_level", "opp_ppg", "opp_form", "opp_n", "scout_basis"]
    if sc.empty or matches.empty:
        return pd.DataFrame(columns=cols, index=pd.Index([], name="match_id"))
    f = sc.fetches.copy()
    f["club_id"], f["for_match"] = f["club_id"].astype(str), f["for_match"].astype(str)
    f = f.set_index(["club_id", "for_match"])
    rows = {}
    for r in matches.itertuples():
        key = (str(r.opp_id), str(r.match_id))
        if key not in f.index:
            continue
        fr = f.loc[key]
        if not fr["ok"]:
            rows[r.match_id] = {"scouted": False, "scout_at": fr["at"]}
            continue
        ours = fr["our_sr"] if not pd.isna(fr["our_sr"]) else our_sr_now
        diff = (fr["sr"] - ours) if not (pd.isna(fr["sr"]) or ours is None or pd.isna(ours)) else np.nan
        games, basis = games_for(sc, r.opp_id, r.match_id, r.ts)
        s = summary(games)
        rows[r.match_id] = {
            "scouted": True, "scout_at": fr["at"], "opp_sr": fr["sr"], "our_sr": ours, "sr_diff": diff,
            "strength": bucket(diff), "expectation": expectation(diff), "verdict": verdict(diff, r.result),
            "opp_level": s.get("level", np.nan), "opp_ppg": s.get("ppg", np.nan), "opp_form": s.get("form"),
            "opp_n": s.get("n", 0), "scout_basis": basis}
    out = pd.DataFrame.from_dict(rows, orient="index", columns=cols)
    out.index.name = "match_id"
    return out
