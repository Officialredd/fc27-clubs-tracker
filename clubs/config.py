"""Settings, loaded from config.json in the project folder (created on first run)."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("CLUBS_DATA_DIR") or (ROOT / "data"))
CONFIG_PATH = Path(os.environ.get("CLUBS_CONFIG") or (ROOT / "config.json"))

DEFAULTS = {
    # EA platform group: "common-gen5" = PS5, Xbox Series X|S and PC (crossplay), "nx" = Nintendo Switch 2
    "platform": "common-gen5",
    # Your club (EA club id) and its name
    "club_id": None,
    "club_name": "",
    # You: your EA name as it shows in the club, and (filled in by itself) your player id
    "me_name": "",
    "me_id": None,
    # Which matches to collect: league, playoffs and friendlies (EA keeps only the last 10 of each)
    "match_types": ["leagueMatch", "playoffMatch", "friendlyMatch"],
    # Minutes between checks: while FC 27 is open on this PC or right after a new match ...
    "poll_active_minutes": 2,
    # ... and otherwise (EA keeps 10 matches per type, so even this never misses a match)
    "poll_idle_minutes": 15,
    # After a new match, keep checking often for this long (for playing on a console)
    "active_window_minutes": 45,
    # Part of the game's program name, to notice when FC 27 is open on this PC
    "game_process": "fc27",
    # Beep when a new match is saved
    "beep_on_new_match": False,
    # Matches closer together than this belong to the same session
    "session_gap_minutes": 45,
    # Your names for EA's archetype numbers, e.g. {"11": "Finisher"}
    "archetype_names": {},
    "welcome_done": False,
    # The online copy: your GitHub repository (owner/name), its branch, an access token (Contents: read and
    # write) and whether to send new data after each check
    "online_repo": "",
    "online_branch": "main",
    "online_token": "",
    "online_on": True,
    # Install new versions from GitHub by itself (when this folder is a git clone of the repository)
    "auto_update": True,
}

UI_DEFAULTS = {
    "home_kpis": True, "home_last_match": True, "home_form": True, "home_charts": True,
    "stats_default": "Team",
    "rolling_window": 5,
}


def load() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    else:
        try:
            CONFIG_PATH.write_text(json.dumps(DEFAULTS, indent=2), encoding="utf-8")
        except OSError:
            pass
    return cfg


def save(updates: dict) -> None:
    """Change settings in config.json (only the given keys; everything else stays as it is)."""
    try:
        cur = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.exists() else dict(DEFAULTS)
    except Exception:
        cur = dict(DEFAULTS)
    for k, v in updates.items():
        if isinstance(v, dict) and isinstance(cur.get(k), dict) and k != "archetype_names":
            cur[k] = {**cur[k], **v}
        else:
            cur[k] = v
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cur, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)


def ui() -> dict:
    """The app's display options (Settings page)."""
    return {**UI_DEFAULTS, **(load().get("ui") or {})}


DATA_DIR.mkdir(parents=True, exist_ok=True)

try:
    VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip() or "dev"
except OSError:
    VERSION = "dev"
