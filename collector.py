"""FC27 Clubs collector: runs in the background and saves every match your club plays.

EA's Clubs API only keeps the last 10 matches of each type, so this checks regularly and stores
everything in data/clubs.db, building the full history EA doesn't keep.
- Every poll_active_minutes while FC 27 is open on this PC, or for a while after a new match showed up
  (so it also follows along when you play on a console).
- Every poll_idle_minutes otherwise. 10 matches take hours to play, so nothing is ever missed.
It can be started with Windows (Settings in the app) and stopped from the app as well.
After each check it also scouts the clubs you played (their last 10 games and EA's numbers for them) and, when
the online copy is set up, sends what's new to GitHub for the online app. When the folder is a git clone of the
repository it installs updates by itself (about once an hour), and when the tracker's code changes it restarts
itself with the new code.

Run: python collector.py   (start_collector.bat does this in a window; the app starts it hidden)
"""
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from clubs import cloud, config, db, scout, sync, updater

LOG_PATH = config.DATA_DIR / "collector_log.txt"
STATUS_PATH = config.DATA_DIR / "collector_status.json"
STOP_PATH = config.DATA_DIR / "collector_stop"       # the app creates this to ask the collector to stop
NOW_PATH = config.DATA_DIR / "collector_check_now"   # ... and this for a full check right away
_status = {"pid": os.getpid(), "state": "starting", "since": None}
HERE = Path(__file__).resolve().parent


def code_signature() -> float:
    """Newest change to the tracker's code: when it changes, the collector restarts with the new code."""
    files = [HERE / "collector.py", HERE / "VERSION"] + list((HERE / "clubs").glob("*.py"))
    return max((f.stat().st_mtime for f in files if f.exists()), default=0.0)


def relaunch():
    """Start a fresh collector (with the updated code); this one ends right after."""
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    flags = 0x00000008 | 0x08000000 | 0x00000200 if os.name == "nt" else 0   # detached, no window
    subprocess.Popen([str(w if os.name == "nt" and w.exists() else exe), str(HERE / "collector.py")], cwd=str(HERE),
                     creationflags=flags, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True)


def log(msg):
    line = f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    try:
        print(line, flush=True)   # nothing happens when running without a window (pythonw)
    except Exception:
        pass
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > 1_000_000:
            LOG_PATH.write_text(LOG_PATH.read_text(encoding="utf-8")[-200_000:], encoding="utf-8")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def set_status(state=None, **kw):
    if state and state != _status.get("state"):
        _status["state"], _status["since"] = state, dt.datetime.now().isoformat(timespec="seconds")
    _status.update(kw)
    _status["updated"] = dt.datetime.now().isoformat(timespec="seconds")
    try:
        tmp = STATUS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(_status), encoding="utf-8")
        os.replace(tmp, STATUS_PATH)
    except Exception:
        pass


def other_instance() -> int | None:
    """pid of a collector that is already running (only one at a time). One that is restarting for an update
    gets a few seconds to finish."""
    for _ in range(20):
        try:
            import psutil
            st = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
            pid = int(st.get("pid") or 0)
            if not (pid and pid != os.getpid() and psutil.pid_exists(pid)
                    and any("collector.py" in part for part in psutil.Process(pid).cmdline())):
                return None
            if st.get("state") != "restarting":
                return pid
        except Exception:
            return None
        time.sleep(1)
    return None


def game_running(name_part: str) -> bool:
    if not name_part:
        return False
    try:
        import psutil
        return any(name_part.lower() in (p.info.get("name") or "").lower() for p in psutil.process_iter(["name"]))
    except Exception:
        return False


def below_normal_priority():
    try:
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if sys.platform == "win32" else 10)
    except Exception:
        pass


def recent_new_match(cfg) -> bool:
    """A new match turned up a little while ago (you're probably still playing)."""
    st = sync.last_sync()
    ts = st.get("last_new_ts")
    if not ts:
        return False
    return time.time() - ts < float(cfg.get("active_window_minutes", 45)) * 60


def describe(m: dict) -> str:
    kind = {"leagueMatch": "League", "playoffMatch": "Playoffs", "friendlyMatch": "Friendly"}.get(m["match_type"], "")
    return f"{kind} {m['goals_for']}-{m['goals_against']} vs {m.get('opp_name') or '?'} ({m.get('result')})"


def beep():
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_OK)
    except Exception:
        pass


