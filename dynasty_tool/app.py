"""Penner HQ — a FantasyPros-"My Playbook"-style dashboard over dynasty_tool.

Run:  python -m streamlit run dynasty_tool/app.py --server.port 8502
(the .claude/launch.json "dynasty-app" entry passes the dark theme flags)

Left sidebar = league switcher, team switcher ("view any team"), grouped nav.
All analysis comes from the shared dynasty_tool core (league_analysis.analyze);
this file only loads, caches, and renders.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit.components.v1 import html as components_html

from dynasty_tool import config as dt
from dynasty_tool import webapp_helpers as wh
from dynasty_tool.cache import DiskCache
from dynasty_tool.ingest.sleeper_client import SleeperClient
from dynasty_tool.ingest.context import build_context
from dynasty_tool.value.current_provider import CurrentValueProvider, load_dp_frames
from dynasty_tool.analysis import league_analysis as la
from dynasty_tool.analysis import trade_calc as tc
from dynasty_tool.write.sleeper_deeplink import sleeper_lineup_url

INK, SERIES, POS, TIER, STATUS = wh.INK, wh.SERIES, wh.POS_COLORS, wh.TIER_COLORS, wh.STATUS
EXTRA_LEAGUES_PATH = dt.CACHE_DIR / "app_leagues.json"

# Feed styling only. Guarded because this file is executed via runpy: a hard
# import failure here would take down all 11 existing pages, not just the feed.
try:
    from dynasty_tool.analysis.news_render import NEWS_CSS
except Exception:  # pragma: no cover
    NEWS_CSS = ""


def _is_cloud() -> bool:
    """Cloud (public) build: show only Sleeper dynasty leagues; never seed the
    MFL entry (its projections come from the private DFS model, not shipped)."""
    if os.environ.get("PENNER_HQ_CLOUD"):
        return True
    try:
        import streamlit as _st
        return bool(_st.secrets.get("PENNER_HQ_CLOUD", ""))
    except Exception:
        return False


CLOUD = _is_cloud()


def _check_password() -> bool:
    """Optional gate — active only when APP_PW_SHA256 is set in st.secrets
    (the public deploy). Compares a SHA-256 of the entry; the plaintext password
    is never stored anywhere. Local runs (no secret) stay open."""
    import hashlib
    import hmac
    try:
        expected = str(st.secrets.get("APP_PW_SHA256", "") or "")
    except Exception:
        expected = ""
    if not expected or st.session_state.get("_authed"):
        return True
    _, mid, _ = st.columns([1, 1.2, 1])
    with mid:
        st.markdown("<div class='hq-brand' style='margin-top:13vh'>🏈 PENNER "
                    "<span>HQ</span></div><div class='hq-sub'>enter password to "
                    "continue</div>", unsafe_allow_html=True)
        # a form submits the field value atomically with the button (one rerun),
        # so Enter-to-submit works and the value is never read stale
        with st.form("gate", clear_on_submit=False, border=False):
            pw = st.text_input("Password", type="password",
                               label_visibility="collapsed", placeholder="Password")
            submitted = st.form_submit_button("Enter", type="primary", width="stretch")
        if submitted:
            if hmac.compare_digest(hashlib.sha256(pw.encode("utf-8")).hexdigest(),
                                   expected):
                st.session_state["_authed"] = True
                st.rerun()
            st.error("Wrong password.")
    return False

st.set_page_config(page_title="Penner HQ", page_icon="🏈", layout="wide",
                   initial_sidebar_state="expanded")

# ---------------------------------------------------------------------------
# CSS — dark, vibrant, card-based (tokens from the validated dark palette)
# ---------------------------------------------------------------------------
st.markdown(f"""
<style>
:root {{
  --page:{INK['page']}; --surface:{INK['surface']}; --ink:{INK['primary']};
  --ink2:{INK['secondary']}; --muted:{INK['muted']}; --grid:{INK['grid']};
  --border:{INK['border']}; --accent:{SERIES['blue']};
}}
html, body, [data-testid="stAppViewContainer"] {{ background: var(--page); }}
[data-testid="stHeader"] {{ background: rgba(0,0,0,0); }}
#MainMenu, footer {{ visibility: hidden; }}
[data-testid="stSidebar"] {{ background: #111110; border-right: 1px solid var(--border); }}
[data-testid="stSidebar"] .stButton button {{
  justify-content: flex-start; text-align: left; width: 100%;
  border-radius: 8px; padding: 4px 10px; font-size: 14px;
}}
.hq-brand {{ font-size: 20px; font-weight: 800; color: var(--ink);
  letter-spacing: .02em; padding: 2px 0 0 2px; }}
.hq-brand span {{ color: var(--accent); }}
.hq-sub {{ color: var(--muted); font-size: 11.5px; margin: -2px 0 10px 2px; }}
.hq-navhead {{ color: var(--muted); font-size: 10.5px; font-weight: 700;
  text-transform: uppercase; letter-spacing: .08em; margin: 14px 0 2px 4px; }}
.hq-card {{ background: var(--surface); border: 1px solid var(--border);
  border-radius: 14px; padding: 16px 18px; margin-bottom: 14px; }}
.hq-tile {{ background: var(--surface); border: 1px solid var(--border);
  border-radius: 14px; padding: 14px 16px; }}
.hq-tile .t {{ color: var(--muted); font-size: 11px; font-weight: 600;
  text-transform: uppercase; letter-spacing: .06em; }}
.hq-tile .v {{ color: var(--ink); font-size: 26px; font-weight: 800; margin-top: 2px; }}
.hq-tile .s {{ color: var(--ink2); font-size: 12px; margin-top: 2px; }}
.hq-badge {{ display: inline-block; color: #fff; font-size: 11px; font-weight: 700;
  padding: 2px 9px; border-radius: 999px; margin-right: 6px; white-space: nowrap; }}
.hq-pos {{ display: inline-block; color: #fff; font-size: 10.5px; font-weight: 800;
  padding: 1px 7px; border-radius: 6px; margin-right: 7px; }}
.hq-h {{ color: var(--ink); font-size: 16px; font-weight: 700; margin: 2px 0 8px; }}
.hq-note {{ color: var(--muted); font-size: 12px; border-left: 3px solid var(--grid);
  padding: 3px 0 3px 10px; margin: 6px 0; line-height: 1.5; }}
.hq-row {{ display: flex; justify-content: space-between; padding: 6px 2px;
  border-bottom: 1px solid var(--grid); color: var(--ink2); font-size: 13.5px; }}
.hq-row b {{ color: var(--ink); }}
.hq-bar {{ height: 9px; background: {INK['baseline']}; border-radius: 5px; overflow: hidden; }}
.hq-bar > span {{ display: block; height: 100%; background: var(--accent); }}
.hq-vs {{ color: var(--muted); font-size: 12px; padding: 0 8px; }}
.hq-split {{ display: flex; height: 26px; border-radius: 8px; overflow: hidden;
  border: 1px solid var(--border); }}
.hq-split > div {{ display: flex; align-items: center; font-size: 12px;
  font-weight: 700; color: #fff; padding: 0 10px; white-space: nowrap; }}
.hq-asset {{ display: flex; align-items: center; gap: 8px; padding: 7px 2px;
  border-bottom: 1px solid var(--grid); font-size: 13.5px; color: var(--ink); }}
.hq-asset .who {{ color: var(--muted); font-size: 11.5px; }}
.hq-asset .val {{ margin-left: auto; font-weight: 700; font-variant-numeric: tabular-nums; }}
a {{ color: var(--accent); }}
{NEWS_CSS}
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# cached loaders (all args primitive; all returns pickle-safe)
# ---------------------------------------------------------------------------
@st.cache_resource
def sleeper_client() -> SleeperClient:
    return SleeperClient(DiskCache(dt.CACHE_DIR))


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def dp_frames():
    players, picks, ids = load_dp_frames(DiskCache(dt.CACHE_DIR))
    return players, picks, ids


@st.cache_data(ttl=1800, show_spinner=False)
def discover_leagues(username: str, season: str):
    c = sleeper_client()
    u = c.user(username) or {}
    uid = str(u.get("user_id")) if u.get("user_id") else ""
    out = []
    if uid:
        for lg in c.user_leagues(uid, season):
            if (lg.get("settings") or {}).get("type") == 2:
                out.append({"platform": "sleeper", "league_id": lg["league_id"],
                            "name": lg.get("name", ""), "season": season,
                            "status": lg.get("status", ""), "team": username})
    out.sort(key=lambda x: x["name"])
    return uid, out


@st.cache_data(ttl=900, show_spinner=False)
def load_league(platform: str, league_id: str, season: str, host: str,
                my_token: str, week: int):
    """Analyze one league -> pickle-safe bundle for every page."""
    cache = DiskCache(dt.CACHE_DIR)
    if platform == "sleeper":
        client = sleeper_client()
        ctx = build_context(client, league_id, full=False)
        prov = CurrentValueProvider.from_source(cache, ctx.qb_format)
        u = client.user(my_token) or {}
        my_uid = str(u.get("user_id")) if u.get("user_id") else None
        g2s: dict = {}
        scrape = str(prov.scrape_date or "")
    else:  # mfl
        from dynasty_tool.ingest.mfl_client import MflClient
        from dynasty_tool.ingest.mfl_context import build_mfl_context, resolve_mfl_team
        from dynasty_tool.value.projection_provider import ProjectionValueProvider
        inner = ProjectionValueProvider.from_source(cache)
        mfl = MflClient(cache, host)
        ctx = build_mfl_context(mfl, inner, league_id, season)
        prov = inner.view("gsis")
        my_uid = resolve_mfl_team(ctx, my_token)
        g2s = {g: s for s, g in inner.sleeper_to_gsis.items()}
        scrape = ""
    A = la.analyze(ctx, prov, weekly_week=(week or None), generate_weekly=False)
    rostered = [p.sleeper_id for rv in A.rosters.values() for p in rv.players]
    meta = wh.meta_subset(ctx.players_meta, rostered)
    return {"A": A, "meta": meta, "g2s": g2s, "my_uid": my_uid,
            "qb": ctx.qb_format, "scrape": scrape}


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_flow(league_id: str):
    """Full-history value flow (walks the whole chain — heavy on first run)."""
    from dynasty_tool.ingest.trades import normalize_trades
    from dynasty_tool.analysis.value_flow import compute_flow
    client = sleeper_client()
    ctx = build_context(client, league_id, full=True)
    prov = CurrentValueProvider.from_source(DiskCache(dt.CACHE_DIR), ctx.qb_format)
    trades = normalize_trades(client, ctx.chain, ctx.roster_maps)
    return compute_flow(ctx, prov, trades)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def trade_universe(redraft: bool, qb_format: int) -> dict:
    """Every tradeable asset (players + picks) -> Asset, for the calculator's search.

    Dynasty reads the DynastyProcess values through the validated provider;
    redraft/keeper reads this season's projections from our own engine.
    """
    if redraft:
        return tc.build_redraft_universe(pd.read_parquet(dt.SEASON_RANKINGS))
    players, picks, ids = dp_frames()
    prov = CurrentValueProvider(players, picks, ids, qb_format=qb_format)
    return tc.build_dynasty_universe(prov, players)


@st.cache_data(ttl=3600, show_spinner=False)
def nfl_week() -> int:
    try:
        return int((sleeper_client().state() or {}).get("week") or 0)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# beat feed loaders
# ---------------------------------------------------------------------------
def secret(name: str) -> str:
    """st.secrets first, env second. Never hardcode a key in this repo."""
    try:
        v = st.secrets.get(name, "")
        if v:
            return str(v)
    except Exception:
        pass
    return str(os.environ.get(name, "") or "")


@st.cache_data(ttl=dt.NEWS_MAX_AGE_MINUTES * 60, show_spinner=False)
def load_news(_sig: str, twitter_key: str):
    """Every enabled source, in parallel. Returns (items as dicts, health as
    dicts) — plain data, so the cache stays pickle-safe and version-tolerant.

    ``_sig`` is a hash of the enabled source list: editing feeds.json changes it
    and busts this cache immediately, rather than serving a stale feed.
    """
    from dynasty_tool.ingest.news_client import NewsClient, fetch_all
    from dynasty_tool.ingest.news_model import load_sources
    specs = load_sources()
    client = NewsClient(DiskCache(dt.CACHE_DIR), twitter_key=twitter_key)
    items, health = fetch_all(client, specs)
    return [i.to_dict() for i in items], [h.__dict__ for h in health]


@st.cache_data(ttl=1800, show_spinner=False)
def my_rostered_ids(username: str, season: str) -> tuple:
    """Every sleeper player id you roster, across ALL your dynasty leagues.

    Three cheap endpoints — no chain walk, no DynastyProcess CSVs, no Monte
    Carlo. Deliberately not derived from ``load_league``: the feed should flag a
    player you own in *any* league, not just the one selected in the sidebar,
    and this costs ~1% of a full league analysis.
    """
    c = sleeper_client()
    u = c.user(username) or {}
    uid = str(u.get("user_id") or "")
    if not uid:
        return ()
    out: set[str] = set()
    for lg in c.user_leagues(uid, season) or []:
        if (lg.get("settings") or {}).get("type") != 2:
            continue
        for r in c.rosters(str(lg.get("league_id") or "")) or []:
            if str(r.get("owner_id") or "") != uid:
                continue
            for pid in (r.get("players") or []):
                out.add(str(pid))
    return tuple(sorted(out))


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def news_index(qb_format: int, extra_ids: tuple) -> dict:
    """Name index over (top-N by value ∪ your rostered players).

    Never the full ~11k Sleeper blob: shrinking the candidate pool is the single
    most effective false-positive control in the tagger, and it is what keeps two
    different Josh Allens from making every mention ambiguous.
    """
    from dynasty_tool.analysis.news_feed import build_name_index
    players, _picks, ids = dp_frames()
    ecr = wh.ecr_map(players, ids, qb_format=qb_format)
    ranked = sorted(ecr.items(),
                    key=lambda kv: -(kv[1].get("value") or 0))[:dt.NEWS_POOL_TOP_N]
    pool = {str(pid) for pid, _ in ranked} | {str(p) for p in (extra_ids or ())}
    meta = wh.meta_subset(sleeper_client().players(), pool)
    return build_name_index(meta, pool=pool)


# ---------------------------------------------------------------------------
# movers loaders
# ---------------------------------------------------------------------------
@st.cache_data(ttl=6 * 3600, show_spinner=False)
def dp_movers(days: int):
    """(now rows, then rows, as-of label) from the DynastyProcess git history.

    No snapshot storage of our own: the values CSV is committed weekly to a
    public repo, so history already exists and Streamlit Cloud's ephemeral disk
    can't lose it.
    """
    import datetime as _d
    from dynasty_tool.ingest import movers_client as mc
    cache = DiskCache(dt.CACHE_DIR)
    now_rows = mc.values_now(cache)
    target = (_d.datetime.now(_d.timezone.utc) - _d.timedelta(days=days))
    sha = mc.commit_before(target.strftime("%Y-%m-%dT%H:%M:%SZ"), cache)
    then_rows = mc.values_at(sha, cache)
    asof = (then_rows[0].get("scrape_date") if then_rows else "") or target.date().isoformat()
    return now_rows, then_rows, str(asof)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def ktc_board():
    from dynasty_tool.ingest import movers_client as mc
    return mc.fetch_ktc(DiskCache(dt.CACHE_DIR))


@st.cache_data(ttl=12 * 3600, show_spinner=False)
def dp_pick_rows():
    from dynasty_tool.ingest import movers_client as mc
    return mc.values_now(DiskCache(dt.CACHE_DIR), path=mc.PICKS_PATH)


@st.cache_data(ttl=1800, show_spinner=False)
def sleeper_trending(kind: str, hours: int, limit: int):
    """Sleeper's add/drop volume, enriched with player meta.

    This is behaviour, not opinion: what managers across every Sleeper league
    are actually doing, which moves hours before expert values react.
    """
    c = sleeper_client()
    rows = c.trending(kind, lookback_hours=int(hours), limit=int(limit)) or []
    ids = [str(r.get("player_id")) for r in rows if r.get("player_id")]
    meta = wh.meta_subset(c.players(), ids)
    return [{"pid": str(r.get("player_id")), "count": int(r.get("count") or 0),
             **{k: (meta.get(str(r.get("player_id")), {}) or {}).get(k)
                for k in ("full_name", "position", "team", "age", "injury_status")}}
            for r in rows if r.get("player_id")]


@st.cache_data(ttl=12 * 3600, show_spinner=False)
def usage_season(year: int):
    """Route participation + snap share for one season, keyed by sleeper_id.

    Every join here is by id — sleeper -> gsis -> pfr comes straight out of
    DynastyProcess's db_playerids, which the app already downloads for values.
    """
    from dynasty_tool.analysis import usage as usg
    from dynasty_tool.ingest import usage_client as uc
    cache = DiskCache(dt.CACHE_DIR)
    _players, _picks, ids = dp_frames()
    id_rows = ids.to_dict("records")
    pfr2gsis = usg.crosswalk(id_rows, "pfr_id", "gsis_id")
    gsis2sleeper = usg.crosswalk(id_rows, "gsis_id", "sleeper_id")

    part = uc.participation_summary(int(year), cache)
    snaps = usg.aggregate_snaps(uc.snap_counts(int(year), cache), pfr2gsis)
    try:
        targets = uc.season_targets(int(year), cache)
    except Exception:
        targets = {}
    built = usg.build_usage(part.get("routes") or {}, part.get("team_dropbacks") or {},
                            part.get("teams") or {}, snaps, targets)
    ok, note = usg.looks_sane(built)

    # Participation covers all 11 players on the field, so offensive linemen sit
    # at ~100% route share and would otherwise own every leaderboard. Names and
    # positions come from the same id file, not from the league bundle, which
    # only knows about players someone in this league rosters.
    meta = {}
    for r in id_rows:
        g = str(r.get("gsis_id") or "").strip()
        if g and g not in ("NA", "nan"):
            meta.setdefault(g, {"name": r.get("name"),
                                "position": str(r.get("position") or "").upper()})
    # QBs are on the field for every dropback by definition, so they sit at 100%
    # route share and would top the board with a tgt/route of 0.000. These are
    # receiving-usage metrics; quarterbacks don't belong in them.
    skill = {"RB", "WR", "TE"}

    rows = []
    for gsis, u in built.items():
        sid = gsis2sleeper.get(gsis)
        m = meta.get(gsis) or {}
        if not sid or m.get("position") not in skill:
            continue
        rows.append({"sleeper_id": sid, "name": m.get("name") or sid,
                     "position": m.get("position"), "routes": u.routes,
                     "route_pct": u.route_pct, "snap_pct": u.snap_pct,
                     "tprr": u.tprr, "targets": u.targets, "games": u.games})
    return rows, {"ok": ok, "note": note, "plays": part.get("plays", 0),
                  "resolved": len(rows), "total": len(built)}


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def college_features(_api_key: str, first_year: int, last_year: int):
    """{cfb_player_id: {dominator, breakout, pedigree}} for the rookie model.

    Bridges CFBD to nflverse on (draft year, overall pick) — exact, no names.
    Returns ({}, report) without a key so the model simply reports those
    features as absent instead of training them to a phantom zero weight.
    """
    from dynasty_tool.analysis import college_features as cfeat
    from dynasty_tool.ingest.cfbd_client import CfbdClient
    from dynasty_tool.ingest import nflverse_client as nv
    if not _api_key:
        return {}, {"skipped": "no CFBD key"}
    cache = DiskCache(dt.CACHE_DIR)
    client = CfbdClient(_api_key, cache)
    years = range(int(first_year), int(last_year) + 1)

    stats, picks, recruits = [], [], []
    for y in years:
        for bucket, call in ((stats, lambda yy=y: client.player_season_stats(yy)),
                             (picks, lambda yy=y: client.draft_picks(yy)),
                             (recruits, lambda yy=y: client.recruits(yy - 3))):
            try:
                bucket.extend(call() or [])
            except Exception:
                continue          # one bad season must not blank the feature

    pivot = cfeat.pivot_season_stats(stats)
    dom = cfeat.dominators(pivot)
    positions = {pid: "" for (_s, pid) in pivot}
    birth = {}
    ped = {}
    for r in recruits:
        aid = str(r.get("athleteId") or "").strip()
        if aid and r.get("rating"):
            ped[aid] = float(r["rating"])
    bridge, brep = cfeat.draft_bridge(picks, nv.draft_picks(cache))
    breakout = cfeat.breakout_ages(dom, pivot, birth, positions)
    feats, rep = cfeat.build_college_features(dom, breakout, ped, bridge)
    rep.update(brep)
    return feats, rep


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def rookie_model(holdout: int, positions: tuple, max_season: int,
                 _cfbd_key: str = ""):
    """Train the hit-rate model on every skill player drafted since 2010.

    Everything joins on ids (cfb_player_id / pfr_id), never on names. Returns
    (rookie fit, held-out AUC, devy fit, devy held-out AUC, rows) so the page can
    show both the post-draft and the pre-draft view side by side.
    """
    from dynasty_tool.analysis import rookie_model as rmod
    from dynasty_tool.ingest import nflverse_client as nv
    cache = DiskCache(dt.CACHE_DIR)
    draft = [d for d in nv.draft_picks(cache)
             if str(d.get("season") or "").isdigit() and int(d["season"]) >= 2010]
    college, _crep = college_features(_cfbd_key, 2010, int(max_season))
    rows = rmod.build_rows(draft, nv.combine(cache), college=college or None,
                           positions=positions, max_season=int(max_season))
    fit_r, oos_r = rmod.fit_holdout(rows, rmod.ROOKIE_FEATURES,
                                    holdout_seasons=int(holdout))
    fit_d, oos_d = rmod.fit_holdout(rows, rmod.DEVY_FEATURES,
                                    holdout_seasons=int(holdout))
    return fit_r, oos_r, fit_d, oos_d, rows


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def devy_board(_api_key: str, year: int, positions: tuple):
    """College usage + recruiting pedigree -> a ranked devy board.

    ``_api_key`` is underscore-prefixed so Streamlit skips hashing it: the cache
    key is (year, positions), and the key never lands in a cache entry.

    Four recruiting classes are pulled so that freshmen through seniors all
    resolve a class year; that plus one league-wide usage call is the whole
    request budget.
    """
    from dynasty_tool.analysis import devy as dvy
    from dynasty_tool.ingest.cfbd_client import CfbdClient
    client = CfbdClient(_api_key, DiskCache(dt.CACHE_DIR))
    usage = client.player_usage(int(year))
    # One extra call buys the year-over-year trend, which is the whole point:
    # a sophomore going 12% -> 28% of his offence is the profile that turns
    # into draft capital, and it shows up a year before any big board says so.
    try:
        prior = dvy.index_usage(client.player_usage(int(year) - 1))
    except Exception:
        prior = {}
    recruits: list = []
    for back in range(0, 5):
        try:
            recruits.extend(client.recruits(int(year) - back))
        except Exception:
            continue          # one missing class shouldn't blank the board
    prospects, report = dvy.build_board(
        usage, dvy.index_recruits(recruits), season=int(year),
        positions=positions or dvy.DEVY_POSITIONS, prior_usage=prior)
    report["with_trend"] = sum(1 for p in prospects if p.prev_usage is not None)
    return prospects, report


# ---------------------------------------------------------------------------
# small render helpers
# ---------------------------------------------------------------------------
def tile(col, title: str, value: str, sub: str = ""):
    col.markdown(f"<div class='hq-tile'><div class='t'>{title}</div>"
                 f"<div class='v'>{value}</div><div class='s'>{sub}</div></div>",
                 unsafe_allow_html=True)


def pos_badge(pos: str) -> str:
    c = POS.get(pos, "#6b6a64")
    return f"<span class='hq-pos' style='background:{c}'>{pos}</span>"


def window_badge(rv) -> str:
    c = TIER.get(rv.value_axis, "#6b6a64")
    return f"<span class='hq-badge' style='background:{c}'>{rv.window}</span>"


def headshot_for(pid: str, bundle) -> str | None:
    sid = pid if bundle["g2s"] == {} else bundle["g2s"].get(pid)
    return wh.headshot_url(sid) if sid else None


def _fig(height: int = 360) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        height=height, margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family='system-ui, -apple-system, "Segoe UI", sans-serif',
                  color=INK["secondary"], size=12),
        hoverlabel=dict(bgcolor=INK["surface"], font_color=INK["primary"],
                        bordercolor=INK["baseline"]),
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color=INK["secondary"])),
    )
    return fig


