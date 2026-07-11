"""Valuation layer.

``ValueProvider`` is the interface (R3). ``CurrentValueProvider`` (DynastyProcess
current values) is shipped now; ``HistoricalValueProvider`` is a stub because no
free historical dynasty-value source exists. ``make_provider`` enforces the R3
guard: requesting ``point_in_time`` with no historical source configured fails
loudly instead of silently returning current values.
"""
from .provider import (
    ValueProvider,
    PointInTimeUnavailable,
    make_provider,
    REALIZED,
    CURRENT,
    POINT_IN_TIME,
)

__all__ = [
    "ValueProvider",
    "PointInTimeUnavailable",
    "make_provider",
    "REALIZED",
    "CURRENT",
    "POINT_IN_TIME",
]
