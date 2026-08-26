"""Low, likely and high sale prices, and how they answer to the room.

A range is a claim about what a player will fetch, so these hold it to the
things that actually decide that: how uncertain the player himself is, how
loosely the room has been pricing, what it pays for his position, and the two
hard limits -- nobody bids more than they have, and nobody bids below the money
already standing on the board.
"""

import copy

import pytest

from sportsball import bidrange
from sportsball.bidrange import bid_range, bid_ranges, room_scatter
from sportsball.draft import DraftState


@pytest.fixture
def state(sfb16, scored):
    return DraftState(league=sfb16, players=list(scored))


def one(state, name, **kw):
    board = state.board()
    return bid_range(state, board, state.find(name), **kw)


# -- the shape of a range --------------------------------------------------

def test_the_range_is_ordered(state):
    for row in bid_ranges(state, limit=120):
        assert row.low <= row.likely <= row.high, row.name


def test_nothing_falls_below_the_minimum_bid(state, sfb16):
    for row in bid_ranges(state, limit=200):
        assert row.low >= sfb16.min_bid


def test_the_studs_get_a_range_worth_reading(state):
    """A wide dollar spread at the top is the point, not a bug."""
    top = bid_ranges(state, limit=1)[0]
    assert top.high > top.low
    assert (top.high - top.low) > 0.2 * top.likely


def test_replacement_level_players_collapse_to_the_minimum(state, sfb16):
    rows = bid_ranges(state)
    tail = rows[-25:]
    assert all(r.at_minimum for r in tail)
    assert all(r.span == f"${sfb16.min_bid:,.0f}" for r in tail)


def test_a_sold_player_has_no_range(state):
    player = state.find("Ja'Marr Chase")
    state.record_sale(player, 200, "alice")
    assert bid_range(state, state.board(), player) is None


def test_scanning_the_board_agrees_with_asking_one_at_a_time(state):
    board = state.board()
    scanned = bid_ranges(state, board, limit=15)
    for row in scanned:
        alone = bid_range(state, board, row.player)
        assert (alone.low, alone.likely, alone.high) == (row.low, row.likely, row.high)


# -- the player's own uncertainty ------------------------------------------

def test_a_boom_bust_player_gets_a_wider_range(state):
    """Two players, same projection, different amounts of it from big plays."""
    board = state.board()
    player = state.find("Ja'Marr Chase")
    steady, volatile = (
        _width(state, board, _clone(player, bonus_share=share))
        for share in (0.05, 0.45)
    )
    assert volatile > steady


def _clone(player, bonus_share):
    out = copy.deepcopy(player)
    out.bonus_points = out.points * bonus_share
    out.base_points = out.points - out.bonus_points
    return out


def _width(state, board, player):
    row = bid_range(state, board, player)
    return row.high - row.low


# -- what the room has been doing ------------------------------------------

def test_a_disciplined_room_narrows_the_ranges(state):
    """Sales landing exactly on the model shrink the scatter below the prior."""
    before, n = room_scatter(state)
    assert n == 0 and before == bidrange.ROOM_SIGMA_PRIOR

    baseline = state.baseline_board()
    for v in baseline.top(20):
        state.record_sale(v.player, round(v.value), "alice")
    after, n = room_scatter(state)
    assert n >= 10
    assert after < before


def test_a_wild_room_widens_the_ranges(state):
    baseline = state.baseline_board()
    for i, v in enumerate(baseline.top(20)):
        # Alternate paying double and paying half.
        state.record_sale(v.player, max(round(v.value * (2.0 if i % 2 else 0.5)), 1),
                          f"team{i % 6}")
    after, n = room_scatter(state)
    assert n >= 10
    assert after > bidrange.ROOM_SIGMA_PRIOR


def test_the_scatter_ignores_the_dollar_store_sales(state, sfb16):
    """Cheap sales say nothing about discipline, so they are left out."""
    board = state.baseline_board()
    cheap = [v for v in board if v.value < sfb16.budget * bidrange.MEANINGFUL_SHARE]
    for v in cheap[:20]:
        state.record_sale(v.player, sfb16.min_bid, "alice")
    scatter, n = room_scatter(state)
    assert n == 0
    assert scatter == bidrange.ROOM_SIGMA_PRIOR


