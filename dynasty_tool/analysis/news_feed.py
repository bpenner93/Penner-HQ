"""Pure feed logic: tag players, collapse duplicates, filter. No network, no
Streamlit — every function here is hermetically testable.

On name matching. ``value/current_provider.py`` documents that this codebase
deliberately *refuses* to join on display name, because names drift. Article text
gives us nothing but names, so here it is unavoidable. The response is to make it
narrow and to report it, per the repo's rule that fuzzy matches are counted and
reported, never silently hidden:

* the candidate pool is your rostered players plus the top-N by value (~500
  names), never Sleeper's ~11,000 — that alone kills most false positives
* a match needs first **and** last name; a bare surname never tags
* suffixes (Jr./Sr./II/III/IV) are indexed both ways
* an ambiguous name is *never guessed* — it tags nothing and increments a counter
* :class:`TagReport` carries the numbers the UI prints
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Iterable, Optional, Sequence

from ..ingest.news_model import NewsItem
from ..ingest.news_parse import canonical_url

# Reused verbatim rather than reimplemented: a second normalizer that drifts from
# the first is a bug factory. Handles Ja'Marr -> jamarr, D.K. -> dk, Amon-Ra.
from ..value.current_provider import _norm_name

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'.\-]*")
_WORD_RE = re.compile(r"[a-z0-9]{4,}")

_STOP = {
    "with", "from", "that", "this", "have", "will", "been", "they", "their",
    "after", "before", "about", "would", "could", "should", "there", "which",
    "were", "than", "them", "what", "when", "into", "over", "more", "said",
    "says", "report", "reports", "source", "sources", "news", "week", "season",
    "game", "games", "team", "teams", "player", "players", "nfl", "football",
}

TEAM_ALIASES: dict[str, tuple[str, ...]] = {
    "ARI": ("cardinals", "arizona"), "ATL": ("falcons", "atlanta"),
    "BAL": ("ravens", "baltimore"), "BUF": ("bills", "buffalo"),
    "CAR": ("panthers", "carolina"), "CHI": ("bears", "chicago"),
    "CIN": ("bengals", "cincinnati"), "CLE": ("browns", "cleveland"),
    "DAL": ("cowboys", "dallas"), "DEN": ("broncos", "denver"),
    "DET": ("lions", "detroit"), "GB": ("packers", "green bay"),
    "HOU": ("texans", "houston"), "IND": ("colts", "indianapolis"),
    "JAX": ("jaguars", "jacksonville"), "KC": ("chiefs", "kansas city"),
    "LAC": ("chargers",), "LAR": ("rams",), "LV": ("raiders", "las vegas"),
    "MIA": ("dolphins", "miami"), "MIN": ("vikings", "minnesota"),
    "NE": ("patriots", "new england"), "NO": ("saints", "new orleans"),
    "NYG": ("giants",), "NYJ": ("jets",), "PHI": ("eagles", "philadelphia"),
    "PIT": ("steelers", "pittsburgh"), "SEA": ("seahawks", "seattle"),
    "SF": ("49ers", "niners", "san francisco"), "TB": ("buccaneers", "bucs", "tampa"),
    "TEN": ("titans", "tennessee"), "WAS": ("commanders", "washington"),
}


@dataclass
class TagReport:
    """The executable form of the repo's "count and report fuzzy matches" rule."""
    items_scanned: int = 0
    items_tagged: int = 0
    ambiguous_skipped: int = 0
    pool_size: int = 0

    def line(self) -> str:
        return (f"{self.items_scanned} items scanned · {self.items_tagged} tagged · "
                f"{self.ambiguous_skipped} ambiguous mentions skipped · "
                f"pool {self.pool_size} players")


# ---------------------------------------------------------------------------
# name index
# ---------------------------------------------------------------------------
def _strip_suffix(tokens: list[str]) -> list[str]:
    return tokens[:-1] if len(tokens) > 2 and tokens[-1] in _SUFFIXES else tokens


