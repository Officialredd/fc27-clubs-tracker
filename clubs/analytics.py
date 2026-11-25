"""Everything the app's tables and charts need, worked out from the stored matches."""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import db

SUM_COLS = ["goals", "assists", "shots", "passes_made", "pass_attempts", "tackles_made", "tackle_attempts",
            "saves", "red_cards", "mom"]


@dataclass
class Data:
    club_id: str
    matches: pd.DataFrame            # one row per match, oldest first (game_no 1..n), with team totals
    players: pd.DataFrame            # every player line of both clubs, with match info and game_no
    ours: pd.DataFrame               # our club's player lines
    me: pd.DataFrame                 # your lines (one per match you played)
    me_id: str | None = None
    me_name: str = ""
    names: dict = field(default_factory=dict)   # player_id -> latest name


def _ratio(a, b):
    a = pd.to_numeric(a, errors="coerce").astype(float)
    b = pd.to_numeric(b, errors="coerce").astype(float)
    return (a / b.where(b > 0)).astype(float)


def add_rates(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ga"] = df["goals"].fillna(0) + df["assists"].fillna(0)
    df["pass_pct"] = _ratio(df["passes_made"], df["pass_attempts"]) * 100
    df["tackle_pct"] = _ratio(df["tackles_made"], df["tackle_attempts"]) * 100
    df["conversion"] = _ratio(df["goals"], df["shots"]) * 100
    df["minutes"] = df["seconds"] / 60.0
    return df


def load(club_id, me_id=None, me_name: str = "") -> Data:
    m = db.matches_df(club_id)
    p = db.players_df(club_id)
    if m.empty:
        empty = pd.DataFrame()
        return Data(str(club_id), m, empty, empty, empty, me_id, me_name)
    p = p.merge(m[["match_id", "game_no", "gd", "points", "date"]], on="match_id", how="left")
    p = add_rates(p)
    names = (p.sort_values("ts").groupby("player_id")["name"].last().to_dict() if not p.empty else {})

    # team totals per match, for both clubs
    tot = p.groupby(["match_id", "side"])[SUM_COLS].sum(min_count=1)
    tot["rating"] = p.groupby(["match_id", "side"])["rating"].mean()
    tot = tot.unstack("side")
    tot.columns = [f"{side}_{col}" for col, side in tot.columns]
    m = m.merge(tot, left_on="match_id", right_index=True, how="left")
    for side in ("us", "opp"):
        if f"{side}_passes_made" in m:
            m[f"{side}_pass_pct"] = _ratio(m[f"{side}_passes_made"], m[f"{side}_pass_attempts"]) * 100
            m[f"{side}_tackle_pct"] = _ratio(m[f"{side}_tackles_made"], m[f"{side}_tackle_attempts"]) * 100

    ours = p[p["side"] == "us"].copy()
    if me_id:
        mine = ours[ours["player_id"].astype(str) == str(me_id)]
    else:
        mine = ours.iloc[0:0]
    if mine.empty and me_name:
        mine = ours[ours["name"].str.lower() == me_name.strip().lower()]
        if not mine.empty:
            me_id = mine["player_id"].iloc[-1]
    me = mine.sort_values("ts").drop_duplicates("match_id", keep="last").reset_index(drop=True)
    m["me_played"] = m["match_id"].isin(set(me["match_id"]))
    return Data(str(club_id), m, p, ours, me, me_id, me_name, names)


# ---------------------------------------------------------------- simple numbers
def record(m: pd.DataFrame) -> dict:
    if m.empty:
        return {"n": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0, "win_pct": None, "ppg": None}
    w, d, lo = (m["result"] == "W").sum(), (m["result"] == "D").sum(), (m["result"] == "L").sum()
    n = len(m)
    return {"n": n, "w": int(w), "d": int(d), "l": int(lo), "gf": int(m["goals_for"].sum()),
            "ga": int(m["goals_against"].sum()), "win_pct": 100.0 * w / n if n else None,
            "ppg": (3 * w + d) / n if n else None, "clean_sheets": int((m["goals_against"] == 0).sum())}


def streaks(results: pd.Series) -> dict:
    """Current streak (e.g. W3) and the longest win / unbeaten / losing runs."""
    res = list(results.dropna())
    out = {"current": None, "current_n": 0, "best_win": 0, "best_unbeaten": 0, "worst_loss": 0}
    if not res:
        return out
    last, n = res[-1], 0
    for r in reversed(res):
        if r != last:
            break
        n += 1
    out["current"], out["current_n"] = last, n

    def longest(ok):
        best = cur = 0
        for r in res:
            cur = cur + 1 if ok(r) else 0
            best = max(best, cur)
        return best
    out["best_win"] = longest(lambda r: r == "W")
    out["best_unbeaten"] = longest(lambda r: r != "L")
    out["worst_loss"] = longest(lambda r: r == "L")
    return out


def rolling(s: pd.Series, n: int = 5) -> pd.Series:
    return s.rolling(n, min_periods=max(1, min(n, 3))).mean()


def trend(x: pd.Series, y: pd.Series):
    """Least squares slope and intercept, or (None, None)."""
    x, y = pd.to_numeric(x, errors="coerce"), pd.to_numeric(y, errors="coerce")
    ok = x.notna() & y.notna()
    if ok.sum() < 3 or x[ok].nunique() < 2:
        return None, None
    slope, icept = np.polyfit(x[ok].astype(float), y[ok].astype(float), 1)
    return float(slope), float(icept)


# ---------------------------------------------------------------- sessions and time
def sessions(m: pd.DataFrame, gap_minutes: float = 45) -> pd.DataFrame:
    """Adds session (1..n) and match_in_session (1..k): matches less than gap_minutes apart are one session."""
    if m.empty:
        return m.assign(session=[], match_in_session=[])
    m = m.sort_values("ts").copy()
    gap = m["ts"].diff().fillna(10**9) > gap_minutes * 60
    m["session"] = gap.cumsum().astype(int)
    m["match_in_session"] = m.groupby("session").cumcount() + 1
    return m


def session_table(m: pd.DataFrame, gap_minutes: float = 45) -> pd.DataFrame:
    s = sessions(m, gap_minutes)
    if s.empty:
        return pd.DataFrame()
    g = s.groupby("session")
    out = pd.DataFrame({
        "start": g["when"].min(), "end": g["when"].max(), "matches": g.size(),
        "w": g["result"].apply(lambda r: (r == "W").sum()), "d": g["result"].apply(lambda r: (r == "D").sum()),
        "l": g["result"].apply(lambda r: (r == "L").sum()), "gf": g["goals_for"].sum(), "ga": g["goals_against"].sum(),
    })
    out["points"] = 3 * out["w"] + out["d"]
    out["ppg"] = out["points"] / out["matches"]
    return out.sort_values("start", ascending=False)


def by_match_in_session(m: pd.DataFrame, me: pd.DataFrame, gap_minutes: float = 45, cap: int = 8) -> pd.DataFrame:
    """Win rate and your rating by the match's number in the session (do you fade late in a session?)."""
    s = sessions(m, gap_minutes)
    if s.empty:
        return pd.DataFrame()
    s["k"] = s["match_in_session"].clip(upper=cap)
    out = s.groupby("k").agg(matches=("match_id", "size"), win_pct=("result", lambda r: 100 * (r == "W").mean()),
                             ppg=("points", "mean"))
    if not me.empty:
        r = me.merge(s[["match_id", "k"]], on="match_id").groupby("k")["rating"].mean()
        out["rating"] = r
    return out


WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def weekday_hour(m: pd.DataFrame, value: str = "win") -> pd.DataFrame:
    """7 x 24 table: matches played or win % per weekday and hour."""
    if m.empty:
        return pd.DataFrame()
    t = m.assign(wd=m["when"].dt.weekday, hr=m["when"].dt.hour, win=(m["result"] == "W") * 100.0)
    if value == "count":
        tab = t.pivot_table(index="wd", columns="hr", values="match_id", aggfunc="count")
    else:
        tab = t.pivot_table(index="wd", columns="hr", values="win", aggfunc="mean")
    return tab.reindex(index=range(7), columns=range(24))


# ---------------------------------------------------------------- squad, teammates, opponents
def player_table(lines: pd.DataFrame, names: dict | None = None) -> pd.DataFrame:
    """One row per player: matches, results and totals / averages from the tracked matches."""
    if lines.empty:
        return pd.DataFrame()
    g = lines.groupby("player_id")
    out = pd.DataFrame({
        "matches": g.size(),
        "wins": g["result"].apply(lambda r: (r == "W").sum()),
        "draws": g["result"].apply(lambda r: (r == "D").sum()),
        "losses": g["result"].apply(lambda r: (r == "L").sum()),
        "goals": g["goals"].sum(), "assists": g["assists"].sum(), "shots": g["shots"].sum(),
        "passes_made": g["passes_made"].sum(), "pass_attempts": g["pass_attempts"].sum(),
        "tackles_made": g["tackles_made"].sum(), "tackle_attempts": g["tackle_attempts"].sum(),
        "saves": g["saves"].sum(), "mom": g["mom"].sum(), "red_cards": g["red_cards"].sum(),
        "rating": g["rating"].mean(), "best_rating": g["rating"].max(),
        "pos": g["pos"].agg(lambda s: s.mode().iloc[0] if not s.mode().empty else None),
        "archetype": g["archetype"].agg(lambda s: s.mode().iloc[0] if not s.mode().empty else None),
        "device": g["device"].last(), "last_played": g["when"].max(), "first_played": g["when"].min(),
        "left_early": g["left_early"].sum(),
    })
    out["name"] = [(names or {}).get(i) or lines.loc[lines["player_id"] == i, "name"].iloc[-1] for i in out.index]
    out["win_pct"] = 100.0 * out["wins"] / out["matches"]
    out["ga"] = out["goals"] + out["assists"]
    out["ga_pm"] = out["ga"] / out["matches"]
    out["goals_pm"] = out["goals"] / out["matches"]
    out["assists_pm"] = out["assists"] / out["matches"]
    out["pass_pct"] = _ratio(out["passes_made"], out["pass_attempts"]) * 100
    out["tackle_pct"] = _ratio(out["tackles_made"], out["tackle_attempts"]) * 100
    out["conversion"] = _ratio(out["goals"], out["shots"]) * 100
    return out.sort_values(["matches", "rating"], ascending=False)


def teammates(d: Data) -> pd.DataFrame:
    """For each player who played with you: matches together, results together and your rating with them."""
    if d.me.empty or d.ours.empty:
        return pd.DataFrame()
    my_matches = set(d.me["match_id"])
    others = d.ours[(d.ours["player_id"].astype(str) != str(d.me_id)) & d.ours["match_id"].isin(my_matches)]
    if others.empty:
        return pd.DataFrame()
    my_rating = d.me.set_index("match_id")["rating"]
    rows = []
    for pid, g in others.groupby("player_id"):
        ids = set(g["match_id"])
        mm = d.matches[d.matches["match_id"].isin(ids)]
        without = d.matches[d.matches["match_id"].isin(my_matches - ids)]
        rows.append({
            "player_id": pid, "name": d.names.get(pid, g["name"].iloc[-1]), "together": len(ids),
            "w": int((mm["result"] == "W").sum()), "d": int((mm["result"] == "D").sum()),
            "l": int((mm["result"] == "L").sum()), "win_pct": 100.0 * (mm["result"] == "W").mean(),
            "ppg": mm["points"].mean(), "gf_pm": mm["goals_for"].mean(), "ga_pm": mm["goals_against"].mean(),
            "win_pct_without": 100.0 * (without["result"] == "W").mean() if len(without) else np.nan,
            "my_rating_with": my_rating.reindex(list(ids)).mean(),
            "my_rating_without": my_rating.reindex(list(my_matches - ids)).mean() if my_matches - ids else np.nan,
            "their_rating": g["rating"].mean(), "their_ga": (g["goals"] + g["assists"]).sum(),
            "pos": g["pos"].mode().iloc[0] if not g["pos"].mode().empty else None,
        })
    return pd.DataFrame(rows).sort_values("together", ascending=False)


def lineups(d: Data, min_matches: int = 2) -> pd.DataFrame:
    """Results by the exact group of club players on the pitch."""
    if d.ours.empty:
        return pd.DataFrame()
    grp = d.ours.groupby("match_id")["player_id"].apply(lambda s: tuple(sorted(set(map(str, s)))))
    m = d.matches.set_index("match_id").join(grp.rename("group"))
    rows = []
    for group, g in m.groupby("group"):
        if len(g) < min_matches:
            continue
        rows.append({"players": " + ".join(d.names.get(p, p) for p in group), "size": len(group), "matches": len(g),
                     "w": int((g["result"] == "W").sum()), "d": int((g["result"] == "D").sum()),
                     "l": int((g["result"] == "L").sum()), "win_pct": 100.0 * (g["result"] == "W").mean(),
                     "ppg": g["points"].mean(), "gf_pm": g["goals_for"].mean(), "ga_pm": g["goals_against"].mean()})
    return pd.DataFrame(rows).sort_values(["matches", "win_pct"], ascending=False) if rows else pd.DataFrame()


def by_humans(m: pd.DataFrame) -> pd.DataFrame:
    """Results by how many of us played and the difference in humans with the other club."""
    if m.empty:
        return pd.DataFrame()
    t = m.assign(diff=(m["our_players"] - m["opp_players"]).clip(-4, 4))
    out = t.groupby("diff").agg(matches=("match_id", "size"), win_pct=("result", lambda r: 100 * (r == "W").mean()),
                                ppg=("points", "mean"), gf=("goals_for", "mean"), ga=("goals_against", "mean"))
    return out


def opponents(m: pd.DataFrame) -> pd.DataFrame:
    if m.empty:
        return pd.DataFrame()
    g = m.groupby("opp_id")
    out = pd.DataFrame({
        "name": g["opp_name"].last(), "matches": g.size(),
        "w": g["result"].apply(lambda r: (r == "W").sum()), "d": g["result"].apply(lambda r: (r == "D").sum()),
        "l": g["result"].apply(lambda r: (r == "L").sum()), "gf": g["goals_for"].sum(), "ga": g["goals_against"].sum(),
        "humans": g["opp_players"].mean(), "last": g["when"].max(), "quits": g["they_quit"].sum(),
    })
    out["win_pct"] = 100.0 * out["w"] / out["matches"]
    return out.sort_values(["matches", "last"], ascending=False)


def group_stats(lines: pd.DataFrame, by: str) -> pd.DataFrame:
    """Your numbers per position / archetype / match type."""
    if lines.empty or by not in lines:
        return pd.DataFrame()
    g = lines.groupby(by)
    out = pd.DataFrame({
        "matches": g.size(), "rating": g["rating"].mean(), "goals_pm": g["goals"].mean(),
        "assists_pm": g["assists"].mean(), "ga_pm": g["ga"].mean(), "shots_pm": g["shots"].mean(),
        "pass_pct": _ratio(g["passes_made"].sum(), g["pass_attempts"].sum()) * 100,
        "tackle_pct": _ratio(g["tackles_made"].sum(), g["tackle_attempts"].sum()) * 100,
        "win_pct": g["result"].apply(lambda r: 100 * (r == "W").mean()), "mom": g["mom"].sum(),
    })
    return out.sort_values("matches", ascending=False)


def records(d: Data) -> list[dict]:
    """Best and worst moments: [{label, value, sub, match_id}]"""
    m, me, out = d.matches, d.me, []
    if m.empty:
        return out

    def add(label, row, value, sub):
        out.append({"label": label, "value": value, "sub": sub, "match_id": row["match_id"]})
    w = m[m["result"] == "W"]
    if not w.empty:
        r = w.loc[w["gd"].idxmax()]
        add("Biggest win", r, f"{r.goals_for}-{r.goals_against}", f"vs {r.opp_name}")
    lo = m[m["result"] == "L"]
    if not lo.empty:
        r = lo.loc[lo["gd"].idxmin()]
        add("Heaviest defeat", r, f"{r.goals_for}-{r.goals_against}", f"vs {r.opp_name}")
    r = m.loc[(m["goals_for"] + m["goals_against"]).idxmax()]
    add("Most goals in a match", r, f"{r.goals_for}-{r.goals_against}", f"vs {r.opp_name}")
    if not me.empty:
        r = me.loc[me["rating"].idxmax()]
        add("Your best rating", r, f"{r.rating:.1f}", f"vs {r.opp_name}")
        r = me.loc[me["goals"].idxmax()]
        if r.goals:
            add("Your most goals", r, f"{int(r.goals)}", f"vs {r.opp_name}")
        r = me.loc[me["assists"].idxmax()]
        if r.assists:
            add("Your most assists", r, f"{int(r.assists)}", f"vs {r.opp_name}")
        r = me.loc[me["ga"].idxmax()]
        if r.ga:
            add("Your most goal contributions", r, f"{int(r.ga)}", f"{int(r.goals)} G + {int(r.assists)} A vs {r.opp_name}")
        ok = me[me["pass_attempts"] >= 15]
        if not ok.empty:
            r = ok.loc[ok["pass_pct"].idxmax()]
            add("Your best passing", r, f"{r.pass_pct:.0f}%", f"{int(r.passes_made)}/{int(r.pass_attempts)} vs {r.opp_name}")
        ok = me[me["shots"] > 0]
        if not ok.empty:
            r = ok.loc[ok["shots"].idxmax()]
            add("Your most shots", r, f"{int(r.shots)}", f"{int(r.goals)} scored vs {r.opp_name}")
    return out


def day_table(m: pd.DataFrame) -> pd.DataFrame:
    if m.empty:
        return pd.DataFrame()
    g = m.groupby("date")
    out = pd.DataFrame({"matches": g.size(), "w": g["result"].apply(lambda r: (r == "W").sum()),
                        "points": g["points"].sum()})
    out["ppg"] = out["points"] / out["matches"]
    out.index = pd.to_datetime(out.index)
    return out


# ---------------------------------------------------------------- comparing players
def compare_table(lines: pd.DataFrame, names: dict | None = None) -> pd.DataFrame:
    """player_table plus the per-match numbers the comparison needs."""
    t = player_table(lines, names)
    if t.empty:
        return t
    g = lines.groupby("player_id")
    t["rating_sd"] = g["rating"].std()
    t["shots_pm"] = t["shots"] / t["matches"]
    t["passes_pm"] = t["passes_made"] / t["matches"]
    t["tackles_pm"] = t["tackles_made"] / t["matches"]
    t["saves_pm"] = t["saves"] / t["matches"]
    t["mom_pct"] = 100.0 * t["mom"] / t["matches"]
    t["clean_sheets"] = g["clean_sheet"].sum()
    t["clean_sheet_pct"] = 100.0 * t["clean_sheets"] / t["matches"]
    t["red_p10"] = 10.0 * t["red_cards"] / t["matches"]
    t["left_pct"] = 100.0 * t["left_early"] / t["matches"]
    t["minutes_pm"] = g["minutes"].mean()
    return t


DUEL_METRICS = [("rating", "Higher rating"), ("ga", "More goals + assists"), ("goals", "More goals"),
                ("assists", "More assists"), ("shots", "More shots"), ("passes_made", "More passes"),
                ("pass_pct_ok", "Better pass accuracy"), ("tackles_made", "More tackles won")]


def _per_match(lines: pd.DataFrame, pid) -> pd.DataFrame:
    p = lines[lines["player_id"].astype(str) == str(pid)].drop_duplicates("match_id").set_index("match_id")
    return p.assign(pass_pct_ok=p["pass_pct"].where(p["pass_attempts"] >= 5))


def duels(lines: pd.DataFrame, a, b) -> tuple[int, list[dict]]:
    """In the matches two players both played: how often each came out on top, per measurement."""
    pa, pb = _per_match(lines, a), _per_match(lines, b)
    shared = pa.index.intersection(pb.index)
    out = []
    for col, label in DUEL_METRICS:
        x, y = pa.loc[shared, col].astype(float), pb.loc[shared, col].astype(float)
        ok = x.notna() & y.notna()
        out.append({"label": label, "a": int((x[ok] > y[ok]).sum()), "tie": int((x[ok] == y[ok]).sum()),
                    "b": int((x[ok] < y[ok]).sum()), "n": int(ok.sum())})
    return len(shared), out


def duel_matrix(lines: pd.DataFrame, ids: list, col: str = "rating") -> tuple[pd.DataFrame, pd.DataFrame]:
    """share[a][b] = % of their shared matches where a beat b on col (a tie counts half); n[a][b] = shared matches."""
    ids = [str(i) for i in ids]
    share = pd.DataFrame(np.nan, index=ids, columns=ids)
    n = pd.DataFrame(0, index=ids, columns=ids)
    per = {i: _per_match(lines, i) for i in ids}
    for a in ids:
        for b in ids:
            if a == b:
                continue
            shared = per[a].index.intersection(per[b].index)
            x, y = per[a].loc[shared, col].astype(float), per[b].loc[shared, col].astype(float)
            ok = x.notna() & y.notna()
            k = int(ok.sum())
            n.at[a, b] = k
            if k:
                share.at[a, b] = 100.0 * ((x[ok] > y[ok]).sum() + 0.5 * (x[ok] == y[ok]).sum()) / k
    return share, n


# ---------------------------------------------------------------- strength, luck and the clubs you've played
ELO_SCALE = 400.0   # like chess ratings: 400 SR more means about 10 to 1


def expected_points(sr_diff, draw_rate: float = 0.1):
    """Points a match the skill ratings predict (sr_diff = their SR minus yours): the stronger club usually comes out
    on top, chess-style, and a share of matches are draws."""
    p = 1.0 / (1.0 + np.power(10.0, np.asarray(sr_diff, dtype=float) / ELO_SCALE))
    return 3.0 * (1.0 - draw_rate) * p + draw_rate


def luck(m: pd.DataFrame) -> pd.DataFrame:
    """Scouted matches in order with actual and predicted points, each added up (the 'luck meter')."""
    k = m.dropna(subset=["sr_diff"]).sort_values("game_no") if "sr_diff" in m else m.iloc[0:0]
    if k.empty:
        return pd.DataFrame()
    draws = float((m["result"] == "D").mean()) if len(m) else 0.1
    exp = expected_points(k["sr_diff"], min(max(draws, 0.05), 0.3))
    return pd.DataFrame({"game_no": k["game_no"].values, "match_id": k["match_id"].values,
                         "opp_name": k["opp_name"].fillna("").values, "sr_diff": k["sr_diff"].values,
                         "points": k["points"].values, "expected": exp,
                         "points_cum": k["points"].cumsum().values, "expected_cum": np.cumsum(exp)})


LOBBY_STATS = [   # (label, column, decimals, higher is better, suffix)
    ("Goals a match", "gf", 2, True, ""), ("Conceded a match", "ga", 2, False, ""),
    ("Shots per player", "shots", 1, True, ""), ("Pass accuracy", "pass_pct", 0, True, "%"),
    ("Tackles won per player", "tackles", 1, True, ""), ("Team grade", "grade", 0, True, ""),
]


def lobby(m: pd.DataFrame, games: pd.DataFrame, lines: pd.DataFrame, own_club=None) -> tuple[pd.DataFrame, int]:
    """Your averages next to the clubs you've played (from their own last games): for each number, the share of
    those clubs you're better than. Shots and tackles are per human player (EA only counts the humans, and clubs
    bring different numbers of them); the team grade leaves out the opponent-strength part (their opponents' skill
    rating isn't known). Returns (table, number of clubs)."""
    if m.empty or games is None or games.empty or lines is None or lines.empty:
        return pd.DataFrame(), 0
    g = games[games["persp"].astype(str) != str(own_club)]
    ln = lines[lines["persp"].astype(str) != str(own_club)]
    per_game = ln.groupby(["persp", "match_id"]).agg(shots=("shots", "sum"), passes_made=("passes_made", "sum"),
                                                      pass_attempts=("pass_attempts", "sum"),
                                                      tackles_made=("tackles_made", "sum"), humans=("name", "size"))
    per_game = per_game.reset_index()
    per_game["shots"] = per_game["shots"] / per_game["humans"]
    per_game["tackles_made"] = per_game["tackles_made"] / per_game["humans"]
    tot = per_game.groupby("persp").agg(shots=("shots", "mean"), tackles=("tackles_made", "mean"),
                                        pm=("passes_made", "sum"), pa=("pass_attempts", "sum"))
    clubs = g.groupby("persp").agg(gf=("goals_for", "mean"), ga=("goals_against", "mean"),
                                   grade=("team_grade_score", "mean"), n=("match_id", "nunique"))
    clubs = clubs.join(tot, how="left")
    clubs["pass_pct"] = 100.0 * clubs["pm"] / clubs["pa"].where(clubs["pa"] > 0)
    clubs = clubs[clubs["n"] >= 3]
    humans = m["our_players"].where(m["our_players"] > 0) if "our_players" in m else np.nan
    grade = m["team_grade_score"] - m["team_str_adj"].fillna(0) if "team_str_adj" in m else m.get("team_grade_score")
    ours = {"gf": m["goals_for"].mean(), "ga": m["goals_against"].mean(),
            "shots": (m["us_shots"] / humans).mean() if "us_shots" in m else np.nan,
            "tackles": (m["us_tackles_made"] / humans).mean() if "us_tackles_made" in m else np.nan,
            "pass_pct": (100.0 * m["us_passes_made"].sum() / m["us_pass_attempts"].sum()
                         if "us_pass_attempts" in m and m["us_pass_attempts"].sum() else np.nan),
            "grade": grade.mean() if grade is not None else np.nan}
    rows = []
    for label, col, dec, higher, suf in LOBBY_STATS:
        theirs = clubs[col].dropna()
        mine = ours.get(col)
        if theirs.empty or mine is None or pd.isna(mine):
            continue
        better = (theirs < mine).sum() if higher else (theirs > mine).sum()
        pct = 100.0 * (better + 0.5 * (theirs == mine).sum()) / len(theirs)
        rows.append({"label": label, "ours": mine, "median": theirs.median(), "pct": pct, "clubs": len(theirs),
                     "dec": dec, "suffix": suf, "higher": higher})
    return pd.DataFrame(rows), int(len(clubs))


def session_sr(sess: pd.DataFrame, snaps: pd.DataFrame) -> pd.DataFrame:
    """Skill rating before and after each session (from the club snapshots), oldest first."""
    if sess.empty or snaps is None or snaps.empty or "skill_rating" not in snaps:
        return pd.DataFrame()
    s = sess.sort_values("start").reset_index(drop=True)
    sn = snaps.dropna(subset=["skill_rating"]).sort_values("at")
    rows = []
    for i, r in s.iterrows():
        before = sn[sn["at"] < r["start"]]
        nxt = s["start"].iloc[i + 1] if i + 1 < len(s) else None
        after = sn[(sn["at"] > r["end"]) & ((sn["at"] < nxt) if nxt is not None else True)]
        if before.empty or after.empty:
            continue
        a, b = float(before["skill_rating"].iloc[-1]), float(after["skill_rating"].iloc[-1])
        rows.append({"start": r["start"], "end": r["end"], "matches": int(r["matches"]), "w": int(r["w"]),
                     "d": int(r["d"]), "l": int(r["l"]), "sr_before": a, "sr_after": b, "change": b - a})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- skill rating and division, step by step
SNAP_BY = ("match", "session", "day", "time")


def snap_by(snaps: pd.DataFrame, m: pd.DataFrame, col: str = "skill_rating", by: str = "match",
            gap_minutes: float = 45, fill: str = "interpolate") -> pd.DataFrame:
    """A number from the club snapshots (skill rating, division) after each match, session or day: the last value
    the collector saw before the next one began. 'time' gives every check instead. Where the collector saw nothing
    in between, the value is filled in from the known ones around it (fill: 'interpolate' or 'ffill') and marked
    estimated. Columns: x, label, info, result, value, change (from the one before), estimated."""
    if snaps is None or snaps.empty or col not in snaps:
        return pd.DataFrame()
    sn = snaps[["at", col]].copy()
    sn[col] = pd.to_numeric(sn[col], errors="coerce")
    sn = sn.dropna(subset=[col]).sort_values("at")
    if sn.empty:
        return pd.DataFrame()
    if by == "time":
        out = pd.DataFrame({"x": sn["at"].values, "label": sn["at"].dt.strftime("%a %d %b, %H:%M").values,
                            "info": sn["at"].dt.strftime("%a %d %b, %H:%M").values, "result": None,
                            "value": sn[col].values, "estimated": False})
        out["change"] = out["value"].diff()
        return out
    mm = m.sort_values("when") if m is not None and not m.empty else pd.DataFrame()
    if mm.empty:
        return pd.DataFrame()

    def rec(g):
        w, d, lo = (g["result"] == "W").sum(), (g["result"] == "D").sum(), (g["result"] == "L").sum()
        return f"{len(g)} {'match' if len(g) == 1 else 'matches'}: {w}-{d}-{lo}"

    groups = []
    if by == "match":
        for _, r in mm.iterrows():
            opp = r.get("opp_name") if isinstance(r.get("opp_name"), str) else "?"
            groups.append((r["when"], {"x": r["game_no"], "label": f"Match {r['game_no']}", "result": r["result"],
                                       "info": f"Match {r['game_no']}: {opp} {int(r['goals_for'])}-{int(r['goals_against'])} "
                                               f"({r['result']}) · {r['when']:%a %d %b, %H:%M}"}))
    elif by == "session":
        sess = sessions(mm, gap_minutes)
        for _, g in sess.groupby("session"):
            t = g["when"].min()
            groups.append((t, {"x": t, "label": f"{t:%a %d %b, %H:%M}", "result": None,
                               "info": f"Session {t:%a %d %b, %H:%M} · {rec(g)}"}))
    elif by == "day":
        for day, g in mm.groupby(mm["when"].dt.normalize()):
            groups.append((g["when"].min(), {"x": day, "label": f"{day:%a %d %b}", "result": None,
                                             "info": f"{day:%a %d %b} · {rec(g)}"}))
    else:
        raise ValueError(f"by must be one of {SNAP_BY}")
    groups.sort(key=lambda g: g[0])
    starts = [t for t, _ in groups]
    vals = []
    for i, t in enumerate(starts):
        nxt = starts[i + 1] if i + 1 < len(starts) else None
        w = sn[(sn["at"] >= t) & ((sn["at"] < nxt) if nxt is not None else True)]
        vals.append(float(w[col].iloc[-1]) if not w.empty else np.nan)
    out = pd.DataFrame([g for _, g in groups])
    out["value"] = vals
    out["estimated"] = out["value"].isna()
    out["value"] = out["value"].interpolate(limit_area="inside") if fill == "interpolate" else out["value"].ffill()
    before = sn[sn["at"] < starts[0]]
    prev = out["value"].shift(1)
    prev.iloc[0] = float(before[col].iloc[-1]) if not before.empty else np.nan
    out["change"] = out["value"] - prev
    out.attrs["start"] = prev.iloc[0]   # the value before the first one (when the collector saw it)
    return out
