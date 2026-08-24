"""Roster construction, budget discipline, and max-bid economics."""

import pytest

from sportsball.optimize import _HAVE_PULP, max_bid, optimize_roster


@pytest.fixture(scope="module")
def plan(sfb16, board):
    return optimize_roster(board, sfb16)


def test_roster_is_exactly_full(sfb16, plan):
    assert len(plan.roster) == sfb16.roster_size


def test_budget_is_respected(sfb16, plan):
    assert plan.spend <= sfb16.budget + 1e-6


def test_lineup_is_legal(sfb16, plan):
    assert len(plan.starters) == sfb16.starters
    qbs = sum(1 for p in plan.starters if p.position == "QB")
    assert qbs <= sfb16.max_starters_at("QB")


def test_starters_and_bench_partition_the_roster(plan):
    assert len(plan.starters) + len(plan.bench) == len(plan.roster)
    assert not ({p.player_id for p in plan.bench} & plan.lineup.starter_ids)


def test_excluded_players_are_absent(sfb16, board):
    target = board.valuations[0].player
    plan = optimize_roster(board, sfb16, exclude=[target.player_id])
    assert target.player_id not in {p.player_id for p in plan.roster}


def test_required_players_are_present(sfb16, board):
    """Regression: PuLP resets the bounds of Binary variables, so pinning a
    player through lowBound silently did nothing and max bids came out absurd."""
    target = board.valuations[80].player
    plan = optimize_roster(board, sfb16, require=[target.player_id])
    assert target.player_id in {p.player_id for p in plan.roster}


def test_an_expensive_required_player_still_gets_rostered(sfb16, board):
    """Even at a price no sane manager would pay, the pin must hold."""
    target = board.valuations[0].player
    plan = optimize_roster(
        board, sfb16, require=[target.player_id],
        prices={target.player_id: 150.0},
    )
    assert target.player_id in {p.player_id for p in plan.roster}
    assert plan.spend <= sfb16.budget + 1e-6


def test_owned_players_are_kept_and_cost_nothing(sfb16, board):
    owned = [board.valuations[0].player, board.valuations[5].player]
    plan = optimize_roster(
        board, sfb16, budget=sfb16.budget / 2, slots_to_fill=sfb16.roster_size - 2,
        owned=owned
    )
    ids = {p.player_id for p in plan.roster}
    assert all(p.player_id in ids for p in owned)
    assert len(plan.roster) == sfb16.roster_size
    assert plan.spend <= sfb16.budget / 2 + 1e-6


def test_bench_is_valued_over_replacement_not_raw_points(sfb16, board, plan):
    """Regression: crediting bench players with raw points made a bench full of
    replacement-level quarterbacks look optimal, because quarterbacks post big
    totals even when they are freely available."""
    bench_qbs = [p for p in plan.bench if p.position == "QB"]
    levels = board.levels
    for qb in bench_qbs:
        # Any QB worth a bench spot must actually beat the QB you could stream.
        assert qb.points >= levels.for_position("QB") - 1e-6


def test_a_bigger_budget_never_hurts(sfb16, board):
    poor = optimize_roster(board, sfb16, budget=sfb16.budget / 2)
    rich = optimize_roster(board, sfb16, budget=sfb16.budget)
    assert rich.objective >= poor.objective - 1e-6


def test_leftover_is_budget_minus_spend(plan):
    assert plan.leftover == pytest.approx(plan.budget - plan.spend)


@pytest.mark.skipif(not _HAVE_PULP, reason="needs the CBC solver")
def test_ilp_is_used_when_available(plan):
    assert plan.solver == "cbc"


# -- max bid ---------------------------------------------------------------


def test_max_bid_is_close_to_list_value_before_the_draft(sfb16, board):
    """With a full budget and an untouched board, break-even should track the
    list price -- that is what the list price means."""
    for v in board.top(5):
        bid = max_bid(v.player, board, sfb16, budget=sfb16.budget, slots_to_fill=sfb16.roster_size)
        assert bid == pytest.approx(v.value, abs=0.03 * sfb16.budget)


def test_max_bid_never_exceeds_what_you_can_afford(sfb16, board):
    target = board.valuations[0].player
    bid = max_bid(target, board, sfb16, budget=sfb16.budget / 4, slots_to_fill=sfb16.roster_size)
    assert bid <= sfb16.budget / 4 - (sfb16.roster_size - 1) * sfb16.min_bid


def test_max_bid_is_higher_for_a_better_player(sfb16, board):
    best = board.valuations[0].player
    worse = board.valuations[40].player
    assert max_bid(best, board, sfb16, budget=sfb16.budget, slots_to_fill=sfb16.roster_size) >= max_bid(
        worse, board, sfb16, budget=sfb16.budget, slots_to_fill=sfb16.roster_size
    )


def test_max_bid_is_zero_with_no_slots_left(sfb16, board):
    target = board.valuations[0].player
    assert max_bid(target, board, sfb16, budget=sfb16.budget / 4, slots_to_fill=0) == 0.0


def test_paying_more_than_max_bid_makes_the_roster_worse(sfb16, board):
    """The definition of the number: one dollar past it, you are behind."""
    target = board.valuations[3].player
    bid = max_bid(target, board, sfb16, budget=sfb16.budget, slots_to_fill=sfb16.roster_size)
    baseline = optimize_roster(
        board, sfb16, budget=sfb16.budget, slots_to_fill=sfb16.roster_size, exclude=[target.player_id]
    ).objective
    over = optimize_roster(
        board, sfb16, budget=sfb16.budget, slots_to_fill=sfb16.roster_size,
        require=[target.player_id], prices={target.player_id: bid + 0.05 * sfb16.budget},
    ).objective
    assert over < baseline


# -- fallback solver -------------------------------------------------------


@pytest.fixture
def no_pulp(monkeypatch):
    """Force the dependency-free path."""
    monkeypatch.setattr("sportsball.optimize._HAVE_PULP", False)


def test_greedy_fallback_builds_a_legal_roster(sfb16, board, no_pulp):
    plan = optimize_roster(board, sfb16)
    assert plan.solver == "greedy"
    assert len(plan.roster) == sfb16.roster_size
    assert plan.spend <= sfb16.budget + 1e-6
    assert len(plan.starters) == sfb16.starters
    qbs = sum(1 for p in plan.starters if p.position == "QB")
    assert qbs <= sfb16.max_starters_at("QB")


def test_greedy_fallback_honours_require_and_exclude(sfb16, board, no_pulp):
    keep = board.valuations[10].player
    drop = board.valuations[0].player
    plan = optimize_roster(
        board, sfb16, require=[keep.player_id], exclude=[drop.player_id]
    )
    ids = {p.player_id for p in plan.roster}
    assert keep.player_id in ids
    assert drop.player_id not in ids


def test_greedy_fallback_lands_near_the_exact_answer(sfb16, board, monkeypatch):
    """The fallback should be usable, not merely legal."""
    exact = optimize_roster(board, sfb16).objective
    monkeypatch.setattr("sportsball.optimize._HAVE_PULP", False)
    approx = optimize_roster(board, sfb16).objective
    assert approx >= 0.9 * exact
