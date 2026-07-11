"""Module 3 -- Roster / Window Optimizer.

For each roster (keyed on user_id) we value players at current value, fill the
league's actual starting lineup (parsed from roster_positions, incl. FLEX /
SUPER_FLEX / IDP), and split starter value from (down-weighted) depth so
positional gluts vs holes show up. Contention window is reported on TWO explicit
axes -- starter value and roster age -- never conflating "old" with "bad". Trade
targets match another roster's positional surplus to the user's holes and flag
whether the partner is a competitor.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Optional

from .. import config

_VALUE_POSITIONS = ("QB", "RB", "WR", "TE")  # offense carries dynasty value (IDP unvalued)
_FLEX_MAP = {
    "FLEX": config.FLEX_ELIGIBLE,
    "WRRB_FLEX": {"WR", "RB"},
    "REC_FLEX": {"WR", "TE"},
    "SUPER_FLEX": config.SUPERFLEX_ELIGIBLE,
    "IDP_FLEX": config.IDP_POSITIONS,
}


@dataclass
class RPlayer:
    sleeper_id: str
    name: str
    pos: str
    value: float
    age: Optional[float]
    matched: bool


@dataclass
class RosterValue:
    user_id: str
    display: str
    players: list[RPlayer] = field(default_factory=list)
    starters: list[tuple[str, RPlayer]] = field(default_factory=list)  # (slot, player)
    starter_value: float = 0.0
    depth_value: float = 0.0
    total_value: float = 0.0
    value_by_pos: dict = field(default_factory=dict)
    starter_value_by_pos: dict = field(default_factory=dict)
    avg_starter_age: Optional[float] = None
    n_unmatched: int = 0

    # window (filled by classify_window)
    starter_value_pct: float = 0.0     # percentile among league (0..1)
    window: str = ""
    age_axis: str = ""
    value_axis: str = ""


def _lineup_slots(roster_positions: list[str]) -> list[str]:
    return [s for s in roster_positions if s not in config.NON_STARTER_SLOTS]


def _fill_lineup(players: list[RPlayer], slots: list[str], key=None) -> list[tuple[str, RPlayer]]:
    """Greedy best-value lineup fill: fixed position slots first, then flex slots.

    ``key`` scores each player (default: dynasty/season value). Pass a weekly
    projection lookup to build a week-specific optimal lineup.
    """
    if key is None:
        key = lambda p: p.value  # noqa: E731
    pool = sorted(players, key=key, reverse=True)
    used: set[str] = set()

    def take(eligible: set[str]) -> Optional[RPlayer]:
        for p in pool:
            if p.sleeper_id not in used and p.pos in eligible:
                used.add(p.sleeper_id)
                return p
        return None

    fixed = [s for s in slots if s not in _FLEX_MAP]
    flex = [s for s in slots if s in _FLEX_MAP]
    starters: list[tuple[str, RPlayer]] = []
    for s in fixed:
        p = take({s})
        if p:
            starters.append((s, p))
    for s in flex:
        p = take(_FLEX_MAP[s])
        if p:
            starters.append((s, p))
    return starters


def compute_rosters(ctx, provider) -> dict[str, RosterValue]:
    from ..value import assets

    slots = _lineup_slots(ctx.newest.get("roster_positions") or [])
    rmap = ctx.newest_roster_map()
    out: dict[str, RosterValue] = {}

    for roster in ctx.client.rosters(ctx.newest["league_id"]):
        uid = rmap.get(roster["roster_id"])
        if uid is None:
            continue
        rv = RosterValue(user_id=uid, display=ctx.display(uid))
        for pid in (roster.get("players") or []):
            meta = ctx.players_meta.get(str(pid)) or {}
            av = assets.value_player(provider, ctx.players_meta, pid)
            age = meta.get("age")
            try:
                age = float(age) if age is not None else None
            except (TypeError, ValueError):
                age = None
            if age is not None and age != age:  # NaN age (e.g. unprojected player) -> unknown
                age = None
            # clean name (pos is a separate field; av.label carries a "(POS)"
            # suffix that would double up wherever we render name + pos)
            name = f"{meta.get('first_name', '')} {meta.get('last_name', '')}".strip()
            rp = RPlayer(str(pid), name or av.label, meta.get("position") or "?",
                         av.value, age, av.matched)
            rv.players.append(rp)
            if not av.matched:
                rv.n_unmatched += 1

        rv.total_value = sum(p.value for p in rv.players)
        for p in rv.players:
            rv.value_by_pos[p.pos] = rv.value_by_pos.get(p.pos, 0.0) + p.value

        rv.starters = _fill_lineup(rv.players, slots)
        rv.starter_value = sum(p.value for _, p in rv.starters)
        rv.depth_value = (rv.total_value - rv.starter_value) * config.DEPTH_WEIGHT
        for _, p in rv.starters:
            rv.starter_value_by_pos[p.pos] = rv.starter_value_by_pos.get(p.pos, 0.0) + p.value

        # value-weighted average age of matched starters (0-value IDP don't skew)
        aged = [(p.value, p.age) for _, p in rv.starters if p.age and p.value > 0]
        if aged:
            wsum = sum(v for v, _ in aged)
            rv.avg_starter_age = sum(v * a for v, a in aged) / wsum if wsum else None
        out[uid] = rv
    return out


def classify_windows(rosters: dict[str, RosterValue]) -> None:
    vals = sorted((r.starter_value for r in rosters.values()))
    n = len(vals)

    def pct(v: float) -> float:
        below = sum(1 for x in vals if x < v)
        return below / max(1, n - 1) if n > 1 else 0.5

    hi_cut = vals[int(0.66 * (n - 1))] if n else 0
    lo_cut = vals[int(0.33 * (n - 1))] if n else 0

    for r in rosters.values():
        r.starter_value_pct = pct(r.starter_value)
        if r.starter_value >= hi_cut:
            r.value_axis = "high"
        elif r.starter_value <= lo_cut:
            r.value_axis = "low"
        else:
            r.value_axis = "mid"

        a = r.avg_starter_age
        if a is None:
            r.age_axis = "unknown"
        elif a < 25.5:
            r.age_axis = "young"
        elif a > 27.5:
            r.age_axis = "old"
        else:
            r.age_axis = "prime"

        r.window = _window_label(r.value_axis, r.age_axis)


def _window_label(value_axis: str, age_axis: str) -> str:
    if value_axis == "high":
        if age_axis == "young":
            return "Contender (sustainable)"
        if age_axis == "old":
            return "Win-now (aging core)"
        return "Contender"
    if value_axis == "low":
        if age_axis == "young":
            return "Rebuild (ascending)"
        if age_axis == "old":
            return "Rebuild (teardown)"
        return "Rebuild"
    if age_axis == "old":
        return "Bubble (aging -> sell)"
    if age_axis == "young":
        return "Bubble (rising)"
    return "Bubble / Retool"


# -- trade targets -----------------------------------------------------------

@dataclass
class TargetFit:
    partner_uid: str
    partner_display: str
    score: float
    they_fill: dict          # pos -> value they can send into my hole
    i_fill: dict             # pos -> value I can send into their hole
    competitor: bool


def _needs_and_surplus(rosters: dict[str, RosterValue]):
    """Per position: league median starter value; each roster's need & surplus."""
    med: dict[str, float] = {}
    for pos in _VALUE_POSITIONS:
        svals = [r.starter_value_by_pos.get(pos, 0.0) for r in rosters.values()]
        med[pos] = median(svals) if svals else 0.0

    needs: dict[str, dict[str, float]] = {}
    surplus: dict[str, dict[str, float]] = {}
    for uid, r in rosters.items():
        needs[uid] = {}
        surplus[uid] = {}
        for pos in _VALUE_POSITIONS:
            sv = r.starter_value_by_pos.get(pos, 0.0)
            needs[uid][pos] = max(0.0, med[pos] - sv)               # weak starting spot
            surplus[uid][pos] = max(0.0, r.value_by_pos.get(pos, 0.0) - sv)  # depth beyond starters
    return med, needs, surplus


