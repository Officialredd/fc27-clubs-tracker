"""Grades: a letter for every player in every match, for each team in every match, and an overall grade
per player. Unlike the performance score (which compares a player with their own other matches), grades are
absolute, so they can be compared between players, teams and matches.

Player match grade
- Five parts, each 0-100 on a fixed scale (the same for everyone):
  Rating     EA's match rating: 5.0 -> 0, 7.5 -> 50, 10 -> 100.
  Attack     goals (70%; it takes more to impress as a forward than as a defender, and every extra goal counts a
             bit less than the one before) and conversion, the share of shots scored, from 2 shots (30%).
  Creating   assists (45%), pass accuracy (35%, from 5 passes: 60% -> 0, 95% -> 100) and passes made (20%).
  Defending  tackles won and tackle success, and (more so for defenders) goals conceded.
  Goalkeeping (keepers only) saves and the share of shots on target saved.
  Result     the win, draw or loss (70%) and the goal difference (30%).
- The parts count differently per position, the position EA lists for that match (WEIGHTS): a forward is judged
  most on attack, a midfielder on creating and defending, a defender on defending and a keeper on goalkeeping.
  Man of the match adds 8, a red card takes 10 off. A stronger opponent (higher skill rating than your club,
  from scouting) adds up to 8 (3 per 100 SR), a weaker one takes the same off.
- That raw number is ranked against real FC 27 Clubs performances by players in the same position
  (CALIBRATION, from matches sampled from EA in November 2026): the grade score 0-100 is the share of those
  performances it beats, and the letter follows from it: S 90+, A 75+, B 60+, C 40+ (the middle), D 25+,
  E 10+, F under 10.

Team match grade
- Result 35%, team match rating 25%, attack 15% (goals and shots), defence 15% (goals conceded) and passing
  10%, plus a bonus for playing with fewer humans than the other club (or a deduction for more) and for
  playing a club with a higher skill rating (4 per 100 SR, up to 10; a lower one takes it off).
- Ranked the same way against real FC 27 Clubs team performances.

Overall player grade
- The average of the player's match grade scores, pulled towards the middle (50) while they have only a few
  matches: as if they also had 4 average matches. With more matches their own level takes over.
"""
import numpy as np
import pandas as pd

# S is the top 10%, C the middle, F the bottom 10% (a grade score is the share of real performances beaten)
LETTERS = [(90, "S"), (75, "A"), (60, "B"), (40, "C"), (25, "D"), (10, "E"), (0, "F")]
SHRINK = 4          # overall grade: as if every player also had this many average (50) matches
# Opponent strength: points added to the raw score per point of skill rating (SR) the other club has above
# the player's own club (taken off when it's below), up to a cap. EA's SR decides the matchups, so it's the
# best measure of how strong a club is (from scouting: clubs/scout.py).
STR_PLAYER, STR_PLAYER_MAX = 0.03, 8.0      # 100 SR stronger: +3, at most +-8
STR_TEAM, STR_TEAM_MAX = 0.04, 10.0         # 100 SR stronger: +4, at most +-10
POSITIONS = ("goalkeeper", "defender", "midfielder", "forward")

K_GOALS = {"forward": 1.3, "midfielder": 0.9, "defender": 0.5, "goalkeeper": 0.5}
K_ASSISTS = {"forward": 0.8, "midfielder": 0.9, "defender": 0.6, "goalkeeper": 0.5}
K_TACKLES = {"forward": 1.5, "midfielder": 2.0, "defender": 2.5, "goalkeeper": 1.0}
# How much each part counts, by the position the player played in that match (EA's pos for the match)
WEIGHTS = {
    "forward": {"Rating": .35, "Attack": .35, "Creating": .10, "Defending": .05, "Result": .15},
    "midfielder": {"Rating": .35, "Attack": .10, "Creating": .30, "Defending": .15, "Result": .10},
    "defender": {"Rating": .35, "Attack": .05, "Creating": .15, "Defending": .35, "Result": .10},
    "goalkeeper": {"Rating": .35, "Goalkeeping": .35, "Defending": .15, "Creating": .05, "Result": .10},
}
PARTS = ["Rating", "Attack", "Creating", "Defending", "Goalkeeping", "Result"]
PART_HELP = {
    "Rating": "EA's match rating: 5.0 is 0, 7.5 is 50, 10 is 100.",
    "Attack": "Goals (70%) and conversion, the share of shots scored, from 2 shots (30%). "
              "A goal counts for more from a defender than from a forward.",
    "Creating": "Assists, pass accuracy (from 5 passes) and passes made.",
    "Defending": "Tackles won, tackle success and (mostly for defenders and keepers) goals conceded.",
    "Goalkeeping": "Saves and the share of shots on target saved.",
    "Result": "Win, draw or loss, and the goal difference.",
}
TEAM_PARTS = ["Result", "Team rating", "Attack", "Defence", "Passing"]
TEAM_HELP = {
    "Result": "Win, draw or loss (60%) and the goal difference (40%).",
    "Team rating": "The average EA match rating of the club's human players.",
    "Attack": "Goals scored (70%) and shots (30%).",
    "Defence": "Goals conceded: 0 is 100, every goal takes 18 off.",
    "Passing": "The team's pass accuracy (from 10 passes): 60% is 0, 95% is 100.",
}

