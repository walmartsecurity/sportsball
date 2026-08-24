"""What to actually bid on next.

A ranked list of "best players available" would be close to meaningless in an
auction. Pricing sets every tier to the same value over replacement per dollar,
so at list price one player is as good a deal as the next, and the optimizer is
genuinely indifferent between them. Presenting the top of the board as a
recommendation would dress that indifference up as insight.

Real edge comes from four places, and each is checked separately here:

1. **Price.** The room is not a pricing model. A player available under your
   walk-away number is the whole edge, and everything else is commentary.
2. **Fit.** Value you cannot start is worth a fraction of its price. A third
   quarterback in a format that starts two is a bench body however good the
   projection looks.
3. **Scarcity.** Only the quarterback slots are capped, so quarterbacks are the
   one position that can run out from under you. When fewer startable ones
   remain than there are jobs to fill, waiting gets expensive.
4. **What the room is doing.** Rooms have habits: they chase running backs,
   they sleep on tight ends. Comparing realized sale prices against the model's
   values, position by position, is the edge that actually shows up mid-draft,
   and it is measured rather than assumed.

Note what is deliberately absent. Falling prices are not an edge: when the room
overspends early, everything left gets cheaper for everyone at once, including
you. An edge needs your money to go further than theirs, which is why the
break-even below compares your rate against the market's rather than watching
prices drop.

The break-even price here is the closed form -- see the module docstring in
:mod:`sportsball.optimize` for the exact integer program, which the ``max``
command uses for a single player. The closed form is what makes scanning the
whole board fast enough to be worth doing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Mapping, Sequence

from .lineup import best_lineup
from .players import Player
from .valuation import ValuationBoard

if TYPE_CHECKING:  # pragma: no cover
    from .draft import DraftState

# A position's market read needs a real sample before it says anything.
MARKET_SAMPLE = 3
# Below this share of a position's model value, the room is discounting it.
DISCOUNT_THRESHOLD = 0.9
# Never suggest more of one position than this, so a single underpriced
# position cannot crowd out everything else.
PER_POSITION_CAP = 3


@dataclass
class Suggestion:
    """One player worth bidding on, and why."""

    player: Player
    price: float
    max_bid: float
    edge: float
    reasons: list[str] = field(default_factory=list)
    urgent: bool = False
    discount: float = 0.0

    @property
    def name(self) -> str:
        return self.player.name

    @property
    def position(self) -> str:
        return self.player.position


def room_pricing(state: "DraftState") -> dict[str, tuple[float, int]]:
    """How the room is paying for each position, as a share of model value."""
    baseline = state.baseline_board()
    grouped: dict[str, list[tuple[float, float]]] = {}
    for sale in state.sales:
        player = state._by_id.get(sale.player_id)
        listed = baseline.get(sale.player_id) if player else None
        if player is None or listed is None:
            continue
        grouped.setdefault(player.position, []).append((sale.price, listed.value))

    out: dict[str, tuple[float, int]] = {}
    for position, rows in grouped.items():
        if len(rows) < MARKET_SAMPLE:
            continue
        worth = sum(value for _, value in rows)
        if worth <= 0:
            continue
        out[position] = (sum(price for price, _ in rows) / worth, len(rows))
    return out


def personal_rate(
    state: "DraftState", board: ValuationBoard, exclude_id: str | None = None
) -> float:
    """Dollars per point of value that *your* remaining money has to pay.

    Your budget must fill your open spots. When it exceeds what those spots can
    absorb at market prices, the money has nowhere else to go and your bids
    should rise to match.
    """
    open_slots = state.my_open_slots
    if open_slots <= 0:
        return 0.0
    pool = [v for v in board if v.player.player_id != exclude_id][:open_slots]
    absorbable = sum(max(v.vor, 0.0) for v in pool)
    surplus = max(state.my_budget - open_slots * state.league.min_bid, 0.0)
    return surplus / absorbable if absorbable > 0 else 0.0


def would_start(state: "DraftState", player: Player) -> bool:
    """Would this player crack your lineup, or only sit on your bench?"""
    solved = best_lineup(
        [*state.my_roster, player], state.league.lineup, value=state._score
    )
    return player.player_id in solved.starter_ids


def quick_max_bid(
    state: "DraftState", board: ValuationBoard, player: Player
) -> float:
    """Break-even price in closed form, for scanning the whole board."""
    league = state.league
    if state.my_open_slots <= 0:
        return 0.0
    cap = int(state.max_affordable_bid())
    if cap < league.min_bid:
        return 0.0
    rate = max(board.dollars_per_point, personal_rate(state, board, player.player_id))
    vor = max(board.get(player.player_id).vor if board.get(player.player_id) else 0.0, 0.0)
    if not would_start(state, player):
        vor *= league.bench_weight
    return min(float(cap), max(float(league.min_bid), round(league.min_bid + vor * rate)))


def suggestions(
    state: "DraftState", board: ValuationBoard | None = None, limit: int = 6
) -> list[Suggestion]:
    """Players worth bidding on right now, most compelling first."""
    league = state.league
    if state.my_open_slots <= 0:
        return []
    board = board or state.board()
    cap = state.max_affordable_bid()

    # Below this, a gap between price and walk-away is rounding, not an edge.
    meaningful = max(league.min_bid, round(league.budget * 0.01))

    qb_slots = league.max_starters_at("QB")
    lineup_now = best_lineup(state.my_roster, league.lineup, value=state._score)
    my_qbs = sum(1 for p in lineup_now.starters if p.position == "QB")
    need_qb = my_qbs < qb_slots
    qb_left = sum(1 for v in board if v.position == "QB" and v.vor > 0)
    qb_jobs = max(
        league.teams * qb_slots
        - sum(1 for s in state.sales if state._by_id[s.player_id].position == "QB"),
        0,
    )
    squeeze = qb_left <= qb_jobs
    room = room_pricing(state)

    rows: list[Suggestion] = []
    for v in board:
        price = round(v.price)
        if price > cap:
            continue
        if not would_start(state, v.player):
            continue
        bid = quick_max_bid(state, board, v.player)
        edge = bid - price
        reasons: list[str] = []
        if edge >= meaningful:
            reasons.append(f"${edge:,.0f} under your walk-away")

        habit = room.get(v.position)
        discount = 0.0
        if habit and habit[0] < DISCOUNT_THRESHOLD:
            discount = 1.0 - habit[0]
            reasons.append(
                f"room is paying {habit[0]:.0%} of value for "
                f"{v.position}s ({habit[1]} sold)"
            )
        if v.position == "QB" and need_qb:
            reasons.append("fills a superflex slot")
            if squeeze:
                reasons.append(f"only {qb_left} startable left for {qb_jobs} jobs")
        if not reasons:
            continue
        rows.append(Suggestion(
            player=v.player, price=price, max_bid=bid,
            edge=edge if edge >= meaningful else 0.0,
            reasons=reasons, discount=discount,
            urgent=v.position == "QB" and need_qb and squeeze,
        ))

    rows.sort(key=lambda r: (r.urgent, r.edge, r.discount,
                             board.get(r.player.player_id).vor), reverse=True)

    free: dict[str, int] = {}
    kept: list[Suggestion] = []
    for row in rows:
        position = row.position
        if position not in free:
            started = sum(1 for p in lineup_now.starters if p.position == position)
            free[position] = min(
                league.max_starters_at(position) - started, PER_POSITION_CAP
            )
        if free[position] <= 0:
            continue
        free[position] -= 1
        kept.append(row)
        if len(kept) >= limit:
            break
    return kept


def pacing(state: "DraftState", any_edge: bool) -> str:
    """The number that helps when nothing on the board stands out."""
    open_slots = state.my_open_slots
    if open_slots <= 0:
        return "Your roster is full."
    per_slot = (state.my_budget - open_slots * state.league.min_bid) / open_slots
    lead = "" if any_edge else "Nothing on the board is mispriced for you right now. "
    plural = "" if open_slots == 1 else "s"
    return (
        f"{lead}You have ${state.my_budget:,.0f} for {open_slots} spot{plural} — "
        f"about ${per_slot:,.0f} of spending money each. "
        "Bid on whoever the room lets slide under the market price."
    )
