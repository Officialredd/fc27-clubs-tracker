"""Used by setup.bat: check that EA's Clubs API answers from this PC and, when a club is set up already,
import the matches EA still has. Exit code 0 = OK, 2 = EA didn't answer, 3 = no club chosen yet."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clubs import api, config, db, sync  # noqa: E402


def main() -> int:
    cfg = config.load()
    print("Checking the connection to EA's Clubs API...")
    try:
        if cfg.get("club_id"):
            info = api.club_info(cfg["club_id"], cfg["platform"])
            name = (info or {}).get("name") or cfg.get("club_name")
        else:
            api.get("currentSeasonLeaderboard", {"platform": cfg["platform"]}, "list")
            name = None
    except api.ApiError as e:
        print("  EA didn't answer:", str(e)[:400])
        print("  That can be a short EA outage. The collector keeps trying by itself, and the app shows its status.")
        return 2
    print(f"  OK, EA answers (connection: {api.last_info.get('transport')}).")
    if not cfg.get("club_id"):
        print("  No club chosen yet: the app asks for it when it opens.")
        return 3
    print(f"Importing the matches EA still has for {name}...")
    res = sync.sync(full=True)
    by_type = {}
    for m in res["new"]:
        by_type[m["match_type"]] = by_type.get(m["match_type"], 0) + 1
    total = db.counts(cfg["club_id"])["matches"]
    detail = ", ".join(f"{n} {t.replace('Match', '')}" for t, n in by_type.items())
    n = len(res["new"])
    print(f"  Saved {n} new {'match' if n == 1 else 'matches'}" + (f" ({detail})" if detail else "") + f". {total} in total.")
    for e in res["errors"][:3]:
        print("  Note:", e[:200])
    me = sync.resolve_me()
    if cfg.get("me_name"):
        print(f"  Your player: {cfg['me_name']}" + ("" if me else " (not in these matches yet)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
