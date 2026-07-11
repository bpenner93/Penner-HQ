"""Walk ``previous_league_id`` back to the origin to assemble every season.

Termination is defensive: Sleeper marks the origin's previous_league_id as the
string ``"0"`` (which 404s), but null / empty / a broken link / a cycle must all
terminate cleanly too.
"""
from __future__ import annotations

from typing import Any

_TERMINAL = {None, "", "0"}


def walk_chain(client, league_id: str) -> list[dict]:
    """Return the season league objects oldest -> newest.

    Oldest-first because cumulative value flow and identity canonicalization
    both want to iterate history forward.
    """
    seasons: list[dict] = []
    seen: set[str] = set()
    cur: Any = str(league_id)

    while cur not in _TERMINAL and cur not in seen:
        seen.add(cur)
        league = client.league(cur)
        if league is None:  # 404 / deleted league -> stop
            break
        seasons.append(league)
        nxt = league.get("previous_league_id")
        cur = str(nxt) if nxt is not None else None

    seasons.reverse()
    return seasons
