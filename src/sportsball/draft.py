"""Live auction state: who is gone, what is left, and what you can still spend.

The core idea is that prices are not fixed. The league's money is a closed
system -- every dollar gets spent and every roster spot gets filled -- so when
the room underpays for the early studs, the surplus does not evaporate. It
gets redistributed across everyone still on the board. Repricing against
*remaining* money and *remaining* value after every sale is what turns a static
cheat sheet into something that stays honest in the third hour.

Replacement levels stay pinned to the full pre-draft pool. They describe the
league's lineup requirements, which do not change as players come off the
board; only the money does.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .config import LeagueConfig
from .lineup import best_lineup
from .optimize import RosterPlan, max_bid, optimize_roster
from .players import Player
from .replacement import ReplacementLevels, compute_replacement
from .scoring import valuation_points
from .valuation import Valuation, ValuationBoard, value_players


class DraftError(ValueError):
    """Raised on an illegal draft action."""


@dataclass
class Sale:
    """One completed nomination."""

    player_id: str
    price: float
    team: str

    def to_dict(self) -> dict:
        return {"player_id": self.player_id, "price": self.price, "team": self.team}


@dataclass
class DraftState:
    """Mutable state of an in-progress auction."""

    league: LeagueConfig
    players: list[Player]
    my_team: str = "me"
    sales: list[Sale] = field(default_factory=list)
    _levels: ReplacementLevels | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self._by_id = {p.player_id: p for p in self.players}
        if self._levels is None:
            self._levels = compute_replacement(
                self.players, self.league, value=self._score
            )

    # -- lookups ------------------------------------------------------------

    def _score(self, player: Player) -> float:
        return valuation_points(player, self.league)

    @property
    def levels(self) -> ReplacementLevels:
        assert self._levels is not None
        return self._levels

    def find(self, query: str) -> Player:
        """Resolve a player by id, exact name, or unambiguous prefix/substring."""
        needle = query.strip().lower()
        if not needle:
            raise DraftError("no player given")
        if query in self._by_id:
            return self._by_id[query]

        exact = [p for p in self.players if p.name.lower() == needle]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            raise DraftError(f"{query!r} is ambiguous: " + _describe(exact))

        prefix = [p for p in self.players if p.name.lower().startswith(needle)]
        if len(prefix) == 1:
            return prefix[0]
        if len(prefix) > 1:
            raise DraftError(f"{query!r} is ambiguous: " + _describe(prefix))

        loose = [p for p in self.players if needle in p.name.lower()]
        if len(loose) == 1:
            return loose[0]
        if len(loose) > 1:
            raise DraftError(f"{query!r} is ambiguous: " + _describe(loose))
        raise DraftError(f"no player matching {query!r}")

    # -- draft actions ------------------------------------------------------

    @property
    def sold_ids(self) -> set[str]:
        return {sale.player_id for sale in self.sales}

    def record_sale(self, player: Player, price: float, team: str) -> Sale:
        if player.player_id in self.sold_ids:
            raise DraftError(f"{player.name} has already been drafted")
        if price < self.league.min_bid:
            raise DraftError(
                f"price {price:g} is below the {self.league.min_bid} minimum bid"
            )
        if team == self.my_team:
            if len(self.my_roster) >= self.league.roster_size:
                raise DraftError("your roster is already full")
            if price > self.max_affordable_bid():
                raise DraftError(
                    f"you cannot afford {price:g}; "
                    f"max legal bid is {self.max_affordable_bid():g}"
                )
        sale = Sale(player_id=player.player_id, price=float(price), team=team)
        self.sales.append(sale)
        return sale

    def undo(self) -> Sale:
        if not self.sales:
            raise DraftError("nothing to undo")
        return self.sales.pop()

    # -- money --------------------------------------------------------------

    @property
    def my_sales(self) -> list[Sale]:
        return [s for s in self.sales if s.team == self.my_team]

    @property
    def my_roster(self) -> list[Player]:
        return [self._by_id[s.player_id] for s in self.my_sales]

    @property
    def my_spent(self) -> float:
        return sum(s.price for s in self.my_sales)

    @property
    def my_budget(self) -> float:
        return self.league.budget - self.my_spent

    @property
    def my_open_slots(self) -> int:
        return self.league.roster_size - len(self.my_roster)

    def max_affordable_bid(self) -> float:
        """Most you can bid and still fill every remaining spot at minimum."""
        if self.my_open_slots <= 0:
            return 0.0
        return self.my_budget - (self.my_open_slots - 1) * self.league.min_bid

    @property
    def league_spent(self) -> float:
        return sum(s.price for s in self.sales)

    @property
    def league_money_left(self) -> float:
        return self.league.total_budget - self.league_spent

    @property
    def league_slots_left(self) -> int:
        return self.league.drafted_players - len(self.sales)

    def team_spent(self, team: str) -> float:
        return sum(s.price for s in self.sales if s.team == team)

    def team_roster(self, team: str) -> list[Player]:
        return [self._by_id[s.player_id] for s in self.sales if s.team == team]

    def teams_seen(self) -> list[str]:
        seen: list[str] = []
        for sale in self.sales:
            if sale.team not in seen:
                seen.append(sale.team)
        return seen

    # -- pricing ------------------------------------------------------------

    def available(self) -> list[Player]:
        gone = self.sold_ids
        return [p for p in self.players if p.player_id not in gone]

    def board(self) -> ValuationBoard:
        """Reprice everyone still available against the money still in the room."""
        return value_players(
            self.available(),
            self.league,
            levels=self.levels,
            budget_pool=self.league_money_left,
            spots_to_fill=self.league_slots_left,
        )

    def baseline_board(self) -> ValuationBoard:
        """Pre-draft prices, for comparing what a player *should* have cost."""
        return value_players(self.players, self.league, levels=self.levels)

    def inflation(self) -> float:
        """Ratio of live prices to pre-draft prices. >1 means money is chasing."""
        pre = self.baseline_board().dollars_per_point
        live = self.board().dollars_per_point
        return live / pre if pre > 0 else 1.0

    # -- advice -------------------------------------------------------------

    def plan(self, board: ValuationBoard | None = None) -> RosterPlan:
        """Best roster you can still finish, at current prices."""
        board = board or self.board()
        return optimize_roster(
            board,
            self.league,
            budget=self.my_budget,
            slots_to_fill=self.my_open_slots,
            owned=self.my_roster,
            value=self._score,
        )

    def max_bid_for(
        self, player: Player, board: ValuationBoard | None = None
    ) -> float:
        """Your true walk-away price for this player, given everything else."""
        board = board or self.board()
        if self.my_open_slots <= 0:
            return 0.0
        return max_bid(
            player,
            board,
            self.league,
            budget=self.my_budget,
            slots_to_fill=self.my_open_slots,
            owned=self.my_roster,
            value=self._score,
        )

    def suggestions(self, board: ValuationBoard | None = None, limit: int = 6):
        """Players worth bidding on right now. See :mod:`sportsball.suggest`."""
        from .suggest import suggestions as _suggest

        return _suggest(self, board or self.board(), limit=limit)

    def room_pricing(self):
        """What the room is paying per position, as a share of model value."""
        from .suggest import room_pricing as _room

        return _room(self)

    def pacing(self, any_edge: bool = False) -> str:
        from .suggest import pacing as _pacing

        return _pacing(self, any_edge)

    def my_lineup(self):
        return best_lineup(self.my_roster, self.league.lineup, value=self._score)

    # -- persistence --------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "league": self.league.name,
            "my_team": self.my_team,
            "sales": [s.to_dict() for s in self.sales],
        }

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.write_text(json.dumps(self.to_dict(), indent=2))
        return path

    def load_sales(self, path: str | Path) -> None:
        data = json.loads(Path(path).read_text())
        self.my_team = data.get("my_team", self.my_team)
        self.sales = [
            Sale(
                player_id=item["player_id"],
                price=float(item["price"]),
                team=item.get("team", "?"),
            )
            for item in data.get("sales", [])
        ]
        unknown = [s.player_id for s in self.sales if s.player_id not in self._by_id]
        if unknown:
            raise DraftError(
                "saved draft references players missing from the current "
                f"projections: {', '.join(unknown[:5])}"
            )


def _describe(players: Sequence[Player], limit: int = 6) -> str:
    shown = ", ".join(f"{p.name} ({p.position}-{p.team})" for p in players[:limit])
    if len(players) > limit:
        shown += f", and {len(players) - limit} more"
    return shown