def trade_targets(rosters: dict[str, RosterValue], target_uid: str,
                  top_n: int = 5) -> list[TargetFit]:
    if target_uid not in rosters:
        return []
    med, needs, surplus = _needs_and_surplus(rosters)
    my_need, my_surplus = needs[target_uid], surplus[target_uid]

    median_starter = median([r.starter_value for r in rosters.values()])
    fits: list[TargetFit] = []
    for uid, r in rosters.items():
        if uid == target_uid:
            continue
        they_fill = {pos: min(my_need[pos], surplus[uid][pos])
                     for pos in _VALUE_POSITIONS if my_need[pos] > 0 and surplus[uid][pos] > 0}
        i_fill = {pos: min(needs[uid][pos], my_surplus[pos])
                  for pos in _VALUE_POSITIONS if needs[uid][pos] > 0 and my_surplus[pos] > 0}
        score = sum(they_fill.values()) + sum(i_fill.values())
        if score <= 0:
            continue
        fits.append(TargetFit(
            partner_uid=uid,
            partner_display=r.display,
            score=score,
            they_fill=they_fill,
            i_fill=i_fill,
            competitor=r.starter_value >= median_starter,
        ))
    fits.sort(key=lambda f: f.score, reverse=True)
    return fits[:top_n]


# -- rendering ---------------------------------------------------------------

