"""Projection ingest: alias tolerance and clear failures."""

import io

import pytest

from sportsball.players import (
    Player,
    ProjectionError,
    load_projections,
    parse_projections,
    sample_path,
)


def csv(text):
    return io.StringIO(text.strip() + "\n")


def test_sample_loads(sfb16):
    players = load_projections()
    assert len(players) > 150
    assert {p.position for p in players} == {"QB", "RB", "WR", "TE"}


def test_sample_path_exists():
    assert sample_path().exists()


def test_common_header_aliases_are_understood():
    """FantasyPros-style headers should work without renaming anything."""
    players = parse_projections(csv(
        "Player,Pos,Team,Rec,Receiving Yds,Receiving TDs\n"
        "Some Guy,WR,BUF,80,1100,9"
    ))
    assert players[0].name == "Some Guy"
    assert players[0].stats.receptions == 80
    assert players[0].stats.rec_yds == 1100
    assert players[0].stats.rec_td == 9


def test_missing_stat_columns_default_to_zero():
    players = parse_projections(csv("name,position\nNo Stats,RB"))
    assert players[0].stats.rush_yds == 0.0
    assert players[0].stats.games == 17.0


def test_commas_and_blanks_in_numbers():
    players = parse_projections(csv(
        "name,position,rush_yds,rec_yds\nBig Back,RB,\"1,450\",-"
    ))
    assert players[0].stats.rush_yds == 1450.0
    assert players[0].stats.rec_yds == 0.0


def test_blank_rows_are_skipped():
    players = parse_projections(csv("name,position\nReal Guy,RB\n,\n"))
    assert len(players) == 1


def test_missing_name_column_is_an_error():
    with pytest.raises(ProjectionError, match="name"):
        parse_projections(csv("pos,team\nRB,BUF"))


def test_missing_position_column_is_an_error():
    with pytest.raises(ProjectionError, match="position"):
        parse_projections(csv("name,team\nSome Guy,BUF"))


def test_unknown_position_is_rejected_with_the_player_named():
    with pytest.raises(ProjectionError, match="Some Guy"):
        parse_projections(csv("name,position\nSome Guy,LB"))


def test_bad_number_reports_the_line():
    with pytest.raises(ProjectionError, match="line 2"):
        parse_projections(csv("name,position,rush_yds\nSome Guy,RB,lots"))


def test_duplicate_players_are_rejected():
    with pytest.raises(ProjectionError, match="duplicate"):
        parse_projections(csv("name,position\nSame Guy,RB\nSame Guy,RB"))


def test_same_name_different_position_is_allowed():
    players = parse_projections(csv("name,position\nSame Guy,RB\nSame Guy,WR"))
    assert len(players) == 2


def test_empty_file_is_an_error():
    with pytest.raises(ProjectionError):
        parse_projections(csv("name,position"))


def test_missing_file_is_an_error():
    with pytest.raises(ProjectionError, match="no projections file"):
        load_projections("/nonexistent/projections.csv")


def test_explicit_first_downs_are_kept_and_absent_ones_are_none():
    players = parse_projections(csv(
        "name,position,rec,rec_first_downs\nA,WR,50,30\nB,WR,50,"
    ))
    assert players[0].stats.rec_first_downs == 30
    assert players[1].stats.rec_first_downs is None


def test_player_ids_are_stable():
    a = Player(name="Ja'Marr Chase", position="WR", team="CIN")
    b = Player(name="Ja'Marr Chase", position="WR", team="CIN")
    assert a.player_id == b.player_id


# -- projections that arrive already scored --------------------------------


def test_a_supplied_points_column_is_picked_up():
    players = parse_projections(csv(
        "name,position,fantasy_points,rec\nPre Scored,TE,412.5,80"
    ))
    assert players[0].supplied_points == 412.5


def test_supplied_points_bypass_the_scoring_engine(sfb16):
    """Trust a source that scored under your rules; it has news this does not."""
    from sportsball.scoring import score_player

    player = parse_projections(csv(
        "name,position,fantasy_points,rec,rec_yds,rec_td\nPre Scored,TE,412.5,80,900,7"
    ))[0]
    score_player(player, sfb16)
    assert player.points == 412.5
    assert player.bonus_points == 0.0
    assert player.bonus_breakdown == {}


def test_a_file_can_mix_scored_and_unscored_players(sfb16):
    from sportsball.scoring import score_player

    players = parse_projections(csv(
        "name,position,fantasy_points,rec,rec_yds,rec_td\n"
        "Pre Scored,TE,412.5,80,900,7\n"
        "Score Me,TE,,80,900,7"
    ))
    for player in players:
        score_player(player, sfb16)
    assert players[0].points == 412.5
    assert players[1].supplied_points is None
    assert players[1].bonus_points > 0
    assert players[1].points != 412.5


@pytest.mark.parametrize("header", ["fpts", "points", "proj_points", "fantasy_pts"])
def test_common_spellings_of_a_points_column(header):
    players = parse_projections(csv(f"name,position,{header}\nSome Guy,WR,301"))
    assert players[0].supplied_points == 301


def test_a_missing_points_column_leaves_players_unscored():
    players = parse_projections(csv("name,position,rec\nSome Guy,WR,80"))
    assert players[0].supplied_points is None
