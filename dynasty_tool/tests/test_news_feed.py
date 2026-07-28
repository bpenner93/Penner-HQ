"""Hermetic tests for the beat-reporter feed: parsing, fetching, tagging,
dedupe and escaping. No network."""
from __future__ import annotations

import pytest

from dynasty_tool.analysis import news_feed as nf
from dynasty_tool.analysis import news_render as nr
from dynasty_tool.cache import DiskCache
from dynasty_tool.ingest import news_parse as np_
from dynasty_tool.ingest.news_client import NewsClient, fetch_all
from dynasty_tool.ingest.news_model import (NewsItem, SourceSpec, load_sources,
                                            team_names)
from dynasty_tool.tests import fixtures_news as fx

RSS = SourceSpec(id="pft", kind="rss", label="ProFootballTalk",
                 ref="https://profootballtalk.nbcsports.com/feed/")
ATOM = SourceSpec(id="ath", kind="rss", label="The Athletic",
                  ref="https://theathletic.com/feed/", team="CHI")
BSKY = SourceSpec(id="rap", kind="bluesky", label="Ian Rapoport", ref="rapsheet.bsky.social")
TW = SourceSpec(id="tw", kind="twitter", label="Beat X", ref="JoshKendall")


# ---------------------------------------------------------------- parsing ---
def test_rss_dates_are_utc_epoch_ms():
    """Regression guard for calendar.timegm vs time.mktime: mktime would apply
    the server's local offset and silently shift every timestamp."""
    items = np_.parse_rss(fx.RSS_PFT, RSS)
    top = [i for i in items if "Bijan" in i.title][0]
    # "Fri, 24 Jul 2026 15:30:00 GMT", computed independently of the parser via
    # datetime.fromisoformat(...).replace(tzinfo=utc).timestamp()
    assert top.published_ms == 1784907000 * 1000


def test_rss_html_description_becomes_plain_text():
    items = np_.parse_rss(fx.RSS_PFT, RSS)
    bijan = [i for i in items if "Bijan" in i.title][0]
    assert "<" not in bijan.text and "<b>" not in bijan.text
    assert "Bijan Robinson" in bijan.text
    assert "\n" in bijan.text          # <p> became a paragraph break


def test_rss_entities_are_decoded_exactly_once():
    items = np_.parse_rss(fx.RSS_PFT, RSS)
    jets = [i for i in items if "Jets" in i.title][0]
    assert "&" in jets.title and "&amp;" not in jets.title


def test_atom_prefers_content_over_summary():
    items = np_.parse_rss(fx.ATOM_SAMPLE, ATOM)
    assert items[0].published_ms == 1784894400 * 1000   # 2026-07-24T12:00:00Z
    assert "first team" in items[0].text


def test_malformed_xml_yields_items_not_exception():
    assert isinstance(np_.parse_rss(fx.MALFORMED_XML, RSS), list)


def test_bluesky_skips_reposts_and_replies():
    items = np_.parse_bluesky(fx.BSKY_FEED, BSKY)
    assert len(items) == 1
    it = items[0]
    assert it.kind == "post"
    assert it.author_handle == "@rapsheet.bsky.social"
    assert it.url == "https://bsky.app/profile/rapsheet.bsky.social/post/3kxyz"
    assert it.published_ms == 1784907900 * 1000         # 2026-07-24T15:45:00Z


def test_twitterapi_nested_envelope():
    items = np_.parse_twitterapi(fx.TWEETS, TW)
    assert len(items) == 2
    assert items[0].author_handle == "@JoshKendall"
    assert items[0].url == "https://x.com/JoshKendall/status/1811"
    assert items[0].published_ms > 0


def test_item_ids_are_stable_across_repeated_parses():
    a = [i.id for i in np_.parse_rss(fx.RSS_PFT, RSS)]
    b = [i.id for i in np_.parse_rss(fx.RSS_PFT, RSS)]
    assert a == b and all(a)


def test_canonical_url_strips_www_query_and_slash():
    assert (np_.canonical_url("https://WWW.Example.com/a/b/?utm=1#x")
            == "example.com/a/b")


# ---------------------------------------------------------------- client ----
def _client(tmp_path, session):
    return NewsClient(DiskCache(tmp_path), session=session)


def test_one_source_failure_does_not_affect_others(tmp_path):
    s = fx.FakeSession({"profootballtalk": fx.RSS_PFT, "bsky": fx.BSKY_FEED},
                       raise_for=("theathletic",))
    items, health = fetch_all(_client(tmp_path, s), [RSS, ATOM, BSKY])
    assert len(items) >= 2
    bad = [h for h in health if not h.ok]
    assert len(bad) == 1 and bad[0].id == "ath" and "boom" in bad[0].error
    assert {h.id for h in health if h.ok} == {"pft", "rap"}


