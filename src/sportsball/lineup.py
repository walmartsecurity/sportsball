"""Optimal starting lineup selection.

SFB16's lineup is "0-2 from QB/RB/WR/TE, 0-8 from RB/WR/TE" -- ten starters
with no positional minimums at all. Greedy slotting (take the best player, put
them in the first slot that fits) is not optimal under overlapping eligibility:
it can burn a superflex slot on a running back and then have nowhere to put a
quarterback who would have scored more.

This solves the assignment exactly with min-cost max-flow. The graph is tiny --
a roster of twenty players against a handful of slot groups -- so an exact
solve costs nothing, and the same routine is reused to compute league-wide
replacement level and to evaluate candidate rosters inside the optimizer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from .config import LeagueConfig, LineupSlot
from .players import Player

_INF = float("inf")


class _MinCostFlow:
    """Successive-shortest-path min-cost max-flow with SPFA.

    Costs are negative (we are maximising points), so Dijkstra with potentials
    would need an initial Bellman-Ford pass anyway; at these sizes SPFA alone
    is simpler and plenty fast.
    """

    def __init__(self, nodes: int) -> None:
        self.nodes = nodes
        # graph[u] = list of [to, capacity, cost, index of reverse edge]
        self.graph: list[list[list]] = [[] for _ in range(nodes)]

    def add_edge(self, src: int, dst: int, capacity: int, cost: float) -> None:
        self.graph[src].append([dst, capacity, cost, len(self.graph[dst])])
        self.graph[dst].append([src, 0, -cost, len(self.graph[src]) - 1])

    def flow(self, source: int, sink: int) -> float:
        """Push flow while it improves cost. Returns the total cost."""
        total_cost = 0.0
        while True:
            dist = [_INF] * self.nodes
            in_queue = [False] * self.nodes
            prev_node = [-1] * self.nodes
            prev_edge = [-1] * self.nodes
            dist[source] = 0.0
            queue = [source]
            in_queue[source] = True

            while queue:
                node = queue.pop(0)
                in_queue[node] = False
                for index, edge in enumerate(self.graph[node]):
                    to, capacity, cost, _ = edge
                    if capacity > 0 and dist[node] + cost < dist[to] - 1e-12:
                        dist[to] = dist[node] + cost
                        prev_node[to] = node
                        prev_edge[to] = index
                        if not in_queue[to]:
                            in_queue[to] = True
                            queue.append(to)

            # Stop once no path remains, or the cheapest one no longer helps.
            if dist[sink] == _INF or dist[sink] >= -1e-12:
                return total_cost

            # Bottleneck along the path.
            amount = _INF
            node = sink
            while node != source:
                edge = self.graph[prev_node[node]][prev_edge[node]]
                amount = min(amount, edge[1])
                node = prev_node[node]

            node = sink
            while node != source:
                edge = self.graph[prev_node[node]][prev_edge[node]]
                edge[1] -= amount
                self.graph[node][edge[3]][1] += amount
                node = prev_node[node]

            total_cost += dist[sink] * amount


@dataclass
class Lineup:
    """A solved starting lineup."""

    starters: list[Player]
    assignments: list[tuple[Player, LineupSlot]]
    points: float

    @property
    def starter_ids(self) -> set[str]:
        return {p.player_id for p in self.starters}

    def by_slot(self) -> dict[str, list[Player]]:
        grouped: dict[str, list[Player]] = {}
        for player, slot in self.assignments:
            grouped.setdefault(slot.label(), []).append(player)
        return grouped


def best_lineup(
    players: Sequence[Player],
    lineup: Sequence[LineupSlot],
    *,
    value: Callable[[Player], float] | None = None,
) -> Lineup:
    """Highest-scoring legal starting lineup from ``players``.

    ``value`` defaults to the player's projected points, but can be swapped
    for an upside-weighted or otherwise adjusted figure.
    """
    score = value or (lambda p: p.points)
    slots = [slot for slot in lineup if slot.count > 0]
    if not players or not slots:
        return Lineup(starters=[], assignments=[], points=0.0)

    # Node layout: 0 = source, 1..n = players, then one node per slot group,
    # then the sink.
    n = len(players)
    g = len(slots)
    source = 0
    sink = 1 + n + g
    flow = _MinCostFlow(sink + 1)

    for i, player in enumerate(players):
        flow.add_edge(source, 1 + i, 1, 0.0)
        for j, slot in enumerate(slots):
            if slot.accepts(player.position):
                # Negative cost: min-cost flow then maximises points.
                flow.add_edge(1 + i, 1 + n + j, 1, -score(player))
    for j, slot in enumerate(slots):
        flow.add_edge(1 + n + j, sink, slot.count, 0.0)

    total_cost = flow.flow(source, sink)

    assignments: list[tuple[Player, LineupSlot]] = []
    for i, player in enumerate(players):
        for edge in flow.graph[1 + i]:
            to, capacity, _, _ = edge
            # A saturated forward edge into a slot node means "assigned here".
            if 1 + n <= to < 1 + n + g and capacity == 0:
                assignments.append((player, slots[to - 1 - n]))
                break

    assignments.sort(key=lambda pair: score(pair[0]), reverse=True)
    starters = [player for player, _ in assignments]
    return Lineup(starters=starters, assignments=assignments, points=-total_cost)


def lineup_points(
    players: Sequence[Player],
    lineup: Sequence[LineupSlot],
    *,
    value: Callable[[Player], float] | None = None,
) -> float:
    return best_lineup(players, lineup, value=value).points


def scale_lineup(lineup: Sequence[LineupSlot], factor: int) -> tuple[LineupSlot, ...]:
    """Multiply every slot group by ``factor``.

    Used to turn one team's lineup into the league-wide pool of starting
    spots. The aggregate is exact for this class of lineup: any allocation
    respecting the scaled totals can be dealt out to individual teams without
    violating a per-team cap, because the groups are nested by eligibility.
    """
    return tuple(
        LineupSlot(count=slot.count * factor, positions=slot.positions, name=slot.name)
        for slot in lineup
    )
