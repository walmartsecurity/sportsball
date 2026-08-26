"""Turn stat projections into fantasy points under a league's rules."""

from __future__ import annotations

import math

from typing import Iterable, Sequence

from .bonuses import project_bonuses
from .config import FirstDownModel, LeagueConfig, ScoringRules
from .players import Player, StatLine

# Season-level coefficient of variation used by the upside model.
_SEASON_CV_BASE = 0.22
_SEASON_CV_BONUS_SLOPE = 0.25


def estimate_first_downs(
    player: Player, model: FirstDownModel
) -> tuple[float, float]:
    """(rushing, receiving) first downs, using projected counts when present.

    Public projections almost never include first downs, but SFB16 pays half a
    point for each (and a point and a half for a tight end), so they cannot be
    ignored. Estimate from volume at positional rates, and count touchdowns as
    first downs since a score always converts.
    """
    stats: StatLine = player.stats

    if stats.rush_first_downs is not None:
        rush_fd = stats.rush_first_downs
    else:
        rate = model.per_rush.get(player.position, 0.22)
        rush_fd = stats.rush_att * rate
        if model.touchdowns_count_as_first_down:
            # Touchdown runs are already inside the per-carry rate on average;
            # only credit scores beyond what the rate implies.
            rush_fd = max(rush_fd, min(stats.rush_att, rush_fd + stats.rush_td * 0.5))

    if stats.rec_first_downs is not None:
        rec_fd = stats.rec_first_downs
    else:
        rate = model.per_reception.get(player.position, 0.50)
        rec_fd = stats.receptions * rate
        if model.touchdowns_count_as_first_down:
            rec_fd = max(rec_fd, min(stats.receptions, rec_fd + stats.rec_td * 0.5))

    return rush_fd, rec_fd


def base_points(player: Player, rules: ScoringRules, model: FirstDownModel) -> float:
    """Points from ordinary scoring, before any video-game bonuses."""
    stats = player.stats
    rush_fd, rec_fd = estimate_first_downs(player, model)

    total = 0.0
    total += stats.pass_yds * rules.pass_yds
    total += stats.pass_td * rules.pass_td
    total += stats.interceptions * rules.interception
    total += stats.rush_yds * rules.rush_yds
    total += stats.rush_td * rules.rush_td
    total += stats.rec_yds * rules.rec_yds
    total += stats.rec_td * rules.rec_td
    total += stats.fumbles_lost * rules.fumble_lost
    total += stats.two_point * rules.two_point

    # Receptions, plus any positional premium (SFB16: tight ends get +1).
    per_reception = rules.reception + rules.reception_bonus.get(player.position, 0.0)
    total += stats.receptions * per_reception

    # First downs, likewise premium-adjusted.
    fd_premium = rules.first_down_bonus.get(player.position, 0.0)
    total += rush_fd * (rules.rush_first_down + fd_premium)
    total += rec_fd * (rules.rec_first_down + fd_premium)

    return total


def score_player(player: Player, league: LeagueConfig) -> Player:
    """Score one player in place and return it.

    A projection that arrives already scored in this league's rules is taken as
    given for its *total*. That is the right call when the source computed it
    under the same scoring -- it will have news this model does not -- but it
    means the video game bonuses come from *their* model rather than the fitted
    one here, so a total scored under some other ruleset will be silently wrong
    for SFB16. The split between ordinary and bonus points stays ours, because
    nothing downstream can recover it from a single number.
    """
    player.base_points = base_points(player, league.scoring, league.first_downs)
    player.bonus_points, player.bonus_breakdown = project_bonuses(
        player, league.bonuses
    )
    player.points = player.base_points + player.bonus_points

    if player.supplied_points is not None:
        # Take their total, keep our split. The ceiling model reads
        # bonus_points / points to decide how much spread a season carries, so
        # dropping the whole total into base_points would tell it that nobody's
        # points come from big plays and flatten every ceiling to the same
        # curve. Scaling both parts onto their total keeps that share intact.
        scale = (player.supplied_points / player.points) if player.points > 0 else 0.0
        player.base_points *= scale
        player.bonus_points *= scale
        player.bonus_breakdown = {k: v * scale for k, v in player.bonus_breakdown.items()}
        if player.points <= 0:
            player.base_points = player.supplied_points
        player.points = player.supplied_points
    return player


def score_all(players: Iterable[Player], league: LeagueConfig) -> list[Player]:
    """Score every player and return them sorted by projected points."""
    scored = [score_player(p, league) for p in players]
    scored.sort(key=lambda p: p.points, reverse=True)
    return scored


def season_sigma(player: Player) -> float:
    """Log-scale spread of the player's season outcome.

    The season is modelled as lognormal with the projection as its median.
    Players whose value leans on the high-variance bonus categories get more
    spread, because that is where their points come from: a receiver priced on
    explosive plays can miss by a mile in either direction, while one priced on
    volume mostly cannot.

    Returned as sigma rather than a coefficient of variation so callers can
    quote any quantile they like -- the ceiling below is one such quantile, and
    the bid range in :mod:`sportsball.bidrange` is another.
    """
    if player.points <= 0.0:
        return 0.0
    bonus_share = player.bonus_points / player.points
    cv = _SEASON_CV_BASE + _SEASON_CV_BONUS_SLOPE * bonus_share
    return math.sqrt(math.log(1.0 + cv * cv))


def upside_points(player: Player, league: LeagueConfig) -> float:
    """A ceiling estimate for the player's season.

    Season totals are far less volatile than single games, but SFB is a
    tournament: only the top of the overall leaderboard matters, so a roster
    built on medians is playing the wrong game. This returns the quantile
    ``league.upside_sigma`` standard deviations up the distribution described
    by :func:`season_sigma` (0.85 is roughly the 80th percentile).
    """
    if player.points <= 0.0:
        return player.points
    return player.points * math.exp(league.upside_sigma * season_sigma(player))


def valuation_points(player: Player, league: LeagueConfig) -> float:
    """The point total the valuation layer should price, per league settings."""
    if league.upside_weight <= 0.0:
        return player.points
    ceiling = upside_points(player, league)
    w = min(max(league.upside_weight, 0.0), 1.0)
    return (1.0 - w) * player.points + w * ceiling
