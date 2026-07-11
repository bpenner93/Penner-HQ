"""Directed value-flow visualization: per-season Sankey diagrams.

Each diagram reads "left team GAVE this much value to right team" for one season
(ribbon width = value handed over). The same ``_draw_sankey`` renders both:
  * a static small-multiples PNG (all seasons at a glance), and
  * per-season SVGs embedded in a self-contained interactive HTML with a year
    selector (no external assets, opens straight in a browser).

Gross directed flows are shown (A->B and B->A are separate ribbons) because the
question is "who handed value to whom," not net outcome. The realized/current
caveat from value_flow still applies and is printed on the page.
"""
from __future__ import annotations

import html
import io
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.path import Path as MPath  # noqa: E402
from matplotlib.patches import PathPatch, Rectangle  # noqa: E402

# Keep SVG text as <text> (not vector-path outlines): far smaller files, faster
# to render in the browser, and still self-contained.
plt.rcParams["svg.fonttype"] = "none"

_TAB20 = plt.get_cmap("tab20")


# -- helpers -----------------------------------------------------------------

def _hex(rgba) -> str:
    r, g, b = rgba[0], rgba[1], rgba[2]
    return f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"


def team_colors(res) -> dict[str, str]:
    """Stable color per account (by net rank, so the funnel winner is color 0)."""
    order = sorted(res.accounts.values(), key=lambda a: a.net, reverse=True)
    return {a.user_id: _hex(_TAB20(i % 20)) for i, a in enumerate(order)}


def _name(res, uid: str) -> str:
    return res.accounts[uid].display if uid in res.accounts else str(uid)


def year_flows(res, year: str) -> dict[tuple[str, str], float]:
    if year == "All":
        agg: dict[tuple[str, str], float] = defaultdict(float)
        for d in res.flows_by_year.values():
            for k, v in d.items():
                agg[k] += v
        return dict(agg)
    return res.flows_by_year.get(year, {})


def _title(res, year: str) -> str:
    label = "all seasons" if year == "All" else year
    tot = sum(year_flows(res, year).values())
    return f"Value handed over — {label}   ({tot:,.0f} total)"


# -- the diagram -------------------------------------------------------------

def _draw_sankey(ax, flows: dict[tuple[str, str], float], res, colors) -> None:
    items = [(g, r, v) for (g, r), v in flows.items() if v > 0]
    ax.set_xlim(-0.34, 1.34)
    ax.set_ylim(-0.04, 1.06)
    ax.axis("off")
    if not items:
        ax.text(0.5, 0.5, "no trades this season", ha="center", va="center",
                color="#999", fontsize=12)
        return

    total = sum(v for _, _, v in items)
    out_tot: dict[str, float] = defaultdict(float)
    in_tot: dict[str, float] = defaultdict(float)
    for g, r, v in items:
        out_tot[g] += v
        in_tot[r] += v
    left = sorted(out_tot, key=out_tot.get, reverse=True)
    right = sorted(in_tot, key=in_tot.get, reverse=True)
    left_idx = {u: i for i, u in enumerate(left)}
    right_idx = {u: i for i, u in enumerate(right)}

    gap = 0.015
    hscale = (1.0 - gap * (max(len(left), len(right)) - 1)) / total

    xL0, xL1 = -0.01, 0.03
    xR0, xR1 = 0.97, 1.01

    left_top: dict[str, float] = {}
    y = 1.0
    for u in left:
        left_top[u] = y
        y -= out_tot[u] * hscale + gap
    right_top: dict[str, float] = {}
    y = 1.0
    for u in right:
        right_top[u] = y
        y -= in_tot[u] * hscale + gap

    # node bars + labels
    for u in left:
        h = out_tot[u] * hscale
        ax.add_patch(Rectangle((xL0, left_top[u] - h), xL1 - xL0, h,
                               color=colors.get(u, "#888"), ec="none"))
        ax.text(xL0 - 0.015, left_top[u] - h / 2, f"{_name(res, u)}  {out_tot[u]:,.0f}",
                ha="right", va="center", fontsize=7.5, color="#222")
    for u in right:
        h = in_tot[u] * hscale
        ax.add_patch(Rectangle((xR0, right_top[u] - h), xR1 - xR0, h,
                               color=colors.get(u, "#888"), ec="none"))
        ax.text(xR1 + 0.015, right_top[u] - h / 2, f"{_name(res, u)}  {in_tot[u]:,.0f}",
                ha="left", va="center", fontsize=7.5, color="#222")

    # ribbons, stacked within each node band in node order
    off_l = dict(left_top)
    off_r = dict(right_top)
    for g, r, v in sorted(items, key=lambda t: (left_idx[t[0]], right_idx[t[1]])):
        h = v * hscale
        tL, bL = off_l[g], off_l[g] - h
        tR, bR = off_r[r], off_r[r] - h
        off_l[g] -= h
        off_r[r] -= h
        xm = (xL1 + xR0) / 2
        verts = [
            (xL1, tL),
            (xm, tL), (xm, tR), (xR0, tR),
            (xR0, bR),
            (xm, bR), (xm, bL), (xL1, bL),
            (xL1, tL),
        ]
        codes = [MPath.MOVETO,
                 MPath.CURVE4, MPath.CURVE4, MPath.CURVE4,
                 MPath.LINETO,
                 MPath.CURVE4, MPath.CURVE4, MPath.CURVE4,
                 MPath.CLOSEPOLY]
        ax.add_patch(PathPatch(MPath(verts, codes), facecolor=colors.get(g, "#888"),
                               edgecolor="none", alpha=0.45))