def radar_fig(A, uid) -> go.Figure:
    pct = wh.radar_percentiles(A.rosters, uid)
    axes = list(pct.keys())
    mine = [round(pct[a] * 100) for a in axes]
    fig = _fig(330)
    fig.add_trace(go.Scatterpolar(
        r=mine + mine[:1], theta=axes + axes[:1], name=A.rosters[uid].display,
        fill="toself", line=dict(color=SERIES["blue"], width=2),
        fillcolor="rgba(57,135,229,0.25)",
        hovertemplate="%{theta}: %{r}th pct<extra></extra>"))
    fig.add_trace(go.Scatterpolar(
        r=[50] * (len(axes) + 1), theta=axes + axes[:1], name="league median",
        line=dict(color=INK["muted"], width=1.5, dash="dot"), hoverinfo="skip"))
    fig.update_layout(polar=dict(
        bgcolor="rgba(0,0,0,0)",
        radialaxis=dict(range=[0, 100], gridcolor=INK["grid"], tickfont=dict(size=9),
                        color=INK["muted"]),
        angularaxis=dict(gridcolor=INK["grid"], color=INK["secondary"])))
    return fig


def power_fig(A, uid) -> go.Figure:
    """Title odds by team — magnitude, one hue; my team at full strength."""
    sims = list(A.sim)
    names = [s.display for s in sims][::-1]
    vals = [s.title_pct * 100 for s in sims][::-1]
    colors = [SERIES["blue"] if s.user_id == uid else "rgba(57,135,229,0.38)"
              for s in sims][::-1]
    fig = _fig(max(300, 26 * len(names)))
    fig.add_trace(go.Bar(
        x=vals, y=names, orientation="h", marker=dict(color=colors),
        text=[f"{v:.0f}%" for v in vals], textposition="outside",
        textfont=dict(color=INK["secondary"], size=11),
        hovertemplate="%{y}: %{x:.1f}% title odds<extra></extra>"))
    fig.update_layout(showlegend=False,
                      xaxis=dict(showgrid=False, zeroline=False, visible=False,
                                 range=[0, max(vals) * 1.25 if vals else 1]),
                      yaxis=dict(showgrid=False, color=INK["secondary"]))
    return fig


