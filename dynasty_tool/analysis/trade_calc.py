"""KTC-style interactive trade calculator -- the pure core.

Where ``trade_eval`` scores a trade typed as text specs (CLI), this module backs
an *interactive* builder: it exposes the whole tradeable universe as pickable
``Asset`` rows (every valued player + every pick label the source covers), so a
UI can offer type-ahead search the way KeepTradeCut does.

Two things it adds over KTC, because the app already knows the league:

  * **ownership** -- each asset carries who rosters it in *this* league.
  * **roster impact** -- the same lineup filler the analyzer uses, re-run on the
    hypothetical post-trade roster, so a trade that adds value but doesn't crack
    the starting lineup is visible as such.

Values are never re-derived here: dynasty values come from the validated
``CurrentValueProvider`` join (sleeper_id -> fantasypros_id -> fp_id) and pick
values from its ECR->value curve; redraft values come from the project's own
season projection. Fairness thresholds and the verdict come from ``trade_eval``
so the app and the CLI can never disagree.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

import pandas as pd

from .. import config
from ..value.assets import AssetValue
from . import optimizer
from .optimizer import RPlayer, RosterValue
from .trade_eval import SideResult, TradeEvaluation

PLAYER_PREFIX = "p:"
PICK_PREFIX = "k:"
PICK_POS = "PICK"


@dataclass(frozen=True)
class Asset:
    """One tradeable thing: a player or a draft pick, already valued."""
    key: str                  # "p:<sleeper_id>" | "p:fp:<fp_id>" | "k:<pick label>"
    kind: str                 # 'player' | 'pick'
    label: str                # display name ("Ja'Marr Chase", "2027 Mid 1st")
    pos: str                  # QB/RB/WR/TE/... or PICK
    team: str                 # NFL team ("" for picks)
    age: Optional[float]
    value: float

    @property
    def player_id(self) -> Optional[str]:
        """The roster id this asset joins on (None for picks / unmapped players)."""
        if not self.key.startswith(PLAYER_PREFIX):
            return None
        pid = self.key[len(PLAYER_PREFIX):]
        return None if pid.startswith("fp:") else pid


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN -> None


# ---------------------------------------------------------------------------
# universes
# ---------------------------------------------------------------------------

def build_dynasty_universe(provider, players_df: pd.DataFrame) -> dict[str, Asset]:
    """Every valued player + every covered pick label, keyed for a picker.

    ``provider`` is a :class:`CurrentValueProvider`; we read its already-built
    id map, value column, and pick curve rather than re-deriving any of them.
    Players the crosswalk can't map to a sleeper_id are still included (keyed by
    fp_id) so search never hides a valued asset -- they simply carry no owner.
    """
    fp_to_sleeper = provider.fp_to_sleeper()
    val_col = provider.value_col
    uni: dict[str, Asset] = {}

    for r in players_df.itertuples(index=False):
        fp = _num(getattr(r, "fp_id", None))
        val = _num(getattr(r, val_col, None))
        if fp is None or val is None:
            continue
        fp_id = str(int(fp))
        sid = fp_to_sleeper.get(fp_id)
        key = PLAYER_PREFIX + (sid if sid else f"fp:{fp_id}")
        uni[key] = Asset(
            key=key, kind="player",
            label=str(getattr(r, "player", "") or "").strip(),
            pos=str(getattr(r, "pos", "") or "?"),
            team=str(getattr(r, "team", "") or ""),
            age=_num(getattr(r, "age", None)),
            value=val,
        )

    for label, val in provider.pick_labels().items():
        key = PICK_PREFIX + label
        uni[key] = Asset(key=key, kind="pick", label=str(label), pos=PICK_POS,
                         team="", age=None, value=float(val))
    return uni


def build_redraft_universe(rankings_df: pd.DataFrame) -> dict[str, Asset]:
    """Redraft/keeper leagues: value = this season's projected points (gsis-keyed).

    Draft picks are not a redraft asset (the provider values them 0), so the
    universe is players only -- the UI hides the pick picker in this mode.
    """
    uni: dict[str, Asset] = {}
    for r in rankings_df.itertuples(index=False):
        pid = getattr(r, "player_id", None)
        pts = _num(getattr(r, "season_projected_pts", None))
        if not isinstance(pid, str) or pts is None:
            continue
        key = PLAYER_PREFIX + pid
        uni[key] = Asset(
            key=key, kind="player",
            label=str(getattr(r, "player_display_name", "") or "").strip(),
            pos=str(getattr(r, "position", "") or "?"),
            team=str(getattr(r, "team", "") or ""),
            age=_num(getattr(r, "age", None)),
            value=pts,
        )
    return uni


def extend_with_rosters(uni: dict[str, Asset],
                        rosters: dict[str, RosterValue]) -> dict[str, Asset]:
    """Add rostered players the values file doesn't cover, at the analyzer's value.

    IDP and deep-bench players carry no dynasty value (DynastyProcess doesn't
    publish one), but they still get traded -- search must find them rather than
    pretend they don't exist. They come in at their real value, which is 0.
    """
    out = dict(uni)
    for rv in rosters.values():
        for p in rv.players:
            key = PLAYER_PREFIX + str(p.sleeper_id)
            if key not in out:
                out[key] = Asset(key=key, kind="player", label=p.name,
                                 pos=p.pos or "?", team="", age=p.age,
                                 value=float(p.value or 0.0))
    return out


def owner_map(rosters: dict[str, RosterValue]) -> dict[str, str]:
    """asset key -> the display name of the team that rosters it."""
    out: dict[str, str] = {}
    for rv in rosters.values():
        for p in rv.players:
            out[PLAYER_PREFIX + str(p.sleeper_id)] = rv.display
    return out


def roster_asset_keys(rv: RosterValue) -> list[str]:
    """This roster's players as universe keys, best first."""
    return [PLAYER_PREFIX + str(p.sleeper_id)
            for p in sorted(rv.players, key=lambda p: p.value, reverse=True)]


