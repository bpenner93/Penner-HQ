"""Normalize every trade across the whole chain into directed asset transfers,
keyed on ``user_id`` (R1) via each season's roster map (R2).

The atomic unit is a ``Transfer(giver, receiver, asset)``. Modeling each asset
as a directed edge (rather than a per-side bundle) makes pairwise flow exact
even for 3+ team trades, and it reads the giver/receiver straight from Sleeper's
own fields:
  * players : ``adds`` = {player_id: receiver_roster}, ``drops`` = {player_id: giver_roster}
  * picks   : ``previous_owner_id`` = giver roster, ``owner_id`` = receiver roster
  * faab    : waiver_budget[] = {sender, receiver, amount} (roster ids)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Sleeper trade status values we count. A vetoed/failed trade never moved value.
_COUNTED_STATUS = {None, "complete"}


@dataclass
class Asset:
    kind: str                     # 'player' | 'pick' | 'faab'
    player_id: Optional[str] = None
    pick: Optional[dict] = None   # raw draft_picks[] entry
    faab: Optional[int] = None


@dataclass
class Transfer:
    giver: Optional[str]          # user_id (None if unmappable)
    receiver: Optional[str]       # user_id
    asset: Asset


@dataclass
class NormalizedTrade:
    league_id: str
    season: str
    week: int
    tid: str
    timestamp: Optional[int]      # status_updated, epoch ms
    participants: list[str]       # user_ids
    transfers: list[Transfer] = field(default_factory=list)


def normalize_trades(client, chain: list[dict], roster_maps: dict[str, dict[int, str]]) -> list[NormalizedTrade]:
    """All completed trades across the chain, oldest league first."""
    out: list[NormalizedTrade] = []
    for league in chain:
        lid = league["league_id"]
        season = str(league["season"])
        rmap = roster_maps.get(lid, {})
        for week in range(0, 19):  # 0 = offseason
            for t in client.transactions(lid, week):
                if t.get("type") != "trade":
                    continue
                if t.get("status") not in _COUNTED_STATUS:
                    continue
                transfers: list[Transfer] = []

                adds = t.get("adds") or {}
                drops = t.get("drops") or {}
                for pid, recv_rid in adds.items():
                    giver_rid = drops.get(pid)
                    transfers.append(Transfer(
                        giver=rmap.get(giver_rid),
                        receiver=rmap.get(recv_rid),
                        asset=Asset("player", player_id=str(pid)),
                    ))

                for pk in (t.get("draft_picks") or []):
                    transfers.append(Transfer(
                        giver=rmap.get(pk.get("previous_owner_id")),
                        receiver=rmap.get(pk.get("owner_id")),
                        asset=Asset("pick", pick=pk),
                    ))

                for wb in (t.get("waiver_budget") or []):
                    transfers.append(Transfer(
                        giver=rmap.get(wb.get("sender")),
                        receiver=rmap.get(wb.get("receiver")),
                        asset=Asset("faab", faab=wb.get("amount")),
                    ))

                participants = [rmap.get(rid) for rid in (t.get("roster_ids") or [])]
                out.append(NormalizedTrade(
                    league_id=lid,
                    season=season,
                    week=week,
                    tid=str(t.get("transaction_id")),
                    timestamp=t.get("status_updated"),
                    participants=[p for p in participants if p is not None],
                    transfers=transfers,
                ))
    return out
