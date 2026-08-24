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
sportsball draft                   # live auction assistant
```

Everything runs on the bundled sample projections out of the box. Point
`--projections` at your own CSV for real numbers.

---

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
| TE       | 1.50      | 0.87           | **2.37**        |
| WR       | 0.50      | 0.26           | 0.76            |
| RB       | 0.50      | 0.19           | 0.69            |

A tight end's reception is worth roughly three times a wide receiver's. This
is not a rounding error, and it drives the tool's most striking output: at list
prices it wants to start a lot of tight ends. That is a real property of
TE-premium formats with open flex slots, not a modelling artifact — but see
[the caveats](#what-to-be-skeptical-of).

**First downs are scored but never projected.** No public projection source
publishes first downs, and they are worth up to 1.5 points each here. The tool
estimates them from receptions and carries at positional rates, counting
touchdowns as conversions. Supply real `rec_first_downs` / `rush_first_downs`
columns and it uses those instead.

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
65 every single week, on nearly identical season totals, earns none. So single
game yardage is modelled as a lognormal distribution around the projected
per-game mean, with a positional coefficient of variation, and the tool
integrates the tail above each threshold. Boom/bust players are correctly
priced *up*.

**Play bonuses reward efficiency.** These fire on every qualifying play, so
expected value is linear in volume — but the *rate* scales with yards per
opportunity, because explosive plays are exactly what pulls a yards-per-carry
average above league norm. A back at 5.2 YPC is credited with more 40-yard runs
than one at 3.8 on the same carries.

Every constant in both models lives in the league config and is meant to be
re-fit against real data. `sportsball player NAME` breaks down exactly where a
projection comes from:

```
Brock Bowers  (TE - LV)
  receiving 105 rec, 1200 yds, 8 TD (11.4 Y/R)

  base scoring        423.2
    rec_20_plays              147.8
    scrimmage_yardage_games     37.0
  bonus total         184.8   (30% of projection)
  PROJECTION          608.1   (35.8/gm)
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

## The live draft assistant

```
sportsball draft --me "my team"
```

```
me <player> <price>        you won the bid
sold <player> <price> <tm> someone else won the bid
undo                       take back the last sale
max <player>               your true walk-away price
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
- `upside_weight` — blend median and ceiling when pricing. SFB is a tournament
  with one overall winner, so pricing pure medians is arguably the wrong game.
  Raise toward 1.0 to pay up for ceiling.
- `play_bonus_once_per_game` — for leagues that cap big-play bonuses per game
  (SFB16 does not; see above)
- everything in `scoring` and `bonuses`

## What to be skeptical of

Stated plainly, because a tool that hides its assumptions is worse than no tool:

1. **The bundled projections are synthetic.** They are illustrative numbers
   with plausible names, generated by `tools/make_sample.py` so the tool runs
   and the tests have a realistic board. Do not draft off them. They also
   almost certainly overstate tight end depth, which exaggerates the all-TE
   result.
2. **The bonus rate constants are estimates**, not fits. `pass_40_rate`,
   `rec_20_rate`, the positional CVs — all are reasoned league-average
   baselines. They are the single largest source of error in the projections,
   and they are exposed in config precisely so you can do better.
3. **The optimizer assumes you can buy at list price.** It will happily plan a
   roster of eight tight ends. In a real room, if tight ends are this
   dominant, eleven other managers will bid them up and that roster will not be
   available. The inflation model tracks that as it happens, but the pre-draft
   plan is an upper bound, not a script.
4. **SFB16 turnover scoring is assumed to be zero.** The published graphic
   lists positive scoring only. Interceptions and lost fumbles are set to 0
   rather than guessed at. Override in config if that is wrong.
5. **Team assignments in the sample data may be stale.** Irrelevant to the
   math, but do not read anything into them.

## Development

```
pip install -e '.[dev]'
pytest
```

130 tests covering scoring against hand-computed totals, the lognormal bonus
model, exact lineup assignment, replacement derivation, the money identity,
budget discipline, max-bid economics, draft bookkeeping, and every CLI command.

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
| `cli.py` | the commands |