# ---------------------------------------------------------------------------
# scoring (delegates the fairness verdict to trade_eval)
# ---------------------------------------------------------------------------

def _to_asset_value(a: Asset) -> AssetValue:
    return AssetValue(kind=a.kind, label=a.label, value=a.value, matched=True,
                      detail={"key": a.key, "pos": a.pos})


def winner(ev: TradeEvaluation) -> Optional[str]:
    """Who nets value, when each side lists what that team **sends**.

    ``TradeEvaluation.favored`` names the side with the *larger package*; the
    team that RECEIVES it is the one that wins the trade. Keeping the flip here
    means the app and any future caller can't get it backwards.
    """
    if ev.delta > 0:
        return ev.side_b.name
    if ev.delta < 0:
        return ev.side_a.name
    return None


def evaluate(side_a: Iterable[Asset], side_b: Iterable[Asset], basis: str,
             qb_format: int, num_teams: int = 12,
             name_a: str = "Side A", name_b: str = "Side B") -> TradeEvaluation:
    """Score two already-resolved sides with the CLI's fairness thresholds."""
    return TradeEvaluation(
        basis=basis,
        qb_format=qb_format,
        side_a=SideResult(name=name_a, items=[_to_asset_value(a) for a in side_a]),
        side_b=SideResult(name=name_b, items=[_to_asset_value(a) for a in side_b]),
        num_teams=num_teams,
    )


# ---------------------------------------------------------------------------
# roster impact -- what the trade does to the starting lineup
# ---------------------------------------------------------------------------

@dataclass
class Impact:
    starter_before: float
    starter_after: float
    total_before: float
    total_after: float
    lineup_in: list[RPlayer]        # newly cracks the starting lineup
    lineup_out: list[RPlayer]       # loses its starting spot (traded or bumped)
    picks_in: float = 0.0
    picks_out: float = 0.0

    @property
    def starter_delta(self) -> float:
        return self.starter_after - self.starter_before

    @property
    def total_delta(self) -> float:
        return self.total_after - self.total_before


