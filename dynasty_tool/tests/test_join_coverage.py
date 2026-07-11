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
