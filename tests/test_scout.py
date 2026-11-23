"""Opponent scouting: labels, the strength part of the grades, and storing and reading a scouted club's games.
Run: python tests/test_scout.py"""
import copy
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
os.environ.setdefault("CLUBS_DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("CLUBS_CONFIG", os.path.join(os.environ["CLUBS_DATA_DIR"], "config.json"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from clubs import db, grades, parse, scout  # noqa: E402

RAW = json.loads((HERE / "fixtures" / "real_match_dnf.json").read_text(encoding="utf-8"))
US, THEM = "2315992", "1881695"


def test_labels():
    assert [scout.bucket(v) for v in (200, 150, 60, 0, -49, -50, -150, None)] == \
        ["Much stronger", "Much stronger", "Stronger", "Even", "Even", "Weaker", "Much weaker", None]
    assert scout.expectation(80) == "Underdogs" and scout.expectation(-80) == "Favourites"
    assert scout.expectation(10) == "Even match"
    assert scout.verdict(80, "W") == "Upset win" and scout.verdict(-80, "L") == "Upset loss"
    assert scout.verdict(80, "L") is None and scout.verdict(np.nan, "W") is None


def test_strength_in_grades():
    line = {"pos": "midfielder", "rating": 7.5, "goals": 0, "assists": 1, "shots": 1, "passes_made": 20,
            "pass_attempts": 25, "tackles_made": 2, "tackle_attempts": 5, "saves": 0, "mom": 0, "red_cards": 0,
            "gf": 2, "ga": 1, "res": "W"}
    df = pd.DataFrame([line, {**line, "sr_gap": 100}, {**line, "sr_gap": -1000}])
    raw = grades.player_parts(df)["raw"].to_numpy()
    assert abs((raw[1] - raw[0]) - 3.0) < 1e-9          # 100 SR stronger: +3
    assert abs((raw[2] - raw[0]) + 8.0) < 1e-9          # capped at -8
    team = {"gf": 2, "ga": 1, "res": "W", "shots": 8, "passes_made": 80, "pass_attempts": 100, "rating": 7.5,
            "humans": 3, "opp_humans": 3}
    t = grades.team_parts(pd.DataFrame([team, {**team, "sr_gap": 100}, {**team, "sr_gap": 500}]))["raw"].to_numpy()
    assert abs((t[1] - t[0]) - 4.0) < 1e-9 and abs((t[2] - t[0]) - 10.0) < 1e-9


def _game(raw, mid, ts, swap_goals=False):
    g = copy.deepcopy(raw)
    g["matchId"], g["timestamp"] = str(mid), ts
    if swap_goals:   # make it a win for THEM
        a, b = g["clubs"][US], g["clubs"][THEM]
        a["goals"], b["goals"] = b["goals"], a["goals"]
        a["goalsAgainst"], b["goalsAgainst"] = b["goalsAgainst"], a["goalsAgainst"]
        for c, w in ((a, "0"), (b, "1")):
            c["wins"], c["losses"], c["ties"], c["result"] = w, "1" if w == "0" else "0", "0", "1" if w == "1" else "2"
    return g


def test_store_and_profile():
    # our match against THEM at ts 1000; their games: 4 before (3 wins), ours, 1 after
    ts0 = 1_000_000
    ours = _game(RAW, 900, ts0)
    m, players, clubs = parse.parse_match(ours, US, "common-gen5", "leagueMatch")
    db.save_match(m, players, clubs, ours)
    db.save_club_snapshot(US, {"skillRating": "1500"}, None)
    games = [_game(RAW, 901 + i, ts0 - 3600 * (i + 1), swap_goals=(i < 3)) for i in range(4)]
    games += [ours, _game(RAW, 999, ts0 + 3600)]
    parsed = [parse.parse_match(g, THEM, "common-gen5", "leagueMatch") + (g,) for g in games]
    db.save_scouting(THEM, "900", parsed, {"skillRating": "1620", "wins": "30"}, our_sr=1500)
    sc = scout.load()
    assert len(sc.games) == 6 and set(sc.games["persp"]) == {THEM}
    assert sc.games["team_grade_score"].notna().all()
    picked, basis = scout.games_for(sc, THEM, "900", ts0)
    assert basis == "before" and len(picked) == 4 and "900" not in set(picked["match_id"])
    s = scout.summary(picked)
    assert (s["w"], s["l"]) == (3, 1) and s["n"] == 4
    pm = scout.per_match(sc, db.matches_df(US))
    r = pm.loc["900"]
    assert r["opp_sr"] == 1620 and r["sr_diff"] == 120 and r["strength"] == "Stronger"
    assert r["expectation"] == "Underdogs" and r["verdict"] == "Upset win"   # we won 3-0
    pl = scout.their_players(sc, picked, THEM)
    assert len(pl) == 2 and pl["matches"].max() == 4


def test_later_games_when_none_before():
    sc = scout.load()
    picked, basis = scout.games_for(sc, THEM, "900", 1)     # a match long before all their stored games
    assert basis == "later" and len(picked) == 5


def test_pending_and_reuse():
    assert db.scout_pending(US) == []                       # match 900 was scouted
    db.scout_failed(THEM, "900", "nothing")                 # a later failure keeps the good one
    assert db.scout_fetches_df().set_index("for_match").loc["900", "ok"] == 1


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
