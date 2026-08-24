"""League configuration: roster shape, scoring rules, and bonus modelling.

Everything that varies between leagues lives here so the optimizer itself
stays format-agnostic. Configs are plain YAML; see ``data/leagues/``.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml

POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")

_LEAGUE_DIR = Path(__file__).parent / "data" / "leagues"


class ConfigError(ValueError):
    """Raised when a league config is malformed."""


@dataclass(frozen=True)
class LineupSlot:
    """A group of interchangeable starting slots.

    ``count`` slots, each of which may be filled by any player whose position
    is in ``positions``. SFB16's "0-2: QB, RB, WR, TE" is one group of 2 and
    "0-8: RB, WR, TE" is a second group of 8.
    """

    count: int
    positions: tuple[str, ...]
    name: str = ""

    def __post_init__(self) -> None:
        if self.count < 0:
            raise ConfigError(f"lineup slot count must be >= 0, got {self.count}")
        if not self.positions:
            raise ConfigError("lineup slot must allow at least one position")
        unknown = set(self.positions) - set(POSITIONS)
        if unknown:
            raise ConfigError(f"unknown position(s) in lineup slot: {sorted(unknown)}")

    def accepts(self, position: str) -> bool:
        return position in self.positions

    def label(self) -> str:
        return self.name or "/".join(self.positions)


@dataclass(frozen=True)
class ScoringRules:
    """Per-stat point values.

    Defaults match the SFB16 scoring graphic. Note that SFB16 publishes only
    positive scoring -- interceptions and lost fumbles are listed at 0 here
    rather than guessed at; override them if your league penalises turnovers.
    """

    pass_yds: float = 0.04           # 1 point per 25 yards
    pass_td: float = 6.0             # "6 PT for all touchdowns"
    interception: float = 0.0
    rush_yds: float = 0.1            # 1 point per 10 yards
    rush_td: float = 6.0
    rec_yds: float = 0.1
    rec_td: float = 6.0
    reception: float = 0.5
    fumble_lost: float = 0.0
    two_point: float = 2.0           # "2 PT for all 2 point conversions"
    rush_first_down: float = 0.5
    rec_first_down: float = 0.5
    pass_first_down: float = 0.0     # SFB16 scores rushing/receiving first downs only

    # Position premiums, added on top of the base rates above.
    # SFB16: tight ends get +1 per reception and +1 per first down.
    reception_bonus: Mapping[str, float] = field(
        default_factory=lambda: {"TE": 1.0}
    )
    first_down_bonus: Mapping[str, float] = field(
        default_factory=lambda: {"TE": 1.0}
    )


@dataclass(frozen=True)
class BonusRules:
    """SFB16 "video game bonuses" and the model used to project them.

    Each bonus is worth ``points`` (10 in SFB16). Two families:

    * **Game bonuses** trigger when a single-game total clears a threshold, so
      their expected value depends on the whole distribution of single-game
      outcomes -- a boom/bust player earns more than a metronome with the same
      season total. Game yardage is modelled as a gamma distribution whose
      spread shrinks as volume rises.
    * **Play bonuses** trigger on every individual play clearing a threshold,
      so expected value is linear in volume, with rates that scale with
      projected efficiency.

    Defaults are fitted against nflverse play-by-play, 2021-2025 regular
    season; regenerate with ``tools/fit_bonus_rates.py``. Every constant stays
    in config because refitting on newer data is expected.
    """

    points: float = 10.0
    enabled: bool = True

    # Single-game yardage thresholds. These stack: a 400-yard passing game
    # clears both the 300 and 400 thresholds for +20.
    pass_game_thresholds: tuple[float, ...] = (300.0, 400.0)
    scrimmage_game_thresholds: tuple[float, ...] = (100.0, 200.0)

    # Shape of the single-game distribution. Gamma fits the observed tail
    # markedly better than lognormal, which over-predicted 200-yard games by
    # 88-292% depending on position.
    distribution: str = "gamma"

    # Game-to-game spread. A player's coefficient of variation is not constant:
    # a 97-yards-per-game back is far steadier than a 33-yards-per-game back
    # (0.43 against 0.83). It is modelled as
    #     cv = scrimmage_cv * (yards_per_game / scrimmage_cv_ref) ** slope
    scrimmage_cv: Mapping[str, float] = field(
        default_factory=lambda: {"QB": 0.648, "RB": 0.555, "WR": 0.627, "TE": 0.641}
    )
    scrimmage_cv_ref: Mapping[str, float] = field(
        default_factory=lambda: {"QB": 34.9, "RB": 62.3, "WR": 47.0, "TE": 37.0}
    )
    scrimmage_cv_slope: Mapping[str, float] = field(
        default_factory=lambda: {"QB": -0.391, "RB": -0.588, "WR": -0.398, "TE": -0.364}
    )
    pass_yds_cv: float = 0.288
    pass_yds_cv_ref: float = 231.9
    pass_yds_cv_slope: float = -0.802

    # Guard rails on the extrapolated coefficient of variation.
    cv_floor: float = 0.15
    cv_ceiling: float = 1.50

    # Big-play rates, per opportunity at league-average efficiency. The
    # elasticities are fitted against *prior* season efficiency: yards per
    # carry is partly caused by the 40-yard runs being predicted, so a
    # same-season fit is circular and inflates them badly (4.66 against 1.72
    # for rushing). Prior-season fits match how the tool is used, since it is
    # fed a projection.
    pass_40_rate: float = 0.01326      # per pass attempt
    pass_40_ypa_base: float = 6.592
    pass_40_elasticity: float = 0.579

    rush_40_rate: float = 0.00473      # per carry
    rush_40_ypc_base: float = 4.323
    rush_40_elasticity: float = 1.724

    rec_20_rate: float = 0.14017       # per reception
    rec_20_ypr_base: float = 11.153
    rec_20_elasticity: float = 1.354

    # Quarterback yards per carry comes from scrambles rather than breakaway
    # speed, so the shared rate model over-predicts their 40-yard runs by
    # roughly a factor of two.
    rush_40_position_multiplier: Mapping[str, float] = field(
        default_factory=lambda: {"QB": 0.49}
    )

    # SFB16 scores these per play: three 40-yard runs in a game is three
    # bonuses. Leagues that instead pay the bonus once per game regardless of
    # how many big plays a player has should set this true, which caps expected
    # counts at the probability of at least one such play per game.
    play_bonus_once_per_game: bool = False


@dataclass(frozen=True)
class FirstDownModel:
    """First downs are rarely present in public projections, so estimate them.

    Rates are first downs per opportunity, fitted against nflverse play-by-play
    2021-2025. Note that receivers convert first downs at a *higher* rate per
    catch than tight ends (0.60 against 0.53) because they catch the ball
    further downfield -- the opposite of what the position premium might
    suggest, and it meaningfully narrows the tight end advantage in SFB16.

    Touchdowns are already counted as first downs in the source data, so
    ``touchdowns_count_as_first_down`` defaults to false; turning it on with
    fitted rates would double-count scores.
    """

    per_reception: Mapping[str, float] = field(
        default_factory=lambda: {"QB": 0.50, "RB": 0.336, "WR": 0.602, "TE": 0.529}
    )
    per_rush: Mapping[str, float] = field(
        default_factory=lambda: {"QB": 0.365, "RB": 0.225, "WR": 0.266, "TE": 0.266}
    )
    touchdowns_count_as_first_down: bool = False


@dataclass(frozen=True)
class LeagueConfig:
    """Full description of a league."""

    name: str = "Custom league"
    teams: int = 12
    budget: int = 200
    roster_size: int = 20
    min_bid: int = 1
    lineup: tuple[LineupSlot, ...] = ()
    scoring: ScoringRules = field(default_factory=ScoringRules)
    bonuses: BonusRules = field(default_factory=BonusRules)
    first_downs: FirstDownModel = field(default_factory=FirstDownModel)
    games: int = 17

    # How much a bench spot is worth relative to a starting spot when the
    # optimizer picks a roster. Bench players score nothing directly; they are
    # bye-week and injury insurance plus in-season upside.
    bench_weight: float = 0.35

    # Blend between median projection and upside case when pricing players.
    # 0.0 prices the median, 1.0 prices the ceiling. SFB is a tournament with
    # a single overall winner, so leaning on upside is defensible.
    upside_weight: float = 0.0
    upside_sigma: float = 0.85  # ~84th percentile when upside_weight = 1

    def __post_init__(self) -> None:
        if self.teams < 2:
            raise ConfigError("a league needs at least 2 teams")
        if self.roster_size < self.starters:
            raise ConfigError(
                f"roster_size ({self.roster_size}) is smaller than the "
                f"starting lineup ({self.starters})"
            )
        if self.budget < self.roster_size * self.min_bid:
            raise ConfigError(
                f"budget ({self.budget}) cannot fill {self.roster_size} spots "
                f"at a {self.min_bid} minimum bid"
            )

    @property
    def starters(self) -> int:
        return sum(slot.count for slot in self.lineup)

    @property
    def bench(self) -> int:
        return self.roster_size - self.starters

    @property
    def total_budget(self) -> int:
        return self.teams * self.budget

    @property
    def drafted_players(self) -> int:
        return self.teams * self.roster_size

    @property
    def league_starters(self) -> int:
        return self.teams * self.starters

    def positions_in_play(self) -> tuple[str, ...]:
        seen: list[str] = []
        for slot in self.lineup:
            for pos in slot.positions:
                if pos not in seen:
                    seen.append(pos)
        return tuple(seen)

    def max_starters_at(self, position: str) -> int:
        """Most players of ``position`` that can start on one team."""
        return sum(slot.count for slot in self.lineup if slot.accepts(position))

    def with_overrides(self, **kwargs: Any) -> "LeagueConfig":
        """Return a copy with top-level fields replaced."""
        return replace(self, **kwargs)


def _slot_from_dict(raw: Mapping[str, Any]) -> LineupSlot:
    try:
        count = int(raw["count"])
        positions = tuple(str(p).upper() for p in raw["positions"])
    except (KeyError, TypeError) as exc:
        raise ConfigError(f"lineup slot needs 'count' and 'positions': {raw!r}") from exc
    return LineupSlot(count=count, positions=positions, name=str(raw.get("name", "")))


def _merge_dataclass(cls, defaults, raw: Mapping[str, Any] | None):
    """Build ``cls`` from ``defaults``, overriding with keys present in ``raw``."""
    if not raw:
        return defaults
    known = {f.name for f in defaults.__dataclass_fields__.values()}
    unknown = set(raw) - known
    if unknown:
        raise ConfigError(f"unknown {cls.__name__} key(s): {sorted(unknown)}")
    values: dict[str, Any] = {}
    for key, value in raw.items():
        current = getattr(defaults, key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged = dict(current)
            merged.update({str(k).upper(): float(v) for k, v in value.items()})
            values[key] = merged
        elif isinstance(current, tuple) and isinstance(value, (list, tuple)):
            values[key] = tuple(value)
        else:
            values[key] = value
    return replace(defaults, **values)


def load_league(source: str | Path) -> LeagueConfig:
    """Load a league config by name (``sfb16``) or path to a YAML file."""
    path = Path(source)
    if not path.exists():
        candidate = _LEAGUE_DIR / f"{source}.yaml"
        if candidate.exists():
            path = candidate
        else:
            available = ", ".join(sorted(p.stem for p in _LEAGUE_DIR.glob("*.yaml")))
            raise ConfigError(
                f"no league config at {source!r}; bundled configs: {available}"
            )
    with path.open() as handle:
        raw = yaml.safe_load(handle) or {}
    return league_from_dict(raw)


def league_from_dict(raw: Mapping[str, Any]) -> LeagueConfig:
    raw = copy.deepcopy(dict(raw))
    lineup_raw = raw.pop("lineup", None)
    if not lineup_raw:
        raise ConfigError("league config must define a 'lineup'")
    lineup = tuple(_slot_from_dict(slot) for slot in lineup_raw)

    scoring = _merge_dataclass(ScoringRules, ScoringRules(), raw.pop("scoring", None))
    bonuses = _merge_dataclass(BonusRules, BonusRules(), raw.pop("bonuses", None))
    first_downs = _merge_dataclass(
        FirstDownModel, FirstDownModel(), raw.pop("first_downs", None)
    )

    known = {
        "name", "teams", "budget", "roster_size", "min_bid", "games",
        "bench_weight", "upside_weight", "upside_sigma",
    }
    unknown = set(raw) - known
    if unknown:
        raise ConfigError(f"unknown league config key(s): {sorted(unknown)}")

    return LeagueConfig(
        lineup=lineup,
        scoring=scoring,
        bonuses=bonuses,
        first_downs=first_downs,
        **raw,
    )


def bundled_leagues() -> list[str]:
    return sorted(p.stem for p in _LEAGUE_DIR.glob("*.yaml"))
