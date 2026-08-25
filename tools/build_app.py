"""Build the standalone draft-room web app.

The heavy, static math -- scoring, the video game bonus models, league-wide
replacement level -- is done here in Python and baked into the page as JSON.
The browser only needs the parts that change as players come off the board:
repricing against the money still in the room, tracking your roster, and
solving for a max bid.

    python tools/build_app.py --out app.html

Point --projections at a real export to build the app around your own numbers.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sportsball.config import load_league
from sportsball.players import load_projections
from sportsball.scoring import score_all, upside_points
from sportsball.valuation import value_players

TEMPLATE = Path(__file__).parent / "app_template.html"
# The board opens on the real draft in progress, not an empty room. A bare
# build shipping an unseeded page looks identical to a seeded one until you
# notice every "now" price equals list value, so make it the default.
DEFAULT_SEED = (Path(__file__).resolve().parents[1]
                / "src" / "sportsball" / "data" / "seed_draft.json")


def build_payload(league_name: str, projections: str | None, pool: int,
                  seed: dict | None = None) -> dict:
    league = load_league(league_name)
    players = score_all(load_projections(projections), league)
    board = value_players(players, league)

    rows = []
    for v in board.top(pool):
        p = v.player
        rows.append({
            "id": p.player_id,
            "name": p.name,
            "pos": p.position,
            "team": p.team,
            "pts": round(v.points, 1),
            "vor": round(v.vor, 1),
            "val": round(v.value, 1),
            "bonus": round(p.bonus_points, 1),
            "ceil": round(upside_points(p, league), 1),
        })

    # Recompute the rate from the rounded figures the browser will actually
    # see, so an untouched board reports exactly 100% of list rather than
    # drifting by a rounding error.
    spots = league.drafted_players
    positive_vor = sum(max(r["vor"], 0.0) for r in rows[:spots])
    surplus = max(league.total_budget - spots * league.min_bid, 0.0)
    dpp = surplus / positive_vor if positive_vor > 0 else 0.0

    payload_seed = seed or {}
    if payload_seed:
        known = {r["id"] for r in rows}
        dropped = [s for s in payload_seed.get("sales", []) if s["id"] not in known]
        if dropped:
            raise SystemExit(
                f"the seed references {len(dropped)} players outside the "
                f"{pool}-player board (e.g. {dropped[0]['id']}); raise --pool"
            )

    return {
        "seed": payload_seed,
        "league": {
            "name": league.name,
            "teams": league.teams,
            "budget": league.budget,
            "rosterSize": league.roster_size,
            "minBid": league.min_bid,
            "benchWeight": league.bench_weight,
            "starters": league.starters,
            "lineup": [
                {"name": s.label(), "count": s.count, "positions": list(s.positions)}
                for s in league.lineup
            ],
        },
        "replacement": {k: round(v, 1) for k, v in board.levels.by_position.items()},
        "dollarsPerPoint": dpp,
        "players": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--league", "-l", default="sfb16")
    ap.add_argument("--projections", "-p", default=None)
    ap.add_argument("--pool", type=int, default=280,
                    help="how many players to bake in (default 280)")
    ap.add_argument("--seed", default=str(DEFAULT_SEED),
                    help="JSON of sales and team budgets to open with "
                         "(see tools/seed_draft.py); defaults to the bundled "
                         "board, pass --no-seed for an empty one")
    ap.add_argument("--no-seed", dest="seed", action="store_const", const=None,
                    help="open on an empty board instead of the bundled one")
    ap.add_argument("--out", "-o", default="app.html")
    args = ap.parse_args()

    seed = json.loads(Path(args.seed).read_text()) if args.seed else None
    if args.seed and not seed.get("sales"):
        raise SystemExit(f"{args.seed} has no sales in it")
    payload = build_payload(args.league, args.projections, args.pool, seed)
    html = TEMPLATE.read_text()
    if "__PAYLOAD__" not in html:
        raise SystemExit("template is missing the __PAYLOAD__ placeholder")
    html = html.replace("__PAYLOAD__", json.dumps(payload, separators=(",", ":")))

    out = Path(args.out)
    out.write_text(html)
    size = out.stat().st_size / 1024
    seeded = len(payload.get("seed", {}).get("sales", []))
    print(f"wrote {out} ({size:.0f} KB, {len(payload['players'])} players, "
          f"{payload['league']['name']}, ${payload['league']['budget']} budget"
          + (f", opening with {seeded} sales already recorded)" if seeded else ")"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
