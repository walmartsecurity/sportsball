"""Reading a MyFantasyLeague auction.

MFL is the only source here that scores in the league's own rules, so its
projections arrive as this format's points and need no re-scoring, and it knows
what every player went for and what every franchise has left.

Stdlib only, so the live server can use it without the optional extras. The
response shapes vary by endpoint and have changed over the years -- notably, a
collection with exactly one member comes back as a bare object rather than a
list -- so every reader here tolerates a missing layer rather than assuming a
shape.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping

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


def gather(host: str = "www43", year: int = 2026, league: str | None = None,
           apikey: str | None = None, from_dir: str | Path | None = None,
           kinds: tuple[str, ...] = ENDPOINTS,
           on_note=None) -> dict[str, Any]:
    """Every endpoint we need, from the network or from saved files.

    A league with no auction yet, or one that publishes no projections, is a
    normal state rather than an error, so those two are allowed to be missing.
    """
    note = on_note or (lambda message: print(f"  note: {message}", file=sys.stderr))
    out: dict[str, Any] = {}
    if from_dir:
        base = Path(from_dir)
        for kind in kinds:
            path = base / f"{kind}.json"
            if path.exists():
                out[kind] = json.loads(path.read_text())
        if not out:
            raise MFLError(f"no MFL json files found in {base}")
        return out

    if not league:
        raise MFLError("a league id is required to fetch from MFL")
    for kind in kinds:
        week = "YTD" if kind == "projectedScores" else None
        try:
            out[kind] = fetch(build_url(host, year, kind, league, apikey, week))
        except MFLError as exc:
            if kind in ("auctionResults", "projectedScores"):
                note(f"{kind} unavailable ({exc})")
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


# Fields a row might use to say an auction has not finished yet. MFL's slow
# auction keeps a player open for hours while bids come in, and the shape of
# this varies; --inspect prints what your league actually sends.
_END_KEYS = ("bidEnd", "endTime", "auctionEnd", "expires", "closeTime")
_REMAINING_KEYS = ("timeRemaining", "secondsRemaining")
_CLOSED_KEYS = ("closed", "isClosed", "complete")


def _auction_is_open(row: Mapping, now: float | None = None) -> bool:
    """Is this player still under bidding rather than sold?

    Getting this wrong in a slow auction is costly in both directions: calling
    an open auction sold takes a gettable player off the board and records a
    price that is not final, while calling a closed one open leaves a player
    you cannot have in the plan. When a row says nothing either way this
    returns False, because the endpoint is named for results and treating an
    unknown as sold matches what MFL has always meant by it.
    """
    now = time.time() if now is None else now

    for key in _CLOSED_KEYS:
        if key in row:
            value = str(row[key]).strip().lower()
            if value in ("1", "true", "yes", "y"):
                return False
            if value in ("0", "false", "no", "n"):
                return True

    status = str(row.get("status") or "").strip().lower()
    if status:
        if status in ("open", "active", "pending", "bidding", "inprogress"):
            return True
        if status in ("closed", "complete", "completed", "sold", "final"):
            return False

    for key in _REMAINING_KEYS:
        if row.get(key) not in (None, ""):
            try:
                return float(row[key]) > 0
            except ValueError:
                pass

    for key in _END_KEYS:
        if row.get(key) not in (None, ""):
            try:
                # MFL writes epoch seconds; tolerate milliseconds too.
                end = float(row[key])
            except ValueError:
                continue
            if end > 1e11:
                end /= 1000.0
            return end > now

    return False


def read_auction(payload: Any) -> list[dict]:
    """Every auction row, marked open or closed.

    MFL nests these under one or more auction units. A row that is still open
    carries the *current* high bid, not a sale price -- see ``_auction_is_open``.
    """
    units = listify(dig(payload, "auctionResults", "auctionUnit"))
    rows = []
    for unit in units:
        for row in listify(dig(unit, "auction") if isinstance(unit, Mapping) else None):
            if not isinstance(row, Mapping):
                continue
            bid = row.get("winningBid") or row.get("currentBid") or row.get("bid")
            if row.get("player") is None or bid in (None, ""):
                continue
            try:
                price = float(str(bid).replace("$", "").replace(",", ""))
            except ValueError:
                continue
            rows.append({"player": str(row["player"]),
                         "franchise": str(row.get("franchise") or ""),
                         "price": price,
                         "open": _auction_is_open(row)})
    return rows


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




def player_id_for(meta: dict) -> str:
    from .players import make_player_id

    return make_player_id(meta["name"], meta["position"], meta["team"])


def build_seed_state(players: dict[str, dict], sales: list[dict],
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

    out_sales, open_bids, missing = [], {}, 0
    spent: dict[str, float] = {}
    for sale in sales:
        meta = players.get(sale["player"])
        if meta is None:
            missing += 1
            continue
        if sale.get("open"):
            # Still gettable. The current high bid is the best estimate of what
            # it will take, so it rides through as a price rather than a sale.
            open_bids[player_id_for(meta)] = round(sale["price"])
            continue
        team = team_of(sale["franchise"])
        out_sales.append({"id": player_id_for(meta),
                          "price": round(sale["price"]),
                          "team": team})
        spent[team] = spent.get(team, 0.0) + sale["price"]
    if missing:
        notes.append(f"{missing} auctions were for players outside QB/RB/WR/TE")
    if open_bids:
        notes.append(f"{len(open_bids)} players still under bidding")

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
    return ({"sales": out_sales, "openBids": open_bids, "teamEdits": teams,
             "myTeam": "you"}, notes)


