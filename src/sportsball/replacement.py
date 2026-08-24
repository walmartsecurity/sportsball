"""Replacement level: what a position is worth *relative to free agents*.

Conventional value-over-replacement math assumes fixed starting slots -- "the
12th best quarterback is replacement level in a 12-team league". SFB16 has no
positional minimums, so that shortcut breaks entirely: a team can start zero
quarterbacks and eight tight ends if it wants to. Replacement level has to be
derived from the lineup rules rather than assumed.

The approach here: solve for the set of players who would actually start
somewhere in the league if every roster were optimal, then define replacement
at each position as the best player at that position who does *not* make that
cut. That is precisely the free-agent alternative -- the guy you could have
for a dollar instead -- and it falls out correctly for interchangeable
positions without any special-casing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from .config import LeagueConfig
from .lineup import best_lineup, scale_lineup
from .players import Player


@dataclass
class ReplacementLevels:
    """Replacement point totals by position, plus the supporting detail."""

    by_position: dict[str, float]
    league_starters: list[Player]
    marginal_starter: float

    def for_position(self, position: str) -> float:
        return self.by_position.get(position, self.marginal_starter)


def compute_replacement(
    players: Sequence[Player],
    league: LeagueConfig,
    *,
    value: Callable[[Player], float] | None = None,
) -> ReplacementLevels:
    """Derive replacement level from the league's actual lineup rules."""
    score = value or (lambda p: p.points)
    eligible = [p for p in players if p.position in league.positions_in_play()]
    if not eligible:
        return ReplacementLevels({}, [], 0.0)

    league_lineup = scale_lineup(league.lineup, league.teams)
    solved = best_lineup(eligible, league_lineup, value=score)
    starting_ids = solved.starter_ids

    by_position: dict[str, float] = {}
    for position in league.positions_in_play():
        pool = sorted(
            (p for p in eligible if p.position == position),
            key=score,
            reverse=True,
        )
        if not pool:
            continue
        benched = [p for p in pool if p.player_id not in starting_ids]
        if benched:
            by_position[position] = score(benched[0])
        else:
            # Every player at this position starts somewhere; the position is
            # scarce enough that the worst starter is the effective floor.
            by_position[position] = score(pool[-1])

    marginal = min((score(p) for p in solved.starters), default=0.0)
    return ReplacementLevels(
        by_position=by_position,
        league_starters=solved.starters,
        marginal_starter=marginal,
    )


def value_over_replacement(
    player: Player,
    levels: ReplacementLevels,
    *,
    value: Callable[[Player], float] | None = None,
) -> float:
    score = value or (lambda p: p.points)
    return score(player) - levels.for_position(player.position)
