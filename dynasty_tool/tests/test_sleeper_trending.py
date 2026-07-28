"""Hermetic tests for the Sleeper trending add/drop endpoint. No network."""
from __future__ import annotations

import pytest

from dynasty_tool.ingest.sleeper_client import SleeperClient


class _Recorder(SleeperClient):
    """Captures the path/TTL the trending helper asks for, without a session."""

    def __init__(self):
        self.seen: list[tuple[str, float]] = []
        self.reply: list = []

    def _get(self, path, max_age_hours=None):
        self.seen.append((path, max_age_hours))
        return self.reply


@pytest.fixture
def rec():
    return _Recorder()


def test_add_path_and_params(rec):
    rec.trending("add", lookback_hours=24, limit=25)
    path, ttl = rec.seen[0]
    assert path == "players/nfl/trending/add?lookback_hours=24&limit=25"
    assert ttl == 0.5          # short on purpose: immediacy is the point


def test_drop_variants_normalise(rec):
    for word in ("drop", "drops", "DROP", "dropped"):
        rec.trending(word)
    assert all("/trending/drop?" in p for p, _ in rec.seen)


def test_anything_else_is_treated_as_add(rec):
    rec.trending("adds")
    rec.trending("")
    assert all("/trending/add?" in p for p, _ in rec.seen)


def test_numeric_args_are_coerced(rec):
    rec.trending("add", lookback_hours="6", limit="40")
    assert rec.seen[0][0].endswith("lookback_hours=6&limit=40")


def test_none_response_becomes_empty_list(rec):
    rec.reply = None
    assert rec.trending("add") == []


def test_windows_get_distinct_cache_keys(rec):
    """The disk-cache key is derived from the path, so a 6h and a 24h window
    must not collide on one file."""
    from dynasty_tool.cache import _safe_key
    rec.trending("add", lookback_hours=6)
    rec.trending("add", lookback_hours=24)
    keys = {_safe_key("sleeper__" + p.strip("/").replace("/", "__") + ".json")
            for p, _ in rec.seen}
    assert len(keys) == 2
