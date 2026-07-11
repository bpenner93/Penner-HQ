"""Streamlit Community Cloud entry point (auto-detected filename).

Forces the public, dynasty-only build (PENNER_HQ_CLOUD=1 -> Sleeper leagues
only, no private DFS-model data) and runs the real app fresh on every rerun.
"""
import os
import runpy
import sys
from pathlib import Path

os.environ.setdefault("PENNER_HQ_CLOUD", "1")
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# run_path re-executes the app top-to-bottom each Streamlit rerun (unlike import,
# which would run the body only once).
runpy.run_path(str(ROOT / "dynasty_tool" / "app.py"), run_name="__main__")
