"""Scoring under SFB16 rules, checked against hand-computed totals."""

import dataclasses

import pytest

from sportsball.bonuses import lognormal_tail, project_bonuses
from sportsball.config import BonusRules, load_league
from sportsball.players import Player, StatLine
from sportsball.scoring import base_points, estimate_first_downs, score_player


@pytest.fixture
def no_bonus_league(sfb16):
    return sfb16.with_overrides(
        bonuses=dataclasses.replace(sfb16.bonuses, enabled=False)
    )


def make(position, **stats):
    return Player(name=f"Test {position}", position=position, stats=StatLine(**stats))


def test_passing_touchdowns_are_worth_six(no_bonus_league):
    qb = make("QB", pass_yds=0, pass_td=1)
    assert score_player(qb, no_bonus_league).points == pytest.approx(6.0)


def test_passing_yards_are_one_per_twenty_five(no_bonus_league):
    qb = make("QB", pass_yds=2500)
    assert score_player(qb, no_bonus_league).points == pytest.approx(100.0)


def test_rushing_yards_are_one_per_ten(no_bonus_league):
    rb = make("RB", rush_yds=1000, rush_first_downs=0)
    assert score_player(rb, no_bonus_league).points == pytest.approx(100.0)


def test_tight_end_reception_premium(no_bonus_league):
    """A TE gets 1.5 per catch and 1.5 per first down; a WR gets 0.5 and 0.5."""
    te = make("TE", receptions=10, rec_first_downs=10)
    wr = make("WR", receptions=10, rec_first_downs=10)
    te_pts = score_player(te, no_bonus_league).points
    wr_pts = score_player(wr, no_bonus_league).points
    assert te_pts == pytest.approx(10 * 1.5 + 10 * 1.5)
    assert wr_pts == pytest.approx(10 * 0.5 + 10 * 0.5)
    assert te_pts - wr_pts == pytest.approx(20.0)


def test_first_downs_are_estimated_when_absent(sfb16):
    """No first-down column means they get modelled, not dropped."""
    supplied = make("WR", receptions=50, rec_first_downs=25)
    absent = make("WR", receptions=50)
    _, supplied_fd = estimate_first_downs(supplied, sfb16.first_downs)
    _, estimated_fd = estimate_first_downs(absent, sfb16.first_downs)
    assert supplied_fd == 25
    assert estimated_fd > 0
    assert estimated_fd <= 50


def test_estimated_first_downs_never_exceed_receptions(sfb16):
    player = make("TE", receptions=40, rec_td=20)
    _, rec_fd = estimate_first_downs(player, sfb16.first_downs)
    assert rec_fd <= 40


def test_interceptions_do_not_score_in_sfb16(no_bonus_league):
    """The published SFB16 rules list positive scoring only."""
    clean = make("QB", pass_yds=4000, pass_td=30)
    picky = make("QB", pass_yds=4000, pass_td=30, interceptions=20)
    assert score_player(clean, no_bonus_league).points == pytest.approx(
        score_player(picky, no_bonus_league).points
    )


# -- bonuses --------------------------------------------------------------


def test_lognormal_tail_is_a_probability():
    for threshold in (50, 100, 300, 1000):
        p = lognormal_tail(100.0, 0.6, threshold)
        assert 0.0 <= p <= 1.0


def test_lognormal_tail_decreases_with_threshold():
    tails = [lognormal_tail(100.0, 0.6, t) for t in (50, 100, 200, 400)]
    assert tails == sorted(tails, reverse=True)


def test_higher_variance_raises_odds_of_a_big_game():
    """Same season total, more boom/bust, more threshold bonuses."""
    steady = lognormal_tail(80.0, 0.3, 100.0)
    volatile = lognormal_tail(80.0, 0.9, 100.0)
    assert volatile > steady


def test_variance_is_worth_real_points(sfb16):
    """Two backs, identical season lines, different game-to-game spread."""
    stats = dict(rush_att=250, rush_yds=1200, receptions=30, rec_yds=250)
    boom = make("RB", **stats)
    steady = make("RB", **stats)
    rules = sfb16.bonuses
    boom_pts, _ = project_bonuses(
        boom, dataclasses.replace(rules, scrimmage_cv={"RB": 0.9})
    )
    steady_pts, _ = project_bonuses(
        steady, dataclasses.replace(rules, scrimmage_cv={"RB": 0.3})
    )
    assert boom_pts > steady_pts


def test_efficiency_raises_big_play_bonuses(sfb16):
    """Same carries, more yards per carry, more 40-yard runs."""
    efficient = make("RB", rush_att=200, rush_yds=1200)
    plodder = make("RB", rush_att=200, rush_yds=700)
    eff_bonus, eff_parts = project_bonuses(efficient, sfb16.bonuses)
    plod_bonus, plod_parts = project_bonuses(plodder, sfb16.bonuses)
    assert eff_parts["rush_40_plays"] > plod_parts["rush_40_plays"]
    assert eff_bonus > plod_bonus


def test_once_per_game_capping_lowers_play_bonuses(sfb16):
    """The alternate reading of the rules can only reduce big-play credit."""
    wr = make("WR", receptions=100, rec_yds=1400)
    per_play, _ = project_bonuses(wr, sfb16.bonuses)
    capped, _ = project_bonuses(
        wr, dataclasses.replace(sfb16.bonuses, play_bonus_once_per_game=True)
    )
    assert capped < per_play
    assert capped > 0


def test_disabling_bonuses_zeroes_them(sfb16):
    wr = make("WR", receptions=100, rec_yds=1400)
    total, parts = project_bonuses(
        wr, dataclasses.replace(sfb16.bonuses, enabled=False)
    )
    assert total == 0.0
    assert parts == {}


def test_bonus_breakdown_sums_to_total(sfb16, scored):
    for player in scored[:30]:
        assert sum(player.bonus_breakdown.values()) == pytest.approx(
            player.bonus_points
        )


def test_points_are_base_plus_bonus(scored):
    for player in scored[:30]:
        assert player.points == pytest.approx(
            player.base_points + player.bonus_points
        )


def test_players_with_no_stats_score_nothing(sfb16):
    assert score_player(make("WR"), sfb16).points == pytest.approx(0.0)