def format_rosters(ctx, rosters: dict[str, RosterValue], basis: str = "realized") -> str:
    lines: list[str] = []
    lines.append(f"ROSTER VALUE & CONTENTION WINDOWS  [basis={basis} | {ctx.qb_label}]")
    lines.append("=" * 92)
    lines.append(f"{'account':<20}{'starter':>9}{'depth':>8}{'total':>9}"
                 f"{'age':>6}{'val%ile':>8}  window")
    lines.append("-" * 92)
    for r in sorted(rosters.values(), key=lambda x: x.starter_value, reverse=True):
        age = f"{r.avg_starter_age:.1f}" if r.avg_starter_age else "  -"
        lines.append(f"{r.display:<20}{r.starter_value:>9.0f}{r.depth_value:>8.0f}"
                     f"{r.total_value:>9.0f}{age:>6}{r.starter_value_pct*100:>7.0f}%  "
                     f"{r.window}")
    lines.append("\naxes are independent: 'starter value' (win-now strength) and "
                 "'age' (window length).")
    lines.append("a low-value young roster is a rebuild with runway; a high-value old "
                 "roster should sell.")
    return "\n".join(lines)


def format_targets(ctx, rosters: dict[str, RosterValue], target_uid: str,
                   fits: list[TargetFit], basis: str = "realized") -> str:
    lines: list[str] = []
    me = rosters.get(target_uid)
    if me is None:
        return f"(no roster for user {target_uid})"
    lines.append(f"\nTRADE TARGETS for {me.display}  [{ctx.qb_label} | {basis}]")
    lines.append("=" * 72)
    _, needs, _ = _needs_and_surplus(rosters)
    my_holes = {p: v for p, v in needs[target_uid].items() if v > 0}
    holes_txt = ", ".join(f"{p} (-{v:.0f})" for p, v in
                          sorted(my_holes.items(), key=lambda kv: kv[1], reverse=True)) or "none"
    lines.append(f"your weak starting spots vs league median: {holes_txt}")
    if not fits:
        lines.append("no strong positional-fit partners found.")
        return "\n".join(lines)
    for f in fits:
        tag = "COMPETITOR" if f.competitor else "non-competitor"
        fill = ", ".join(f"{p}+{v:.0f}" for p, v in
                         sorted(f.they_fill.items(), key=lambda kv: kv[1], reverse=True)) or "-"
        give = ", ".join(f"{p}+{v:.0f}" for p, v in
                         sorted(f.i_fill.items(), key=lambda kv: kv[1], reverse=True)) or "-"
        lines.append(f"  {f.partner_display:<20} fit={f.score:>6.0f}  [{tag}]")
        lines.append(f"      they send you: {fill}")
        lines.append(f"      you send them: {give}")
    lines.append("\nflag: trading a COMPETITOR into their strength helps a rival -- "
                 "value math alone won't show that.")
    return "\n".join(lines)
