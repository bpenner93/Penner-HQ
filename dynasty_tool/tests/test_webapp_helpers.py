"""Tests for the Streamlit app's pure helpers. No network, no Streamlit."""
from __future__ import annotations

import pandas as pd

from ..analysis.optimizer import RosterValue
from .. import webapp_helpers as wh


def test_cdn_urls():
    assert wh.headshot_url("4046") == "https://sleepercdn.com/content/nfl/players/thumb/4046.jpg"
    assert wh.headshot_url("4046", thumb=False).endswith("/players/4046.jpg")
    assert wh.team_logo_url("KC") == "https://sleepercdn.com/images/team_logos/nfl/kc.png"
    assert wh.team_logo_url(None) is None
    assert wh.avatar_url(None) is None


def test_meta_subset_keeps_only_requested_and_known_keys():
    meta = {"1": {"first_name": "A", "position": "QB", "junk": "x", "search_rank": 5},
            "2": {"first_name": "B"}, "3": "notadict"}
    out = wh.meta_subset(meta, ["1", "3", "999"])
    assert set(out) == {"1"}
    assert out["1"] == {"first_name": "A", "position": "QB"}  # junk keys dropped


def test_ecr_map_joins_sleeper_to_fp():
    players = pd.DataFrame([
        {"player": "Ja'Marr Chase", "pos": "WR", "team": "CIN", "fp_id": 19788,
         "ecr_1qb": 1.1, "ecr_2qb": 6.1, "ecr_pos": 1.1, "value_1qb": 10232,
         "value_2qb": 9098, "draft_year": 2021},
    ])
    ids = pd.DataFrame([
        {"sleeper_id": 7564, "fantasypros_id": 19788},
        {"sleeper_id": 9999, "fantasypros_id": None},     # no fp -> skipped
    ])
    m1 = wh.ecr_map(players, ids, qb_format=1)
    assert m1["7564"]["ecr"] == 1.1 and m1["7564"]["value"] == 10232
    m2 = wh.ecr_map(players, ids, qb_format=2)
    assert m2["7564"]["ecr"] == 6.1 and m2["7564"]["value"] == 9098
    assert "9999" not in m1


def test_radar_percentiles_rank_within_league():
    def rv(uid, qb, wr):
        r = RosterValue(user_id=uid, display=uid)
        r.starter_value_by_pos = {"QB": qb, "WR": wr}
        return r
    rosters = {"a": rv("a", 100, 10), "b": rv("b", 200, 30), "c": rv("c", 300, 20)}
    p = wh.radar_percentiles(rosters, "c")
    assert p["QB"] == 1.0          # best QB room
    assert 0 < p["WR"] < 1         # middle WR room
    assert wh.radar_percentiles(rosters, "zz") == {}


def test_fmt_k():
    assert wh.fmt_k(43872) == "43.9k"
    assert wh.fmt_k(432) == "432"


def test_extra_leagues_roundtrip(tmp_path):
    p = tmp_path / "leagues.json"
    assert wh.load_extra_leagues(p) == []                  # missing file -> []
    wh.save_extra_leagues(p, wh.default_extra_leagues())
    back = wh.load_extra_leagues(p)
    assert back and back[0]["platform"] == "mfl" and back[0]["league_id"] == "65695"
    p.write_text("{broken", encoding="utf-8")              # corrupt -> [] not crash
    assert wh.load_extra_leagues(p) == []
