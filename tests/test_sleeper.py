"""Importing Sleeper's projected stat lines.

Sleeper's API is undocumented and unreachable from some networks, so the parser
is exercised against recorded payloads rather than the live service. What is
pinned here is the shape handling and the field mapping — the parts that break
silently and produce a plausible-looking board built on zeros.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from fetch_sleeper import SleeperError, convert, rows_of, write_csv  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "sleeper_projections.json"


@pytest.fixture(scope="module")
def payload():
    return json.loads(FIXTURE.read_text())


@pytest.fixture(scope="module")
def rows(payload):
    return convert(payload)


def find(rows, name):
    return next(r for r in rows if r["name"] == name)


def test_skill_players_are_kept_and_others_dropped(rows):
    names = {r["name"] for r in rows}
    assert {"Ja'Marr Chase", "Brock Bowers", "Lamar Jackson", "Bijan Robinson"} <= names
    assert "Some Linebacker" not in names


def test_receiving_stats_map_across(rows):
    chase = find(rows, "Ja'Marr Chase")
    assert chase["targets"] == 175
    assert chase["rec"] == 118
    assert chase["rec_yds"] == 1650
    assert chase["rec_td"] == 13
    assert chase["position"] == "WR"
    assert chase["team"] == "CIN"


def test_passing_stats_map_across(rows):
    lamar = find(rows, "Lamar Jackson")
    assert lamar["pass_att"] == 480
    assert lamar["pass_yds"] == 3900
    assert lamar["pass_td"] == 32
    assert lamar["int"] == 7
    assert lamar["rush_yds"] == 800


def test_first_downs_are_taken_when_projected(rows):
    """SFB16 scores these and almost no source publishes them."""
    assert find(rows, "Ja'Marr Chase")["rec_first_downs"] == 71
    assert find(rows, "Lamar Jackson")["rush_first_downs"] == 48


def test_missing_first_downs_stay_blank_rather_than_zero(rows):
    """Blank means 'estimate it'; zero would score as genuinely none."""
    bijan = find(rows, "Bijan Robinson")
    assert bijan["rec_first_downs"] == ""
    assert bijan["rush_first_downs"] == ""


def test_two_point_conversions_are_summed(rows):
    assert find(rows, "Lamar Jackson")["two_point"] == pytest.approx(0.6)


def test_completions_are_filled_in_when_absent(rows):
    """Chase has pass attempts of zero, so nothing to fill; Bijan has none."""
    assert find(rows, "Bijan Robinson")["pass_cmp"] == 0.0


def test_games_default_when_not_projected():
    rows = convert([{"player": {"full_name": "No Games", "position": "WR"},
                     "stats": {"rec": 50, "rec_yd": 600}}])
    assert rows[0]["games"] == 17.0
    rows = convert([{"player": {"full_name": "No Games", "position": "WR"},
                     "stats": {"rec": 50}}], season_games=15.0)
    assert rows[0]["games"] == 15.0


# -- payload shapes --------------------------------------------------------


def test_an_object_keyed_by_player_id_is_accepted(payload):
    keyed = {str(i): row for i, row in enumerate(payload)}
    assert len(rows_of(keyed)) == len(payload)
    assert len(convert(keyed)) == 4


def test_a_flat_row_without_a_nested_player_object_works():
    rows = convert([{"full_name": "Flat Guy", "position": "TE", "team": "KC",
                     "stats": {"rec": 60, "rec_yd": 700, "rec_td": 5}}])
    assert rows[0]["name"] == "Flat Guy"
    assert rows[0]["position"] == "TE"


def test_first_and_last_name_are_joined_when_full_name_is_absent():
    rows = convert([{"player": {"first_name": "Split", "last_name": "Name",
                                "position": "WR"},
                     "stats": {"rec": 40, "rec_yd": 500}}])
    assert rows[0]["name"] == "Split Name"


def test_alternate_stat_key_spellings_are_understood():
    rows = convert([{"player": {"full_name": "Alt Keys", "position": "WR"},
                     "stats": {"targets": 90, "rec": 60, "rec_yds": 800,
                               "rec_td": 4}}])
    assert rows[0]["targets"] == 90
    assert rows[0]["rec_yds"] == 800


def test_an_unrecognised_payload_says_so():
    with pytest.raises(SleeperError, match="unrecognised payload"):
        rows_of("not json we know")


def test_a_payload_with_nothing_usable_says_so():
    with pytest.raises(SleeperError, match="no usable projections"):
        convert([{"player": {"full_name": "Kicker", "position": "K"},
                  "stats": {"fgm": 30}}])


# -- the whole chain -------------------------------------------------------


def test_sleeper_projections_score_under_sfb16_rules(rows, sfb16, tmp_path):
    """The point of the exercise: Sleeper's stats, this league's scoring."""
    from sportsball.players import load_projections
    from sportsball.scoring import score_all
    from sportsball.valuation import value_players

    out = tmp_path / "sleeper.csv"
    write_csv(rows, out, limit=100)
    players = score_all(load_projections(out), sfb16)
    board = value_players(players, sfb16)

    assert len(players) == 4
    chase = next(p for p in players if p.name == "Ja'Marr Chase")
    bowers = next(p for p in players if p.name == "Brock Bowers")

    # Sleeper ranks Chase well ahead of Bowers on PPR; the tight end premium
    # and the bonus model narrow that considerably under SFB16.
    assert chase.points > 0 and bowers.points > 0
    assert bowers.points / chase.points > 0.8
    assert chase.bonus_points > 0
    assert board.get(chase.player_id).value >= sfb16.min_bid


def test_supplied_first_downs_are_used_not_re_estimated(rows, sfb16, tmp_path):
    from sportsball.players import load_projections
    from sportsball.scoring import estimate_first_downs

    out = tmp_path / "s.csv"
    write_csv(rows, out, limit=100)
    loaded = {p.name: p for p in load_projections(out)}
    _, rec_fd = estimate_first_downs(loaded["Ja'Marr Chase"], sfb16.first_downs)
    assert rec_fd == 71


def test_cli_converts_a_saved_payload(tmp_path):
    out = tmp_path / "sleeper.csv"
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "fetch_sleeper.py"),
         "--from-json", str(FIXTURE), "--out", str(out)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert out.exists()
    assert "wrote 4 players" in result.stdout


def test_cli_inspect_reports_the_mapping(tmp_path):
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "fetch_sleeper.py"),
         "--from-json", str(FIXTURE), "--inspect"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "stat keys" in result.stdout
    assert "rec_first_downs" in result.stdout


def test_cli_reports_an_unreachable_api(tmp_path):
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "fetch_sleeper.py"),
         "--url", "https://127.0.0.1:9/nope", "--out", str(tmp_path / "x.csv")],
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "could not reach Sleeper" in result.stderr
    assert "--from-json" in result.stderr
