"""Hermetic tests for the movers / draft-class maths. No network."""
from __future__ import annotations

import pytest

from dynasty_tool.analysis import movers as mv
from dynasty_tool.ingest.movers_client import parse_ktc


def row(fp, name, pos, team, val, year=2023):
    return {"fp_id": fp, "player": name, "pos": pos, "team": team,
            "value_1qb": str(val), "draft_year": str(year)}


NOW = [row("1", "Kenneth Walker III", "RB", "KC", 5387),
       row("2", "Christian McCaffrey", "RB", "SF", 4381),
       row("3", "Rookie Guy", "WR", "NYJ", 3000, year=2026),
       row("4", "Deep Bench", "TE", "LV", 300)]
THEN = [row("1", "Kenneth Walker III", "RB", "SEA", 4678),
        row("2", "Christian McCaffrey", "RB", "SF", 4858),
        row("4", "Deep Bench", "TE", "LV", 260),
        row("9", "Retired Guy", "QB", "FA", 2000)]


def test_diff_ranks_risers_first():
    out = mv.diff_values(NOW, THEN)
    assert out[0].name == "Kenneth Walker III"
    assert out[0].delta == pytest.approx(709)
    assert out[0].pct == pytest.approx(709 / 4678 * 100)
    assert out[-1].name == "Christian McCaffrey"
    assert out[-1].delta == pytest.approx(-477)


def test_players_missing_from_either_side_are_skipped():
    """A first-appearing rookie is not a riser and a departing player is not a
    faller — treating either as a move from zero would dominate the board."""
    names = {m.name for m in mv.diff_values(NOW, THEN)}
    assert "Rookie Guy" not in names
    assert "Retired Guy" not in names


def test_deep_bench_noise_is_filtered():
    assert "Deep Bench" not in {m.name for m in mv.diff_values(NOW, THEN)}
    assert "Deep Bench" in {m.name for m in mv.diff_values(NOW, THEN, min_value=0)}


def test_zero_old_value_never_divides():
    rows_now = [row("7", "Zero", "WR", "NYJ", 4000)]
    rows_then = [row("7", "Zero", "WR", "NYJ", 0)]
    assert mv.diff_values(rows_now, rows_then) == []


def test_top_movers_splits_and_caps():
    risers, fallers = mv.top_movers(mv.diff_values(NOW, THEN), n=1)
    assert [m.name for m in risers] == ["Kenneth Walker III"]
    assert [m.name for m in fallers] == ["Christian McCaffrey"]


def test_top_movers_by_pct_reranks():
    rows_now = [row("1", "Big", "RB", "KC", 9000), row("2", "Small", "WR", "NYJ", 1200)]
    rows_then = [row("1", "Big", "RB", "KC", 8500), row("2", "Small", "WR", "NYJ", 800)]
    by_pts, _ = mv.top_movers(mv.diff_values(rows_now, rows_then), n=1)
    by_pct, _ = mv.top_movers(mv.diff_values(rows_now, rows_then), n=1, by_pct=True)
    assert by_pts[0].name == "Big" and by_pct[0].name == "Small"


def test_tier_boundaries():
    assert mv.tier_of(9000) == "super elite"
    assert mv.tier_of(8000) == "super elite"
    assert mv.tier_of(7999) == "elite"
    assert mv.tier_of(100) == "depth"


def test_class_strength_counts_tiers_and_keeps_top_names():
    rows = [row("1", "A", "WR", "CIN", 9000, 2021),
            row("2", "B", "RB", "ATL", 6500, 2021),
            row("3", "C", "TE", "CHI", 1000, 2021),
            row("4", "D", "QB", "BUF", 8500, 2022)]
    s = mv.class_strength(rows)
    assert s[2021]["super elite"] == 1 and s[2021]["elite"] == 1
    assert s[2021]["depth"] == 1 and s[2021]["n"] == 3
    assert s[2021]["top"][0] == "A"
    assert s[2022]["super elite"] == 1


def test_class_baseline_excludes_the_unsettled_recent_classes():
    strength = {y: {"super elite": 2, "elite": 1, "very good": 1, "good": 1,
                    "depth": 1, "n": 20} for y in (2019, 2020, 2021)}
    strength[2022] = {"super elite": 0, "elite": 0, "very good": 0, "good": 0,
                      "depth": 30, "n": 30}
    strength[2023] = dict(strength[2022])
    base = mv.class_baseline(strength, exclude_recent=2)
    assert base["super elite"] == 2.0        # the two raw classes were excluded


def test_class_baseline_handles_empty():
    assert mv.class_baseline({}) == {}


def test_pick_market_reads_generic_future_labels_only():
    picks = [{"player": "2026 Pick 1.01", "ecr_1qb": "20"},
             {"player": "2027 Early 1st", "ecr_1qb": "39"},
             {"player": "2027 Mid 1st", "ecr_1qb": "72"},
             {"player": "2028 Mid 1st", "ecr_1qb": "90"}]
    m = mv.pick_market(picks)
    assert "2026" not in m                    # slot-level rows are not class signal
    assert m["2027"]["early 1st"] == 39
    assert m["2028"]["mid 1st"] == 90


def test_class_premium_prices_years_against_the_cheapest():
    m = {"2027": {"mid 1st": 72.0}, "2028": {"mid 1st": 90.0}}
    p = mv.class_premium(m)
    assert p["2028"] == 1.0                   # cheapest class is the baseline
    assert p["2027"] == pytest.approx(1.25, abs=0.01)


# ------------------------------------------------------------------ KTC ----
KTC_HTML = """<html><body><script>
var playersArray = [
 {"playerName":"Ja'Marr Chase","playerID":1,"position":"WR","team":"CIN",
  "oneQBValues":{"value":9800,"overallTrend":250},
  "superflexValues":{"value":9100,"overallTrend":180}},
 {"playerName":"Deep Guy","playerID":2,"position":"TE","team":"LV",
  "oneQBValues":{"value":300,"overallTrend":-20},
  "superflexValues":{"value":280,"overallTrend":-10}},
 {"playerName":"Faller","playerID":3,"position":"RB","team":"SF",
  "oneQBValues":{"value":4381,"overallTrend":-477},
  "superflexValues":{"value":4000,"overallTrend":-400}}
];
</script></body></html>
"""


def test_parse_ktc_extracts_the_board():
    players = parse_ktc(KTC_HTML)
    assert len(players) == 3
    assert players[0]["playerName"] == "Ja'Marr Chase"


def test_parse_ktc_returns_empty_when_the_page_shape_changes():
    assert parse_ktc("<html>no board here</html>") == []
    assert parse_ktc("") == []


def test_parse_ktc_survives_broken_json():
    assert parse_ktc("var playersArray = [ {bad json ;") == []


def test_ktc_movers_uses_the_published_trend():
    m = mv.ktc_movers(parse_ktc(KTC_HTML))
    assert m[0].name == "Ja'Marr Chase"
    assert m[0].delta == pytest.approx(250)
    assert m[0].old == pytest.approx(9550)     # value - trend
    assert m[-1].name == "Faller" and m[-1].delta == pytest.approx(-477)
    assert "Deep Guy" not in {x.name for x in m}    # below min_value


def test_ktc_movers_superflex_band():
    m = mv.ktc_movers(parse_ktc(KTC_HTML), superflex=True)
    assert m[0].delta == pytest.approx(180)


def test_ktc_movers_tolerates_junk_rows():
    assert mv.ktc_movers([None, "x", {}, {"oneQBValues": "nope"}]) == []
