"""Command line interface.

Four things you actually do with this tool:

    sportsball values        print the auction cheat sheet
    sportsball roster        show the best roster the board allows
    sportsball player NAME   explain where one player's points come from
    sportsball draft         run the live auction assistant
"""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path
from typing import Iterable, Sequence

from .config import ConfigError, LeagueConfig, bundled_leagues, load_league
from .draft import DraftError, DraftState
from .optimize import optimize_roster
from .players import (ProjectionError, default_projections_path,
                      load_projections)
from .scoring import score_all, upside_points
from .valuation import Valuation, ValuationBoard, value_players

# Tier break: a gap of this many dollars between consecutive players at a
# position is treated as the edge of a tier.
_TIER_GAP = 3.0


# --------------------------------------------------------------------------
# formatting helpers
# --------------------------------------------------------------------------

def _money(amount: float) -> str:
    return f"${amount:,.0f}"


def _table(rows: Sequence[Sequence[str]], headers: Sequence[str]) -> str:
    if not rows:
        return "(nothing to show)"
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    def line(cells: Sequence[str]) -> str:
        return "  ".join(str(c).ljust(widths[i]) for i, c in enumerate(cells)).rstrip()
    out = [line(headers), "  ".join("-" * w for w in widths)]
    out.extend(line(r) for r in rows)
    return "\n".join(out)


def _value_rows(
    valuations: Iterable[Valuation], *, show_price: bool = False
) -> list[list[str]]:
    rows: list[list[str]] = []
    for rank, v in enumerate(valuations, start=1):
        row = [
            str(rank),
            v.name,
            v.position,
            v.player.team,
            f"{v.points:.0f}",
            f"{v.vor:.0f}",
            _money(v.value),
        ]
        if show_price:
            row.append(_money(v.price))
        rows.append(row)
    return rows


def _value_headers(show_price: bool = False) -> list[str]:
    headers = ["#", "PLAYER", "POS", "TM", "PTS", "VOR", "VALUE"]
    if show_price:
        headers.append("NOW")
    return headers


# --------------------------------------------------------------------------
# shared setup
# --------------------------------------------------------------------------

def _build(args: argparse.Namespace) -> tuple[LeagueConfig, list, ValuationBoard]:
    league = load_league(args.league)
    overrides = {}
    if args.teams:
        overrides["teams"] = args.teams
    if args.budget:
        overrides["budget"] = args.budget
    if args.upside is not None:
        overrides["upside_weight"] = args.upside
    if overrides:
        league = league.with_overrides(**overrides)

    players = score_all(load_projections(args.projections), league)
    board = value_players(players, league)
    return league, players, board


def _header(league: LeagueConfig, board: ValuationBoard) -> str:
    lineup = ", ".join(f"{s.count}x {s.label()}" for s in league.lineup)
    return (
        f"{league.name}: {league.teams} teams, {_money(league.budget)} each, "
        f"{league.roster_size}-man rosters\n"
        f"Lineup: {lineup} ({league.starters} starters, {league.bench} bench)\n"
        f"Pricing: {_money(league.total_budget)} chasing "
        f"{league.drafted_players} roster spots "
        f"({board.dollars_per_point:.3f} $/pt over replacement)"
    )


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_leagues(args: argparse.Namespace) -> int:
    print("Bundled league configs:")
    for name in bundled_leagues():
        league = load_league(name)
        print(f"  {name:12s} {league.name}")
    print(f"\nBundled projections: {default_projections_path()}")
    return 0