def _as_rplayer(a: Asset) -> RPlayer:
    pid = a.player_id or a.key
    return RPlayer(pid, a.label, a.pos, a.value, a.age, True, fantasy_positions=[a.pos])


def roster_impact(rv: RosterValue, roster_positions: list[str],
                  incoming: Iterable[Asset], outgoing: Iterable[Asset]) -> Impact:
    """Re-fill the lineup on the hypothetical post-trade roster.

    Picks carry value but never a lineup slot, so they move ``total`` and are
    reported separately rather than being silently counted as starters.
    """
    incoming, outgoing = list(incoming), list(outgoing)
    out_ids = {a.player_id for a in outgoing if a.player_id}
    slots = [s for s in roster_positions if s not in config.NON_STARTER_SLOTS]

    kept = [p for p in rv.players if str(p.sleeper_id) not in out_ids]
    after_players = kept + [_as_rplayer(a) for a in incoming if a.kind == "player"]

    before = optimizer._fill_lineup(rv.players, slots)
    after = optimizer._fill_lineup(after_players, slots)
    before_ids = {p.sleeper_id for _, p in before}
    after_ids = {p.sleeper_id for _, p in after}

    picks_in = sum(a.value for a in incoming if a.kind == "pick")
    picks_out = sum(a.value for a in outgoing if a.kind == "pick")
    total_before = sum(p.value for p in rv.players)
    total_after = (total_before - sum(a.value for a in outgoing)
                   + sum(a.value for a in incoming))

    return Impact(
        starter_before=sum(p.value for _, p in before),
        starter_after=sum(p.value for _, p in after),
        total_before=total_before,
        total_after=total_after,
        lineup_in=[p for _, p in after if p.sleeper_id not in before_ids],
        lineup_out=[p for _, p in before if p.sleeper_id not in after_ids],
        picks_in=picks_in,
        picks_out=picks_out,
    )


# ---------------------------------------------------------------------------
# "what closes the gap?"
# ---------------------------------------------------------------------------

def suggest_sweeteners(pool: Iterable[Asset], gap: float, exclude: Iterable[str] = (),
                       n: int = 3, overshoot: float = 1.6) -> list[Asset]:
    """Assets from ``pool`` that would most nearly close a ``gap`` of value.

    Ranked by how close they land to the gap; anything far past it is dropped so
    the suggestion evens the trade instead of flipping it.
    """
    if gap <= 0:
        return []
    skip = set(exclude)
    cands = [a for a in pool
             if a.key not in skip and 0 < a.value <= gap * overshoot]
    cands.sort(key=lambda a: abs(a.value - gap))
    return cands[:n]


# ---------------------------------------------------------------------------
# shareable summary
# ---------------------------------------------------------------------------

def trade_summary_text(ev: TradeEvaluation, side_a: Iterable[Asset],
                       side_b: Iterable[Asset], unit: str = "value") -> str:
    """Plain-text recap to paste into a league chat."""
    def block(name: str, items: list[Asset], total: float) -> list[str]:
        rows = [f"  {a.label} ({a.pos}) — {a.value:,.0f}" for a in items] or ["  (nothing)"]
        return [f"{name} sends:", *rows, f"  = {total:,.0f} {unit}"]

    a_items, b_items = list(side_a), list(side_b)
    lines = block(ev.side_a.name, a_items, ev.side_a.total)
    lines += [""] + block(ev.side_b.name, b_items, ev.side_b.total)
    verdict = ev.verdict
    tail = (f"within {config.FAIRNESS_EVEN*100:.0f}% — fair" if verdict == "EVEN"
            else f"{winner(ev)} nets {abs(ev.delta):,.0f} ({ev.pct_gap*100:.0f}%)")
    lines += ["", f"VERDICT: {verdict} — {tail}",
              f"[basis={ev.basis} · {ev.qb_format}QB]"]
    return "\n".join(lines)
