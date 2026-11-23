"""Performance score: one number (0-100) for how well a player played a match, compared with their own
other matches.

How it works
- Each match gives a set of measurements: EA's match rating, goals, shots, conversion, assists, passes,
  pass accuracy, tackles won, tackle success, saves, goals conceded, clean sheet, the result, goal
  difference and man of the match. Rates (pass accuracy, conversion, tackle success) only count when
  there were enough attempts to mean something.
- Every measurement is ranked against the player's other matches: 50 = what they usually do,
  90 = better than 90% of them. With fewer than 5 matches it compares with all club players in the
  same position instead.
- Measurements are grouped into parts (Rating, Attack, Creating, Defending, Goalkeeping, Result), and the
  parts count differently per position: a defender is judged more on defending, a forward on attack.
- The overall number is ranked again among the player's matches, so it uses the whole 0-100 range:
  80 means the match beat 80% of their matches when everything is taken together.
"""
import numpy as np
import pandas as pd

MIN_OWN = 5   # matches needed to compare a player with themselves

# name: (part, weight, higher is better, positions it counts for (None = all), column, min attempts column, min)
METRICS = {
    "Match rating": ("Rating", 1.0, True, None, "rating", None, 0),
    "Goals": ("Attack", 1.0, True, None, "goals", None, 0),
    "Shots": ("Attack", 0.35, True, ("forward", "midfielder"), "shots", None, 0),
    "Conversion": ("Attack", 0.5, True, None, "conversion", "shots", 2),
    "Assists": ("Creating", 1.0, True, None, "assists", None, 0),
    "Passes made": ("Creating", 0.5, True, None, "passes_made", None, 0),
    "Pass accuracy": ("Creating", 0.7, True, None, "pass_pct", "pass_attempts", 5),
    "Tackles won": ("Defending", 1.0, True, ("defender", "midfielder", "forward"), "tackles_made", None, 0),
    "Tackle success": ("Defending", 0.6, True, ("defender", "midfielder", "forward"), "tackle_pct", "tackle_attempts", 2),
    "Goals conceded": ("Defending", 0.8, False, ("defender", "goalkeeper"), "conceded", None, 0),
    "Clean sheet": ("Defending", 0.6, True, ("defender", "goalkeeper"), "clean", None, 0),
    "Saves": ("Goalkeeping", 1.0, True, ("goalkeeper",), "saves", None, 0),
    "Result": ("Result", 1.0, True, None, "res_value", None, 0),
    "Goal difference": ("Result", 0.6, True, None, "gd_side", None, 0),
    "Man of the match": ("Result", 0.4, True, None, "mom", None, 0),
}
PARTS_BY_POS = {
    "forward": {"Rating": 0.30, "Attack": 0.30, "Creating": 0.15, "Defending": 0.05, "Result": 0.20},
    "midfielder": {"Rating": 0.30, "Attack": 0.15, "Creating": 0.25, "Defending": 0.10, "Result": 0.20},
    "defender": {"Rating": 0.30, "Attack": 0.05, "Creating": 0.15, "Defending": 0.30, "Result": 0.20},
    "goalkeeper": {"Rating": 0.30, "Goalkeeping": 0.30, "Defending": 0.20, "Creating": 0.05, "Result": 0.15},
}
PART_HELP = {
    "Rating": "EA's match rating.",
    "Attack": "Goals, shots and conversion (goals per shot, from 2 shots).",
    "Creating": "Assists, passes made and pass accuracy (from 5 passes).",
    "Defending": "Tackles won and tackle success; for defenders and keepers also goals conceded and clean sheets.",
    "Goalkeeping": "Saves.",
    "Result": "Win, draw or loss, the goal difference and man of the match.",
}
GRADES = [(90, "Superb"), (75, "Great"), (60, "Good"), (40, "Average"), (25, "Below par"), (0, "Poor")]


def grade(v) -> str | None:
    if v is None or pd.isna(v):
        return None
    return next(g for t, g in GRADES if v >= t)


def _pct(pool: np.ndarray, x: float, higher: bool) -> float:
    pool = pool[~np.isnan(pool)]
    if len(pool) < 2 or np.isnan(x):
        return np.nan
    below, same = (pool < x).sum(), (pool == x).sum()
    p = (below + 0.5 * same) / len(pool) * 100.0
    return p if higher else 100.0 - p


def measurements(lines: pd.DataFrame) -> pd.DataFrame:
    """The measurement values per player line (NaN where a measurement doesn't apply)."""
    df = lines.copy()
    us = (df["side"] == "us") if "side" in df else pd.Series(True, index=df.index)
    gf = np.where(us, df["goals_for"], df["goals_against"]).astype(float)
    ga = np.where(us, df["goals_against"], df["goals_for"]).astype(float)
    res = df["result"].where(us, df["result"].map({"W": "L", "L": "W", "D": "D"}))
    df["res_value"] = res.map({"W": 1.0, "D": 0.5, "L": 0.0})
    df["conceded"] = ga
    df["gd_side"] = gf - ga
    df["clean"] = (ga == 0).astype(float)
    out = pd.DataFrame(index=df.index)
    for name, (_part, _w, _hi, positions, col, need, need_min) in METRICS.items():
        v = pd.to_numeric(df[col], errors="coerce").astype(float)
        if positions:
            v = v.where(df["pos"].isin(positions))
        if need:
            v = v.where(pd.to_numeric(df[need], errors="coerce") >= need_min)
        out[name] = v
    out["pos"] = df["pos"]
    out["player_id"] = df["player_id"].astype(str)
    return out