def cmd_values(args: argparse.Namespace) -> int:
    league, players, board = _build(args)
    print(_header(league, board))
    print()

    if args.position:
        positions = [p.upper() for p in args.position]
    else:
        positions = [None]

    for position in positions:
        pool = [v for v in board if position is None or v.position == position]
        pool = pool[: args.limit]
        if position:
            print(f"== {position} ==")
        if args.tiers:
            _print_tiers(pool)
        else:
            print(_table(_value_rows(pool), _value_headers()))
        print()

    spent = sum(max(v.value, 0.0) for v in board.top(league.drafted_players))
    print(
        f"Top {league.drafted_players} players account for {_money(spent)} "
        f"of the league's {_money(league.total_budget)}."
    )
    print(
        "Replacement level: "
        + ", ".join(
            f"{pos} {pts:.0f}pts"
            for pos, pts in sorted(board.levels.by_position.items())
        )
    )
    return 0


def _print_tiers(pool: Sequence[Valuation]) -> None:
    tier = 1
    bucket: list[Valuation] = []
    for i, v in enumerate(pool):
        bucket.append(v)
        last = i == len(pool) - 1
        gap = 0.0 if last else v.value - pool[i + 1].value
        if last or gap >= _TIER_GAP:
            span = f"{_money(bucket[-1].value)}-{_money(bucket[0].value)}"
            print(f"-- Tier {tier} ({span}) --")
            print(_table(_value_rows(bucket), _value_headers()))
            print()
            tier += 1
            bucket = []


def cmd_roster(args: argparse.Namespace) -> int:
    league, players, board = _build(args)
    print(_header(league, board))
    print()
    plan = optimize_roster(board, league)
    print(
        f"Best roster at list prices (solver: {plan.solver}) -- "
        f"spend {_money(plan.spend)} of {_money(plan.budget)}, "
        f"{plan.starter_points:.0f} starting points"
    )
    print()
    starting = plan.lineup.starter_ids
    rows = []
    for player, slot in plan.lineup.assignments:
        rows.append([
            slot.label(), player.name, player.position, player.team,
            f"{player.points:.0f}", _money(plan.prices.get(player.player_id, 0)),
        ])
    for player in plan.bench:
        rows.append([
            "bench", player.name, player.position, player.team,
            f"{player.points:.0f}", _money(plan.prices.get(player.player_id, 0)),
        ])
    print(_table(rows, ["SLOT", "PLAYER", "POS", "TM", "PTS", "COST"]))
    return 0


def cmd_player(args: argparse.Namespace) -> int:
    league, players, board = _build(args)
    state = DraftState(league=league, players=players)
    try:
        player = state.find(" ".join(args.name))
    except DraftError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    v = board.get(player.player_id)
    print(f"{player.name}  ({player.position} - {player.team})")
    print("-" * 46)
    s = player.stats
    print(f"  {s.games:.0f} games")
    if s.pass_att:
        print(
            f"  passing   {s.pass_att:.0f} att, {s.pass_yds:.0f} yds, "
            f"{s.pass_td:.0f} TD ({s.yards_per_attempt:.1f} Y/A)"
        )
    if s.rush_att:
        print(
            f"  rushing   {s.rush_att:.0f} att, {s.rush_yds:.0f} yds, "
            f"{s.rush_td:.0f} TD ({s.yards_per_carry:.1f} Y/C)"
        )
    if s.receptions:
        print(
            f"  receiving {s.receptions:.0f} rec, {s.rec_yds:.0f} yds, "
            f"{s.rec_td:.0f} TD ({s.yards_per_reception:.1f} Y/R)"
        )
    print()
    print(f"  base scoring     {player.base_points:8.1f}")
    for label, points in sorted(
        player.bonus_breakdown.items(), key=lambda kv: -kv[1]
    ):
        if points > 0.05:
            print(f"    {label:22s} {points:8.1f}")
    print(f"  bonus total      {player.bonus_points:8.1f}"
          f"   ({player.bonus_points / player.points:.0%} of projection)"
          if player.points else "")
    print(f"  PROJECTION       {player.points:8.1f}"
          f"   ({player.points_per_game:.1f}/gm)")
    print(f"  upside case      {upside_points(player, league):8.1f}")
    print()
    if v:
        print(f"  replacement      {v.replacement:8.1f}")
        print(f"  value over repl  {v.vor:8.1f}")
        print(f"  AUCTION VALUE    {_money(v.value):>8s}")
    return 0


