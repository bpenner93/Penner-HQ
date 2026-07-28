"""Hermetic tests for college production features and the CFBD->nflverse bridge,
plus regressions for the devy defects found in audit. No network."""
from __future__ import annotations

import pytest

from dynasty_tool.analysis import college_features as cf
from dynasty_tool.analysis import devy as dv


def stat(season, pid, name, team, kind, value, category="receiving"):
    return {"season": season, "playerId": pid, "player": name, "team": team,
            "category": category, "statType": kind, "stat": value}


# 2021 Team A: 1000 rec yards, 10 rec TDs total
ROWS = [
    stat(2021, "1", "Alpha", "Team A", "YDS", 600), stat(2021, "1", "Alpha", "Team A", "TD", 6),
    stat(2021, "2", "Bravo", "Team A", "YDS", 400), stat(2021, "2", "Bravo", "Team A", "TD", 4),
    stat(2022, "1", "Alpha", "Team A", "YDS", 300), stat(2022, "1", "Alpha", "Team A", "TD", 2),
    stat(2022, "3", "Delta", "Team A", "YDS", 700), stat(2022, "3", "Delta", "Team A", "TD", 8),
    stat(2021, "9", "Rusher", "Team A", "YDS", 900, category="rushing"),
]


# ------------------------------------------------------------- dominator ----
def test_pivot_folds_long_format_into_one_row_per_player_season():
    p = cf.pivot_season_stats(ROWS)
    assert p[(2021, "1")]["yards"] == 600
    assert p[(2021, "1")]["tds"] == 6
    assert (2021, "9") not in p          # rushing filtered out


def test_dominator_is_the_mean_of_yard_share_and_td_share():
    dom = cf.dominators(cf.pivot_season_stats(ROWS))
    # Alpha 2021: 600/1000 yards, 6/10 TDs -> (0.6 + 0.6)/2
    assert dom[(2021, "1")] == pytest.approx(0.6)
    assert dom[(2021, "2")] == pytest.approx(0.4)


def test_dominator_is_not_the_usage_proxy():
    """usage.overall is share of plays involved in — a different quantity.
    Swapping one for the other silently would be a semantic bug, not a fix."""
    dom = cf.dominators(cf.pivot_season_stats(ROWS))
    assert dom[(2021, "1")] != pytest.approx(0.5)   # not a play-share number


def test_team_with_no_receiving_tds_uses_yard_share_alone():
    """Averaging in a fake 0 TD share would halve every dominator on the roster."""
    rows = [stat(2021, "1", "A", "T", "YDS", 800),
            stat(2021, "2", "B", "T", "YDS", 200)]
    dom = cf.dominators(cf.pivot_season_stats(rows))
    assert dom[(2021, "1")] == pytest.approx(0.8)


def test_pivot_ignores_junk_rows():
    p = cf.pivot_season_stats([None, "x", {}, {"season": "abc", "playerId": "1"}])
    assert p == {}


# ------------------------------------------------------------- breakout ----
def test_age_in_season():
    assert cf.age_in_season("2003-09-01", 2022) == pytest.approx(19.0, abs=0.05)
    assert cf.age_in_season(None, 2022) is None
    assert cf.age_in_season("not-a-date", 2022) is None
    assert cf.age_in_season("0001-01-01", 2022) is None


def test_breakout_takes_the_earliest_qualifying_season():
    """The point is *when* dominance first happened; a bigger later season must
    not overwrite it."""
    dom = {(2021, "1"): 0.30, (2022, "1"): 0.55}
    ages = cf.breakout_ages(dom, {}, {"1": "2002-06-15"}, {"1": "WR"})
    assert ages["1"] == pytest.approx(cf.age_in_season("2002-06-15", 2021))


def test_breakout_thresholds_are_position_specific():
    dom = {(2021, "1"): 0.17, (2021, "2"): 0.17}
    ages = cf.breakout_ages(dom, {}, {"1": "2002-01-01", "2": "2002-01-01"},
                            {"1": "WR", "2": "TE"})
    assert "1" not in ages          # 0.17 < WR threshold 0.20
    assert "2" in ages              # 0.17 > TE threshold 0.15


def test_breakout_skips_players_without_a_usable_birthdate():
    ages = cf.breakout_ages({(2021, "1"): 0.9}, {}, {}, {"1": "WR"})
    assert ages == {}


# --------------------------------------------------------------- bridge ----
NFLVERSE = [{"season": "2021", "pick": "5", "cfb_player_id": "jamarr-chase-1"},
            {"season": "2021", "pick": "65", "cfb_player_id": "andre-cisco-1"},
            {"season": "2020", "pick": "5", "cfb_player_id": "tua-1"}]
CFBD = [{"collegeAthleteId": "111", "year": 2021, "overall": 5},
        {"collegeAthleteId": "222", "year": 2021, "overall": 65},
        {"collegeAthleteId": "333", "year": 2021, "overall": 999}]


