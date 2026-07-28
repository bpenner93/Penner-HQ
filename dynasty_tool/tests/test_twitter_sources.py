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


def test_per_team_x_beats_ship_disabled():
    """32 extra billable calls per refresh is a cost decision, not a default."""
    reg = load_registry()
    team_x = [s for b in reg["teams"].values() for s in b["sources"]
              if s["type"] == "twitter_search"]
    assert len(team_x) == 32
    assert all(s.get("enabled") is False for s in team_x)
    assert not any(s.id.endswith("-x") for s in load_sources())   # none loaded


def test_every_registry_x_query_builds():
    reg = load_registry()
    rows = [s for s in reg["national"] if s["type"] == "twitter_search"]
    rows += [s for b in reg["teams"].values() for s in b["sources"]
             if s["type"] == "twitter_search"]
    for s in rows:
        q = build_from_query(s["query"])
        assert q.startswith("(from:") and " OR " in q or q.count("from:") == 1
