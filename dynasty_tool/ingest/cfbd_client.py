"""CollegeFootballData.com client — the devy board's data source.

Free with an API key (https://collegefootballdata.com/profile). The key belongs
in Streamlit secrets as ``CFBD_API_KEY``; never in this repo.

Deliberately built on the two league-wide endpoints rather than per-team ones:
``/player/usage`` returns every player's share of their offence in **one**
request, and ``/recruiting/players`` returns a whole recruiting class in one
more. A per-team approach would be 130+ requests per season and would burn the
rate limit for no extra information.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import requests

from ..cache import DiskCache

BASE = "https://api.collegefootballdata.com"
UA = "Penner-HQ/1.0 (+https://github.com/bpenner93/Penner-HQ)"


class CfbdClient:
    def __init__(self, api_key: str, cache: DiskCache,
                 session: Optional[requests.Session] = None,
                 timeout: float = 30.0) -> None:
        if not api_key:
            raise RuntimeError("no CFBD API key configured")
        self.api_key = api_key
        self.cache = cache
        self.session = session or requests.Session()
        self.timeout = timeout
        self.fetch_count = 0

    def _get(self, path: str, params: dict,
             max_age_hours: Optional[float] = 24.0) -> Any:
        stamp = "__".join(f"{k}-{v}" for k, v in sorted(params.items()))
        key = f"cfbd__{path.strip('/').replace('/', '__')}__{stamp}.json"
        if self.cache.fresh(key, max_age_hours):
            try:
                return self.cache.get_json(key)
            except Exception:
                pass
        r = self.session.get(
            f"{BASE}/{path.lstrip('/')}", params=params,
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Accept": "application/json", "User-Agent": UA},
            timeout=self.timeout)
        if r.status_code == 401:
            raise RuntimeError("CFBD rejected the API key (401)")
        if r.status_code == 429:
            raise RuntimeError("CFBD rate limit hit (429) — try again later")
        r.raise_for_status()
        data = r.json()
        self.cache.put_json(key, data)
        self.fetch_count += 1
        return data

    # -- endpoints ----------------------------------------------------------
    def player_usage(self, year: int, exclude_garbage_time: bool = True) -> list[dict]:
        """Every FBS player's share of their team's offence, league-wide."""
        return self._get("player/usage",
                         {"year": int(year),
                          "excludeGarbageTime": str(bool(exclude_garbage_time)).lower()}
                         ) or []

    def recruits(self, year: int) -> list[dict]:
        """One recruiting class. Immutable once signed, so cached for a month."""
        return self._get("recruiting/players", {"year": int(year)},
                         max_age_hours=24 * 30) or []
