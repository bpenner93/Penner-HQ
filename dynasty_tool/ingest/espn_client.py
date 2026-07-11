"""Thin, disk-cached client for the ESPN Fantasy read API.

Private leagues need the ``espn_s2`` cookie (SWID optional in practice). We read
it from the env (ESPN_S2 / ESPN_SWID) or ``cache/espn_cookies.json`` so secrets
never have to be passed on the command line.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

import requests

from .. import config

READS_BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"
_DEFAULT_VIEWS = ("mTeam", "mRoster", "mSettings", "mMatchup")


def load_espn_cookies(cache_dir: Optional[Path] = None) -> dict:
    s2 = os.environ.get("ESPN_S2")
    if s2:
        return {"espn_s2": s2, "SWID": os.environ.get("ESPN_SWID", "")}
    path = (cache_dir or config.CACHE_DIR) / "espn_cookies.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"espn_s2": "", "SWID": ""}


class EspnClient:
    def __init__(self, cache, cookies: Optional[dict] = None,
                 session: Optional[requests.Session] = None) -> None:
        self.cache = cache
        self.cookies = cookies or {}
        self.session = session or requests.Session()
        self.fetch_count = 0

    def _cookie_header(self) -> str:
        c = self.cookies
        h = f"espn_s2={c.get('espn_s2', '')}"
        if c.get("SWID"):
            h += f"; SWID={c['SWID']}"
        return h

    def league(self, league_id: str, season: str, views=_DEFAULT_VIEWS,
               max_age_hours: Optional[float] = None) -> dict:
        key = f"espn__{season}__{league_id}__{'_'.join(views)}.json"
        if self.cache.fresh(key, max_age_hours):
            return self.cache.get_json(key)
        q = "&".join(f"view={v}" for v in views)
        url = f"{READS_BASE}/seasons/{season}/segments/0/leagues/{league_id}?{q}"
        headers = {"User-Agent": "Mozilla/5.0", "Cookie": self._cookie_header()}
        for attempt in range(4):
            r = self.session.get(url, headers=headers, timeout=30)
            if r.status_code in (401, 403):
                raise RuntimeError(
                    "ESPN auth failed (401/403). Your espn_s2 cookie is missing/expired. "
                    "Re-grab it: log into fantasy.espn.com, F12 -> Application -> Cookies "
                    "-> copy espn_s2 into dynasty_tool/cache/espn_cookies.json."
                )
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
