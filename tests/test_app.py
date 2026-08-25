"""Check the browser engine against the Python one.

The web app reimplements pricing, lineup assignment and the max-bid search in
JavaScript so it can run at a draft table without Python. Two implementations
of the same maths will drift unless something holds them together; this is that
something. Skipped when node or the built app is unavailable.
"""

from __future__ import annotations


from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = Path(__file__).parent / "js_harness.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="needs node to run the browser engine"
)


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    """An empty board, for the tests that reason about an untouched room."""
    out = tmp_path_factory.mktemp("app") / "app.html"
    subprocess.run(
        [sys.executable, str(ROOT / "tools" / "build_app.py"),
         "--no-seed", "--out", str(out)],
        check=True, capture_output=True,
    )
    return out


def run_js(app: Path, request: dict, tmp_path: Path) -> dict:
    req = tmp_path / "req.json"
    req.write_text(json.dumps(request))
    result = subprocess.run(
        ["node", str(HARNESS), str(app), str(req)],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def py_board(sfb16):
    from sportsball.players import load_projections
    from sportsball.scoring import score_all
    from sportsball.valuation import value_players

    return value_players(score_all(load_projections(), sfb16), sfb16)


def test_starting_prices_match_python(app, py_board, tmp_path):
    ids = [v.player.player_id for v in py_board.top(12)]
    out = run_js(app, {"prices": ids}, tmp_path)
    for pid, js_price in zip(ids, out["prices"]):
        assert js_price == pytest.approx(py_board.get(pid).value, rel=1e-3)


def test_untouched_board_has_no_inflation(app, tmp_path):
    out = run_js(app, {}, tmp_path)
    assert out["state"]["inflation"] == pytest.approx(1.0, abs=1e-6)


def test_overpaying_deflates_the_rest(app, py_board, tmp_path):
    big = [v.player.player_id for v in py_board.top(3)]
    sales = [{"id": pid, "price": 800, "team": "spendthrift"} for pid in big]
    out = run_js(app, {"sales": sales}, tmp_path)
    assert out["state"]["inflation"] < 1.0


def test_bargains_inflate_the_rest(app, py_board, tmp_path):
    cheap = [v.player.player_id for v in py_board.top(5)]
    sales = [{"id": pid, "price": 1, "team": "bargain"} for pid in cheap]
    out = run_js(app, {"sales": sales}, tmp_path)
    assert out["state"]["inflation"] > 1.0


def test_lineup_matches_python(app, sfb16, py_board, tmp_path):
    """Same roster, same optimal lineup and same points."""
    from sportsball.lineup import best_lineup

    roster_ids = [v.player.player_id for v in py_board.top(30)][:14]
    roster = [py_board.get(i).player for i in roster_ids]
    out = run_js(app, {"lineup": roster_ids}, tmp_path)
    solved = best_lineup(roster, sfb16.lineup)
    assert out["lineupPts"] == pytest.approx(solved.points, rel=1e-4)
    assert set(out["starters"]) == solved.starter_ids


def test_lineup_respects_the_quarterback_cap(app, py_board, tmp_path):
    qbs = [v.player.player_id for v in py_board.top(400, position="QB")][:6]
    flex = [v.player.player_id for v in py_board.top(400, position="WR")][:10]
    out = run_js(app, {"lineup": qbs + flex}, tmp_path)
    started_qbs = [i for i in out["starters"] if i in set(qbs)]
    assert len(started_qbs) <= 2
    assert len(out["starters"]) == 10


def test_max_bid_tracks_python_before_the_draft(app, sfb16, py_board, tmp_path):
    """The expensive one: the browser's roster search should land close to the
    exact integer program the CLI uses."""
    from sportsball.optimize import max_bid

    ids = [v.player.player_id for v in py_board.top(4)]
    out = run_js(app, {"maxBids": ids}, tmp_path)
    for pid, js_bid in zip(ids, out["maxBids"]):
        py_bid = max_bid(
            py_board.get(pid).player, py_board, sfb16,
            budget=sfb16.budget, slots_to_fill=sfb16.roster_size,
        )
        assert js_bid == pytest.approx(py_bid, rel=0.10), pid


def test_max_bid_falls_after_you_spend(app, py_board, tmp_path):
    target = py_board.top(6)[5].player.player_id
    rich = run_js(app, {"maxBids": [target]}, tmp_path)["maxBids"][0]
    spend = [
        {"id": py_board.top(2)[0].player.player_id, "price": 400, "team": "you"},
        {"id": py_board.top(2)[1].player.player_id, "price": 400, "team": "you"},
    ]
    poor = run_js(app, {"sales": spend, "maxBids": [target]}, tmp_path)["maxBids"][0]
    assert poor < rich


def test_max_bid_is_zero_with_a_full_roster(app, py_board, tmp_path):
    ids = [v.player.player_id for v in py_board.top(21)]
    sales = [{"id": i, "price": 1, "team": "you"} for i in ids[:20]]
    out = run_js(app, {"sales": sales, "maxBids": [ids[20]]}, tmp_path)
    assert out["state"]["myOpen"] == 0
    assert out["maxBids"][0] == 0


def test_cash_rich_endgame_bids_above_list(app, py_board, tmp_path):
    """Money you cannot deploy elsewhere is worth spending here.

    Fill most of the roster with cheap bodies and the remaining budget has few
    spots left to absorb it, so the break-even price rises above the market's.
    """
    filler = [v.player.player_id for v in py_board.valuations[100:118]]
    sales = [{"id": pid, "price": 1, "team": "you"} for pid in filler]
    target = py_board.top(1)[0]
    out = run_js(
        app, {"sales": sales, "maxBids": [target.player.player_id]}, tmp_path
    )
    assert out["state"]["myOpen"] == 2
    assert out["maxBids"][0] > target.value, "should pay over list when flush"
    assert out["maxBids"][0] <= out["state"]["hardCap"]


def test_a_third_quarterback_is_only_worth_a_bench_spot(app, py_board, tmp_path):
    """SFB16 starts at most two, so the next one cannot crack the lineup."""
    qbs = [v.player.player_id for v in py_board.top(400, position="QB")]
    sales = [{"id": pid, "price": 50, "team": "you"} for pid in qbs[:2]]
    fresh = run_js(app, {"maxBids": [qbs[2]]}, tmp_path)["maxBids"][0]
    stacked = run_js(app, {"sales": sales, "maxBids": [qbs[2]]}, tmp_path)["maxBids"][0]
    assert stacked < fresh


# -- suggestions, in both implementations ----------------------------------


def _py_state(sfb16, scored, sales):
    from sportsball.draft import DraftState

    state = DraftState(league=sfb16, players=list(scored))
    for s in sales:
        state.record_sale(state._by_id[s["id"]], s["price"], s["team"])
    return state


def test_fresh_suggestions_match_python(app, sfb16, scored, tmp_path):
    out = run_js(app, {"suggest": 6}, tmp_path)
    state = _py_state(sfb16, scored, [])
    py = state.suggestions(limit=6)
    assert [r["id"] for r in out["suggestions"]] == [r.player.player_id for r in py]


def test_room_pricing_matches_python(app, sfb16, scored, py_board, tmp_path):
    tes = [v for v in py_board if v.position == "TE"][:4]
    sales = [{"id": v.player.player_id, "price": round(v.value * 0.7), "team": "x"}
             for v in tes]
    out = run_js(app, {"sales": sales, "suggest": 6}, tmp_path)
    state = _py_state(sfb16, scored, sales)
    py_room = state.room_pricing()
    assert set(out["room"]) == set(py_room)
    for pos, (ratio, n) in py_room.items():
        # The payload rounds values to a decimal place, so the two agree to
        # within that rounding rather than exactly.
        assert out["room"][pos]["ratio"] == pytest.approx(ratio, rel=1e-3)
        assert out["room"][pos]["n"] == n


def test_underpriced_position_surfaces_in_both(app, sfb16, scored, py_board, tmp_path):
    tes = [v for v in py_board if v.position == "TE"][:4]
    sales = [{"id": v.player.player_id, "price": round(v.value * 0.7), "team": "x"}
             for v in tes]
    out = run_js(app, {"sales": sales, "suggest": 6}, tmp_path)
    state = _py_state(sfb16, scored, sales)
    js_te = [r["id"] for r in out["suggestions"] if r["pos"] == "TE"]
    py_te = [r.player.player_id for r in state.suggestions(limit=6) if r.position == "TE"]
    assert js_te and js_te == py_te


def test_endgame_edge_matches_python(app, sfb16, scored, py_board, tmp_path):
    filler = [v.player.player_id for v in py_board.valuations[100:118]]
    sales = [{"id": pid, "price": 1, "team": "you"} for pid in filler]
    out = run_js(app, {"sales": sales, "suggest": 4}, tmp_path)
    state = _py_state(sfb16, scored, [dict(s, team="me") for s in sales])
    py = state.suggestions(limit=4)
    assert out["suggestions"] and py

    # Compared as sets: players can tie exactly on projected points, and the
    # order between tied players is not defined by either implementation.
    js_ids = {r["id"] for r in out["suggestions"]}
    py_ids = {r.player.player_id for r in py}
    assert len(js_ids & py_ids) >= len(py) - 1

    py_bids = {r.player.player_id: r.max_bid for r in py}
    for row in out["suggestions"]:
        if row["id"] in py_bids:
            assert row["max"] == pytest.approx(py_bids[row["id"]], abs=1)


# -- live auction values, team money, and the target roster ----------------


def test_an_expected_bid_reports_its_gap_from_value(app, py_board, tmp_path):
    """Type in what you think a player goes for; see how far that is off."""
    target = py_board.top(1)[0]
    pid = target.player.player_id
    bid = round(target.value * 0.6)
    out = run_js(app, {"expected": {pid: bid}, "bids": [pid]}, tmp_path)
    row = out["bids"][0]
    assert row["bid"] == bid
    assert row["effective"] == bid, "the optimizer must price off the entered bid"
    assert row["pct"] == pytest.approx(bid / target.value - 1, abs=0.01)


def test_a_player_with_no_bid_entered_has_no_gap(app, py_board, tmp_path):
    pid = py_board.top(1)[0].player.player_id
    out = run_js(app, {"bids": [pid]}, tmp_path)
    assert out["bids"][0]["bid"] is None
    assert out["bids"][0]["pct"] is None


def test_a_recorded_sale_is_the_bid(app, py_board, tmp_path):
    target = py_board.top(1)[0]
    pid = target.player.player_id
    sales = [{"id": pid, "price": 500, "team": "Team 2"}]
    out = run_js(app, {"sales": sales, "bids": [pid]}, tmp_path)
    assert out["bids"][0]["bid"] == 500
    assert out["bids"][0]["pct"] == pytest.approx(500 / target.value - 1, abs=0.01)


# -- team money ------------------------------------------------------------


def test_every_team_in_the_league_is_tracked(app, sfb16, tmp_path):
    out = run_js(app, {}, tmp_path)
    assert len(out["teams"]) == sfb16.teams
    assert out["league"]["money"] == sfb16.total_budget
    assert out["league"]["slots"] == sfb16.drafted_players


def test_typing_in_a_team_budget_moves_the_market(app, py_board, tmp_path):
    """Money you cannot see still sets prices, so it has to be correctable."""
    pid = py_board.top(2)[1].player.player_id
    before = run_js(app, {"bids": [pid]}, tmp_path)
    after = run_js(app, {"teamEdits": {"Team 2": {"budget": 100},
                                       "Team 3": {"budget": 100}},
                         "bids": [pid]}, tmp_path)
    assert after["league"]["money"] < before["league"]["money"]
    assert after["bids"][0]["effective"] < before["bids"][0]["effective"]


def test_typing_in_a_team_roster_count_changes_the_spots_left(app, sfb16, tmp_path):
    out = run_js(app, {"teamEdits": {"Team 2": {"players": 10}}}, tmp_path)
    assert out["teams"]["Team 2"]["players"] == 10
    assert out["teams"]["Team 2"]["open"] == sfb16.roster_size - 10
    assert out["league"]["slots"] == sfb16.drafted_players - 10


def test_a_corrected_team_keeps_updating_as_it_buys(app, py_board, tmp_path):
    """A correction should survive the next sale, not be overwritten by it."""
    pid = py_board.top(3)[2].player.player_id
    out = run_js(app, {"teamEdits": {"Team 2": {"budget": 500, "players": 3}},
                       "sales": [{"id": pid, "price": 120, "team": "Team 2"}]},
                 tmp_path)
    assert out["teams"]["Team 2"]["budget"] == 380
    assert out["teams"]["Team 2"]["players"] == 4


def test_your_own_budget_can_be_corrected(app, sfb16, tmp_path):
    out = run_js(app, {"teamEdits": {"you": {"budget": 250, "players": 5}}}, tmp_path)
    assert out["state"]["myBudget"] == 250
    assert out["state"]["myOpen"] == sfb16.roster_size - 5


# -- the target roster -----------------------------------------------------


@pytest.fixture(scope="module")
def studs(py_board):
    return [v.player.player_id for v in py_board.top(3)]


def test_the_plan_fills_the_roster_and_spends_the_budget(app, sfb16, tmp_path):
    out = run_js(app, {"plan": True}, tmp_path)
    plan = out["plan"]
    assert len(plan["additions"]) == sfb16.roster_size
    assert plan["spend"] <= sfb16.budget + 1e-6
    # Unspent money scores nothing, so a plan that leaves much behind is wrong.
    assert plan["spend"] >= sfb16.budget * 0.95
    assert len(plan["starters"]) == sfb16.starters


def test_the_plan_lands_near_the_exact_solver(app, sfb16, py_board, tmp_path):
    """The app uses greedy plus swaps; the CLI runs an integer program."""
    from sportsball.optimize import optimize_roster

    out = run_js(app, {"plan": True}, tmp_path)
    exact = optimize_roster(py_board, sfb16)
    assert out["plan"]["lineupPts"] >= exact.starter_points * 0.97


def test_the_plan_takes_players_you_expect_to_go_cheap(app, py_board, studs,
                                                       tmp_path):
    """This is the point of entering bids: it replans around the real room."""
    values = {v.player.player_id: v.value for v in py_board}
    cheap = {pid: round(values[pid] * 0.35) for pid in studs}
    base = run_js(app, {"plan": True}, tmp_path)
    out = run_js(app, {"expected": cheap, "plan": True}, tmp_path)
    taken = [pid for pid in studs if pid in out["plan"]["additions"]]
    assert len(taken) == len(studs)
    assert out["plan"]["lineupPts"] > base["plan"]["lineupPts"]


def test_the_plan_avoids_players_you_expect_to_go_over(app, py_board, studs,
                                                       tmp_path):
    values = {v.player.player_id: v.value for v in py_board}
    dear = {pid: round(values[pid] * 2.5) for pid in studs}
    out = run_js(app, {"expected": dear, "plan": True}, tmp_path)
    assert not [pid for pid in studs if pid in out["plan"]["additions"]]


def test_the_plan_keeps_players_you_already_bought(app, py_board, tmp_path):
    pid = py_board.top(1)[0].player.player_id
    out = run_js(app, {"sales": [{"id": pid, "price": 300, "team": "you"}],
                       "plan": True}, tmp_path)
    assert pid in out["plan"]["starters"]
    assert out["plan"]["spend"] <= out["state"]["myBudget"] + 1e-6


def test_the_plan_shrinks_as_your_money_does(app, tmp_path):
    rich = run_js(app, {"plan": True}, tmp_path)
    poor = run_js(app, {"teamEdits": {"you": {"budget": 200}}, "plan": True},
                  tmp_path)
    assert poor["plan"]["lineupPts"] < rich["plan"]["lineupPts"]
    assert poor["plan"]["spend"] <= 200 + 1e-6


# -- shipping with a draft already in progress -----------------------------


SEED_PATH = ROOT / "src" / "sportsball" / "data" / "seed_draft.json"


@pytest.fixture(scope="module")
def seed():
    return json.loads(SEED_PATH.read_text())


@pytest.fixture(scope="module")
def seeded_app(tmp_path_factory):
    out = tmp_path_factory.mktemp("seeded") / "app.html"
    subprocess.run(
        [sys.executable, str(ROOT / "tools" / "build_app.py"),
         "--seed", str(SEED_PATH), "--out", str(out)],
        check=True, capture_output=True,
    )
    return out


def test_the_board_opens_with_the_draft_already_recorded(seeded_app, seed, tmp_path):
    out = run_js(seeded_app, {"load": True}, tmp_path)
    mine = [s for s in seed["sales"] if s["team"] == "you"]
    assert out["state"]["myOpen"] == 20 - len(mine)
    assert out["state"]["myBudget"] == seed["teamEdits"]["you"]["budget"]


def test_every_manager_budget_survives_the_trip(seeded_app, seed, tmp_path):
    """The seed's figures are what the room reported; they must arrive intact."""
    out = run_js(seeded_app, {"load": True}, tmp_path)
    for team, edit in seed["teamEdits"].items():
        assert out["teams"][team]["budget"] == edit["budget"], team


def test_managers_who_have_not_bought_are_still_seats_at_the_table(
    seeded_app, seed, sfb16, tmp_path
):
    """Their money sets prices too, so the league must still be full size."""
    out = run_js(seeded_app, {"load": True}, tmp_path)
    assert len(out["teams"]) == sfb16.teams
    untouched = [t for t, v in out["teams"].items() if v["budget"] == sfb16.budget]
    assert len(untouched) == sfb16.teams - len(seed["teamEdits"])


def test_the_seeded_league_money_matches_the_seeded_budgets(seeded_app, sfb16,
                                                            seed, tmp_path):
    out = run_js(seeded_app, {"load": True}, tmp_path)
    expected = sum(e["budget"] for e in seed["teamEdits"].values())
    expected += (sfb16.teams - len(seed["teamEdits"])) * sfb16.budget
    assert out["league"]["money"] == expected


def test_a_room_paying_under_value_inflates_what_is_left(seeded_app, tmp_path):
    """Money not spent on the players already gone has to land somewhere."""
    out = run_js(seeded_app, {"load": True}, tmp_path)
    assert out["state"]["inflation"] > 1.0
    assert all(r["ratio"] < 1.0 for r in out["room"].values())


def test_seeded_players_are_off_the_board(seeded_app, seed, tmp_path):
    sold = [s["id"] for s in seed["sales"]][:5]
    out = run_js(seeded_app, {"load": True, "bids": sold}, tmp_path)
    prices = {s["id"]: s["price"] for s in seed["sales"]}
    for row in out["bids"]:
        assert row["bid"] == prices[row["id"]]


def test_the_plan_from_a_seeded_board_fills_what_is_left(seeded_app, sfb16, seed,
                                                         tmp_path):
    out = run_js(seeded_app, {"load": True, "plan": True}, tmp_path)
    mine = len([s for s in seed["sales"] if s["team"] == "you"])
    assert len(out["plan"]["additions"]) == sfb16.roster_size - mine
    assert out["plan"]["spend"] <= out["state"]["myBudget"] + 1e-6
    assert len(out["plan"]["starters"]) == sfb16.starters


def test_an_unseeded_build_still_starts_empty(app, tmp_path):
    out = run_js(app, {"load": True}, tmp_path)
    assert out["state"]["myOpen"] == 20
    assert out["state"]["inflation"] == pytest.approx(1.0, abs=1e-6)


# -- players still under bidding -------------------------------------------


def test_a_player_under_bidding_stays_on_the_board(app, py_board, tmp_path):
    """He is not sold: still gettable, and the standing bid is what it takes."""
    target = py_board.top(4)[3].player.player_id
    out = run_js(app, {"roomBids": {target: 120}, "bids": [target]}, tmp_path)
    row = out["bids"][0]
    assert row["open"] is True
    assert row["source"] == "open"
    assert row["bid"] == 120
    assert row["effective"] == 120


def test_the_standing_bid_beats_your_guess(app, py_board, tmp_path):
    """What the room is actually paying outranks what you typed."""
    target = py_board.top(4)[3].player.player_id
    out = run_js(app, {"expected": {target: 500}, "roomBids": {target: 120},
                       "bids": [target]}, tmp_path)
    assert out["bids"][0]["bid"] == 120


def test_a_sale_beats_a_standing_bid(app, py_board, tmp_path):
    """Once the gavel falls the sale price is the record."""
    target = py_board.top(4)[3].player.player_id
    out = run_js(app, {"roomBids": {target: 120},
                       "sales": [{"id": target, "price": 140, "team": "Team 2"}],
                       "bids": [target]}, tmp_path)
    assert out["bids"][0]["bid"] == 140
    assert out["bids"][0]["source"] == "sold"
    assert out["bids"][0]["open"] is False


def test_the_plan_can_still_take_a_player_under_bidding(app, py_board, tmp_path):
    """A cheap standing bid is exactly the sort of thing to chase."""
    target = py_board.top(4)[3]
    pid = target.player.player_id
    out = run_js(app, {"roomBids": {pid: round(target.value * 0.3)},
                       "plan": True}, tmp_path)
    assert pid in out["plan"]["additions"]


def test_a_bare_build_opens_on_the_real_draft(tmp_path, seed):
    """The default build must ship the seed.

    An unseeded page renders without complaint and looks right; the only tell
    is that every "now" price equals list value, because nothing has been sold
    to reprice against. That is a silent way to hand someone a dead board.
    """
    out = tmp_path / "bare.html"
    subprocess.run(
        [sys.executable, str(ROOT / "tools" / "build_app.py"), "--out", str(out)],
        check=True, capture_output=True,
    )
    state = run_js(out, {"load": True}, tmp_path)
    assert state["state"]["myBudget"] == seed["teamEdits"]["you"]["budget"]
    assert state["state"]["inflation"] > 1.0


def test_a_seeded_board_prices_survivors_above_list(seeded_app, py_board, seed,
                                                    tmp_path):
    """The whole point of the now price: the room underpaid, so the rest cost more."""
    gone = {s["id"] for s in seed["sales"]}
    survivor = next(v.player.player_id for v in py_board.top(60)
                    if v.player.player_id not in gone)
    out = run_js(seeded_app, {"load": True, "bids": [survivor]}, tmp_path)
    baseline = run_js(seeded_app, {"bids": [survivor]}, tmp_path)
    assert out["bids"][0]["effective"] > baseline["bids"][0]["effective"]
