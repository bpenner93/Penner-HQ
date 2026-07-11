"""ESPN lineup writer — UNOFFICIAL (reverse-engineered private API).

Builds the standard ROSTER transaction (move each player to its target
lineupSlotId) that the community's espn-api uses, and POSTs it to the writes
host with the user's SWID + espn_s2 cookies. ESPN does not document or support
this, so it is EXPERIMENTAL: dry-run shows the exact payload; commit fires it and
reports ESPN's response. Verify the dry-run against your league before committing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Standard ESPN lineupSlotId values.
ESPN_SLOT = {"QB": 0, "RB": 2, "WR": 4, "TE": 6, "FLEX": 23, "SUPER_FLEX": 7,
             "OP": 7, "DST": 16, "K": 17, "BN": 20, "BE": 20, "IR": 21}
WRITES_HOST = "https://lm-api-writes.fantasy.espn.com"


@dataclass
class EspnSubmission:
    week: int
    team_id: int
    targets: list[tuple]                      # (pid, name, pos, slot, slot_id)
    url: str
    payload: dict
    ready: bool = False                       # have current slots -> can commit
    note: str = ""


class EspnLineupWriter:
    def __init__(self, year, league_id, team_id):
        self.year = str(year)
        self.lid = str(league_id)
        self.team_id = int(team_id)

    def build(self, plan, week: int, current_slot: Optional[dict[str, int]] = None,
              swid: str = "") -> EspnSubmission:
        targets, items = [], []
        for s in plan.starters:
            to_id = ESPN_SLOT.get(s.slot, ESPN_SLOT.get(s.pos, 20))
            targets.append((s.pid, s.name, s.pos, s.slot, to_id))
            if current_slot is not None:
                items.append({"playerId": int(s.pid) if str(s.pid).isdigit() else s.pid,
                              "type": "LINEUP",
                              "fromLineupSlotId": current_slot.get(str(s.pid), 20),
                              "toLineupSlotId": to_id})
        url = (f"{WRITES_HOST}/apis/v3/games/ffl/seasons/{self.year}"
               f"/segments/0/leagues/{self.lid}/transactions/")
        payload = {"isLeagueManager": False, "teamId": self.team_id, "type": "ROSTER",
                   "memberId": swid, "scoringPeriodId": int(week),
                   "executionType": "EXECUTE", "items": items}
        ready = current_slot is not None
        note = "" if ready else ("current-lineup slot ids unavailable — commit disabled "
                                 "until the ESPN read exposes each player's lineupSlotId")
        return EspnSubmission(int(week), self.team_id, targets, url, payload, ready, note)

    def commit(self, submission: EspnSubmission, swid: str, espn_s2: str) -> dict:
        if not submission.ready:
            return {"error": submission.note}
        import requests
        cookies = {"SWID": swid, "espn_s2": espn_s2}
        headers = {"Content-Type": "application/json",
                   "x-fantasy-source": "kona", "x-fantasy-platform": "kona-PROD"}
        r = requests.post(submission.url, json=submission.payload,
                          cookies=cookies, headers=headers, timeout=30)
        try:
            return {"status": r.status_code, "body": r.json()}
        except Exception:
            return {"status": r.status_code, "text": r.text[:400]}
