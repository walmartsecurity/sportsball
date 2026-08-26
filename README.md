# sportsball

A fantasy football auction optimizer, built for **Scott Fish Bowl 16** scoring
and its "SFB9 Redux" lineup.

It does three things: projects fantasy points under SFB16's unusual rules,
turns those projections into dollar values, and sits next to you during the
auction telling you what you can still afford to pay.

```
pip install -e .
sportsball values --tiers          # the cheat sheet
sportsball roster                  # the best roster the board allows
sportsball player "Brock Bowers"   # where one player's points come from
sportsball targets                 # what is worth bidding on, and why
sportsball draft                   # live auction assistant
```

Or build the web app, which is what you actually want open at the draft table:

```
python tools/build_app.py --out app.html   # then open app.html
```

Projections for real players are bundled, modelled from nflverse play-by-play —
see [where the projections come from](#where-the-projections-come-from). Point
`--projections` at your own CSV to use someone else's numbers instead.

---

## Where the projections come from

nflverse publishes what *happened*, not what will happen, so the bundled
projections are a model fitted to five seasons of play-by-play, not a download.
Regenerate them any time:

```
pip install -e '.[fit]'
python tools/project_from_nflverse.py --season 2026 --out projections.csv
python tools/project_from_nflverse.py --backtest        # score it yourself
```

The model, in the order it matters:

- **Volume does the work.** Attempts, carries and targets per game, weighted
  across three seasons and shrunk toward the positional median by how many
  games a player actually has. Volume is the stable part of production.
- **Efficiency is regressed hard.** Yards per opportunity, and touchdown rates
  especially, are noisy — they get pulled toward positional means with much
  heavier shrinkage than volume.
- **An age curve**, flat to a positional peak and declining after. Adding it cut
  mean absolute error from 85 to 81 points.
- **Rookies come from draft capital**, since they have no history at all. Where
  a player was taken predicts his rookie scoring rate with a Spearman
  correlation of 0.43–0.58, comfortably beating a positional average.
- **Slot-aware volume priors.** Volume shrinks toward what a player's *slot* on
  the depth chart is worth, not toward one number for the whole position. This
  matters more than it sounds: player populations are bimodal — thirty-two men
  throw the ball and everyone else holds a clipboard — so a positional median
  *is* a starter's workload, and shrinking a backup toward it promotes him.
  Before this, the model had eighty quarterbacks over 300 attempts and
  projected 35,000 league pass attempts against a real 19,500, which inflated
  the 40-yard-pass-play bonus by 79%.
- **A blend toward the current depth chart**, so someone who changed teams is
  not projected into the job he used to have. `--role-weight 0` turns it off.
- **Filtered to the season's actual rosters**, which drops retirements.

### It beats the natural baselines, by a little

Backtested against three seasons, predicting each from the seasons before it:

| season | last season | 3yr weighted | this model |
|---|---|---|---|
| 2023 | 0.650 | 0.639 | **0.726** |
| 2024 | 0.680 | 0.689 | **0.721** |
| 2025 | 0.722 | 0.742 | **0.787** |

(Spearman correlation with actual SFB16 points, players with 8+ games. Mean
absolute error improves from 88–98 to 74–81 over the same window.)

### The projected spread is too narrow

Checked against five seasons of actual SFB16 scoring, the model's ranking is
sound but its *spread* is not. Points at each rank, model against the
2021–2025 average:

| rank | actual (5yr mean) | projected | ratio |
|---|---|---|---|
| 1 | 722 | 520 | **72%** |
| 10 | 567 | 459 | 81% |
| 50 | 374 | 352 | 94% |
| 120 | 236 | 272 | 115% |
| 200 | 139 | 193 | **138%** |

The top of the board is compressed and the tail is inflated: the model's best
player to its 120th is a 1.91x gap where reality is 3.06x.

This is not the model failing to guess a lucky outlier. The top score is
strikingly stable — 779, 737, 711, 692, 688 — so *someone* posts about 720
every year. It is the signature of shrinkage: pulling every player toward a
positional prior is exactly what makes individual projections accurate, and it
necessarily produces a distribution narrower than the one reality draws from.

Spread matters here because value over replacement is a distance, and dollars
are proportional to it. `--calibrate-spread` maps each player onto what the
player at his rank has historically scored, leaving the ranking untouched:

```
python tools/project_from_nflverse.py --calibrate-spread --out projections.csv
```

The rank is his rank **within his own position**, against that position's own
history. Doing it on one pooled ranking was wrong in two ways at once:

- **Positions do not share a shape.** The fall from the best quarterback to the
  tenth is nothing like the fall from the best running back to the tenth. A
  pooled curve imposes the blend of those shapes on all of them, flattening the
  steep positions and steepening the flat ones.
- **A pooled curve cannot correct a position's level**, because it never
  compares a position against itself. A tight end ranked 40th overall is handed
  the 40th best score from a list that is mostly wide receivers — so whatever
  the model believes tight ends are worth passes straight through the
  correction untouched.

Both land directly on replacement level, which is computed per position, and
so on every dollar figure downstream. The run reports one line per position —
where its top projection started and where the curve put it, and how many
players were mapped onto how many ranks of history — so the size of the
correction is visible rather than assumed. A position the history has nothing
for is left on the model's own scale and says so out loud, rather than being
quietly left behind while its rivals move.

It moves less than the point gap suggests, because normalising to a fixed
budget absorbs most of a proportional squeeze — the top of the board rises
roughly 10–15% and the middle gives some back.

It is opt-in because the trade is real: this makes individual projections
*worse* by squared error, since it predicts a 720-point season for whoever
happens to rank first. It makes the board better shaped for pricing.

### Are the big plays calibrated?

SFB16 pays ten points a pop for explosive plays, so a projection that is right
about yardage and wrong about how that yardage arrives will misprice the board.
`--calibrate` projects a past season and compares expected big plays against
what those *same players* actually did — same-player rather than top-N against
top-N, which would flatter or punish the model for selection rather than
accuracy:

```
python tools/project_from_nflverse.py --calibrate
```

| bonus | position | ratio |
|---|---|---|
| 20+ yard receptions | WR | 85% |
| | TE | 90% |
| | RB | 107% |
| 40+ yard pass plays | QB | 93% |
| 40+ yard runs | RB | 69% |
| | QB | 71% |

The receiving numbers are the ones that matter: 20-yard catches are **83% of a
receiver's bonus points and 94% of a tight end's**, and they land within about
10%. Coming in slightly under is the expected direction, since a projection is
a mean and big plays are right-skewed.

**40-yard runs are genuinely under-projected by ~30%,** and worth stating
plainly rather than burying. They are also the least valuable bonus in the
format — 12% of a running back's bonus points, which are themselves 28% of his
value — so the miss is worth roughly two points on a four-hundred-point back.
The cause is the efficiency elasticity compounding with the projection's own
regression toward the mean. Correcting it in the bonus model would break every
*other* projection source, which supplies unshrunk efficiency, so it stays
documented rather than patched.

Scaling each team's projected volume to a realistic team season was tried as a
fix for the quarterback inflation and **removed**: it corrects the total while
leaving the shares wrong, so it deflated the starters that were already right,
dropping the top thirty receivers to 54% of their real workload. Slot-aware
priors fix the shares instead, and the totals then take care of themselves.

Read that as a real but modest edge, and note what the model cannot know: camp
battles, holdouts, suspensions, scheme changes, or who looked good in August. A
consensus projection that incorporates all of that *should* beat it. Its job is
to make the tool run on real players instead of invented ones.

The honest failure mode is recency without news. The model has Justin Jefferson
outside the top twenty, because he really did post 1,048 yards and 2 touchdowns
in 2025; a human would bounce him back and the model will not. Bundled
projections are a starting point — regenerate before you draft, and override
with better numbers where you have them.

## Running it live

The published app is static: the board and the draft state are baked in when
it is built. That is right for something you open on a phone at a table, and
wrong for a draft that is moving, where every sale would mean a rebuild.

`sportsball serve` closes the loop. It serves the app **from your own machine**
and polls MFL behind it, so sales and franchise budgets arrive on their own.
This has to run somewhere that can reach both your league and your browser,
which means your laptop -- not a cloud shell, and not the published artifact.

From a clean checkout, one command sets up and starts everything:

```
./draft.sh --league 36570 --host www43 --me "Your Franchise"
```

It creates a virtualenv, installs, rebuilds the board, and starts the server;
re-running reuses the virtualenv. Anything you pass goes through to `serve`.
The long way, if you would rather see the steps:

```
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[solver]'
python tools/build_app.py --out app.html
sportsball serve --league 36570 --host www43 --me "Your Franchise"
```

On Windows, use the second form from PowerShell with
`.venv\Scripts\Activate.ps1`; `draft.sh` needs bash, so it wants WSL or Git
Bash.

Open the address it prints. A dot in the header shows how fresh the sync is,
and goes amber when the league data is older than three sync cycles.

`--me` has to match your franchise name in MFL exactly, or the app will track
the room correctly and think you own nothing. `--apikey` is required for a
private league; without it MFL answers with a login page rather than data.
Run `python tools/fetch_mfl.py --league <id> --host <host> --inspect` to see
what your league actually returns, franchise names included.

Two problems solve themselves by putting a server in the middle. **The browser
cannot call MFL directly** — a `file://` page or a published artifact is
blocked from cross-origin requests, and the artifact's content policy forbids
them outright. Here the page only ever talks to the server it came from, and
that server talks to MFL. And **a private league's API key stays in the
process**; it is never handed to the page.

The room is authoritative about what sold and what everyone has left, so those
are replaced on every sync and Undo is disabled while it is connected. **The
bids you typed in are yours and survive** — they are the one thing the league
does not know. If a fetch fails the last good state stays on screen rather than
blanking mid-draft, and repeated failures back off to a minute instead of
hammering a league that is down.

Fetching runs on a background thread, so a page poll is answered from cache and
never waits on MFL. Only `auctionResults` and `rosters` are refetched: the
player dictionary is every player in the league's universe and cannot change
mid-draft, so it is read once. That is what makes `--refresh 5` (the default)
affordable to hold for a three-hour auction — a cycle is two small requests,
not five large ones. A change in the league reaches the open page in about
three and a half seconds.

**A slow auction keeps players open for hours**, and `auctionResults` returns
those in-progress rows alongside finished ones. They are not sales: the player
is still winnable and the price is not final, so they arrive as standing bids,
tagged `BIDDING` on the board, priced at the current high bid, and they do not
spend the bidder's budget. Time remaining above zero settles it; where a row
contradicts itself the open reading wins, because a player wrongly marked sold
is one you never bid on.

`--from-dir` syncs from saved endpoint json instead of the network, which is
how it can be exercised without a live league.

## Using MyFantasyLeague

If your league runs on MFL, this is the best source available, for one reason:
**MFL scores in your league's own rules.** Its projections arrive as SFB16
points — tight end premium, first downs and video game bonuses included — so
nothing has to be re-scored or approximated. It also knows what every player
went for and what every franchise has left.

```
python tools/fetch_mfl.py --league 36570 --host www43 --year 2026 \
    --me "Your Franchise" --out-dir mfl/
python tools/build_app.py --projections mfl/projections.csv \
    --seed mfl/seed.json --out app.html
```

That writes the board (`projections.csv`, with MFL's league-scored points in a
`fantasy_points` column) and the draft state (`seed.json`, every completed sale
plus every franchise's remaining salary).

If the API is unreachable from your network, save the endpoints and point the
tool at them — it does the same work either way:

```
base='https://www43.myfantasyleague.com/2026/export'
for t in league players auctionResults rosters; do
  curl -sS "$base?TYPE=$t&L=36570&JSON=1" > mfl/$t.json
done
curl -sS "$base?TYPE=projectedScores&L=36570&W=YTD&JSON=1" > mfl/projectedScores.json
python tools/fetch_mfl.py --from-dir mfl/ --me "Your Franchise" --out-dir mfl/
```

A private league needs `--apikey`; without it MFL returns a login page instead
of data, which the tool reports rather than parsing into nonsense.

Two details worth knowing. **Budgets come from committed roster salary, not
from summing the auction** — a kicker's salary is spent money even though
kickers are not on this board, so the two disagree and the roster is right.
And **franchises that have not bought anything are still carried**, because
their money sets prices for everyone.

MFL's response shapes vary by endpoint — a collection with exactly one member
comes back as a bare object rather than a list — so every reader tolerates a
missing layer, and `--inspect` prints what actually arrived before anything is
written:

```
python tools/fetch_mfl.py --league 36570 --host www43 --inspect
```

## Using Sleeper's projections instead

Sleeper projects stat lines, which is what this tool wants — their fantasy
totals are computed under their own scoring, and SFB16 is nothing like it. The
importer takes the stats and lets this engine apply SFB16 rules to them:

```
python tools/fetch_sleeper.py --season 2026 --out sleeper.csv
python tools/build_app.py --projections sleeper.csv --out app.html
```

If your network blocks the API, fetch it yourself and parse the saved payload —
same result:

```
curl -sS 'https://api.sleeper.com/projections/nfl/2026?season_type=regular\
&position[]=QB&position[]=RB&position[]=WR&position[]=TE&order_by=pts_ppr' > sleeper.json
python tools/fetch_sleeper.py --from-json sleeper.json --out sleeper.csv
```

The URL above is season-level; swap `grouping=season` for a week number to
pull weekly instead.

**Taken:** volume, yardage, touchdowns, interceptions, fumbles, two-point
conversions, and — when Sleeper projects them — rushing and receiving first
downs, which SFB16 scores and almost nobody publishes.

**Deliberately not taken:** Sleeper's own `bonus_*` projections. They are
computed for Sleeper's thresholds, which do not match SFB16's — Sleeper splits
rushing and receiving hundred-yard bonuses where SFB16 pays on the combined
scrimmage total, and Sleeper has no 20-yard-reception bonus at all. Using them
would silently mis-score the format, so the video game bonuses stay with this
tool's fitted models.

Sleeper's API is undocumented and its field names have changed before, so the
mapping is tolerant of aliases and `--inspect` prints exactly what came back and
how each field resolved:

```
python tools/fetch_sleeper.py --season 2026 --inspect
```

The parser is tested against recorded payloads rather than the live service, so
a silent shape change shows up as missing fields in `--inspect` rather than a
plausible-looking board built on zeros.

### Projections that are already scored

If a source computed points under *your* league's rules, use them — it will
have news no model does. Any projections CSV can carry a `fantasy_points`
column (or `fpts`, `points`, `proj_points`), and those players skip the scoring
engine entirely. The column can be left blank per row, so a file can mix
already-scored players with ones this tool should score itself.

`fetch_sleeper.py --points ppr` carries Sleeper's own totals through the same
way, and prints a warning while doing it, because **Sleeper's totals are PPR
and SFB16 is not**: no tight end premium, no first downs, and none of the video
game bonuses that are 44% of a receiver's value here. It is the right switch
only if the totals were scored the way your league scores. Otherwise take
Sleeper's stat lines and let this apply SFB16 to them, which is the default.

## Why SFB16 needs its own model

Most auction tools assume a conventional league: fixed starting slots, one
point per reception, replacement level at "the 12th best quarterback". Every
one of those assumptions is wrong here.

**The lineup has no positional minimums.** SFB16 starts ten players: two from
QB/RB/WR/TE and eight from RB/WR/TE. That is the *only* constraint. A legal
lineup can start zero quarterbacks and ten tight ends. So "replacement level"
cannot be looked up from a slot count — it has to be derived from the rules.
This tool solves for the set of players who would actually start somewhere in
the league if every roster were optimal, then defines replacement at each
position as the best player who misses that cut. That is the real free-agent
alternative, and it handles interchangeable positions without special cases.

**The tight end premium is enormous.** SFB16 pays 0.5 per reception and 0.5 per
first down, then gives tight ends *another point of each*. Run the numbers on
what one catch is worth:

| position | per catch | per first down | total per catch |
|----------|-----------|----------------|-----------------|
| TE       | 1.50      | 0.79           | **2.29**        |
| WR       | 0.50      | 0.30           | 0.80            |
| RB       | 0.50      | 0.17           | 0.67            |

A tight end's reception is worth roughly **2.9x** a wide receiver's on the base
scoring. That is enormous — but it does not settle the question, because the
20+ yard reception bonus pushes hard the other way: receivers catch the ball
further downfield and bank far more of those. The two effects very nearly
cancel. Forcing the optimizer off receivers entirely costs 0.9% of roster
value; it just rebuilds around tight ends. The practical read is that
TE-heavy and WR-heavy builds are interchangeable in SFB16, so take whichever
the room is underpricing.

**First downs are scored but never projected.** No public projection source
publishes first downs, and they are worth up to 1.5 points each here. The tool
estimates them from receptions and carries at rates fitted from play-by-play
(0.60 per catch for a receiver, 0.53 for a tight end, 0.34 for a back). Supply
real `rec_first_downs` / `rush_first_downs` columns and it uses those instead.

**Passing touchdowns are worth six**, not four, which moves quarterbacks up
substantially — though only two can start, which caps how much that matters.

## The video game bonuses

This is where SFB16 gets genuinely interesting, and where most spreadsheets
give up. Seven bonuses, ten points each:

| bonus | fires |
|-------|-------|
| 300+ / 400+ passing yards | once per game, and they stack |
| 100+ / 200+ scrimmage yards | once per game, and they stack |
| 40+ yard passing play | every qualifying play |
| 40+ yard rushing play | every qualifying play |
| 20+ yard receiving play | every qualifying play |

Ten points is enormous in a format where a six-catch, 100-yard game is worth
about thirteen. For a target hog these bonuses are 30-45% of the entire
projection. Ignoring them, or bolting on a flat adjustment, gets the board
badly wrong.

They also need two different models, because they behave differently:

**Game bonuses reward variance, not volume.** A back who runs for 100 yards
eight times and 20 yards nine times earns 80 bonus points. One who grinds out
65 every single week, on nearly identical season totals, earns none. Single
game yardage is modelled as a **gamma** distribution around the projected
per-game mean, and the tool integrates the tail above each threshold.

The spread is not a constant. Fitted against five seasons of play-by-play, a
player's game-to-game coefficient of variation falls sharply as volume rises:

| running backs | yards/game | observed CV |
|---|---|---|
| bottom quartile | 33 | 0.83 |
| second | 50 | 0.69 |
| third | 72 | 0.52 |
| top quartile | 97 | 0.43 |

Holding it constant — as the first version of this model did — badly overstates
big games for exactly the workhorses who live near the 200-yard threshold.

**Play bonuses reward efficiency.** These fire on every qualifying play, so
expected value is linear in volume — but the *rate* scales with yards per
opportunity, because explosive plays are what pull an average above league
norm.

Every constant in both models is fitted, and lives in the league config so it
can be re-fitted. `sportsball player NAME` breaks down exactly where a
projection comes from:

```
Brock Bowers  (TE - LV)
  17 games
  receiving 105 rec, 1200 yds, 8 TD (11.4 Y/R)
  base scoring        409.2
    rec_20_plays              152.1
    scrimmage_yardage_games     32.3
  bonus total         184.4   (31% of projection)
  PROJECTION          593.6   (34.9/gm)
  upside case         760.4
  replacement         254.1
  value over repl     339.5
  AUCTION VALUE         $56
```

### Play bonuses are per play

Confirmed against the SFB16 rules: a player with three 40-yard runs in a game
scores the bonus three times. That is what the tool does by default, and it is
why explosiveness is priced as heavily as it is here — a receiver with twenty
catches of 20+ yards banks 200 points from that bonus alone.

For other leagues that pay the bonus at most once per game, setting
`play_bonus_once_per_game: true` models per-game occurrences as Poisson and
counts only the probability of at least one. It lowers top-end valuations by a
few percent.

## Where the constants come from

Every rate, elasticity and variance parameter in the bonus and first-down
models is fitted against [nflverse](https://github.com/nflverse) play-by-play,
2021-2025 regular season — 236,000 plays. Refit them yourself:

```
pip install -e '.[fit]'
python tools/fit_bonus_rates.py --seasons 2021 2022 2023 2024 2025
```

That prints calibration diagnostics and a YAML fragment to paste into a league
config. Two methodology choices materially change the answers:

**Big-play rates are fitted against *prior*-season efficiency.** Yards per
carry is partly *caused by* the 40-yard runs being predicted, so fitting on the
same season is circular and inflates the efficiency elasticity badly:

| bonus | same-season (circular) | prior-season (used) |
|---|---|---|
| 20+ yard receptions | 2.00 | **1.35** |
| 40+ yard runs | 4.66 | **1.72** |
| 40+ yard pass plays | 2.85 | **0.58** |

Since the tool is fed a *projection* at prediction time, the lagged fit is the
one that matches how it is actually used. The difference is not academic — the
circular rushing elasticity would have credited a 5.5 Y/C back with nearly
three times the 40-yard runs the honest model gives him.

The payoff is calibration. Against a flat league-average rate, the fitted
receiving model is dramatically better across the efficiency distribution:

| prior-season Y/R quartile | flat rate error | fitted error |
|---|---|---|
| Q1 (low) | +102% | +15% |
| Q2 | +17% | +0.3% |
| Q3 | −15% | −8% |
| Q4 (high) | −27% | +3% |

For 40+ yard pass plays the fitted elasticity is 0.58 ± 0.33 — barely
distinguishable from no effect, and a flat rate is already well calibrated
there. Yards per attempt simply does not predict next year's deep completions
very well, and the model says so rather than pretending otherwise.

**Gamma beats lognormal in the tail.** Checked directly against observed
threshold games:

| | actual | gamma | lognormal |
|---|---|---|---|
| RB 100+ yards | 913 | −7% | −14% |
| RB 200+ yards | 27 | +39% | +88% |
| WR 200+ yards | 14 | +88% | +190% |
| QB 300+ pass yards | 391 | +0.2% | −3% |
| QB 400+ pass yards | 40 | +20% | +40% |

Both still overshoot the rarest thresholds, but a 200-yard scrimmage game
happens about once per 40 player-games, so even a large relative error there is
worth well under a point of season value. The 100-yard threshold, which is
worth 50-90 points, lands within 7%.

**Two guesses the data overturned.** Wide receivers convert first downs at a
*higher* rate per catch than tight ends (0.60 against 0.53) — they catch the
ball further downfield. I had assumed the opposite, and it meaningfully narrows
the SFB16 tight end edge. And quarterback yards per carry comes from scrambles
rather than breakaway speed, so the shared rate model over-predicted their
40-yard runs by roughly a factor of two; they now carry an explicit multiplier.

### How much did fitting change the board?

Honestly, less than the effort suggests — and that is worth stating plainly.
Across the top 112 players, the median auction value moved **$0.40 (2.7%)**,
the mean $0.64, the largest single move $4.10 (Lamar Jackson, down, from the
quarterback rushing correction). Quarterbacks fell about $1.30 on average,
receivers rose about $0.70.

Prices are relative, so corrections that move a whole position together
largely cancel in the valuation step. What *did* change materially is roster
construction — see below.

## What to bid on

The obvious feature would be a ranked list of best available players. It would
also be close to meaningless: auction pricing sets every tier to the same value
per dollar, so at list price one player is as good a deal as the next, and the
optimizer is genuinely indifferent between them. Forcing the top of the board
into a recommendation dresses that indifference up as insight.

`sportsball targets` (and the app's panel) looks for the four things that
actually constitute an edge:

1. **Price.** A player available under your walk-away number. That gap is the
   whole edge; everything else is commentary.
2. **Fit.** Value you cannot start is worth a fraction of its price — a third
   quarterback in a format that starts two is a bench body however good the
   projection.
3. **Scarcity.** Only the superflex slots are capped, so quarterbacks are the
   one position that can run out from under you. When fewer startable ones
   remain than there are jobs to fill, waiting gets expensive.
4. **What the room is doing.** Rooms have habits: they chase running backs and
   sleep on tight ends. Comparing realized sale prices against the model's
   values, position by position, is the edge that shows up mid-draft — and it
   is measured, not assumed.

```
PLAYER            POS  NOW   YOUR MAX  WHY
----------------  ---  ----  --------  -----------------------------------------
* Joe Burrow      QB   $174  $174      fills a superflex slot; only 21 startable
                                       left for 24 jobs
T.J. Hockenson    TE   $121  $121      room is paying 70% of value for TEs (4 sold)
```

Two things it deliberately will not do. It stays quiet when nothing is
mispriced, rather than manufacturing a pick — an empty list with the money you
have left per remaining spot is the honest answer, and the common one early.
And **it does not treat falling prices as an edge**: when the room overspends,
everything left gets cheaper for everyone at once, including you. An edge needs
your money to go further than theirs, which is why the break-even compares your
rate against the market's instead of watching prices drop.

## From points to dollars

Auction pricing rests on an accounting identity: every team fills every roster
spot, and every dollar gets spent. So the money actually in play is the
league's total budget minus a minimum bid reserved for each spot that must be
filled. That surplus is distributed across drafted players in proportion to
their value over replacement.

Mid-draft, the same identity is re-applied to what is *left* — remaining money
over remaining value. That is what produces inflation, and it is why the room
overpaying for the first three studs makes everyone else cheaper, not more
expensive. The tool recomputes after every sale.

Replacement levels deliberately do **not** drift during the draft. They
describe the league's lineup requirements, which do not change as players come
off the board. Only the money does.

## Max bid

The number the tool exists to produce. Not a rule of thumb — a definition:

> The most you can pay for a player and still come out ahead is the price at
> which the best roster you can build *with* them exactly equals the best
> roster you can build *without* them.

That is computed honestly, by binary search over integer dollars, re-solving
the roster optimization at each probe. It already accounts for what the money
would otherwise buy, which is why it falls as your wallet empties and rises
when the board thins out around you.

```
[$140 / 19 slots] > max mcbride
Trey McBride (TE) -- 542 projected pts
  list value   $45
  market now   $45
  YOUR MAX BID $44
  (hard cap $122 with 19 slots to fill)
```

Before the draft, with a full budget and an untouched board, max bid tracks
list value closely — that is what list value means, and the test suite pins it.
The two diverge as the auction goes on, which is the entire point.

## What he will actually go for

Your max bid is a number about *you*. It says nothing about whether you will
ever get the chance to pay it. So every player also carries a range — what he
costs if the room lets him slide, what he ought to go for, and what he costs
when two teams want him:

```
[$565 / 18 slots] > max loveland
Colston Loveland (TE) -- 641 projected pts
  list value   $228
  market now   $285
  goes for     $145-$287  (likely $211)
  YOUR MAX BID $285
  (hard cap $548 with 18 slots to fill)
  Winnable — you can pay the $211 he ought to go for, up to $285, but not a war.
```

Read against your own number, the range answers the question the model alone
cannot. Your max above the high end and he should be yours, with nothing to
lose but the overpay. Your max below the low end and he is not yours at any
price the room will accept — watching that bidding is a waste of the one thing
you cannot get back mid-draft, which is attention.

**The width comes from two places.** How uncertain *this player* is comes free
from the ceiling model: a receiver whose points come from explosive plays draws
a wider spread of opinion than one with the same projection built out of
volume, and that is exactly the spread `season_sigma` already measures. It is
scaled against the typical drafted player, so an ordinary player gets the
ordinary width and the tilt is genuinely about him.

How loose *this room* is gets measured, not assumed. Each completed sale is run
backwards through the pricing identity — the price paid implies a value over
replacement, which implies a season — and the scatter of those against the
projections is how far this room's opinion sits from the model's. A stated
prior carries it until there are enough sales to say. Measuring in points
rather than dollars matters: measured in dollars and applied to points, the
same wobble would be magnified a second time on the way back out.

The dollar width then falls out of the identity rather than being applied to
the price. Dollars track value over replacement, so a proportional wobble in
points is worth real money at the top of the board and almost nothing at the
bottom — which is why the tail of the board reads `$1` rather than a range. It
is not the model giving up. Below the point where value over replacement is
worth anything, what a player fetches is decided by whose turn it is to throw a
dollar.

**Two facts about the room override all of it.** A price needs somebody able to
pay it, so nothing sells above the largest bid any single team can still
afford — late in a draft, with the wallets empty, that ceiling binds long
before the model's number does. And a player already under the hammer will not
go backwards from the bid standing on him.

The room's positional habit shifts the range as well as the price. The example
above is a room that has been paying 35% of model value for tight ends across
four sales; that is why a $285 market price comes with a $211 likely sale.

## Roster optimization

Choosing a roster is a bilevel problem: bench players score nothing, so what
you are maximising is the *starting lineup* your roster can field, which means
re-solving the lineup for every candidate roster. Both levels go into one
integer program — a roster variable and a set of lineup-assignment variables
per player, tied together by "you can only start someone you rostered."

Bench players are credited with their value **over replacement**, not their raw
projection. This matters enormously in a format with no positional minimums:
scored on raw points, a bench full of streamable quarterbacks looks like the
best roster in the league, because quarterbacks post big numbers even when they
are freely available. What a bench spot is worth is the gap between the player
in it and the one you could add for a dollar.

Lineup assignment itself is solved exactly with min-cost max-flow rather than
greedy slotting, because greedy is wrong under overlapping eligibility: it will
burn a superflex slot on a running back and leave a quarterback with nowhere to
go.

[PuLP](https://github.com/coin-or/pulp) and its bundled CBC solver do the
integer programming when installed (`pip install -e '.[solver]'`). Without it,
a greedy construction plus a swap pass lands within a few percent, so the tool
still works in a bare environment.

## The draft-room web app

`tools/build_app.py` bakes the board into a single self-contained HTML file —
no server, no install, works from a phone at the table. The draft is saved in
the browser.

Four things it does that a cheat sheet cannot:

- **Record what players actually go for**, and see the gap from the model's
  value as a percentage. Green under, red over.
- **Enter the bids you expect** on players still on the board. This is the
  useful one: at list prices the optimizer is indifferent between players, so a
  target roster is a restatement of the model. Enter real numbers and it
  becomes a plan for the room you are sitting in.
- **Correct every team's money and roster count** by typing over it. You will
  not log every sale in a live room, and prices are only right if the money in
  them is. A corrected team keeps updating as it buys.
- **Replan the target roster** against all of the above, then tell you your
  walk-away price on anyone.
- **Show what each player will go for**, low to high, beside your own max, so
  the board says at a glance who is out of reach and who should be yours. Green
  means your number clears the top of the range; red means it does not reach
  the bottom.

```
python tools/build_app.py --projections mine.csv --out app.html
```

### Opening on a draft already under way

Paste the draft room's results page into a text file and the app can ship with
it, rather than you re-entering forty sales one at a time:

```
python tools/seed_draft.py --board board.txt --me "Your Name" \
    --out src/sportsball/data/seed_draft.json
python tools/build_app.py --seed src/sportsball/data/seed_draft.json --out app.html
```

Boards sometimes print two prices on your own rows, and which one was charged
matters. The tool does not guess: it sums each manager's sales both ways and
keeps whichever reconciles with the remaining budget printed beside their name,
saying which it chose. If neither reconciles, it says that too. Managers who
have not bought anything are still carried, because their money sets prices for
everyone. "Reset draft" returns to the seeded state rather than an empty one.

The static maths — scoring, the bonus models, league-wide replacement level —
is computed in Python and baked in as JSON. The browser only runs what changes
during a draft: repricing, your roster, and the max-bid calculation.

That last one is done differently in the two places, deliberately. The CLI
solves an exact integer program over the whole roster. The browser uses the
closed form: auction pricing is an accounting identity, so a dollar buys a
fixed amount of value over replacement, and paying above the market rate for
one player is only correct when you cannot deploy the money elsewhere — which
is exactly what happens late, with cash left and few spots. The app takes the
higher of the market's rate and your own, capped by your wallet.

Two implementations of the same maths drift unless something holds them
together. `tests/test_app.py` runs the browser engine under node and checks it
against the Python one — prices, inflation, lineup assignment, and max bids
(which agree within a few percent). Those tests skip when node is missing.

## The live draft assistant (CLI)

```
sportsball draft --me "my team"
```

```
me <player> <price>        you won the bid
sold <player> <price> <tm> someone else won the bid
undo                       take back the last sale
max <player>               his price range, and your true walk-away
block <player> <price>     record the bid standing on him right now
block <player> off         he is no longer under the hammer
targets [n]                what is worth bidding on, and why
best [pos] [n]             best remaining values at current prices
plan                       best roster you can still finish
roster [team]              show a roster (default: yours)
budget                     money and inflation across the league
save <file> / load <file>  persist the draft
```

Player names accept any unambiguous prefix or substring; ambiguous ones list
the candidates rather than guessing. `save` writes JSON, so a dropped
connection mid-auction is recoverable.

## Bringing your own projections

Any CSV with a name and position column works. Header aliases are normalised,
so FantasyPros and ESPN exports load without renaming anything:

```csv
Player,Pos,Team,Rec,Receiving Yds,Receiving TDs
Some Guy,WR,BUF,80,1100,9
```

Recognised stats: `games`, `pass_att`, `pass_cmp`, `pass_yds`, `pass_td`,
`int`, `rush_att`, `rush_yds`, `rush_td`, `targets`, `rec`, `rec_yds`,
`rec_td`, `fumbles_lost`, `two_point`, and optionally `rush_first_downs` /
`rec_first_downs`. Anything missing is treated as zero.

Because the bonus model works off per-game rates and efficiency, **projections
with per-play detail are worth much more here than bare fantasy-point totals**.
A source that gives you carries and targets lets the tool price explosiveness;
one that gives you only a points total does not.

## League configuration

`src/sportsball/data/leagues/sfb16.yaml` holds the SFB16 rules; `standard12.yaml`
is a conventional PPR league for comparison. Copy either and pass
`--league path/to/yours.yaml`. Useful knobs:

- `bench_weight` — what a bench spot is worth relative to a starter (default 0.35)
- `distribution` — `gamma` (fitted default) or `lognormal` for game thresholds
- `upside_weight` — blend median and ceiling when pricing. SFB is a tournament
  with one overall winner, so pricing pure medians is arguably the wrong game.
  Raise toward 1.0 to pay up for ceiling.
- `play_bonus_once_per_game` — for leagues that cap big-play bonuses per game
  (SFB16 does not; see above)
- everything in `scoring` and `bonuses`

## What to be skeptical of

Stated plainly, because a tool that hides its assumptions is worse than no tool:

1. **The projections know no news.** They are modelled from play-by-play and
   nothing else — no camp reports, no holdouts, no scheme changes. They beat a
   last-season baseline in backtest, and they will still miss the bounce-back
   and breakout calls a human gets right. `tools/make_sample.py` still
   generates the old synthetic set if you want a board with no real names on
   it.
2. **The bonus constants are fitted, but on five seasons of a changing game.**
   Rates drift: 2025 produced 25% more 40-yard runs than the 2021-2024 fit
   predicted. The 40+ pass play elasticity (0.58 ± 0.33) is barely significant.
   Refit annually; the tool is one command.
3. **Position-heavy builds are near-equivalent, not a recommendation.** Forcing
   the optimizer off wide receivers entirely costs only **0.9%** of roster
   value — it simply rebuilds around tight ends. Read the output as "these
   builds are interchangeable, take whichever the room underprices," not as a
   plan to corner one position. It also assumes you can buy at list price; in a
   real room eleven other managers bid too. The inflation model tracks that
   live, but the pre-draft plan is an upper bound, not a script.
4. **SFB16 turnover scoring is assumed to be zero.** The published graphic
   lists positive scoring only. Interceptions and lost fumbles are set to 0
   rather than guessed at. Override in config if that is wrong.
5. **Teams and depth charts move.** The bundled set was built from the rosters
   and depth charts published on the day it was generated. Regenerate before
   drafting.
6. **The bid range starts on a prior, not on evidence.** Before the first sale
   its width is a stated guess at how far a room's opinion drifts from a
   projection, and only the sales in front of it turn that into a measurement.
   Early in a draft the low and the high are a sense of scale, not a forecast;
   by the third hour they are worth something. The positional habit inside it
   is measured from as few as three sales, deliberately shrunk toward paying
   list for exactly that reason.

## Development

```
pip install -e '.[dev]'
pytest
```

189 tests covering scoring against hand-computed totals, the lognormal bonus
model, exact lineup assignment, replacement derivation, the money identity,
budget discipline, max-bid economics, draft bookkeeping, and every CLI command.
The fitting pipeline lives in `tools/fit_bonus_rates.py` and is rerun offline,
not on import — the runtime package depends on nothing but PyYAML.

Layout:

| module | what it does |
|--------|--------------|
| `config.py` | league rules, YAML loading |
| `players.py` | projection ingest, header aliasing |
| `scoring.py` | stats to fantasy points |
| `bonuses.py` | the video game bonus models |
| `lineup.py` | exact lineup assignment (min-cost max-flow) |
| `replacement.py` | replacement level from lineup rules |
| `valuation.py` | value over replacement to dollars |
| `optimize.py` | roster ILP and max bid |
| `draft.py` | live auction state and inflation |
| `suggest.py` | what to bid on, and when to stay quiet |
| `cli.py` | the commands |
| `mfl.py` | the MyFantasyLeague client |
| `serve.py` | local server that syncs the app from your league |
| `tools/fit_bonus_rates.py` | fits the constants from nflverse play-by-play |
| `tools/project_from_nflverse.py` | builds projections from play-by-play history |
| `tools/fetch_mfl.py` | imports an MFL league: board, auction, budgets |
| `tools/fetch_sleeper.py` | imports Sleeper's projected stat lines |
| `tools/make_sample.py` | regenerates a synthetic board with no real names |
| `tools/build_app.py` | builds the standalone draft-room web app |
| `tools/app_template.html` | the app's markup, styles and browser engine |
