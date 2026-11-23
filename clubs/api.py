"""EA's public Clubs API (the one EA's own Clubs website uses). No login or key is needed.

EA doesn't document it and is picky about which programs may call it: plain scripts sometimes get
"403 Access Denied" or a dropped connection, while a normal browser gets the data. So requests go out
with a Chrome network profile first (curl_cffi), and fall back to ordinary Python requests.
EA has also been seen sending real data with a 403 status, so every answer is judged by its content.

Endpoints (base https://proclubs.ea.com/api/fc/):
  allTimeLeaderboard/search, currentSeasonLeaderboard/search   platform, clubName
  clubs/info, clubs/overallStats                                platform, clubIds (one id only)
  members/stats, members/career/stats                           platform, clubId
  clubs/matches                                                 platform, clubIds, matchType, maxResultCount (max 10)
"""
import json
import os
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

BASE = "https://proclubs.ea.com/api/fc/"
TIMEOUT = 20
MIN_GAP = 1.0          # seconds between two requests, to stay gentle
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-GB,en;q=0.9",
    "Origin": "https://www.ea.com",
    "Referer": "https://www.ea.com/",
}
CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/142.0.0.0 Safari/537.36")

_lock = threading.Lock()
_last = [0.0]
_preferred = [None]       # name of the transport that worked last
_sessions = {}
last_info = {"transport": None, "status": None, "at": None, "error": None}


class ApiError(Exception):
    def __init__(self, message, status=None, transport=None, body=None):
        super().__init__(message)
        self.status, self.transport, self.body = status, transport, body


# ---------------------------------------------------------------- transports
def _curl(url):
    from curl_cffi import requests as creq   # Chrome's network fingerprint
    s = _sessions.get("chrome")
    if s is None:
        s = _sessions["chrome"] = creq.Session(impersonate="chrome")
    r = s.get(url, headers=HEADERS, timeout=TIMEOUT)
    return r.status_code, r.text


def _requests(url):
    import requests
    s = _sessions.get("requests")
    if s is None:
        s = _sessions["requests"] = requests.Session()
    r = s.get(url, headers={**HEADERS, "User-Agent": CHROME_UA}, timeout=TIMEOUT)
    return r.status_code, r.text


def _urllib(url):
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, headers={**HEADERS, "User-Agent": CHROME_UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _powershell(url):
    """Windows' own web client (a different network fingerprint than Python's), as a last resort."""
    import subprocess
    if os.name != "nt":
        raise ImportError("Windows only")
    cmd = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; $ProgressPreference='SilentlyContinue'; "
           f"try {{ $r = Invoke-WebRequest -UseBasicParsing -Uri '{url}' -UserAgent '{CHROME_UA}' "
           "-Headers @{'Origin'='https://www.ea.com'; 'Accept-Language'='en-GB,en;q=0.9'} -TimeoutSec 20; "
           "Write-Output ('STATUS:' + $r.StatusCode); Write-Output $r.Content } "
           "catch { Write-Output ('STATUS:' + [int]$_.Exception.Response.StatusCode) }")
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd], capture_output=True,
                         timeout=45, creationflags=0x08000000)
    first, _, body = out.stdout.decode("utf-8", "replace").lstrip("\ufeff").partition("\n")
    try:
        status = int(first.replace("STATUS:", "").strip() or 0)
    except ValueError:
        status, body = 0, first + body
    return status, body


TRANSPORTS = [("chrome", _curl), ("requests", _requests), ("urllib", _urllib), ("windows", _powershell)]


def _fake(path: str, params: dict):
    """Development only: answer from JSON files in the folder named by CLUBS_FAKE_API."""
    root = Path(os.environ["CLUBS_FAKE_API"])
    name = path.replace("/", "_")
    if path == "clubs/matches":
        name += "_" + params.get("matchType", "")
    cid = str(params.get("clubIds") or "")
    f = root / f"{name}_{cid}.json"          # a file for this one club (scouting)...
    own = f.exists()
    if not own:
        f = root / f"{name}.json"            # ... or the general one (your club)
    if not f.exists():
        return (200, "[]") if path == "clubs/matches" else (404, '{"error": "not found"}')
    data = json.loads(f.read_text(encoding="utf-8"))
    if not own and cid and path in ("clubs/matches", "clubs/overallStats"):
        # the general files are your club's: another club only finds its games against you
        if path == "clubs/matches":
            data = [m for m in data if cid in (m.get("clubs") or {})]
        else:
            data = [r for r in data if str(r.get("clubId")) == cid]
            if not data:
                return 404, '{"error": "not found"}'
    if path == "clubs/matches":
        data = data[: int(params.get("maxResultCount", 10))]
    if path.endswith("/search"):
        q = params.get("clubName", "").lower()
        data = [c for c in data if q in (c.get("clubName") or "").lower()]
    return 200, json.dumps(data)


