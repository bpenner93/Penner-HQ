"""Normalize an ESPN league into a LeagueContext the analyzer already understands.

ESPN's identity is the owner GUID (stable, like a Sleeper user_id); members[]
maps GUID->name. Players are keyed by espn_id -> gsis_id (crosswalk) so rosters
value off the same season projections. Lineup slots, records, and the schedule
translate straight into the Sleeper-shaped structures the analyzer consumes, via
a tiny fake client that serves rosters/matchups/state/players.
"""
from __future__ import annotations

import pandas as pd

from .context import LeagueContext
from .identity import Identity
from .. import config

# ESPN lineup-slot id -> analyzer token (optimizer._FLEX_MAP tokens)
_ESPN_SLOT = {
    0: "QB", 1: "QB", 2: "RB", 3: "WRRB_FLEX", 4: "WR", 5: "REC_FLEX", 6: "TE",
    7: "SUPER_FLEX", 16: "DST", 17: "K", 23: "FLEX", 20: "BN", 21: "IR", 24: "BN",
    8: "DL", 9: "DL", 10: "LB", 11: "DL", 12: "DB", 13: "DB", 14: "DB",
}
# ESPN defaultPositionId -> position
_ESPN_POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DST",
             9: "DL", 10: "LB", 11: "DL", 12: "DB"}


def _roster_positions(lineup_counts: dict) -> list[str]:
    toks: list[str] = []
    for slot, cnt in sorted((int(k), v) for k, v in lineup_counts.items()):
        tok = _ESPN_SLOT.get(slot)
        if tok and cnt:
            toks += [tok] * int(cnt)
    return toks


class _EspnFakeClient:
    """Serves the four methods the analyzer calls on ctx.client."""
    def __init__(self, rosters, matchups_by_week, state, players_meta):
        self._rosters = rosters
        self._mw = matchups_by_week
        self._state = state
        self._players = players_meta

    def rosters(self, _league_id):
        return self._rosters

    def matchups(self, _league_id, week):
        return self._mw.get(week, [])

    def state(self):
        return self._state

    def players(self):
        return self._players


def _players_meta_from_rankings() -> dict:
    sr = pd.read_parquet(config.SEASON_RANKINGS)
    meta = {}
    for row in sr.itertuples(index=False):
        meta[str(row.player_id)] = {
            "first_name": row.player_display_name, "last_name": "",
            "position": row.position, "team": row.team, "age": getattr(row, "age", None),
        }
    return meta


def resolve_espn_team(ctx: LeagueContext, token):
    """Resolve --team for ESPN: a numeric teamId, an owner GUID, or a name."""
    if token is None:
        return None
    token = str(token).strip()
    rmap = ctx.roster_maps.get(ctx.newest["league_id"], {})
    if token.isdigit() and int(token) in rmap:      # teamId
        return rmap[int(token)]
    if token in ctx.identities:                      # GUID
        return token
    low = token.lower()
    for uid, idn in ctx.identities.items():          # display name
        if idn.canonical.lower() == low or low in [n.lower() for n in idn.names]:
            return uid
    return None


def build_espn_context(espn_client, inner_provider, league_id: str, season: str) -> LeagueContext:
    data = espn_client.league(str(league_id), str(season))
    s = data.get("settings", {})
    lineup = s.get("rosterSettings", {}).get("lineupSlotCounts", {}) or {}
    roster_positions = _roster_positions(lineup)
    sched = s.get("scheduleSettings", {})
    playoff_teams = sched.get("playoffTeamCount", 6)
    reg_weeks = sched.get("matchupPeriodCount", 14)

    members = {m["id"]: (f"{m.get('firstName', '')} {m.get('lastName', '')}".strip()
                         or m.get("displayName") or m["id"])
               for m in data.get("members", [])}

    players_meta = _players_meta_from_rankings()
    e2g = inner_provider.espn_to_gsis

    rosters, roster_map = [], {}
    for t in data.get("teams", []):
        tid = t["id"]
        guid = (t.get("owners") or [f"team{tid}"])[0]
        roster_map[tid] = guid
        players = []
        for e in (t.get("roster", {}) or {}).get("entries", []) or []:
            p = e.get("playerPoolEntry", {}).get("player", {})
            g = e2g.get(str(p.get("id")))
            if not g:
                continue
            players.append(g)
            if g not in players_meta:  # rostered but unprojected -> keep position for lineup fill
                players_meta[g] = {"first_name": p.get("fullName", ""), "last_name": "",
                                   "position": _ESPN_POS.get(p.get("defaultPositionId"), "?"),
                                   "team": None, "age": None}
        rec = (t.get("record", {}) or {}).get("overall", {}) or {}
        fpts = rec.get("pointsFor", 0.0) or 0.0
        rosters.append({
            "roster_id": tid, "owner_id": guid, "players": players,
            "settings": {"wins": rec.get("wins", 0), "losses": rec.get("losses", 0),
                         "ties": rec.get("ties", 0), "fpts": int(fpts),
                         "fpts_decimal": int(round((fpts - int(fpts)) * 100))},
        })

    matchups_by_week: dict[int, list] = {}
    for g in data.get("schedule", []) or []:
        wk = g.get("matchupPeriodId")
        home, away = g.get("home"), g.get("away")
        if not home or not away:
            continue
        for side in (home, away):
            matchups_by_week.setdefault(wk, []).append({
                "matchup_id": g.get("id"), "roster_id": side.get("teamId"),
                "points": side.get("totalPoints", 0.0) or 0.0, "starters_points": [],
            })

    cur_week = data.get("status", {}).get("currentMatchupPeriod", 0) or 0
    status = ("complete" if cur_week > reg_weeks else
              "in_season" if cur_week >= 1 else "pre_draft")
    league = {
        "league_id": str(league_id), "season": str(season), "name": s.get("name", "ESPN league"),
        "status": status, "total_rosters": s.get("size", len(rosters)),
        "roster_positions": roster_positions,
        "settings": {"type": 0, "playoff_teams": playoff_teams,
                     "playoff_week_start": reg_weeks + 1},
    }
    identities = {guid: Identity(user_id=guid, names=[members.get(guid, f"({guid})")],
                                 canonical=members.get(guid, f"({guid})"))
                  for guid in roster_map.values()}
    qb_format = 2 if int(lineup.get("7", 0) or 0) > 0 or int(lineup.get("0", 0) or 0) >= 2 else 1

    return LeagueContext(
        requested_league_id=str(league_id), chain=[league],
        seasons={str(league_id): league}, newest=league, identities=identities,
        roster_maps={str(league_id): roster_map}, drafts_by_season={},
        qb_format=qb_format,
        client=_EspnFakeClient(rosters, matchups_by_week,
                               {"week": cur_week, "season": str(season)}, players_meta),
        players_meta=players_meta,
    )
