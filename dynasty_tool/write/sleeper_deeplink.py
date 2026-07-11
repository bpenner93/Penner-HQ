"""Sleeper has no official write API, so we don't touch the account.

Instead we hand back the optimal lineup plus a deep link straight to the team's
lineup screen, so the manager taps Save themselves: a ~5-second, zero-risk
"button" with no session token and no ToS exposure.
"""
from __future__ import annotations


def sleeper_lineup_url(league_id: str) -> str:
    # Opens the league's team/lineup screen (web + deep-links into the mobile app).
    return f"https://sleeper.com/leagues/{league_id}/team"
