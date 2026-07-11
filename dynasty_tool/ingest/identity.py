"""Identity resolution (R1) and per-season roster maps (R2).

R1: the stable identity is ``user_id``. Display names change across seasons
(one account can be "LACrams" in 2020 and "TheGrimGaffer" in 2025). We collect
ALL display names per user_id and canonicalize to the most-recent one. We NEVER
merge or split on name similarity -- only on user_id. Two near-identical names
("daAverageJoes" vs "DaAverageJoes65") stay separate iff their user_ids differ.

R2: ``roster_id -> user_id`` is scoped to a single season's league and is not
stable across seasons. Every season gets its own map.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Identity:
    user_id: str
    names: list[str] = field(default_factory=list)  # first-seen order, oldest->newest
    canonical: str = ""  # most-recent display name

    @property
    def is_alias(self) -> bool:
        return len(self.names) > 1


def build_identities(client, chain: list[dict]) -> dict[str, Identity]:
    """Map user_id -> Identity by walking every season's /users (oldest->newest)."""
    names_by_user: dict[str, list[str]] = {}
    latest: dict[str, str] = {}

    for league in chain:  # oldest -> newest, so `latest` ends on the newest name
        for u in client.users(league["league_id"]):
            uid = str(u["user_id"])
            name = u.get("display_name") or f"(user {uid})"
            names_by_user.setdefault(uid, [])
            if name not in names_by_user[uid]:
                names_by_user[uid].append(name)
            latest[uid] = name

    return {
        uid: Identity(user_id=uid, names=names, canonical=latest[uid])
        for uid, names in names_by_user.items()
    }


def alias_warnings(identities: dict[str, Identity]) -> dict[str, list[str]]:
    """user_id -> [all names] for every account that used more than one name."""
    return {uid: idn.names for uid, idn in identities.items() if idn.is_alias}


def build_roster_maps(client, chain: list[dict]) -> dict[str, dict[int, str]]:
    """league_id -> {roster_id: user_id} for that specific season (R2)."""
    maps: dict[str, dict[int, str]] = {}
    for league in chain:
        lid = league["league_id"]
        m: dict[int, str] = {}
        for r in client.rosters(lid):
            owner = r.get("owner_id")
            if owner is not None:
                m[r["roster_id"]] = str(owner)
        maps[lid] = m
    return maps


def user_to_roster(roster_map: dict[int, str]) -> dict[str, int]:
    """Reverse a single season's roster map: user_id -> roster_id."""
    return {uid: rid for rid, uid in roster_map.items()}
