# Penner HQ — dynasty dashboard (Streamlit Cloud deploy bundle)

A dark, FantasyPros-"My Playbook"-style dashboard for Sleeper **dynasty** leagues:
power rankings, playoff/title odds, positional-strength radar, contention map,
player profiles, trades, waivers, and the value-flow funnel.

This folder is a **self-contained, public, dynasty-only** build — it fetches
public DynastyProcess values live and contains **no private DFS-model data**.
(The full local app adds MFL/ESPN + weekly start-sit, which use your private
projection files and are intentionally left out of this public bundle.)

## Deploy to Streamlit Community Cloud (one time)

```bash
cd penner_hq
git init && git add . && git commit -m "Penner HQ"
git branch -M main
git remote add origin https://github.com/bpenner93/Penner-HQ.git   # create this repo first (empty)
git push -u origin main
```

Then at **https://share.streamlit.io** → **Create app** → pick the `Penner-HQ`
repo, branch `main`, **main file `streamlit_app.py`** → **Deploy**.

**Turn on the password gate** (required, or the public URL is open): in the app's
**Settings → Secrets**, paste the one line from your local
`.streamlit/secrets.toml` (the `APP_PW_SHA256 = "…"` line). Save. Now the app
asks for your password before showing anything. Then add the URL to your phone.

## Beat Feed & Movers

**📰 Beat Feed** — NFL beat writers for all 32 teams plus the national wire, with
a **My Players** tab that groups everything by the players you actually roster,
across every dynasty league you're in.

X's API is paywalled (~$200/mo, no usable free read tier), so the feed runs on
free sources by default: RSS, Google News scoped per named reporter, and
Bluesky's public API. Sources live in `dynasty_tool/ingest/feeds.json` as **data,
not code** — a dead feed is a one-line JSON edit, and the page's *source health*
panel names exactly which ones failed so a short feed is never mistaken for a
healthy one. Expect to prune a few on first run; none of the URLs could be
verified from the sandbox this was built in.

**📈 Movers** — who the market is buying and selling, from two signals kept
separate on purpose:
- *Dynasty values* (DynastyProcess / FantasyPros expert consensus, weekly) — the
  same scale the Trade Calculator uses, so a riser is directly tradeable. History
  comes from the public repo's own git commits; nothing is stored locally.
- *KTC* (crowd trade votes, daily) — the bullish/bearish signal. Unofficial
  (KTC has no public API), gated behind a button, and isolated so a break there
  leaves the expert tab working.
- *Draft classes* — past classes scored by what they actually produced, future
  classes priced by what the market charges for their picks.
- *Devy board* — college prospects ranked on **early** production (usage share
  discounted by how far into a career it happened), joined to recruiting
  pedigree, and rolled up into "the 2028 class is carrying X elite, Y good".
  A production screen, not a scouting report — no free consensus devy board
  exists, so it knows nothing about traits, injuries, or scheme. Pair it with
  the pick pricing: a class the board likes that the market hasn't priced yet
  is when to buy picks.

### Optional keys (all features work without them)

Add to **Settings → Secrets** on Streamlit Cloud, or `.streamlit/secrets.toml`
locally (gitignored — never commit a key):

```toml
ANTHROPIC_API_KEY  = "…"   # ✨ on-demand article summaries + roster digest
TWITTERAPI_IO_KEY  = "…"   # real beat-reporter tweets via twitterapi.io
CFBD_API_KEY       = "…"   # devy board (free: collegefootballdata.com/profile)
```

Without them the feed still works — the ✨ buttons hide themselves and X sources
are skipped. Summaries are disk-cached, so the same article is never paid for
twice.

## Notes
- The gate stores a **SHA-256 hash**, not your password — the plaintext lives
  nowhere in this repo or on the server. `.streamlit/secrets.toml` is
  **gitignored** and never pushed; only the hash goes into Streamlit Cloud's
  encrypted secrets. Without the secret set, the app is open (local dev).
- Set your **Sleeper username** in the sidebar (defaults to `PennerBoy`); it
  auto-discovers your dynasty (type-2) leagues.
- First load is slower (cold fetch of the Sleeper chain + DynastyProcess CSVs),
  then cached.
- To refresh after code changes: re-sync this folder from `dynasty_tool/` and
  `git push` — Streamlit Cloud redeploys automatically.
