#!/usr/bin/env python3
"""Generate a football fixture calendar (.ics) with live league table context.

Fixtures, kickoff times and scores come from the public Fantasy Premier League
API, which needs no API key:
  - /api/bootstrap-static/  team list, gameweeks
  - /api/fixtures/          all 380 fixtures

The league table is computed from finished fixtures rather than read from a
standings endpoint, so it always agrees with the fixtures in the calendar.

Any Premier League team can be the subject. See README for the limits of that.
"""

import argparse
import datetime as dt
import json
import pathlib
import re
import sys
import tomllib
import urllib.request

FPL = "https://fantasy.premierleague.com/api"
UID_DOMAIN = "soccer-team-calendar-sync"
COMPETITION = "Premier League"
TEAMS_IN_LEAGUE = 20
GAMES_IN_SEASON = 38

# The FPL API carries no venue data, so home grounds are mapped by team name.
# Promoted clubs need an entry each August or their location shows as TBC.
VENUES = {
    "Arsenal": "Emirates Stadium, London",
    "Aston Villa": "Villa Park, Birmingham",
    "Bournemouth": "Vitality Stadium, Bournemouth",
    "Brentford": "Gtech Community Stadium, London",
    "Brighton": "American Express Stadium, Brighton",
    "Chelsea": "Stamford Bridge, London",
    "Coventry City": "Coventry Building Society Arena, Coventry",
    "Crystal Palace": "Selhurst Park, London",
    "Everton": "Hill Dickinson Stadium, Liverpool",
    "Fulham": "Craven Cottage, London",
    "Hull City": "MKM Stadium, Hull",
    "Ipswich Town": "Portman Road, Ipswich",
    "Leeds": "Elland Road, Leeds",
    "Liverpool": "Anfield, Liverpool",
    "Man City": "Etihad Stadium, Manchester",
    "Man Utd": "Old Trafford, Manchester",
    "Newcastle": "St James' Park, Newcastle upon Tyne",
    "Nott'm Forest": "The City Ground, Nottingham",
    "Spurs": "Tottenham Hotspur Stadium, London",
    "Sunderland": "Stadium of Light, Sunderland",
}

# Points and goal-difference swing applied to the home team for each result.
# Goal difference moves by a single goal as a minimal assumption.
RESULTS = {"Win": (3, 0, 1), "Draw": (1, 1, 0), "Loss": (0, 3, -1)}


