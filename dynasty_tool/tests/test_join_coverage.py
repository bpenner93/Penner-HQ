"""Join coverage: unmatched players are counted and reported, never hidden."""
from __future__ import annotations

from ..ingest.context import build_context
from ..value import assets
from .fixtures import FakeClient, make_test_provider


def test_unmatched_player_counted_not_hidden():
    provider = make_test_provider()
    # player 999 has no value row -> (0.0, False), and it must be tracked.
    val, matched = provider.player_value("999", name="Unvalued Idp")
    assert val == 0.0
    assert matched is False

    cov = provider.coverage()
    assert cov["players_unmatched"] == 1
    assert "999" in cov["unmatched_ids"]
    assert cov["players_matched"] == 0  # only 999 touched so far


def test_matched_and_unmatched_both_reported():
    provider = make_test_provider()
    provider.player_value("200")                       # matched
    provider.player_value("999", name="Unvalued Idp")  # unmatched
    cov = provider.coverage()
    assert cov["players_seen"] == 2
    assert cov["players_matched"] == 1
    assert cov["players_unmatched"] == 1


def test_asset_value_flags_unmatched_player():
    provider = make_test_provider()
    client = FakeClient()
    av = assets.value_player(provider, client.players(), "999")
    assert av.matched is False
    assert av.flag == "UNMATCHED_PLAYER"
    assert av.value == 0.0
    # label still identifies the player so it is auditable, not silently dropped
    assert "Unvalued" in av.label


def test_coverage_reports_scrape_date_and_basis():
    provider = make_test_provider()
    cov = provider.coverage()
    assert cov["scrape_date"] == "2026-07-03"
    assert cov["basis"] == "realized"
    assert cov["qb_format"] == 1


# ---------------------------------------------- Sleeper cache freshness -----
def test_mutable_sleeper_endpoints_all_carry_a_ttl():
    """DiskCache.fresh(key, None) means "reuse forever". Rosters change on every
    trade, so caching them uncapped made trades invisible until the container
    restarted. Past drafts are genuinely immutable and may stay uncapped.
    """
    import inspect
    from dynasty_tool.ingest import sleeper_client as sc
    src = inspect.getsource(sc.SleeperClient)
    for method in ("def rosters", "def matchups", "def transactions",
                   "def users", "def league", "def user_leagues"):
        body = src.split(method, 1)[1].split("\n    def ", 1)[0]
        assert "max_age_hours" in body, f"{method} caches forever"


def test_immutable_endpoints_may_stay_uncapped():
    import inspect
    from dynasty_tool.ingest import sleeper_client as sc
    src = inspect.getsource(sc.SleeperClient)
    body = src.split("def draft_picks", 1)[1].split("\n    def ", 1)[0]
    assert "max_age_hours" not in body      # a completed draft never changes


def test_roster_ttl_is_short_enough_to_notice_a_trade():
    from dynasty_tool import config as cfg
    assert cfg.ROSTERS_MAX_AGE_HOURS <= 0.25          # 15 minutes or better


def test_disk_cache_invalidate_removes_only_the_matching_prefix(tmp_path):
    from dynasty_tool.cache import DiskCache
    c = DiskCache(tmp_path)
    c.put_json("sleeper__league__1__rosters.json", [1])
    c.put_json("sleeper__league__1__users.json", [2])
    c.put_text("news__pft__abc.txt", "keep me")
    assert c.invalidate("sleeper__") == 2
    assert not c.fresh("sleeper__league__1__rosters.json")
    assert c.fresh("news__pft__abc.txt")               # untouched


def test_disk_cache_invalidate_everything(tmp_path):
    from dynasty_tool.cache import DiskCache
    c = DiskCache(tmp_path)
    c.put_json("a.json", 1)
    c.put_json("b.json", 2)
    assert c.invalidate() == 2