def test_all_sources_failing_returns_empty_and_full_health(tmp_path):
    s = fx.FakeSession({}, raise_for=("profootballtalk", "theathletic"))
    items, health = fetch_all(_client(tmp_path, s), [RSS, ATOM])
    assert items == []
    assert len(health) == 2 and not any(h.ok for h in health)


def test_500_retries_then_reports_failure(tmp_path):
    s = fx.FakeSession({}, status_for={"profootballtalk": 500})
    c = _client(tmp_path, s)
    items, health = fetch_all(c, [RSS])
    assert items == [] and not health[0].ok
    assert s.calls == c.max_retries


def test_disk_cache_hit_avoids_network(tmp_path):
    s = fx.FakeSession({"profootballtalk": fx.RSS_PFT})
    cache = DiskCache(tmp_path)
    for _ in range(2):
        fetch_all(NewsClient(cache, session=s), [RSS])
    assert s.calls == 1


def test_changing_the_url_busts_the_cache(tmp_path):
    """Fixing a dead URL in feeds.json must refetch immediately, not serve the
    stale empty body for the rest of the TTL."""
    s = fx.FakeSession({"profootballtalk": fx.RSS_PFT, "example.com": fx.RSS_PFT})
    cache = DiskCache(tmp_path)
    fetch_all(NewsClient(cache, session=s), [RSS])
    moved = SourceSpec(id="pft", kind="rss", label="PFT",
                       ref="https://example.com/feed/")
    fetch_all(NewsClient(cache, session=s), [moved])
    assert s.calls == 2


def test_404_is_cached_as_empty(tmp_path):
    s = fx.FakeSession({})
    cache = DiskCache(tmp_path)
    for _ in range(2):
        fetch_all(NewsClient(cache, session=s), [RSS])
    assert s.calls == 1          # the miss was remembered


def test_twitter_source_without_a_key_fails_cleanly(tmp_path):
    s = fx.FakeSession({})
    items, health = fetch_all(NewsClient(DiskCache(tmp_path), session=s), [TW])
    assert items == [] and not health[0].ok and "key" in health[0].error


# ---------------------------------------------------------------- registry --
def test_packaged_registry_loads_and_covers_32_teams():
    assert len(team_names()) == 32
    specs = load_sources()
    assert len(specs) > 40
    assert len({s.id for s in specs}) == len(specs)      # ids unique


def test_registry_drops_bad_rows():
    reg = {"national": [
        {"id": "ok", "type": "rss", "name": "OK", "url": "https://a.com/f"},
        {"id": "ok", "type": "rss", "name": "dupe id", "url": "https://b.com/f"},
        {"id": "Bad Id", "type": "rss", "name": "bad", "url": "https://c.com/f"},
        {"id": "badkind", "type": "carrier-pigeon", "name": "x", "url": "https://d.com"},
        {"id": "badurl", "type": "rss", "name": "x", "url": "javascript:alert(1)"},
    ], "teams": {}}
    assert [s.id for s in load_sources(registry=reg)] == ["ok"]


def test_gnews_spec_builds_a_search_url():
    spec = SourceSpec(id="g", kind="gnews", label="X", ref='"Josh Kendall" Falcons')
    assert spec.url.startswith("https://news.google.com/rss/search?q=")
    assert "Josh" in spec.url and "Kendall" in spec.url


# ---------------------------------------------------------------- tagging ---
@pytest.fixture
def index():
    return nf.build_name_index(fx.PLAYERS_META, pool=fx.POOL)


def _tag(text, index, team=""):
    return nf.tag_text(text, index, hint_team=team)


def test_requires_first_and_last_name(index):
    assert _tag("Chase went off for 150 yards", index)[0] == ()
    assert _tag("Ja'Marr Chase went off", index)[0] == ("300",)


def test_common_surname_alone_never_tags(index):
    assert _tag("Robinson had a big day", index)[0] == ()
    assert _tag("Bijan Robinson had a big day", index)[0] == ("500",)


def test_suffix_matches_in_both_directions(index):
    assert _tag("Marvin Harrison Jr. did not practice", index)[0] == ("200",)
    assert _tag("Marvin Harrison was limited", index)[0] == ("200",)


def test_punctuated_names(index):
    assert _tag("Amon-Ra St. Brown is questionable", index)[0] == ("400",)


