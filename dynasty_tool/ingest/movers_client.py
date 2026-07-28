"""Fetching for the Movers page: historical DynastyProcess snapshots and the
KeepTradeCut board.

**DynastyProcess history.** The values CSV lives in a public git repo that is
committed weekly, so history is already there — no snapshot storage of our own
is needed (which matters, because Streamlit Cloud's disk is ephemeral and would
lose any history we tried to accumulate). The GitHub commits API gives the SHA
that was current before a target date; ``raw.githubusercontent.com`` then serves
the file at that exact SHA. Verified working: SHA-pinned raw fetches resolve, and
``raw.githubusercontent.com`` is reachable even from the restricted dev sandbox.

**KeepTradeCut.** No public API. The rankings page embeds its board as a
JavaScript array literal, which is extracted here. That is unofficial and can
break without notice, so every failure is reported rather than swallowed, and the
page degrades to DynastyProcess-only rather than going blank.
"""
from __future__ import annotations

import csv
import io
import json
import re
from typing import Any, Optional

import requests

from .. import config
from ..cache import DiskCache

GH_API = "https://api.github.com/repos/dynastyprocess/data/commits"
DP_RAW = "https://raw.githubusercontent.com/dynastyprocess/data"
DP_PATH = "files/values-players.csv"
PICKS_PATH = "files/values-picks.csv"
KTC_URL = "https://keeptradecut.com/dynasty-rankings"
UA = "Penner-HQ/1.0 (+https://github.com/bpenner93/Penner-HQ)"


def _get(session: requests.Session, url: str, timeout: float = 20.0, **kw):
    return session.get(url, headers={"User-Agent": UA}, timeout=timeout, **kw)


def commit_before(iso_date: str, cache: DiskCache,
                  session: Optional[requests.Session] = None,
                  path: str = DP_PATH) -> str:
    """The SHA of the last commit touching ``path`` at or before ``iso_date``.

    Cached hard (7 days): history is immutable, so re-asking costs a request
    against GitHub's 60/hour unauthenticated limit for an answer that cannot
    change.
    """
    key = f"dpsha__{path.replace('/', '_')}__{iso_date}.json"
    if cache.fresh(key, 24 * 7):
        try:
            return str(cache.get_json(key) or "")
        except Exception:
            pass
    sess = session or requests.Session()
    r = _get(sess, GH_API, params={"path": path, "until": iso_date, "per_page": 1})
    if r.status_code != 200:
        raise RuntimeError(f"GitHub commits API {r.status_code}: {r.text[:120]}")
    rows = r.json() or []
    sha = str(rows[0].get("sha") or "") if rows else ""
    cache.put_json(key, sha)
    return sha


def values_at(sha: str, cache: DiskCache, session: Optional[requests.Session] = None,
              path: str = DP_PATH) -> list[dict]:
    """The values CSV as of one commit. Immutable, so cached indefinitely."""
    if not sha:
        return []
    key = f"dpvals__{path.replace('/', '_')}__{sha[:12]}.csv"
    if cache.fresh(key, None):
        return list(csv.DictReader(io.StringIO(cache.get_text(key))))
    sess = session or requests.Session()
    r = _get(sess, f"{DP_RAW}/{sha}/{path}", timeout=40)
    r.raise_for_status()
    cache.put_text(key, r.text)
    return list(csv.DictReader(io.StringIO(r.text)))


def values_now(cache: DiskCache, session: Optional[requests.Session] = None,
               path: str = DP_PATH) -> list[dict]:
    key = f"dpvals__{path.replace('/', '_')}__HEAD.csv"
    if cache.fresh(key, config.VALUES_MAX_AGE_HOURS):
        return list(csv.DictReader(io.StringIO(cache.get_text(key))))
    sess = session or requests.Session()
    r = _get(sess, f"{DP_RAW}/master/{path}", timeout=40)
    r.raise_for_status()
    cache.put_text(key, r.text)
    return list(csv.DictReader(io.StringIO(r.text)))


# ---------------------------------------------------------------------------
# KeepTradeCut
# ---------------------------------------------------------------------------
# The board is emitted as `var playersArray = [ ... ];`. Non-greedy up to the
# terminating `];` at a line end, so a `];` inside a string can't truncate it.
_KTC_RE = re.compile(r"var\s+playersArray\s*=\s*(\[.*?\])\s*;\s*$",
                     re.DOTALL | re.MULTILINE)


def parse_ktc(html_text: str) -> list[dict]:
    """Extract KTC's embedded board. Returns [] when the page shape changes —
    the caller reports that as a source failure rather than showing nothing."""
    if not html_text:
        return []
    m = _KTC_RE.search(html_text)
    if not m:
        # Fallback: some builds ship it without the trailing semicolon-at-EOL.
        m = re.search(r"playersArray\s*=\s*(\[.*?\])\s*[;\n]", html_text, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except (ValueError, TypeError):
        return []
    return [d for d in data if isinstance(d, dict)]


def fetch_ktc(cache: DiskCache, session: Optional[requests.Session] = None,
              max_age_hours: float = 6.0) -> list[dict]:
    key = "ktc__board.json"
    if cache.fresh(key, max_age_hours):
        try:
            return list(cache.get_json(key) or [])
        except Exception:
            pass
    sess = session or requests.Session()
    r = _get(sess, KTC_URL, timeout=25)
    r.raise_for_status()
    players = parse_ktc(r.text)
    if not players:
        raise RuntimeError("KTC board not found in page — the page shape changed")
    cache.put_json(key, players)
    return players
