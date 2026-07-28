"""A calibrated rookie/devy hit-rate model — the ZAP-style piece.

Replaces hand-picked weights with weights *learned from what actually happened*
to 1,200+ skill players drafted since 2010. Everything joins on IDs
(``cfb_player_id`` / ``pfr_id``), never on names, which is the join discipline
the rest of this codebase already insists on.

Two models come out of the same pipeline, because they answer different
questions and one of them is useless without the other:

* **Rookie** — includes draft capital. Draft capital is far and away the
  strongest single predictor of NFL fantasy production, so any post-draft model
  that ignores it is lying to you.
* **Devy** — deliberately *excludes* draft capital, because a college player
  hasn't been drafted yet. This is what you use to decide who to stash.

Comparing the two is itself informative: a prospect the devy model likes who the
rookie model then downgrades is someone the NFL disagreed with you about.

Implementation is a hand-rolled logistic regression on numpy (already a
dependency) rather than scikit-learn. Three reasons: no new dependency for
Streamlit Cloud to fail on, ~1,200 rows does not need anything heavier, and
logistic coefficients are *readable* — the point of a model like this is being
able to see what it weights, not to be a black box.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

import numpy as np

# "Hit" thresholds in yards per game, applied over a career with a minimum
# sample. These are fantasy-relevance lines, not greatness lines: the question
# is "did this pick ever give me a startable player", which is the decision a
# dynasty manager actually makes on draft night.
HIT_YPG = {"WR": 45.0, "TE": 30.0, "RB": 55.0, "QB": 175.0}
MIN_GAMES = 16          # at least a full season's worth before judging
SEASONS_TO_SETTLE = 3   # the last N draft classes are too raw to train on

ROOKIE_FEATURES = ("draft_capital", "age", "forty", "bmi", "vertical",
                   "broad_jump", "dominator", "breakout")
DEVY_FEATURES = ("age", "forty", "bmi", "vertical", "broad_jump",
                 "dominator", "breakout", "pedigree")


@dataclass
class FitResult:
    features: tuple[str, ...]
    weights: np.ndarray          # standardized-space coefficients
    intercept: float
    mu: np.ndarray
    sigma: np.ndarray
    n: int
    auc: float
    base_rate: float

    def importance(self) -> list[tuple[str, float]]:
        """Coefficients as shares of total absolute weight — 'what the model
        learned', in the only units a human can read at a glance."""
        mag = np.abs(self.weights)
        total = float(mag.sum()) or 1.0
        pairs = [(f, round(float(m) / total, 3)) for f, m in zip(self.features, mag)]
        return sorted(pairs, key=lambda kv: kv[1], reverse=True)

    def predict(self, X: np.ndarray) -> np.ndarray:
        z = (np.asarray(X, dtype=float) - self.mu) / self.sigma
        return _sigmoid(z @ self.weights + self.intercept)


def _sigmoid(z: np.ndarray) -> np.ndarray:
    # Clipped so a large |z| can't overflow exp and produce nan gradients.
    return 1.0 / (1.0 + np.exp(-np.clip(z, -35.0, 35.0)))


def _fnum(v, default: float = np.nan) -> float:
    try:
        f = float(v)
        return f if np.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def draft_capital(overall_pick) -> float:
    """Pick number -> a 0-1 value that falls off the way real draft value does.

    Linear pick number would say pick 1 and pick 33 are nearly the same distance
    apart as 200 and 232, which is badly wrong. The log transform matches the
    shape of every published draft-value curve.
    """
    p = _fnum(overall_pick, np.nan)
    if not np.isfinite(p) or p <= 0:
        return 0.0
    return float(max(0.0, 1.0 - np.log(p) / np.log(262.0)))


def hit_label(position: str, games, yards) -> Optional[int]:
    """1/0, or None when there isn't enough career to judge."""
    pos = str(position or "").upper()
    if pos not in HIT_YPG:
        return None
    # games defaults to NaN, not 0: an unknown career must stay *unlabelled* and
    # drop out of training. Defaulting to 0 would silently relabel every player
    # with missing data as a confirmed miss and poison the training set.
    g, y = _fnum(games, np.nan), _fnum(yards, 0.0)
    if not np.isfinite(g):
        return None
    if g < MIN_GAMES:
        return 0
    return int((y / g) >= HIT_YPG[pos])


