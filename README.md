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
