"""FantasyPros-style "set my lineup" bookmarklets.

A bookmarklet runs inside your already-logged-in tab, so it needs NO credentials
from us: MFL's lineup import and ESPN's roster transaction both authenticate off
the session cookie the browser already holds. We embed the optimal lineup and let
the site do the write, behind a confirm() dialog.

  * MFL  — in-session form POST to import?TYPE=lineup. Verified mechanism.
  * ESPN — same-origin fetch of the current roster + POST a ROSTER transaction.
           Experimental (unofficial private API), untested until a live league.
"""
from __future__ import annotations

import html
import json


def _js_str(s: str) -> str:
    """Escape a Python string for embedding in a single-quoted JS literal."""
    return (s.replace("\\", "\\\\").replace("'", "\\'")
            .replace("\r", "").replace("\n", "\\n"))


# -- MFL ---------------------------------------------------------------------

def mfl_import_url(host: str, year, league_id, week, starter_ids) -> str:
    ids = ",".join(str(s) for s in starter_ids)
    return (f"https://{host}/{year}/import?TYPE=lineup&L={league_id}"
            f"&W={int(week)}&STARTERS={ids}")


def mfl_bookmarklet(host: str, year, league_id, week, starter_ids, summary: str) -> str:
    action = f"https://{host}/{year}/import"
    ids = ",".join(str(s) for s in starter_ids)
    return (
        "javascript:(function(){"
        "var S='" + _js_str(summary) + "';if(!confirm(S))return;"
        "var P={TYPE:'lineup',L:'" + str(league_id) + "',W:'" + str(int(week)) +
        "',STARTERS:'" + ids + "'};"
        "var f=document.createElement('form');f.method='POST';"
        "f.action='" + action + "';"
        "for(var k in P){var i=document.createElement('input');i.type='hidden';"
        "i.name=k;i.value=P[k];f.appendChild(i);}"
        "document.body.appendChild(f);f.submit();})();"
    )


# -- ESPN (experimental) -----------------------------------------------------

def espn_bookmarklet(year, league_id, team_id, targets, week, summary: str) -> str:
    """targets: list of (playerId, toLineupSlotId). Reads current slots in-session."""
    T = json.dumps([[int(p), int(s)] for p, s in targets], separators=(",", ":"))
    base = (f"https://lm-api-writes.fantasy.espn.com/apis/v3/games/ffl/seasons/"
            f"{year}/segments/0/leagues/{league_id}")
    tid = str(int(team_id))
    return (
        "javascript:(function(){"
        "var S='" + _js_str(summary) + "';if(!confirm(S))return;"
        "var T=" + T + ";var W=" + str(int(week)) + ";"
        "fetch('" + base + "?view=mRoster&scoringPeriodId='+W,{credentials:'include'})"
        ".then(function(r){return r.json();}).then(function(d){"
        "var tm=(d.teams||[]).filter(function(t){return t.id==" + tid + ";})[0]||{};"
        "var cur={};(((tm.roster||{}).entries)||[]).forEach(function(e){cur[e.playerId]=e.lineupSlotId;});"
        "var items=T.map(function(x){return {playerId:x[0],type:'LINEUP',"
        "fromLineupSlotId:(x[0] in cur?cur[x[0]]:20),toLineupSlotId:x[1]};});"
        "var body={isLeagueManager:false,teamId:" + tid + ",type:'ROSTER',"
        "scoringPeriodId:W,executionType:'EXECUTE',items:items};"
        "return fetch('" + base + "/transactions/',{method:'POST',credentials:'include',"
        "headers:{'Content-Type':'application/json','x-fantasy-source':'kona',"
        "'x-fantasy-platform':'kona-PROD'},body:JSON.stringify(body)});"
        "}).then(function(r){return r.json();}).then(function(o){"
        "alert('ESPN response: '+JSON.stringify(o).slice(0,300));})"
        ".catch(function(e){alert('ESPN set failed: '+e);});})();"
    )


# -- installer page ----------------------------------------------------------

_CSS = """
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;background:#f4f5f7;color:#1a1a1a}
.wrap{max-width:720px;margin:0 auto;padding:28px 20px 70px}
h1{font-size:21px;margin:0 0 4px}.meta{color:#666;font-size:13px;margin-bottom:20px}
.drag{display:inline-block;background:#1b2a4a;color:#fff;padding:12px 20px;border-radius:10px;font-size:15px;font-weight:600;text-decoration:none;box-shadow:0 2px 6px rgba(0,0,0,.15)}
.drag:hover{background:#26386080}
.card{background:#fff;border:1px solid #e6e8ec;border-radius:12px;padding:18px;margin:16px 0;box-shadow:0 1px 3px rgba(0,0,0,.04)}
ol{margin:0;padding-left:20px;line-height:1.7;font-size:14px}
ul.lineup{list-style:none;margin:8px 0 0;padding:0;font-size:14px}
ul.lineup li{padding:4px 0;border-bottom:1px solid #f2f3f5;display:flex;justify-content:space-between}
.slot{color:#888;font-size:12px;text-transform:uppercase;width:70px;display:inline-block}
.warn{background:#fff7ed;border:1px solid #fed7aa;color:#9a3412;border-radius:8px;padding:10px 12px;font-size:13px;margin-top:10px}
code{background:#eef0f3;padding:1px 5px;border-radius:4px;font-size:12px;word-break:break-all}
textarea{width:100%;height:70px;font-size:11px;font-family:monospace;margin-top:8px}
"""


def render_installer(platform: str, team: str, week, label: str, bookmarklet: str,
                     lineup_rows: list[tuple], notes: list[str]) -> str:
    esc = html.escape
    rows = "".join(
        f"<li><span><span class=slot>{esc(str(slot))}</span>{esc(name)} "
        f"<span style='color:#888'>({esc(pos)})</span></span>"
        f"<span>{esc(str(proj))}</span></li>"
        for slot, name, pos, proj in lineup_rows
    )
    warn = "".join(f"<div class=warn>{esc(n)}</div>" for n in notes)
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width, initial-scale=1'>"
        f"<title>Set Lineup — {esc(team)}</title><style>{_CSS}</style></head><body><div class=wrap>"
        f"<h1>⚡ One-click lineup — {esc(team)}</h1>"
        f"<div class=meta>{esc(platform)} · Week {esc(str(week))} · drag the button to your bookmarks bar</div>"
        "<div class=card><b>Install (once):</b><ol>"
        "<li>Show your browser's bookmarks bar (Ctrl/Cmd-Shift-B).</li>"
        f"<li><b>Drag</b> this button up to the bar → <a class=drag href=\"{esc(bookmarklet)}\">{esc(label)}</a></li>"
        "<li>Each week: open your league (logged in), click the bookmark, confirm. Done.</li>"
        "</ol></div>"
        f"<div class=card><b>This week's optimal lineup:</b><ul class=lineup>{rows}</ul>{warn}</div>"
        "<div class=card><b>If dragging doesn't work,</b> make a new bookmark and paste this as the URL:"
        f"<textarea readonly onclick='this.select()'>{esc(bookmarklet)}</textarea></div>"
        "</div></body></html>"
    )
