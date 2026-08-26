"""What a player will actually go for, low to high.

A single price is the wrong shape for an auction. The model's number is a
point estimate of a thing that happens once, in a room of twelve people, and
quoting it alone invites the two mistakes that cost the most: passing on a
player who was never going to reach your number anyway, and sitting in a
bidding war you had already lost.

So every player gets three figures instead of one:

* **Low** -- what he costs if the room lets him slide.
* **Likely** -- the repriced market number, which is the model's best guess.
* **High** -- what he costs when two teams want him.

Read against your own walk-away price, those three answer the only question
that matters at nomination time. Your max above the high end means he should
be yours and the only risk is paying more than you had to. Your max below the
low end means he is not yours at any price the room will accept, and watching
the bidding is a waste of the one thing you cannot get back mid-draft, which is
attention. In between is where an auction is actually played.

**Where the width comes from.** Two things move a sale price off the model.

The first is the room's disagreement about the player, and that is not the same
size for everyone: a receiver whose points come from explosive plays draws a
much wider spread of opinion than one with the same projection built out of
volume. :func:`sportsball.scoring.season_sigma` already measures exactly that
spread for the ceiling model, so the range borrows it rather than inventing a
second uncertainty model, and scales it against the typical player on the board
so an ordinary player gets the ordinary width.

The second is how loose the room itself is. Some auctions track the model
closely and some scatter wildly around it, and which one you are sitting in is
*measurable* from the sales already on the board -- the same discipline the
positional read in :mod:`sportsball.suggest` follows. Until there are enough
sales to say, a stated prior carries it, and the measurement takes over as the
draft fills in.

The dollar width then falls out of the pricing identity rather than being
applied to the price directly, which matters more than it sounds. Dollars are
proportional to value *over replacement*, so the same proportional wobble in
points is worth a lot of money at the top of the board and almost none at the
bottom -- a fifteen-dollar spread on a stud and a two-dollar spread on a flier,
which is how auctions really behave.

**Where it gets clamped.** Two hard facts about a real room beat the model
whenever they bind. A price needs someone able to pay it, so nothing sells for
more than the largest bid any single team can still afford -- late in a draft,
with the wallets empty, that ceiling binds long before the model's number does.
And a player already under bidding will not go for less than the standing bid,
whatever the model thinks he is worth.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median
from typing import TYPE_CHECKING, Sequence

from .players import Player
from .scoring import season_sigma
from .suggest import quick_max_bid
from .valuation import ValuationBoard

if TYPE_CHECKING:  # pragma: no cover
    from .draft import DraftState

# How far either side of the likely price the range reaches, in standard
# deviations. One sigma spans the middle two thirds of the distribution, which
# is the honest read of "what he usually goes for" -- wide enough to contain
# the ordinary result, narrow enough that landing outside it is news.
RANGE_Z = 1.0

# How far the room's implied opinion of a player typically sits from the
# model's, on the log scale, before this room has shown us any of its own. A
# room that priced straight off the board would be 0; this says a typical sale
# implies a season within about 10% of the projection either way. Stated in
# points rather than dollars because that is where it gets applied -- the
# dollar spread comes out of the pricing identity, and is wider.
ROOM_SIGMA_PRIOR = 0.10

# Sales before the room's own measured scatter outweighs the prior. Price
# dispersion needs a real sample -- three sales can look tight or wild by luck.
ROOM_SIGMA_WEIGHT = 8

# Sales at a position before its measured habit is taken at face value. The
# habit shifts a range; the scatter widens it, and a shift needs less evidence
# than a shape, so this is the smaller of the two.
HABIT_WEIGHT = 6

# Sales below this share of a team's budget are left out of the scatter
# measurement. Running a cheap sale backwards through the identity is unstable
# for reasons that have nothing to do with the room's discipline -- near
# replacement a couple of dollars either way implies a wildly different season,
# and a dollar player going for three is no information at all.
MEANINGFUL_SHARE = 0.02


@dataclass
class BidRange:
    """What a player goes for, and what he is worth to you."""

    player: Player
    low: float
    likely: float
    high: float
    max_bid: float
    sigma: float = 0.0

    @property
    def name(self) -> str:
        return self.player.name

    @property
    def position(self) -> str:
        return self.player.position

    @property
    def priced_out(self) -> bool:
        """Even the friendly end of the room's range is past your number."""
        return self.max_bid < self.low

    @property
    def should_win(self) -> bool:
        """Your number covers the whole range, war included."""
        return self.max_bid >= self.high

    @property
    def at_minimum(self) -> bool:
        """The whole range sits at the minimum bid.

        True for everyone near replacement level, and it is not a failure of
        the model so much as the honest answer: below the point where value
        over replacement is worth real money, what a player fetches is decided
        by whose turn it is to throw a dollar, not by anything on this board.
        """
        return self.high <= self.low

    @property
    def span(self) -> str:
        if self.at_minimum:
            return f"${self.low:,.0f}"
        return f"${self.low:,.0f}-${self.high:,.0f}"

    def verdict(self) -> str:
        """One line on where your walk-away sits against the room's range."""
        if self.max_bid < 1:
            return "Not worth a bid from you at these prices."
        if self.at_minimum:
            return ("Minimum-bid territory — nothing here is worth a raise, "
                    "so take him if he is still there and nobody wants him.")
        if self.priced_out:
            return (f"Priced out — the room starts around ${self.low:,.0f} and "
                    f"you stop at ${self.max_bid:,.0f}. Let him go.")
        if self.should_win:
            return (f"Should be yours — ${self.max_bid:,.0f} covers the top of "
                    f"the range, bidding war included.")
        if self.max_bid >= self.likely:
            return (f"Winnable — you can pay the ${self.likely:,.0f} he ought to "
                    f"go for, up to ${self.max_bid:,.0f}, but not a war.")
        return (f"Long shot — he ought to reach ${self.likely:,.0f} and you stop "
                f"at ${self.max_bid:,.0f}. Only yours if the room is asleep.")