def test_a_position_the_room_discounts_gets_cheaper_ranges(state):
    """Measured habit, not assumption: the ranges follow the realised prices."""
    before = one(state, "Saquon Barkley")
    baseline = state.baseline_board()
    backs = [v for v in baseline if v.position == "RB"][:6]
    for i, v in enumerate(backs):
        if v.player.player_id == before.player.player_id:
            continue
        state.record_sale(v.player, max(round(v.value * 0.5), 1), f"team{i}")
    after = one(state, "Saquon Barkley")
    assert after.likely < before.likely
    assert after.high < before.high


def test_the_habit_is_shrunk_toward_no_habit_at_all(state):
    """Three cheap backs do not reprice every back on the board."""
    baseline = state.baseline_board()
    backs = [v for v in baseline if v.position == "RB"][1:4]
    for i, v in enumerate(backs):
        state.record_sale(v.player, max(round(v.value * 0.4), 1), f"team{i}")
    raw = state.room_pricing()["RB"][0]
    shrunk = bidrange.position_habit(state, "RB")
    assert raw < shrunk < 1.0


# -- the hard limits -------------------------------------------------------

def test_nobody_bids_more_than_the_richest_team_has(state, sfb16):
    """With every wallet nearly empty, the ceiling binds before the model does."""
    baseline = state.baseline_board()
    pool = iter([v.player for v in baseline.top(250)])
    for team in [f"team{i}" for i in range(sfb16.teams)]:
        for _ in range(sfb16.roster_size - 2):
            state.record_sale(next(pool), sfb16.min_bid, team)
    ceiling = state.richest_bid()
    assert ceiling < sfb16.budget
    for row in bid_ranges(state, limit=40):
        assert row.high <= ceiling


def test_the_ceiling_is_the_fattest_wallet_not_the_average(state, sfb16):
    state.record_sale(state.find("Ja'Marr Chase"), 900, "spendthrift")
    # Eleven teams are untouched, so the ceiling is a full budget's worth.
    untouched = sfb16.budget - (sfb16.roster_size - 1) * sfb16.min_bid
    assert state.richest_bid() == untouched


def test_a_full_team_cannot_bid(state, sfb16):
    baseline = state.baseline_board()
    for v in baseline.top(sfb16.roster_size):
        state.record_sale(v.player, sfb16.min_bid, "alice")
    assert state.team_max_bid("alice") == 0.0


def test_a_standing_bid_floors_the_range(state):
    player = state.find("Chris Godwin Jr.")
    before = one(state, player.name)
    state.open_bids[player.player_id] = before.high + 50
    after = one(state, player.name)
    assert after.low >= before.high + 50
    assert after.likely >= after.low


# -- reading it against your own number ------------------------------------

def test_priced_out_when_your_max_sits_under_the_range(state, sfb16):
    """Spend almost everything, then look at a player you can no longer reach."""
    baseline = state.baseline_board()
    state.record_sale(baseline.top(1)[0].player, sfb16.budget - 19, state.my_team)
    row = one(state, "Puka Nacua")
    assert row.priced_out
    assert "Priced out" in row.verdict()


def test_should_win_when_your_max_clears_the_top_of_the_range(state):
    row = one(state, "Ja'Marr Chase")
    forced = bidrange.BidRange(player=row.player, low=row.low, likely=row.likely,
                               high=row.high, max_bid=row.high + 1)
    assert forced.should_win
    assert "Should be yours" in forced.verdict()


def test_the_verdict_says_something_about_every_player(state):
    for row in bid_ranges(state, limit=60):
        assert row.verdict().strip()


def test_the_exact_walk_away_can_be_swapped_in(state):
    """The closed form scans the board; the integer program answers one player."""
    player = state.find("Trey McBride")
    quick = state.bid_range(player)
    exact = state.bid_range(player, exact=True)
    assert (exact.low, exact.high) == (quick.low, quick.high)
    assert exact.max_bid == pytest.approx(quick.max_bid, rel=0.1, abs=2)
