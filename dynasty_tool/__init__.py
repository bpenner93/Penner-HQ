"""dynasty_tool — Sleeper dynasty league value tool.

A single ingestion + valuation core with three analysis modules on top:
  1. Trade Evaluator          (analysis.trade_eval)
  2. League Value-Flow        (analysis.value_flow)
  3. Roster/Window Optimizer  (analysis.optimizer)

See README.md for the value-basis limitation (point-in-time is unavailable
without a supplied historical source).
"""

__version__ = "0.1.0"
