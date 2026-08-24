"""Projecting SFB16's "video game bonuses".

These bonuses are worth 10 points each in a format where a 100-yard, 6-catch
game is worth roughly 13 points before them. They are not a rounding error:
they are a large share of a boom player's value, and because two of them are
single-game thresholds, they reward game-to-game *variance* rather than season
totals. A back who runs for 100 yards eight times and 20 yards nine times
earns 80 bonus points; one who runs for 65 every week earns none, on nearly
identical season totals.

Two distinct models are needed:

* **Game bonuses** (300/400 passing yards, 100/200 scrimmage yards) fire at
  most once per game per threshold, so their expected value depends on the
  whole distribution of single-game outcomes. Game yardage is modelled as
  lognormal -- it is non-negative, right-skewed, and a decent empirical fit --
  parameterised by the projected per-game mean and a positional coefficient
  of variation.
* **Play bonuses** (40+ yard pass/rush plays, 20+ yard receptions) fire on
  every qualifying play, so expected value is linear in volume: the expected
  count of such plays times the bonus. Rates scale with efficiency, since
  explosive plays are precisely what drives yards per attempt above average.

Every constant is exposed through :class:`~sportsball.config.BonusRules`.
"""

from __future__ import annotations

import math
from typing import Mapping

from .config import BonusRules
from .players import Player, StatLine


def _normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def lognormal_tail(mean: float, cv: float, threshold: float) -> float:
    """P(X >= threshold) for a lognormal with the given mean and CV.

    Returns 0 for a non-positive mean, and clamps a non-positive CV to a
    degenerate point mass (all-or-nothing at the mean).
    """
    if mean <= 0.0 or threshold <= 0.0:
        return 0.0
    if cv <= 0.0:
        return 1.0 if mean >= threshold else 0.0
    sigma_sq = math.log(1.0 + cv * cv)
    sigma = math.sqrt(sigma_sq)
    mu = math.log(mean) - sigma_sq / 2.0
    return _normal_cdf((mu - math.log(threshold)) / sigma)


def _game_bonus(
    per_game_mean: float,
    cv: float,
    thresholds: tuple[float, ...],
    games: float,
    points: float,
) -> float:
    """Expected points from thresholds that can fire once per game.

    Thresholds stack: a 400-yard game clears both the 300 and 400 bonus.
    """
    if per_game_mean <= 0.0 or games <= 0.0:
        return 0.0
    return points * games * sum(
        lognormal_tail(per_game_mean, cv, t) for t in thresholds
    )


def _expected_plays(
    opportunities: float, rate: float, games: float, once_per_game: bool
) -> float:
    """Expected number of *scoring events* from big plays.

    With the default per-play reading, that is simply volume times rate. If a
    league instead pays the bonus at most once per game, big plays within a
    game stop stacking: model per-game occurrences as Poisson and count the
    probability of at least one.
    """
    expected = opportunities * rate
    if not once_per_game or games <= 0 or expected <= 0:
        return expected
    per_game = expected / games
    return games * (1.0 - math.exp(-per_game))


def _efficiency_multiplier(actual: float, base: float, elasticity: float) -> float:
    """Scale a big-play rate by how efficient the player is projected to be.

    Explosive plays and yards-per-opportunity move together, so a back at 5.2
    yards per carry should be credited with more 40-yard runs than one at 3.8.
    The relationship is convex, hence the elasticity exponent. Clamped to keep
    small-sample or malformed inputs from producing absurd rates.
    """
    if actual <= 0.0 or base <= 0.0:
        return 0.0
    return min((actual / base) ** elasticity, 4.0)


def project_bonuses(
    player: Player, rules: BonusRules
) -> tuple[float, dict[str, float]]:
    """Expected bonus points for a season, plus a per-bonus breakdown."""
    if not rules.enabled:
        return 0.0, {}

    stats: StatLine = player.stats
    games = max(stats.games, 0.0)
    breakdown: dict[str, float] = {}

    # --- Single-game yardage thresholds -------------------------------------
    if games > 0 and stats.pass_yds > 0:
        breakdown["pass_yardage_games"] = _game_bonus(
            stats.pass_yds / games,
            rules.pass_yds_cv,
            rules.pass_game_thresholds,
            games,
            rules.points,
        )

    if games > 0 and stats.scrimmage_yds > 0:
        cv = rules.scrimmage_cv.get(player.position, 0.65)
        breakdown["scrimmage_yardage_games"] = _game_bonus(
            stats.scrimmage_yds / games,
            cv,
            rules.scrimmage_game_thresholds,
            games,
            rules.points,
        )

    # --- Explosive plays ----------------------------------------------------
    if stats.pass_att > 0:
        rate = rules.pass_40_rate * _efficiency_multiplier(
            stats.yards_per_attempt, rules.pass_40_ypa_base, rules.pass_40_elasticity
        )
        breakdown["pass_40_plays"] = rules.points * _expected_plays(
            stats.pass_att, rate, games, rules.play_bonus_once_per_game
        )

    if stats.rush_att > 0:
        rate = rules.rush_40_rate * _efficiency_multiplier(
            stats.yards_per_carry, rules.rush_40_ypc_base, rules.rush_40_elasticity
        )
        breakdown["rush_40_plays"] = rules.points * _expected_plays(
            stats.rush_att, rate, games, rules.play_bonus_once_per_game
        )

    if stats.receptions > 0:
        rate = rules.rec_20_rate * _efficiency_multiplier(
            stats.yards_per_reception, rules.rec_20_ypr_base, rules.rec_20_elasticity
        )
        breakdown["rec_20_plays"] = rules.points * _expected_plays(
            stats.receptions, rate, games, rules.play_bonus_once_per_game
        )

    total = sum(breakdown.values())
    return total, breakdown
