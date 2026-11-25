"""FC27 Clubs Tracker - Streamlit app.  Run with:  streamlit run app.py

Layout: a website-style top bar (logo, page links, collector status, division, skill rating) and one page
per job: Team (the start page: the club's form, last match and squad grades), You (your own numbers), Matches
(every match with its full report and opponent scouting), Stats (all charts, by topic), Squad (every player of the
club), Settings and Help.
The data comes from collector.py, which saves every match of your club from EA's Clubs API."""
import copy
import datetime as dt
import html
import importlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

APP_DIR = Path(__file__).resolve().parent


def _online_mode() -> bool:
    """The online copy (Streamlit's hosting): the repository's online-data folder is here and there's no local
    setup (config.json). CLUBS_ONLINE=1 or 0 decides it either way."""
    flag = os.environ.get("CLUBS_ONLINE")
    if flag in ("0", "1"):
        return flag == "1"
    folder = Path(os.environ.get("CLUBS_ONLINE_DIR") or (APP_DIR / "online-data"))
    return ((folder / "club.json").exists() and not (APP_DIR / "config.json").exists()
            and not os.environ.get("CLUBS_CONFIG"))


ONLINE = _online_mode()
if ONLINE:   # its own database, rebuilt from online-data
    os.environ["CLUBS_ONLINE"] = "1"   # decided once per app process: the settings set below would undo it next run
    _base = Path(tempfile.gettempdir()) / "fc27-clubs-online"
    os.environ.setdefault("CLUBS_DATA_DIR", str(_base / "data"))
    os.environ.setdefault("CLUBS_CONFIG", str(_base / "config.json"))

import clubs as _pkg  # noqa: E402

# clubs/*.py in import order (a module comes after the ones it imports)
_RELOAD_ORDER = ["config", "tz", "seasons", "playoffs", "labels", "api", "parse", "db", "grades", "score", "analytics", "scout", "sync",
                 "collector_ctl", "cloud", "updater"]


