"""League Analyzer -- a full-league read built on the shared core.

Produces (all keyed on user_id, R1):
  * power rankings + contention tiers + per-team advice   (roster value; any status)
  * concrete trade suggestions (balanced packages)        (roster value; any status)
  * waiver / free-agent targets by team need              (roster value; any status)
  * standings + points                                    (if a season has been played)
  * strength of schedule                                  (if a schedule exists)
  * playoff / championship odds                           (roster-strength Monte Carlo)

Honest about data: in the NFL offseason a pre-draft league has no schedule and no
results, so odds are a PRESEASON roster-strength projection (stated assumptions,
wide uncertainty), SOS is unavailable, and standings are "not played yet". A
completed/in-season league uses its real schedule and results.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import median
from typing import Optional

import numpy as np

from .. import config
from . import optimizer
from .optimizer import RosterValue, RPlayer, _needs_and_surplus, _VALUE_POSITIONS, _FLEX_MAP

# --- roster-strength simulation model (documented, deliberately not overconfident) ---
SIM_MU = 110.0     # league-average weekly team score (scale only; win% is scale-free)
SIM_BETA = 8.0     # points of weekly mean per 1 SD of starter value (separation)
SIM_SIGMA = 26.0   # week-to-week scoring noise (SD)
SIM_TAU = 8.0      # true-talent uncertainty per team per season (humility term)
SIM_N = 4000


# ==========================================================================
# standings / schedule / SOS (data-dependent)
# ==========================================================================

@dataclass
class Standing:
    user_id: str
    display: str
    wins: int
    losses: int
    ties: int
    fpts: float
    fpts_against: float
    ppts: float          # potential points (coaching efficiency numerator)


def standings(ctx, league_id: str) -> Optional[list[Standing]]:
    """From roster settings; None if nothing has been played (preseason)."""
    rmap = ctx.roster_maps.get(league_id, {})
    rows: list[Standing] = []
    played = False
    for r in ctx.client.rosters(league_id):
        uid = rmap.get(r["roster_id"])
        if uid is None:
            continue
        s = r.get("settings", {}) or {}
        w, l, t = s.get("wins", 0), s.get("losses", 0), s.get("ties", 0)
        fpts = s.get("fpts", 0) + s.get("fpts_decimal", 0) / 100.0
        fpa = s.get("fpts_against", 0) + s.get("fpts_against_decimal", 0) / 100.0
        ppts = s.get("ppts", 0) + s.get("ppts_decimal", 0) / 100.0
        if (w + l + t) > 0 or fpts > 0:
            played = True
        rows.append(Standing(uid, ctx.display(uid), w, l, t, fpts, fpa, ppts))
    if not played:
        return None
    rows.sort(key=lambda x: (x.wins, x.fpts), reverse=True)
    return rows


def extract_schedule(ctx, league_id: str, reg_weeks: int) -> Optional[dict[int, list[tuple[str, str]]]]:
    """week -> [(user_a, user_b), ...] from matchups (grouped by matchup_id).

    Returns None if no schedule is set (pre-draft leagues in the offseason).
    """
    rmap = ctx.roster_maps.get(league_id, {})
    sched: dict[int, list[tuple[str, str]]] = {}
    any_games = False
    for wk in range(1, reg_weeks + 1):
        ms = ctx.client.matchups(league_id, wk)
        if not ms:
            continue
        by_mid: dict[int, list[str]] = {}
        for e in ms:
            mid = e.get("matchup_id")
            uid = rmap.get(e.get("roster_id"))
            if mid is None or uid is None:
                continue
            by_mid.setdefault(mid, []).append(uid)
        pairs = [(v[0], v[1]) for v in by_mid.values() if len(v) == 2]
        if pairs:
            sched[wk] = pairs
            any_games = True
    return sched if any_games else None


def strength_of_schedule(schedule: dict[int, list[tuple[str, str]]],
                         strength: dict[str, float]) -> dict[str, dict]:
    """Per team: opponents and average opponent strength (higher = tougher)."""
    opp: dict[str, list[str]] = {}
    for _wk, pairs in schedule.items():
        for a, b in pairs:
            opp.setdefault(a, []).append(b)
            opp.setdefault(b, []).append(a)
    out: dict[str, dict] = {}
    league_avg = (sum(strength.values()) / len(strength)) if strength else 0.0
    for uid, opps in opp.items():
        vals = [strength.get(o, league_avg) for o in opps]
        avg = sum(vals) / len(vals) if vals else league_avg
        out[uid] = {"n_games": len(opps), "avg_opp_strength": avg,
                    "vs_league_avg": avg - league_avg}
    return out


# ==========================================================================
# playoff / championship simulation (roster-strength Monte Carlo)
# ==========================================================================

@dataclass
class SimResult:
    user_id: str
    display: str
    proj_wins: float
    playoff_pct: float
    title_pct: float
    avg_points: float


def team_weekly_means(rosters: dict[str, RosterValue]) -> dict[str, float]:
    """Map each team to an expected weekly score from its starter value.

    Shared by the season sim and the per-matchup win odds so both speak the same
    scale. Scale is nominal (win% is scale-free); the spread comes from SIM_BETA.
    """
    uids = list(rosters)
    sv = np.array([max(1.0, rosters[u].starter_value) for u in uids], dtype=float)
    z = (sv - sv.mean()) / (sv.std() or 1.0)
    return {u: float(SIM_MU + SIM_BETA * zz) for u, zz in zip(uids, z)}


def matchup_win_prob(mean_a: float, mean_b: float) -> float:
    """P(A beats B) for two normal weekly scores with SD SIM_SIGMA each."""
    diff = mean_a - mean_b
    return 0.5 * (1 + math.erf(diff / (SIM_SIGMA * math.sqrt(2))))


def _round_robin(n_teams: int, weeks: int) -> list[list[tuple[int, int]]]:
    """Circle-method balanced schedule as a list of weeks of (i, j) index pairs."""
    teams = list(range(n_teams))
    if n_teams % 2:
        teams.append(-1)  # bye marker
    half = len(teams) // 2
    rounds: list[list[tuple[int, int]]] = []
    arr = teams[:]
    for _ in range(weeks):
        games = []
        for i in range(half):
            a, b = arr[i], arr[-(i + 1)]
            if a != -1 and b != -1:
                games.append((a, b))
        rounds.append(games)
        arr = [arr[0]] + [arr[-1]] + arr[1:-1]  # rotate, fixing first
    return rounds


def simulate_season(rosters: dict[str, RosterValue], playoff_teams: int, reg_weeks: int,
                    schedule: Optional[dict[int, list[tuple[str, str]]]] = None,
                    n: int = SIM_N, seed: int = 12345) -> tuple[list[SimResult], bool]:
    """Monte-Carlo the season from starter-value strength. Returns (results, used_real_schedule)."""
    uids = list(rosters)
    T = len(uids)
    idx = {u: i for i, u in enumerate(uids)}
    means = team_weekly_means(rosters)
    base_mean = np.array([means[u] for u in uids], dtype=float)

    rng = np.random.default_rng(seed)
    true_mean = base_mean[None, :] + rng.normal(0, SIM_TAU, size=(n, T))  # per-sim talent

    # schedule as index rounds
    used_real = schedule is not None
    if used_real:
        rounds = [[(idx[a], idx[b]) for a, b in pairs if a in idx and b in idx]
                  for pairs in schedule.values()]
    else:
        rounds = _round_robin(T, reg_weeks)

    wins = np.zeros((n, T))
    pts = np.zeros((n, T))
    for games in rounds:
        for a, b in games:
            sa = rng.normal(true_mean[:, a], SIM_SIGMA)
            sb = rng.normal(true_mean[:, b], SIM_SIGMA)
            wins[:, a] += (sa > sb)
            wins[:, b] += (sb > sa)
            pts[:, a] += sa
            pts[:, b] += sb

    # seed playoffs by (wins, points); simulate a reseeding single-elim bracket
    rank_key = wins + pts / 1e6
    order = np.argsort(-rank_key, axis=1)          # n x T, best first
    K = min(playoff_teams, T)
    made = order[:, :K]
    playoff_counts = np.zeros(T)
    title_counts = np.zeros(T)
    for s in range(n):
        seeds = list(made[s])
        playoff_counts[np.array(seeds)] += 1
        champ = _sim_bracket(seeds, true_mean[s], rng)
        title_counts[champ] += 1

    results = [
        SimResult(
            user_id=u, display=rosters[u].display,
            proj_wins=float(wins[:, idx[u]].mean()),
            playoff_pct=float(playoff_counts[idx[u]] / n),
            title_pct=float(title_counts[idx[u]] / n),
            avg_points=float(pts[:, idx[u]].mean()),
        )
        for u in uids
    ]
    results.sort(key=lambda r: r.title_pct, reverse=True)
    return results, used_real


def _sim_bracket(seeds: list[int], means: np.ndarray, rng) -> int:
    """Reseeding single elimination among `seeds` (best-first). Returns champion index."""
    alive = list(seeds)
    while len(alive) > 1:
        # best seed gets a bye if odd count
        bye = [alive[0]] if len(alive) % 2 else []
        contenders = alive[len(bye):]
        winners = []
        i, j = 0, len(contenders) - 1
        while i < j:
            a, b = contenders[i], contenders[j]
            sa = rng.normal(means[a], SIM_SIGMA)
            sb = rng.normal(means[b], SIM_SIGMA)
            winners.append(a if sa >= sb else b)
            i += 1
            j -= 1
        alive = bye + sorted(winners, key=lambda t: seeds.index(t))
    return alive[0]


# ==========================================================================
# rest-of-season playoff race
# ==========================================================================

@dataclass
class RaceResult:
    user_id: str
    display: str
    current_wins: float          # ties count as half a win
    current_losses: int
    games_remaining: int
    proj_wins: float             # projected FINAL wins (current + simulated rest)
    playoff_pct: float
    title_pct: float
    status: str                  # 'clinched' | 'eliminated' | 'in the hunt' | 'preseason'


def _current_standings(ctx, league_id: str) -> tuple[dict[str, float], dict[str, float], int]:
    """(wins_by_uid, points_by_uid, completed_weeks) from live roster settings."""
    rmap = ctx.roster_maps.get(league_id, {})
    wins: dict[str, float] = {}
    pts: dict[str, float] = {}
    completed = 0
    for r in ctx.client.rosters(league_id):
        uid = rmap.get(r["roster_id"])
        if uid is None:
            continue
        s = r.get("settings", {}) or {}
        w, l, t = s.get("wins", 0), s.get("losses", 0), s.get("ties", 0)
        wins[uid] = w + 0.5 * t
        pts[uid] = s.get("fpts", 0) + s.get("fpts_decimal", 0) / 100.0
        completed = max(completed, w + l + t)
    return wins, pts, completed


def playoff_race(ctx, rosters: dict[str, RosterValue], playoff_teams: int, reg_weeks: int,
                 schedule: Optional[dict[int, list[tuple[str, str]]]],
                 n: int = SIM_N, seed: int = 999) -> tuple[list[RaceResult], int]:
    """Seed from current record, simulate the REAL remaining schedule to the finish."""
    lid = ctx.newest["league_id"]
    cur_w, cur_pts, completed = _current_standings(ctx, lid)
    first_unplayed = completed + 1

    uids = list(rosters)
    T = len(uids)
    idx = {u: i for i, u in enumerate(uids)}
    means = team_weekly_means(rosters)
    base_mean = np.array([means[u] for u in uids], dtype=float)

    rng = np.random.default_rng(seed)
    true_mean = base_mean[None, :] + rng.normal(0, SIM_TAU, size=(n, T))

    # remaining rounds: real schedule from first_unplayed on, else balanced fill
    if schedule:
        rounds = [[(idx[a], idx[b]) for a, b in pairs if a in idx and b in idx]
                  for wk, pairs in sorted(schedule.items()) if wk >= first_unplayed]
    else:
        rounds = _round_robin(T, max(0, reg_weeks - first_unplayed + 1))

    wins = np.array([[cur_w.get(u, 0.0) for u in uids]] * 1, dtype=float).repeat(n, axis=0)
    pts = np.array([[cur_pts.get(u, 0.0) for u in uids]] * 1, dtype=float).repeat(n, axis=0)
    games_left = np.zeros(T)
    for games in rounds:
        for a, b in games:
            sa = rng.normal(true_mean[:, a], SIM_SIGMA)
            sb = rng.normal(true_mean[:, b], SIM_SIGMA)
            wins[:, a] += (sa > sb)
            wins[:, b] += (sb > sa)
            pts[:, a] += sa
            pts[:, b] += sb
            games_left[a] += 1
            games_left[b] += 1

    rank_key = wins + pts / 1e9
    order = np.argsort(-rank_key, axis=1)
    K = min(playoff_teams, T)
    made = order[:, :K]
    playoff_counts = np.zeros(T)
    title_counts = np.zeros(T)
    for s in range(n):
        seeds = list(made[s])
        playoff_counts[np.array(seeds)] += 1
        title_counts[_sim_bracket(seeds, true_mean[s], rng)] += 1

    out: list[RaceResult] = []
    for u in uids:
        i = idx[u]
        pp = playoff_counts[i] / n
        gl = int(round(games_left[i]))
        if completed == 0:
            status = "preseason"
        elif pp >= 0.999:
            status = "clinched"
        elif pp <= 0.001:
            status = "eliminated"
        else:
            status = "in the hunt"
        w, l, t = 0, 0, 0
        cw = cur_w.get(u, 0.0)
        out.append(RaceResult(
            user_id=u, display=rosters[u].display,
            current_wins=cw, current_losses=completed - int(cw + 0.001) if completed else 0,
            games_remaining=gl,
            proj_wins=float(wins[:, i].mean()),
            playoff_pct=pp, title_pct=title_counts[i] / n, status=status,
        ))
    out.sort(key=lambda r: (r.playoff_pct, r.proj_wins), reverse=True)
    return out, completed


# ==========================================================================
# start / sit (weekly lineup recommendation)
# ==========================================================================

@dataclass
class LineupRec:
    lineup: list[tuple[str, RPlayer]]          # slot -> optimal player
    bench: list[RPlayer]
    close_calls: list[tuple[str, RPlayer, RPlayer, float]]  # slot, starter, challenger, gap
    empty_slots: list[str]
    weekly: bool = False                        # ranked by weekly DFS projection?
    week: Optional[int] = None
    score: dict[str, float] = field(default_factory=dict)   # player_id -> displayed number
    opp: dict[str, str] = field(default_factory=dict)       # player_id -> opponent


def lineup_recommendation(rv: RosterValue, roster_positions: list[str],
                          close_threshold: float = 0.15,
                          weekly: Optional[dict[str, float]] = None,
                          week: Optional[int] = None,
                          opp: Optional[dict[str, str]] = None) -> LineupRec:
    """Optimal lineup + benched challengers within threshold, with close-call flags.

    Default ranks by dynasty/season value (a talent proxy). Pass ``weekly`` (a
    player_id -> this-week projected-points map from the DFS engine) to rank by
    the matchup-specific weekly projection instead -- the correct start/sit signal.
    """
    from . import optimizer
    slots = [s for s in roster_positions if s not in config.NON_STARTER_SLOTS]
    is_weekly = weekly is not None
    score = (lambda p: weekly.get(p.sleeper_id, 0.0)) if is_weekly else (lambda p: p.value)

    if is_weekly:
        lineup = optimizer._fill_lineup(rv.players, slots, key=score)
    else:
        lineup = rv.starters

    starter_sids = {p.sleeper_id for _, p in lineup}
    bench = sorted([p for p in rv.players if p.sleeper_id not in starter_sids and score(p) > 0],
                   key=score, reverse=True)
    filled = {slot for slot, _ in lineup}
    empty = [s for s in slots if s not in filled] if len(lineup) < len(slots) else []

    close: list[tuple[str, RPlayer, RPlayer, float]] = []
    used_challengers: set[str] = set()
    for slot, starter in lineup:
        if score(starter) <= 0:
            continue
        cand = next((p for p in bench
                     if optimizer.slot_eligible(slot, p) and p.sleeper_id not in used_challengers), None)
        if cand:
            gap = (score(starter) - score(cand)) / score(starter)
            if 0 <= gap <= close_threshold:
                close.append((slot, starter, cand, gap))
                used_challengers.add(cand.sleeper_id)

    score_map = {p.sleeper_id: round(score(p), 1) for p in rv.players}
    return LineupRec(lineup, bench, close, empty, weekly=is_weekly, week=week,
                     score=score_map, opp=opp or {})


# ==========================================================================
# weekly matchups + win odds
# ==========================================================================

@dataclass
class Matchup:
    home_uid: str
    away_uid: str
    home_disp: str
    away_disp: str
    home_proj: float
    away_proj: float
    home_win_prob: float
    home_points: float = 0.0     # live/actual points if the week is underway
    away_points: float = 0.0
    live: bool = False


def week_matchups(ctx, league_id: str, week: int, rosters: dict[str, RosterValue],
                  means: dict[str, float]) -> list[Matchup]:
    """This week's head-to-heads with projected scores and win probability.

    Pre-game the win prob is the roster-strength projection. If the week is live
    (points already posted), the actual margin is blended in, weighted by how much
    of each roster has yet to score.
    """
    rmap = ctx.roster_maps.get(league_id, {})
    ms = ctx.client.matchups(league_id, week)
    by_mid: dict[int, list[str]] = {}
    pts: dict[str, float] = {}
    yet_to_play: dict[str, int] = {}
    for e in ms:
        mid, uid = e.get("matchup_id"), rmap.get(e.get("roster_id"))
        if mid is None or uid is None:
            continue
        by_mid.setdefault(mid, []).append(uid)
        pts[uid] = e.get("points") or 0.0
        sp = e.get("starters_points") or []
        yet_to_play[uid] = sum(1 for x in sp if not x)

    out: list[Matchup] = []
    for uids in by_mid.values():
        if len(uids) != 2:
            continue
        a, b = uids
        ma, mb = means.get(a, SIM_MU), means.get(b, SIM_MU)
        pa, pb = pts.get(a, 0.0), pts.get(b, 0.0)
        live = (pa + pb) > 0
        if live:
            n_start = max(1, len(ctx.newest.get("roster_positions", [])) or 12)
            frac_remaining = (yet_to_play.get(a, 0) + yet_to_play.get(b, 0)) / (2 * n_start)
            frac_remaining = min(1.0, max(0.05, frac_remaining))
            proj_a = pa + (ma * frac_remaining)
            proj_b = pb + (mb * frac_remaining)
            sd = SIM_SIGMA * math.sqrt(frac_remaining)
            wp = 0.5 * (1 + math.erf((proj_a - proj_b) / (sd * math.sqrt(2)))) if sd else \
                (1.0 if proj_a > proj_b else 0.0)
        else:
            wp = matchup_win_prob(ma, mb)
        out.append(Matchup(
            a, b,
            rosters[a].display if a in rosters else a,
            rosters[b].display if b in rosters else b,
            ma, mb, wp, pa, pb, live,
        ))
    out.sort(key=lambda m: abs(m.home_win_prob - 0.5))  # closest games first
    return out


# ==========================================================================
# waivers / free agents
# ==========================================================================

@dataclass
class Available:
    sleeper_id: str
    name: str
    pos: str
    value: float
    age: Optional[float]


def available_players(ctx, provider, min_value: float = 1.0,
                      limit: int = 80) -> list[Available]:
    """Unrostered players with real dynasty value, best first.

    In a deep, long-running league the wire is thin (every valuable player is
    hoarded), so we surface the best available regardless of an arbitrary floor
    and let the values themselves show how slim the pickings are.
    """
    rostered: set[str] = set()
    for r in ctx.client.rosters(ctx.newest["league_id"]):
        for pid in (r.get("players") or []):
            rostered.add(str(pid))

    out: list[Available] = []
    for pid, meta in ctx.players_meta.items():
        if not isinstance(meta, dict) or str(pid) in rostered:
            continue
        pos = meta.get("position")
        if pos not in _VALUE_POSITIONS:
            continue
        val, matched = provider.player_value(pid)
        if not matched or val < min_value:
            continue
        name = f"{meta.get('first_name','')} {meta.get('last_name','')}".strip()
        age = meta.get("age")
        try:
            age = float(age) if age is not None else None
        except (TypeError, ValueError):
            age = None
        out.append(Available(str(pid), name, pos, val, age))
    out.sort(key=lambda a: a.value, reverse=True)
    return out[:limit]


def waiver_targets_for(rv: RosterValue, needs: dict[str, float],
                       available: list[Available], per_pos: int = 2) -> list[Available]:
    """Best available at the team's weak positions."""
    hole_pos = [p for p, v in sorted(needs.items(), key=lambda kv: kv[1], reverse=True) if v > 0]
    picks: list[Available] = []
    for pos in hole_pos:
        take = [a for a in available if a.pos == pos][:per_pos]
        picks.extend(take)
    return picks[:5]


