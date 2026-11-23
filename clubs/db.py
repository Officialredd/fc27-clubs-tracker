"""SQLite storage: matches, every player's line in every match (both clubs), the raw EA data and
snapshots of the club's record and members over time (EA only shows the current numbers)."""
import datetime as dt
import json
import sqlite3
import zlib
from contextlib import contextmanager

import pandas as pd

from . import config
from .parse import PLAYER_COLUMNS

DB_PATH = config.DATA_DIR / "clubs.db"

MATCH_COLUMNS = ["match_id", "club_id", "platform", "match_type", "ts", "opp_id", "opp_name", "goals_for",
                 "goals_against", "result", "result_code", "opp_result_code", "we_quit", "they_quit", "our_players",
                 "opp_players", "match_seconds", "season_id"]
CLUB_COLUMNS = ["club_id", "platform", "name", "team_id", "region_id", "crest_asset_id", "selected_kit_type",
                "stadium", "crest_url"]

_INT_COLS = [c for c in PLAYER_COLUMNS if c not in ("match_id", "club_id", "side", "player_id", "name", "pos",
                                                    "archetype", "device", "rating", "events")]
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS clubs (
    club_id TEXT NOT NULL, platform TEXT NOT NULL, name TEXT, team_id TEXT, region_id TEXT,
    crest_asset_id TEXT, selected_kit_type TEXT, stadium TEXT, crest_url TEXT, updated_at TEXT,
    PRIMARY KEY (club_id, platform));
CREATE TABLE IF NOT EXISTS matches (
    match_id TEXT NOT NULL, club_id TEXT NOT NULL, platform TEXT, match_type TEXT, ts INTEGER,
    opp_id TEXT, opp_name TEXT, goals_for INTEGER, goals_against INTEGER, result TEXT,
    result_code INTEGER, opp_result_code INTEGER, we_quit INTEGER, they_quit INTEGER,
    our_players INTEGER, opp_players INTEGER, match_seconds INTEGER, season_id TEXT,
    fetched_at TEXT, notes TEXT,
    PRIMARY KEY (match_id, club_id));
CREATE INDEX IF NOT EXISTS matches_club_ts ON matches (club_id, ts);
CREATE TABLE IF NOT EXISTS player_matches (
    match_id TEXT NOT NULL, club_id TEXT NOT NULL, side TEXT, player_id TEXT NOT NULL, name TEXT, pos TEXT,
    archetype TEXT, device TEXT, rating REAL, {", ".join(c + " INTEGER" for c in _INT_COLS)}, events TEXT,
    PRIMARY KEY (match_id, club_id, player_id));
CREATE INDEX IF NOT EXISTS pm_player ON player_matches (player_id);
CREATE TABLE IF NOT EXISTS raw_matches (
    match_id TEXT NOT NULL, club_id TEXT NOT NULL, fetched_at TEXT, data BLOB,
    PRIMARY KEY (match_id, club_id));
CREATE TABLE IF NOT EXISTS club_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT, club_id TEXT NOT NULL, at TEXT NOT NULL,
    skill_rating INTEGER, division TEXT, best_division TEXT, reputation TEXT,
    season_points INTEGER, season_games INTEGER, season_wins INTEGER, season_ties INTEGER, season_losses INTEGER,
    season_goals INTEGER, season_goals_against INTEGER, season_clean_sheets INTEGER,
    games INTEGER, wins INTEGER, ties INTEGER, losses INTEGER, goals INTEGER, goals_against INTEGER,
    playoff_games INTEGER, promotions INTEGER, relegations INTEGER, win_streak INTEGER, unbeaten_streak INTEGER,
    raw TEXT);
CREATE TABLE IF NOT EXISTS member_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT, club_id TEXT NOT NULL, at TEXT NOT NULL, name TEXT NOT NULL,
    pro_name TEXT, games INTEGER, win_rate INTEGER, goals INTEGER, assists INTEGER, rating_avg REAL, mom INTEGER,
    overall INTEGER, pro_pos TEXT, favorite_position TEXT, pass_rate INTEGER, shot_rate INTEGER,
    tackle_rate INTEGER, passes INTEGER, tackles INTEGER, clean_sheets_def INTEGER, clean_sheets_gk INTEGER,
    red_cards INTEGER, height INTEGER, nationality TEXT, raw TEXT);