def test_ambiguous_name_is_not_guessed(index):
    pids, amb = _tag("Josh Allen threw for 300 yards", index)
    assert pids == () and amb == 1


def test_ambiguous_resolved_by_team_context(index):
    assert _tag("Bills QB Josh Allen threw for 300", index)[0] == ("100",)
    assert _tag("Jaguars LB Josh Allen had two sacks", index)[0] == ("101",)


def test_ambiguous_resolved_by_source_team_hint(index):
    assert _tag("Josh Allen threw for 300", index, team="BUF")[0] == ("100",)


def test_out_of_pool_player_is_never_tagged(index):
    assert _tag("Chris Moore caught a pass", index)[0] == ()


def test_tag_report_counts_are_consistent(index):
    items = [
        NewsItem(id="1", source_id="s", source_label="S", kind="article",
                 title="Bijan Robinson returns", text=""),
        NewsItem(id="2", source_id="s", source_label="S", kind="article",
                 title="Josh Allen threw for 300", text=""),
        NewsItem(id="3", source_id="s", source_label="S", kind="article",
                 title="Nothing to see", text=""),
    ]
    tagged, rep = nf.tag_items(items, index)
    assert rep.items_scanned == 3
    assert rep.items_tagged == 1
    assert rep.ambiguous_skipped == 1
    assert rep.pool_size == len(fx.POOL)
    assert "ambiguous" in rep.line()


# ---------------------------------------------------------------- dedupe ----
def _it(i, title, ms, src="a", kind="article", url="", players=()):
    return NewsItem(id=str(i), source_id=src, source_label=src.upper(), kind=kind,
                    title=title, text=title, url=url, published_ms=ms,
                    player_ids=players)


def test_dedupe_collapses_the_same_story_from_many_outlets():
    t = "Falcons sign Bijan Robinson to an extension worth 30 million"
    items = [_it(i, t, 1000 + i, src=f"s{i}") for i in range(5)]
    out = nf.dedupe(items)
    assert len(out) == 1
    assert out[0].dupe_count == 4
    assert len(out[0].dupe_sources) == 4


def test_dedupe_collapses_on_canonical_url():
    a = _it(1, "Headline one", 1000, url="https://x.com/a?utm=1")
    b = _it(2, "Completely different words entirely", 2000, url="https://www.x.com/a/")
    assert len(nf.dedupe([a, b])) == 1


def test_dedupe_keeps_genuinely_distinct_updates():
    a = _it(1, "Bijan Robinson is questionable for Sunday", 0)
    b = _it(2, "Bijan Robinson has been ruled OUT for Sunday", 5 * 3600 * 1000)
    assert len(nf.dedupe([a, b])) == 2


def test_dedupe_respects_the_time_window():
    t = "Falcons sign Bijan Robinson to an extension worth 30 million"
    a, b = _it(1, t, 0), _it(2, t, 12 * 3600 * 1000)
    assert len(nf.dedupe([a, b])) == 2


def test_dedupe_never_merges_disjoint_player_sets():
    """Boilerplate is near-identical across players; the player veto is what
    stops 'X did not practice' collapsing into 'Y did not practice'."""
    a = _it(1, "did not practice Wednesday injury report", 0, players=("500",))
    b = _it(2, "did not practice Wednesday injury report", 1000, players=("600",))
    assert len(nf.dedupe([a, b])) == 2


def test_dedupe_prefers_the_post_over_the_article():
    t = "Falcons sign Bijan Robinson to an extension worth 30 million dollars"
    art = _it(1, t, 1000, src="pft", kind="article")
    post = _it(2, t, 2000, src="rap", kind="post")
    out = nf.dedupe([art, post])
    assert len(out) == 1 and out[0].kind == "post"


def test_dedupe_merges_player_ids_across_the_cluster():
    """Overlapping player sets merge and take the union — one outlet naming both
    players and another naming only one must not lose the second."""
    t = "Falcons trade Bijan Robinson for Rome Odunze in a blockbuster deal"
    a = _it(1, t, 1000, src="a", players=("500",))
    b = _it(2, t, 2000, src="b", players=("500", "600"))
    out = nf.dedupe([a, b])
    assert len(out) == 1
    assert out[0].player_ids == ("500", "600")


# ---------------------------------------------------------------- filters ---
def test_filter_by_team_source_query_player_and_since():
    a = _it(1, "Falcons news", 5000, src="atl")
    a = a.__class__(**{**a.__dict__, "team": "ATL", "player_ids": ("500",)})
    b = _it(2, "Bears news", 1000, src="chi")
    b = b.__class__(**{**b.__dict__, "team": "CHI"})
    assert [i.id for i in nf.filter_items([a, b], teams=["ATL"])] == ["1"]
    assert [i.id for i in nf.filter_items([a, b], sources=["chi"])] == ["2"]
    assert [i.id for i in nf.filter_items([a, b], query="bears")] == ["2"]
    assert [i.id for i in nf.filter_items([a, b], player_ids=["500"])] == ["1"]
    assert [i.id for i in nf.filter_items([a, b], since_ms=2000)] == ["1"]


