"""What the tool tells you to bid on, and — as importantly — when it stays quiet."""

import pytest

from sportsball.draft import DraftState
from sportsball.suggest import MARKET_SAMPLE, PER_POSITION_CAP, room_pricing


@pytest.fixture
def state(sfb16, scored):
    return DraftState(league=sfb16, players=list(scored))


def by_position(rows, position):
    return [r for r in rows if r.position == position]


def test_a_fresh_board_suggests_filling_the_capped_slots(state):
    """Nothing is mispriced at the start, so the only real signal is structural:
    the superflex slots are the one thing that can run out."""
    rows = state.suggestions()
    assert rows
    assert all(r.position == "QB" for r in rows)
    assert all("superflex" in " ".join(r.reasons) for r in rows)


def test_a_fresh_board_claims_no_price_edge(state):
    """At list prices every tier buys the same value per dollar, and the tool
    should not pretend otherwise."""
    rows = state.suggestions()
    assert all(r.edge == 0 for r in rows)
    assert "Nothing on the board is mispriced" in state.pacing(any_edge=False)


def test_it_suggests_the_best_available_not_a_mid_tier_name(state):
    """Ordering must not fall through to rounding noise."""
    rows = by_position(state.suggestions(), "QB")
    best_qb = max(
        (v for v in state.board() if v.position == "QB"), key=lambda v: v.vor
    )
    assert rows[0].player.player_id == best_qb.player.player_id


def test_quarterbacks_stop_being_suggested_once_your_slots_are_full(state):
    qbs = [v.player for v in state.board() if v.position == "QB"][:2]
    for qb in qbs:
        state.record_sale(qb, state.league.budget * 0.15, state.my_team)
    assert not by_position(state.suggestions(), "QB")


def test_a_player_who_cannot_start_is_never_suggested(state):
    """Two quarterbacks already start; a third is a bench body."""
    board = state.board()
    qbs = [v.player for v in board if v.position == "QB"]
    for qb in qbs[:2]:
        state.record_sale(qb, state.league.budget * 0.15, state.my_team)
    assert qbs[2].player_id not in {r.player.player_id for r in state.suggestions(limit=20)}


# -- reading the room ------------------------------------------------------


def test_room_pricing_waits_for_a_real_sample(state):
    board = state.baseline_board()
    tes = [v for v in board if v.position == "TE"][: MARKET_SAMPLE - 1]
    for v in tes:
        state.record_sale(v.player, v.value * 0.5, "other")
    assert "TE" not in room_pricing(state)


def test_room_pricing_measures_the_discount(state):
    board = state.baseline_board()
    for v in [v for v in board if v.position == "TE"][:4]:
        state.record_sale(v.player, round(v.value * 0.7), "other")
    ratio, n = room_pricing(state)["TE"]
    assert n == 4
    assert ratio == pytest.approx(0.7, abs=0.03)


def test_a_position_the_room_sleeps_on_gets_surfaced(state):
    board = state.baseline_board()
    for v in [v for v in board if v.position == "TE"][:4]:
        state.record_sale(v.player, round(v.value * 0.7), "other")
    tes = by_position(state.suggestions(), "TE")
    assert tes
    assert any("room is paying" in r for r in tes[0].reasons)


def test_a_position_the_room_chases_is_not_surfaced(state):
    board = state.baseline_board()
    for v in [v for v in board if v.position == "RB"][:4]:
        state.record_sale(v.player, round(v.value * 1.4), "other")
    for row in by_position(state.suggestions(), "RB"):
        assert not any("room is paying" in r for r in row.reasons)


def test_no_single_position_can_crowd_out_the_list(state):
    board = state.baseline_board()
    for v in [v for v in board if v.position == "TE"][:4]:
        state.record_sale(v.player, round(v.value * 0.6), "other")
    rows = state.suggestions(limit=10)
    assert len(by_position(rows, "TE")) <= PER_POSITION_CAP


# -- money -----------------------------------------------------------------


def test_cheap_early_buys_create_a_real_edge_later(state):
    """Fill most of the roster cheaply and the leftover money has few spots to
    land in, so the break-even price rises above the market's."""
    cheap = [v.player for v in state.board().valuations[100:118]]
    for player in cheap:
        state.record_sale(player, 1, state.my_team)
    rows = state.suggestions()
    assert rows
    assert rows[0].edge > 0
    assert any("under your walk-away" in r for r in rows[0].reasons)


def test_falling_prices_alone_are_not_an_edge(state):
    """When the room overspends, everything left gets cheaper for everyone at
    once — including you — so there is nothing to exploit."""
    board = state.board()
    for v in board.top(8):
        state.record_sale(v.player, round(v.value * 1.6), "whale")
    assert state.inflation() < 1.0
    assert all(r.edge == 0 for r in state.suggestions())


def test_nothing_is_suggested_once_the_roster_is_full(state):
    for v in state.board().top(state.league.roster_size):
        state.record_sale(v.player, 1, state.my_team)
    assert state.suggestions() == []
    assert "roster is full" in state.pacing()


def test_suggestions_are_always_affordable(state):
    state.record_sale(state.find("Ja'Marr Chase"), state.league.budget * 0.9,
                      state.my_team)
    for row in state.suggestions(limit=10):
        assert row.price <= state.max_affordable_bid()


def test_targets_command_runs(capsys):
    from sportsball.cli import main

    assert main(["targets", "-n", "4"]) == 0
    out = capsys.readouterr().out
    assert "YOUR MAX" in out or "mispriced" in out
