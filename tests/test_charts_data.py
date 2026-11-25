"""Numbers behind the strength, luck, lobby and session charts. Run: python -m pytest tests/test_charts_data.py"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clubs import analytics  # noqa: E402


def test_expected_points():
    even, stronger, weaker = analytics.expected_points([0, 100, -100], 0.1)
    assert abs(even - 1.45) < 1e-9                    # 45% wins, 10% draws
    assert stronger < even < weaker and abs((stronger + weaker) / 2 - even) < 1e-9
    assert abs(analytics.expected_points(400, 0.0) - 3 / 11) < 1e-9   # 400 SR more: about 10 to 1


def test_luck_adds_up_actual_and_predicted_points():
    m = pd.DataFrame({"game_no": [1, 2, 3, 4], "match_id": list("abcd"), "opp_name": ["A", "B", "C", "D"],
                      "sr_diff": [0, np.nan, 100, -100], "points": [3, 0, 1, 3], "result": ["W", "L", "D", "W"]})
    lk = analytics.luck(m)
    assert list(lk["game_no"]) == [1, 3, 4]           # only scouted matches
    assert list(lk["points_cum"]) == [3, 4, 7]
    exp = analytics.expected_points([0, 100, -100], 0.25)   # a quarter of the matches were draws
    assert np.allclose(lk["expected_cum"], np.cumsum(exp))


def test_lobby_compares_per_player():
    games = pd.DataFrame({"persp": ["1"] * 3 + ["2"] * 3 + ["9"] * 3, "match_id": list("abcdefghi"),
                          "goals_for": [1, 1, 1, 4, 4, 4, 9, 9, 9], "goals_against": [3] * 3 + [1] * 3 + [0] * 3,
                          "team_grade_score": [40] * 3 + [80] * 3 + [99] * 3})
    rows = []
    for persp, mid, humans, shots in [("1", "a", 2, 4), ("1", "b", 2, 4), ("1", "c", 2, 4),
                                      ("2", "d", 4, 8), ("2", "e", 4, 8), ("2", "f", 4, 8),
                                      ("9", "g", 1, 9), ("9", "h", 1, 9), ("9", "i", 1, 9)]:
        for i in range(humans):
            rows.append({"persp": persp, "match_id": mid, "name": f"p{i}", "shots": shots // humans,
                         "passes_made": 7, "pass_attempts": 10, "tackles_made": 1})
    lines = pd.DataFrame(rows)
    ours = pd.DataFrame({"goals_for": [3, 3], "goals_against": [2, 2], "us_shots": [6, 6], "our_players": [2, 2],
                         "us_tackles_made": [2, 2], "us_passes_made": [8, 8], "us_pass_attempts": [10, 10],
                         "team_grade_score": [70, 70], "team_str_adj": [5, 5]})
    t, n = analytics.lobby(ours, games, lines, own_club="9")   # club 9 is us, seen through Search: left out
    assert n == 2
    t = t.set_index("label")
    assert t.loc["Goals a match", "pct"] == 50.0          # better than club 1, not club 2
    assert t.loc["Conceded a match", "pct"] == 50.0       # fewer than club 1, more than club 2
    assert t.loc["Shots per player", "ours"] == 3.0 and t.loc["Shots per player", "pct"] == 100.0   # 3 vs 2 and 2
    assert t.loc["Team grade", "ours"] == 65.0            # without the opponent-strength part


def test_session_skill_rating():
    sess = pd.DataFrame({"start": pd.to_datetime(["2026-11-23 19:00", "2026-11-25 00:20"]),
                         "end": pd.to_datetime(["2026-11-23 23:00", "2026-11-25 01:10"]),
                         "matches": [14, 3], "w": [8, 2], "d": [1, 0], "l": [5, 1]})
    snaps = pd.DataFrame({"at": pd.to_datetime(["2026-11-23 18:00", "2026-11-23 21:00", "2026-11-23 23:20",
                                                "2026-11-25 01:15"]), "skill_rating": [1500, 1520, 1550, 1540]})
    out = analytics.session_sr(sess, snaps)
    assert list(out["change"]) == [50.0, -10.0] and list(out["sr_before"]) == [1500.0, 1550.0]


def test_skill_rating_by_match_session_day_and_time():
    t = pd.Timestamp("2026-11-23 19:00")
    mins = pd.Timedelta(minutes=1)
    m = pd.DataFrame({"game_no": [1, 2, 3, 4], "match_id": list("abcd"), "opp_name": ["A", "B", "C", "D"],
                      "when": [t, t + 15 * mins, t + 30 * mins, t + pd.Timedelta(days=1)],
                      "result": ["W", "L", "W", "W"], "goals_for": [2, 0, 3, 1], "goals_against": [1, 1, 0, 0]})
    m["ts"] = [int(w.timestamp()) for w in m["when"]]
    snaps = pd.DataFrame({"at": [t - 60 * mins, t + 5 * mins, t + 33 * mins, t + pd.Timedelta(days=1, minutes=4)],
                          "skill_rating": [1500, 1512, 1520, 1531], "division": ["5", "5", "4", "4"]})
    by_match = analytics.snap_by(snaps, m, "skill_rating", "match")
    # after match 2 the collector saw nothing before match 3 began: filled in between 1512 and 1520, marked estimated
    assert list(by_match["value"]) == [1512, 1516, 1520, 1531] and list(by_match["estimated"]) == [False, True, False, False]
    assert by_match["change"].iloc[0] == 12 and by_match.attrs["start"] == 1500
    by_session = analytics.snap_by(snaps, m, "skill_rating", "session", gap_minutes=45)
    assert list(by_session["value"]) == [1520, 1531] and list(by_session["change"]) == [20, 11]
    by_day = analytics.snap_by(snaps, m, "skill_rating", "day")
    assert list(by_day["value"]) == [1520, 1531] and list(by_day["label"]) == ["Mon 23 Nov", "Tue 24 Nov"]
    assert len(analytics.snap_by(snaps, m, "skill_rating", "time")) == 4
    div = analytics.snap_by(snaps, m, "division", "match", fill="ffill")
    assert list(div["value"]) == [5, 5, 4, 4]   # carried over, not averaged