def test_mute_terms_drop_sportsbook_spam():
    a = _it(1, "Best NFL sportsbook promo code today", 1000)
    b = _it(2, "Real football news", 1000)
    assert [i.id for i in nf.filter_items([a, b], mute=["sportsbook promo"])] == ["2"]


def test_group_by_player_respects_caller_order():
    a = _it(1, "x", 1000, players=("500",))
    b = _it(2, "y", 2000, players=("600", "500"))
    grouped = nf.group_by_player([a, b], order=["600", "500"])
    assert [pid for pid, _ in grouped] == ["600", "500"]
    assert [i.id for i in grouped[1][1]] == ["2", "1"]     # newest first


# ---------------------------------------------------------------- render ----
def test_script_tag_in_text_is_escaped():
    it = _it(1, "<script>alert(1)</script>", 1000)
    out = nr.item_card(it, 2000)
    assert "<script>" not in out and "&lt;script&gt;" in out


def test_bluesky_display_name_injection_is_escaped():
    items = np_.parse_bluesky(fx.BSKY_FEED, BSKY)
    out = nr.item_card(items[0], items[0].published_ms + 60000)
    assert "onerror" not in out or "&lt;img" in out
    assert "<img src=x" not in out


def test_apostrophe_cannot_break_out_of_an_attribute():
    it = NewsItem(id="1", source_id="s", source_label="S", kind="post",
                  author="x' onmouseover='alert(1)", text="hi",
                  url="https://a.com", published_ms=1000)
    out = nr.item_card(it, 2000)
    assert "onmouseover='alert(1)" not in out
    assert "&#x27;" in out


@pytest.mark.parametrize("bad", [
    "javascript:alert(1)", "JaVaScRiPt:alert(1)", "java\nscript:alert(1)",
    "data:text/html,<script>alert(1)</script>", "//evil.com/x", "vbscript:msgbox",
    "  javascript:alert(1)  ", "file:///etc/passwd",
])
def test_dangerous_urls_are_dropped(bad):
    assert nr.safe_url(bad) == ""


def test_good_urls_survive():
    assert nr.safe_url("https://a.com/x") == "https://a.com/x"
    assert nr.safe_url("http://a.com/x") == "http://a.com/x"


def test_javascript_link_from_rss_renders_no_href():
    items = np_.parse_rss(fx.RSS_PFT, RSS)
    sketchy = [i for i in items if "Sketchy" in i.title][0]
    out = nr.item_card(sketchy, 9_999_999_999_999)
    assert "javascript:" not in out and "href=" not in out


def test_links_have_noopener_noreferrer():
    it = _it(1, "t", 1000, url="https://a.com/x")
    out = nr.item_card(it, 2000)
    assert "rel='noopener noreferrer'" in out and "target='_blank'" in out


def test_image_host_allowlist():
    assert nr.safe_url("https://cdn.bsky.app/img/a.jpg", img=True)
    assert nr.safe_url("https://evil.com/a.jpg", img=True) == ""


def test_avatar_falls_back_to_an_initial_when_host_is_blocked():
    it = NewsItem(id="1", source_id="s", source_label="S", kind="post",
                  author="Ian Rapoport", avatar="https://evil.com/a.jpg",
                  text="hi", published_ms=1000)
    out = nr.item_card(it, 2000)
    assert "evil.com" not in out and "nf-av-i" in out


def test_no_double_escaping():
    it = _it(1, "Jets & Packers", 1000)
    out = nr.item_card(it, 2000)
    assert "&amp;amp;" not in out and "&amp;" in out


def test_rel_time_buckets():
    now = 1_000_000_000_000
    assert nr.rel_time(now, now - 30_000) == "now"
    assert nr.rel_time(now, now - 5 * 60_000) == "5m"
    assert nr.rel_time(now, now - 3 * 3_600_000) == "3h"
    assert nr.rel_time(now, now - 2 * 86_400_000) == "2d"


def test_feed_html_is_one_wrapper():
    items = [_it(i, f"t{i}", 1000 + i) for i in range(3)]
    out = nr.feed_html(items, 5000)
    assert out.count("nf-feed") == 1 and out.count("nf-item") == 3