def main():
    pid = other_instance()
    if pid:
        log(f"Another collector is already running (pid {pid}), so this one stops.")
        return
    for p in (STOP_PATH, NOW_PATH):
        try:
            p.unlink()
        except OSError:
            pass
    below_normal_priority()
    code = code_signature()
    log(f"Collector started (version {config.VERSION}).")
    set_status("starting", version=config.VERSION, code=code)
    full_next, trouble, restart, code_seen = True, 0, False, code
    last_update, last_update_error = 0.0, None
    while True:
        if STOP_PATH.exists():
            try:
                STOP_PATH.unlink()
            except OSError:
                pass
            break
        cfg = config.load()
        if not cfg.get("club_id"):
            set_status("no_club", next_check=None)
            time.sleep(5)
            continue
        if NOW_PATH.exists():
            try:
                NOW_PATH.unlink()
            except OSError:
                pass
            full_next = True
        set_status("checking")
        try:
            res = sync.sync(full=full_next, log=log, cfg=cfg)
        except Exception as e:   # never die: log it and try again later
            res = {"new": [], "errors": [f"{type(e).__name__}: {e}"], "ok": False}
        full_next = False
        for m in res["new"]:
            log("Saved: " + describe(m))
        if res["new"] and cfg.get("beep_on_new_match"):
            beep()
        if res.get("ok"):
            if trouble:
                log("EA answers again.")
            trouble = 0
            set_status("scouting")
            try:   # the clubs you just played (and a few older ones): their last 10 games
                scout.run(cfg, log=log, limit=len(res["new"]) + 4)
            except Exception as e:
                log(f"Scouting failed: {type(e).__name__}: {e}")
            if cloud.settings(cfg):
                set_status("sending")
                try:   # the online copy
                    cloud.push(cfg, log=log)
                except Exception as e:
                    log(f"Online copy failed: {type(e).__name__}: {e}")
        else:
            trouble += 1
            log("Check failed: " + "; ".join(res["errors"][-3:]))
        if cfg.get("auto_update", True) and time.time() - last_update > 3600 and updater.available():
            last_update = time.time()
            try:   # new versions from GitHub (the restart below picks up the new code)
                u = updater.update(log=log)
                if not u.get("ok") and u.get("error") != last_update_error:
                    log(f"Update check failed: {u.get('error')}")
                last_update_error = u.get("error")
            except Exception as e:
                log(f"Update check failed: {type(e).__name__}: {e}")
        playing = game_running(cfg.get("game_process", "fc27"))
        busy = playing or recent_new_match(cfg)
        minutes = float(cfg.get("poll_active_minutes", 2) if busy else cfg.get("poll_idle_minutes", 15))
        if trouble:   # EA is down or blocking: wait longer each time (up to an hour)
            minutes = max(minutes, min(60.0, 2.0 ** trouble))
        next_at = time.time() + minutes * 60
        state = "error" if trouble else ("playing" if playing else "active" if busy else "idle")
        set_status(state, next_check=dt.datetime.fromtimestamp(next_at).isoformat(timespec="seconds"),
                   interval_min=minutes, last_check=dt.datetime.now().isoformat(timespec="seconds"),
                   last_error=("; ".join(res["errors"][-2:]) or None), new_last=len(res["new"]),
                   transport=sync.last_sync().get("transport"))
        n = 0
        while time.time() < next_at:
            if STOP_PATH.exists() or NOW_PATH.exists():
                break
            time.sleep(2)
            n += 1
            if n % 15 == 0:   # every 30 s: heartbeat, and check sooner when the game was just opened
                now_code = code_signature()
                if now_code != code:   # updated: restart once the files have stopped changing for 30 s
                    if now_code == code_seen:
                        restart = True
                        break
                    code_seen = now_code
                if not playing and game_running(cfg.get("game_process", "fc27")):
                    playing = True
                    next_at = min(next_at, time.time() + float(cfg.get("poll_active_minutes", 2)) * 60)
                    set_status("playing", next_check=dt.datetime.fromtimestamp(next_at).isoformat(timespec="seconds"))
                else:
                    set_status()
        if restart:
            break
    if restart:
        log("The tracker was updated: restarting the collector with the new version.")
        set_status("restarting", next_check=None)
        relaunch()
        return
    log("Collector stopped.")
    set_status("stopped", next_check=None)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Collector stopped (Ctrl+C).")
        set_status("stopped", next_check=None)
