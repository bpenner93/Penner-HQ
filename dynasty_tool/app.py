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
    ("LINEUP", [("📊", "Dashboard"), ("👥", "My Team"), ("⚡", "Start/Sit"),
                ("🆚", "Matchups")]),
    ("SEASON", [("🏆", "Playoff Race"), ("📈", "History & SOS")]),
    ("MOVES", [("🔄", "Trades"), ("🧮", "Trade Calc"), ("➕", "Waivers"),
               ("🎯", "Set Lineup")]),
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
if page == "Dashboard":
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
    pkgs = la.propose_trades(A.rosters, view_uid)
    if not pkgs:
        st.info("No cleanly-balanced surplus-for-surplus packages right now — "
                "your need may require an overpay or a pick sweetener.", icon="🤝")
    for p in pkgs:
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

    st.markdown("<div class='hq-h'>League trade block — needs & surplus</div>",
                unsafe_allow_html=True)
    st.dataframe(pd.DataFrame([{
        "team": A.rosters[u].display,
        "needs": ", ".join(k for k, v in sorted(A.needs[u].items(),
                           key=lambda kv: kv[1], reverse=True) if v > 0) or "—",
        "surplus": ", ".join(k for k, v in sorted(A.surplus[u].items(),
                             key=lambda kv: kv[1], reverse=True) if v > 500) or "—",
    } for u in uids]), hide_index=True, width="stretch")

# ===========================================================================
elif page == "Trade Calc":
    st.markdown("## Trade calculator")
    unit = "proj pts" if A.redraft else "value"

    s1, s2, s3 = st.columns([1.2, 1.2, 1])
    uid_a = s1.selectbox("Team A", uids, index=uids.index(view_uid),
                         format_func=lambda u: A.rosters[u].display)
    others = [u for u in uids if u != uid_a]
    uid_b = s2.selectbox("Team B", others, format_func=lambda u: A.rosters[u].display)
    if A.redraft:
        qb_fmt = A.qb_format
        s3.markdown("<div class='hq-note'>Redraft — assets valued at this season's "
                    "projected points from your own engine.</div>",
                    unsafe_allow_html=True)
    else:
        qb_fmt = 2 if s3.radio("Value format", ["1QB", "Superflex"], horizontal=True,
                               index=1 if A.qb_format == 2 else 0) == "Superflex" else 1

    uni = tc.extend_with_rosters(trade_universe(bool(A.redraft), int(qb_fmt)), A.rosters)
    owners = tc.owner_map(A.rosters)
    by_value = sorted(uni, key=lambda k: -uni[k].value)

    def opts_for(uid: str) -> list[str]:
        """That team's assets first (so their roster is one click away), then everyone."""
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
    lid = str(lg["league_id"])
    for wkey in (f"tc_a_{lid}", f"tc_b_{lid}"):
        # a roster move between refreshes can retire a key; drop it rather than
        # let Streamlit raise on a selection that is no longer an option
        if wkey in st.session_state:
            st.session_state[wkey] = [k for k in st.session_state[wkey] if k in uni]
    with ca:
        st.markdown(f"<div class='hq-h'>{disp_a} sends</div>", unsafe_allow_html=True)
        sel_a = st.multiselect("side a", opts_for(uid_a), format_func=fmt_asset,
                               key=f"tc_a_{lid}", label_visibility="collapsed",
                               placeholder="Add a player or pick…")
    with cb:
        st.markdown(f"<div class='hq-h'>{disp_b} sends</div>", unsafe_allow_html=True)
        sel_b = st.multiselect("side b", opts_for(uid_b), format_func=fmt_asset,
                               key=f"tc_b_{lid}", label_visibility="collapsed",
                               placeholder="Add a player or pick…")

    assets_a = [uni[k] for k in sel_a]
    assets_b = [uni[k] for k in sel_b]
    ev = tc.evaluate(assets_a, assets_b, basis=A.basis, qb_format=qb_fmt,
                     num_teams=len(uids), name_a=disp_a, name_b=disp_b)
    ca.markdown(asset_rows(assets_a, ev.side_a.total, disp_a), unsafe_allow_html=True)
    cb.markdown(asset_rows(assets_b, ev.side_b.total, disp_b), unsafe_allow_html=True)

    if not assets_a and not assets_b:
        st.info("Add assets to both sides — search by name, or scroll: each side's "
                "own roster is listed first. Picks are in the same box "
                "(type “2027” or “1.05”).", icon="🧮")
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

        # -- roster impact: re-fill both starting lineups after the swap ------
        st.markdown("<div class='hq-h' style='margin-top:18px'>Roster impact — does it "
                    "change your starting lineup?</div>", unsafe_allow_html=True)
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
        stray = ([a.label for a in assets_a if a.kind == "player" and a.player_id not in rid_a]
                 + [a.label for a in assets_b if a.kind == "player" and a.player_id not in rid_b])
        if stray:
            st.markdown("<div class='hq-note'>Hypothetical: " + ", ".join(stray)
                        + " isn't on the sending team's roster, so roster impact counts "
                        "them as an add only.</div>", unsafe_allow_html=True)

        # -- what closes the gap ---------------------------------------------
        if ev.verdict != "EVEN":
            light_uid = uid_a if ev.delta < 0 else uid_b
            pool = [uni[k] for k in tc.roster_asset_keys(A.rosters[light_uid]) if k in uni]
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

        with st.expander("📋 Copy this trade"):
            st.code(tc.trade_summary_text(ev, assets_a, assets_b, unit=unit),
                    language=None)

    st.markdown(f"<div class='hq-note'>Values: {A.basis} basis · {qb_fmt}QB"
                + (f" · DynastyProcess {bundle['scrape']}" if bundle['scrape'] else "")
                + ". Roster impact re-fills each lineup with the league's real slots "
                "(FLEX/SUPER_FLEX included); picks carry value but no lineup slot."
                "</div>", unsafe_allow_html=True)

# ===========================================================================
elif page == "Waivers":
    st.markdown("## Waiver wire")
    tgts = A.waivers.get(view_uid, [])
    if tgts:
        st.markdown(f"<div class='hq-h'>Targets for {me.display} (fill your holes)"
                    "</div>", unsafe_allow_html=True)
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
