"""Shapes for the beat-reporter feed: one item type, one source spec, one health
record — plus the loader for the ``feeds.json`` registry.

This module is deliberately the leaf of the news import graph (mirroring
``ingest/context.py``, which owns ``LeagueContext``): parse, client, analysis and
render all import from here, so nothing else needs to import *them*.

Two design choices worth stating, because both prevent a class of bug:

* ``published_ms`` is an int of epoch milliseconds, not a ``datetime``. It matches
  the wire convention already used in this repo (``SAME_DAY_MS`` in config.py,
  Sleeper's ``status_updated``), it round-trips through the JSON disk cache with
  no custom serializer, and it makes a naive-vs-aware ``TypeError`` impossible
  when sorting items that came from four different sources. ``published_at``
  gives you a tz-aware UTC datetime where you actually want one.
* Every field is ``str``/``int``/``tuple`` and the dataclass lives *in the
  package*, not in ``app.py``. ``app.py`` runs under ``run_name="__main__"``, so
  a class defined there pickles as ``__main__.NewsItem`` and would not survive
  ``st.cache_data`` reliably.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

PKG_ROOT = Path(__file__).resolve().parent
PACKAGED_FEEDS = PKG_ROOT / "feeds.json"

# Source ids become disk-cache filenames. DiskCache._safe_key collapses anything
# outside [A-Za-z0-9._-] to "_", so "a:b" and "a_b" would silently share a file.
# Validating ids as slugs up front makes that collision impossible.
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

KNOWN_KINDS = {"rss", "gnews", "bluesky", "twitter", "twitter_search"}


@dataclass(frozen=True)
class SourceSpec:
    """One fetchable source. ``ref`` means whatever ``kind`` says it means:
    a feed URL, a Google News query, a Bluesky actor, or an X handle."""
    id: str
    kind: str
    label: str
    ref: str
    team: str = ""          # "" for national sources
    outlet: str = ""
    enabled: bool = True

    @property
    def url(self) -> str:
        """The URL this spec resolves to (empty for kinds the client builds)."""
        if self.kind == "rss":
            return self.ref
        if self.kind == "gnews":
            from urllib.parse import quote_plus
            return ("https://news.google.com/rss/search?q="
                    f"{quote_plus(self.ref)}&hl=en-US&gl=US&ceid=US%3Aen")
        return ""


@dataclass(frozen=True)
class SourceHealth:
    """Why a source did or didn't produce items. Rendered in the UI so that a
    short feed can never be confused with a healthy one."""
    id: str
    label: str
    ok: bool
    n_items: int = 0
    error: str = ""
    from_cache: bool = False
    kind: str = ""
    team: str = ""


@dataclass(frozen=True)
class NewsItem:
    id: str
    source_id: str
    source_label: str
    kind: str                # "post" (tweet/skeet) | "article"
    author: str = ""
    author_handle: str = ""
    avatar: str = ""
    team: str = ""           # team beat this source covers ("" = national)
    title: str = ""
    text: str = ""           # PLAIN TEXT by contract — tags stripped at parse
    url: str = ""
    published_ms: int = 0
    body: str = ""           # fetched article text, "" until lazily filled
    summary: str = ""        # Claude summary, "" until requested
    player_ids: tuple[str, ...] = ()
    dupe_count: int = 0
    dupe_sources: tuple[str, ...] = ()

    @property
    def published_at(self) -> datetime:
        return datetime.fromtimestamp(self.published_ms / 1000, tz=timezone.utc)

    def with_players(self, pids: Iterable[str]) -> "NewsItem":
        return replace(self, player_ids=tuple(pids))

    # -- JSON disk layer ----------------------------------------------------
    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["player_ids"] = list(self.player_ids)
        d["dupe_sources"] = list(self.dupe_sources)
        return d

    @staticmethod
    def from_dict(d: dict) -> "NewsItem":
        d = dict(d)
        d["player_ids"] = tuple(d.get("player_ids") or ())
        d["dupe_sources"] = tuple(d.get("dupe_sources") or ())
        known = NewsItem.__dataclass_fields__.keys()
        return NewsItem(**{k: v for k, v in d.items() if k in known})


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------
def _spec_from(raw: dict, team: str, seen: set[str]) -> Optional[SourceSpec]:
    """One registry row -> SourceSpec, or None (with the reason dropped) if the
    row is malformed. Never raises: a typo in feeds.json must not blank the page."""
    if not isinstance(raw, dict):
        return None
    sid = str(raw.get("id") or "").strip().lower()
    kind = str(raw.get("type") or "").strip().lower()
    if not _ID_RE.match(sid) or sid in seen or kind not in KNOWN_KINDS:
        return None
    ref = str(raw.get("url") or raw.get("query") or raw.get("actor")
              or raw.get("handle") or "").strip()
    if not ref:
        return None
    if kind == "rss" and not ref.lower().startswith(("http://", "https://")):
        return None
    seen.add(sid)
    return SourceSpec(id=sid, kind=kind, label=str(raw.get("name") or sid),
                      ref=ref, team=team, outlet=str(raw.get("outlet") or ""),
                      enabled=bool(raw.get("enabled", True)))


def load_registry(path: Optional[Path] = None) -> dict:
    """Raw registry dict. Mirrors ``wh.load_extra_leagues``: never raises, falls
    back to the packaged file, and finally to an empty registry."""
    for p in [p for p in (path, PACKAGED_FEEDS) if p]:
        try:
            if Path(p).exists():
                return json.loads(Path(p).read_text(encoding="utf-8"))
        except Exception:
            continue
    return {"national": [], "teams": {}}


def load_sources(path: Optional[Path] = None,
                 registry: Optional[dict] = None) -> list[SourceSpec]:
    """Every enabled, well-formed source in the registry.

    Malformed rows are dropped silently *here* but the caller compares
    ``len(load_sources())`` against the raw row count to report the difference,
    so nothing vanishes without a number moving.
    """
    reg = registry if registry is not None else load_registry(path)
    seen: set[str] = set()
    out: list[SourceSpec] = []
    for raw in reg.get("national") or []:
        s = _spec_from(raw, "", seen)
        if s and s.enabled:
            out.append(s)
    for team, block in (reg.get("teams") or {}).items():
        rows = (block or {}).get("sources") if isinstance(block, dict) else block
        for raw in rows or []:
            s = _spec_from(raw, str(team).upper(), seen)
            if s and s.enabled:
                out.append(s)
    return out


def team_names(registry: Optional[dict] = None) -> dict[str, str]:
    """{'ATL': 'Atlanta Falcons', ...} for the team filter."""
    reg = registry if registry is not None else load_registry()
    out = {}
    for team, block in (reg.get("teams") or {}).items():
        if isinstance(block, dict) and block.get("name"):
            out[str(team).upper()] = str(block["name"])
    return out


def handle_teams(registry: Optional[dict] = None) -> dict[str, str]:
    """{lowercased X handle: NFL team}.

    Batching beat writers by division is what keeps the pull count at 8 instead
    of 32, but a batched source can't carry one team on the spec. This map
    restores per-item team attribution from the author's handle, so the By Team
    tab keeps working. Lowercased because X handles are case-insensitive.
    """
    reg = registry if registry is not None else load_registry()
    return {str(h).lstrip("@").lower(): str(t).upper()
            for h, t in (reg.get("x_handles") or {}).items() if h and t}


def mute_terms(registry: Optional[dict] = None) -> tuple[str, ...]:
    """Lowercased substrings that drop an item. NFL feeds are infested with
    sportsbook promos; this kills them without a code change."""
    reg = registry if registry is not None else load_registry()
    return tuple(str(t).lower() for t in (reg.get("mute") or []) if str(t).strip())
