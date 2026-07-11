"""Normalize an MFL league into a LeagueContext the analyzer understands.

MFL identity is the franchise id ("0001", stable); its ``name`` is the manager.
Players are keyed by mfl_id -> gsis (crosswalk) so rosters value off the same
projections. Starter position ranges (e.g. "2-3") become fixed slots + flex;
standings and the weekly schedule map to the Sleeper-shaped structures via a
small fake client.
"""
from __future__ import annotations

from .context import LeagueContext
from .identity import Identity
from .mfl_client import aslist
from .espn_context import _players_meta_from_rankings, _EspnFakeClient

_MFL_POS = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE", "PK": "K", "Def": "DST", "K": "K"}


def _roster_positions(starters: dict) -> tuple[list[str], int]:
    """MFL starters -> (tokens, qb_min). '2-3' means min 2 with the extra as flex."""
    count = int(starters.get("count", 0) or 0)
    fixed: list[str] = []
    total_min = 0
    qb_min = 0
    for p in aslist(starters.get("position")):
        name = _MFL_POS.get(p.get("name"), p.get("name"))
        lim = str(p.get("limit", "0"))
        mn, mx = (lim.split("-") + [lim.split("-")[0]])[:2] if "-" in lim else (lim, lim)
        mn, mx = int(mn), int(mx)
        fixed += [name] * mn
        total_min += mn
        if name == "QB":
            qb_min = mn
    flex_n = max(0, count - total_min)
    return fixed + ["FLEX"] * flex_n, qb_min


def resolve_mfl_team(ctx: LeagueContext, token):
    """Resolve --team for MFL: a franchise id ("0001"/"1") or a manager name."""
    if token is None:
        return None
    token = str(token).strip()
    ids = ctx.identities
    if token in ids:
        return token
    padded = token.zfill(4)
    if padded in ids:
        return padded
    low = token.lower()
    for uid, idn in ids.items():
        if idn.canonical.lower() == low or low in [n.lower() for n in idn.names]:
            return uid
    return None


def build_mfl_context(mfl_client, inner_provider, league_id: str, year: str) -> LeagueContext:
    L = mfl_client.league(year, league_id)
    roster_positions, qb_min = _roster_positions(L.get("starters", {}) or {})
    reg_weeks = int(L.get("lastRegularSeasonWeek") or 14)

    franchises = aslist((L.get("franchises") or {}).get("franchise"))
    names = {f["id"]: (f.get("name") or f"Franchise {f['id']}") for f in franchises}

    # standings -> per-franchise record + points
    stg = {f["id"]: f for f in aslist(mfl_client.standings(year, league_id).get("franchise"))}
    completed = 0
    for f in stg.values():
        completed = max(completed, int(f.get("h2hw", 0) or 0) + int(f.get("h2hl", 0) or 0)
                        + int(f.get("h2ht", 0) or 0))

    players_meta = _players_meta_from_rankings()
    m2g = inner_provider.mfl_to_gsis

    ros = {f["id"]: f for f in aslist(mfl_client.rosters(year, league_id).get("franchise"))}
    rosters, roster_map = [], {}
    for fid in names:
        roster_map[fid] = fid  # franchise id is both roster_id and user_id
        players = []
        for p in aslist(ros.get(fid, {}).get("player")):
            g = m2g.get(str(p.get("id")))
            if g:
                players.append(g)
        rec = stg.get(fid, {})
        pf = float(rec.get("pf", 0) or 0)
        rosters.append({
            "roster_id": fid, "owner_id": fid, "players": players,
            "settings": {"wins": int(rec.get("h2hw", 0) or 0), "losses": int(rec.get("h2hl", 0) or 0),
                         "ties": int(rec.get("h2ht", 0) or 0), "fpts": int(pf),
                         "fpts_decimal": int(round((pf - int(pf)) * 100))},
        })

    # schedule -> matchups by week
    matchups_by_week: dict[int, list] = {}
    for w in aslist(mfl_client.schedule(year, league_id).get("weeklySchedule")):
        wk = int(w.get("week", 0) or 0)
        for i, m in enumerate(aslist(w.get("matchup"))):
            for fr in aslist(m.get("franchise")):
                sc = fr.get("score")
                matchups_by_week.setdefault(wk, []).append({
                    "matchup_id": f"{wk}_{i}", "roster_id": fr.get("id"),
                    "points": float(sc) if sc not in (None, "") else 0.0, "starters_points": [],
                })

    status = ("complete" if completed >= reg_weeks and completed > 0 else
              "in_season" if completed > 0 else "pre_draft")
    league = {
        "league_id": str(league_id), "season": str(year), "name": L.get("name", "MFL league"),
        "status": status, "total_rosters": len(names), "roster_positions": roster_positions,
        "settings": {"type": 0, "playoff_teams": 6, "playoff_week_start": reg_weeks + 1},
    }
    identities = {fid: Identity(user_id=fid, names=[nm], canonical=nm) for fid, nm in names.items()}
    qb_format = 2 if qb_min >= 2 else 1

    return LeagueContext(
        requested_league_id=str(league_id), chain=[league],
        seasons={str(league_id): league}, newest=league, identities=identities,
        roster_maps={str(league_id): roster_map}, drafts_by_season={},
        qb_format=qb_format,
        client=_EspnFakeClient(rosters, matchups_by_week,
                               {"week": completed, "season": str(year)}, players_meta),
        players_meta=players_meta,
    )
