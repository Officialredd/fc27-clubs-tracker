"""One check with EA: save every match we don't have yet and refresh the club and member numbers.

EA only returns the last 10 matches of each type (league, playoffs, friendlies), so the collector checks
regularly and keeps everything; a quick check asks for the last 3 and only fetches all 10 when all 3 are new.
"""
import datetime as dt
import json
import time

from . import api, config, db, parse

STATE_PATH = config.DATA_DIR / "sync_state.json"
SNAPSHOT_EVERY = 60 * 60        # seconds between club/member refreshes when nothing new happened


def _state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(st: dict):
    try:
        STATE_PATH.write_text(json.dumps(st, indent=1), encoding="utf-8")
    except OSError:
        pass


def last_sync() -> dict:
    return _state()


def resolve_me(cfg: dict | None = None) -> str | None:
    """Your player id, found from your name in the stored matches (kept in config.json)."""
    cfg = cfg or config.load()
    if cfg.get("me_id") or not cfg.get("me_name") or not cfg.get("club_id"):
        return cfg.get("me_id")
    with db.connect() as con:
        r = con.execute("SELECT p.player_id FROM player_matches p JOIN matches m ON m.match_id = p.match_id "
                        "WHERE m.club_id = ? AND p.side = 'us' AND LOWER(p.name) = LOWER(?) "
                        "ORDER BY m.ts DESC LIMIT 1", (str(cfg["club_id"]), cfg["me_name"].strip())).fetchone()
    if r:
        config.save({"me_id": r[0]})
        return r[0]
    return None


def refresh_club(cfg: dict, log=print) -> dict:
    """Club info (name, crest), record, division, skill rating and the members' season numbers."""
    cid, plat = cfg["club_id"], cfg["platform"]
    out = {"club": False, "members": 0, "errors": []}
    try:
        info = api.club_info(cid, plat)
        if info:
            db.save_club(parse.club_row(cid, info, plat))
            if info.get("name") and info["name"] != cfg.get("club_name"):
                config.save({"club_name": info["name"]})
                cfg["club_name"] = info["name"]
    except api.ApiError as e:
        out["errors"].append(f"club info: {e}")
    overall = season = None
    try:
        overall = api.overall_stats(cid, plat)
    except api.ApiError as e:
        out["errors"].append(f"club stats: {e}")
    try:
        season = api.season_stats(cid, cfg.get("club_name"), plat)
    except api.ApiError as e:
        out["errors"].append(f"season: {e}")
    out["club"] = db.save_club_snapshot(cid, overall, season)
    try:
        mem = api.members(cid, plat)
        out["members"] = db.save_member_snapshots(cid, mem.get("members") or [])
    except api.ApiError as e:
        out["errors"].append(f"members: {e}")
    return out


def sync(full: bool = False, log=print, cfg: dict | None = None) -> dict:
    """Check EA once. full=True asks for all 10 matches of every type (first start, 'Check now')."""
    cfg = cfg or config.load()
    if not cfg.get("club_id"):
        return {"new": [], "errors": ["No club chosen yet."], "checked": False}
    cid, plat = str(cfg["club_id"]), cfg["platform"]
    known = db.known_match_ids(cid)
    new, errors, got, answered = [], [], 0, 0
    for mt in cfg.get("match_types") or ["leagueMatch", "playoffMatch", "friendlyMatch"]:
        try:
            n = 10 if full or not known else 3
            data = api.matches(cid, plat, mt, n)
            if n < 10 and len(data) == n and all(str(m.get("matchId")) not in known for m in data):
                data = api.matches(cid, plat, mt, 10)    # more than 3 new ones: get everything EA has
            got += len(data)
            answered += 1
            for raw in sorted(data, key=lambda m: m.get("timestamp") or 0):
                mid = str(raw.get("matchId"))
                if mid in known:
                    continue
                try:
                    match, players, clubs = parse.parse_match(raw, cid, plat, mt)
                except Exception as e:
                    errors.append(f"couldn't read match {mid}: {e!r}")
                    continue
                if db.save_match(match, players, clubs, raw):
                    known.add(mid)
                    new.append(match)
        except api.ApiError as e:
            errors.append(f"{mt}: {e}")
        except Exception as e:
            errors.append(f"{mt}: {type(e).__name__}: {e}")
    st = _state()
    club_out = None
    if new or full or time.time() - st.get("club_refreshed", 0) > SNAPSHOT_EVERY:
        club_out = refresh_club(cfg, log)
        errors += club_out["errors"]
        st["club_refreshed"] = time.time()
    ok = answered > 0
    st.update(last_check=dt.datetime.now().isoformat(timespec="seconds"), last_ok=ok, last_errors=errors[-5:],
              transport=api.last_info.get("transport"))
    if ok:
        st["last_success"] = st["last_check"]
    if new:
        st["last_new"] = st["last_check"]
        st["last_new_ts"] = max(m["ts"] or 0 for m in new)
    _save_state(st)
    if new:
        resolve_me(cfg)
    return {"new": new, "errors": errors, "checked": True, "ok": ok, "club": club_out}
