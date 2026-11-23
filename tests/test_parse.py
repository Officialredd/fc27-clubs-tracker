"""Run: python -m pytest tests   (or python tests/test_parse.py)"""
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
os.environ.setdefault("CLUBS_DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("CLUBS_CONFIG", os.path.join(os.environ["CLUBS_DATA_DIR"], "config.json"))

from clubs import db, parse  # noqa: E402

RAW = json.loads((HERE / "fixtures" / "real_match_dnf.json").read_text(encoding="utf-8"))


def test_real_match_dnf():
    m, players, clubs = parse.parse_match(RAW, "2315992", "common-gen5", "leagueMatch")
    assert m["match_id"] == "205714193780190"
    assert (m["goals_for"], m["goals_against"], m["result"]) == (3, 0, "W")
    assert m["they_quit"] == 1 and m["we_quit"] == 0
    assert m["opp_name"] == "baddie hedvig" and m["opp_id"] == "1881695"
    assert (m["our_players"], m["opp_players"]) == (2, 2)
    me = next(p for p in players if p["name"] == "ReDD_TVx")
    assert me["side"] == "us" and me["player_id"] == "865347485"
    assert (me["goals"], me["assists"], me["shots"], me["mom"], me["rating"]) == (2, 1, 3, 1, 10.0)
    assert me["pos"] == "forward" and me["archetype"] == "11" and me["device"] == "3"
    assert me["left_early"] == 0
    quitter = next(p for p in players if p["name"] == "Smutsmun")
    assert quitter["side"] == "opp" and quitter["left_early"] == 1   # userResult 6
    ours = next(c for c in clubs if c["club_id"] == "2315992")
    assert ours["name"] == "Dog Care FC" and ours["crest_url"].endswith("l99160224.png")


def test_opponent_view_of_same_match():
    m, _, _ = parse.parse_match(RAW, "1881695", "common-gen5")
    assert m["result"] == "L" and m["we_quit"] == 1 and m["match_type"] == "leagueMatch"


def test_store_once():
    m, players, clubs = parse.parse_match(RAW, "2315992", "common-gen5", "leagueMatch")
    assert db.save_match(m, players, clubs, RAW) is True
    assert db.save_match(m, players, clubs, RAW) is False
    df = db.players_df("2315992")
    assert len(df) == 4 and set(df["side"]) == {"us", "opp"}
    assert db.raw_match("205714193780190", "2315992")["matchId"] == "205714193780190"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
