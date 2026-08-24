import pytest

from sportsball.config import (
    ConfigError,
    LeagueConfig,
    LineupSlot,
    ScoringRules,
    league_from_dict,
    load_league,
)


def test_sfb16_shape(sfb16):
    assert sfb16.teams == 12
    assert sfb16.budget == 200
    assert sfb16.roster_size == 20
    assert sfb16.starters == 10
    assert sfb16.bench == 10
    assert sfb16.total_budget == 2400
    assert sfb16.drafted_players == 240


def test_sfb16_caps_quarterbacks_at_two(sfb16):
    """The 0-2 group is the only one that accepts QB."""
    assert sfb16.max_starters_at("QB") == 2
    for position in ("RB", "WR", "TE"):
        assert sfb16.max_starters_at(position) == 10


def test_sfb16_has_no_positional_minimums(sfb16):
    """Every slot group accepts more than one position, so nothing is forced."""
    assert all(len(slot.positions) > 1 for slot in sfb16.lineup)


def test_tight_end_premium_is_configured(sfb16):
    assert sfb16.scoring.reception_bonus["TE"] == 1.0
    assert sfb16.scoring.first_down_bonus["TE"] == 1.0
    assert sfb16.scoring.pass_td == 6


def test_standard_league_differs(sfb16):
    standard = load_league("standard12")
    assert standard.scoring.pass_td == 4
    assert standard.bonuses.enabled is False
    assert standard.max_starters_at("QB") == 1


def test_unknown_league_lists_alternatives():
    with pytest.raises(ConfigError, match="sfb16"):
        load_league("no-such-league")


def test_lineup_rejects_unknown_position():
    with pytest.raises(ConfigError, match="unknown position"):
        LineupSlot(count=1, positions=("QB", "PUNTER"))


def test_roster_smaller_than_lineup_is_rejected():
    with pytest.raises(ConfigError, match="smaller than"):
        LeagueConfig(roster_size=2, lineup=(LineupSlot(3, ("RB",)),))


def test_budget_must_cover_minimum_bids():
    with pytest.raises(ConfigError, match="cannot fill"):
        LeagueConfig(budget=5, roster_size=10, lineup=(LineupSlot(1, ("RB",)),))


def test_unknown_config_key_is_rejected():
    with pytest.raises(ConfigError, match="unknown league config key"):
        league_from_dict({"lineup": [{"count": 1, "positions": ["RB"]}], "wat": 1})


def test_scoring_overrides_merge_with_defaults():
    league = league_from_dict({
        "lineup": [{"count": 1, "positions": ["RB"]}],
        "scoring": {"rush_td": 4},
    })
    assert league.scoring.rush_td == 4
    assert league.scoring.rush_yds == ScoringRules().rush_yds
