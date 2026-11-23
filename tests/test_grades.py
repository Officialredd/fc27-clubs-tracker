"""Grades: the formulas give the raw scores worked out during calibration, and letters follow the scale.
Run: python tests/test_grades.py"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("CLUBS_DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("CLUBS_CONFIG", os.path.join(os.environ["CLUBS_DATA_DIR"], "config.json"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from clubs import grades  # noqa: E402

PLAYERS = [  # pos, rating, goals, assists, shots, passes made/tried, tackles won/tried, saves, gf, ga, result, mom, red -> raw
    ("forward", 7.4, 1, 0, 1, 12, 14, 1, 14, 0, 1, 1, "D", 0, 0, 48.4883),
    ("defender", 7.1, 0, 0, 0, 5, 9, 3, 13, 0, 2, 5, "L", 0, 0, 26.3839),
    ("forward", 7.9, 1, 1, 2, 12, 13, 3, 9, 0, 4, 2, "W", 0, 0, 67.0115),
    ("midfielder", 8.3, 2, 0, 3, 14, 18, 3, 4, 0, 3, 3, "D", 0, 0, 57.4945),
    ("midfielder", 7.1, 0, 0, 0, 15, 15, 1, 5, 0, 0, 1, "L", 0, 0, 37.8788),
]
TEAMS = [  # gf, ga, result, shots, passes made/tried, avg rating, humans, their humans -> raw
    (1, 2, "L", 6, 129, 156, 6.75, 4, 3, 32.6593),
    (4, 2, "W", 13, 123, 150, 7.825, 8, 5, 62.0636),
]


def test_player_formula():
    cols = ["pos", "rating", "goals", "assists", "shots", "passes_made", "pass_attempts", "tackles_made",
            "tackle_attempts", "saves", "gf", "ga", "res", "mom", "red_cards", "expected"]
    df = pd.DataFrame(PLAYERS, columns=cols)
    got = grades.player_parts(df)["raw"].to_numpy()
    assert np.abs(got - df["expected"].to_numpy()).max() < 1e-3


def test_team_formula():
    cols = ["gf", "ga", "res", "shots", "passes_made", "pass_attempts", "rating", "humans", "opp_humans", "expected"]
    df = pd.DataFrame(TEAMS, columns=cols)
    got = grades.team_parts(df)["raw"].to_numpy()
    assert np.abs(got - df["expected"].to_numpy()).max() < 1e-3


def test_letters_and_overall():
    assert [grades.letter(v) for v in (99, 90, 89.9, 75, 60, 50, 40, 30, 25, 15, 10, 5, 0)] == \
        ["S", "S", "A", "A", "B", "C", "C", "D", "D", "E", "E", "F", "F"]
    assert grades.overall(pd.Series([90, 90, 90])) == (270 + 4 * 50) / 7
    assert 0 <= grades.to_score([30.0], "forward")[0] <= 100


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
