"""Thin, disk-cached client for the MyFantasyLeague (MFL) export API.

Public JSON, no auth. Every league lives on a specific host (e.g. www45); the
caller passes it. MFL returns numbers as strings and collapses single-element
arrays to objects -- callers use ``aslist`` to normalize.
"""
from __future__ import annotations

import time
from typing import Optional

import requests


def aslist(x):
    """MFL emits a bare object when a list has one element; normalize to a list."""
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


class MflClient:
    def __init__(self, cache, host: str, session: Optional[requests.Session] = None) -> None:
        self.cache = cache
        self.host = host
        self.session = session or requests.Session()
        self.fetch_count = 0

    def export(self, typ: str, year: str, league_id: str,
               max_age_hours: Optional[float] = None, **params) -> dict:
        extra = "_".join(f"{k}-{v}" for k, v in sorted(params.items()))
        key = f"mfl__{year}__{league_id}__{typ}{('__' + extra) if extra else ''}.json"
        if self.cache.fresh(key, max_age_hours):
            return self.cache.get_json(key)
        q = "&".join([f"TYPE={typ}", f"L={league_id}", "JSON=1"]
                     + [f"{k}={v}" for k, v in params.items()])
        url = f"https://{self.host}/{year}/export?{q}"
        headers = {"User-Agent": "Mozilla/5.0"}
        for attempt in range(4):
            r = self.session.get(url, headers=headers, timeout=30)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
            data = r.json()
            self.cache.put_json(key, data)
            self.fetch_count += 1
            return data
        r.raise_for_status()
        return {}

    def league(self, year, league_id):
        return self.export("league", year, league_id).get("league", {})

    def rosters(self, year, league_id):
        return self.export("rosters", year, league_id).get("rosters", {})

    def standings(self, year, league_id):
        return self.export("leagueStandings", year, league_id).get("leagueStandings", {})

    def schedule(self, year, league_id):
        return self.export("schedule", year, league_id).get("schedule", {})

    def players_detail(self, year, league_id, ids: list[str]) -> dict[str, dict]:
        """id -> {name, position} for specific players (used to place K/DST)."""
        if not ids:
            return {}
        data = self.export("players", year, league_id, DETAILS=1, PLAYERS=",".join(ids))
        out = {}
        for p in aslist(data.get("players", {}).get("player")):
            out[str(p.get("id"))] = {"name": p.get("name", ""), "position": p.get("position", "")}
        return out

    def login(self, year: str, username: str, password: str) -> Optional[str]:
        """Return the MFL_USER_ID cookie value for authenticated writes, or None."""
        url = f"https://{self.host}/{year}/login"
        r = self.session.get(url, params={"USERNAME": username, "PASSWORD": password, "XML": 1},
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        # MFL sets an MFL_USER_ID cookie on success
        for c in self.session.cookies:
            if c.name == "MFL_USER_ID":
                return c.value
        # some hosts return it in the body: <status MFL_USER_ID="...">
        if "MFL_USER_ID" in r.text:
            import re
            m = re.search(r'MFL_USER_ID="([^"]+)"', r.text)
            if m:
                return m.group(1)
        return None

    def import_lineup(self, year: str, league_id: str, week: int, starters: list[str],
                      user_cookie: str, franchise_id: Optional[str] = None) -> dict:
        """POST the official TYPE=lineup import. Requires a valid MFL_USER_ID cookie."""
        url = f"https://{self.host}/{year}/import"
        params = {"TYPE": "lineup", "L": league_id, "W": int(week),
                  "STARTERS": ",".join(starters), "JSON": 1}
        if franchise_id:
            params["FRANCHISE_ID"] = franchise_id
        r = self.session.post(url, params=params,
                              cookies={"MFL_USER_ID": user_cookie},
                              headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        try:
            return r.json()
        except Exception:
            return {"status_code": r.status_code, "text": r.text[:400]}