def build_name_index(meta: dict, pool: Optional[Iterable[str]] = None,
                     aliases: Optional[dict] = None) -> dict:
    """{pid: meta} -> a normalized-name index over just the pooled players.

    Every variant is indexed: the full name, first+last, and both with a trailing
    suffix removed — so "Marvin Harrison" and "Marvin Harrison Jr." unify no
    matter which side carries the suffix.
    """
    pool_set = {str(p) for p in pool} if pool is not None else None
    by_norm: dict[str, list[str]] = {}
    team_of: dict[str, str] = {}
    slim: dict[str, dict] = {}

    for pid, m in (meta or {}).items():
        pid = str(pid)
        if pool_set is not None and pid not in pool_set:
            continue
        if not isinstance(m, dict):
            continue
        full = str(m.get("full_name") or "").strip()
        first = str(m.get("first_name") or "").strip()
        last = str(m.get("last_name") or "").strip()
        variants: set[str] = set()
        for raw in (full, f"{first} {last}".strip()):
            toks = [_norm_name(t) for t in _TOKEN_RE.findall(raw)]
            toks = [t for t in toks if t]
            if len(toks) < 2:
                continue
            variants.add("".join(toks))
            variants.add("".join(_strip_suffix(toks)))
        for alias in (aliases or {}).get(pid, ()):
            toks = [_norm_name(t) for t in _TOKEN_RE.findall(str(alias))]
            if len(toks) >= 2:
                variants.add("".join(toks))
        if not variants:
            continue
        slim[pid] = m
        team_of[pid] = str(m.get("team") or "").upper()
        for v in variants:
            if pid not in by_norm.setdefault(v, []):
                by_norm[v].append(pid)

    return {"by_norm": {k: tuple(v) for k, v in by_norm.items()},
            "team_of": team_of, "meta": slim}


def _teams_in(text: str) -> set[str]:
    low = text.lower()
    return {abbr for abbr, names in TEAM_ALIASES.items()
            if any(n in low for n in names)}


def tag_text(text: str, index: dict, hint_team: str = "") -> tuple[tuple[str, ...], int]:
    """(player ids, ambiguous count) for one blob of text.

    Scans left to right over token n-grams, longest first, non-overlapping.
    Minimum width is 2 — a bare surname never tags, because one false "Hill" on
    an unrelated story poisons the My Players view, which is the whole feature.
    """
    by_norm = index.get("by_norm") or {}
    if not text or not by_norm:
        return (), 0
    toks = [_norm_name(t) for t in _TOKEN_RE.findall(text)]
    toks = [t for t in toks if t]
    ctx = _teams_in(text)
    if hint_team:
        ctx.add(hint_team.upper())

    hits: list[str] = []
    ambiguous = 0
    i = 0
    while i < len(toks) - 1:
        matched = 0
        for width in (4, 3, 2):
            if i + width > len(toks):
                continue
            pids = by_norm.get("".join(toks[i:i + width]))
            if not pids:
                continue
            if len(pids) > 1:
                narrowed = tuple(p for p in pids
                                 if (index.get("team_of") or {}).get(p) in ctx)
                pids = narrowed or pids
            if len(pids) == 1:
                hits.append(pids[0])
            else:
                ambiguous += 1      # >1 survivor: do not guess
            matched = width
            break
        i += matched if matched else 1
    return tuple(dict.fromkeys(hits)), ambiguous


def tag_items(items: Sequence[NewsItem], index: dict) -> tuple[list[NewsItem], TagReport]:
    rep = TagReport(pool_size=len(index.get("meta") or {}))
    out: list[NewsItem] = []
    for it in items:
        rep.items_scanned += 1
        pids, amb = tag_text(f"{it.title}\n{it.text}", index, hint_team=it.team)
        rep.ambiguous_skipped += amb
        if pids:
            rep.items_tagged += 1
        out.append(it.with_players(pids))
    return out, rep


# ---------------------------------------------------------------------------
# dedupe
# ---------------------------------------------------------------------------
def _tokens(it: NewsItem) -> frozenset[str]:
    words = _WORD_RE.findall(f"{it.title} {it.text}".lower())
    return frozenset([w for w in words if w not in _STOP][:25])


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / float(len(a) + len(b) - inter)