def _render_year_svg(res, year: str, colors) -> str:
    fig, ax = plt.subplots(figsize=(11, 7.2))
    _draw_sankey(ax, year_flows(res, year), res, colors)
    ax.set_title(_title(res, year), fontsize=13, color="#111", pad=12)
    fig.text(0.5, 0.015,
             "left = gave  →  right = received   ·   ribbon width = value handed over"
             f"   ·   basis={res.basis} · {res.qb_format}QB",
             ha="center", fontsize=8.5, color="#777")
    buf = io.BytesIO()
    fig.savefig(buf, format="svg", bbox_inches="tight")
    plt.close(fig)
    svg = buf.getvalue().decode("utf-8")
    i = svg.find("<svg")
    return svg[i:] if i >= 0 else svg


def write_small_multiples_png(res, colors, path: Path) -> Path:
    panels = ["All"] + res.years
    cols = 3
    rows = (len(panels) + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(cols * 6.4, rows * 4.7))
    axes = axes.flatten() if hasattr(axes, "flatten") else [axes]
    for ax, yr in zip(axes, panels):
        _draw_sankey(ax, year_flows(res, yr), res, colors)
        ax.set_title(_title(res, yr), fontsize=10.5)
    for ax in axes[len(panels):]:
        ax.axis("off")
    fig.suptitle(f"Value handed over, by season  [basis={res.basis} | {res.qb_format}QB]  "
                 "— left gave → right received, ribbon = value",
                 fontsize=13, y=0.997)
    fig.tight_layout(rect=(0, 0, 1, 0.985))
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


# -- interactive HTML --------------------------------------------------------

def _esc(s) -> str:
    return html.escape(str(s))


def _rank_rows(res, year: str, colors) -> str:
    items = sorted(year_flows(res, year).items(), key=lambda kv: kv[1], reverse=True)
    items = [(g, r, v) for (g, r), v in items if v > 0][:25]
    if not items:
        return '<div class="empty">no trades this season</div>'
    top = items[0][2]
    rows = []
    for g, r, v in items:
        w = max(3, round(100 * v / top))
        rows.append(
            f'<div class="row">'
            f'<span class="sw" style="background:{colors.get(g,"#888")}"></span>'
            f'<span class="pair"><b>{_esc(_name(res,g))}</b> → {_esc(_name(res,r))}</span>'
            f'<span class="bar"><span style="width:{w}%;background:{colors.get(g,"#888")}"></span></span>'
            f'<span class="val">{v:,.0f}</span>'
            f'</div>'
        )
    return "".join(rows)


