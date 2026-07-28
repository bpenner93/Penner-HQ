"""Route participation, snap share and targets-per-route-run — pure maths.

The metric that matters most here is **TPRR** (targets per route run): it
separates "he's on the field a lot" from "the offence actually looks for him",
and it stabilises far faster than target share. It needs a route denominator,
which is why route counting is worth the trouble.

**How routes are counted, stated plainly.** PFF charts routes by hand and
charges for it. nflverse publishes, per play, the 11 offensive players on the
field. So a route here means *the player was on the field for a dropback*.

That is a close proxy for wide receivers and a **generous** one for tight ends
and backs, who are credited a route on snaps they spent blocking. Validated
against 2023: CeeDee Lamb 736 and Amon-Ra St. Brown 719 land within a few
percent of published route totals, but Cade Otton shows 754 (97% of Tampa's
dropbacks) because he rarely left the field. So read TE and RB route share as
"pass-snap participation", and read their targets-per-route as conservative —
the denominator is inflated, which pushes the rate down.

The error is one-directional and never hides usage: nobody is under-counted.

A play counts as a dropback when NGS charted it as one, i.e. any of
``time_to_throw``, ``route`` or ``defense_coverage_type`` is populated. Sacks and
scrambles carry coverage data, so they are included, which matches how PFF counts
routes.

Route participation is deliberately expressed as a **share of team dropbacks**.
Any error in the dropback classifier lands in both numerator and denominator and
largely cancels, which makes the share far more robust than the raw count.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional, Sequence

# Fields whose presence marks a play as a charted dropback.
_DROPBACK_MARKERS = ("time_to_throw", "route", "defense_coverage_type")

# A full-time receiver's expected season route count, used only to sanity-check
# the proxy in the UI — never to filter.
SANE_SEASON_ROUTES = (400, 700)


@dataclass(frozen=True)
class PlayerUsage:
    gsis_id: str
    routes: int = 0
    dropbacks: int = 0          # team dropbacks while he was rostered/active
    targets: int = 0
    snaps: int = 0
    snap_pct: float = 0.0
    games: int = 0

    @property
    def route_pct(self) -> float:
        """Share of his team's dropbacks he was on the field for.

        Capped at 1.0: a player traded mid-season accumulates routes across two
        teams while the denominator is only the team he played most for, which
        would otherwise show him above 100%.
        """
        if not self.dropbacks:
            return 0.0
        return min(1.0, self.routes / self.dropbacks)

    @property
    def tprr(self) -> float:
        """Targets per route run. Undercounting routes inflates this, which is
        why the route total travels with it everywhere it's displayed."""
        return self.targets / self.routes if self.routes else 0.0

    @property
    def routes_per_game(self) -> float:
        return self.routes / self.games if self.games else 0.0


def _has(row: Mapping, key: str) -> bool:
    v = row.get(key)
    if v is None:
        return False
    s = str(v).strip()
    return s not in ("", "nan", "None", "NA", "<NA>")


def is_dropback(row: Mapping) -> bool:
    return any(_has(row, k) for k in _DROPBACK_MARKERS)


def split_players(value) -> list[str]:
    """``"00-0034445;00-0033831"`` -> ids. Tolerates None and stray spacing."""
    if not value:
        return []
    return [p.strip() for p in str(value).split(";") if p.strip()]


def aggregate_participation(rows: Iterable[Mapping]) -> tuple[dict[str, int], dict[str, int]]:
    """(routes per gsis_id, dropbacks per possession team).

    One pass over the season. Both dicts come out of the *same* dropback
    classifier, which is what makes their ratio robust.
    """
    routes: dict[str, int] = {}
    team_db: dict[str, int] = {}
    for row in rows:
        if not is_dropback(row):
            continue
        team = str(row.get("possession_team") or "").strip()
        if team:
            team_db[team] = team_db.get(team, 0) + 1
        for pid in split_players(row.get("offense_players")):
            routes[pid] = routes.get(pid, 0) + 1
    return routes, team_db


def player_team(rows: Iterable[Mapping]) -> dict[str, str]:
    """gsis_id -> the team he appeared for most. Handles mid-season trades by
    attributing a player to where he actually played the most snaps."""
    counts: dict[str, dict[str, int]] = {}
    for row in rows:
        team = str(row.get("possession_team") or "").strip()
        if not team:
            continue
        for pid in split_players(row.get("offense_players")):
            counts.setdefault(pid, {})[team] = counts.setdefault(pid, {}).get(team, 0) + 1
    return {pid: max(t.items(), key=lambda kv: kv[1])[0] for pid, t in counts.items()}


