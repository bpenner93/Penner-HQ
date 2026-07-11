"""dynasty_tool command-line interface.

    python -m dynasty_tool.cli ingest   --league-id <id>
    python -m dynasty_tool.cli evaluate --league-id <id> --side-a "..." --side-b "..."
    python -m dynasty_tool.cli flow     --league-id <id>
    python -m dynasty_tool.cli optimize --league-id <id> [--team <name|user_id>]

Every value-bearing output labels its basis (realized/current/point_in_time) and
QB format. Point-in-time fails loudly without a historical source (R3).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import config
from .cache import DiskCache
from .ingest.sleeper_client import SleeperClient
from .ingest.context import build_context
from .ingest.identity import alias_warnings
from .ingest.trades import normalize_trades
from .value import make_provider, PointInTimeUnavailable


# -- shared setup ------------------------------------------------------------

def _client(args) -> SleeperClient:
    cache_dir = Path(args.cache_dir) if args.cache_dir else config.CACHE_DIR
    return SleeperClient(DiskCache(cache_dir, refresh=args.refresh))


def _context(args, client):
    ctx = build_context(client, args.league_id)
    if args.qb:  # explicit override
        ctx.qb_format = int(args.qb)
    return ctx


def _provider(args, ctx, client):
    """Build the value provider, enforcing the R3 point-in-time guard."""
    return make_provider(
        args.basis,
        cache=client.cache,
        qb_format=ctx.qb_format,
        refresh=args.refresh,
    )


def _resolve_user(ctx, token: str):
    token = token.strip()
    if token in ctx.identities:
        return token
    low = token.lower()
    for uid, idn in ctx.identities.items():
        if idn.canonical.lower() == low or low in [n.lower() for n in idn.names]:
            return uid
    return None


# -- subcommands -------------------------------------------------------------

def cmd_ingest(args) -> int:
    client = _client(args)
    ctx = _context(args, client)
    print(f"LEAGUE: {ctx.newest.get('name')!r}  [{ctx.qb_label} | "
          f"{len(ctx.chain)} seasons]")
    print("CHAIN (oldest -> newest):")
    for lg in ctx.chain:
        print(f"    {lg['season']}  {lg['league_id']}  {lg.get('name')!r}")

    aliases = alias_warnings(ctx.identities)
    print(f"\nIDENTITY (R1): {len(ctx.identities)} accounts, "
          f"{len(aliases)} with multiple display names")
    if aliases:
        print("  ALIAS WARNINGS (verify these are the same human):")
        for uid, names in aliases.items():
            print(f"    {uid}: {names}  -> canonical {ctx.display(uid)!r}")

    # trades + join coverage (build a provider only for realized/current)
    print(f"\nnetwork fetches this run: {client.fetch_count}")
    try:
        provider = _provider(args, ctx, client)
    except PointInTimeUnavailable as e:
        print(f"\n[basis=point_in_time] {e}", file=sys.stderr)
        return 2

    trades = normalize_trades(client, ctx.chain, ctx.roster_maps)
    print(f"TRADES: {len(trades)} across the chain")

    # touch every rostered player so coverage reflects the live rosters
    from .value import assets
    for r in client.rosters(ctx.newest["league_id"]):
        for pid in (r.get("players") or []):
            assets.value_player(provider, ctx.players_meta, pid)
    if hasattr(provider, "coverage"):
        cov = provider.coverage()
        print(f"\nVALUE JOIN COVERAGE [basis={cov['basis']} | {cov['qb_format']}QB | "
              f"scrape_date={cov['scrape_date']}]:")
        print(f"    matched   {cov['players_matched']}/{cov['players_seen']}")
        print(f"    unmatched {cov['players_unmatched']}  (valued 0, counted not hidden "
              f"-- typically IDP/retired)")
    return 0


def cmd_evaluate(args) -> int:
    from .analysis import trade_eval
    client = _client(args)
    ctx = _context(args, client)
    try:
        provider = _provider(args, ctx, client)
    except PointInTimeUnavailable as e:
        print(f"[basis=point_in_time] {e}", file=sys.stderr)
        return 2

    side_a = [s for s in (args.side_a or "").split(",") if s.strip()]
    side_b = [s for s in (args.side_b or "").split(",") if s.strip()]
    if not side_a or not side_b:
        print("evaluate needs both --side-a and --side-b (comma-separated assets)",
              file=sys.stderr)
        return 1
    ev = trade_eval.evaluate_trade(ctx, provider, side_a, side_b,
                                   name_a=args.name_a, name_b=args.name_b)
    print(trade_eval.format_evaluation(ev))
    return 0


def cmd_flow(args) -> int:
    from .analysis import value_flow
    client = _client(args)
    ctx = _context(args, client)
    try:
        provider = _provider(args, ctx, client)
    except PointInTimeUnavailable as e:
        print(f"[basis=point_in_time] {e}", file=sys.stderr)
        return 2

    trades = normalize_trades(client, ctx.chain, ctx.roster_maps)
    res = value_flow.compute_flow(ctx, provider, trades)
    print(value_flow.format_flow(res))

    out_dir = Path(args.out_dir) if args.out_dir else config.OUT_DIR
    written = value_flow.write_flow_outputs(res, out_dir, league_name=ctx.newest.get("name", ""))
    print("\nwrote:")
    for p in written:
        print(f"    {p}")
    return 0


def _setup_espn(args, cache):
    """Build an ESPN redraft context + gsis-resolving projection provider."""
    from .value.projection_provider import ProjectionValueProvider
    from .ingest.espn_client import EspnClient, load_espn_cookies
    from .ingest.espn_context import build_espn_context, resolve_espn_team
    inner = ProjectionValueProvider.from_source(cache, refresh=args.refresh)
    espn = EspnClient(cache, cookies=load_espn_cookies())
    season = args.season or "2025"
    print(f"(ESPN league {args.league_id} {season} -> projected-points redraft analysis)")
    ctx = build_espn_context(espn, inner, args.league_id, season)
    return ctx, inner.view("gsis"), resolve_espn_team(ctx, args.team)


def _setup_mfl(args, cache):
    """Build an MFL redraft context + gsis-resolving projection provider."""
    from .value.projection_provider import ProjectionValueProvider
    from .ingest.mfl_client import MflClient
    from .ingest.mfl_context import build_mfl_context, resolve_mfl_team
    if not args.host:
        print("MFL needs --host (e.g. www45.myfantasyleague.com).", file=sys.stderr)
        raise SystemExit(2)
    inner = ProjectionValueProvider.from_source(cache, refresh=args.refresh)
    year = args.season or "2025"
    mfl = MflClient(cache, args.host)
    print(f"(MFL league {args.league_id} {year} @ {args.host} -> projected-points redraft analysis)")
    ctx = build_mfl_context(mfl, inner, args.league_id, year)
    return ctx, inner.view("gsis"), resolve_mfl_team(ctx, args.team)


def cmd_analyze(args) -> int:
    from .analysis import league_analysis as la
    from .analysis import dashboard
    from .value import make_provider
    client = _client(args)
    platform = getattr(args, "platform", "sleeper")

    if platform == "espn":
        ctx, provider, target_uid = _setup_espn(args, client.cache)
    elif platform == "mfl":
        ctx, provider, target_uid = _setup_mfl(args, client.cache)
    else:
        ctx = _context(args, client)
        # redraft/keeper leagues (settings.type 0/1) are valued on projected points,
        # not dynasty value -- auto-detected, with --redraft/--dynasty to force it.
        stype = (ctx.newest.get("settings") or {}).get("type")
        if getattr(args, "dynasty", False):
            basis = args.basis
        elif getattr(args, "redraft", False) or (args.basis == "realized" and stype in (0, 1)):
            basis = "redraft"
        else:
            basis = args.basis
        try:
            provider = make_provider(basis, cache=client.cache, qb_format=ctx.qb_format,
                                     refresh=args.refresh)
        except PointInTimeUnavailable as e:
            print(f"[basis=point_in_time] {e}", file=sys.stderr)
            return 2
        if basis == "redraft":
            print("(redraft/keeper league -> valuing rosters by PROJECTED POINTS from your "
                  "season_rankings engine, not dynasty value)")
        target_uid = _resolve_user(ctx, args.team) if args.team else None

    _wk = getattr(args, "week", None)
    if _wk:
        print(f"(loading DFS weekly projection for week {_wk} — start/sit will use it; generating if missing…)")
    A = la.analyze(ctx, provider, weekly_week=_wk, generate_weekly=bool(_wk))

    print(f"LEAGUE ANALYZER — {A.league_name!r}  [{A.qb_label if hasattr(A,'qb_label') else str(A.qb_format)+'QB'} "
          f"| status={A.status} | basis={A.basis}]")
    if not A.sim_used_real_schedule:
        print("  (offseason/pre-draft: odds are a PRESEASON roster-strength projection, "
              "balanced schedule; SOS/matchups appear once the schedule is set)")
    print("\nPOWER RANKINGS & TITLE ODDS (roster-strength Monte Carlo):")
    print(f"  {'#':>2} {'team':<20}{'starter':>8}{'age':>6}{'projW':>7}{'plyf%':>7}{'title%':>7}  window")
    for i, s in enumerate(A.sim, 1):
        rv = A.rosters[s.user_id]
        age = f"{rv.avg_starter_age:.1f}" if rv.avg_starter_age else "  —"
        print(f"  {i:>2} {s.display:<20}{rv.starter_value:>8.0f}{age:>6}{s.proj_wins:>7.1f}"
              f"{s.playoff_pct*100:>6.0f}%{s.title_pct*100:>6.0f}%  {rv.window}")

    byval = sorted(A.rosters.values(), key=lambda x: x.starter_value, reverse=True)
    print(f"\n  best:  {', '.join(r.display for r in byval[:3])}")
    print(f"  worst: {', '.join(r.display for r in byval[-3:])}")

    if target_uid:
        print(f"\nADVICE — {ctx.display(target_uid)}: {A.advice.get(target_uid, '')}")
        pkgs = la.propose_trades(A.rosters, target_uid)
        print("SUGGESTED TRADES:")
        for p in pkgs[:5]:
            tag = "COMPETITOR" if p.competitor else "non-comp"
            get = ", ".join(f"{n}({v:,.0f})" for n, _, v in p.you_get)
            give = ", ".join(f"{n}({v:,.0f})" for n, _, v in p.you_give)
            print(f"   w/ {p.partner_display} [{tag}] gap {p.pct_gap*100:.0f}%:  GET {get}  |  GIVE {give}")
        if not pkgs:
            print("   (no cleanly-balanced surplus-for-surplus package; your need needs an overpay/pick)")
        tgts = A.waivers.get(target_uid, [])
        if tgts:
            print("WAIVER TARGETS:", ", ".join(f"{t.name}({t.pos} {t.value:,.0f})" for t in tgts))
        rec = la.lineup_recommendation(A.rosters[target_uid], A.roster_positions,
                                       weekly=A.weekly, week=A.weekly_week, opp=A.weekly_opp)
        tag = (f"Week {rec.week} DFS weekly proj" if rec.weekly
               else "season proj" if A.redraft else "dynasty value")
        if rec.close_calls:
            print(f"START/SIT tough calls [{tag}]:",
                  "; ".join(f"{slot} start {st.name} > {ch.name} ({gap*100:.0f}%)"
                            for slot, st, ch, gap in rec.close_calls))
        else:
            print(f"START/SIT [{tag}]: lineup is clear-cut (no close calls)")
        mine = next((r for r in A.race if r.user_id == target_uid), None)
        if mine:
            print(f"PLAYOFF RACE: proj {mine.proj_wins:.1f} wins, "
                  f"make playoffs {mine.playoff_pct*100:.0f}%, title {mine.title_pct*100:.0f}% [{mine.status}]")

    if A.last_season:
        champ = A.last_season["standings"][0]
        print(f"\nLAST SEASON ({A.last_season['season']}) #1 seed: {champ.display} "
              f"{champ.wins}-{champ.losses} ({champ.fpts:,.0f} pts)")

    out_dir = Path(args.out_dir) if args.out_dir else config.OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = dashboard.write_dashboard(A, out_dir / "league_report.html", target_uid)
    print(f"\nwrote interactive dashboard: {html_path}")
    return 0


def cmd_optimize(args) -> int:
    from .analysis import optimizer
    client = _client(args)
    ctx = _context(args, client)
    try:
        provider = _provider(args, ctx, client)
    except PointInTimeUnavailable as e:
        print(f"[basis=point_in_time] {e}", file=sys.stderr)
        return 2

    rosters = optimizer.compute_rosters(ctx, provider)
    optimizer.classify_windows(rosters)
    print(optimizer.format_rosters(ctx, rosters, basis=provider.basis))

    if args.team:
        uid = _resolve_user(ctx, args.team)
        if uid is None:
            print(f"\ncould not resolve --team {args.team!r} to an account", file=sys.stderr)
            return 1
        fits = optimizer.trade_targets(rosters, uid)
        print(optimizer.format_targets(ctx, rosters, uid, fits, basis=provider.basis))
    return 0


def cmd_portfolio(args) -> int:
    from .analysis import portfolio as pf_mod
    client = _client(args)
    season = args.season or (client.state() or {}).get("season") or "2026"
    out_dir = Path(args.out_dir) if args.out_dir else config.OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    pf = pf_mod.build_portfolio(client, client.cache, args.user, str(season), out_dir=out_dir)
    if not pf.slices:
        print(f"No dynasty (type-2) leagues found for user {args.user!r} in {season}.",
              file=sys.stderr)
        return 1

    print(f"DYNASTY PORTFOLIO — {pf.user_display}  ({season}) · {len(pf.slices)} leagues")
    print(f"  contending: {pf.n_contend} | bubble: {pf.n_bubble} | rebuilding: {pf.n_rebuild}")
    print(f"\n  {'league':<32}{'rank':>7}{'projW':>7}{'title%':>8}  window")
    for s in sorted(pf.slices, key=lambda x: x.title_pct, reverse=True):
        rk = f"{s.rank}/{s.n_teams}"
        print(f"  {s.name[:30]:<32}{rk:>7}{s.proj_wins:>7.1f}{s.title_pct*100:>7.0f}%  {s.window}")

    print("\n  TOP CROSS-LEAGUE EXPOSURE (a boom/bust here hits your whole portfolio):")
    for e in pf.exposures[:8]:
        flag = "   <-- high exposure" if e.n_leagues >= 3 else ""
        print(f"    {e.name} ({e.pos}) — {e.n_leagues} leagues, total value {e.total_value:,.0f}{flag}")

    html_path = pf_mod.write_portfolio(pf, out_dir / "portfolio.html")
    linked = sum(1 for s in pf.slices if s.report_file)
    print(f"\nwrote portfolio dashboard: {html_path}")
    print(f"  + {linked} per-league dashboards (league_<id>.html) linked from each row")
    return 0


def cmd_set_lineup(args) -> int:
    """Compute the optimal weekly lineup and set it on the platform (dry-run by default)."""
    from .analysis import league_analysis as la
    from .write import plan as planmod
    client = _client(args)
    wk = args.week
    platform = args.platform

    if platform == "mfl":
        from .value.projection_provider import ProjectionValueProvider
        from .ingest.mfl_client import MflClient, aslist
        from .ingest.mfl_context import build_mfl_context, resolve_mfl_team
        from .write.mfl_writer import MflLineupWriter
        if not args.host:
            print("MFL needs --host.", file=sys.stderr)
            return 2
        inner = ProjectionValueProvider.from_source(client.cache, refresh=args.refresh)
        year = args.season or "2025"
        mfl = MflClient(client.cache, args.host)
        ctx = build_mfl_context(mfl, inner, args.league_id, year)
        provider = inner.view("gsis")
        target = resolve_mfl_team(ctx, args.team)
        if not target:
            print(f"Could not resolve --team {args.team!r} to a franchise.", file=sys.stderr)
            return 2
        A = la.analyze(ctx, provider, weekly_week=wk, generate_weekly=bool(wk))
        plan = planmod.build_plan(A, target)
        rosters = {f["id"]: f for f in aslist(mfl.rosters(year, args.league_id).get("franchise"))}
        roster_ids = [str(p.get("id")) for p in aslist(rosters.get(target, {}).get("player"))]
        g2m = {g: m for m, g in inner.mfl_to_gsis.items()}
        writer = MflLineupWriter(mfl, year, args.league_id, None, g2m)
        sub = writer.build(plan, roster_ids, A.roster_positions)

        print(f"SET LINEUP — MFL · {ctx.display(target)} · Week {sub.week}"
              f"  [{'DFS weekly proj' if plan.weekly else 'season proj'}]")
        print(planmod.format_plan(plan))
        for _mid, name, pos, slot in sub.filled_from_roster:
            print(f"   {slot:6s} {name} ({pos})   [from roster — no projection]")
        if sub.missing:
            print(f"   !! could not fill required slots: {', '.join(sub.missing)} "
                  "(check your roster has one rostered)")
        print(f"\nOfficial MFL request (import?TYPE=lineup):")
        print(f"   POST {sub.url}")
        print(f"   L={args.league_id}  W={sub.week}  STARTERS={sub.params['STARTERS']}")

        # FantasyPros-style in-session bookmarklet (no password needed)
        from .write.bookmarklet import mfl_bookmarklet, render_installer
        team_disp = ctx.display(target)
        rows = [(s.slot, s.name, s.pos, (f"{s.proj:.1f}" if plan.weekly else f"{s.proj:.0f}"))
                for s in plan.starters]
        rows += [(slot, name, pos, "—") for (_m, name, pos, slot) in sub.filled_from_roster]
        summary = (f"Set Week {sub.week} lineup for {team_disp}?\n\n"
                   + "\n".join(f"{slot}: {name}" for slot, name, _pos, _proj in rows))
        bm = mfl_bookmarklet(args.host, year, args.league_id, sub.week, sub.starter_ids, summary)
        out_dir = Path(args.out_dir) if args.out_dir else config.OUT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        installer = render_installer("MFL", team_disp, sub.week, f"⚡ Set {team_disp} Wk{sub.week}",
                                     bm, rows, notes=(["Missing: " + ", ".join(sub.missing)] if sub.missing else []))
        ipath = out_dir / f"set_lineup_mfl_{args.league_id}.html"
        ipath.write_text(installer, encoding="utf-8")
        print(f"\n⚡ FantasyPros-style button (no password): {ipath}")
        print("   Drag it to your bookmarks bar; click while logged into MFL to set the lineup in your session.")

        if not args.commit:
            print("\n[dry-run] Nothing was sent. Use the bookmarklet above (in-session, no password), "
                  "or --commit with MFL_USERNAME/MFL_PASSWORD to set it via the API now.")
            return 0
        user, pw = os.environ.get("MFL_USERNAME"), os.environ.get("MFL_PASSWORD")
        if not (user and pw):
            print("--commit needs MFL_USERNAME and MFL_PASSWORD environment variables.", file=sys.stderr)
            return 2
        print("\n>>> committing to MFL…")
        res = writer.commit(sub, user, pw)
        print("MFL response:", res)
        return 0

    if platform == "espn":
        from .write.espn_writer import EspnLineupWriter
        ctx, provider, target = _setup_espn(args, client.cache)
        if not target:
            print(f"Could not resolve --team {args.team!r}.", file=sys.stderr)
            return 2
        A = la.analyze(ctx, provider, weekly_week=wk, generate_weekly=bool(wk))
        plan = planmod.build_plan(A, target)
        year = args.season or "2025"
        writer = EspnLineupWriter(year, args.league_id, target)
        sub = writer.build(plan, wk or plan.week or 1, current_slot=None)
        team_disp = ctx.display(target)
        print(f"SET LINEUP — ESPN (experimental) · {team_disp} · Week {sub.week}")
        print(planmod.format_plan(plan))

        from .write.bookmarklet import espn_bookmarklet, render_installer
        rows = [(s.slot, s.name, s.pos, (f"{s.proj:.1f}" if plan.weekly else f"{s.proj:.0f}"))
                for s in plan.starters]
        targets = [(pid, sid) for (pid, _n, _p, _sl, sid) in sub.targets]
        summary = (f"Set Week {sub.week} ESPN lineup for {team_disp}? (experimental)\n\n"
                   + "\n".join(f"{slot}: {name}" for slot, name, _pos, _proj in rows))
        bm = espn_bookmarklet(year, args.league_id, target, targets, sub.week, summary)
        out_dir = Path(args.out_dir) if args.out_dir else config.OUT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        installer = render_installer(
            "ESPN (experimental)", team_disp, sub.week, f"⚡ Set {team_disp} Wk{sub.week}", bm, rows,
            notes=["ESPN's write API is unofficial. The bookmarklet reads your current lineup "
                   "in-session and POSTs the change — verify the result on ESPN after clicking."])
        ipath = out_dir / f"set_lineup_espn_{args.league_id}.html"
        ipath.write_text(installer, encoding="utf-8")
        print(f"\n⚡ ESPN bookmarklet (experimental, no password): {ipath}")
        print("   The bookmarklet reads your current lineup in-session — solving the slot-read gap. "
              "Verify the result on ESPN; their write API is unofficial.")
        return 0

    # sleeper — no write API; emit plan + deep link
    from .write.sleeper_deeplink import sleeper_lineup_url
    ctx = _context(args, client)
    provider = _provider(args, ctx, client)
    target = _resolve_user(ctx, args.team) if args.team else None
    if not target:
        print("Sleeper needs --team <you>.", file=sys.stderr)
        return 2
    A = la.analyze(ctx, provider, weekly_week=wk, generate_weekly=bool(wk))
    plan = planmod.build_plan(A, target)
    basis_lbl = f"DFS weekly proj (wk {A.weekly_week})" if plan.weekly else "dynasty value"
    print(f"SET LINEUP — Sleeper · {ctx.display(target)}  [{basis_lbl}]")
    print(planmod.format_plan(plan))
    print("\nSleeper has no write API. Open your lineup and set the above (zero-risk):")
    print(f"   {sleeper_lineup_url(ctx.newest['league_id'])}")
    return 0


# -- parser ------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dynasty_tool", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp):
        sp.add_argument("--league-id", required=True, help="any-season Sleeper league id; the chain is walked")
        sp.add_argument("--basis", default="realized",
                        choices=["realized", "current", "point_in_time"],
                        help="valuation basis (default realized)")
        sp.add_argument("--qb", type=int, choices=[1, 2], default=None,
                        help="override QB format (default: derived from roster_positions)")
        sp.add_argument("--refresh", action="store_true", help="ignore cache; re-fetch")
        sp.add_argument("--cache-dir", default=None)
        sp.add_argument("--out-dir", default=None)

    sp = sub.add_parser("ingest", help="build/refresh cache; print chain, aliases, coverage")
    common(sp)
    sp.set_defaults(func=cmd_ingest)

    sp = sub.add_parser("evaluate", help="score a proposed trade")
    common(sp)
    sp.add_argument("--side-a", required=True, help='comma-separated assets, e.g. "Bijan Robinson, 2027 1st"')
    sp.add_argument("--side-b", required=True, help='comma-separated assets, e.g. "Ja\'Marr Chase"')
    sp.add_argument("--name-a", default="Side A")
    sp.add_argument("--name-b", default="Side B")
    sp.set_defaults(func=cmd_evaluate)

    sp = sub.add_parser("flow", help="league value-flow analyzer (funnel)")
    common(sp)
    sp.set_defaults(func=cmd_flow)

    sp = sub.add_parser("optimize", help="roster value, windows, trade targets")
    common(sp)
    sp.add_argument("--team", default=None, help="user_id or display name for trade targets")
    sp.set_defaults(func=cmd_optimize)

    sp = sub.add_parser("analyze", help="full league analyzer: rankings, odds, trades, waivers, SOS -> dashboard")
    common(sp)
    sp.add_argument("--team", default=None, help="your user_id or display name (for tailored trades/waivers)")
    sp.add_argument("--redraft", action="store_true",
                    help="force redraft mode (projected-points value); auto-detected for type 0/1 leagues")
    sp.add_argument("--dynasty", action="store_true", help="force dynasty mode (dynasty value)")
    sp.add_argument("--platform", choices=["sleeper", "espn", "mfl"], default="sleeper",
                    help="league platform (espn/mfl use projected-points redraft valuation)")
    sp.add_argument("--season", default=None, help="season year (ESPN/MFL); default 2025")
    sp.add_argument("--host", default=None, help="MFL host, e.g. www45.myfantasyleague.com")
    sp.add_argument("--week", type=int, default=None,
                    help="rank Start/Sit by your DFS WEEKLY projection for this week "
                         "(redraft; generates data/processed/weekly_independent_* if missing)")
    sp.set_defaults(func=cmd_analyze)

    sp = sub.add_parser("set-lineup", help="compute the optimal weekly lineup and set it (dry-run by default)")
    sp.add_argument("--league-id", required=True)
    sp.add_argument("--platform", choices=["sleeper", "espn", "mfl"], default="sleeper")
    sp.add_argument("--team", default=None, help="your team (user_id / franchise id / name)")
    sp.add_argument("--week", type=int, default=None, help="week to set (uses DFS weekly projection)")
    sp.add_argument("--season", default=None)
    sp.add_argument("--host", default=None, help="MFL host")
    sp.add_argument("--commit", action="store_true",
                    help="actually write the lineup (MFL: needs MFL_USERNAME/MFL_PASSWORD env). Default is dry-run.")
    sp.add_argument("--basis", default="realized", choices=["realized", "current", "point_in_time"])
    sp.add_argument("--qb", type=int, choices=[1, 2], default=None)
    sp.add_argument("--espn-s2", default=None)
    sp.add_argument("--swid", default=None)
    sp.add_argument("--refresh", action="store_true")
    sp.add_argument("--cache-dir", default=None)
    sp.add_argument("--out-dir", default=None)
    sp.set_defaults(func=cmd_set_lineup)

    sp = sub.add_parser("portfolio", help="cross-league dashboard: one manager across all their dynasty leagues")
    sp.add_argument("--user", required=True, help="Sleeper username or user_id")
    sp.add_argument("--season", default=None, help="season year (default: current)")
    sp.add_argument("--refresh", action="store_true", help="ignore cache; re-fetch")
    sp.add_argument("--cache-dir", default=None)
    sp.add_argument("--out-dir", default=None)
    sp.set_defaults(func=cmd_portfolio)
    return p


def main(argv=None) -> int:
    config.force_utf8_stdout()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except PointInTimeUnavailable as e:  # safety net
        print(e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
