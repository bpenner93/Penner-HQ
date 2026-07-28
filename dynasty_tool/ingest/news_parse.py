"""Pure parsers: raw payload text -> list[NewsItem]. No network, no Streamlit.

Contract for every parser here: ``NewsItem.text`` and ``.title`` come out as
**plain text** — tags stripped, entities decoded exactly once. Escaping happens
later, at render. Getting that order backwards yields either ``&amp;amp;`` on
screen or an XSS hole, so there is a test pinning it.
"""
from __future__ import annotations

import calendar
import hashlib
import html as _html
import json
import re
from datetime import datetime, timezone
from typing import Any, Optional

from .news_model import NewsItem, SourceSpec

# feedparser is soft-imported: this repo keeps two requirements files that must
# stay in sync, and app.py is executed via runpy, so a hard top-level import that
# fails would take down all 11 existing pages — not just the feed. As a soft
# import, a desync degrades to "RSS sources failed" in the health panel.
try:
    import feedparser as _fp
except Exception:  # pragma: no cover - exercised only when the dep is missing
    _fp = None

MAX_TEXT = 1200
MAX_TITLE = 300
MAX_AUTHOR = 80

_BLOCK_RE = re.compile(r"(?i)</?(?:br|p|div|li|tr|h[1-6])\b[^>]*>")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\xa0]+")
_NL_RE = re.compile(r"\n{3,}")


def strip_html(raw: Any, limit: int = MAX_TEXT) -> str:
    """HTML fragment -> plain text. Block tags become newlines so a multi-paragraph
    RSS description keeps its shape; everything else is dropped, then entities are
    decoded exactly once."""
    if not raw:
        return ""
    s = _BLOCK_RE.sub("\n", str(raw))
    s = _TAG_RE.sub("", s)
    s = _html.unescape(s)
    s = _WS_RE.sub(" ", s)
    s = _NL_RE.sub("\n\n", s)
    s = "\n".join(line.strip() for line in s.split("\n")).strip()
    return s[:limit].rstrip()


def _iso_to_ms(value: Any) -> int:
    """ISO-8601 (incl. trailing Z) -> epoch ms UTC. 0 when unparseable."""
    if not value:
        return 0
    s = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def item_id(source_id: str, url: str, text: str) -> str:
    """Stable id. Unstable ids would break dedupe, paging and summary caching, so
    this hashes only fields that do not drift between fetches."""
    basis = f"{source_id}|{canonical_url(url)}|{text[:120]}"
    return hashlib.sha1(basis.encode("utf-8", "replace")).hexdigest()[:16]


def canonical_url(u: Any) -> str:
    """Lowercase host, drop www/query/fragment/trailing slash. Used both for the
    item id and as the strongest dedupe key."""
    if not u:
        return ""
    from urllib.parse import urlparse
    try:
        p = urlparse(str(u).strip())
    except ValueError:
        return ""
    if not p.scheme or not p.netloc:
        return ""
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return f"{host}{p.path.rstrip('/')}"


# ---------------------------------------------------------------------------
# RSS / Atom / Google News
# ---------------------------------------------------------------------------
def parse_rss(payload: str, spec: SourceSpec, limit: int = 40) -> list[NewsItem]:
    if _fp is None:
        raise RuntimeError("feedparser is not installed — RSS sources disabled")
    parsed = _fp.parse(payload)
    out: list[NewsItem] = []
    for e in (parsed.entries or [])[:limit]:
        title = strip_html(e.get("title"), MAX_TITLE)
        # Atom carries both: <content> is the full body, <summary> the teaser.
        # Prefer the fuller text — a feed reader wants the substance, and the
        # extra words materially improve player tagging and dedupe.
        content = ""
        raw_content = e.get("content")
        if isinstance(raw_content, (list, tuple)) and raw_content:
            first = raw_content[0]
            content = first.get("value", "") if isinstance(first, dict) else str(first)
        summary = strip_html(content or e.get("summary") or e.get("description"))
        link = str(e.get("link") or "")

        # feedparser normalises every date dialect to a UTC struct_time.
        # calendar.timegm is the correct inverse; time.mktime would silently
        # apply the SERVER's local offset and shift every timestamp.
        ms = 0
        for key in ("published_parsed", "updated_parsed"):
            st = e.get(key)
            if st:
                try:
                    ms = int(calendar.timegm(st) * 1000)
                    break
                except (TypeError, ValueError):
                    pass
        if not ms:
            ms = _iso_to_ms(e.get("published") or e.get("updated"))

        author = strip_html(e.get("author") or "", MAX_AUTHOR)
        # Google News titles arrive as "Headline - Outlet"; the outlet is the
        # useful byline and repeating it in the title is noise.
        if spec.kind == "gnews" and " - " in title:
            head, _, tail = title.rpartition(" - ")
            if head and len(tail) < 60:
                title, author = head, (author or tail)

        out.append(NewsItem(
            id=item_id(spec.id, link, title or summary),
            source_id=spec.id, source_label=spec.label, kind="article",
            author=author or spec.label, team=spec.team,
            title=title, text=summary, url=link, published_ms=ms,
        ))
    return out


