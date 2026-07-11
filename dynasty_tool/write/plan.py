"""Platform-agnostic lineup plan derived from the weekly-optimal lineup.

The analyzer optimizes offense (QB/RB/WR/TE/FLEX/SUPERFLEX); K/DST carry no
projection and are filled per-platform by the writer from the raw roster.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..analysis.league_analysis import lineup_recommendation


@dataclass
class PlanStarter:
    slot: str
    pid: str          # roster-native id (gsis for redraft, sleeper_id for dynasty)
    name: str
    pos: str
    proj: float
    opp: str = ""


@dataclass
class LineupPlan:
    team_display: str
    week: int
    weekly: bool                              # ranked by weekly DFS projection?
    starters: list[PlanStarter]               # offense the analyzer optimizes
    close_calls: list = field(default_factory=list)


def build_plan(A, target_uid: str) -> LineupPlan:
    rv = A.rosters[target_uid]
    rec = lineup_recommendation(rv, A.roster_positions, weekly=A.weekly,
                                week=A.weekly_week, opp=A.weekly_opp)
    starters = [
        PlanStarter(slot, p.sleeper_id, p.name, p.pos,
                    rec.score.get(p.sleeper_id, p.value), rec.opp.get(p.sleeper_id, ""))
        for slot, p in rec.lineup
    ]
    return LineupPlan(rv.display, rec.week or (A.weekly_week or 0), rec.weekly,
                      starters, rec.close_calls)


def format_plan(plan: LineupPlan) -> str:
    fmt = "{:.1f}" if plan.weekly else "{:.0f}"
    lines = []
    for s in plan.starters:
        opp = f"  {s.opp}" if s.opp else ""
        lines.append(f"   {s.slot:6s} {s.name} ({s.pos}){opp}   {fmt.format(s.proj)}")
    return "\n".join(lines)
