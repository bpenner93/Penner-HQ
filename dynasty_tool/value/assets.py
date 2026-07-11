"""Unify player, pick, and future-pick valuation into a single ``AssetValue``.

Players are valued directly. Picks are valued through their ``PickResolution``
(R3-realized): a drafted pick is valued by the player it became; a known-slot
future pick by its slot value; an undetermined future pick by its round's
generic value and flagged ``UNRESOLVED_FUTURE_PICK`` (R4) -- never dropped.

Player labels/positions come from the Sleeper players/nfl blob, re-read at
valuation time rather than carried forward from a summary (R5).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..ingest.drafts import PickResolution, UNRESOLVED_FUTURE_PICK

_ORD = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th", 6: "6th", 7: "7th"}


@dataclass
class AssetValue:
    kind: str                 # 'player' | 'pick' | 'faab'
    label: str
    value: float
    matched: bool = True
    flag: str = ""            # UNMATCHED_PLAYER | UNRESOLVED_FUTURE_PICK | ...
    detail: dict = field(default_factory=dict)


def _player_name(players_meta: dict, sleeper_id: str) -> tuple[str, str]:
    meta = players_meta.get(str(sleeper_id)) or {}
    name = f"{meta.get('first_name', '')} {meta.get('last_name', '')}".strip()
    if not name:
        name = str(meta.get("full_name") or f"player {sleeper_id}")
    return name, meta.get("position") or "?"


def value_player(provider, players_meta: dict, sleeper_id: str) -> AssetValue:
    name, pos = _player_name(players_meta, sleeper_id)
    val, matched = provider.player_value(sleeper_id, name=name)
    return AssetValue(
        kind="player",
        label=f"{name} ({pos})",
        value=val,
        matched=matched,
        flag="" if matched else "UNMATCHED_PLAYER",
        detail={"sleeper_id": str(sleeper_id), "pos": pos},
    )


def value_pick(provider, players_meta: dict, res: PickResolution,
               num_teams: int = 12) -> AssetValue:
    ordn = _ORD.get(res.rnd, f"{res.rnd}th")

    if res.kind == "drafted":
        inner = value_player(provider, players_meta, res.player_id)
        return AssetValue(
            kind="pick",
            label=f"{res.season} {ordn} [{res.draft_kind}] -> {inner.label}",
            value=inner.value,
            matched=inner.matched,
            flag=inner.flag,
            detail={"resolution": "drafted", "became": inner.detail, "round": res.rnd,
                    "season": res.season, "draft_kind": res.draft_kind},
        )

    if res.kind == "slot":
        val = provider.pick_value(res.season, res.rnd, slot=res.slot, num_teams=num_teams)
        slot_txt = f"{res.rnd}.{res.slot:02d}" if res.slot is not None else ordn
        flag = "" if res.note == "future_slot_known" else res.note.upper()
        return AssetValue(
            kind="pick",
            label=f"{res.season} Pick {slot_txt}",
            value=val,
            matched=True,
            flag=flag,
            detail={"resolution": "slot", "round": res.rnd, "slot": res.slot,
                    "season": res.season},
        )

    # future -- slot not determinable
    val = provider.pick_value(res.season, res.rnd, num_teams=num_teams)
    return AssetValue(
        kind="pick",
        label=f"{res.season} {ordn} (slot TBD)",
        value=val,
        matched=True,
        flag=UNRESOLVED_FUTURE_PICK,
        detail={"resolution": "future", "round": res.rnd, "season": res.season},
    )


def value_faab(amount: Optional[int]) -> AssetValue:
    """FAAB dollars are not on the dynasty-value scale; tracked, valued 0 in points."""
    amt = amount or 0
    return AssetValue(
        kind="faab",
        label=f"${amt} FAAB",
        value=0.0,
        matched=True,
        flag="FAAB_NOT_IN_VALUE_SCALE",
        detail={"faab": amt},
    )
