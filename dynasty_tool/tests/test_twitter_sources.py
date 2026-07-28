"""Hermetic tests for the X/twitterapi.io sources. No network."""
from __future__ import annotations

import json

import pytest

from dynasty_tool.cache import DiskCache
from dynasty_tool.ingest.news_client import NewsClient, build_from_query, fetch_all
from dynasty_tool.ingest.news_model import SourceSpec, load_sources, load_registry
from dynasty_tool.tests.fixtures_news import FakeSession, TWEETS

SEARCH = SourceSpec(id="x-nat", kind="twitter_search", label="Insiders (X)",
                    ref="AdamSchefter RapSheet")
SINGLE = SourceSpec(id="x-one", kind="twitter", label="Schefter", ref="@AdamSchefter")


# ------------------------------------------------------------ query build --
def test_build_from_query_batches_handles():
    q = build_from_query("AdamSchefter RapSheet")
    assert q == "(from:AdamSchefter OR from:RapSheet) -filter:replies"


def test_build_from_query_accepts_commas_and_at_signs():
    assert build_from_query("@a, @b,c") == "(from:a OR from:b OR from:c) -filter:replies"


def test_build_from_query_can_keep_replies():
    assert build_from_query("a", exclude_replies=False) == "(from:a)"


def test_build_from_query_rejects_an_empty_list():
    with pytest.raises(RuntimeError, match="no handles"):
        build_from_query("  ,  ")


# --------------------------------------------------------------- requests --
def _client(tmp_path, key="k", session=None):
    return NewsClient(DiskCache(tmp_path), session=session or FakeSession({}),
                      twitter_key=key)


def test_search_hits_advanced_search_with_a_batched_query(tmp_path):
    url, params, headers = _client(tmp_path).request_for(SEARCH)
    assert url.endswith("/twitter/tweet/advanced_search")
    assert params["query"] == "(from:AdamSchefter OR from:RapSheet) -filter:replies"
    assert params["queryType"] == "Latest"
    assert headers["X-API-Key"] == "k"


def test_single_handle_still_uses_last_tweets(tmp_path):
    url, params, _ = _client(tmp_path).request_for(SINGLE)
    assert url.endswith("/twitter/user/last_tweets")
    assert params == {"userName": "AdamSchefter"}      # leading @ stripped


@pytest.mark.parametrize("spec", [SEARCH, SINGLE])
def test_x_sources_fail_cleanly_without_a_key(tmp_path, spec):
    items, health = fetch_all(_client(tmp_path, key=""), [spec])
    assert items == [] and not health[0].ok and "key" in health[0].error


def test_cache_key_is_computable_without_a_key(tmp_path):
    """request_for raises without a key; the cache key must not depend on it."""
    c = _client(tmp_path, key="")
    assert c.cache_key(SEARCH).startswith("news__x-nat__")
    assert c.cache_key(SEARCH) != c.cache_key(SINGLE)


def test_changing_the_handle_list_busts_the_cache(tmp_path):
    c = _client(tmp_path)
    other = SourceSpec(id="x-nat", kind="twitter_search", label="Insiders",
                       ref="AdamSchefter RapSheet TomPelissero")
    assert c.cache_key(SEARCH) != c.cache_key(other)


# ---------------------------------------------------------------- parsing --
def test_search_results_parse_through_the_tweet_parser(tmp_path):
    s = FakeSession({"advanced_search": TWEETS})
    items, health = fetch_all(_client(tmp_path, session=s), [SEARCH])
    assert health[0].ok and len(items) == 2
    assert items[0].kind == "post"
    assert items[0].author_handle.startswith("@")
    assert items[0].url.startswith("https://x.com/")


def test_one_dead_x_source_does_not_sink_the_others(tmp_path):
    s = FakeSession({"advanced_search": TWEETS}, raise_for=("last_tweets",))
    items, health = fetch_all(_client(tmp_path, session=s), [SEARCH, SINGLE])
    assert len(items) == 2
    assert {h.id for h in health if h.ok} == {"x-nat"}


# --------------------------------------------------------------- registry --
def test_registry_ships_x_sources_and_they_are_valid():
    specs = {s.id: s for s in load_sources()}
    assert "nat-x-insiders" in specs
    assert specs["nat-x-insiders"].kind == "twitter_search"


