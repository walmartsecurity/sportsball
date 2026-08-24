"""Check the browser engine against the Python one.

The web app reimplements pricing, lineup assignment and the max-bid search in
JavaScript so it can run at a draft table without Python. Two implementations
of the same maths will drift unless something holds them together; this is that
something. Skipped when node or the built app is unavailable.
"""

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
    out = tmp_path_factory.mktemp("app") / "app.html"
    subprocess.run(
        [sys.executable, str(ROOT / "tools" / "build_app.py"), "--out", str(out)],
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
    assert [r["id"] for r in out["suggestions"]] == [r.player.player_id for r in py]
    for js, pyr in zip(out["suggestions"], py):
        assert js["max"] == pytest.approx(pyr.max_bid, abs=1)
