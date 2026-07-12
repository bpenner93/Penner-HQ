"""Tests for the league analyzer: matchup odds, weekly means, season sim,
schedule generation. Pure/synthetic -- no network."""
from __future__ import annotations

from ..analysis.optimizer import RosterValue, RPlayer, slot_eligible
from ..analysis import league_analysis as la


def test_slot_eligible_idp_uses_fantasy_positions():
    # Myles Garrett: NFL position DE, but Sleeper slot-eligibility is ['DL']
    garrett = RPlayer("1", "Myles Garrett", "DE", 0.0, 26, True, fantasy_positions=["DL"])
    assert slot_eligible("DL", garrett)           # fills DL via fantasy_positions
    assert not slot_eligible("LB", garrett)
    assert not slot_eligible("WR", garrett)
    wr = RPlayer("2", "AJ Brown", "WR", 5000, 27, True, fantasy_positions=["WR"])
    assert slot_eligible("WR", wr) and slot_eligible("FLEX", wr)
    assert not slot_eligible("QB", wr)            # WR can't fill a 1QB slot
    # no fantasy_positions -> fall back to the granular position
    rb = RPlayer("3", "JT", "RB", 6000, 25, True)
    assert slot_eligible("RB", rb) and slot_eligible("FLEX", rb) and not slot_eligible("DL", rb)


def _rosters(values):
    return {f"U{i}": RosterValue(user_id=f"U{i}", display=f"U{i}", starter_value=v)
            for i, v in enumerate(values)}


def test_matchup_win_prob_symmetry_and_monotonic():
    assert abs(la.matchup_win_prob(100, 100) - 0.5) < 1e-9
    assert la.matchup_win_prob(130, 100) > 0.5
    # complementary: P(A>B) + P(B>A) == 1
    assert abs(la.matchup_win_prob(130, 100) + la.matchup_win_prob(100, 130) - 1.0) < 1e-9


def test_team_weekly_means_track_strength():
    means = la.team_weekly_means(_rosters([60000, 30000, 10000]))
    assert means["U0"] > means["U1"] > means["U2"]


def test_round_robin_is_balanced():
    rounds = la._round_robin(6, 5)
    assert len(rounds) == 5
    assert all(len(r) == 3 for r in rounds)  # 6 teams -> 3 games/week
    # nobody plays themselves
    assert all(a != b for r in rounds for a, b in r)


def test_simulate_season_probabilities_are_coherent():
    rosters = _rosters([60000, 50000, 40000, 30000, 20000, 10000])
    sim, used_real = la.simulate_season(rosters, playoff_teams=4, reg_weeks=6, n=400, seed=1)
    assert used_real is False
    # title odds form a distribution
    assert abs(sum(s.title_pct for s in sim) - 1.0) < 0.02
    assert all(0.0 <= s.title_pct <= 1.0 for s in sim)
    # exactly `playoff_teams` make the playoffs each sim -> odds sum to that
    assert abs(sum(s.playoff_pct for s in sim) - 4.0) < 0.05
    # the strongest roster is favored over the weakest
    best = max(sim, key=lambda s: s.title_pct)
    worst = min(sim, key=lambda s: s.title_pct)
    assert best.user_id == "U0" and worst.title_pct < best.title_pct


def test_simulate_uses_real_schedule_when_given():
    rosters = _rosters([50000, 40000, 30000, 20000])
    schedule = {1: [("U0", "U1"), ("U2", "U3")], 2: [("U0", "U2"), ("U1", "U3")]}
    sim, used_real = la.simulate_season(rosters, playoff_teams=2, reg_weeks=2,
                                        schedule=schedule, n=200, seed=2)
    assert used_real is True
    assert abs(sum(s.playoff_pct for s in sim) - 2.0) < 0.05


def test_lineup_recommendation_optimal_and_close_calls():
    qb = RPlayer("1", "QB1", "QB", 5000, 25, True)
    rb1 = RPlayer("2", "RB1", "RB", 4000, 24, True)   # starts
    rb2 = RPlayer("3", "RB2", "RB", 3800, 24, True)   # bench, 5% behind RB1 -> close call
    rb3 = RPlayer("4", "RB3", "RB", 1000, 26, True)   # bench, not close
    rv = RosterValue(user_id="U", display="U", players=[qb, rb1, rb2, rb3])
    rv.starters = [("QB", qb), ("RB", rb1)]

    rec = la.lineup_recommendation(rv, ["QB", "RB", "BN", "BN"])
    assert [p.name for _, p in rec.lineup] == ["QB1", "RB1"]         # optimal lineup
    assert {p.name for p in rec.bench} == {"RB2", "RB3"}
    # RB2 (3800) is within 15% of RB1 (4000) -> flagged; RB3 is not
    assert any(ch.name == "RB2" for _, _, ch, _ in rec.close_calls)
    assert all(ch.name != "RB3" for _, _, ch, _ in rec.close_calls)


def test_playoff_race_seeds_from_current_record():
    # a fake context just rich enough for _current_standings + playoff_race
    class _FakeClient:
        def rosters(self, _lid):
            return [{"roster_id": 1, "settings": {"wins": 8, "losses": 2, "fpts": 1000}},
                    {"roster_id": 2, "settings": {"wins": 2, "losses": 8, "fpts": 800}}]

    class _Ctx:
        newest = {"league_id": "L"}
        roster_maps = {"L": {1: "A", 2: "B"}}
        client = _FakeClient()

    rosters = {"A": RosterValue(user_id="A", display="A", starter_value=50000),
               "B": RosterValue(user_id="B", display="B", starter_value=48000)}
    race, completed = la.playoff_race(_Ctx(), rosters, playoff_teams=1, reg_weeks=12,
                                      schedule=None, n=300)
    assert completed == 10
    a = next(r for r in race if r.user_id == "A")
    b = next(r for r in race if r.user_id == "B")
    # A is 8-2 with a comparable roster -> should project more wins & better odds than 2-8 B
    assert a.proj_wins > b.proj_wins
    assert a.playoff_pct > b.playoff_pct


def test_lineup_weekly_projection_overrides_season_value():
    # Season value says start A; the weekly DFS projection says start B this week.
    a = RPlayer("1", "QB-A", "QB", 5000, 25, True)   # high season value
    b = RPlayer("2", "QB-B", "QB", 1000, 25, True)   # low season value
    rv = RosterValue(user_id="U", display="U", players=[a, b])
    rv.starters = [("QB", a)]                         # season-optimal starts A
    weekly = {"1": 9.0, "2": 24.0}                    # but B out-projects A this week
    rec = la.lineup_recommendation(rv, ["QB", "BN"], weekly=weekly, week=7,
                                   opp={"2": "@ BUF"})
    assert rec.weekly and rec.week == 7
    assert [p.name for _, p in rec.lineup] == ["QB-B"]   # weekly re-fill flips the start
    assert rec.score["2"] == 24.0 and rec.opp["2"] == "@ BUF"
