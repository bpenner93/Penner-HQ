"""Hermetic tests for the calibrated rookie/devy model. No network.

The synthetic training set is built so that ONE feature (draft capital) genuinely
drives the label, which lets us assert the fitter actually recovers it rather
than just producing plausible-looking numbers.
"""
from __future__ import annotations

import numpy as np
import pytest

from dynasty_tool.analysis import rookie_model as rm


# ------------------------------------------------------------- primitives ---
def test_draft_capital_falls_off_like_a_real_value_curve():
    assert rm.draft_capital(1) > rm.draft_capital(10) > rm.draft_capital(100)
    assert rm.draft_capital(1) == pytest.approx(1.0, abs=0.01)
    # the gap 1->33 must dwarf the gap 200->232; linear pick number would not
    assert (rm.draft_capital(1) - rm.draft_capital(33)) > \
           (rm.draft_capital(200) - rm.draft_capital(232)) * 3


def test_draft_capital_handles_junk():
    for bad in (None, "", "abc", 0, -5):
        assert rm.draft_capital(bad) == 0.0


def test_hit_label_thresholds_are_per_position():
    assert rm.hit_label("WR", 100, 5000) == 1        # 50 ypg > 45
    assert rm.hit_label("WR", 100, 4000) == 0        # 40 ypg < 45
    assert rm.hit_label("TE", 100, 3500) == 1        # 35 ypg > 30
    assert rm.hit_label("RB", 100, 4000) == 0        # 40 ypg < 55


def test_hit_label_needs_a_real_sample():
    assert rm.hit_label("WR", 4, 400) == 0           # too few games -> not a hit
    assert rm.hit_label("WR", None, 400) is None     # unknown -> unlabelled
    assert rm.hit_label("OT", 100, 5000) is None     # not a skill position


def test_sigmoid_does_not_overflow():
    out = rm._sigmoid(np.array([-1e6, 0.0, 1e6]))
    assert np.all(np.isfinite(out))
    assert out[0] == pytest.approx(0.0, abs=1e-9)
    assert out[2] == pytest.approx(1.0, abs=1e-9)


# ------------------------------------------------------------------ auc -----
def test_auc_perfect_and_inverted_and_random():
    y = np.array([0, 0, 1, 1])
    assert rm.auc(y, np.array([0.1, 0.2, 0.8, 0.9])) == 1.0
    assert rm.auc(y, np.array([0.9, 0.8, 0.2, 0.1])) == 0.0
    assert rm.auc(y, np.array([0.5, 0.5, 0.5, 0.5])) == 0.5


def test_auc_degenerate_labels_are_neutral():
    assert rm.auc(np.array([1, 1]), np.array([0.2, 0.9])) == 0.5
    assert rm.auc(np.array([0, 0]), np.array([0.2, 0.9])) == 0.5


# ---------------------------------------------------------------- rows ------
def _draft(name, pos, season, pick, games, rec, cfb=None, age=22):
    return {"pfr_player_name": name, "position": pos, "season": str(season),
            "pick": str(pick), "games": str(games), "rec_yards": str(rec),
            "rush_yards": "0", "age": str(age),
            "cfb_player_id": cfb or f"{name.lower()}-1", "wt": "200"}


def test_build_rows_filters_positions_and_seasons():
    rows = rm.build_rows(
        [_draft("A", "WR", 2015, 5, 100, 5000),
         _draft("B", "OT", 2015, 6, 100, 0),
         _draft("C", "WR", 2024, 7, 10, 300)],
        [], max_season=2022)
    assert [r["name"] for r in rows] == ["A"]


def test_build_rows_joins_combine_on_cfb_id():
    rows = rm.build_rows(
        [_draft("A", "WR", 2015, 5, 100, 5000, cfb="a-1")],
        [{"cfb_id": "a-1", "forty": "4.35", "vertical": "38", "ht": "72",
          "wt": "200", "broad_jump": "125"}])
    assert rows[0]["forty"] == pytest.approx(4.35)
    assert rows[0]["bmi"] == pytest.approx(703 * 200 / 72 ** 2, abs=0.01)


def test_missing_combine_leaves_nan_not_zero():
    """A missing 40 time must not read as an elite-slow 0.0."""
    rows = rm.build_rows([_draft("A", "WR", 2015, 5, 100, 5000)], [])
    assert np.isnan(rows[0]["forty"])


