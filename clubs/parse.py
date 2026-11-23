"""Turn EA's match JSON into rows for the database.

A match from clubs/matches looks like this (numbers arrive as text):
  {"matchId": "2057...", "timestamp": 1795219020,
   "clubs":   {"<clubId>": {"goals", "goalsAgainst", "wins", "ties", "losses", "result", "winnerByDnf",
                            "matchType", "TEAM", "details": {"name", "teamId", "customKit": {...}}}, ...},
   "players": {"<clubId>": {"<playerId>": {"playername", "pos", "archetypeid", "rating", "goals", "assists",
                            "shots", "passesmade", "passattempts", "tacklesmade", "tackleattempts", "saves",
                            "mom", "redcards", "secondsPlayed", "namespace", "userResult", ...}}},
   "aggregate": {"<clubId>": {...the club's totals...}}}

Only human players are listed: AI teammates aren't in the data.
"result" is a bit field: 1 win, 2 loss, 4 draw, +8 when that club didn't finish (quit or disconnected),
+16384 when it won because the other club quit ("winnerByDnf": "1").
"""
import json

from . import labels

INT_FIELDS = {   # our column: EA's field
    "goals": "goals", "assists": "assists", "shots": "shots",
    "passes_made": "passesmade", "pass_attempts": "passattempts",
    "tackles_made": "tacklesmade", "tackle_attempts": "tackleattempts",
    "saves": "saves", "mom": "mom", "red_cards": "redcards", "goals_conceded": "goalsconceded",
    "clean_sheet": "cleansheetsany", "clean_sheet_def": "cleansheetsdef", "clean_sheet_gk": "cleansheetsgk",
    "seconds": "secondsPlayed", "game_time": "gameTime", "rt_game": "realtimegame", "rt_idle": "realtimeidle",
    "user_result": "userResult", "score_field": "SCORE",
    "dive_saves": "ballDiveSaves", "cross_saves": "crossSaves", "good_dir_saves": "goodDirectionSaves",
    "parry_saves": "parrySaves", "punch_saves": "punchSaves", "reflex_saves": "reflexSaves",
}
PLAYER_COLUMNS = (["match_id", "club_id", "side", "player_id", "name", "pos", "archetype", "device", "rating"]
                  + list(INT_FIELDS) + ["left_early", "events"])


def _i(v):
    try:
        if v is None or v == "":
            return None
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _f(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _s(v):
    return None if v is None else str(v)


def club_row(club_id, details: dict | None, platform: str) -> dict:
    d = details or {}
    kit = d.get("customKit") or {}
    return {
        "club_id": str(club_id), "platform": platform, "name": d.get("name"),
        "team_id": _s(d.get("teamId")), "region_id": _s(d.get("regionId")),
        "crest_asset_id": _s(kit.get("crestAssetId")), "selected_kit_type": _s(kit.get("selectedKitType")),
        "stadium": kit.get("stadName"),
        "crest_url": labels.crest_url(d.get("teamId"), kit.get("selectedKitType"), kit.get("crestAssetId")),
    }


def result_letter(c: dict) -> str | None:
    if _i(c.get("wins")):
        return "W"
    if _i(c.get("losses")):
        return "L"
    if _i(c.get("ties")):
        return "D"
    gf, ga = _i(c.get("goals")), _i(c.get("goalsAgainst"))
    if gf is None or ga is None:
        return None
    return "W" if gf > ga else "L" if gf < ga else "D"


def parse_match(raw: dict, club_id, platform: str, match_type: str | None = None):
    """(match row, player rows for both clubs, club rows) for one match of club_id."""
    cid = str(club_id)
    clubs = raw.get("clubs") or {}
    if cid not in clubs:
        raise ValueError(f"match {raw.get('matchId')} doesn't include club {cid}")
    us = clubs[cid]
    opp_id = next((k for k in clubs if k != cid), None)
    opp = clubs.get(opp_id) or {}
    players = raw.get("players") or {}
    mtype = match_type or labels.MATCH_TYPE_CODES.get(str(us.get("matchType")), "leagueMatch")
    code, opp_code = _i(us.get("result")) or 0, _i(opp.get("result")) or 0

    rows = []
    for side_id, side in ((cid, "us"), (opp_id, "opp")):
        if side_id is None:
            continue
        for pid, p in (players.get(side_id) or {}).items():
            row = {"match_id": str(raw["matchId"]), "club_id": str(side_id), "side": side, "player_id": str(pid),
                   "name": p.get("playername") or str(pid), "pos": p.get("pos"),
                   "archetype": _s(p.get("archetypeid")), "device": _s(p.get("namespace")),
                   "rating": _f(p.get("rating"))}
            for col, key in INT_FIELDS.items():
                row[col] = _i(p.get(key))
            ev = {k[-1]: p[k] for k in ("match_event_aggregate_0", "match_event_aggregate_1",
                                         "match_event_aggregate_2", "match_event_aggregate_3") if p.get(k)}
            row["events"] = json.dumps(ev) if ev else None
            rows.append(row)
    # left early: EA flags it in userResult, or the player was on the pitch for far less than the match
    full = max([r["seconds"] or 0 for r in rows] or [0])
    for r in rows:
        r["left_early"] = int(bool(r["user_result"]) or (full > 0 and (r["seconds"] or 0) < 0.75 * full))

    ts = _i(raw.get("timestamp")) or _i(us.get("date"))
    match = {
        "match_id": str(raw["matchId"]), "club_id": cid, "platform": platform, "match_type": mtype,
        "ts": ts, "opp_id": opp_id, "opp_name": (opp.get("details") or {}).get("name"),
        "goals_for": _i(us.get("goals")), "goals_against": _i(us.get("goalsAgainst")),
        "result": result_letter(us), "result_code": code, "opp_result_code": opp_code,
        "we_quit": int(bool(code & 8)), "they_quit": int(bool(opp_code & 8) or _i(us.get("winnerByDnf")) == 1),
        "our_players": sum(1 for r in rows if r["side"] == "us"),
        "opp_players": sum(1 for r in rows if r["side"] == "opp"),
        "match_seconds": full or None, "season_id": _s(us.get("season_id")),
    }
    club_rows = [club_row(k, (v or {}).get("details"), platform) for k, v in clubs.items()]
    return match, rows, club_rows
