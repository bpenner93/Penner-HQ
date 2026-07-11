"""MFL lineup writer — the OFFICIAL, supported path (import?TYPE=lineup).

Builds the exact request MFL documents: STARTERS = comma-separated MFL player
ids for the week. Offense comes from the analyzer (mapped gsis -> mfl_id); K/DST
are pulled from the raw roster (they carry no projection) so the submitted
lineup is complete. Dry-run returns the request for review; commit logs in and
POSTs it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

_K_POS = {"PK", "K"}
_DST_POS = {"Def", "DEF", "DST", "DF"}


@dataclass
class MflSubmission:
    week: int
    starter_ids: list[str]
    labels: list[tuple]                       # (mfl_id, name, pos, slot)
    url: str
    params: dict
    filled_from_roster: list[tuple] = field(default_factory=list)  # K/DST added by the writer
    missing: list[str] = field(default_factory=list)   # required slots we couldn't fill


class MflLineupWriter:
    def __init__(self, mfl_client, year, league_id, franchise_id, gsis_to_mfl):
        self.c = mfl_client
        self.year = str(year)
        self.lid = str(league_id)
        self.fid = franchise_id
        self.g2m = gsis_to_mfl

    def build(self, plan, roster_mfl_ids: list[str], roster_positions: list[str]) -> MflSubmission:
        used: set[str] = set()
        starters: list[str] = []
        labels: list[tuple] = []
        for s in plan.starters:                         # analyzer-optimized offense
            mid = self.g2m.get(str(s.pid))
            if mid and mid not in used:
                starters.append(mid)
                used.add(mid)
                labels.append((mid, s.name, s.pos, s.slot))

        # fill only the DEFICIT of required K/DST slots the analyzer didn't already
        # cover (it fills the K slot if a kicker is rostered; DST carries no value)
        have = {}
        for _mid, _nm, pos, _slot in labels:
            have[pos] = have.get(pos, 0) + 1
        remaining = [i for i in roster_mfl_ids if i not in used]
        det = self.c.players_detail(self.year, self.lid, remaining)
        extra: list[tuple] = []
        missing: list[str] = []
        for slot, need, want in (("K", roster_positions.count("K"), _K_POS),
                                 ("DST", roster_positions.count("DST"), _DST_POS)):
            for _ in range(need - have.get(slot, 0)):
                pick = next((i for i in remaining
                             if i not in used and det.get(i, {}).get("position") in want), None)
                if pick:
                    used.add(pick)
                    starters.append(pick)
                    lab = (pick, det.get(pick, {}).get("name", pick), slot, slot)
                    labels.append(lab)
                    extra.append(lab)
                else:
                    missing.append(slot)

        url = f"https://{self.c.host}/{self.year}/import"
        params = {"TYPE": "lineup", "L": self.lid, "W": int(plan.week),
                  "STARTERS": ",".join(starters), "JSON": 1}
        if self.fid:
            params["FRANCHISE_ID"] = self.fid
        return MflSubmission(int(plan.week), starters, labels, url, params, extra, missing)

    def commit(self, submission: MflSubmission, username: str, password: str) -> dict:
        cookie = self.c.login(self.year, username, password)
        if not cookie:
            return {"error": "MFL login failed — check MFL_USERNAME / MFL_PASSWORD."}
        return self.c.import_lineup(self.year, self.lid, submission.week,
                                    submission.starter_ids, cookie, self.fid)