CREATE INDEX IF NOT EXISTS ms_club_name ON member_snapshots (club_id, name, at);
CREATE TABLE IF NOT EXISTS scout_fetches (
    club_id TEXT NOT NULL, for_match TEXT NOT NULL, at TEXT, ok INTEGER, tries INTEGER, n INTEGER, error TEXT,
    sr INTEGER, our_sr INTEGER, games INTEGER, wins INTEGER, ties INTEGER, losses INTEGER, goals INTEGER,
    goals_against INTEGER, best_division TEXT, reputation TEXT, win_streak INTEGER, unbeaten_streak INTEGER,
    PRIMARY KEY (club_id, for_match));
CREATE TABLE IF NOT EXISTS scout_matches (
    match_id TEXT NOT NULL, club_id TEXT NOT NULL, platform TEXT, match_type TEXT, ts INTEGER,
    opp_id TEXT, opp_name TEXT, goals_for INTEGER, goals_against INTEGER, result TEXT,
    result_code INTEGER, opp_result_code INTEGER, we_quit INTEGER, they_quit INTEGER,
    our_players INTEGER, opp_players INTEGER, match_seconds INTEGER, season_id TEXT, fetched_at TEXT,
    PRIMARY KEY (match_id, club_id));
CREATE INDEX IF NOT EXISTS scout_matches_club_ts ON scout_matches (club_id, ts);
CREATE TABLE IF NOT EXISTS scout_players (
    match_id TEXT NOT NULL, club_id TEXT NOT NULL, side TEXT, player_id TEXT NOT NULL, name TEXT, pos TEXT,
    archetype TEXT, device TEXT, rating REAL, {", ".join(c + " INTEGER" for c in _INT_COLS)}, events TEXT,
    PRIMARY KEY (match_id, club_id, player_id));
