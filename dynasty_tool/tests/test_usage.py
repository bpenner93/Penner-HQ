"""Hermetic tests for route participation, snap share and TPRR. No network."""
from __future__ import annotations

import pytest

from dynasty_tool.analysis import usage as us

A, B, C, OL = "00-0001", "00-0002", "00-0003", "00-0009"


def play(team="KC", players=(A, B, OL), **markers):
    row = {"possession_team": team, "offense_players": ";".join(players)}
    row.update(markers)
    return row


DROPBACK = {"time_to_throw": "2.4"}
RUN = {"time_to_throw": None, "route": None, "defense_coverage_type": None}


# ------------------------------------------------------ dropback classifier --
@pytest.mark.parametrize("markers,expected", [
    ({"time_to_throw": "2.4"}, True),
    ({"route": "HITCH/CURL"}, True),
    ({"defense_coverage_type": "COVER_3"}, True),
    ({"time_to_throw": None, "route": "", "defense_coverage_type": None}, False),
    ({}, False),
])
def test_is_dropback(markers, expected):
    assert us.is_dropback(play(**markers)) is expected


def test_pandas_null_sentinels_are_not_mistaken_for_data():
    """Parquet round-trips missing values as 'nan'/'<NA>' strings once stringified;
    treating those as present would classify every run play as a dropback."""
    for junk in ("nan", "None", "NA", "<NA>", "  "):
        assert us.is_dropback(play(time_to_throw=junk)) is False


def test_split_players_handles_junk():
    assert us.split_players(f"{A};{B}") == [A, B]
    assert us.split_players(None) == []
    assert us.split_players("") == []
    assert us.split_players(f" {A} ; ; {B} ") == [A, B]


# ------------------------------------------------------------- aggregation --
def test_aggregate_counts_only_dropbacks():
    rows = [play(**DROPBACK), play(**DROPBACK), play(**RUN)]
    routes, team_db = us.aggregate_participation(rows)
    assert routes[A] == 2 and routes[OL] == 2
    assert team_db["KC"] == 2          # the run play is excluded from both


def test_aggregate_is_per_team():
    rows = [play("KC", **DROPBACK), play("BUF", (C,), **DROPBACK)]
    _routes, team_db = us.aggregate_participation(rows)
    assert team_db == {"KC": 1, "BUF": 1}


def test_player_team_picks_where_he_played_most():
    rows = ([play("KC", (A,), **DROPBACK)] * 3) + [play("BUF", (A,), **DROPBACK)]
    assert us.player_team(rows)[A] == "KC"


def test_empty_input_is_safe():
    assert us.aggregate_participation([]) == ({}, {})
    assert us.player_team([]) == {}


# ------------------------------------------------------------------ snaps --
def test_aggregate_snaps_averages_pct_across_games():
    rows = [{"pfr_player_id": "P1", "offense_snaps": "50", "offense_pct": "0.8"},
            {"pfr_player_id": "P1", "offense_snaps": "30", "offense_pct": "0.6"}]
    out = us.aggregate_snaps(rows)
    assert out["P1"]["snaps"] == 80
    assert out["P1"]["games"] == 2
    assert out["P1"]["snap_pct"] == pytest.approx(0.7)


def test_aggregate_snaps_skips_zero_and_junk():
    rows = [{"pfr_player_id": "P1", "offense_snaps": "0", "offense_pct": "0"},
            {"pfr_player_id": "", "offense_snaps": "10", "offense_pct": "0.5"},
            {"pfr_player_id": "P2", "offense_snaps": "x", "offense_pct": "0.5"}]
    assert us.aggregate_snaps(rows) == {}


def test_aggregate_snaps_translates_pfr_to_gsis_and_drops_unmapped():
    rows = [{"pfr_player_id": "P1", "offense_snaps": "50", "offense_pct": "0.8"},
            {"pfr_player_id": "P9", "offense_snaps": "50", "offense_pct": "0.8"}]
    out = us.aggregate_snaps(rows, pfr_to_gsis={"P1": A})
    assert set(out) == {A}


# ------------------------------------------------------------------ build --
def _usage(routes=500, dropbacks=600, targets=100, snaps=800, pct=0.85, games=17):
    return us.PlayerUsage(gsis_id=A, routes=routes, dropbacks=dropbacks,
                          targets=targets, snaps=snaps, snap_pct=pct, games=games)