# ---------------------------------------------------------------------------
# feature assembly
# ---------------------------------------------------------------------------
def _combine_index(combine_rows: Iterable[dict]) -> dict[str, dict]:
    """Keyed by cfb_id first (joins the college side) then pfr_id."""
    out: dict[str, dict] = {}
    for r in combine_rows or []:
        if not isinstance(r, dict):
            continue
        for k in ("cfb_id", "pfr_id"):
            v = str(r.get(k) or "").strip()
            if v:
                out.setdefault(v, r)
    return out


def _athletic(rec: dict, weight_lbs: float) -> dict:
    ht = _fnum(rec.get("ht"))
    wt = _fnum(rec.get("wt"), weight_lbs)
    bmi = (703.0 * wt / (ht * ht)) if np.isfinite(ht) and ht > 0 and np.isfinite(wt) else np.nan
    return {"forty": _fnum(rec.get("forty")), "bmi": bmi,
            "vertical": _fnum(rec.get("vertical")),
            "broad_jump": _fnum(rec.get("broad_jump"))}


def build_rows(draft_rows: Iterable[dict], combine_rows: Iterable[dict],
               college: Optional[dict] = None,
               positions: Sequence[str] = ("WR", "RB", "TE"),
               max_season: Optional[int] = None) -> list[dict]:
    """One row per drafted skill player, with features + label.

    ``college`` is {cfb_player_id: {'dominator':…, 'breakout':…, 'pedigree':…}},
    optional: without it those features are missing and get imputed, so the model
    still trains on draft capital and athleticism alone.
    """
    comb = _combine_index(combine_rows)
    posset = {p.upper() for p in positions}
    out: list[dict] = []
    for r in draft_rows or []:
        if not isinstance(r, dict):
            continue
        pos = str(r.get("position") or "").upper()
        if pos not in posset:
            continue
        season = _fnum(r.get("season"))
        if not np.isfinite(season):
            continue
        if max_season is not None and season > max_season:
            continue
        cfb = str(r.get("cfb_player_id") or "").strip()
        yards = _fnum(r.get("rec_yards"), 0.0) + _fnum(r.get("rush_yards"), 0.0)
        label = hit_label(pos, r.get("games"), yards)
        c = (college or {}).get(cfb) or {}
        rec = comb.get(cfb) or comb.get(str(r.get("pfr_player_id") or "")) or {}
        row = {
            "cfb_id": cfb, "name": str(r.get("pfr_player_name") or ""),
            "position": pos, "season": int(season),
            "pick": _fnum(r.get("pick")), "label": label,
            "draft_capital": draft_capital(r.get("pick")),
            "age": _fnum(r.get("age")),
            "dominator": _fnum(c.get("dominator")),
            "breakout": _fnum(c.get("breakout")),
            "pedigree": _fnum(c.get("pedigree")),
        }
        row.update(_athletic(rec, _fnum(r.get("wt"), np.nan)))
        out.append(row)
    return out


def matrix(rows: Sequence[dict], features: Sequence[str]) -> np.ndarray:
    X = np.array([[_fnum(r.get(f)) for f in features] for r in rows], dtype=float)
    if X.size == 0:
        return X.reshape(0, len(features))
    # Impute missing with the column median — a missing combine number should
    # read as "average", never as zero, which would look like elite slowness.
    for j in range(X.shape[1]):
        col = X[:, j]
        good = col[np.isfinite(col)]
        col[~np.isfinite(col)] = np.median(good) if good.size else 0.0
        X[:, j] = col
    return X


