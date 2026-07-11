"""The valuation interface and the basis factory (R3).

Two bases answer different questions:
  * ``realized`` / ``current`` : value assets by what they are worth NOW. A
    traded pick is valued by the player it became. Answers "who won." Sourceable
    today via DynastyProcess.
  * ``point_in_time`` : value each asset as of the trade date. Answers "was it a
    knowing fleece." There is NO free historical dynasty-value source going back
    to ~2019, so this fails loudly unless a historical provider is supplied. It
    must never silently fall back to current values and mislabel them.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

REALIZED = "realized"
CURRENT = "current"
POINT_IN_TIME = "point_in_time"
REDRAFT = "redraft"

_REALIZED_ALIASES = {REALIZED, CURRENT}
_PIT_ALIASES = {POINT_IN_TIME, "pit", "historical"}
_REDRAFT_ALIASES = {REDRAFT, "projected", "projection", "points"}


class PointInTimeUnavailable(RuntimeError):
    """Raised when point_in_time is requested but no historical source exists."""


class ValueProvider(ABC):
    """Values a resolved asset. ``basis`` labels every output it produces."""

    basis: str = REALIZED

    @abstractmethod
    def player_value(self, sleeper_id: str, name: Optional[str] = None) -> tuple[float, bool]:
        """Return (value, matched). Unmatched -> (0.0, False), never hidden."""

    @abstractmethod
    def pick_value(self, season: str, rnd: int, slot: Optional[int] = None,
                   tier: Optional[str] = None, num_teams: int = 12) -> float:
        ...


_PIT_MESSAGE = (
    "point_in_time basis is unavailable: no historical dynasty-value source is "
    "configured.\n"
    "  Why: DynastyProcess publishes only CURRENT values (a single scrape_date), "
    "so it cannot say what an asset was worth on a 2019-2024 trade date.\n"
    "  What would fix it: supply a dated historical value set via a "
    "HistoricalValueProvider -- e.g. KeepTradeCut historical values (API key or "
    "scraped dataset), or dated DynastyProcess snapshots keyed by scrape_date, "
    "covering every trade date in the league's history.\n"
    "  Refusing to fall back to current values, which would silently mislabel an "
    "OUTCOME (who won) as a POINT-IN-TIME judgement (was it fair when made).\n"
    "  Re-run with --basis realized for the current-value (outcome) analysis."
)


def make_provider(
    basis: str,
    *,
    cache=None,
    qb_format: int = 1,
    refresh: bool = False,
    historical_source=None,
    current_provider: Optional[ValueProvider] = None,
) -> ValueProvider:
    b = (basis or REALIZED).lower()
    if b in _REALIZED_ALIASES:
        if current_provider is not None:
            return current_provider
        from .current_provider import CurrentValueProvider
        prov = CurrentValueProvider.from_source(cache, qb_format, refresh=refresh)
        prov.basis = b
        return prov
    if b in _REDRAFT_ALIASES:
        from .projection_provider import ProjectionValueProvider
        prov = ProjectionValueProvider.from_source(cache, refresh=refresh)
        prov.basis = REDRAFT
        return prov
    if b in _PIT_ALIASES:
        if historical_source is None:
            raise PointInTimeUnavailable(_PIT_MESSAGE)
        from .historical_provider import HistoricalValueProvider
        return HistoricalValueProvider(historical_source, qb_format)
    raise ValueError(
        f"unknown value basis {basis!r}; expected one of "
        f"{sorted(_REALIZED_ALIASES | _PIT_ALIASES)}"
    )
