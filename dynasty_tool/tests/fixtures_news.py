"""Hermetic news fixtures -- no network.

Each payload is built to break something specific:

  * RSS_PFT      -- CDATA HTML, an ``&amp;`` entity, and a ``javascript:`` link.
  * ATOM_SAMPLE  -- RFC3339 dates and <content> winning over <summary>.
  * BSKY_FEED    -- a repost, a reply, and a displayName carrying an <img onerror>.
  * TWEETS       -- the nested {"data": {"tweets": []}} envelope shape.
  * PLAYERS_META -- the adversarial pool: two Josh Allens (ambiguity), a suffix
                    (Marvin Harrison Jr.), punctuation (Ja'Marr Chase, Amon-Ra
                    St. Brown), and a common surname that must never tag alone.
"""
from __future__ import annotations

import json

# 2026-07-24T15:30:00Z == 1785079800 ; used to pin the timegm-vs-mktime bug
RSS_PFT = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>ProFootballTalk</title>
  <item>
    <title>Bijan Robinson returns to full practice</title>
    <link>https://profootballtalk.nbcsports.com/bijan-full</link>
    <description><![CDATA[<p>Falcons RB <b>Bijan Robinson</b> was a full
      participant Wednesday.</p><p>No injury designation is expected.</p>]]></description>
    <pubDate>Fri, 24 Jul 2026 15:30:00 GMT</pubDate>
    <author>Mike Florio</author>
  </item>
  <item>
    <title>Jets &amp;amp; Packers discuss a trade</title>
    <link>https://profootballtalk.nbcsports.com/jets-packers</link>
    <description>Talks are early.</description>
    <pubDate>Fri, 24 Jul 2026 14:00:00 GMT</pubDate>
  </item>
  <item>
    <title>Sketchy item</title>
    <link>javascript:alert(1)</link>
    <description>No link should render.</description>
    <pubDate>Fri, 24 Jul 2026 13:00:00 GMT</pubDate>
  </item>
</channel></rss>
"""

ATOM_SAMPLE = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>The Athletic</title>
  <entry>
    <title>Rome Odunze takes WR1 reps</title>
    <link href="https://theathletic.com/odunze-wr1"/>
    <updated>2026-07-24T12:00:00Z</updated>
    <author><name>Adam Jahns</name></author>
    <summary>Short summary.</summary>
    <content type="html">&lt;p&gt;Odunze ran with the first team all week.&lt;/p&gt;</content>
  </entry>
</feed>
"""

MALFORMED_XML = "<rss><channel><item><title>broken"

BSKY_FEED = json.dumps({"feed": [
    {"post": {
        "uri": "at://did:plc:abc/app.bsky.feed.post/3kxyz",
        "author": {"handle": "rapsheet.bsky.social",
                   "displayName": "Ian Rapoport <img src=x onerror=alert(1)>",
                   "avatar": "https://cdn.bsky.app/img/a.jpg"},
        "record": {"text": "Sources: Falcons and Bijan Robinson agree to terms.",
                   "createdAt": "2026-07-24T15:45:00.000Z"}}},
    {"reason": {"$type": "app.bsky.feed.defs#reasonRepost"},
     "post": {"uri": "at://did:plc:zzz/app.bsky.feed.post/repost1",
              "author": {"handle": "someone.bsky.social", "displayName": "Someone"},
              "record": {"text": "This is a repost.", "createdAt": "2026-07-24T15:00:00Z"}}},
    {"post": {"uri": "at://did:plc:abc/app.bsky.feed.post/reply1",
              "author": {"handle": "rapsheet.bsky.social", "displayName": "Ian Rapoport"},
              "record": {"text": "Replying to you.", "reply": {"parent": {}},
                         "createdAt": "2026-07-24T14:30:00Z"}}},
]})

TWEETS = json.dumps({"data": {"tweets": [
    {"id": "1811", "text": "Bijan Robinson was a full participant Wednesday.",
     "createdAt": "Fri Jul 24 15:20:00 +0000 2026",
     "author": {"userName": "JoshKendall", "name": "Josh Kendall",
                "profilePicture": "https://pbs.twimg.com/p.jpg"}},
    {"id": "1812", "text": "Marvin Harrison Jr. did not practice.",
     "createdAt": "Fri Jul 24 14:10:00 +0000 2026",
     "author": {"userName": "JoshWeinfuss", "name": "Josh Weinfuss"}},
]}})

EMPTY_JSON = "{}"

# -- the adversarial player pool ---------------------------------------------
PLAYERS_META = {
    "100": {"full_name": "Josh Allen", "first_name": "Josh", "last_name": "Allen",
            "position": "QB", "team": "BUF"},
    "101": {"full_name": "Josh Allen", "first_name": "Josh", "last_name": "Allen",
            "position": "LB", "team": "JAX"},
    "200": {"full_name": "Marvin Harrison Jr.", "first_name": "Marvin",
            "last_name": "Harrison", "position": "WR", "team": "ARI"},
    "300": {"full_name": "Ja'Marr Chase", "first_name": "Ja'Marr",
            "last_name": "Chase", "position": "WR", "team": "CIN"},
    "400": {"full_name": "Amon-Ra St. Brown", "first_name": "Amon-Ra",
            "last_name": "St. Brown", "position": "WR", "team": "DET"},
    "500": {"full_name": "Bijan Robinson", "first_name": "Bijan",
            "last_name": "Robinson", "position": "RB", "team": "ATL"},
    "600": {"full_name": "Rome Odunze", "first_name": "Rome",
            "last_name": "Odunze", "position": "WR", "team": "CHI"},
    # in the meta blob but NOT in the pool -> must never tag
    "999": {"full_name": "Chris Moore", "first_name": "Chris",
            "last_name": "Moore", "position": "WR", "team": "FA"},
}

POOL = ["100", "101", "200", "300", "400", "500", "600"]


class FakeResponse:
    def __init__(self, text="", status_code=200):
        self.text = text
        self.status_code = status_code
        self.content = text.encode("utf-8")

    def json(self):
        import json as _j
        return _j.loads(self.text or "{}")

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    """Routes by URL substring. Mirrors tests/fixtures.py FakeClient."""

    def __init__(self, routes: dict, raise_for: tuple = (), status_for: dict = None):
        self.routes = routes
        self.raise_for = raise_for
        self.status_for = status_for or {}
        self.calls = 0
        self.urls: list[str] = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls += 1
        self.urls.append(url)
        for frag in self.raise_for:
            if frag in url:
                import requests
                raise requests.ConnectionError(f"boom: {frag}")
        for frag, code in self.status_for.items():
            if frag in url:
                return FakeResponse("", code)
        for frag, body in self.routes.items():
            if frag in url:
                return FakeResponse(body, 200)
        return FakeResponse("", 404)
