"""nflverse data for the rookie model: draft picks and combine results.

Both are published as release assets on GitHub, which is the one host reachable
even from the restricted dev sandbox — so unlike the news feeds, this path was
verified end to end while building it.

The join that makes the whole model possible is ``cfb_player_id`` on draft picks
matching ``cfb_id`` on combine (and CollegeFootballData's own player ids). 93% of
skill players drafted since 2010 carry it, so college production, draft capital
and athletic testing line up by **id, not by name** — which is exactly the
discipline ``value/current_provider.py`` documents for the rest of this codebase.
"""
from __future__ import annotations

import csv
import gzip
import io
from typing import Optional

import requests

from ..cache import DiskCache

RELEASES = "https://github.com/nflverse/nflverse-data/releases/download"
UA = "Penner-HQ/1.0 (+https://github.com/bpenner93/Penner-HQ)"

DRAFT_URL = f"{RELEASES}/draft_picks/draft_picks.csv"
COMBINE_URL = f"{RELEASES}/combine/combine.csv"
NGS_REC_URL = f"{RELEASES}/nextgen_stats/ngs_receiving.csv.gz"


def _fetch_csv(url: str, cache: DiskCache, key: str, max_age_hours: float,
               session: Optional[requests.Session] = None,
               gzipped: bool = False) -> list[dict]:
    if cache.fresh(key, max_age_hours):
        try:
            return list(csv.DictReader(io.StringIO(cache.get_text(key))))
        except Exception:
            pass
    sess = session or requests.Session()
    r = sess.get(url, headers={"User-Agent": UA}, timeout=90, allow_redirects=True)
    r.raise_for_status()
    text = (gzip.decompress(r.content).decode("utf-8", "replace")
            if gzipped else r.text)
    cache.put_text(key, text)
    return list(csv.DictReader(io.StringIO(text)))


def draft_picks(cache: DiskCache, session: Optional[requests.Session] = None) -> list[dict]:
    """Every NFL draft pick with career production. History is immutable apart
    from the newest class, so a week is a safe TTL."""
    return _fetch_csv(DRAFT_URL, cache, "nflverse__draft_picks.csv", 24 * 7, session)


def combine(cache: DiskCache, session: Optional[requests.Session] = None) -> list[dict]:
    return _fetch_csv(COMBINE_URL, cache, "nflverse__combine.csv", 24 * 7, session)


def ngs_receiving(cache: DiskCache,
                  session: Optional[requests.Session] = None) -> list[dict]:
    """Next Gen Stats receiving — average separation, cushion, YAC over expected.

    The closest public stand-in for a route-running grade: separation is
    literally how open a receiver gets. Joins on ``player_gsis_id``. Updated
    in-season, so a shorter TTL than the historical files.
    """
    return _fetch_csv(NGS_REC_URL, cache, "nflverse__ngs_receiving.csv", 12,
                      session, gzipped=True)
