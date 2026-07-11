"""Shared set-lineup builder used by the Streamlit app (and eventually the CLI —
cmd_set_lineup's MFL branch predates this and still inlines the same steps).

Builds everything the "one-click set lineup" UI needs for an MFL league:
the optimal plan, the official import submission, the in-session bookmarklet,
and the drag-to-install page. Nothing here sends anything.
"""
from __future__ import annotations

from typing import Optional

from ..analysis import league_analysis as la
from ..ingest.mfl_client import MflClient, aslist
from ..ingest.mfl_context import build_mfl_context, resolve_mfl_team
from ..value.projection_provider import ProjectionValueProvider
from .plan import build_plan
from .mfl_writer import MflLineupWriter
from .bookmarklet import mfl_bookmarklet, render_installer


def build_mfl_set(cache, host: str, year: str, league_id: str, team_token: str,
                  week: Optional[int] = None, generate_weekly: bool = False) -> dict:
    """Return {'team','week','rows','submission','bookmarklet','installer_html'}.

    Raises ValueError if the team can't be resolved to a franchise.
    """
    inner = ProjectionValueProvider.from_source(cache)
    mfl = MflClient(cache, host)
    ctx = build_mfl_context(mfl, inner, str(league_id), str(year))
    provider = inner.view("gsis")
    target = resolve_mfl_team(ctx, team_token)
    if not target:
        raise ValueError(f"Could not resolve team {team_token!r} to an MFL franchise.")

    A = la.analyze(ctx, provider, weekly_week=week, generate_weekly=generate_weekly)
    plan = build_plan(A, target)

    rosters = {f["id"]: f for f in aslist(mfl.rosters(str(year), str(league_id)).get("franchise"))}
    roster_ids = [str(p.get("id")) for p in aslist(rosters.get(target, {}).get("player"))]
    g2m = {g: m for m, g in inner.mfl_to_gsis.items()}
    writer = MflLineupWriter(mfl, str(year), str(league_id), None, g2m)
    sub = writer.build(plan, roster_ids, A.roster_positions)

    team = ctx.display(target)
    rows = [(s.slot, s.name, s.pos, (f"{s.proj:.1f}" if plan.weekly else f"{s.proj:.0f}"))
            for s in plan.starters]
    rows += [(slot, name, pos, "—") for (_m, name, pos, slot) in sub.filled_from_roster]
    summary = (f"Set Week {sub.week} lineup for {team}?\n\n"
               + "\n".join(f"{slot}: {name}" for slot, name, _pos, _proj in rows))
    bm = mfl_bookmarklet(host, str(year), str(league_id), sub.week, sub.starter_ids, summary)
    notes = (["Missing: " + ", ".join(sub.missing)] if sub.missing else [])
    installer = render_installer("MFL", team, sub.week, f"⚡ Set {team} Wk{sub.week}",
                                 bm, rows, notes=notes)
    return {"team": team, "week": sub.week, "rows": rows, "submission": sub,
            "bookmarklet": bm, "installer_html": installer, "weekly": plan.weekly}
