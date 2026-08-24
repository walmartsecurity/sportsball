"""Roster construction: what to buy, given prices and a budget.

The objective is not "most projected points on the roster" -- bench players
score nothing. It is the points of the *starting lineup* the roster can field,
plus a discounted credit for bench depth. That makes it a bilevel problem:
choosing a roster implies re-solving the lineup. Formulating both levels in a
single integer program handles it exactly, by giving every player a roster
variable and a set of lineup-assignment variables tied together by a
constraint that you can only start someone you rostered.

Bench players are credited with their value *over replacement*, not their raw
projection. This matters enormously in a format with no positional minimums:
scored on raw points, a bench full of streamable quarterbacks looks like the
best roster in the league, because quarterbacks put up large numbers even at
replacement level. What a bench spot is actually worth is the gap between the
player sitting in it and the one you could add off waivers for a dollar.

PuLP (with its bundled CBC solver) does the integer programming when it is
installed. When it is not, a greedy construction plus a swap-improvement pass
gets close -- usually within a percent or two on realistic boards -- so the
tool still works in a bare environment.

Two things keep the solve fast enough to sit inside a binary search for a max
bid: prices are rounded to whole dollars (auctions are bid in whole dollars,
and an integer knapsack is far easier than a fractional one), and the
candidate pool is pruned to players who could plausibly appear in an optimal
roster before the solver ever sees it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

from .config import LeagueConfig, LineupSlot
from .lineup import Lineup, best_lineup
from .players import Player
from .valuation import Valuation, ValuationBoard

try:  # pragma: no cover - exercised by whichever branch is installed
    import pulp

    _HAVE_PULP = True
except ImportError:  # pragma: no cover
    pulp = None
    _HAVE_PULP = False


# CBC settles for a solution within this relative gap of optimal, which on a
# board this size is well inside the noise of the projections themselves.
_MIP_GAP = 0.002
_TIME_LIMIT = 20

# Candidates kept per position after pruning. Generous enough that the pruned
# pool provably contains an optimal roster for realistic budgets.
_KEEP_PER_POSITION = 45


@dataclass
class RosterPlan:
    """A proposed roster."""

    roster: list[Player]
    additions: list[Player]
    lineup: Lineup
    spend: float
    budget: float
    objective: float
    solver: str
    prices: Mapping[str, float] = field(default_factory=dict)

    @property
    def starters(self) -> list[Player]:
        return self.lineup.starters

    @property
    def bench(self) -> list[Player]:
        starting = self.lineup.starter_ids
        return [p for p in self.roster if p.player_id not in starting]

    @property
    def leftover(self) -> float:
        return self.budget - self.spend

    @property
    def starter_points(self) -> float:
        return self.lineup.points


def _price_map(
    board: ValuationBoard | Sequence[Valuation],
    league: LeagueConfig,
    override: Mapping[str, float] | None = None,
) -> dict[str, float]:
    prices = {
        v.player.player_id: float(max(round(v.price), league.min_bid)) for v in board
    }
    if override:
        prices.update({k: float(max(round(v), 0)) for k, v in override.items()})
    return prices


def _bench_valuer(
    board: ValuationBoard | Sequence[Valuation],
    league: LeagueConfig,
    score: Callable[[Player], float],
) -> Callable[[Player], float]:
    """Bench credit: value over replacement, scaled by the league's weight.

    Falls back to a flat share of raw points only if no replacement levels are
    available, which should not happen for a board built by `value_players`.
    """
    levels = getattr(board, "levels", None)
    if levels is None:
        return lambda p: league.bench_weight * score(p)
    return lambda p: league.bench_weight * max(
        score(p) - levels.for_position(p.position), 0.0
    )


def _prune(
    candidates: Sequence[Player],
    league: LeagueConfig,
    budget: float,
    slots_to_fill: int,
    price_by_id: Mapping[str, float],
    score: Callable[[Player], float],
    keep: set[str],
) -> list[Player]:
    """Drop players who cannot appear in an optimal roster.

    Two cuts. First, anything you cannot afford even by filling every other
    spot at the minimum bid. Second, for each position, keep only the best
    handful at each price point -- if two players cost the same and one scores
    more, the cheaper-scoring one is never worth taking, and the solver should
    not have to discover that.
    """
    if slots_to_fill <= 0:
        return [p for p in candidates if p.player_id in keep]

    affordable_cap = budget - (slots_to_fill - 1) * league.min_bid
    pool = [
        p for p in candidates
        if p.player_id in keep
        or price_by_id.get(p.player_id, float(league.min_bid)) <= affordable_cap
    ]

    by_position: dict[str, list[Player]] = {}
    for player in pool:
        by_position.setdefault(player.position, []).append(player)

    kept: list[Player] = []
    for position, group in by_position.items():
        # Sort by points descending; walk down keeping only players that are
        # cheaper than every better player seen so far (the efficient frontier),
        # plus a buffer of the top names regardless.
        group.sort(key=score, reverse=True)
        cheapest_so_far = float("inf")
        frontier: list[Player] = []
        for rank, player in enumerate(group):
            price = price_by_id.get(player.player_id, float(league.min_bid))
            if rank < _KEEP_PER_POSITION or price < cheapest_so_far or player.player_id in keep:
                frontier.append(player)
            cheapest_so_far = min(cheapest_so_far, price)
        kept.extend(frontier)
    return kept


def optimize_roster(
    board: ValuationBoard | Sequence[Valuation],
    league: LeagueConfig,
    *,
    budget: float | None = None,
    slots_to_fill: int | None = None,
    owned: Sequence[Player] = (),
    prices: Mapping[str, float] | None = None,
    exclude: Sequence[str] = (),
    require: Sequence[str] = (),
    value: Callable[[Player], float] | None = None,
) -> RosterPlan:
    """Best roster obtainable at the given prices.

    ``owned`` players are already yours: they cost nothing more and occupy no
    remaining slot. ``require`` forces player ids into the roster (used by the
    max-bid search); ``exclude`` removes them from consideration.
    """
    score = value or (lambda p: p.points)
    bench_value = _bench_valuer(board, league, score)
    price_by_id = _price_map(board, league, prices)

    owned_list = list(owned)
    owned_ids = {p.player_id for p in owned_list}
    excluded = set(exclude)
    required = set(require)

    candidates = [
        v.player
        for v in board
        if v.player.player_id not in owned_ids
        and v.player.player_id not in excluded
    ]

    if budget is None:
        budget = float(league.budget)
    if slots_to_fill is None:
        slots_to_fill = league.roster_size - len(owned_list)
    slots_to_fill = max(int(slots_to_fill), 0)

    candidates = _prune(
        candidates, league, budget, slots_to_fill, price_by_id, score, required
    )

    args = (
        candidates, owned_list, league, budget, slots_to_fill,
        price_by_id, score, bench_value, required,
    )
    if _HAVE_PULP:
        return _solve_ilp(*args)
    return _solve_greedy(*args)


def _assemble(
    roster: list[Player],
    additions: list[Player],
    league: LeagueConfig,
    budget: float,
    price_by_id: Mapping[str, float],
    score: Callable[[Player], float],
    bench_value: Callable[[Player], float],
    solver: str,
) -> RosterPlan:
    solved = best_lineup(roster, league.lineup, value=score)
    starting = solved.starter_ids
    bench_points = sum(
        bench_value(p) for p in roster if p.player_id not in starting
    )
    spend = sum(price_by_id.get(p.player_id, 0.0) for p in additions)
    return RosterPlan(
        roster=sorted(roster, key=score, reverse=True),
        additions=sorted(additions, key=score, reverse=True),
        lineup=solved,
        spend=spend,
        budget=budget,
        objective=solved.points + bench_points,
        solver=solver,
        prices={p.player_id: price_by_id.get(p.player_id, 0.0) for p in roster},
    )


def _solve_ilp(
    candidates: Sequence[Player],
    owned: list[Player],
    league: LeagueConfig,
    budget: float,
    slots_to_fill: int,
    price_by_id: Mapping[str, float],
    score: Callable[[Player], float],
    bench_value: Callable[[Player], float],
    require: set[str],
) -> RosterPlan:
    slots = [s for s in league.lineup if s.count > 0]
    everyone = list(owned) + list(candidates)
    owned_ids = {p.player_id for p in owned}

    problem = pulp.LpProblem("auction_roster", pulp.LpMaximize)

    # x[p]: p is on the roster.
    x = {p.player_id: pulp.LpVariable(f"x_{p.player_id}", cat="Binary") for p in everyone}

    # Players you already own, and players the caller pinned, are on the roster
    # by definition. This has to be an explicit constraint: PuLP resets the
    # bounds of a Binary variable to [0, 1], so a lowBound passed to the
    # constructor is silently discarded.
    for player in everyone:
        pid = player.player_id
        if pid in owned_ids or pid in require:
            problem += (x[pid] == 1, f"fixed_{pid}")

    # y[p, g]: p starts in slot group g.
    y: dict[tuple[str, int], "pulp.LpVariable"] = {}
    for player in everyone:
        for gi, slot in enumerate(slots):
            if slot.accepts(player.position):
                y[player.player_id, gi] = pulp.LpVariable(
                    f"y_{player.player_id}_{gi}", cat="Binary"
                )

    # Objective, linearised: every rostered player is credited with bench
    # value, and starting adds the difference between full and bench value.
    problem += pulp.lpSum(
        [bench_value(p) * x[p.player_id] for p in everyone]
        + [
            (score(p) - bench_value(p)) * y[p.player_id, gi]
            for p in everyone
            for gi in range(len(slots))
            if (p.player_id, gi) in y
        ]
    )

    # Exactly fill the remaining roster spots.
    problem += (
        pulp.lpSum([x[p.player_id] for p in candidates]) == slots_to_fill,
        "roster_spots",
    )

    # Stay under budget.
    problem += (
        pulp.lpSum(
            [price_by_id.get(p.player_id, 0.0) * x[p.player_id] for p in candidates]
        )
        <= budget,
        "budget",
    )

    # You can only start someone you rostered, and only in one slot.
    for player in everyone:
        starts = [
            y[player.player_id, gi]
            for gi in range(len(slots))
            if (player.player_id, gi) in y
        ]
        if starts:
            problem += pulp.lpSum(starts) <= x[player.player_id]

    # Respect each slot group's capacity.
    for gi, slot in enumerate(slots):
        members = [y[p.player_id, gi] for p in everyone if (p.player_id, gi) in y]
        if members:
            problem += pulp.lpSum(members) <= slot.count

    problem.solve(
        pulp.PULP_CBC_CMD(msg=False, timeLimit=_TIME_LIMIT, gapRel=_MIP_GAP)
    )

    # A time limit can stop CBC at a feasible-but-unproven solution, which is
    # still perfectly usable; only fall back when there is no solution at all.
    if any(x[p.player_id].value() is None for p in candidates):
        return _solve_greedy(
            candidates, owned, league, budget, slots_to_fill,
            price_by_id, score, bench_value, require,
        )

    additions = [p for p in candidates if (x[p.player_id].value() or 0) > 0.5]
    if len(additions) != slots_to_fill:
        return _solve_greedy(
            candidates, owned, league, budget, slots_to_fill,
            price_by_id, score, bench_value, require,
        )

    roster = list(owned) + additions
    return _assemble(
        roster, additions, league, budget, price_by_id, score, bench_value, "cbc"
    )


def _solve_greedy(
    candidates: Sequence[Player],
    owned: list[Player],
    league: LeagueConfig,
    budget: float,
    slots_to_fill: int,
    price_by_id: Mapping[str, float],
    score: Callable[[Player], float],
    bench_value: Callable[[Player], float],
    require: set[str],
) -> RosterPlan:
    """Marginal-value greedy plus a swap pass. Used when PuLP is unavailable."""
    roster = list(owned)
    additions: list[Player] = []
    remaining = budget
    pool = [p for p in candidates if p.player_id not in require]

    for pid in require:
        forced = next((p for p in candidates if p.player_id == pid), None)
        if forced is not None:
            roster.append(forced)
            additions.append(forced)
            remaining -= price_by_id.get(pid, 0.0)
            slots_to_fill -= 1

    def objective(members: Sequence[Player]) -> float:
        solved = best_lineup(members, league.lineup, value=score)
        starting = solved.starter_ids
        bench = sum(bench_value(p) for p in members if p.player_id not in starting)
        return solved.points + bench

    current = objective(roster)
    while slots_to_fill > 0:
        # Every remaining spot after this one still needs a minimum bid.
        affordable = remaining - (slots_to_fill - 1) * league.min_bid
        best_player = None
        best_ratio = float("-inf")
        best_gain = 0.0
        for player in pool:
            price = price_by_id.get(player.player_id, float(league.min_bid))
            if price > affordable:
                continue
            gain = objective(roster + [player]) - current
            ratio = gain / max(price, 1e-9)
            if ratio > best_ratio:
                best_ratio, best_player, best_gain = ratio, player, gain
        if best_player is None:
            break
        roster.append(best_player)
        additions.append(best_player)
        pool.remove(best_player)
        remaining -= price_by_id.get(best_player.player_id, float(league.min_bid))
        current += best_gain
        slots_to_fill -= 1

    # Swap pass: try replacing each pick with an affordable alternative.
    # Pinned players are off limits -- the caller asked for them by name.
    improved = True
    while improved:
        improved = False
        for i, held in enumerate(list(additions)):
            if held.player_id in require:
                continue
            held_price = price_by_id.get(held.player_id, float(league.min_bid))
            for player in pool:
                price = price_by_id.get(player.player_id, float(league.min_bid))
                if price - held_price > remaining:
                    continue
                trial = [p for p in roster if p.player_id != held.player_id]
                trial.append(player)
                score_trial = objective(trial)
                if score_trial > current + 1e-9:
                    roster = trial
                    additions[i] = player
                    pool.remove(player)
                    pool.append(held)
                    remaining -= price - held_price
                    current = score_trial
                    improved = True
                    break
            if improved:
                break

    return _assemble(
        roster, additions, league, budget, price_by_id, score, bench_value, "greedy"
    )


def max_bid(
    player: Player,
    board: ValuationBoard,
    league: LeagueConfig,
    *,
    budget: float,
    slots_to_fill: int,
    owned: Sequence[Player] = (),
    prices: Mapping[str, float] | None = None,
    exclude: Sequence[str] = (),
    value: Callable[[Player], float] | None = None,
) -> float:
    """The most you can pay for ``player`` and still come out ahead.

    Defined properly rather than by rule of thumb: compare the best roster you
    can build *with* this player at price ``c`` against the best roster you can
    build *without* them at all. The break-even ``c`` is your true walk-away
    number, and it already accounts for what the money would otherwise buy --
    which is why it drops as the board thins and rises when you are flush.

    Found by binary search on integer dollars; each probe is a roster solve.
    """
    score = value or (lambda p: p.points)
    excluded = list(exclude)

    # With no spot to put them in, no price is worth paying.
    if slots_to_fill <= 0:
        return 0.0

    without = optimize_roster(
        board, league, budget=budget, slots_to_fill=slots_to_fill,
        owned=owned, prices=prices, value=score,
        exclude=excluded + [player.player_id],
    )
    baseline = without.objective

    ceiling = int(budget - (slots_to_fill - 1) * league.min_bid)
    if ceiling < league.min_bid:
        return 0.0

    def objective_at(bid: int) -> float:
        trial_prices = dict(prices or {})
        trial_prices[player.player_id] = float(bid)
        plan = optimize_roster(
            board, league, budget=budget, slots_to_fill=slots_to_fill,
            owned=owned, prices=trial_prices, exclude=excluded,
            require=[player.player_id], value=score,
        )
        return plan.objective

    if objective_at(league.min_bid) < baseline - 1e-9:
        return 0.0
    if objective_at(ceiling) >= baseline - 1e-9:
        return float(ceiling)

    low, high = league.min_bid, ceiling  # worth it at low, not at high
    while high - low > 1:
        mid = (low + high) // 2
        if objective_at(mid) >= baseline - 1e-9:
            low = mid
        else:
            high = mid
    return float(low)
