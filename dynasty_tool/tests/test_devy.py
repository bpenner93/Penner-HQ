"""Hermetic tests for the devy board. No network."""
from __future__ import annotations

import pytest

from dynasty_tool.analysis import devy as dv

SEASON = 2026

USAGE = [
    # a true sophomore commanding the offence -> should top the board
    {"name": "Bright Sophomore", "position": "WR", "team": "Ohio State",
     "conference": "Big Ten", "usage": {"overall": 0.30}},
    # a senior with identical usage -> heavily discounted
    {"name": "Old Compiler", "position": "WR", "team": "Ohio State",
     "conference": "Big Ten", "usage": {"overall": 0.30}},
    # unmatched pedigree, decent usage
    {"name": "Nobody Knows", "position": "RB", "team": "Troy",
     "conference": "Sun Belt", "usage": {"overall": 0.22}},
    # below the noise floor
    {"name": "Scrub Guy", "position": "WR", "team": "Troy",
     "conference": "Sun Belt", "usage": {"overall": 0.02}},
    # wrong position for devy purposes
    {"name": "Big Fella", "position": "OT", "team": "Troy",
     "conference": "Sun Belt", "usage": {"overall": 0.40}},
]

RECRUITS = [
    {"name": "Bright Sophomore", "committedTo": "Ohio State", "year": 2025,
     "stars": 5, "rating": 0.98},
    {"name": "Old Compiler", "committedTo": "Ohio State", "year": 2023,
     "stars": 3, "rating": 0.84},
]


@pytest.fixture
def board():
    return dv.build_board(USAGE, dv.index_recruits(RECRUITS), season=SEASON)


def test_early_production_outranks_identical_senior_production(board):
    prospects, _ = board
    assert prospects[0].name == "Bright Sophomore"
    soph = prospects[0]
    old = [p for p in prospects if p.name == "Old Compiler"][0]
    assert soph.usage == old.usage          # same production...
    assert soph.score > old.score           # ...but the sophomore wins


def test_low_usage_and_non_skill_positions_are_dropped(board):
    names = {p.name for p in board[0]}
    assert "Scrub Guy" not in names
    assert "Big Fella" not in names


def test_class_year_and_draft_class_derive_from_recruiting_year(board):
    soph = [p for p in board[0] if p.name == "Bright Sophomore"][0]
    assert soph.class_year == "SO"          # 2026 season, 2025 recruit
    assert soph.draft_class == 2028         # 2025 + 3


def test_unmatched_pedigree_is_neutral_not_a_bonus(board):
    unknown = [p for p in board[0] if p.name == "Nobody Knows"][0]
    assert unknown.recruit_year is None
    assert unknown.class_year == "?"
    assert unknown.draft_class is None
    assert unknown.score > 0                # still ranked on production


def test_join_report_counts_matches(board):
    _prospects, rep = board
    assert rep["players"] == 3              # after usage + position filters
    assert rep["pedigree_matched"] == 2
    assert rep["pedigree_rate"] == pytest.approx(2 / 3, abs=0.01)


def test_pedigree_join_is_name_and_school_insensitive_to_punctuation():
    usage = [{"name": "Ja'Marr O'Brien", "position": "WR", "team": "Ohio State",
              "usage": {"overall": 0.25}}]
    recruits = [{"name": "JaMarr OBrien", "committedTo": "ohio state",
                 "year": 2025, "stars": 4}]
    prospects, rep = dv.build_board(usage, dv.index_recruits(recruits), SEASON)
    assert rep["pedigree_matched"] == 1
    assert prospects[0].stars == 4


def test_stars_stand_in_when_rating_is_missing():
    usage = [{"name": "A", "position": "WR", "team": "T", "usage": {"overall": 0.2}}]
    five = dv.build_board(usage, dv.index_recruits(
        [{"name": "A", "committedTo": "T", "year": 2025, "stars": 5}]), SEASON)[0][0]
    two = dv.build_board(usage, dv.index_recruits(
        [{"name": "A", "committedTo": "T", "year": 2025, "stars": 2}]), SEASON)[0][0]
    assert five.score > two.score


def test_junk_rows_do_not_crash():
    prospects, rep = dv.build_board([None, "x", {}, {"usage": "nope"}], {}, SEASON)
    assert prospects == [] and rep["players"] == 0


def test_by_draft_class_groups_and_sorts():
    prospects, _ = dv.build_board(USAGE, dv.index_recruits(RECRUITS), SEASON)
    grouped = dv.by_draft_class(prospects)
    assert 2028 in grouped and 2026 in grouped
    assert grouped[2028][0].name == "Bright Sophomore"


def test_class_projection_bands_and_totals():
    prospects, _ = dv.build_board(USAGE, dv.index_recruits(RECRUITS), SEASON)
    proj = dv.class_projection(prospects)
    assert proj[2028]["n"] == 1
    assert sum(proj[2028][b] for b in
               ("super elite", "elite", "very good", "good")) <= proj[2028]["n"]
    assert proj[2028]["top"] == ["Bright Sophomore"]


def test_earliness_monotonically_decreases():
    vals = [dv._earliness(2026, 2026 - n) for n in range(4)]
    assert vals == sorted(vals, reverse=True)
    assert dv._earliness(2026, None) < vals[0]     # unknown is not a bonus


def test_score_is_bounded():
    p = dv.Prospect(name="X", position="WR", team="T", conference="C",
                    usage=5.0, season=2026, recruit_year=2026, rating=9.9)
    assert 0 <= p.score <= 100
