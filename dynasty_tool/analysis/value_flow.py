"""Module 2 -- League Value-Flow Analyzer (the "who fleeced whom" tool).

Everything keys on ``user_id`` (R1). For every trade across the full chain we
value each directed transfer (players at current value; picks traced to the
player they became, or slot/generic value for future picks -- R3/R4), then roll
up per-account IN/OUT/net, a pairwise net-flow matrix, partner concentration,
and same-day clustering.

Honesty (printed in output): ``realized`` basis shows the OUTCOME (who ended up
with the value). It CANNOT by itself distinguish a knowing fleece from a fair
bet that happened to hit -- that needs ``point_in_time``, which is unavailable
without a historical value source.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .. import config
from .common import value_transfers


@dataclass
class AccountFlow:
    user_id: str
    display: str
    total_in: float = 0.0
    total_out: float = 0.0
    n_trades: int = 0
    in_by_partner: dict = field(default_factory=lambda: defaultdict(float))
    out_by_partner: dict = field(default_factory=lambda: defaultdict(float))
    trades_by_partner: dict = field(default_factory=lambda: defaultdict(int))

    @property
    def net(self) -> float:
        return self.total_in - self.total_out

    def net_with(self, partner: str) -> float:
        return self.in_by_partner[partner] - self.out_by_partner[partner]

    @property
    def top_counterparty(self) -> Optional[str]:
        if not self.trades_by_partner:
            return None
        return max(self.trades_by_partner, key=lambda p: self.trades_by_partner[p])

    @property
    def trade_concentration(self) -> float:
        total = sum(self.trades_by_partner.values())
        if total == 0:
            return 0.0
        return max(self.trades_by_partner.values()) / total


@dataclass
class Cluster:
    a: str
    b: str
    day: str
    n_trades: int


@dataclass
class FlowResult:
    basis: str
    qb_format: int
    accounts: dict[str, AccountFlow]
    pairwise_net: dict[tuple[str, str], float]        # (A,B) -> net A extracted from B
    timeline: dict[str, list[tuple[datetime, float]]]  # uid -> [(t, cum_net)]
    clusters: list[Cluster]
    n_trades: int
    faab_count: int = 0
    unresolved_future_count: int = 0
    unmatched_player_count: int = 0
    # season -> {(giver_uid, receiver_uid): gross value handed over that year}
    flows_by_year: dict = field(default_factory=dict)
    years: list = field(default_factory=list)


def compute_flow(ctx, provider, trades) -> FlowResult:
    accounts: dict[str, AccountFlow] = {}

    def acct(uid: str) -> AccountFlow:
        if uid not in accounts:
            accounts[uid] = AccountFlow(uid, ctx.display(uid))
        return accounts[uid]

    pairwise_raw: dict[tuple[str, str], float] = defaultdict(float)  # (recv,giver)->value
    flows_by_year: dict[str, dict[tuple[str, str], float]] = defaultdict(lambda: defaultdict(float))
    timeline: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
    running_net: dict[str, float] = defaultdict(float)
    pair_days: dict[tuple[str, str], set] = defaultdict(set)
    pair_day_counts: dict[tuple[tuple[str, str], str], int] = defaultdict(int)

    faab_count = unresolved = unmatched = 0
    ordered = sorted(trades, key=lambda t: t.timestamp or 0)

    for tr in ordered:
        vts = value_transfers(ctx, provider, tr)

        # per-transfer accounting
        changed: set[str] = set()
        for vt in vts:
            av = vt.av
            if av.kind == "faab":
                faab_count += 1
            if av.flag == "UNRESOLVED_FUTURE_PICK":
                unresolved += 1
            if av.flag and "UNMATCHED_PLAYER" in av.flag:
                unmatched += 1
            g, r, v = vt.giver, vt.receiver, av.value
            if v == 0 or g is None or r is None:
                continue
            acct(r).total_in += v
            acct(r).in_by_partner[g] += v
            acct(g).total_out += v
            acct(g).out_by_partner[r] += v
            pairwise_raw[(r, g)] += v
            flows_by_year[tr.season][(g, r)] += v   # directed: giver -> receiver, that season
            running_net[r] += v
            running_net[g] -= v
            changed.update([r, g])

        # trade-participation counts (per unordered pair actually in the trade)
        parts = [p for p in tr.participants if p is not None]
        for p in parts:
            acct(p).n_trades += 1
        for i in range(len(parts)):
            for j in range(i + 1, len(parts)):
                a, b = parts[i], parts[j]
                acct(a).trades_by_partner[b] += 1
                acct(b).trades_by_partner[a] += 1
                key = tuple(sorted((a, b)))
                if tr.timestamp:
                    day = _day(tr.timestamp)
                    pair_days[key].add(day)
                    pair_day_counts[(key, day)] += 1

        # timeline snapshot for accounts whose cumulative net moved
        if tr.timestamp:
            t = _dt(tr.timestamp)
            for uid in changed:
                timeline[uid].append((t, running_net[uid]))

    # anchor every timeline to the last event time so lines extend to "now"
    if ordered and ordered[-1].timestamp:
        end = _dt(ordered[-1].timestamp)
        for uid, pts in timeline.items():
            if pts and pts[-1][0] != end:
                pts.append((end, running_net[uid]))

    # pairwise NET: A extracted from B
    pairwise_net: dict[tuple[str, str], float] = {}
    seen_pairs = {(r, g) for (r, g) in pairwise_raw}
    parties = set()
    for (r, g) in seen_pairs:
        parties.update([r, g])
    for a in parties:
        for b in parties:
            if a == b:
                continue
            net = pairwise_raw.get((a, b), 0.0) - pairwise_raw.get((b, a), 0.0)
            if net != 0.0:
                pairwise_net[(a, b)] = net

    clusters = [
        Cluster(a=key[0], b=key[1], day=day, n_trades=cnt)
        for (key, day), cnt in sorted(pair_day_counts.items(), key=lambda kv: -kv[1])
        if cnt > 1
    ]

    return FlowResult(
        basis=provider.basis,
        qb_format=ctx.qb_format,
        accounts=accounts,
        pairwise_net=pairwise_net,
        timeline=timeline,
        clusters=clusters,
        n_trades=len(ordered),
        faab_count=faab_count,
        unresolved_future_count=unresolved,
        unmatched_player_count=unmatched,
        flows_by_year={yr: dict(d) for yr, d in flows_by_year.items()},
        years=sorted(flows_by_year.keys()),
    )


def _dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def _day(ms: int) -> str:
    return _dt(ms).strftime("%Y-%m-%d")


# -- rendering / outputs -----------------------------------------------------

def format_flow(res: FlowResult) -> str:
    lines: list[str] = []
    lines.append(f"LEAGUE VALUE FLOW  [basis={res.basis} | {res.qb_format}QB | "
                 f"{res.n_trades} trades]")
    lines.append("=" * 78)
    lines.append(f"{'account':<22}{'in':>9}{'out':>9}{'net':>9}{'trades':>8}"
                 f"  top partner (conc.)")
    lines.append("-" * 78)
    for af in sorted(res.accounts.values(), key=lambda a: a.net, reverse=True):
        top = af.top_counterparty
        top_disp = res.accounts[top].display if top and top in res.accounts else "-"
        conc = af.trade_concentration
        flag = "  <CONCENTRATED>" if conc > config.CONCENTRATION_FLAG else ""
        lines.append(f"{af.display:<22}{af.total_in:>9.0f}{af.total_out:>9.0f}"
                     f"{af.net:>+9.0f}{af.n_trades:>8}  {top_disp} ({conc*100:.0f}%){flag}")

    lines.append("\nTOP NET FLOWS (A extracted this much value FROM B, realized):")
    top_flows = sorted(res.pairwise_net.items(), key=lambda kv: kv[1], reverse=True)[:10]
    for (a, b), net in top_flows:
        if net <= 0:
            continue
        lines.append(f"    {res.accounts[a].display:<20} <- {res.accounts[b].display:<20} "
                     f"{net:>+8.0f}")

    if res.clusters:
        lines.append("\nSAME-DAY TRADE BURSTS (report only -- consistent with either "
                     "coordination OR normal multi-part dealmaking):")
        for c in res.clusters[:10]:
            da = res.accounts[c.a].display if c.a in res.accounts else c.a
            db = res.accounts[c.b].display if c.b in res.accounts else c.b
            lines.append(f"    {c.day}: {da} <-> {db}  x{c.n_trades} trades")

    lines.append("\nnotes:")
    lines.append(f"    faab transfers seen: {res.faab_count} (excluded from value points)")
    lines.append(f"    unresolved future picks valued generically: "
                 f"{res.unresolved_future_count}")
    lines.append(f"    unmatched players (valued 0): {res.unmatched_player_count}")
    lines.append("    HONESTY: 'realized' shows the OUTCOME (who ended up with value). "
                 "It cannot")
    lines.append("    distinguish a knowing fleece from a fair bet that hit -- that needs")
    lines.append("    'point_in_time' values, which require a historical source (unavailable).")
    return "\n".join(lines)


def write_flow_outputs(res: FlowResult, out_dir: Path, league_name: str = "") -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    # per-account summary CSV
    summ = out_dir / "flow_accounts.csv"
    with summ.open("w", encoding="utf-8", newline="") as f:
        f.write("user_id,display,total_in,total_out,net,n_trades,"
                "top_counterparty,trade_concentration_pct\n")
        for af in sorted(res.accounts.values(), key=lambda a: a.net, reverse=True):
            top = af.top_counterparty
            top_disp = res.accounts[top].display if top and top in res.accounts else ""
            f.write(f"{af.user_id},{_csv(af.display)},{af.total_in:.1f},{af.total_out:.1f},"
                    f"{af.net:.1f},{af.n_trades},{_csv(top_disp)},"
                    f"{af.trade_concentration*100:.0f}\n")
    written.append(summ)

    # pairwise matrix CSV (rows = receiver A, cols = giver B, cell = net A from B)
    matrix = out_dir / "flow_pairwise_matrix.csv"
    accounts = sorted(res.accounts.values(), key=lambda a: a.display.lower())
    uids = [a.user_id for a in accounts]
    with matrix.open("w", encoding="utf-8", newline="") as f:
        f.write("A\\B," + ",".join(_csv(a.display) for a in accounts) + "\n")
        for a in accounts:
            row = [_csv(a.display)]
            for b_uid in uids:
                if a.user_id == b_uid:
                    row.append("")
                else:
                    row.append(f"{res.pairwise_net.get((a.user_id, b_uid), 0.0):.0f}")
            f.write(",".join(row) + "\n")
    written.append(matrix)

    png = _write_chart(res, out_dir / "flow_cumulative.png")
    if png:
        written.append(png)

    # directed value-flow-by-year: static small-multiples PNG + interactive HTML
    try:
        from . import flow_viz
        colors = flow_viz.team_colors(res)
        written.append(flow_viz.write_small_multiples_png(res, colors, out_dir / "flow_by_year.png"))
        written.append(flow_viz.write_flow_html(res, out_dir / "flow_report.html", league_name))
    except Exception as exc:  # never let a viz failure kill the tables/CSVs
        print(f"    (flow-by-year viz skipped: {exc})")
    return written


def _write_chart(res: FlowResult, path: Path) -> Optional[Path]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except Exception:
        return None

    series = {uid: pts for uid, pts in res.timeline.items() if pts}
    if not series:
        return None

    fig, ax = plt.subplots(figsize=(12, 7))
    ordered = sorted(series.items(), key=lambda kv: kv[1][-1][1], reverse=True)
    cmap = plt.get_cmap("tab20")
    for i, (uid, pts) in enumerate(ordered):
        xs = [t for t, _ in pts]
        ys = [v for _, v in pts]
        ax.plot(xs, ys, drawstyle="steps-post", linewidth=1.7, color=cmap(i % 20),
                label=res.accounts[uid].display)
    ax.axhline(0, color="#888", linewidth=0.8)
    ax.set_title(f"Cumulative net value flow per account  "
                 f"[basis={res.basis} | {res.qb_format}QB]")
    ax.set_ylabel("cumulative net value (in - out)")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.legend(loc="center left", bbox_to_anchor=(1.01, 0.5), fontsize=8, frameon=False)
    fig.text(0.5, 0.01,
             "realized basis = OUTCOME (who won), not intent; a fleece and a fair "
             "bet that hit look identical here.",
             ha="center", fontsize=7, color="#666")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def _csv(s: str) -> str:
    s = str(s)
    if "," in s or '"' in s:
        return '"' + s.replace('"', '""') + '"'
    return s
