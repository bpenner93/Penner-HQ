# dynasty_tool — Sleeper Dynasty League Value Tool

Ingests a Sleeper dynasty league's **entire history** (chain-walked from any one
season) and runs three analyses over one shared ingestion + valuation core:

1. **Trade Evaluator** (`evaluate`) — score a proposed trade: per-asset values,
   side totals, delta, and a fairness verdict.
2. **League Value-Flow Analyzer** (`flow`) — per-account cumulative net value
   flow across all history (the "who fleeced whom" funnel), a pairwise net-flow
   matrix, partner concentration, and same-day clustering.
3. **Roster / Window Optimizer** (`optimize`) — value each roster, split
   starter value from depth, classify the contention window on two independent
   axes (value **and** age), and surface positional-fit trade targets.

Plus two league-wide views built on the same core:

4. **League Analyzer** (`analyze`) — one interactive HTML dashboard: power
   rankings + **playoff/championship odds** (roster-strength Monte-Carlo), a
   **rest-of-season playoff race** (seeds current records, sims the *real*
   remaining schedule to the finish, with clinch/eliminated status), a
   value-vs-age **contention map**, per-team buy/sell **advice**, a
   **start/sit** recommender (optimal lineup + the close bench-vs-starter
   calls), concrete balanced **trade** packages, **waiver/FA** targets,
   **weekly matchups with win odds**, and **strength-of-schedule** +
   last-season standings.
5. **Cross-League Portfolio** (`portfolio`) — one manager across *all* their
   dynasty leagues at once: per-league rank/window/title-odds, **player-exposure**
   analysis (which players your whole season rides on because you roster them in
   3–4 leagues), and a **full dashboard for every league** (`league_<id>.html`)
   linked from each row so you can drill in.

---

## ⚠️ Read this first: the value-basis limitation

Every value-bearing output is produced under an explicit **basis**, and the two
bases answer different questions:

| basis | question | availability |
|---|---|---|
| `realized` *(default)* | *Who ended up with the value / who won?* Assets valued at **current** worth; a traded pick is valued by **the player it became**. | ✅ sourceable today (DynastyProcess current values) |
| `point_in_time` | *Was the trade fair when it was made / a knowing fleece?* Each asset valued **as of the trade date**. | ❌ **unavailable** — no free historical dynasty-value source back to ~2019 |

**There is no confirmed free historical value source.** DynastyProcess exposes
only current values (a single `scrape_date`). This tool will **NOT** silently
substitute current values for historical ones. Requesting `--basis
point_in_time` without a configured historical provider **fails loudly** and
tells you exactly what data would be needed:

```
$ python -m dynasty_tool.cli flow --league-id <id> --basis point_in_time
point_in_time basis is unavailable: no historical dynasty-value source is configured.
  Why: DynastyProcess publishes only CURRENT values (a single scrape_date)...
  What would fix it: supply a dated historical value set via a HistoricalValueProvider
  -- e.g. KeepTradeCut historical values, or dated DynastyProcess snapshots...
```

To enable `point_in_time`, implement `value/historical_provider.py` against a
dated source (e.g. KTC history) — the interface (`ValueProvider`) is already in
place, so nothing else needs to change.

**Honesty about the funnel:** `realized` shows the **outcome** (who ended up
with the value). It **cannot** by itself distinguish a knowing fleece from a
fair bet that happened to hit — that distinction requires `point_in_time`. The
`flow` output and chart say so explicitly; do not read intent into them.

---

## The app (Penner HQ)

Everything above is also packaged as a dark, FantasyPros-"My Playbook"-style
**Streamlit dashboard** — league switcher + team switcher + grouped nav all in
the left sidebar; player profiles with headshots, ECR, and injury badges;
positional-strength radar; title-odds bars; contention map; playoff race;
trades; the trade calculator; waivers; the one-click MFL set-lineup button;
the value-flow funnel; and a cross-league portfolio with player exposure.

### Trade Calc — a KTC-style calculator that knows your league

Build a trade on both sides from a type-ahead search over **every** valued
player plus every pick label the source covers (`analysis/trade_calc.py`), and
get the same fairness verdict the `evaluate` CLI gives. Two things a
KeepTradeCut-style calculator can't do, because this one has the league loaded:

* **ownership** — each search result shows who rosters that player right now,
  and each side lists its own team's assets first;
* **roster impact** — both starting lineups are re-filled from the league's real
  slots after the swap, so you see the *lineup* delta, not just the value delta
  (a 5,767 WR who only upgrades your starter by 2,463 is exactly the trade the
  raw value chart talks you into).

Picks carry value but never a lineup slot, and that's reported separately rather
than blended in. Lopsided? It suggests the assets from the light side's roster
that land closest to the gap. 1QB/Superflex follows the league and can be
overridden; redraft/keeper leagues value assets at projected points from this
project's own engine instead, and hide picks (not a redraft asset).

```bash
# one-click: double-click dynasty_tool/start_app.bat   (opens localhost:8504)
# or by hand:
python -m streamlit run dynasty_tool/app.py --server.port 8504
```

Phone on the same WiFi: `http://<your-pc-ip>:8504`. Extra (MFL/ESPN) leagues
live in `dynasty_tool/cache/app_leagues.json`. The app is read-only except the
Set Lineup page, which only *builds* the bookmarklet/deep link — it never
writes to a platform by itself.

## Install