# Raw score -> percentile among real FC 27 Clubs performances: the raw score at 0, 5, 10, ... 100 %.
# From 115 FC 27 Clubs league matches between about 40 clubs (common-gen5, sampled from EA on 22 Nov 2026,
# matches where a club quit left out): 1,376 player performances (70 GK, 234 DEF, 681 MID, 391 FWD, players
# who left early left out) and 230 team performances.
CALIBRATION_SAMPLE = {"goalkeeper": 70, "defender": 234, "midfielder": 681, "forward": 391, "team": 230}
CALIBRATION = {
    "goalkeeper": [9.18, 12.55, 19.37, 22.41, 24, 26.25, 29.29, 33.21, 37.9, 42.76, 45.94, 48.17, 49.83, 51.02, 54.61, 56.74, 58.33, 60.8, 65.3, 72.83, 77],
    "defender": [15.96, 23.31, 26.43, 29.42, 32, 34.23, 37.82, 40.62, 41.72, 42.95, 44.62, 45.35, 46.73, 47.91, 50.12, 52.1, 54, 57.23, 58.38, 63.74, 86.29],
    "midfielder": [0.38, 19.23, 23.76, 27.41, 29.4, 32.58, 34.69, 36.56, 38.39, 40.63, 42.32, 44.53, 46.43, 48.84, 51.18, 53.84, 56.98, 59.15, 64.26, 72.41, 99.91],
    "forward": [0, 12.52, 14.91, 17.68, 19.5, 22.53, 25.52, 29.51, 33.74, 35.98, 39.19, 42.51, 45.15, 49.1, 52.15, 56.07, 61.96, 68.2, 74.37, 85.68, 100],
    "team": [3.36, 19.47, 22.8, 28.34, 31.66, 34.72, 37.5, 39.44, 42.6, 46.9, 51.61, 55.62, 58.53, 61.51, 63.81, 65.81, 69.71, 73.06, 77.02, 80.65, 97.59],
}


def letter(v) -> str | None:
    if v is None or pd.isna(v):
        return None
    return next(g for t, g in LETTERS if v >= t)


def _sat(x, k):
    x = np.maximum(np.asarray(x, dtype=float), 0)
    return 100.0 * (1.0 - np.exp(-x / k))


def _lin(x, lo, hi):
    return np.clip((np.asarray(x, dtype=float) - lo) / (hi - lo) * 100.0, 0, 100)


def _num(df, col):
    return pd.to_numeric(df[col], errors="coerce").fillna(0).astype(float).to_numpy()


def strength_adj(df, per: float, cap: float) -> np.ndarray:
    """Raw points for the other club's strength: df["sr_gap"] = their SR minus the own club's (0 if unknown)."""
    if "sr_gap" not in df:
        return np.zeros(len(df))
    return np.clip(per * _num(df, "sr_gap"), -cap, cap)


