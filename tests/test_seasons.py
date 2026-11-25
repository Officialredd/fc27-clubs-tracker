"""Clubs league seasons, found from EA's season record in the club snapshots. Run: python -m pytest tests/test_seasons.py"""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clubs import seasons  # noqa: E402

T0 = pd.Timestamp("2026-11-21 20:00")


def _matches(n, start=T0, types=None):
    """n matches, one every 15 minutes."""
    when = [start + pd.Timedelta(minutes=15 * i) for i in range(n)]
    res = ["W", "D", "L"] * (n // 3 + 1)
    return pd.DataFrame({"match_id": [str(100 + i) for i in range(n)], "when": when,
                         "match_type": types or ["leagueMatch"] * n, "result": res[:n],
                         "goals_for": [2] * n, "goals_against": [1] * n})


def _snaps(rows):
    """rows: (minutes after T0, season games, seasons finished or None)."""
    return pd.DataFrame([{"at": T0 + pd.Timedelta(minutes=t), "season_games": g, "skill_rating": 1500 + t,
                          "division": 5, "season_points": g, "promotions": 0,
                          "raw": json.dumps({"overall": {"finishesInDivision5Group1": f}}) if f is not None else None}
                         for t, g, f in rows])


def test_one_season():
    m = _matches(6)
    s = _snaps([(10, 1, 0), (50, 4, 0), (90, 6, 0)])
    assert seasons.starts(s, m) == []
    assert set(seasons.number(m, [])) == {1}


def test_new_season_when_the_record_starts_again():
    m = _matches(8)   # matches at 0, 15, ... 105 minutes
    s = _snaps([(20, 2, 0), (80, 6, 0), (110, 2, 1)])   # after 105: the last 2 matches are the new season's
    starts = seasons.starts(s, m)
    assert starts == [m["when"].iloc[6]]
    assert list(seasons.number(m, starts)) == [1] * 6 + [2] * 2
    tab = seasons.table(m, s, starts)
    assert list(tab["season"]) == [2, 1] and list(tab["matches"]) == [2, 6]
    assert tab.loc[0, "label"] == "Season 2 (this season)" and tab.loc[1, "label"].startswith("Season 1 (21 Nov")
    assert tab.loc[1, "sr_end"] == 1580 and tab.loc[0, "sr_end"] == 1610


def test_new_season_seen_late_only_by_the_finished_count():
    m = _matches(14)   # the PC was off: the new season already has more games than the old one had
    s = _snaps([(20, 2, 0), (60, 5, 0), (200, 7, 1)])
    starts = seasons.starts(s, m)
    assert starts == [m["when"].iloc[-7]]   # 9 matches since the last check, 7 games in the new season: the last 7


def test_an_odd_answer_from_ea_is_not_a_new_season():
    m = _matches(6)
    s = _snaps([(20, 2, None), (40, 0, None), (60, 4, None)])
    assert seasons.starts(s, m) == []


def test_friendlies_dont_count_as_season_games():
    m = _matches(5, types=["leagueMatch", "leagueMatch", "leagueMatch", "friendlyMatch", "leagueMatch"])
    s = _snaps([(40, 3, 0), (70, 1, 1)])   # the new season has 1 league game: match 5 (match 4 was a friendly)
    starts = seasons.starts(s, m)
    assert starts == [m["when"].iloc[4]]


# ---------------------------------------------------------------- EA's calendar
def _around(t, n=6, step=pd.Timedelta(hours=10)):
    """n league matches, half of them before t and half after."""
    return _matches(n, start=t - step * (n // 2) + pd.Timedelta(minutes=30), types=None).assign(
        when=[t - step * (n // 2) + pd.Timedelta(minutes=30) + step * i for i in range(n)])


def test_the_calendar_names_the_season():
    m = _matches(6)   # 21 November: Season 2 (22 October to 3 December)
    assert seasons.breaks(_snaps([(10, 1, 0), (90, 6, 0)]), m) == ([], 2)


def test_a_calendar_date_splits_when_the_collector_saw_nothing_around_it():
    t3 = dict(seasons.calendar_starts())[3]
    m = _around(t3)
    brk, first = seasons.breaks(pd.DataFrame(), m)
    assert first == 2 and brk == [t3]
    assert list(seasons.number(m, brk, first)) == [2, 2, 2, 3, 3, 3]
    assert list(seasons.table(m, None, brk, first)["label"]) == ["Season 3 (this season)",
                                                                   f"Season 2 ({m['when'].iloc[0]:%d %b} - {m['when'].iloc[2]:%d %b})"]


def test_eas_record_beats_the_calendar():
    t3 = dict(seasons.calendar_starts())[3]
    m = _around(t3)
    s = pd.DataFrame([{"at": t3 - pd.Timedelta(hours=1), "season_games": 30, "skill_rating": 1600, "raw": None},
                      {"at": t3 + pd.Timedelta(days=1), "season_games": 33, "skill_rating": 1610, "raw": None}])
    assert seasons.breaks(s, m) == ([], 2)   # the record carried on past the date: still the same season


def test_a_change_seen_near_the_calendar_date_is_used_once():
    t3 = dict(seasons.calendar_starts())[3]
    m = _around(t3)   # matches at t3 - 29.5 h, -19.5 h, -9.5 h, +0.5 h, +10.5 h, +20.5 h
    s = pd.DataFrame([{"at": t3 - pd.Timedelta(hours=2), "season_games": 30, "skill_rating": 1600, "raw": None},
                      {"at": t3 + pd.Timedelta(hours=21), "season_games": 2, "skill_rating": 1590, "raw": None}])
    brk, first = seasons.breaks(s, m)
    assert brk == [m["when"].iloc[4]] and first == 2   # EA says 2 games in the new season: the last two