def contention_fig(A, uid) -> go.Figure:
    rosters = list(A.rosters.values())
    xs = [r.avg_starter_age or 0 for r in rosters]
    med_age = sorted(x for x in xs if x)[len([x for x in xs if x]) // 2] if any(xs) else 26
    med_val = sorted(r.starter_value for r in rosters)[len(rosters) // 2]
    fig = _fig(380)
    tiers = [("high", "Contend"), ("mid", "Bubble"), ("low", "Rebuild")]
    for axis, label in tiers:
        pts = [r for r in rosters if r.value_axis == axis]
        if not pts:
            continue
        fig.add_trace(go.Scatter(
            x=[p.avg_starter_age or med_age for p in pts],
            y=[p.starter_value for p in pts],
            mode="markers+text", name=label,
            text=[p.display for p in pts], textposition="top center",
            textfont=dict(size=10, color=INK["secondary"]),
            marker=dict(size=[15 if p.user_id == uid else 10 for p in pts],
                        color=TIER[axis],
                        line=dict(color=INK["surface"], width=2)),
            hovertemplate="%{text}: value %{y:,.0f}, age %{x:.1f}<extra></extra>"))
    fig.add_vline(x=med_age, line=dict(color=INK["baseline"], dash="dot", width=1))
    fig.add_hline(y=med_val, line=dict(color=INK["baseline"], dash="dot", width=1))
    fig.update_layout(
        xaxis=dict(title="avg starter age → older", gridcolor=INK["grid"],
                   zeroline=False, color=INK["muted"]),
        yaxis=dict(title="starter value → stronger", gridcolor=INK["grid"],
                   zeroline=False, color=INK["muted"]))
    return fig


def race_fig(A, uid) -> go.Figure:
    race = list(A.race or [])
    race.sort(key=lambda r: (r.playoff_pct, r.title_pct))
    status_color = {"clinched": STATUS["good"], "eliminated": "#6b6a64",
                    "in the hunt": SERIES["blue"], "preseason": SERIES["blue"]}
    fig = _fig(max(300, 26 * len(race)))
    seen = set()
    for r in race:
        fig.add_trace(go.Bar(
            x=[r.playoff_pct * 100], y=[r.display], orientation="h",
            name=r.status, legendgroup=r.status,
            showlegend=r.status not in seen,
            marker=dict(color=status_color.get(r.status, SERIES["blue"]),
                        opacity=1.0 if r.user_id == uid else 0.6),
            text=[f"{r.playoff_pct*100:.0f}% ({r.title_pct*100:.0f}% title)"],
            textposition="outside", textfont=dict(size=11, color=INK["secondary"]),
            hovertemplate=(f"{r.display}: {r.current_wins:g} wins now → "
                           f"proj {r.proj_wins:.1f}<extra></extra>")))
        seen.add(r.status)
    fig.update_layout(barmode="overlay",
                      xaxis=dict(visible=False, range=[0, 130]),
                      yaxis=dict(showgrid=False, color=INK["secondary"]))
    return fig


def flow_fig(res, top_n: int = 4) -> go.Figure:
    accounts = sorted(res.accounts.values(), key=lambda a: abs(a.net), reverse=True)
    top = {a.user_id for a in accounts[:top_n]}
    palette = [SERIES["blue"], SERIES["aqua"], SERIES["red"], SERIES["yellow"]]
    fig = _fig(420)
    ci = 0
    for uid, series in res.timeline.items():
        if not series:
            continue
        xs = [t for t, _ in series]
        ys = [v for _, v in series]
        disp = res.accounts[uid].display
        if uid in top:
            fig.add_trace(go.Scatter(
                x=xs, y=ys, mode="lines", name=disp,
                line=dict(color=palette[ci % len(palette)], width=2.4),
                hovertemplate=disp + " %{x|%Y-%m-%d}: %{y:,.0f}<extra></extra>"))
            ci += 1
        else:
            fig.add_trace(go.Scatter(
                x=xs, y=ys, mode="lines", showlegend=False,
                line=dict(color="rgba(137,135,129,0.35)", width=1.1),
                hovertemplate=disp + " %{x|%Y-%m-%d}: %{y:,.0f}<extra></extra>"))
    fig.add_hline(y=0, line=dict(color=INK["baseline"], width=1))
    fig.update_layout(
        xaxis=dict(gridcolor=INK["grid"], color=INK["muted"]),
        yaxis=dict(title="cumulative net value (realized)", gridcolor=INK["grid"],
                   color=INK["muted"]))
    return fig


def year_flow_fig(res, uid: str) -> go.Figure:
    """Net value the account extracted per season — diverging around zero."""
    years = sorted(res.years)
    nets = []
    for y in years:
        flows = res.flows_by_year.get(y, {})
        received = sum(v for (g, r), v in flows.items() if r == uid)
        given = sum(v for (g, r), v in flows.items() if g == uid)
        nets.append(received - given)
    fig = _fig(300)
    fig.add_trace(go.Bar(
        x=[str(y) for y in years], y=nets,
        marker=dict(color=[SERIES["blue"] if v >= 0 else SERIES["red"] for v in nets]),
        text=[wh.fmt_k(v) for v in nets], textposition="outside",
        textfont=dict(size=11, color=INK["secondary"]),
        hovertemplate="%{x}: net %{y:,.0f}<extra></extra>"))
    fig.add_hline(y=0, line=dict(color=INK["baseline"], width=1))
    fig.update_layout(showlegend=False,
                      xaxis=dict(color=INK["secondary"], showgrid=False),
                      yaxis=dict(gridcolor=INK["grid"], color=INK["muted"]))
    return fig


# ---------------------------------------------------------------------------
# sidebar — league switcher, team switcher, grouped nav (everything on the left)
# ---------------------------------------------------------------------------
NAV = [
    # First: this is the several-times-a-day page, the one replacing the Twitter
    # habit, so it should be the first thing your thumb reaches.
    ("WIRE", [("📰", "Beat Feed"), ("📈", "Movers")]),
    ("LINEUP", [("📊", "Dashboard"), ("👥", "My Team"), ("⚡", "Start/Sit"),
                ("🆚", "Matchups")]),
    ("SEASON", [("🏆", "Playoff Race"), ("📈", "History & SOS")]),
    ("MOVES", [("🔄", "Trades"), ("➕", "Waivers"), ("🎯", "Set Lineup")]),
    ("LEAGUE", [("💸", "Value Flow"), ("🌐", "Portfolio")]),
]

st.session_state.setdefault("page", "Dashboard")
st.session_state.setdefault("username", "PennerBoy")
st.session_state.setdefault("season", "2026")

if not _check_password():
    st.stop()

with st.sidebar:
    st.markdown("<div class='hq-brand'>🏈 PENNER <span>HQ</span></div>"
                "<div class='hq-sub'>my playbook — powered by your own engine</div>",
                unsafe_allow_html=True)

    with st.expander("⚙️ Account & leagues"):
        st.session_state.username = st.text_input("Sleeper username",
                                                  st.session_state.username)
        st.session_state.season = st.text_input("Season", st.session_state.season)
        if st.button("🔄 Refresh all data", width="stretch"):
            st.cache_data.clear()
            st.rerun()

    my_uid_hint, sleeper_leagues = discover_leagues(st.session_state.username,
                                                    st.session_state.season)
    extras = wh.load_extra_leagues(EXTRA_LEAGUES_PATH)
    if not extras and not CLOUD:
        extras = wh.default_extra_leagues()
    leagues = sleeper_leagues + extras
    if not leagues:
        st.error("No leagues found — check the Sleeper username.")
        st.stop()

    labels = [f"{lg['name']}  ·  {lg['platform']}" for lg in leagues]
    li = st.selectbox("League", range(len(leagues)), format_func=lambda i: labels[i])
    lg = leagues[li]

    wk_default = nfl_week()
    week = st.number_input("Week (for start/sit & odds)", 1, 18,
                           value=(wk_default if 1 <= wk_default <= 18 else 1))

    with st.spinner(f"Analyzing {lg['name']}…"):
        try:
            bundle = load_league(lg["platform"], str(lg["league_id"]),
                                 str(lg.get("season", st.session_state.season)),
                                 str(lg.get("host", "")),
                                 str(lg.get("team", st.session_state.username)),
                                 int(week))
        except Exception as e:  # keep the app alive; show the real error
            st.error(f"League load failed: {e}")
            st.stop()

    A = bundle["A"]
    uids = sorted(A.rosters, key=lambda u: A.rosters[u].starter_value, reverse=True)
    default_uid = bundle["my_uid"] if bundle["my_uid"] in A.rosters else uids[0]
    view_uid = st.selectbox("Viewing team", uids,
                            index=uids.index(default_uid),
                            format_func=lambda u: A.rosters[u].display)

    for group, items in NAV:
        st.markdown(f"<div class='hq-navhead'>{group}</div>", unsafe_allow_html=True)
        for icon, name in items:
            is_active = st.session_state.page == name
            slug = name.replace(" ", "_").replace("/", "_").replace("&", "and")
            if st.button(f"{icon}  {name}", key=f"nav_{slug}",
                         type="primary" if is_active else "tertiary",
                         width="stretch"):
                st.session_state.page = name
                st.rerun()

    st.markdown(f"<div class='hq-note'>basis: {A.basis} · {A.qb_format}QB"
                + (f" · values {bundle['scrape']}" if bundle['scrape'] else "")
                + "</div>", unsafe_allow_html=True)

page = st.session_state.page
if page not in {n for _g, items in NAV for _i, n in items}:
    page = st.session_state.page = "Dashboard"   # a page that no longer exists
me = A.rosters[view_uid]
my_sim = next((s for s in A.sim if s.user_id == view_uid), None)
my_rank = uids.index(view_uid) + 1


def roster_df(rv, rows) -> pd.DataFrame:
    """(slot, RPlayer) rows -> display dataframe with headshots."""
    recs = []
    for slot, p in rows:
        proj = A.weekly.get(p.sleeper_id) if A.weekly else None
        recs.append({
            "": headshot_for(p.sleeper_id, bundle) or "",
            "slot": slot, "player": p.name, "pos": p.pos,
            "value": round(p.value),
            "this wk": (round(proj, 1) if proj is not None else None),
            "age": (round(p.age, 1) if p.age else None),
        })
    df = pd.DataFrame(recs)
    if A.weekly is None and "this wk" in df.columns:
        df = df.drop(columns=["this wk"])
    return df


def show_roster_table(df: pd.DataFrame, height: int | None = None):
    kwargs = {"height": height} if height else {}
    st.dataframe(
        df, hide_index=True, width="stretch",
        column_config={"": st.column_config.ImageColumn("", width=42)}, **kwargs)


# ===========================================================================
if page == "Beat Feed":
    # Imports are lazy and local: this file is executed via runpy, so a failure
    # importing the news stack must not take down the other 11 pages.
    import time as _time
    from dynasty_tool.analysis import news_feed as nf
    from dynasty_tool.analysis import news_render as nr
    from dynasty_tool.ingest.news_model import (NewsItem, handle_teams, load_sources,
                                                mute_terms, team_names)

    st.markdown("## Beat feed")
    st.caption("NFL beat writers and news wires — the Twitter replacement.")

    now_ms = int(_time.time() * 1000)
    specs = load_sources()
    sig = str(hash(tuple(sorted((s.id, s.ref) for s in specs))))
    tw_key = secret("TWITTERAPI_IO_KEY")
    anthropic_key = secret("ANTHROPIC_API_KEY")

    with st.spinner("Fetching the wire…"):
        raw_items, raw_health = load_news(sig, tw_key)
    items = [NewsItem.from_dict(d) for d in raw_items]
    # X sources are batched by division to keep the pull count at 8 instead of
    # 32, so their items arrive without a team; recover it from the handle.
    items = nf.attribute_teams(items, handle_teams())

    # -- tag against your players, then collapse duplicates ------------------
    mine = my_rostered_ids(st.session_state.username, st.session_state.season)
    index = news_index(int(A.qb_format), tuple(mine))
    items, report = nf.tag_items(items, index)
    items = nf.dedupe(items, jaccard=dt.DEDUPE_JACCARD,
                      window_ms=dt.DEDUPE_WINDOW_HOURS * 3600 * 1000)
    meta = index.get("meta") or {}
    mute = mute_terms()
    since = now_ms - dt.NEWS_WINDOW_HOURS * 3600 * 1000

    ok = [h for h in raw_health if h.get("ok")]
    bad = [h for h in raw_health if not h.get("ok")]
    c1, c2, c3, c4 = st.columns(4)
    tile(c1, "items", f"{len(items):,}", f"last {dt.NEWS_WINDOW_HOURS}h window")
    tile(c2, "sources", f"{len(ok)}/{len(raw_health)}",
         "all healthy" if not bad else f"{len(bad)} failing")
    tile(c3, "your players", f"{len(mine)}", "across every dynasty league")
    newest = max((i.published_ms for i in items), default=0)
    tile(c4, "newest", nr.rel_time(now_ms, newest) or "—", "ago")

    if bad:
        st.warning(f"{len(bad)} of {len(raw_health)} sources failed — the feed is "
                   "incomplete. Fix them by editing `dynasty_tool/ingest/feeds.json`.")
    with st.expander(f"Source health — {len(ok)}/{len(raw_health)} OK"):
        st.dataframe(pd.DataFrame([{
            "ok": "✅" if h.get("ok") else "❌", "source": h.get("label"),
            "kind": h.get("kind"), "team": h.get("team") or "—",
            "items": h.get("n_items"), "cached": h.get("from_cache"),
            "error": h.get("error") or "",
        } for h in raw_health]), hide_index=True, width="stretch")
    st.markdown(f"<div class='hq-note'>Player tagging — {report.line()}. Matching "
                "needs first <i>and</i> last name; ambiguous mentions are skipped "
                "rather than guessed.</div>", unsafe_allow_html=True)

    if not anthropic_key:
        st.caption("✨ AI summaries are off — add `ANTHROPIC_API_KEY` to Streamlit "
                   "secrets to enable the Summarize and Digest buttons.")
    if not tw_key:
        st.caption("🐦 X/Twitter sources are off — add `TWITTERAPI_IO_KEY` to "
                   "Streamlit secrets to pull actual beat-reporter tweets.")

    t_mine, t_all, t_team = st.tabs(
        ["⭐ My Players", "🌐 Around the League", "🏟️ By Team"])

    def _summarize_button(it, key_prefix: str):
        """Per-item ✨ Summarize. The result is written to a NON-widget session
        key — writing a widget's own key after the widget exists is forbidden by
        Streamlit (see the Trades page comment at the top of that branch)."""
        if not anthropic_key:
            return ""
        skey = f"_sum_{it.id}"
        if st.session_state.get(skey):
            return st.session_state[skey]
        if st.button("✨ Summarize", key=f"{key_prefix}_{it.id}", type="tertiary"):
            from dynasty_tool.ingest import summarize as sm
            cache = DiskCache(dt.CACHE_DIR)
            with st.spinner("Reading the article…"):
                body = sm.fetch_article(it.url, cache) or it.text
                try:
                    st.session_state[skey] = sm.summarize_item(
                        anthropic_key, it.title or it.text[:80], body, cache)
                except Exception as e:
                    st.error(f"Summary failed: {e}")
            st.rerun()
        return ""

    def _render(rows, chips=True, key_prefix="f"):
        """One markdown call for the whole list — per-item calls would add
        Streamlit's inter-container padding and read as a stack of widgets."""
        st.markdown(nr.feed_html(rows, now_ms, meta, show_chips=chips),
                    unsafe_allow_html=True)
        if anthropic_key:
            with st.expander("✨ Summarize an item"):
                for it in rows[:25]:
                    st.caption((it.title or it.text)[:90])
                    _summarize_button(it, key_prefix)

    # -- tab 1: My Players --------------------------------------------------
    with t_mine:
        mineset = {str(p) for p in mine}
        mine_items = nf.filter_items(items, player_ids=mineset, since_ms=since,
                                     mute=mute)
        if anthropic_key and mine_items:
            if st.button("✨ Digest my players", type="primary"):
                from dynasty_tool.ingest import summarize as sm
                blocks = [(meta.get(pid, {}).get("full_name", pid),
                           [f"{i.title} {i.text}" for i in its])
                          for pid, its in nf.group_by_player(
                              mine_items, sorted(mineset))]
                with st.spinner("Reading everything about your roster…"):
                    try:
                        st.session_state["_news_digest"] = sm.digest_players(
                            anthropic_key, blocks, DiskCache(dt.CACHE_DIR))
                    except Exception as e:
                        st.error(f"Digest failed: {e}")
        if st.session_state.get("_news_digest"):
            st.markdown("<div class='nf-sum'><b>✨ today's digest</b><br>"
                        + st.session_state["_news_digest"].replace("\n", "<br>")
                        + "</div>", unsafe_allow_html=True)

        if not mine_items:
            st.info(f"No news about your players in the last "
                    f"{dt.NEWS_WINDOW_HOURS}h.")
        else:
            # Most-covered players first — the guys the wire is actually
            # talking about today rise to the top of your section.
            order = sorted(
                {p for i in mine_items for p in i.player_ids},
                key=lambda p: (-len([1 for i in mine_items if p in i.player_ids]),
                               meta.get(p, {}).get("full_name", "")))
            for pid, its in nf.group_by_player(mine_items, order):
                m = meta.get(pid, {})
                logo = wh.team_logo_url(m.get("team"))
                st.markdown(
                    f"<div class='nf-pgroup'>"
                    f"<img src='{wh.headshot_url(pid)}' alt=''>"
                    f"<span><span class='n'>{nr.esc(m.get('full_name') or pid)}</span> "
                    f"{pos_badge(str(m.get('position') or ''))}"
                    f"<span class='x'>{nr.esc(m.get('team') or '')} · "
                    f"{len(its)} update{'s' if len(its) != 1 else ''}</span></span>"
                    + (f"<img src='{logo}' alt='' style='margin-left:auto'>" if logo else "")
                    + "</div>", unsafe_allow_html=True)
                _render(its, chips=False, key_prefix=f"m{pid}")

    # -- tab 2: Around the League -------------------------------------------
    with t_all:
        names = team_names()
        f1, f2, f3 = st.columns([1, 1, 2])
        pick_team = f1.multiselect("Team", sorted(names),
                                   format_func=lambda t: f"{t} — {names[t]}")
        pick_kind = f2.multiselect("Type", ["post", "article"])
        q = f3.text_input("Search", placeholder="player, coach, phrase…")
        rows = nf.filter_items(items, query=q, teams=pick_team, kinds=pick_kind,
                               since_ms=since, mute=mute)
        st.caption(f"{len(rows):,} items")
        n = st.session_state.setdefault("_news_n", 25)
        _render(rows[:n], key_prefix="a")
        if len(rows) > n:
            if st.button(f"Load 25 more ({len(rows) - n:,} left)"):
                st.session_state["_news_n"] = n + 25
                st.rerun()

    # -- tab 3: By Team -----------------------------------------------------
    with t_team:
        names = team_names()
        opts = sorted(names)
        sel = st.selectbox("Team", opts, format_func=lambda t: f"{t} — {names[t]}",
                           key="_news_team")
        rows = nf.filter_items(items, teams=[sel], since_ms=since, mute=mute)
        beat = sorted({s.label for s in specs if s.team == sel})
        st.caption(f"{len(rows):,} items · beat: {', '.join(beat) or '—'}")
        _render(rows[:50], key_prefix="t")


# ===========================================================================
elif page == "Movers":
    from dynasty_tool.analysis import movers as mvs

    st.markdown("## Movers — who the market is buying and selling")
    qb_fmt = int(A.qb_format)
    vcol = "value_2qb" if qb_fmt == 2 else "value_1qb"
    ecol = "ecr_2qb" if qb_fmt == 2 else "ecr_1qb"

    # fp_id -> sleeper_id, so your own players can be starred. Best-effort: a
    # missing id column costs the stars, not the page.
    @st.cache_data(ttl=6 * 3600, show_spinner=False)
    def _fp_to_sleeper() -> dict:
        try:
            _p, _k, ids = dp_frames()
            out = {}
            for _, r in ids.iterrows():
                fp, sl = r.get("fantasypros_id"), r.get("sleeper_id")
                if fp == fp and sl == sl and fp is not None and sl is not None:
                    out[str(int(float(fp)))] = str(sl).split(".")[0]
            return out
        except Exception:
            return {}

    mine = set(my_rostered_ids(st.session_state.username, st.session_state.season))
    fp2s = _fp_to_sleeper()

    def mover_table(rows, label: str):
        if not rows:
            st.caption("Nothing here.")
            return
        st.dataframe(pd.DataFrame([{
            "": "★" if fp2s.get(m.key) in mine else "",
            "player": m.name, "pos": m.pos, "team": m.team,
            "was": round(m.old), "now": round(m.new),
            "Δ": round(m.delta), "Δ%": round(m.pct, 1),
        } for m in rows]), hide_index=True, width="stretch")

    t_dp, t_ktc, t_class, t_devy, t_model = st.tabs(
        ["📊 Dynasty values (experts)", "🔥 KTC (the crowd)", "🎓 Draft classes",
         "🔭 Devy board", "🧪 Rookie model"])

    # -- expert consensus, week over week -----------------------------------
    with t_dp:
        st.caption("DynastyProcess / FantasyPros expert consensus — the same "
                   "scale the Trade Calculator prices trades on, so a riser "
                   "here is directly actionable. Updates weekly.")
        c1, c2 = st.columns([1, 1])
        days = c1.radio("Window", [7, 30, 90], index=1, horizontal=True,
                        format_func=lambda d: f"{d}d")
        by_pct = c2.toggle("Rank by %", value=False,
                           help="Surfaces mid-tier movement that raw points buries.")
        try:
            now_rows, then_rows, asof = dp_movers(int(days))
        except Exception as e:
            st.error(f"Couldn't load value history: {e}")
            now_rows, then_rows, asof = [], [], ""
        if then_rows:
            movers = mvs.diff_values(now_rows, then_rows, value_col=vcol)
            risers, fallers = mvs.top_movers(movers, n=15, by_pct=by_pct)
            st.caption(f"comparing today against {asof} · {len(movers)} players "
                       f"matched · {qb_fmt}QB · ★ = on one of your rosters")
            a, b = st.columns(2)
            with a:
                st.markdown("<div class='hq-h'>▲ Risers</div>", unsafe_allow_html=True)
                mover_table(risers, "risers")
            with b:
                st.markdown("<div class='hq-h'>▼ Fallers</div>", unsafe_allow_html=True)
                mover_table(fallers, "fallers")
        elif now_rows:
            st.info("No history available for that window yet.")

    # -- the crowd ----------------------------------------------------------
    with t_ktc:
        st.caption("KeepTradeCut — crowd-sourced trade votes, moving daily. This "
                   "is the bullish/bearish signal: when the crowd moves before "
                   "the experts do, that's the buy or sell window.")
        st.markdown("<div class='hq-note'>KTC has no public API, so this reads "
                    "the board embedded in their rankings page. It is unofficial "
                    "and can break without notice — if it does, the expert tab "
                    "still works.</div>", unsafe_allow_html=True)
        if st.button("Load KTC board", type="primary"):
            st.session_state["_ktc_on"] = True
        if st.session_state.get("_ktc_on"):
            try:
                board = ktc_board()
                km = mvs.ktc_movers(board, superflex=(qb_fmt == 2))
                risers, fallers = mvs.top_movers(km, n=15)
                st.caption(f"{len(board):,} players on the board · "
                           f"{'Superflex' if qb_fmt == 2 else '1QB'} · "
                           "movement is KTC's own 30-day trend")
                a, b = st.columns(2)
                with a:
                    st.markdown("<div class='hq-h'>▲ Crowd is buying</div>",
                                unsafe_allow_html=True)
                    mover_table(risers, "ktc risers")
                with b:
                    st.markdown("<div class='hq-h'>▼ Crowd is selling</div>",
                                unsafe_allow_html=True)
                    mover_table(fallers, "ktc fallers")
            except Exception as e:
                st.error(f"KTC unavailable: {e}")
                st.caption("Nothing else is affected — the expert tab is independent.")

    # -- draft classes ------------------------------------------------------
    with t_class:
        st.caption("How strong is a class, and what should a future pick cost? "
                   "Past classes are priced by what they actually produced; "
                   "future classes by what the market is charging for their picks.")
        try:
            now_rows, _t, _a = dp_movers(30)
        except Exception:
            now_rows = []
        if now_rows:
            strength = mvs.class_strength(now_rows, value_col=vcol)
            base = mvs.class_baseline(strength)
            if base:
                st.markdown("<div class='hq-h'>What a normal class produces</div>",
                            unsafe_allow_html=True)
                cols = st.columns(len(base))
                for col, (tier, avg) in zip(cols, base.items()):
                    tile(col, tier, f"{avg:g}", "per class (avg)")
                st.markdown("<div class='hq-note'>Averaged over settled classes — "
                            "the two most recent are excluded, since they haven't "
                            "sorted themselves out yet and would drag the elite "
                            "counts down.</div>", unsafe_allow_html=True)

            st.markdown("<div class='hq-h'>Every class, by what it actually produced</div>",
                        unsafe_allow_html=True)
            st.dataframe(pd.DataFrame([{
                "class": y, "players valued": s.get("n", 0),
                "super elite": s.get("super elite", 0), "elite": s.get("elite", 0),
                "very good": s.get("very good", 0), "good": s.get("good", 0),
                "headliners": ", ".join(s.get("top", [])[:3]),
            } for y, s in sorted(strength.items(), reverse=True)
                if s.get("n", 0) >= 5]), hide_index=True, width="stretch")

        # -- who is actually in each upcoming class -------------------------
        st.markdown("<div class='hq-h'>Who's in each upcoming class</div>",
                    unsafe_allow_html=True)
        ck = secret("CFBD_API_KEY")
        if not ck:
            st.info("Add `CFBD_API_KEY` to Streamlit secrets to see the named "
                    "players in each incoming class and how they're trending.")
        else:
            cy = st.number_input("College season", 2015, 2030,
                                 value=int(st.session_state.season) - 1,
                                 key="_cls_year")
            if st.button("Load class rosters", type="primary", key="_cls_go"):
                st.session_state["_cls_on"] = int(cy)
            if st.session_state.get("_cls_on"):
                y = int(st.session_state["_cls_on"])
                try:
                    with st.spinner(f"Pulling {y} and {y-1} college usage…"):
                        from dynasty_tool.analysis.devy import DEVY_POSITIONS
                        pros, crep = devy_board(ck, y, DEVY_POSITIONS)
                except Exception as e:
                    st.error(f"CFBD unavailable: {e}")
                    pros, crep = [], {}
                if pros:
                    from dynasty_tool.analysis import devy as _dvy
                    grouped = _dvy.by_draft_class(pros)
                    st.caption(
                        f"{crep.get('players', 0)} qualifying players · "
                        f"{crep.get('with_trend', 0)} have a prior season to "
                        f"trend against · ▲ = gaining offensive share year over year")
                    for dc in sorted(grouped):
                        if dc < y:
                            continue          # already drafted
                        group = grouped[dc]
                        with st.expander(f"{dc} NFL draft class — {len(group)} prospects",
                                         expanded=(dc == min(
                                             k for k in grouped if k >= y))):
                            st.dataframe(pd.DataFrame([{
                                "trend": p.trend, "score": p.score, "player": p.name,
                                "pos": p.position, "yr": p.class_year,
                                "school": p.team, "conf": p.conference,
                                "usage": f"{p.usage:.0%}",
                                "prior": (f"{p.prev_usage:.0%}"
                                          if p.prev_usage is not None else "—"),
                                "Δ": (f"{p.usage_delta:+.0%}"
                                      if p.usage_delta is not None else "—"),
                                "stars": p.stars or "",
                            } for p in group[:40]]), hide_index=True, width="stretch")
                    st.markdown(
                        "<div class='hq-note'>Read this next to the pick pricing "
                        "below: a class stacked with risers that the market "
                        "hasn't marked up yet is when to <b>buy</b> its picks."
                        "</div>", unsafe_allow_html=True)

        st.markdown("<div class='hq-h'>What the market thinks of future classes</div>",
                    unsafe_allow_html=True)
        try:
            market = mvs.pick_market(dp_pick_rows(), ecr_col=ecol)
            prem = mvs.class_premium(market)
            if prem:
                cols = st.columns(len(prem))
                for col, (yr, mult) in zip(cols, sorted(prem.items())):
                    tile(col, f"{yr} picks", f"{mult:.2f}×",
                         "vs the cheapest class")
                st.markdown("<div class='hq-note'>Higher multiple = the market is "
                            "paying up for that class. A class priced well above "
                            "the others is one to <b>sell</b> picks into; a cheap "
                            "class is when to <b>buy</b> them.</div>",
                            unsafe_allow_html=True)
                st.dataframe(pd.DataFrame([
                    {"class": y, **{k: round(v, 1) for k, v in sorted(b.items())}}
                    for y, b in sorted(market.items())]),
                    hide_index=True, width="stretch")
            else:
                st.caption("No future-class pick pricing available.")
        except Exception as e:
            st.error(f"Pick market unavailable: {e}")

    # -- devy board ---------------------------------------------------------
    with t_devy:
        from dynasty_tool.analysis import devy as dvy

        st.caption("College players worth holding before they get drafted — "
                   "ranked on early production, because a true sophomore taking "
                   "30% of his offence is a far better bet than a senior doing "
                   "the same.")
        cfbd_key = secret("CFBD_API_KEY")
        if not cfbd_key:
            st.info("Add `CFBD_API_KEY` to Streamlit secrets to enable this "
                    "(free at collegefootballdata.com/profile).")
        else:
            d1, d2 = st.columns([1, 2])
            yr = d1.number_input("Season", 2015, 2030,
                                 value=int(st.session_state.season) - 1)
            pos = d2.multiselect("Positions", list(dvy.DEVY_POSITIONS),
                                 default=list(dvy.DEVY_POSITIONS))
            b1, b2 = st.columns([1, 1])
            if b1.button("Build devy board", type="primary"):
                # Both inputs are captured on click. Reading `pos` live meant
                # toggling a position silently rebuilt the board without the
                # button, defeating the gate.
                st.session_state["_devy_on"] = (int(yr), tuple(pos))
            if st.session_state.get("_devy_on") and b2.button("Clear"):
                st.session_state.pop("_devy_on", None)
                st.rerun()
            if st.session_state.get("_devy_on"):
                y, built_pos = st.session_state["_devy_on"]
                try:
                    with st.spinner(f"Pulling {y} college usage and recruiting…"):
                        prospects, rep = devy_board(cfbd_key, y, tuple(built_pos))
                except Exception as e:
                    st.error(f"CFBD unavailable: {e}")
                    prospects, rep = [], {}
                if prospects:
                    st.markdown(
                        f"<div class='hq-note'>{rep['players']} qualifying players · "
                        f"recruiting pedigree matched for {rep['pedigree_matched']} "
                        f"({rep['pedigree_rate']:.0%}) by name+school — unmatched "
                        f"players are still ranked, just on production alone."
                        "</div>", unsafe_allow_html=True)

                    proj = dvy.class_projection(prospects)
                    if proj:
                        st.markdown("<div class='hq-h'>How each incoming class "
                                    "is shaping up</div>", unsafe_allow_html=True)
                        st.dataframe(pd.DataFrame([{
                            "NFL draft class": dc, "prospects": b["n"],
                            "super elite": b["super elite"], "elite": b["elite"],
                            "very good": b["very good"], "good": b["good"],
                            "headliners": ", ".join(b["top"][:3]),
                        } for dc, b in sorted(proj.items())]),
                            hide_index=True, width="stretch")
                        st.markdown(
                            "<div class='hq-note'>Bands describe how much early, "
                            "high-usage talent a class is carrying <i>today</i> — "
                            "a leading indicator for what its rookie picks will be "
                            "worth, not a prediction of NFL outcomes. Pair it with "
                            "the pick pricing on the Draft classes tab: a strong "
                            "class the market hasn't priced yet is when to buy."
                            "</div>", unsafe_allow_html=True)

                    st.markdown("<div class='hq-h'>Top prospects</div>",
                                unsafe_allow_html=True)
                    st.dataframe(pd.DataFrame([{
                        "score": p.score, "player": p.name, "pos": p.position,
                        "yr": p.class_year, "team": p.team, "conf": p.conference,
                        "usage": f"{p.usage:.0%}", "stars": p.stars or "",
                        "draft": p.draft_class or "",
                    } for p in prospects[:60]]), hide_index=True, width="stretch")
                    st.caption("Production screen, not a scouting report — there "
                               "is no free consensus devy board, so this knows "
                               "nothing about traits, injuries or scheme.")
                elif rep:
                    st.warning("No qualifying players found for that season.")

    # -- the calibrated model ----------------------------------------------
    with t_model:
        st.caption("A hit-rate model trained on what actually happened to every "
                   "skill player drafted since 2010 — not hand-picked weights. "
                   "\"Hit\" = a career yards-per-game pace that would have made "
                   "him startable.")
        m1, m2 = st.columns([1, 1])
        hold = m1.slider("Hold out the last N draft classes", 2, 6, 4,
                         help="Scored on classes it never saw — a random split "
                              "would leak the future and flatter the model.")
        maxs = m2.slider("Train through season", 2016, 2023, 2022,
                         help="Recent classes haven't settled; including them "
                              "would count good players as misses.")
        if st.button("Train the model", type="primary"):
            st.session_state["_rm_on"] = (int(hold), int(maxs))
        if st.session_state.get("_rm_on"):
            h, ms = st.session_state["_rm_on"]
            try:
                with st.spinner("Pulling nflverse draft + combine and fitting…"):
                    fit_r, oos_r, fit_d, oos_d, rows = rookie_model(
                        h, ("WR", "RB", "TE"), ms, secret("CFBD_API_KEY"))
            except Exception as e:
                st.error(f"Couldn't train: {e}")
                fit_r = None
            if fit_r is not None:
                k1, k2, k3, k4 = st.columns(4)
                tile(k1, "training players", f"{fit_r.n:,}", f"drafted 2010–{ms}")
                tile(k2, "held-out AUC", f"{oos_r:.3f}",
                     "post-draft · unseen classes")
                tile(k3, "devy AUC", f"{oos_d:.3f}", "no draft capital")
                tile(k4, "base hit rate", f"{fit_r.base_rate:.0%}",
                     "of drafted skill players")

                if fit_r.dropped or fit_d.dropped:
                    missing = sorted(set(fit_r.dropped) | set(fit_d.dropped))
                    st.warning(
                        f"Dropped from the fit — no data at all: **{', '.join(missing)}**. "
                        "These are college features; add `CFBD_API_KEY` to Streamlit "
                        "secrets to populate them. They are excluded rather than "
                        "shown at weight 0.000, which would read as 'the model "
                        "considered it and decided it didn't matter'.")

                st.markdown("<div class='hq-h'>What the model learned</div>",
                            unsafe_allow_html=True)
                a, b = st.columns(2)
                for col, fitobj, name in ((a, fit_r, "Rookie (post-draft)"),
                                          (b, fit_d, "Devy (pre-draft)")):
                    with col:
                        st.markdown(f"<div class='hq-note'><b>{name}</b></div>",
                                    unsafe_allow_html=True)
                        imp = fitobj.importance()
                        top = imp[0][1] or 1.0
                        # feature names are our own constants, not feed input
                        st.markdown("".join(
                            f"<div class='nf-mv'><span>{f}</span>"
                            f"<span class='d'>{w:.3f}</span></div>"
                            f"<div class='hq-bar'><span style='width:"
                            f"{min(100, w / top * 100):.0f}%'></span></div>"
                            for f, w in imp), unsafe_allow_html=True)
                st.markdown(
                    "<div class='hq-note'>Draft capital dominating the rookie "
                    "model is the expected, correct result — it is the strongest "
                    "public predictor of NFL production, which is exactly why the "
                    "devy model excludes it and scores lower. That gap is the "
                    "honest cost of picking players before the NFL does."
                    "</div>", unsafe_allow_html=True)

                st.markdown("<div class='hq-h'>Score a class</div>",
                            unsafe_allow_html=True)
                seasons = sorted({int(r["season"]) for r in rows}, reverse=True)
                pick_season = st.selectbox("Draft class", seasons)
                from dynasty_tool.analysis import rookie_model as rmod
                scored = rmod.score_prospects(
                    fit_r, [r for r in rows if int(r["season"]) == pick_season])
                if scored:
                    st.dataframe(pd.DataFrame([{
                        "hit prob": f"{s['prob']:.0%}", "player": s["name"],
                        "pos": s["position"],
                        "pick": int(s["pick"]) if s["pick"] == s["pick"] else None,
                        **{k: v for k, v in sorted(
                            s["contributions"].items(),
                            key=lambda kv: -abs(kv[1]))[:4]},
                    } for s in scored[:40]]), hide_index=True, width="stretch")
                    st.caption("Columns after `pick` are that player's four "
                               "biggest score drivers — positive helped, "
                               "negative hurt. The model explains itself.")


# ===========================================================================
elif page == "Dashboard":
    st.markdown(f"## {lg['name']}")
    st.caption(f"{A.status.replace('_', ' ')} · {len(A.rosters)} teams · "
               f"{A.playoff_teams} playoff spots · {A.reg_weeks}-wk season · "
               f"viewing **{me.display}**")

    c1, c2, c3, c4, c5 = st.columns(5)
    tile(c1, "Power rank", f"{my_rank}<span style='font-size:14px;color:{INK['muted']}'>"
         f" / {len(uids)}</span>", me.window)
    tile(c2, "Starter value", wh.fmt_k(me.starter_value),
         f"age {me.avg_starter_age:.1f}" if me.avg_starter_age else "")
    tile(c3, "Proj wins", f"{my_sim.proj_wins:.1f}" if my_sim else "—",
         f"of {A.reg_weeks}")
    tile(c4, "Playoff odds", f"{my_sim.playoff_pct*100:.0f}%" if my_sim else "—",
         "roster-strength sim")
    tile(c5, "Title odds", f"{my_sim.title_pct*100:.0f}%" if my_sim else "—",
         f"{'real schedule' if A.sim_used_real_schedule else 'balanced schedule'}")

    st.write("")
    left, right = st.columns([1.05, 1])
    with left:
        st.markdown("<div class='hq-h'>Positional strength (percentile vs league)</div>",
                    unsafe_allow_html=True)
        st.plotly_chart(radar_fig(A, view_uid), width="stretch",
                        config={"displayModeBar": False})
        st.markdown(f"<div class='hq-note'>{A.advice.get(view_uid, '')}</div>",
                    unsafe_allow_html=True)
    with right:
        st.markdown("<div class='hq-h'>This week</div>", unsafe_allow_html=True)
        mu = next((m for m in (A.matchups or [])
                   if view_uid in (m.home_uid, m.away_uid)), None)
        if mu:
            mine_home = mu.home_uid == view_uid
            wp = mu.home_win_prob if mine_home else 1 - mu.home_win_prob
            opp = mu.away_disp if mine_home else mu.home_disp
            mep = mu.home_proj if mine_home else mu.away_proj
            opp_p = mu.away_proj if mine_home else mu.home_proj
            st.markdown(
                f"<div class='hq-card'><div class='hq-row'><b>{me.display}</b>"
                f"<span class='hq-vs'>vs</span><b>{opp}</b></div>"
                f"<div style='margin:10px 0 6px'><div class='hq-bar'>"
                f"<span style='width:{wp*100:.0f}%'></span></div></div>"
                f"<div class='hq-row' style='border:0'><span>win prob "
                f"<b>{wp*100:.0f}%</b></span><span>proj <b>{mep:.0f}–{opp_p:.0f}</b>"
                f"</span></div>"
                + (f"<div class='hq-note'>live: {mu.home_points:.1f} – "
                   f"{mu.away_points:.1f}</div>" if mu.live else "")
                + "</div>", unsafe_allow_html=True)
        else:
            st.markdown("<div class='hq-note'>No matchup yet — the schedule appears "
                        "once the league generates it (offseason).</div>",
                        unsafe_allow_html=True)
        st.markdown("<div class='hq-h'>Title odds — whole league</div>",
                    unsafe_allow_html=True)
        st.plotly_chart(power_fig(A, view_uid), width="stretch",
                        config={"displayModeBar": False})

    st.markdown("<div class='hq-h'>Contention map — value vs age</div>",
                unsafe_allow_html=True)
    st.plotly_chart(contention_fig(A, view_uid), width="stretch",
                    config={"displayModeBar": False})
    st.markdown("<div class='hq-note'>Odds are a roster-strength Monte-Carlo "
                f"({la.SIM_N:,} sims) — a projection of roster strength, not a "
                "guarantee. Windows read value and age as separate axes.</div>",
                unsafe_allow_html=True)

# ===========================================================================
elif page == "My Team":
    st.markdown(f"## {me.display} {window_badge(me)}", unsafe_allow_html=True)
    st.caption(f"rank {my_rank}/{len(uids)} · starter value {wh.fmt_k(me.starter_value)} "
               f"· depth {wh.fmt_k(me.depth_value)} · "
               f"{me.n_unmatched} unvalued (IDP/deep) players")

    st.markdown("<div class='hq-h'>Starters (value-optimal)</div>",
                unsafe_allow_html=True)
    show_roster_table(roster_df(me, me.starters))

    bench = [p for p in sorted(me.players, key=lambda x: x.value, reverse=True)
             if p.sleeper_id not in {q.sleeper_id for _, q in me.starters}]
    with st.expander(f"Bench ({len(bench)})"):
        show_roster_table(roster_df(me, [("BN", p) for p in bench]))

    # -- how they're actually being used ------------------------------------
    st.markdown("<div class='hq-h'>Usage — snaps, routes, targets per route</div>",
                unsafe_allow_html=True)
    st.caption("Targets per route run is the one to watch: it separates "
               "\"he's on the field\" from \"the offence looks for him\", and it "
               "stabilises far sooner than target share.")
    u_year = st.number_input("Season", 2019, 2030,
                             value=max(2019, int(st.session_state.season) - 1),
                             key="_usage_year")
    if st.button("Load usage data", key="_usage_go"):
        st.session_state["_usage_on"] = int(u_year)
    if st.session_state.get("_usage_on"):
        uy = int(st.session_state["_usage_on"])
        try:
            with st.spinner(f"Pulling {uy} participation and snap counts…"):
                urows, urep = usage_season(uy)
        except Exception as e:
            st.error(f"Usage data unavailable: {e}")
            urows, urep = [], {}
        if urows:
            by_sid = {str(r["sleeper_id"]): r for r in urows}
            mine_rows = []
            for p in sorted(me.players, key=lambda x: x.value, reverse=True):
                r = by_sid.get(str(p.sleeper_id))
                if not r:
                    continue
                mine_rows.append({
                    "": headshot_for(p.sleeper_id, bundle) or "",
                    "player": p.name, "pos": p.pos,
                    "snap %": round(r["snap_pct"] * 100),
                    "route %": round(r["route_pct"] * 100),
                    "routes": r["routes"], "tgts": r["targets"],
                    "tgt/route": round(r["tprr"], 3) or None,
                })
            if mine_rows:
                show_roster_table(pd.DataFrame(mine_rows))
                st.markdown(
                    "<div class='hq-note'>A route here means <i>on the field for "
                    "a dropback</i> — nflverse publishes participation, not "
                    "charted routes. Close for receivers; <b>generous for TEs and "
                    "backs</b>, who are credited a route on snaps they spent "
                    "blocking, so read their tgt/route as conservative."
                    "</div>", unsafe_allow_html=True)
            else:
                st.caption("None of your players have usage rows for that season.")

            st.markdown("<div class='hq-h'>League leaderboard</div>",
                        unsafe_allow_html=True)
            metric = st.radio("Rank by", ["route_pct", "tprr", "snap_pct", "routes"],
                              horizontal=True, key="_usage_metric",
                              format_func=lambda m: {"route_pct": "route %",
                                                     "tprr": "tgt/route",
                                                     "snap_pct": "snap %",
                                                     "routes": "routes"}[m])
            mine_ids = {str(p.sleeper_id) for p in me.players}
            free_ids = {str(a.sleeper_id) for a in (A.available or [])}
            board = [r for r in urows if r["routes"] >= 50]
            board.sort(key=lambda r: r[metric], reverse=True)
            st.dataframe(pd.DataFrame([{
                "": wh.headshot_url(str(r["sleeper_id"])),
                "who": ("★ yours" if str(r["sleeper_id"]) in mine_ids
                        else "✅ FREE" if str(r["sleeper_id"]) in free_ids else ""),
                "player": r["name"], "pos": r["position"],
                "snap %": round(r["snap_pct"] * 100),
                "route %": round(r["route_pct"] * 100),
                "routes": r["routes"], "tgt/route": round(r["tprr"], 3),
            } for r in board[:40]]), hide_index=True, width="stretch",
                column_config={"": st.column_config.ImageColumn("", width=42)})
            st.caption(
                f"{urep.get('plays', 0):,} plays · {urep.get('resolved', 0):,} of "
                f"{urep.get('total', 0):,} players resolved to a Sleeper id · "
                f"sanity: {urep.get('note', '')}"
                + ("" if urep.get("ok") else "  ⚠️ route counting looks off"))

    st.markdown("<div class='hq-h'>Player profile</div>", unsafe_allow_html=True)
    order = [p for _, p in me.starters] + bench
    sel = st.selectbox("Player", range(len(order)),
                       format_func=lambda i: f"{order[i].name} ({order[i].pos})")
    p = order[sel]
    meta = bundle["meta"].get(p.sleeper_id, {})
    players_df, _picks, ids_df = dp_frames()
    ecr = wh.ecr_map(players_df, ids_df, bundle["qb"]).get(
        p.sleeper_id if not bundle["g2s"] else str(bundle["g2s"].get(p.sleeper_id)), {})

    pc1, pc2 = st.columns([1, 2.2])
    with pc1:
        url = headshot_for(p.sleeper_id, bundle)
        if url:
            st.image(url, width=230)
    with pc2:
        team = meta.get("team") or ecr.get("team") or ""
        logo = wh.team_logo_url(team)
        st.markdown(
            f"### {p.name}"
            f"<div style='margin:6px 0 10px'>{pos_badge(p.pos)}"
            + (f"<img src='{logo}' width='26' style='vertical-align:middle;"
               f"margin-right:6px'>" if logo else "")
            + f"<span style='color:{INK['secondary']}'>{team}</span></div>",
            unsafe_allow_html=True)
        inj = (meta.get("injury_status") or "").strip()
        if inj:
            st.markdown(f"<span class='hq-badge' style='background:{STATUS['serious']}'>"
                        f"⚕ {inj}</span>", unsafe_allow_html=True)
        m1, m2, m3, m4 = st.columns(4)
        tile(m1, "Value" if A.basis != "redraft" else "Proj pts", wh.fmt_k(p.value), A.basis)
        tile(m2, "ECR", f"{ecr['ecr']:.0f}" if ecr.get("ecr") == ecr.get("ecr") and
             ecr.get("ecr") is not None else "—", f"{bundle['qb']}QB overall")
        tile(m3, "Pos ECR", f"{ecr['ecr_pos']:.0f}" if ecr.get("ecr_pos") == ecr.get("ecr_pos")
             and ecr.get("ecr_pos") is not None else "—", p.pos)
        tile(m4, "Age", f"{p.age:.1f}" if p.age else "—",
             f"{meta.get('years_exp', '—')} yrs exp")
        bits = [str(meta.get(k)) for k in ("college", "number") if meta.get(k)]
        if A.weekly and p.sleeper_id in A.weekly:
            bits.append(f"wk{A.weekly_week} proj {A.weekly[p.sleeper_id]:.1f} "
                        f"{A.weekly_opp.get(p.sleeper_id, '')}")
        if bits:
            st.caption(" · ".join(bits))
        pool_max = max((q.value for rv in A.rosters.values() for q in rv.players
                        if q.pos == p.pos), default=1) or 1
        st.markdown(f"<div class='hq-note'>vs best {p.pos} in league</div>"
                    f"<div class='hq-bar'><span style='width:{min(100, p.value/pool_max*100):.0f}%;"
                    f"background:{POS.get(p.pos, SERIES['blue'])}'></span></div>",
                    unsafe_allow_html=True)

# ===========================================================================
elif page == "Start/Sit":
    st.markdown(f"## Start/Sit — {me.display}")
    rec = la.lineup_recommendation(me, A.roster_positions, weekly=A.weekly,
                                   week=A.weekly_week, opp=A.weekly_opp or None)
    if rec.weekly:
        st.caption(f"Ranked by **weekly DFS projection** (week {rec.week}).")
    else:
        st.info("Offseason: ranked by dynasty/season value. Weekly matchup "
                "projections take over automatically once the season starts.",
                icon="🗓️")
    rows = [(slot, p) for slot, p in rec.lineup]
    show_roster_table(roster_df(me, rows))
    if rec.close_calls:
        st.markdown("<div class='hq-h'>Tough calls (within 15%)</div>",
                    unsafe_allow_html=True)
        for slot, starter, ch, gap in rec.close_calls:
            st.warning(f"**{slot}** — start **{starter.name}** over {ch.name} "
                       f"(gap {gap*100:.0f}%)", icon="⚖️")
    if rec.empty_slots:
        st.error("Unfillable slots: " + ", ".join(rec.empty_slots))

# ===========================================================================
elif page == "Matchups":
    st.markdown(f"## Week {A.matchup_week or '—'} matchups")
    if not A.matchups:
        st.info("No schedule yet — matchups appear once the league generates one.",
                icon="🗓️")
    else:
        if A.matchups_live:
            st.caption("LIVE — odds blend live scores with rest-of-roster projection.")
        for m in A.matchups:
            hp = m.home_win_prob * 100
            live = (f"<div class='hq-note'>live {m.home_points:.1f} – "
                    f"{m.away_points:.1f}</div>") if m.live else ""
            st.markdown(
                f"<div class='hq-card'><div class='hq-row' style='border:0'>"
                f"<b>{m.home_disp}</b><span class='hq-vs'>vs</span><b>{m.away_disp}</b></div>"
                f"<div style='margin:8px 0'><div class='hq-bar'>"
                f"<span style='width:{hp:.0f}%'></span></div></div>"
                f"<div class='hq-row' style='border:0'>"
                f"<span>{m.home_disp} <b>{hp:.0f}%</b></span>"
                f"<span>proj {m.home_proj:.0f}–{m.away_proj:.0f}</span>"
                f"<span>{m.away_disp} <b>{100-hp:.0f}%</b></span></div>{live}</div>",
                unsafe_allow_html=True)

# ===========================================================================
elif page == "Playoff Race":
    st.markdown("## Playoff race")
    if A.completed_weeks:
        st.caption(f"Through week {A.completed_weeks}: current records seeded, real "
                   f"remaining schedule simulated {la.SIM_N:,}×. "
                   f"{A.playoff_teams} playoff spots.")
    else:
        st.caption(f"Preseason projection — no games played yet. "
                   f"{A.playoff_teams} playoff spots.")
    st.plotly_chart(race_fig(A, view_uid), width="stretch",
                    config={"displayModeBar": False})
    race = sorted(A.race or [], key=lambda r: (r.playoff_pct, r.title_pct), reverse=True)
    df = pd.DataFrame([{
        "team": r.display,
        "record": f"{r.current_wins:g}-{r.current_losses}",
        "proj W": round(r.proj_wins, 1),
        "playoff %": round(r.playoff_pct * 100),
        "title %": round(r.title_pct * 100),
        "status": r.status,
    } for r in race])
    st.dataframe(df, hide_index=True, width="stretch")

# ===========================================================================
elif page == "History & SOS":
    st.markdown("## History & strength of schedule")
    if A.standings:
        st.markdown("<div class='hq-h'>Current standings</div>", unsafe_allow_html=True)
        st.dataframe(pd.DataFrame([{
            "team": s.display, "W": s.wins, "L": s.losses,
            "PF": round(s.fpts), "PA": round(s.fpts_against),
            "potential": round(s.ppts) or None} for s in A.standings]),
            hide_index=True, width="stretch")
    if A.sos:
        st.markdown("<div class='hq-h'>Strength of schedule</div>", unsafe_allow_html=True)
        rows = sorted(A.sos.items(), key=lambda kv: kv[1]["avg_opp_strength"], reverse=True)
        st.dataframe(pd.DataFrame([{
            "team": A.rosters[u].display if u in A.rosters else u,
            "avg opp strength": round(d["avg_opp_strength"]),
            "vs league avg": round(d["vs_league_avg"]),
        } for u, d in rows]), hide_index=True, width="stretch")
    elif not A.standings:
        st.info("No schedule or results yet this season.", icon="🗓️")
    if A.last_season:
        ls = A.last_season
        st.markdown(f"<div class='hq-h'>Last season — {ls['season']}</div>",
                    unsafe_allow_html=True)
        st.dataframe(pd.DataFrame([{
            "#": i, "team": s.display, "record": f"{s.wins}-{s.losses}",
            "PF": round(s.fpts), "PA": round(s.fpts_against),
            "potential": round(s.ppts)} for i, s in enumerate(ls["standings"], 1)]),
            hide_index=True, width="stretch")

# ===========================================================================
elif page == "Trades":
    st.markdown(f"## Trade center — {me.display}")
    lid = str(lg["league_id"])
    k_a, k_b = f"tc_a_{lid}", f"tc_b_{lid}"                 # the two asset pickers
    k_ta, k_tb = f"tc_team_a_{lid}", f"tc_team_b_{lid}"     # the two team pickers
    k_qb = f"tc_qb_{lid}"

    # A package loaded from the Suggested tab lands here, and must be applied
    # BEFORE the widgets exist — Streamlit forbids writing a widget's key once
    # that widget has been instantiated in the same run.
    pending = st.session_state.pop("_tc_load", None)
    if pending:
        st.session_state[k_ta], st.session_state[k_tb] = pending["team_a"], pending["team_b"]
        st.session_state[k_a], st.session_state[k_b] = pending["send_a"], pending["send_b"]

    unit = "proj pts" if A.redraft else "value"
    qb_fmt = (A.qb_format if A.redraft else
              (2 if st.session_state.get(k_qb, "Superflex" if A.qb_format == 2
                                         else "1QB") == "Superflex" else 1))
    uni = tc.extend_with_rosters(trade_universe(bool(A.redraft), int(qb_fmt)), A.rosters)
    owners = tc.owner_map(A.rosters)
    by_value = sorted(uni, key=lambda k: -uni[k].value)

    t_calc, t_sugg, t_block = st.tabs(["🧮 Calculator", "🤝 Suggested packages",
                                       "📋 League trade block"])

    # -- tab 1: the calculator ----------------------------------------------
    with t_calc:
        s1, s2, s3 = st.columns([1.2, 1.2, 1])
        uid_a = s1.selectbox("Team A", uids, index=uids.index(view_uid), key=k_ta,
                             format_func=lambda u: A.rosters[u].display)
        b_default = next((i for i, u in enumerate(uids) if u != view_uid), 0)
        uid_b = s2.selectbox("Team B", uids, index=b_default, key=k_tb,
                             format_func=lambda u: A.rosters[u].display)
        if A.redraft:
            s3.markdown("<div class='hq-note'>Redraft — assets valued at this season's "
                        "projected points from your own engine.</div>",
                        unsafe_allow_html=True)
        else:
            s3.radio("Value format", ["1QB", "Superflex"], horizontal=True, key=k_qb,
                     index=1 if A.qb_format == 2 else 0)

        def opts_for(uid: str) -> list[str]:
            """That team's assets first (their roster is one click away), then everyone."""
            mine = [k for k in tc.roster_asset_keys(A.rosters[uid]) if k in uni]
            seen = set(mine)
            return mine + [k for k in by_value if k not in seen]

        def fmt_asset(k: str) -> str:
            a = uni[k]
            loc = f" {a.team}" if a.team else ""
            who = owners.get(k)
            return (f"{a.label} · {a.pos}{loc} · {a.value:,.0f}"
                    + (f" · {who}" if who else ""))

        def asset_rows(assets, total: float, side_name: str) -> str:
            rows = "".join(
                f"<div class='hq-asset'>{pos_badge(a.pos)}<span>{a.label}</span>"
                f"<span class='who'>{owners.get(a.key, 'free agent' if a.kind == 'player' else '')}"
                f"</span><span class='val'>{a.value:,.0f}</span></div>" for a in assets)
            if not rows:
                rows = ("<div class='hq-asset' style='color:var(--muted)'>nothing yet — "
                        "search above</div>")
            return (f"<div class='hq-card'>{rows}<div class='hq-row' style='border:0;"
                    f"padding-top:10px'><b>{side_name} sends</b>"
                    f"<b>{total:,.0f} {unit}</b></div></div>")

        ca, cb = st.columns(2)
        disp_a, disp_b = A.rosters[uid_a].display, A.rosters[uid_b].display
        with ca:
            st.markdown(f"<div class='hq-h'>{disp_a} sends</div>", unsafe_allow_html=True)
            sel_a = st.multiselect("side a", opts_for(uid_a), format_func=fmt_asset,
                                   key=k_a, label_visibility="collapsed",
                                   placeholder="Add a player or pick…")
        with cb:
            st.markdown(f"<div class='hq-h'>{disp_b} sends</div>", unsafe_allow_html=True)
            sel_b = st.multiselect("side b", opts_for(uid_b), format_func=fmt_asset,
                                   key=k_b, label_visibility="collapsed",
                                   placeholder="Add a player or pick…")

        assets_a = [uni[k] for k in sel_a]
        assets_b = [uni[k] for k in sel_b]
        ev = tc.evaluate(assets_a, assets_b, basis=A.basis, qb_format=qb_fmt,
                         num_teams=len(uids), name_a=disp_a, name_b=disp_b)
        ca.markdown(asset_rows(assets_a, ev.side_a.total, disp_a), unsafe_allow_html=True)
        cb.markdown(asset_rows(assets_b, ev.side_b.total, disp_b), unsafe_allow_html=True)

        if uid_a == uid_b:
            st.warning("Team A and Team B are the same team — pick a trade partner.",
                       icon="🔁")
        elif not assets_a and not assets_b:
            st.info("Add assets to both sides — search by name, or scroll: each side's "
                    "own roster is listed first. Picks are in the same box "
                    "(type “2027” or “1.05”). The Suggested tab can fill this in "
                    "for you.", icon="🧮")
        else:
            # who nets value: the side RECEIVING the larger package
            win = tc.winner(ev)
            tot = ev.side_a.total + ev.side_b.total
            pa = (ev.side_a.total / tot * 100) if tot > 0 else 50.0
            st.markdown(
                f"<div class='hq-split'>"
                f"<div style='width:{pa:.1f}%;background:{SERIES['blue']}'>"
                f"{disp_a} {ev.side_a.total:,.0f}</div>"
                f"<div style='width:{100-pa:.1f}%;background:{SERIES['aqua']};"
                f"justify-content:flex-end'>{ev.side_b.total:,.0f} {disp_b}</div></div>",
                unsafe_allow_html=True)
            vc = {"EVEN": STATUS["good"], "SLIGHT EDGE": STATUS["warning"],
                  "LOPSIDED": STATUS["critical"]}[ev.verdict]
            tail = (f"within {dt.FAIRNESS_EVEN*100:.0f}% — fair deal" if ev.verdict == "EVEN"
                    else f"<b>{win}</b> nets {abs(ev.delta):,.0f} {unit} "
                         f"({ev.pct_gap*100:.0f}% of the larger side)")
            st.markdown(f"<div style='margin:10px 0 4px'>"
                        f"<span class='hq-badge' style='background:{vc}'>{ev.verdict}</span>"
                        f"<span style='color:{INK['secondary']};font-size:14px'>{tail}</span>"
                        f"</div>", unsafe_allow_html=True)

            # -- roster impact: re-fill both starting lineups after the swap --
            st.markdown("<div class='hq-h' style='margin-top:18px'>Roster impact — does "
                        "it change your starting lineup?</div>", unsafe_allow_html=True)
            ia = tc.roster_impact(A.rosters[uid_a], A.roster_positions, assets_b, assets_a)
            ib = tc.roster_impact(A.rosters[uid_b], A.roster_positions, assets_a, assets_b)
            ica, icb = st.columns(2)   # a fresh row, so the tiles sit under their heading
            for col, disp, imp in ((ica, disp_a, ia), (icb, disp_b, ib)):
                with col:
                    t1, t2 = st.columns(2)
                    tile(t1, f"{disp} · starters", wh.fmt_k(imp.starter_after),
                         f"{imp.starter_delta:+,.0f} from {wh.fmt_k(imp.starter_before)}")
                    tile(t2, f"{disp} · all assets", wh.fmt_k(imp.total_after),
                         f"{imp.total_delta:+,.0f} from {wh.fmt_k(imp.total_before)}")
                    moves = "".join(
                        f"<div class='hq-row'><span>{'🟢 into lineup' if into else '🔴 out of lineup'}"
                        f"</span><span>{pos_badge(p.pos)}<b>{p.name}</b></span></div>"
                        for into, p in ([(True, p) for p in imp.lineup_in]
                                        + [(False, p) for p in imp.lineup_out]))
                    if imp.picks_in or imp.picks_out:
                        moves += (f"<div class='hq-row'><span>picks</span><span>"
                                  f"{imp.picks_in - imp.picks_out:+,.0f} {unit} "
                                  f"(no lineup slot)</span></div>")
                    st.markdown(f"<div class='hq-card'>{moves or ''}"
                                + ("" if moves else "<span style='color:var(--muted);"
                                   "font-size:13px'>starting lineup unchanged</span>")
                                + "</div>", unsafe_allow_html=True)

            rid_a = {str(p.sleeper_id) for p in A.rosters[uid_a].players}
            rid_b = {str(p.sleeper_id) for p in A.rosters[uid_b].players}
            stray = ([a.label for a in assets_a
                      if a.kind == "player" and a.player_id not in rid_a]
                     + [a.label for a in assets_b
                        if a.kind == "player" and a.player_id not in rid_b])
            if stray:
                st.markdown("<div class='hq-note'>Hypothetical: " + ", ".join(stray)
                            + " isn't on the sending team's roster, so roster impact "
                            "counts them as an add only.</div>", unsafe_allow_html=True)

            # -- what closes the gap -----------------------------------------
            if ev.verdict != "EVEN":
                light_uid = uid_a if ev.delta < 0 else uid_b
                pool = [uni[k] for k in tc.roster_asset_keys(A.rosters[light_uid])
                        if k in uni]
                if not A.redraft:
                    pool += [a for a in uni.values() if a.kind == "pick"]
                sugg = tc.suggest_sweeteners(pool, abs(ev.delta),
                                             exclude=set(sel_a) | set(sel_b))
                if sugg:
                    st.markdown(f"<div class='hq-h' style='margin-top:14px'>To even it, "
                                f"{A.rosters[light_uid].display} adds ~{abs(ev.delta):,.0f}"
                                f"</div>" + " ".join(
                                    f"<span class='hq-badge' style='background:"
                                    f"{POS.get(s.pos, SERIES['violet'])}'>{s.label} · "
                                    f"{s.value:,.0f}</span>" for s in sugg),
                                unsafe_allow_html=True)
                    if any(s.kind == "pick" for s in sugg):
                        st.markdown("<div class='hq-note'>Picks here are priced from the "
                                    "league-wide curve — check the sender actually owns "
                                    "that one.</div>", unsafe_allow_html=True)

            with st.expander("📋 Copy this trade"):
                st.code(tc.trade_summary_text(ev, assets_a, assets_b, unit=unit),
                        language=None)

        st.markdown(f"<div class='hq-note'>Values: {A.basis} basis · {qb_fmt}QB"
                    + (f" · DynastyProcess {bundle['scrape']}" if bundle['scrape'] else "")
                    + ". Roster impact re-fills each lineup with the league's real slots "
                    "(FLEX/SUPER_FLEX included); picks carry value but no lineup slot."
                    "</div>", unsafe_allow_html=True)

    # -- tab 2: auto-proposed packages, each loadable into the calculator ----
    with t_sugg:
        pkgs = la.propose_trades(A.rosters, view_uid)
        if not pkgs:
            st.info("No cleanly-balanced surplus-for-surplus packages right now — "
                    "your need may require an overpay or a pick sweetener. Build one "
                    "by hand in the Calculator tab.", icon="🤝")
        for i, p in enumerate(pkgs):
            tag = "competitor" if p.competitor else "non-competitor"
            tagc = STATUS["serious"] if p.competitor else SERIES["aqua"]
            get = ", ".join(f"<b>{n}</b> <span style='color:{INK['muted']}'>({ps} "
                            f"{v:,.0f})</span>" for n, ps, v in p.you_get)
            give = ", ".join(f"<b>{n}</b> <span style='color:{INK['muted']}'>({ps} "
                             f"{v:,.0f})</span>" for n, ps, v in p.you_give)
            st.markdown(
                f"<div class='hq-card'><div class='hq-row' style='border:0'>"
                f"<span>with <b>{p.partner_display}</b> "
                f"<span class='hq-badge' style='background:{tagc}'>{tag}</span></span>"
                f"<span style='color:{INK['muted']}'>gap {p.pct_gap*100:.0f}%</span></div>"
                f"<div class='hq-row'><span>YOU GET</span><span>{get}</span></div>"
                f"<div class='hq-row' style='border:0'><span>YOU GIVE</span>"
                f"<span>{give}</span></div></div>", unsafe_allow_html=True)
            if st.button("🧮 Open in calculator", key=f"tc_load_{i}"):
                st.session_state["_tc_load"] = {
                    "team_a": view_uid, "team_b": p.partner_uid,
                    "send_a": [k for k in (tc.PLAYER_PREFIX + i for i in p.give_ids)
                               if k in uni],
                    "send_b": [k for k in (tc.PLAYER_PREFIX + i for i in p.get_ids)
                               if k in uni],
                }
                st.rerun()
        if pkgs:
            st.markdown("<div class='hq-note'>Packages pair their surplus at your "
                        "biggest need against your surplus at theirs, inside a 10% "
                        "value gap. Open one in the calculator to see what it does to "
                        "both starting lineups, then tune it.</div>",
                        unsafe_allow_html=True)

    # -- tab 3: the league-wide block ---------------------------------------
    with t_block:
        st.markdown("<div class='hq-h'>Needs & surplus, whole league</div>",
                    unsafe_allow_html=True)
        st.dataframe(pd.DataFrame([{
            "team": A.rosters[u].display,
            "needs": ", ".join(k for k, v in sorted(A.needs[u].items(),
                               key=lambda kv: kv[1], reverse=True) if v > 0) or "—",
            "surplus": ", ".join(k for k, v in sorted(A.surplus[u].items(),
                                 key=lambda kv: kv[1], reverse=True) if v > 500) or "—",
        } for u in uids]), hide_index=True, width="stretch")
        st.markdown("<div class='hq-note'>Need = starting spot below the league median "
                    "at that position. Surplus = value beyond the starters.</div>",
                    unsafe_allow_html=True)

# ===========================================================================
elif page == "Waivers":
    st.markdown("## Waiver wire")

    # -- what the whole Sleeper population is doing right now ---------------
    st.markdown("<div class='hq-h'>🔥 Trending on Sleeper</div>",
                unsafe_allow_html=True)
    st.caption("Real add/drop volume across every Sleeper league — behaviour, "
               "not opinion. The only column that matters is whether he's "
               "actually free in *your* league.")
    w1, w2 = st.columns([1, 1])
    tr_hours = w1.radio("Window", [6, 24, 48], index=1, horizontal=True,
                        format_func=lambda h: f"{h}h")
    tr_kind = w2.radio("Direction", ["add", "drop"], horizontal=True,
                       format_func=lambda k: "📈 Adds" if k == "add" else "📉 Drops")
    try:
        trend = sleeper_trending(str(tr_kind), int(tr_hours), 40)
    except Exception as e:
        trend = []
        st.error(f"Sleeper trending unavailable: {e}")

    if trend:
        # Availability is the whole point: a hot add you can't have is noise.
        free_ids = {str(a.sleeper_id) for a in (A.available or [])}
        val_of = {str(a.sleeper_id): a.value for a in (A.available or [])}
        taken = {str(p.sleeper_id): rv.display
                 for rv in A.rosters.values() for p in rv.players}
        rows, n_free = [], 0
        for t in trend:
            pid = t["pid"]
            is_free = pid in free_ids
            n_free += 1 if is_free else 0
            rows.append({
                "": wh.headshot_url(pid),
                "status": "✅ FREE" if is_free else "—",
                "player": t.get("full_name") or pid,
                "pos": t.get("position") or "",
                "team": t.get("team") or "",
                f"{tr_kind}s": f"{t['count']:,}",
                "value": round(val_of.get(pid, 0)) or None,
                "rostered by": taken.get(pid, ""),
                "inj": t.get("injury_status") or "",
            })
        st.caption(f"{n_free} of {len(rows)} trending {tr_kind}s are still free "
                   f"in {lg['name']}.")
        show_roster_table(pd.DataFrame(rows), height=430)
        if tr_kind == "add" and not n_free:
            st.info("Everyone trending is already rostered here — normal in a "
                    "deep dynasty league.", icon="🏜️")

    st.markdown("<div class='hq-h'>Your targets</div>", unsafe_allow_html=True)
    tgts = A.waivers.get(view_uid, [])
    if tgts:
        st.caption(f"Fills the holes on {me.display}.")
        st.markdown(" ".join(
            f"<span class='hq-badge' style='background:{POS.get(t.pos, '#6b6a64')}'>"
            f"{t.name} · {t.value:,.0f}</span>" for t in tgts),
            unsafe_allow_html=True)
    st.markdown("<div class='hq-h'>Best available</div>", unsafe_allow_html=True)
    if not A.available:
        st.info("Wire is bone dry (deep league).", icon="🏜️")
    else:
        df = pd.DataFrame([{
            "": headshot_for(a.sleeper_id, bundle) or "",
            "player": a.name, "pos": a.pos, "value": round(a.value),
            "age": (round(a.age) if a.age else None),
        } for a in A.available[:25]])
        show_roster_table(df)

# ===========================================================================
elif page == "Set Lineup":
    st.markdown(f"## Set lineup — {me.display}")
    rec = la.lineup_recommendation(me, A.roster_positions, weekly=A.weekly,
                                   week=A.weekly_week, opp=A.weekly_opp or None)
    show_roster_table(roster_df(me, list(rec.lineup)))
    st.caption("Ranked by " + ("weekly DFS projection" if rec.weekly
                               else "season value (offseason)"))
    if lg["platform"] == "sleeper":
        st.link_button("🔗 Open this lineup screen in Sleeper",
                       sleeper_lineup_url(str(lg["league_id"])))
        st.markdown("<div class='hq-note'>Sleeper has no write API — set the lineup "
                    "above in the app (~5 seconds, zero risk). Your leagues on MFL get "
                    "a true one-click button below instead.</div>",
                    unsafe_allow_html=True)
    elif lg["platform"] == "mfl":
        if st.button("⚡ Build my one-click set-lineup button", type="primary"):
            from dynasty_tool.write.service import build_mfl_set
            with st.spinner("Building bookmarklet…"):
                try:
                    res = build_mfl_set(DiskCache(dt.CACHE_DIR), lg["host"],
                                        str(lg["season"]), str(lg["league_id"]),
                                        str(lg.get("team", "")), int(week))
                except Exception as e:
                    st.error(f"Build failed: {e}")
                    res = None
            if res:
                st.success(f"Week {res['week']} button ready for {res['team']} — drag "
                           "it to your bookmarks bar, then click it on MFL (logged in).")
                st.download_button("⬇️ Download installer page",
                                   res["installer_html"],
                                   file_name=f"set_lineup_mfl_{lg['league_id']}.html",
                                   mime="text/html")
                components_html(res["installer_html"], height=650, scrolling=True)
    else:
        st.info("ESPN set-lineup is experimental — generate it from the CLI for now.",
                icon="🧪")

# ===========================================================================
elif page == "Value Flow":
    st.markdown("## Value flow — who fleeced whom")
    if lg["platform"] != "sleeper":
        st.info("Full trade-history flow is available for Sleeper leagues "
                "(needs the chain-walked transaction history).", icon="💸")
    else:
        st.markdown("<div class='hq-note'>Realized basis: values every asset at what "
                    "it's worth NOW (picks by the player they became). It shows the "
                    "OUTCOME — who ended up with the value — and cannot by itself "
                    "distinguish a knowing fleece from a fair bet that hit.</div>",
                    unsafe_allow_html=True)
        if st.button("Run full-history analysis", type="primary") or \
           st.session_state.get(f"flow_{lg['league_id']}"):
            st.session_state[f"flow_{lg['league_id']}"] = True
            with st.spinner("Walking the full league history (cached after first run)…"):
                res = load_flow(str(lg["league_id"]))
            accounts = sorted(res.accounts.values(), key=lambda a: a.net, reverse=True)
            c1, c2, c3, c4 = st.columns(4)
            tile(c1, "Trades all-time", str(res.n_trades), "completed")
            if accounts:
                tile(c2, "Biggest winner", accounts[0].display,
                     f"+{wh.fmt_k(accounts[0].net)} net")
                tile(c3, "Biggest loser", accounts[-1].display,
                     f"{wh.fmt_k(accounts[-1].net)} net")
            tile(c4, "Unresolved futures", str(res.unresolved_future_count),
                 "future picks (kept, not dropped)")
            st.markdown("<div class='hq-h'>Cumulative net value (top movers colored)"
                        "</div>", unsafe_allow_html=True)
            st.plotly_chart(flow_fig(res), width="stretch",
                            config={"displayModeBar": False})
            st.markdown(f"<div class='hq-h'>{me.display} — net extracted per season"
                        "</div>", unsafe_allow_html=True)
            st.plotly_chart(year_flow_fig(res, view_uid), width="stretch",
                            config={"displayModeBar": False})
            st.markdown("<div class='hq-h'>Per-account ledger</div>",
                        unsafe_allow_html=True)
            st.dataframe(pd.DataFrame([{
                "account": a.display, "trades": a.n_trades,
                "value in": round(a.total_in), "value out": round(a.total_out),
                "net": round(a.net),
                "top partner": (res.accounts[a.top_counterparty].display
                                if a.top_counterparty in res.accounts else "—"),
                "concentration": f"{a.trade_concentration*100:.0f}%",
            } for a in accounts]), hide_index=True, width="stretch")

# ===========================================================================
elif page == "Portfolio":
    st.markdown("## Portfolio — every league at once")
    rows, exposure = [], {}
    prog = st.progress(0.0, "Analyzing leagues…")
    for i, entry in enumerate(leagues):
        try:
            b = load_league(entry["platform"], str(entry["league_id"]),
                            str(entry.get("season", st.session_state.season)),
                            str(entry.get("host", "")),
                            str(entry.get("team", st.session_state.username)),
                            int(week))
        except Exception:
            continue
        finally:
            prog.progress((i + 1) / len(leagues))
        eA = b["A"]
        euid = b["my_uid"]
        if euid not in eA.rosters:
            continue
        er = eA.rosters[euid]
        es = next((s for s in eA.sim if s.user_id == euid), None)
        eorder = sorted(eA.rosters.values(), key=lambda r: r.starter_value, reverse=True)
        rows.append({
            "league": entry["name"], "platform": entry["platform"],
            "rank": f"{next(i for i, r in enumerate(eorder, 1) if r.user_id == euid)}"
                    f"/{len(eorder)}",
            "window": er.window,
            "proj W": round(es.proj_wins, 1) if es else None,
            "playoff %": round(es.playoff_pct * 100) if es else None,
            "title %": round(es.title_pct * 100) if es else None,
        })
        for p in er.players:
            if p.value <= 0:
                continue
            sid = p.sleeper_id if not b["g2s"] else b["g2s"].get(p.sleeper_id)
            if not sid:
                continue
            e = exposure.setdefault(sid, {"name": p.name, "pos": p.pos, "n": 0,
                                          "leagues": []})
            e["n"] += 1
            e["leagues"].append(entry["name"])
    prog.empty()
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.markdown("<div class='hq-h'>Player exposure — who your season rides on</div>",
                unsafe_allow_html=True)
    hot = sorted(exposure.items(), key=lambda kv: kv[1]["n"], reverse=True)
    df = pd.DataFrame([{
        "": wh.headshot_url(sid), "player": e["name"], "pos": e["pos"],
        "leagues": e["n"], "in": ", ".join(e["leagues"]),
    } for sid, e in hot if e["n"] >= 2][:25])
    if len(df):
        show_roster_table(df)
        st.markdown("<div class='hq-note'>3+ leagues = a boom or bust there hits "
                    "your whole portfolio at once.</div>", unsafe_allow_html=True)
    else:
        st.caption("No multi-league overlaps found.")