# ---------------------------------------------------------------------------
# logistic regression
# ---------------------------------------------------------------------------
def fit(rows: Sequence[dict], features: Sequence[str] = ROOKIE_FEATURES,
        l2: float = 1.0, iters: int = 4000, lr: float = 0.12) -> FitResult:
    """Standardized logistic regression by gradient descent with L2.

    Standardizing first is what makes the coefficients comparable — otherwise a
    40 time (~4.5) and a pick number (~1-262) would have wildly different scales
    and the 'importance' readout would be meaningless.
    """
    labelled = [r for r in rows if r.get("label") in (0, 1)]
    if len(labelled) < 30:
        raise ValueError(f"not enough labelled rows to fit ({len(labelled)})")
    feats = tuple(features)
    X = matrix(labelled, feats)
    y = np.array([float(r["label"]) for r in labelled])

    mu, sigma = X.mean(axis=0), X.std(axis=0)
    sigma[sigma == 0] = 1.0
    Z = (X - mu) / sigma

    w = np.zeros(Z.shape[1])
    b = 0.0
    n = float(len(y))
    for _ in range(int(iters)):
        p = _sigmoid(Z @ w + b)
        err = p - y
        w -= lr * ((Z.T @ err) / n + l2 * w / n)
        b -= lr * float(err.mean())

    res = FitResult(features=feats, weights=w, intercept=b, mu=mu, sigma=sigma,
                    n=len(y), auc=0.0, base_rate=float(y.mean()))
    res.auc = auc(y, res.predict(X))
    return res


def auc(y_true: np.ndarray, p: np.ndarray) -> float:
    """Rank-based AUC (Mann-Whitney), ties handled by average rank.

    Reported honestly: fit here is in-sample unless the caller holds seasons
    out, and ``fit_holdout`` exists precisely so the number you show is not a
    self-graded exam.
    """
    y = np.asarray(y_true, dtype=float)
    p = np.asarray(p, dtype=float)
    pos, neg = y == 1, y == 0
    n_pos, n_neg = int(pos.sum()), int(neg.sum())
    if n_pos == 0 or n_neg == 0:
        return 0.5
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(1, len(p) + 1, dtype=float)
    # average ranks within ties
    srt = p[order]
    i = 0
    while i < len(srt):
        j = i
        while j + 1 < len(srt) and srt[j + 1] == srt[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + j + 2) / 2.0
        i = j + 1
    return float((ranks[pos].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def fit_holdout(rows: Sequence[dict], features: Sequence[str] = ROOKIE_FEATURES,
                holdout_seasons: int = 4, **kw) -> tuple[FitResult, float]:
    """(model fit on older seasons, AUC on the held-out recent ones).

    Splitting by season rather than at random is the point: a random split leaks
    future information, and the real task is always "predict a class you have
    not seen".
    """
    labelled = [r for r in rows if r.get("label") in (0, 1)]
    seasons = sorted({int(r["season"]) for r in labelled})
    if len(seasons) <= holdout_seasons:
        f = fit(labelled, features, **kw)
        return f, f.auc
    cut = seasons[-holdout_seasons]
    train = [r for r in labelled if int(r["season"]) < cut]
    test = [r for r in labelled if int(r["season"]) >= cut]
    model = fit(train, features, **kw)
    if not test:
        return model, model.auc
    y = np.array([float(r["label"]) for r in test])
    return model, auc(y, model.predict(matrix(test, model.features)))


def score_prospects(model: FitResult, rows: Sequence[dict]) -> list[dict]:
    """Rows -> [{name, position, prob, contributions}] sorted by probability.

    Per-player contributions are the standardized feature times its weight, so
    you can see *why* the model likes someone, not just that it does.
    """
    if not rows:
        return []
    X = matrix(rows, model.features)
    Z = (X - model.mu) / model.sigma
    probs = _sigmoid(Z @ model.weights + model.intercept)
    out = []
    for r, z, p in zip(rows, Z, probs):
        contrib = {f: round(float(zi * wi), 3)
                   for f, zi, wi in zip(model.features, z, model.weights)}
        out.append({"name": r.get("name"), "position": r.get("position"),
                    "season": r.get("season"), "pick": r.get("pick"),
                    "prob": round(float(p), 4), "contributions": contrib})
    out.sort(key=lambda d: d["prob"], reverse=True)
    return out
