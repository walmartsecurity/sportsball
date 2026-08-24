"""Lineup assignment under overlapping slot eligibility."""

import pytest

from sportsball.config import LineupSlot
from sportsball.lineup import best_lineup, lineup_points, scale_lineup
from sportsball.players import Player, StatLine


def player(name, position, points):
    p = Player(name=name, position=position, stats=StatLine())
    p.points = points
    return p


SFB_LINEUP = (
    LineupSlot(2, ("QB", "RB", "WR", "TE"), "SUPERFLEX"),
    LineupSlot(8, ("RB", "WR", "TE"), "FLEX"),
)


def test_quarterbacks_are_capped_at_two():
    qbs = [player(f"QB{i}", "QB", 500 - i) for i in range(6)]
    fillers = [player(f"WR{i}", "WR", 100) for i in range(10)]
    solved = best_lineup(qbs + fillers, SFB_LINEUP)
    assert len(solved.starters) == 10
    assert sum(1 for p in solved.starters if p.position == "QB") == 2


def test_greedy_slotting_would_lose_points():
    """The case that breaks naive first-fit: don't burn superflex on a back.

    Ranked by points the order is RB, RB, QB. First-fit puts the two backs in
    the superflex group, leaving the quarterback with nowhere to go. The right
    answer keeps one back in the flex and starts the quarterback.
    """
    lineup = (
        LineupSlot(2, ("QB", "RB", "WR", "TE"), "SUPERFLEX"),
        LineupSlot(1, ("RB", "WR", "TE"), "FLEX"),
    )
    roster = [
        player("Back A", "RB", 300),
        player("Back B", "RB", 290),
        player("Passer", "QB", 280),
    ]
    solved = best_lineup(roster, lineup)
    assert solved.points == pytest.approx(870.0)
    assert {p.name for p in solved.starters} == {"Back A", "Back B", "Passer"}


def test_ineligible_positions_never_start():
    lineup = (LineupSlot(2, ("RB", "WR", "TE"), "FLEX"),)
    roster = [player("Passer", "QB", 999), player("Back", "RB", 10)]
    solved = best_lineup(roster, lineup)
    assert [p.name for p in solved.starters] == ["Back"]
    assert solved.points == pytest.approx(10.0)


def test_partial_rosters_fill_what_they_can():
    roster = [player("Back", "RB", 100)]
    solved = best_lineup(roster, SFB_LINEUP)
    assert len(solved.starters) == 1
    assert solved.points == pytest.approx(100.0)


def test_empty_roster_scores_zero():
    solved = best_lineup([], SFB_LINEUP)
    assert solved.starters == []
    assert solved.points == 0.0


def test_lineup_never_exceeds_slot_count():
    roster = [player(f"WR{i}", "WR", 100 - i) for i in range(30)]
    solved = best_lineup(roster, SFB_LINEUP)
    assert len(solved.starters) == 10


def test_best_lineup_takes_the_best_available():
    roster = [player(f"WR{i}", "WR", 100 - i) for i in range(30)]
    solved = best_lineup(roster, SFB_LINEUP)
    assert solved.points == pytest.approx(sum(100 - i for i in range(10)))


def test_negative_value_players_are_left_on_the_bench():
    roster = [player("Good", "RB", 50), player("Bad", "RB", -20)]
    solved = best_lineup(roster, SFB_LINEUP)
    assert [p.name for p in solved.starters] == ["Good"]


def test_custom_value_function_is_respected():
    """Scoring by an alternate metric changes who starts."""
    roster = [player("A", "RB", 10), player("B", "RB", 20)]
    lineup = (LineupSlot(1, ("RB",)),)
    by_points = best_lineup(roster, lineup)
    by_inverse = best_lineup(roster, lineup, value=lambda p: 100 - p.points)
    assert [p.name for p in by_points.starters] == ["B"]
    assert [p.name for p in by_inverse.starters] == ["A"]


def test_assignments_cover_every_starter():
    roster = [player(f"P{i}", "WR", 100 - i) for i in range(15)]
    solved = best_lineup(roster, SFB_LINEUP)
    assert len(solved.assignments) == len(solved.starters)
    assert {p.player_id for p, _ in solved.assignments} == solved.starter_ids


def test_scale_lineup_multiplies_every_group():
    scaled = scale_lineup(SFB_LINEUP, 12)
    assert [s.count for s in scaled] == [24, 96]
    assert [s.positions for s in scaled] == [s.positions for s in SFB_LINEUP]


def test_lineup_points_matches_best_lineup():
    roster = [player(f"P{i}", "WR", 100 - i) for i in range(15)]
    assert lineup_points(roster, SFB_LINEUP) == pytest.approx(
        best_lineup(roster, SFB_LINEUP).points
    )
