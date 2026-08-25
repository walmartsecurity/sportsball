"""Turn Sleeper's projections into a projections CSV this tool can score.

Sleeper projects *stat lines*, not SFB16 points, which is what we want: their
fantasy totals are computed under their own scoring, and SFB16 is nothing like
it. This pulls the projected stats and writes them in the tool's CSV schema, so
the scoring engine applies SFB16 rules -- six-point passing touchdowns, the
tight end premium, first downs, and the video game bonuses -- to Sleeper's
numbers.

    python tools/fetch_sleeper.py --season 2026 --out sleeper.csv
    python tools/build_app.py --projections sleeper.csv --out app.html

If the API is unreachable from where you are, fetch it yourself and parse the
saved payload -- this does the same work either way:

    curl -sS 'https://api.sleeper.com/projections/nfl/2026?season_type=regular\\
&position[]=QB&position[]=RB&position[]=WR&position[]=TE&order_by=pts_ppr' \\
      > sleeper.json
    python tools/fetch_sleeper.py --from-json sleeper.json --out sleeper.csv

**What is taken and what is not.** Volume, yardage, touchdowns, interceptions,
fumbles, two-point conversions and -- when Sleeper projects them -- rushing and
receiving first downs, which SFB16 scores and almost nobody publishes.

Sleeper's own `bonus_*` projections are deliberately ignored. They are computed
for Sleeper's thresholds, which do not match SFB16's: Sleeper splits rushing
and receiving hundred-yard bonuses, where SFB16 pays on the combined scrimmage
total, and Sleeper has no notion of a 20-yard reception bonus at all. Applying
them would silently mis-score the format. The video game bonuses stay with this
tool's own fitted models instead -- see `tools/fit_bonus_rates.py`.

Sleeper's API is undocumented, so the field mapping is written to be tolerant
and `--inspect` prints exactly what came back:

    python tools/fetch_sleeper.py --season 2026 --inspect
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Mapping

PROJECTIONS_URL = (
    "https://api.sleeper.com/projections/nfl/{season}"
    "?season_type=regular&grouping=season"
    "&position[]=QB&position[]=RB&position[]=WR&position[]=TE"
    "&order_by=pts_ppr"
)
# Sleeper's own scored totals, by format. Only useful if the league they were
# computed for scores the same way yours does.
POINTS_KEYS = {"ppr": "pts_ppr", "half_ppr": "pts_half_ppr", "std": "pts_std"}
POSITIONS = ("QB", "RB", "WR", "TE")

# Sleeper stat key -> our column. Several aliases per field because the API is
# undocumented and has changed key names before.
STAT_MAP: dict[str, tuple[str, ...]] = {
    "games": ("gp", "gms_active", "games"),
    "pass_att": ("pass_att",),
    "pass_cmp": ("pass_cmp",),
    "pass_yds": ("pass_yd", "pass_yds"),
    "pass_td": ("pass_td",),
    "int": ("pass_int", "int"),
    "rush_att": ("rush_att",),
    "rush_yds": ("rush_yd", "rush_yds"),
    "rush_td": ("rush_td",),
    "targets": ("rec_tgt", "targets", "tgt"),
    "rec": ("rec",),
    "rec_yds": ("rec_yd", "rec_yds"),
    "rec_td": ("rec_td",),
    "rush_first_downs": ("rush_fd",),
    "rec_first_downs": ("rec_fd",),
    "fumbles_lost": ("fum_lost",),
}
TWO_POINT_KEYS = ("pass_2pt", "rush_2pt", "rec_2pt")

OUT_COLS = ["name", "position", "team", "fantasy_points", "games",
            "pass_att", "pass_cmp",
            "pass_yds", "pass_td", "int", "rush_att", "rush_yds", "rush_td",
            "targets", "rec", "rec_yds", "rec_td", "rush_first_downs",
            "rec_first_downs", "fumbles_lost", "two_point"]


class SleeperError(RuntimeError):
    """Raised when the payload is not shaped like anything we recognise."""


def fetch(url: str, timeout: float = 60.0) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "sportsball"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raise SleeperError(f"Sleeper returned HTTP {exc.code} for {url}") from exc
    except urllib.error.URLError as exc:
        raise SleeperError(
            f"could not reach Sleeper ({exc.reason}). If your network blocks it, "
            "fetch the URL yourself and pass --from-json."
        ) from exc


def rows_of(payload: Any) -> list[Mapping[str, Any]]:
    """Sleeper has returned both a list and an id-keyed object over the years."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, Mapping)]
    if isinstance(payload, Mapping):
        values = [v for v in payload.values() if isinstance(v, Mapping)]
        if values:
            return values
    raise SleeperError(
        "unrecognised payload: expected a list of projections or an object "
        f"keyed by player id, got {type(payload).__name__}"
    )


def _first(stats: Mapping[str, Any], keys: Iterable[str]) -> float | None:
    for key in keys:
        if key in stats and stats[key] is not None:
            try:
                return float(stats[key])
            except (TypeError, ValueError):
                continue
    return None


