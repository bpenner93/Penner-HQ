"""Interactive HTML league dashboard for the League Analyzer.

Self-contained (inline CSS/JS, embedded matplotlib SVG) so it opens straight in a
browser. Tabs: Power & Odds · Contention Map · Advice · Trades · Waivers ·
History/SOS. Odds are a roster-strength Monte-Carlo (labeled as such).
"""
from __future__ import annotations

import html
import io
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

plt.rcParams["svg.fonttype"] = "none"

from . import league_analysis as la  # noqa: E402
from .flow_viz import team_colors  # noqa: E402

_TIER_COLOR = {  # by value_axis
    "high": "#1a8a4a", "mid": "#c9871f", "low": "#3a6ea5",
}


def _esc(s) -> str:
    return html.escape(str(s))


def _tier_color(rv) -> str:
    return _TIER_COLOR.get(rv.value_axis, "#666")


def _val_label(A) -> str:
    return "proj pts" if getattr(A, "redraft", False) else "starter value"


# -- contention map (value vs age scatter) ----------------------------------

def _contention_map_svg(A) -> str:
    rosters = list(A.rosters.values())
    xs = [r.avg_starter_age or 0 for r in rosters]
    ys = [r.starter_value for r in rosters]
    med_age = sorted([x for x in xs if x])[len(xs) // 2] if xs else 26
    med_val = sorted(ys)[len(ys) // 2] if ys else 0

    fig, ax = plt.subplots(figsize=(9.5, 6.2))
    for r in rosters:
        c = _tier_color(r)
        x = r.avg_starter_age or med_age
        ax.scatter(x, r.starter_value, s=140, color=c, alpha=0.85, edgecolor="white", zorder=3)
        ax.annotate(r.display, (x, r.starter_value), fontsize=8, xytext=(6, 4),
                    textcoords="offset points", color="#222")
    ax.axvline(med_age, color="#bbb", ls="--", lw=1)
    ax.axhline(med_val, color="#bbb", ls="--", lw=1)
    # quadrant labels
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    ax.text(x0 + (med_age - x0) * 0.15, y1, "CONTEND\nstrong · young", fontsize=8.5,
            color="#1a8a4a", va="top", weight="bold")
    ax.text(x1, y1, "WIN-NOW / SELL\nstrong · aging", fontsize=8.5, color="#b0642a",
            va="top", ha="right", weight="bold")
    ax.text(x0 + (med_age - x0) * 0.15, y0, "REBUILD\nweak · young", fontsize=8.5,
            color="#3a6ea5", va="bottom", weight="bold")
    ax.text(x1, y0, "STUCK\nweak · aging", fontsize=8.5, color="#8a8a8a",
            va="bottom", ha="right", weight="bold")
    ax.set_xlabel("avg starter age (value-weighted)  →  older")
    ax.set_ylabel("starter value  →  stronger")
    ax.set_title("Contention map — value vs age (independent axes)")
    ax.grid(True, alpha=0.15)
    buf = io.BytesIO()
    fig.savefig(buf, format="svg", bbox_inches="tight")
    plt.close(fig)
    svg = buf.getvalue().decode("utf-8")
    i = svg.find("<svg")
    return svg[i:] if i >= 0 else svg


# -- sections ----------------------------------------------------------------

def _power_table(A, colors) -> str:
    max_title = max((s.title_pct for s in A.sim), default=1) or 1
    max_val = max((r.starter_value for r in A.rosters.values()), default=1) or 1
    rows = []
    for rank, s in enumerate(A.sim, 1):
        rv = A.rosters[s.user_id]
        age = f"{rv.avg_starter_age:.1f}" if rv.avg_starter_age else "—"
        vbar = round(100 * rv.starter_value / max_val)
        tbar = round(100 * s.title_pct / max_title)
        rows.append(
            f"<tr>"
            f"<td class=rk>{rank}</td>"
            f"<td><span class=dot style='background:{colors.get(s.user_id,'#888')}'></span>{_esc(s.display)}</td>"
            f"<td class=num>{rv.starter_value:,.0f}<span class=mini><span style='width:{vbar}%'></span></span></td>"
            f"<td class=num>{age}</td>"
            f"<td class=num>{s.proj_wins:.1f}</td>"
            f"<td class=num>{s.playoff_pct*100:.0f}%</td>"
            f"<td class=num><b>{s.title_pct*100:.0f}%</b><span class=mini><span style='width:{tbar}%;background:#1b2a4a'></span></span></td>"
            f"<td><span class=badge style='background:{_tier_color(rv)}'>{_esc(rv.window)}</span></td>"
            f"</tr>"
        )
    tier_hdr = "tier" if getattr(A, "redraft", False) else "window"
    head = (f"<tr><th>#</th><th>team</th><th>{_val_label(A)}</th><th>age</th>"
            f"<th>proj W</th><th>playoff%</th><th>title%</th><th>{tier_hdr}</th></tr>")
    return f"<table class=power>{head}{''.join(rows)}</table>"


def _advice_cards(A) -> str:
    cards = []
    for r in sorted(A.rosters.values(), key=lambda x: x.starter_value, reverse=True):
        cards.append(
            f"<div class=advice style='border-left-color:{_tier_color(r)}'>"
            f"<div class=at>{_esc(r.display)} "
            f"<span class=sub>{_esc(r.window)} · {_val_label(A)} {r.starter_value:,.0f} · age "
            f"{r.avg_starter_age:.1f}</span></div>".replace("age nan", "age —")
            + f"<div class=ax>{_esc(A.advice[r.user_id])}</div></div>"
        )
    return "".join(cards)


def _trades_section(A, target_uid) -> str:
    out = []
    if target_uid and target_uid in A.rosters:
        pkgs = la.propose_trades(A.rosters, target_uid)
        me = A.rosters[target_uid]
        out.append(f"<h3>Suggested trades for {_esc(me.display)}</h3>")
        if not pkgs:
            out.append("<div class=empty>No cleanly-balanced surplus-for-surplus "
                       "packages found — your needs may require an overpay or a pick.</div>")
        for p in pkgs:
            tag = "competitor" if p.competitor else "non-competitor"
            get = ", ".join(f"{_esc(n)} <span class=sub>({ps} {v:,.0f})</span>" for n, ps, v in p.you_get)
            give = ", ".join(f"{_esc(n)} <span class=sub>({ps} {v:,.0f})</span>" for n, ps, v in p.you_give)
            out.append(
                f"<div class=trade><div class=th>with <b>{_esc(p.partner_display)}</b> "
                f"<span class=tagpill>{tag}</span> "
                f"<span class=sub>· gap {p.pct_gap*100:.0f}%</span></div>"
                f"<div class=tg><span class=gl>you get</span> {get}</div>"
                f"<div class=tg><span class=gl>you give</span> {give}</div></div>"
            )
    # league trade block: needs vs surplus per team
    out.append("<h3>League trade block — needs &amp; surplus</h3><table class=block>"
               "<tr><th>team</th><th>needs (weak starters)</th><th>surplus (tradeable depth)</th></tr>")
    for r in sorted(A.rosters.values(), key=lambda x: x.starter_value, reverse=True):
        needs = ", ".join(f"{p}" for p, v in sorted(A.needs[r.user_id].items(),
                          key=lambda kv: kv[1], reverse=True) if v > 0) or "—"
        surp = ", ".join(f"{p}" for p, v in sorted(A.surplus[r.user_id].items(),
                         key=lambda kv: kv[1], reverse=True) if v > 500) or "—"
        out.append(f"<tr><td>{_esc(r.display)}</td><td>{needs}</td><td>{surp}</td></tr>")
    out.append("</table>")
    return "".join(out)


def _waivers_section(A) -> str:
    out = ["<h3>Best available (unrostered, by dynasty value)</h3>"]
    if not A.available:
        out.append("<div class=empty>Wire is empty of valued players (deep league).</div>")
    else:
        out.append("<table class=block><tr><th>player</th><th>pos</th><th>value</th><th>age</th></tr>")
        for a in A.available[:20]:
            age = f"{a.age:.0f}" if a.age else "—"
            out.append(f"<tr><td>{_esc(a.name)}</td><td>{a.pos}</td>"
                       f"<td class=num>{a.value:,.0f}</td><td class=num>{age}</td></tr>")
        out.append("</table>")
    out.append("<h3>Per-team pickup targets (fills your holes)</h3><table class=block>"
               "<tr><th>team</th><th>target pickups</th></tr>")
    for r in sorted(A.rosters.values(), key=lambda x: x.starter_value, reverse=True):
        tgts = A.waivers.get(r.user_id, [])
        txt = ", ".join(f"{_esc(t.name)} <span class=sub>({t.pos} {t.value:,.0f})</span>"
                        for t in tgts) or "—"
        out.append(f"<tr><td>{_esc(r.display)}</td><td>{txt}</td></tr>")
    out.append("</table>")
    return "".join(out)


def _matchups_section(A) -> str:
    if not A.matchups:
        return "<div class=empty>No schedule set yet — matchups appear once the league schedule is generated.</div>"
    live = A.matchups_live
    out = [f"<h3>Week {A.matchup_week} matchups — {'LIVE' if live else 'projected'} win odds</h3>",
           "<div class=honest>Win odds from the same roster-strength model"
           + (" blended with live scores." if live else " (pre-game projection).") + "</div>"]
    for m in A.matchups:
        hp = round(m.home_win_prob * 100)
        ap = 100 - hp
        score = f"<div class=sub>live score: {m.home_points:.1f} – {m.away_points:.1f}</div>" if live else ""
        out.append(
            f"<div class=mu><div class=mut><b>{_esc(m.home_disp)}</b> "
            f"<span class=sub>vs</span> <b>{_esc(m.away_disp)}</b></div>"
            f"<div class=mubar><span style='width:{hp}%'></span></div>"
            f"<div class=sub>{_esc(m.home_disp)} <b>{hp}%</b> · {_esc(m.away_disp)} <b>{ap}%</b> "
            f"· proj {m.home_proj:.0f}–{m.away_proj:.0f}</div>{score}</div>"
        )
    return "".join(out)


_STATUS_COLOR = {"clinched": "#1a8a4a", "eliminated": "#b04a3a",
                 "in the hunt": "#c9871f", "preseason": "#8a93a3"}


def _race_section(A) -> str:
    if not A.race:
        return "<div class=empty>No teams.</div>"
    pre = A.completed_weeks == 0
    note = ("Preseason projection — everyone 0-0, the full real slate simulated."
            if pre else
            f"Through week {A.completed_weeks}: current records seeded, then the REAL "
            f"remaining schedule simulated {la.SIM_N:,}x to the finish.")
    rows = []
    for i, r in enumerate(A.race, 1):
        rec = f"{r.current_wins:.0f}-{r.current_losses}" if A.completed_weeks else "0-0"
        bar = round(100 * r.playoff_pct)
        rows.append(
            f"<tr><td class=rk>{i}</td><td>{_esc(r.display)}</td>"
            f"<td class=num>{rec}</td>"
            f"<td class=num>{r.proj_wins:.1f}</td>"
            f"<td class=num>{r.playoff_pct*100:.0f}%<span class=mini>"
            f"<span style='width:{bar}%;background:#2f9e6b'></span></span></td>"
            f"<td class=num>{r.title_pct*100:.0f}%</td>"
            f"<td><span class=pill style='background:{_STATUS_COLOR.get(r.status,'#888')}'>{r.status}</span></td></tr>"
        )
    return (f"<h3>Playoff race — projected final standings</h3>"
            f"<div class=honest>{note} Playoff spots: {A.playoff_teams}.</div>"
            "<table class=power><tr><th>#</th><th>team</th><th>record</th><th>proj W</th>"
            "<th>make playoffs</th><th>title%</th><th>status</th></tr>" + "".join(rows) + "</table>")


def _startsit_section(A, target_uid) -> str:
    if not target_uid or target_uid not in A.rosters:
        return ("<div class=empty>Start/Sit is tailored to one team — re-run with "
                "<code>--team &lt;you&gt;</code> to see your recommended lineup.</div>")
    rv = A.rosters[target_uid]
    rec = la.lineup_recommendation(rv, A.roster_positions,
                                   weekly=A.weekly, week=A.weekly_week, opp=A.weekly_opp)
    sc = lambda p: rec.score.get(p.sleeper_id, p.value)  # noqa: E731
    fmt = "{:,.1f}" if rec.weekly else "{:,.0f}"

    if rec.weekly:
        head = f"Recommended lineup — {_esc(rv.display)} · Week {rec.week}"
        note = ("Ranked by your DFS <b>weekly</b> projection — this-week points, "
                "opponent- and availability-aware (a benched/injured player drops out "
                "for the week). This is the correct start/sit signal.")
        val_hdr, opp_hdr = "proj pts", "<th>opp</th>"
    else:
        metric = ("projected season points" if A.basis == "redraft"
                  else "dynasty value (a talent proxy)")
        hint = (" Pass <code>--week N</code> to rank by your DFS <b>weekly</b> projection instead."
                if A.basis == "redraft" else "")
        head = f"Recommended lineup — {_esc(rv.display)}"
        note = (f"Ranked by {metric}, not a matchup-specific weekly projection.{hint}")
        val_hdr, opp_hdr = "value", ""

    out = [f"<h3>{head}</h3>", f"<div class=honest>{note}</div>",
           f"<table class=block><tr><th>slot</th><th>player</th><th>pos</th>{opp_hdr}<th>{val_hdr}</th></tr>"]
    for slot, p in rec.lineup:
        opp_cell = f"<td class=sub>{_esc(rec.opp.get(p.sleeper_id, ''))}</td>" if rec.weekly else ""
        valtxt = "—" if (rec.weekly and sc(p) == 0) else fmt.format(sc(p))
        out.append(f"<tr><td class=sub>{slot}</td><td><b>{_esc(p.name)}</b></td>"
                   f"<td>{p.pos}</td>{opp_cell}<td class=num>{valtxt}</td></tr>")
    out.append("</table>")
    if rec.weekly:
        out.append("<div class=sub>Weekly engine covers offense (QB/RB/WR/TE); "
                   "K/DST show — and keep their rostered spot.</div>")
    if rec.close_calls:
        out.append("<h3>Tough calls — bench within 15% of a starter</h3>"
                   "<table class=block><tr><th>slot</th><th>starting</th><th>vs bench</th><th>gap</th></tr>")
        for slot, st, ch, gap in rec.close_calls:
            out.append(f"<tr><td class=sub>{slot}</td>"
                       f"<td>{_esc(st.name)} <span class=sub>({fmt.format(sc(st))})</span></td>"
                       f"<td>{_esc(ch.name)} <span class=sub>({fmt.format(sc(ch))})</span></td>"
                       f"<td class=num>{gap*100:.0f}%</td></tr>")
        out.append("</table>")
    else:
        out.append("<div class=sub>No close calls — your best lineup is clear-cut.</div>")
    if rec.bench:
        out.append("<h3>Bench</h3><div class=sub>"
                   + ", ".join(f"{_esc(p.name)} ({p.pos} {fmt.format(sc(p))})" for p in rec.bench[:12])
                   + "</div>")
    return "".join(out)


def _history_section(A) -> str:
    out = []
    if A.sos:
        out.append("<h3>Strength of schedule (this season)</h3>")
        out.append(_sos_table(A.sos, A))
    else:
        out.append("<div class=empty>No schedule set yet (pre-draft / offseason) — "
                   "strength of schedule and matchups appear once the league schedule "
                   "is generated.</div>")
    if A.last_season:
        ls = A.last_season
        out.append(f"<h3>Last season recap — {ls['season']}</h3>")
        out.append("<table class=block><tr><th>#</th><th>team</th><th>record</th>"
                   "<th>points for</th><th>points against</th><th>potential</th></tr>")
        for i, st in enumerate(ls["standings"], 1):
            out.append(f"<tr><td class=rk>{i}</td><td>{_esc(st.display)}</td>"
                       f"<td class=num>{st.wins}-{st.losses}{('-'+str(st.ties)) if st.ties else ''}</td>"
                       f"<td class=num>{st.fpts:,.0f}</td><td class=num>{st.fpts_against:,.0f}</td>"
                       f"<td class=num>{st.ppts:,.0f}</td></tr>")
        out.append("</table>")
        if ls.get("sos"):
            out.append(f"<h3>{ls['season']} strength of schedule (by opponents' points)</h3>")
            out.append(_sos_table(ls["sos"], A, by_points=True))
    return "".join(out)


def _sos_table(sos, A, by_points=False) -> str:
    unit = "opp pts/gm" if by_points else "opp strength"
    rows = sorted(sos.items(), key=lambda kv: kv[1]["avg_opp_strength"], reverse=True)
    body = []
    for uid, d in rows:
        disp = A.rosters[uid].display if uid in A.rosters else uid
        tough = "tougher" if d["vs_league_avg"] > 0 else "easier"
        body.append(f"<tr><td>{_esc(disp)}</td><td class=num>{d['avg_opp_strength']:,.0f}</td>"
                    f"<td class=num>{d['vs_league_avg']:+,.0f}</td><td>{tough}</td></tr>")
    return (f"<table class=block><tr><th>team</th><th>{unit}</th><th>vs avg</th>"
            f"<th></th></tr>{''.join(body)}</table>")


# -- assembly ----------------------------------------------------------------

_CSS = """
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:#1a1a1a;background:#f4f5f7}
.wrap{max-width:1080px;margin:0 auto;padding:22px 18px 70px}
h1{font-size:22px;margin:0 0 3px}
h3{font-size:15px;margin:20px 0 8px;color:#243}
.meta{color:#666;font-size:13px;margin-bottom:6px}
.honest{font-size:12px;color:#7a7a7a;border-left:3px solid #d9dce1;padding:4px 0 4px 11px;margin:8px 0 16px;line-height:1.5}
.tabs{display:flex;flex-wrap:wrap;gap:7px;margin:6px 0 16px}
.tab{border:1px solid #cfd3da;background:#fff;color:#333;padding:7px 14px;border-radius:8px;font-size:13px;cursor:pointer}
.tab:hover{border-color:#8a93a3}
.tab.active{background:#1b2a4a;color:#fff;border-color:#1b2a4a}
.pane{display:none}
.card{background:#fff;border:1px solid #e6e8ec;border-radius:12px;padding:16px;box-shadow:0 1px 3px rgba(0,0,0,.04);margin-bottom:16px;overflow-x:auto}
.card svg{width:100%;height:auto;display:block}
table{border-collapse:collapse;width:100%;font-size:13px}
th{text-align:left;color:#667;font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.03em;padding:6px 8px;border-bottom:2px solid #eef0f3}
td{padding:6px 8px;border-bottom:1px solid #f2f3f5;vertical-align:middle}
td.num{text-align:right;font-variant-numeric:tabular-nums}
td.rk{color:#999;width:26px}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:7px;vertical-align:middle}
.mini{display:block;height:4px;background:#eef0f3;border-radius:3px;margin-top:2px;overflow:hidden}
.mini span{display:block;height:100%;background:#8aa0c8}
.badge{color:#fff;font-size:11px;padding:2px 8px;border-radius:999px;white-space:nowrap}
.pill{color:#fff;font-size:11px;padding:2px 9px;border-radius:999px;white-space:nowrap;text-transform:capitalize}
.power td:nth-child(2){font-weight:600}
.advice{background:#fff;border:1px solid #eee;border-left:4px solid #888;border-radius:8px;padding:10px 12px;margin-bottom:8px}
.at{font-weight:600;font-size:14px}
.sub{color:#8a8a8a;font-weight:400;font-size:12px}
.ax{font-size:13px;color:#333;margin-top:3px;line-height:1.45}
.trade{border:1px solid #eef0f3;border-radius:8px;padding:9px 11px;margin-bottom:8px;background:#fbfcfd}
.th{font-size:13px;margin-bottom:4px}
.tg{font-size:13px;margin:2px 0}
.gl{display:inline-block;width:64px;color:#8a8a8a;font-size:11px;text-transform:uppercase}
.tagpill{background:#eef0f3;color:#555;font-size:11px;padding:1px 7px;border-radius:999px}
.empty{color:#999;font-size:13px;padding:10px 2px}
.mu{border:1px solid #eef0f3;border-radius:8px;padding:9px 11px;margin-bottom:8px;background:#fbfcfd}
.mut{font-size:14px;margin-bottom:6px}
.mubar{height:9px;background:#e0655a;border-radius:5px;overflow:hidden}
.mubar span{display:block;height:100%;background:#2f9e6b}
"""

_JS = """
function tab(id){
 document.querySelectorAll('.pane').forEach(function(e){e.style.display='none'});
 document.querySelectorAll('.tab').forEach(function(e){e.classList.remove('active')});
 var p=document.getElementById('pane-'+id); if(p)p.style.display='block';
 var b=document.getElementById('tab-'+id); if(b)b.classList.add('active');
}
window.addEventListener('DOMContentLoaded',function(){tab('power')});
"""


def build_dashboard(A, target_uid=None) -> str:
    colors = team_colors(_ColorShim(A))
    tabs = [("power", "Power &amp; Odds")]
    if A.race:
        tabs.append(("race", "Playoff Race"))
    if not A.redraft:  # contention map is a dynasty age-vs-value view
        tabs.append(("map", "Contention Map"))
    tabs.append(("advice", "Advice"))
    if target_uid:
        tabs.append(("startsit", "Start/Sit"))
    tabs += [("trades", "Trades"), ("waivers", "Waivers")]
    if A.matchups:
        lbl = "Matchups (live)" if A.matchups_live else "Matchups"
        tabs.append(("matchups", lbl))
    tabs.append(("history", "History / SOS"))
    tabbar = "".join(f"<button class=tab id=tab-{k} onclick=\"tab('{k}')\">{v}</button>"
                     for k, v in tabs)

    sched_note = ("real league schedule" if A.sim_used_real_schedule
                  else "balanced schedule (none set yet)")
    strength_src = ("its starters' projected fantasy points" if A.redraft
                    else "its starter value")
    honest = (f"Odds are a Monte-Carlo ({la.SIM_N:,} seasons, {sched_note}): each "
              f"team's weekly score is modeled from {strength_src} with real "
              f"week-to-week variance and true-talent uncertainty. It is a "
              f"projection, not a guarantee — treat title% as 'roughly how strong is "
              f"this roster', not a lock.")

    panes = {
        "power": f"<div class=card>{_power_table(A, colors)}</div>"
                 f"<div class=honest>{honest}</div>",
        "advice": f"<div class=card>{_advice_cards(A)}</div>",
        "trades": f"<div class=card>{_trades_section(A, target_uid)}</div>",
        "waivers": f"<div class=card>{_waivers_section(A)}</div>",
        "history": f"<div class=card>{_history_section(A)}</div>",
    }
    if A.race:
        panes["race"] = f"<div class=card>{_race_section(A)}</div>"
    if not A.redraft:
        panes["map"] = f"<div class=card>{_contention_map_svg(A)}</div>"
    if target_uid:
        panes["startsit"] = f"<div class=card>{_startsit_section(A, target_uid)}</div>"
    if A.matchups:
        panes["matchups"] = f"<div class=card>{_matchups_section(A)}</div>"
    body_panes = "".join(f"<div class=pane id=pane-{k}>{v}</div>" for k, v in panes.items())

    status_txt = {"pre_draft": "pre-draft (preseason)", "in_season": "in season",
                  "complete": "complete"}.get(A.status, A.status)
    kind = "Redraft Analyzer" if A.redraft else "League Analyzer"
    title = f"{kind} — {_esc(A.league_name)}"
    meta = (f"{status_txt} · {A.qb_format}QB · {len(A.rosters)} teams · "
            f"basis={A.basis} · {A.playoff_teams} playoff spots · {A.reg_weeks}-week season")

    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width, initial-scale=1'>"
        f"<title>{_esc(title)}</title><style>{_CSS}</style></head><body><div class=wrap>"
        f"<h1>{_esc(title)}</h1><div class=meta>{_esc(meta)}</div>"
        f"<div class=tabs>{tabbar}</div>{body_panes}"
        f"<script>{_JS}</script></div></body></html>"
    )


class _ColorShim:
    """team_colors() wants an object with .accounts; adapt LeagueAnalysis.rosters."""
    def __init__(self, A):
        self.accounts = {uid: _Acct(uid, rv.display, rv.starter_value)
                         for uid, rv in A.rosters.items()}


class _Acct:
    def __init__(self, uid, display, net):
        self.user_id = uid
        self.display = display
        self.net = net


def write_dashboard(A, path: Path, target_uid=None) -> Path:
    path = Path(path)
    path.write_text(build_dashboard(A, target_uid), encoding="utf-8")
    return path
