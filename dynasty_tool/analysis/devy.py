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

    @property
    def draft_class(self) -> Optional[int]:
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
        ped = ((self.rating or 0.0) if self.rating else _stars_to_rating(self.stars))
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
    """{norm(name)|norm(school): recruit} for the pedigree join.

    Name+school is the only join available — CFBD's recruiting and usage feeds
    do not share an id. Per this repo's convention the caller reports the match
    rate rather than hiding the misses.
    """
    out: dict[str, dict] = {}
    for r in recruits or []:
        if not isinstance(r, dict):
            continue
        name = str(r.get("name") or "")
        school = str(r.get("committedTo") or r.get("school") or "")
        if not name or not school:
            continue
        out.setdefault(_key(name, school), r)
    return out


def build_board(usage_rows: Iterable[dict], recruit_index: dict[str, dict],
                season: int, positions: Sequence[str] = DEVY_POSITIONS,
                min_usage: float = 0.08) -> tuple[list[Prospect], dict]:
    """(ranked prospects, join report).

    ``min_usage`` drops the long tail of players with a handful of touches, where
    the share number is noise rather than signal.
    """
    out: list[Prospect] = []
    seen = matched = 0
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
        rec = recruit_index.get(_key(name, team)) or {}
        if rec:
            matched += 1
        year = rec.get("year")
        out.append(Prospect(
            name=name, position=pos, team=team,
            conference=str(row.get("conference") or ""),
            usage=overall, season=int(season),
            recruit_year=int(year) if year else None,
            stars=rec.get("stars"),
            rating=float(rec["rating"]) if rec.get("rating") else None,
        ))
    out.sort(key=lambda p: p.score, reverse=True)
    report = {"players": seen, "pedigree_matched": matched,
              "pedigree_rate": round(matched / seen, 3) if seen else 0.0}
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


def class_projection(prospects: Sequence[Prospect],
                     bands: Sequence[tuple[str, float]] = (
                         ("super elite", 60.0), ("elite", 50.0),
                         ("very good", 42.0), ("good", 35.0))) -> dict[int, dict]:
    """{draft_class: {band: count}} — the "2028 looks like X elite, Y good" view.

    Bands are on the 0-100 score, not NFL outcomes. This says how much *early,
    high-usage* talent a class is carrying today, which is the honest version of
    the question; it is a leading indicator, not a prediction of NFL results.
    """
    out: dict[int, dict] = {}
    for dc, group in by_draft_class(prospects).items():
        row = {name: 0 for name, _ in bands}
        row["n"] = len(group)
        for p in group:
            for name, floor in bands:
                if p.score >= floor:
                    row[name] += 1
                    break
        row["top"] = [p.name for p in group[:5]]
        out[dc] = row
    return out
