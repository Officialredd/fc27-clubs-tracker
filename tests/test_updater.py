"""Updates with git: fast-forward to the repository's newest version, never over local changes.
Run: python tests/test_updater.py (needs git)"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
os.environ.setdefault("CLUBS_DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("CLUBS_CONFIG", os.path.join(os.environ["CLUBS_DATA_DIR"], "config.json"))

from clubs import updater  # noqa: E402


def sh(cwd, *args):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def test_update():
    if not shutil.which("git"):
        print("skipped: no git")
        return
    base = Path(tempfile.mkdtemp())
    origin, work, pc = base / "origin.git", base / "work", base / "pc"
    sh(base, "git", "init", "--bare", "-b", "main", str(origin))
    sh(base, "git", "clone", str(origin), str(work))
    for d in (work,):
        sh(d, "git", "config", "user.email", "t@t"), sh(d, "git", "config", "user.name", "t")
    (work / "VERSION").write_text("1.0\n")
    (work / "app.py").write_text("print(1)\n")
    sh(work, "git", "add", "."), sh(work, "git", "commit", "-m", "one"), sh(work, "git", "push", "origin", "main")
    sh(base, "git", "clone", str(origin), str(pc))
    updater.ROOT = pc
    assert updater.available()
    st = updater.check()
    assert st["ok"] and st["behind"] == 0 and not st["dirty"]
    # a new version on GitHub
    (work / "VERSION").write_text("1.1\n")
    (work / "requirements.txt").write_text("streamlit\n")
    sh(work, "git", "add", "."), sh(work, "git", "commit", "-m", "two"), sh(work, "git", "push", "origin", "main")
    pip = []
    updater._pip_install = lambda log=print: pip.append(1)
    logs = []
    res = updater.update(log=logs.append)
    assert res["ok"] and res["updated"], res
    assert (pc / "VERSION").read_text() == "1.1\n" and pip == [1] and "1.1" in logs[-1]
    # data-only commits (online-data) aren't a new version
    (work / "online-data").mkdir()
    (work / "online-data" / "club.json").write_text("{}")
    sh(work, "git", "add", "."), sh(work, "git", "commit", "-m", "data"), sh(work, "git", "push", "origin", "main")
    res = updater.update(log=logs.append)
    assert res["ok"] and not res["updated"] and (pc / "online-data" / "club.json").exists()
    # never over local changes
    (work / "app.py").write_text("print(2)\n")
    sh(work, "git", "commit", "-am", "three"), sh(work, "git", "push", "origin", "main")
    (pc / "app.py").write_text("print('mine')\n")
    res = updater.update(log=logs.append)
    assert not res["ok"] and "changed on this PC" in res["error"] and (pc / "app.py").read_text() == "print('mine')\n"


if __name__ == "__main__":
    test_update()
    print("ok test_update")
