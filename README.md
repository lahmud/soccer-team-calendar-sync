# soccer-team-calendar-sync

Builds a subscribable calendar feed for a football club: every league fixture,
each carrying the table as it stands and what the result would do to the club's
position.

Set up for Chelsea. Any Premier League club works via `config.toml` or `--team`.

## What an event looks like

Titles carry the result once played (`[W 3-2] Fulham v Chelsea`) and the matchup
before that (`Chelsea vs Spurs`, `Chelsea at Sunderland`). Location is the home
ground. The next match gets the full treatment:

```
Premier League, Gameweek 8
Home at Stamford Bridge, London

TABLE (after 7 played)
  Chelsea 4th, 13 pts, GD +4 - Champions League places
  Spurs 16th, 6 pts, GD -4
  3 pts behind Brighton (3rd) | 1 pt ahead of Liverpool (5th)

IF THIS RESULT STANDS
  Win    4th, 16 pts - level with Brighton on points, behind on GD
  Draw   4th, 14 pts
  Loss   4th, 13 pts

Once the rest of gameweek 8 is played, Chelsea can finish as high as 4th
or as low as 7th.
```

Results that don't move the club say why, so an unchanged position reads as a
fact about the table rather than a broken calculation.

The high/low range is exact, not an estimate. Every other match in the gameweek
is set to the outcome that helps or hurts the club most. Because each fixture
only moves its own two teams, picking the best outcome fixture by fixture gives
the true extreme.

## Run it

```bash
python3 scripts/build_calendar.py                      # uses config.toml
python3 scripts/build_calendar.py --team SUN            # any PL club
python3 scripts/build_calendar.py --team "Man Utd" -o /tmp/mun.ics
```

Standard library only, no API key. Pass a name that doesn't match and the script
lists the valid ones.

Fixtures, kickoff times and scores come from the public Fantasy Premier League
API. The table is computed from finished fixtures rather than read from a
standings endpoint, so it always agrees with the fixtures in the calendar.

## Subscribe in Google Calendar

Subscribing beats importing. An imported file is a one-time snapshot; a
subscription re-reads the feed, so positions and stakes stay current.

1. Google Calendar > Other calendars > + > **From URL**
2. Paste the published feed URL
3. Give it a colour

It arrives as its own calendar, so it can be hidden without touching anything
else, and works from any Google account. Events are marked
`TRANSP:TRANSPARENT`, so they show up without making you look busy to anyone
checking your availability.

Google sets its own refresh cadence for subscribed calendars, usually 8 to 24
hours. Fine for a feed that changes at most once a matchday, but a result won't
appear the moment the whistle goes.

## Staying current

`.github/workflows/build.yml` rebuilds the feed at 06:00 and 18:00 UTC and
commits any change. Running on Actions rather than a local cron means the feed
keeps updating with the laptop closed.

Output goes to `docs/`, published by GitHub Pages. The repo has to stay public
for Google to fetch the feed.

## Scope

The FPL API only covers the Premier League, so league fixtures are all this
produces today. For Chelsea in 2026/27 that is close to the whole season: they
have no European football, having missed out on the Champions League, Europa
League and Conference League. Not covered:

- FA Cup (third round onward, January)
- EFL Cup

Adding cups means a second data source. football-data.org keeps both English
cups behind its EUR 49/month tier.

## Extending past the Premier League

Two things are league-specific and would need replacing for another competition:

- `fetch()` and the FPL response shape, in place of a different fixtures source
- `VENUES`, which maps clubs to home grounds by name

The table, ranking, scenario and ICS code is generic and would carry over. The
constants at the top (`COMPETITION`, `TEAMS_IN_LEAGUE`, `GAMES_IN_SEASON`) exist
for that reason, though `zone_note()` encodes English qualification and
relegation rules and would need adjusting.

## Season upkeep

Promoted clubs each August need a `VENUES` entry or their location shows as
`TBC`. The script warns on stderr naming any club it can't map.
