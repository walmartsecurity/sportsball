"""End-to-end checks that each command runs and prints something sane."""

import pytest

from sportsball.cli import main


def run(capsys, *argv):
    code = main(list(argv))
    return code, capsys.readouterr()


def test_leagues_lists_the_bundled_configs(capsys):
    code, out = run(capsys, "leagues")
    assert code == 0
    assert "sfb16" in out.out
    assert "Scott Fish Bowl 16" in out.out


def test_values_prints_a_board(capsys):
    code, out = run(capsys, "values", "-n", "5")
    assert code == 0
    assert "VALUE" in out.out
    assert "Replacement level" in out.out


def test_values_can_filter_by_position(capsys):
    code, out = run(capsys, "values", "-P", "TE", "-n", "5")
    assert code == 0
    assert "== TE ==" in out.out
    assert "Brock Bowers" in out.out


def test_values_can_group_into_tiers(capsys):
    code, out = run(capsys, "values", "-P", "QB", "-n", "10", "--tiers")
    assert code == 0
    assert "Tier 1" in out.out


def test_roster_prints_a_full_legal_roster(capsys, sfb16):
    code, out = run(capsys, "roster")
    assert code == 0
    assert "bench" in out.out
    assert out.out.count("\n") > sfb16.roster_size


def test_player_explains_a_projection(capsys):
    code, out = run(capsys, "player", "Brock", "Bowers")
    assert code == 0
    assert "AUCTION VALUE" in out.out
    assert "base scoring" in out.out
    assert "rec_20_plays" in out.out


def test_player_reports_an_unknown_name(capsys):
    code, out = run(capsys, "player", "Nobody", "Here")
    assert code == 1
    assert "no player matching" in out.err


def test_a_bad_league_name_is_reported(capsys):
    code, out = run(capsys, "--league", "nope", "values")
    assert code == 1
    assert "no league config" in out.err


def test_a_bad_projections_path_is_reported(capsys):
    code, out = run(capsys, "-p", "/nope/players.csv", "values")
    assert code == 1
    assert "no projections file" in out.err


def test_standard_league_runs_end_to_end(capsys):
    code, out = run(capsys, "--league", "standard12", "values", "-n", "5")
    assert code == 0
    assert "Standard 12-team PPR" in out.out


def test_team_and_budget_overrides_are_applied(capsys):
    code, out = run(capsys, "--teams", "10", "--budget", "300", "values", "-n", "3")
    assert code == 0
    assert "10 teams, $300 each" in out.out


def test_no_command_prints_help(capsys):
    code, out = run(capsys)
    assert code == 1
    assert "usage" in out.out.lower()


def test_draft_session_runs(capsys, monkeypatch, tmp_path):
    """Drive the REPL through a short scripted auction."""
    script = iter([
        "best TE 3",
        "me bowers 55",
        "sold ja'marr 70 alice",
        "budget",
        "roster",
        "undo",
        f"save {tmp_path / 'd.json'}",
        "quit",
    ])
    monkeypatch.setattr("builtins.input", lambda _: next(script))
    code, out = run(capsys, "draft")
    assert code == 0
    assert "Brock Bowers" in out.out
    assert "inflation" in out.out
    assert (tmp_path / "d.json").exists()


def test_draft_reports_bad_input_without_crashing(capsys, monkeypatch):
    script = iter(["nonsense", "me", "me nobody 5", "max nobody", "quit"])
    monkeypatch.setattr("builtins.input", lambda _: next(script))
    code, out = run(capsys, "draft")
    assert code == 0
    assert out.out.count("error:") >= 3
