"""Escaped HTML for feed cards.

This lives in ``analysis/`` rather than ``app.py`` for one reason: ``app.py``
calls ``st.set_page_config`` at module scope and so cannot be imported by a test.
Escaping is the highest-risk logic in this feature and must be unit-tested, so it
has to live somewhere importable. ``analysis/dashboard.py`` sets the precedent of
an analysis module emitting escaped HTML.

Feed text is the first externally-controlled string this app renders. Bluesky
display names, RSS titles and tweet text are all attacker-reachable, and the rest
of ``app.py`` escapes nothing because everything it renders is self-generated.
Do not rely on Streamlit's internal sanitizer — it is undocumented and moves
across the ``streamlit>=1.50`` range this project allows. Escaping is ours.
"""
from __future__ import annotations

import html
from typing import Optional, Sequence
from urllib.parse import urlparse

from ..ingest.news_model import NewsItem
from ..webapp_helpers import POS_COLORS, headshot_url

_OK_SCHEMES = {"http", "https"}
# Hotlinking arbitrary feed images leaks your IP/UA to any host a feed operator
# names, and a 4000px image wrecks the layout.
_IMG_HOSTS = {"cdn.bsky.app", "sleepercdn.com", "a.espncdn.com",
              "pbs.twimg.com", "abs.twimg.com"}


def esc(s) -> str:
    """quote=True is load-bearing: cards interpolate into single-quoted
    attributes, so an unescaped apostrophe in a display name would break out."""
    return html.escape(str(s if s is not None else ""), quote=True)


def safe_url(u, img: bool = False) -> str:
    """http(s) only. Blocks javascript:/data:/vbscript:, protocol-relative
    //evil.com, and the ``java\\nscript:`` control-character bypass that a plain
    ``.strip()`` misses. Anything rejected is omitted entirely."""
    if not u:
        return ""
    s = str(u).strip()
    if not s or s.startswith("//") or any(ord(c) < 32 or ord(c) == 127 for c in s):
        return ""
    try:
        p = urlparse(s)
    except ValueError:
        return ""
    if p.scheme.lower() not in _OK_SCHEMES or not p.netloc:
        return ""
    if img and (p.hostname or "").lower() not in _IMG_HOSTS:
        return ""
    return s


