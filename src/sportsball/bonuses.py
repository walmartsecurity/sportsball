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
  whole distribution of single-game outcomes. Game yardage is modelled as a
  gamma distribution, which fits the observed tail markedly better than the
  lognormal this model started with -- against five seasons of play-by-play,
  lognormal over-predicted 200-yard games by 88-292% depending on position,
  gamma by 34-88% on an event rare enough to be worth under a point a season.

  The spread is not constant either. A player's coefficient of variation falls
  as volume rises -- a 97-yards-per-game back swings far less than a
  33-yards-per-game back (0.43 against 0.83) -- so it is modelled as a power
  function of the projected per-game mean.
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


def gamma_tail(mean: float, cv: float, threshold: float) -> float:
    """P(X >= threshold) for a gamma with the given mean and CV."""
    if mean <= 0.0 or threshold <= 0.0:
        return 0.0
    if cv <= 0.0:
        return 1.0 if mean >= threshold else 0.0
    shape = 1.0 / (cv * cv)
    return _upper_gamma(shape, threshold / (mean / shape))


def _upper_gamma(a: float, x: float) -> float:
    """Regularised upper incomplete gamma Q(a, x), by series or continued
    fraction depending on which converges faster."""
    if x <= 0.0:
        return 1.0
    if x < a + 1.0:
        total = term = 1.0 / a
        for n in range(1, 500):
            term *= x / (a + n)
            total += term
            if abs(term) < abs(total) * 1e-13:
                break
        return 1.0 - total * math.exp(-x + a * math.log(x) - math.lgamma(a))
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for i in range(1, 500):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-13:
            break
    return h * math.exp(-x + a * math.log(x) - math.lgamma(a))


def volume_adjusted_cv(
    per_game_mean: float, cv_ref: float, ref_volume: float, slope: float,
    floor: float, ceiling: float,
) -> float:
    """Coefficient of variation at a given per-game volume.

    Bigger workloads are steadier, so the spread declines with the mean. The
    result is clamped, since the power law is fitted over a limited range of
    volumes and extrapolates badly outside it.
    """
    if per_game_mean <= 0.0 or ref_volume <= 0.0:
        return cv_ref
    cv = cv_ref * (per_game_mean / ref_volume) ** slope
    return min(max(cv, floor), ceiling)


def _threshold_tail(mean: float, cv: float, threshold: float, dist: str) -> float:
    if dist == "lognormal":
        return lognormal_tail(mean, cv, threshold)
    return gamma_tail(mean, cv, threshold)


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
    dist: str,
) -> float:
    """Expected points from thresholds that can fire once per game.

    Thresholds stack: a 400-yard game clears both the 300 and 400 bonus.
    """
    if per_game_mean <= 0.0 or games <= 0.0:
        return 0.0
    return points * games * sum(
        _threshold_tail(per_game_mean, cv, t, dist) for t in thresholds
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
        per_game = stats.pass_yds / games
        breakdown["pass_yardage_games"] = _game_bonus(
            per_game,
            volume_adjusted_cv(
                per_game, rules.pass_yds_cv, rules.pass_yds_cv_ref,
                rules.pass_yds_cv_slope, rules.cv_floor, rules.cv_ceiling,
            ),
            rules.pass_game_thresholds,
            games,
            rules.points,
            rules.distribution,
        )

    if games > 0 and stats.scrimmage_yds > 0:
        per_game = stats.scrimmage_yds / games
        breakdown["scrimmage_yardage_games"] = _game_bonus(
            per_game,
            volume_adjusted_cv(
                per_game,
                rules.scrimmage_cv.get(player.position, 0.63),
                rules.scrimmage_cv_ref.get(player.position, 47.0),
                rules.scrimmage_cv_slope.get(player.position, -0.4),
                rules.cv_floor,
                rules.cv_ceiling,
            ),
            rules.scrimmage_game_thresholds,
            games,
            rules.points,
            rules.distribution,
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
        rate *= rules.rush_40_position_multiplier.get(player.position, 1.0)
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