# ==========================================================================
# trade suggestions (concrete balanced packages)
# ==========================================================================

@dataclass
class TradePackage:
    partner_uid: str
    partner_display: str
    you_get: list[tuple[str, str, float]]     # (name, pos, value)
    you_give: list[tuple[str, str, float]]
    get_value: float
    give_value: float
    competitor: bool
    # player ids, so a UI can load the package straight into the trade calculator
    get_ids: list[str] = field(default_factory=list)
    give_ids: list[str] = field(default_factory=list)

    @property
    def pct_gap(self) -> float:
        larger = max(self.get_value, self.give_value)
        return abs(self.get_value - self.give_value) / larger if larger else 0.0


def _surplus_players(rv: RosterValue, starters_ids: set[str], pos: str) -> list[RosterValue]:
    return sorted(
        [p for p in rv.players if p.pos == pos and p.sleeper_id not in starters_ids and p.value > 0],
        key=lambda p: p.value, reverse=True,
    )


def propose_trades(rosters: dict[str, RosterValue], target_uid: str,
                   fairness: float = 0.10, max_packages: int = 6) -> list[TradePackage]:
    """Build concrete, roughly-balanced packages: their surplus at my need for my
    surplus at their need, within `fairness`."""
    if target_uid not in rosters:
        return []
    med, needs, _ = _needs_and_surplus(rosters)
    me = rosters[target_uid]
    my_starter_ids = {p.sleeper_id for _, p in me.starters}
    median_starter = median([r.starter_value for r in rosters.values()])

    packages: list[TradePackage] = []
    for uid, other in rosters.items():
        if uid == target_uid:
            continue
        their_starter_ids = {p.sleeper_id for _, p in other.starters}

        # what I want: their surplus at my biggest need
        my_needs = [p for p, v in sorted(needs[target_uid].items(), key=lambda kv: kv[1],
                                         reverse=True) if v > 0]
        want_pool: list = []
        for pos in my_needs:
            want_pool += _surplus_players(other, their_starter_ids, pos)
        if not want_pool:
            continue
        target_player = want_pool[0]  # their best surplus piece at my need

        # what they want: my surplus at their needs, to match value
        their_needs = [p for p, v in sorted(needs[uid].items(), key=lambda kv: kv[1],
                                            reverse=True) if v > 0]
        give_pool: list = []
        for pos in their_needs:
            give_pool += _surplus_players(me, my_starter_ids, pos)
        give_pool.sort(key=lambda p: p.value, reverse=True)
        if not give_pool:
            continue

        # greedily assemble my side to land within fairness of target_player.value
        target_v = target_player.value
        give: list = []
        acc = 0.0
        for p in give_pool:
            if acc >= target_v * (1 - fairness):
                break
            give.append(p)
            acc += p.value
        if not give:
            continue
        gap = abs(acc - target_v) / max(acc, target_v)
        if gap > fairness:
            continue

        packages.append(TradePackage(
            partner_uid=uid,
            partner_display=other.display,
            you_get=[(target_player.name, target_player.pos, target_player.value)],
            you_give=[(p.name, p.pos, p.value) for p in give],
            get_value=target_v,
            give_value=acc,
            competitor=other.starter_value >= median_starter,
            get_ids=[str(target_player.sleeper_id)],
            give_ids=[str(p.sleeper_id) for p in give],
        ))
    packages.sort(key=lambda pk: (pk.get_value, -pk.pct_gap), reverse=True)
    return packages[:max_packages]


