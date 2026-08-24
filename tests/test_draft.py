"""Live draft bookkeeping: money, rosters, inflation, persistence."""

import pytest

from sportsball.draft import DraftError, DraftState


@pytest.fixture
def state(sfb16, scored):
    return DraftState(league=sfb16, players=list(scored))


def test_starts_empty(state, sfb16):
    assert state.my_budget == sfb16.budget
    assert state.my_open_slots == sfb16.roster_size
    assert state.sales == []


def test_recording_a_sale_to_yourself_moves_money(state):
    player = state.find("Ja'Marr Chase")
    state.record_sale(player, 60, state.my_team)
    assert state.my_spent == 60
    assert state.my_budget == 140
    assert state.my_open_slots == 19
    assert state.my_roster == [player]


def test_a_sale_to_someone_else_leaves_your_money_alone(state, sfb16):
    state.record_sale(state.find("Ja'Marr Chase"), 60, "alice")
    assert state.my_budget == sfb16.budget
    assert state.team_spent("alice") == 60
    assert len(state.team_roster("alice")) == 1


def test_a_player_cannot_be_sold_twice(state):
    player = state.find("Ja'Marr Chase")
    state.record_sale(player, 60, "alice")
    with pytest.raises(DraftError, match="already been drafted"):
        state.record_sale(player, 30, "bob")


def test_bids_below_the_minimum_are_rejected(state):
    with pytest.raises(DraftError, match="minimum bid"):
        state.record_sale(state.find("Ja'Marr Chase"), 0, "alice")


def test_you_cannot_outbid_your_own_wallet(state):
    """Every remaining spot still has to be filled at a dollar apiece."""
    with pytest.raises(DraftError, match="cannot afford"):
        state.record_sale(state.find("Ja'Marr Chase"), 200, state.my_team)


def test_max_affordable_bid_reserves_a_dollar_per_open_slot(state, sfb16):
    assert state.max_affordable_bid() == sfb16.budget - (sfb16.roster_size - 1)


def test_undo_restores_the_previous_state(state, sfb16):
    player = state.find("Ja'Marr Chase")
    state.record_sale(player, 60, state.my_team)
    state.undo()
    assert state.my_budget == sfb16.budget
    assert state.my_roster == []
    assert player.player_id not in state.sold_ids


def test_undo_on_an_empty_draft_is_an_error(state):
    with pytest.raises(DraftError, match="nothing to undo"):
        state.undo()


def test_sold_players_leave_the_board(state):
    player = state.find("Ja'Marr Chase")
    before = len(state.available())
    state.record_sale(player, 60, "alice")
    assert len(state.available()) == before - 1
    assert state.board().get(player.player_id) is None


def test_overpaying_early_deflates_the_rest_of_the_board(state):
    """Money spent above value has to come out of everyone else's price."""
    before = state.board().dollars_per_point
    for name, price in (("Ja'Marr", 150), ("Brock Bowers", 150), ("Bijan", 150)):
        state.record_sale(state.find(name), price, "spendthrift")
    assert state.board().dollars_per_point < before
    assert state.inflation() < 1.0


def test_bargains_early_inflate_the_rest_of_the_board(state):
    before = state.board().dollars_per_point
    for name in ("Ja'Marr", "Brock Bowers", "Bijan", "Justin Jefferson", "CeeDee"):
        state.record_sale(state.find(name), 1, "bargain hunter")
    assert state.board().dollars_per_point > before
    assert state.inflation() > 1.0


def test_replacement_levels_do_not_drift_during_the_draft(state):
    """They describe the lineup rules, which do not change as players sell."""
    before = dict(state.levels.by_position)
    state.record_sale(state.find("Ja'Marr Chase"), 60, "alice")
    assert state.levels.by_position == before


def test_max_bid_falls_as_your_wallet_empties(state):
    target = state.find("Brock Bowers")
    rich = state.max_bid_for(target)
    for name, price in (("Bijan", 60), ("Justin Jefferson", 60)):
        state.record_sale(state.find(name), price, state.my_team)
    assert state.max_bid_for(target) < rich


def test_plan_completes_a_partly_built_roster(state, sfb16):
    state.record_sale(state.find("Brock Bowers"), 55, state.my_team)
    plan = state.plan()
    assert len(plan.roster) == sfb16.roster_size
    assert "brock-bowers-te" in {p.player_id for p in plan.roster}
    assert plan.spend <= state.my_budget + 1e-6


def test_my_lineup_respects_the_quarterback_cap(state, sfb16):
    for name in ("Lamar", "Joe Burrow", "Josh Allen", "Jayden Daniels"):
        state.record_sale(state.find(name), 20, state.my_team)
    lineup = state.my_lineup()
    assert sum(1 for p in lineup.starters if p.position == "QB") == 2


# -- player lookup ---------------------------------------------------------


def test_lookup_by_exact_name(state):
    assert state.find("Brock Bowers").name == "Brock Bowers"


def test_lookup_is_case_insensitive(state):
    assert state.find("brock bowers").name == "Brock Bowers"


def test_lookup_by_prefix(state):
    assert state.find("Bijan").name == "Bijan Robinson"


def test_lookup_by_substring(state):
    assert state.find("McBride").name == "Trey McBride"


def test_ambiguous_lookup_lists_the_candidates(state):
    with pytest.raises(DraftError, match="ambiguous"):
        state.find("Ja")


def test_unknown_player_is_an_error(state):
    with pytest.raises(DraftError, match="no player matching"):
        state.find("Nobody At All")


# -- persistence -----------------------------------------------------------


def test_a_draft_round_trips_through_disk(state, sfb16, scored, tmp_path):
    state.record_sale(state.find("Ja'Marr Chase"), 60, state.my_team)
    state.record_sale(state.find("Bijan"), 45, "alice")
    path = state.save(tmp_path / "draft.json")

    restored = DraftState(league=sfb16, players=list(scored))
    restored.load_sales(path)
    assert len(restored.sales) == 2
    assert restored.my_spent == 60
    assert restored.team_spent("alice") == 45


def test_loading_a_draft_with_unknown_players_fails_loudly(
    state, sfb16, scored, tmp_path
):
    """Swapping projection files mid-draft should not silently drop picks."""
    path = tmp_path / "draft.json"
    path.write_text('{"my_team": "me", "sales": '
                    '[{"player_id": "ghost-wr", "price": 5, "team": "me"}]}')
    with pytest.raises(DraftError, match="missing from the current projections"):
        state.load_sales(path)
