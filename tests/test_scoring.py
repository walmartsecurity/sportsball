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


# -- fitted distribution model --------------------------------------------


def test_gamma_tail_is_a_probability():
    from sportsball.bonuses import gamma_tail

    for threshold in (10, 50, 100, 300, 1000):
        assert 0.0 <= gamma_tail(100.0, 0.6, threshold) <= 1.0


def test_gamma_tail_decreases_with_threshold():
    from sportsball.bonuses import gamma_tail

    tails = [gamma_tail(100.0, 0.6, t) for t in (50, 100, 200, 400)]
    assert tails == sorted(tails, reverse=True)


def test_gamma_has_a_lighter_upper_tail_than_lognormal():
    """The reason the model switched: lognormal over-predicted 200-yard games."""
    from sportsball.bonuses import gamma_tail

    assert gamma_tail(80.0, 0.65, 200.0) < lognormal_tail(80.0, 0.65, 200.0)


def test_gamma_matches_its_mean():
    """Sanity check on the incomplete gamma implementation."""
    from sportsball.bonuses import gamma_tail

    # For a gamma, P(X >= mean) sits just under a half for moderate spread.
    assert 0.35 < gamma_tail(100.0, 0.6, 100.0) < 0.5


def test_variance_shrinks_as_volume_rises(sfb16):
    """Fitted from play-by-play: a 97 yd/gm back is far steadier than a 33."""
    from sportsball.bonuses import volume_adjusted_cv

    rules = sfb16.bonuses
    args = (rules.scrimmage_cv["RB"], rules.scrimmage_cv_ref["RB"],
            rules.scrimmage_cv_slope["RB"], rules.cv_floor, rules.cv_ceiling)
    low = volume_adjusted_cv(33.0, *args)
    high = volume_adjusted_cv(97.0, *args)
    assert low > high
    assert 0.75 < low < 0.95
    assert 0.35 < high < 0.50


def test_variance_is_clamped_at_the_extremes(sfb16):
    from sportsball.bonuses import volume_adjusted_cv

    rules = sfb16.bonuses
    args = (rules.scrimmage_cv["RB"], rules.scrimmage_cv_ref["RB"],
            rules.scrimmage_cv_slope["RB"], rules.cv_floor, rules.cv_ceiling)
    assert volume_adjusted_cv(0.5, *args) <= rules.cv_ceiling
    assert volume_adjusted_cv(5000.0, *args) >= rules.cv_floor


def test_quarterback_big_runs_are_discounted(sfb16):
    """QB yards per carry comes from scrambles, not breakaway speed."""
    qb = make("QB", rush_att=140, rush_yds=800)
    rb = make("RB", rush_att=140, rush_yds=800)
    qb_parts = project_bonuses(qb, sfb16.bonuses)[1]
    rb_parts = project_bonuses(rb, sfb16.bonuses)[1]
    assert qb_parts["rush_40_plays"] < rb_parts["rush_40_plays"]
    ratio = qb_parts["rush_40_plays"] / rb_parts["rush_40_plays"]
    assert ratio == pytest.approx(sfb16.bonuses.rush_40_position_multiplier["QB"])


def test_distribution_is_configurable(sfb16):
    """Both shapes stay available; gamma is the fitted default."""
    assert sfb16.bonuses.distribution == "gamma"
    wr = make("WR", receptions=90, rec_yds=1300, games=17)
    gamma_pts, _ = project_bonuses(wr, sfb16.bonuses)
    logn_pts, _ = project_bonuses(
        wr, dataclasses.replace(sfb16.bonuses, distribution="lognormal")
    )
    assert gamma_pts != logn_pts
    assert gamma_pts > 0 and logn_pts > 0


# -- fitted first down rates ----------------------------------------------


def test_receivers_convert_more_first_downs_per_catch_than_tight_ends(sfb16):
    """Fitted from play-by-play, and the opposite of the obvious guess: wideouts
    catch the ball further downfield, which narrows the SFB16 tight end edge."""
    rates = sfb16.first_downs.per_reception
    assert rates["WR"] > rates["TE"] > rates["RB"]


def test_touchdowns_are_not_double_counted(sfb16):
    """The fitted rates already include scores, so the separate adjustment is
    off by default; turning it on can only inflate first downs."""
    assert sfb16.first_downs.touchdowns_count_as_first_down is False
    scorer = make("WR", receptions=60, rec_td=12)
    _, plain = estimate_first_downs(scorer, sfb16.first_downs)
    _, doubled = estimate_first_downs(
        scorer, dataclasses.replace(
            sfb16.first_downs, touchdowns_count_as_first_down=True)
    )
    assert doubled > plain
    assert plain == pytest.approx(60 * sfb16.first_downs.per_reception["WR"])