def _reload_changed_code() -> bool:
    """Streamlit reads app.py again on every run, but keeps the clubs/*.py modules it loaded when it started.
    After an update those are reloaded here (all of them, in import order), so refreshing the page is enough."""
    base = Path(_pkg.__file__).resolve().parent
    try:
        newest = max([p.stat().st_mtime for p in base.glob("*.py")] + [(base.parent / "VERSION").stat().st_mtime])
    except (OSError, ValueError):
        return False
    loaded_at = getattr(_pkg, "_loaded_at", None)
    if loaded_at is None:   # first run of this app process: the modules are as old as the process
        try:
            import psutil
            loaded_at = psutil.Process().create_time()
        except Exception:
            loaded_at = time.time()
        _pkg._loaded_at = loaded_at
    try:
        version_now = (base.parent / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        version_now = None
    loaded_version = getattr(sys.modules.get("clubs.config"), "VERSION", None)
    if newest <= loaded_at and (version_now is None or loaded_version is None or version_now == loaded_version):
        return False
    for name in _RELOAD_ORDER:
        mod = sys.modules.get("clubs." + name) or getattr(_pkg, name, None)
        if mod is not None:
            sys.modules["clubs." + name] = mod
            importlib.reload(mod)
    _pkg._loaded_at = time.time()
    return True


_CODE_RELOADED = _reload_changed_code()

from clubs import analytics, api, cloud, collector_ctl, config, db, grades, labels, parse, playoffs, score, scout, seasons, sync, tz, updater  # noqa: E402,E501

if _CODE_RELOADED:   # numbers worked out by the old code
    st.cache_data.clear()

st.set_page_config(page_title="FC27 Clubs Tracker", page_icon=":material/sports_soccer:", layout="wide",
                   initial_sidebar_state="collapsed")

def online_sync():
    """Online copy: read what the collector sent (at most every 20 seconds per app)."""
    import threading
    lock = getattr(_pkg, "_online_lock", None) or threading.Lock()
    _pkg._online_lock = lock
    if time.time() - getattr(_pkg, "_online_checked", 0) < 20:
        return
    with lock:
        if time.time() - getattr(_pkg, "_online_checked", 0) < 20:
            return
        try:
            if not getattr(_pkg, "_online_checked", 0):
                with st.spinner("Loading the club's data..."):
                    res = cloud.import_new()
            else:
                res = cloud.import_new()
        except Exception as e:
            res = {"n": 0, "errors": [f"{type(e).__name__}: {e}"], "newest": None}
        prev = getattr(_pkg, "_online_result", None) or {}
        res["newest"] = res.get("newest") or prev.get("newest")
        _pkg._online_result = res
        _pkg._online_checked = time.time()


_TZ_BEFORE = os.environ.get("TZ")
if ONLINE:
    online_sync()

CFG = config.load()
if ONLINE:   # Streamlit's servers run on UTC: show the club's own times (club.json brings the PC's time zone)
    tz.apply(CFG.get("timezone"))
    if os.environ.get("TZ") != _TZ_BEFORE:   # times worked out before were UTC
        st.cache_data.clear()
UI = config.ui()
ARCH = {str(k): v for k, v in (CFG.get("archetype_names") or {}).items() if v}
CLUB_ID = str(CFG.get("club_id") or "")
PARTS = ["Rating", "Attack", "Creating", "Defending", "Goalkeeping", "Result"]
GRADE_COLS = ["grade_score", "grade"] + [f"gp_{p}" for p in grades.PARTS] + ["str_adj"]

VOLT, VOLT2, GOLD = "#c4f23a", "#dcff6a", "#f2c94c"
GOOD, BAD, DRAW, BLUE, AVG = "#3ecf8e", "#f25c54", "#f2b84b", "#4f8ff7", "#5ad1e6"
RESULT_COLORS = {"W": GOOD, "D": DRAW, "L": BAD}
RESULT_NAME = {"W": "Win", "D": "Draw", "L": "Defeat"}
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
HOUR_ORDER = list(range(6, 24)) + list(range(0, 6))   # a gaming day runs past midnight

# ---------------------------------------------------------------- chart look
pio.templates["clubs"] = go.layout.Template(pio.templates["plotly_dark"])
pio.templates["clubs"].layout.update(
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(255,255,255,0.012)", legend=dict(bgcolor="rgba(0,0,0,0)"),
    font=dict(family="Inter, Segoe UI, sans-serif", size=13, color="#c3cec9"),
    title=dict(font=dict(family="Inter, Segoe UI, sans-serif", size=15, color="#eef3f0"), x=0, xanchor="left"),
    xaxis=dict(gridcolor="#1f2925", zerolinecolor="#33423b", linecolor="#33423b"),
    yaxis=dict(gridcolor="#1f2925", zerolinecolor="#33423b", linecolor="#33423b"),
    hoverlabel=dict(bgcolor="#141a18", bordercolor=VOLT, font=dict(family="Inter, sans-serif", size=13)),
    colorway=[VOLT, BLUE, BAD, GOOD, DRAW, AVG, "#b281f7", "#a8b3ae"],
)
pio.templates.default = "clubs"
PLOT_CONFIG = {"displaylogo": False, "modeBarButtonsToRemove": ["select2d", "lasso2d", "autoScale2d"]}
_CHART_N = [0]


def few_matches(fig, df, col="game_no", n=10):
    """With only a few matches, show room for 10 so single bars and points don't fill the whole chart."""
    if df is not None and 0 < len(df) < n:
        lo = float(df[col].min())
        fig.update_xaxes(range=[lo - 0.6, lo + n - 0.4], dtick=1)
    return fig


def chart(fig, height=340, boxed=True):
    """Show a plotly figure, in its own card unless it's already inside one."""
    fig.update_layout(height=height, margin=dict(l=8, r=30 if fig.layout.meta == "pad_right" else 8, t=48, b=8),
                      legend=dict(orientation="h", yanchor="top", y=-0.2, x=0))
    _CHART_N[0] += 1
    if boxed:
        with st.container(border=True):
            st.plotly_chart(fig, width="stretch", theme=None, key=f"chart_{_CHART_N[0]}", config=PLOT_CONFIG)
    else:
        st.plotly_chart(fig, width="stretch", theme=None, key=f"chart_{_CHART_N[0]}", config=PLOT_CONFIG)


def avg_line(fig, value, label="Average", color=AVG, fmt_=",.2f", pos="top left"):
    if value is None or pd.isna(value):
        return
    fig.add_hline(y=float(value), line_dash="dot", line_color=color, line_width=2,
                  annotation_text=f"{label} {value:{fmt_}}", annotation_position=pos, annotation_font_color=color)


def trend_trace(fig, x, y, name="Trend", color=BAD):
    slope, icept = analytics.trend(pd.Series(x), pd.Series(y))
    if slope is None:
        return
    xs = pd.Series(x, dtype=float)
    ends = np.array([xs.min(), xs.max()])
    fig.add_trace(go.Scatter(x=ends, y=slope * ends + icept, mode="lines", name=name,
                             line=dict(dash="dash", color=color, width=2), hoverinfo="skip"))


# ---------------------------------------------------------------- formatting helpers
def isna(v):
    try:
        return v is None or bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def fmt(v, d=0, suffix=""):
    if isna(v):
        return "-"
    return f"{v:,.{d}f}{suffix}"


def pct(v, d=0):
    return fmt(v, d, "%")


def esc(s):
    return html.escape("" if s is None else str(s))


def plural(n, word, many=None):
    n = int(n)
    return f"{n} {word if n == 1 else (many or word + 's')}"


def ago(when):
    if isna(when):
        return ""
    sec = (dt.datetime.now() - pd.Timestamp(when).to_pydatetime()).total_seconds()
    if sec < 90:
        return "just now"
    if sec < 3600:
        return f"{sec / 60:.0f} min ago"
    if sec < 86400:
        return f"{sec / 3600:.0f} h ago"
    if sec < 2 * 86400:
        return "yesterday"
    return f"{sec / 86400:.0f} days ago"


def when_str(when, with_day=True):
    if isna(when):
        return "-"
    t = pd.Timestamp(when)
    return t.strftime("%a %d %b, %H:%M" if with_day else "%H:%M")


def in_minutes(when):
    if isna(when):
        return ""
    sec = (pd.Timestamp(when).to_pydatetime() - dt.datetime.now()).total_seconds()
    if sec <= 30:
        return "any moment"
    return f"in {sec / 60:.0f} min" if sec < 5400 else f"at {pd.Timestamp(when):%H:%M}"


def rating_tone(r):
    if isna(r):
        return "muted"
    return "r9" if r >= 8.5 else "r7" if r >= 7 else "r6" if r >= 6 else "r5"


def arch_name(a):
    return labels.archetype(a, ARCH)


# ---------------------------------------------------------------- HTML building blocks
def kpis(items, cols=None):
    """A grid of stat tiles. items: dicts with label, value and optional sub, tone ('good'|'bad'|'volt'|'gold'),
    help (tooltip) and sub_tone."""
    tiles = []
    for it in items:
        sub = it.get("sub")
        sub_html = f'<div class="kpi-sub {it.get("sub_tone", "")}">{sub}</div>' if sub else ""
        tip = f' title="{esc(it["help"])}"' if it.get("help") else ""
        tiles.append(f'<div class="kpi {it.get("tone", "")}"{tip}><div class="kpi-label">{esc(it["label"])}'
                     + (' <span class="kpi-i">?</span>' if it.get("help") else "")
                     + f'</div><div class="kpi-value">{it["value"]}</div>{sub_html}</div>')
    style = f' style="grid-template-columns: repeat({cols}, minmax(0, 1fr));"' if cols else ""
    st.html(f'<div class="kpis"{style}>{"".join(tiles)}</div>')


def chip(text, tone=""):
    return f'<span class="chip {tone}">{esc(text)}</span>'


def page_head(title, subtitle=None, eyebrow=None):
    st.html('<div class="page-head">' + (f'<div class="eyebrow">{esc(eyebrow)}</div>' if eyebrow else "")
            + f'<h1 class="page-title">{esc(title)}</h1>'
            + (f'<div class="page-sub">{subtitle}</div>' if subtitle else "") + '</div>')


def section(title, caption=None):
    st.html(f'<div class="section"><div class="section-title">{esc(title)}</div>'
            + (f'<div class="section-cap">{caption}</div>' if caption else "") + '</div>')


def empty_state(icon, title, text):
    st.html(f'<div class="empty"><div class="empty-icon">{icon}</div><div class="empty-title">{esc(title)}</div>'
            f'<div class="empty-text">{text}</div></div>')


def flash(msg, icon="✅"):
    """A toast shown after the next rerun."""
    st.session_state["_flash"] = (msg, icon)


def crest(url, size=40, cls="crest"):
    if not url:
        return f'<div class="{cls} crest-none" style="width:{size}px;height:{size}px"></div>'
    return (f'<img class="{cls}" src="{esc(url)}" width="{size}" height="{size}" loading="lazy" alt="" '
            f'onerror="this.style.visibility=\'hidden\'">')


def result_pill(res):
    return f'<span class="res res-{esc(res)}">{esc(res or "-")}</span>'


def form_dots(results, n=10):
    res = list(results)[-n:]
    return '<div class="form">' + "".join(f'<span class="res res-{r}" title="{RESULT_NAME.get(r, r)}">{r}</span>'
                                          for r in res) + "</div>"


def score_badge(v, label="SCORE"):
    if isna(v):
        return ""
    c = _score_color(v)
    return (f'<span class="score-badge" style="color:{c};border-color:{c}66;background:{c}14" '
            f'title="Performance score: {int(v)} of 100 ({score.grade(v)}) compared with your other matches">'
            f'<small>{label}</small><b>{int(v)}</b></span>')


def _score_color(v):
    if isna(v):
        return "#93a39c"
    return GOOD if v >= 75 else VOLT if v >= 60 else DRAW if v >= 40 else "#f2884b" if v >= 25 else BAD


GRADE_S = "#c08cff"


def grade_color(v):
    """S violet, A green, B volt, C amber, D orange, E red-orange, F red."""
    if isna(v):
        return "#93a39c"
    return (GRADE_S if v >= 90 else GOOD if v >= 75 else VOLT if v >= 60 else DRAW if v >= 40 else
            "#f2924b" if v >= 25 else "#f26f4c" if v >= 10 else "#e8453c")


def grade_badge(v, label=None, big=False, title=None):
    if isna(v):
        return ""
    c, L = grade_color(v), grades.letter(v)
    tip = title or f"Grade {L} ({int(v)} of 100): better than {int(v)}% of FC 27 Clubs performances"
    return (f'<span class="gbadge{" big" if big else ""}" style="color:{c};border-color:{c}66;background:{c}14" '
            f'title="{esc(tip)}">' + (f'<small>{esc(label)}</small>' if label else "")
            + f'<b>{L}</b><i>{int(v)}</i></span>')


GRADE_DOTS = {"S": "🟣", "A": "🟢", "B": "🟡", "C": "🟠", "D": "🟠", "E": "🔴", "F": "🔴"}


def grade_text(v):
    """'🟢 A  81' for tables that can't colour a cell."""
    if isna(v):
        return ""
    L = grades.letter(v)
    return f"{GRADE_DOTS[L]} {L}  {int(v)}"


def grade_cell(v, title=None):
    if isna(v):
        return '<span class="dim">-</span>'
    c = grade_color(v)
    return (f'<span class="gl" style="color:{c};background:{c}1f;cursor:help" title="{esc(title or f"{int(v)} of 100")}">'
            f'{grades.letter(v)}</span>')


POS_COLS = [("forward", "FWD"), ("midfielder", "MID"), ("defender", "DEF"), ("goalkeeper", "GK")]
PART_WHAT = {
    "Rating": "EA's match rating (5.0 is 0, 7.5 is 50, 10 is 100)",
    "Attack": "goals 70%, conversion 30% (share of shots scored, from 2 shots)",
    "Creating": "assists 45%, pass accuracy 35%, passes made 20%",
    "Defending": "tackles won and tackle success; for defenders and keepers mostly goals conceded",
    "Goalkeeping": "saves and the share of shots saved",
    "Result": "win/draw/loss 70%, goal difference 30%",
}


def letter_scale_html():
    """S to F with the grade number each letter starts at."""
    cells = "".join(f'<span style="color:{grade_color(t + 1)};border-color:{grade_color(t + 1)}55">'
                    f'<b>{g}</b>{t}+</span>' for t, g in grades.LETTERS[:-1])
    cells += f'<span style="color:{grade_color(0)};border-color:{grade_color(0)}55"><b>F</b>&lt;10</span>'
    return f'<div class="it-scale">{cells}</div>'


def info_icon(body, align="left", up=False):
    """A small (i): hover over it (or click it, then it stays open) to see the info box. up: opens above."""
    return (f'<span class="info-i" tabindex="0" aria-label="How this is calculated">i'
            f'<span class="info-tip {align}{" up" if up else ""}">{body}</span></span>')


def grade_info(pos=None):
    """How a player's match grade is worked out, with the weights per position (the player's own highlighted)."""
    head = "".join(f'<th class="{"on" if p == pos else ""}">{a}</th>' for p, a in POS_COLS)
    rows = []
    for part in grades.PARTS:
        cells = "".join(f'<td class="{"on" if p == pos else ""}">{int(grades.WEIGHTS[p][part] * 100)}%</td>'
                        if part in grades.WEIGHTS[p] else f'<td class="{"on" if p == pos else ""}">-</td>' for p, _ in POS_COLS)
        rows.append(f"<tr><td>{part}</td>{cells}</tr>")
    who = (f"This player played as a <b>{labels.position(pos).lower()}</b>, so the highlighted column counts. "
           if pos in grades.WEIGHTS else "")
    parts = "".join(f"<div><b>{p}</b>: {PART_WHAT[p]}</div>" for p in grades.PARTS)
    n = grades.CALIBRATION_SAMPLE
    return (f'<div class="it-h">How a match grade works</div>'
            f'<div>Five scores from 0 to 100, from the player\'s own numbers in that match. How much each counts depends on '
            f'the position played in that match: a striker is judged mostly on goals and conversion, a midfielder on '
            f'assists, passing and tackles, a defender on defending. {who}</div>'
            f'<table><tr><th>Part</th>{head}</tr>{"".join(rows)}</table>'
            f'<div style="margin-top:8px">{parts}</div>'
            f'<div class="it-note">Man of the match +8, red card -10, a stronger opponent up to +8 (3 per 100 skill '
            f'rating above your club; a weaker one takes it off). The total is then compared with real FC 27 Clubs '
            f'performances in the same position ({n["forward"]} forwards, {n["midfielder"]} midfielders, {n["defender"]} '
            f'defenders, {n["goalkeeper"]} keepers): the number is the share it beats.</div>'
            + letter_scale_html())


def team_grade_info():
    return ('<div class="it-h">How the team grade works</div>'
            '<div><b>Result</b> 35% (win/draw/loss 60%, goal difference 40%) · <b>Team rating</b> 25% (the average EA '
            'rating of the club\'s humans) · <b>Attack</b> 15% (goals 70%, shots 30%) · <b>Defence</b> 15% (no goals '
            'conceded is 100, every goal takes 18 off) · <b>Passing</b> 10% (team pass accuracy, 60% is 0, 95% is 100).</div>'
            '<p>Playing with fewer humans than the other club adds 4 per player (up to 12); more humans takes the same '
            'off. A stronger opponent adds 4 per 100 skill rating they have above you (up to 10); a weaker one takes '
            'it off.</p>'
            f'<div class="it-note">Compared with {grades.CALIBRATION_SAMPLE["team"]} real FC 27 Clubs team performances, '
            'on the same letter scale as players.</div>' + letter_scale_html())


def overall_info():
    return ('<div class="it-h">Overall grade and form</div>'
            '<div><b>Overall</b> is the average of the player\'s match grades, pulled towards the middle (50) as if they '
            'had also played 4 average matches, so one great match doesn\'t top the table. It settles as matches come in. '
            '<b>Form</b> is the plain average of the last 5 match grades.</div>'
            '<div class="it-note">Every match grade is position-based: hover the (i) next to a match grade to see how.</div>')


def grade_title(r):
    """Native tooltip text for one player's grade: the parts and what they counted for."""
    if isna(r.get("grade_score")):
        return ""
    pos = r["pos"] if r["pos"] in grades.WEIGHTS else "midfielder"
    w = grades.WEIGHTS[pos]
    lines = [f'{labels.position(pos)} grade {grades.letter(r["grade_score"])} ({int(r["grade_score"])} of 100)']
    lines += [f'{p}: {fmt(r.get(f"gp_{p}"))} x {int(w[p] * 100)}%' for p in grades.PARTS if p in w]
    if r.get("mom"):
        lines.append("Man of the match: +8")
    if r.get("red_cards"):
        lines.append("Red card: -10")
    adj = r.get("str_adj")
    if not isna(adj) and abs(adj) >= 0.5:
        lines.append(f"Opponent strength: {adj:+.0f}")
    return "\n".join(lines)


def grade_ring(v, caption="Grade"):
    c = grade_color(v)
    deg = 0 if isna(v) else 3.6 * v
    return (f'<div class="score-ring"><div class="ring" style="background:conic-gradient({c} {deg}deg, '
            f'rgba(255,255,255,0.07) {deg}deg)"><div class="ring-in"><b>{esc(grades.letter(v) or "-")}</b>'
            f'<span style="color:{c}">{fmt(v)} / 100</span></div></div>'
            f'<div class="mini" style="text-align:center">{esc(caption)}</div></div>')


def player_grades(lines):
    """Per player: overall grade (average, pulled to 50 with few matches), plain average and form (last 5)."""
    if lines.empty or "grade_score" not in lines:
        return pd.DataFrame(columns=["overall", "avg_grade", "form"])
    s = lines.sort_values("ts").groupby(lines["player_id"].astype(str))["grade_score"]
    return pd.DataFrame({"overall": s.apply(grades.overall), "avg_grade": s.mean(),
                         "form": s.apply(lambda x: x.dropna().tail(5).mean() if x.notna().any() else np.nan)})


def bar_rows(rows):
    """Horizontal 0-100 bars: rows of (label, value, help)."""
    out = []
    for label, v, tip in rows:
        if isna(v):
            out.append(f'<div class="sbar" title="{esc(tip)}"><div class="sbar-label">{esc(label)}</div>'
                       f'<div class="sbar-track"></div><div class="sbar-val muted">-</div></div>')
            continue
        c = _score_color(v)
        out.append(f'<div class="sbar" title="{esc(tip)}"><div class="sbar-label">{esc(label)}</div>'
                   f'<div class="sbar-track"><div class="sbar-fill" style="width:{max(2, v):.0f}%;background:{c}"></div>'
                   f'<div class="sbar-mid"></div></div><div class="sbar-val" style="color:{c}">{v:.0f}</div></div>')
    return '<div class="sbars">' + "".join(out) + "</div>"


# ---------------------------------------------------------------- page style
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700;800&family=Inter:wght@400;500;600;700&display=swap');
:root {
  --bg: #0a0e0d; --panel: #111715; --panel2: #17201c; --line: #222d29; --line2: #2f3d37;
  --text: #e8efec; --muted: #93a39c; --faint: #627069;
  --volt: #c4f23a; --volt2: #dcff6a; --gold: #f2c94c; --good: #3ecf8e; --bad: #f25c54; --draw: #f2b84b; --blue: #4f8ff7;
}
header[data-testid="stHeader"], [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"],
[data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stStatusWidget"] {display: none !important;}
.stApp {
  background: radial-gradient(1200px 420px at 50% -160px, rgba(196,242,58,0.08), transparent 70%),
              repeating-linear-gradient(90deg, rgba(255,255,255,0.008) 0 120px, rgba(0,0,0,0) 120px 240px), var(--bg);
}
.stApp, .stApp p, .stApp li, .stApp label, .stApp input, .stApp textarea, .stApp button, .stApp td, .stApp th {
  font-family: 'Inter', 'Segoe UI', sans-serif;
}
.block-container {padding: 76px 2.25rem 4rem 2.25rem !important; max-width: 1480px;}
h1, h2, h3, h4 {font-family: 'Barlow Condensed', 'Segoe UI', sans-serif !important; letter-spacing: .5px;}
a {color: var(--volt2);}

/* ---- top bar */
.st-key-topbar {
  position: fixed !important; top: 0; left: 0; right: 0; z-index: 999;
  width: 100vw !important; max-width: 100vw !important; margin: 0 !important; box-sizing: border-box;
  padding: 10px max(2.25rem, calc((100vw - 1480px) / 2 + 2.25rem));
  background: rgba(10,14,13,0.88); backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--line);
}
.brand {display: flex; align-items: center; gap: 10px; padding-right: 18px; margin-right: 6px;
        border-right: 1px solid var(--line); height: 40px; white-space: nowrap;}
.brand-name {font-family: 'Barlow Condensed', sans-serif; font-weight: 800; font-size: 22px; letter-spacing: 2px; color: var(--text);}
.brand-name span {color: var(--volt);}
.st-key-topbar [data-testid="stPageLink"] a, .st-key-topbar [data-testid="stPageLink-NavLink"] {
  padding: 7px 13px !important; border-radius: 8px; margin: 0 !important; background: transparent;
}
.st-key-topbar [data-testid="stPageLink"] p {
  font-family: 'Inter', sans-serif; font-weight: 600 !important; font-size: 14px; color: var(--muted) !important; white-space: nowrap;
}
.st-key-topbar [data-testid="stPageLink"] a:hover {background: rgba(255,255,255,0.06);}
.st-key-topbar [data-testid="stPageLink"] a:hover p {color: var(--text) !important;}
[class*="st-key-navon_"] [data-testid="stPageLink"] a {background: rgba(196,242,58,0.13) !important;}
[class*="st-key-navon_"] [data-testid="stPageLink"] p {color: var(--volt2) !important;}
.hud {display: flex; gap: 8px; align-items: center; white-space: nowrap;}
.hud-box {border: 1px solid var(--line2); background: var(--panel2); border-radius: 8px; padding: 4px 12px; height: 34px;
          display: flex; align-items: center; gap: 6px;
          font-family: 'Barlow Condensed', sans-serif; font-weight: 700; font-size: 19px; color: var(--text); letter-spacing: .5px;}
.hud-box small {font-size: 11px; letter-spacing: 2px; color: var(--muted); font-weight: 600; font-family: 'Inter', sans-serif;}
.hud-box.status {font-family: 'Inter', sans-serif; font-size: 12.5px; font-weight: 700; letter-spacing: 1px;}
.hud-dot {display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: var(--good);}
.hud-dot.live {background: var(--volt); box-shadow: 0 0 0 0 rgba(196,242,58,.6); animation: pulse 1.6s infinite;}
.hud-dot.off {background: var(--faint);} .hud-dot.err {background: var(--bad);}
@keyframes pulse {0% {box-shadow: 0 0 0 0 rgba(196,242,58,.6);} 70% {box-shadow: 0 0 0 8px rgba(196,242,58,0);}
                  100% {box-shadow: 0 0 0 0 rgba(196,242,58,0);}}
.hud img {border-radius: 6px;}
/* Home: the season card and the playoffs card */
.po-top {display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; flex-wrap: wrap; margin-bottom: 10px;}
.po-title {font-family: 'Barlow Condensed', sans-serif; font-weight: 800; font-size: 30px; letter-spacing: .5px;
           text-transform: uppercase; color: var(--text); line-height: 1.05;}
.po-used {font-weight: 600; color: var(--text); font-size: 14px; padding-top: 4px;}
.po-bar {height: 8px; border-radius: 6px; background: rgba(255,255,255,0.07); overflow: hidden; margin: 2px 0 12px 0;}
.po-bar > div {height: 100%; border-radius: 6px; background: linear-gradient(90deg, #a9d92b, var(--volt));}
.po-soon {margin-top: 10px; padding: 10px 12px; border-radius: 10px; border: 1px solid rgba(196,242,58,0.35);
          background: rgba(196,242,58,0.07); color: var(--text); font-size: 14px;}
.season-id {display: flex; align-items: center; gap: 12px;}
.st-key-playoffs_card {border-color: rgba(242,201,76,0.55) !important;
                       background: linear-gradient(135deg, rgba(242,201,76,0.10), rgba(242,201,76,0.02) 60%) !important;}
.st-key-playoffs_card .eyebrow {color: var(--gold);}
.st-key-playoffs_card .po-bar > div {background: linear-gradient(90deg, #d9a92e, var(--gold));}

/* the menu with the other pages: phones only (see below) */
.st-key-topbar > [data-testid="stLayoutWrapper"]:has(> [class*="st-key-navmenu"]) {display: none;}
[class*="st-key-navmenu"] [data-testid="stPopoverButton"] {height: 36px; padding: 0 12px;}
.st-key-navmenu_on [data-testid="stPopoverButton"] {background: rgba(196,242,58,0.13) !important; border-color: rgba(196,242,58,0.45) !important;}
.st-key-navmenu_on [data-testid="stPopoverButton"] p, .st-key-navmenu_on [data-testid="stPopoverButton"] span {color: var(--volt2) !important;}
[data-testid="stPopoverBody"] [data-testid="stPageLink"] a {padding: 9px 12px !important; border-radius: 8px;}
[data-testid="stPopoverBody"] [data-testid="stPageLink"] p {font-weight: 600 !important; font-size: 15px;}
.hud-menu {flex-wrap: wrap; margin-top: 4px; padding-top: 12px; border-top: 1px solid var(--line);}

/* ---- page heading */
.page-head {margin: 0.2rem 0 0.4rem 0;}
.eyebrow {font-family: 'Barlow Condensed', sans-serif; font-weight: 700; letter-spacing: 3px; text-transform: uppercase;
          color: var(--volt); font-size: 14px; margin-bottom: 2px;}
.page-title {font-family: 'Barlow Condensed', sans-serif !important; font-weight: 800 !important; font-size: 2.6rem !important;
             letter-spacing: 1px !important; color: var(--text); margin: 0 !important; padding: 0 !important; line-height: 1.05 !important;
             text-transform: uppercase;}
.page-sub {color: var(--muted); font-size: 15px; margin-top: 6px;}
.section {margin: 1.4rem 0 0.1rem 0;}
.section-title {font-family: 'Barlow Condensed', sans-serif; font-weight: 700; font-size: 1.3rem; letter-spacing: 2px;
                text-transform: uppercase; color: var(--text);}
.section-cap {color: var(--muted); font-size: 13.5px; margin-top: 2px;}

/* ---- cards */
[data-testid="stVerticalBlockBorderWrapper"], [data-testid="stVerticalBlock"][class*="border"],
div[data-testid="stExpander"] details {
  border-radius: 12px !important; border-color: var(--line) !important;
  background: linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0.01));
}
div[data-testid="stExpander"] summary p {font-weight: 600;}

/* ---- hero (club header) */
.hero {display: grid; grid-template-columns: auto 1fr auto; gap: 22px; align-items: center; padding: 18px 22px;
       border-radius: 16px; border: 1px solid rgba(196,242,58,0.28);
       background: linear-gradient(115deg, rgba(196,242,58,0.12), rgba(196,242,58,0.02) 50%, rgba(255,255,255,0.01));}
.hero .crest {border-radius: 14px; background: rgba(255,255,255,0.04);}
.hero-name {font-family: 'Barlow Condensed', sans-serif; font-weight: 800; font-size: 44px; line-height: 1; letter-spacing: 1px;
            text-transform: uppercase; color: var(--text);}
.hero-sub {color: var(--muted); font-size: 14px; margin: 6px 0 10px 0;}
.hero-stats {display: grid; grid-template-columns: repeat(5, minmax(88px, auto)); gap: 10px;}
.hstat {background: rgba(0,0,0,0.22); border: 1px solid var(--line2); border-radius: 12px; padding: 10px 14px; text-align: center;}
.hstat-label {font-size: 11px; font-weight: 600; letter-spacing: 1.6px; text-transform: uppercase; color: var(--muted);}
.hstat-value {font-family: 'Barlow Condensed', sans-serif; font-weight: 800; font-size: 30px; line-height: 1.1; color: var(--text);}
.hstat-sub {font-size: 12px; color: var(--muted);}
@media (max-width: 1200px) {.hero {grid-template-columns: auto 1fr;} .hero-stats {grid-column: 1 / -1; grid-template-columns: repeat(5, 1fr);}}

/* ---- results */
.form {display: flex; gap: 5px; flex-wrap: wrap;}
.res {display: inline-flex; width: 26px; height: 26px; border-radius: 7px; align-items: center; justify-content: center;
      font-family: 'Barlow Condensed', sans-serif; font-weight: 800; font-size: 16px; color: #0a0e0d;}
.res-W {background: var(--good);} .res-D {background: var(--draw);} .res-L {background: var(--bad);}
.res-None, .res- {background: var(--line2); color: var(--muted);}

/* ---- stat tiles */
.kpis {display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; width: 100%;}
.kpi {background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 13px 15px 11px 15px;
      position: relative; overflow: hidden;}
.kpi::before {content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px; background: var(--line2);}
.kpi.good::before {background: var(--good);} .kpi.bad::before {background: var(--bad);}
.kpi.volt::before {background: var(--volt);} .kpi.gold::before {background: var(--gold);}
.kpi-label {font-size: 11.5px; font-weight: 600; letter-spacing: 1.5px; text-transform: uppercase; color: var(--muted);}
.kpi-i {display: inline-block; width: 14px; height: 14px; line-height: 14px; text-align: center; border-radius: 50%;
        border: 1px solid var(--faint); color: var(--faint); font-size: 9px; letter-spacing: 0; margin-left: 3px; cursor: help;}
.kpi-value {font-family: 'Barlow Condensed', sans-serif; font-weight: 800; font-size: clamp(24px, 2vw, 32px); line-height: 1.1;
            color: var(--text); margin-top: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;}
.kpi.good .kpi-value {color: #7fe6b5;} .kpi.bad .kpi-value {color: #ff8f88;} .kpi.volt .kpi-value {color: var(--volt2);}
.kpi.gold .kpi-value {color: var(--gold);}
.kpi-sub {font-size: 12.5px; color: var(--muted); margin-top: 1px;}
.kpi-sub.good {color: var(--good);} .kpi-sub.bad {color: var(--bad);}

/* ---- chips */
.chip {display: inline-flex; align-items: center; gap: 5px; padding: 3px 10px; border-radius: 999px; font-size: 12.5px;
       font-weight: 600; border: 1px solid var(--line2); color: var(--text); background: var(--panel2); white-space: nowrap;}
.chip.good {color: #7fe6b5; border-color: rgba(62,207,142,.45); background: rgba(62,207,142,.10);}
.chip.bad {color: #ff8f88; border-color: rgba(242,92,84,.45); background: rgba(242,92,84,.10);}
.chip.draw {color: #ffd28a; border-color: rgba(242,184,75,.45); background: rgba(242,184,75,.10);}
.chip.volt {color: var(--volt2); border-color: rgba(196,242,58,.5); background: rgba(196,242,58,.10);}
.chip.gold {color: var(--gold); border-color: rgba(242,201,76,.5); background: rgba(242,201,76,.10);}
.chip.muted {color: var(--muted);}
.chips {display: flex; flex-wrap: wrap; gap: 6px; align-items: center;}

/* ---- match card / scoreline */
.mc-top {display: flex; justify-content: space-between; align-items: center; gap: 10px; color: var(--muted); font-size: 13.5px;}
.scoreline {display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: 16px; margin: 14px 0 6px 0;}
.team {display: flex; align-items: center; gap: 12px; min-width: 0;}
.team.right {justify-content: flex-end; text-align: right;}
.team-name {font-family: 'Barlow Condensed', sans-serif; font-weight: 700; font-size: 24px; line-height: 1.05; color: var(--text);
            overflow: hidden; text-overflow: ellipsis;}
.team-sub {color: var(--muted); font-size: 12.5px;}
.team .crest {border-radius: 8px; flex: none;}
.crest-none {border-radius: 8px; background: var(--panel2); border: 1px solid var(--line2); flex: none;}
.score {font-family: 'Barlow Condensed', sans-serif; font-weight: 800; font-size: 54px; line-height: 1; letter-spacing: 2px;
        color: var(--text); white-space: nowrap; padding: 4px 16px; border-radius: 12px; background: rgba(0,0,0,0.25);
        border: 1px solid var(--line2);}
.score span {color: var(--faint); margin: 0 6px;}
.big-result {font-family: 'Barlow Condensed', sans-serif; font-weight: 800; font-size: 20px; letter-spacing: 3px; text-transform: uppercase;
             text-align: center;}
.big-result.W {color: var(--good);} .big-result.D {color: var(--draw);} .big-result.L {color: var(--bad);}
.scorers {color: var(--muted); font-size: 13.5px; text-align: center; margin-top: 4px;}
.scorers b {color: var(--text); font-weight: 600;}
.myline {display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--line);}

/* ---- comparison bars */
.cmp {display: flex; flex-direction: column; gap: 9px;}
.cmp-row {display: grid; grid-template-columns: 64px 1fr 150px 1fr 64px; align-items: center; gap: 10px; font-size: 14px;}
.cmp-l, .cmp-r {font-family: 'Barlow Condensed', sans-serif; font-weight: 700; font-size: 19px;}
.cmp-l {text-align: right;} .cmp-label {text-align: center; color: var(--muted); font-size: 12.5px; letter-spacing: 1px; text-transform: uppercase; font-weight: 600;}
.cmp-bar {height: 9px; background: rgba(255,255,255,0.05); border-radius: 5px; overflow: hidden; display: flex;}
.cmp-bar.l {justify-content: flex-end;}
.cmp-bar div {height: 100%; border-radius: 5px;}

/* ---- lineup tables */
.tbl-wrap {overflow-x: auto; width: 100%;}
table.lineup {width: 100%; border-collapse: collapse; font-size: 13.5px;}
table.lineup th {text-align: right; font-size: 11px; letter-spacing: 1.2px; text-transform: uppercase; color: var(--muted);
                 font-weight: 600; padding: 6px 7px; border-bottom: 1px solid var(--line2); white-space: nowrap;}
table.lineup td {text-align: right; padding: 7px 6px; border-bottom: 1px solid var(--line); white-space: nowrap;}
table.lineup th:first-child, table.lineup td:first-child {text-align: left;}
table.lineup tr.me td {background: rgba(196,242,58,0.07);}
table.lineup tr.me td:first-child {box-shadow: inset 3px 0 0 var(--volt);}
.pname {font-weight: 600; color: var(--text);} .pname small {color: var(--muted); font-weight: 500;}
.pos {display: inline-block; min-width: 34px; text-align: center; padding: 1px 6px; border-radius: 5px; font-size: 11px; font-weight: 700;
      letter-spacing: .5px; color: #0a0e0d;}
.rt {display: inline-block; min-width: 38px; text-align: center; padding: 2px 6px; border-radius: 6px; font-weight: 700;
     font-family: 'Barlow Condensed', sans-serif; font-size: 16px;}
.rt.r9 {background: rgba(62,207,142,.25); color: #8ff0c2;} .rt.r7 {background: rgba(196,242,58,.16); color: var(--volt2);}
.rt.r6 {background: rgba(242,184,75,.18); color: #ffd28a;} .rt.r5 {background: rgba(242,92,84,.18); color: #ff8f88;}
.rt.muted {color: var(--muted);}
.quit-note {color: var(--muted); font-size: 12px; letter-spacing: 2px;}
.star {color: var(--gold);} .tag-left {color: var(--bad); font-size: 11px; font-weight: 600; margin-left: 4px;}
.tl {display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 7px 16px; margin-top: 12px;
      padding-top: 12px; border-top: 1px solid var(--line);}
.tl-p {display: flex; align-items: center; gap: 7px; font-size: 13.5px; min-width: 0;}
.tl-p .pname {white-space: nowrap; overflow: hidden; text-overflow: ellipsis;}
.sq-card {background: var(--panel); border: 1px solid var(--line); border-top: 3px solid var(--c); border-radius: 12px;
          padding: 12px 14px; display: flex; flex-direction: column; gap: 8px;}
.sq-top {display: flex; justify-content: space-between; align-items: flex-start; gap: 8px;}
.sq-name {font-family: 'Barlow Condensed', sans-serif; font-weight: 800; font-size: 23px; line-height: 1.05; color: var(--text);
          word-break: break-word;}
.sq-name small {font-family: 'Inter', sans-serif; font-size: 10.5px; color: var(--volt2); letter-spacing: 1px; margin-left: 6px;}
.sq-sub {color: var(--muted); font-size: 12.5px; margin-top: 3px;}
.sq-grade {font-family: 'Barlow Condensed', sans-serif; font-weight: 800; font-size: 40px; line-height: .9; text-align: right;}
.sq-grade small {display: block; font-family: 'Inter', sans-serif; font-size: 11.5px; font-weight: 600; opacity: .8;}
.sq-form {font-size: 12.5px; color: var(--muted);} .sq-form b {font-size: 15px;}
.sq-form .up {color: #7fe6b5; font-weight: 600;} .sq-form .down {color: #ff8f88; font-weight: 600;}
.sq-stats {display: grid; grid-template-columns: repeat(4, 1fr); gap: 4px; border-top: 1px solid var(--line); padding-top: 8px;}
.sq-stats div {text-align: center; font-size: 11px; color: var(--muted); letter-spacing: .5px;}
.sq-stats b {display: block; font-size: 17px; color: var(--text); font-family: 'Barlow Condensed', sans-serif;}
.tag-vs {color: var(--volt2); font-size: 10.5px; font-weight: 700; margin-left: 6px; border: 1px solid rgba(196,242,58,.45);
         border-radius: 6px; padding: 0 5px; letter-spacing: .5px;}
table.scout-games td:nth-child(2), table.scout-games th:nth-child(2) {text-align: left;}
.sc-club {display: inline-flex; align-items: center; gap: 7px;} .sc-club img, .sc-club .crest-none {flex: none;}
.dim {color: var(--faint);}

/* ---- info icon + info box */
.info-i {position: relative; display: inline-flex; align-items: center; justify-content: center; width: 17px; height: 17px;
         border-radius: 50%; border: 1px solid var(--volt); color: var(--volt); font: italic 700 11px/1 Georgia, serif;
         cursor: help; margin-left: 6px; vertical-align: 1px; text-transform: none; letter-spacing: 0; outline: none;}
.info-i:hover, .info-i:focus {background: rgba(196,242,58,0.15);}
.info-tip {display: none; position: absolute; top: 24px; left: -14px; z-index: 10000; width: 390px; padding: 13px 15px;
           background: #0f1513; border: 1px solid #3b4b44; border-radius: 12px; box-shadow: 0 14px 44px rgba(0,0,0,.65);
           color: #dfe7e3; font: normal 400 13px/1.5 'Inter', 'Segoe UI', sans-serif; text-align: left; white-space: normal;
           letter-spacing: 0; text-transform: none; cursor: default;}
.info-tip.right {left: auto; right: -14px;}
.info-tip.up {top: auto; bottom: 24px;}
.info-i:hover .info-tip, .info-i:focus .info-tip {display: block;}
.info-tip b {color: var(--volt2); font-weight: 700;}
.info-tip .it-h {font-family: 'Barlow Condensed', sans-serif; font-weight: 800; font-size: 17px; letter-spacing: 1px;
                 text-transform: uppercase; color: var(--text); margin-bottom: 4px;}
.info-tip p {margin: 6px 0 0 0;}
.info-tip table {width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 12.5px;}
.info-tip th {color: var(--muted); font-weight: 600; font-size: 11px; letter-spacing: 1px; padding: 3px 5px; text-align: right;
              border-bottom: 1px solid #2f3d37;}
.info-tip td {padding: 3px 5px; border-bottom: 1px solid #1f2925; text-align: right;}
.info-tip th:first-child, .info-tip td:first-child {text-align: left;}
.info-tip td.on, .info-tip th.on {color: #0a0e0d; background: var(--volt); font-weight: 800;}
.info-tip .it-note {color: var(--muted); font-size: 12px; margin-top: 7px;}
.it-scale {display: flex; gap: 3px; margin-top: 8px; flex-wrap: wrap;}
.it-scale span {border: 1px solid; border-radius: 7px; padding: 1px 5px; font-size: 11px; color: var(--muted); white-space: nowrap;}
.it-scale span b {font-family: 'Barlow Condensed', sans-serif; font-size: 15px; margin-right: 2px;}

/* ---- grades */
.gbadge {display: inline-flex; align-items: baseline; gap: 6px; border: 1px solid; border-radius: 10px; padding: 2px 10px;
         font-family: 'Barlow Condensed', sans-serif; font-weight: 800; line-height: 1.1; white-space: nowrap;}
.gbadge small {font-size: 11px; letter-spacing: 1.5px; opacity: .85; font-family: 'Inter', sans-serif; font-weight: 700;}
.gbadge b {font-size: 23px;} .gbadge i {font-style: normal; font-size: 14px; opacity: .75;}
.gbadge.big b {font-size: 30px;}
.gl {display: inline-block; min-width: 34px; text-align: center; padding: 1px 6px; border-radius: 6px;
     font-family: 'Barlow Condensed', sans-serif; font-weight: 800; font-size: 17px;}
.team-grade {margin-top: 6px;}
.team.right .team-grade {text-align: right;}

/* ---- score ring and bars */
.score-badge {display: inline-flex; align-items: baseline; gap: 6px; border: 1px solid; border-radius: 10px;
  padding: 2px 10px; font-family: 'Barlow Condensed', sans-serif; font-weight: 800; line-height: 1.1;}
.score-badge small {font-size: 11px; letter-spacing: 1.5px; opacity: .85; font-family: 'Inter', sans-serif; font-weight: 700;}
.score-badge b {font-size: 23px;}
.score-ring {display: flex; flex-direction: column; align-items: center; gap: 8px;}
.score-ring .ring {width: 150px; height: 150px; border-radius: 50%; display: flex; align-items: center; justify-content: center;}
.score-ring .ring-in {width: 122px; height: 122px; border-radius: 50%; background: #101614; display: flex; flex-direction: column;
  align-items: center; justify-content: center; font-family: 'Barlow Condensed', sans-serif; font-weight: 800; line-height: 1;}
.score-ring .ring-in b {font-size: 50px; color: #f1f5f3;} .score-ring .ring-in span {font-size: 16px; letter-spacing: 1.5px; margin-top: 4px;}
.sbars {display: flex; flex-direction: column; gap: 9px;}
.sbar {display: grid; grid-template-columns: 138px 1fr 34px; align-items: center; gap: 10px; cursor: default;}
.sbar-label {font-weight: 600; font-size: 14px;}
.sbar-track {position: relative; height: 12px; background: rgba(255,255,255,0.06); border-radius: 6px; overflow: hidden;}
.sbar-fill {height: 100%; border-radius: 6px;}
.sbar-mid {position: absolute; left: 50%; top: 0; bottom: 0; width: 1px; background: rgba(255,255,255,0.4);}
.sbar-val {text-align: right; font-family: 'Barlow Condensed', sans-serif; font-weight: 800; font-size: 18px;}
.sbar-val.muted {color: var(--faint);}
.hl {font-size: 13.5px; padding: 6px 0; border-bottom: 1px solid var(--line); display: flex; justify-content: space-between; gap: 10px;}
.hl:last-child {border-bottom: none;}
.hl .up {color: var(--good); font-weight: 700;} .hl .down {color: var(--bad); font-weight: 700;}

/* ---- player comparison */
.pcards {display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px;}
.pcard {background: var(--panel); border: 1px solid var(--line); border-top: 3px solid var(--c); border-radius: 12px; padding: 12px 14px;}
.pcard-name {font-family: 'Barlow Condensed', sans-serif; font-weight: 800; font-size: 24px; line-height: 1.1; color: var(--text);}
.pcard-sub {color: var(--muted); font-size: 12.5px; margin-top: 3px;}
table.cmpt {width: 100%; border-collapse: collapse; font-size: 14px;}
table.cmpt th {padding: 10px 10px; text-align: center; border-bottom: 1px solid var(--line2); white-space: nowrap;}
table.cmpt th:first-child {text-align: left;}
table.cmpt td {padding: 7px 10px; text-align: center; border-bottom: 1px solid var(--line); white-space: nowrap;}
table.cmpt td.metric {text-align: left; color: var(--muted); font-weight: 600; font-size: 13.5px;}
table.cmpt tr.grp td {font-size: 11.5px; font-weight: 700; letter-spacing: 2px; text-transform: uppercase; color: var(--volt);
                      padding-top: 16px; border-bottom: 1px solid var(--line2); text-align: left;}
table.cmpt .v {font-family: 'Barlow Condensed', sans-serif; font-weight: 700; font-size: 20px; color: #cfd8d4;
               display: inline-block; min-width: 64px; padding: 1px 10px; border-radius: 8px;}
table.cmpt .v.lead {color: #0a0e0d; font-weight: 800;}
table.cmpt .s {display: block; font-size: 11.5px; color: var(--faint); margin-top: 1px;}
.pdot {display: inline-block; width: 11px; height: 11px; border-radius: 50%; margin-right: 7px; vertical-align: 1px;}
.duel {display: grid; grid-template-columns: 190px 34px 1fr 34px; gap: 10px; align-items: center; padding: 6px 0;
       border-bottom: 1px solid var(--line); font-size: 14px;}
.duel:last-child {border-bottom: none;}
.duel-label {color: var(--muted); font-weight: 600;}
.duel-n {font-family: 'Barlow Condensed', sans-serif; font-weight: 800; font-size: 19px; text-align: center;}
.duel-bar {display: flex; height: 14px; border-radius: 7px; overflow: hidden; background: rgba(255,255,255,0.05);}
.duel-bar div {height: 100%;}
.duel-head {display: grid; grid-template-columns: 190px 1fr; gap: 10px; margin-bottom: 4px; font-size: 13px; color: var(--muted);}
.duel-head div:last-child {display: flex; justify-content: space-between; font-weight: 700;}

/* ---- records */
.records {display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 12px;}
.rec {background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px;}
.rec-label {font-size: 11.5px; font-weight: 600; letter-spacing: 1.5px; text-transform: uppercase; color: var(--muted);}
.rec-value {font-family: 'Barlow Condensed', sans-serif; font-weight: 800; font-size: 34px; color: var(--volt2); line-height: 1.1;}
.rec-sub {font-size: 13px; color: var(--muted);}

/* ---- misc */
.empty {text-align: center; padding: 42px 20px; border: 1px dashed var(--line2); border-radius: 14px; background: rgba(255,255,255,0.015);}
.empty-icon {font-size: 38px;} .empty-title {font-family: 'Barlow Condensed', sans-serif; font-weight: 800; font-size: 24px;
             letter-spacing: 1px; margin-top: 6px; text-transform: uppercase;} .empty-text {color: var(--muted); margin-top: 4px; font-size: 14.5px;}
.mini {color: var(--muted); font-size: 13px;}
.line-item {display: flex; justify-content: space-between; padding: 7px 0; border-bottom: 1px solid var(--line); font-size: 14px; gap: 10px;}
.line-item:last-child {border-bottom: none;}
.line-item span:first-child {color: var(--muted);}
.status-line {display: flex; align-items: center; gap: 10px; font-size: 14.5px; font-weight: 600;}
.log {font-family: Consolas, monospace; font-size: 12px; color: var(--muted); white-space: pre-wrap; background: rgba(0,0,0,0.25);
      border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; max-height: 260px; overflow: auto;}
.club-pick {display: flex; align-items: center; gap: 12px;}

/* ---- widgets */
.stButton button, .stDownloadButton button, [data-testid="stPopover"] button, [data-testid="stFormSubmitButton"] button {
  border-radius: 9px; font-weight: 600; border: 1px solid var(--line2); background: var(--panel2);
}
.stButton button[kind="primary"], [data-testid="stFormSubmitButton"] button[kind="primaryFormSubmit"] {
  background: linear-gradient(180deg, #d4ff56, #a9d92b); color: #0c1204; border-color: #cdf552; font-weight: 700;
}
.stButton button[kind="primary"]:hover {background: linear-gradient(180deg, #e0ff7a, #b6e33a); color: #000;}
.stButton button[kind="primary"] p, [data-testid="stFormSubmitButton"] button[kind="primaryFormSubmit"] p {color: #0c1204; font-weight: 700;}
[data-baseweb="select"] > div, [data-baseweb="input"], .stNumberInput input, .stTextInput input {border-radius: 8px !important;}
[data-testid="stDataFrame"], [data-testid="stTable"] {border: 1px solid var(--line); border-radius: 10px; overflow: hidden;}
[data-testid="stAlert"] {border-radius: 10px;}
[data-testid="stCaptionContainer"] {color: var(--muted);}
hr {border-color: var(--line);}
[data-testid="stNumberInputStepDown"], [data-testid="stNumberInputStepUp"] {display: none;}
[data-baseweb="tag"] {background: rgba(196,242,58,0.14) !important; border: 1px solid rgba(196,242,58,0.45) !important;}
[data-baseweb="tag"] span, [data-baseweb="tag"] svg {color: var(--volt2) !important; fill: var(--volt2) !important;}
[data-testid="stMultiSelectTagsContainer"] > span > span {background: rgba(196,242,58,0.14) !important;
  border: 1px solid rgba(196,242,58,0.45) !important; color: var(--volt2) !important;}
[data-testid="stMultiSelectTagsContainer"] > span > span * {color: var(--volt2) !important; fill: var(--volt2) !important;}

/* ---- narrower screens (tablets, smaller windows): the bar wraps onto more lines than the page leaves room for when
   it's fixed, so there it sticks to the top instead (its wrapper keeps its place, so nothing hides under it) */
@media (max-width: 1480px) {   /* up to the page's full width: wider, the page is centred and the bar stays fixed */
  .block-container {padding-top: 0 !important;}
  .st-key-topbar {position: static !important; width: 100% !important; max-width: 100% !important; padding: 10px 0 !important;
                  background: transparent; backdrop-filter: none; -webkit-backdrop-filter: none; border-bottom: none;}
  [data-testid="stLayoutWrapper"]:has(> .st-key-topbar) {
    position: sticky !important; top: 0; z-index: 999; margin: -1rem -2.25rem 10px -2.25rem !important;
    width: calc(100% + 4.5rem) !important; max-width: none !important; padding: 0 2.25rem !important;
    background: rgba(10,14,13,0.9); backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
    border-bottom: 1px solid var(--line);
  }
}

/* ---- phones */
@media (max-width: 700px) {
  .block-container {padding: 0 0.85rem 3rem 0.85rem !important;}
  .st-key-topbar {position: static !important; width: 100% !important; max-width: 100% !important; padding: 2px 0 9px 0;
                  background: transparent; backdrop-filter: none; -webkit-backdrop-filter: none; border-bottom: none;
                  margin: 0 !important;}
  /* the bar stays at the top while you scroll: its wrapper sticks (the bar's own box is only as tall as the bar) */
  [data-testid="stLayoutWrapper"]:has(> .st-key-topbar) {
    position: sticky !important; top: 0; z-index: 999; margin: -1rem -0.85rem 6px -0.85rem !important;
    width: calc(100% + 1.7rem) !important; max-width: none !important; padding: 10px 0.85rem 0 0.85rem !important;
    background: rgba(10,14,13,0.94); backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
    border-bottom: 1px solid var(--line);
  }
  .brand {border-right: none; padding-right: 4px; height: 32px;}
  .brand-name {font-size: 19px;}
  .st-key-topbar [data-testid="stPageLink"] a {padding: 2px 8px !important;}
  .st-key-topbar [data-testid="stPageLink"] p {font-size: 13.5px !important;}
  /* phones: brand and menu on one line, the main tabs below as one bar, the status boxes in the menu */
  .st-key-topbar {row-gap: 8px !important;}
  .st-key-topbar > [data-testid="stLayoutWrapper"]:has(> .st-key-hudbar) {display: none !important;}
  .st-key-topbar > [data-testid="stLayoutWrapper"]:has(> [class*="st-key-navmenu"]) {display: flex !important; order: 2;}
  .st-key-topbar > [data-testid="stLayoutWrapper"]:has(> .st-key-navrow) {order: 3; flex: 1 1 100% !important;
                                                                          width: 100% !important; max-width: 100% !important;}
  .st-key-navrow {width: 100% !important; flex-wrap: nowrap !important; gap: 3px !important; padding: 3px !important;
                  background: var(--panel2); border: 1px solid var(--line); border-radius: 11px;}
  .st-key-navrow > [data-testid="stLayoutWrapper"]:has([class*="_more_"]) {display: none !important;}
  .st-key-navrow > [data-testid="stLayoutWrapper"] {flex: 1 1 0 !important; min-width: 0 !important; width: auto !important;}
  .st-key-navrow [class*="st-key-nav"], .st-key-navrow [data-testid="stElementContainer"],
  .st-key-navrow [data-testid="stPageLink"], .st-key-topbar .st-key-navrow [data-testid="stPageLink"] a {width: 100% !important;}
  .st-key-topbar .st-key-navrow [data-testid="stElementContainer"] {margin: 0 !important;}
  .st-key-topbar .st-key-navrow [data-testid="stPageLink"] a {justify-content: center; padding: 2px !important;}
  .st-key-topbar .st-key-navrow [data-testid="stPageLink"] p {font-size: 14px !important;}
  .hud-box {height: 30px; padding: 3px 9px;}
  .hero {grid-template-columns: 1fr !important; gap: 12px; padding: 14px;}
  .hero img.crest, .hero .crest-none {width: 64px !important; height: 64px !important;}
  .hero-name {font-size: 32px;}
  .hero-stats {grid-column: auto !important; grid-template-columns: repeat(2, minmax(0, 1fr)) !important;}
  .kpi-value {font-size: 26px;}
  .mc-top {flex-direction: column; align-items: flex-start; gap: 6px;}
  .scoreline {gap: 8px;}
  .team {gap: 6px;} .team-name {font-size: 16px;} .team-sub {font-size: 11px;}
  .team img.crest, .team .crest-none {width: 34px !important; height: 34px !important;}
  .score {font-size: 36px; padding: 2px 8px; letter-spacing: 1px;} .score span {margin: 0 3px;}
  .cmp-row {grid-template-columns: 38px 1fr 84px 1fr 38px; gap: 5px; font-size: 12px;}
  .cmp-label {font-size: 9.5px; letter-spacing: .5px;}
  .cmp-l, .cmp-r {font-size: 16px;}
  .info-tip {position: fixed; left: 10px !important; right: 10px !important; top: 118px !important; bottom: auto !important;
             width: auto; max-height: calc(100vh - 140px); overflow-y: auto;}   /* below the top bar */
  .section-title {font-size: 22px;}
  .modebar-container {display: none !important;}   /* touch screens show the chart toolbar all the time, over the title */
  .po-title {font-size: 24px;}
  .po-used {padding-top: 0;}
}
@media (max-width: 380px) {   /* small phones: the tab labels alone, so "Matches" fits */
  .st-key-navrow [data-testid="stPageLink"] a > span:has([data-testid="stIconMaterial"]) {display: none !important;}
  .st-key-topbar .st-key-navrow [data-testid="stPageLink"] a {gap: 0 !important;}
}
html, body {background-color: #0a0e0d;}
[data-testid="stElementContainer"]:has(.paint-hook) {display: none !important;}
</style>
""", unsafe_allow_html=True)

# Phones colour the bars around the page (the clock and battery at the top, the address bar at the bottom) from
# the page itself, and the page Streamlit's hosting wraps around the app asks for white. The app is served from
# the same site, so it can paint that page in the app's colours too (on the PC there's no wrapper: nothing changes).
# Tapping the top of an iPhone's screen scrolls that outer page to the top, but the app scrolls inside it, so the
# outer page is given 1 pixel to scroll and kept there: when a tap scrolls it back, the app scrolls to the top too.
st.html("""<div class="paint-hook"></div><script>
(function () {
  var BG = "#0a0e0d";
  function tapToTop(top, app) {
    try {
      top.__clubsApp = app;   // the newest app page (it can be reloaded inside the wrapper)
      if (top === app || top.__clubsTapTop) return;
      if (!(top.matchMedia && top.matchMedia("(pointer: coarse)").matches)) return;   // phones and tablets only
      top.__clubsTapTop = true;
      var d = top.document, spacer = d.createElement("div");
      spacer.setAttribute("aria-hidden", "true");
      spacer.style.height = "1px";
      d.body.appendChild(spacer);
      var park = function () { if (top.scrollY < 1) top.scrollTo(0, 1); };
      park();
      setTimeout(park, 400);
      var busy = false;
      top.addEventListener("scroll", function () {
        if (busy || top.scrollY >= 1) return;
        busy = true;
        try {
          var main = top.__clubsApp.document.querySelector('[data-testid="stMain"]');
          if (main) main.scrollTo({top: 0, behavior: "smooth"});
        } catch (e) {}
        setTimeout(function () { park(); busy = false; }, 600);
      }, {passive: true});
    } catch (e) {}
  }
  function meta(d, name, content) {
    var ms = d.querySelectorAll('meta[name="' + name + '"]');
    if (!ms.length) {
      var m = d.createElement("meta");
      m.setAttribute("name", name);
      d.head.appendChild(m);
      ms = [m];
    }
    for (var i = 0; i < ms.length; i++) ms[i].setAttribute("content", content);
  }
  function paint(w) {
    try {
      var d = w.document;
      d.documentElement.style.backgroundColor = BG;
      if (d.body) d.body.style.backgroundColor = BG;
      meta(d, "theme-color", BG);
      meta(d, "color-scheme", "dark");
    } catch (e) {}   // a page from another site: leave it alone
  }
  var w = window, top = window;
  paint(w);
  for (var k = 0; k < 3 && w.parent && w.parent !== w; k++) {
    w = w.parent;
    paint(w);
    try { if (w.document) top = w; } catch (e) { break; }   // the outermost page from this site
  }
  tapToTop(top, window);
})();
</script>""", unsafe_allow_javascript=True)


# ================================================================= DATA FOR THIS RUN
@st.cache_data(show_spinner=False, max_entries=3)
def load_data(sig, club_id, me_id, me_name, version=None):
    d = analytics.load(club_id, me_id, me_name)
    d.scouting = scout.Scouting(pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
    d.season_breaks, d.season_first, d.season_range = [], 1, None   # EA's league seasons (see clubs/seasons.py)
    if not d.matches.empty:
        d.season_breaks, d.season_first = seasons.breaks(db.club_snapshots_df(club_id), d.matches)
        d.matches["season"] = seasons.number(d.matches, d.season_breaks, d.season_first)
    if d.ours.empty:
        return d
    # scouting: how strong each opponent was (their SR minus ours) and how they had been playing
    d.scouting = scout.load()
    pm = scout.per_match(d.scouting, d.matches, db.latest_club_snapshot(club_id).get("skill_rating"))
    d.matches = d.matches.merge(pm, left_on="match_id", right_index=True, how="left")
    gap = pd.to_numeric(d.players["match_id"].map(d.matches.set_index("match_id")["sr_diff"]), errors="coerce")
    d.players["sr_gap"] = np.where(d.players["side"] == "us", gap, -gap)
    g = grades.grade_lines(d.players).rename(columns={p: f"gp_{p}" for p in grades.PARTS})
    d.players = d.players.join(g[GRADE_COLS])
    d.ours = d.ours.join(g[GRADE_COLS])
    tg = grades.grade_teams(d.players, d.matches)
    if not tg.empty:
        d.matches = d.matches.merge(tg, left_on="match_id", right_index=True, how="left")
    sc = score.scores(d.ours).rename(columns={"grade": "score_label"})
    few = d.ours.groupby("player_id")["match_id"].transform("nunique") < score.MIN_OWN
    sc.loc[few, ["score", "score_label"]] = np.nan   # a score needs a few matches to compare with
    d.ours = d.ours.join(sc[["score", "score_label"] + [p for p in PARTS if p in sc] + [c for c in sc if c.startswith("pct_")]])
    if not d.me.empty:
        mine = d.ours[d.ours["player_id"].astype(str) == str(d.me_id)]
        cols = (["match_id", "score", "score_label"] + GRADE_COLS + [p for p in PARTS if p in mine]
                + [c for c in mine if c.startswith("pct_")])
        d.me = d.me.merge(mine[cols].drop_duplicates("match_id"), on="match_id", how="left")
        d.matches = d.matches.merge(d.me[["match_id", "score", "rating", "goals", "assists", "grade_score", "grade"]].rename(
            columns={"score": "my_score", "rating": "my_rating", "goals": "my_goals", "assists": "my_assists",
                     "grade_score": "my_grade_score", "grade": "my_grade"}), on="match_id", how="left")
    return d


def viewer():
    """(player id, name) whose numbers the You pages show: online, each visitor picks; here, it's you."""
    v = st.session_state.get("viewer") if ONLINE else None
    if v:
        return v
    return CFG.get("me_id"), CFG.get("me_name") or ""


def pick_hint():
    """Where to change whose numbers the You pages show."""
    return ("Pick yourself under Whose numbers? at the top of the You page." if ONLINE
            else "Pick yourself in Settings if this is wrong.")


def data():
    me_id, me_name = viewer()
    return load_data(db.signature(), CLUB_ID, me_id, me_name, config.VERSION)


def viewer_picker(d):
    """Online: 'Whose numbers?' for the You page (remembered for the visit, and in the link as ?me=name)."""
    if not ONLINE or d.ours.empty:
        return
    g = d.ours.groupby(d.ours["player_id"].astype(str)).agg(name=("name", "last"), n=("match_id", "nunique"))
    g = g.sort_values("n", ascending=False)
    ids = list(g.index)
    cur = str(viewer()[0] or "")
    want = st.query_params.get("me")
    if want and not st.session_state.get("viewer"):
        hit = g[g["name"].str.lower() == str(want).lower()]
        if not hit.empty and str(hit.index[0]) != cur:
            st.session_state["viewer"] = (hit.index[0], hit.iloc[0]["name"])
            st.rerun()
    pick = st.selectbox("Whose numbers?", ids, index=ids.index(cur) if cur in ids else None, key="viewer_pick",
                        format_func=lambda i: f'{g.loc[i, "name"]} ({plural(g.loc[i, "n"], "match", "matches")})',
                        placeholder="Pick a player")
    if pick and pick != cur:
        st.session_state["viewer"] = (pick, g.loc[pick, "name"])
        st.query_params["me"] = g.loc[pick, "name"]
        st.rerun()


def run_check(full=True):
    """Check EA now: through the collector when it runs, otherwise right here."""
    if collector_ctl.check_now():
        flash("Asked the collector to check EA now. New matches show up in a moment.", "🔄")
        return
    with st.spinner("Checking EA for new matches..."):
        res = sync.sync(full=full)
    if res["new"]:
        flash(f"Saved {plural(len(res['new']), 'new match', 'new matches')}.", "⚽")
    elif res.get("ok"):
        flash("No new matches. Everything EA has is saved.", "👍")
    else:
        flash("Couldn't reach EA's Clubs API: " + "; ".join(res["errors"][-2:]), "⚠️")


# ================================================================= SHARED BLOCKS
def hero(d):
    club = db.club(CLUB_ID)
    snap = db.latest_club_snapshot(CLUB_ID)
    m = d.matches
    rec = analytics.record(m)
    stk = analytics.streaks(m["result"]) if not m.empty else {}
    sw, sd, sl = snap.get("season_wins"), snap.get("season_ties"), snap.get("season_losses")
    season_rec = f"{sw}-{sd}-{sl}" if sw is not None else f"{rec['w']}-{rec['d']}-{rec['l']}"
    pts = snap.get("season_points")
    stats = [
        ("Season", season_rec, "W-D-L"),
        ("Points", fmt(pts) if pts is not None else fmt(3 * rec["w"] + rec["d"]), "this season" if pts is not None else "tracked"),
        ("Skill rating", fmt(snap.get("skill_rating")), labels.reputation(snap.get("reputation"))),
        ("Streak", f'{stk.get("current") or "-"}{stk.get("current_n") or ""}', f'best run W{stk.get("best_win", 0)}'),
    ]
    if "team_grade_score" in m and m["team_grade_score"].notna().any():
        form = m["team_grade_score"].dropna().tail(10).mean()
        stats.append(("Team form", grades.letter(form), f"avg grade, last {min(10, m['team_grade_score'].notna().sum())}"))
    stat_html = "".join(f'<div class="hstat"><div class="hstat-label">{esc(a)}</div><div class="hstat-value">{esc(b)}</div>'
                        f'<div class="hstat-sub">{esc(c)}</div></div>' for a, b, c in stats)
    name = club.get("name") or CFG.get("club_name") or "Your club"
    sub = " · ".join(x for x in [club.get("stadium"), labels.platform(CFG.get("platform")),
                                 plural(rec["n"], "match", "matches") + " tracked"] if x)
    st.html(f'<div class="hero">{crest(club.get("crest_url"), 104)}<div><div class="eyebrow">'
            f'{esc(labels.division(snap.get("division")))}</div><div class="hero-name">{esc(name)}</div>'
            f'<div class="hero-sub">{esc(sub)}</div>{form_dots(m["result"] if not m.empty else [])}</div>'
            f'<div class="hero-stats">{stat_html}</div></div>')


def scorers_html(lines, side="us"):
    s = lines[(lines["side"] == side) & (lines["goals"] > 0)].sort_values("goals", ascending=False)
    if s.empty:
        return ""
    return "⚽ " + " · ".join(f'<b>{esc(r["name"])}</b>' + (f" ×{int(r['goals'])}" if r["goals"] > 1 else "")
                             for _, r in s.iterrows())


def scoreline_html(row, opp_crest=None, our_crest=None, with_grades=True):
    res = row["result"]
    tg = og = ""
    if with_grades:
        tg = grade_badge(row.get("team_grade_score"), "TEAM")
        og = grade_badge(row.get("opp_team_grade_score"), "TEAM")
    flags = []
    if row.get("they_quit"):
        flags.append("they quit")
    if row.get("we_quit"):
        flags.append("we didn't finish")
    flag = f' <span class="quit-note">({", ".join(flags)})</span>' if flags else ""
    return (f'<div class="scoreline"><div class="team">{crest(our_crest, 48)}<div><div class="team-name">'
            f'{esc(CFG.get("club_name") or "Us")}</div><div class="team-sub">{plural(row["our_players"] or 0, "player")}'
            f'</div><div class="team-grade">{tg}</div></div></div><div class="score">{int(row["goals_for"])}<span>-</span>'
            f'{int(row["goals_against"])}</div>'
            f'<div class="team right"><div><div class="team-name">{esc(row["opp_name"] or "Opponent")}</div>'
            f'<div class="team-sub">{plural(row["opp_players"] or 0, "player")}</div><div class="team-grade">{og}</div></div>'
            f'{crest(opp_crest, 48)}</div></div>'
            f'<div class="big-result {res}">{RESULT_NAME.get(res, "")}{flag}</div>')


def crest_of(club_id):
    c = CRESTS.get(str(club_id))
    return c


def my_line_html(me_row):
    if me_row is None:
        return '<div class="myline"><span class="mini">You didn\'t play this one.</span></div>'
    r = me_row
    bits = [f'<span class="rt {rating_tone(r["rating"])}" title="Match rating">{fmt(r["rating"], 1)}</span>',
            chip(f'{int(r["goals"] or 0)} G'), chip(f'{int(r["assists"] or 0)} A'),
            chip(f'{int(r["shots"] or 0)} shots'),
            chip(f'{int(r["passes_made"] or 0)}/{int(r["pass_attempts"] or 0)} passes'),
            chip(f'{int(r["tackles_made"] or 0)}/{int(r["tackle_attempts"] or 0)} tackles')]
    if r.get("saves"):
        bits.append(chip(f'{int(r["saves"])} saves'))
    if r.get("mom"):
        bits.append(chip("★ Man of the match", "gold"))
    bits.append(grade_badge(r.get("grade_score"), "GRADE", title=grade_title(r)) + info_icon(grade_info(r.get("pos"))))
    return f'<div class="myline"><span class="mini" style="font-weight:600">YOU</span>{"".join(bits)}</div>'


def team_lines_html(lines):
    """Every player of ours in a match with their grade (best first)."""
    us = lines[lines["side"] == "us"]
    if us.empty:
        return ""
    items = []
    for _, r in us.sort_values("grade_score", ascending=False).iterrows():
        bits = [f'{int(r["goals"])} G' if r["goals"] else "", f'{int(r["assists"])} A' if r["assists"] else "",
                f'{int(r["saves"])} saves' if r.get("saves") else "", fmt(r["rating"], 1)]
        star = ' <span class="star" title="Man of the match">★</span>' if r["mom"] else ""
        items.append(f'<div class="tl-p">{grade_cell(r.get("grade_score"), grade_title(r))}'
                     f'<span class="pos" style="background:{labels.POS_COLORS.get(r["pos"], "#888")}">'
                     f'{labels.pos_short(r["pos"])}</span><span class="pname">{esc(r["name"])}</span>{star}'
                     f'<span class="dim">{" · ".join(b for b in bits if b)}</span></div>')
    return f'<div class="tl">{"".join(items)}</div>'


def match_card(d, match_id, eyebrow="Last match", team=False):
    m = d.matches
    row = m[m["match_id"] == match_id].iloc[0]
    lines = d.players[d.players["match_id"] == match_id]
    me_row = d.me[d.me["match_id"] == match_id]
    me_row = me_row.iloc[0] if not me_row.empty else None
    with st.container(border=True):
        st.html(f'<div class="mc-top"><div class="chips"><span class="eyebrow" style="margin:0">{esc(eyebrow)}</span>'
                f'{chip(labels.match_type(row["match_type"]), "volt")}{"".join(strength_chips(row))}</div>'
                f'<div>{when_str(row["when"])} · {ago(row["when"])}</div></div>'
                + scoreline_html(row, crest_of(row["opp_id"]), crest_of(CLUB_ID))
                + f'<div class="scorers">{scorers_html(lines)}</div>'
                + (team_lines_html(lines) if team else my_line_html(me_row)))
        if st.button("Open match report", key=f"open_{eyebrow}_{team}_{match_id}", icon=":material/open_in_new:"):
            st.switch_page(PAGES["matches"], query_params={"match": str(match_id)})


def online_card():
    res = getattr(_pkg, "_online_result", None) or {}
    newest = res.get("newest")
    with st.container(border=True):
        st.html(f'<div class="eyebrow">Online copy</div><div class="mini"><span class="hud-dot live"></span> '
                f'Updated from {esc(CFG.get("club_name") or "the club")}\'s tracker after every match'
                + (f": last update {ago(pd.Timestamp(newest))}" if newest else "") + ".</div>")
        if res.get("errors"):
            st.caption(f"{plural(len(res['errors']), 'file')} couldn't be read: " + "; ".join(res["errors"][:2])[:200])


def collector_card(compact=False):
    if ONLINE:
        online_card()
        return
    stt = collector_ctl.status()
    last = sync.last_sync()
    with st.container(border=True):
        if stt.get("running"):
            state = stt.get("state")
            txt = {"playing": "FC 27 is open: checking every few minutes",
                   "active": "You're playing: checking every few minutes",
                   "idle": "Waiting for your next match", "checking": "Checking EA right now",
                   "error": "EA isn't answering right now: trying again later",
                   "no_club": "No club chosen yet", "starting": "Starting", "scouting": "Scouting the clubs you played",
                   "restarting": "Restarting with the new version"}.get(state, "Running")
            dot = "live" if state in ("playing", "active", "checking", "scouting") else "err" if state == "error" else ""
            nxt = in_minutes(stt.get("next_check")) if stt.get("next_check") else ""
            st.html(f'<div class="status-line"><span class="hud-dot {dot}"></span>Collector on · {esc(txt)}</div>'
                    f'<div class="mini" style="margin-top:4px">Last check {esc(ago(stt.get("last_check")) or "-")}'
                    + (f" · next {esc(nxt)}" if nxt else "") + "</div>")
        else:
            st.html('<div class="status-line"><span class="hud-dot off"></span>Collector off</div>'
                    '<div class="mini" style="margin-top:4px">EA keeps only your last 10 matches of each type: '
                    'turn the collector on so every match gets saved.</div>')
        if last.get("last_errors") and not last.get("last_ok"):
            st.caption("Last error: " + last["last_errors"][-1][:220])
        a, b = st.columns(2)
        with a:
            if st.button("Check now", key=f"chk_{compact}", icon=":material/refresh:", width="stretch"):
                run_check(full=True)
                st.rerun()
        with b:
            if not stt.get("running"):
                if st.button("Turn on", key=f"on_{compact}", type="primary", icon=":material/play_arrow:", width="stretch"):
                    ok = collector_ctl.start()
                    flash("Collector started." if ok else "The collector didn't start: see Settings.", "▶️")
                    st.rerun()
            else:
                st.page_link(PAGES["settings"], label="Collector settings", icon=":material/tune:")


def my_kpis(d):
    me = d.me
    if me.empty:
        empty_state("🧍", "No matches for you yet",
                    f"Nothing found for <b>{esc(viewer()[1] or 'you')}</b> in the tracked matches. " + pick_hint())
        return
    n = len(me)
    w = (me["result"] == "W").sum()
    pa, pm = me["pass_attempts"].sum(), me["passes_made"].sum()
    last5 = me.tail(5)["rating"].mean()
    avg = me["rating"].mean()
    ov = grades.overall(me["grade_score"]) if "grade_score" in me else np.nan
    form = me["grade_score"].dropna().tail(5).mean() if "grade_score" in me else np.nan
    kpis([
        {"label": "Overall grade", "value": f'{grades.letter(ov) or "-"} <span style="font-size:.6em;opacity:.7">{fmt(ov)}</span>',
         "sub": f"form {grades.letter(form) or '-'} (last 5)", "tone": "good" if ov >= 75 else "volt" if ov >= 60 else "gold" if ov >= 40 else "bad",
         "help": "Your average match grade, pulled towards the middle while you have only a few matches. Each match "
                 "grade depends on the position you played: as a striker mostly goals and conversion, as a midfielder "
                 "assists, passing and tackles. Form = your average grade over the last 5 matches."},
        {"label": "Matches", "value": fmt(n), "sub": f"{w}-{(me['result'] == 'D').sum()}-{(me['result'] == 'L').sum()}"},
        {"label": "Win rate", "value": pct(100 * w / n), "tone": "good" if w / n >= 0.5 else ""},
        {"label": "Goals", "value": fmt(me["goals"].sum()), "sub": f'{me["goals"].mean():.2f} per match', "tone": "volt"},
        {"label": "Assists", "value": fmt(me["assists"].sum()), "sub": f'{me["assists"].mean():.2f} per match'},
        {"label": "Avg rating", "value": fmt(avg, 2), "sub": f"last 5: {last5:.2f}",
         "sub_tone": "good" if last5 > avg else "bad" if last5 < avg else ""},
        {"label": "Man of the match", "value": fmt(me["mom"].sum()), "tone": "gold",
         "sub": pct(100 * me["mom"].sum() / n) + " of matches"},
        {"label": "Pass accuracy", "value": pct(100 * pm / pa if pa else None),
         "sub": f'{me["passes_made"].mean():.1f} passes a match'},
    ])


# ================================================================= CHARTS
def _hover(df):
    return [f"{when_str(r.when)}<br>{labels.match_type(r.match_type)} vs {esc(r.opp_name)}<br>"
            f"{int(r.goals_for)}-{int(r.goals_against)} ({RESULT_NAME.get(r.result, '')})" for r in df.itertuples()]


def rating_chart(me, n=None, title="Your match rating"):
    df = me.tail(n) if n else me
    if df.empty:
        return None
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df["game_no"], y=df["rating"], mode="markers", name="Rating",
                             marker=dict(size=10, color=[RESULT_COLORS.get(r, "#888") for r in df["result"]],
                                         line=dict(width=1, color="#0a0e0d")),
                             text=_hover(df), hovertemplate="%{text}<br>Rating <b>%{y:.1f}</b><extra></extra>"))
    fig.add_trace(go.Scatter(x=df["game_no"], y=analytics.rolling(df["rating"], UI["rolling_window"]), mode="lines",
                             name=f"Average of last {UI['rolling_window']}", line=dict(color=VOLT, width=3), hoverinfo="skip"))
    avg_line(fig, me["rating"].mean(), "Overall")
    fig.update_layout(title=title, xaxis_title="Match", yaxis=dict(range=[max(0, min(5.0, df["rating"].min() - 0.6)), 10.3]))
    return few_matches(fig, df)


def ga_chart(me, n=None):
    df = me.tail(n) if n else me
    if df.empty:
        return None
    fig = go.Figure()
    fig.add_trace(go.Bar(x=df["game_no"], y=df["goals"], name="Goals", marker_color=VOLT, hovertext=_hover(df),
                         hovertemplate="%{hovertext}<br>Goals <b>%{y}</b><extra></extra>"))
    fig.add_trace(go.Bar(x=df["game_no"], y=df["assists"], name="Assists", marker_color=BLUE, hovertext=_hover(df),
                         hovertemplate="%{hovertext}<br>Assists <b>%{y}</b><extra></extra>"))
    fig.add_trace(go.Scatter(x=df["game_no"], y=analytics.rolling(df["ga"], UI["rolling_window"]), mode="lines",
                             name=f"G+A, last {UI['rolling_window']}", line=dict(color=GOLD, width=3), hoverinfo="skip"))
    fig.update_layout(title="Your goals and assists", barmode="stack", xaxis_title="Match", bargap=0.25)
    return few_matches(fig, df)


def results_chart(m, n=None):
    df = m.tail(n) if n else m
    if df.empty:
        return None
    fig = go.Figure(go.Bar(x=df["game_no"], y=df["gd"].replace(0, 0.12), marker_color=[RESULT_COLORS.get(r) for r in df["result"]],
                           hovertext=_hover(df), hovertemplate="%{hovertext}<extra></extra>", name="Goal difference"))
    fig.update_layout(title="Results (goal difference)", xaxis_title="Match", yaxis_title="Goal difference", showlegend=False)
    return few_matches(fig, df)


def points_chart(m):
    if m.empty:
        return None
    df = m.copy()
    df["cum"] = df["points"].cumsum()
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df["game_no"], y=df["cum"], mode="lines", name="Points", line=dict(color=VOLT, width=3),
                             fill="tozeroy", fillcolor="rgba(196,242,58,0.08)", text=_hover(df),
                             hovertemplate="%{text}<br>Total <b>%{y}</b> points<extra></extra>"))
    fig.add_trace(go.Scatter(x=df["game_no"], y=df["game_no"] * 1.5, mode="lines", name="1.5 points a match",
                             line=dict(color="#93a39c", dash="dot", width=1.5), hoverinfo="skip"))
    fig.update_layout(title="Points collected", xaxis_title="Match")
    return few_matches(fig, df)


def score_chart(me):
    df = me.dropna(subset=["score"]) if "score" in me else pd.DataFrame()
    if df.empty:
        return None
    fig = go.Figure()
    fig.add_trace(go.Bar(x=df["game_no"], y=df["score"], marker_color=[_score_color(v) for v in df["score"]],
                         hovertext=_hover(df), hovertemplate="%{hovertext}<br>Score <b>%{y:.0f}</b><extra></extra>", name="Score"))
    fig.add_trace(go.Scatter(x=df["game_no"], y=analytics.rolling(df["score"], UI["rolling_window"]), mode="lines",
                             name=f"Average of last {UI['rolling_window']}", line=dict(color="#f1f5f3", width=2.5), hoverinfo="skip"))
    fig.update_layout(title="Your performance score per match", xaxis_title="Match", yaxis=dict(range=[0, 102]))
    return few_matches(fig, df)


def grade_lines_(fig):
    """Faint lines where each letter starts (S 90, A 75, B 60, C 40, D 25, E 10)."""
    for t_, l_ in grades.LETTERS[:-1]:
        fig.add_hline(y=t_, line_dash="dot", line_color="#2f3c36", line_width=1, annotation_text=l_,
                      annotation_position="right", annotation_font_color="#627069", annotation_font_size=11)


def grade_chart(df_, col="grade_score", title="Your match grades"):
    df = df_.dropna(subset=[col]) if col in df_ else pd.DataFrame()
    if df.empty:
        return None
    fig = go.Figure()
    fig.add_trace(go.Bar(x=df["game_no"], y=df[col], marker_color=[grade_color(v) for v in df[col]],
                         text=[grades.letter(v) for v in df[col]], textposition="outside", textfont=dict(size=11),
                         hovertext=_hover(df), hovertemplate="%{hovertext}<br>Grade <b>%{text}</b> (%{y:.0f})<extra></extra>",
                         name="Grade", cliponaxis=False))
    fig.add_trace(go.Scatter(x=df["game_no"], y=analytics.rolling(df[col], UI["rolling_window"]), mode="lines",
                             name=f"Average of last {UI['rolling_window']}", line=dict(color="#f1f5f3", width=2.5), hoverinfo="skip"))
    grade_lines_(fig)
    fig.update_layout(title=title, xaxis_title="Match", yaxis=dict(range=[0, 108]), meta="pad_right")
    if len(df) > 40:
        fig.update_traces(selector=dict(type="bar"), text=None)
    return few_matches(fig, df)


def grade_mix_chart(scores, title):
    s = pd.to_numeric(scores, errors="coerce").dropna()
    if s.empty:
        return None
    order = [g for _, g in grades.LETTERS]
    groups = s.map(grades.letter)
    cnt = groups.value_counts().reindex(order, fill_value=0)
    colors = [grade_color(v) for v in (95, 80, 65, 50, 30, 15, 5)]
    fig = go.Figure(go.Bar(x=order, y=cnt.values, marker_color=colors, text=[f"{v}" for v in cnt.values], textposition="outside",
                           hovertemplate="%{x} grades: <b>%{y}</b><extra></extra>", cliponaxis=False))
    fig.update_layout(title=title, yaxis=dict(range=[0, max(cnt.max() * 1.25, 1)]), showlegend=False)
    return fig


# ================================================================= PAGE: TEAM
def team_kpis(d):
    m = d.matches
    rec = analytics.record(m)
    n = max(rec["n"], 1)
    tg = m["team_grade_score"].dropna() if "team_grade_score" in m else pd.Series(dtype=float)
    ov = grades.overall(tg) if len(tg) else np.nan
    form = tg.tail(5).mean() if len(tg) else np.nan
    k = m.dropna(subset=["sr_diff"]) if "sr_diff" in m else m.iloc[0:0]
    up_w, up_l = int((k.get("verdict") == "Upset win").sum()), int((k.get("verdict") == "Upset loss").sum())
    kpis([
        {"label": "Team grade", "value": f'{grades.letter(ov) or "-"} <span style="font-size:.6em;opacity:.7">{fmt(ov)}</span>',
         "sub": f"form {grades.letter(form) or '-'} (last 5)",
         "tone": "good" if ov >= 75 else "volt" if ov >= 60 else "gold" if ov >= 40 else "bad",
         "help": "The team's average match grade, pulled towards the middle (50) while there are only a few matches. "
                 "Form = the average of the last 5."},
        {"label": "Matches", "value": fmt(rec["n"]), "sub": f"{rec['w']}-{rec['d']}-{rec['l']}"},
        {"label": "Win rate", "value": pct(rec["win_pct"]), "tone": "good" if (rec["win_pct"] or 0) >= 50 else ""},
        {"label": "Points per match", "value": fmt(rec["ppg"], 2), "sub": f"{3 * rec['w'] + rec['d']} points"},
        {"label": "Goals per match", "value": f"{rec['gf'] / n:.2f}", "sub": f"conceded {rec['ga'] / n:.2f}", "tone": "volt"},
        {"label": "Clean sheets", "value": fmt(rec["clean_sheets"]), "sub": pct(100 * rec["clean_sheets"] / n) + " of matches"},
        {"label": "Opponents", "value": (f'{k["sr_diff"].mean():+.0f} SR' if len(k) else "-"),
         "sub": "their skill rating vs yours" if len(k) else "appears once they're scouted",
         "help": "How strong the clubs you played were on average: their skill rating minus yours, from scouting."},
        {"label": "Upsets", "value": f"{up_w} / {up_l}" if len(k) else "-", "sub": "wins vs stronger / losses vs weaker",
         "tone": "good" if up_w > up_l else "bad" if up_l > up_w else ""},
    ])


def squad_cards(d, recent=30):
    """A card per player of the club (from the last `recent` matches): overall grade, form and numbers."""
    ours = d.ours
    if ours.empty:
        return
    recent_ids = set(d.matches.tail(recent)["match_id"])
    ours = ours[ours["player_id"].isin(set(ours[ours["match_id"].isin(recent_ids)]["player_id"]))]
    pg = player_grades(ours)
    g = ours.groupby(ours["player_id"].astype(str))
    t = pd.DataFrame({"name": g["name"].last(), "matches": g["match_id"].nunique(), "goals": g["goals"].sum(),
                      "assists": g["assists"].sum(), "rating": g["rating"].mean(), "mom": g["mom"].sum(),
                      "pos": g["pos"].agg(lambda x: x.mode().iat[0] if not x.mode().empty else None)}).join(pg)
    t = t.sort_values(["overall", "matches"], ascending=False)
    cards = []
    for pid, r in t.iterrows():
        ov, form = r["overall"], r["form"]
        c = grade_color(ov)
        trend = ""
        if not isna(form) and not isna(ov) and r["matches"] >= 3:
            trend = (' <span class="up">▲ in form</span>' if form >= ov + 8 else
                     ' <span class="down">▼ below their level</span>' if form <= ov - 8 else " steady")
        me = '<small>YOU</small>' if d.me_id and str(pid) == str(d.me_id) else ""
        cards.append(
            f'<div class="sq-card" style="--c:{c}"><div class="sq-top"><div><div class="sq-name">{esc(r["name"])}{me}</div>'
            f'<div class="sq-sub">{esc(labels.position(r["pos"]))} · {plural(r["matches"], "match", "matches")}</div></div>'
            f'<div class="sq-grade" style="color:{c}" title="Overall grade: {fmt(ov)} of 100">{grades.letter(ov) or "-"}'
            f'<small>{fmt(ov)}</small></div></div>'
            f'<div class="sq-form">Form <b style="color:{grade_color(form)}">{grades.letter(form) or "-"}</b> '
            f'(last 5){trend}</div>'
            f'<div class="sq-stats"><div><b>{int(r["goals"])}</b>GOALS</div><div><b>{int(r["assists"])}</b>ASSISTS</div>'
            f'<div><b>{fmt(r["rating"], 1)}</b>RATING</div><div><b>{int(r["mom"])}</b>MOTM</div></div></div>')
    st.html(f'<div class="pcards">{"".join(cards)}</div>')


def top_performers(d, n=10):
    m = d.matches.tail(n)
    ln = d.ours[d.ours["match_id"].isin(set(m["match_id"]))]
    if ln.empty:
        return
    g = ln.groupby(ln["player_id"].astype(str))
    t = pd.DataFrame({"name": g["name"].last(), "matches": g["match_id"].nunique(), "grade": g["grade_score"].mean(),
                      "goals": g["goals"].sum(), "assists": g["assists"].sum(), "mom": g["mom"].sum()})
    items = []
    best = t[t["matches"] >= min(3, t["matches"].max())].sort_values("grade", ascending=False)
    if not best.empty:
        b = best.iloc[0]
        items.append({"label": "Best grade", "value": esc(b["name"]), "tone": "volt",
                      "sub": f'average {grades.letter(b["grade"])} {b["grade"]:.0f} in {plural(b["matches"], "match", "matches")}'})
    for col, label, word in (("goals", "Top scorer", "goal"), ("assists", "Most assists", "assist"),
                             ("mom", "Man of the match", "time")):
        top = t.sort_values([col, "grade"], ascending=False)
        if not top.empty and top.iloc[0][col] > 0:
            r = top.iloc[0]
            items.append({"label": label, "value": esc(r["name"]), "tone": "gold" if col == "mom" else "",
                          "sub": f'{plural(r[col], word)} in {plural(r["matches"], "match", "matches")}'})
    if items:
        kpis(items, cols=len(items))


def goals_chart(m):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=m["game_no"], y=analytics.rolling(m["goals_for"], 10), name="Scored (last 10)",
                             line=dict(color=VOLT, width=3)))
    fig.add_trace(go.Scatter(x=m["game_no"], y=analytics.rolling(m["goals_against"], 10), name="Conceded (last 10)",
                             line=dict(color=BAD, width=3)))
    fig.update_layout(title="Goals per match", xaxis_title="Match")
    return few_matches(fig, m)


def team_session_card(d):
    m = d.matches
    sess = analytics.session_table(m, CFG.get("session_gap_minutes", 45))
    if sess.empty:
        return
    s_ = sess.iloc[0]
    in_s = m[(m["when"] >= s_["start"]) & (m["when"] <= s_["end"])]
    ln = d.ours[d.ours["match_id"].isin(set(in_s["match_id"]))]
    best = ""
    if not ln.empty and "grade_score" in ln:
        b = ln.groupby("name")["grade_score"].mean().sort_values(ascending=False)
        if len(b) and not isna(b.iloc[0]):
            best = f'Best: <b>{esc(b.index[0])}</b> ({grades.letter(b.iloc[0])} {b.iloc[0]:.0f} average)'
    tg = in_s["team_grade_score"].mean() if "team_grade_score" in in_s else np.nan
    with st.container(border=True):
        st.html(f'<div class="eyebrow">Latest session</div><div class="mini">{when_str(s_["start"])} - '
                f'{when_str(s_["end"], False)}</div>')
        kpis([{"label": "Matches", "value": fmt(s_["matches"]), "sub": f'{int(s_["w"])}-{int(s_["d"])}-{int(s_["l"])}'},
              {"label": "Points", "value": fmt(s_["points"]), "sub": f'{s_["ppg"]:.2f} a match',
               "tone": "good" if s_["ppg"] >= 1.5 else "bad" if s_["ppg"] < 1 else ""},
              {"label": "Team grade", "value": grades.letter(tg) or "-", "sub": f"{fmt(tg)} average"}], cols=3)
        ssr = analytics.session_sr(sess.head(2), db.club_snapshots_df(CLUB_ID)) if CLUB_ID else pd.DataFrame()
        ssr = ssr[ssr["start"] == s_["start"]] if not ssr.empty else ssr
        if not ssr.empty:
            r = ssr.iloc[0]
            tone = "var(--good)" if r["change"] > 0 else "var(--bad)" if r["change"] < 0 else "var(--muted)"
            best = (f'Skill rating <b style="color:{tone}">{r["change"]:+.0f}</b> ({fmt(r["sr_before"])} to '
                    f'{fmt(r["sr_after"])})' + (f'<br>{best}' if best else ""))
        if best:
            st.html(f'<div class="mini" style="margin-top:6px">{best}</div>')


# ================================================================= PAGE: HOME
def left_text(td) -> str:
    h = td.total_seconds() / 3600
    if h < 1:
        return "less than an hour"
    if h < 36:
        return plural(int(round(h)), "hour")
    return plural(int(td.days + (1 if td.seconds >= 43200 else 0)), "day")


def playoffs_card(d, ps, now):
    """Home, during the playoff week (and a few days after): matches used, record and every playoff match."""
    div = labels.division(ps["division"]) if ps["division"] else None
    head = f"Season {ps['season']} playoffs" if ps["season"] else "Playoffs"
    span_ = f'{ps["start"]:%a %d %b, %H:%M} - {ps["end"]:%a %d %b, %H:%M}'
    title = (f"Open: {left_text(ps['end'] - now)} left" if ps["open"] else f"Closed {ago(ps['end'])}")
    limit = ps["limit"]
    used = (f'{ps["played"]} of {limit} playoff matches played' + (f' · {ps["left"]} left' if ps["open"] else "")
            if limit else plural(ps["played"], "playoff match", "playoff matches") + " played")
    bar = (f'<div class="po-bar"><div style="width:{min(100, 100 * ps["played"] / limit):.0f}%"></div></div>'
           if limit else "")
    n = ps["w"] + ps["d"] + ps["l"]
    with st.container(border=True, key="playoffs_card"):
        st.html(f'<div class="po-top"><div><div class="eyebrow">🏆 {esc(head)}</div>'
                f'<div class="po-title">{esc(title)}</div><div class="mini">{esc(span_)}'
                + (f' · {esc(div)}: up to {limit} playoff matches' if div and limit else "") + '</div></div>'
                f'<div class="po-used">{esc(used)}</div></div>{bar}')
        if n:
            kpis([{"label": "Playoff record", "value": f'{ps["w"]}-{ps["d"]}-{ps["l"]}', "sub": f'{100 * ps["w"] / n:.0f}% won',
                   "tone": "good" if ps["w"] / n >= 0.5 else ""},
                  {"label": "Points", "value": fmt(ps["points"]), "sub": f'{ps["points"] / n:.2f} a match'},
                  {"label": "Goals", "value": f'{ps["gf"]}-{ps["ga"]}', "sub": f'{(ps["gf"] - ps["ga"]):+d} difference'},
                  {"label": "Team grade", "value": grades.letter(ps["matches"]["team_grade_score"].mean()) or "-",
                   "sub": f'{fmt(ps["matches"]["team_grade_score"].mean())} average'}
                  if "team_grade_score" in ps["matches"] else {"label": "League matches", "value": fmt(ps["league_during"])}])
            pm = ps["matches"]
            table = pd.DataFrame({
                "When": pm["when"], "Opponent": pm["opp_name"],
                "Score": pm["goals_for"].astype(int).astype(str) + " - " + pm["goals_against"].astype(int).astype(str),
                "Result": pm["result"],
                "Opp. SR": [sr_diff_text(v) for v in pm.get("sr_diff", pd.Series(np.nan, index=pm.index))],
                "Team grade": [grade_text(v) for v in pm.get("team_grade_score", pd.Series(np.nan, index=pm.index))]})
            ev = st.dataframe(table, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                              key="po_table", height=min(380, 38 + 35 * len(table)),
                              column_config={"When": st.column_config.DatetimeColumn(format="ddd D MMM, HH:mm")})
            sel = ev.selection.rows if ev and ev.selection else []
            if sel:
                mid = str(pm.iloc[sel[0]]["match_id"])
                st.session_state.pop("po_table", None)   # coming back shouldn't jump to the report again
                st.switch_page(PAGES["matches"], query_params={"match": mid})
        elif ps["open"]:
            st.html('<div class="mini">No playoff matches yet. Every win counts: the final placing decides the fans '
                    'the club gets.</div>')
        st.caption("Your final placing (as a share of all clubs) decides the fans the club gets. League matches still "
                   "count during the week, but promotions stop until the next season, which starts in Division 5.")


def season_card(d, now):
    """Home: the season so far and how long it has left (with the playoffs coming up)."""
    m = d.matches
    snap = db.latest_club_snapshot(CLUB_ID)
    club = db.club(CLUB_ID)
    no = int(m["season"].max()) if "season" in m and not m.empty else None
    ends = next((t for _, t in seasons.calendar_starts() if t > now), None)
    nxt = playoffs.next_window(now, m)
    snaps = db.club_snapshots_df(CLUB_ID)
    lo, _ = seasons.span(d.season_breaks, d.season_first, no) if no else (None, None)
    sn = snaps.dropna(subset=["skill_rating"]) if not snaps.empty else snaps
    if lo is not None and not sn.empty:
        sn = sn[sn["at"] >= lo]
    cur = m[m["season"] == no] if no else m
    rec = analytics.record(cur)
    sw, sd, sl = snap.get("season_wins"), snap.get("season_ties"), snap.get("season_losses")
    right = f"Ends {ends:%a %d %b} · in {left_text(ends - now)}" if ends is not None else ""
    with st.container(border=True, key="season_card"):
        st.html(f'<div class="po-top"><div class="season-id">{crest(club.get("crest_url"), 46)}<div>'
                f'<div class="eyebrow">{esc(f"Season {no}" if no else "This season")} · '
                f'{esc(labels.division(snap.get("division")))}</div>'
                f'<div class="po-title">{esc(club.get("name") or CFG.get("club_name") or "Your club")}</div></div></div>'
                f'<div class="po-used">{esc(right)}</div></div>')
        chg = (sn["skill_rating"].iloc[-1] - sn["skill_rating"].iloc[0]) if len(sn) >= 2 else None
        kpis([{"label": "Season record", "value": f"{sw}-{sd}-{sl}" if sw is not None else f"{rec['w']}-{rec['d']}-{rec['l']}",
               "sub": f"{plural(int(sw + sd + sl), 'league match', 'league matches')}" if sw is not None
               else plural(rec["n"], "match", "matches")},
              {"label": "League points", "value": fmt(snap.get("season_points")) if snap.get("season_points") is not None
               else fmt(3 * rec["w"] + rec["d"]), "sub": "EA's count"},
              {"label": "Skill rating", "value": fmt(snap.get("skill_rating")),
               "sub": f"{chg:+.0f} since {sn['at'].iloc[0]:%d %b}" if chg is not None else "",
               "sub_tone": "good" if (chg or 0) > 0 else "bad" if (chg or 0) < 0 else ""},
              {"label": "Form", "value": form_dots(m["result"], n=5), "sub": "last 5 matches"}])
        if nxt is not None and nxt[0] - now <= playoffs.SOON:
            div = str(snap.get("division")) if snap.get("division") is not None else None
            lim = playoffs.LIMITS.get(div) if div else None
            when_ = "today" if nxt[0].date() == now.date() else "tomorrow" if (nxt[0].date() - now.date()).days == 1 \
                else f"in {left_text(nxt[0] - now)}"
            st.html(f'<div class="po-soon">🏆 The playoffs open <b>{nxt[0]:%a %d %b, %H:%M}</b> ({esc(when_)}) and last '
                    f'a week' + (f': up to <b>{lim}</b> playoff matches in {esc(labels.division(div))}' if lim else "")
                    + '. Promotions stop then; league matches still count.</div>')


def recent_matches(d, n=6):
    m = d.matches.sort_values("ts", ascending=False).head(n)
    table = pd.DataFrame({
        "When": m["when"], "Type": m["match_type"].map(labels.MATCH_TYPES), "Opponent": m["opp_name"],
        "Score": m["goals_for"].astype(int).astype(str) + " - " + m["goals_against"].astype(int).astype(str),
        "Result": m["result"],
        "Opp. SR": [sr_diff_text(v) for v in m.get("sr_diff", pd.Series(np.nan, index=m.index))],
        "Team grade": [grade_text(v) for v in m.get("team_grade_score", pd.Series(np.nan, index=m.index))]})
    ev = st.dataframe(table, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                      key="home_recent", column_config={"When": st.column_config.DatetimeColumn(format="ddd D MMM, HH:mm")})
    sel = ev.selection.rows if ev and ev.selection else []
    if sel:
        mid = str(m.iloc[sel[0]]["match_id"])
        st.session_state.pop("home_recent", None)   # coming back shouldn't jump to the report again
        st.switch_page(PAGES["matches"], query_params={"match": mid})


def page_start():
    d = data()
    m = d.matches
    if m.empty:
        hero(d)
        st.html("<div style='height:14px'></div>")
        empty_state("⚽", "No matches yet",
                    "Play a league or playoff match with your club: it shows up here a minute or two after the final "
                    "whistle. EA's last 10 matches of each type are fetched when the collector starts.")
        collector_card()
        return
    now = pd.Timestamp(os.environ.get("CLUBS_NOW") or dt.datetime.now())   # CLUBS_NOW: try another moment (tests)
    ps = playoffs.status(m, db.club_snapshots_df(CLUB_ID), now)
    if ps:
        playoffs_card(d, ps, now)
    season_card(d, now)
    a, b = st.columns([1.35, 1], gap="medium")
    with a:
        match_card(d, m.iloc[-1]["match_id"], team=True)
    with b:
        team_session_card(d)
        collector_card(compact=True)
    section("Recent matches", "The latest first: tap one for its report.")
    recent_matches(d)
    section("Top performers", "The last 10 matches.")
    top_performers(d)


def page_team():
    d = data()
    hero(d)
    m = d.matches
    if m.empty:
        st.html("<div style='height:14px'></div>")
        empty_state("⚽", "No matches yet",
                    "Play a league or playoff match with your club: it shows up here a minute or two after the final "
                    "whistle. EA's last 10 matches of each type are fetched when the collector starts.")
        collector_card()
        return
    section("The team", f"{plural(len(m), 'match', 'matches')} tracked · team grade " + info_icon(team_grade_info()))
    team_kpis(d)
    a, b = st.columns([1.35, 1], gap="medium")
    with a:
        match_card(d, m.iloc[-1]["match_id"], team=True)
    with b:
        team_session_card(d)
        collector_card(compact=True)
    section("The squad", "Everyone who played in the last 30 matches: overall grade (best first), form over the last 5 "
                         "matches and their numbers. " + info_icon(overall_info()))
    squad_cards(d)
    a, b = st.columns(2, gap="medium")
    with a:
        fig = grade_chart(m.tail(40), "team_grade_score", "Team grade per match")
        chart(fig if fig else results_chart(m, 40))
    with b:
        chart(points_chart(m))
    a, b = st.columns(2, gap="medium")
    with a:
        chart(results_chart(m, 40))
    with b:
        chart(goals_chart(m))
    section("Top performers", "The last 10 matches.")
    top_performers(d)


# ================================================================= PAGE: YOU
def page_home():
    d = data()
    viewer_picker(d)
    hero(d)
    m = d.matches
    if m.empty:
        st.html("<div style='height:14px'></div>")
        empty_state("⚽", "No matches yet",
                    "Play a league or playoff match with your club: it shows up here a minute or two after the final "
                    "whistle. EA's last 10 matches of each type are fetched when the collector starts.")
        collector_card()
        return
    if UI["home_kpis"]:
        section("You", f"{esc(viewer()[1])} · all tracked matches")
        my_kpis(d)
    a, b = st.columns([1.35, 1], gap="medium")
    with a:
        if UI["home_last_match"]:
            match_card(d, m.iloc[-1]["match_id"])
    with b:
        sess = analytics.session_table(m, CFG.get("session_gap_minutes", 45))
        if not sess.empty:
            s = sess.iloc[0]
            mine = d.me[(d.me["when"] >= s["start"]) & (d.me["when"] <= s["end"])] if not d.me.empty else pd.DataFrame()
            with st.container(border=True):
                st.html(f'<div class="eyebrow">Latest session</div><div class="mini">{when_str(s["start"])} - '
                        f'{when_str(s["end"], False)}</div>')
                kpis([{"label": "Matches", "value": fmt(s["matches"]), "sub": f'{int(s["w"])}-{int(s["d"])}-{int(s["l"])}'},
                      {"label": "Points", "value": fmt(s["points"]), "sub": f'{s["ppg"]:.2f} a match',
                       "tone": "good" if s["ppg"] >= 1.5 else "bad" if s["ppg"] < 1 else ""},
                      {"label": "Your G+A", "value": fmt(mine["ga"].sum()) if not mine.empty else "-",
                       "sub": f'{fmt(mine["goals"].sum())} G · {fmt(mine["assists"].sum())} A' if not mine.empty else ""}],
                     cols=3)
        collector_card(compact=True)
    if UI["home_charts"]:
        n = 40
        a, b = st.columns(2, gap="medium")
        with a:
            fig = grade_chart(d.me.tail(n))
            if fig:
                chart(fig)
        with b:
            fig = ga_chart(d.me, n)
            if fig:
                chart(fig)
        a, b = st.columns(2, gap="medium")
        with a:
            fig = grade_chart(m.tail(n), "team_grade_score", "Team grades")
            chart(fig if fig else results_chart(m, n))
        with b:
            chart(points_chart(m))


# ================================================================= PAGE: MATCHES
STRENGTH_TEXT = {"Much stronger": "Much stronger opponent", "Stronger": "Stronger opponent", "Even": "Even opponent",
                 "Weaker": "Weaker opponent", "Much weaker": "Much weaker opponent"}
STRENGTH_TONE = {"Much stronger": "bad", "Stronger": "draw", "Even": "muted", "Weaker": "good", "Much weaker": "good"}


def yes(v):
    return (not isna(v)) and bool(v)


def txt(v):
    """A text value, or None for missing ones (NaN)."""
    return v if isinstance(v, str) and v else None


def sr_diff_text(v):
    """'▲ +112' (stronger than you), '▼ -89' (weaker) or '= +12' (even), for tables."""
    if isna(v):
        return ""
    v = int(round(v))
    return f"▲ +{v}" if v >= scout.EVEN else f"▼ {v}" if v <= -scout.EVEN else f"= {v:+d}"


def strength_chips(row):
    if isna(row.get("sr_diff")):
        return []
    s_ = row.get("strength")
    out = [f'<span class="chip {STRENGTH_TONE.get(s_, "")}" title="Their skill rating minus yours, when they were '
           f'scouted">{esc(STRENGTH_TEXT.get(s_, s_))} · SR {int(row["opp_sr"])} ({int(row["sr_diff"]):+d})</span>']
    if row.get("verdict") == "Upset win":
        out.append(chip("Upset win", "good"))
    elif row.get("verdict") == "Upset loss":
        out.append(chip("Upset loss", "bad"))
    return out


def scout_info():
    return ('<div class="it-h">Scouting and opponent strength</div>'
            '<div>A few minutes after each match the collector pulls the other club\'s last 10 games (league and '
            'playoffs) and EA\'s numbers for the club. <b>Strength</b> is their skill rating (SR, the number EA uses '
            'to pair clubs) minus yours: +50 or more is a stronger club, +150 a much stronger one, and -50 and -150 '
            'the same for weaker clubs.</div>'
            '<p>It counts in the grades: every 100 SR they have above you adds 4 to the team grade (up to 10) and 3 '
            'to each player\'s grade (up to 8). A weaker club takes the same off. Beating a stronger club is an '
            '<b>upset win</b>, losing to a weaker one an <b>upset loss</b>.</p>'
            '<p><b>Their form</b>, goals, humans and players come from their last 10 games before your match, and '
            '<b>their level</b> is their average team grade in those games.</p>'
            '<div class="it-note">EA keeps only a club\'s last 10 games of each type. For older matches (or when the '
            'collector was off for a while) their games after the match are used instead, and the report says so.'
            '</div>')


def scout_games_table(games):
    if games.empty:
        return '<div class="mini">No games.</div>'
    rows = []
    for r in games.sort_values("ts", ascending=False).itertuples():
        when = dt.datetime.fromtimestamp(int(r.ts)) if not isna(r.ts) else None
        tg = getattr(r, "team_grade_score", np.nan)
        rows.append(f'<tr><td class="dim">{when.strftime("%d %b %H:%M") if when else "-"}</td>'
                    f'<td><span class="sc-club">{crest(CRESTS.get(str(r.opp_id)), 20)}<span>{esc(r.opp_name or "?")}'
                    f'</span></span></td><td><b>{int(r.goals_for)}-{int(r.goals_against)}</b></td>'
                    f'<td>{result_pill(r.result)}</td>'
                    f'<td class="dim">{int(r.our_players or 0)} v {int(r.opp_players or 0)}</td>'
                    f'<td>{grade_cell(tg)}</td></tr>')
    head = "<tr><th>When</th><th>Against</th><th>Score</th><th></th><th>Humans</th><th>Grade</th></tr>"
    return f'<div class="tbl-wrap"><table class="lineup scout-games">{head}{"".join(rows)}</table></div>'


def scout_players_table(pl, vs_ids):
    if pl.empty:
        return '<div class="mini">No players listed.</div>'
    rows = []
    for pid, r in pl.head(14).iterrows():
        color = labels.POS_COLORS.get(r["pos"], "#888")
        tag = '<span class="tag-vs">vs you</span>' if str(pid) in vs_ids else ""
        rows.append(f'<tr><td><span class="pname">{esc(r["name"])}</span>{tag}</td>'
                    f'<td><span class="pos" style="background:{color}">{labels.pos_short(r["pos"])}</span></td>'
                    f'<td>{int(r["matches"])}</td><td>{int(r["goals"] or 0)}</td><td>{int(r["assists"] or 0)}</td>'
                    f'<td><span class="rt {rating_tone(r["rating"])}">{fmt(r["rating"], 1)}</span></td>'
                    f'<td>{int(r["mom"] or 0) or "-"}</td><td>{grade_cell(r["grade"], "Average match grade")}</td></tr>')
    head = "<tr><th>Player</th><th>Pos</th><th>Games</th><th>G</th><th>A</th><th>Rating</th><th>MOTM</th><th>Grade</th></tr>"
    return f'<div class="tbl-wrap"><table class="lineup">{head}{"".join(rows)}</table></div>'


def going_in(row):
    """(before kick-off label, how it went) from the strength and the result."""
    e, v, res = txt(row.get("expectation")), txt(row.get("verdict")), row.get("result")
    if v:
        return e, v
    if e == "Underdogs":
        return e, {"L": "as expected", "D": "a good draw"}.get(res, "")
    if e == "Favourites":
        return e, {"W": "as expected", "D": "points dropped"}.get(res, "")
    return e, ""


def scouting_block(d, row):
    opp = row.get("opp_name") or "the other club"
    section(f"Scouting: {opp}", "Their last 10 games and EA's numbers for them, pulled a few minutes after the match. "
            "Hover the (i) for how strength works " + info_icon(scout_info(), up=True))
    sc = getattr(d, "scouting", None)
    if sc is None or not yes(row.get("scouted")):
        failed = not isna(row.get("scouted"))
        with st.container(border=True):
            st.html('<div class="mini">' + (
                "EA didn't send their games yet: the collector tries again at its next checks." if failed else
                "Not scouted yet. While the collector runs, it pulls the other club's last 10 games a few minutes "
                "after each match, and older matches fill in over its next checks.") + "</div>")
            if st.button("Pull their last 10 games now", key=f"scout_{row['match_id']}", icon=":material/travel_explore:"):
                with st.spinner(f"Asking EA about {opp}..."):
                    try:
                        res = scout.scout_match(CFG, row["match_id"], row["opp_id"])
                    except Exception as e:
                        res = {"ok": False, "n": 0, "errors": [f"{type(e).__name__}: {e}"]}
                if res["ok"]:
                    flash(f"Scouted {opp}: {res['n']} of their games.", "🔎")
                else:
                    flash("EA didn't answer: " + "; ".join(res["errors"][-1:])[:200], "⚠️")
                st.rerun()
        return
    games, basis = scout.games_for(sc, row["opp_id"], row["match_id"], row["ts"])
    s_ = scout.summary(games)
    diff, lvl = row.get("sr_diff"), row.get("opp_level")
    label, how = going_in(row)
    n_ = s_.get("n", 0)
    kpis([
        {"label": "Their strength", "value": esc(txt(row.get("strength")) or "-"),
         "sub": (f'SR {int(row["opp_sr"])} vs your {int(row["our_sr"])} ({int(diff):+d})' if not isna(diff)
                 else "EA sent no skill rating"),
         "tone": {"Much stronger": "bad", "Stronger": "gold", "Weaker": "good", "Much weaker": "good"}.get(row.get("strength"), "")},
        {"label": "Before kick-off", "value": esc(label or "-"), "sub": esc(how or ""),
         "tone": "good" if row.get("verdict") == "Upset win" else "bad" if row.get("verdict") == "Upset loss" else ""},
        {"label": "Their form", "value": f'{s_["w"]}-{s_["d"]}-{s_["l"]}' if n_ else "-",
         "sub": f'{s_["ppg"]:.2f} points a game' if n_ else ""},
        {"label": "Their goals", "value": f'{s_["gf"]:.1f} - {s_["ga"]:.1f}' if n_ else "-", "sub": "scored - conceded a game"},
        {"label": "Their level", "value": (f'{grades.letter(lvl)} <span style="font-size:.6em;opacity:.7">{fmt(lvl)}</span>'
                                           if not isna(lvl) else "-"),
         "sub": "their usual team grade", "help": "Their average team grade in these games (pulled towards 50 when there are few)."},
        {"label": "Their humans", "value": fmt(s_.get("humans"), 1) if n_ else "-",
         "sub": f"a game (against you: {int(row['opp_players'] or 0)})"},
    ], cols=6)
    when = when_str(pd.Timestamp(row["scout_at"])) if not isna(row.get("scout_at")) else "-"
    note = (f"their {plural(n_, 'game')} before this match" if basis == "before" else
            f"their {plural(n_, 'game')} after this match: EA no longer had the ones before it when they were scouted")
    vs_ids = set(d.players[(d.players["match_id"] == row["match_id"]) & (d.players["side"] == "opp")]["player_id"].astype(str))
    if st.button(f"Their profile now (Search)", key=f"prof_{row['match_id']}", icon=":material/search:"):
        st.switch_page(PAGES["search"], query_params={"club": str(row["opp_id"])})
    a, b = st.columns([1.1, 1], gap="medium")
    with a:
        st.html(f'<div class="mini" style="margin:8px 0 6px 0">Last games: {esc(note)}. Scouted {esc(when)}.</div>')
        with st.container(border=True):
            st.html(scout_games_table(games))
    with b:
        st.html('<div class="mini" style="margin:8px 0 6px 0">Their players in those games (most games first)</div>')
        with st.container(border=True):
            st.html(scout_players_table(scout.their_players(sc, games, row["opp_id"]), vs_ids))


def team_compare(row, lines):
    us, op = lines[lines["side"] == "us"], lines[lines["side"] == "opp"]

    def tot(df, c):
        return df[c].sum() if not df.empty else np.nan

    def rate(df, a, b):
        x, y = tot(df, a), tot(df, b)
        return 100.0 * x / y if y else np.nan
    rows = [("Goals", row["goals_for"], row["goals_against"], 0, ""),
            ("Shots", tot(us, "shots"), tot(op, "shots"), 0, ""),
            ("Passes", tot(us, "passes_made"), tot(op, "passes_made"), 0, ""),
            ("Pass accuracy", rate(us, "passes_made", "pass_attempts"), rate(op, "passes_made", "pass_attempts"), 0, "%"),
            ("Tackles won", tot(us, "tackles_made"), tot(op, "tackles_made"), 0, ""),
            ("Tackle success", rate(us, "tackles_made", "tackle_attempts"), rate(op, "tackles_made", "tackle_attempts"), 0, "%"),
            ("Saves", tot(us, "saves"), tot(op, "saves"), 0, ""),
            ("Avg rating", us["rating"].mean(), op["rating"].mean(), 1, ""),
            ("Players", row["our_players"], row["opp_players"], 0, "")]
    out = []
    for label, a, b, d_, suf in rows:
        a_, b_ = (0 if isna(a) else float(a)), (0 if isna(b) else float(b))
        tot_ = a_ + b_
        wa, wb = (100 * a_ / tot_, 100 * b_ / tot_) if tot_ else (0, 0)
        ca, cb = (VOLT, "#5c6b65") if a_ >= b_ else ("#5c6b65", BAD)
        out.append(f'<div class="cmp-row"><div class="cmp-l">{fmt(a, d_, suf)}</div>'
                   f'<div class="cmp-bar l"><div style="width:{wa:.0f}%;background:{ca}"></div></div>'
                   f'<div class="cmp-label">{esc(label)}</div>'
                   f'<div class="cmp-bar"><div style="width:{wb:.0f}%;background:{cb}"></div></div>'
                   f'<div class="cmp-r">{fmt(b, d_, suf)}</div></div>')
    return '<div class="cmp">' + "".join(out) + "</div>"


def lineup_table(lines, me_id=None):
    df = lines.assign(_o=lines["pos"].map(labels.POS_ORDER).fillna(9)).sort_values(["_o", "rating"], ascending=[True, False])
    head = ("<tr><th>Player</th><th>Pos</th><th>Archetype</th><th>Rating</th><th>G</th><th>A</th><th>Shots</th>"
            "<th>Passes</th><th>Tackles</th><th>Saves</th><th>Min</th><th>Grade</th></tr>")
    rows = []
    for _, r in df.iterrows():
        color = labels.POS_COLORS.get(r["pos"], "#888")
        name = esc(r["name"]) + (' <span class="star" title="Man of the match">★</span>' if r["mom"] else "") \
            + ('<span class="tag-left">left early</span>' if r["left_early"] else "")
        dev = labels.device(r["device"])
        passes = (f'{int(r["passes_made"] or 0)}/{int(r["pass_attempts"] or 0)}'
                  + (f' <span class="dim">{r["pass_pct"]:.0f}%</span>' if not isna(r["pass_pct"]) else ""))
        tackles = f'{int(r["tackles_made"] or 0)}/{int(r["tackle_attempts"] or 0)}'
        rows.append(f'<tr class="{"me" if me_id and str(r["player_id"]) == str(me_id) else ""}">'
                    f'<td><span class="pname">{name}</span> <small class="dim">{esc(dev)}</small></td>'
                    f'<td><span class="pos" style="background:{color}">{labels.pos_short(r["pos"])}</span></td>'
                    f'<td class="dim">{esc(arch_name(r["archetype"]))}</td>'
                    f'<td><span class="rt {rating_tone(r["rating"])}">{fmt(r["rating"], 1)}</span></td>'
                    f'<td>{int(r["goals"] or 0)}</td><td>{int(r["assists"] or 0)}</td><td>{int(r["shots"] or 0)}</td>'
                    f'<td>{passes}</td><td>{tackles}</td><td>{int(r["saves"] or 0) or "-"}</td>'
                    f'<td>{fmt(r["minutes"])}</td><td>{grade_cell(r.get("grade_score"), grade_title(r))}</td></tr>')
    return f'<div class="tbl-wrap"><table class="lineup">{head}{"".join(rows)}</table></div>'


def score_ring(v):
    c = _score_color(v)
    deg = 0 if isna(v) else 3.6 * v
    return (f'<div class="score-ring"><div class="ring" style="background:conic-gradient({c} {deg}deg, '
            f'rgba(255,255,255,0.07) {deg}deg)"><div class="ring-in"><b>{fmt(v)}</b>'
            f'<span style="color:{c}">{esc(score.grade(v) or "")}</span></div></div></div>')


def performance_block(me_row, n_mine=0):
    if me_row is None or isna(me_row.get("grade_score")):
        return
    pos = me_row["pos"] if me_row["pos"] in grades.WEIGHTS else "midfielder"
    section("Your match grade", f"Graded as a {labels.position(pos).lower()}: {int(me_row['grade_score'])} means better than "
                                f"{int(me_row['grade_score'])}% of FC 27 Clubs performances in that position."
                                + info_icon(grade_info(pos), up=True))
    with st.container(border=True):
        a, b, c = st.columns([0.8, 1.4, 1.3], gap="large")
        with a:
            st.html(grade_ring(me_row["grade_score"], f"{labels.position(pos)} grade"))
        with b:
            w = grades.WEIGHTS[pos]
            rows = [(f"{p} ({int(w[p] * 100)}%)", me_row.get(f"gp_{p}"), grades.PART_HELP[p]) for p in grades.PARTS if p in w]
            extra = []
            if me_row.get("mom"):
                extra.append("+8 for man of the match")
            if me_row.get("red_cards"):
                extra.append("-10 for a red card")
            adj = me_row.get("str_adj")
            if not isna(adj) and abs(adj) >= 0.5:
                extra.append(f"{adj:+.0f} for a {'stronger' if adj > 0 else 'weaker'} opponent")
            st.html(bar_rows(rows) + (f'<div class="mini" style="margin-top:8px">{esc(", ".join(extra))}</div>' if extra else ""))
        with c:
            st.html('<div class="mini" style="margin-bottom:4px">Compared with your own other matches</div>')
            if isna(me_row.get("score")):
                st.html(f'<div class="mini">Appears once you have {score.MIN_OWN} tracked matches ({n_mine} so far).</div>')
            else:
                best, worst = score.highlights(me_row, me_row)
                items = [f'<div class="hl"><span>Overall vs your usual</span>{score_badge(me_row["score"], "VS YOU")}</div>']
                for name, p in best:
                    items.append(f'<div class="hl"><span>{esc(name)}</span><span class="up">better than {p:.0f}%</span></div>')
                for name, p in worst:
                    items.append(f'<div class="hl"><span>{esc(name)}</span><span class="down">worse than {100 - p:.0f}%</span></div>')
                st.html("".join(items))


def team_grade_block(row):
    if isna(row.get("team_grade_score")):
        return
    with st.container(border=True):
        a, b = st.columns([0.8, 1.5], gap="medium")
        with a:
            st.html(grade_ring(row["team_grade_score"], "Team grade"))
        with b:
            weights = {"Result": 35, "Team rating": 25, "Attack": 15, "Defence": 15, "Passing": 10}
            rows = [(f"{p} ({weights[p]}%)", row.get(f"team_{p}"), grades.TEAM_HELP[p]) for p in grades.TEAM_PARTS]
            adj = row.get("team_humans_adj")
            note = ""
            if not isna(adj) and adj:
                note = (f'+{adj:.0f} for playing with fewer humans than them' if adj > 0
                        else f'{adj:.0f} for playing with more humans than them')
            sadj = row.get("team_str_adj")
            if not isna(sadj) and abs(sadj) >= 0.5:
                note = (note + " · " if note else "") + (
                    f'{sadj:+.0f} for playing a {"stronger" if sadj > 0 else "weaker"} club '
                    f'(SR {int(row["sr_diff"]):+d})')
            opp = row.get("opp_team_grade_score")
            st.html(bar_rows(rows) + (f'<div class="mini" style="margin-top:8px">{esc(note)}</div>' if note else "")
                    + (f'<div class="mini" style="margin-top:6px">{esc(row.get("opp_name") or "Them")}: '
                       f'{grade_badge(opp, "TEAM")}</div>' if not isna(opp) else ""))


def match_report(d, match_id):
    m = d.matches
    pos_ = m.index[m["match_id"] == match_id]
    if len(pos_) == 0:
        st.warning("That match isn't in the database.")
        return
    i = list(m.index).index(pos_[0])
    row = m.iloc[i]
    lines = d.ours[d.ours["match_id"] == match_id]
    opp_lines = d.players[(d.players["match_id"] == match_id) & (d.players["side"] == "opp")]
    all_lines = d.players[d.players["match_id"] == match_id]
    me_row = d.me[d.me["match_id"] == match_id]
    me_row = me_row.iloc[0] if not me_row.empty else None
    nav = st.columns([1, 1, 6])
    with nav[0]:
        if i > 0 and st.button("Previous", icon=":material/chevron_left:", key="prev", width="stretch"):
            st.query_params["match"] = str(m.iloc[i - 1]["match_id"])
            st.rerun()
    with nav[1]:
        if i < len(m) - 1 and st.button("Next", icon=":material/chevron_right:", key="next", width="stretch"):
            st.query_params["match"] = str(m.iloc[i + 1]["match_id"])
            st.rerun()
    with st.container(border=True):
        chips_ = [chip(labels.match_type(row["match_type"]), "volt"), chip(f'Match {int(row["game_no"])}'),
                  chip(f'{int(row["our_players"] or 0)} v {int(row["opp_players"] or 0)} players')]
        if row["they_quit"]:
            chips_.append(chip("The other club quit", "good"))
        if row["we_quit"]:
            chips_.append(chip("We didn't finish", "bad"))
        chips_ += strength_chips(row)
        st.html(f'<div class="mc-top"><div class="chips">{"".join(chips_)}</div><div>{when_str(row["when"])} · '
                f'{ago(row["when"])}</div></div>' + scoreline_html(row, crest_of(row["opp_id"]), crest_of(CLUB_ID))
                + f'<div class="scorers">{scorers_html(all_lines)}</div>'
                + (f'<div class="scorers">{scorers_html(all_lines, "opp").replace("⚽", "Them: ⚽")}</div>'
                   if scorers_html(all_lines, "opp") else ""))
    a, b = st.columns([1, 1.45], gap="medium")
    with a:
        section("Team grade", "How the team did, compared with real FC 27 Clubs team performances."
                + info_icon(team_grade_info()))
        team_grade_block(row)
        section("Team stats")
        with st.container(border=True):
            st.html(team_compare(row, all_lines))
    with b:
        section(CFG.get("club_name") or "Us", "Only human players are listed: EA doesn't include AI teammates. "
                f"Grades depend on the position played: hover a grade for its breakdown, or the (i) {info_icon(grade_info())}")
        with st.container(border=True):
            st.html(lineup_table(lines, d.me_id))
        section(row["opp_name"] or "Opponent")
        with st.container(border=True):
            st.html(lineup_table(opp_lines) if not opp_lines.empty else '<div class="mini">No players listed.</div>')
    scouting_block(d, row)
    performance_block(me_row, len(d.me))
    with st.expander("Raw data" if ONLINE else "Notes and raw data"):
        c1, c2 = st.columns([1, 1])
        with c1:
            if not ONLINE:
                note = st.text_area("Notes for this match", value=row.get("notes") or "", key=f"note_{match_id}",
                                    placeholder="e.g. lag, new formation, played with randoms")
                if st.button("Save note", key=f"savenote_{match_id}", icon=":material/save:"):
                    db.set_note(match_id, CLUB_ID, note)
                    flash("Note saved.", "📝")
                    st.rerun()
        with c2:
            raw = db.raw_match(match_id, CLUB_ID)
            if raw:
                st.download_button("Download EA's data (JSON)", json.dumps(raw, indent=1), f"match_{match_id}.json",
                                   "application/json", icon=":material/download:")


def page_matches():
    d = data()
    m = d.matches
    page_head("Matches", f"{plural(len(m), 'match', 'matches')} tracked. Click a match for its full report.")
    if m.empty:
        empty_state("📋", "No matches yet", "They show up here once the collector has saved your first match.")
        return
    f1, f2, f3, f4 = st.columns([1.6, 1.2, 1.4, 1])
    with f1:
        types = st.segmented_control("Type", ["All", "League", "Playoffs", "Friendly"], default="All", key="mt_filter")
    with f2:
        res = st.segmented_control("Result", ["All", "W", "D", "L"], default="All", key="res_filter")
    with f3:
        q = st.text_input("Opponent", placeholder="Search a club", key="opp_filter")
    with f4:
        only_me = st.toggle("Only matches I played", value=False, key="only_me")
    view = m.copy()
    tmap = {"League": "leagueMatch", "Playoffs": "playoffMatch", "Friendly": "friendlyMatch"}
    if types and types != "All":
        view = view[view["match_type"] == tmap[types]]
    if res and res != "All":
        view = view[view["result"] == res]
    if q:
        view = view[view["opp_name"].fillna("").str.contains(q, case=False, regex=False)]
    if only_me:
        view = view[view["me_played"]]
    view = view.sort_values("ts", ascending=False)
    table = pd.DataFrame({
        "When": view["when"], "Type": view["match_type"].map(labels.MATCH_TYPES),
        "Opponent": view["opp_name"], "Score": view["goals_for"].astype(str) + " - " + view["goals_against"].astype(str),
        "Result": view["result"], "Players": view["our_players"].astype(str) + " v " + view["opp_players"].astype(str),
        "Strength": [sr_diff_text(v) for v in view.get("sr_diff", pd.Series(np.nan, index=view.index))],
        "Team": [grade_text(v) for v in view.get("team_grade_score", pd.Series(np.nan, index=view.index))],
        "You": [grade_text(v) for v in view.get("my_grade_score", pd.Series(np.nan, index=view.index))],
        "Rating": view.get("my_rating"), "G": view.get("my_goals"), "A": view.get("my_assists"),
        "Note": view["notes"].notna().map({True: "📝", False: ""}),
    })
    st.html(f'<div class="mini" style="margin:2px 0 6px 0">Team grade {info_icon(team_grade_info())}'
            f'<span style="margin:0 10px">·</span>Your grade, based on the position you played {info_icon(grade_info())}'
            f'<span style="margin:0 10px">·</span>Opponent strength {info_icon(scout_info())}</div>')
    ev = st.dataframe(table, hide_index=True, width="stretch", height=min(460, 38 + 35 * len(table)),
                      on_select="rerun", selection_mode="single-row", key="match_table",
                      column_config={
                          "When": st.column_config.DatetimeColumn(format="ddd D MMM, HH:mm"),
                          "Rating": st.column_config.NumberColumn("Your rating", format="%.1f"),
                          "G": st.column_config.NumberColumn("Goals", format="%d"),
                          "A": st.column_config.NumberColumn("Assists", format="%d"),
                          "Team": st.column_config.TextColumn("Team grade"),
                          "Strength": st.column_config.TextColumn(
                              "Opp. SR", help="The other club's skill rating minus yours: ▲ stronger, ▼ weaker, = even"),
                          "You": st.column_config.TextColumn("Your grade"),
                      })
    sel = ev.selection.rows if ev and ev.selection else []
    if sel:
        mid = str(view.iloc[sel[0]]["match_id"])
        if st.query_params.get("match") != mid:
            st.query_params["match"] = mid
    mid = st.query_params.get("match") or str(m.iloc[-1]["match_id"])
    st.html("<div style='height:6px'></div>")
    match_report(d, mid)


# ================================================================= PAGE: STATS
def where_you_play_best(me):
    """Your average match grade by position and by archetype (only where you've played more than one)."""
    if me.empty or "grade_score" not in me:
        return
    parts = []
    for by, title, fmt_fn in (("pos", "Your grade by position", labels.position), ("archetype", "Your grade by archetype", arch_name)):
        g = me.dropna(subset=[by]).groupby(by).agg(grade=("grade_score", "mean"), matches=("match_id", "nunique"),
                                                   rating=("rating", "mean")).sort_values("grade", ascending=False)
        if len(g) >= 2:
            parts.append((title, g, fmt_fn))
    if not parts:
        return
    cols = st.columns(2, gap="medium")
    for col, (title, g, fmt_fn) in zip(cols, parts):
        with col:
            fig = go.Figure(go.Bar(x=[fmt_fn(i) for i in g.index], y=g["grade"], marker_color=[grade_color(v) for v in g["grade"]],
                                   text=[f"{grades.letter(v)} {v:.0f} · {plural(int(n_), 'match', 'matches')}"
                                         for v, n_ in zip(g["grade"], g["matches"])], textposition="outside",
                                   cliponaxis=False, customdata=np.stack([g["matches"], g["rating"]], axis=1),
                                   hovertemplate="%{x}: grade <b>%{y:.0f}</b><br>%{customdata[0]} matches, "
                                                 "rating %{customdata[1]:.2f}<extra></extra>"))
            fig.update_layout(title=title, yaxis=dict(range=[0, 108], title="Average match grade"), showlegend=False)
            chart(fig, height=300)


def stats_you(d):
    me = d.me
    if me.empty:
        empty_state("🧍", "No matches for you yet", pick_hint())
        return
    my_kpis(d)
    a, b = st.columns(2, gap="medium")
    with a:
        chart(rating_chart(me))
    with b:
        fig = grade_chart(me)
        if fig:
            chart(fig)
    a, b = st.columns(2, gap="medium")
    with a:
        chart(ga_chart(me))
    with b:
        df = me
        fig = go.Figure()
        fig.add_trace(go.Bar(x=df["game_no"], y=df["shots"], name="Shots", marker_color="#3a4a43", hovertext=_hover(df),
                             hovertemplate="%{hovertext}<br>Shots <b>%{y}</b><extra></extra>"))
        fig.add_trace(go.Bar(x=df["game_no"], y=df["goals"], name="Goals", marker_color=VOLT, hoverinfo="skip"))
        conv = analytics.rolling(df["goals"], 10) / analytics.rolling(df["shots"], 10).replace(0, np.nan) * 100
        fig.add_trace(go.Scatter(x=df["game_no"], y=conv, name="Conversion (last 10)", yaxis="y2", mode="lines",
                                 line=dict(color=GOLD, width=3), hovertemplate="Conversion %{y:.0f}%<extra></extra>"))
        fig.update_layout(title="Shooting", barmode="overlay", xaxis_title="Match",
                          yaxis2=dict(overlaying="y", side="right", range=[0, 100], ticksuffix="%", showgrid=False,
                                      tickmode="linear", dtick=25))
        chart(few_matches(fig, df))
    a, b = st.columns(2, gap="medium")
    with a:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=me["game_no"], y=me["pass_pct"], mode="markers", name="Pass accuracy",
                                 marker=dict(size=8, color="#4f8ff7", opacity=0.6), text=_hover(me),
                                 hovertemplate="%{text}<br>%{y:.0f}% of passes<extra></extra>"))
        fig.add_trace(go.Scatter(x=me["game_no"], y=analytics.rolling(me["pass_pct"], UI["rolling_window"]), mode="lines",
                                 name=f"Average of last {UI['rolling_window']}", line=dict(color=VOLT, width=3), hoverinfo="skip"))
        fig.update_layout(title="Passing accuracy", yaxis=dict(ticksuffix="%", range=[0, 103]), xaxis_title="Match")
        chart(few_matches(fig, me))
    with b:
        fig = go.Figure()
        fig.add_trace(go.Bar(x=me["game_no"], y=me["tackle_attempts"], name="Tackles tried", marker_color="#3a4a43",
                             hovertext=_hover(me), hovertemplate="%{hovertext}<br>Tried <b>%{y}</b><extra></extra>"))
        fig.add_trace(go.Bar(x=me["game_no"], y=me["tackles_made"], name="Tackles won", marker_color=BLUE, hoverinfo="skip"))
        fig.update_layout(title="Defending", barmode="overlay", xaxis_title="Match")
        chart(few_matches(fig, me))
    section("By position, archetype and match type")
    where_you_play_best(me)
    for by, label, fmt_fn in (("pos", "Position", labels.position), ("archetype", "Archetype", arch_name),
                              ("match_type", "Match type", labels.match_type)):
        g = analytics.group_stats(me, by)
        if g.empty:
            continue
        g.index = [fmt_fn(i) for i in g.index]
        g.index.name = label
        st.dataframe(g.reset_index(), hide_index=True, width="stretch", column_config={
            "matches": "Matches", "rating": st.column_config.NumberColumn("Avg rating", format="%.2f"),
            "goals_pm": st.column_config.NumberColumn("Goals / match", format="%.2f"),
            "assists_pm": st.column_config.NumberColumn("Assists / match", format="%.2f"),
            "ga_pm": st.column_config.NumberColumn("G+A / match", format="%.2f"),
            "shots_pm": st.column_config.NumberColumn("Shots / match", format="%.1f"),
            "pass_pct": st.column_config.NumberColumn("Pass %", format="%.0f%%"),
            "tackle_pct": st.column_config.NumberColumn("Tackle %", format="%.0f%%"),
            "win_pct": st.column_config.NumberColumn("Win %", format="%.0f%%"), "mom": "MOTM"})


def stats_club(d):
    m = d.matches
    rec = analytics.record(m)
    stk = analytics.streaks(m["result"])
    kpis([
        {"label": "Matches", "value": fmt(rec["n"]), "sub": f"{rec['w']}-{rec['d']}-{rec['l']}"},
        {"label": "Win rate", "value": pct(rec["win_pct"]), "tone": "good" if (rec["win_pct"] or 0) >= 50 else ""},
        {"label": "Points per match", "value": fmt(rec["ppg"], 2)},
        {"label": "Goals for", "value": fmt(rec["gf"]), "sub": f"{rec['gf'] / rec['n']:.2f} a match", "tone": "volt"},
        {"label": "Goals against", "value": fmt(rec["ga"]), "sub": f"{rec['ga'] / rec['n']:.2f} a match"},
        {"label": "Clean sheets", "value": fmt(rec["clean_sheets"])},
        {"label": "Best win run", "value": fmt(stk["best_win"]), "sub": f"unbeaten {stk['best_unbeaten']}"},
        {"label": "Opponents quit", "value": fmt(m["they_quit"].sum()), "sub": f"we didn't finish {int(m['we_quit'].sum())}"},
    ])
    if "team_grade_score" in m and m["team_grade_score"].notna().any():
        st.html(f'<div class="mini" style="margin-top:10px">How the team grade works {info_icon(team_grade_info())}</div>')
        a, b = st.columns([1.6, 1], gap="medium")
        with a:
            chart(grade_chart(m, "team_grade_score", "Team grade per match"))
        with b:
            chart(grade_mix_chart(m["team_grade_score"], "Team grades so far"))
    a, b = st.columns(2, gap="medium")
    with a:
        chart(results_chart(m))
    with b:
        chart(points_chart(m))
    a, b = st.columns(2, gap="medium")
    with a:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=m["game_no"], y=analytics.rolling(m["goals_for"], 10), name="Scored (last 10)",
                                 line=dict(color=VOLT, width=3)))
        fig.add_trace(go.Scatter(x=m["game_no"], y=analytics.rolling(m["goals_against"], 10), name="Conceded (last 10)",
                                 line=dict(color=BAD, width=3)))
        fig.update_layout(title="Goals per match", xaxis_title="Match")
        chart(few_matches(fig, m))
    with b:
        h = analytics.by_humans(m)
        if not h.empty:
            labels_ = [("≤" if i <= -4 else "≥+" if i >= 4 else "+" if i > 0 else "") + str(int(i)).replace("+", "")
                       for i in h.index]
            fig = go.Figure(go.Bar(x=labels_, y=h["win_pct"], marker_color=[GOOD if v >= 50 else DRAW if v >= 35 else BAD for v in h["win_pct"]],
                                   text=[f"{int(n)} matches" for n in h["matches"]], textposition="outside",
                                   hovertemplate="%{x} players: %{y:.0f}% won<br>%{text}<extra></extra>"))
            fig.update_layout(title="Win rate by player difference (us minus them)", xaxis_title="Humans: us minus them",
                              yaxis=dict(ticksuffix="%", range=[0, 110]))
            chart(fig)
    snaps = season_snaps(d, db.club_snapshots_df(CLUB_ID))
    if len(snaps) >= 2 and snaps["skill_rating"].notna().sum() >= 2:
        a, b = st.columns(2, gap="medium")
        with a:
            fig = go.Figure(go.Scatter(x=snaps["at"], y=snaps["skill_rating"], mode="lines+markers", line=dict(color=VOLT, width=3),
                                       hovertemplate="%{x|%d %b %H:%M}<br>Skill rating <b>%{y}</b><extra></extra>"))
            fig.update_layout(title="Skill rating over time")
            chart(fig)
        with b:
            div = pd.to_numeric(snaps["division"], errors="coerce")
            if div.notna().sum() >= 2:
                fig = go.Figure(go.Scatter(x=snaps["at"], y=div, mode="lines+markers", line=dict(color=BLUE, width=3, shape="hv"),
                                           text=[labels.division(x) for x in snaps["division"]],
                                           hovertemplate="%{x|%d %b}<br>%{text}<extra></extra>"))
                fig.update_layout(title="Division over time", yaxis=dict(autorange="reversed", dtick=1))
                chart(fig)
    elif len(snaps):
        st.caption("Skill rating and division charts appear once the collector has seen them change.")
    section("By match type")
    g = m.groupby("match_type").agg(matches=("match_id", "size"), w=("result", lambda r: (r == "W").sum()),
                                    d=("result", lambda r: (r == "D").sum()), l=("result", lambda r: (r == "L").sum()),
                                    gf=("goals_for", "mean"), ga=("goals_against", "mean"), ppg=("points", "mean"))
    g.index = [labels.match_type(i) for i in g.index]
    st.dataframe(g.reset_index(names="Type"), hide_index=True, width="stretch", column_config={
        "matches": "Matches", "w": "W", "d": "D", "l": "L",
        "gf": st.column_config.NumberColumn("Scored / match", format="%.2f"),
        "ga": st.column_config.NumberColumn("Conceded / match", format="%.2f"),
        "ppg": st.column_config.NumberColumn("Points / match", format="%.2f")})


def stats_teammates(d):
    t = analytics.teammates(d)
    if t.empty:
        empty_state("🤝", "No teammates yet", "Once you play with others, this shows how you do together.")
    else:
        section("Who you play best with", "Your results with each player on the pitch, and without them.")
        st.dataframe(t.drop(columns=["player_id"]), hide_index=True, width="stretch", column_config={
            "name": "Player", "together": "Matches together", "w": "W", "d": "D", "l": "L",
            "win_pct": st.column_config.NumberColumn("Win % together", format="%.0f%%"),
            "win_pct_without": st.column_config.NumberColumn("Win % without", format="%.0f%%"),
            "ppg": st.column_config.NumberColumn("Points / match", format="%.2f"),
            "gf_pm": st.column_config.NumberColumn("Scored / match", format="%.2f"),
            "ga_pm": st.column_config.NumberColumn("Conceded / match", format="%.2f"),
            "my_rating_with": st.column_config.NumberColumn("Your rating with", format="%.2f"),
            "my_rating_without": st.column_config.NumberColumn("Your rating without", format="%.2f"),
            "their_rating": st.column_config.NumberColumn("Their rating", format="%.2f"),
            "their_ga": "Their G+A", "pos": st.column_config.TextColumn("Usual position")})
        top = t.head(8)
        fig = go.Figure()
        fig.add_trace(go.Bar(x=top["name"], y=top["win_pct"], name="With them", marker_color=VOLT))
        fig.add_trace(go.Bar(x=top["name"], y=top["win_pct_without"], name="Without them", marker_color="#3a4a43"))
        fig.update_layout(title="Win rate with and without each teammate", barmode="group", yaxis=dict(ticksuffix="%"))
        chart(fig)
    lu = analytics.lineups(d)
    if not lu.empty:
        section("Line-ups", "Results by the exact group of club players on the pitch (2+ matches).")
        st.dataframe(lu, hide_index=True, width="stretch", column_config={
            "players": "Players", "size": "Humans", "matches": "Matches", "w": "W", "d": "D", "l": "L",
            "win_pct": st.column_config.NumberColumn("Win %", format="%.0f%%"),
            "ppg": st.column_config.NumberColumn("Points / match", format="%.2f"),
            "gf_pm": st.column_config.NumberColumn("Scored / match", format="%.2f"),
            "ga_pm": st.column_config.NumberColumn("Conceded / match", format="%.2f")})


def strength_charts(d, k):
    """Their strength match by match, the luck meter, every match as a dot and you next to the clubs you've played."""
    k = k.sort_values("game_no")
    hover = np.stack([k["opp_name"].fillna("").map(str), k["goals_for"], k["goals_against"], k["sr_diff"],
                      k["when"].dt.strftime("%a %d %b")], axis=1)
    a, b = st.columns(2, gap="medium")
    with a:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=k["game_no"], y=k["our_sr"], mode="lines", name="Your SR",
                                 line=dict(color=VOLT, width=3, shape="hv"),
                                 hovertemplate="Match %{x}: your SR <b>%{y:.0f}</b><extra></extra>"))
        fig.add_trace(go.Scatter(x=k["game_no"], y=k["opp_sr"], mode="markers", name="Their SR (colour: result)",
                                 marker=dict(size=10, color=[RESULT_COLORS.get(r) for r in k["result"]],
                                             line=dict(width=1, color="#0a0e0d")), customdata=hover,
                                 hovertemplate="%{customdata[0]}: <b>%{y:.0f}</b> SR (%{customdata[3]:+.0f})<br>"
                                               "%{customdata[1]}-%{customdata[2]} · %{customdata[4]}<extra></extra>"))
        fig.update_layout(title="Their skill rating and yours, match by match", xaxis_title="Club match",
                          yaxis_title="Skill rating")
        chart(few_matches(fig, k))
    with b:
        lk = analytics.luck(d.matches)
        if not lk.empty:
            diff = lk["points_cum"].iloc[-1] - lk["expected_cum"].iloc[-1]
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=lk["game_no"], y=lk["expected_cum"], mode="lines", name="Predicted",
                                     line=dict(color="#8a9a93", width=2, dash="dot"),
                                     hovertemplate="After match %{x}: <b>%{y:.1f}</b> points predicted<extra></extra>"))
            fig.add_trace(go.Scatter(x=lk["game_no"], y=lk["points_cum"], mode="lines", name="Actual",
                                     line=dict(color=VOLT, width=3), fill="tonexty",
                                     fillcolor="rgba(62,207,142,0.12)" if diff >= 0 else "rgba(242,92,84,0.12)",
                                     hovertemplate="After match %{x}: <b>%{y}</b> points<extra></extra>"))
            fig.update_layout(title=f"Luck meter: {diff:+.1f} points against the prediction", xaxis_title="Club match",
                              yaxis_title="Points, added up")
            chart(few_matches(fig, k))
            st.caption("The prediction comes from the skill ratings, like chess ratings: a club 100 SR stronger comes "
                       "out on top about 64% of the time, and draws happen as often as in your own matches. Above the "
                       "grey line means more points than the strength of your opponents predicted.")
    a, b = st.columns(2, gap="medium")
    with a:
        fig = go.Figure()
        for res, name in (("W", "Won"), ("D", "Drew"), ("L", "Lost")):
            g = k[k["result"] == res]
            if g.empty:
                continue
            fig.add_trace(go.Scatter(x=g["sr_diff"], y=g["gd"], mode="markers", name=name,
                                     marker=dict(size=12, color=RESULT_COLORS[res], opacity=0.85,
                                                 line=dict(width=1, color="#0a0e0d")),
                                     customdata=hover[k["result"].values == res],
                                     hovertemplate="%{customdata[0]} (%{x:+.0f} SR)<br>%{customdata[1]}-%{customdata[2]}"
                                                   " · %{customdata[4]}<extra></extra>"))
        fig.add_vline(x=0, line=dict(color="#3a4a43", width=1, dash="dot"))
        fig.add_hline(y=0, line=dict(color="#3a4a43", width=1))
        fig.update_layout(title="Every match: how strong they were and the goal difference",
                          xaxis_title="Their SR minus yours (right: stronger)", yaxis_title="Goal difference")
        chart(fig)
    with b:
        sc = getattr(d, "scouting", None)
        t, n_clubs = (analytics.lobby(d.matches, sc.games, sc.lines, CLUB_ID) if sc is not None and not sc.empty
                      else (pd.DataFrame(), 0))
        if not t.empty and n_clubs >= 5:
            t = t.iloc[::-1]
            ylab = [f"{esc(lb)}<br><span style='font-size:11px;color:#8a9a93'>you {fmt(o, dec_, suf)} · "
                    f"they {fmt(md, dec_, suf)}</span>" for lb, o, md, dec_, suf in
                    zip(t["label"], t["ours"], t["median"], t["dec"], t["suffix"])]
            fig = go.Figure(go.Bar(x=t["pct"], y=ylab, orientation="h", text=[f"{v:.0f}%" for v in t["pct"]],
                                   textposition="outside", cliponaxis=False,
                                   marker_color=[GOOD if v >= 60 else DRAW if v >= 40 else BAD for v in t["pct"]],
                                   hovertemplate="Better than <b>%{x:.0f}%</b> of them<extra></extra>"))
            fig.add_vline(x=50, line=dict(color="#5c6b65", width=1, dash="dot"))
            fig.update_layout(title=f"You next to the {n_clubs} clubs you've played",
                              xaxis=dict(range=[0, 112], tickvals=[0, 50, 100], ticksuffix="%",
                                         title="% of clubs below you"), showlegend=False)
            chart(fig, height=380)
            st.caption("Their numbers come from their own last games (scouting); yours from all your tracked matches. "
                       "Shots and tackles are per human player, and the team grade leaves out opponent strength.")


def strength_section(d):
    m = d.matches
    k = m.dropna(subset=["sr_diff"]) if "sr_diff" in m else m.iloc[0:0]
    section("By opponent strength", "Their skill rating (SR) minus yours, from scouting. " + info_icon(scout_info()))
    if k.empty:
        st.html('<div class="mini">Appears once the collector has scouted the clubs you played (a few minutes after '
                'each match; older matches fill in over its next checks).</div>')
        return
    up_w, up_l = k[k["verdict"] == "Upset win"], k[k["verdict"] == "Upset loss"]
    kpis([
        {"label": "Scouted", "value": f"{len(k)}", "sub": f"of {plural(len(m), 'match', 'matches')}"},
        {"label": "Average opponent", "value": f'{k["sr_diff"].mean():+.0f} SR', "sub": "their SR minus yours"},
        {"label": "Toughest", "value": f'{k["sr_diff"].max():+.0f} SR', "sub": esc(k.loc[k["sr_diff"].idxmax(), "opp_name"] or "")},
        {"label": "Upset wins", "value": fmt(len(up_w)), "sub": "beat a stronger club", "tone": "good"},
        {"label": "Upset losses", "value": fmt(len(up_l)), "sub": "lost to a weaker club", "tone": "bad"},
    ], cols=5)
    k = k.assign(strength=pd.Categorical(k["strength"], scout.BUCKETS, ordered=True))
    g = k.groupby("strength", observed=True).agg(
        matches=("match_id", "size"), w=("result", lambda r: (r == "W").sum()), d=("result", lambda r: (r == "D").sum()),
        l=("result", lambda r: (r == "L").sum()), ppg=("points", "mean"), gf=("goals_for", "mean"),
        ga=("goals_against", "mean"), grade=("team_grade_score", "mean"))
    g["win_pct"] = 100.0 * g["w"] / g["matches"]
    g = g[["matches", "w", "d", "l", "win_pct", "ppg", "gf", "ga", "grade"]]
    a, b = st.columns([1.25, 1], gap="medium")
    with a:
        st.dataframe(g.reset_index(names="Opponent"), hide_index=True, width="stretch", column_config={
            "Opponent": st.column_config.TextColumn("Opponent", help="Much stronger: +150 SR or more, stronger: +50, "
                                                    "even: within 50, weaker: -50, much weaker: -150"),
            "matches": "Matches", "w": "W", "d": "D", "l": "L",
            "ppg": st.column_config.NumberColumn("Points / match", format="%.2f"),
            "gf": st.column_config.NumberColumn("Scored / match", format="%.2f"),
            "ga": st.column_config.NumberColumn("Conceded / match", format="%.2f"),
            "grade": st.column_config.NumberColumn("Team grade (avg)", format="%.0f"),
            "win_pct": st.column_config.NumberColumn("Win %", format="%.0f%%")})
    with b:
        cols = {"Much stronger": BAD, "Stronger": "#f2924b", "Even": "#93a39c", "Weaker": "#7fd4a8", "Much weaker": GOOD}
        fig = go.Figure(go.Bar(x=list(g.index.astype(str)), y=g["ppg"], marker_color=[cols[str(i)] for i in g.index],
                               text=[plural(n_, "match", "matches") for n_ in g["matches"]], textposition="outside",
                               hovertemplate="%{x}: <b>%{y:.2f}</b> points a match<br>%{text}<extra></extra>", cliponaxis=False))
        fig.update_layout(title="Points per match by opponent strength", yaxis=dict(range=[0, 3.4]), showlegend=False)
        chart(fig)
    strength_charts(d, k)
    a, b = st.columns(2, gap="medium")
    for col, title, df in ((a, "Best wins (strongest clubs beaten)", k[k["result"] == "W"].nlargest(5, "sr_diff")),
                           (b, "Worst losses (weakest clubs lost to)", k[k["result"] == "L"].nsmallest(5, "sr_diff"))):
        with col:
            st.html(f'<div class="mini" style="margin:6px 0">{esc(title)}</div>')
            if df.empty:
                st.html('<div class="mini">None yet.</div>')
                continue
            st.dataframe(pd.DataFrame({
                "When": df["when"], "Opponent": df["opp_name"],
                "Score": df["goals_for"].astype(int).astype(str) + " - " + df["goals_against"].astype(int).astype(str),
                "Strength": [sr_diff_text(v) for v in df["sr_diff"]],
                "Team grade": [grade_text(v) for v in df["team_grade_score"]]}),
                hide_index=True, width="stretch",
                column_config={"When": st.column_config.DatetimeColumn(format="ddd D MMM, HH:mm"),
                               "Strength": st.column_config.TextColumn("Opp. SR")})


def stats_opponents(d):
    o = analytics.opponents(d.matches)
    if o.empty:
        return
    strength_section(d)
    m = d.matches
    if "opp_sr" in m:
        last = m.dropna(subset=["opp_sr"]).groupby("opp_id")[["opp_sr", "opp_level"]].last()
        o = o.join(last)
    section("Clubs you've played", f"{plural(len(o), 'club')} so far.")
    st.dataframe(o.reset_index(drop=True), hide_index=True, width="stretch", column_config={
        "name": "Club", "matches": "Played", "w": "W", "d": "D", "l": "L", "gf": "Scored", "ga": "Conceded",
        "humans": st.column_config.NumberColumn("Their players (avg)", format="%.1f"),
        "last": st.column_config.DatetimeColumn("Last played", format="ddd D MMM, HH:mm"),
        "quits": "They quit", "win_pct": st.column_config.NumberColumn("Win %", format="%.0f%%"),
        "opp_sr": st.column_config.NumberColumn("Their SR", format="%d", help="EA's skill rating, when last scouted"),
        "opp_level": st.column_config.NumberColumn("Their level", format="%.0f",
                                                   help="Their usual team grade (0-100) in their games around yours")})
    a, b = st.columns(2, gap="medium")
    with a:
        c = m["opp_players"].value_counts().sort_index()
        fig = go.Figure(go.Bar(x=c.index.astype(str), y=c.values, marker_color=BLUE))
        fig.update_layout(title="How many humans the other club had", xaxis_title="Their players", yaxis_title="Matches")
        chart(fig)
    with b:
        sc = (m["goals_for"].clip(upper=6).astype(int).astype(str) + "-" + m["goals_against"].clip(upper=6).astype(int).astype(str))
        top = sc.value_counts().head(10)
        fig = go.Figure(go.Bar(x=top.values, y=top.index, orientation="h", marker_color=VOLT))
        fig.update_layout(title="Most common scores (us first)", yaxis=dict(autorange="reversed"), xaxis_title="Matches")
        chart(fig)


def stats_time(d):
    m = d.matches
    gap = CFG.get("session_gap_minutes", 45)
    a, b = st.columns(2, gap="medium")
    with a:
        tab = analytics.weekday_hour(m, "count")
        hours = [h for h in HOUR_ORDER if tab[h].notna().any()]
        if hours:
            fig = go.Figure(go.Heatmap(z=tab[hours].values, x=[f"{h:02d}" for h in hours], y=WEEKDAYS, colorscale=[[0, "#13201b"], [1, VOLT]],
                                       hovertemplate="%{y} %{x}:00<br>%{z} matches<extra></extra>", showscale=False))
            fig.update_layout(title="When you play", yaxis=dict(autorange="reversed"))
            chart(fig)
    with b:
        tab = analytics.weekday_hour(m, "win")
        hours = [h for h in HOUR_ORDER if tab[h].notna().any()]
        if hours:
            fig = go.Figure(go.Heatmap(z=tab[hours].values, x=[f"{h:02d}" for h in hours], y=WEEKDAYS, zmin=0, zmax=100,
                                       colorscale=[[0, BAD], [0.5, "#2a3530"], [1, GOOD]],
                                       hovertemplate="%{y} %{x}:00<br>%{z:.0f}% won<extra></extra>", showscale=False))
            fig.update_layout(title="Win rate by day and hour", yaxis=dict(autorange="reversed"))
            chart(fig)
    s = analytics.by_match_in_session(m, d.me, gap)
    if not s.empty:
        a, b = st.columns(2, gap="medium")
        with a:
            x = [str(i) if i < 8 else "8+" for i in s.index]
            fig = go.Figure()
            fig.add_trace(go.Bar(x=x, y=s["win_pct"], name="Win %", marker_color=[GOOD if v >= 50 else DRAW if v >= 35 else BAD for v in s["win_pct"]],
                                 text=[f"{int(n)}" for n in s["matches"]], hovertemplate="Match %{x} of the session<br>%{y:.0f}% won (%{text} matches)<extra></extra>"))
            if "rating" in s:
                fig.add_trace(go.Scatter(x=x, y=s["rating"], name="Your rating", yaxis="y2", mode="lines+markers",
                                         line=dict(color=VOLT, width=3)))
            fig.update_layout(title="Early vs late in a session", xaxis_title="Match number in the session",
                              yaxis=dict(ticksuffix="%", range=[0, 105]),
                              yaxis2=dict(overlaying="y", side="right", range=[5, 10], showgrid=False, tickmode="linear",
                                          dtick=1, tickformat=".0f", title="Rating"))
            chart(fig)
        with b:
            days = analytics.day_table(m)
            fig = go.Figure(go.Bar(x=days.index, y=days["matches"], marker_color=[GOOD if v >= 1.5 else DRAW if v >= 1 else BAD for v in days["ppg"]],
                                   customdata=np.stack([days["w"], days["ppg"]], axis=1),
                                   hovertemplate="%{x|%a %d %b}<br>%{y} matches, %{customdata[0]} won<br>%{customdata[1]:.2f} points a match<extra></extra>"))
            fig.update_layout(title="Matches per day (colour: points per match)")
            chart(fig)
    sess = analytics.session_table(m, gap)
    if not sess.empty:
        ssr = analytics.session_sr(sess, season_snaps(d, db.club_snapshots_df(CLUB_ID)))
        if not ssr.empty:
            fig = go.Figure(go.Bar(
                x=ssr["start"].dt.strftime("%a %d %b, %H:%M"), y=ssr["change"],
                marker_color=[GOOD if v > 0 else BAD if v < 0 else "#5c6b65" for v in ssr["change"]],
                text=[f"{v:+.0f}" for v in ssr["change"]], textposition="outside", cliponaxis=False,
                customdata=np.stack([ssr["matches"], ssr["w"], ssr["d"], ssr["l"], ssr["sr_before"], ssr["sr_after"]], axis=1),
                hovertemplate="%{x}<br><b>%{y:+.0f}</b> SR (%{customdata[4]:.0f} to %{customdata[5]:.0f})<br>"
                              "%{customdata[0]} matches: %{customdata[1]}-%{customdata[2]}-%{customdata[3]}<extra></extra>"))
            fig.update_layout(title="Skill rating won or lost per session", yaxis_title="Skill rating", xaxis_type="category")
            chart(fig)
        section("Sessions", f"Matches less than {gap} minutes apart count as one session.")
        st.dataframe(sess.head(30), hide_index=True, width="stretch", column_config={
            "start": st.column_config.DatetimeColumn("Started", format="ddd D MMM, HH:mm"),
            "end": st.column_config.DatetimeColumn("Last match", format="HH:mm"), "matches": "Matches",
            "w": "W", "d": "D", "l": "L", "gf": "Scored", "ga": "Conceded", "points": "Points",
            "ppg": st.column_config.NumberColumn("Points / match", format="%.2f")})


def stats_records(d):
    recs = analytics.records(d)
    stk = analytics.streaks(d.matches["result"])
    tiles = [f'<div class="rec"><div class="rec-label">{esc(r["label"])}</div><div class="rec-value">{esc(r["value"])}</div>'
             f'<div class="rec-sub">{esc(r["sub"])}</div></div>' for r in recs]
    m = d.matches
    if "team_grade_score" in m and m["team_grade_score"].notna().any():
        r = m.loc[m["team_grade_score"].idxmax()]
        tiles.insert(0, f'<div class="rec"><div class="rec-label">Best team grade</div><div class="rec-value">'
                        f'{esc(r["team_grade"])} <span style="font-size:.55em;opacity:.7">{int(r["team_grade_score"])}</span></div>'
                        f'<div class="rec-sub">{int(r["goals_for"])}-{int(r["goals_against"])} vs {esc(r["opp_name"])}</div></div>')
    if not d.me.empty and "grade_score" in d.me and d.me["grade_score"].notna().any():
        r = d.me.loc[d.me["grade_score"].idxmax()]
        tiles.insert(0, f'<div class="rec"><div class="rec-label">Your best grade</div><div class="rec-value">'
                        f'{esc(r["grade"])} <span style="font-size:.55em;opacity:.7">{int(r["grade_score"])}</span></div>'
                        f'<div class="rec-sub">{int(r["goals_for"])}-{int(r["goals_against"])} vs {esc(r["opp_name"])}</div></div>')
    tiles += [f'<div class="rec"><div class="rec-label">{a}</div><div class="rec-value">{b}</div><div class="rec-sub">{c}</div></div>'
              for a, b, c in (("Longest winning run", stk["best_win"], "matches in a row"),
                              ("Longest unbeaten run", stk["best_unbeaten"], "matches in a row"),
                              ("Longest losing run", stk["worst_loss"], "matches in a row"))]
    st.html('<div class="records">' + "".join(tiles) + "</div>")
    if not d.me.empty and "grade_score" in d.me:
        section("Your best matches", "By match grade.")
        top = d.me.dropna(subset=["grade_score"]).sort_values(["grade_score", "rating"], ascending=False).head(8)
        cols = st.columns(2, gap="medium")
        for k, (_, r) in enumerate(top.iterrows()):
            with cols[k % 2]:
                with st.container(border=True):
                    st.html(f'<div class="mc-top"><div class="chips">{result_pill(r["result"])}<b>{int(r["goals_for"])}-'
                            f'{int(r["goals_against"])}</b> vs {esc(r["opp_name"])}</div><div>{grade_badge(r["grade_score"], "GRADE")}</div></div>'
                            f'<div class="mini" style="margin-top:6px">{when_str(r["when"])} · rating {fmt(r["rating"], 1)} · '
                            f'{int(r["goals"])} G · {int(r["assists"])} A</div>')
                    if st.button("Open match report", key=f"best_{r['match_id']}", icon=":material/open_in_new:"):
                        st.switch_page(PAGES["matches"], query_params={"match": str(r["match_id"])})


PLAYER_COLORS = [VOLT, BLUE, "#b281f7", DRAW, AVG]

# One row of the comparison: group, label, column, decimals, suffix, higher is better (None = no leader),
# small text under the value, only shown when some player has a value above 0, and for rates the column and
# minimum count needed to lead (a rate from a handful of attempts says little).
CMP_ROWS = [
    dict(g="Grade", label="Overall grade", col="overall", hi=True, fmtf=lambda v: f"{grades.letter(v)} {v:.0f}",
         sub=lambda r: f"average {grades.letter(r.avg_grade)} {r.avg_grade:.0f}"),
    dict(g="Grade", label="Form (last 5)", col="form", hi=True, fmtf=lambda v: f"{grades.letter(v)} {v:.0f}"),
    dict(g="Results", label="Matches", col="matches", sub=lambda r: f"{int(r.wins)}-{int(r.draws)}-{int(r.losses)}"),
    dict(g="Results", label="Win rate", col="win_pct", suf="%", hi=True),
    dict(g="Results", label="Man of the match", col="mom_pct", suf="%", hi=True, sub=lambda r: f"{int(r.mom)} times"),
    dict(g="Rating", label="Avg rating", col="rating", dec=2, hi=True, sub=lambda r: f"best {r.best_rating:.1f}"),
    dict(g="Rating", label="Rating spread", col="rating_sd", dec=2, hi=False, sub=lambda r: "lower = steadier",
         need=("matches", 3)),
    dict(g="Attack", label="Goals per match", col="goals_pm", dec=2, hi=True, sub=lambda r: f"{int(r.goals)} in total"),
    dict(g="Attack", label="Shots per match", col="shots_pm", dec=1, hi=True, sub=lambda r: f"{int(r.shots)} in total"),
    dict(g="Attack", label="Conversion", col="conversion", suf="%", hi=True,
         sub=lambda r: f"{int(r.goals)} of {int(r.shots)} shots", need=("shots", 10)),
    dict(g="Creating", label="Assists per match", col="assists_pm", dec=2, hi=True, sub=lambda r: f"{int(r.assists)} in total"),
    dict(g="Creating", label="Goals + assists per match", col="ga_pm", dec=2, hi=True, sub=lambda r: f"{int(r.ga)} in total"),
    dict(g="Creating", label="Passes per match", col="passes_pm", dec=1, hi=True),
    dict(g="Creating", label="Pass accuracy", col="pass_pct", suf="%", hi=True,
         sub=lambda r: f"{int(r.passes_made)} of {int(r.pass_attempts)}", need=("pass_attempts", 30)),
    dict(g="Defending", label="Tackles won per match", col="tackles_pm", dec=1, hi=True),
    dict(g="Defending", label="Tackle success", col="tackle_pct", suf="%", hi=True,
         sub=lambda r: f"{int(r.tackles_made)} of {int(r.tackle_attempts)}", need=("tackle_attempts", 10)),
    dict(g="Defending", label="Clean sheets", col="clean_sheet_pct", suf="%", hi=True,
         sub=lambda r: f"{int(r.clean_sheets)} in total", any_only=True),
    dict(g="Defending", label="Saves per match", col="saves_pm", dec=1, hi=True,
         sub=lambda r: f"{int(r.saves)} in total", any_only=True),
    dict(g="Discipline", label="Red cards per 10 matches", col="red_p10", dec=1, hi=False,
         sub=lambda r: f"{int(r.red_cards)} in total", any_only=True),
    dict(g="Discipline", label="Left early", col="left_pct", suf="%", hi=False,
         sub=lambda r: f"{int(r.left_early)} times", any_only=True),
]


def compare_table_html(t, ids, colors):
    head = '<tr><th></th>' + "".join(
        f'<th><span class="pdot" style="background:{colors[i]}"></span>{esc(t.loc[i, "name"])}</th>' for i in ids) + "</tr>"
    rows, group = [], None
    for row in CMP_ROWS:
        col, dec, suf, higher = row["col"], row.get("dec", 0), row.get("suf", ""), row.get("hi")
        vals = pd.to_numeric(t.loc[ids, col], errors="coerce").astype(float)
        if row.get("any_only") and not (vals.fillna(0) > 0).any():
            continue
        if row["g"] != group:
            group = row["g"]
            rows.append(f'<tr class="grp"><td colspan="{len(ids) + 1}">{esc(group)}</td></tr>')
        eligible = vals.copy()
        if row.get("need"):
            ncol, nmin = row["need"]
            eligible = vals.where(pd.to_numeric(t.loc[ids, ncol], errors="coerce") >= nmin)
        lead = None
        ok = eligible.dropna()
        if higher is not None and len(ok) >= 2 and ok.nunique() > 1:
            best = ok.max() if higher else ok.min()
            winners = list(ok[ok == best].index)
            lead = winners[0] if len(winners) == 1 else None
        cells = []
        for i in ids:
            v = vals[i]
            style = f' style="background:{colors[i]}"' if i == lead else ""
            subtxt = ""
            if row.get("sub") and not isna(v):
                try:
                    subtxt = row["sub"](t.loc[i])
                except (TypeError, ValueError):
                    subtxt = ""
            if row.get("need") and not isna(v) and isna(eligible[i]):
                subtxt = (subtxt + " · " if subtxt else "") + "too few to rank"
            txt = row["fmtf"](v) if row.get("fmtf") and not isna(v) else fmt(v, dec, suf)
            cells.append(f'<td><span class="v{" lead" if i == lead else ""}"{style}>{esc(txt)}</span>'
                         + (f'<span class="s">{esc(subtxt)}</span>' if subtxt else "") + "</td>")
        rows.append(f'<tr><td class="metric">{esc(row["label"])}</td>{"".join(cells)}</tr>')
    return f'<div class="tbl-wrap"><table class="cmpt">{head}{"".join(rows)}</table></div>'


def duel_html(name_a, name_b, ca, cb, items):
    rows = [f'<div class="duel-head"><div></div><div><span style="color:{ca}">{esc(name_a)}</span>'
            f'<span class="dim">level</span><span style="color:{cb}">{esc(name_b)}</span></div></div>']
    for it in items:
        n = it["n"]
        if not n:
            continue
        wa, wt, wb = 100 * it["a"] / n, 100 * it["tie"] / n, 100 * it["b"] / n
        rows.append(f'<div class="duel"><div class="duel-label">{esc(it["label"])}</div>'
                    f'<div class="duel-n" style="color:{ca}">{it["a"]}</div>'
                    f'<div class="duel-bar" title="{it["a"]} vs {it["b"]}, {it["tie"]} level, in {n} matches">'
                    f'<div style="width:{wa:.1f}%;background:{ca}"></div><div style="width:{wt:.1f}%;background:#3a4a43"></div>'
                    f'<div style="width:{wb:.1f}%;background:{cb}"></div></div>'
                    f'<div class="duel-n" style="color:{cb}">{it["b"]}</div></div>')
    return "".join(rows)


def stats_compare(d):
    lines = d.ours
    if lines.empty:
        empty_state("⚖️", "Nobody to compare yet", "Comparisons appear once matches are saved.")
        return
    allt = analytics.compare_table(lines, d.names)
    options = [str(i) for i in allt.index]
    me = str(d.me_id) if d.me_id is not None else None
    default = ([me] if me in options else []) + [o for o in options if o != me][: 2 if me in options else 3]
    default = default[:3] if len(options) > 2 else default[:2]
    if "cmp_players" not in st.session_state or not set(st.session_state["cmp_players"]) <= set(options):
        st.session_state["cmp_players"] = [p for p in st.session_state.get("cmp_players", []) if p in options] or default
    section("Compare players", "Pick two to five club players. Numbers come from the tracked matches; "
                               "EA's own season totals are at the bottom. Grades: overall " + info_icon(overall_info())
                               + " and per match " + info_icon(grade_info()))
    c1, c2, c3, c4 = st.columns([2.4, 1.6, 1.1, 1.3], gap="medium")
    with c1:
        sel = st.multiselect("Players", options, key="cmp_players", max_selections=5,
                             format_func=lambda p: allt.loc[p, "name"] + (" (you)" if p == me else ""))
    with c2:
        mt = st.segmented_control("Matches", ["All", "League", "Playoffs", "Friendly"], default="All", key="cmp_type")
    with c3:
        last = st.selectbox("Period", ["All matches", "Last 10 each", "Last 20 each"], key="cmp_last")
    with c4:
        together = st.toggle("Only matches together", key="cmp_together",
                             help="Count only matches where all the chosen players played, so everyone is judged "
                                  "on the same matches: same opponents, same team-mates.")
    if len(sel) < 2:
        st.info("Pick at least two players to compare.", icon=":material/group_add:")
        return
    sub = lines[lines["player_id"].astype(str).isin(sel)].copy()
    sub["player_id"] = sub["player_id"].astype(str)
    tmap = {"League": "leagueMatch", "Playoffs": "playoffMatch", "Friendly": "friendlyMatch"}
    if mt and mt != "All":
        sub = sub[sub["match_type"] == tmap[mt]]
    if together:
        k = sub.groupby("match_id")["player_id"].nunique()
        sub = sub[sub["match_id"].isin(k[k == len(sel)].index)]
    if last != "All matches":
        n = 10 if "10" in last else 20
        sub = sub.sort_values("ts").groupby("player_id", group_keys=False).tail(n)
    ids = [p for p in sel if p in set(sub["player_id"])]
    if len(ids) < 2:
        empty_state("🔎", "Not enough matches", "With these filters fewer than two of the players have matches. "
                                                  "Try All, or turn off Only matches together.")
        return
    t = analytics.compare_table(sub, d.names)
    t.index = t.index.astype(str)
    t = t.join(player_grades(sub))
    colors = {p: PLAYER_COLORS[k % len(PLAYER_COLORS)] for k, p in enumerate(ids)}

    cards = []
    for p in ids:
        r = t.loc[p]
        cards.append(f'<div class="pcard" style="--c:{colors[p]}"><div class="pcard-name">{esc(r["name"])}'
                     + (' <span class="chip volt" style="vertical-align:4px">you</span>' if p == me else "")
                     + f'</div><div class="pcard-sub">{esc(labels.position(r["pos"]))} · {esc(arch_name(r["archetype"]))} · '
                     f'{esc(labels.device(r["device"]))}</div><div class="pcard-sub">{plural(r["matches"], "match", "matches")}'
                     f' · {int(r["wins"])}-{int(r["draws"])}-{int(r["losses"])} · last {esc(ago(r["last_played"]))}</div></div>')
    st.html(f'<div class="pcards">{"".join(cards)}</div>')
    small = int(t.loc[ids, "matches"].min())
    if small < 5:
        st.caption(f"Small samples: with only {plural(small, 'match', 'matches')} for someone, the numbers can still "
                   "swing a lot.")
    if len(set(t.loc[ids, "pos"])) > 1:
        st.caption("They usually play different positions, so attacking and defending numbers aren't a like-for-like "
                   "comparison.")

    a, b = st.columns([1.25, 1], gap="medium")
    with a:
        with st.container(border=True):
            st.html(compare_table_html(t, ids, colors))
    with b:
        axes = [("Grade", "overall", 0, 100), ("Rating", "rating", 5, 10), ("G+A / match", "ga_pm", 0, 3),
                ("Shots / match", "shots_pm", 0, 6),
                ("Pass %", "pass_pct", 40, 100), ("Tackles won / match", "tackles_pm", 0, 4),
                ("Win %", "win_pct", 0, 100), ("MOTM %", "mom_pct", 0, 50)]

        def norm(v, lo, hi):
            return float(np.clip(((lo if isna(v) else v) - lo) / (hi - lo) * 100, 0, 100))
        fig = go.Figure()
        for p in ids:
            r = t.loc[p]
            vals = [norm(r[c], lo, hi) for _, c, lo, hi in axes]
            real = [(f"{grades.letter(r[c])} {r[c]:.0f}" if c == "overall" and not isna(r[c]) else
                     fmt(r[c], 2 if c in ("rating", "ga_pm") else 1 if c.endswith("_pm") else 0,
                         "%" if c.endswith("pct") else "")) for _, c, _lo, _hi in axes]
            fig.add_trace(go.Scatterpolar(r=vals + vals[:1], theta=[x for x, *_ in axes] + [axes[0][0]], fill="toself",
                                          name=r["name"], line=dict(color=colors[p], width=2.5), opacity=0.85,
                                          customdata=real + real[:1], hovertemplate="%{theta}: <b>%{customdata}</b><extra>%{fullData.name}</extra>"))
        fig.update_layout(title="Profiles", polar=dict(bgcolor="rgba(255,255,255,0.02)", radialaxis=dict(visible=False, range=[0, 100]),
                                                       angularaxis=dict(tickfont=dict(color="#e8efec", size=12), gridcolor="#2f3d37")))
        chart(fig, height=430)
        fig = go.Figure()
        for p in ids:
            r = t.loc[p]
            fig.add_trace(go.Bar(name=r["name"], x=["Goals", "Assists", "Shots", "Tackles won"],
                                 y=[r["goals_pm"], r["assists_pm"], r["shots_pm"], r["tackles_pm"]], marker_color=colors[p],
                                 hovertemplate="%{x}: <b>%{y:.2f}</b> per match<extra>" + esc(r["name"]) + "</extra>"))
        fig.update_layout(title="Per match", barmode="group", bargap=0.25)
        chart(fig, height=300)

    if len(ids) == 2:
        n_shared, items = analytics.duels(sub, ids[0], ids[1])
        section("Head to head", f"In the {plural(n_shared, 'match', 'matches')} they played together: "
                                "who came out on top, match by match.")
        if n_shared:
            with st.container(border=True):
                st.html(duel_html(t.loc[ids[0], "name"], t.loc[ids[1], "name"], colors[ids[0]], colors[ids[1]], items))
        else:
            st.caption("They haven't played a tracked match together yet.")
    else:
        share, n = analytics.duel_matrix(sub, ids)
        if (n.values > 0).any():
            section("Who out-rates whom", "Share of their matches together where the row player had the higher rating "
                                          "(a tie counts half).")
            nm = [t.loc[p, "name"] for p in ids]
            fig = go.Figure(go.Heatmap(z=share.values, x=nm, y=nm, zmin=0, zmax=100, colorscale=[[0, BAD], [0.5, "#2a3530"], [1, GOOD]],
                                       text=[[("" if pd.isna(share.iat[i, j]) else f"{share.iat[i, j]:.0f}%<br>({n.iat[i, j]})")
                                              for j in range(len(ids))] for i in range(len(ids))],
                                       texttemplate="%{text}", hovertemplate="%{y} vs %{x}: %{z:.0f}%<extra></extra>", showscale=False))
            fig.update_layout(yaxis=dict(autorange="reversed"), xaxis=dict(side="top"))
            chart(fig, height=120 + 70 * len(ids))

    section("Over time", "Match grades and goal contributions, on the club's match timeline.")
    a, b = st.columns(2, gap="medium")
    with a:
        fig = go.Figure()
        for p in ids:
            df = sub[sub["player_id"] == p].sort_values("ts")
            fig.add_trace(go.Scatter(x=df["game_no"], y=df["grade_score"], mode="markers", name=t.loc[p, "name"],
                                     marker=dict(size=8, color=colors[p], opacity=0.5), legendgroup=p, showlegend=False,
                                     customdata=[grades.letter(v) for v in df["grade_score"]], hovertext=_hover(df),
                                     hovertemplate="%{hovertext}<br>Grade <b>%{customdata}</b> (%{y:.0f})<extra>"
                                                   + esc(t.loc[p, "name"]) + "</extra>"))
            fig.add_trace(go.Scatter(x=df["game_no"], y=analytics.rolling(df["grade_score"], UI["rolling_window"]),
                                     mode="lines", name=t.loc[p, "name"], line=dict(color=colors[p], width=3), legendgroup=p,
                                     hoverinfo="skip"))
        grade_lines_(fig)
        fig.update_layout(title=f"Match grade (lines: average of last {UI['rolling_window']})", xaxis_title="Club match",
                          yaxis=dict(range=[0, 104]), meta="pad_right")
        chart(few_matches(fig, sub.drop_duplicates("game_no")))
    with b:
        fig = go.Figure()
        for p in ids:
            df = sub[sub["player_id"] == p].sort_values("ts")
            fig.add_trace(go.Scatter(x=df["game_no"], y=df["ga"].cumsum(), mode="lines+markers", name=t.loc[p, "name"],
                                     line=dict(color=colors[p], width=3, shape="hv"), marker=dict(size=5),
                                     hovertext=_hover(df), hovertemplate="%{hovertext}<br>Total G+A <b>%{y}</b><extra>" + esc(t.loc[p, "name"]) + "</extra>"))
        fig.update_layout(title="Goals + assists, added up", xaxis_title="Club match")
        chart(few_matches(fig, sub.drop_duplicates("game_no")))
    a, b = st.columns(2, gap="medium")
    with a:
        fig = go.Figure()
        for p in ids:
            df = sub[sub["player_id"] == p]
            fig.add_trace(go.Box(y=df["rating"], name=t.loc[p, "name"], marker_color=colors[p], boxpoints="all", jitter=0.4,
                                 pointpos=0, line=dict(width=2), fillcolor="rgba(255,255,255,0.03)",
                                 hovertemplate="Rating %{y:.1f}<extra>" + esc(t.loc[p, "name"]) + "</extra>"))
        fig.update_layout(title="How steady: spread of match ratings", showlegend=False, yaxis=dict(range=[0, 10.4]))
        chart(fig)
    with b:   # finishing: shots and goals a match, with lines for the share of shots that go in
        fig = go.Figure()
        top = max([float(t.loc[p, "shots_pm"]) for p in ids if not isna(t.loc[p, "shots_pm"])] + [1.0]) * 1.2
        for conv in (0.1, 0.2, 0.3, 0.5):
            fig.add_trace(go.Scatter(x=[0, top], y=[0, conv * top], mode="lines+text", text=["", f"{conv:.0%}"],
                                     textposition="top left", textfont=dict(color="#5c6b65", size=11), hoverinfo="skip",
                                     line=dict(color="#2a3530", width=1, dash="dot"), showlegend=False))
        for p in ids:
            r = t.loc[p]
            fig.add_trace(go.Scatter(x=[r["shots_pm"]], y=[r["goals_pm"]], mode="markers+text", text=[r["name"]],
                                     textposition="top center", name=r["name"], showlegend=False,
                                     marker=dict(size=16, color=colors[p], line=dict(width=1, color="#0a0e0d")),
                                     hovertemplate=f"<b>{esc(r['name'])}</b><br>%{{x:.1f}} shots and %{{y:.2f}} goals a match<br>"
                                                   f"{fmt(r['conversion'], 0, '%')} of shots scored<extra></extra>"))
        fig.update_layout(title="Finishing (lines: share of shots scored)", xaxis=dict(title="Shots a match", range=[0, top]),
                          yaxis=dict(title="Goals a match", rangemode="tozero"))
        chart(fig)
    a, _ = st.columns([1, 1], gap="medium")
    with a:
        fig = go.Figure()
        cats = ["Win %", "Pass accuracy", "Tackle success", "Conversion"]
        for p in ids:
            r = t.loc[p]
            fig.add_trace(go.Bar(name=r["name"], x=cats, y=[r["win_pct"], r["pass_pct"], r["tackle_pct"], r["conversion"]],
                                 marker_color=colors[p], hovertemplate="%{x}: <b>%{y:.0f}%</b><extra>" + esc(r["name"]) + "</extra>"))
        fig.update_layout(title="Rates", barmode="group", bargap=0.25, yaxis=dict(ticksuffix="%", range=[0, 105]))
        chart(fig)

    mem = db.members_latest(CLUB_ID)
    names_sel = [t.loc[p, "name"] for p in ids]
    mem = mem[mem["name"].isin(names_sel)] if not mem.empty else mem
    if not mem.empty:
        section("EA's season numbers", "EA's own totals for this season, including matches from before the tracker.")
        show = mem.set_index("name").reindex([n for n in names_sel if n in set(mem["name"])])
        rows = [("Pro OVR", "overall", 0, ""), ("Matches", "games", 0, ""), ("Win rate", "win_rate", 0, "%"),
                ("Goals", "goals", 0, ""), ("Assists", "assists", 0, ""), ("Avg rating", "rating_avg", 1, ""),
                ("Man of the match", "mom", 0, ""), ("Pass accuracy", "pass_rate", 0, "%"),
                ("Shot success", "shot_rate", 0, "%"), ("Tackle success", "tackle_rate", 0, "%")]
        table = pd.DataFrame({label: [fmt(v, dec, suf) for v in show[col]] for label, col, dec, suf in rows},
                             index=show.index).T
        st.dataframe(table, width="stretch")


# ---------------------------------------------------------------- seasons
def season_view(d, no):
    """The same numbers for one season only (its matches numbered 1, 2, 3... within the season)."""
    v = copy.copy(d)
    v.matches = d.matches[d.matches["season"] == no].copy()
    ids = set(v.matches["match_id"])
    renum = dict(zip(v.matches["game_no"], range(1, len(v.matches) + 1)))
    v.matches["game_no"] = v.matches["game_no"].map(renum)
    for name in ("players", "ours", "me"):
        df = getattr(d, name)
        if not df.empty:
            df = df[df["match_id"].isin(ids)].copy()
            if "game_no" in df:
                df["game_no"] = df["game_no"].map(renum)
            setattr(v, name, df)
    v.season_range = seasons.span(d.season_breaks, d.season_first, no)
    return v


def season_snaps(d, snaps):
    """Club snapshots within the picked season (all of them when no season is picked)."""
    rng = getattr(d, "season_range", None)
    if not rng or snaps.empty:
        return snaps
    lo, hi = rng
    keep = pd.Series(True, index=snaps.index)
    if lo is not None:
        keep &= snaps["at"] >= lo
    if hi is not None:
        keep &= snaps["at"] < hi
    return snaps[keep]


def season_picker(d):
    """Stats for all seasons or one of them: the picker shows once there's more than one season (not on the
    Seasons section, which compares them all; the pick is kept for when you come back)."""
    if "season" not in d.matches or d.matches["season"].nunique() < 2:
        return d
    tab = seasons.table(d.matches, None, d.season_breaks, d.season_first)   # newest first
    opts = ["All seasons"] + list(tab["label"])
    keep = st.session_state.get("stats_season_keep")
    pick = keep if keep in opts else "All seasons"
    if st.session_state.get("stats_pick") != "Seasons":
        with st.container(key="season_pick", width=340):
            pick = st.selectbox("Season", opts, index=opts.index(pick), key="stats_season_box")
        st.session_state["stats_season_keep"] = pick
    if pick == "All seasons":
        return d
    return season_view(d, int(tab.loc[tab["label"] == pick, "season"].iloc[0]))


def stats_seasons(d):
    snaps = db.club_snapshots_df(CLUB_ID)
    tab = seasons.table(d.matches, snaps, d.season_breaks, d.season_first)
    if tab.empty:
        return
    tab["grade"] = tab["team_grade"].map(lambda v: "" if isna(v) else f"{grades.letter(v)} {v:.0f}") if "team_grade" in tab else ""
    section("Seasons", "EA's Clubs league seasons (numbered like EA's calendar), newest first. A season ends after its "
                       "playoffs. Points count 3 for a win and 1 for a draw.")
    cols = ["label", "matches", "w", "d", "l", "points", "ppg", "win_pct", "gf", "ga", "clean_sheets", "grade",
            "sr_start", "sr_end", "division", "promotions", "playoffs"]
    st.dataframe(tab[[c for c in cols if c in tab]], hide_index=True, width="stretch", column_config={
        "label": "Season", "matches": "Matches", "w": "W", "d": "D", "l": "L", "points": "Points",
        "ppg": st.column_config.NumberColumn("Points / match", format="%.2f"),
        "win_pct": st.column_config.NumberColumn("Win %", format="%.0f%%"), "gf": "Scored", "ga": "Conceded",
        "clean_sheets": "Clean sheets", "grade": "Team grade (avg)",
        "sr_start": st.column_config.NumberColumn("SR at the start", format="%d"),
        "sr_end": st.column_config.NumberColumn("SR now / at the end", format="%d"),
        "division": st.column_config.NumberColumn("Division", format="%d"), "promotions": "Promotions",
        "playoffs": "Playoff matches"})
    if len(tab) < 2:
        st.caption("The next season gets its own row as soon as EA starts it (after the playoffs), and a Season picker "
                   "then appears at the top of Stats, so every section can show one season at a time.")
    m = d.matches
    a, b = st.columns(2, gap="medium")
    with a:   # the points race: this season against the earlier ones, match by match
        fig = go.Figure()
        others = [BLUE, AVG, "#8a9a93", "#5c6b65"]
        for i, r in tab.iterrows():
            g = m[m["season"] == r["season"]]
            cur = i == 0
            fig.add_trace(go.Scatter(x=list(range(1, len(g) + 1)), y=g["points"].cumsum(), mode="lines",
                                     name=r["label"], line=dict(color=VOLT if cur else others[(i - 1) % len(others)],
                                                                width=3.5 if cur else 2),
                                     hovertemplate="Match %{x}: %{y} points<extra>" + esc(r["label"]) + "</extra>"))
        fig.update_layout(title="Points race, season by season", xaxis_title="Match of the season",
                          yaxis_title="Points")
        chart(fig)
    with b:
        t = tab.iloc[::-1]
        fig = go.Figure()
        fig.add_trace(go.Bar(x=t["label"], y=t["gf"] / t["matches"], name="Scored a match", marker_color=VOLT))
        fig.add_trace(go.Bar(x=t["label"], y=t["ga"] / t["matches"], name="Conceded a match", marker_color=BAD))
        fig.update_layout(title="Goals a match, season by season", barmode="group", yaxis=dict(tickformat=".1f"))
        chart(fig)
    o = d.ours.merge(m[["match_id", "season"]], on="match_id", how="inner")
    if o.empty:
        return
    g = o.groupby(["season", "player_id"]).agg(matches=("match_id", "nunique"), goals=("goals", "sum"),
                                               assists=("assists", "sum"), rating=("rating", "mean"),
                                               grade=("grade_score", "mean"), mom=("mom", "sum")).reset_index()
    g["name"] = g["player_id"].map(d.names).fillna(g["player_id"])
    g["ga_pm"] = (g["goals"] + g["assists"]) / g["matches"]
    g["grade_txt"] = g["grade"].map(lambda v: "" if isna(v) else f"{grades.letter(v)} {v:.0f}")
    g["label"] = g["season"].map(dict(zip(tab["season"], tab["label"])))
    g = g.sort_values(["season", "grade"], ascending=[False, False])
    section("Players, season by season", "Everyone who played for the club, per season (best grade first).")
    st.dataframe(g[["label", "name", "matches", "goals", "assists", "ga_pm", "rating", "grade_txt", "mom"]],
                 hide_index=True, width="stretch", column_config={
                     "label": "Season", "name": "Player", "matches": "Matches", "goals": "Goals", "assists": "Assists",
                     "ga_pm": st.column_config.NumberColumn("G+A / match", format="%.2f"),
                     "rating": st.column_config.NumberColumn("Avg rating", format="%.2f"),
                     "grade_txt": "Grade (avg)", "mom": "MOTM"})


STATS_SECTIONS = {"Team": stats_club, "You": stats_you, "Compare": stats_compare,
                  "Teammates": stats_teammates, "Opponents": stats_opponents, "Time": stats_time, "Records": stats_records,
                  "Seasons": stats_seasons}


def page_stats():
    d = data()
    page_head("Stats", "Everything from your tracked matches, by topic.")
    if d.matches.empty:
        empty_state("📈", "Nothing to show yet", "Charts appear once your first matches are saved.")
        return
    view = season_picker(d)
    default = UI.get("stats_default") if UI.get("stats_default") in STATS_SECTIONS else "Team"
    if st.session_state.get("stats_pick") not in STATS_SECTIONS:
        st.session_state["stats_pick"] = default
    with st.container(key="section"):
        pick = st.segmented_control("Section", list(STATS_SECTIONS), key="stats_pick", label_visibility="collapsed")
    pick = pick or default
    STATS_SECTIONS[pick](d if pick == "Seasons" else view)   # Seasons always compares all of them


# ================================================================= PAGE: SEARCH
@st.cache_data(ttl=600, show_spinner=False)
def search_cached(q, platform):
    return api.search_clubs(q, platform)


@st.cache_data(ttl=300, show_spinner=False, max_entries=20)
def pull_cached(club_id, platform, stamp=0):
    return scout.pull(club_id, platform)


@st.cache_data(ttl=600, show_spinner=False, max_entries=20)
def members_cached(club_id, platform):
    return api.members(club_id, platform).get("members") or []


def vs_rows_html(rows):
    """'You vs them' bars: rows of (label, ours, theirs, decimals, suffix, lower_is_better)."""
    out = []
    for label, a, b, d_, suf, low in rows:
        a_, b_ = (0 if isna(a) else float(a)), (0 if isna(b) else float(b))
        tot_ = abs(a_) + abs(b_)
        wa, wb = (100 * abs(a_) / tot_, 100 * abs(b_) / tot_) if tot_ else (0, 0)
        better_a = (a_ < b_) if low else (a_ > b_)
        better_b = (b_ < a_) if low else (b_ > a_)
        ca = VOLT if better_a else "#5c6b65"
        cb = BAD if better_b else "#5c6b65"
        out.append(f'<div class="cmp-row"><div class="cmp-l">{fmt(a, d_, suf)}</div>'
                   f'<div class="cmp-bar l"><div style="width:{wa:.0f}%;background:{ca}"></div></div>'
                   f'<div class="cmp-label">{esc(label)}</div>'
                   f'<div class="cmp-bar"><div style="width:{wb:.0f}%;background:{cb}"></div></div>'
                   f'<div class="cmp-r">{fmt(b, d_, suf)}</div></div>')
    return '<div class="cmp">' + "".join(out) + "</div>"


def club_identity(club_id, res, meta):
    """Name, crest, stadium and division of a looked-up club, from its games, EA's club info or the search."""
    cid = str(club_id)
    for _, _, clubs, _ in res.get("parsed") or []:
        for c in clubs:
            if str(c.get("club_id")) == cid and c.get("name"):
                return c["name"], c.get("crest_url"), c.get("stadium"), (meta or {}).get("division")
    if res.get("info"):
        c = parse.club_row(cid, res["info"], CFG.get("platform"))
        return c.get("name"), c.get("crest_url"), c.get("stadium"), (meta or {}).get("division")
    m = meta or {}
    return m.get("name") or db.club(cid).get("name") or f"Club {cid}", m.get("crest"), None, m.get("division")


def club_profile(d, club_id):
    plat = CFG.get("platform") or "common-gen5"
    cid = str(club_id)
    stamp = st.session_state.get(f"pull_{cid}", 0)
    with st.spinner("Asking EA about this club..."):
        res = pull_cached(cid, plat, stamp)
    meta = (st.session_state.get("search_meta") or {}).get(cid)
    name, crest_url, stadium, division = club_identity(cid, res, meta)
    stats = res.get("stats") or {}
    if not res.get("parsed") and not stats:
        st.warning("EA didn't send anything for this club. " + "; ".join(res.get("errors", [])[-2:])[:300])
        if st.button("Try again", icon=":material/refresh:"):
            st.session_state[f"pull_{cid}"] = stamp + 1
            st.rerun()
        return
    sc = scout.from_parsed(res.get("parsed") or [])
    games = sc.games.sort_values("ts") if not sc.games.empty else sc.games
    last = games.tail(scout.LAST_N) if not games.empty else games
    s_ = scout.summary(last)
    n_ = s_.get("n", 0)
    their_sr = db._i(stats.get("skillRating"))
    our_sr = db.latest_club_snapshot(CLUB_ID).get("skill_rating") if CLUB_ID else None
    diff = (their_sr - our_sr) if their_sr is not None and our_sr else None
    w_, d__, l_ = (db._i(stats.get(k)) for k in ("wins", "ties", "losses"))
    is_us = cid == CLUB_ID
    sub = " · ".join(x for x in [labels.division(division) if division else None, stadium,
                                 plural(n_, "recent game") if n_ else "no recent games"] if x)
    st.html(f'<div class="hero" style="margin-top:6px">{crest(crest_url, 92)}<div><div class="eyebrow">Club profile</div>'
            f'<div class="hero-name">{esc(name)}</div><div class="hero-sub">{esc(sub)}</div>'
            f'{form_dots(last["result"] if n_ else [])}</div></div>')
    a, b = st.columns([1, 6])
    with a:
        if st.button("Pull again", icon=":material/refresh:", key=f"repull_{cid}", width="stretch"):
            st.session_state[f"pull_{cid}"] = stamp + 1
            st.rerun()
    with b:
        if res.get("errors"):
            st.caption("Some of it didn't come through: " + "; ".join(res["errors"][-2:])[:240])
    expect = scout.expectation(diff) if diff is not None and not is_us else None
    kpis([
        {"label": "Skill rating", "value": fmt(their_sr),
         "sub": (f"yours {fmt(our_sr)} ({diff:+d})" if diff is not None and not is_us else "EA's rating for the club"),
         "tone": "" if diff is None or is_us else "bad" if diff >= scout.BIG else "gold" if diff >= scout.EVEN
         else "good" if diff <= -scout.EVEN else ""},
        {"label": "Against them", "value": esc(expect or "-"),
         "sub": esc(STRENGTH_TEXT.get(scout.bucket(diff), "")) if expect else "from the skill ratings",
         "help": "What the skill ratings say about a match between you: their SR minus yours of +50 or more makes you "
                 "underdogs, -50 or less favourites."},
        {"label": "All time", "value": f"{w_}-{d__}-{l_}" if w_ is not None else "-",
         "sub": f'{fmt(db._i(stats.get("gamesPlayed")))} games · win run {fmt(db._i(stats.get("wstreak")))}'},
        {"label": "Last 10 games", "value": f'{s_["w"]}-{s_["d"]}-{s_["l"]}' if n_ else "-",
         "sub": f'{s_["ppg"]:.2f} points a game' if n_ else ""},
        {"label": "Goals", "value": f'{s_["gf"]:.1f} - {s_["ga"]:.1f}' if n_ else "-", "sub": "scored - conceded a game"},
        {"label": "Their level", "value": (f'{grades.letter(s_["level"])} <span style="font-size:.6em;opacity:.7">'
                                           f'{fmt(s_["level"])}</span>' if n_ and not isna(s_.get("level")) else "-"),
         "sub": "usual team grade", "help": "Their average team grade in these games (pulled towards 50 when there are few)."},
        {"label": "Humans", "value": fmt(s_.get("humans"), 1) if n_ else "-", "sub": "a game"},
    ], cols=7)
    m = d.matches
    if not is_us and not m.empty and n_:
        ours = m.tail(scout.LAST_N)
        og = ours["team_grade_score"] if "team_grade_score" in ours else pd.Series(dtype=float)
        section("You vs them", f"Your last {len(ours)} matches against their last {n_} games (lime: you're better, "
                               "red: they are).")
        with st.container(border=True):
            st.html(vs_rows_html([
                ("Skill rating", our_sr, their_sr, 0, "", False),
                ("Points a game", ours["points"].mean(), s_["ppg"], 2, "", False),
                ("Scored a game", ours["goals_for"].mean(), s_["gf"], 1, "", False),
                ("Conceded a game", ours["goals_against"].mean(), s_["ga"], 1, "", True),
                ("Team grade", scout._shrunk(og), s_["level"], 0, "", False),
                ("Humans a game", ours["our_players"].mean(), s_["humans"], 1, "", False),
            ]))
    h2h = m[m["opp_id"].astype(str) == cid] if not m.empty and not is_us else m.iloc[0:0]
    vs_ids = set(d.players[d.players["match_id"].isin(set(h2h["match_id"])) & (d.players["side"] == "opp")]["player_id"]
                 .astype(str)) if len(h2h) else set()
    if n_:
        a, b = st.columns([1.1, 1], gap="medium")
        with a:
            st.html(f'<div class="mini" style="margin:10px 0 6px 0">Their last {plural(n_, "game")} (league and playoffs)</div>')
            with st.container(border=True):
                st.html(scout_games_table(last))
        with b:
            st.html('<div class="mini" style="margin:10px 0 6px 0">Their players in those games (most games first)'
                    + (" · \"vs you\": played against you" if vs_ids else "") + "</div>")
            with st.container(border=True):
                st.html(scout_players_table(scout.their_players(sc, last, cid), vs_ids))
    if len(h2h):
        rec = analytics.record(h2h)
        section("Against you", f"{plural(rec['n'], 'match', 'matches')}: {rec['w']}-{rec['d']}-{rec['l']} "
                               f"({rec['gf']}-{rec['ga']} in goals). Click one to open its report.")
        view = h2h.sort_values("ts", ascending=False)
        ev = st.dataframe(pd.DataFrame({
            "When": view["when"], "Type": view["match_type"].map(labels.MATCH_TYPES),
            "Score": view["goals_for"].astype(int).astype(str) + " - " + view["goals_against"].astype(int).astype(str),
            "Result": view["result"],
            "Team grade": [grade_text(v) for v in view.get("team_grade_score", pd.Series(np.nan, index=view.index))],
            "Strength": [sr_diff_text(v) for v in view.get("sr_diff", pd.Series(np.nan, index=view.index))]}),
            hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key=f"h2h_{cid}",
            column_config={"When": st.column_config.DatetimeColumn(format="ddd D MMM, HH:mm"),
                           "Strength": st.column_config.TextColumn("Opp. SR")})
        sel = ev.selection.rows if ev and ev.selection else []
        if sel:
            st.switch_page(PAGES["matches"], query_params={"match": str(view.iloc[sel[0]]["match_id"])})
    if st.toggle("Show their members' season numbers (one more request to EA)", key=f"mem_{cid}"):
        try:
            with st.spinner("Asking EA for their members..."):
                mem = members_cached(cid, plat)
        except api.ApiError as e:
            st.warning(f"EA didn't answer: {e}")
            mem = []
        if mem:
            def n(v, f=int):
                try:
                    return f(float(v))
                except (TypeError, ValueError):
                    return None
            t = pd.DataFrame([{"Player": r.get("name"), "Position": (r.get("favoritePosition") or "").capitalize(),
                               "Games": n(r.get("gamesPlayed")), "Win %": n(r.get("winRate")), "Goals": n(r.get("goals")),
                               "Assists": n(r.get("assists")), "Rating": n(r.get("ratingAve"), float),
                               "MOTM": n(r.get("manOfTheMatch")), "Pass %": n(r.get("passSuccessRate")),
                               "OVR": n(r.get("proOverall"))} for r in mem]).sort_values("Games", ascending=False)
            st.dataframe(t, hide_index=True, width="stretch", column_config={
                "Win %": st.column_config.NumberColumn(format="%d%%"), "Pass %": st.column_config.NumberColumn(format="%d%%"),
                "Rating": st.column_config.NumberColumn(format="%.1f")})
        elif mem == []:
            st.caption("No members listed.")


def page_search():
    d = data()
    plat = CFG.get("platform") or "common-gen5"
    page_head("Search", "Look up any club in FC 27 Clubs: their last games, players and skill rating, and how they "
                        "compare with you. Handy for the club you're about to play.")
    with st.form("club_search", border=False):
        a, b = st.columns([5, 1], vertical_alignment="bottom")
        with a:
            q = st.text_input("Club name or club ID", key="search_q", placeholder="e.g. Kompistaxi AB, or a club ID")
        with b:
            go_ = st.form_submit_button("Search", icon=":material/search:", type="primary", width="stretch")
    if go_ and (q or "").strip():
        q = q.strip()
        if q.isdigit():
            st.session_state["search_res"] = None
            st.query_params["club"] = q
        else:
            try:
                with st.spinner(f'Searching EA for "{q}"...'):
                    st.session_state["search_res"] = (q, search_cached(q, plat))
            except api.ApiError as e:
                st.session_state["search_res"] = None
                st.warning(f"EA didn't answer: {e}")
            if "club" in st.query_params:
                del st.query_params["club"]
    m = d.matches
    if not m.empty:
        played = (m.sort_values("ts").groupby("opp_id").agg(name=("opp_name", "last"), last=("when", "max"),
                                                            n=("match_id", "size")).sort_values("last", ascending=False))
        played.index = played.index.astype(str)

        def picked():
            v = st.session_state.get("played_pick")
            if v:
                st.session_state["search_res"] = None
                st.query_params["club"] = v
                st.session_state["played_pick"] = None
        st.selectbox("Or pick a club you've played", list(played.index), index=None, key="played_pick", on_change=picked,
                     format_func=lambda i: f'{played.loc[i, "name"]} ({plural(played.loc[i, "n"], "match", "matches")}, '
                                           f'last {played.loc[i, "last"]:%d %b})',
                     placeholder="Clubs you've played, most recent first")
    found = st.session_state.get("search_res")
    if found and not st.query_params.get("club"):
        q, res = found
        if not res:
            empty_state("🔎", "No clubs found", f'EA has no club called "{esc(q)}". Try part of the name.')
        else:
            meta, rows = {}, []
            for r in res:
                info = r.get("clubInfo") or {}
                cid = str(r.get("clubId") or info.get("clubId"))
                c = parse.club_row(cid, info, plat)
                name = r.get("clubName") or info.get("name") or cid
                meta[cid] = {"name": name, "crest": c.get("crest_url"), "division": r.get("currentDivision")}
                w_, d__, l_ = (db._i(r.get(k)) or 0 for k in ("wins", "ties", "losses"))
                rows.append({"club_id": cid, "Crest": c.get("crest_url"), "Club": name,
                             "Division": labels.division(r.get("currentDivision")) if r.get("currentDivision") else "",
                             "Record": f"{w_}-{d__}-{l_}", "Games": db._i(r.get("gamesPlayed")),
                             "Goals": f'{db._i(r.get("goals")) or 0}-{db._i(r.get("goalsAgainst")) or 0}',
                             "Numbers": r.get("source", "")})
            st.session_state["search_meta"] = {**(st.session_state.get("search_meta") or {}), **meta}
            section("Clubs found", f"{plural(len(rows), 'club')} for \"{esc(q)}\". Click one for its profile.")
            t = pd.DataFrame(rows)
            ev = st.dataframe(t.drop(columns=["club_id"]), hide_index=True, width="stretch", on_select="rerun",
                              selection_mode="single-row", key=f"found_{abs(hash(q))}",
                              column_config={"Crest": st.column_config.ImageColumn("", width="small"),
                                             "Numbers": st.column_config.TextColumn(
                                                 "Numbers", help="Record and goals for this season or all time")})
            sel = ev.selection.rows if ev and ev.selection else []
            if sel:
                st.query_params["club"] = t.iloc[sel[0]]["club_id"]
                st.rerun()
    cid = st.query_params.get("club")
    if cid:
        if found:
            if st.button("Back to the results", icon=":material/arrow_back:"):
                del st.query_params["club"]
                st.rerun()
        club_profile(d, cid)
    elif not found:
        empty_state("🔎", "Find a club", "Type a club's name (or part of it) or its club ID, or pick a club you've "
                                        "played. You get their last 10 games, players and skill rating, and how they "
                                        "compare with you.")


# ================================================================= PAGE: SQUAD
def page_squad():
    d = data()
    page_head("Squad", "Everyone who has played for the club: EA's season numbers and the tracked matches.")
    mem = db.members_latest(CLUB_ID)
    tracked = analytics.player_table(d.ours, d.names) if not d.ours.empty else pd.DataFrame()
    if mem.empty and tracked.empty:
        empty_state("👥", "No players yet", "The squad appears after the first check with EA.")
        return
    if not mem.empty:
        section("Club members this season", "EA's own season totals for every member (also matches from before tracking).")
        show = mem[["name", "pro_name", "favorite_position", "overall", "games", "win_rate", "goals", "assists", "rating_avg",
                    "mom", "pass_rate", "shot_rate", "tackle_rate", "height"]].copy()
        show["favorite_position"] = show["favorite_position"].map(labels.position)
        st.dataframe(show, hide_index=True, width="stretch", column_config={
            "name": "EA name", "pro_name": "Pro", "favorite_position": "Position", "overall": "OVR", "games": "Matches",
            "win_rate": st.column_config.NumberColumn("Win %", format="%d%%"), "goals": "Goals", "assists": "Assists",
            "rating_avg": st.column_config.NumberColumn("Avg rating", format="%.1f"), "mom": "MOTM",
            "pass_rate": st.column_config.NumberColumn("Pass %", format="%d%%"),
            "shot_rate": st.column_config.NumberColumn("Shot success", format="%d%%"),
            "tackle_rate": st.column_config.NumberColumn("Tackle %", format="%d%%"),
            "height": st.column_config.NumberColumn("Height", format="%d cm")})
    if tracked.empty:
        return
    section("Tracked matches", "Every human who played for the club in a saved match, with numbers from those matches. "
                               "Grade = overall grade " + info_icon(overall_info()) + ", match grades are position-based "
                               + info_icon(grade_info()))
    pg = player_grades(d.ours)
    tracked = tracked.join(pg)
    t = tracked.reset_index()
    t["grade_txt"] = [grade_text(v) for v in t["overall"]]
    t["form_txt"] = [grade_text(v) for v in t["form"]]
    t["pos"] = t["pos"].map(labels.position)
    t["archetype"] = t["archetype"].map(arch_name)
    st.dataframe(t[["name", "grade_txt", "form_txt", "pos", "archetype", "matches", "win_pct", "rating", "goals", "assists",
                    "ga_pm", "shots", "conversion", "pass_pct", "tackle_pct", "saves", "mom", "last_played"]],
                 hide_index=True, width="stretch",
                 column_config={"name": "Player", "grade_txt": "Grade", "form_txt": "Form (last 5)",
                                "pos": "Usual position", "archetype": "Usual archetype", "matches": "Matches",
                                "win_pct": st.column_config.NumberColumn("Win %", format="%.0f%%"),
                                "rating": st.column_config.NumberColumn("Avg rating", format="%.2f"),
                                "goals": "Goals", "assists": "Assists",
                                "ga_pm": st.column_config.NumberColumn("G+A / match", format="%.2f"), "shots": "Shots",
                                "conversion": st.column_config.NumberColumn("Conversion", format="%.0f%%"),
                                "pass_pct": st.column_config.NumberColumn("Pass %", format="%.0f%%"),
                                "tackle_pct": st.column_config.NumberColumn("Tackle %", format="%.0f%%"),
                                "saves": "Saves", "mom": "MOTM",
                                "last_played": st.column_config.DatetimeColumn("Last played", format="D MMM")})
    section("Player")
    options = list(tracked.index)
    default = options.index(str(d.me_id)) if d.me_id and str(d.me_id) in options else 0
    pid = st.selectbox("Player", options, index=default, format_func=lambda p: tracked.loc[p, "name"],
                       label_visibility="collapsed")
    lines = d.ours[d.ours["player_id"].astype(str) == str(pid)].sort_values("ts")
    r = tracked.loc[pid]
    others = [str(p) for p in tracked.index if str(p) != str(pid)]
    if others and st.button("Compare with the others", icon=":material/compare_arrows:", key="sq_cmp"):
        mine = str(d.me_id) if d.me_id is not None else None
        pick = [str(pid)] + ([mine] if mine in others else []) + [o for o in others if o != mine]
        st.session_state["cmp_players"] = pick[:3]
        st.session_state["stats_pick"] = "Compare"
        st.switch_page(PAGES["stats"])
    ov, fm = r.get("overall"), r.get("form")
    kpis([{"label": "Overall grade", "value": f'{grades.letter(ov) or "-"} <span style="font-size:.6em;opacity:.7">{fmt(ov)}</span>',
           "sub": f"form {grades.letter(fm) or '-'} (last 5)", "tone": "good" if ov >= 75 else "volt" if ov >= 60 else "gold" if ov >= 40 else "bad",
           "help": "Average match grade, pulled towards the middle while there are only a few matches."},
          {"label": "Matches", "value": fmt(r["matches"]), "sub": f'{int(r["wins"])}-{int(r["draws"])}-{int(r["losses"])}'},
          {"label": "Avg rating", "value": fmt(r["rating"], 2), "sub": f'best {fmt(r["best_rating"], 1)}'},
          {"label": "Goals", "value": fmt(r["goals"]), "sub": f'{r["goals_pm"]:.2f} a match', "tone": "volt"},
          {"label": "Assists", "value": fmt(r["assists"]), "sub": f'{r["assists_pm"]:.2f} a match'},
          {"label": "Pass accuracy", "value": pct(r["pass_pct"])},
          {"label": "Tackle success", "value": pct(r["tackle_pct"])},
          {"label": "Man of the match", "value": fmt(r["mom"]), "tone": "gold"}])
    a, b = st.columns([1.3, 1], gap="medium")
    with a:
        fig = grade_chart(lines, title=f"{r['name']}: match grades")
        if fig:
            chart(fig)
        fig = rating_chart(lines, title=f"{r['name']}: match rating")
        if fig:
            chart(fig)
    with b:
        club_avg = tracked[tracked["matches"] >= 3]
        axes = [("Rating", "rating", 5, 10), ("G+A / match", "ga_pm", 0, 3), ("Pass %", "pass_pct", 40, 100),
                ("Tackle %", "tackle_pct", 0, 80), ("Win %", "win_pct", 0, 100), ("Conversion", "conversion", 0, 80)]

        def norm(v, lo, hi):
            return float(np.clip((0 if isna(v) else v - lo) / (hi - lo) * 100, 0, 100))
        fig = go.Figure()
        fig.add_trace(go.Scatterpolar(r=[norm(r[c], lo, hi) for _, c, lo, hi in axes] + [norm(r[axes[0][1]], axes[0][2], axes[0][3])],
                                      theta=[a_ for a_, *_ in axes] + [axes[0][0]], fill="toself", name=r["name"],
                                      line=dict(color=VOLT), fillcolor="rgba(196,242,58,0.18)"))
        if len(club_avg) > 1:
            avg = club_avg.mean(numeric_only=True)
            fig.add_trace(go.Scatterpolar(r=[norm(avg[c], lo, hi) for _, c, lo, hi in axes] + [norm(avg[axes[0][1]], axes[0][2], axes[0][3])],
                                          theta=[a_ for a_, *_ in axes] + [axes[0][0]], name="Club average",
                                          line=dict(color=AVG, dash="dot")))
        fig.update_layout(title="Profile", polar=dict(bgcolor="rgba(255,255,255,0.02)", radialaxis=dict(visible=False, range=[0, 100]),
                                                      angularaxis=dict(tickfont=dict(color="#e8efec", size=12), gridcolor="#2f3d37")))
        chart(fig)
    hist = db.member_history(CLUB_ID, r["name"])
    if len(hist) >= 2 and hist["overall"].nunique() > 1:
        fig = go.Figure(go.Scatter(x=hist["at"], y=hist["overall"], mode="lines+markers", line=dict(color=GOLD, width=3, shape="hv")))
        fig.update_layout(title=f"{r['name']}: Pro OVR over time")
        chart(fig)


# ================================================================= PAGE: SETTINGS
def online_settings():
    section("Online copy", "Puts the tracker online (Streamlit's free hosting) so your clubmates can open it on their "
                           "phones. The collector sends new data to your GitHub repository after every check, and the "
                           "online app reads it from there. Match notes stay on this PC.")
    ost = cloud.state()
    with st.container(border=True):
        a, b = st.columns([1.3, 1], gap="medium")
        with a:
            repo = st.text_input("GitHub repository", value=CFG.get("online_repo") or "",
                                 placeholder="owner/name",
                                 help="Your GitHub user name and the repository name, e.g. yourname/fc27-clubs-tracker")
            token = st.text_input("Access token", value=CFG.get("online_token") or "", type="password",
                                  help="A fine-grained personal access token for that repository only, with "
                                       "Contents: Read and write. It's kept in config.json on this PC.")
            on = st.toggle("Send new data after every check", value=CFG.get("online_on", True) is not False)
        with b:
            if ost.get("last_push"):
                st.html(f'<div class="mini">Last sent: <b>{esc(when_str(pd.Timestamp(ost["last_push"])))}</b> '
                        f'({plural(ost.get("last_sent", 0), "file")})'
                        + (f', {ost["left"]} to go' if ost.get("left") else "") + "</div>")
            if ost.get("last_check"):
                st.html(f'<div class="mini">Last check: {esc(when_str(pd.Timestamp(ost["last_check"])))}</div>')
            if ost.get("last_error"):
                st.html(f'<div class="mini" style="color:var(--bad)">Problem: {esc(ost["last_error"])}</div>')
            if not (ost.get("last_push") or ost.get("last_error")):
                st.html('<div class="mini">Nothing sent yet.</div>')
        c1, c2, c3 = st.columns(3)
        new_cfg = {"online_repo": repo.strip(), "online_token": token.strip(), "online_on": on}
        changed = any((CFG.get(k) or "") != (v or "") if k != "online_on" else (CFG.get(k, True) is not False) != v
                      for k, v in new_cfg.items())
        with c1:
            if st.button("Save", icon=":material/save:", width="stretch", disabled=not changed, key="online_save"):
                config.save(new_cfg)
                flash("Online copy settings saved.", "💾")
                st.rerun()
        with c2:
            if st.button("Test connection", icon=":material/link:", width="stretch", key="online_test"):
                try:
                    st.success("Connected: " + cloud.check({**CFG, **new_cfg}))
                except cloud.OnlineError as e:
                    st.error(f"Not working yet: {e}")
        with c3:
            if st.button("Send now", icon=":material/cloud_upload:", width="stretch", key="online_send",
                         disabled=not cloud.settings({**CFG, **new_cfg})):
                with st.spinner("Sending to GitHub..."):
                    res = cloud.push({**CFG, **new_cfg, "online_on": True}, log=lambda *a: None)
                if res.get("ok"):
                    flash(f"Sent {plural(res['sent'], 'file')}" + (f", {res['left']} to go (Send now again)"
                                                                   if res.get("left") else "") + ".", "☁️")
                else:
                    flash(f"Sending failed: {res.get('error')}", "⚠️")
                st.rerun()


def update_settings():
    section("Updates", f"Version {config.VERSION}. When this folder is a git clone of the GitHub repository, new "
                       "versions are installed by themselves (the collector checks about once an hour).")
    with st.container(border=True):
        if not updater.available():
            st.html('<div class="mini">This folder isn\'t a git clone of the repository yet, so updates come in by '
                    'hand. The README explains how to turn it into one (a couple of git commands).</div>')
            return
        a, b = st.columns([1.3, 1], gap="medium")
        with a:
            auto = st.toggle("Install updates by themselves", value=CFG.get("auto_update", True) is not False)
            if auto != (CFG.get("auto_update", True) is not False):
                config.save({"auto_update": auto})
                st.rerun()
        with b:
            if st.button("Check for updates now", icon=":material/system_update:", width="stretch"):
                with st.spinner("Asking GitHub..."):
                    res = updater.update(log=lambda *a: None)
                if res.get("updated"):
                    flash("Updated: refresh the page in a moment (the collector restarts by itself).", "⬆️")
                elif res.get("ok"):
                    flash("You have the newest version.", "✅")
                else:
                    flash(f"Couldn't update: {res.get('error')}", "⚠️")
                st.rerun()


def page_settings():
    page_head("Settings", "Club, collector, names and data. Changes are saved straight away.")
    stt = collector_ctl.status()
    section("Collector", "Saves every match from EA in the background. EA keeps only the last 10 of each type.")
    with st.container(border=True):
        a, b = st.columns([1.3, 1], gap="large")
        with a:
            if stt.get("running"):
                st.html(f'<div class="status-line"><span class="hud-dot live"></span>On · {esc(stt.get("state") or "")}</div>'
                        f'<div class="mini">Last check {esc(ago(stt.get("last_check")) or "-")}'
                        + (f' · next {esc(in_minutes(stt.get("next_check")))}' if stt.get("next_check") else "")
                        + (f' · connection: {esc(stt.get("transport"))}' if stt.get("transport") else "") + "</div>")
            else:
                st.html('<div class="status-line"><span class="hud-dot off"></span>Off</div>')
            c1, c2, c3 = st.columns(3)
            with c1:
                if stt.get("running"):
                    if st.button("Turn off", icon=":material/stop:", width="stretch"):
                        collector_ctl.stop()
                        flash("Collector stopped.", "⏹️")
                        st.rerun()
                elif st.button("Turn on", type="primary", icon=":material/play_arrow:", width="stretch"):
                    ok = collector_ctl.start()
                    flash("Collector started." if ok else "The collector didn't start. Try start_collector.bat to see why.", "▶️")
                    st.rerun()
            with c2:
                if st.button("Check now", icon=":material/refresh:", width="stretch", key="set_check"):
                    run_check(full=True)
                    st.rerun()
            with c3:
                boot = st.toggle("Start with Windows", value=collector_ctl.autostart_enabled(),
                                 help="Starts the collector hidden whenever you log in, so you never have to remember it.")
                if boot != collector_ctl.autostart_enabled():
                    collector_ctl.set_autostart(boot)
                    st.rerun()
        with b:
            act = st.number_input("Minutes between checks while playing", 1, 30, int(CFG.get("poll_active_minutes", 2)),
                                  help="While FC 27 is open on this PC, or for a while after a new match turned up.")
            idle = st.number_input("Minutes between checks otherwise", 5, 120, int(CFG.get("poll_idle_minutes", 15)),
                                   help="10 matches take hours to play, so even an hour never misses one.")
            beep = st.toggle("Beep when a new match is saved", value=bool(CFG.get("beep_on_new_match")))
            if (act, idle, beep) != (CFG.get("poll_active_minutes"), CFG.get("poll_idle_minutes"), CFG.get("beep_on_new_match")):
                config.save({"poll_active_minutes": int(act), "poll_idle_minutes": int(idle), "beep_on_new_match": bool(beep)})
        with st.expander("Collector log"):
            lines_ = collector_ctl.last_log_lines(30)
            st.html(f'<div class="log">{esc(chr(10).join(lines_) or "Nothing logged yet.")}</div>')

    online_settings()
    update_settings()
    section("Club and you")
    with st.container(border=True):
        club = db.club(CLUB_ID)
        a, b = st.columns([1.2, 1], gap="large")
        with a:
            st.html(f'<div class="club-pick">{crest(club.get("crest_url"), 56)}<div><div class="team-name">'
                    f'{esc(club.get("name") or CFG.get("club_name"))}</div><div class="mini">Club id {esc(CLUB_ID)} · '
                    f'{esc(labels.platform(CFG.get("platform")))}</div></div></div>')
            if st.button("Change club", icon=":material/swap_horiz:"):
                config.save({"welcome_done": False})
                st.rerun()
        with b:
            d = data()
            names = sorted(set(d.ours["name"]) if not d.ours.empty else set()
                           | set(db.members_latest(CLUB_ID)["name"] if CLUB_ID else []))
            cur = CFG.get("me_name") or ""
            if cur and cur not in names:
                names = [cur] + names
            if names:
                me_name = st.selectbox("You are", names, index=names.index(cur) if cur in names else 0)
                if me_name != cur:
                    config.save({"me_name": me_name, "me_id": None})
                    sync.resolve_me(config.load())
                    flash(f"Stats now follow {me_name}.", "🧍")
                    st.rerun()
        types = st.multiselect("Match types to collect", list(labels.MATCH_TYPES), default=CFG.get("match_types"),
                               format_func=labels.match_type)
        if types and types != CFG.get("match_types"):
            config.save({"match_types": types})
            st.rerun()

    section("Archetype names", "EA only sends a number for each archetype. Name the ones you know (yours, for a start).")
    with st.container(border=True):
        d = data()
        seen = sorted({str(a) for a in d.ours["archetype"].dropna()} if not d.ours.empty else set(), key=lambda x: int(x) if x.isdigit() else 99)
        if not seen:
            st.caption("Archetypes appear here after the first matches.")
        else:
            cnt = d.ours["archetype"].astype(str).value_counts()
            usual = d.ours.groupby(d.ours["archetype"].astype(str))["pos"].agg(lambda s: s.mode().iloc[0] if not s.mode().empty else "")
            df = pd.DataFrame({"Number": seen, "Name": [ARCH.get(a, "") for a in seen],
                               "Used in": [int(cnt.get(a, 0)) for a in seen],
                               "Usual position": [labels.position(usual.get(a, "")) for a in seen]})
            ed = st.data_editor(df, hide_index=True, disabled=["Number", "Used in", "Usual position"], key="arch_ed",
                                column_config={"Used in": st.column_config.NumberColumn("Player matches")})
            new = {r["Number"]: (r["Name"] or "").strip() for _, r in ed.iterrows() if (r["Name"] or "").strip()}
            if new != ARCH and st.button("Save names", icon=":material/save:"):
                config.save({"archetype_names": new})
                st.rerun()

    section("Display")
    with st.container(border=True):
        a, b, c = st.columns(3)
        with a:
            rw = st.number_input("Matches in rolling averages", 3, 20, int(UI["rolling_window"]))
        with b:
            gap = st.number_input("Minutes between matches that start a new session", 15, 240,
                                  int(CFG.get("session_gap_minutes", 45)))
        with c:
            sd = st.selectbox("Stats page opens on", list(STATS_SECTIONS),
                              index=list(STATS_SECTIONS).index(UI.get("stats_default")) if UI.get("stats_default") in STATS_SECTIONS else 0)
        t1, t2, t3 = st.columns(3)
        with t1:
            hk = st.toggle("You page: your numbers", value=UI["home_kpis"])
        with t2:
            hl = st.toggle("You page: last match", value=UI["home_last_match"])
        with t3:
            hc = st.toggle("You page: charts", value=UI["home_charts"])
        newui = {"rolling_window": int(rw), "stats_default": sd, "home_kpis": hk, "home_last_match": hl, "home_charts": hc}
        if newui != {k: UI[k] for k in newui} or gap != CFG.get("session_gap_minutes"):
            config.save({"ui": newui, "session_gap_minutes": int(gap)})
            st.rerun()

    section("Data", f"Everything is stored on this PC in {esc(str(db.DB_PATH))}.")
    with st.container(border=True):
        d = data()
        a, b, c = st.columns(3)
        with a:
            if not d.matches.empty:
                st.download_button("Matches (CSV)", d.matches.drop(columns=["date"], errors="ignore").to_csv(index=False),
                                   "matches.csv", "text/csv", icon=":material/download:", width="stretch")
        with b:
            if not d.players.empty:
                st.download_button("Every player line (CSV)", d.players.drop(columns=["events"], errors="ignore").to_csv(index=False),
                                   "player_lines.csv", "text/csv", icon=":material/download:", width="stretch")
        with c:
            st.caption(f"{plural(len(d.matches), 'match', 'matches')} · {plural(len(d.players), 'player line')}")


# ================================================================= PAGE: HELP
def grades_help_md():
    """The Grades part of the Help page, with the position weights straight from clubs/grades.py."""
    cols = [("forward", "Forward"), ("midfielder", "Midfielder"), ("defender", "Defender"), ("goalkeeper", "Keeper")]
    rows = "\n".join(f"| {p} | " + " | ".join(f"{int(grades.WEIGHTS[k][p] * 100)}%" if p in grades.WEIGHTS[k] else "-"
                                              for k, _ in cols) + " |" for p in grades.PARTS)
    scale = ", ".join(f"**{g}** from {t}" for t, g in grades.LETTERS[:-1]) + " and **F** under 10"
    return f"""
**Grades.** Every player in every match (both clubs) and both teams get a grade from **S** (best) to **F**, and
every player gets an overall grade. Grades are absolute, so they can be compared between players, matches and
clubs. Hover the (i) next to a grade for how it works, and hover a grade in a line-up for that player's breakdown.

- *Player match grade.* Five parts, each 0 to 100, from the player's own numbers in that match: **Rating** (EA's
  match rating: 5.0 is 0, 7.5 is 50, 10 is 100), **Attack** (goals 70% and conversion 30%: the share of shots
  scored, from 2 shots), **Creating** (assists 45%, pass accuracy 35%, passes made 20%), **Defending** (tackles
  won and tackle success; for defenders and keepers mostly goals conceded), **Goalkeeping** (saves and the share
  of shots saved, keepers only) and **Result** (win/draw/loss 70%, goal difference 30%). How much each part counts
  depends on the position played in that match, so a striker is judged on goals and conversion and a midfielder
  on assists, passing and tackles:

| Part | Forward | Midfielder | Defender | Keeper |
|---|---|---|---|---|
{rows}

  Man of the match adds 8, a red card takes off 10, and a stronger opponent adds up to 8 (3 per 100 skill rating
  above your club; a weaker one takes it off, see Opponent scouting below). A goal counts for a bit more from a
  defender than from a forward, because it's rarer.
- *Team match grade.* Result 35%, the team's average match rating 25%, attack (goals and shots) 15%, defence
  (goals conceded) 15% and passing 10%, plus up to 12 extra for playing with fewer humans than the other club
  (or up to 12 off for more) and up to 10 for playing a stronger club (4 per 100 skill rating; a weaker one takes
  it off).
- *From score to letter.* The total is compared with real FC 27 Clubs performances in the same position (or real
  team performances), from 115 matches between about 40 clubs: the grade number (0 to 100) is the share of them
  it beats. {scale}. So S is the top 10% and C is the middle.
- *Overall grade.* A player's average match grade, pulled towards the middle while they have only a few
  matches (as if they also played 4 average ones), so one great match doesn't top the table. *Form* is the
  average of the last 5 match grades.

"""


def page_help():
    page_head("Help", f"How the tracker works. Version {esc(config.VERSION)}.")
    st.markdown("""
**Where the numbers come from.** EA's own Clubs website (ea.com, Clubs) reads its numbers from a public Clubs API.
The tracker reads the same data: no login, no screenshots, and the numbers are exactly EA's. Every match has the
score and, for each human player on both clubs, goals, assists, shots, passes, tackles, saves, rating, man of the
match, position, archetype and minutes.

**Why the collector matters.** EA only keeps the **last 10 matches of each type** (league, playoffs, friendlies).
The collector checks every few minutes while you play (and every 15 minutes otherwise) and stores every match in
`data/clubs.db`, so your history keeps growing. Matches you played while it wasn't running are still saved
later, as long as there were fewer than 10 of that type in between.

**Starting it.** Turn on *Start with Windows* in Settings and you never have to think about it. On a console, the
PC still has to be on for the collector to check.

**What's not in EA's data.** Drop In and Rush matches (they aren't club matches), AI teammates, exact positions
(EA only says goalkeeper, defender, midfielder or forward), dribbles, interceptions and distance covered. EA sends
archetypes as numbers: name them in Settings.

""")
    st.markdown(grades_help_md())
    st.markdown("""
**Opponent scouting.** A few minutes after each match the collector pulls the other club's last 10 games (league
and playoffs) and EA's numbers for the club, and older matches fill in over its next checks (or press *Pull their
last 10 games now* in a match report). The report then shows how strong they were, their form, goals, humans and
usual team grade before your match, their last games and their players. *Strength* is their skill rating (SR, the
number EA pairs clubs by) minus yours: +50 or more is a stronger club, +150 a much stronger one (and the same below
zero for weaker clubs). It also counts in the grades: every 100 SR they have above you adds 4 to the team grade (up to
10) and 3 to each player's grade (up to 8), and a weaker club takes the same off. Beating a stronger club is an
*upset win*, losing to a weaker one an *upset loss*. EA keeps only a club's last 10 games of each type, so for older
matches their games after the match are used, and the report says so. Stats > Opponents has your results by strength.

**Seasons.** A Clubs league season ends with the game's season, after a final week of playoffs. EA's match data
doesn't say which season a match was in, so the tracker notices a new season when EA's season record starts again
from zero (or EA's count of finished seasons goes up), and uses the new season's game count to tell which matches
belong to it. Seasons are numbered like EA's calendar (Season 2 runs from 22 October to 3 December 2026), and if
the collector saw nothing around a season change, the calendar date splits the matches. Stats > Seasons compares
the seasons and their players, and once there's more than one, a Season picker at the top of Stats shows every
section for one season. On phones, the
pages that don't fit in the top bar are under *More*.

**Home.** What's happening now: the season so far and how long it has left, the last match and session, recent
matches and the top performers. During the playoffs (and for a few days after) a playoffs card sits on top: how
many of your playoff matches you've used, your playoff record and every playoff match.

**Playoffs.** The last week of each season, from Thursday to Thursday. Promotions stop when they open, and a club
can play only so many playoff matches, set by its division then: 10 in Divisions 5 and 4, 15 in Divisions 3 and 2,
20 in Division 1 and 25 in the Elite Division. League matches still count during the week. The final placing (a
share of all clubs) decides the fans the club gets, and the next season starts in Division 5.

**Search.** Look up any club by name (or club ID), or pick one you've played: you get their skill rating and
record, their last 10 games and players, how they compare with you and your matches against them. It asks EA right
away, so it's handy for the club you're about to play (their games are kept for scouting later, too).

**Online copy.** The tracker can also run online (Streamlit's free hosting) so the whole club can open it on their
phones. The collector on the PC sends new data to the club's GitHub repository after every check, and the online
app reads it from there, so new matches show up online a minute or two after they're saved. On the online copy,
pick whose numbers the You page shows at the top of that page. Times are in the PC's time zone. Match notes and
settings stay on the PC. Setting it up is in Settings > Online copy and the README.

**Compared with your usual.** In a match report, your match is also compared with your own other matches (from 5
matches): 50 is your usual level, 90 means better than 90% of your matches.

**If EA doesn't answer.** EA sometimes blocks programs or has outages. The collector waits longer each time and
tries again by itself. The Collector log in Settings shows what happened.
""")
    if ONLINE:   # the files below are the PC's
        return
    with st.expander("Files"):
        st.markdown(f"""
- `collector.py`: the background collector (`start_collector.bat` runs it in a window, to watch it work)
- `app.py`: this app (`start_app.bat`)
- `config.json`: settings, `data/clubs.db`: all matches, `data/collector_log.txt`: what the collector did
- Installed in `{esc(str(config.ROOT))}`
""")


# ================================================================= WELCOME (first start)
def needs_welcome() -> bool:
    return not (CFG.get("welcome_done") and CFG.get("club_id"))


def welcome_page():
    st.html('<div class="page-head"><div class="eyebrow">Welcome</div><h1 class="page-title">Find your club</h1>'
            '<div class="page-sub">The tracker saves every match your club plays, with every player\'s numbers, '
            'straight from EA. Three quick steps.</div></div>')
    plat_names = {"common-gen5": "PS5, Xbox Series X|S or PC", "nx": "Nintendo Switch 2"}
    with st.container(border=True):
        st.html('<div class="section-title" style="font-size:1.1rem">1 · Your platform and club</div>')
        a, b = st.columns([1, 2], gap="medium")
        with a:
            plat = st.radio("Platform", list(plat_names), format_func=plat_names.get,
                            index=list(plat_names).index(CFG.get("platform", "common-gen5")) if CFG.get("platform") in plat_names else 0)
        with b:
            q = st.text_input("Club name", value=st.session_state.get("w_q", CFG.get("club_name") or ""),
                              placeholder="Type your club's name, e.g. Dog Care FC")
            go_ = st.button("Search", icon=":material/search:", type="primary")
        if go_ and q.strip():
            st.session_state["w_q"] = q
            try:
                with st.spinner("Asking EA..."):
                    st.session_state["w_results"] = api.search_clubs(q, plat)
                st.session_state["w_err"] = None
            except api.ApiError as e:
                st.session_state["w_results"], st.session_state["w_err"] = [], str(e)
        if st.session_state.get("w_err"):
            st.error("Couldn't reach EA's Clubs API. Check your internet connection and try again in a minute.\n\n"
                     + st.session_state["w_err"][:300])
        results = st.session_state.get("w_results")
        if results is not None and not st.session_state.get("w_err"):
            if not results:
                st.warning("No club with that name. Check the spelling (the club's name, not your Pro's).")
            else:
                def label(r):
                    info = r.get("clubInfo") or {}
                    rec = f'{r.get("wins", "?")}-{r.get("ties", "?")}-{r.get("losses", "?")}'
                    return f'{r.get("clubName") or info.get("name")}   ·   {labels.division(r.get("currentDivision"))}   ·   {rec}'
                pick = st.radio("Which one is yours?", range(len(results[:12])), format_func=lambda i: label(results[i]))
                st.session_state["w_pick"] = results[pick]
    chosen = st.session_state.get("w_pick") if st.session_state.get("w_results") else None
    if chosen:
        cid = str(chosen.get("clubId"))
        with st.container(border=True):
            st.html('<div class="section-title" style="font-size:1.1rem">2 · Which player are you?</div>')
            if st.session_state.get("w_members_for") != cid:
                try:
                    st.session_state["w_members"] = [m.get("name") for m in api.members(cid, plat).get("members", []) if m.get("name")]
                except api.ApiError:
                    st.session_state["w_members"] = []
                st.session_state["w_members_for"] = cid
            mem = st.session_state.get("w_members") or []
            if mem:
                me = st.selectbox("You", mem + ["Someone else (type it)"], index=0)
                if me == "Someone else (type it)":
                    me = st.text_input("Your EA name (as it shows in the club)")
            else:
                me = st.text_input("Your EA name (as it shows in the club)", help="Your PSN / Xbox / EA name")
            st.caption("The tracker collects every player's numbers. This only decides whose stats the 'You' pages show.")
        with st.container(border=True):
            st.html('<div class="section-title" style="font-size:1.1rem">3 · Collecting</div>'
                    '<div class="mini">EA keeps only the last 10 matches of each type. The collector checks in the '
                    'background (every few minutes while you play) and saves everything.</div>')
            run = st.toggle("Start the collector now", value=True)
            boot = st.toggle("Start the collector with Windows", value=True, help="So you never have to remember it.")
        if st.button("Save and import my matches", type="primary", icon=":material/rocket_launch:"):
            info = chosen.get("clubInfo") or {}
            config.save({"platform": plat, "club_id": cid, "club_name": chosen.get("clubName") or info.get("name") or "",
                         "me_name": (me or "").strip(), "me_id": None, "welcome_done": True})
            if info:
                from clubs import parse
                db.save_club(parse.club_row(cid, info, plat))
            with st.spinner("Importing the matches EA still has..."):
                res = sync.sync(full=True, cfg=config.load())
            msg = f"Imported {plural(len(res['new']), 'match', 'matches')}."
            if not res.get("ok"):
                msg = "Saved your club, but EA didn't answer yet: the collector keeps trying."
            if run:
                collector_ctl.start()
            collector_ctl.set_autostart(bool(boot))
            for k in ("w_results", "w_pick", "w_members", "w_members_for", "w_q", "w_err"):
                st.session_state.pop(k, None)
            flash(msg, "⚽")
            st.rerun()


# ================================================================= ROUTING + TOP BAR
PAGES = {
    "home": st.Page(page_start, title="Home", icon=":material/home:", url_path="home", default=True),
    "team": st.Page(page_team, title="Team", icon=":material/shield:", url_path="team"),
    "you": st.Page(page_home, title="You", icon=":material/person:", url_path="you"),
    "matches": st.Page(page_matches, title="Matches", icon=":material/scoreboard:", url_path="matches"),
    "stats": st.Page(page_stats, title="Stats", icon=":material/insights:", url_path="stats"),
    "squad": st.Page(page_squad, title="Squad", icon=":material/groups:", url_path="squad"),
    "search": st.Page(page_search, title="Search", icon=":material/search:", url_path="search"),
    "settings": st.Page(page_settings, title="Settings", icon=":material/settings:", url_path="settings"),
    "help": st.Page(page_help, title="Help", icon=":material/help:", url_path="help"),
}
if ONLINE:   # settings live on the PC with the collector
    PAGES.pop("settings")
current = st.navigation(list(PAGES.values()), position="hidden")

BRAND = ('<div class="brand"><svg width="30" height="32" viewBox="0 0 34 38">'
         '<path d="M17 1 L32 6 V18 C32 28 25 34 17 37 C9 34 2 28 2 18 V6 Z" fill="#c4f23a"/>'
         '<circle cx="17" cy="18" r="8.2" fill="#0a0e0d"/>'
         '<path d="M17 12.6 l3.4 2.5 -1.3 4 h-4.2 l-1.3 -4 Z" fill="#c4f23a"/></svg>'
         '<div class="brand-name">CLUBS <span>TRACKER</span></div></div>')


NAV_MAIN = ("home", "you", "matches", "stats")   # always in the top bar; on phones the other pages are in a menu


def hud_html(stt, snap, club):
    """Status, division, skill rating and crest: the boxes at the right of the top bar."""
    boxes = []
    if ONLINE:
        res = getattr(_pkg, "_online_result", None) or {}
        when = f"last update {ago(pd.Timestamp(res['newest']))}" if res.get("newest") else "online"
        boxes.append(f'<div class="hud-box status" title="The online copy of the tracker: {esc(when)}">'
                     f'<span class="hud-dot live"></span>ONLINE</div>')
    elif stt.get("running"):
        live = stt.get("state") in ("playing", "active", "checking", "scouting")
        err = stt.get("state") == "error"
        boxes.append(f'<div class="hud-box status" title="The collector is on (state: {esc(stt.get("state"))})">'
                     f'<span class="hud-dot {"live" if live else "err" if err else ""}"></span>'
                     f'{"LIVE" if live else "EA DOWN" if err else "AUTO"}</div>')
    else:
        boxes.append('<div class="hud-box status" title="The collector is off: matches are not being saved" '
                     'style="color:var(--muted)"><span class="hud-dot off"></span>OFF</div>')
    if snap.get("division"):
        boxes.append(f'<div class="hud-box" title="Division"><small>DIV</small>{esc(labels.division(snap.get("division")).replace("Division ", ""))}</div>')
    if snap.get("skill_rating"):
        boxes.append(f'<div class="hud-box" title="Skill rating"><small>SR</small>{fmt(snap.get("skill_rating"))}</div>')
    if club.get("crest_url"):
        boxes.append(crest(club["crest_url"], 34))
    return f'<div class="hud">{"".join(boxes)}</div>'


def top_bar():
    stt = collector_ctl.status()
    snap = db.latest_club_snapshot(CLUB_ID) if CLUB_ID else {}
    club = db.club(CLUB_ID) if CLUB_ID else {}
    hud = hud_html(stt, snap, club)
    rest = [k for k in PAGES if k not in NAV_MAIN]
    here = next((k for k in rest if current.url_path == PAGES[k].url_path), None)   # a menu page is open
    with st.container(key="topbar", horizontal=True, vertical_alignment="center", gap="small"):
        st.html(BRAND, width="content")
        with st.container(key="navrow", horizontal=True, vertical_alignment="center", gap="small", width="content"):
            for key, pg in PAGES.items():
                active = current.url_path == pg.url_path
                more = "" if key in NAV_MAIN else "more_"   # hidden on phones: they're in the menu
                with st.container(key=("navon_" if active else "nav_") + more + key, width="content"):
                    st.page_link(pg, label=pg.title, icon=None)
        st.space("stretch")
        with st.container(key="hudbar", width="content"):
            st.html(hud, width="content")
        with st.container(key="navmenu_on" if here else "navmenu", width="content"):   # phones only
            with st.popover(PAGES[here].title if here else "More", icon=":material/menu:"):
                for key in rest:
                    st.page_link(PAGES[key], label=PAGES[key].title, icon=PAGES[key].icon, width="stretch")
                st.html(hud.replace('class="hud"', 'class="hud hud-menu"'))

if ONLINE and not CLUB_ID:
    res_ = getattr(_pkg, "_online_result", None) or {}
    empty_state("📡", "No data yet", "The online copy fills in once the tracker on the club's PC has sent its data. "
                + esc("; ".join(res_.get("errors", [])[:2])))
    st.stop()

if needs_welcome():
    welcome_page()
    st.stop()

if not CFG.get("team_tab_default"):   # 1.4: the Stats page opens on Team (the old Club section)
    config.save({"ui": {"stats_default": "Team"}, "team_tab_default": True})
    UI = config.ui()
    CFG = config.load()

if CLUB_ID and not ONLINE and not st.session_state.get("_collector_update_checked"):
    st.session_state["_collector_update_checked"] = True
    if collector_ctl.outdated():   # a collector from before 1.4 can't restart itself after an update
        with st.spinner("Updating the collector to the new version..."):
            ok_ = collector_ctl.restart()
        flash("The collector was updated: it now also scouts the clubs you play." if ok_
              else "The collector didn't restart: turn it on in Settings.", "🔄")

CRESTS = db.clubs_df().set_index("club_id")["crest_url"].to_dict() if CLUB_ID else {}
top_bar()
if "_flash" in st.session_state:
    msg, icon = st.session_state.pop("_flash")
    st.toast(msg, icon=icon)

if "sig" not in st.session_state:
    st.session_state.sig = db.signature()

current.run()


@st.fragment(run_every=10)
def auto_refresh():
    """Reload the page when a new match was saved (online: when the collector sent new data)."""
    if ONLINE:
        online_sync()
    sig = db.signature()
    if sig != st.session_state.sig:
        st.session_state.sig = sig
        st.rerun(scope="app")


auto_refresh()
st.session_state.sig = db.signature()
