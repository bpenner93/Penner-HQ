"""R4: undrafted future picks are flagged, never silently dropped or zeroed."""
from __future__ import annotations

from ..ingest.context import build_context
from ..ingest.trades import normalize_trades
from ..ingest.drafts import resolve_pick, UNRESOLVED_FUTURE_PICK
from ..value import assets
from ..analysis.common import value_transfers
from .fixtures import FakeClient, make_test_provider, U1, U2


def _ctx():
    return build_context(FakeClient(), "L2021")


def test_future_pick_flagged_unresolved():
    ctx = _ctx()
    trades = normalize_trades(ctx.client, ctx.chain, ctx.roster_maps)
    trade = next(t for t in trades if t.league_id == "L2021")
    pick = next(x.asset.pick for x in trade.transfers if x.asset.kind == "pick")

    res = resolve_pick(ctx.drafts_by_season, ctx.roster_maps, trade.league_id,
                       pick, trade.timestamp)
    # 2027 draft does not exist -> future, flagged, not dropped.
    assert res.kind == "future"
    assert res.note == UNRESOLVED_FUTURE_PICK


def test_future_pick_not_dropped_and_carries_value():
    ctx = _ctx()
    provider = make_test_provider()
    trades = normalize_trades(ctx.client, ctx.chain, ctx.roster_maps)
    trade = next(t for t in trades if t.league_id == "L2021")

    valued = value_transfers(ctx, provider, trade)
    picks = [vt for vt in valued if vt.av.kind == "pick"]
    assert len(picks) == 1  # the pick survives as an asset (not dropped)
    av = picks[0].av
    assert av.flag == UNRESOLVED_FUTURE_PICK
    assert av.value > 0            # generic "2027 1st" value, not zeroed
    assert picks[0].giver == U2 and picks[0].receiver == U1


def test_known_slot_future_pick_values_by_slot():
    # A pre-draft (not-complete) draft WITH an order gives a known slot -> slot value.
    from ..ingest.drafts import SeasonDraft
    provider = make_test_provider()
    sd = SeasonDraft(season="2026", league_id="L", draft_id="d", status="pre_draft",
                     dtype="linear", rounds=4, num_teams=12, start_time=1,
                     draft_order={U2: 5}, picks_by_round_slot={})
    res = resolve_pick({"2026": [sd]}, {"LX": {9: U2}}, "LX",
                       {"season": "2026", "round": 1, "roster_id": 9,
                        "owner_id": 1, "previous_owner_id": 9})
    assert res.kind == "slot"
    assert res.slot == 5
    assert res.note == "future_slot_known"
    av = assets.value_pick(provider, {}, res)
    assert av.flag == ""  # a known-slot future pick is resolved, not flagged unresolved
