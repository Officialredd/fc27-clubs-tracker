"""The online copy of the tracker (Streamlit's free hosting, so the club can open it on their phones).

The collector on your PC keeps collecting from EA, and after each check it sends what's new to your GitHub
repository, into the online-data folder (GitHub's API, with an access token you paste into Settings). The online
app runs from the same repository: Streamlit's hosting updates it when the repository changes, and the app reads
online-data into its own database.

online-data holds one small JSON file per thing. Files are only added (scouting records are replaced when they
change), so the repository stays small:
  club.json                       which club this is (id, platform, name, the owner's player)
  m/<match_id>.json               our matches as EA sent them: {"club_id", "platform", "match_type", "raw"}
  s/<club_id>/<match_id>.json     scouted clubs' games, the same way (club_id = the scouted club)
  f/<club_id>_<for_match>.json    what scouting found for one of our matches
  c/<id>.json                     club snapshots (skill rating, division, record)
  p/<club_id>_<time>.json         EA's season numbers of every member, from one refresh
Match notes stay on your PC.
"""
import datetime as dt
import hashlib
import json
import os
import time
import zlib
from pathlib import Path

from . import config, db, parse

FOLDER = "online-data"
API = "https://api.github.com"
MAX_BYTES = 1_500_000      # per commit
MAX_FILES = 300            # per commit
STATE_PATH = config.DATA_DIR / "online_state.json"


class OnlineError(Exception):
    pass


# ---------------------------------------------------------------- settings and state
def settings(cfg: dict) -> dict | None:
    """Repository, branch and token from Settings, or None while they aren't filled in."""
    repo = (cfg.get("online_repo") or "").strip().strip("/")
    if repo.startswith("https://github.com/"):
        repo = repo[len("https://github.com/"):].removesuffix(".git").strip("/")
    token = (cfg.get("online_token") or "").strip()
    if repo.count("/") != 1 or not token:
        return None
    return {"repo": repo, "branch": (cfg.get("online_branch") or "main").strip() or "main", "token": token}


def state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(**kw):
    st = state()
    st.update(kw)
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(st, indent=1), encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------- GitHub
def _gh(method: str, path: str, token: str, payload=None, timeout: int = 40):
    import requests
    try:
        r = requests.request(method, API + path, json=payload, timeout=timeout, headers={
            "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
            "Authorization": f"Bearer {token}", "User-Agent": "fc27-clubs-tracker"})
    except requests.RequestException as e:
        raise OnlineError(f"couldn't reach GitHub ({type(e).__name__})") from None
    if r.status_code >= 400:
        try:
            msg = r.json().get("message") or r.text[:200]
        except ValueError:
            msg = r.text[:200]
        raise OnlineError(f"GitHub said {r.status_code}: {msg}")
    return r.json() if r.content else {}


def check(cfg: dict) -> str:
    """Test the repository and token; returns a short description (raises OnlineError)."""
    s = settings(cfg)
    if not s:
        raise OnlineError("fill in the repository (owner/name) and the access token first")
    info = _gh("GET", f"/repos/{s['repo']}", s["token"])
    if not (info.get("permissions") or {}).get("push"):
        raise OnlineError("the token can read the repository but not write to it: give it Contents read and write")
    try:
        _gh("GET", f"/repos/{s['repo']}/git/ref/heads/{s['branch']}", s["token"])
    except OnlineError as e:
        if "409" in str(e) or "404" in str(e):
            raise OnlineError(f"the repository has no branch '{s['branch']}' yet (is it empty?)") from None
        raise
    return f"{info.get('full_name')} ({'private' if info.get('private') else 'public'}), branch {s['branch']}"


# ---------------------------------------------------------------- sending (your PC)
def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _stamp(at: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in str(at))