def rel_time(now_ms: int, ms: int) -> str:
    """Relative stamps only — timezone-free, and the texture the user misses."""
    if not ms:
        return ""
    d = max(0, (now_ms - ms) // 1000)
    if d < 60:
        return "now"
    if d < 3600:
        return f"{d // 60}m"
    if d < 86400:
        return f"{d // 3600}h"
    if d < 7 * 86400:
        return f"{d // 86400}d"
    return f"{d // 604800}w"


def _avatar(item: NewsItem) -> str:
    av = safe_url(item.avatar, img=True)
    if av:
        return (f"<img class='nf-av' src='{esc(av)}' alt='' loading='lazy' "
                f"referrerpolicy='no-referrer'>")
    initial = esc((item.author or item.source_label or "?")[:1].upper())
    return f"<div class='nf-av nf-av-i'>{initial}</div>"


def player_chip(pid: str, meta: dict) -> str:
    pos = str((meta or {}).get("position") or "")
    name = (meta or {}).get("full_name") or pid
    color = POS_COLORS.get(pos, "#6b6a64")
    img = safe_url(headshot_url(str(pid)), img=True)
    imghtml = (f"<img src='{esc(img)}' alt='' loading='lazy' "
               f"referrerpolicy='no-referrer'>" if img else "")
    return (f"<span class='nf-chip'>{imghtml}"
            f"<span class='hq-pos' style='background:{color};margin:0'>{esc(pos)}</span>"
            f"{esc(name)}</span>")


def item_card(item: NewsItem, now_ms: int, meta: Optional[dict] = None,
              show_chips: bool = True) -> str:
    href = safe_url(item.url)
    chips = ""
    if show_chips and item.player_ids:
        chips = "".join(player_chip(p, (meta or {}).get(str(p), {}))
                        for p in item.player_ids)
    also = (f"<span class='nf-src'>· also on {item.dupe_count} more</span>"
            if item.dupe_count else "")
    link = (f"<a href='{esc(href)}' target='_blank' rel='noopener noreferrer'>"
            f"open ↗</a>" if href else "")
    handle = (f"<span class='nf-hnd'>{esc(item.author_handle)}</span>"
              if item.author_handle else "")
    icon = "🐦" if item.kind == "post" else "📰"
    title = (f"<div class='nf-title'>{esc(item.title)}</div>"
             if item.title and item.title != item.text else "")
    # A Claude summary is clearly labelled and never replaces the link, so the
    # source is always one click away from any summarised claim.
    summary = (f"<div class='nf-sum'><b>✨ summary</b><br>{esc(item.summary)}</div>"
               if item.summary else "")
    body = esc(item.text)
    return (
        f"<div class='hq-card nf-item'>"
        f"<div class='nf-head'>{_avatar(item)}"
        f"<span><span class='nf-who'>{icon} {esc(item.author)}</span> {handle}</span>"
        f"<span class='nf-when'>{esc(rel_time(now_ms, item.published_ms))}</span></div>"
        f"{title}<div class='nf-body'>{body}</div>{summary}"
        f"<div class='nf-foot'>{chips}"
        f"<span class='nf-src'>{esc(item.source_label)}</span>{also}"
        f"<span style='margin-left:auto'>{link}</span></div>"
        f"</div>")


def feed_html(items: Sequence[NewsItem], now_ms: int,
              meta: Optional[dict] = None, show_chips: bool = True) -> str:
    """The whole list in one string — the caller emits it with a single
    ``st.markdown``. One call per item would add Streamlit's inter-container
    padding between every card and read as a stack of widgets, not a feed."""
    inner = "".join(item_card(i, now_ms, meta, show_chips) for i in items)
    return f"<div class='nf-feed'>{inner}</div>"


NEWS_CSS = """
.nf-feed { display:flex; flex-direction:column; gap:10px; }
.nf-item { padding:12px 14px !important; margin-bottom:0 !important; }
.nf-head { display:flex; align-items:center; gap:9px; margin-bottom:6px; }
.nf-av { width:30px; height:30px; border-radius:50%; object-fit:cover;
         background:#383835; flex:none; }
.nf-av-i { display:flex; align-items:center; justify-content:center;
           font-size:13px; font-weight:800; color:#c3c2b7; }
.nf-who { color:#fff; font-size:13px; font-weight:700; }
.nf-hnd { color:#898781; font-size:11.5px; }
.nf-when { margin-left:auto; color:#898781; font-size:11.5px; white-space:nowrap; }
.nf-title { color:#fff; font-size:14.5px; font-weight:700; line-height:1.35;
            margin:2px 0 4px; }
.nf-body { color:#c3c2b7; font-size:13.5px; line-height:1.5; white-space:pre-wrap; }
.nf-sum { margin-top:8px; padding:8px 10px; border-radius:8px;
          background:rgba(57,135,229,0.10); border:1px solid rgba(57,135,229,0.30);
          color:#c3c2b7; font-size:13px; line-height:1.5; }
.nf-sum b { color:#3987e5; font-size:11px; text-transform:uppercase;
            letter-spacing:.06em; }
.nf-foot { display:flex; align-items:center; gap:6px; margin-top:9px; flex-wrap:wrap; }
.nf-chip { display:inline-flex; align-items:center; gap:5px; background:#232322;
           border:1px solid rgba(255,255,255,0.10); border-radius:999px;
           padding:2px 9px 2px 3px; font-size:11.5px; color:#fff; }
.nf-chip img { width:18px; height:18px; border-radius:50%; background:#383835; }
.nf-src { color:#898781; font-size:11px; text-transform:uppercase; letter-spacing:.06em; }
.nf-pgroup { display:flex; align-items:center; gap:9px; margin:16px 0 6px; }
.nf-pgroup img { width:34px; height:34px; border-radius:50%; background:#383835; }
.nf-pgroup .n { color:#fff; font-size:15px; font-weight:800; }
.nf-pgroup .x { color:#898781; font-size:11.5px; }
.nf-mv { display:flex; align-items:center; gap:8px; padding:7px 2px;
         border-bottom:1px solid #2c2c2a; font-size:13.5px; color:#fff; }
.nf-mv img { width:26px; height:26px; border-radius:50%; background:#383835; }
.nf-mv .d { margin-left:auto; font-weight:800; font-variant-numeric:tabular-nums; }
.nf-up { color:#199e70; } .nf-dn { color:#e66767; }
"""