# ---------------------------------------------------------------------------
# Bluesky — public AppView, no auth
# ---------------------------------------------------------------------------
def parse_bluesky(payload: str, spec: SourceSpec, limit: int = 40,
                  include_reposts: bool = False) -> list[NewsItem]:
    data = json.loads(payload) if isinstance(payload, str) else (payload or {})
    out: list[NewsItem] = []
    for row in (data.get("feed") or [])[:limit * 2]:
        if len(out) >= limit:
            break
        if not include_reposts and (row.get("reason") or {}).get("$type", "").endswith("reasonRepost"):
            continue
        post = row.get("post") or {}
        rec = post.get("record") or {}
        if rec.get("reply"):          # replies are conversation, not reporting
            continue
        text = strip_html(rec.get("text"))
        if not text:
            continue
        a = post.get("author") or {}
        handle = str(a.get("handle") or "")
        uri = str(post.get("uri") or "")
        # at://did:plc:xxx/app.bsky.feed.post/RKEY -> the public permalink
        rkey = uri.rsplit("/", 1)[-1] if uri else ""
        url = f"https://bsky.app/profile/{handle}/post/{rkey}" if handle and rkey else ""
        out.append(NewsItem(
            id=item_id(spec.id, uri or url, text),
            source_id=spec.id, source_label=spec.label, kind="post",
            author=strip_html(a.get("displayName") or handle, MAX_AUTHOR),
            author_handle=f"@{handle}" if handle else "",
            avatar=str(a.get("avatar") or ""), team=spec.team,
            text=text, url=url, published_ms=_iso_to_ms(rec.get("createdAt")),
        ))
    return out


# ---------------------------------------------------------------------------
# twitterapi.io — third-party X proxy
# ---------------------------------------------------------------------------
def parse_twitterapi(payload: str, spec: SourceSpec, limit: int = 40) -> list[NewsItem]:
    """Tolerant of the two envelope shapes this API has shipped: a bare
    ``{"tweets": [...]}`` and a nested ``{"data": {"tweets": [...]}}``."""
    data = json.loads(payload) if isinstance(payload, str) else (payload or {})
    rows = data.get("tweets")
    if rows is None and isinstance(data.get("data"), dict):
        rows = data["data"].get("tweets")
    if rows is None:
        rows = data.get("data") if isinstance(data.get("data"), list) else []

    out: list[NewsItem] = []
    for t in (rows or [])[:limit]:
        if not isinstance(t, dict):
            continue
        text = strip_html(t.get("text") or t.get("full_text"))
        if not text:
            continue
        a = t.get("author") or t.get("user") or {}
        handle = str(a.get("userName") or a.get("screen_name") or spec.ref or "").lstrip("@")
        tid = str(t.get("id") or t.get("id_str") or "")
        url = str(t.get("url") or "") or (
            f"https://x.com/{handle}/status/{tid}" if handle and tid else "")
        ms = _iso_to_ms(t.get("createdAt") or t.get("created_at"))
        if not ms:
            ms = _twitter_time_to_ms(t.get("createdAt") or t.get("created_at"))
        out.append(NewsItem(
            id=item_id(spec.id, url or tid, text),
            source_id=spec.id, source_label=spec.label, kind="post",
            author=strip_html(a.get("name") or spec.label, MAX_AUTHOR),
            author_handle=f"@{handle}" if handle else "",
            avatar=str(a.get("profilePicture") or a.get("profile_image_url_https") or ""),
            team=spec.team, text=text, url=url, published_ms=ms,
        ))
    return out


_TW_FMT = "%a %b %d %H:%M:%S %z %Y"   # "Wed Jul 23 14:02:11 +0000 2026"


def _twitter_time_to_ms(value: Any) -> int:
    if not value:
        return 0
    try:
        return int(datetime.strptime(str(value), _TW_FMT).timestamp() * 1000)
    except (ValueError, TypeError):
        return 0


PARSERS = {
    "rss": parse_rss,
    "gnews": parse_rss,
    "bluesky": parse_bluesky,
    "twitter": parse_twitterapi,
}
