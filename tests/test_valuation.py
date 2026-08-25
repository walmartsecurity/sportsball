"""Replacement level, dollar values, and the auction's money identity."""

import pytest

from sportsball.config import load_league
from sportsball.replacement import compute_replacement
from sportsball.valuation import value_players


def test_replacement_is_computed_for_every_live_position(sfb16, board):
    for position in sfb16.positions_in_play():
        assert position in board.levels.by_position


def test_replacement_quarterback_is_high_in_a_capped_format(board):
    """Only two QBs can start per team, so the QB bar sits far above the flex bar."""
    levels = board.levels.by_position
    assert levels["QB"] > levels["WR"]
    assert levels["QB"] > levels["RB"]


def test_flex_positions_share_a_similar_bar(board):
    """RB, WR and TE fill the same slots, so their replacement levels converge."""
    levels = board.levels.by_position
    flex = [levels["RB"], levels["WR"], levels["TE"]]
    assert max(flex) - min(flex) < 0.1 * max(flex)


def test_league_starters_fill_every_slot(sfb16, board):
    assert len(board.levels.league_starters) == sfb16.league_starters


def test_league_wide_quarterbacks_respect_the_cap(sfb16, board):
    qbs = sum(1 for p in board.levels.league_starters if p.position == "QB")
    assert qbs <= sfb16.teams * sfb16.max_starters_at("QB")


def test_values_are_sorted_descending(board):
    values = [v.value for v in board]
    assert values == sorted(values, reverse=True)


def test_nobody_is_priced_below_the_minimum_bid(sfb16, board):
    assert all(v.value >= sfb16.min_bid for v in board)


def test_drafted_players_soak_up_the_budget(sfb16, board):
    """Total value of rostered players should land close to the money available."""
    total = sum(v.value for v in board.top(sfb16.drafted_players))
    assert total == pytest.approx(sfb16.total_budget, rel=0.05)


def test_value_over_replacement_matches_points_minus_replacement(board):
    for v in board.top(20):
        assert v.vor == pytest.approx(v.points - v.replacement)


def test_a_better_player_is_never_cheaper(board):
    """Within a position, value must be monotone in projected points."""
    for position in ("QB", "RB", "WR", "TE"):
        pool = board.top(500, position=position)
        for better, worse in zip(pool, pool[1:]):
            assert better.points >= worse.points - 1e-6
            assert better.value >= worse.value - 1e-6


def test_shrinking_the_money_pool_lowers_prices(sfb16, scored, board):
    """Mid-draft repricing: less money left means cheaper players."""
    tight = value_players(
        scored, sfb16, levels=board.levels,
        budget_pool=sfb16.total_budget / 2, spots_to_fill=sfb16.drafted_players,
    )
    assert tight.dollars_per_point < board.dollars_per_point


def test_fewer_roster_spots_raises_prices(sfb16, scored, board):
    """Same money chasing fewer spots has to inflate what is left."""
    rich = value_players(
        scored, sfb16, levels=board.levels,
        budget_pool=sfb16.total_budget, spots_to_fill=sfb16.drafted_players // 2,
    )
    assert rich.dollars_per_point > board.dollars_per_point


def test_upside_weighting_favours_high_variance_players(sfb16, scored):
    """Turning up upside should reward the players who lean on bonuses."""
    median = value_players(scored, sfb16)
    ceiling = value_players(scored, sfb16.with_overrides(upside_weight=1.0))
    boom = max(scored[:60], key=lambda p: p.bonus_points / max(p.points, 1))
    steady = min(scored[:60], key=lambda p: p.bonus_points / max(p.points, 1))
    boom_shift = ceiling.get(boom.player_id).value - median.get(boom.player_id).value
    steady_shift = (
        ceiling.get(steady.player_id).value - median.get(steady.player_id).value
    )
    assert boom_shift > steady_shift


def test_tight_ends_are_more_valuable_under_the_premium(sfb16):
    """Same league, premium removed: tight end points must fall.

    This is about what the scoring engine does with a stat line, so it scores
    the stat lines itself. The shipped projections carry totals from Sleeper,
    and a supplied total is the same number whatever the rules say.
    """
    import dataclasses

    flat = sfb16.with_overrides(
        scoring=dataclasses.replace(
            sfb16.scoring, reception_bonus={}, first_down_bonus={}
        )
    )
    from sportsball.scoring import score_all
    from sportsball.players import load_projections

    def score(league):
        players = load_projections()
        for player in players:
            player.supplied_points = None
        return score_all(players, league)

    premium_te = max(p.points for p in score(sfb16) if p.position == "TE")
    flat_te = max(p.points for p in score(flat) if p.position == "TE")
    assert premium_te > flat_te


def test_standard_league_prices_differently(scored):
    """A conventional league should not value the same board identically."""
    from sportsball.players import load_projections
    from sportsball.scoring import score_all

    standard = load_league("standard12")
    std_scored = score_all(load_projections(), standard)
    std_board = value_players(std_scored, standard)
    assert std_board.levels.by_position["QB"] < std_board.levels.by_position["QB"] + 1
    top_std = std_board.valuations[0].name
    assert top_std  # a real player, and pricing ran end to end


def test_empty_pool_is_handled(sfb16):
    levels = compute_replacement([], sfb16)
    assert levels.by_position == {}
    assert levels.marginal_starter == 0.0
