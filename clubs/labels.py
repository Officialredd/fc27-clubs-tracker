"""Names for EA's codes (platforms, match types, divisions, positions, ...) and club crest links.

EA doesn't document these. They come from EA's own Clubs website and the open source proclubs-sdk
project; unknown codes are shown as they are instead of guessed."""

PLATFORMS = {
    "common-gen5": "PS5, Xbox Series X|S and PC",
    "nx": "Nintendo Switch 2",
}

MATCH_TYPES = {"leagueMatch": "League", "playoffMatch": "Playoffs", "friendlyMatch": "Friendly"}
MATCH_TYPE_CODES = {"1": "leagueMatch", "3": "playoffMatch", "5": "friendlyMatch"}
MATCH_TYPE_SHORT = {"leagueMatch": "LGE", "playoffMatch": "PO", "friendlyMatch": "FRD"}

DIVISIONS = {"1": "Elite", "2": "Division 1", "3": "Division 2", "4": "Division 3", "5": "Division 4",
             "6": "Division 5"}

REPUTATION = {"0": "Hometown Heroes", "1": "Emerging Stars", "2": "Well Known", "3": "World Renown"}

POSITIONS = {"goalkeeper": "Goalkeeper", "defender": "Defender", "midfielder": "Midfielder", "forward": "Forward"}
POS_SHORT = {"goalkeeper": "GK", "defender": "DEF", "midfielder": "MID", "forward": "FWD"}
POS_ORDER = {"goalkeeper": 0, "defender": 1, "midfielder": 2, "forward": 3}
POS_COLORS = {"goalkeeper": "#f2b84b", "defender": "#4f8ff7", "midfielder": "#3ecf8e", "forward": "#f25c54"}

# the player's console family (EA's "namespace")
DEVICES = {"1": "PlayStation", "2": "Xbox", "3": "PC", "4": "Nintendo"}

CREST_BASE = ("https://eafc24.content.easports.com/fifa/fltOnlineAssets/24B23FDE-7835-41C2-87A2-F453DFDB2E82/"
              "2024/fcweb/crests/256x256/l")


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def platform(p) -> str:
    return PLATFORMS.get(_s(p), _s(p))


def match_type(t) -> str:
    t = _s(t)
    return MATCH_TYPES.get(t) or MATCH_TYPES.get(MATCH_TYPE_CODES.get(t, ""), t or "Match")


def division(code) -> str:
    c = _s(code)
    if not c or c in ("-1", "None", "null"):
        return "-"
    return DIVISIONS.get(c, f"Division code {c}")


def reputation(code) -> str:
    """The club's reputation (EA's name for it), which comes from how many fans the club has."""
    c = _s(code)
    return REPUTATION.get(c, f"Tier {c}" if c else "-")


def next_reputation(code) -> str | None:
    """The fan level after this one (None at the top or when unknown)."""
    c = _s(code)
    return REPUTATION.get(str(int(c) + 1)) if c and c.isdigit() else None


def position(p) -> str:
    return POSITIONS.get(_s(p), _s(p).title() or "-")


def pos_short(p) -> str:
    return POS_SHORT.get(_s(p), _s(p)[:3].upper() or "-")


def device(ns) -> str:
    return DEVICES.get(_s(ns), "-")


def archetype(aid, names: dict | None = None) -> str:
    a = _s(aid)
    if not a or a == "0":
        return "-"
    name = (names or {}).get(a)
    return name or f"Archetype {a}"


def crest_url(team_id=None, selected_kit_type=None, crest_asset_id=None) -> str | None:
    """EA's crest picture for a club: its custom crest when it uses one, otherwise the real team's crest."""
    asset = None
    if _s(selected_kit_type) == "1" and _s(crest_asset_id).isdigit():
        asset = _s(crest_asset_id)
    if asset is None and _s(team_id).isdigit():
        asset = _s(team_id)
    return f"{CREST_BASE}{asset}.png" if asset else None
