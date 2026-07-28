"""Optional, on-demand Claude summaries + lazy article-body extraction.

Nothing here runs unless the user clicks. The feed is fully functional with no
API key — this only lights up when ``ANTHROPIC_API_KEY`` is present, which keeps
the project's "no API key is required" promise intact.

Deliberately uses plain ``requests`` against the Messages API rather than the
``anthropic`` SDK: ``requests`` is already a dependency, the call is a dozen
lines, and every dependency added here is another way the Streamlit Cloud build
can fail.

Article bodies are extracted with the stdlib ``html.parser`` rather than
BeautifulSoup, for the same reason. Mildly noisy text is fine — the model
tolerates it, and paywalled or bot-blocked pages simply fall back to the RSS
blurb rather than failing.
"""
from __future__ import annotations

import hashlib
import json
from html.parser import HTMLParser
from typing import Iterable, Optional, Sequence

import requests

from .. import config
from ..cache import DiskCache

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
UA = "Penner-HQ/1.0 (+https://github.com/bpenner93/Penner-HQ)"

# Content-bearing containers, in preference order.
_MAIN_TAGS = ("article", "main")
_DROP_TAGS = {"script", "style", "nav", "footer", "aside", "form", "noscript",
              "figure", "figcaption", "header"}


class _ArticleText(HTMLParser):
    """Collects <p> text, preferring what's inside <article>/<main> when present."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._drop = 0
        self._depth_main = 0
        self._in_p = False
        self._buf: list[str] = []
        self.all_paras: list[str] = []
        self.main_paras: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _DROP_TAGS:
            self._drop += 1
        elif tag in _MAIN_TAGS:
            self._depth_main += 1
        elif tag == "p" and not self._drop:
            self._in_p, self._buf = True, []

    def handle_endtag(self, tag):
        if tag in _DROP_TAGS:
            self._drop = max(0, self._drop - 1)
        elif tag in _MAIN_TAGS:
            self._depth_main = max(0, self._depth_main - 1)
        elif tag == "p" and self._in_p:
            text = " ".join("".join(self._buf).split())
            self._in_p, self._buf = False, []
            if len(text) >= 40:          # skip captions, bylines, promo lines
                self.all_paras.append(text)
                if self._depth_main:
                    self.main_paras.append(text)

    def handle_data(self, data):
        if self._in_p and not self._drop:
            self._buf.append(data)


def extract_article_text(html_text: str, limit: int = 6000) -> str:
    if not html_text:
        return ""
    p = _ArticleText()
    try:
        p.feed(html_text)
    except Exception:
        return ""
    paras = p.main_paras or p.all_paras
    return "\n\n".join(paras)[:limit].strip()


def fetch_article(url: str, cache: DiskCache, session: Optional[requests.Session] = None,
                  timeout: float = 12.0) -> str:
    """Article body text, disk-cached. Returns "" on paywall/block/failure — the
    caller falls back to the feed blurb rather than surfacing an error."""
    if not url:
        return ""
    key = f"article__{hashlib.sha1(url.encode('utf-8', 'replace')).hexdigest()[:16]}.txt"
    if cache.fresh(key, config.ARTICLE_MAX_AGE_HOURS):
        return cache.get_text(key)
    sess = session or requests.Session()
    try:
        r = sess.get(url, headers={"User-Agent": UA}, timeout=timeout)
        text = extract_article_text(r.text) if r.status_code == 200 else ""
    except Exception:
        text = ""
    cache.put_text(key, text)      # cache the miss too; don't re-hit a paywall
    return text


# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------
def have_key(api_key: str) -> bool:
    return bool(api_key and api_key.strip())


def _call(api_key: str, prompt: str, max_tokens: int = 400,
          session: Optional[requests.Session] = None, timeout: float = 45.0) -> str:
    sess = session or requests.Session()
    r = sess.post(
        API_URL,
        headers={"x-api-key": api_key, "anthropic-version": API_VERSION,
                 "content-type": "application/json", "user-agent": UA},
        data=json.dumps({
            "model": config.SUMMARY_MODEL,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }),
        timeout=timeout,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Claude API {r.status_code}: {r.text[:200]}")
    blocks = (r.json() or {}).get("content") or []
    return "".join(b.get("text", "") for b in blocks if isinstance(b, dict)).strip()


_ITEM_PROMPT = """You are summarizing an NFL beat-reporter item for a dynasty \
fantasy football manager.

Write 2-3 short sentences covering only what a fantasy manager would act on: \
snap/target/touch share, injury status and timeline, depth-chart movement, \
role changes, contract or trade news. Skip narrative colour and speculation.

If the text does not actually say something fantasy-relevant, say so in one \
sentence instead of padding. Never invent detail that is not in the text.

HEADLINE: {title}

TEXT:
{body}"""

_DIGEST_PROMPT = """You are briefing a dynasty fantasy manager on today's news \
about players they roster.

Below are news items, each tagged with the rostered player it concerns. Write a \
tight bulleted digest, one bullet per player who has something that matters. \
Lead each bullet with the player name in bold, then what changed and why it \
matters for their dynasty value.

Rules: only use what is in the items — never invent detail. If several items \
cover one player, merge them. Skip players whose news is not actionable. Keep \
the whole thing under 200 words.

ITEMS:
{items}"""


def summarize_item(api_key: str, title: str, body: str, cache: DiskCache,
                   session: Optional[requests.Session] = None) -> str:
    """One item -> a short summary. Disk-cached by content hash, so the same
    article is never paid for twice."""
    body = (body or "").strip()
    if not body:
        return ""
    key = ("summary__"
           + hashlib.sha1(f"{config.SUMMARY_MODEL}|{title}|{body[:2000]}"
                          .encode("utf-8", "replace")).hexdigest()[:16] + ".json")
    if cache.fresh(key, config.SUMMARY_MAX_AGE_HOURS):
        try:
            return str(cache.get_json(key) or "")
        except Exception:
            pass
    out = _call(api_key, _ITEM_PROMPT.format(title=title or "(none)", body=body[:6000]),
                max_tokens=300, session=session)
    cache.put_json(key, out)
    return out


def digest_players(api_key: str, blocks: Sequence[tuple[str, Iterable[str]]],
                   cache: DiskCache, session: Optional[requests.Session] = None) -> str:
    """[(player_name, [item texts])] -> one bulleted roster digest."""
    lines: list[str] = []
    for name, texts in blocks:
        for t in texts:
            snippet = " ".join(str(t).split())[:400]
            if snippet:
                lines.append(f"- [{name}] {snippet}")
    if not lines:
        return ""
    payload = "\n".join(lines[:60])
    key = ("digest__"
           + hashlib.sha1(f"{config.SUMMARY_MODEL}|{payload}"
                          .encode("utf-8", "replace")).hexdigest()[:16] + ".json")
    if cache.fresh(key, 6):
        try:
            return str(cache.get_json(key) or "")
        except Exception:
            pass
    out = _call(api_key, _DIGEST_PROMPT.format(items=payload), max_tokens=900,
                session=session)
    cache.put_json(key, out)
    return out
