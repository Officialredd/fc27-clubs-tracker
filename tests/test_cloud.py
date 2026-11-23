"""The online copy: sending to GitHub (against a fake GitHub) and reading it back into a fresh database.
Run: python tests/test_cloud.py"""
import copy
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
os.environ.setdefault("CLUBS_DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("CLUBS_CONFIG", os.path.join(os.environ["CLUBS_DATA_DIR"], "config.json"))

from clubs import cloud, config, db, parse, tz  # noqa: E402

RAW = json.loads((HERE / "fixtures" / "real_match_dnf.json").read_text(encoding="utf-8"))
US, THEM = "2315992", "1881695"
CFG = {"club_id": US, "platform": "common-gen5", "club_name": "Dog Care FC", "me_name": "ReDD_TVx",
       "online_repo": "someone/fc27-clubs-tracker", "online_token": "t", "online_branch": "main"}


class FakeGitHub:
    """Just enough of GitHub's Git Data API: refs, commits and trees (with a race on demand)."""

    def __init__(self):
        self.trees = {"t0": {"app.py": "code"}}
        self.commits = {"c0": {"tree": "t0", "parents": []}}
        self.head = "c0"
        self.n = 0
        self.race = False
        self.calls = []

    def _id(self, p):
        self.n += 1
        return f"{p}{self.n}"

    def __call__(self, method, path, token, payload=None, timeout=40):
        self.calls.append((method, path))
        assert token == "t"
        if method == "GET" and path.endswith("/git/ref/heads/main"):
            return {"object": {"sha": self.head}}
        if method == "GET" and "/git/commits/" in path:
            return {"tree": {"sha": self.commits[path.rsplit("/", 1)[1]]["tree"]}}
        if method == "POST" and path.endswith("/git/trees"):
            files = dict(self.trees[payload["base_tree"]])
            files.update({e["path"]: e["content"] for e in payload["tree"]})
            sha = self._id("t")
            self.trees[sha] = files
            return {"sha": sha}
        if method == "POST" and path.endswith("/git/commits"):
            sha = self._id("c")
            self.commits[sha] = {"tree": payload["tree"], "parents": payload["parents"]}
            return {"sha": sha}
        if method == "PATCH" and path.endswith("/git/refs/heads/main"):
            if self.race:   # someone pushed meanwhile
                self.race = False
                other = self._id("c")
                self.commits[other] = {"tree": self.commits[self.head]["tree"], "parents": [self.head]}
                self.head = other
            if self.commits[payload["sha"]]["parents"] != [self.head]:
                raise cloud.OnlineError("GitHub said 422: Update is not a fast forward")
            self.head = payload["sha"]
            return {}
        raise AssertionError(f"unexpected call {method} {path}")

    def files(self):
        return self.trees[self.commits[self.head]["tree"]]


def _match(mid, ts):
    g = copy.deepcopy(RAW)
    g["matchId"], g["timestamp"] = str(mid), ts
    return g


def test_push_and_read_back():
    gh = FakeGitHub()
    cloud._gh = gh
    m1 = _match(501, 1_000_000)
    db.save_match(*parse.parse_match(m1, US, "common-gen5", "leagueMatch"), m1)
    their = [_match(601, 999_000), m1]
    db.save_scouting(THEM, "501", [parse.parse_match(g, THEM, "common-gen5", "leagueMatch") + (g,) for g in their],
                     {"skillRating": "1600"}, our_sr=1500)
    db.save_club_snapshot(US, {"skillRating": "1500", "wins": "3"}, None)
    res = cloud.push(CFG, log=lambda *a: None)
    files = gh.files()
    assert res["ok"] and res["sent"] == 6, res          # club, match, 2 scouted games, 1 scouting record, 1 snapshot
    assert "online-data/m/501.json" in files and "online-data/s/1881695/601.json" in files
    assert "online-data/f/1881695_501.json" in files and files["app.py"] == "code"
    club = json.loads(files["online-data/club.json"])
    assert club["club_id"] == US and club["timezone"] == tz.describe()   # the PC's time zone, for the online copy
    # nothing new: nothing sent; one new match: one file, even when someone pushes at the same moment
    assert cloud.push(CFG, log=lambda *a: None)["sent"] == 0
    m2 = _match(502, 1_000_500)
    db.save_match(*parse.parse_match(m2, US, "common-gen5", "leagueMatch"), m2)
    gh.race = True
    res = cloud.push(CFG, log=lambda *a: None)
    assert res["ok"] and res["sent"] == 1 and "online-data/m/502.json" in gh.files()
    # read it all back into a fresh database
    folder = Path(tempfile.mkdtemp()) / "online-data"
    for path, text in gh.files().items():
        if path.startswith("online-data/"):
            f = folder / path[len("online-data/"):]
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text, encoding="utf-8")
    os.environ["CLUBS_ONLINE_DIR"] = str(folder)
    old_db = db.DB_PATH
    db.DB_PATH = Path(tempfile.mkdtemp()) / "online.db"
    db.init_db()
    try:
        r = cloud.import_new()
        assert r["n"] == 7 and not r["errors"], r
        assert len(db.matches_df(US)) == 2
        assert len(db.scout_matches_df()) == 2 and db.scout_fetches_df().iloc[0]["sr"] == 1600
        assert db.latest_club_snapshot(US).get("skill_rating") == 1500
        assert cloud.import_new()["n"] == 0                    # already read
        assert config.load()["timezone"] == club["timezone"]
    finally:
        db.DB_PATH = old_db


def test_settings():
    assert cloud.settings({}) is None
    assert cloud.settings({"online_repo": "https://github.com/a/b.git", "online_token": "x"})["repo"] == "a/b"
    assert cloud.settings({"online_repo": "a/b", "online_token": ""}) is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
