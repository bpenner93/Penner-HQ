"""Redraft value provider backed by the project's OWN season projection engine.

For redraft/keeper leagues, "value" is projected fantasy points this season, not
dynasty value. Dynasty value age-discounts win-now studs (a 34-yo Kelce is a
weak dynasty asset but a strong redraft starter), so using it for redraft would
systematically misrank rosters. Instead we read ``season_rankings.parquet``
(season_projected_pts, proj_pg, VOR), keyed by ``gsis_id``.

Join path: sleeper ``player_id`` -> ``db_playerids.sleeper_id`` -> ``gsis_id`` ->
``season_rankings.player_id``. (The same crosswalk carries espn_id / mfl_id, so
this provider will serve ESPN and MFL rosters too once those adapters land.)
Unmatched players -> value 0, counted and reported, never hidden.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from .provider import ValueProvider
from .current_provider import load_dp_frames, _norm_name
from .. import config


class ProjectionValueProvider(ValueProvider):
    basis = "redraft"

    def __init__(self, rankings_df: pd.DataFrame, ids_df: pd.DataFrame) -> None:
        self.unmatched_players: set[str] = set()
        self.matched_players: set[str] = set()
        self.season = str(rankings_df["season"].iloc[0]) if "season" in rankings_df and len(rankings_df) else ""
        self._build(rankings_df, ids_df)

    def _build(self, sr: pd.DataFrame, ids: pd.DataFrame) -> None:
        # platform id -> gsis (sleeper now; espn/mfl reserved for later adapters)
        self.sleeper_to_gsis: dict[str, str] = {}
        self.espn_to_gsis: dict[str, str] = {}
        self.mfl_to_gsis: dict[str, str] = {}
        for _, row in ids.iterrows():
            g = row.get("gsis_id")
            if not isinstance(g, str):
                continue
            for col, dest in (("sleeper_id", self.sleeper_to_gsis),
                              ("espn_id", self.espn_to_gsis),
                              ("mfl_id", self.mfl_to_gsis)):
                v = row.get(col)
                if pd.notna(v):
                    try:
                        dest[str(int(v))] = g
                    except (TypeError, ValueError):
                        dest[str(v)] = g

        self.g2pts = dict(zip(sr["player_id"], sr["season_projected_pts"]))
        self.g2ppg = dict(zip(sr["player_id"], sr.get("proj_pg", sr["season_projected_pts"] / 17.0)))
        vor = sr["value_over_replacement"] if "value_over_replacement" in sr else sr["season_projected_pts"]
        self.g2vor = dict(zip(sr["player_id"], vor))
        self.name_to_gsis = {_norm_name(n): g for n, g in
                             zip(sr["player_display_name"], sr["player_id"])}

    # -- gsis-keyed core (used by every platform) ---------------------------
    def _pts_for_gsis(self, gsis: Optional[str]) -> Optional[float]:
        if gsis is None:
            return None
        v = self.g2pts.get(gsis)
        return float(v) if v is not None and v == v else None

    def _resolve_gsis(self, pid: str, id_type: str, name: Optional[str]) -> Optional[str]:
        if id_type == "gsis":  # already a gsis id (normalized cross-platform key)
            g = str(pid) if str(pid) in self.g2pts else None
        else:
            table = {"sleeper": self.sleeper_to_gsis, "espn": self.espn_to_gsis,
                     "mfl": self.mfl_to_gsis}.get(id_type, self.sleeper_to_gsis)
            g = table.get(str(pid))
        if g is None and name:
            g = self.name_to_gsis.get(_norm_name(name))
        return g

    # -- ValueProvider interface --------------------------------------------
    def player_value(self, sleeper_id: str, name: Optional[str] = None,
                     id_type: str = "sleeper") -> tuple[float, bool]:
        gsis = self._resolve_gsis(sleeper_id, id_type, name)
        pts = self._pts_for_gsis(gsis)
        if pts is None:
            self.unmatched_players.add(str(sleeper_id))
            return 0.0, False
        self.matched_players.add(str(sleeper_id))
        return pts, True

    def ppg(self, pid: str, name: Optional[str] = None, id_type: str = "sleeper") -> float:
        gsis = self._resolve_gsis(pid, id_type, name)
        if gsis is None:
            return 0.0
        v = self.g2ppg.get(gsis)
        return float(v) if v is not None and v == v else 0.0

    def pick_value(self, season: str, rnd: int, slot: Optional[int] = None,
                   tier: Optional[str] = None, num_teams: int = 12) -> float:
        return 0.0  # draft picks are not a redraft-season asset

    def view(self, id_type: str) -> "ProviderView":
        """A provider that resolves a specific id space (e.g. 'gsis' for ESPN/MFL)."""
        return ProviderView(self, id_type)

    @classmethod
    def from_source(cls, cache, refresh: bool = False, rankings_path=None):
        path = rankings_path or config.SEASON_RANKINGS
        try:
            sr = pd.read_parquet(path)
        except (FileNotFoundError, OSError) as exc:
            raise RuntimeError(
                f"redraft basis needs the season projection file at {path}, which is "
                f"missing ({exc}). Generate it with the project's season_rankings step, "
                f"or pass a different path."
            ) from exc
        _players, _picks, ids = load_dp_frames(cache, refresh=refresh)
        return cls(sr, ids)


class ProviderView:
    """Wraps a ProjectionValueProvider to fix the player-id space it resolves.

    ESPN/MFL contexts normalize rosters to gsis ids, so they pass ``view('gsis')``;
    the analyzer keeps calling ``player_value(pid)`` id-space-agnostically.
    """

    def __init__(self, inner: "ProjectionValueProvider", id_type: str) -> None:
        self.inner = inner
        self.id_type = id_type
        self.basis = inner.basis

    def player_value(self, pid, name=None):
        return self.inner.player_value(pid, name, id_type=self.id_type)

    def ppg(self, pid, name=None):
        return self.inner.ppg(pid, name, id_type=self.id_type)

    def pick_value(self, *a, **k):
        return self.inner.pick_value(*a, **k)

    @property
    def unmatched_players(self):
        return self.inner.unmatched_players

    @property
    def matched_players(self):
        return self.inner.matched_players
