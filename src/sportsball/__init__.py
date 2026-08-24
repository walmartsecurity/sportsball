"""Fantasy football auction optimizer.

Public surface is intentionally small; see :mod:`sportsball.cli` for the
command line entry point.
"""

from .config import LeagueConfig, LineupSlot, ScoringRules, BonusRules
from .players import (Player, StatLine, default_projections_path,
                      load_projections)
from .scoring import score_player, score_all
from .valuation import Valuation, value_players
from .optimize import optimize_roster, RosterPlan
from .draft import DraftState

__all__ = [
    "LeagueConfig",
    "LineupSlot",
    "ScoringRules",
    "BonusRules",
    "Player",
    "StatLine",
    "load_projections",
    "default_projections_path",
    "score_player",
    "score_all",
    "Valuation",
    "value_players",
    "optimize_roster",
    "RosterPlan",
    "DraftState",
]

__version__ = "0.1.0"
