"""R2: draft-pick ownership fields are ROSTER ids, resolved per-season."""
from __future__ import annotations

from ..ingest.context import build_context
from ..ingest.trades import normalize_trades
from ..ingest.drafts import resolve_pick
from .fixtures import FakeClient, U1, U2, U3


def _ctx():
    return build_context(FakeClient(), "L2021")


def test_pick_attributed_via_season_roster_map_not_raw_owner_id():
    ctx = _ctx()
    trades = normalize_trades(ctx.client, ctx.chain, ctx.roster_maps)
    trade = next(t for t in trades if t.league_id == "L2020")

    pick_transfers = [x for x in trade.transfers if x.asset.kind == "pick"]
    assert len(pick_transfers) == 1
    pt = pick_transfers[0]

    # owner_id/previous_owner_id were roster ids 1 and 2 in the 2020 league.
    # They must map to USER ids via the 2020 roster map, not be used raw.
    assert pt.receiver == U1   # roster 1 (2020) -> U1
    assert pt.giver == U2      # roster 2 (2020) -> U2
    assert pt.receiver not in (1, 2, "1", "2")


def test_pick_resolves_through_original_owner_slot():
    ctx = _ctx()
    trades = normalize_trades(ctx.client, ctx.chain, ctx.roster_maps)
    trade = next(t for t in trades if t.league_id == "L2020")
    pick = next(x.asset.pick for x in trade.transfers if x.asset.kind == "pick")

    res = resolve_pick(ctx.drafts_by_season, ctx.roster_maps, trade.league_id,
                       pick, trade.timestamp)
    # original owner roster_id=2 in 2020 -> U2 -> slot 2 in 2021 draft -> player "200".
    # A wrong read via the 2021 map (roster 2 -> U3 -> slot 3) would give "300".
    assert res.kind == "drafted"
    assert res.player_id == "200"
    assert res.orig_user == U2


def test_roster_map_is_per_season():
    ctx = _ctx()
    # roster_id 2 maps to different users in different seasons.
    assert ctx.roster_maps["L2020"][2] == U2
    assert ctx.roster_maps["L2021"][2] == U3
