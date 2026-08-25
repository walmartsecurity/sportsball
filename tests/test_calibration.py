"""Rescaling the projected distribution onto history's spread.

Shrinking every player toward a positional prior makes individual projections
accurate and makes the distribution too narrow. That matters for an auction,
where value over replacement is a distance and dollars are proportional to it.
These pin the behaviour of the opt-in correction.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

pd = pytest.importorskip("pandas", reason="calibration needs the [fit] extras")

from project_from_nflverse import calibrate_spread  # noqa: E402

STAT_COLS = ["games", "pass_att", "pass_yds", "pass_td", "interceptions",
             "rush_att", "rush_yds", "rush_td", "rush_first_downs", "targets",
             "receptions", "rec_yds", "rec_td", "rec_first_downs"]


def frame(receptions):
    rows = []
    for i, rec in enumerate(receptions):
        row = {c: 0.0 for c in STAT_COLS}
        row.update(games=16.0, position="WR", pid=f"p{i}",
                   receptions=rec, targets=rec * 1.5, rec_yds=rec * 12)
        rows.append(row)
    return pd.DataFrame(rows)


def test_calibration_adopts_the_curve(sfb16):
    curve = [700.0, 500.0, 300.0]
    out = calibrate_spread(frame([100, 70, 40]), curve, sfb16)
    assert list(out.fantasy_points) == curve


def test_calibration_leaves_the_ranking_alone(sfb16):
    """It changes the spread, never who is better than whom."""
    df = frame([40, 100, 70])
    out = calibrate_spread(df, [700.0, 500.0, 300.0], sfb16)
    ranked = out.sort_values("fantasy_points", ascending=False).receptions.tolist()
    assert ranked == [100, 70, 40]


def test_the_board_takes_the_curve_spread_exactly(sfb16):
    """The contract: whatever spread the curve has becomes the board's spread."""
    df = frame([110, 95, 80, 65, 50, 35])
    curve = [720.0, 600.0, 480.0, 380.0, 300.0, 230.0]
    after = calibrate_spread(df, curve, sfb16).fantasy_points.tolist()
    assert sorted(after, reverse=True) == curve
    assert max(after) / min(after) == pytest.approx(max(curve) / min(curve))


def test_an_empty_curve_changes_nothing(sfb16):
    df = frame([100, 70])
    out = calibrate_spread(df, [], sfb16)
    assert "fantasy_points" not in out or out.equals(df)


def test_a_short_curve_does_not_run_off_the_end(sfb16):
    """More players than history has ranks: the tail clamps rather than crashing."""
    out = calibrate_spread(frame([100, 70, 40, 20]), [700.0, 500.0], sfb16)
    assert len(out) == 4
    assert out.fantasy_points.tolist() == [700.0, 500.0, 500.0, 500.0]


def test_calibrated_points_reach_the_board_as_supplied_points(sfb16, tmp_path):
    """End to end: the CSV carries them and the scoring engine defers."""
    from sportsball.players import load_projections
    from sportsball.scoring import score_all

    path = tmp_path / "p.csv"
    path.write_text(
        "name,position,team,fantasy_points,rec,rec_yds,rec_td\n"
        "Top Guy,WR,LA,721.6,105,1382,8\n"
    )
    player = score_all(load_projections(path), sfb16)[0]
    assert player.points == 721.6
    assert player.supplied_points == 721.6
