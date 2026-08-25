"""Turn a live auction board into the app's starting state.

Paste the draft room's results page into a text file and this converts it into
a seed the app opens with, so you are not re-entering forty sales by hand
mid-draft.

    python tools/seed_draft.py --board board.txt --me "Your Name" \\
        --out src/sportsball/data/seed_draft.json
    python tools/build_app.py --seed src/sportsball/data/seed_draft.json

Expected line format, one sale per line::

    Chase, Ja'Marr CIN WR   $162.00 ($175.00)   Jeffrey Smar ($565.00)
    Allen, Josh BUF QB      $100.00             Allan Hepworth ($561.00)

That is ``Last, First TEAM POS``, the price, an optional second price in
parentheses, then the manager and their remaining budget. A trailing ``(R)``
marks a rookie and is ignored.

**On the two prices.** Some boards show a second figure on your own rows only.
Which one was actually charged is settled by arithmetic rather than assumption:
this sums each manager's sales both ways and keeps whichever reconciles with
the remaining budget printed next to their name, reporting the choice. If
neither reconciles it says so instead of guessing.

Every manager's budget is carried across, including the ones who have not
bought anything, because league money is what sets every price on the board.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sportsball.config import load_league  # noqa: E402
from sportsball.players import load_projections  # noqa: E402

# "Last, First TEAM POS (R)  $162.00 ($175.00)  Manager Name ($565.00)"
LINE = re.compile(
    r"^\s*(?P<last>[^,]+),\s*(?P<first>.+?)\s+(?P<team>[A-Z]{2,3})\s+"
    r"(?P<pos>QB|RB|WR|TE|K|DST)\s*(?:\(R\))?\s*"
    r"\$?(?P<price>[\d,]+(?:\.\d+)?)\s*"
    r"(?:\(\$?(?P<alt>[\d,]+(?:\.\d+)?)\)\s*)?"
    r"(?P<manager>.+?)\s*\(\$?(?P<left>[\d,]+(?:\.\d+)?)\)\s*$"
)
# The same board with fields already separated by pipes.
PIPED = re.compile(
    r"^(?P<who>[^|]+)\|(?P<price>[^|]*)\|(?P<alt>[^|]*)\|(?P<manager>[^|]+)\|(?P<left>.+)$"
)
WHO = re.compile(r"^(?P<last>[^,]+),\s*(?P<first>.+?)\s+(?P<team>[A-Z]{2,3})\s+"
                 r"(?P<pos>QB|RB|WR|TE|K|DST)\s*(?:\(R\))?\s*$")


def _num(text: str | None) -> float | None:
    if not text:
        return None
    try:
        return float(str(text).replace(",", "").replace("$", "").strip())
    except ValueError:
        return None


def parse_board(text: str) -> list[dict]:
    rows = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        piped = PIPED.match(line)
        if piped:
            who = WHO.match(piped.group("who").strip())
            if not who:
                raise ValueError(f"line {lineno}: cannot read player {piped.group('who')!r}")
            data = who.groupdict() | {
                "price": piped.group("price"), "alt": piped.group("alt"),
                "manager": piped.group("manager"), "left": piped.group("left"),
            }
        else:
            match = LINE.match(line)
            if not match:
                raise ValueError(f"line {lineno}: cannot read {line!r}")
            data = match.groupdict()

        rows.append({
            "name": f"{data['first'].strip()} {data['last'].strip()}",
            "position": data["pos"],
            "team": data["team"],
            "price": _num(data["price"]),
            "alt": _num(data.get("alt")),
            "manager": data["manager"].strip(),
            "left": _num(data["left"]),
        })
    if not rows:
        raise ValueError("no sales found in the board")
    return rows


def reconcile(rows: list[dict], budget: float) -> tuple[dict[str, str], list[str]]:
    """Decide, per manager, which of the two prices was actually charged."""
    managers: dict[str, list[dict]] = {}
    for row in rows:
        managers.setdefault(row["manager"], []).append(row)

    choice, notes = {}, []
    for manager, sales in managers.items():
        implied = budget - sales[0]["left"]
        first = sum(s["price"] for s in sales)
        alt = sum(s["alt"] if s["alt"] is not None else s["price"] for s in sales)
        if abs(first - implied) < 0.01:
            choice[manager] = "price"
        elif abs(alt - implied) < 0.01:
            choice[manager] = "alt"
            notes.append(f"{manager}: used the parenthesised price "
                         f"(${alt:,.0f} reconciles, ${first:,.0f} does not)")
        else:
            choice[manager] = "price"
            notes.append(f"{manager}: NEITHER price reconciles — sales sum to "
                         f"${first:,.0f} or ${alt:,.0f}, budget implies "
                         f"${implied:,.0f}. Using the first; check this one.")
    return choice, notes


def _key(name: str, position: str) -> str:
    text = name.lower()
    for junk in (".", "'", "`", "-", ","):
        text = text.replace(junk, "")
    parts = [t for t in text.split() if t not in ("jr", "sr", "ii", "iii", "iv", "v")]
    return " ".join(parts) + "|" + position.upper()


def build_seed(rows: list[dict], me: str, league, projections) -> tuple[dict, list[str]]:
    lookup = {}
    for player in projections:
        lookup.setdefault(_key(player.name, player.position), player)

    choice, notes = reconcile(rows, float(league.budget))

    sales, missing = [], []
    for row in rows:
        player = lookup.get(_key(row["name"], row["position"]))
        if player is None:
            missing.append(f"{row['name']} ({row['position']}, {row['team']})")
            continue
        price = row["alt"] if choice[row["manager"]] == "alt" and row["alt"] else row["price"]
        sales.append({
            "id": player.player_id,
            "price": round(price),
            "team": "you" if row["manager"] == me else row["manager"],
        })

    # Every manager's remaining money, whether or not their sales all matched.
    teams: dict[str, dict] = {}
    for row in rows:
        team = "you" if row["manager"] == me else row["manager"]
        counted = sum(1 for s in sales if s["team"] == team)
        teams[team] = {"budget": round(row["left"]), "players": counted}

    return {"sales": sales, "teamEdits": teams, "myTeam": "you"}, missing + notes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--board", required=True, help="text file of the results page")
    ap.add_argument("--me", required=True, help="your manager name as it appears")
    ap.add_argument("--league", "-l", default="sfb16")
    ap.add_argument("--projections", "-p", default=None)
    ap.add_argument("--out", "-o", default="seed_draft.json")
    args = ap.parse_args()

    league = load_league(args.league)
    projections = load_projections(args.projections)
    rows = parse_board(Path(args.board).read_text())
    seed, notes = build_seed(rows, args.me, league, projections)

    Path(args.out).write_text(json.dumps(seed, indent=2))
    spent = sum(s["price"] for s in seed["sales"])
    print(f"parsed {len(rows)} sales, matched {len(seed['sales'])} to the board")
    print(f"{len(seed['teamEdits'])} managers, ${spent:,.0f} spent so far")
    mine = [s for s in seed["sales"] if s["team"] == "you"]
    print(f"you: {len(mine)} players, ${sum(s['price'] for s in mine):,.0f} spent, "
          f"${seed['teamEdits'].get('you', {}).get('budget', 0):,.0f} left")
    for note in notes:
        print(f"  note: {note}")
    print(f"written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
