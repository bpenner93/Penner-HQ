"""Hermetic fixtures: a FakeClient and tiny value frames -- no network.

The synthetic league is built specifically to exercise R1-R4:

  * U1 uses two display names across seasons (LACrams -> TheGrimGaffer)  -> alias.
  * U2 "daAverageJoes" and U3 "DaAverageJoes65" are near-identical names but
    DIFFERENT user_ids                                                  -> stay separate.
  * roster_id -> user_id is shuffled between 2020 and 2021               -> R2 matters.
  * a 2020 trade moves U2's 2021 R1 pick to U1 (resolves to a real player).
  * a 2021 trade moves a 2027 R1 pick with no draft yet                  -> R4 future.
  * player 999 has no value row                                         -> join coverage.
"""
from __future__ import annotations

import pandas as pd

U1, U2, U3 = "U1", "U2", "U3"

LEAGUES = {
    "L2021": {"league_id": "L2021", "season": "2021", "previous_league_id": "L2020",
              "name": "Test League", "total_rosters": 3,
              "roster_positions": ["QB", "RB", "WR", "FLEX", "BN"],
              "settings": {"type": 2}, "status": "complete"},
    "L2020": {"league_id": "L2020", "season": "2020", "previous_league_id": "0",
              "name": "Test League", "total_rosters": 3,
              "roster_positions": ["QB", "RB", "WR", "FLEX", "BN"],
              "settings": {"type": 2}, "status": "complete"},
}

USERS = {
    "L2020": [
        {"user_id": U1, "display_name": "LACrams"},
        {"user_id": U2, "display_name": "daAverageJoes"},
        {"user_id": U3, "display_name": "DaAverageJoes65"},
    ],
    "L2021": [
        {"user_id": U1, "display_name": "TheGrimGaffer"},   # alias of LACrams
        {"user_id": U2, "display_name": "daAverageJoes"},
        {"user_id": U3, "display_name": "DaAverageJoes65"},
    ],
}

# roster_id -> owner_id, deliberately shuffled between seasons (R2)
ROSTERS = {
    "L2020": [
        {"roster_id": 1, "owner_id": U1, "players": ["1", "200"]},
        {"roster_id": 2, "owner_id": U2, "players": ["50"]},
        {"roster_id": 3, "owner_id": U3, "players": ["999"]},
    ],
    "L2021": [
        {"roster_id": 1, "owner_id": U2, "players": ["50"]},
        {"roster_id": 2, "owner_id": U3, "players": ["999", "300"]},
        {"roster_id": 3, "owner_id": U1, "players": ["1", "200", "100"]},
    ],
}

DRAFTS = {
    "L2020": [],
    "L2021": [{
        "draft_id": "D2021", "season": "2021", "type": "linear", "status": "complete",
        "start_time": 1000, "settings": {"rounds": 1},
        "draft_order": {U1: 1, U2: 2, U3: 3},  # user_id -> slot
    }],
}

DRAFT_PICKS = {
    "D2021": [
        {"round": 1, "pick_no": 1, "draft_slot": 1, "roster_id": 3, "player_id": "100"},
        {"round": 1, "pick_no": 2, "draft_slot": 2, "roster_id": 3, "player_id": "200"},
        {"round": 1, "pick_no": 3, "draft_slot": 3, "roster_id": 2, "player_id": "300"},
    ],
}

# 2020 trade: U2 sends player 50 + U2's 2021 R1 pick to U1.
#   pick: original owner roster_id=2 (U2 in 2020), previous_owner_id=2 (U2), owner_id=1 (U1)
#   -> under the 2020 map slot(U2)=2 -> draft (1,2) -> player "200".
#   (a wrong 2021-map read would give roster 2 -> U3 -> slot 3 -> "300".)
_TRADE_R2 = {
    "type": "trade", "status": "complete", "transaction_id": "t1",
    "status_updated": 1600000000000, "roster_ids": [1, 2],
    "adds": {"50": 1}, "drops": {"50": 2},
    "draft_picks": [{"season": "2021", "round": 1, "league_id": None,
                     "roster_id": 2, "owner_id": 1, "previous_owner_id": 2}],
    "waiver_budget": [],
}