def test_beat_writers_are_batched_not_one_source_per_team():
    """Per-team sources would be 32 billable calls a refresh. Division batching
    gets the same 32 teams for 8, so no per-team X source should remain."""
    reg = load_registry()
    assert not [s for b in reg["teams"].values() for s in b["sources"]
                if s["type"] == "twitter_search"]
    x = [s for s in load_sources() if s.kind == "twitter_search"]
    assert len(x) == 5          # 4 balanced beat groups + national insiders
    assert all(s.enabled for s in x)


def test_every_registry_x_query_builds():
    reg = load_registry()
    rows = [s for s in reg["national"] if s["type"] == "twitter_search"]
    rows += [s for b in reg["teams"].values() for s in b["sources"]
             if s["type"] == "twitter_search"]
    for s in rows:
        q = build_from_query(s["query"])
        assert q.startswith("(from:") and " OR " in q or q.count("from:") == 1


# ------------------------------------------------- division batching --------
def test_all_32_teams_are_covered_by_eight_division_calls():
    """The whole point: 8 pulls instead of 32, with nothing dropped."""
    from dynasty_tool.ingest.news_model import handle_teams
    specs = [s for s in load_sources() if s.kind == "twitter_search"]
    divisions = [s for s in specs if s.id.startswith("x-beat")]
    assert len(divisions) == 4
    covered = {handle_teams()[h.lower()] for s in divisions
               for h in s.ref.split() if h.lower() in handle_teams()}
    assert len(covered) == 32


def test_no_handle_is_billed_twice_across_divisions():
    specs = [s for s in load_sources()
             if s.kind == "twitter_search" and s.id.startswith("x-")]
    handles = [h.lower() for s in specs for h in s.ref.split()]
    assert len(handles) == len(set(handles))


def test_queries_stay_short_enough_for_the_search_api():
    """X caps a search query near 512 chars. Groups are bin-packed by length
    rather than by division precisely to keep headroom under it."""
    for s in [s for s in load_sources() if s.kind == "twitter_search"]:
        assert len(build_from_query(s.ref)) < 490


def test_x_pacing_respects_the_documented_free_tier_limit():
    """twitterapi.io free tier is one request every 5 seconds, and says so in
    its own 429 body. Pacing below that produced a wall of failures."""
    from dynasty_tool.ingest.news_client import NewsClient
    assert NewsClient.PACE_SECONDS >= 5.0


def test_x_sources_are_cached_longer_than_rss():
    """A five-second-per-call source should not be refetched on the same
    cadence as a free RSS host that answers instantly."""
    from dynasty_tool import config as cfg
    assert cfg.NEWS_X_MAX_AGE_MINUTES > cfg.NEWS_MAX_AGE_MINUTES


def test_team_attribution_survives_batching():
    """A batched division source can't carry one team on the spec, so items
    would otherwise arrive untagged and vanish from the By Team filter."""
    from dynasty_tool.analysis.news_feed import attribute_teams
    from dynasty_tool.ingest.news_model import NewsItem, handle_teams
    it = NewsItem(id="1", source_id="x-afc-east", source_label="AFC East beat (X)",
                  kind="post", author_handle="@JoeBuscaglia", text="Bills news")
    assert attribute_teams([it], handle_teams())[0].team == "BUF"


def test_attribution_is_case_insensitive_and_tolerates_the_at_sign():
    from dynasty_tool.analysis.news_feed import attribute_teams
    from dynasty_tool.ingest.news_model import NewsItem
    it = NewsItem(id="1", source_id="s", source_label="S", kind="post",
                  author_handle="@JOEBUSCAGLIA", text="x")
    assert attribute_teams([it], {"joebuscaglia": "BUF"})[0].team == "BUF"


def test_attribution_never_overwrites_an_existing_team():
    from dynasty_tool.analysis.news_feed import attribute_teams
    from dynasty_tool.ingest.news_model import NewsItem
    it = NewsItem(id="1", source_id="s", source_label="S", kind="post",
                  author_handle="@JoeBuscaglia", team="ATL", text="x")
    assert attribute_teams([it], {"joebuscaglia": "BUF"})[0].team == "ATL"


def test_attribution_leaves_unknown_handles_alone():
    from dynasty_tool.analysis.news_feed import attribute_teams
    from dynasty_tool.ingest.news_model import NewsItem
    it = NewsItem(id="1", source_id="s", source_label="S", kind="post",
                  author_handle="@nobody", text="x")
    assert attribute_teams([it], {"joebuscaglia": "BUF"})[0].team == ""


