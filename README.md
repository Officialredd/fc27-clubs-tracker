# FC27 Clubs Tracker

Saves every Clubs match your club plays in EA SPORTS FC 27, with every player's numbers, and shows it all
in a dashboard, with letter grades for every player and both teams in every match and an overall grade per
player: results, your rating and performance score per match, goals, assists, passing, tackles,
teammates, line-ups, opponents (scouted: their last 10 games and how strong they were), sessions, records
and the club's division and skill rating over time.

- **collector.py** runs in the background and saves new matches from EA's Clubs API (the same public data
  EA's own Clubs website shows). No screenshots, no login: the numbers are exactly EA's. After each match it
  also scouts the other club, and when the tracker is updated it restarts itself with the new version.
- **app.py** is the Streamlit dashboard.
- Data lives in `data/clubs.db` (SQLite), settings in `config.json`.

EA only keeps the **last 10 matches of each type** (league, playoffs, friendlies). The collector checks every
2 minutes while FC 27 is open on this PC or right after a new match turned up (so it also follows along when
you play on a console), and every 15 minutes otherwise, so nothing is lost and your history keeps growing.

## One-time setup (Windows)

1. Python 3.10 or newer, 64 bit (3.12 is already installed for the Wardogs tracker).
2. Double-click **setup.bat**. It installs everything into a `.venv` folder, checks that EA answers,
   imports the matches EA still has, offers to start the collector with Windows and opens the app.

## Every day

Nothing: the collector starts with Windows and saves matches by itself.
Open the dashboard with **start_app.bat** (it runs at http://localhost:8527, next to the Wardogs tracker).
**start_collector.bat** runs the collector in a window, if you want to watch it work.

## Pages

- **Home** (the start page): the season so far and how long it has left, the playoffs card during the playoff
  week (matches used out of your division's limit, playoff record, every playoff match), the last match and
  session, recent matches and top performers
- **Team**: the club (division, record, skill rating, form), the team's key numbers, the last
  match with every player's grade, the latest session, the squad's overall grades and form, top performers
- **You**: your key numbers, the last match from your side and your charts
- **Matches**: every match; click one for the full report (team stats, both line-ups, opponent scouting, your
  match grade)
- **Stats**: Team, You (including your grade by position and archetype), Compare (any 2 to 5 club players side
  by side, head to head in the matches they played together, profiles, rating over time, how steady, finishing),
  Teammates, Opponents (results by opponent strength, their skill rating match by match, a luck meter against what
  the skill ratings predicted, every match as a dot, and you next to the clubs you've played), Time (when you play,
  early vs late in a session, skill rating won or lost per session), Records, Seasons (every EA league season side
  by side, players per season; once there's more than one season, a Season picker shows any section for one season)
- **Squad**: EA's season numbers for every member plus everyone's tracked numbers and a player profile
  (with a shortcut to compare that player with the others)
- **Search**: look up any club by name or club ID (or one you've played): their skill rating and record, last 10
  games, players, how they compare with you, your matches against them and their members' season numbers
- **Settings**: collector on/off, start with Windows, check intervals, the online copy, updates, you, archetype
  names, export to CSV
- **Help**: how it works and what EA's data does and doesn't include

## Grades

Every player (both clubs) and both teams get a grade from S (best) to F for every match, and every player gets an
overall grade. A player's match grade combines EA's rating (35%) with attack, creating, defending, goalkeeping and
the result, weighted by the position played in that match: a forward is judged mostly on goals and conversion, a
midfielder on assists, passing and tackles, a defender on defending and a keeper on saves. A team's grade combines
the result, the team's rating, attack, defence and passing. The number behind the letter is the share of real
FC 27 Clubs performances it beats (calibrated on 115 real matches): S from 90, A from 75, B from 60, C from 40,
D from 25, E from 10 and F under 10. The overall grade is the average match grade, pulled towards the middle
while a player has only a few matches. Playing a stronger club (by EA's skill rating) adds a little to the grades
and a weaker one takes a little off (see Opponent scouting). In the app, the (i) next to a grade explains it, and the Help page and
`clubs/grades.py` have the details.

## Opponent scouting

A few minutes after each match the collector pulls the other club's last 10 games (league and playoffs) and EA's
numbers for the club (older matches fill in over the next checks, or press *Pull their last 10 games now* in a
match report). The report shows how strong they were, their form, goals, humans and usual team grade before your
match, their last games and their players. Strength is their skill rating (SR, the number EA pairs clubs by) minus
yours: +50 is a stronger club, +150 a much stronger one. It counts in the grades: every 100 SR above you adds 4 to
the team grade (up to 10) and 3 to each player's grade (up to 8), and a weaker club takes the same off. Beating a
stronger club is an upset win, losing to a weaker one an upset loss. EA keeps only a club's last 10 games of each
type, so for older matches their games after the match are used instead (the report says so).

## Online copy (for the club's phones)

The tracker can also run online on Streamlit's free hosting, so everyone in the club can open it on their phone.
Your PC keeps collecting from EA (EA's API answers home connections reliably), and after every check the collector
sends what's new to your GitHub repository (the `online-data` folder). The online app runs from the same repository:
it updates when the repository changes and reads `online-data` into its own database. Match notes and your settings
stay on your PC. On the online copy, each visitor picks whose numbers the You page shows (the link remembers it as
`?me=name`).

Set it up once:

1. **A GitHub repository** for the tracker (private is fine), with this code in it.
2. **An access token**: on GitHub, Settings > Developer settings > Personal access tokens > Fine-grained tokens >
   Generate new token. Repository access: only this repository. Permissions: Contents, Read and write.
3. **In the tracker on your PC**: Settings > Online copy. Fill in the repository (owner/name) and paste the token,
   then Save, Test connection and Send now. From then on the collector sends new data by itself.
4. **On Streamlit's hosting** (share.streamlit.io): sign in with GitHub, create an app from the repository
   (branch `main`, file `app.py`) and deploy it. Its settings decide who can open it (anyone with the link, or only
   people you invite).

## Updates

When this folder is a git clone of the repository, the collector installs new versions by itself (about once an
hour, fast-forward only, never over files you changed) and restarts with them; refresh the app afterwards. To turn
an existing folder into a clone (your `data` folder and `config.json` aren't touched):

```
git init
git remote add origin https://github.com/OWNER/fc27-clubs-tracker.git
git fetch origin
git checkout -f -B main origin/main
git branch --set-upstream-to=origin/main main
```

Then run `setup.bat` once so new requirements are installed. Settings > Updates shows the state and has a button to
check right away.

## What EA's data has (and hasn't)

Per match: the score, match type, both clubs, and for every **human** player on both sides: goals, assists,
shots, passes made/attempted, tackles made/attempted, saves, rating, man of the match, red cards, position
group (GK/DEF/MID/FWD), archetype number and time played. Not included: Drop In and Rush matches, AI
teammates, exact positions, dribbles, interceptions and distance. The raw EA data of every match is kept in
the database too, in case more of it can be decoded later.