```bash
pip install -r dynasty_tool/requirements.txt   # requests, pandas, numpy, matplotlib
# tests additionally need: pytest
```

No API key is required — the Sleeper API and DynastyProcess CSVs are public.

## Usage

Run from the directory that contains the `dynasty_tool/` package.

```bash
# Build/refresh the on-disk cache; print the chain, identity aliases, coverage.
python -m dynasty_tool.cli ingest   --league-id 1329657907859423232

# Score a trade (players by name or sleeper_id; picks as "2027 1st" / "2026 1.05").
python -m dynasty_tool.cli evaluate --league-id <id> \
    --side-a "Bijan Robinson, 2027 1st" --side-b "Ja'Marr Chase" \
    --name-a Me --name-b Them

# League value-flow funnel -> tables + out/flow_*.csv + out/flow_cumulative.png
python -m dynasty_tool.cli flow     --league-id <id>

# Roster values, contention windows, and trade targets for one team.
python -m dynasty_tool.cli optimize --league-id <id> --team PennerBoy

# Full league analyzer -> CLI summary + out/league_report.html (interactive).
python -m dynasty_tool.cli analyze  --league-id <id> --team PennerBoy

# Cross-league portfolio for a manager -> CLI + out/portfolio.html.
# --user takes a Sleeper username OR user_id; season defaults to current.
python -m dynasty_tool.cli portfolio --user PennerBoy
```

### Projections & odds — what the model is (and isn't)

`analyze` and `portfolio` project **playoff/championship odds** and **weekly
matchup win %** from a transparent roster-strength Monte-Carlo: each team's
weekly score is modeled from its **starter value** with real week-to-week
variance plus a true-talent uncertainty term (so the sim is deliberately *not*
overconfident). In the NFL offseason a pre-draft league has no schedule and no
results, so odds are a **preseason projection on a balanced schedule** and
strength-of-schedule/matchups are shown as unavailable until the schedule is
set; an in-season league uses its **real schedule** and blends live scores into
matchup odds. It is a projection of roster strength, **not** a validated
predictive model — read title% as "how strong is this roster," not a lock. All
model constants live at the top of `analysis/league_analysis.py`.

Common flags: `--basis {realized,current,point_in_time}` (default `realized`),
`--qb {1,2}` (override; otherwise derived from `roster_positions`), `--refresh`
(ignore cache), `--cache-dir`, `--out-dir`. The QB format and the number of
teams are derived from the league itself. Outputs go to `out/`; the cache lives
in `dynasty_tool/cache/`.

## Architecture

```
dynasty_tool/
  ingest/   sleeper_client · chain · identity · trades · drafts · context
  value/    provider (interface) · current_provider · historical_provider (stub) · assets
  analysis/ common · trade_eval · trade_calc (interactive calculator core) · value_flow
            optimizer · league_analysis (rankings/odds/waivers/matchups/SOS)
            dashboard · portfolio
  cli.py    argparse entrypoint (ingest / evaluate / flow / optimize / analyze / portfolio)
  cache/    on-disk JSON + CSV cache (one fetch, replayed)
  tests/    R1–R4 + join-coverage + analyzer suite (hermetic, no network)
```

Ingestion and valuation are built once and shared; the three modules never
re-implement them.

## Correctness guarantees (the non-obvious bugs this avoids)

- **R1 — identity is `user_id`, never display name.** Names drift across seasons
  (`LACrams → TheGrimGaffer`); all grouping keys on `user_id`, and every account
  with >1 name is listed as an alias warning. Near-identical names with
  different ids (`daAverageJoes` vs `DaAverageJoes65`) are **never** merged.
- **R2 — pick ownership fields are roster ids.** `owner_id` /
  `previous_owner_id` / `roster_id` in a trade are resolved through **that
  season's** `roster_id → user_id` map (which is *not* stable across seasons).
- **R3 — value basis is explicit and point-in-time is guarded** (see above).
- **R4 — undrafted future picks are a distinct case.** A future pick with a
  known slot is valued by slot; with an undetermined slot it is flagged
  `UNRESOLVED_FUTURE_PICK` — **never dropped or silently zeroed**.
- **R5 — values are re-read from raw data at report time**, not carried forward
  from an earlier summary.
- **Join coverage is reported, not hidden.** Sleeper players are joined to
  values via `sleeper_id → fantasypros_id → fp_id` (never by name). Unmatched
  players (IDP, retired) are valued 0 **and counted/reported**.

## Known properties / limitations

- **Multiple drafts per season** (inaugural startup + rookie) are disambiguated
  by trade timing: a traded pick is for the next draft occurring at/after the
  trade. Deep startup rounds not actually drafted resolve to a slot value.
- Under `realized`, a player who has since **retired** is worth 0 now — correct
  for "who won," but it means very old trades of now-irrelevant players net
  toward 0. The unmatched count surfaces this.
- **IDP** (DL/LB/DB) carry no DynastyProcess value, so IDP-heavy rosters show
  value only from offense. Reported, not hidden.
- **FAAB** dollars are tracked but not converted to the dynasty-value scale
  (they are counted separately and valued 0 in points).

## Tests

```bash
python -m pytest dynasty_tool/tests -v
```

Covers R1 (alias collapse + separate-ids), R2 (per-season pick attribution),
R3 (point-in-time guard does not fall back to current values), R4 (future-pick
handling), and join-coverage reporting — all hermetic (no network).