_CSS = """
* { box-sizing: border-box; }
body { margin:0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
       color:#1a1a1a; background:#f4f5f7; }
.wrap { max-width:1180px; margin:0 auto; padding:22px 18px 60px; }
h1 { font-size:22px; margin:0 0 4px; }
.meta { color:#666; font-size:13px; margin-bottom:16px; }
.pills { display:flex; flex-wrap:wrap; gap:7px; margin:0 0 16px; }
.pill { border:1px solid #cfd3da; background:#fff; color:#333; padding:6px 13px; border-radius:999px;
        font-size:13px; cursor:pointer; transition:all .12s; }
.pill:hover { border-color:#8a93a3; }
.pill.active { background:#1b2a4a; color:#fff; border-color:#1b2a4a; }
.grid { display:grid; grid-template-columns: 1fr 340px; gap:18px; align-items:start; }
@media (max-width: 900px){ .grid { grid-template-columns: 1fr; } }
.card { background:#fff; border:1px solid #e6e8ec; border-radius:12px; padding:12px;
        box-shadow:0 1px 3px rgba(0,0,0,.04); }
.panel { display:none; }
.panel svg { width:100%; height:auto; display:block; }
.overflow { overflow-x:auto; }
.rank { display:none; }
.rank h3 { font-size:13px; margin:2px 0 10px; color:#444; text-transform:uppercase; letter-spacing:.04em; }
.row { display:grid; grid-template-columns: 14px 1fr 70px 62px; align-items:center; gap:8px;
       font-size:12.5px; padding:3px 0; border-bottom:1px solid #f0f1f3; }
.sw { width:11px; height:11px; border-radius:3px; display:inline-block; }
.pair { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.bar { background:#eef0f3; border-radius:4px; height:8px; overflow:hidden; }
.bar span { display:block; height:100%; }
.val { text-align:right; font-variant-numeric:tabular-nums; color:#333; }
.empty { color:#999; font-size:13px; padding:14px 2px; }
.note { margin-top:20px; font-size:12px; color:#7a7a7a; line-height:1.5;
        border-left:3px solid #d9dce1; padding:4px 0 4px 12px; }
"""

_JS = """
function show(y){
  document.querySelectorAll('.panel').forEach(function(e){e.style.display='none';});
  document.querySelectorAll('.rank').forEach(function(e){e.style.display='none';});
  document.querySelectorAll('.pill').forEach(function(e){e.classList.remove('active');});
  var p=document.getElementById('panel-'+y); if(p)p.style.display='block';
  var r=document.getElementById('rank-'+y); if(r)r.style.display='block';
  var b=document.getElementById('pill-'+y); if(b)b.classList.add('active');
}
window.addEventListener('DOMContentLoaded',function(){show('All');});
"""


def write_flow_html(res, path: Path, league_name: str = "") -> Path:
    colors = team_colors(res)
    panels_order = ["All"] + res.years

    pills = "".join(
        f'<button class="pill" id="pill-{y}" onclick="show(\'{y}\')">'
        f'{"All seasons" if y == "All" else y}</button>'
        for y in panels_order
    )
    panels = "".join(
        f'<div class="panel overflow" id="panel-{y}">{_render_year_svg(res, y, colors)}</div>'
        for y in panels_order
    )
    ranks = "".join(
        f'<div class="rank" id="rank-{y}"><h3>Top transfers · '
        f'{"all seasons" if y == "All" else y}</h3>{_rank_rows(res, y, colors)}</div>'
        for y in panels_order
    )

    title = f"Value flow by season{' — ' + _esc(league_name) if league_name else ''}"
    meta = (f"basis={res.basis} · {res.qb_format}QB · {res.n_trades} trades · "
            f"{len(res.accounts)} accounts · gross value handed over (giver → receiver)")

    doc = (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width, initial-scale=1'>"
        f"<title>{_esc(title)}</title><style>{_CSS}</style></head><body><div class=wrap>"
        f"<h1>{_esc(title)}</h1><div class=meta>{_esc(meta)}</div>"
        f"<div class=pills>{pills}</div>"
        f"<div class=grid><div class=card>{panels}</div><div class=card>{ranks}</div></div>"
        "<div class=note>Gross directed flow: each ribbon is value one team handed to "
        "another that season (A→B and B→A shown separately). <b>realized/current basis "
        "shows the OUTCOME</b> (what an asset is worth now / what a pick became), not what "
        "it was worth on the trade date — a knowing fleece and a fair bet that hit look "
        "identical here.</div>"
        f"<script>{_JS}</script></div></body></html>"
    )
    path = Path(path)
    path.write_text(doc, encoding="utf-8")
    return path
