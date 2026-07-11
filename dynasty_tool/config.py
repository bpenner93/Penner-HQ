"""Central configuration: paths, thresholds, and small shared constants.

Nothing here reaches the network. Paths default to living beside the package
(cache/) and under the current working directory (out/), and both can be
overridden by the CLI.
"""
from __future__ import annotations

import sys
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PKG_ROOT.parent  # the surrounding dfs_nfl project

# Redraft value basis: the user's own season projection engine output.
# (dynasty value would wrongly age-discount redraft studs like Kelce/Kupp.)
SEASON_RANKINGS = PROJECT_ROOT / "data" / "processed" / "season_rankings.parquet"

# On-disk cache for every Sleeper endpoint + the DynastyProcess value CSVs.
CACHE_DIR = PKG_ROOT / "cache"
# Structured outputs (CSVs, PNG charts) land here.
OUT_DIR = Path.cwd() / "out"

# --- refresh policy (hours) -------------------------------------------------
# Sleeper's players/nfl blob and the DynastyProcess CSVs update ~daily; league
# history is effectively immutable, so it is cached indefinitely until --refresh.
PLAYERS_MAX_AGE_HOURS = 24
VALUES_MAX_AGE_HOURS = 24

# --- trade fairness thresholds (fraction of the larger side) ----------------
FAIRNESS_EVEN = 0.05    # within  5%      -> "even"
FAIRNESS_SLIGHT = 0.15  # 5%-15%          -> "slight edge"
#                         > 15%           -> "lopsided"

# --- value-flow flags -------------------------------------------------------
CONCENTRATION_FLAG = 0.50  # >50% of an account's net flow on one partner -> flag
SAME_DAY_MS = 24 * 3600 * 1000  # window for "same-day burst" clustering

# --- valuation --------------------------------------------------------------
# Depth (players beyond the startable slots at their position) is real but
# illiquid; weight it down when computing "win-now" starter value.
DEPTH_WEIGHT = 0.30

# Sleeper roster_positions tokens that are *startable* (not bench/IR/taxi).
NON_STARTER_SLOTS = {"BN", "IR", "TAXI"}
FLEX_ELIGIBLE = {"RB", "WR", "TE"}
SUPERFLEX_ELIGIBLE = {"QB", "RB", "WR", "TE"}
IDP_POSITIONS = {"DL", "LB", "DB", "DE", "DT", "CB", "S", "IDP_FLEX"}


def force_utf8_stdout() -> None:
    """Windows consoles default to cp1252 and crash on emoji team names.

    Call once at CLI entry. Idempotent and safe if stdout can't be reconfigured.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass
