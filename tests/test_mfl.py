"""Importing a MyFantasyLeague auction.

MFL scores in the league's own rules, so its projections arrive as SFB16 points
and need no re-scoring. Its response shapes vary by endpoint — a collection
with one member comes back as a bare object rather than a list — so the readers
are deliberately tolerant, and these tests hold that tolerance in place against
recorded payloads rather than the live service.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from sportsball.mfl import (  # noqa: E402
    MFLError, _auction_is_open, build_seed_state as build_seed, build_url, dig,
    fetch, flip_name, listify, read_auction, read_franchises, read_players,
    read_projections, read_roster_salaries, read_salary_cap,
)

FIXTURES = Path(__file__).parent / "fixtures" / "mfl"


@pytest.fixture(scope="module")
def payloads():
    return {p.stem: json.loads(p.read_text()) for p in FIXTURES.glob("*.json")}


# -- shape handling --------------------------------------------------------


def test_a_single_member_collection_is_still_a_collection():
    """MFL drops the list when a collection has exactly one member."""
    assert listify({"id": "1"}) == [{"id": "1"}]
    assert listify([{"id": "1"}, {"id": "2"}]) == [{"id": "1"}, {"id": "2"}]
    assert listify(None) == []


def test_digging_through_a_missing_layer_returns_nothing():
    assert dig({"a": {"b": 1}}, "a", "b") == 1
    assert dig({"a": {}}, "a", "b") is None
    assert dig({}, "a", "b", "c") is None
    assert dig("not a mapping", "a") is None


def test_names_are_flipped_into_reading_order():
    assert flip_name("Nacua, Puka") == "Puka Nacua"
    assert flip_name("Walker III, Kenneth") == "Kenneth Walker III"
    assert flip_name("Chase, Ja'Marr") == "Ja'Marr Chase"
    assert flip_name("Already Forward") == "Already Forward"


# -- reading the endpoints -------------------------------------------------


def test_only_scoring_positions_reach_the_board(payloads):
    players = read_players(payloads["players"])
    assert {p["position"] for p in players.values()} <= {"QB", "RB", "WR", "TE"}
    assert not any(p["name"].startswith("Kicker") for p in players.values())


def test_franchises_and_cap_are_read(payloads):
    assert read_franchises(payloads["league"])["0001"] == "Jeffrey Smar"
    assert read_salary_cap(payloads["league"]) == 1000.0


def test_every_auction_row_is_read_and_flagged(payloads):
    rows = read_auction(payloads["auctionResults"])
    assert len(rows) == 7
    assert sum(1 for r in rows if not r["open"]) == 6
    top = next(r for r in rows if r["player"] == "14801")
    assert top["price"] == 155.0
    assert top["franchise"] == "0004"
    assert top["open"] is False


def test_a_franchise_with_one_player_still_parses(payloads):
    """That roster comes back as a bare object, not a list of one."""
    assert read_roster_salaries(payloads["rosters"])["0002"] == 100.0


def test_league_scored_projections_are_read(payloads):
    scores = read_projections(payloads["projectedScores"])
    assert scores["14801"] == pytest.approx(721.6)


# -- the seed --------------------------------------------------------------


@pytest.fixture(scope="module")
def seed(payloads):
    return build_seed(
        read_players(payloads["players"]),
        read_auction(payloads["auctionResults"]),
        read_franchises(payloads["league"]),
        read_salary_cap(payloads["league"]),
        read_roster_salaries(payloads["rosters"]),
        "Jeffrey Smar",
    )


def test_your_franchise_becomes_you(seed):
    built, _ = seed
    mine = [s for s in built["sales"] if s["team"] == "you"]
    assert len(mine) == 2
    assert sum(s["price"] for s in mine) == 300


def test_budgets_come_from_committed_salary_not_the_skill_sales(seed):
    """A kicker's salary is spent money even though he is not on this board."""
    built, notes = seed
    assert built["teamEdits"]["PunkerDad"]["budget"] == 917
    assert built["teamEdits"]["PunkerDad"]["players"] == 1
    assert any("outside QB/RB/WR/TE" in n for n in notes)


def test_every_franchise_is_carried_even_without_sales(payloads):
    """Their money still sets prices, so they must be seats at the table."""
    built, _ = build_seed(
        read_players(payloads["players"]), [],
        read_franchises(payloads["league"]),
        read_salary_cap(payloads["league"]), {}, "Jeffrey Smar",
    )
    assert len(built["teamEdits"]) == 4
    assert all(t["budget"] == 1000 for t in built["teamEdits"].values())


def test_an_unmatched_manager_name_is_reported(payloads):
    _, notes = build_seed(
        read_players(payloads["players"]),
        read_auction(payloads["auctionResults"]),
        read_franchises(payloads["league"]),
        read_salary_cap(payloads["league"]),
        read_roster_salaries(payloads["rosters"]),
        "Nobody At All",
    )
    assert any("no franchise named" in n for n in notes)


# -- urls and failures -----------------------------------------------------


