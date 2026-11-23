"""Updates from GitHub: when the tracker's folder is a clone of its GitHub repository, new versions are
fetched and installed with git (fast-forward only, never over changes you made yourself). The collector checks
about once an hour when automatic updates are on, and restarts itself after an update (see collector.py).
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import config

ROOT = config.ROOT


def _git(*args, timeout: int = 90) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}   # never wait for a login prompt
    flags = 0x08000000 if os.name == "nt" else 0                                       # no console window
    return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True, timeout=timeout, env=env,
                          creationflags=flags)


def available() -> bool:
    """git is installed and this folder is a clone of the repository."""
    return shutil.which("git") is not None and (ROOT / ".git").exists()


def check(fetch: bool = True) -> dict:
    """{"ok", "branch", "behind", "dirty", "error"}: how many new versions (commits) are waiting."""
    if not available():
        return {"ok": False, "error": "this folder isn't a git clone of the repository"}
    try:
        branch = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "main"
        if fetch:
            f = _git("fetch", "--quiet", "origin", branch)
            if f.returncode != 0:
                return {"ok": False, "branch": branch, "error": (f.stderr or f.stdout).strip()[:300] or "git fetch failed"}
        behind = _git("rev-list", "--count", f"HEAD..origin/{branch}").stdout.strip()
        dirty = _git("status", "--porcelain", "--untracked-files=no").stdout.strip()
        return {"ok": True, "branch": branch, "behind": int(behind or 0), "dirty": bool(dirty),
                "changed": dirty.splitlines()[:5]}
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def update(log=print) -> dict:
    """Install what's new (fast-forward). Returns {"ok", "updated", "error"}."""
    st = check()
    if not st.get("ok"):
        return {"ok": False, "updated": False, "error": st.get("error")}
    if not st["behind"]:
        return {"ok": True, "updated": False}
    if st["dirty"]:
        return {"ok": False, "updated": False,
                "error": "files of the tracker were changed on this PC, so updates aren't installed over them: "
                         + ", ".join(st["changed"])}
    try:
        old = _git("rev-parse", "HEAD").stdout.strip()
        m = _git("merge", "--ff-only", f"origin/{st['branch']}")
        if m.returncode != 0:
            return {"ok": False, "updated": False, "error": (m.stderr or m.stdout).strip()[:300]}
        changed = _git("diff", "--name-only", old, "HEAD").stdout.split()
        code = [c for c in changed if not c.startswith("online-data/")]
        if "requirements.txt" in changed:
            _pip_install(log)
        if code:
            log(f"Updated to version {(ROOT / 'VERSION').read_text(encoding='utf-8').strip()} "
                f"({len(code)} file{'s' if len(code) != 1 else ''} changed).")
        return {"ok": True, "updated": bool(code)}
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "updated": False, "error": f"{type(e).__name__}: {e}"}


def _pip_install(log=print):
    exe = Path(sys.executable)
    py = exe.with_name("python.exe") if exe.name.lower() == "pythonw.exe" else exe
    flags = 0x08000000 if os.name == "nt" else 0
    r = subprocess.run([str(py), "-m", "pip", "install", "--quiet", "-r", str(ROOT / "requirements.txt")],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=900, creationflags=flags)
    log("Installed the new requirements." if r.returncode == 0 else f"pip failed: {(r.stderr or '')[-300:]}")