def reference_sigma(board: ValuationBoard, spots: int) -> float:
    """The season spread of a typical drafted player.

    The width of a range is set relative to this, so that a player of ordinary
    uncertainty gets the ordinary width and the tilt is genuinely about *him*
    rather than about the league's scoring format.
    """
    pool = [season_sigma(v.player) for v in board.top(max(spots, 1))]
    pool = [s for s in pool if s > 0]
    return median(pool) if pool else 0.0


def room_scatter(state: "DraftState") -> tuple[float, int]:
    """How loosely this room prices, on the log scale, and the sample behind it.

    Measured in *points*, by running each completed sale backwards through the
    pricing identity: the price the room paid implies a value over replacement,
    which implies a season, and the gap from the projection is the room's
    disagreement with the model about that player. Measuring where the number
    is used is what keeps the prior above meaning what it says -- a scatter
    measured in dollars and then applied to points would be magnified a second
    time on the way back out.

    Both sides are read off the pre-draft board, so the ruler does not change
    as the draft inflates or deflates. The spread is taken around the room's
    own average rather than around the model, because the average shift is
    already carried by the positional habit; what is wanted here is what is
    left over.
    """
    league = state.league
    floor = max(league.min_bid, league.budget * MEANINGFUL_SHARE)
    baseline = state.baseline_board()
    rate = baseline.dollars_per_point

    ratios: list[float] = []
    if rate > 0:
        for sale in state.sales:
            listed = baseline.get(sale.player_id)
            if listed is None or listed.value < floor or sale.price < floor:
                continue
            if listed.points <= 0:
                continue
            implied = listed.replacement + (sale.price - league.min_bid) / rate
            if implied <= 0:
                continue
            ratios.append(math.log(implied / listed.points))

    n = len(ratios)
    if n < 2:
        return ROOM_SIGMA_PRIOR, n
    mean = sum(ratios) / n
    # Population spread, not the sample estimator: this is a description of the
    # sales in front of us, not an inference about a wider population of them.
    measured = math.sqrt(sum((r - mean) ** 2 for r in ratios) / n)
    blended = (n * measured ** 2 + ROOM_SIGMA_WEIGHT * ROOM_SIGMA_PRIOR ** 2) / (
        n + ROOM_SIGMA_WEIGHT
    )
    return math.sqrt(blended), n