def player_parts(lines: pd.DataFrame) -> pd.DataFrame:
    """The 0-100 parts and the raw match score of every player line. Needs the line's pos, rating, goals,
    assists, shots, passes_made, pass_attempts, tackles_made, tackle_attempts, saves, mom, red_cards and,
    from its own club's point of view, gf, ga and res ('W'/'D'/'L')."""
    pos = lines["pos"].where(lines["pos"].isin(POSITIONS), "midfielder").to_numpy()
    rating = pd.to_numeric(lines["rating"], errors="coerce").to_numpy(dtype=float)
    goals, assists, shots = _num(lines, "goals"), _num(lines, "assists"), _num(lines, "shots")
    pm, pa = _num(lines, "passes_made"), _num(lines, "pass_attempts")
    tm, ta = _num(lines, "tackles_made"), _num(lines, "tackle_attempts")
    saves, gf, ga = _num(lines, "saves"), _num(lines, "gf"), _num(lines, "ga")
    mom, red = _num(lines, "mom"), _num(lines, "red_cards")
    res = lines["res"].map({"W": 100.0, "D": 50.0, "L": 0.0}).fillna(50.0).to_numpy()

    kg = np.array([K_GOALS[p] for p in pos])
    ka = np.array([K_ASSISTS[p] for p in pos])
    kt = np.array([K_TACKLES[p] for p in pos])
    out = pd.DataFrame(index=lines.index)
    out["Rating"] = np.where(np.isnan(rating), np.nan, _lin(rating, 5, 10))
    conv = np.where(shots >= 2, _lin(np.divide(goals, shots, out=np.zeros_like(goals), where=shots > 0), 0.10, 0.60), 50.0)
    out["Attack"] = 0.7 * _sat(goals, kg) + 0.3 * conv
    acc = np.where(pa >= 5, _lin(np.divide(pm, pa, out=np.zeros_like(pm), where=pa > 0), 0.60, 0.95), 50.0)
    out["Creating"] = 0.45 * _sat(assists, ka) + 0.35 * acc + 0.20 * _sat(pm, 15)
    tk = _sat(tm, kt)
    tsucc = np.where(ta >= 2, _lin(np.divide(tm, ta, out=np.zeros_like(tm), where=ta > 0), 0.10, 0.60), 50.0)
    conc = np.clip(100 - 22 * ga, 0, 100)
    out["Defending"] = np.select([pos == "defender", pos == "goalkeeper"],
                                 [0.35 * tk + 0.15 * tsucc + 0.50 * conc, conc],
                                 0.55 * tk + 0.25 * tsucc + 0.20 * conc)
    faced = saves + ga
    save_pct = np.divide(saves, faced, out=np.ones_like(saves), where=faced > 0)
    out["Goalkeeping"] = np.where(pos == "goalkeeper", 0.5 * _sat(saves, 3) + 0.5 * _lin(save_pct, 0.40, 0.90), np.nan)
    out["Result"] = 0.7 * res + 0.3 * np.clip(50 + 15 * (gf - ga), 0, 100)
    raw = np.zeros(len(lines))
    for p, w in WEIGHTS.items():
        sel = pos == p
        for part, wt in w.items():
            v = out[part].to_numpy(dtype=float)
            raw = np.where(sel, raw + wt * np.nan_to_num(v, nan=50.0), raw)
    out["str_adj"] = strength_adj(lines, STR_PLAYER, STR_PLAYER_MAX)
    out["raw"] = np.clip(raw + 8 * (mom > 0) - 10 * (red > 0) + out["str_adj"].to_numpy(), 0, 100)
    out.loc[np.isnan(rating), "raw"] = np.nan
    return out


def team_parts(t: pd.DataFrame) -> pd.DataFrame:
    """The 0-100 parts and raw score of a team in a match. Needs gf, ga, res, humans, opp_humans, shots,
    passes_made, pass_attempts and rating (the average match rating of its humans)."""
    gf, ga = _num(t, "gf"), _num(t, "ga")
    res = t["res"].map({"W": 100.0, "D": 50.0, "L": 0.0}).fillna(50.0).to_numpy()
    pm, pa, shots = _num(t, "passes_made"), _num(t, "pass_attempts"), _num(t, "shots")
    rating = pd.to_numeric(t["rating"], errors="coerce").to_numpy(dtype=float)
    out = pd.DataFrame(index=t.index)
    out["Result"] = 0.6 * res + 0.4 * np.clip(50 + 12 * (gf - ga), 0, 100)
    out["Team rating"] = np.where(np.isnan(rating), 50.0, _lin(rating, 5, 10))
    out["Attack"] = 0.7 * _sat(gf, 2.0) + 0.3 * _sat(shots, 8)
    out["Defence"] = np.clip(100 - 18 * ga, 0, 100)
    out["Passing"] = np.where(pa >= 10, _lin(np.divide(pm, pa, out=np.zeros_like(pm), where=pa > 0), 0.60, 0.95), 50.0)
    humans = np.clip(4 * (_num(t, "opp_humans") - _num(t, "humans")), -12, 12)
    strength = strength_adj(t, STR_TEAM, STR_TEAM_MAX)
    raw = (0.35 * out["Result"] + 0.25 * out["Team rating"] + 0.15 * out["Attack"] + 0.15 * out["Defence"]
           + 0.10 * out["Passing"]).to_numpy() + humans + strength
    out["humans_adj"] = humans
    out["str_adj"] = strength
    out["raw"] = np.clip(raw, 0, 100)
    return out


