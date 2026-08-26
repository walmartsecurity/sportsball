"""Rescaling the projected distribution onto history's spread.

Shrinking every player toward a positional prior makes individual projections
accurate and makes the distribution too narrow. That matters for an auction,
where value over replacement is a distance and dollars are proportional to it.
These pin the behaviour of the opt-in correction.

The correction runs per position, against that position's own history. The
tests below hold it to both halves of what that buys: each position takes its
own *shape*, and each position's *level* can move relative to the others --
which a single pooled ranking could not do, because it never compared a
position against itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

pd = pytest.importorskip("pandas", reason="calibration needs the [fit] extras")

from project_from_nflverse import (  # noqa: E402
    calibrate_spread, historical_rank_curves,
)

STAT_COLS = ["games", "pass_att", "pass_yds", "pass_td", "interceptions",
             "rush_att", "rush_yds", "rush_td", "rush_first_downs", "targets",
             "receptions", "rec_yds", "rec_td", "rec_first_downs"]


def rows_for(position, volumes, start=0):
    """Stat lines at one position, ordered best first by the volume given."""
    rows = []
    for i, volume in enumerate(volumes):
        row = {c: 0.0 for c in STAT_COLS}
        row.update(games=16.0, position=position, pid=f"{position}{start + i}")
        if position == "QB":
            row.update(pass_att=volume * 4, pass_yds=volume * 30,
                       pass_td=volume * 0.2)
        else:
            row.update(receptions=volume, targets=volume * 1.5,
                       rec_yds=volume * 12)
        rows.append(row)
    return rows


def frame(receptions):
    return pd.DataFrame(rows_for("WR", receptions))


def test_calibration_adopts_the_curve(sfb16):
    curve = [700.0, 500.0, 300.0]
    out = calibrate_spread(frame([100, 70, 40]), {"WR": curve}, sfb16)
    assert list(out.fantasy_points) == curve


def test_calibration_leaves_the_ranking_alone(sfb16):
    """It changes the spread, never who is better than whom."""
    df = frame([40, 100, 70])
    out = calibrate_spread(df, {"WR": [700.0, 500.0, 300.0]}, sfb16)
    ranked = out.sort_values("fantasy_points", ascending=False).receptions.tolist()
    assert ranked == [100, 70, 40]


def test_the_board_takes_the_curve_spread_exactly(sfb16):
    """The contract: whatever spread the curve has becomes the board's spread."""
    df = frame([110, 95, 80, 65, 50, 35])
    curve = [720.0, 600.0, 480.0, 380.0, 300.0, 230.0]
    after = calibrate_spread(df, {"WR": curve}, sfb16).fantasy_points.tolist()
    assert sorted(after, reverse=True) == curve
    assert max(after) / min(after) == pytest.approx(max(curve) / min(curve))


def test_an_empty_curve_changes_nothing(sfb16):
    df = frame([100, 70])
    out = calibrate_spread(df, {}, sfb16)
    assert "fantasy_points" not in out or out.equals(df)


def test_a_short_curve_does_not_run_off_the_end(sfb16):
    """More players than history has ranks: the tail clamps rather than crashing."""
    out = calibrate_spread(frame([100, 70, 40, 20]), {"WR": [700.0, 500.0]}, sfb16)
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


# -- one curve per position ------------------------------------------------

def two_position_frame():
    """Four receivers and three quarterbacks, each ordered best first."""
    return pd.DataFrame(rows_for("WR", [110, 90, 70, 50])
                        + rows_for("QB", [140, 120, 100]))


def test_each_position_lands_on_its_own_curve(sfb16):
    """A quarterback is ranked against quarterbacks, not against the field."""
    curves = {"WR": [700.0, 560.0, 430.0, 320.0], "QB": [640.0, 600.0, 520.0]}
    out = calibrate_spread(two_position_frame(), curves, sfb16)
    by_pos = {pos: sorted(d.fantasy_points, reverse=True)
              for pos, d in out.groupby("position")}
    assert by_pos["WR"] == curves["WR"]
    assert by_pos["QB"] == curves["QB"]


def test_a_positions_level_is_corrected_not_just_its_shape(sfb16):
    """The pooled version could not do this.

    Every quarterback here projects below every receiver, so on one pooled
    ranking they take the bottom of the curve whatever history says about
    them. Ranked within the position they take the quarterback curve, which
    puts the best of them above the best receiver.
    """
    frame_ = pd.DataFrame(rows_for("WR", [110, 90, 70]) + rows_for("QB", [8, 6, 4]))
    curves = {"WR": [400.0, 360.0, 300.0], "QB": [700.0, 650.0, 600.0]}
    out = calibrate_spread(frame_, curves, sfb16)
    best = out.sort_values("fantasy_points", ascending=False).iloc[0]
    assert best.position == "QB"
    assert best.fantasy_points == 700.0


def test_a_position_with_no_history_is_left_alone_and_says_so(sfb16):
    """Silently leaving it on the model's scale would misprice it."""
    notes = []
    out = calibrate_spread(two_position_frame(), {"WR": [700.0, 560.0, 430.0, 320.0]},
                           sfb16, on_note=notes.append)
    qbs = out[out.position == "QB"]
    assert sorted(out[out.position == "WR"].fantasy_points, reverse=True) == [
        700.0, 560.0, 430.0, 320.0]
    # Untouched: still whatever the league's own scoring makes of the stat line.
    assert qbs.fantasy_points.max() < 700.0
    assert any("QB" in n and "uncalibrated" in n for n in notes)


def test_ranking_within_a_position_survives(sfb16):
    """Shuffled input, and every position still comes out in its own order."""
    shuffled = pd.DataFrame(rows_for("WR", [70, 110, 90])
                            + rows_for("QB", [100, 140, 120]))
    curves = {"WR": [700.0, 560.0, 430.0], "QB": [640.0, 600.0, 520.0]}
    out = calibrate_spread(shuffled, curves, sfb16)
    wr = out[out.position == "WR"].sort_values("fantasy_points", ascending=False)
    assert wr.receptions.tolist() == [110, 90, 70]
    qb = out[out.position == "QB"].sort_values("fantasy_points", ascending=False)
    assert qb.pass_att.tolist() == [140 * 4, 120 * 4, 100 * 4]


def test_the_curves_are_built_per_position(sfb16):
    """Depth and level both come from the position's own seasons."""
    history = pd.DataFrame(
        rows_for("WR", [120, 100, 80], start=0) + rows_for("QB", [150, 130], start=0)
    ).assign(season=2024)
    history = pd.concat([history, pd.DataFrame(
        rows_for("WR", [110, 90, 70], start=10) + rows_for("QB", [140, 120], start=10)
    ).assign(season=2025)])

    curves = historical_rank_curves(history, sfb16)
    assert set(curves) == {"WR", "QB"}
    assert len(curves["WR"]) == 3 and len(curves["QB"]) == 2
    # Each rank is that rank's average across the seasons, so the curve falls.
    assert curves["WR"] == sorted(curves["WR"], reverse=True)
    assert curves["QB"][0] > curves["WR"][0]


def test_curves_are_only_as_deep_as_the_thinnest_season(sfb16):
    """A season short at a position cannot invent ranks for it."""
    history = pd.concat([
        pd.DataFrame(rows_for("WR", [120, 100, 80], start=0)).assign(season=2024),
        pd.DataFrame(rows_for("WR", [110, 90], start=10)).assign(season=2025),
    ])
    assert len(historical_rank_curves(history, sfb16)["WR"]) == 2
