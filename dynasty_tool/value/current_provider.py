"""Current-value provider backed by DynastyProcess (via GitHub raw).

Join path (validated): sleeper ``player_id`` -> ``db_playerids.sleeper_id`` ->
``.fantasypros_id`` -> ``values-players.fp_id``. Never joins on display name
(names drift: "Marvin Harrison" vs "...Jr."); a normalized-name lookup exists
only as a last-resort fallback and is reported, not hidden.

Picks: ``values-picks.csv`` carries only ECR (consensus rank), not a value on
the player scale. We map a pick's ECR onto the players' ECR->value curve (both
files share the same ``ecr_1qb``/``ecr_2qb`` scale) -- which is exactly how
DynastyProcess derives its own pick values. Pick labels come in three
granularities: exact ``"2026 Pick 1.05"``, tiered ``"2027 Mid 1st"``, and
generic ``"2027 1st"`` / ``"2028 1st"``.
"""
from __future__ import annotations

import io
from typing import Optional

import numpy as np
import pandas as pd
import requests

from .provider import ValueProvider, REALIZED
from .. import config

DP_BASE = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/"
_FILES = {
    "players": "values-players.csv",
    "picks": "values-picks.csv",
    "ids": "db_playerids.csv",
}

_ORD = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th", 6: "6th", 7: "7th"}


def _norm_name(s: str) -> str:
    return "".join(ch for ch in str(s).lower() if ch.isalnum())


def load_dp_frames(cache, refresh: bool = False):
    """Fetch + cache the three DynastyProcess CSVs; return DataFrames."""
    frames = {}
    for key, fname in _FILES.items():
        ckey = "dp__" + fname
        if cache is not None and cache.fresh(ckey, config.VALUES_MAX_AGE_HOURS):
            txt = cache.get_text(ckey)
        else:
            resp = requests.get(DP_BASE + fname, timeout=45)
            resp.raise_for_status()
            txt = resp.content.decode("utf-8", errors="replace")
            if cache is not None:
                cache.put_text(ckey, txt)
        frames[key] = pd.read_csv(io.StringIO(txt))
    return frames["players"], frames["picks"], frames["ids"]


