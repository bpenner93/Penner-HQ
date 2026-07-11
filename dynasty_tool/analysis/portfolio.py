"""Cross-league portfolio view: one manager across ALL their dynasty leagues.

Enumerates the user's type-2 (dynasty) leagues for a season, runs a lightweight
per-league read (roster value, window, title odds, biggest need, top trade), and
adds the portfolio-only insight you can't get one league at a time: PLAYER
EXPOSURE -- which players you're invested in across multiple leagues, and how
concentrated your fortunes are on them.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..ingest.context import build_context
from ..value.current_provider import CurrentValueProvider
from . import optimizer  # noqa: F401  (kept for API symmetry)
from .optimizer import _needs_and_surplus  # noqa: F401
from . import league_analysis as la
from .dashboard import _CSS, _esc, _TIER_COLOR, write_dashboard


@dataclass
class LeagueSlice:
    league_id: str
    name: str
    status: str
    n_teams: int
    qb_format: int
    rank: int
    title_pct: float
    playoff_pct: float
    proj_wins: float
    starter_value: float
    window: str
    value_axis: str
    biggest_need: str
    deepest_surplus: str
    advice: str
    top_trade: str
    report_file: str = ""        # per-league dashboard filename (for linking)


@dataclass
class Exposure:
    sleeper_id: str
    name: str
    pos: str
    n_leagues: int
    total_value: float
    avg_value: float
    leagues: list[str]


@dataclass
class Portfolio:
    user_id: str
    user_display: str
    season: str
    slices: list[LeagueSlice]
    exposures: list[Exposure]

    @property
    def n_contend(self) -> int:
        return sum(1 for s in self.slices if s.value_axis == "high")

    @property
    def n_rebuild(self) -> int:
        return sum(1 for s in self.slices if s.value_axis == "low")

    @property
    def n_bubble(self) -> int:
        return sum(1 for s in self.slices if s.value_axis == "mid")


def build_portfolio(client, cache, user_id: str, season: str,
                    out_dir: Optional[Path] = None) -> Portfolio:
    """Analyze every dynasty league the user is in.

    If out_dir is given, each league's FULL dashboard is written there as
    ``league_<id>.html`` and linked from the portfolio (so each row drills in).
    """
    u = client.user(user_id) or {}
    disp = u.get("display_name") or str(user_id)
    real_uid = str(u.get("user_id") or user_id)

    leagues = [lg for lg in client.user_leagues(real_uid, season)
               if (lg.get("settings") or {}).get("type") == 2]
    leagues.sort(key=lambda lg: lg.get("name", ""))

    slices: list[LeagueSlice] = []
    expo: dict[str, dict] = {}
    for lg in leagues:
        lid = lg["league_id"]
        ctx = build_context(client, lid, full=False)
        prov = CurrentValueProvider.from_source(cache, ctx.qb_format)
        A = la.analyze(ctx, prov)          # full analysis on the light context
        if real_uid not in A.rosters:
            continue
        rosters, needs, surplus = A.rosters, A.needs, A.surplus

        me = rosters[real_uid]
        order = sorted(rosters.values(), key=lambda r: r.starter_value, reverse=True)
        rank = next((i for i, r in enumerate(order, 1) if r.user_id == real_uid), 0)
        sm = next((x for x in A.sim if x.user_id == real_uid), None)
        need = max(needs[real_uid].items(), key=lambda kv: kv[1], default=("—", 0))
        surp = max(surplus[real_uid].items(), key=lambda kv: kv[1], default=("—", 0))

        pkgs = la.propose_trades(rosters, real_uid)
        top_trade = ""
        if pkgs:
            p = pkgs[0]
            g = ", ".join(n for n, _, _ in p.you_get)
            gv = ", ".join(n for n, _, _ in p.you_give)
            top_trade = f"GET {g} for {gv} (w/ {p.partner_display})"

        report_file = ""
        if out_dir is not None:
            report_file = f"league_{lid}.html"
            write_dashboard(A, Path(out_dir) / report_file, target_uid=real_uid)

        slices.append(LeagueSlice(
            league_id=lid, name=lg.get("name", ""), status=lg.get("status", ""),
            n_teams=lg.get("total_rosters", len(rosters)), qb_format=ctx.qb_format,
            rank=rank,
            title_pct=sm.title_pct if sm else 0.0,
            playoff_pct=sm.playoff_pct if sm else 0.0,
            proj_wins=sm.proj_wins if sm else 0.0,
            starter_value=me.starter_value, window=me.window, value_axis=me.value_axis,
            biggest_need=need[0] if need[1] > 0 else "—",
            deepest_surplus=surp[0] if surp[1] > 500 else "—",
            advice=A.advice.get(real_uid, ""),
            top_trade=top_trade, report_file=report_file,
        ))

        for p in me.players:
            if p.value <= 0:
                continue
            e = expo.setdefault(p.sleeper_id, {"name": p.name, "pos": p.pos,
                                               "n": 0, "val": 0.0, "lg": []})
            e["n"] += 1
            e["val"] += p.value
            e["lg"].append(lg.get("name", ""))

    exposures = [Exposure(sid, e["name"], e["pos"], e["n"], e["val"], e["val"] / e["n"], e["lg"])
                 for sid, e in expo.items()]
    exposures.sort(key=lambda x: (x.n_leagues, x.total_value), reverse=True)
    return Portfolio(real_uid, disp, str(season), slices, exposures)


# -- rendering ---------------------------------------------------------------

_EXTRA_CSS = """
.chips{display:flex;gap:10px;margin:6px 0 18px;flex-wrap:wrap}
.chip{background:#fff;border:1px solid #e6e8ec;border-radius:10px;padding:9px 14px;font-size:13px}
.chip b{font-size:19px;display:block}
.expbar{display:inline-block;height:8px;border-radius:4px;background:#8aa0c8;vertical-align:middle}
.hot{color:#b0642a;font-weight:600}
a{color:#1b2a4a;text-decoration:none;border-bottom:1px solid #cdd5e2}
a:hover{border-color:#1b2a4a}
"""


def render_portfolio_html(pf: Portfolio) -> str:
    tier = _TIER_COLOR
    rows = []
    for s in sorted(pf.slices, key=lambda x: x.title_pct, reverse=True):
        name_cell = (f"<a href='{_esc(s.report_file)}'>{_esc(s.name)}</a> "
                     f"<span class=sub>&#8599;</span>") if s.report_file else _esc(s.name)
        rows.append(
            f"<tr><td>{name_cell}</td>"
            f"<td class=sub>{_esc(s.status)} · {s.qb_format}QB · {s.n_teams}t</td>"
            f"<td class=num>{s.rank}/{s.n_teams}</td>"
            f"<td class=num>{s.starter_value:,.0f}</td>"
            f"<td class=num>{s.proj_wins:.1f}</td>"
            f"<td class=num>{s.playoff_pct*100:.0f}%</td>"
            f"<td class=num><b>{s.title_pct*100:.0f}%</b></td>"
            f"<td><span class=badge style='background:{tier.get(s.value_axis,'#666')}'>{_esc(s.window)}</span></td>"
            f"<td>{_esc(s.biggest_need)}</td><td>{_esc(s.deepest_surplus)}</td>"
            f"<td class=sub>{_esc(s.top_trade) or '—'}</td></tr>"
        )
    slices_tbl = (
        "<table class=block><tr><th>league</th><th>setup</th><th>your rank</th>"
        "<th>starter val</th><th>proj W</th><th>playoff%</th><th>title%</th>"
        "<th>window</th><th>need</th><th>surplus</th><th>top trade</th></tr>"
        + "".join(rows) + "</table>"
    )

    max_n = max((e.n_leagues for e in pf.exposures), default=1)
    exp_rows = []
    for e in pf.exposures[:25]:
        hot = " class=hot" if e.n_leagues >= 3 else ""
        w = round(60 * e.n_leagues / max_n)
        exp_rows.append(
            f"<tr><td{hot}>{_esc(e.name)}</td><td>{e.pos}</td>"
            f"<td class=num>{e.n_leagues} <span class=expbar style='width:{w}px'></span></td>"
            f"<td class=num>{e.total_value:,.0f}</td><td class=num>{e.avg_value:,.0f}</td>"
            f"<td class=sub>{_esc(', '.join(e.leagues))}</td></tr>"
        )
    exp_tbl = (
        "<table class=block><tr><th>player</th><th>pos</th><th>leagues</th>"
        "<th>total value</th><th>avg value</th><th>in</th></tr>"
        + "".join(exp_rows) + "</table>"
    )

    chips = (
        f"<div class=chips>"
        f"<div class=chip><b>{len(pf.slices)}</b>dynasty leagues</div>"
        f"<div class=chip><b>{pf.n_contend}</b>contending</div>"
        f"<div class=chip><b>{pf.n_bubble}</b>on the bubble</div>"
        f"<div class=chip><b>{pf.n_rebuild}</b>rebuilding</div>"
        f"<div class=chip><b>{len(pf.exposures)}</b>distinct valued players</div>"
        f"</div>"
    )

    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width, initial-scale=1'>"
        f"<title>Portfolio — {_esc(pf.user_display)}</title>"
        f"<style>{_CSS}{_EXTRA_CSS}</style></head><body><div class=wrap>"
        f"<h1>Dynasty Portfolio — {_esc(pf.user_display)}</h1>"
        f"<div class=meta>{pf.season} season · roster-strength projections (offseason: preseason odds)</div>"
        f"{chips}"
        f"<h3>Your team in each league</h3><div class=card>{slices_tbl}</div>"
        f"<h3>Player exposure — who your season rides on across leagues</h3>"
        f"<div class=honest>Players you roster in multiple leagues. High counts "
        f"(3+) mean your whole portfolio moves with that player — a boom or bust "
        f"hits everywhere at once. Click any league name above to open its full "
        f"dashboard (power rankings, odds, playoff race, start/sit, trades…).</div>"
        f"<div class=card>{exp_tbl}</div>"
        f"</div></body></html>"
    )


def write_portfolio(pf: Portfolio, path: Path) -> Path:
    path = Path(path)
    path.write_text(render_portfolio_html(pf), encoding="utf-8")
    return path