# ==========================================================================
# advice
# ==========================================================================

def team_advice(rv: RosterValue, needs: dict[str, float], surplus: dict[str, float]) -> str:
    hole = max(needs.items(), key=lambda kv: kv[1], default=("-", 0))
    glut = max(surplus.items(), key=lambda kv: kv[1], default=("-", 0))
    hole_txt = f"weakest starter: {hole[0]}" if hole[1] > 0 else "no glaring starter hole"
    glut_txt = f"deepest surplus: {glut[0]}" if glut[1] > 0 else "little tradeable depth"

    if rv.value_axis == "high":
        if rv.age_axis == "old":
            lead = "WIN NOW / SELL SOON. Elite but aging -- push all-in this year and sell your oldest stars before decline."
        elif rv.age_axis == "young":
            lead = "CONTEND (sustainable). Strong and young -- your window is open and durable; buy the last piece."
        else:
            lead = "CONTEND. Championship-caliber -- trade youth/picks for win-now upgrades."
    elif rv.value_axis == "low":
        if rv.age_axis == "old":
            lead = "TEARDOWN REBUILD. Weak and old -- sell everything with value for youth and picks."
        else:
            lead = "REBUILD (ascending). Weak but young -- keep accumulating picks and let the core grow."
    else:
        lead = "PICK A LANE. Middling -- either buy in aggressively or sell vets for picks; don't tread water."
    return f"{lead}  ({hole_txt}; {glut_txt}; use the surplus to fix the hole)"


