"""Lineup writers — turn the weekly-optimal lineup into an actual set-lineup
action per platform. Dry-run by default: nothing is written without an explicit
commit + the user's own credentials.

  * MFL     — official import API (TYPE=lineup). Supported, stable.
  * ESPN    — unofficial private API (ROSTER transaction). Experimental.
  * Sleeper — no write API; emits the plan + a deep link to the lineup screen.
"""