def fetch(path):
    req = urllib.request.Request(
        f"{FPL}{path}", headers={"User-Agent": f"Mozilla/5.0 ({UID_DOMAIN})"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def resolve_team(boot, wanted):
    """Match a team by full name, FPL short code, or unambiguous substring."""
    teams = boot["teams"]
    needle = wanted.strip().lower()

    for t in teams:
        if needle in (t["name"].lower(), t["short_name"].lower()):
            return t["id"], t["name"]

    partial = [t for t in teams if needle in t["name"].lower()]
    if len(partial) == 1:
        return partial[0]["id"], partial[0]["name"]

    known = ", ".join(sorted(t["name"] for t in teams))
    if len(partial) > 1:
        options = ", ".join(sorted(t["name"] for t in partial))
        raise SystemExit(f"'{wanted}' matches more than one team: {options}")
    raise SystemExit(f"No team matching '{wanted}'. Available: {known}")


def build_table(fixtures, team_ids):
    """Points table from all finished fixtures."""
    table = {tid: {"p": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0, "pts": 0}
             for tid in team_ids}
    for f in fixtures:
        if not f["finished"]:
            continue
        pairs = (
            (f["team_h"], f["team_h_score"], f["team_a_score"]),
            (f["team_a"], f["team_a_score"], f["team_h_score"]),
        )
        for tid, gf, ga in pairs:
            row = table[tid]
            row["p"] += 1
            row["gf"] += gf
            row["ga"] += ga
            if gf > ga:
                row["w"] += 1
                row["pts"] += 3
            elif gf == ga:
                row["d"] += 1
                row["pts"] += 1
            else:
                row["l"] += 1
    return table


def rank(table):
    """Return {team_id: position}, ordered by points, then GD, then goals for."""
    order = sorted(
        table.items(),
        key=lambda kv: (-kv[1]["pts"], -(kv[1]["gf"] - kv[1]["ga"]), -kv[1]["gf"]),
    )
    return {tid: i for i, (tid, _) in enumerate(order, start=1)}


def ordinal(n):
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def zone_note(pos):
    if pos == 1:
        return "top of the league"
    if pos <= 4:
        return "Champions League places"
    if pos == 5:
        return "Europa League place"
    if pos > TEAMS_IN_LEAGUE - 3:
        return "relegation zone"
    return "midtable"


def apply_result(projected, team_a, team_b, pts_a, pts_b, gd):
    """Credit one match result to two teams in a projected table."""
    for tid, pts, swing in ((team_a, pts_a, gd), (team_b, pts_b, -gd)):
        if tid not in projected:
            continue
        projected[tid]["pts"] += pts
        projected[tid]["gf"] += max(swing, 0)
        projected[tid]["ga"] += max(-swing, 0)


def outcome_scenarios(table, team_id, opp_id):
    """Where the team sits after each result, holding other clubs' points flat."""
    scenarios = {}
    for label, (own_pts, opp_pts, gd) in RESULTS.items():
        projected = {tid: dict(row) for tid, row in table.items()}
        apply_result(projected, team_id, opp_id, own_pts, opp_pts, gd)
        ranked = rank(projected)
        scenarios[label] = {
            "pos": ranked[team_id],
            "pts": projected[team_id]["pts"],
            "table": projected,
        }
    return scenarios


def gameweek_range(table, fixtures, event, team_id, opp_id):
    """Best and worst position the team can hold once the whole gameweek is played.

    Every other match in the gameweek is set to the outcome that helps or hurts
    the team most. Because each fixture only moves its own two teams, choosing
    the best outcome fixture by fixture gives the true extreme.
    """
    others = [
        f
        for f in fixtures
        if f["event"] == event
        and not f["finished"]
        and team_id not in (f["team_h"], f["team_a"])
    ]

    extremes = {}
    for mode, own_result in (("best", "Win"), ("worst", "Loss")):
        own_pts, opp_pts, gd = RESULTS[own_result]
        projected = {tid: dict(row) for tid, row in table.items()}
        apply_result(projected, team_id, opp_id, own_pts, opp_pts, gd)
        target = projected[team_id]
        key = lambda r: (r["pts"], r["gf"] - r["ga"], r["gf"])

        for f in others:
            candidates = []
            for _, (h_pts, a_pts, swing) in RESULTS.items():
                trial = {
                    f["team_h"]: dict(projected[f["team_h"]]),
                    f["team_a"]: dict(projected[f["team_a"]]),
                }
                apply_result(trial, f["team_h"], f["team_a"], h_pts, a_pts, swing)
                rivals = sum(key(row) > key(target) for row in trial.values())
                candidates.append((rivals, trial))
            # Fewest rivals above us for the best case, most for the worst.
            chosen = (min if mode == "best" else max)(candidates, key=lambda c: c[0])[1]
            projected.update(chosen)

        extremes[mode] = rank(projected)[team_id]

    return extremes["best"], extremes["worst"]


def neighbours(table, positions, names, team_id):
    """Points gap to the clubs directly above and below."""
    by_pos = {pos: tid for tid, pos in positions.items()}
    own_pos = positions[team_id]
    own_pts = table[team_id]["pts"]
    parts = []

    for offset, word in ((-1, "behind"), (1, "ahead of")):
        tid = by_pos.get(own_pos + offset)
        if tid is None:
            continue
        gap = abs(table[tid]["pts"] - own_pts)
        unit = "pt" if gap == 1 else "pts"
        label = f"{names[tid]} ({ordinal(positions[tid])})"
        parts.append(f"level with {label}" if gap == 0 else f"{gap} {unit} {word} {label}")

    return " | ".join(parts)


def finish(lines):
    lines.append("")
    lines.append("Table and fixtures via the Fantasy Premier League API.")
    return "\n".join(lines)


def describe(fixture, all_fixtures, names, table, positions, team_id, is_next):
    """Build the event description body."""
    home, away = fixture["team_h"], fixture["team_a"]
    opp_id = away if home == team_id else home
    at_home = home == team_id
    name = names[team_id]
    lines = []

    own, opp = table[team_id], table[opp_id]
    own_pos, opp_pos = positions[team_id], positions[opp_id]

    lines.append(f"{COMPETITION}, Gameweek {fixture['event']}")
    lines.append(f"{'Home' if at_home else 'Away'} at {VENUES.get(names[home], 'TBC')}")
    lines.append("")

    if fixture["finished"]:
        hs, as_ = fixture["team_h_score"], fixture["team_a_score"]
        own_gf, own_ga = (hs, as_) if at_home else (as_, hs)
        verdict = "Won" if own_gf > own_ga else "Drew" if own_gf == own_ga else "Lost"
        lines.append(f"RESULT: {verdict} {own_gf}-{own_ga}")
        lines.append(f"{names[home]} {hs}-{as_} {names[away]}")
        return finish(lines)

    lines.append(f"TABLE (after {own['p']} played)")
    lines.append(
        f"  {name} {ordinal(own_pos)}, {own['pts']} pts, "
        f"GD {own['gf'] - own['ga']:+d} - {zone_note(own_pos)}"
    )
    lines.append(
        f"  {names[opp_id]} {ordinal(opp_pos)}, {opp['pts']} pts, "
        f"GD {opp['gf'] - opp['ga']:+d}"
    )
    gaps = neighbours(table, positions, names, team_id)
    if gaps:
        lines.append(f"  {gaps}")
    lines.append("")

    if not is_next:
        lines.append(f"{GAMES_IN_SEASON - own['p']} of {GAMES_IN_SEASON} league games still to play.")
        return finish(lines)

    scn = outcome_scenarios(table, team_id, opp_id)
    lines.append("IF THIS RESULT STANDS")
    for label in ("Win", "Draw", "Loss"):
        s = scn[label]
        note = ""
        if s["pos"] == own_pos:
            # Explain a position that holds despite the points change.
            blocker = next(
                (
                    tid
                    for tid, pos in rank(s["table"]).items()
                    if pos == s["pos"] - 1
                    and s["table"][tid]["pts"] == s["table"][team_id]["pts"]
                ),
                None,
            )
            if blocker is not None:
                note = f" - level with {names[blocker]} on points, behind on GD"
        else:
            move = own_pos - s["pos"]
            note = f" - {'up' if move > 0 else 'down'} {abs(move)}"
        lines.append(f"  {label:<5}{ordinal(s['pos']):>4}, {s['pts']} pts{note}")

    best, worst = gameweek_range(table, all_fixtures, fixture["event"], team_id, opp_id)
    lines.append("")
    if best == worst:
        lines.append(
            f"{name} finish gameweek {fixture['event']} {ordinal(best)} "
            "whatever else happens."
        )
    else:
        lines.append(
            f"Once the rest of gameweek {fixture['event']} is played, {name} can "
            f"finish as high as {ordinal(best)} or as low as {ordinal(worst)}."
        )
    return finish(lines)


def ics_escape(text):
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def fold(line):
    """RFC 5545 caps content lines at 75 octets."""
    if len(line.encode("utf-8")) <= 73:
        return line
    out, chunk = [], b""
    for ch in line:
        enc = ch.encode("utf-8")
        if len(chunk) + len(enc) > 73:
            out.append(chunk.decode("utf-8"))
            chunk = b""
        chunk += enc
    out.append(chunk.decode("utf-8"))
    return "\r\n ".join(out)


def stamp(value):
    return value.strftime("%Y%m%dT%H%M%SZ")


def build_ics(fixtures, all_fixtures, names, table, positions, team_id, next_id,
              calendar_name):
    now = dt.datetime.now(dt.timezone.utc)
    name = names[team_id]
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//{UID_DOMAIN}//{COMPETITION} Fixtures//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        fold(f"X-WR-CALNAME:{ics_escape(calendar_name)}"),
        "X-WR-TIMEZONE:Europe/London",
        fold(
            "X-WR-CALDESC:"
            + ics_escape(
                f"{name} fixtures with live league table context. "
                "Regenerated twice daily."
            )
        ),
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
    ]

    for f in fixtures:
        home, away = f["team_h"], f["team_a"]
        at_home = home == team_id
        opp = names[away if at_home else home]

        if f["finished"]:
            hs, as_ = f["team_h_score"], f["team_a_score"]
            own_gf, own_ga = (hs, as_) if at_home else (as_, hs)
            mark = "W" if own_gf > own_ga else "D" if own_gf == own_ga else "L"
            title = f"[{mark} {own_gf}-{own_ga}] {names[home]} v {names[away]}"
        else:
            title = f"{name} {'vs' if at_home else 'at'} {opp}"

        # The FPL fixture `code` is stable across the season, so re-running the
        # script updates events in place instead of creating duplicates.
        uid = f"fpl-{f['code']}@{UID_DOMAIN}"
        body = describe(
            f, all_fixtures, names, table, positions, team_id,
            is_next=(f["id"] == next_id),
        )

        lines += ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp(now)}"]

        if f["kickoff_time"]:
            start = dt.datetime.fromisoformat(
                f["kickoff_time"].replace("Z", "+00:00")
            ).astimezone(dt.timezone.utc)
            lines += [
                f"DTSTART:{stamp(start)}",
                f"DTEND:{stamp(start + dt.timedelta(hours=2))}",
            ]
        else:
            # Kickoff not yet confirmed. Park it on today so it stays visible.
            lines += [f"DTSTART;VALUE=DATE:{dt.date.today():%Y%m%d}"]
            title += " (time TBC)"

        lines += [
            fold(f"SUMMARY:{ics_escape(title)}"),
            fold(f"LOCATION:{ics_escape(VENUES.get(names[home], 'TBC'))}"),
            fold(f"DESCRIPTION:{ics_escape(body)}"),
            f"STATUS:{'CONFIRMED' if f['kickoff_time'] else 'TENTATIVE'}",
            # Fixtures shouldn't make you look busy to anyone checking availability.
            "TRANSP:TRANSPARENT",
            f"LAST-MODIFIED:{stamp(now)}",
            # Clients need a rising SEQUENCE to pick up edited events. Twelve-hour
            # granularity matches the rebuild schedule.
            f"SEQUENCE:{int(now.timestamp()) // 43200}",
            "END:VEVENT",
        ]

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def load_config(path):
    if not path.exists():
        return {}
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def main():
    root = pathlib.Path(__file__).resolve().parents[1]
    cfg = load_config(root / "config.toml")

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--team", help="Team name or FPL short code, e.g. Chelsea or CHE")
    ap.add_argument("--name", help="Calendar name shown in Google Calendar")
    ap.add_argument("-o", "--out", help="Output .ics path")
    args = ap.parse_args()

    wanted = args.team or cfg.get("team", {}).get("name") or "Chelsea"

    boot = fetch("/bootstrap-static/")
    fixtures = fetch("/fixtures/")

    team_id, team_name = resolve_team(boot, wanted)
    names = {t["id"]: t["name"] for t in boot["teams"]}

    missing = sorted(set(names.values()) - set(VENUES))
    if missing:
        print(f"WARNING: no venue mapped for {', '.join(missing)} (shows as TBC)",
              file=sys.stderr)

    table = build_table(fixtures, names)
    positions = rank(table)

    mine = [f for f in fixtures if team_id in (f["team_h"], f["team_a"])]
    mine.sort(key=lambda f: (f["kickoff_time"] or "9999", f["event"] or 99))

    upcoming = [f for f in mine if not f["finished"]]
    next_id = upcoming[0]["id"] if upcoming else None

    calendar_name = args.name or cfg.get("calendar", {}).get("name") or f"{team_name} FC"
    out = pathlib.Path(
        args.out or cfg.get("calendar", {}).get("out")
        or root / "docs" / f"{slugify(team_name)}.ics"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        build_ics(mine, fixtures, names, table, positions, team_id, next_id,
                  calendar_name),
        encoding="utf-8",
    )

    row = table[team_id]
    print(f"Wrote {len(mine)} {team_name} fixtures to {out}")
    print(
        f"{team_name}: {ordinal(positions[team_id])}, {row['pts']} pts, "
        f"{row['w']}W-{row['d']}D-{row['l']}L, GD {row['gf'] - row['ga']:+d}"
    )
    if next_id:
        nxt = upcoming[0]
        opp = names[nxt["team_a"] if nxt["team_h"] == team_id else nxt["team_h"]]
        print(f"Next: {opp} on {nxt['kickoff_time']}")


if __name__ == "__main__":
    main()