# redraft leagues are all win-now: no windows/picks, value = projected points
_REDRAFT_TIER = {"high": "Contender", "mid": "In the mix", "low": "Longshot"}


def team_advice_redraft(rv: RosterValue, needs: dict[str, float], surplus: dict[str, float]) -> str:
    hole = max(needs.items(), key=lambda kv: kv[1], default=("-", 0))
    glut = max(surplus.items(), key=lambda kv: kv[1], default=("-", 0))
    hole_txt = f"weakest starter: {hole[0]}" if hole[1] > 0 else "balanced lineup"
    glut_txt = f"tradeable depth: {glut[0]}" if glut[1] > 0 else "thin bench"
    if rv.value_axis == "high":
        lead = ("CONTENDER. Strong projected starters -- work the waiver wire for the "
                "last edge and win the close start/sit calls.")
    elif rv.value_axis == "low":
        lead = ("LONGSHOT. Projected thin -- chase upside on waivers and package depth "
                "into one difference-maker.")
    else:
        lead = ("IN THE MIX. Middle of the pack -- your season is won on start/sit, "
                "waivers, and one smart trade.")
    return f"{lead}  ({hole_txt}; {glut_txt})"


# ==========================================================================
# orchestrator
# ==========================================================================

@dataclass
class LeagueAnalysis:
    basis: str
    qb_format: int
    league_name: str
    status: str
    playoff_teams: int
    reg_weeks: int
    rosters: dict[str, RosterValue]
    standings: Optional[list[Standing]]
    sos: Optional[dict[str, dict]]
    sim: list[SimResult]
    sim_used_real_schedule: bool
    available: list[Available]
    waivers: dict[str, list[Available]]           # uid -> targets
    advice: dict[str, str]                          # uid -> advice
    needs: dict[str, dict[str, float]]
    surplus: dict[str, dict[str, float]]
    last_season: Optional[dict] = None             # recap of most recent completed season
    matchups: Optional[list[Matchup]] = None       # this week's head-to-heads
    matchup_week: Optional[int] = None
    matchups_live: bool = False
    race: list[RaceResult] = field(default_factory=list)  # rest-of-season playoff race
    completed_weeks: int = 0
    roster_positions: list[str] = field(default_factory=list)
    redraft: bool = False          # projection-based (redraft/keeper) vs dynasty
    weekly: Optional[dict[str, float]] = None   # player_id -> this-week DFS projection
    weekly_week: Optional[int] = None
    weekly_opp: dict[str, str] = field(default_factory=dict)