def _identity(row: Mapping[str, Any]) -> tuple[str, str, str] | None:
    """(name, position, team) from whichever shape the row uses."""
    player = row.get("player") if isinstance(row.get("player"), Mapping) else {}
    name = (player.get("full_name")
            or " ".join(x for x in (player.get("first_name"),
                                    player.get("last_name")) if x).strip()
            or row.get("full_name") or row.get("player_name") or "")
    position = (player.get("position") or row.get("position")
                or (player.get("fantasy_positions") or [None])[0] or "")
    team = (row.get("team") or player.get("team") or "FA")
    if not name or position not in POSITIONS:
        return None
    return name.strip(), position, (team or "FA").strip()


def convert(payload: Any, season_games: float | None = None,
            points_key: str | None = None) -> list[dict]:
    """Sleeper projections -> rows in this tool's CSV schema.

    ``points_key`` carries one of Sleeper's already-scored totals straight
    through, which bypasses this tool's scoring engine for those players.
    """
    out: list[dict] = []
    skipped = 0
    for row in rows_of(payload):
        identity = _identity(row)
        stats = row.get("stats")
        if identity is None or not isinstance(stats, Mapping):
            skipped += 1
            continue
        name, position, team = identity

        record = {"name": name, "position": position, "team": team}
        for column, keys in STAT_MAP.items():
            value = _first(stats, keys)
            record[column] = value
        record["two_point"] = sum(
            _first(stats, (k,)) or 0.0 for k in TWO_POINT_KEYS
        )
        record["fantasy_points"] = (
            _first(stats, (points_key,)) if points_key else None
        )

        games = record.get("games")
        if not games or games <= 0:
            record["games"] = season_games if season_games else 17.0
        if not record.get("pass_cmp") and record.get("pass_att"):
            record["pass_cmp"] = record["pass_att"] * 0.655

        # First downs stay empty rather than zero when Sleeper does not project
        # them, so the scoring engine estimates instead of scoring them as none.
        for column in ("rush_first_downs", "rec_first_downs"):
            if record.get(column) is None:
                record[column] = ""

        # A blank points column means "score this one yourself".
        if record.get("fantasy_points") is None:
            record["fantasy_points"] = ""
        for column in OUT_COLS:
            if record.get(column) is None:
                record[column] = 0.0
        out.append(record)

    if not out:
        raise SleeperError(
            f"no usable projections found ({skipped} rows skipped). "
            "Run with --inspect to see what came back."
        )
    return out


def inspect(payload: Any) -> None:
    rows = rows_of(payload)
    print(f"rows: {len(rows)}")
    if not rows:
        return
    sample = rows[0]
    print(f"top-level keys: {sorted(sample)}")
    stats = sample.get("stats")
    if isinstance(stats, Mapping):
        print(f"stat keys ({len(stats)}): {sorted(stats)}")
        mapped = {c: next((k for k in ks if k in stats), None)
                  for c, ks in STAT_MAP.items()}
        print("\nmapping into this tool's schema:")
        for column, key in mapped.items():
            print(f"  {column:18s} <- {key or 'MISSING (will be 0 or estimated)'}")
    identity = _identity(sample)
    print(f"\nfirst player resolves to: {identity}")


def write_csv(rows: list[dict], out: Path, limit: int) -> int:
    rows = sorted(
        rows,
        key=lambda r: (float(r.get("pass_yds") or 0) * 0.04
                       + float(r.get("rush_yds") or 0) * 0.1
                       + float(r.get("rec_yds") or 0) * 0.1
                       + float(r.get("rec") or 0) * 0.5),
        reverse=True,
    )[:limit]
    with out.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUT_COLS)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, 0.0) for c in OUT_COLS})
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--url", default=None, help="override the projections URL")
    ap.add_argument("--from-json", default=None,
                    help="parse a saved payload instead of fetching")
    ap.add_argument("--inspect", action="store_true",
                    help="print the payload's shape and field mapping, write nothing")
    ap.add_argument("--games", type=float, default=None,
                    help="games to assume when Sleeper does not project them")
    ap.add_argument("--points", choices=sorted(POINTS_KEYS), default=None,
                    help="carry one of Sleeper's scored totals through instead "
                         "of scoring their stats under your league's rules. "
                         "Only correct if that format matches your league")
    ap.add_argument("--limit", type=int, default=320)
    ap.add_argument("--out", "-o", default="sleeper.csv")
    args = ap.parse_args()

    try:
        if args.from_json:
            payload = json.loads(Path(args.from_json).read_text())
        else:
            payload = fetch(args.url or PROJECTIONS_URL.format(season=args.season))
        if args.inspect:
            inspect(payload)
            return 0
        rows = convert(payload, args.games, POINTS_KEYS.get(args.points or ""))
    except SleeperError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    written = write_csv(rows, Path(args.out), args.limit)
    has_fd = sum(1 for r in rows if r.get("rec_first_downs") not in ("", 0.0))
    print(f"wrote {written} players to {args.out}")
    print(f"  first downs projected by Sleeper for {has_fd} players"
          f"{'; the rest are estimated' if has_fd < written else ''}")
    if args.points:
        print(f"  WARNING: using Sleeper's {args.points} totals, so this "
              "league's scoring is NOT applied — no tight end premium, no "
              "first downs, no video game bonuses. Correct only if your league "
              "scores exactly that way.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
