"""Shared helpers bridging ingestion + valuation for the analysis modules:
transfer valuation (used by value_flow) and query parsing (used by trade_eval).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from ..ingest.drafts import resolve_pick
from ..value import assets
from ..value.assets import AssetValue

# -- transfer valuation (Module 2) ------------------------------------------


@dataclass
class ValuedTransfer:
    giver: Optional[str]       # user_id
    receiver: Optional[str]
    av: AssetValue


def value_transfers(ctx, provider, trade) -> list[ValuedTransfer]:
    """Value every asset moved in a normalized trade (R5: re-resolve from raw)."""
    num_teams = ctx.newest.get("total_rosters") or 12
    out: list[ValuedTransfer] = []
    for tr in trade.transfers:
        a = tr.asset
        if a.kind == "player":
            av = assets.value_player(provider, ctx.players_meta, a.player_id)
        elif a.kind == "pick":
            res = resolve_pick(ctx.drafts_by_season, ctx.roster_maps,
                               trade.league_id, a.pick, trade.timestamp)
            av = assets.value_pick(provider, ctx.players_meta, res, num_teams=num_teams)
        else:
            av = assets.value_faab(a.faab)
        out.append(ValuedTransfer(tr.giver, tr.receiver, av))
    return out


# -- query parsing (Module 1) -----------------------------------------------

_ORD_WORDS = {
    "1st": 1, "first": 1, "2nd": 2, "second": 2, "3rd": 3, "third": 3,
    "4th": 4, "fourth": 4, "5th": 5, "fifth": 5, "6th": 6, "7th": 7,
}
_TIERS = {"early": "Early", "mid": "Mid", "late": "Late"}


@dataclass
class PickSpec:
    season: str
    rnd: int
    slot: Optional[int] = None
    tier: Optional[str] = None


def parse_pick_spec(spec: str) -> Optional[PickSpec]:
    """Parse '2027 1st', '2026 1.05', '2026 Pick 2.06', '2027 early 2nd'."""
    s = spec.strip().lower()
    s = s.replace("pick", " ").replace("round", " ")
    s = re.sub(r"\s+", " ", s).strip()
    m = re.match(r"^(20\d\d)\b", s)
    if not m:
        return None
    season = m.group(1)
    rest = s[m.end():].strip()

    ms = re.search(r"(\d+)\.(\d+)", rest)  # slot form "1.05"
    if ms:
        return PickSpec(season, int(ms.group(1)), slot=int(ms.group(2)))

    tier = None
    for w, t in _TIERS.items():
        if re.search(rf"\b{w}\b", rest):
            tier = t
            rest = re.sub(rf"\b{w}\b", " ", rest)
    for w, rnd in _ORD_WORDS.items():
        if re.search(rf"\b{re.escape(w)}\b", rest):
            return PickSpec(season, rnd, tier=tier)
    mr = re.search(r"\b([1-7])\b", rest)  # bare round number
    if mr:
        return PickSpec(season, int(mr.group(1)), tier=tier)
    return None


def _norm(s: str) -> str:
    return "".join(ch for ch in str(s).lower() if ch.isalnum())


def build_name_index(players_meta: dict) -> dict[str, list[str]]:
    """normalized full name -> [sleeper_id, ...] (only real, positioned players)."""
    idx: dict[str, list[str]] = {}
    for pid, meta in players_meta.items():
        if not isinstance(meta, dict):
            continue
        first = meta.get("first_name") or ""
        last = meta.get("last_name") or ""
        full = f"{first} {last}".strip() or (meta.get("full_name") or "")
        if not full or not meta.get("position"):
            continue
        idx.setdefault(_norm(full), []).append(str(pid))
    return idx


@dataclass
class PlayerMatch:
    sleeper_id: Optional[str]
    label: str
    note: str = ""


def resolve_player_query(query: str, players_meta: dict, name_index: dict,
                         provider=None) -> PlayerMatch:
    """Resolve a player name or a raw sleeper_id to a sleeper_id.

    Ambiguous names resolve to the highest-value candidate (the relevant dynasty
    asset), with a note recording the ambiguity.
    """
    q = query.strip()
    if q.isdigit() and q in players_meta:
        meta = players_meta[q]
        name = f"{meta.get('first_name','')} {meta.get('last_name','')}".strip()
        return PlayerMatch(q, f"{name} ({meta.get('position','?')})")

    cands = name_index.get(_norm(q), [])
    if not cands:
        return PlayerMatch(None, query, note="UNMATCHED_NAME")
    if len(cands) > 1 and provider is not None:
        cands = sorted(cands, key=lambda sid: provider.player_value(sid)[0], reverse=True)
    sid = cands[0]
    meta = players_meta.get(sid, {})
    name = f"{meta.get('first_name','')} {meta.get('last_name','')}".strip()
    note = "AMBIGUOUS_NAME_TOOK_HIGHEST_VALUE" if len(name_index.get(_norm(q), [])) > 1 else ""
    return PlayerMatch(sid, f"{name} ({meta.get('position','?')})", note=note)