def _sleeper_to_gsis(ctx) -> dict:
    """sleeper_id -> gsis from the crosswalk, so gsis-keyed weekly projections can
    be remapped onto sleeper-id-keyed dynasty rosters. Cached; empty on failure."""
    cache = getattr(ctx.client, "cache", None)
    if cache is None:
        return {}
    try:
        from ..value.projection_provider import ProjectionValueProvider
        return ProjectionValueProvider.from_source(cache).sleeper_to_gsis
    except Exception:
        return {}


def analyze(ctx, provider, weekly_week: Optional[int] = None,
            generate_weekly: bool = False) -> LeagueAnalysis:
    redraft = getattr(provider, "basis", "") == "redraft"
    rosters = optimizer.compute_rosters(ctx, provider)
    optimizer.classify_windows(rosters)
    if redraft:  # relabel dynasty windows as redraft power tiers (age is irrelevant)
        for rv in rosters.values():
            rv.window = _REDRAFT_TIER.get(rv.value_axis, "In the mix")
    _med, needs, surplus = _needs_and_surplus(rosters)

    lg = ctx.newest
    s = lg.get("settings", {}) or {}
    playoff_teams = s.get("playoff_teams", 6)
    reg_weeks = (s.get("playoff_week_start", 15) or 15) - 1

    strength = {uid: rv.starter_value for uid, rv in rosters.items()}
    schedule = extract_schedule(ctx, lg["league_id"], reg_weeks)
    sos = strength_of_schedule(schedule, strength) if schedule else None
    sim, used_real = simulate_season(rosters, playoff_teams, reg_weeks, schedule)
    race, completed_weeks = playoff_race(ctx, rosters, playoff_teams, reg_weeks, schedule)

    # this week's matchups + win odds (only if a schedule exists)
    matchups = matchup_week = None
    matchups_live = False
    if schedule:
        means = team_weekly_means(rosters)
        wk = (ctx.client.state() or {}).get("week") or 0
        if wk < 1 or wk > reg_weeks:  # offseason/preseason -> preview opening week
            wk = min(k for k in schedule) if schedule else 1
        mus = week_matchups(ctx, lg["league_id"], wk, rosters, means)
        if mus:
            matchups, matchup_week = mus, wk
            matchups_live = any(m.live for m in mus)

    avail = available_players(ctx, provider)
    waivers = {uid: waiver_targets_for(rv, needs[uid], avail) for uid, rv in rosters.items()}
    _adv = team_advice_redraft if redraft else team_advice
    advice = {uid: _adv(rv, needs[uid], surplus[uid]) for uid, rv in rosters.items()}

    # weekly DFS projections for start/sit. Redraft rosters are gsis-keyed (direct);
    # Sleeper dynasty rosters are sleeper_id-keyed, so remap gsis -> sleeper_id.
    weekly = weekly_opp = None
    weekly_wk = None
    if weekly_week:
        from ..value.weekly_provider import load_weekly
        wp = load_weekly(int(lg.get("season", 0) or 0), int(weekly_week),
                         generate=generate_weekly)
        if wp and wp.proj:
            if redraft:
                weekly, weekly_opp, weekly_wk = dict(wp.proj), dict(wp.opp), wp.week
            else:
                s2g = _sleeper_to_gsis(ctx)
                weekly = {sid: wp.proj[g] for sid, g in s2g.items() if g in wp.proj}
                weekly_opp = {sid: wp.opp.get(g, "") for sid, g in s2g.items() if g in wp.proj}
                weekly_wk = wp.week

    # recap of most recent completed prior season (real standings/SOS if any)
    last_season = None
    for lg_prev in reversed(ctx.chain[:-1]):
        st = standings(ctx, lg_prev["league_id"])
        if st:
            prev_sched = extract_schedule(ctx, lg_prev["league_id"], reg_weeks)
            prev_strength = {r.user_id: r.fpts for r in st}
            prev_sos = strength_of_schedule(prev_sched, prev_strength) if prev_sched else None
            last_season = {"season": lg_prev["season"], "standings": st, "sos": prev_sos}
            break

    return LeagueAnalysis(
        basis=provider.basis, qb_format=ctx.qb_format,
        league_name=lg.get("name", ""), status=lg.get("status", ""),
        playoff_teams=playoff_teams, reg_weeks=reg_weeks,
        rosters=rosters, standings=standings(ctx, lg["league_id"]), sos=sos,
        sim=sim, sim_used_real_schedule=used_real,
        available=avail, waivers=waivers, advice=advice,
        needs=needs, surplus=surplus, last_season=last_season,
        matchups=matchups, matchup_week=matchup_week, matchups_live=matchups_live,
        race=race, completed_weeks=completed_weeks,
        roster_positions=lg.get("roster_positions") or [],
        redraft=redraft,
        weekly=weekly, weekly_week=weekly_wk, weekly_opp=weekly_opp or {},
    )
