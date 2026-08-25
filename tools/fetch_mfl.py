"""Pull a MyFantasyLeague auction into this tool.

MFL is a better fit than any other source here for one reason: it scores in
*your* league's rules. Its projections are already SFB16 points -- tight end
premium, first downs, video game bonuses and all -- so nothing has to be
re-scored or approximated. It also knows what every player has gone for and
what every franchise has left, which is the state the draft app wants.

    python tools/fetch_mfl.py --league 36570 --host www43 --year 2026 \\
        --me "Jeffrey Smar" --out-dir mfl/

That writes two files:

* ``projections.csv`` -- the board, with MFL's league-scored projections in a
  ``fantasy_points`` column, so this tool's scoring engine defers to them.
* ``seed.json`` -- every completed sale and every franchise's remaining
  salary, ready for ``build_app.py --seed``.

Then::

    python tools/build_app.py --projections mfl/projections.csv \\
        --seed mfl/seed.json --out app.html

**If the API is unreachable from where you are**, fetch the endpoints yourself
and point the tool at the saved files -- it does the same work either way::

    base='https://www43.myfantasyleague.com/2026/export'
    for t in league players auctionResults rosters; do
      curl -sS "$base?TYPE=$t&L=36570&JSON=1" > mfl/$t.json
    done
    curl -sS "$base?TYPE=projectedScores&L=36570&W=YTD&JSON=1" > mfl/projectedScores.json
    python tools/fetch_mfl.py --from-dir mfl/ --me "Jeffrey Smar" --out-dir mfl/

**Private leagues** need credentials: pass ``--apikey`` and it is appended to
every request. Without it MFL returns a login page rather than data, which
``--inspect`` will show you plainly.

The response shapes MFL returns vary by endpoint and have changed over the
years, so every field lookup here is written to tolerate a missing layer, and
``--inspect`` prints what actually came back before anything is written.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sportsball.mfl import (  # noqa: E402
    ENDPOINTS, MFLError, POSITIONS, build_seed_state as build_seed, build_url,
    dig, fetch, flip_name, gather, listify, player_id_for, read_auction,
    read_franchises, read_players, read_projections, read_roster_salaries,
    read_salary_cap,
)

OUT_COLS = ["name", "position", "team", "fantasy_points", "games"]


# --------------------------------------------------------------------------
# outputs
# --------------------------------------------------------------------------

def write_projections(players: dict[str, dict], scores: dict[str, float],
                      out: Path, limit: int, games: float) -> int:
    rows = []
    for pid, meta in players.items():
        score = scores.get(pid)
        if score is None:
            continue
        rows.append({**meta, "fantasy_points": round(score, 1), "games": games})
    rows.sort(key=lambda r: -r["fantasy_points"])
    rows = rows[:limit]
    with out.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUT_COLS)
        writer.writeheader()
        writer.writerows({c: r.get(c, "") for c in OUT_COLS} for r in rows)
    return len(rows)


def inspect(payloads: dict[str, Any]) -> None:
    print(f"endpoints received: {', '.join(sorted(payloads))}\n")
    players = read_players(payloads.get("players"))
    franchises = read_franchises(payloads.get("league"))
    sales = read_auction(payloads.get("auctionResults"))
    scores = read_projections(payloads.get("projectedScores"))
    cap = read_salary_cap(payloads.get("league"))

    print(f"  players (QB/RB/WR/TE): {len(players)}")
    if players:
        pid, meta = next(iter(players.items()))
        print(f"    example: id {pid} -> {meta}")
    print(f"  franchises: {len(franchises)}")
    for fid, name in list(franchises.items())[:4]:
        print(f"    {fid}: {name}")
    print(f"  salary cap: {cap if cap is not None else 'NOT FOUND'}")
    print(f"  completed auction sales: {len(sales)}")
    if sales:
        s = sales[0]
        who = players.get(s["player"], {}).get("name", f"id {s['player']}")
        print(f"    example: {who} for {s['price']:.0f} to "
              f"{franchises.get(s['franchise'], s['franchise'])}")
    print(f"  league-scored projections: {len(scores)}")
    if scores:
        top = sorted(scores.items(), key=lambda kv: -kv[1])[:3]
        for pid, score in top:
            print(f"    {players.get(pid, {}).get('name', pid)}: {score}")
    else:
        print("    none — try a different --week, or the league may not publish them")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--league", help="MFL league id, e.g. 36570")
    ap.add_argument("--host", default="www43",
                    help="the host your league lives on (from its URL)")
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--apikey", default=None, help="needed for a private league")
    ap.add_argument("--from-dir", default=None,
                    help="read saved endpoint json from this directory instead")
    ap.add_argument("--me", default=None, help="your franchise name")
    ap.add_argument("--games", type=float, default=17.0)
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--out-dir", default="mfl")
    ap.add_argument("--inspect", action="store_true",
                    help="print what came back and write nothing")
    args = ap.parse_args()

    if not args.league and not args.from_dir:
        ap.error("need --league (or --from-dir for saved files)")

    try:
        payloads = gather(host=args.host, year=args.year, league=args.league,
                          apikey=args.apikey, from_dir=args.from_dir)
        if args.inspect:
            inspect(payloads)
            return 0

        players = read_players(payloads.get("players"))
        if not players:
            raise MFLError("no players came back; run with --inspect")
        franchises = read_franchises(payloads.get("league"))
        cap = read_salary_cap(payloads.get("league"))
        sales = read_auction(payloads.get("auctionResults"))
        committed = read_roster_salaries(payloads.get("rosters"))
        scores = read_projections(payloads.get("projectedScores"))
    except MFLError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if scores:
        written = write_projections(players, scores, out_dir / "projections.csv",
                                    args.limit, args.games)
        print(f"wrote {written} players to {out_dir / 'projections.csv'} "
              "(MFL's own league-scored projections)")
    else:
        print("no projections available — keep using the bundled board")

    seed, notes = build_seed(players, sales, franchises, cap, committed, args.me)
    (out_dir / "seed.json").write_text(json.dumps(seed, indent=2))
    spent = sum(s["price"] for s in seed["sales"])
    print(f"wrote {len(seed['sales'])} sales and {len(seed['teamEdits'])} "
          f"franchises to {out_dir / 'seed.json'} (${spent:,.0f} spent so far)")
    mine = [s for s in seed["sales"] if s["team"] == "you"]
    if mine:
        print(f"  you: {len(mine)} players, ${sum(s['price'] for s in mine):,.0f} spent")
    for note in notes:
        print(f"  note: {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