def dedupe(items: Sequence[NewsItem], jaccard: float = 0.75,
           window_ms: int = 6 * 3600 * 1000) -> list[NewsItem]:
    """Collapse the same story arriving from many outlets — never *hide* it.

    Survivors carry ``dupe_count``/``dupe_sources`` so the UI can say "also on 4
    more". Two guards keep genuinely distinct updates apart: the time window, and
    a hard veto when both items have tagged players and those sets are disjoint
    (so "X did not practice" and "Y did not practice" can't merge on boilerplate).

    When a cluster holds both a post and an article, the post wins: the
    reporter's own words are the thing being replaced here, and the write-up of
    it is derivative.
    """
    ordered = sorted(items, key=lambda i: i.published_ms)
    by_url: dict[str, int] = {}
    clusters: list[dict] = []

    for it in ordered:
        cu = canonical_url(it.url)
        idx = by_url.get(cu) if cu else None
        if idx is None:
            toks = _tokens(it)
            for ci, c in enumerate(clusters):
                if abs(it.published_ms - c["ms"]) > window_ms:
                    continue
                if it.player_ids and c["players"] and not (set(it.player_ids) & c["players"]):
                    continue          # different players, same boilerplate
                if _jaccard(toks, c["tokens"]) >= jaccard:
                    idx = ci
                    break
            else:
                idx = None
        if idx is None:
            clusters.append({"items": [it], "ms": it.published_ms,
                             "tokens": _tokens(it), "players": set(it.player_ids)})
            if cu:
                by_url[cu] = len(clusters) - 1
        else:
            clusters[idx]["items"].append(it)
            clusters[idx]["players"] |= set(it.player_ids)

    out: list[NewsItem] = []
    for c in clusters:
        group = c["items"]
        posts = [g for g in group if g.kind == "post"]
        primary = (posts or group)[0]
        others = [g for g in group if g.id != primary.id]
        merged = sorted({p for g in group for p in g.player_ids})
        out.append(replace(
            primary,
            player_ids=tuple(merged),
            dupe_count=len(others),
            dupe_sources=tuple(dict.fromkeys(g.source_label for g in others)),
        ))
    out.sort(key=lambda i: i.published_ms, reverse=True)
    return out


# ---------------------------------------------------------------------------
# filtering / grouping
# ---------------------------------------------------------------------------
def filter_items(items: Sequence[NewsItem], *, query: str = "",
                 teams: Sequence[str] = (), sources: Sequence[str] = (),
                 kinds: Sequence[str] = (), player_ids: Sequence[str] = (),
                 since_ms: int = 0, mute: Sequence[str] = ()) -> list[NewsItem]:
    q = (query or "").strip().lower()
    tset, sset = set(teams or ()), set(sources or ())
    kset, pset = set(kinds or ()), {str(p) for p in (player_ids or ())}
    out = []
    for it in items:
        if since_ms and it.published_ms < since_ms:
            continue
        if tset and it.team not in tset:
            continue
        if sset and it.source_id not in sset:
            continue
        if kset and it.kind not in kset:
            continue
        if pset and not (set(it.player_ids) & pset):
            continue
        blob = f"{it.title} {it.text}".lower()
        if q and q not in blob:
            continue
        if mute and any(m in blob for m in mute):
            continue
        out.append(it)
    return out


def group_by_player(items: Sequence[NewsItem], order: Sequence[str]) -> list[tuple[str, list[NewsItem]]]:
    """[(pid, items)] in the caller's priority order — the My Players view.

    An item about two of your players appears under both; that is intended, since
    you are reading the section player by player.
    """
    buckets: dict[str, list[NewsItem]] = {}
    for it in items:
        for pid in it.player_ids:
            buckets.setdefault(str(pid), []).append(it)
    out = []
    for pid in order:
        got = buckets.get(str(pid))
        if got:
            out.append((str(pid), sorted(got, key=lambda i: i.published_ms, reverse=True)))
    return out
