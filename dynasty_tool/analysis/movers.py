"""Pure movement maths: who's gaining and losing dynasty value, and how strong a
future draft class looks. No network, no Streamlit.

Two value lenses, deliberately kept separate rather than blended, because they
measure different things and a blended number would hide which one moved:

* **DynastyProcess** — FantasyPros expert consensus, refreshed weekly. Slow,
  considered, and the same scale the Trade Calculator already prices trades on,
  so a riser here is directly actionable in a trade.
* **KeepTradeCut** — crowd trade votes, moving continuously. This is the
  "dynasty managers are getting bullish/bearish" signal; KTC publishes its own
  30-day trend per player, which is used directly when present rather than
  re-derived.

Neither is authoritative on its own. Shown side by side, disagreement is the
interesting part: the crowd moving before the experts is exactly when a buy or
sell window is open.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

# Value tiers for describing a draft class. Thresholds are on the DynastyProcess
# 1QB scale, where an elite young WR sits near 8-10k and a startable piece ~2k.
TIERS: tuple[tuple[str, float], ...] = (
    ("super elite", 8000.0),
    ("elite", 6000.0),
    ("very good", 4000.0),
    ("good", 2000.0),
    ("depth", 0.0),
)


@dataclass(frozen=True)
class Mover:
    key: str            # fp_id (DynastyProcess) or ktc id
    name: str
    pos: str
    team: str
    old: float
    new: float

    @property
    def delta(self) -> float:
        return self.new - self.old

    @property
    def pct(self) -> float:
        return (self.new - self.old) / self.old * 100.0 if self.old else 0.0


def _fnum(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def diff_values(now_rows: Iterable[dict], then_rows: Iterable[dict],
                value_col: str = "value_1qb", key_col: str = "fp_id",
                min_value: float = 500.0) -> list[Mover]:
    """Two value snapshots -> movers, biggest gain first.

    Players missing from either side are skipped rather than treated as a move
    from zero: a rookie appearing for the first time is not a riser, and a
    retiring player dropping out is not a faller. ``min_value`` filters the deep
    bench, where a 40-point wobble is 100% and pure noise.
    """
    then = {}
    for r in then_rows:
        k = str(r.get(key_col) or "").strip()
        if k:
            then[k] = r
    out: list[Mover] = []
    for r in now_rows:
        k = str(r.get(key_col) or "").strip()
        prev = then.get(k)
        if not k or prev is None:
            continue
        old, new = _fnum(prev.get(value_col)), _fnum(r.get(value_col))
        if old < min_value and new < min_value:
            continue
        if old <= 0:
            continue
        out.append(Mover(key=k, name=str(r.get("player") or ""),
                         pos=str(r.get("pos") or ""), team=str(r.get("team") or ""),
                         old=old, new=new))
    out.sort(key=lambda m: m.delta, reverse=True)
    return out


def top_movers(movers: Sequence[Mover], n: int = 15,
               by_pct: bool = False) -> tuple[list[Mover], list[Mover]]:
    """(risers, fallers). ``by_pct`` ranks by percentage rather than raw points —
    useful for spotting movement among mid-tier assets that raw points buries
    under the top of the board."""
    key = (lambda m: m.pct) if by_pct else (lambda m: m.delta)
    ranked = sorted(movers, key=key, reverse=True)
    risers = [m for m in ranked if key(m) > 0][:n]
    fallers = [m for m in reversed(ranked) if key(m) < 0][:n]
    return risers, fallers


def tier_of(value: float, tiers: Sequence[tuple[str, float]] = TIERS) -> str:
    for name, floor in tiers:
        if value >= floor:
            return name
    return tiers[-1][0]


def class_strength(rows: Iterable[dict], value_col: str = "value_1qb",
                   tiers: Sequence[tuple[str, float]] = TIERS) -> dict[int, dict]:
    """{draft_year: {tier: count, ...,'n': total, 'top': [names]}}.

    This is *hindsight*: what each class has actually produced, priced today.
    It is the honest baseline for "how good is a normal class", which is what
    makes a projection for an undrafted class meaningful rather than arbitrary.
    """
    out: dict[int, dict] = {}
    for r in rows:
        try:
            year = int(float(r.get("draft_year")))
        except (TypeError, ValueError):
            continue
        v = _fnum(r.get(value_col))
        b = out.setdefault(year, {t: 0 for t, _ in tiers})
        b["n"] = b.get("n", 0) + 1
        b[tier_of(v, tiers)] += 1
        b.setdefault("_top", []).append((v, str(r.get("player") or "")))
    for b in out.values():
        b["top"] = [n for _v, n in sorted(b.pop("_top", []), reverse=True)[:5]]
    return out


def class_baseline(strength: dict[int, dict], exclude_recent: int = 2,
                   tiers: Sequence[tuple[str, float]] = TIERS) -> dict[str, float]:
    """Average tier counts for a 'normal' class.

    The two most recent classes are excluded by default: they have not had time
    to sort themselves out, so counting them would drag the elite average down
    and make every incoming class look strong by comparison.
    """
    if not strength:
        return {}
    years = sorted(strength)[:-exclude_recent] if exclude_recent else sorted(strength)
    years = [y for y in years if strength[y].get("n", 0) >= 10]
    if not years:
        return {}
    return {t: round(sum(strength[y].get(t, 0) for y in years) / len(years), 1)
            for t, _ in tiers}


# ---------------------------------------------------------------------------
# future-class pricing
# ---------------------------------------------------------------------------
def pick_market(pick_rows: Iterable[dict], ecr_col: str = "ecr_1qb") -> dict[str, dict]:
    """{'2027': {'early 1st': ecr, ...}} from the DynastyProcess picks file.

    Lower ECR is better. Future classes are priced generically ("2027 Early 1st")
    rather than by slot, so the *shape* of that curve is the market's opinion of
    the class — which is the only forward-looking class signal available without
    a paid scouting feed.
    """
    import re
    out: dict[str, dict] = {}
    for r in pick_rows:
        label = str(r.get("player") or "")
        m = re.match(r"^(\d{4})\s+(.*)$", label.strip())
        if not m:
            continue
        year, rest = m.group(1), m.group(2).strip().lower()
        if re.match(r"^pick\s", rest):      # "2026 Pick 1.01" -> slot-level
            continue
        out.setdefault(year, {})[rest] = _fnum(r.get(ecr_col))
    return out


def class_premium(market: dict[str, dict], slot: Optional[str] = None) -> dict[str, float]:
    """How each future year's pick is priced relative to the cheapest year.

    A ratio, so it reads as "2027 1sts cost 1.2x what 2028 1sts cost". Higher
    means the market is paying up for that class.

    The slot is chosen from those every year actually has. DynastyProcess prices
    the nearest future class by early/mid/late but the year beyond it only
    generically, so hard-coding "mid 1st" left exactly one year with a value and
    rendered a meaningless 1.00x against itself — comparing a class to nothing.
    """
    if not market:
        return {}
    if slot:
        candidates = [slot]
    else:
        common = set.intersection(*(set(b) for b in market.values())) if market else set()
        # Prefer a first-rounder; that is the pick a class premium is really about.
        order = ["mid 1st", "1st", "early 1st", "late 1st", "mid 2nd", "2nd"]
        candidates = [s for s in order if s in common] or sorted(common)
    for cand in candidates:
        vals = {y: b.get(cand) for y, b in market.items() if b.get(cand)}
        if len(vals) >= 2:
            worst = max(vals.values())      # highest ECR == cheapest class
            return {y: round(worst / v, 3) for y, v in vals.items() if v}
    return {}


# ---------------------------------------------------------------------------
# KeepTradeCut
# ---------------------------------------------------------------------------
def ktc_movers(players: Iterable[dict], superflex: bool = False,
               min_value: float = 500.0) -> list[Mover]:
    """KTC's own player blobs -> movers, using KTC's published trend.

    KTC ships ``overallTrend`` (its 30-day change) alongside the current value,
    so the trend is read rather than re-derived — no snapshot history needed,
    and it matches what the site itself shows.
    """
    band = "superflexValues" if superflex else "oneQBValues"
    out: list[Mover] = []
    for p in players or []:
        if not isinstance(p, dict):
            continue
        vals = p.get(band) or {}
        if not isinstance(vals, dict):
            continue
        new = _fnum(vals.get("value"))
        if new < min_value:
            continue
        trend = _fnum(vals.get("overallTrend"))
        out.append(Mover(key=str(p.get("playerID") or p.get("slug") or p.get("playerName")),
                         name=str(p.get("playerName") or ""),
                         pos=str(p.get("position") or ""),
                         team=str(p.get("team") or ""),
                         old=new - trend, new=new))
    out.sort(key=lambda m: m.delta, reverse=True)
    return out
