"""Weekly DFS projections for start/sit (redraft) — the matchup-specific signal.

Reads the per-week projection your DFS pipeline emits at
``data/processed/weekly_independent_<season>_wk<NN>.parquet`` (from
``weekly_independent.py``), keyed by the gsis ``player_id``. Redraft rosters
(ESPN/MFL) are already gsis-keyed so the join is direct; the weekly engine's
availability/injury overlay also fixes the season-long QB over-projection (a
benched/rookie QB drops out for the week).

If the file is missing it can generate it (slow: runs the full weekly engine).
Returns None when unavailable so callers fall back to season-long value.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from .. import config

_PROCESSED = config.PKG_ROOT.parent / "data" / "processed"


@dataclass
class WeeklyProjections:
    season: int
    week: int
    proj: dict[str, float] = field(default_factory=dict)      # gsis -> proj_final
    floor: dict[str, float] = field(default_factory=dict)
    ceiling: dict[str, float] = field(default_factory=dict)
    opp: dict[str, str] = field(default_factory=dict)         # gsis -> "vs BUF" / "@ NYJ"

    def value(self, gsis) -> Optional[float]:
        return self.proj.get(str(gsis))


def weekly_path(season: int, week: int):
    return _PROCESSED / f"weekly_independent_{int(season)}_wk{int(week):02d}.parquet"


def load_weekly(season: int, week: int, generate: bool = False) -> Optional[WeeklyProjections]:
    path = weekly_path(season, week)
    if not path.exists() and generate:
        try:
            from weekly_independent import export_week  # repo-root module
            export_week(int(season), int(week))
        except Exception:
            return None
    if not path.exists():
        return None

    df = pd.read_parquet(path)
    wp = WeeklyProjections(int(season), int(week))
    has_home = "is_home" in df.columns
    has_opp = "opp" in df.columns
    for r in df.itertuples(index=False):
        g = str(r.player_id)
        wp.proj[g] = float(getattr(r, "proj_final", 0) or 0)
        wp.floor[g] = float(getattr(r, "proj_floor", 0) or 0)
        wp.ceiling[g] = float(getattr(r, "proj_ceiling", 0) or 0)
        o = (str(getattr(r, "opp", "")) if has_opp else "").strip()
        if o and o.lower() != "nan":
            wp.opp[g] = ("vs " if (has_home and bool(getattr(r, "is_home"))) else "@ ") + o
    return wp
