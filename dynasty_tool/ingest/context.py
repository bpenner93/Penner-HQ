"""The shared ingestion context consumed by all three analysis modules.

Build it once (``build_context``) and hand it to trade_eval / value_flow /
optimizer. It owns the chain, identity map, per-season roster maps, per-season
drafts, the derived QB format, and the Sleeper player metadata blob.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .chain import walk_chain
from .identity import Identity, build_identities, build_roster_maps, user_to_roster
from .drafts import SeasonDraft, build_drafts


def derive_qb_format(league: dict) -> int:
    """2 for superflex/2QB leagues, else 1 -- from the newest roster_positions."""
    rp = league.get("roster_positions") or []
    if "SUPER_FLEX" in rp or rp.count("QB") >= 2:
        return 2
    return 1


@dataclass
class LeagueContext:
    requested_league_id: str
    chain: list[dict]                                # oldest -> newest
    seasons: dict[str, dict]                         # league_id -> league obj
    newest: dict
    identities: dict[str, Identity]
    roster_maps: dict[str, dict[int, str]]           # league_id -> {roster_id: user_id}
    drafts_by_season: dict[str, list[SeasonDraft]]   # season -> [startup?, rookie]
    qb_format: int
    client: object
    players_meta: dict = field(default_factory=dict)

    # -- convenience --------------------------------------------------------
    @property
    def qb_label(self) -> str:
        return f"{self.qb_format}QB"

    def display(self, user_id: str | None) -> str:
        if user_id is None:
            return "(unknown)"
        idn = self.identities.get(str(user_id))
        return idn.canonical if idn else f"(user {user_id})"

    def newest_roster_map(self) -> dict[int, str]:
        return self.roster_maps.get(self.newest["league_id"], {})

    def newest_user_to_roster(self) -> dict[str, int]:
        return user_to_roster(self.newest_roster_map())

    def all_user_ids(self) -> list[str]:
        return list(self.identities.keys())


def build_context(client, league_id: str, full: bool = True) -> LeagueContext:
    """Assemble the shared context.

    full=True  : walk the whole previous_league_id chain (needed for value-flow,
                 trade history, cross-season identity aliasing, pick resolution).
    full=False : load only the requested season (fast) -- enough for roster value,
                 windows, sim, matchups; used by the cross-league portfolio view.
    """
    if full:
        chain = walk_chain(client, league_id)
    else:
        one = client.league(str(league_id))
        chain = [one] if one else []
    if not chain:
        raise RuntimeError(
            f"No leagues found walking the chain from league_id={league_id!r}. "
            "Check the id is a valid Sleeper NFL league."
        )
    identities = build_identities(client, chain)
    roster_maps = build_roster_maps(client, chain)
    drafts_by_season = build_drafts(client, chain)
    newest = chain[-1]
    return LeagueContext(
        requested_league_id=str(league_id),
        chain=chain,
        seasons={lg["league_id"]: lg for lg in chain},
        newest=newest,
        identities=identities,
        roster_maps=roster_maps,
        drafts_by_season=drafts_by_season,
        qb_format=derive_qb_format(newest),
        client=client,
        players_meta=client.players(),
    )