# --------------------------------------------------------------------------
# live draft
# --------------------------------------------------------------------------

_DRAFT_HELP = """
Commands (player names accept any unambiguous prefix):

  me <player> <price>        you won the bid
  sold <player> <price> <tm> someone else won the bid
  undo                       take back the last sale
  targets [n]                what is worth bidding on, and why
  max <player>               your true walk-away price
  best [pos] [n]             best remaining values at current prices
  plan                       best roster you can still finish
  roster [team]              show a roster (default: yours)
  budget                     money and inflation across the league
  save <file> / load <file>  persist the draft
  help                       this list
  quit                       exit
"""


def cmd_targets(args: argparse.Namespace) -> int:
    league, players, board = _build(args)
    state = DraftState(league=league, players=players)
    print(_header(league, board))
    print()
    _do_targets(state, [str(args.limit)])
    return 0


def cmd_draft(args: argparse.Namespace) -> int:
    league, players, board = _build(args)
    state = DraftState(league=league, players=players, my_team=args.me)
    if args.load:
        state.load_sales(args.load)
        print(f"loaded {len(state.sales)} sales from {args.load}")

    print(_header(league, board))
    print(_DRAFT_HELP)

    while True:
        try:
            raw = input(f"[{_money(state.my_budget)} / "
                        f"{state.my_open_slots} slots] > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not raw:
            continue
        try:
            parts = shlex.split(raw)
        except ValueError as exc:
            print(f"error: {exc}")
            continue
        command, rest = parts[0].lower(), parts[1:]

        try:
            if command in ("quit", "exit", "q"):
                break
            elif command in ("help", "?"):
                print(_DRAFT_HELP)
            elif command == "me":
                _do_sale(state, rest, state.my_team)
            elif command == "sold":
                _do_sale(state, rest, None)
            elif command == "undo":
                sale = state.undo()
                print(f"undid: {state.find(sale.player_id).name} "
                      f"{_money(sale.price)} to {sale.team}")
            elif command in ("targets", "t"):
                _do_targets(state, rest)
            elif command == "max":
                _do_max(state, rest)
            elif command == "best":
                _do_best(state, rest)
            elif command == "plan":
                _do_plan(state)
            elif command == "roster":
                _do_roster(state, rest)
            elif command == "budget":
                _do_budget(state)
            elif command == "save":
                path = state.save(rest[0] if rest else "draft.json")
                print(f"saved to {path}")
            elif command == "load":
                state.load_sales(rest[0] if rest else "draft.json")
                print(f"loaded {len(state.sales)} sales")
            else:
                print(f"unknown command {command!r}; try 'help'")
        except (DraftError, IndexError, ValueError) as exc:
            print(f"error: {exc}")

    return 0


def _split_price(rest: Sequence[str]) -> tuple[str, float, str | None]:
    """Parse '<name words> <price> [team]'."""
    if len(rest) < 2:
        raise DraftError("need a player and a price")
    tokens = list(rest)
    team = None
    # A trailing token that is not a number, after a number, is the team.
    if len(tokens) >= 3 and not _is_number(tokens[-1]) and _is_number(tokens[-2]):
        team = tokens.pop()
    price_token = tokens.pop()
    if not _is_number(price_token):
        raise DraftError(f"{price_token!r} is not a price")
    price = float(price_token.lstrip("$"))
    if not tokens:
        raise DraftError("need a player name")
    return " ".join(tokens), price, team


def _is_number(token: str) -> bool:
    try:
        float(token.lstrip("$"))
        return True
    except ValueError:
        return False


def _do_sale(state: DraftState, rest: Sequence[str], team: str | None) -> None:
    name, price, parsed_team = _split_price(rest)
    player = state.find(name)
    board_before = state.board()
    listed = board_before.get(player.player_id)
    final_team = team or parsed_team or "other"
    state.record_sale(player, price, final_team)

    note = ""
    if listed:
        delta = price - listed.price
        note = (f"  ({_money(abs(delta))} {'over' if delta > 0 else 'under'} "
                f"the {_money(listed.price)} price)")
    print(f"sold: {player.name} {player.position} -> {final_team} "
          f"for {_money(price)}{note}")
    if final_team == state.my_team:
        lineup = state.my_lineup()
        print(f"  your roster: {len(state.my_roster)}/{state.league.roster_size}, "
              f"{_money(state.my_budget)} left, "
              f"max bid {_money(state.max_affordable_bid())}, "
              f"lineup {lineup.points:.0f} pts")
    inflation = state.inflation()
    if abs(inflation - 1.0) > 0.02:
        direction = "up" if inflation > 1 else "down"
        print(f"  market is {direction}: remaining players now cost "
              f"{inflation:.0%} of list")


def _do_targets(state: DraftState, rest: Sequence[str]) -> None:
    limit = int(rest[0]) if rest and rest[0].isdigit() else 6
    board = state.board()
    rows = state.suggestions(board, limit=limit)

    room = state.room_pricing()
    if room:
        print("room is paying " + ", ".join(
            f"{pos} {ratio:.0%} ({n} sold)" for pos, (ratio, n) in sorted(room.items())
        ) + " of model value")

    if rows:
        table = [[
            ("* " if r.urgent else "") + r.name,
            r.position,
            _money(r.price),
            _money(r.max_bid),
            "; ".join(r.reasons),
        ] for r in rows]
        print(_table(table, ["PLAYER", "POS", "NOW", "YOUR MAX", "WHY"]))
    print(state.pacing(any_edge=any(r.edge > 0 for r in rows)))


def _do_max(state: DraftState, rest: Sequence[str]) -> None:
    if not rest:
        raise DraftError("which player?")
    player = state.find(" ".join(rest))
    if player.player_id in state.sold_ids:
        raise DraftError(f"{player.name} is already off the board")
    board = state.board()
    listed = board.get(player.player_id)
    print(f"{player.name} ({player.position}) -- {player.points:.0f} projected pts")
    if listed:
        print(f"  list value   {_money(listed.value)}")
        print(f"  market now   {_money(listed.price)}")
    print(f"  YOUR MAX BID {_money(state.max_bid_for(player, board))}")
    print(f"  (hard cap {_money(state.max_affordable_bid())} "
          f"with {state.my_open_slots} slots to fill)")


def _do_best(state: DraftState, rest: Sequence[str]) -> None:
    position = None
    limit = 15
    for token in rest:
        if token.isdigit():
            limit = int(token)
        else:
            position = token.upper()
    board = state.board()
    pool = board.top(limit, position=position)
    print(_table(_value_rows(pool, show_price=True), _value_headers(True)))


def _do_plan(state: DraftState) -> None:
    board = state.board()
    plan = state.plan(board)
    print(f"Best finish from here ({plan.solver}): "
          f"spend {_money(plan.spend)} of {_money(plan.budget)}, "
          f"{plan.starter_points:.0f} starting points")
    owned = {p.player_id for p in state.my_roster}
    rows = []
    for player, slot in plan.lineup.assignments:
        rows.append([
            slot.label(),
            player.name,
            player.position,
            "HAVE" if player.player_id in owned else _money(
                plan.prices.get(player.player_id, 0)
            ),
            f"{player.points:.0f}",
        ])
    for player in plan.bench:
        rows.append([
            "bench",
            player.name,
            player.position,
            "HAVE" if player.player_id in owned else _money(
                plan.prices.get(player.player_id, 0)
            ),
            f"{player.points:.0f}",
        ])
    print(_table(rows, ["SLOT", "PLAYER", "POS", "COST", "PTS"]))


def _do_roster(state: DraftState, rest: Sequence[str]) -> None:
    team = rest[0] if rest else state.my_team
    roster = state.team_roster(team)
    if not roster:
        print(f"{team} has not won a player yet")
        return
    spent = state.team_spent(team)
    from .lineup import best_lineup

    lineup = best_lineup(roster, state.league.lineup)
    starting = lineup.starter_ids
    rows = []
    for player, slot in lineup.assignments:
        rows.append([slot.label(), player.name, player.position, f"{player.points:.0f}"])
    for player in roster:
        if player.player_id not in starting:
            rows.append(["bench", player.name, player.position, f"{player.points:.0f}"])
    print(f"{team}: {len(roster)} players, {_money(spent)} spent, "
          f"lineup {lineup.points:.0f} pts")
    print(_table(rows, ["SLOT", "PLAYER", "POS", "PTS"]))


def _do_budget(state: DraftState) -> None:
    league = state.league
    print(f"league: {_money(state.league_money_left)} left for "
          f"{state.league_slots_left} spots "
          f"({_money(state.league_money_left / max(state.league_slots_left, 1))}/spot)")
    print(f"inflation: {state.inflation():.0%} of pre-draft prices")
    rows = []
    for team in [state.my_team] + [t for t in state.teams_seen() if t != state.my_team]:
        roster = state.team_roster(team)
        spent = state.team_spent(team)
        left = league.budget - spent
        open_slots = league.roster_size - len(roster)
        rows.append([
            team + (" (you)" if team == state.my_team else ""),
            str(len(roster)),
            _money(spent),
            _money(left),
            _money(left - max(open_slots - 1, 0) * league.min_bid),
        ])
    print(_table(rows, ["TEAM", "N", "SPENT", "LEFT", "MAXBID"]))


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sportsball",
        description="Fantasy football auction optimizer.",
    )
    parser.add_argument(
        "--league", "-l", default="sfb16",
        help="bundled league name or path to a YAML config (default: sfb16)",
    )
    parser.add_argument(
        "--projections", "-p", default=None,
        help="projections CSV (default: the bundled nflverse-based set)",
    )
    parser.add_argument("--teams", type=int, default=None, help="override team count")
    parser.add_argument("--budget", type=int, default=None, help="override budget")
    parser.add_argument(
        "--upside", type=float, default=None, metavar="W",
        help="weight ceiling over median when pricing, 0.0-1.0",
    )

    subs = parser.add_subparsers(dest="command")

    p_values = subs.add_parser("values", help="print the auction cheat sheet")
    p_values.add_argument("--position", "-P", action="append",
                          help="limit to a position (repeatable)")
    p_values.add_argument("--limit", "-n", type=int, default=40)
    p_values.add_argument("--tiers", action="store_true", help="group into tiers")
    p_values.set_defaults(func=cmd_values)

    p_roster = subs.add_parser("roster", help="best roster at list prices")
    p_roster.set_defaults(func=cmd_roster)

    p_player = subs.add_parser("player", help="explain one player's projection")
    p_player.add_argument("name", nargs="+")
    p_player.set_defaults(func=cmd_player)

    p_draft = subs.add_parser("draft", help="live auction assistant")
    p_draft.add_argument("--me", default="me", help="your team name")
    p_draft.add_argument("--load", default=None, help="resume a saved draft")
    p_draft.set_defaults(func=cmd_draft)

    p_targets = subs.add_parser("targets", help="what is worth bidding on")
    p_targets.add_argument("--limit", "-n", type=int, default=6)
    p_targets.set_defaults(func=cmd_targets)

    p_leagues = subs.add_parser("leagues", help="list bundled league configs")
    p_leagues.set_defaults(func=cmd_leagues)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    try:
        return args.func(args)
    except (ConfigError, ProjectionError, DraftError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