# 2021 trade: a 2027 R1 pick with no 2027 draft -> UNRESOLVED_FUTURE_PICK (R4).
#   2021 roster map: roster 1 -> U2 (giver), roster 3 -> U1 (receiver).
_TRADE_FUTURE = {
    "type": "trade", "status": "complete", "transaction_id": "t2",
    "status_updated": 1610000000000, "roster_ids": [1, 3],
    "adds": None, "drops": None,
    "draft_picks": [{"season": "2027", "round": 1, "league_id": None,
                     "roster_id": 1, "owner_id": 3, "previous_owner_id": 1}],
    "waiver_budget": [],
}

TRANSACTIONS = {
    "L2020": {0: [_TRADE_R2]},
    "L2021": {0: [_TRADE_FUTURE]},
}

PLAYERS_META = {
    "1": {"first_name": "Star", "last_name": "Wr", "position": "WR", "age": 24},
    "50": {"first_name": "Traded", "last_name": "Player", "position": "WR", "age": 26},
    "100": {"first_name": "Pick", "last_name": "Onehundred", "position": "RB", "age": 22},
    "200": {"first_name": "Pick", "last_name": "Twohundred", "position": "RB", "age": 22},
    "300": {"first_name": "Pick", "last_name": "Threehundred", "position": "WR", "age": 22},
    "999": {"first_name": "Unvalued", "last_name": "Idp", "position": "LB", "age": 30},
}


class FakeClient:
    """Implements the SleeperClient surface used by ingestion, from fixtures."""

    def __init__(self) -> None:
        self.fetch_count = 0
        self.cache = None

    def league(self, lid):
        return LEAGUES.get(lid)

    def users(self, lid):
        return USERS.get(lid, [])

    def rosters(self, lid):
        return ROSTERS.get(lid, [])

    def transactions(self, lid, week):
        return TRANSACTIONS.get(lid, {}).get(week, [])

    def drafts(self, lid):
        return DRAFTS.get(lid, [])

    def draft_picks(self, did):
        return DRAFT_PICKS.get(did, [])

    def players(self):
        return PLAYERS_META


def make_test_provider(qb_format: int = 1):
    """A CurrentValueProvider over tiny in-memory frames (no network)."""
    from ..value.current_provider import CurrentValueProvider

    players_df = pd.DataFrame([
        {"player": "Star Wr", "pos": "WR", "ecr_1qb": 1.0, "ecr_2qb": 1.0,
         "value_1qb": 9000, "value_2qb": 9000, "scrape_date": "2026-07-03", "fp_id": 1000},
        {"player": "Pick Twohundred", "pos": "RB", "ecr_1qb": 10.0, "ecr_2qb": 10.0,
         "value_1qb": 5000, "value_2qb": 5200, "scrape_date": "2026-07-03", "fp_id": 2000},
        {"player": "Traded Player", "pos": "WR", "ecr_1qb": 40.0, "ecr_2qb": 40.0,
         "value_1qb": 1500, "value_2qb": 1500, "scrape_date": "2026-07-03", "fp_id": 5000},
        {"player": "Low Guy", "pos": "WR", "ecr_1qb": 150.0, "ecr_2qb": 150.0,
         "value_1qb": 100, "value_2qb": 100, "scrape_date": "2026-07-03", "fp_id": 3000},
    ])
    picks_df = pd.DataFrame([
        {"player": "2027 1st", "pos": "PICK", "ecr_1qb": 20.0, "ecr_2qb": 20.0, "pick": None},
        {"player": "2027 2nd", "pos": "PICK", "ecr_1qb": 70.0, "ecr_2qb": 70.0, "pick": None},
    ])
    ids_df = pd.DataFrame([
        {"sleeper_id": 1, "fantasypros_id": 1000, "name": "Star Wr", "ktc_id": None},
        {"sleeper_id": 200, "fantasypros_id": 2000, "name": "Pick Twohundred", "ktc_id": None},
        {"sleeper_id": 50, "fantasypros_id": 5000, "name": "Traded Player", "ktc_id": None},
        # NOTE: no row for sleeper_id 999 -> must be reported unmatched, not hidden.
    ])
    return CurrentValueProvider(players_df, picks_df, ids_df, qb_format=qb_format)
