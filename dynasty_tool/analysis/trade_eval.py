"""Module 1 -- Trade Evaluator.

Score a proposed trade from two sides of asset specs (player names / sleeper_ids
and/or picks like "2027 1st", "2026 1.05"). Reports per-asset values so the user
can audit, side totals, the delta, and a fairness verdict against stated
thresholds. Future picks are handled per R4; every output labels its basis + QB
format.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .. import config
from ..value import assets
from ..value.assets import AssetValue
from .common import (
    PickSpec,
    parse_pick_spec,
    build_name_index,
    resolve_player_query,
)


@dataclass
class SideResult:
    name: str
    items: list[AssetValue] = field(default_factory=list)

    @property
    def total(self) -> float:
        return sum(a.value for a in self.items)


@dataclass
class TradeEvaluation:
    basis: str
    qb_format: int
    side_a: SideResult
    side_b: SideResult
    num_teams: int = 12

    @property
    def delta(self) -> float:
        return self.side_a.total - self.side_b.total

    @property
    def larger(self) -> float:
        return max(self.side_a.total, self.side_b.total)

    @property
    def pct_gap(self) -> float:
        return abs(self.delta) / self.larger if self.larger > 0 else 0.0

    @property
    def verdict(self) -> str:
        g = self.pct_gap
        if g <= config.FAIRNESS_EVEN:
            return "EVEN"
        if g <= config.FAIRNESS_SLIGHT:
            return "SLIGHT EDGE"
        return "LOPSIDED"

    @property
    def favored(self) -> Optional[str]:
        if self.delta > 0:
            return self.side_a.name
        if self.delta < 0:
            return self.side_b.name
        return None


def _value_spec(spec: str, ctx, provider, name_index, num_teams) -> AssetValue:
    pick = parse_pick_spec(spec)
    if pick is not None:
        val = provider.pick_value(pick.season, pick.rnd, slot=pick.slot,
                                  tier=pick.tier, num_teams=num_teams)
        if pick.slot is not None:
            label = f"{pick.season} Pick {pick.rnd}.{pick.slot:02d}"
        elif pick.tier:
            label = f"{pick.season} {pick.tier} {assets._ORD.get(pick.rnd, pick.rnd)}"
        else:
            label = f"{pick.season} {assets._ORD.get(pick.rnd, pick.rnd)}"
        return AssetValue("pick", label, val, matched=True, detail={"spec": spec})

    match = resolve_player_query(spec, ctx.players_meta, name_index, provider)
    if match.sleeper_id is None:
        return AssetValue("player", f"{spec}  [UNMATCHED]", 0.0, matched=False,
                          flag="UNMATCHED_NAME", detail={"spec": spec})
    av = assets.value_player(provider, ctx.players_meta, match.sleeper_id)
    if match.note:
        av.flag = (av.flag + " " + match.note).strip()
    return av


def evaluate_trade(ctx, provider, side_a_specs: list[str], side_b_specs: list[str],
                   name_a: str = "Side A", name_b: str = "Side B") -> TradeEvaluation:
    name_index = build_name_index(ctx.players_meta)
    num_teams = ctx.newest.get("total_rosters") or 12

    def build(specs, name) -> SideResult:
        sr = SideResult(name=name)
        for spec in specs:
            spec = spec.strip()
            if spec:
                sr.items.append(_value_spec(spec, ctx, provider, name_index, num_teams))
        return sr

    return TradeEvaluation(
        basis=provider.basis,
        qb_format=ctx.qb_format,
        side_a=build(side_a_specs, name_a),
        side_b=build(side_b_specs, name_b),
        num_teams=num_teams,
    )


def format_evaluation(ev: TradeEvaluation) -> str:
    lines: list[str] = []
    lines.append(f"TRADE EVALUATION  [basis={ev.basis} | {ev.qb_format}QB]")
    lines.append("=" * 60)
    for side in (ev.side_a, ev.side_b):
        lines.append(f"\n{side.name}:")
        for a in side.items:
            flag = f"  <{a.flag}>" if a.flag else ""
            lines.append(f"    {a.value:8.0f}  {a.label}{flag}")
        lines.append(f"    {'-'*40}")
        lines.append(f"    {side.total:8.0f}  TOTAL")

    lines.append("\n" + "=" * 60)
    lines.append(f"  {ev.side_a.name} total : {ev.side_a.total:8.0f}")
    lines.append(f"  {ev.side_b.name} total : {ev.side_b.total:8.0f}")
    lines.append(f"  delta (A - B)   : {ev.delta:+8.0f}  ({ev.pct_gap*100:.1f}% of larger side)")
    verdict = ev.verdict
    if verdict == "EVEN":
        tail = f"within {config.FAIRNESS_EVEN*100:.0f}% -> fair"
    else:
        tail = f"favors {ev.favored}"
    lines.append(f"  VERDICT         : {verdict}  ({tail})")
    lines.append(
        f"  thresholds      : <={config.FAIRNESS_EVEN*100:.0f}% even, "
        f"<={config.FAIRNESS_SLIGHT*100:.0f}% slight edge, > lopsided"
    )
    if ev.basis in ("realized", "current"):
        lines.append("  NOTE: 'realized' values assets at CURRENT worth (outcome), "
                     "not their worth on a past trade date.")
    return "\n".join(lines)
