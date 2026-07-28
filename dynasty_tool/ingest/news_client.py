"""Disk-cached, parallel, failure-isolated fetching for the beat-reporter feed.

Follows the house pattern in ``sleeper_client.py`` (namespaced cache key,
freshness check first, retry with linear backoff, injectable session, fetch
counter) with four deliberate differences, each for a reason:

===============  ==========  ========  ==========================================
                 Sleeper     News      why
===============  ==========  ========  ==========================================
timeout          30s         8s        a news page must paint fast; one slow feed
                                       cannot own the page
retries          4           2         worst case per source 45s -> 19s
on failure       raises      never     per-source isolation is the whole point
User-Agent       default     explicit  several RSS hosts 403 ``python-requests/x``
===============  ==========  ========  ==========================================

**Threading rule:** worker threads here touch only ``requests``, ``DiskCache``
and the pure parsers. They must never call a ``st.*`` API or a
``@st.cache_data``-decorated function — Streamlit's ScriptRunContext is
thread-local and doing so is unsupported.
"""
from __future__ import annotations

import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Optional

import requests

from .. import config
from ..cache import DiskCache
from .news_model import NewsItem, SourceHealth, SourceSpec
from .news_parse import PARSERS

USER_AGENT = "Penner-HQ/1.0 (+https://github.com/bpenner93/Penner-HQ)"
BSKY_BASE = "https://public.api.bsky.app/xrpc"
TWITTERAPI_BASE = "https://api.twitterapi.io"


def _retry_after(resp) -> float:
    """Seconds from a Retry-After header, 0 when absent or unparseable.

    Honouring the server's own number beats guessing at a backoff curve.
    """
    try:
        return max(0.0, float(str(resp.headers.get("Retry-After", "")).strip()))
    except (TypeError, ValueError, AttributeError):
        return 0.0


def build_from_query(handles: str, exclude_replies: bool = True) -> str:
    """``"a, @b c"`` -> ``"(from:a OR from:b OR from:c) -filter:replies"``.

    Accepts comma or whitespace separation and tolerates a leading ``@`` so the
    registry can be written the way handles are actually quoted.
    """
    parts = [h.strip().lstrip("@") for h in str(handles or "").replace(",", " ").split()]
    parts = [p for p in parts if p]
    if not parts:
        raise RuntimeError("no handles in query")
    q = "(" + " OR ".join(f"from:{p}" for p in parts) + ")"
    return f"{q} -filter:replies" if exclude_replies else q