def pending(cfg: dict) -> dict[str, str]:
    """Files (path inside online-data -> text) that the repository doesn't have yet or that changed."""
    club, plat = str(cfg["club_id"]), cfg.get("platform") or "common-gen5"
    sent = db.online_sent()
    out = {}

    def add(path, obj):
        text = _dump(obj)
        if sent.get(path) != _digest(text):
            out[path] = text

    with db.connect() as con:
        for r in con.execute("SELECT m.match_id, m.platform, m.match_type, r.data FROM matches m JOIN raw_matches r "
                             "ON r.match_id = m.match_id AND r.club_id = m.club_id WHERE m.club_id = ?", (club,)):
            path = f"m/{r['match_id']}.json"
            if path not in sent:   # never changes once sent
                add(path, {"club_id": club, "platform": r["platform"] or plat, "match_type": r["match_type"],
                           "raw": json.loads(zlib.decompress(r["data"]))})
        for r in con.execute("SELECT s.match_id, s.club_id, s.platform, s.match_type, r.data FROM scout_matches s "
                             "JOIN scout_raw r ON r.match_id = s.match_id"):
            path = f"s/{r['club_id']}/{r['match_id']}.json"
            if path not in sent:
                add(path, {"club_id": str(r["club_id"]), "platform": r["platform"] or plat,
                           "match_type": r["match_type"], "raw": json.loads(zlib.decompress(r["data"]))})
        for r in con.execute("SELECT * FROM scout_fetches"):
            add(f"f/{r['club_id']}_{r['for_match']}.json", dict(r))
        for r in con.execute("SELECT * FROM club_snapshots WHERE club_id = ?", (club,)):
            path = f"c/{r['id']}.json"
            if path not in sent:
                add(path, dict(r))
        groups = {}
        for r in con.execute("SELECT * FROM member_snapshots WHERE club_id = ? ORDER BY at, id", (club,)):
            groups.setdefault(r["at"], []).append(dict(r))
        for at, rows in groups.items():
            path = f"p/{club}_{_stamp(at)}.json"
            if path not in sent:
                add(path, rows)
    add("club.json", {"club_id": club, "platform": plat, "club_name": cfg.get("club_name") or "",
                      "me_name": cfg.get("me_name") or "", "me_id": cfg.get("me_id"),
                      "session_gap_minutes": cfg.get("session_gap_minutes", 45),
                      "archetype_names": cfg.get("archetype_names") or {}})
    return out


