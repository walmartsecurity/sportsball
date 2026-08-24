"""Player records and projection ingest.

Projections come from a CSV. Column names vary wildly between sources
(FantasyPros, ESPN, Sleeper exports, hand-rolled spreadsheets), so the loader
normalises aliases rather than demanding one exact schema. Any stat column
that is missing is treated as zero.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Sequence

from .config import POSITIONS, ConfigError

_SAMPLE = Path(__file__).parent / "data" / "sample_projections.csv"


class ProjectionError(ValueError):
    """Raised when a projections file cannot be parsed."""


@dataclass
class StatLine:
    """Season stat projection for one player."""

    games: float = 17.0
    pass_att: float = 0.0
    pass_cmp: float = 0.0
    pass_yds: float = 0.0
    pass_td: float = 0.0
    interceptions: float = 0.0
    rush_att: float = 0.0
    rush_yds: float = 0.0
    rush_td: float = 0.0
    targets: float = 0.0
    receptions: float = 0.0
    rec_yds: float = 0.0
    rec_td: float = 0.0
    fumbles_lost: float = 0.0
    two_point: float = 0.0
    # First downs are optional; when absent they are estimated from volume.
    rush_first_downs: float | None = None
    rec_first_downs: float | None = None

    @property
    def scrimmage_yds(self) -> float:
        return self.rush_yds + self.rec_yds

    @property
    def yards_per_attempt(self) -> float:
        return self.pass_yds / self.pass_att if self.pass_att else 0.0

    @property
    def yards_per_carry(self) -> float:
        return self.rush_yds / self.rush_att if self.rush_att else 0.0

    @property
    def yards_per_reception(self) -> float:
        return self.rec_yds / self.receptions if self.receptions else 0.0


@dataclass
class Player:
    """A projectable player.

    ``points`` and the fields below it are filled in by the scoring pipeline;
    they are zero on a freshly loaded player.
    """

    name: str
    position: str
    team: str = "FA"
    stats: StatLine = field(default_factory=StatLine)
    player_id: str = ""
    bye: int = 0

    # Populated by sportsball.scoring
    points: float = 0.0
    base_points: float = 0.0
    bonus_points: float = 0.0
    bonus_breakdown: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.position = self.position.upper().strip()
        if self.position not in POSITIONS:
            raise ProjectionError(
                f"{self.name}: unknown position {self.position!r} "
                f"(expected one of {', '.join(POSITIONS)})"
            )
        if not self.player_id:
            self.player_id = make_player_id(self.name, self.position, self.team)

    @property
    def points_per_game(self) -> float:
        return self.points / self.stats.games if self.stats.games else 0.0

    def __str__(self) -> str:
        return f"{self.name} ({self.position} - {self.team})"


def make_player_id(name: str, position: str, team: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in name).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return f"{slug}-{position.lower()}"


# Canonical field name -> accepted header aliases (lowercased, punctuation stripped).
_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("name", "player", "playername", "fullname"),
    "position": ("position", "pos", "fantasyposition"),
    "team": ("team", "tm", "nflteam", "proteam"),
    "player_id": ("playerid", "id", "sleeperid"),
    "bye": ("bye", "byeweek"),
    "games": ("games", "g", "gp", "gamesplayed"),
    "pass_att": ("passatt", "patt", "passingatt", "att", "passattempts"),
    "pass_cmp": ("passcmp", "cmp", "completions", "passingcmp"),
    "pass_yds": ("passyds", "pyds", "passingyds", "passyards", "passingyards"),
    "pass_td": ("passtd", "ptd", "passingtd", "passtds", "passingtds"),
    "interceptions": ("int", "ints", "interceptions", "passingint"),
    "rush_att": ("rushatt", "carries", "ratt", "rushingatt", "rushattempts"),
    "rush_yds": ("rushyds", "ryds", "rushingyds", "rushyards", "rushingyards"),
    "rush_td": ("rushtd", "rtd", "rushingtd", "rushtds", "rushingtds"),
    "targets": ("targets", "tgt", "tgts"),
    "receptions": ("receptions", "rec", "catches", "receivingrec"),
    "rec_yds": ("recyds", "receivingyds", "recyards", "receivingyards"),
    "rec_td": ("rectd", "receivingtd", "rectds", "receivingtds"),
    "fumbles_lost": ("fumbleslost", "fl", "fum", "fumbles"),
    "two_point": ("twopoint", "2pt", "twopt", "twoptconversions", "2pc"),
    "rush_first_downs": ("rushfirstdowns", "rush1d", "r1d", "rushingfirstdowns"),
    "rec_first_downs": ("recfirstdowns", "rec1d", "receivingfirstdowns"),
}

_STAT_FIELDS = {f.name for f in fields(StatLine)}


def _normalise(header: str) -> str:
    return "".join(ch for ch in header.lower() if ch.isalnum())


def _build_header_map(headers: Sequence[str]) -> dict[str, str]:
    """Map canonical field names to the actual header used in the file."""
    lookup: dict[str, str] = {}
    for canonical, aliases in _ALIASES.items():
        for header in headers:
            if _normalise(header) in aliases:
                lookup[canonical] = header
                break
    return lookup


def _to_float(raw: str | None, default: float = 0.0) -> float:
    if raw is None:
        return default
    text = raw.strip().replace(",", "")
    if not text or text in {"-", "--", "NA", "N/A"}:
        return default
    try:
        return float(text)
    except ValueError as exc:
        raise ProjectionError(f"expected a number, got {raw!r}") from exc


def parse_projections(
    handle: Iterable[str], *, default_games: float = 17.0
) -> list[Player]:
    """Parse projections from any iterable of CSV lines."""
    reader = csv.DictReader(handle)
    if not reader.fieldnames:
        raise ProjectionError("projections file is empty")
    columns = _build_header_map(reader.fieldnames)
    for required in ("name", "position"):
        if required not in columns:
            raise ProjectionError(
                f"projections file needs a {required!r} column; "
                f"found: {', '.join(reader.fieldnames)}"
            )

    players: list[Player] = []
    for lineno, row in enumerate(reader, start=2):
        name = (row.get(columns["name"]) or "").strip()
        if not name:
            continue  # skip blank filler rows
        try:
            stats = StatLine(games=default_games)
            for canonical, header in columns.items():
                if canonical not in _STAT_FIELDS:
                    continue
                raw = row.get(header)
                if canonical in ("rush_first_downs", "rec_first_downs"):
                    text = (raw or "").strip()
                    setattr(stats, canonical, _to_float(raw) if text else None)
                else:
                    default = default_games if canonical == "games" else 0.0
                    setattr(stats, canonical, _to_float(raw, default))

            player = Player(
                name=name,
                position=(row.get(columns["position"]) or "").strip(),
                team=(row.get(columns.get("team", ""), "") or "FA").strip() or "FA",
                player_id=(row.get(columns.get("player_id", ""), "") or "").strip(),
                bye=int(_to_float(row.get(columns.get("bye", "")))),
                stats=stats,
            )
        except ProjectionError as exc:
            raise ProjectionError(f"line {lineno}: {exc}") from exc
        players.append(player)

    if not players:
        raise ProjectionError("projections file contained no players")
    _reject_duplicates(players)
    return players


def _reject_duplicates(players: Sequence[Player]) -> None:
    seen: dict[str, Player] = {}
    for player in players:
        if player.player_id in seen:
            raise ProjectionError(
                f"duplicate player {player.name} ({player.position}); "
                "give each row a distinct player_id to disambiguate"
            )
        seen[player.player_id] = player


def load_projections(
    source: str | Path | None = None, *, default_games: float = 17.0
) -> list[Player]:
    """Load projections from ``source``, or the bundled sample when omitted."""
    path = Path(source) if source is not None else _SAMPLE
    if not path.exists():
        raise ProjectionError(f"no projections file at {path}")
    with path.open(newline="") as handle:
        return parse_projections(handle, default_games=default_games)


def sample_path() -> Path:
    return _SAMPLE