# ------------------------------------------------------ rate limiting -------
class _Resp:
    def __init__(self, status=200, text="{}", headers=None):
        self.status_code, self.text = status, text
        self.headers = headers or {}
        self.content = text.encode()
    def json(self):
        import json as _j
        return _j.loads(self.text or "{}")
    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(str(self.status_code))


class _TimedSession:
    """Records the wall-clock moment of every call, to prove pacing."""
    def __init__(self, resp=None):
        import time as _t
        self._t = _t
        self.stamps: list[float] = []
        self.resp = resp or _Resp(200, TWEETS)
    def get(self, url, params=None, headers=None, timeout=None):
        self.stamps.append(self._t.monotonic())
        return self.resp


def test_x_calls_are_serialised_not_fired_in_parallel(tmp_path):
    """All nine X sources hitting a metered API at once is what produced a
    wall of HTTP 429s."""
    s = _TimedSession()
    c = NewsClient(DiskCache(tmp_path), session=s, twitter_key="k")
    c.PACE_SECONDS = 0.05
    specs = [SourceSpec(id=f"x{i}", kind="twitter_search", label=f"g{i}",
                        ref=f"handle{i}") for i in range(5)]
    fetch_all(c, specs, max_workers=5)
    assert len(s.stamps) == 5
    gaps = [b - a for a, b in zip(sorted(s.stamps), sorted(s.stamps)[1:])]
    assert all(g >= 0.04 for g in gaps), gaps      # none overlapped


def test_rss_sources_are_not_paced(tmp_path):
    """Independent hosts should still burst in parallel; pacing everything
    would make a 40-source refresh crawl."""
    from dynasty_tool.tests.fixtures_news import RSS_PFT
    s = _TimedSession(_Resp(200, RSS_PFT))
    c = NewsClient(DiskCache(tmp_path), session=s)
    c.PACE_SECONDS = 5.0        # would be obvious if it applied
    specs = [SourceSpec(id=f"r{i}", kind="rss", label=f"f{i}",
                        ref=f"https://e{i}.com/feed") for i in range(4)]
    import time as _t
    t0 = _t.monotonic()
    fetch_all(c, specs, max_workers=4)
    assert _t.monotonic() - t0 < 2.0


def test_429_body_is_surfaced_so_throttling_and_no_credit_differ(tmp_path):
    """A metered API returns 429 for both 'slow down' and 'out of credit'.
    Those need opposite responses, so the message has to reach the user."""
    s = _TimedSession(_Resp(429, '{"msg":"insufficient balance"}'))
    c = NewsClient(DiskCache(tmp_path), session=s, twitter_key="k")
    c.PACE_SECONDS = 0.0
    _items, health = fetch_all(c, [SEARCH])
    assert not health[0].ok
    assert "429" in health[0].error and "insufficient balance" in health[0].error


def test_retry_after_header_is_honoured(tmp_path):
    from dynasty_tool.ingest.news_client import _retry_after
    assert _retry_after(_Resp(429, "", {"Retry-After": "3"})) == 3.0
    assert _retry_after(_Resp(429, "", {})) == 0.0
    assert _retry_after(_Resp(429, "", {"Retry-After": "soon"})) == 0.0


# --------------------------------------------------- X behind a button ------
def test_excluding_x_leaves_only_free_instant_sources():
    """The page must be able to render articles without paying the ~26s X toll."""
    from dynasty_tool.ingest.news_client import NewsClient
    specs = load_sources()
    without_x = [s for s in specs if s.kind not in NewsClient.PACED_KINDS]
    assert len(without_x) == len(specs) - 5      # the 5 X groups drop out
    assert all(s.kind in ("rss", "gnews", "bluesky") for s in without_x)
    assert len(without_x) > 100                  # plenty still loads instantly


def test_no_x_sources_means_no_pacing_cost(tmp_path):
    from dynasty_tool.tests.fixtures_news import RSS_PFT
    s = _TimedSession(_Resp(200, RSS_PFT))
    c = NewsClient(DiskCache(tmp_path), session=s)
    c.PACE_SECONDS = 5.2
    specs = [x for x in load_sources()
             if x.kind not in NewsClient.PACED_KINDS][:6]
    import time as _t
    t0 = _t.monotonic()
    fetch_all(c, specs, max_workers=6)
    assert _t.monotonic() - t0 < 3.0             # no serialisation applied
