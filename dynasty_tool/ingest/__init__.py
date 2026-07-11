"""Ingestion layer: Sleeper API access, chain walking, identity/roster maps,
trade normalization, and draft-pick resolution.

All grouping keys on ``user_id`` (R1). All pick ownership fields are resolved
through per-season ``roster_id -> user_id`` maps (R2).
"""
