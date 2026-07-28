"""Thin, disk-cached wrapper over the public Sleeper API.

No auth required. Every endpoint response is cached to disk so the full chain
(~250 transaction fetches x N seasons) is fetched once and replayed. 404s are
cached as ``null`` so dead endpoints (e.g. the origin's previous_league_id) are
not re-hit. Transient 429/5xx are retried with linear backoff.
"""
from __future__ import annotations

import time
from typing import Any, Optional

import requests

from ..cache import DiskCache
from .. import config

BASE = "https://api.sleeper.app/v1"


class SleeperClient:
    def __init__(
        self,
        cache: DiskCache,
        session: Optional[requests.Session] = None,
        request_pause: float = 0.0,
        max_retries: int = 4,
    ) -> None:
        self.cache = cache
        self.session = session or requests.Session()
        self.request_pause = request_pause
        self.max_retries = max_retries
        self.fetch_count = 0  # network fetches this run (cache misses)

    # -- core ---------------------------------------------------------------
    def _get(self, path: str, max_age_hours: Optional[float] = None) -> Any:
        key = "sleeper__" + path.strip("/").replace("/", "__") + ".json"
        if self.cache.fresh(key, max_age_hours):
            return self.cache.get_json(key)

        url = f"{BASE}/{path.lstrip('/')}"
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                resp = self.session.get(url, timeout=30)
            except requests.RequestException as exc:  # network hiccup
                last_exc = exc
                time.sleep(1.0 * (attempt + 1))
                continue
            if resp.status_code == 404:
                self.cache.put_json(key, None)  # cache the miss
                return None
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(1.5 * (attempt + 1))
                continue
            resp.raise_for_status()
            data = resp.json()
            self.cache.put_json(key, data)
            self.fetch_count += 1
            if self.request_pause:
                time.sleep(self.request_pause)
            return data
        if last_exc:
            raise last_exc
        raise RuntimeError(f"Sleeper GET failed after {self.max_retries} tries: {url}")

    # -- endpoints ----------------------------------------------------------
    def league(self, league_id: str) -> Optional[dict]:
        return self._get(f"league/{league_id}",
                         max_age_hours=config.LEAGUE_META_MAX_AGE_HOURS)

    def users(self, league_id: str) -> list[dict]:
        return self._get(f"league/{league_id}/users",
                         max_age_hours=config.LEAGUE_META_MAX_AGE_HOURS) or []

    def rosters(self, league_id: str) -> list[dict]:
        """Current rosters. **Must** carry a TTL — this is the one endpoint that
        changes every time anyone trades, and caching it forever is what made
        trades invisible until the container restarted."""
        return self._get(f"league/{league_id}/rosters",
                         max_age_hours=config.ROSTERS_MAX_AGE_HOURS) or []

    def transactions(self, league_id: str, week: int) -> list[dict]:
        return self._get(f"league/{league_id}/transactions/{week}",
                         max_age_hours=config.TRANSACTIONS_MAX_AGE_HOURS) or []

    def drafts(self, league_id: str) -> list[dict]:
        return self._get(f"league/{league_id}/drafts") or []

    def draft_picks(self, draft_id: str) -> list[dict]:
        return self._get(f"draft/{draft_id}/picks") or []

    def matchups(self, league_id: str, week: int) -> list[dict]:
        return self._get(f"league/{league_id}/matchups/{week}",
                         max_age_hours=config.MATCHUPS_MAX_AGE_HOURS) or []

    def state(self) -> dict:
        # tiny + changes daily; refresh often
        return self._get("state/nfl", max_age_hours=6) or {}

    def user_leagues(self, user_id: str, season: str) -> list[dict]:
        return self._get(f"user/{user_id}/leagues/nfl/{season}",
                         max_age_hours=config.LEAGUE_META_MAX_AGE_HOURS) or []

    def user(self, id_or_name: str) -> Optional[dict]:
        """Resolve a Sleeper username OR user_id to the user object (both work)."""
        return self._get(f"user/{id_or_name}")

    def players(self) -> dict:
        """The big (~5MB) player metadata blob; refreshed at most daily."""
        return self._get("players/nfl", max_age_hours=config.PLAYERS_MAX_AGE_HOURS) or {}

    def trending(self, kind: str = "add", lookback_hours: int = 24,
                 limit: int = 25) -> list[dict]:
        """[{'player_id','count'}] — real add/drop volume across every Sleeper
        league, not an opinion.

        This is the closest thing to a live market: it is what managers are
        actually doing right now, which moves hours before expert values do.
        Short cache (30 min) because that immediacy is the entire point.
        """
        kind = "drop" if str(kind).lower().startswith("d") else "add"
        path = (f"players/nfl/trending/{kind}"
                f"?lookback_hours={int(lookback_hours)}&limit={int(limit)}")
        return self._get(path, max_age_hours=0.5) or []