# ---------------------------------------------------------------- core
def _valid(kind: str, data) -> bool:
    if kind == "list":
        return isinstance(data, list)
    if kind == "matches":
        return isinstance(data, list) and all(isinstance(m, dict) and "matchId" in m and "clubs" in m for m in data)
    if kind == "members":
        return isinstance(data, dict) and isinstance(data.get("members"), list)
    if kind == "dict":
        return isinstance(data, dict) and "error" not in data
    return data is not None


def get(path: str, params: dict, kind: str = "any", attempts: int = 2):
    """GET an endpoint and return its JSON. The transport that worked last time goes first (up to `attempts`
    tries), then each other transport once."""
    url = BASE + path + "?" + urlencode(params)
    if os.environ.get("CLUBS_FAKE_API"):
        status, text = _fake(path, params)
        data = json.loads(text)
        if not _valid(kind, data):
            raise ApiError(f"{path}: no data (HTTP {status})", status, "fake", text[:200])
        last_info.update(transport="fake", status=status, at=time.time(), error=None)
        return data
    first = _preferred[0] or TRANSPORTS[0][0]
    plan = [t for t in TRANSPORTS if t[0] == first] * attempts + [t for t in TRANSPORTS if t[0] != first]
    errors, skip, last_status = [], set(), None
    for k, (name, fn) in enumerate(plan):
        if name in skip:
            continue
        with _lock:
            wait = MIN_GAP - (time.time() - _last[0])
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.time()
        try:
            status, text = fn(url)
        except ImportError:
            skip.add(name)
            continue
        except Exception as e:   # timeouts, dropped connections
            errors.append(f"{name}: {type(e).__name__}: {str(e)[:120]}")
            time.sleep(1.5)
            continue
        last_status = status
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if data is not None and _valid(kind, data):
            _preferred[0] = name
            last_info.update(transport=name, status=status, at=time.time(), error=None)
            return data
        errors.append(f"{name}: HTTP {status} {text[:80]!r}")
        if status in (400, 404):   # a wrong request: other programs won't do better
            break
        time.sleep(1.5)
    msg = "EA's Clubs API didn't answer: " + " | ".join(errors[-4:] or ["no connection"])
    last_info.update(error=msg, at=time.time())
    raise ApiError(msg, status=last_status, transport=_preferred[0])


# ---------------------------------------------------------------- endpoints
def search_clubs(name: str, platform: str) -> list[dict]:
    """Clubs whose name contains the text: [{clubId, clubName, wins, ties, losses, currentDivision, clubInfo}, ...]"""
    name = (name or "").strip()[:32]
    if not name:
        return []
    out, seen, err = [], set(), None
    for path, source in (("currentSeasonLeaderboard/search", "season"), ("allTimeLeaderboard/search", "all time")):
        try:
            rows = get(path, {"platform": platform, "clubName": name}, "list")
        except ApiError as e:
            err = e
            continue
        for r in rows:
            cid = str(r.get("clubId") or (r.get("clubInfo") or {}).get("clubId") or "")
            if cid and cid not in seen:
                seen.add(cid)
                out.append({**r, "source": source})
    if not out and err is not None:
        raise err
    return out


def club_info(club_id, platform: str) -> dict | None:
    data = get("clubs/info", {"platform": platform, "clubIds": str(club_id)}, "dict")
    return data.get(str(club_id))


def overall_stats(club_id, platform: str) -> dict | None:
    rows = get("clubs/overallStats", {"platform": platform, "clubIds": str(club_id)}, "list")
    return rows[0] if rows else None


def season_stats(club_id, club_name: str, platform: str) -> dict | None:
    """This season's record, points and division (found through the season leaderboard search)."""
    if not club_name:
        return None
    rows = get("currentSeasonLeaderboard/search", {"platform": platform, "clubName": club_name[:32]}, "list")
    for r in rows:
        if str(r.get("clubId")) == str(club_id):
            return r
    return None


def members(club_id, platform: str) -> dict:
    return get("members/stats", {"platform": platform, "clubId": str(club_id)}, "members")


def members_career(club_id, platform: str) -> dict:
    return get("members/career/stats", {"platform": platform, "clubId": str(club_id)}, "members")


def matches(club_id, platform: str, match_type: str, count: int = 10) -> list[dict]:
    count = max(1, min(10, int(count)))
    return get("clubs/matches", {"platform": platform, "clubIds": str(club_id), "matchType": match_type,
                                 "maxResultCount": count}, "matches")
