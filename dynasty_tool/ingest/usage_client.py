"""Fetching for route participation and snap share.

Both files come from nflverse's GitHub releases, which is the one host reachable
even from the restricted dev sandbox — so unlike the news feeds, this path was
verified end to end while building it.

Two decisions worth stating:

* **Participation is read as parquet, not CSV.** The same season is 4.4 MB as
  parquet against 48 MB as CSV — an 11x saving that matters a lot on Streamlit
  Cloud, where the disk is ephemeral and every cold container re-downloads.
  Only the handful of columns actually needed are read.
* **The raw file is never cached.** It is reduced to a small per-player summary
  (a few hundred KB) and thrown away. Caching 48 MB per season to compute a
  ~200 KB table would be the wrong trade on a disk that keeps disappearing.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
from typing import Optional

import requests

from ..cache import DiskCache

RELEASES = "https://github.com/nflverse/nflverse-data/releases/download"
UA = "Penner-HQ/1.0 (+https://github.com/bpenner93/Penner-HQ)"

# Only what aggregate_participation and player_team actually read. Selecting
# columns at the parquet layer keeps a 46k-row season in a few MB of memory.
PARTICIPATION_COLS = ["possession_team", "offense_players", "time_to_throw",
                      "route", "defense_coverage_type"]


def snap_counts(year: int, cache: DiskCache,
                session: Optional[requests.Session] = None) -> list[dict]:
    """PFR snap counts for one season (~2.3 MB gzipped). Small enough to cache
    whole, and it carries ``offense_pct`` so snap share needs no computation."""
    key = f"nflverse__snaps__{int(year)}.csv"
    if cache.fresh(key, 12):
        return list(csv.DictReader(io.StringIO(cache.get_text(key))))
    sess = session or requests.Session()
    r = sess.get(f"{RELEASES}/snap_counts/snap_counts_{int(year)}.csv.gz",
                 headers={"User-Agent": UA}, timeout=90, allow_redirects=True)
    r.raise_for_status()
    text = gzip.decompress(r.content).decode("utf-8", "replace")
    cache.put_text(key, text)
    return list(csv.DictReader(io.StringIO(text)))


def participation_summary(year: int, cache: DiskCache,
                          session: Optional[requests.Session] = None) -> dict:
    """{'routes': {gsis: n}, 'team_dropbacks': {team: n}, 'teams': {gsis: team}}.

    Downloads the season's participation parquet, reduces it, and keeps only the
    summary. Cached 12h: the file only changes as games are played.
    """
    key = f"nflverse__routes__{int(year)}.json"
    if cache.fresh(key, 12):
        try:
            return cache.get_json(key)
        except Exception:
            pass

    import pandas as pd
    from ..analysis.usage import aggregate_participation, player_team

    url = f"{RELEASES}/pbp_participation/pbp_participation_{int(year)}.parquet"
    sess = session or requests.Session()
    r = sess.get(url, headers={"User-Agent": UA}, timeout=180, allow_redirects=True)
    r.raise_for_status()

    try:
        df = pd.read_parquet(io.BytesIO(r.content), columns=PARTICIPATION_COLS)
    except (ValueError, KeyError):
        # A schema change upstream shouldn't hard-fail the page; take whatever
        # columns exist and let the classifier work with what it has.
        df = pd.read_parquet(io.BytesIO(r.content))
    rows = df.to_dict("records")

    routes, team_db = aggregate_participation(rows)
    teams = player_team(rows)
    out = {"routes": routes, "team_dropbacks": team_db, "teams": teams,
           "season": int(year), "plays": len(rows)}
    cache.put_json(key, out)
    return out


def season_targets(year: int, cache: DiskCache,
                   session: Optional[requests.Session] = None) -> dict[str, int]:
    """{gsis_id: targets} for the season, for the TPRR denominator's numerator.

    Uses nflverse's per-season player stats release. Returns {} rather than
    raising if the asset name has moved — TPRR then simply doesn't render, and
    routes and snap share still do.
    """
    key = f"nflverse__targets__{int(year)}.json"
    if cache.fresh(key, 12):
        try:
            return {str(k): int(v) for k, v in (cache.get_json(key) or {}).items()}
        except Exception:
            pass
    sess = session or requests.Session()
    out: dict[str, int] = {}
    for asset in (f"stats_player/stats_player_reg_{int(year)}.csv.gz",
                  f"player_stats/player_stats_{int(year)}.csv.gz"):
        try:
            r = sess.get(f"{RELEASES}/{asset}", headers={"User-Agent": UA},
                         timeout=120, allow_redirects=True)
            if r.status_code != 200:
                continue
            text = gzip.decompress(r.content).decode("utf-8", "replace")
            for row in csv.DictReader(io.StringIO(text)):
                pid = str(row.get("player_id") or row.get("gsis_id") or "").strip()
                if not pid:
                    continue
                try:
                    out[pid] = out.get(pid, 0) + int(float(row.get("targets") or 0))
                except (TypeError, ValueError):
                    continue
            if out:
                break
        except requests.RequestException:
            continue
    cache.put_json(key, out)
    return out