def position_habit(state: "DraftState", position: str) -> float:
    """What the room pays for this position, as a multiple of model value.

    Shrunk toward 1.0 by how many sales are behind it, so three cheap running
    backs do not rewrite the price of every running back left on the board.
    """
    habit = state.room_pricing().get(position)
    if not habit:
        return 1.0
    ratio, n = habit
    return (n * ratio + HABIT_WEIGHT * 1.0) / (n + HABIT_WEIGHT)


def _dollars(points: float, replacement: float, rate: float, min_bid: float) -> float:
    """The pricing identity, run on one player's points."""
    return min_bid + max(points - replacement, 0.0) * rate


def bid_range(
    state: "DraftState",
    board: ValuationBoard,
    player: Player,
    *,
    max_bid: float | None = None,
    sigma_ref: float | None = None,
    scatter: float | None = None,
) -> BidRange | None:
    """Low, likely and high sale prices for one player, plus your walk-away.

    The optional arguments exist so a whole-board scan pays for the room-level
    figures once instead of per player; on their own they change nothing.
    Returns ``None`` for a player the board has no price for, which is to say
    one already sold.
    """
    league = state.league
    listed = board.get(player.player_id)
    if listed is None:
        return None

    if sigma_ref is None:
        sigma_ref = reference_sigma(board, league.drafted_players)
    if scatter is None:
        scatter, _ = room_scatter(state)

    # The room's looseness, tilted by how uncertain this particular player is
    # against the typical one. Equal uncertainty means the typical width.
    tilt = (season_sigma(player) / sigma_ref) if sigma_ref > 0 else 1.0
    sigma = scatter * tilt

    habit = position_habit(state, player.position)
    rate = board.dollars_per_point
    points = listed.points

    low = habit * _dollars(points * math.exp(-RANGE_Z * sigma),
                           listed.replacement, rate, league.min_bid)
    high = habit * _dollars(points * math.exp(RANGE_Z * sigma),
                            listed.replacement, rate, league.min_bid)
    likely = habit * listed.price

    # A price needs someone able to pay it, and a player under the hammer will
    # not go backwards from the bid already standing on him.
    ceiling = max(state.richest_bid(), float(league.min_bid))
    floor = max(float(league.min_bid), state.open_bids.get(player.player_id, 0.0))

    low = min(max(low, floor), ceiling)
    high = min(max(high, low), ceiling)
    likely = min(max(likely, low), high)

    if max_bid is None:
        max_bid = quick_max_bid(state, board, player)

    return BidRange(
        player=player,
        low=round(low),
        likely=round(likely),
        high=round(high),
        max_bid=round(max_bid),
        sigma=sigma,
    )


def bid_ranges(
    state: "DraftState",
    board: ValuationBoard | None = None,
    players: Sequence[Player] | None = None,
    limit: int | None = None,
) -> list[BidRange]:
    """Ranges for a whole slice of the board, in board order."""
    board = board or state.board()
    if players is None:
        pool = [v.player for v in board]
        if limit is not None:
            pool = pool[:limit]
    else:
        pool = list(players)

    sigma_ref = reference_sigma(board, state.league.drafted_players)
    scatter, _ = room_scatter(state)
    out: list[BidRange] = []
    for player in pool:
        row = bid_range(state, board, player,
                        sigma_ref=sigma_ref, scatter=scatter)
        if row is not None:
            out.append(row)
    return out
