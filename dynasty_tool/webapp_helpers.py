"""Pure helpers for the Streamlit app (dynasty_tool/app.py).

Everything here is import-safe without Streamlit and unit-testable: design
tokens (from the validated dark palette), Sleeper CDN URL builders, player-meta
subsetting, ECR enrichment, radar percentiles, and the extra-league registry.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Design tokens — validated dark palette (dataviz reference instance).
# Surfaces/ink/categorical dark steps come from the pre-validated set; every
# position color is always paired with a text badge (relief rule), so identity
# is never carried by color alone.
# ---------------------------------------------------------------------------
INK = {
    "page": "#0d0d0d",
    "surface": "#1a1a19",
    "primary": "#ffffff",
    "secondary": "#c3c2b7",
    "muted": "#898781",
    "grid": "#2c2c2a",
    "baseline": "#383835",
    "border": "rgba(255,255,255,0.10)",
}
SERIES = {  # categorical, dark-surface steps
    "blue": "#3987e5", "aqua": "#199e70", "yellow": "#c98500", "green": "#008300",
    "violet": "#9085e9", "red": "#e66767", "magenta": "#d55181", "orange": "#d95926",
}
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}

POS_COLORS = {  # fixed assignment; always shown with a text badge
    "QB": SERIES["red"], "RB": SERIES["aqua"], "WR": SERIES["blue"],
    "TE": SERIES["orange"], "K": SERIES["violet"], "DST": SERIES["yellow"],
    "DL": "#6b6a64", "LB": "#6b6a64", "DB": "#6b6a64", "IDP": "#6b6a64",
}
TIER_COLORS = {"high": SERIES["aqua"], "mid": SERIES["yellow"], "low": SERIES["blue"]}

_VALUE_POS = ("QB", "RB", "WR", "TE")


# -- Sleeper CDN --------------------------------------------------------------

def headshot_url(sleeper_id: str, thumb: bool = True) -> str:
    base = "https://sleepercdn.com/content/nfl/players/"
    return f"{base}thumb/{sleeper_id}.jpg" if thumb else f"{base}{sleeper_id}.jpg"


def team_logo_url(team: Optional[str]) -> Optional[str]:
    if not team:
        return None
    return f"https://sleepercdn.com/images/team_logos/nfl/{str(team).lower()}.png"


def avatar_url(avatar_id: Optional[str]) -> Optional[str]:
    return f"https://sleepercdn.com/avatars/{avatar_id}" if avatar_id else None


# -- player meta --------------------------------------------------------------

_META_KEYS = ("first_name", "last_name", "full_name", "position", "team", "age",
              "years_exp", "college", "height", "weight", "number",
              "injury_status", "status")


def meta_subset(players_meta: dict, ids) -> dict:
    """Slim {pid: meta} for just the given ids (pickle-friendly for st.cache)."""
    out = {}
    for pid in ids:
        m = players_meta.get(str(pid))
        if isinstance(m, dict):
            out[str(pid)] = {k: m.get(k) for k in _META_KEYS if m.get(k) is not None}
    return out


def ecr_map(players_df, ids_df, qb_format: int = 1) -> dict:
    """sleeper_id -> {'ecr','ecr_pos','value','pos','team','draft_year'} from the
    DynastyProcess frames (pure; caller supplies frames from load_dp_frames)."""
    ecr_col = "ecr_2qb" if int(qb_format) == 2 else "ecr_1qb"
    val_col = "value_2qb" if int(qb_format) == 2 else "value_1qb"
    by_fp = {}
    for _, r in players_df.iterrows():
        fp = r.get("fp_id")
        if fp == fp and fp is not None:  # not NaN
            by_fp[str(int(fp))] = {
                "ecr": r.get(ecr_col), "ecr_pos": r.get("ecr_pos"),
                "value": r.get(val_col), "pos": r.get("pos"),
                "team": r.get("team"), "draft_year": r.get("draft_year"),
            }
    out = {}
    for _, r in ids_df.iterrows():
        sid, fp = r.get("sleeper_id"), r.get("fantasypros_id")
        if sid == sid and fp == fp and sid is not None and fp is not None:
            row = by_fp.get(str(int(fp)))
            if row:
                out[str(int(sid))] = row
    return out


# -- radar / percentiles -------------------------------------------------------

def radar_percentiles(rosters: dict, uid: str) -> dict:
    """{pos: percentile 0..1} of uid's starter value by position vs the league."""
    if uid not in rosters:
        return {}
    out = {}
    for pos in _VALUE_POS:
        vals = sorted(r.starter_value_by_pos.get(pos, 0.0) for r in rosters.values())
        mine = rosters[uid].starter_value_by_pos.get(pos, 0.0)
        below = sum(1 for v in vals if v < mine)
        out[pos] = below / max(1, len(vals) - 1) if len(vals) > 1 else 0.5
    return out


def fmt_k(v: float) -> str:
    """43,872 -> '43.9k' (compact value labels)."""
    v = float(v)
    return f"{v/1000:.1f}k" if abs(v) >= 1000 else f"{v:.0f}"


# -- extra-league registry (MFL/ESPN entries the API can't discover) ----------

def load_extra_leagues(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_extra_leagues(path: Path, leagues: list[dict]) -> None:
    Path(path).write_text(json.dumps(leagues, indent=2), encoding="utf-8")


def default_extra_leagues() -> list[dict]:
    """Seed with the user's known MFL league so it appears on first launch."""
    return [{
        "platform": "mfl", "host": "www45.myfantasyleague.com",
        "league_id": "65695", "season": "2025",
        "name": "2025 Fantasy Football (MFL)", "team": "Brandon & Cody",
    }]
