import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sportsball.config import load_league
from sportsball.players import load_projections
from sportsball.scoring import score_all
from sportsball.valuation import value_players


@pytest.fixture(scope="session")
def sfb16():
    return load_league("sfb16")


@pytest.fixture(scope="session")
def scored(sfb16):
    return score_all(load_projections(), sfb16)


@pytest.fixture(scope="session")
def board(sfb16, scored):
    return value_players(scored, sfb16)