def test_rates():
    u = _usage()
    assert u.route_pct == pytest.approx(500 / 600)
    assert u.tprr == pytest.approx(100 / 500)
    assert u.routes_per_game == pytest.approx(500 / 17)


def test_traded_player_route_pct_is_capped_at_one():
    """Routes accumulate across both teams while the denominator is one team's
    dropbacks; without the cap he'd read as 120% of his own offence."""
    assert _usage(routes=720, dropbacks=600).route_pct == 1.0


def test_rates_never_divide_by_zero():
    u = us.PlayerUsage(gsis_id=A)
    assert (u.route_pct, u.tprr, u.routes_per_game) == (0.0, 0.0, 0.0)


def test_build_usage_merges_all_three_sources():
    out = us.build_usage({A: 500, B: 10}, {"KC": 600}, {A: "KC", B: "KC"},
                         {A: {"snaps": 800, "snap_pct": 0.85, "games": 17}},
                         targets={A: 100})
    assert out[A].routes == 500 and out[A].targets == 100
    assert out[A].snap_pct == pytest.approx(0.85)
    assert out[B].snaps == 0            # no snap row -> zeros, not a crash


def test_build_usage_respects_min_routes():
    out = us.build_usage({A: 500, B: 10}, {"KC": 600}, {A: "KC", B: "KC"}, {},
                         min_routes=50)
    assert set(out) == {A}


def test_unknown_team_leaves_dropbacks_zero_not_wrong():
    out = us.build_usage({A: 500}, {"KC": 600}, {}, {})
    assert out[A].dropbacks == 0 and out[A].route_pct == 0.0


# ------------------------------------------------------------- crosswalk ----
def test_crosswalk_treats_literal_NA_as_missing():
    """'NA' is a real string in db_playerids and would otherwise become an id."""
    rows = [{"gsis_id": "G1", "sleeper_id": "111"},
            {"gsis_id": "NA", "sleeper_id": "222"},
            {"gsis_id": "G3", "sleeper_id": "NA"},
            {"gsis_id": "G4", "sleeper_id": "444.0"}]
    out = us.crosswalk(rows, "gsis_id", "sleeper_id")
    assert out == {"G1": "111", "G4": "444"}     # float suffix stripped


def test_crosswalk_keeps_the_first_mapping():
    rows = [{"gsis_id": "G1", "sleeper_id": "111"},
            {"gsis_id": "G1", "sleeper_id": "999"}]
    assert us.crosswalk(rows, "gsis_id", "sleeper_id") == {"G1": "111"}


# ------------------------------------------------------------ leaderboard ---
def test_leaderboard_filters_cameos():
    """A 3-route cameo with one target is a 0.33 TPRR and would otherwise top
    the board over every real starter."""
    players = {
        A: us.PlayerUsage(A, routes=3, dropbacks=600, targets=1),
        B: us.PlayerUsage(B, routes=500, dropbacks=600, targets=100),
    }
    assert [u.gsis_id for u in us.leaderboard(players, "tprr", min_routes=50)] == [B]


def test_leaderboard_sorts_desc_and_limits():
    players = {str(i): us.PlayerUsage(str(i), routes=100 + i, dropbacks=600)
               for i in range(5)}
    top = us.leaderboard(players, "routes", min_routes=0, limit=2)
    assert [u.gsis_id for u in top] == ["4", "3"]


# ----------------------------------------------------------------- sanity ---
def test_looks_sane_passes_a_clean_season():
    players = {str(i): us.PlayerUsage(str(i), routes=500, dropbacks=600)
               for i in range(20)}
    ok, msg = us.looks_sane(players)
    assert ok and "0.0%" in msg


def test_looks_sane_tolerates_a_few_traded_players():
    players = {str(i): us.PlayerUsage(str(i), routes=500, dropbacks=600)
               for i in range(40)}
    players["x"] = us.PlayerUsage("x", routes=700, dropbacks=600)
    assert us.looks_sane(players)[0] is True


def test_looks_sane_fails_when_the_classifier_drifts():
    players = {str(i): us.PlayerUsage(str(i), routes=900, dropbacks=600)
               for i in range(20)}
    ok, msg = us.looks_sane(players)
    assert ok is False and "above their team" in msg


def test_looks_sane_on_empty():
    assert us.looks_sane({})[0] is False
