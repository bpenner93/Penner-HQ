"""Stub for a real point-in-time (historical) value source.

This is the ONLY path to a true ``point_in_time`` basis. It is intentionally
not implemented -- no free historical dynasty-value dataset going back to ~2019
is available. It exists so a KTC/other historical provider can be dropped in
behind the same ``ValueProvider`` interface without touching the analysis code.

Wire-up sketch for whoever implements it:
  * ``source`` could be a KTC API client, a CSV of (asset, date, value), or a
    directory of dated DynastyProcess snapshots keyed by scrape_date.
  * ``player_value`` / ``pick_value`` must select the value AS OF the trade date
    passed by the caller (add a ``date`` argument here and thread it through).
"""
from __future__ import annotations

from typing import Optional

from .provider import ValueProvider, POINT_IN_TIME


class HistoricalValueProvider(ValueProvider):
    basis = POINT_IN_TIME

    def __init__(self, source, qb_format: int = 1) -> None:
        self.source = source
        self.qb_format = qb_format

    def player_value(self, sleeper_id: str, name: Optional[str] = None) -> tuple[float, bool]:
        raise NotImplementedError(
            "HistoricalValueProvider is a stub. Implement point-in-time player "
            "valuation against a dated historical source (e.g. KTC history) that "
            "returns the value AS OF the trade date."
        )

    def pick_value(self, season: str, rnd: int, slot: Optional[int] = None,
                   tier: Optional[str] = None, num_teams: int = 12) -> float:
        raise NotImplementedError(
            "HistoricalValueProvider is a stub. Implement point-in-time pick "
            "valuation against a dated historical source."
        )