def test_bridge_joins_on_year_and_overall_pick_exactly():
    """No names anywhere — a draft slot is unique by construction."""
    bridge, rep = cf.draft_bridge(CFBD, NFLVERSE)
    assert bridge == {"111": "jamarr-chase-1", "222": "andre-cisco-1"}
    assert rep["cfbd_picks"] == 3 and rep["bridged"] == 2


def test_bridge_does_not_cross_draft_years():
    bridge, _ = cf.draft_bridge([{"collegeAthleteId": "9", "year": 2020, "overall": 5}],
                                NFLVERSE)
    assert bridge == {"9": "tua-1"}


def test_bridge_accepts_snake_case_alias():
    bridge, _ = cf.draft_bridge([{"college_athlete_id": "111", "year": 2021,
                                  "overall": 5}], NFLVERSE)
    assert bridge == {"111": "jamarr-chase-1"}


def test_bridge_tolerates_junk():
    bridge, rep = cf.draft_bridge([None, {}, {"collegeAthleteId": "1"}], NFLVERSE)
    assert bridge == {} and rep["bridged"] == 0


# ------------------------------------------------------------- assembly ----
def test_build_college_features_emits_the_shape_build_rows_expects():
    dom = {(2021, "111"): 0.35, (2019, "111"): 0.50}
    out, rep = cf.build_college_features(dom, {"111": 19.2}, {"111": 0.98},
                                         {"111": "jamarr-chase-1"})
    assert set(out) == {"jamarr-chase-1"}
    assert out["jamarr-chase-1"]["dominator"] == pytest.approx(0.50)  # best season
    assert out["jamarr-chase-1"]["breakout"] == 19.2
    assert rep["emitted"] == 1 and rep["with_dominator"] == 1


def test_build_college_features_uses_best_season_not_last():
    out, _ = cf.build_college_features({(2022, "1"): 0.20, (2020, "1"): 0.45},
                                       {}, {}, {"1": "slug-1"})
    assert out["slug-1"]["dominator"] == pytest.approx(0.45)


def test_unbridged_players_are_omitted_entirely():
    out, _ = cf.build_college_features({(2021, "999"): 0.9}, {}, {}, {})
    assert out == {}


# ------------------------------------------- devy regressions (audit fixes) --
def _usage_row(name, team, aid=None, overall=0.25, pos="WR"):
    row = {"name": name, "team": team, "position": pos, "usage": {"overall": overall}}
    if aid:
        row["id"] = aid
    return row


def test_devy_joins_recruits_on_cfbd_id_not_names():
    idx = dv.index_recruits([{"athleteId": "777", "name": "Totally Different",
                              "committedTo": "Elsewhere", "year": 2025, "stars": 5}])
    out, rep = dv.build_board([_usage_row("Bright Guy", "Ohio State", aid="777")],
                              idx, season=2026)
    assert rep["matched_by_id"] == 1
    assert out[0].stars == 5          # matched despite name/school disagreeing


def test_devy_does_not_index_recruits_under_their_high_school():
    """`school` is the HIGH school; indexing by it let a 'Miami' high school
    shadow the real Miami commit."""
    idx = dv.index_recruits([{"name": "Kid", "school": "Miami High",
                              "year": 2025, "stars": 4}])
    assert not any(k.endswith("|miamihigh") for k in idx)


def test_unmatched_pedigree_is_imputed_to_the_median_not_zero():
    """A join miss used to score below a 2-star, cutting a real prospect's score
    by more than half. Missing is not the same as bad."""
    idx = dv.index_recruits([
        {"name": "Known One", "committedTo": "State", "year": 2025, "rating": 0.90},
        {"name": "Known Two", "committedTo": "State", "year": 2025, "rating": 0.90}])
    rows = [_usage_row("Known One", "State"), _usage_row("Known Two", "State"),
            _usage_row("Unknown Guy", "State")]
    out, rep = dv.build_board(rows, idx, season=2026)
    unknown = [p for p in out if p.name == "Unknown Guy"][0]
    assert unknown.rating == pytest.approx(rep["imputed_rating"])
    assert unknown.rating > dv._stars_to_rating(2)     # no longer worst-in-class


def test_skill_bands_are_actually_reachable():
    """The old floors (60/50) were above the max achievable score for WR/RB/TE
    (~47.6), so the top two tiers silently counted only quarterbacks."""
    best = dv.Prospect(name="Max", position="WR", team="T", conference="C",
                       usage=0.35, season=2026, recruit_year=2026, rating=0.98)
    assert best.score >= dv.SKILL_BANDS[0][1]


def test_qb_and_skill_get_different_bands():
    assert dv.bands_for("QB") == dv.QB_BANDS
    assert dv.bands_for("WR") == dv.SKILL_BANDS
    qb = dv.Prospect(name="Q", position="QB", team="T", conference="C",
                     usage=0.55, season=2026, recruit_year=2025, rating=0.90)
    wr = dv.Prospect(name="W", position="WR", team="T", conference="C",
                     usage=0.30, season=2026, recruit_year=2025, rating=0.90)
    proj = dv.class_projection([qb, wr])
    # both land in a band; neither position is structurally locked out
    assert proj[2028]["n"] == 2
    assert sum(proj[2028][b] for b, _ in dv.SKILL_BANDS) >= 1