def to_score(raw, key: str):
    """Raw score -> grade score (0-100): the share of real FC 27 Clubs performances it beats."""
    raw = np.asarray(raw, dtype=float)
    q = (CALIBRATION or {}).get(key) or (CALIBRATION or {}).get("midfielder")
    if not q:
        return raw   # not calibrated: the raw score is the grade score
    return np.where(np.isnan(raw), np.nan, np.interp(raw, q, np.linspace(0, 100, len(q))))


def side_view(lines: pd.DataFrame) -> pd.DataFrame:
    """Add gf, ga and res from each line's own club's point of view (lines carry our club's numbers)."""
    us = lines["side"] == "us"
    out = lines.copy()
    out["gf"] = np.where(us, lines["goals_for"], lines["goals_against"])
    out["ga"] = np.where(us, lines["goals_against"], lines["goals_for"])
    out["res"] = lines["result"].where(us, lines["result"].map({"W": "L", "L": "W", "D": "D"}))
    return out


def grade_lines(lines: pd.DataFrame) -> pd.DataFrame:
    """Every player line: its parts, raw score, grade score (0-100) and letter."""
    if lines.empty:
        return pd.DataFrame()
    parts = player_parts(side_view(lines))
    pos = lines["pos"].where(lines["pos"].isin(POSITIONS), "midfielder")
    score = pd.Series(np.nan, index=lines.index)
    for p in POSITIONS:
        sel = pos == p
        if sel.any():
            score[sel] = to_score(parts.loc[sel, "raw"], p)
    parts["grade_score"] = score.round(0)
    parts["grade"] = parts["grade_score"].map(letter)
    return parts


def team_table(players: pd.DataFrame, matches: pd.DataFrame) -> pd.DataFrame:
    """One row per match and side ('us', 'opp') with the team's numbers, ready for team_parts."""
    if players.empty:
        return pd.DataFrame()
    g = players.groupby(["match_id", "side"])
    t = g[["shots", "passes_made", "pass_attempts"]].sum()
    t["rating"] = g["rating"].mean()
    t["humans"] = g.size()
    t = t.reset_index()
    m = matches.set_index("match_id")
    us = t["side"] == "us"
    gf = m.loc[t["match_id"], "goals_for"].to_numpy()
    ga = m.loc[t["match_id"], "goals_against"].to_numpy()
    res = m.loc[t["match_id"], "result"].to_numpy()
    t["gf"] = np.where(us, gf, ga)
    t["ga"] = np.where(us, ga, gf)
    t["res"] = np.where(us, res, pd.Series(res).map({"W": "L", "L": "W", "D": "D"}).to_numpy())
    if "sr_diff" in m:   # their SR minus ours, from our side: the other side sees it the other way round
        diff = pd.to_numeric(m.loc[t["match_id"], "sr_diff"], errors="coerce").to_numpy(dtype=float)
        t["sr_gap"] = np.where(us, diff, -diff)
    hum = t.pivot_table(index="match_id", columns="side", values="humans", aggfunc="first")
    t["opp_humans"] = [hum.loc[mid, "opp" if s == "us" else "us"] if ("opp" if s == "us" else "us") in hum.columns
                       else np.nan for mid, s in zip(t["match_id"], t["side"])]
    return t


def grade_teams(players: pd.DataFrame, matches: pd.DataFrame) -> pd.DataFrame:
    """Per match: our team's and the other club's grade (score and letter) and our parts."""
    t = team_table(players, matches)
    if t.empty:
        return pd.DataFrame()
    parts = team_parts(t)
    t = t.join(parts)
    t["grade_score"] = np.round(to_score(t["raw"], "team"), 0)
    t["grade"] = t["grade_score"].map(letter)
    ours = t[t["side"] == "us"].set_index("match_id")
    opp = t[t["side"] == "opp"].set_index("match_id")
    out = ours[["grade_score", "grade", "raw", "humans_adj", "str_adj"] + TEAM_PARTS].add_prefix("team_")
    out = out.join(opp[["grade_score", "grade"]].add_prefix("opp_team_"), how="left")
    return out


def overall(scores: pd.Series) -> float:
    """A player's overall grade score: their average match grade score, pulled towards 50 with few matches."""
    s = pd.to_numeric(scores, errors="coerce").dropna()
    if s.empty:
        return np.nan
    return float((s.sum() + SHRINK * 50.0) / (len(s) + SHRINK))
