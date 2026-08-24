"""Turning value-over-replacement into auction dollars.

The accounting identity that makes auction pricing work: every team must fill
every roster spot, and every dollar gets spent. So the money genuinely in play
is the league's total budget minus the minimum bid reserved for each spot that
must be filled. That surplus is distributed across drafted players in
proportion to their value over replacement.

During a live draft the same identity is re-applied to what is *left*:
remaining money over remaining value. That is what produces inflation -- when
the room underpays for the early studs, the leftover cash has to land
somewhere, and everyone still on the board gets more expensive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from .config import LeagueConfig
from .players import Player
from .replacement import ReplacementLevels, compute_replacement
from .scoring import valuation_points


@dataclass
class Valuation:
    """A priced player."""

    player: Player
    points: float
    replacement: float
    vor: float
    value: float          # dollar value at the league's baseline
    price: float          # inflation-adjusted market price

    @property
    def name(self) -> str:
        return self.player.name

    @property
    def position(self) -> str:
        return self.player.position

    @property
    def surplus(self) -> float:
        """Dollars of value above the current market price."""
        return self.value - self.price


@dataclass
class ValuationBoard:
    """Every priced player, plus the parameters used to price them."""

    valuations: list[Valuation]
    levels: ReplacementLevels
    dollars_per_point: float
    inflation: float = 1.0
    _by_id: dict[str, Valuation] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._by_id = {v.player.player_id: v for v in self.valuations}

    def __iter__(self):
        return iter(self.valuations)

    def __len__(self) -> int:
        return len(self.valuations)

    def get(self, player_id: str) -> Valuation | None:
        return self._by_id.get(player_id)

    def top(self, n: int = 25, position: str | None = None) -> list[Valuation]:
        pool = self.valuations
        if position:
            pool = [v for v in pool if v.position == position.upper()]
        return pool[:n]


def value_players(
    players: Sequence[Player],
    league: LeagueConfig,
    *,
    levels: ReplacementLevels | None = None,
    budget_pool: float | None = None,
    spots_to_fill: int | None = None,
    inflation: float = 1.0,
) -> ValuationBoard:
    """Price every player in dollars.

    ``budget_pool`` and ``spots_to_fill`` default to a full, undrafted league.
    Pass the live figures mid-draft to reprice against the money still on the
    table.
    """
    score: Callable[[Player], float] = lambda p: valuation_points(p, league)

    if levels is None:
        levels = compute_replacement(players, league, value=score)

    if budget_pool is None:
        budget_pool = float(league.total_budget)
    if spots_to_fill is None:
        spots_to_fill = league.drafted_players

    # Only players good enough to be rostered soak up value.
    ranked = sorted(players, key=score, reverse=True)
    draftable = ranked[: max(spots_to_fill, 0)]

    positive_vor = 0.0
    for player in draftable:
        vor = score(player) - levels.for_position(player.position)
        if vor > 0:
            positive_vor += vor

    reserved = spots_to_fill * league.min_bid
    surplus = max(budget_pool - reserved, 0.0)
    dollars_per_point = surplus / positive_vor if positive_vor > 0 else 0.0

    valuations: list[Valuation] = []
    for player in ranked:
        points = score(player)
        replacement = levels.for_position(player.position)
        vor = points - replacement
        raw = league.min_bid + max(vor, 0.0) * dollars_per_point
        valuations.append(
            Valuation(
                player=player,
                points=points,
                replacement=replacement,
                vor=vor,
                value=raw,
                price=max(raw * inflation, float(league.min_bid)),
            )
        )

    valuations.sort(key=lambda v: v.value, reverse=True)
    return ValuationBoard(
        valuations=valuations,
        levels=levels,
        dollars_per_point=dollars_per_point,
        inflation=inflation,
    )