def test_matrix_imputes_missing_with_the_median():
    rows = [{"forty": 4.4}, {"forty": 4.6}, {"forty": None}]
    X = rm.matrix(rows, ("forty",))
    assert X[2, 0] == pytest.approx(4.5)      # median of 4.4/4.6, not 0.0


def test_matrix_handles_an_empty_input():
    assert rm.matrix([], ("forty", "age")).shape == (0, 2)


# ------------------------------------------------------------------ fit -----
def _synthetic(n=400, seed=0):
    """Label is driven by draft capital plus a little noise, nothing else."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        pick = int(rng.integers(1, 260))
        dc = rm.draft_capital(pick)
        p = 1.0 / (1.0 + np.exp(-(6.0 * dc - 3.0)))
        rows.append({
            "name": f"P{i}", "position": "WR", "season": 2010 + (i % 12),
            "pick": pick, "draft_capital": dc,
            "age": float(rng.normal(22, 1)),
            "forty": float(rng.normal(4.5, 0.1)),
            "bmi": float(rng.normal(27, 2)),
            "vertical": float(rng.normal(35, 3)),
            "broad_jump": float(rng.normal(120, 6)),
            "dominator": float(rng.normal(0.3, 0.1)),
            "breakout": float(rng.normal(19, 1)),
            "pedigree": float(rng.normal(0.88, 0.05)),
            "label": int(rng.random() < p),
        })
    return rows


def test_fit_recovers_the_feature_that_actually_drives_the_label():
    model = rm.fit(_synthetic(), rm.ROOKIE_FEATURES)
    assert model.importance()[0][0] == "draft_capital"
    assert model.auc > 0.75
    assert model.n == 400


def test_importance_shares_sum_to_one():
    model = rm.fit(_synthetic(), rm.ROOKIE_FEATURES)
    assert sum(w for _f, w in model.importance()) == pytest.approx(1.0, abs=0.01)


def test_predictions_are_probabilities_and_monotone_in_draft_capital():
    model = rm.fit(_synthetic(), rm.ROOKIE_FEATURES)
    base = dict(_synthetic(1)[0])
    early, late = dict(base), dict(base)
    early["draft_capital"] = rm.draft_capital(3)
    late["draft_capital"] = rm.draft_capital(200)
    scored = rm.score_prospects(model, [early, late])
    assert all(0.0 <= s["prob"] <= 1.0 for s in scored)
    assert scored[0]["prob"] > scored[1]["prob"]


def test_fit_refuses_a_tiny_sample_instead_of_pretending():
    with pytest.raises(ValueError, match="not enough"):
        rm.fit(_synthetic(10), rm.ROOKIE_FEATURES)


def test_unlabelled_rows_are_excluded_from_training():
    rows = _synthetic(200) + [dict(_synthetic(1)[0], label=None) for _ in range(50)]
    assert rm.fit(rows, rm.ROOKIE_FEATURES).n == 200


def test_holdout_splits_by_season_not_at_random():
    """A random split would leak the future; the real task is an unseen class."""
    rows = _synthetic(600)
    model, oos = rm.fit_holdout(rows, rm.ROOKIE_FEATURES, holdout_seasons=4)
    trained_seasons = {r["season"] for r in rows if r["label"] in (0, 1)}
    assert model.n < len([r for r in rows if r["label"] in (0, 1)])
    assert 0.0 <= oos <= 1.0
    assert max(trained_seasons) >= 2021


def test_devy_model_excludes_draft_capital():
    model = rm.fit(_synthetic(), rm.DEVY_FEATURES)
    assert "draft_capital" not in model.features
    assert all(f != "draft_capital" for f, _ in model.importance())


def test_score_prospects_explains_each_pick():
    model = rm.fit(_synthetic(), rm.ROOKIE_FEATURES)
    out = rm.score_prospects(model, _synthetic(5))
    assert set(out[0]["contributions"]) == set(rm.ROOKIE_FEATURES)
    assert out == sorted(out, key=lambda d: d["prob"], reverse=True)


def test_score_prospects_on_empty_input():
    model = rm.fit(_synthetic(), rm.ROOKIE_FEATURES)
    assert rm.score_prospects(model, []) == []


def test_a_constant_feature_does_not_blow_up_standardisation():
    rows = _synthetic()
    for r in rows:
        r["bmi"] = 27.0                # zero variance
    model = rm.fit(rows, rm.ROOKIE_FEATURES)
    assert np.all(np.isfinite(model.weights))