def test_the_url_carries_the_league_and_key():
    url = build_url("www43", 2026, "auctionResults", "36570", "SECRET", None)
    assert "www43.myfantasyleague.com/2026/export" in url
    assert "TYPE=auctionResults" in url and "L=36570" in url
    assert "APIKEY=SECRET" in url and "JSON=1" in url


def test_a_private_league_login_page_is_explained(tmp_path):
    """MFL answers with HTML, not an error code, when credentials are missing."""
    page = tmp_path / "login.html"
    page.write_text("<html><body>Please log in</body></html>")
    with pytest.raises(MFLError, match="--apikey"):
        fetch(page.resolve().as_uri())


# -- end to end ------------------------------------------------------------


def test_the_cli_writes_a_board_and_a_seed(tmp_path):
    out = tmp_path / "mfl"
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "fetch_mfl.py"),
         "--from-dir", str(FIXTURES), "--me", "Jeffrey Smar",
         "--out-dir", str(out)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (out / "projections.csv").exists()
    assert (out / "seed.json").exists()


def test_mfl_projections_are_used_verbatim(tmp_path, sfb16):
    """The whole reason to prefer MFL: its points are already this format."""
    from sportsball.players import load_projections
    from sportsball.scoring import score_all

    out = tmp_path / "mfl"
    subprocess.run(
        [sys.executable, str(ROOT / "tools" / "fetch_mfl.py"),
         "--from-dir", str(FIXTURES), "--me", "Jeffrey Smar",
         "--out-dir", str(out)],
        check=True, capture_output=True,
    )
    players = {p.name: p for p in score_all(load_projections(out / "projections.csv"),
                                            sfb16)}
    assert players["Puka Nacua"].points == pytest.approx(721.6)
    assert players["Puka Nacua"].supplied_points == pytest.approx(721.6)
    assert players["Puka Nacua"].bonus_points == 0.0


def test_missing_endpoints_do_not_stop_the_import(tmp_path):
    """A league with no auction yet, or no published projections, still works."""
    partial = tmp_path / "partial"
    partial.mkdir()
    for name in ("league", "players"):
        (partial / f"{name}.json").write_text((FIXTURES / f"{name}.json").read_text())
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "fetch_mfl.py"),
         "--from-dir", str(partial), "--out-dir", str(tmp_path / "out")],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "no projections available" in result.stdout
    seed = json.loads((tmp_path / "out" / "seed.json").read_text())
    assert seed["sales"] == []
    assert len(seed["teamEdits"]) == 4


# -- sold, or still under bidding? -----------------------------------------


def test_a_row_that_says_nothing_is_treated_as_sold():
    """The endpoint is named for results; an unknown defaults to finished."""
    assert _auction_is_open({"winningBid": "50"}) is False


@pytest.mark.parametrize("row,expected", [
    ({"status": "open"}, True),
    ({"status": "BIDDING"}, True),
    ({"status": "closed"}, False),
    ({"status": "sold"}, False),
    ({"closed": "0"}, True),
    ({"closed": "1"}, False),
    ({"timeRemaining": "3600"}, True),
    ({"timeRemaining": "0"}, False),
])
def test_the_open_flag_is_read_however_it_is_spelled(row, expected):
    assert _auction_is_open(row) is expected


def test_an_end_time_decides_by_the_clock():
    import time as _time

    now = _time.time()
    assert _auction_is_open({"bidEnd": str(int(now + 7200))}, now) is True
    assert _auction_is_open({"bidEnd": str(int(now - 7200))}, now) is False
    # Some feeds report milliseconds.
    assert _auction_is_open({"bidEnd": str(int((now + 7200) * 1000))}, now) is True


def test_an_open_auction_is_not_a_sale(payloads):
    """Calling one sold takes a gettable player off the board at a price that
    is not final."""
    rows = read_auction(payloads["auctionResults"])
    still_open = [r for r in rows if r["open"]]
    assert len(still_open) == 1
    assert still_open[0]["player"] == "14103"
    assert still_open[0]["price"] == 115.0


def test_open_auctions_ride_through_as_prices_not_sales(payloads):
    built, notes = build_seed(
        read_players(payloads["players"]),
        read_auction(payloads["auctionResults"]),
        read_franchises(payloads["league"]),
        read_salary_cap(payloads["league"]),
        read_roster_salaries(payloads["rosters"]),
        "Jeffrey Smar",
    )
    assert "brock-bowers-te" not in {s["id"] for s in built["sales"]}
    assert built["openBids"]["brock-bowers-te"] == 115
    assert any("under bidding" in n for n in notes)


def test_an_open_auction_does_not_spend_a_budget(payloads):
    """The money is only committed when the gavel falls."""
    built, _ = build_seed(
        read_players(payloads["players"]),
        read_auction(payloads["auctionResults"]),
        read_franchises(payloads["league"]),
        read_salary_cap(payloads["league"]),
        {},  # no roster salaries, so budgets fall back to summing sales
        "Jeffrey Smar",
    )
    # Allan Hepworth's only closed sale is Josh Allen at 100.
    assert built["teamEdits"]["Allan Hepworth"]["budget"] == 900
