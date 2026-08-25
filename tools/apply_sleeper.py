"""Put a Sleeper projection export onto the board.

Sleeper's numbers are scored under the league's own settings, so they carry
news this model does not -- injuries, camp reports, depth chart moves. They
also disagree with the model structurally rather than by a constant: under
SFB16's tight end premium Sleeper's top TE outscores its top QB by 300 points,
where the model has the QB ahead. No single factor reconciles that, so this
does not try to find one.

Players named in the export take Sleeper's total outright. Everyone else --
the deep bench Sleeper does not rank -- is mapped through a per-position least
squares fit of Sleeper's totals against the model's, so the whole pool ends up
on one scale instead of two.

    python tools/apply_sleeper.py sleeper.tsv

The export is name/position/team/points, tab or comma separated, with or
without a header. Writes the points into the projections CSV as a
fantasy_points column, leaving the stat lines alone: the model still projects
the bonus split, the ceiling and the big plays off them.
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sportsball.config import load_league
from sportsball.players import load_projections
from sportsball.scoring import score_all

DEFAULT_OUT = (Path(__file__).resolve().parents[1]
               / "src" / "sportsball" / "data" / "projections_2026.csv")
# Sleeper writes a few teams differently than the projections do.
TEAM_ALIAS = {"LAR": "LA", "ARI": "AZ", "JAC": "JAX", "WSH": "WAS", "SD": "LAC"}
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def name_key(name: str, position: str) -> tuple[str, str, str]:
    """First initial, surname, position.

    Sleeper abbreviates the first name, so the initial is all there is to match
    on. Generational suffixes have to come off or half the rookie class misses:
    "B. Thomas" never lines up with "Brian Thomas Jr.".
    """
    parts = re.sub(r"[.'’]", "", name).split()
    words = [w for w in parts[1:] if w.lower().strip(",") not in SUFFIXES]
    return (parts[0][0].upper() if parts else "", " ".join(words).lower(), position)


def read_export(path: Path) -> list[dict]:
    text = path.read_text()
    sample = text.splitlines()[0] if text.splitlines() else ""
    delim = "\t" if "\t" in sample else ","
    rows = []
    for row in csv.reader(io.StringIO(text), delimiter=delim):
        if len(row) < 4:
            continue
        name, position, team, points = (c.strip() for c in row[:4])
        try:
            value = float(points)
        except ValueError:
            continue          # the header, or a blank line
        # "WR/DB" and friends: the first listed position is the real one.
        position = position.split("/")[0].upper()
        rows.append({"name": name, "position": position,
                     "team": TEAM_ALIAS.get(team.upper(), team.upper()),
                     "points": value})
    return rows


def fit(pairs: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Least squares of their points on ours, plus R^2."""
    n = len(pairs)
    mx = sum(x for x, _ in pairs) / n
    my = sum(y for _, y in pairs) / n
    sxx = sum((x - mx) ** 2 for x, _ in pairs)
    sxy = sum((x - mx) * (y - my) for x, y in pairs)
    syy = sum((y - my) ** 2 for _, y in pairs)
    if sxx <= 0 or syy <= 0:
        return my, 0.0, 0.0
    slope = sxy / sxx
    return my - slope * mx, slope, sxy ** 2 / (sxx * syy)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("export", help="Sleeper projections: name, position, team, points")
    ap.add_argument("--league", "-l", default="sfb16")
    ap.add_argument("--projections", "-p", default=None)
    ap.add_argument("--out", "-o", default=str(DEFAULT_OUT))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-add-missing", dest="add_missing", action="store_false",
                    help="drop exported players the projections do not carry, "
                         "instead of adding them on their supplied total alone")
    args = ap.parse_args()

    league = load_league(args.league)
    source = Path(args.projections) if args.projections else DEFAULT_OUT
    players = load_projections(args.projections)
    # Score without any supplied points, so the fit reads the model's own view.
    for p in players:
        p.supplied_points = None
    scored = score_all(players, league)

    index: dict[tuple, list] = {}
    for p in scored:
        index.setdefault(name_key(p.name, p.position), []).append(p)

    rows = read_export(Path(args.export))
    matched: dict[str, float] = {}
    missed: list[dict] = []
    for row in rows:
        candidates = index.get(name_key(row["name"], row["position"]), [])
        if len(candidates) > 1:
            # Two players share an initial and a surname at one position, so
            # the team is the only thing left to separate them.
            same_team = [c for c in candidates if c.team == row["team"]]
            candidates = same_team or []
        if candidates:
            matched[candidates[0].name] = row["points"]
        else:
            missed.append(row)

    fits = {}
    for pos in sorted({p.position for p in scored}):
        pairs = [(p.points, matched[p.name]) for p in scored
                 if p.position == pos and p.name in matched]
        if len(pairs) >= 8:
            fits[pos] = fit(pairs)

    out_rows, carried = [], 0
    with open(source, newline="") as fh:
        reader = csv.DictReader(fh)
        fields = [f for f in (reader.fieldnames or []) if f != "fantasy_points"]
        by_name = {p.name: p for p in scored}
        for row in reader:
            player = by_name.get(row["name"])
            if row["name"] in matched:
                row["fantasy_points"] = f"{matched[row['name']]:.1f}"
            elif player is not None and player.position in fits:
                a, b, _ = fits[player.position]
                row["fantasy_points"] = f"{max(a + b * player.points, 0.0):.1f}"
                carried += 1
            else:
                row["fantasy_points"] = ""
            out_rows.append(row)

    print(f"matched {len(matched)} of {len(rows)} exported players; "
          f"mapped {carried} more through the per-position fit")
    for pos, (a, b, r2) in sorted(fits.items()):
        n = sum(1 for p in scored if p.position == pos and p.name in matched)
        print(f"  {pos}: n={n:3d}  theirs = {a:7.1f} + {b:.3f} x ours   R2={r2:.2f}")
    if missed:
        print(f"  not in the projections ({len(missed)}): "
              + ", ".join(f"{m['name']} {m['position']}" for m in missed[:12])
              + (" ..." if len(missed) > 12 else ""))
    # A player Sleeper ranks and the projections have never heard of is still a
    # player in the auction. Carry him on his total alone rather than leaving a
    # hole on the board: there is no stat line to invent, so the bonus model
    # scores him zero and the ceiling falls back to its flat default, which is
    # the honest answer to knowing nothing about how he gets his points.
    added = 0
    if args.add_missing and missed:
        blank = {f: "" for f in fields}
        for row in missed:
            new_row = dict(blank)
            new_row["name"] = row["name"]
            new_row["position"] = row["position"]
            if "team" in new_row:
                new_row["team"] = row["team"]
            new_row["fantasy_points"] = f"{row['points']:.1f}"
            out_rows.append(new_row)
            added += 1
        print(f"  added {added} of them on their supplied total alone")

    if args.dry_run:
        return 0

    out = Path(args.out)
    with open(out, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields + ["fantasy_points"])
        writer.writeheader()
        writer.writerows(out_rows)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
