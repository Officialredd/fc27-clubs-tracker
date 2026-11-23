"""Control the background collector (collector.py) from the app: is it running, start, stop, check now,
and start it with Windows."""
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import config

STATUS_PATH = config.DATA_DIR / "collector_status.json"
STOP_PATH = config.DATA_DIR / "collector_stop"
NOW_PATH = config.DATA_DIR / "collector_check_now"
LOG_PATH = config.DATA_DIR / "collector_log.txt"
COLLECTOR = config.ROOT / "collector.py"
STARTUP_NAME = "FC27 Clubs collector.vbs"


def _read() -> dict:
    try:
        return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def status() -> dict:
    """{"running": bool, "state": "checking" | "playing" | "active" | "idle" | "no_club", "next_check": datetime, ...}"""
    st = _read()
    alive = False
    try:
        import psutil
        pid = int(st.get("pid") or 0)
        alive = bool(pid) and psutil.pid_exists(pid) and any("collector.py" in p for p in psutil.Process(pid).cmdline())
    except Exception:
        try:   # no psutil: trust a recent heartbeat
            alive = (dt.datetime.now() - dt.datetime.fromisoformat(st["updated"])).total_seconds() < 45
        except Exception:
            alive = False
    st["running"] = alive
    for k in ("since", "next_check", "last_check", "last_new", "updated"):
        try:
            st[k] = dt.datetime.fromisoformat(st[k]) if st.get(k) else None
        except (TypeError, ValueError):
            st[k] = None
    return st


def _pythonw() -> str:
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w if w.exists() else exe)


def start() -> bool:
    """Start the collector in the background (no window)."""
    if status().get("running"):
        return True
    try:
        STOP_PATH.unlink()
    except OSError:
        pass
    flags = 0
    if os.name == "nt":
        flags = 0x00000008 | 0x08000000 | 0x00000200   # detached, no window, own process group
    subprocess.Popen([_pythonw(), str(COLLECTOR)], cwd=str(config.ROOT), creationflags=flags,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    for _ in range(30):   # wait until it reports in
        time.sleep(0.5)
        if status().get("running"):
            return True
    return False


def stop(timeout: float = 12.0) -> bool:
    st = status()
    if not st.get("running"):
        return True
    STOP_PATH.write_text("stop", encoding="utf-8")
    end = time.time() + timeout
    while time.time() < end:
        time.sleep(0.5)
        if not status().get("running"):
            return True
    try:
        import psutil
        psutil.Process(int(st["pid"])).terminate()
        return True
    except Exception:
        return False


def restart() -> bool:
    """Stop the collector and start it again (to run updated code)."""
    stop(timeout=40.0)
    return start()


def outdated() -> bool:
    """The running collector is from before 1.4: it can't restart itself after an update."""
    st = status()
    return bool(st.get("running")) and not st.get("code")


def check_now() -> bool:
    """Ask the running collector for a full check right away."""
    if not status().get("running"):
        return False
    NOW_PATH.write_text("now", encoding="utf-8")
    return True


def _startup_dir() -> Path | None:
    appdata = os.environ.get("APPDATA")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" if appdata else None


def autostart_enabled() -> bool:
    d = _startup_dir()
    return bool(d) and (d / STARTUP_NAME).exists()


def set_autostart(on: bool) -> bool:
    """Start the collector (hidden) whenever you log in to Windows, or stop doing that."""
    d = _startup_dir()
    if not d:
        return False
    path = d / STARTUP_NAME
    if not on:
        try:
            path.unlink()
        except OSError:
            pass
        return True
    script = ('Set sh = CreateObject("WScript.Shell")\r\n'
              f'sh.CurrentDirectory = "{config.ROOT}"\r\n'
              f'sh.Run """{_pythonw()}"" ""{COLLECTOR}""", 0, False\r\n')
    try:
        d.mkdir(parents=True, exist_ok=True)
        path.write_text(script, encoding="utf-8")
        return True
    except OSError:
        return False


def last_log_lines(n: int = 15) -> list[str]:
    try:
        return LOG_PATH.read_text(encoding="utf-8").splitlines()[-n:]
    except Exception:
        return []