class NewsClient:
    def __init__(self, cache: DiskCache, session: Optional[requests.Session] = None,
                 timeout: float = 8.0, max_retries: int = 2,
                 twitter_key: str = "") -> None:
        self.cache = cache
        self.session = session or requests.Session()
        self.timeout = timeout
        self.max_retries = max_retries
        self.twitter_key = twitter_key or ""
        self.fetch_count = 0
        # RSS hosts are independent and happily take a parallel burst. A metered
        # API is not: firing all nine X sources at once through the thread pool
        # trips twitterapi.io's per-second limit and every one of them 429s.
        # These serialise X calls behind one lock with a minimum gap.
        self._paced_lock = threading.Lock()
        self._paced_last = 0.0

    PACED_KINDS = ("twitter", "twitter_search")
    # twitterapi.io's free tier documents "one request every 5 seconds" and
    # answers 429 with that exact message. 5.2 leaves a little headroom for
    # clock skew rather than sitting exactly on the limit.
    PACE_SECONDS = 5.2

    def _pace(self) -> None:
        with self._paced_lock:
            gap = time.monotonic() - self._paced_last
            if gap < self.PACE_SECONDS:
                time.sleep(self.PACE_SECONDS - gap)
            self._paced_last = time.monotonic()

    # -- request building ---------------------------------------------------
    def request_for(self, spec: SourceSpec) -> tuple[str, dict, dict]:
        """(url, params, headers) for a spec. Kept separate from fetching so the
        tests can assert on it without a network layer."""
        headers = {"User-Agent": USER_AGENT}
        if spec.kind in ("rss", "gnews"):
            return spec.url, {}, headers
        if spec.kind == "bluesky":
            return (f"{BSKY_BASE}/app.bsky.feed.getAuthorFeed",
                    {"actor": spec.ref, "limit": 40, "filter": "posts_no_replies"},
                    headers)
        if spec.kind in ("twitter", "twitter_search"):
            if not self.twitter_key:
                raise RuntimeError("no twitterapi.io key configured")
            headers["X-API-Key"] = self.twitter_key
            if spec.kind == "twitter":
                return (f"{TWITTERAPI_BASE}/twitter/user/last_tweets",
                        {"userName": spec.ref.lstrip("@")}, headers)
            # One search covering many handles is one billable call instead of
            # one per reporter. With ~100 beat writers that is the difference
            # between roughly $180/mo and $20/mo at typical refresh rates.
            return (f"{TWITTERAPI_BASE}/twitter/tweet/advanced_search",
                    {"query": build_from_query(spec.ref), "queryType": "Latest"},
                    headers)
        raise RuntimeError(f"unknown source kind: {spec.kind}")

    def cache_key(self, spec: SourceSpec) -> str:
        """The URL is hashed into the key so that *fixing a dead URL in
        feeds.json automatically busts the cache* — otherwise you'd correct a
        source and still be served the stale empty body for the whole TTL."""
        # X sources are keyed off the spec directly: request_for raises without a
        # key, and the cache key must stay computable either way.
        if spec.kind in ("twitter", "twitter_search"):
            url, params = f"{spec.kind}:{spec.ref}", {}
        else:
            url, params, _ = self.request_for(spec)
        basis = f"{url}|{sorted(params.items()) if params else ''}"
        h = hashlib.sha1(basis.encode("utf-8", "replace")).hexdigest()[:8]
        return f"news__{spec.id}__{h}.txt"

    # -- core ---------------------------------------------------------------
    def fetch_raw(self, spec: SourceSpec,
                  max_age_hours: Optional[float] = None) -> tuple[str, bool]:
        """(payload, from_cache). Raises on definitive failure; the caller
        converts that into a SourceHealth rather than letting it escape."""
        key = self.cache_key(spec)
        if max_age_hours is None:
            # X costs 5 seconds of wall clock per call on the free tier, so it
            # earns a longer cache than a free RSS host that answers instantly.
            minutes = (config.NEWS_X_MAX_AGE_MINUTES
                       if spec.kind in self.PACED_KINDS
                       else config.NEWS_MAX_AGE_MINUTES)
            max_age_hours = minutes / 60.0
        if self.cache.fresh(key, max_age_hours):
            return self.cache.get_text(key), True

        url, params, headers = self.request_for(spec)
        paced = spec.kind in self.PACED_KINDS
        last: Optional[Exception] = None
        for attempt in range(self.max_retries):
            if paced:
                self._pace()
            try:
                resp = self.session.get(url, params=params or None,
                                        headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                last = exc
                time.sleep(0.75 * (attempt + 1))
                continue
            if resp.status_code == 404:
                # Same trick as the Sleeper client: remember the miss so a dead
                # feed stops costing 8 seconds on every page load.
                self.cache.put_text(key, "")
                return "", False
            if resp.status_code == 429 or resp.status_code >= 500:
                # Carry the body: a metered API uses 429 both for "slow down"
                # and for "you are out of credit", and those need opposite
                # responses from the user. The message says which.
                detail = (resp.text or "").strip()[:160]
                last = RuntimeError(
                    f"HTTP {resp.status_code}" + (f" — {detail}" if detail else ""))
                wait_s = _retry_after(resp) or (1.5 * (attempt + 1))
                time.sleep(min(wait_s, 10.0))
                continue
            resp.raise_for_status()
            text = resp.text or ""
            self.cache.put_text(key, text)
            self.fetch_count += 1
            return text, False
        raise last or RuntimeError(f"fetch failed after {self.max_retries} tries")


def _one(client: NewsClient, spec: SourceSpec) -> tuple[list[NewsItem], SourceHealth]:
    """Fetch + parse a single source. **Never raises** — that guarantee is what
    makes one dead feed unable to blank the page."""
    try:
        payload, cached = client.fetch_raw(spec)
        if not payload.strip():
            return [], SourceHealth(spec.id, spec.label, ok=False,
                                    error="empty response", from_cache=cached,
                                    kind=spec.kind, team=spec.team)
        items = PARSERS[spec.kind](payload, spec)
        if not items:
            # A source that fetched fine but yielded nothing is not healthy — it
            # is the exact shape of a wrong handle, a dead feed, or an API that
            # answered 200 with an error body. Reporting it green is how a
            # broken integration hides behind a full-looking page.
            return [], SourceHealth(
                spec.id, spec.label, ok=False,
                error=f"fetched {len(payload):,} bytes but parsed 0 items — "
                      f"starts: {payload.strip()[:120]!r}",
                from_cache=cached, kind=spec.kind, team=spec.team)
        return items, SourceHealth(spec.id, spec.label, ok=True, n_items=len(items),
                                   from_cache=cached, kind=spec.kind, team=spec.team)
    except Exception as exc:
        return [], SourceHealth(spec.id, spec.label, ok=False,
                                error=f"{type(exc).__name__}: {exc}"[:200],
                                kind=spec.kind, team=spec.team)


def fetch_all(client: NewsClient, specs: list[SourceSpec],
              max_workers: int = 10,
              deadline_s: float = 75.0) -> tuple[list[NewsItem], list[SourceHealth]]:
    """Every source in parallel, newest first. Sources that blow the deadline are
    reported as failures rather than being allowed to hold the page."""
    items: list[NewsItem] = []
    health: list[SourceHealth] = []
    if not specs:
        return items, health

    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(specs)))) as ex:
        futs = {ex.submit(_one, client, s): s for s in specs}
        done, pending = wait(futs, timeout=deadline_s)
        for f in done:
            try:
                its, h = f.result()
            except Exception as exc:  # defensive; _one already swallows
                s = futs[f]
                its, h = [], SourceHealth(s.id, s.label, ok=False,
                                          error=str(exc)[:200], kind=s.kind)
            items.extend(its)
            health.append(h)
        for f in pending:
            s = futs[f]
            f.cancel()
            health.append(SourceHealth(s.id, s.label, ok=False,
                                       error=f"timed out after {deadline_s:g}s",
                                       kind=s.kind, team=s.team))

    items.sort(key=lambda i: i.published_ms, reverse=True)
    health.sort(key=lambda h: (h.ok, h.label))
    return items, health
