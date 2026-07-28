"""Hermetic tests for the CFBD HTTP layer. No network.

This client shipped with zero coverage — the injectable ``session`` seam existed
precisely for these tests and they were never written, which is how the empty-
response caching problem went unnoticed.
"""
from __future__ import annotations

import json

import pytest
import requests

from dynasty_tool.cache import DiskCache
from dynasty_tool.ingest.cfbd_client import CfbdClient


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload) if payload is not None else ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, payload=None, status=200):
        self.payload = payload if payload is not None else []
        self.status = status
        self.calls: list[dict] = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params or {},
                           "headers": headers or {}, "timeout": timeout})
        return FakeResponse(self.payload, self.status)


def client(tmp_path, session, key="k123"):
    return CfbdClient(key, DiskCache(tmp_path), session=session)


# ------------------------------------------------------------------ auth ----
def test_missing_key_is_refused_at_construction(tmp_path):
    with pytest.raises(RuntimeError, match="no CFBD API key"):
        CfbdClient("", DiskCache(tmp_path))


def test_bearer_header_and_base_url(tmp_path):
    s = FakeSession([{"id": 1}])
    client(tmp_path, s).player_usage(2024)
    call = s.calls[0]
    assert call["headers"]["Authorization"] == "Bearer k123"
    assert call["url"] == "https://api.collegefootballdata.com/player/usage"


@pytest.mark.parametrize("status,match", [(401, "rejected the API key"),
                                          (429, "rate limit")])
def test_auth_and_rate_limit_give_readable_errors(tmp_path, status, match):
    s = FakeSession([], status=status)
    with pytest.raises(RuntimeError, match=match):
        client(tmp_path, s).player_usage(2024)


def test_other_http_errors_propagate(tmp_path):
    with pytest.raises(requests.HTTPError):
        client(tmp_path, FakeSession([], status=500)).player_usage(2024)


# ------------------------------------------------------------- endpoints ----
def test_endpoint_paths_and_params(tmp_path):
    s = FakeSession([{"x": 1}])
    c = client(tmp_path, s)
    c.player_usage(2024)
    c.recruits(2021)
    c.draft_picks(2022)
    c.player_season_stats(2023, "receiving")
    got = [(x["url"].rsplit("/", 2)[-2] + "/" + x["url"].rsplit("/", 1)[-1],
            x["params"]) for x in s.calls]
    assert got[0] == ("player/usage", {"year": 2024, "excludeGarbageTime": "true"})
    assert got[1] == ("recruiting/players", {"year": 2021})
    assert got[2] == ("draft/picks", {"year": 2022})
    assert got[3] == ("player/season", {"year": 2023, "category": "receiving"})


def test_exclude_garbage_time_serialises_as_a_json_bool_string(tmp_path):
    s = FakeSession([{"x": 1}])
    client(tmp_path, s).player_usage(2024, exclude_garbage_time=False)
    assert s.calls[0]["params"]["excludeGarbageTime"] == "false"


# ----------------------------------------------------------------- cache ----
def test_second_call_is_served_from_disk(tmp_path):
    s = FakeSession([{"id": 1}])
    cache = DiskCache(tmp_path)
    for _ in range(2):
        CfbdClient("k", cache, session=s).player_usage(2024)
    assert len(s.calls) == 1


def test_different_years_do_not_share_a_cache_entry(tmp_path):
    s = FakeSession([{"id": 1}])
    cache = DiskCache(tmp_path)
    CfbdClient("k", cache, session=s).player_usage(2024)
    CfbdClient("k", cache, session=s).player_usage(2023)
    assert len(s.calls) == 2


def test_an_empty_response_is_briefly_cached_then_retried(tmp_path):
    """CFBD returning [] usually means 'that season isn't populated yet', not a
    fact. It used to be pinned for 24h (30 days for recruits), freezing a blank
    page with no way to bust it. Now it gets a short life: reused inside the
    window, refetched after."""
    s = FakeSession([])
    cache = DiskCache(tmp_path)

    c1 = CfbdClient("k", cache, session=s)
    c1.player_usage(2024)
    c1.player_usage(2024)
    assert len(s.calls) == 1              # inside the window: reused

    c2 = CfbdClient("k", cache, session=s)
    c2.EMPTY_TTL_HOURS = 0                # window elapsed
    c2.player_usage(2024)
    assert len(s.calls) == 2              # refetched rather than serving stale []


def test_an_elapsed_empty_window_does_not_evict_real_data(tmp_path):
    """The short empty-TTL must key off emptiness, not age — a populated
    response still honours the normal 24h cache."""
    s = FakeSession([{"id": 1}])
    cache = DiskCache(tmp_path)
    c = CfbdClient("k", cache, session=s)
    c.EMPTY_TTL_HOURS = 0
    c.player_usage(2024)
    c.player_usage(2024)
    assert len(s.calls) == 1


def test_a_non_empty_response_is_still_cached(tmp_path):
    s = FakeSession([{"id": 1}])
    cache = DiskCache(tmp_path)
    for _ in range(3):
        CfbdClient("k", cache, session=s).player_usage(2024)
    assert len(s.calls) == 1


def test_none_payload_becomes_an_empty_list(tmp_path):
    s = FakeSession(None)
    assert client(tmp_path, s).player_usage(2024) == []


def test_fetch_count_tracks_real_requests(tmp_path):
    s = FakeSession([{"id": 1}])
    c = client(tmp_path, s)
    c.player_usage(2024)
    c.player_usage(2024)      # cached
    assert c.fetch_count == 1
