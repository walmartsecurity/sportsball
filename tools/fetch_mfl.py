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
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

POSITIONS = ("QB", "RB", "WR", "TE")
ENDPOINTS = ("league", "players", "auctionResults", "rosters", "projectedScores")

OUT_COLS = ["name", "position", "team", "fantasy_points", "games"]


class MFLError(RuntimeError):
    """Raised when MFL returns something we cannot read."""


# --------------------------------------------------------------------------
# fetching
# --------------------------------------------------------------------------

def build_url(host: str, year: int, kind: str, league: str,
              apikey: str | None, week: str | None) -> str:
    params = {"TYPE": kind, "L": league, "JSON": "1"}
    if apikey:
        params["APIKEY"] = apikey
    if week:
        params["W"] = week
    return (f"https://{host}.myfantasyleague.com/{year}/export?"
            + urllib.parse.urlencode(params))


def fetch(url: str, timeout: float = 60.0) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "sportsball"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise MFLError(f"MFL returned HTTP {exc.code} for {url}") from exc
    except urllib.error.URLError as exc:
        raise MFLError(
            f"could not reach MFL ({exc.reason}). If your network blocks it, "
            "save the endpoints yourself and use --from-dir."
        ) from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        head = body.strip()[:120].replace("\n", " ")
        raise MFLError(
            f"MFL did not return JSON for {url} — got {head!r}. A private "
            "league needs --apikey."
        ) from exc


def gather(args) -> dict[str, Any]:
    """Every endpoint we need, from the network or from saved files."""
    out: dict[str, Any] = {}
    if args.from_dir:
        base = Path(args.from_dir)
        for kind in ENDPOINTS:
            path = base / f"{kind}.json"
            if path.exists():
                out[kind] = json.loads(path.read_text())
        if not out:
            raise MFLError(f"no MFL json files found in {base}")
        return out

    for kind in ENDPOINTS:
        week = "YTD" if kind == "projectedScores" else None
        url = build_url(args.host, args.year, kind, args.league, args.apikey, week)
        try:
            out[kind] = fetch(url)
        except MFLError as exc:
            # A league without an auction, or without projections, is fine.
            if kind in ("auctionResults", "projectedScores"):
                print(f"  note: {kind} unavailable ({exc})", file=sys.stderr)
                continue
            raise
    return out


# --------------------------------------------------------------------------
# shape-tolerant readers
# --------------------------------------------------------------------------

def listify(value: Any) -> list:
    """MFL returns a bare object when a collection has exactly one member."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def dig(payload: Any, *path: str) -> Any:
    """Walk a path of keys, tolerating any missing layer."""
    node = payload
    for key in path:
        if not isinstance(node, Mapping):
            return None
        node = node.get(key)
    return node


def read_players(payload: Any) -> dict[str, dict]:
    """MFL player id -> name, position, team."""
    rows = listify(dig(payload, "players", "player"))
    out = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        pid = row.get("id")
        position = (row.get("position") or "").upper()
        if not pid or position not in POSITIONS:
            continue
        out[str(pid)] = {
            "name": flip_name(row.get("name") or ""),
            "position": position,
            "team": (row.get("team") or "FA").strip() or "FA",
        }
    return out


def flip_name(name: str) -> str:
    """MFL writes "Nacua, Puka"; everything else here wants "Puka Nacua"."""
    if "," not in name:
        return name.strip()
    last, first = name.split(",", 1)
    return f"{first.strip()} {last.strip()}".strip()


def read_franchises(payload: Any) -> dict[str, str]:
    rows = listify(dig(payload, "league", "franchises", "franchise"))
    return {str(r.get("id")): (r.get("name") or str(r.get("id")))
            for r in rows if isinstance(r, Mapping) and r.get("id")}


def read_salary_cap(payload: Any) -> float | None:
    for key in ("salaryCapAmount", "salaryCap"):
        value = dig(payload, "league", key)
        if value:
            try:
                return float(str(value).replace("$", "").replace(",", ""))
            except ValueError:
                continue
    return None


def read_auction(payload: Any) -> list[dict]:
    """Completed sales. MFL nests these under one or more auction units."""
    units = listify(dig(payload, "auctionResults", "auctionUnit"))
    sales = []
    for unit in units:
        for row in listify(dig(unit, "auction") if isinstance(unit, Mapping) else None):
            if not isinstance(row, Mapping):
                continue
            bid = row.get("winningBid") or row.get("bid")
            if row.get("player") is None or bid in (None, ""):
                continue
            try:
                price = float(str(bid).replace("$", "").replace(",", ""))
            except ValueError:
                continue
            sales.append({"player": str(row["player"]),
                          "franchise": str(row.get("franchise") or ""),
                          "price": price})
    return sales


def read_roster_salaries(payload: Any) -> dict[str, float]:
    """Total salary committed per franchise, as a fallback for spend."""
    out: dict[str, float] = {}
    for franchise in listify(dig(payload, "rosters", "franchise")):
        if not isinstance(franchise, Mapping):
            continue
        total = 0.0
        for player in listify(franchise.get("player")):
            if not isinstance(player, Mapping):
                continue
            try:
                total += float(str(player.get("salary") or 0).replace("$", ""))
            except ValueError:
                pass
        out[str(franchise.get("id"))] = total
    return out


def read_projections(payload: Any) -> dict[str, float]:
    rows = listify(dig(payload, "projectedScores", "playerScore"))
    out = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        pid, score = row.get("id"), row.get("score")
        if pid is None or score in (None, ""):
            continue
        try:
            out[str(pid)] = float(score)
        except ValueError:
            continue
    return out


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


def player_id_for(meta: dict) -> str:
    from sportsball.players import make_player_id

    return make_player_id(meta["name"], meta["position"], meta["team"])


def build_seed(players: dict[str, dict], sales: list[dict],
               franchises: dict[str, str], cap: float | None,
               committed: dict[str, float], me: str | None) -> tuple[dict, list[str]]:
    notes: list[str] = []
    mine = None
    if me:
        for fid, name in franchises.items():
            if name.strip().lower() == me.strip().lower():
                mine = fid
                break
        if mine is None:
            notes.append(f"no franchise named {me!r}; you will show as a rival")

    def team_of(fid: str) -> str:
        if mine and fid == mine:
            return "you"
        return franchises.get(fid, f"Franchise {fid}")

    out_sales, missing = [], 0
    spent: dict[str, float] = {}
    for sale in sales:
        meta = players.get(sale["player"])
        if meta is None:
            missing += 1
            continue
        team = team_of(sale["franchise"])
        out_sales.append({"id": player_id_for(meta),
                          "price": round(sale["price"]),
                          "team": team})
        spent[team] = spent.get(team, 0.0) + sale["price"]
    if missing:
        notes.append(f"{missing} sales were for players outside QB/RB/WR/TE")

    teams: dict[str, dict] = {}
    for fid, name in franchises.items():
        team = team_of(fid)
        # Prefer the roster's own salary total; fall back to summing the auction.
        outlay = committed.get(fid)
        if outlay is None:
            outlay = spent.get(team, 0.0)
        entry = {"players": sum(1 for s in out_sales if s["team"] == team)}
        if cap is not None:
            entry["budget"] = round(cap - outlay)
        teams[team] = entry

    if cap is None:
        notes.append("no salary cap found; team budgets left to the league config")
    return {"sales": out_sales, "teamEdits": teams, "myTeam": "you"}, notes


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
        payloads = gather(args)
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
