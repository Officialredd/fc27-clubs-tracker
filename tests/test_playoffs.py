"""The playoff week: when it's open, the match limit and how it's going. Run: python -m pytest tests/test_playoffs.py"""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clubs import playoffs, seasons  # noqa: E402

CLOSE = dict(seasons.calendar_starts())[3]   # Season 2 ends (and its playoffs close) on Thursday 3 December
OPEN = CLOSE - pd.Timedelta(days=7)


def _m(rows):
    """rows: (when, match type, result, goals for, goals against)."""
    return pd.DataFrame([{"match_id": str(i), "when": w, "match_type": t, "result": r, "goals_for": gf,
                          "goals_against": ga} for i, (w, t, r, gf, ga) in enumerate(rows)])


def _snaps(rows, season_count=True):
    """rows: (when, division code, playoff games): this season's count in EA's raw data, or else an all-time count."""
    return pd.DataFrame([{"at": a, "division": d, "playoff_games": None if season_count else p,
                          "raw": json.dumps({"season": {"gamesPlayedPlayoff": p}}) if season_count else None}
                         for a, d, p in rows])


def test_the_playoffs_are_the_last_week_of_the_season():
    assert (OPEN, CLOSE) in playoffs.windows()
    assert OPEN.weekday() == 3 and CLOSE.weekday() == 3   # Thursday to Thursday


def test_shown_from_opening_until_a_few_days_after():
    m = _m([(OPEN - pd.Timedelta(days=1), "leagueMatch", "W", 3, 1)])
    s = _snaps([(OPEN - pd.Timedelta(hours=5), 4, 0)])
    assert playoffs.status(m, s, OPEN - pd.Timedelta(minutes=1)) is None
    st = playoffs.status(m, s, OPEN + pd.Timedelta(hours=2))
    assert st["open"] and st["division"] == "4" and st["limit"] == 15 and st["played"] == 0 and st["left"] == 15
    assert st["season"] == 2
    assert not playoffs.status(m, s, CLOSE + pd.Timedelta(days=2))["open"]
    assert playoffs.status(m, s, CLOSE + pd.Timedelta(days=5)) is None


def test_counts_playoff_matches_only_and_trusts_eas_count():
    h = pd.Timedelta(hours=1)
    m = _m([(OPEN + 10 * h, "playoffMatch", "W", 3, 0), (OPEN + 11 * h, "leagueMatch", "L", 0, 1),
            (OPEN + 12 * h, "playoffMatch", "D", 2, 2), (OPEN + 13 * h, "playoffMatch", "L", 1, 2)])
    s = _snaps([(OPEN - h, 5, 0), (OPEN + 14 * h, 5, 4)])   # EA counted 4: one wasn't tracked
    st = playoffs.status(m, s, OPEN + 15 * h)
    assert (st["w"], st["d"], st["l"], st["points"], st["gf"], st["ga"]) == (1, 1, 1, 4, 6, 4)
    assert st["tracked"] == 3 and st["played"] == 4 and st["limit"] == 10 and st["left"] == 6
    assert st["league_during"] == 1 and list(st["matches"]["match_id"]) == ["3", "2", "0"]


def test_a_playoff_week_the_calendar_doesnt_know_yet():
    later = pd.Timestamp("2027-03-13 21:30")   # a Saturday: that week's playoffs opened on Thursday the 11th
    m = _m([(later, "playoffMatch", "W", 2, 1)])
    st = playoffs.status(m, None, later + pd.Timedelta(hours=1))
    hour = seasons.calendar_starts()[-1][1]
    assert st["start"] == pd.Timestamp("2027-03-11") + pd.Timedelta(hours=hour.hour, minutes=hour.minute)
    assert st["end"] - st["start"] == pd.Timedelta(days=7) and st["season"] is None and st["limit"] is None


def test_eas_all_time_count_is_used_as_the_rise_over_the_week():
    h = pd.Timedelta(hours=1)
    m = _m([(OPEN + 10 * h, "playoffMatch", "W", 3, 0)])
    s = _snaps([(OPEN - h, 4, 12), (OPEN + 20 * h, 4, 14)], season_count=False)   # 12 from earlier seasons
    st = playoffs.status(m, s, OPEN + 21 * h)
    assert st["played"] == 2 and st["tracked"] == 1 and st["left"] == 13