def _batches(items: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
    out, cur, size = [], [], 0
    for p, t in items:
        if cur and (len(cur) >= MAX_FILES or size + len(t) > MAX_BYTES):
            out.append(cur)
            cur, size = [], 0
        cur.append((p, t))
        size += len(t)
    if cur:
        out.append(cur)
    return out


def _message(batch) -> str:
    kinds = {"m": "match", "s": "scouted game", "f": "scouting record", "c": "club snapshot", "p": "member snapshot"}
    counts = {}
    for p, _ in batch:
        k = kinds.get(p.split("/")[0], "file")
        counts[k] = counts.get(k, 0) + 1
    return "Online data: " + ", ".join(f"{n} {k}{'es' if k == 'match' and n != 1 else 's' if n != 1 else ''}"
                                       for k, n in counts.items())


def _commit(s: dict, batch: list[tuple[str, str]]):
    """One commit on the branch with these files (tries again if the branch moved meanwhile)."""
    repo, branch, token = s["repo"], s["branch"], s["token"]
    for attempt in range(4):
        head = _gh("GET", f"/repos/{repo}/git/ref/heads/{branch}", token)["object"]["sha"]
        base = _gh("GET", f"/repos/{repo}/git/commits/{head}", token)["tree"]["sha"]
        tree = _gh("POST", f"/repos/{repo}/git/trees", token, {
            "base_tree": base,
            "tree": [{"path": f"{FOLDER}/{p}", "mode": "100644", "type": "blob", "content": t} for p, t in batch]})
        commit = _gh("POST", f"/repos/{repo}/git/commits", token,
                     {"message": _message(batch), "tree": tree["sha"], "parents": [head]})
        try:
            _gh("PATCH", f"/repos/{repo}/git/refs/heads/{branch}", token, {"sha": commit["sha"]})
            return
        except OnlineError as e:   # someone pushed in between (e.g. a code update): build it again on top
            if "422" not in str(e) or attempt == 3:
                raise
            time.sleep(1 + attempt)


def push(cfg: dict, log=print, max_commits: int = 4) -> dict:
    """Send what's new to the repository. Returns {"ok", "sent", "left", "error"}."""
    s = settings(cfg)
    if not s or not cfg.get("club_id") or cfg.get("online_on") is False:
        return {"ok": False, "skipped": True, "sent": 0}
    try:
        items = sorted(pending(cfg).items())
        if not items:
            _save_state(last_check=db.now_iso(), last_error=None)
            return {"ok": True, "sent": 0, "left": 0}
        sent = 0
        batches = _batches(items)
        for b in batches[:max_commits]:
            _commit(s, b)
            db.mark_online_sent([(p, _digest(t)) for p, t in b])
            sent += len(b)
        left = len(items) - sent
        _save_state(last_push=db.now_iso(), last_check=db.now_iso(), last_error=None, last_sent=sent, left=left)
        log(f"Online copy: sent {sent} file{'s' if sent != 1 else ''}" + (f", {left} to go." if left else "."))
        return {"ok": True, "sent": sent, "left": left}
    except (OnlineError, OSError, ValueError) as e:
        _save_state(last_check=db.now_iso(), last_error=str(e)[:300])
        log(f"Online copy failed: {e}")
        return {"ok": False, "sent": 0, "error": str(e)}


# ---------------------------------------------------------------- reading (the online app)
def data_dir() -> Path:
    return Path(os.environ.get("CLUBS_ONLINE_DIR") or (config.ROOT / FOLDER))


def available() -> bool:
    return (data_dir() / "club.json").exists()


def _order(rel: str) -> int:
    return {"club.json": 0, "m": 1, "s": 2, "f": 3, "c": 4, "p": 5}.get(rel.split("/")[0], 9)


def import_new() -> dict:
    """Read new and changed files of online-data into this app's database. Returns {"n", "errors", "newest"}."""
    base = data_dir()
    if not base.exists():
        return {"n": 0, "errors": [], "newest": None}
    got = db.online_got()
    todo, newest = [], 0.0
    for p in base.rglob("*.json"):
        rel = p.relative_to(base).as_posix()
        stt = p.stat()
        newest = max(newest, stt.st_mtime)
        stamp = f"{stt.st_size}:{stt.st_mtime_ns}"
        if got.get(rel) != stamp:
            todo.append((rel, p, stamp))
    done, errors = [], []
    for rel, p, stamp in sorted(todo, key=lambda x: (_order(x[0]), x[0])):
        try:
            _apply(rel, json.loads(p.read_text(encoding="utf-8")))
            done.append((rel, stamp))
        except Exception as e:   # one bad file shouldn't stop the rest
            errors.append(f"{rel}: {type(e).__name__}: {e}")
        if len(done) >= 200:
            db.mark_online_got(done)
            done = []
    db.mark_online_got(done)
    return {"n": len(todo) - len(errors), "errors": errors,
            "newest": dt.datetime.fromtimestamp(newest) if newest else None}


def _apply(rel: str, obj):
    kind = rel.split("/")[0]
    if rel == "club.json":
        config.save({"club_id": obj.get("club_id"), "platform": obj.get("platform"), "club_name": obj.get("club_name"),
                     "me_name": obj.get("me_name"), "me_id": obj.get("me_id"), "welcome_done": True,
                     "session_gap_minutes": obj.get("session_gap_minutes", 45),
                     "archetype_names": obj.get("archetype_names") or {}})
    elif kind == "m":
        m, players, clubs = parse.parse_match(obj["raw"], obj["club_id"], obj["platform"], obj["match_type"])
        db.save_match(m, players, clubs, obj["raw"])
    elif kind == "s":
        m, players, clubs = parse.parse_match(obj["raw"], obj["club_id"], obj["platform"], obj["match_type"])
        db.save_scouting(obj["club_id"], None, [(m, players, clubs, obj["raw"])], None)
    elif kind == "f":
        db.put_rows("scout_fetches", [obj])
    elif kind == "c":
        db.put_rows("club_snapshots", [obj])
    elif kind == "p":
        db.put_rows("member_snapshots", obj)
