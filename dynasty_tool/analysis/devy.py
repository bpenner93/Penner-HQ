"""Pure devy maths: turn college usage + recruiting pedigree into a ranked board
of players worth holding before they reach the NFL.

The premise this ranks on is the one that actually predicts NFL dynasty value:
**early production against real competition**. A true sophomore commanding 30% of
his team's offence is a far better bet than a senior doing the same, because the
age-adjusted signal is what separates future first-rounders from college
compilers. So the board is usage share, discounted by how far along a player is,
and nudged by recruiting pedigree as a tiebreak.

What this is *not*: a scouting report. There is no free source of consensus devy
big boards, so nothing here knows about traits, injuries, or scheme. It is a
production screen — good for "who should I be asking about", not a substitute
for film.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from ..value.current_provider import _norm_name

# Draft-eligibility: a player is generally eligible three seasons after high
# school. Recruiting class 2024 -> first eligible for the 2027 NFL draft.
YEARS_TO_ELIGIBLE = 3

# How much of the board is production vs pedigree. Production dominates on
# purpose: recruiting rank is a prior that early usage has already overruled.
W_USAGE = 0.80
W_PEDIGREE = 0.20

DEVY_POSITIONS = ("QB", "RB", "WR", "TE")


@dataclass(frozen=True)
class Prospect:
    name: str
    position: str
    team: str
    conference: str
    usage: float                 # share of team offence, 0-1
    season: int
    recruit_year: Optional[int] = None
    stars: Optional[int] = None
    rating: Optional[float] = None
    prev_usage: Optional[float] = None   # same player, prior season

    @property
    def usage_delta(self) -> Optional[float]:
        """Change in offensive share year over year.

        This is the trend that matters for a devy hold: a sophomore going from
        12% to 28% of his offence is the profile that turns into draft capital,
        and it shows up a full year before any big board reflects it.
        """
        if self.prev_usage is None:
            return None
        return round(self.usage - self.prev_usage, 4)

    @property
    def trend(self) -> str:
        d = self.usage_delta
        if d is None:
            return "new"          # no prior season: a true freshman or a transfer
        if d >= 0.05:
            return "▲▲" if d >= 0.12 else "▲"
        if d <= -0.05:
            return "▼▼" if d <= -0.12 else "▼"
        return "—"

    @property
    def draft_class(self) -> Optional[int]:
        """The **earliest** draft a player can declare for, not a prediction of
        when he will.

        NCAA rules make a recruit eligible three years out of high school, so a
        2023 signee is first eligible in 2026 — but he may stay a senior and go
        in 2027, or redshirt and go in 2028. Treating this as "the class he'll
        be in" quietly overstates it, so everything downstream labels it as
        first-eligible.
        """
        return self.recruit_year + YEARS_TO_ELIGIBLE if self.recruit_year else None

    @property
    def class_year(self) -> str:
        """FR/SO/JR/SR-ish, inferred from the recruiting class."""
        if not self.recruit_year:
            return "?"
        n = self.season - self.recruit_year
        return {0: "FR", 1: "SO", 2: "JR", 3: "SR"}.get(n, f"{n}Y" if n > 0 else "?")

    @property
    def score(self) -> float:
        """0-100. Usage carries it; being early carries a multiplier."""
        early = _earliness(self.season, self.recruit_year)
        ped = self.rating if self.rating else _stars_to_rating(self.stars)
        return round(100.0 * (W_USAGE * min(self.usage, 1.0) * early
                              + W_PEDIGREE * min(ped, 1.0)), 1)


def _stars_to_rating(stars: Optional[int]) -> float:
    """Recruiting rating proxy when the composite rating is missing."""
    return {5: 0.98, 4: 0.90, 3: 0.84, 2: 0.78}.get(int(stars or 0), 0.0)


def _earliness(season: int, recruit_year: Optional[int]) -> float:
    """Production earlier in a career is worth more.

    A true freshman's usage counts fully; by year four it is heavily discounted,
    because senior production against younger competition is the classic devy
    trap. Unknown class year gets the neutral middle rather than a bonus.
    """
    if not recruit_year:
        return 0.75
    n = max(0, season - recruit_year)
    return {0: 1.0, 1: 0.92, 2: 0.78, 3: 0.60}.get(n, 0.45)


# ---------------------------------------------------------------------------
# joins
# ---------------------------------------------------------------------------
def _key(name: str, team: str) -> str:
    return f"{_norm_name(name)}|{_norm_name(team)}"


def index_recruits(recruits: Iterable[dict]) -> dict[str, dict]:
    """Recruits indexed for the pedigree join.

    Keyed two ways, in priority order:

    1. ``athleteId`` — CFBD's own athlete id, which also appears as ``id`` on
       ``/player/usage``. This is an exact join and is the primary path.
    2. ``norm(name)|norm(college)`` — the fallback, whose match rate is reported.

    The college is ``committedTo``. ``school`` is the recruit's **high school**,
    so the previous fallback to it indexed uncommitted recruits under a
    high-school name that could never match a usage team — and worse, high
    schools named "Miami", "Houston" or "Cincinnati" would shadow the real
    college entry, since the first key inserted wins.
    """
    out: dict[str, dict] = {}
    for r in recruits or []:
        if not isinstance(r, dict):
            continue
        aid = str(r.get("athleteId") or r.get("athlete_id") or "").strip()
        if aid and aid not in ("0", "None"):
            out.setdefault(f"id:{aid}", r)
        name = str(r.get("name") or "")
        college = str(r.get("committedTo") or "")
        if name and college:
            out.setdefault(_key(name, college), r)
    return out


def _median(values: Sequence[float]) -> float:
    vals = sorted(v for v in values if v and v == v)
    if not vals:
        return 0.0
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0


def index_usage(usage_rows: Iterable[dict]) -> dict[str, float]:
    """{key: overall usage} for a season, keyed by CFBD athlete id when present
    and by name+team otherwise — so a prior season can be matched even for
    players whose id is missing."""
    out: dict[str, float] = {}
    for row in usage_rows or []:
        if not isinstance(row, dict):
            continue
        u = row.get("usage")
        overall = float((u or {}).get("overall") or 0.0) if isinstance(u, dict) else 0.0
        if not overall:
            continue
        aid = str(row.get("id") or "").strip()
        if aid:
            out[f"id:{aid}"] = overall
        name, team = str(row.get("name") or ""), str(row.get("team") or "")
        if name and team:
            out.setdefault(_key(name, team), overall)
    return out


def build_board(usage_rows: Iterable[dict], recruit_index: dict[str, dict],
                season: int, positions: Sequence[str] = DEVY_POSITIONS,
                min_usage: float = 0.08,
                prior_usage: Optional[dict[str, float]] = None) -> tuple[list[Prospect], dict]:
    """(ranked prospects, join report).

    ``min_usage`` drops the long tail of players with a handful of touches, where
    the share number is noise rather than signal.
    """
    staged: list[dict] = []
    seen = matched = by_id = 0
    posset = {p.upper() for p in positions}
    for row in usage_rows or []:
        if not isinstance(row, dict):
            continue
        pos = str(row.get("position") or "").upper()
        if posset and pos not in posset:
            continue
        u = row.get("usage")
        overall = float((u or {}).get("overall") or 0.0) if isinstance(u, dict) else 0.0
        if overall < min_usage:
            continue
        seen += 1
        name = str(row.get("name") or "")
        team = str(row.get("team") or "")
        aid = str(row.get("id") or "").strip()

        # Exact id join first; name+school only as a reported fallback.
        rec = (recruit_index.get(f"id:{aid}") if aid else None)
        if rec:
            by_id += 1
        else:
            rec = recruit_index.get(_key(name, team))
        if rec:
            matched += 1
        year = (rec or {}).get("year")
        prev = None
        if prior_usage:
            prev = (prior_usage.get(f"id:{aid}") if aid else None)
            if prev is None:
                prev = prior_usage.get(_key(name, team))
        staged.append({
            "name": name, "position": pos, "team": team,
            "conference": str(row.get("conference") or ""), "usage": overall,
            "recruit_year": int(year) if year else None,
            "stars": (rec or {}).get("stars"),
            "rating": float(rec["rating"]) if (rec or {}).get("rating") else None,
            "prev_usage": prev,
        })

    # An unmatched player used to score pedigree 0.0 — strictly worse than a
    # 2-star, so a missing recruiting record cut a real prospect's score by more
    # than half. Missing is not the same as bad: impute the population median.
    med = _median([s["rating"] or _stars_to_rating(s["stars"]) for s in staged])
    out = [Prospect(name=s["name"], position=s["position"], team=s["team"],
                    conference=s["conference"], usage=s["usage"], season=int(season),
                    recruit_year=s["recruit_year"], stars=s["stars"],
                    rating=s["rating"] if (s["rating"] or s["stars"]) else med,
                    prev_usage=s.get("prev_usage"))
           for s in staged]
    out.sort(key=lambda p: p.score, reverse=True)
    report = {"players": seen, "pedigree_matched": matched,
              "matched_by_id": by_id,
              "pedigree_rate": round(matched / seen, 3) if seen else 0.0,
              "imputed_rating": round(med, 3)}
    return out, report


def by_draft_class(prospects: Sequence[Prospect]) -> dict[int, list[Prospect]]:
    out: dict[int, list[Prospect]] = {}
    for p in prospects:
        dc = p.draft_class
        if dc:
            out.setdefault(dc, []).append(p)
    for v in out.values():
        v.sort(key=lambda p: p.score, reverse=True)
    return out


# Bands are position-group specific because the score's achievable range is.
# A quarterback's usage.overall runs .50-.60 (he touches every dropback) while a
# receiver tops out near .35, so a single band set made the top two tiers
# structurally impossible for WR/RB/TE and turned them into a silent QB counter:
# a 5-star true freshman receiver at an exceptional .35 usage maxes out at 47.6,
# below the old "elite" floor of 50.
SKILL_BANDS = (("super elite", 40.0), ("elite", 34.0),
               ("very good", 29.0), ("good", 24.0))
QB_BANDS = (("super elite", 58.0), ("elite", 50.0),
            ("very good", 44.0), ("good", 38.0))


def bands_for(position: str) -> Sequence[tuple[str, float]]:
    return QB_BANDS if str(position or "").upper() == "QB" else SKILL_BANDS


def class_projection(prospects: Sequence[Prospect],
                     bands: Optional[Sequence[tuple[str, float]]] = None) -> dict[int, dict]:
    """{draft_class: {band: count}} — the "2028 looks like X elite, Y good" view.

    Bands describe how much *early, high-usage* talent a class carries today.
    A leading indicator for what a class's rookie picks will be worth, not a
    prediction of NFL outcomes.

    ``n`` counts only players whose draft class could be resolved — the same
    population the band counts describe — so the table never mixes denominators.
    """
    out: dict[int, dict] = {}
    names = [n for n, _ in (bands or SKILL_BANDS)]
    for dc, group in by_draft_class(prospects).items():
        row = {name: 0 for name in names}
        row["n"] = len(group)
        for p in group:
            for name, floor in (bands or bands_for(p.position)):
                if p.score >= floor:
                    row[name] = row.get(name, 0) + 1
                    break
        row["top"] = [p.name for p in group[:5]]
        out[dc] = row
    return out
