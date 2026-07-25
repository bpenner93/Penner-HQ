"""Tests for the KTC-style trade calculator core (pure; no network, no Streamlit)."""
from __future__ import annotations

import pandas as pd

from ..analysis import trade_calc as tc
from ..analysis.optimizer import RPlayer, RosterValue
from .fixtures import make_test_frames, make_test_provider

SLOTS = ["QB", "RB", "WR", "FLEX", "BN", "BN"]


def _universe():
    players_df, _picks, _ids = make_test_frames()
    return tc.build_dynasty_universe(make_test_provider(), players_df)


def _roster(uid: str, players: list[RPlayer]) -> RosterValue:
    return RosterValue(user_id=uid, display=uid, players=players)


def test_universe_keys_players_by_sleeper_id_and_includes_picks():
    uni = _universe()
    star = uni["p:1"]                      # fp_id 1000 -> sleeper_id 1
    assert star.label == "Star Wr" and star.value == 9000 and star.kind == "player"
    assert star.player_id == "1"
    # a valued player with no sleeper crosswalk row is still pickable, keyed by fp_id
    assert uni["p:fp:3000"].label == "Low Guy"
    assert uni["p:fp:3000"].player_id is None          # no roster join, and says so
    assert uni["k:2027 1st"].kind == "pick"
    assert uni["k:2027 1st"].value > uni["k:2027 2nd"].value > 0


def test_universe_follows_the_qb_format():
    players_df, _p, _i = make_test_frames()
    one = tc.build_dynasty_universe(make_test_provider(1), players_df)
    sf = tc.build_dynasty_universe(make_test_provider(2), players_df)
    assert one["p:200"].value == 5000 and sf["p:200"].value == 5200


def test_extend_with_rosters_keeps_unvalued_players_pickable():
    uni = _universe()
    idp = RPlayer("999", "Unvalued Idp", "LB", 0.0, 30, False)
    ext = tc.extend_with_rosters(uni, {"U3": _roster("U3", [idp])})
    assert "p:999" not in uni                      # DynastyProcess doesn't value IDP
    assert ext["p:999"].label == "Unvalued Idp"    # ...but search must still find him
    assert ext["p:999"].value == 0.0


def test_winner_is_the_side_receiving_the_larger_package():
    uni = _universe()
    ev = tc.evaluate([uni["p:1"]], [uni["p:200"]], basis="realized", qb_format=1,
                     name_a="Alice", name_b="Bob")
    assert ev.side_a.total == 9000 and ev.side_b.total == 5000
    # Alice sends the bigger package, so Bob nets the value
    assert tc.winner(ev) == "Bob"
    assert ev.verdict == "LOPSIDED"
    assert "Bob nets" in tc.trade_summary_text(ev, [uni["p:1"]], [uni["p:200"]])


def test_even_trade_has_no_winner():
    uni = _universe()
    # 9000 against 5000 + 100 + a 2027 1st (~3833) -> inside the 5% "even" band
    ev = tc.evaluate([uni["p:1"]], [uni["p:200"], uni["p:fp:3000"], uni["k:2027 1st"]],
                     basis="realized", qb_format=1)
    assert ev.pct_gap < 0.05 and ev.verdict == "EVEN"
    same = tc.evaluate([uni["p:1"]], [uni["p:1"]], basis="realized", qb_format=1)
    assert same.verdict == "EVEN" and tc.winner(same) is None


def test_roster_impact_refills_the_lineup():
    uni = _universe()
    mine = [
        RPlayer("qb1", "My Qb", "QB", 1000, 27, True),
        RPlayer("200", "Pick Twohundred", "RB", 5000, 22, True),
        RPlayer("wr1", "My Wr", "WR", 2000, 25, True),
        RPlayer("wr2", "Bench Wr", "WR", 300, 25, True),   # FLEX starter before
    ]
    rv = _roster("U1", mine)
    # send the RB, get the star WR back
    imp = tc.roster_impact(rv, SLOTS, incoming=[uni["p:1"]], outgoing=[uni["p:200"]])
    names_in = {p.name for p in imp.lineup_in}
    names_out = {p.name for p in imp.lineup_out}
    assert "Star Wr" in names_in                     # cracks the lineup immediately
    assert "Pick Twohundred" in names_out            # traded away
    assert imp.total_after == imp.total_before - 5000 + 9000
    assert imp.starter_after > imp.starter_before


def test_picks_move_value_but_never_take_a_lineup_slot():
    rv = _roster("U1", [RPlayer("wr1", "My Wr", "WR", 2000, 25, True)])
    uni = _universe()
    imp = tc.roster_impact(rv, SLOTS, incoming=[uni["k:2027 1st"]], outgoing=[])
    assert imp.picks_in == uni["k:2027 1st"].value
    assert imp.lineup_in == [] and imp.lineup_out == []
    assert imp.starter_after == imp.starter_before          # lineup untouched
    assert imp.total_after > imp.total_before               # asset value still counted


def test_roster_impact_ignores_players_not_on_the_roster():
    """A hypothetical ('what would it cost to get X') must not delete a stranger."""
    rv = _roster("U1", [RPlayer("wr1", "My Wr", "WR", 2000, 25, True)])
    uni = _universe()
    imp = tc.roster_impact(rv, SLOTS, incoming=[], outgoing=[uni["p:200"]])
    assert imp.starter_after == imp.starter_before
    assert imp.total_after == imp.total_before - 5000       # value math still honest


def test_sweetener_lands_closest_to_the_gap_without_flipping_it():
    uni = _universe()
    pool = [uni["p:200"], uni["p:fp:3000"], uni["k:2027 1st"], uni["k:2027 2nd"]]
    picks = tc.suggest_sweeteners(pool, gap=uni["k:2027 2nd"].value, n=2)
    assert picks[0].key == "k:2027 2nd"                     # exact closer
    assert uni["p:200"] not in picks                        # 5000 would flip the deal
    assert tc.suggest_sweeteners(pool, gap=0) == []
    assert tc.suggest_sweeteners(pool, gap=1e9, exclude={a.key for a in pool}) == []


def test_redraft_universe_is_projection_points_keyed_by_gsis():
    sr = pd.DataFrame([
        {"player_id": "00-001", "player_display_name": "Star Wr", "position": "WR",
         "team": "CIN", "age": 26.0, "season_projected_pts": 290.5},
        {"player_id": "00-002", "player_display_name": "Some Rb", "position": "RB",
         "team": "ATL", "age": 24.0, "season_projected_pts": 210.0},
    ])
    uni = tc.build_redraft_universe(sr)
    assert uni["p:00-001"].value == 290.5 and uni["p:00-001"].team == "CIN"
    assert not any(a.kind == "pick" for a in uni.values())   # picks aren't a redraft asset