class CurrentValueProvider(ValueProvider):
    basis = REALIZED

    def __init__(self, players_df: pd.DataFrame, picks_df: pd.DataFrame,
                 ids_df: pd.DataFrame, qb_format: int = 1) -> None:
        self.qb_format = 2 if int(qb_format) == 2 else 1
        self.value_col = "value_2qb" if self.qb_format == 2 else "value_1qb"
        self.ecr_col = "ecr_2qb" if self.qb_format == 2 else "ecr_1qb"
        self.scrape_date = self._first(players_df, "scrape_date")

        # coverage counters (reported, never hidden)
        self.unmatched_players: set[str] = set()
        self.matched_players: set[str] = set()

        self._build_player_maps(players_df, ids_df)
        self._build_pick_curve(players_df)
        self._build_pick_labels(picks_df)

    # -- construction -------------------------------------------------------
    @classmethod
    def from_source(cls, cache, qb_format: int = 1, refresh: bool = False) -> "CurrentValueProvider":
        players_df, picks_df, ids_df = load_dp_frames(cache, refresh=refresh)
        return cls(players_df, picks_df, ids_df, qb_format=qb_format)

    @staticmethod
    def _first(df: pd.DataFrame, col: str):
        if col in df.columns and len(df):
            return df[col].iloc[0]
        return None

    def _build_player_maps(self, players_df, ids_df) -> None:
        # fp_id -> value
        self._fp_to_value: dict[str, float] = {}
        for _, r in players_df.iterrows():
            fp = r.get("fp_id")
            val = r.get(self.value_col)
            if pd.notna(fp) and pd.notna(val):
                self._fp_to_value[str(int(fp))] = float(val)
        # normalized name -> value (last-resort fallback only)
        self._name_to_value: dict[str, float] = {}
        for _, r in players_df.iterrows():
            val = r.get(self.value_col)
            if pd.notna(r.get("player")) and pd.notna(val):
                self._name_to_value.setdefault(_norm_name(r["player"]), float(val))
        # sleeper_id -> fp_id
        self._sleeper_to_fp: dict[str, str] = {}
        for _, r in ids_df.iterrows():
            sid, fp = r.get("sleeper_id"), r.get("fantasypros_id")
            if pd.notna(sid) and pd.notna(fp):
                self._sleeper_to_fp[str(int(sid))] = str(int(fp))

    def _build_pick_curve(self, players_df) -> None:
        cur = players_df.dropna(subset=[self.ecr_col, self.value_col]).sort_values(self.ecr_col)
        self._ecr_xp = cur[self.ecr_col].to_numpy(dtype=float)
        self._val_fp = cur[self.value_col].to_numpy(dtype=float)

    def _ecr_to_value(self, ecr: float) -> float:
        if len(self._ecr_xp) == 0 or pd.isna(ecr):
            return 0.0
        return float(np.interp(ecr, self._ecr_xp, self._val_fp))

    def _build_pick_labels(self, picks_df) -> None:
        self._pick_label_value: dict[str, float] = {}
        for _, r in picks_df.iterrows():
            label = r.get("player")
            ecr = r.get(self.ecr_col)
            if pd.notna(label) and pd.notna(ecr):
                self._pick_label_value[str(label)] = self._ecr_to_value(float(ecr))
        # covered (season, round) -> value, for proxying uncovered years
        self._generic_by_year_round: dict[tuple[str, int], float] = {}
        for label, val in self._pick_label_value.items():
            parts = label.split()
            if len(parts) == 2 and parts[1] in _ORD.values():
                yr = parts[0]
                rnd = next((k for k, v in _ORD.items() if v == parts[1]), None)
                if rnd:
                    self._generic_by_year_round[(yr, rnd)] = val

    # -- interface ----------------------------------------------------------
    def player_value(self, sleeper_id: str, name: Optional[str] = None) -> tuple[float, bool]:
        sid = str(sleeper_id)
        fp = self._sleeper_to_fp.get(sid)
        if fp is not None and fp in self._fp_to_value:
            self.matched_players.add(sid)
            return self._fp_to_value[fp], True
        # last-resort name fallback (reported via matched/unmatched sets)
        if name:
            v = self._name_to_value.get(_norm_name(name))
            if v is not None:
                self.matched_players.add(sid)
                return v, True
        self.unmatched_players.add(sid)
        return 0.0, False

    @staticmethod
    def _ord(rnd: int) -> str:
        return _ORD.get(int(rnd), f"{int(rnd)}th")

    @staticmethod
    def _slot_tier(slot: int, num_teams: int) -> str:
        third = max(1, num_teams / 3.0)
        if slot <= third:
            return "Early"
        if slot > 2 * third:
            return "Late"
        return "Mid"

    def pick_value(self, season: str, rnd: int, slot: Optional[int] = None,
                   tier: Optional[str] = None, num_teams: int = 12) -> float:
        season = str(season)
        rnd = int(rnd)
        labels: list[str] = []
        if slot is not None:
            labels.append(f"{season} Pick {rnd}.{int(slot):02d}")
        if tier:
            labels.append(f"{season} {tier} {self._ord(rnd)}")
        elif slot is not None:
            labels.append(f"{season} {self._slot_tier(int(slot), num_teams)} {self._ord(rnd)}")
        labels.append(f"{season} {self._ord(rnd)}")
        for lab in labels:
            if lab in self._pick_label_value:
                return self._pick_label_value[lab]
        return self._proxy_pick_value(season, rnd)

    def _proxy_pick_value(self, season: str, rnd: int) -> float:
        """No exact/generic label -> use the nearest covered year for that round."""
        try:
            target_yr = int(season)
        except ValueError:
            return 0.0
        candidates = [(yr, val) for (yr, r), val in self._generic_by_year_round.items() if r == rnd]
        if not candidates:
            return 0.0
        nearest = min(candidates, key=lambda yv: abs(int(yv[0]) - target_yr))
        return nearest[1]

    # -- coverage reporting -------------------------------------------------
    def coverage(self) -> dict:
        total = len(self.matched_players) + len(self.unmatched_players)
        return {
            "basis": self.basis,
            "qb_format": self.qb_format,
            "scrape_date": self.scrape_date,
            "players_seen": total,
            "players_matched": len(self.matched_players),
            "players_unmatched": len(self.unmatched_players),
            "unmatched_ids": sorted(self.unmatched_players),
        }
