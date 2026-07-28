"""College production features for the rookie/devy model — pure maths.

Three things live here, and each fixes something that was previously declared
but never computed:

* **Dominator rating.** The real definition: the average of a player's share of
  his team's receiving yards and his share of its receiving touchdowns. The devy
  board previously used CFBD's ``usage.overall``, which is a *different quantity*
  — share of offensive plays involved in. Substituting one for the other would
  have been a silent semantic swap, not a fix.
* **Breakout age.** The age at which a player first cleared a position-specific
  dominator threshold. Early dominance is the single most predictive college
  signal for receivers, and it is what separates a genuine prospect from a
  senior compiling against younger competition.
* **The bridge to nflverse.** CFBD athlete ids are numeric; nflverse's
  ``cfb_player_id`` is a sports-reference slug. They never join directly — that
  was a real defect. They *do* join exactly through draft position: CFBD's draft
  endpoint carries ``collegeAthleteId`` alongside ``year``/``overall``, and
  nflverse's draft table carries ``season``/``pick`` (verified to be the overall
  pick). Matching on (year, overall) is unambiguous and needs no name matching,
  which is the join discipline the rest of this codebase insists on.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional, Sequence

# A "dominant" college season, by position. Receivers command a bigger share of
# a passing game than backs or tight ends ever do, so one threshold would make
# breakout age meaningless for everyone but WRs.
BREAKOUT_THRESHOLD = {"WR": 0.20, "TE": 0.15, "RB": 0.15, "QB": 0.0}

_REC_YARDS = {"YDS", "YARDS"}
_REC_TDS = {"TD", "TDS"}


def _fnum(v, default: float = 0.0) -> float:
    try:
        f = float(v)
        return f if f == f else default          # NaN -> default
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# dominator
# ---------------------------------------------------------------------------
def pivot_season_stats(rows: Iterable[Mapping],
                       category: str = "receiving") -> dict[tuple, dict]:
    """CFBD's long-format season stats -> {(season, playerId): {...}}.

    ``/stats/player/season`` returns one row per stat *type*
    (``category='receiving', statType='YDS', stat='1200'``), so it has to be
    pivoted before any share can be computed.
    """
    out: dict[tuple, dict] = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        if category and str(r.get("category") or "").lower() != category.lower():
            continue
        pid = str(r.get("playerId") or r.get("player_id") or "").strip()
        if not pid:
            continue
        try:
            season = int(float(r.get("season")))
        except (TypeError, ValueError):
            continue
        key = (season, pid)
        rec = out.setdefault(key, {"season": season, "player_id": pid,
                                   "name": str(r.get("player") or ""),
                                   "team": str(r.get("team") or ""),
                                   "conference": str(r.get("conference") or ""),
                                   "yards": 0.0, "tds": 0.0})
        st = str(r.get("statType") or r.get("stat_type") or "").upper()
        if st in _REC_YARDS:
            rec["yards"] = _fnum(r.get("stat"))
        elif st in _REC_TDS:
            rec["tds"] = _fnum(r.get("stat"))
    return out


def dominators(pivoted: Mapping[tuple, Mapping]) -> dict[tuple, float]:
    """{(season, player_id): dominator} — mean of yard share and TD share.

    Team totals are summed from the players themselves, which is the only option
    without a second endpoint and is accurate to within the long tail of players
    CFBD omits from season stats.
    """
    totals: dict[tuple, dict] = {}
    for (season, _pid), rec in pivoted.items():
        t = totals.setdefault((season, rec.get("team") or ""),
                              {"yards": 0.0, "tds": 0.0})
        t["yards"] += _fnum(rec.get("yards"))
        t["tds"] += _fnum(rec.get("tds"))

    out: dict[tuple, float] = {}
    for key, rec in pivoted.items():
        season = key[0]
        t = totals.get((season, rec.get("team") or ""))
        if not t:
            continue
        ys = _fnum(rec.get("yards")) / t["yards"] if t["yards"] > 0 else 0.0
        ts = _fnum(rec.get("tds")) / t["tds"] if t["tds"] > 0 else 0.0
        # When a team scored no receiving TDs at all, TD share is undefined
        # rather than zero — averaging in a fake 0 would halve every dominator
        # on that roster.
        out[key] = (ys + ts) / 2.0 if t["tds"] > 0 else ys
    return out


# ---------------------------------------------------------------------------
# breakout age
# ---------------------------------------------------------------------------
def age_in_season(birthdate: Optional[str], season: int) -> Optional[float]:
    """Age on 1 September of that college season. Returns None on a bad date so
    the caller can impute rather than silently score someone as a newborn."""
    if not birthdate:
        return None
    s = str(birthdate).strip()[:10]
    try:
        y, m, d = (int(x) for x in s.split("-"))
    except (ValueError, TypeError):
        return None
    if not (1900 < y < 2100):
        return None
    return round((season - y) + (9 - m) / 12.0 - (d / 365.0), 2)


def breakout_ages(dominator_by_key: Mapping[tuple, float],
                  pivoted: Mapping[tuple, Mapping],
                  birthdates: Mapping[str, str],
                  positions: Optional[Mapping[str, str]] = None,
                  default_threshold: float = 0.20) -> dict[str, float]:
    """{player_id: age at first dominant season}.

    Earliest qualifying season wins — the whole point is *when* it first
    happened, so a later, bigger season must not overwrite it.
    """
    best: dict[str, tuple[int, float]] = {}
    for (season, pid), dom in dominator_by_key.items():
        pos = str((positions or {}).get(pid) or "").upper()
        if dom < BREAKOUT_THRESHOLD.get(pos, default_threshold):
            continue
        cur = best.get(pid)
        if cur is None or season < cur[0]:
            age = age_in_season(birthdates.get(pid), season)
            if age is not None:
                best[pid] = (season, age)
    return {pid: age for pid, (_s, age) in best.items()}


# ---------------------------------------------------------------------------
# the bridge
# ---------------------------------------------------------------------------
def draft_bridge(cfbd_picks: Iterable[Mapping],
                 nflverse_picks: Iterable[Mapping]) -> tuple[dict[str, str], dict]:
    """({cfbd college athlete id: nflverse cfb_player_id}, report).

    Joins on (draft year, overall pick), which is unique by construction — two
    players cannot share a slot. No names are involved anywhere.
    """
    nfl: dict[tuple, str] = {}
    for r in nflverse_picks or []:
        try:
            season = int(float(r.get("season")))
            pick = int(float(r.get("pick")))
        except (TypeError, ValueError):
            continue
        slug = str(r.get("cfb_player_id") or "").strip()
        if slug:
            nfl[(season, pick)] = slug

    out: dict[str, str] = {}
    seen = matched = 0
    for r in cfbd_picks or []:
        if not isinstance(r, dict):
            continue
        cid = str(r.get("collegeAthleteId") or r.get("college_athlete_id") or "").strip()
        try:
            year = int(float(r.get("year")))
            overall = int(float(r.get("overall")))
        except (TypeError, ValueError):
            continue
        if not cid:
            continue
        seen += 1
        slug = nfl.get((year, overall))
        if slug:
            out[cid] = slug
            matched += 1
    return out, {"cfbd_picks": seen, "bridged": matched,
                 "rate": round(matched / seen, 3) if seen else 0.0}


def build_college_features(dominator_by_key: Mapping[tuple, float],
                           breakout: Mapping[str, float],
                           pedigree: Mapping[str, float],
                           bridge: Mapping[str, str]) -> tuple[dict[str, dict], dict]:
    """Everything above, keyed by nflverse ``cfb_player_id`` — the exact shape
    ``rookie_model.build_rows(college=...)`` expects and has never been given.

    A player's dominator is his best college season, not his last: the model is
    trying to learn what he was capable of, and a senior year split with a
    transfer shouldn't erase a dominant sophomore year.
    """
    best_dom: dict[str, float] = {}
    for (_season, pid), dom in dominator_by_key.items():
        if dom > best_dom.get(pid, -1.0):
            best_dom[pid] = dom

    out: dict[str, dict] = {}
    for cid, slug in (bridge or {}).items():
        rec = {}
        if cid in best_dom:
            rec["dominator"] = round(best_dom[cid], 4)
        if cid in breakout:
            rec["breakout"] = breakout[cid]
        if cid in pedigree:
            rec["pedigree"] = pedigree[cid]
        if rec:
            out[slug] = rec
    report = {
        "bridged_players": len(bridge or {}),
        "with_dominator": sum(1 for v in out.values() if "dominator" in v),
        "with_breakout": sum(1 for v in out.values() if "breakout" in v),
        "with_pedigree": sum(1 for v in out.values() if "pedigree" in v),
        "emitted": len(out),
    }
    return out, report