def aggregate_snaps(rows: Iterable[Mapping],
                    pfr_to_gsis: Optional[Mapping[str, str]] = None) -> dict[str, dict]:
    """PFR snap-count rows -> {gsis_id: {snaps, snap_pct, games}}.

    ``offense_pct`` is already a share, so this averages it across games rather
    than recomputing it — PFR's denominator is the authoritative one.
    """
    acc: dict[str, dict] = {}
    for row in rows:
        pfr = str(row.get("pfr_player_id") or "").strip()
        if not pfr:
            continue
        key = (pfr_to_gsis or {}).get(pfr, pfr) if pfr_to_gsis else pfr
        if pfr_to_gsis and pfr not in pfr_to_gsis:
            continue
        try:
            snaps = int(float(row.get("offense_snaps") or 0))
            pct = float(row.get("offense_pct") or 0.0)
        except (TypeError, ValueError):
            continue
        if snaps <= 0:
            continue
        a = acc.setdefault(key, {"snaps": 0, "pct_sum": 0.0, "games": 0})
        a["snaps"] += snaps
        a["pct_sum"] += pct
        a["games"] += 1
    return {k: {"snaps": v["snaps"], "games": v["games"],
                "snap_pct": (v["pct_sum"] / v["games"]) if v["games"] else 0.0}
            for k, v in acc.items()}


def build_usage(routes: Mapping[str, int], team_dropbacks: Mapping[str, int],
                teams: Mapping[str, str], snaps: Mapping[str, dict],
                targets: Optional[Mapping[str, int]] = None,
                min_routes: int = 1) -> dict[str, PlayerUsage]:
    """Merge the three sources into one per-player record keyed by gsis_id."""
    out: dict[str, PlayerUsage] = {}
    for pid, n in routes.items():
        if n < min_routes:
            continue
        s = snaps.get(pid) or {}
        out[pid] = PlayerUsage(
            gsis_id=pid, routes=int(n),
            dropbacks=int(team_dropbacks.get(teams.get(pid, ""), 0)),
            targets=int((targets or {}).get(pid, 0)),
            snaps=int(s.get("snaps", 0)),
            snap_pct=float(s.get("snap_pct", 0.0)),
            games=int(s.get("games", 0)),
        )
    return out


def crosswalk(ids_rows: Iterable[Mapping], source: str,
              target: str = "sleeper_id") -> dict[str, str]:
    """One id column -> another, from DynastyProcess's ``db_playerids``.

    That file is already fetched and cached by the app, and it carries
    sleeper/gsis/pfr/cfbref together — so every join in this feature is by id and
    none by name. Literal ``"NA"`` is a real value in this file and must be
    treated as missing, not as an id.
    """
    out: dict[str, str] = {}
    for r in ids_rows or []:
        a = str(r.get(source) or "").strip()
        b = str(r.get(target) or "").strip()
        if a and b and a not in ("NA", "nan") and b not in ("NA", "nan"):
            out.setdefault(a, b.split(".")[0])
    return out


def leaderboard(usage: Mapping[str, PlayerUsage], key: str = "route_pct",
                min_routes: int = 50, limit: int = 50) -> list[PlayerUsage]:
    """Top players by a usage metric. ``min_routes`` keeps a 3-route cameo with
    one target from topping a TPRR board at 0.33."""
    rows = [u for u in usage.values() if u.routes >= min_routes]
    rows.sort(key=lambda u: getattr(u, key, 0.0), reverse=True)
    return rows[:limit]


def looks_sane(usage: Mapping[str, PlayerUsage]) -> tuple[bool, str]:
    """Structural guard on the route proxy.

    Deliberately *not* "is the top route count in a plausible range" — the
    highest counts belong to offensive linemen, who are on the field for every
    dropback, so that check would measure the wrong population and needs position
    data this module doesn't have.

    Instead: almost nobody should exceed his team's dropback count. A handful
    will (mid-season trades), but a large share means the dropback classifier or
    the team attribution has drifted.
    """
    if not usage:
        return False, "no usage rows"
    scored = [u for u in usage.values() if u.dropbacks]
    if not scored:
        return False, "no team dropback totals resolved"
    over = sum(1 for u in scored if u.routes > u.dropbacks)
    share = over / len(scored)
    msg = (f"{len(scored):,} players · {over} above their team's dropbacks "
           f"({share:.1%}, expected ~0% plus in-season trades)")
    return share <= 0.05, msg
