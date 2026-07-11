"""Tests for the lineup writers (dry-run construction). No network."""
from __future__ import annotations

from ..write.plan import LineupPlan, PlanStarter
from ..write.mfl_writer import MflLineupWriter
from ..write.sleeper_deeplink import sleeper_lineup_url
from ..write.espn_writer import EspnLineupWriter, ESPN_SLOT
from ..write.bookmarklet import (mfl_bookmarklet, mfl_import_url, espn_bookmarklet,
                                 render_installer)


class _FakeMfl:
    host = "www45.myfantasyleague.com"

    def players_detail(self, year, lid, ids):
        return {"0515": {"name": "Seahawks D/ST", "position": "Def"},
                "999": {"name": "Spare Kicker", "position": "PK"}}


def _plan():
    return LineupPlan("Me", 10, True, [
        PlanStarter("QB", "g_lamar", "Lamar Jackson", "QB", 22.4, "@ MIN"),
        PlanStarter("K", "g_myers", "Jason Myers", "K", 0.0),   # analyzer already filled K
    ])


def test_mfl_writer_maps_ids_and_fills_only_deficit():
    g2m = {"g_lamar": "13593", "g_myers": "12437"}
    w = MflLineupWriter(_FakeMfl(), "2025", "65695", None, g2m)
    roster = ["13593", "12437", "0515", "999"]     # incl DST + a spare kicker
    sub = w.build(_plan(), roster, ["QB", "K", "DST"])

    assert sub.starter_ids.count("12437") == 1     # K present once (no duplicate)
    assert "0515" in sub.starter_ids               # DST filled from roster
    assert "999" not in sub.starter_ids            # K deficit is 0 -> spare kicker NOT added
    assert not sub.missing
    assert sub.params["STARTERS"] == ",".join(sub.starter_ids)
    assert sub.params["TYPE"] == "lineup" and sub.params["W"] == 10


def test_mfl_writer_flags_missing_required_slot():
    g2m = {"g_lamar": "13593", "g_myers": "12437"}
    w = MflLineupWriter(_FakeMfl(), "2025", "65695", None, g2m)
    roster = ["13593", "12437"]                    # no DST on roster
    sub = w.build(_plan(), roster, ["QB", "K", "DST"])
    assert "DST" in sub.missing


def test_sleeper_deeplink_is_the_team_screen():
    assert sleeper_lineup_url("123") == "https://sleeper.com/leagues/123/team"


def test_espn_targets_correct_slot_ids_and_gates_commit():
    w = EspnLineupWriter("2025", "99", team_id=3)
    sub = w.build(_plan(), week=10, current_slot=None)
    slots = {name: sid for _pid, name, _pos, _slot, sid in sub.targets}
    assert slots["Lamar Jackson"] == ESPN_SLOT["QB"]      # QB -> 0
    assert sub.ready is False                              # no current slots -> commit disabled
    assert w.commit(sub, "swid", "s2")["error"]           # refuses without current slots


def test_mfl_bookmarklet_embeds_starters_and_confirms():
    bm = mfl_bookmarklet("www45.myfantasyleague.com", "2025", "65695", 10,
                         ["13593", "0515"], "Set wk10?")
    assert bm.startswith("javascript:")
    assert "confirm(" in bm
    assert "STARTERS:'13593,0515'" in bm
    assert "TYPE:'lineup'" in bm and "65695" in bm


def test_mfl_import_url_format():
    assert (mfl_import_url("h", "2025", "65695", 10, ["1", "2"])
            == "https://h/2025/import?TYPE=lineup&L=65695&W=10&STARTERS=1,2")


def test_bookmarklet_escapes_apostrophes():
    bm = mfl_bookmarklet("h", "2025", "1", 1, ["9"], "QB: Ja'Marr Chase")
    assert "Ja\\'Marr" in bm          # apostrophe escaped for the JS string literal


def test_espn_bookmarklet_targets_and_endpoint():
    bm = espn_bookmarklet("2025", "99", 3, [(101, 0), (202, 2)], 10, "go?")
    assert bm.startswith("javascript:")
    assert "[[101,0],[202,2]]" in bm
    assert "/transactions/" in bm and "credentials:'include'" in bm


def test_installer_html_has_draggable_bookmarklet_and_escapes_team():
    out = render_installer("MFL", "Brandon & Cody", 10, "Set", "javascript:void(0)",
                           [("QB", "Lamar Jackson", "QB", "22.4")], notes=["heads up"])
    assert "javascript:void(0)" in out
    assert "Brandon &amp; Cody" in out and "Lamar Jackson" in out and "heads up" in out