def _rank_pct(v: pd.Series, higher: bool) -> pd.Series:
    """Mid-rank percentile of every value within its own series (NaN stays NaN)."""
    n = v.notna().sum()
    if n < 2:
        return pd.Series(np.nan, index=v.index)
    p = (v.rank(method="average") - 0.5) / n * 100.0
    return p if higher else 100.0 - p


def _pct_in(ref: np.ndarray, x: np.ndarray, higher: bool) -> np.ndarray:
    """Mid-rank percentile of each x within ref."""
    ref = np.sort(ref[~np.isnan(ref)])
    out = np.full(len(x), np.nan)
    if len(ref) < 2:
        return out
    ok = ~np.isnan(x)
    lo = np.searchsorted(ref, x[ok], "left")
    hi = np.searchsorted(ref, x[ok], "right")
    p = (lo + 0.5 * (hi - lo)) / len(ref) * 100.0
    out[ok] = p if higher else 100.0 - p
    return out


def scores(lines: pd.DataFrame, pool_lines: pd.DataFrame | None = None) -> pd.DataFrame:
    """Score every line in `lines` (player lines with match info). Returns per line: score, grade, one column
    per part and the percentile of every measurement. pool_lines = lines to compare with (default: lines)."""
    if lines.empty:
        return pd.DataFrame()
    meas = measurements(lines)
    pool = meas if pool_lines is None else measurements(pool_lines)
    pct = pd.DataFrame(np.nan, index=meas.index, columns=list(METRICS), dtype=float)
    own_n = pool.groupby("player_id").size()
    for pid, idx in meas.groupby("player_id").groups.items():
        rows = meas.loc[idx]
        if own_n.get(pid, 0) >= MIN_OWN and pool_lines is None:
            for name, (_p, _w, hi, *_rest) in METRICS.items():
                pct.loc[idx, name] = _rank_pct(rows[name], hi).to_numpy()
            continue
        own = pool[pool["player_id"] == pid]
        for pos, pidx in rows.groupby("pos", dropna=False).groups.items():
            ref = own if len(own) >= MIN_OWN else pool[pool["pos"] == pos]
            if len(ref) < 3:
                ref = pool
            for name, (_p, _w, hi, *_rest) in METRICS.items():
                pct.loc[pidx, name] = _pct_in(ref[name].to_numpy(dtype=float),
                                              meas.loc[pidx, name].to_numpy(dtype=float), hi)
    parts = sorted({v[0] for v in METRICS.values()})
    part_val = pd.DataFrame(index=meas.index, columns=parts, dtype=float)
    for part in parts:
        names = [n for n, v in METRICS.items() if v[0] == part]
        w = np.array([METRICS[n][1] for n in names])
        vals = pct[names].to_numpy(dtype=float)
        ok = ~np.isnan(vals)
        part_val[part] = np.where(ok.any(axis=1), (np.nan_to_num(vals) * w).sum(axis=1)
                                  / np.maximum((ok * w).sum(axis=1), 1e-9), np.nan)
    num = pd.Series(0.0, index=meas.index)
    den = pd.Series(0.0, index=meas.index)
    for pos_name, weights in PARTS_BY_POS.items():
        sel = meas["pos"] == pos_name
        for part, w in weights.items():
            v = part_val.loc[sel, part]
            num[sel] += (w * v).fillna(0)
            den[sel] += w * v.notna()
    other = ~meas["pos"].isin(list(PARTS_BY_POS))
    for part, w in PARTS_BY_POS["midfielder"].items():
        v = part_val.loc[other, part]
        num[other] += (w * v).fillna(0)
        den[other] += w * v.notna()
    raw = (num / den.where(den > 0)).astype(float)
    # rank the overall number again among each player's own matches so it uses the whole range
    final = raw.copy()
    for pid, idx in meas.groupby("player_id").groups.items():
        if raw[idx].notna().sum() >= MIN_OWN:
            final[idx] = _rank_pct(raw[idx], True)
    out = part_val.copy()
    out["score"] = final.round(0)
    out["raw"] = raw
    out["grade"] = out["score"].map(grade)
    return out.join(pct.add_prefix("pct_"))


def highlights(lines_row: pd.Series, score_row: pd.Series, n_best=3, n_worst=2):
    """The measurements furthest above and below the player's usual level in one match."""
    items = []
    for name in METRICS:
        p = score_row.get("pct_" + name)
        if p is None or pd.isna(p):
            continue
        items.append((name, float(p)))
    items.sort(key=lambda x: -x[1])
    best = [i for i in items if i[1] >= 60][:n_best]
    worst = [i for i in reversed(items) if i[1] <= 35][:n_worst]
    return best, worst