CREATE TABLE IF NOT EXISTS scout_raw (match_id TEXT NOT NULL PRIMARY KEY, data BLOB);
CREATE TABLE IF NOT EXISTS online_sent (path TEXT PRIMARY KEY, digest TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS online_got (path TEXT PRIMARY KEY, stamp TEXT, at TEXT);
"""


@contextmanager
def connect():
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 15000")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db():
    with connect() as con:
        con.execute("PRAGMA journal_mode=WAL")   # the app reads while the collector writes
        con.executescript(SCHEMA)


def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------- writing
def known_match_ids(club_id) -> set[str]:
    with connect() as con:
        return {r[0] for r in con.execute("SELECT match_id FROM matches WHERE club_id=?", (str(club_id),))}


def save_match(match: dict, players: list[dict], clubs: list[dict], raw: dict) -> bool:
    """Store a match (once). True if it was new."""
    with connect() as con:
        cur = con.execute(
            f"INSERT OR IGNORE INTO matches ({', '.join(MATCH_COLUMNS)}, fetched_at) "
            f"VALUES ({', '.join('?' * len(MATCH_COLUMNS))}, ?)",
            [match.get(c) for c in MATCH_COLUMNS] + [now_iso()])
        if cur.rowcount == 0:
            return False
        con.executemany(
            f"INSERT OR REPLACE INTO player_matches ({', '.join(PLAYER_COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(PLAYER_COLUMNS))})",
            [[p.get(c) for c in PLAYER_COLUMNS] for p in players])
        con.execute("INSERT OR REPLACE INTO raw_matches VALUES (?, ?, ?, ?)",
                    (match["match_id"], match["club_id"], now_iso(),
                     zlib.compress(json.dumps(raw, separators=(",", ":")).encode("utf-8"), 6)))
        for c in clubs:
            if c.get("name"):
                con.execute(f"INSERT OR REPLACE INTO clubs ({', '.join(CLUB_COLUMNS)}, updated_at) "
                            f"VALUES ({', '.join('?' * len(CLUB_COLUMNS))}, ?)",
                            [c.get(k) for k in CLUB_COLUMNS] + [now_iso()])
        return True


def save_club(c: dict):
    with connect() as con:
        con.execute(f"INSERT OR REPLACE INTO clubs ({', '.join(CLUB_COLUMNS)}, updated_at) "
                    f"VALUES ({', '.join('?' * len(CLUB_COLUMNS))}, ?)",
                    [c.get(k) for k in CLUB_COLUMNS] + [now_iso()])


def _i(v):
    try:
        return None if v in (None, "", "-1") else int(float(v))
    except (TypeError, ValueError):
        return None


def save_club_snapshot(club_id, overall: dict | None, season: dict | None) -> bool:
    """Store the club's numbers if anything changed since the last snapshot."""
    o, s = overall or {}, season or {}
    if not o and not s:
        return False
    row = {
        "skill_rating": _i(o.get("skillRating")), "division": s.get("currentDivision"),
        "best_division": s.get("bestDivision") or o.get("bestDivision"),
        "reputation": s.get("reputationtier") or o.get("reputationtier"),
        "season_points": _i(s.get("points")), "season_games": _i(s.get("gamesPlayed")),
        "season_wins": _i(s.get("wins")), "season_ties": _i(s.get("ties")), "season_losses": _i(s.get("losses")),
        "season_goals": _i(s.get("goals")), "season_goals_against": _i(s.get("goalsAgainst")),
        "season_clean_sheets": _i(s.get("cleanSheets")),
        "games": _i(o.get("gamesPlayed")), "wins": _i(o.get("wins")), "ties": _i(o.get("ties")),
        "losses": _i(o.get("losses")), "goals": _i(o.get("goals")), "goals_against": _i(o.get("goalsAgainst")),
        "playoff_games": _i(o.get("gamesPlayedPlayoff")), "promotions": _i(o.get("promotions")),
        "relegations": _i(o.get("relegations")), "win_streak": _i(o.get("wstreak")),
        "unbeaten_streak": _i(o.get("unbeatenstreak")),
    }
    with connect() as con:
        last = con.execute("SELECT * FROM club_snapshots WHERE club_id=? ORDER BY id DESC LIMIT 1",
                           (str(club_id),)).fetchone()
        if last and all(str(last[k]) == str(v) for k, v in row.items() if v is not None):
            return False
        cols = list(row)
        con.execute(f"INSERT INTO club_snapshots (club_id, at, {', '.join(cols)}, raw) "
                    f"VALUES (?, ?, {', '.join('?' * len(cols))}, ?)",
                    [str(club_id), now_iso()] + [row[c] for c in cols]
                    + [json.dumps({"overall": o, "season": {k: v for k, v in s.items() if k != "clubInfo"}})])
        return True


MEMBER_MAP = {"pro_name": "proName", "games": "gamesPlayed", "win_rate": "winRate", "goals": "goals",
              "assists": "assists", "rating_avg": "ratingAve", "mom": "manOfTheMatch", "overall": "proOverall",
              "pro_pos": "proPos", "favorite_position": "favoritePosition", "pass_rate": "passSuccessRate",
              "shot_rate": "shotSuccessRate", "tackle_rate": "tackleSuccessRate", "passes": "passesMade",
              "tackles": "tacklesMade", "clean_sheets_def": "cleanSheetsDef", "clean_sheets_gk": "cleanSheetsGK",
              "red_cards": "redCards", "height": "proHeight", "nationality": "proNationality"}


def save_member_snapshots(club_id, members: list[dict]) -> int:
    """One row per member whose season numbers changed since their last snapshot."""
    n = 0
    with connect() as con:
        for m in members or []:
            name = m.get("name")
            if not name:
                continue
            row = {k: m.get(v) for k, v in MEMBER_MAP.items()}
            for k in ("games", "win_rate", "goals", "assists", "mom", "overall", "pass_rate", "shot_rate",
                      "tackle_rate", "passes", "tackles", "clean_sheets_def", "clean_sheets_gk", "red_cards", "height"):
                row[k] = _i(row[k])
            try:
                row["rating_avg"] = float(row["rating_avg"]) if row["rating_avg"] not in (None, "") else None
            except ValueError:
                row["rating_avg"] = None
            last = con.execute("SELECT * FROM member_snapshots WHERE club_id=? AND name=? ORDER BY id DESC LIMIT 1",
                               (str(club_id), name)).fetchone()
            if last and all(str(last[k]) == str(v) for k, v in row.items()):
                continue
            cols = list(row)
            con.execute(f"INSERT INTO member_snapshots (club_id, at, name, {', '.join(cols)}, raw) "
                        f"VALUES (?, ?, ?, {', '.join('?' * len(cols))}, ?)",
                        [str(club_id), now_iso(), name] + [row[c] for c in cols] + [json.dumps(m)])
            n += 1
    return n


# ---------------------------------------------------------------- opponent scouting
FETCH_COLUMNS = ["sr", "our_sr", "games", "wins", "ties", "losses", "goals", "goals_against", "best_division",
                 "reputation", "win_streak", "unbeaten_streak"]


def save_scouting(club_id, for_match, parsed: list, stats: dict | None, our_sr=None, error: str | None = None) -> int:
    """Store a scouted club's games (from its own point of view, both clubs' player lines) and EA's numbers
    for it, for one of our matches (for_match None: a club looked up by hand, only the games are kept).
    parsed: [(match row, player rows, club rows, raw), ...]. Returns how many games were new."""
    o = stats or {}
    new = 0
    with connect() as con:
        for match, players, clubs, raw in parsed:
            cur = con.execute(
                f"INSERT OR IGNORE INTO scout_matches ({', '.join(MATCH_COLUMNS)}, fetched_at) "
                f"VALUES ({', '.join('?' * len(MATCH_COLUMNS))}, ?)",
                [match.get(c) for c in MATCH_COLUMNS] + [now_iso()])
            new += cur.rowcount
            con.executemany(
                f"INSERT OR REPLACE INTO scout_players ({', '.join(PLAYER_COLUMNS)}) "
                f"VALUES ({', '.join('?' * len(PLAYER_COLUMNS))})",
                [[p.get(c) for c in PLAYER_COLUMNS] for p in players])
            con.execute("INSERT OR IGNORE INTO scout_raw VALUES (?, ?)",
                        (match["match_id"], zlib.compress(json.dumps(raw, separators=(",", ":")).encode("utf-8"), 6)))
            for c in clubs:
                if c.get("name"):
                    con.execute(f"INSERT OR REPLACE INTO clubs ({', '.join(CLUB_COLUMNS)}, updated_at) "
                                f"VALUES ({', '.join('?' * len(CLUB_COLUMNS))}, ?)",
                                [c.get(k) for k in CLUB_COLUMNS] + [now_iso()])
        row = {"sr": _i(o.get("skillRating")), "our_sr": _i(our_sr), "games": _i(o.get("gamesPlayed")),
               "wins": _i(o.get("wins")), "ties": _i(o.get("ties")), "losses": _i(o.get("losses")),
               "goals": _i(o.get("goals")), "goals_against": _i(o.get("goalsAgainst")),
               "best_division": o.get("bestDivision"), "reputation": o.get("reputationtier"),
               "win_streak": _i(o.get("wstreak")), "unbeaten_streak": _i(o.get("unbeatenstreak"))}
        if for_match is None:   # looked up by hand (Search page): just keep the games
            return new
        prev = con.execute("SELECT tries FROM scout_fetches WHERE club_id=? AND for_match=?",
                           (str(club_id), str(for_match))).fetchone()
        con.execute(f"INSERT OR REPLACE INTO scout_fetches (club_id, for_match, at, ok, tries, n, error, "
                    f"{', '.join(FETCH_COLUMNS)}) VALUES (?, ?, ?, 1, ?, ?, ?, {', '.join('?' * len(FETCH_COLUMNS))})",
                    [str(club_id), str(for_match), now_iso(), (prev[0] or 0) + 1 if prev else 1, len(parsed), error]
                    + [row[c] for c in FETCH_COLUMNS])
    return new


def scout_failed(club_id, for_match, error: str):
    """Remember a scouting attempt that got nothing (it's tried again a few times)."""
    with connect() as con:
        prev = con.execute("SELECT tries, ok FROM scout_fetches WHERE club_id=? AND for_match=?",
                           (str(club_id), str(for_match))).fetchone()
        if prev and prev["ok"]:
            return   # an earlier attempt worked: keep it
        con.execute("INSERT OR REPLACE INTO scout_fetches (club_id, for_match, at, ok, tries, n, error) "
                    "VALUES (?, ?, ?, 0, ?, 0, ?)",
                    (str(club_id), str(for_match), now_iso(), ((prev["tries"] or 0) + 1) if prev else 1, error[:400]))


def latest_scout(club_id) -> dict | None:
    """The newest successful scouting of a club (for any of our matches)."""
    with connect() as con:
        r = con.execute("SELECT * FROM scout_fetches WHERE club_id=? AND ok=1 ORDER BY at DESC LIMIT 1",
                        (str(club_id),)).fetchone()
    return dict(r) if r else None


def copy_scout(club_id, for_match, src: dict):
    """Use an earlier scouting of the same club for another (older) match of ours: no need to ask EA again."""
    cols = ["at", "ok", "tries", "n", "error"] + FETCH_COLUMNS
    with connect() as con:
        con.execute(f"INSERT OR REPLACE INTO scout_fetches (club_id, for_match, {', '.join(cols)}) "
                    f"VALUES (?, ?, {', '.join('?' * len(cols))})",
                    [str(club_id), str(for_match)] + [src.get(c) for c in cols])


def scout_pending(club_id, limit: int = 5, max_tries: int = 3) -> list[dict]:
    """Our matches whose opponent hasn't been scouted yet (or failed fewer than max_tries times), newest first."""
    with connect() as con:
        rows = con.execute(
            "SELECT m.match_id, m.opp_id, m.opp_name, m.match_type, m.ts FROM matches m "
            "LEFT JOIN scout_fetches f ON f.club_id = m.opp_id AND f.for_match = m.match_id "
            "WHERE m.club_id = ? AND m.opp_id IS NOT NULL AND (f.for_match IS NULL OR "
            "(COALESCE(f.ok, 0) = 0 AND COALESCE(f.tries, 0) < ?)) ORDER BY m.ts DESC LIMIT ?",
            (str(club_id), int(max_tries), int(limit))).fetchall()
    return [dict(r) for r in rows]


def delete_match(match_id, club_id):
    with connect() as con:
        con.execute("DELETE FROM matches WHERE match_id=? AND club_id=?", (str(match_id), str(club_id)))
        con.execute("DELETE FROM raw_matches WHERE match_id=? AND club_id=?", (str(match_id), str(club_id)))
        con.execute("DELETE FROM player_matches WHERE match_id=?", (str(match_id),))


def set_note(match_id, club_id, note: str):
    with connect() as con:
        con.execute("UPDATE matches SET notes=? WHERE match_id=? AND club_id=?", (note or None, str(match_id), str(club_id)))


# ---------------------------------------------------------------- reading
def _local(ts: pd.Series) -> pd.Series:
    """Unix seconds -> local wall-clock time of this PC (summer time included)."""
    return pd.Series([dt.datetime.fromtimestamp(int(t)) if pd.notna(t) else pd.NaT for t in ts],
                     index=ts.index, dtype="datetime64[ns]")


def matches_df(club_id) -> pd.DataFrame:
    """Every stored match of the club, oldest first, with game_no 1..n and local time columns."""
    with connect() as con:
        m = pd.read_sql_query("SELECT * FROM matches WHERE club_id=? ORDER BY ts, match_id", con,
                              params=(str(club_id),))
    if m.empty:
        return m
    m["when"] = _local(m["ts"])
    m["date"] = m["when"].dt.date
    m["game_no"] = range(1, len(m) + 1)
    m["gd"] = m["goals_for"] - m["goals_against"]
    m["points"] = m["result"].map({"W": 3, "D": 1, "L": 0})
    return m


def players_df(club_id) -> pd.DataFrame:
    """Every player line (both clubs) in the club's stored matches, with the match info joined in."""
    with connect() as con:
        p = pd.read_sql_query(
            "SELECT p.*, m.ts, m.match_type, m.result, m.goals_for, m.goals_against, m.opp_id, m.opp_name, "
            "m.our_players, m.opp_players, m.we_quit, m.they_quit, m.match_seconds "
            "FROM player_matches p JOIN matches m ON m.match_id = p.match_id AND m.club_id = ? "
            "ORDER BY m.ts", con, params=(str(club_id),))
    if p.empty:
        return p
    p["when"] = _local(p["ts"])
    return p


def clubs_df() -> pd.DataFrame:
    with connect() as con:
        return pd.read_sql_query("SELECT * FROM clubs", con)


def club(club_id) -> dict:
    with connect() as con:
        r = con.execute("SELECT * FROM clubs WHERE club_id=? ORDER BY updated_at DESC LIMIT 1",
                        (str(club_id),)).fetchone()
    return dict(r) if r else {}


def club_snapshots_df(club_id) -> pd.DataFrame:
    with connect() as con:
        s = pd.read_sql_query("SELECT * FROM club_snapshots WHERE club_id=? ORDER BY id", con, params=(str(club_id),))
    if not s.empty:
        s["at"] = pd.to_datetime(s["at"])
    return s


def latest_club_snapshot(club_id) -> dict:
    with connect() as con:
        r = con.execute("SELECT * FROM club_snapshots WHERE club_id=? ORDER BY id DESC LIMIT 1",
                        (str(club_id),)).fetchone()
    return dict(r) if r else {}


def members_latest(club_id) -> pd.DataFrame:
    with connect() as con:
        df = pd.read_sql_query(
            "SELECT s.* FROM member_snapshots s JOIN (SELECT name, MAX(id) AS id FROM member_snapshots "
            "WHERE club_id=? GROUP BY name) l ON l.id = s.id ORDER BY s.games DESC", con, params=(str(club_id),))
    if not df.empty:
        df["at"] = pd.to_datetime(df["at"])
    return df


def member_history(club_id, name: str | None = None) -> pd.DataFrame:
    q, args = "SELECT * FROM member_snapshots WHERE club_id=?", [str(club_id)]
    if name:
        q, args = q + " AND name=?", args + [name]
    with connect() as con:
        df = pd.read_sql_query(q + " ORDER BY id", con, params=args)
    if not df.empty:
        df["at"] = pd.to_datetime(df["at"])
    return df


def raw_match(match_id, club_id) -> dict | None:
    with connect() as con:
        r = con.execute("SELECT data FROM raw_matches WHERE match_id=? AND club_id=?",
                        (str(match_id), str(club_id))).fetchone()
    return json.loads(zlib.decompress(r[0]).decode("utf-8")) if r else None


# ---------------------------------------------------------------- online copy (clubs/cloud.py)
ROW_TABLES = {"scout_fetches", "club_snapshots", "member_snapshots"}


def put_rows(table: str, rows: list[dict]):
    """Store rows exactly as they came (from the online copy's files)."""
    if table not in ROW_TABLES or not rows:
        return
    with connect() as con:
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
        use = [c for c in cols if c in rows[0]]
        con.executemany(f"INSERT OR REPLACE INTO {table} ({', '.join(use)}) VALUES ({', '.join('?' * len(use))})",
                        [[r.get(c) for c in use] for r in rows])


def online_sent() -> dict:
    with connect() as con:
        return {r[0]: r[1] for r in con.execute("SELECT path, digest FROM online_sent")}


def mark_online_sent(items: list[tuple[str, str]]):
    with connect() as con:
        con.executemany("INSERT OR REPLACE INTO online_sent VALUES (?, ?, ?)", [(p, d, now_iso()) for p, d in items])


def online_got() -> dict:
    with connect() as con:
        return {r[0]: r[1] for r in con.execute("SELECT path, stamp FROM online_got")}


def mark_online_got(items: list[tuple[str, str]]):
    with connect() as con:
        con.executemany("INSERT OR REPLACE INTO online_got VALUES (?, ?, ?)", [(p, d, now_iso()) for p, d in items])


def scout_fetches_df() -> pd.DataFrame:
    with connect() as con:
        return pd.read_sql_query("SELECT * FROM scout_fetches", con)


def scout_matches_df() -> pd.DataFrame:
    """Every scouted game, from the scouted club's point of view (club_id = the scouted club)."""
    with connect() as con:
        return pd.read_sql_query("SELECT * FROM scout_matches ORDER BY ts", con)


def scout_players_df() -> pd.DataFrame:
    with connect() as con:
        return pd.read_sql_query("SELECT * FROM scout_players", con)


def counts(club_id) -> dict:
    with connect() as con:
        r = con.execute("SELECT COUNT(*), MAX(ts), MAX(fetched_at) FROM matches WHERE club_id=?",
                        (str(club_id),)).fetchone()
    return {"matches": r[0] or 0, "last_ts": r[1], "last_fetch": r[2]}


def signature() -> tuple:
    """Changes whenever something is stored (the app reloads its numbers then)."""
    with connect() as con:
        a = con.execute("SELECT COUNT(*), COALESCE(MAX(ts), 0) FROM matches").fetchone()
        b = con.execute("SELECT COUNT(*) FROM club_snapshots").fetchone()
        c = con.execute("SELECT COUNT(*) FROM member_snapshots").fetchone()
        d = con.execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(notes)), 0) FROM matches WHERE notes IS NOT NULL").fetchone()
        e = con.execute("SELECT COUNT(*), COALESCE(SUM(ok), 0), COALESCE(MAX(at), '') FROM scout_fetches").fetchone()
    return tuple(a) + tuple(b) + tuple(c) + tuple(d) + tuple(e)


init_db()
