"""Resolve a traded draft pick to *what it became* (R2, R3-realized, R4).

A pick in a trade is identified by (target season, round, ORIGINAL owner).
The original owner is given as a ``roster_id`` scoped to the transaction's
league, so we map it to a ``user_id`` first (R2). Then, in the target season's
draft, that user's ``draft_slot`` comes from the draft's ``draft_order``
(``{user_id: slot}``) -- because ``slot_to_roster_id`` is null on completed
Sleeper drafts. We resolve the pick by matching ``(round, draft_slot)``, which
is robust to linear vs snake ordering.

Multiple drafts per season: the inaugural season has BOTH a deep snake *startup*
draft and a linear *rookie* draft, and both cover low rounds. We disambiguate by
timing -- a traded pick is for the next draft that occurs at/after the trade --
falling back to the shallowest (rookie) draft. This also lets deep startup-round
picks (e.g. round 22) resolve against the startup draft instead of vanishing.

Outcomes (never dropped -- R4):
  * ``drafted`` : the draft is complete and the slot maps to a player.
  * ``slot``    : the pick's slot is known but not yet drafted (value by slot).
  * ``future``  : slot not determinable (pick order not set) -> UNRESOLVED_FUTURE_PICK.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

UNRESOLVED_FUTURE_PICK = "UNRESOLVED_FUTURE_PICK"


@dataclass
class SeasonDraft:
    season: str
    league_id: str
    draft_id: str
    status: str
    dtype: str
    rounds: Optional[int]
    num_teams: int
    start_time: Optional[int]                    # epoch ms
    draft_order: dict[str, int]                  # user_id -> slot
    picks_by_round_slot: dict[tuple[int, int], dict]  # (round, slot) -> pick

    @property
    def complete(self) -> bool:
        return self.status == "complete"


@dataclass
class PickResolution:
    season: str
    rnd: int
    kind: str                       # 'drafted' | 'slot' | 'future'
    player_id: Optional[str] = None
    slot: Optional[int] = None
    orig_user: Optional[str] = None
    draft_kind: str = ""            # 'rookie' | 'startup' | '' (which draft we used)
    note: str = ""

    @property
    def unresolved_future(self) -> bool:
        return self.note == UNRESOLVED_FUTURE_PICK


def build_drafts(client, chain: list[dict]) -> dict[str, list[SeasonDraft]]:
    """season -> [SeasonDraft, ...] sorted by start_time ascending.

    Keeps every draft a season has (startup + rookie) so pick resolution can
    pick the right one.
    """
    by_season: dict[str, list[SeasonDraft]] = {}
    for league in chain:
        lid = league["league_id"]
        season = str(league["season"])
        drafts: list[SeasonDraft] = []
        for d in client.drafts(lid):
            picks = client.draft_picks(d["draft_id"])
            prs: dict[tuple[int, int], dict] = {}
            for p in picks:
                rnd, slot = p.get("round"), p.get("draft_slot")
                if rnd is not None and slot is not None:
                    prs[(int(rnd), int(slot))] = p
            order = {str(k): int(v) for k, v in (d.get("draft_order") or {}).items()}
            drafts.append(SeasonDraft(
                season=season,
                league_id=lid,
                draft_id=d["draft_id"],
                status=d.get("status", ""),
                dtype=d.get("type", ""),
                rounds=(d.get("settings") or {}).get("rounds"),
                num_teams=league.get("total_rosters") or len(order) or 12,
                start_time=d.get("start_time"),
                draft_order=order,
                picks_by_round_slot=prs,
            ))
        drafts.sort(key=lambda sd: sd.start_time or 0)
        by_season[season] = drafts
    return by_season


def _draft_kind(sd: SeasonDraft, season_drafts: list[SeasonDraft]) -> str:
    """Label a draft rookie vs startup: the deepest draft of a multi-draft
    season is the startup; a lone draft is the rookie draft."""
    if len(season_drafts) < 2:
        return "rookie"
    deepest = max(season_drafts, key=lambda d: d.rounds or 0)
    return "startup" if sd is deepest else "rookie"


def _select_draft(season_drafts: list[SeasonDraft], rnd: int,
                  trade_ts: Optional[int]) -> Optional[SeasonDraft]:
    if not season_drafts:
        return None
    cands = [d for d in season_drafts if d.rounds is None or rnd <= d.rounds]
    if not cands:
        cands = list(season_drafts)  # round exceeds all -> let match fail to slot
    if len(cands) == 1:
        return cands[0]
    # Ambiguous (e.g. inaugural startup + rookie both cover low rounds):
    # the pick is for the next draft occurring at/after the trade.
    if trade_ts is not None:
        future = [d for d in cands if d.start_time and d.start_time >= trade_ts]
        if future:
            return min(future, key=lambda d: d.start_time or 0)
    # Fallback: the shallowest draft (the rookie draft).
    return min(cands, key=lambda d: (d.rounds or 9999))


def resolve_pick(
    drafts_by_season: dict[str, list[SeasonDraft]],
    roster_maps: dict[str, dict[int, str]],
    txn_league_id: str,
    pick: dict,
    trade_ts: Optional[int] = None,
) -> PickResolution:
    """Resolve one ``draft_picks[]`` entry from a trade to its outcome."""
    season = str(pick["season"])
    rnd = int(pick["round"])
    orig_roster = pick.get("roster_id")
    orig_user = roster_maps.get(txn_league_id, {}).get(orig_roster)

    season_drafts = drafts_by_season.get(season, [])
    sd = _select_draft(season_drafts, rnd, trade_ts)
    slot = sd.draft_order.get(orig_user) if (sd and orig_user is not None) else None
    if slot is not None:
        slot = int(slot)
    dkind = _draft_kind(sd, season_drafts) if sd else ""

    if sd is not None and sd.complete:
        if slot is not None:
            p = sd.picks_by_round_slot.get((rnd, slot))
            if p and p.get("player_id"):
                return PickResolution(season, rnd, "drafted", player_id=str(p["player_id"]),
                                      slot=slot, orig_user=orig_user, draft_kind=dkind)
            return PickResolution(season, rnd, "slot", slot=slot, orig_user=orig_user,
                                  draft_kind=dkind, note="drafted_pick_unmatched")
        return PickResolution(season, rnd, "future", orig_user=orig_user,
                              draft_kind=dkind, note=UNRESOLVED_FUTURE_PICK)

    # Draft not complete (pre_draft) or no draft exists yet for this season.
    if slot is not None:
        return PickResolution(season, rnd, "slot", slot=slot, orig_user=orig_user,
                              draft_kind=dkind, note="future_slot_known")
    return PickResolution(season, rnd, "future", orig_user=orig_user,
                          draft_kind=dkind, note=UNRESOLVED_FUTURE_PICK)
