"""Build projections from nflverse history.

nflverse publishes what *happened*, not what will happen, so this is not a
download -- it is a projection model fitted to five seasons of play-by-play and
checked by backtest. Run it to replace the bundled sample data with something
real:

    pip install -e '.[fit]'
    python tools/project_from_nflverse.py --season 2026 --out projections.csv
    python tools/build_app.py --projections projections.csv --out app.html

What it does, in order:

1. **Aggregates play-by-play** into per-player-season stat lines, including the
   rushing and receiving first downs SFB16 scores and public projections never
   publish.
2. **Projects volume** -- attempts, carries, targets per game -- as a weighted
   average of the last three seasons, shrunk toward the positional median by
   how many games the player actually has. Volume is the stable part of
   production and it does most of the work.
3. **Regresses efficiency hard.** Yards per opportunity and especially
   touchdown rates are noisy; they are pulled toward positional means with much
   heavier shrinkage than volume.
4. **Applies an age curve.** Production is flat to a positional peak and
   declines after it. Fitted decline beat no decline in backtest by a clear
   margin.
5. **Projects rookies from draft capital**, since they have no history at all.
   Where a player was taken predicts rookie-year scoring rate with a Spearman
   correlation of 0.43-0.58, comfortably beating a positional average.
6. **Blends toward the depth chart** so a player who changed teams or role is
   not projected into the job he used to have.
7. **Filters to the season's actual rosters**, which drops retirements and
   anyone without an NFL job.
Volume shrinks toward what a player's *slot* is worth, not toward one number
for the whole position. This matters more than it sounds. Player populations
are bimodal -- thirty-two men throw the ball and everyone else holds a
clipboard -- so a positional median is a starter's workload, and shrinking a
backup's small sample toward it promotes him. Before slot-aware priors the
model had eighty quarterbacks over 300 attempts and projected 35,000 league
pass attempts against a real 19,500, which inflated the 40-yard-pass-play
bonus by 79%.

Scaling each team's projected volume to a realistic team season was tried as a
fix and removed: it corrects the total while leaving the shares wrong, so it
simply deflated the starters who were already right, dropping the top thirty
receivers to 54% of their real workload. Slot-aware priors fix the shares
instead, and the totals then take care of themselves -- the top thirty-two
quarterbacks project to 101% of their real attempt volume.

Backtested against 2023-2025 (`--backtest`), the model beats both natural
baselines on every season tried:

    model            spearman   MAE   top-200 spearman
    last season         0.716   88.6            0.617
    3yr weighted        0.718   87.4            0.622
    this projection     0.739   80.8            0.647

Read that as a real but modest edge. It knows nothing about training camp,
holdouts, suspensions, scheme changes or who looked good in August, and a
consensus projection that incorporates all of those should beat it. It is here
so the tool can run on real players rather than invented ones.
"""

from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path
from typing import Mapping
from urllib.request import urlretrieve

try:
    import numpy as np
    import pandas as pd
except ImportError:  # pragma: no cover
    sys.exit("this script needs pandas and numpy: pip install -e '.[fit]'")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

BASE = "https://github.com/nflverse/nflverse-data/releases/download"
PBP_COLS = [
    'season', 'game_id', 'season_type', 'complete_pass', 'pass_attempt',
    'rush_attempt', 'passing_yards', 'receiving_yards', 'rushing_yards',
    'first_down_pass', 'first_down_rush', 'passer_player_id',
    'receiver_player_id', 'rusher_player_id', 'pass_touchdown',
    'rush_touchdown', 'interception',
]
POSITIONS = ('QB', 'RB', 'WR', 'TE')

# --- model constants, chosen by backtest over 2023-2025 -------------------
HISTORY_WEIGHTS = (0.7, 0.2, 0.1)   # most recent season first
K_VOLUME = 4.0      # games of positional prior mixed into per-game volume
# Which slice of the position the volume prior represents. The median is the
# wrong answer: player populations are bimodal -- a handful of starters and a
# long tail of backups -- so shrinking a small sample toward the median hands
# every third-stringer a starter's workload. The lower quartile beats the
# median in backtest on both rank correlation and absolute error.
VOLUME_PRIOR_QUANTILE = 0.25
K_EFFICIENCY = 50.0 # opportunities of prior mixed into yards per opportunity
K_TOUCHDOWN = 120.0 # touchdown rates are noisier still
PROJECTED_GAMES = 16.0
AGE_PEAK = {'QB': 28.0, 'RB': 25.0, 'WR': 26.5, 'TE': 27.0}
AGE_DECLINE = -0.030          # per year past the peak
AGE_CLAMP = (0.60, 1.15)
DEPTH_BLEND = 0.25            # how far toward the depth chart's role to move

# Volume drivers, and the dependent stats that must scale with them.
VOLUME_DRIVERS = {
    'pass_att': ['pass_yds', 'pass_td', 'interceptions', 'pass_cmp'],
    'rush_att': ['rush_yds', 'rush_td', 'rush_first_downs'],
    'targets': ['receptions', 'rec_yds', 'rec_td', 'rec_first_downs'],
}

DRAFT_BUCKETS = ((15, '1-15'), (32, '16-32'), (64, '33-64'),
                 (105, '65-105'), (150, '106-150'), (10_000, '151+'))


def draft_bucket(pick: float) -> str:
    for hi, name in DRAFT_BUCKETS:
        if pick <= hi:
            return name
    return '151+'


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------

def fetch(season: int, history: int, cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    wanted = [(f"pbp/play_by_play_{y}.parquet", f"pbp_{y}.parquet")
              for y in range(season - history, season)]
    wanted += [(f"rosters/roster_{y}.parquet", f"roster_{y}.parquet")
               for y in range(season - history, season + 1)]
    wanted += [
        ("players/players.parquet", "players.parquet"),
        ("draft_picks/draft_picks.parquet", "draft_picks.parquet"),
        (f"depth_charts/depth_charts_{season}.parquet", f"depth_{season}.parquet"),
    ]
    for remote, local in wanted:
        path = cache / local
        if path.exists():
            continue
        print(f"  downloading {local}...", file=sys.stderr)
        try:
            urlretrieve(f"{BASE}/{remote}", path)
        except Exception as exc:  # depth charts may not exist yet
            print(f"    skipped {local}: {exc}", file=sys.stderr)
    return cache


def seasonal_stats(cache: Path) -> pd.DataFrame:
    """Per-player-season lines aggregated from play-by-play."""
    files = sorted(glob.glob(str(cache / "pbp_*.parquet")))
    if not files:
        sys.exit("no play-by-play in the cache; check the downloads above")
    pbp = pd.concat([pd.read_parquet(f, columns=PBP_COLS) for f in files])
    pbp = pbp[pbp.season_type == "REG"]

    def group(idcol, mask, **cols):
        return (pbp[mask].groupby(['season', idcol]).agg(**cols)
                .reset_index().rename(columns={idcol: 'pid'}))

    passing = group('passer_player_id',
                    pbp.pass_attempt.eq(1) & pbp.passer_player_id.notna(),
                    pass_att=('pass_attempt', 'sum'),
                    pass_cmp=('complete_pass', 'sum'),
                    pass_yds=('passing_yards', 'sum'),
                    pass_td=('pass_touchdown', 'sum'),
                    interceptions=('interception', 'sum'))
    rushing = group('rusher_player_id',
                    pbp.rush_attempt.eq(1) & pbp.rusher_player_id.notna(),
                    rush_att=('rush_attempt', 'sum'),
                    rush_yds=('rushing_yards', 'sum'),
                    rush_td=('rush_touchdown', 'sum'),
                    rush_first_downs=('first_down_rush', 'sum'))
    receiving = group('receiver_player_id',
                      pbp.pass_attempt.eq(1) & pbp.receiver_player_id.notna(),
                      targets=('pass_attempt', 'sum'),
                      receptions=('complete_pass', 'sum'),
                      rec_yds=('receiving_yards', 'sum'),
                      rec_td=('pass_touchdown', 'sum'),
                      rec_first_downs=('first_down_pass', 'sum'))

    appearances = pd.concat([
        pbp[pbp[c].notna()][['season', 'game_id', c]].rename(columns={c: 'pid'})
        for c in ('passer_player_id', 'rusher_player_id', 'receiver_player_id')
    ]).drop_duplicates()
    out = appearances.groupby(['season', 'pid']).size().reset_index(name='games')
    for frame in (passing, rushing, receiving):
        out = out.merge(frame, on=['season', 'pid'], how='left')
    out = out.fillna(0.0)

    # One canonical position per player, from the most recent roster he
    # appears on. Position must not vary by season: a quarterback who converts
    # to receiver would otherwise be split into two players, and his passing
    # volume projected onto the receiver.
    rosters = pd.concat([
        pd.read_parquet(f, columns=['season', 'gsis_id', 'position'])
        for f in sorted(glob.glob(str(cache / "roster_*.parquet")))
    ]).dropna(subset=['gsis_id'])
    rosters = rosters.sort_values('season').drop_duplicates('gsis_id', keep='last')
    pos = dict(zip(rosters.gsis_id, rosters.position))
    out['position'] = [pos.get(p) for p in out.pid]
    return out[out.position.isin(POSITIONS)]


# --------------------------------------------------------------------------
# the model
# --------------------------------------------------------------------------

STAT_COLS = ['games', 'pass_att', 'pass_cmp', 'pass_yds', 'pass_td',
             'interceptions', 'rush_att', 'rush_yds', 'rush_td',
             'rush_first_downs', 'targets', 'receptions', 'rec_yds', 'rec_td',
             'rec_first_downs']


def historical_role_volume(stats: pd.DataFrame, cache: Path) -> pd.DataFrame:
    """Typical per-game volume for each slot in a team's pecking order.

    A position's players are not interchangeable draws from one distribution:
    a team has one quarterback who throws and three who do not. Ranking players
    within their own team and season by volume recovers what each slot is
    actually worth, which is the prior a projection needs.
    """
    rosters = pd.concat([
        pd.read_parquet(f, columns=['season', 'gsis_id', 'team'])
        for f in sorted(glob.glob(str(cache / "roster_*.parquet")))
    ]).dropna(subset=['gsis_id']).drop_duplicates(['season', 'gsis_id'])
    teamed = stats.merge(rosters, left_on=['pid', 'season'],
                         right_on=['gsis_id', 'season'], how='inner')
    teamed = teamed[teamed.games > 0]
    if teamed.empty:
        return pd.DataFrame()

    frames = []
    for driver in VOLUME_DRIVERS:
        d = teamed[teamed[driver] > 0].copy()
        if d.empty:
            continue
        d['per_game'] = d[driver] / d.games
        d['rank'] = (d.groupby(['season', 'team', 'position'])[driver]
                     .rank(method='first', ascending=False))
        grouped = (d.groupby(['position', 'rank']).per_game.median()
                   .reset_index().rename(columns={'per_game': driver}))
        frames.append(grouped.set_index(['position', 'rank'])[[driver]])
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, axis=1).fillna(0.0)
    return out


def depth_ranks(cache: Path, season: int) -> dict[str, float]:
    """Each player's current slot on his team's depth chart."""
    path = cache / f"depth_{season}.parquet"
    if not path.exists():
        return {}
    depth = pd.read_parquet(path)
    if 'gsis_id' not in depth.columns or 'pos_rank' not in depth.columns:
        return {}
    depth['dt'] = pd.to_datetime(depth.get('dt'), errors='coerce')
    depth = depth[depth.dt == depth.dt.max()]
    depth = depth[depth.pos_abb.isin(POSITIONS)].dropna(subset=['gsis_id'])
    return dict(zip(depth.gsis_id, depth.pos_rank))


def _priors(agg: pd.DataFrame) -> dict:
    priors = {}
    for position, group in agg.groupby('position'):
        q = group[group.wgames >= 3]
        if not len(q):
            q = group
        safe = lambda a, b: a.sum() / max(b.sum(), 1.0)
        quantile = VOLUME_PRIOR_QUANTILE
        priors[position] = {
            'pass_att': float((q.pass_att / q.wgames).quantile(quantile) or 0),
            'rush_att': float((q.rush_att / q.wgames).quantile(quantile) or 0),
            'targets': float((q.targets / q.wgames).quantile(quantile) or 0),
            'ypa': safe(q.pass_yds, q.pass_att),
            'ypc': safe(q.rush_yds, q.rush_att),
            'ypt': safe(q.rec_yds, q.targets),
            'catch_rate': safe(q.receptions, q.targets),
            'pass_td': safe(q.pass_td, q.pass_att),
            'rush_td': safe(q.rush_td, q.rush_att),
            'rec_td': safe(q.rec_td, q.targets),
            'int': safe(q.interceptions, q.pass_att),
            'rush_1d': safe(q.rush_first_downs, q.rush_att),
            'rec_1d': safe(q.rec_first_downs, q.targets),
        }
    return priors


def project_veterans(stats: pd.DataFrame, season: int, games: float,
                     role_volume: pd.DataFrame | None = None,
                     ranks: Mapping[str, float] | None = None) -> pd.DataFrame:
    """Weighted, shrunk projection from the seasons before ``season``.

    When a depth chart is available, volume shrinks toward what that player's
    *slot* is typically worth rather than toward a single number for the whole
    position. Without it a backup is pulled up toward a starter's workload,
    which put eighty quarterbacks over 300 attempts in a league that starts
    thirty-two.
    """
    weights = {season - i - 1: w for i, w in enumerate(HISTORY_WEIGHTS)}
    hist = stats[stats.season.isin(weights)].copy()
    if hist.empty:
        return pd.DataFrame()
    hist['w'] = hist.season.map(weights)
    for col in STAT_COLS:
        hist[col] = hist[col] * hist['w']
    agg = (hist.groupby(['pid', 'position']).sum(numeric_only=True)
           .reset_index().rename(columns={'games': 'wgames'}))
    priors = _priors(agg)

    ranks = ranks or {}
    has_roles = role_volume is not None and not role_volume.empty

    rows = []
    for row in agg.itertuples():
        prior = dict(priors[row.position])
        rank = ranks.get(row.pid)
        if has_roles and rank is not None:
            key = (row.position, float(rank))
            if key in role_volume.index:
                slot = role_volume.loc[key]
                for driver in VOLUME_DRIVERS:
                    if driver in slot and not pd.isna(slot[driver]):
                        prior[driver] = float(slot[driver])
        gw = max(row.wgames, 0.0)

        def volume(value, key):
            observed = value / gw if gw > 0 else 0.0
            return (gw * observed + K_VOLUME * prior[key]) / (gw + K_VOLUME)

        def rate(numerator, denominator, key, k):
            observed = numerator / denominator if denominator > 0 else 0.0
            return (denominator * observed + k * prior[key]) / (denominator + k)

        pa = volume(row.pass_att, 'pass_att')
        ra = volume(row.rush_att, 'rush_att')
        tg = volume(row.targets, 'targets')
        g = games
        rows.append(dict(
            pid=row.pid, position=row.position, games=g, rookie=False,
            pass_att=pa * g,
            pass_yds=pa * g * rate(row.pass_yds, row.pass_att, 'ypa', K_EFFICIENCY),
            pass_td=pa * g * rate(row.pass_td, row.pass_att, 'pass_td', K_TOUCHDOWN),
            interceptions=pa * g * rate(row.interceptions, row.pass_att, 'int', K_TOUCHDOWN),
            rush_att=ra * g,
            rush_yds=ra * g * rate(row.rush_yds, row.rush_att, 'ypc', K_EFFICIENCY),
            rush_td=ra * g * rate(row.rush_td, row.rush_att, 'rush_td', K_TOUCHDOWN),
            rush_first_downs=ra * g * rate(row.rush_first_downs, row.rush_att,
                                           'rush_1d', K_EFFICIENCY),
            targets=tg * g,
            receptions=tg * g * rate(row.receptions, row.targets, 'catch_rate', K_EFFICIENCY),
            rec_yds=tg * g * rate(row.rec_yds, row.targets, 'ypt', K_EFFICIENCY),
            rec_td=tg * g * rate(row.rec_td, row.targets, 'rec_td', K_TOUCHDOWN),
            rec_first_downs=tg * g * rate(row.rec_first_downs, row.targets,
                                          'rec_1d', K_EFFICIENCY),
        ))
    return pd.DataFrame(rows)


def apply_age(proj: pd.DataFrame, season: int, cache: Path) -> pd.DataFrame:
    """Flat to a positional peak, declining after it."""
    path = cache / "players.parquet"
    if not path.exists():
        return proj
    players = pd.read_parquet(path, columns=['gsis_id', 'birth_date']).dropna()
    born = dict(zip(players.gsis_id, pd.to_datetime(players.birth_date, errors='coerce')))
    kickoff = pd.Timestamp(f"{season}-09-01")

    multipliers = []
    for pid, position in zip(proj.pid, proj.position):
        birth = born.get(pid)
        if birth is None or pd.isna(birth):
            multipliers.append(1.0)
            continue
        age = (kickoff - birth).days / 365.25
        peak = AGE_PEAK.get(position, 26.5)
        m = 1.0 + AGE_DECLINE * max(age - peak, 0.0)
        multipliers.append(min(max(m, AGE_CLAMP[0]), AGE_CLAMP[1]))

    proj = proj.copy()
    scale = np.array(multipliers)
    for col in STAT_COLS:
        if col != 'games' and col in proj:
            proj[col] = proj[col] * scale
    return proj


def _name_key(name: str, position: str) -> str:
    """Normalised (name, position) key for matching across nflverse files."""
    text = str(name).lower()
    for junk in (".", "'", "`", "-"):
        text = text.replace(junk, "")
    parts = [t for t in text.split()
             if t not in ("jr", "sr", "ii", "iii", "iv", "v")]
    return " ".join(parts) + "|" + str(position).upper()


def project_rookies(stats: pd.DataFrame, season: int, cache: Path,
                    games: float) -> pd.DataFrame:
    """Rookies have no history, so project them from where they were drafted.

    Matched by name rather than player id: nflverse assigns real ids to a draft
    class only once the season is under way, so the freshest class carries
    placeholders that join to nothing. Rookies on the roster who do not appear
    in the draft file are treated as undrafted and given the last bucket.
    """
    roster_path = cache / f"roster_{season}.parquet"
    picks_path = cache / "draft_picks.parquet"
    if not roster_path.exists() or not picks_path.exists():
        return pd.DataFrame()

    picks = pd.read_parquet(
        picks_path, columns=['season', 'pick', 'gsis_id', 'position',
                             'pfr_player_name'])
    picks = picks[picks.position.isin(POSITIONS)]
    picks['bucket'] = picks.pick.apply(draft_bucket)

    # The historical profile joins cleanly on ids: past classes have real ones.
    past = stats.merge(picks.dropna(subset=['gsis_id'])[['gsis_id', 'season', 'bucket']],
                       left_on=['pid', 'season'], right_on=['gsis_id', 'season'])
    if past.empty:
        return pd.DataFrame()
    per_game = past.copy()
    for col in STAT_COLS:
        if col != 'games':
            per_game[col] = per_game[col] / per_game.games.clip(lower=1)
    stat_cols = [c for c in STAT_COLS if c != 'games']
    profile = per_game.groupby(['position', 'bucket'])[stat_cols].median()
    fallback = per_game.groupby('position')[stat_cols].median()

    roster = pd.read_parquet(
        roster_path,
        columns=['gsis_id', 'full_name', 'position', 'years_exp', 'status'])
    roster = roster.dropna(subset=['gsis_id'])
    roster = roster[roster.position.isin(POSITIONS)]
    roster = roster[roster.years_exp.fillna(0) == 0]
    if roster.empty:
        return pd.DataFrame()

    incoming = picks[picks.season == season]
    bucket_by_name = {
        _name_key(n, p): b
        for n, p, b in zip(incoming.pfr_player_name, incoming.position,
                           incoming.bucket)
    }

    rows = []
    for row in roster.itertuples():
        bucket = bucket_by_name.get(_name_key(row.full_name, row.position),
                                    DRAFT_BUCKETS[-1][1])
        key = (row.position, bucket)
        source = profile.loc[key] if key in profile.index else fallback.loc[row.position]
        entry = dict(pid=row.gsis_id, position=row.position, games=games,
                     rookie=True)
        for col in stat_cols:
            entry[col] = float(source[col]) * games
        rows.append(entry)
    return pd.DataFrame(rows)


def apply_depth_chart(proj: pd.DataFrame, season: int, cache: Path,
                      blend: float) -> pd.DataFrame:
    """Nudge volume toward the role the player currently holds.

    A player who changed teams should not be projected into the job he used to
    have. Historical usage gives the typical per-game volume for each slot on a
    depth chart; this moves each projection part of the way toward the slot he
    is actually listed in.

    This is the one component without a clean backtest -- depth charts before
    2025 are published in a different shape -- so the default weight is modest
    and `--role-weight 0` turns it off.
    """
    path = cache / f"depth_{season}.parquet"
    if not path.exists() or blend <= 0:
        return proj
    depth = pd.read_parquet(path)
    if 'gsis_id' not in depth.columns or 'pos_rank' not in depth.columns:
        return proj
    depth['dt'] = pd.to_datetime(depth.get('dt'), errors='coerce')
    depth = depth[depth.dt == depth.dt.max()]
    depth = depth[depth.pos_abb.isin(POSITIONS)].dropna(subset=['gsis_id'])
    rank = dict(zip(depth.gsis_id, depth.pos_rank))

    # Typical per-game volume for each depth slot, from this projection set --
    # the players already at that rank define what the rank is worth.
    proj = proj.copy()
    proj['rank'] = [rank.get(p, np.nan) for p in proj.pid]
    known = proj[proj['rank'].notna()]
    if known.empty:
        return proj.drop(columns='rank')
    typical = known.groupby(['position', 'rank'])[['pass_att', 'rush_att', 'targets']].median()

    for col in ('pass_att', 'rush_att', 'targets'):
        blended = []
        for row in proj.itertuples():
            value = getattr(row, col)
            key = (row.position, row.rank)
            if pd.isna(row.rank) or key not in typical.index:
                blended.append(value)
                continue
            target = typical.loc[key, col]
            blended.append((1 - blend) * value + blend * target)
        scale = np.divide(blended, proj[col].replace(0, np.nan))
        scale = pd.Series(scale).fillna(1.0).to_numpy()
        # Move the dependent stats with their volume driver.
        driven = {'pass_att': ['pass_yds', 'pass_td', 'interceptions'],
                  'rush_att': ['rush_yds', 'rush_td', 'rush_first_downs'],
                  'targets': ['receptions', 'rec_yds', 'rec_td', 'rec_first_downs']}[col]
        for dep in driven:
            proj[dep] = proj[dep] * scale
        proj[col] = blended
    return proj.drop(columns='rank')


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------

def _league_points_from(frame: pd.DataFrame, league) -> list[float]:
    """Score a frame of stat lines under a league already loaded."""
    from sportsball.players import Player, StatLine
    from sportsball.scoring import score_player

    out = []
    for row in frame.itertuples():
        line = StatLine(
            games=max(getattr(row, 'games', 17) or 17, 1),
            pass_att=row.pass_att, pass_yds=row.pass_yds, pass_td=row.pass_td,
            interceptions=getattr(row, 'interceptions', 0.0),
            rush_att=row.rush_att, rush_yds=row.rush_yds, rush_td=row.rush_td,
            targets=row.targets, receptions=row.receptions,
            rec_yds=row.rec_yds, rec_td=row.rec_td,
            rush_first_downs=row.rush_first_downs,
            rec_first_downs=row.rec_first_downs)
        out.append(score_player(
            Player(name="x", position=row.position, stats=line), league).points)
    return out


def _league_points(frame: pd.DataFrame) -> list[float]:
    """Score a projection frame under the league's own rules."""
    from sportsball.config import load_league
    from sportsball.players import Player, StatLine
    from sportsball.scoring import score_player

    return _league_points_from(frame, load_league("sfb16"))


OUT_COLS = ['name', 'position', 'team', 'fantasy_points', 'games',
            'pass_att', 'pass_cmp',
            'pass_yds', 'pass_td', 'int', 'rush_att', 'rush_yds', 'rush_td',
            'targets', 'rec', 'rec_yds', 'rec_td', 'rush_first_downs',
            'rec_first_downs', 'fumbles_lost', 'two_point']


def to_csv(proj: pd.DataFrame, season: int, cache: Path, out: Path,
           limit: int) -> int:
    merged = proj.copy()
    merged['pass_cmp'] = merged.pass_att * 0.655
    merged['fumbles_lost'] = merged.rush_att * 0.006 + merged.receptions * 0.008
    merged['two_point'] = 0.2

    # Sort on a scratch column: an incoming fantasy_points is a deliberate
    # override (see calibrate_spread) and must survive to the CSV.
    merged['_rank_by'] = _league_points(merged)
    merged = merged.sort_values('_rank_by', ascending=False).head(limit)
    merged = merged.rename(columns={'full_name': 'name', 'receptions': 'rec',
                                    'interceptions': 'int'})

    for col in OUT_COLS:
        if col not in merged:
            merged[col] = "" if col == 'fantasy_points' else 0.0
    frame = merged[OUT_COLS].copy()
    for col in OUT_COLS:
        if col in ('name', 'position', 'team'):
            continue
        if col == 'fantasy_points':
            frame[col] = pd.to_numeric(frame[col], errors='coerce').round(1)
            frame[col] = frame[col].where(frame[col].notna(), "")
            continue
        frame[col] = frame[col].astype(float).round(1)
    frame.to_csv(out, index=False)
    return len(frame)


# --------------------------------------------------------------------------

def attach_rosters(proj: pd.DataFrame, season: int, cache: Path) -> pd.DataFrame:
    """Keep only players with a job this season, and record who they play for."""
    roster = pd.read_parquet(cache / f"roster_{season}.parquet",
                             columns=['gsis_id', 'full_name', 'team', 'position',
                                      'status'])
    roster = roster.dropna(subset=['gsis_id']).drop_duplicates('gsis_id')
    roster = roster[roster.status.isin(['ACT', 'RES', 'E14'])]
    merged = proj.merge(roster, left_on='pid', right_on='gsis_id', how='inner',
                        suffixes=('', '_r'))
    merged['position'] = merged['position_r'].where(
        merged['position_r'].isin(POSITIONS), merged['position'])
    return merged.drop(columns=[c for c in ('position_r',) if c in merged])


def build(season: int, cache: Path, games: float, blend: float) -> pd.DataFrame:
    stats = seasonal_stats(cache)
    role_volume = historical_role_volume(stats, cache)
    ranks = depth_ranks(cache, season)
    veterans = project_veterans(stats, season, games, role_volume, ranks)
    veterans = apply_age(veterans, season, cache)
    rookies = project_rookies(stats, season, cache, games)
    proj = pd.concat([veterans, rookies], ignore_index=True)
    proj = proj.drop_duplicates('pid', keep='first')
    proj = apply_depth_chart(proj, season, cache, blend)
    # Roster first, then normalise: the team totals must be divided among the
    # players who actually have jobs, not everyone who ever took a snap.
    return attach_rosters(proj, season, cache)


def backtest(cache: Path, seasons: list[int], games: float) -> None:
    from sportsball.config import load_league
    from sportsball.players import Player, StatLine
    from sportsball.scoring import score_player

    league = load_league("sfb16")
    stats = seasonal_stats(cache)

    def points(row) -> float:
        line = StatLine(
            games=max(getattr(row, 'games', 17) or 17, 1),
            pass_att=row.pass_att, pass_yds=row.pass_yds, pass_td=row.pass_td,
            interceptions=row.interceptions, rush_att=row.rush_att,
            rush_yds=row.rush_yds, rush_td=row.rush_td, targets=row.targets,
            receptions=row.receptions, rec_yds=row.rec_yds, rec_td=row.rec_td,
            rush_first_downs=row.rush_first_downs,
            rec_first_downs=row.rec_first_downs)
        return score_player(Player(name="x", position=row.position, stats=line),
                            league).points

    print(f"\n{'season':8s}{'model':18s}{'spearman':>10s}{'MAE':>8s}{'top200 rho':>12s}")
    for target in seasons:
        actual = stats[stats.season == target].copy()
        if actual.empty:
            continue
        actual['actual'] = [points(r) for r in actual.itertuples()]

        proj = project_veterans(stats[stats.season < target], target, games)
        proj = apply_age(proj, target, cache)
        proj['proj'] = [points(r) for r in proj.itertuples()]

        prev = stats[stats.season == target - 1].copy()
        prev['prev'] = [points(r) for r in prev.itertuples()]
        window = stats[stats.season.isin([target - i - 1 for i in range(3)])].copy()
        window['p'] = [points(r) for r in window.itertuples()]
        window['w'] = window.season.map(
            {target - i - 1: w for i, w in enumerate(HISTORY_WEIGHTS)})
        weighted = (window.assign(x=window.p * window.w).groupby('pid')
                    .agg(wpts=('x', 'sum')).reset_index())

        df = (actual[['pid', 'actual', 'games']]
              .merge(proj[['pid', 'proj']], on='pid')
              .merge(prev[['pid', 'prev']], on='pid', how='left')
              .merge(weighted, on='pid', how='left').fillna(0.0))
        df = df[df.games >= 8]
        top = df.nlargest(200, 'actual')
        for label, col in (("last season", 'prev'), ("3yr weighted", 'wpts'),
                           ("this projection", 'proj')):
            rho = df[col].rank().corr(df.actual.rank())
            mae = (df[col] - df.actual).abs().mean()
            trho = top[col].rank().corr(top.actual.rank())
            print(f"{target:<8d}{label:18s}{rho:10.3f}{mae:8.1f}{trho:12.3f}")


def historical_rank_curve(stats: pd.DataFrame, league) -> list[float]:
    """Points scored by the Nth best player, averaged over past seasons."""
    scored = stats.copy()
    scored['pts'] = _league_points_from(scored, league)
    curves = [sorted(d.pts.tolist(), reverse=True)
              for _, d in scored.groupby('season')]
    if not curves:
        return []
    depth = min(len(c) for c in curves)
    return [float(np.mean([c[i] for c in curves])) for i in range(depth)]


def calibrate_spread(proj: pd.DataFrame, curve: list[float], league) -> pd.DataFrame:
    """Keep the model's ordering, adopt history's spread.

    Shrinking every player toward a positional prior is what makes individual
    projections accurate, and it necessarily produces a distribution narrower
    than reality: the model's best player projects to 72% of what the best
    player actually scores each year, while its 200th projects to 138%. Across
    five seasons the top score is remarkably stable -- 779, 737, 711, 692, 688
    -- so that gap is not the model failing to guess a lucky outlier, it is the
    spread being wrong.

    Spread matters for pricing specifically, because value over replacement is
    a distance and dollars are proportional to it. This maps each player onto
    what the player at his rank has historically scored, which leaves the
    ranking untouched and only changes the shape.

    The trade is real and worth stating: this makes individual projections
    *worse* by squared error, since it predicts a 720-point season for whoever
    happens to rank first. It makes the board better shaped for an auction. It
    is opt-in for that reason, and it moves the top of the board about 10-15%
    rather than transforming it, because normalising to a fixed budget absorbs
    most of a proportional squeeze.
    """
    if not curve:
        return proj
    proj = proj.copy()
    proj['fantasy_points'] = _league_points(proj)
    order = proj.fantasy_points.rank(ascending=False, method='first').astype(int)
    proj['fantasy_points'] = [
        curve[min(rank, len(curve)) - 1] for rank in order
    ]
    return proj


def calibrate(cache: Path, season: int, games: float) -> None:
    """Do the projections imply a realistic number of big plays?

    SFB16 pays ten points per explosive play, so a projection that is right
    about yardage and wrong about how that yardage arrives will misprice the
    board. This projects a past season, then compares expected big plays
    against what those *same players* actually did -- same-player rather than
    top-N against top-N, because comparing the best projections to the best
    outcomes flatters or punishes a model for selection rather than accuracy.
    Actuals are paced to the projected number of games so the two are
    comparable.
    """
    from sportsball.config import load_league
    from sportsball.players import Player, StatLine
    from sportsball.scoring import score_player

    league = load_league("sfb16")
    points = league.bonuses.points
    stats = seasonal_stats(cache)
    proj = apply_age(project_veterans(stats[stats.season < season], season, games),
                     season, cache)

    pbp = pd.concat([
        pd.read_parquet(f, columns=PBP_COLS)
        for f in sorted(glob.glob(str(cache / "pbp_*.parquet")))
    ])
    pbp = pbp[(pbp.season_type == "REG") & (pbp.season == season)]
    if pbp.empty:
        print(f"no play-by-play for {season}; try an earlier --season")
        return

    def actual(idcol, mask, ycol, threshold):
        d = pbp[mask].copy()
        d['big'] = (d[ycol] >= threshold).astype(int)
        return d.groupby(idcol).big.sum()

    big = {
        'rec_20_plays': actual('receiver_player_id',
                               pbp.complete_pass.eq(1) & pbp.receiver_player_id.notna(),
                               'receiving_yards', 20),
        'rush_40_plays': actual('rusher_player_id',
                                pbp.rush_attempt.eq(1) & pbp.rusher_player_id.notna(),
                                'rushing_yards', 40),
        'pass_40_plays': actual('passer_player_id',
                                pbp.pass_attempt.eq(1) & pbp.passer_player_id.notna(),
                                'passing_yards', 40),
    }
    played = stats[stats.season == season].set_index('pid').games.to_dict()

    rows = []
    for row in proj.itertuples():
        gp = played.get(row.pid)
        if not gp or gp < 8:
            continue
        line = StatLine(games=games, pass_att=row.pass_att, pass_yds=row.pass_yds,
                        pass_td=row.pass_td, rush_att=row.rush_att,
                        rush_yds=row.rush_yds, rush_td=row.rush_td,
                        targets=row.targets, receptions=row.receptions,
                        rec_yds=row.rec_yds, rec_td=row.rec_td)
        scored = score_player(Player(name="x", position=row.position, stats=line),
                              league)
        pace = games / gp
        entry = {'position': row.position}
        for key, series in big.items():
            entry[f"proj_{key}"] = scored.bonus_breakdown.get(key, 0.0) / points
            entry[f"act_{key}"] = float(series.get(row.pid, 0)) * pace
        rows.append(entry)

    df = pd.DataFrame(rows)
    print(f"\nBIG PLAY CALIBRATION \u2014 {season} projected from the seasons before it")
    print(f"({len(df)} players with 8+ games; actuals paced to {games:.0f} games)\n")
    print(f"  {'bonus':22s}{'pos':6s}{'actual':>10s}{'projected':>12s}{'ratio':>8s}")
    families = (("20+ yard receptions", 'rec_20_plays', ['WR', 'TE', 'RB']),
                ("40+ yard runs", 'rush_40_plays', ['RB', 'QB']),
                ("40+ yard pass plays", 'pass_40_plays', ['QB']))
    for label, key, positions in families:
        for position in positions:
            d = df[df.position == position]
            if d.empty or d[f"act_{key}"].sum() == 0:
                continue
            a, p_ = d[f"act_{key}"].sum(), d[f"proj_{key}"].sum()
            print(f"  {label:22s}{position:6s}{a:10.0f}{p_:12.0f}{p_ / a:8.0%}")
        d = df[df.position.isin(positions)]
        a, p_ = d[f"act_{key}"].sum(), d[f"proj_{key}"].sum()
        if a:
            print(f"  {'':22s}{'ALL':6s}{a:10.0f}{p_:12.0f}{p_ / a:8.0%}\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--league", "-l", default="sfb16",
                    help="league whose scoring the calibration should use")
    ap.add_argument("--history", type=int, default=5,
                    help="seasons of play-by-play to pull (default 5)")
    ap.add_argument("--cache-dir", default=".nflverse-cache")
    ap.add_argument("--games", type=float, default=PROJECTED_GAMES)
    ap.add_argument("--role-weight", type=float, default=DEPTH_BLEND,
                    help="how far to move volume toward the depth chart (0 disables)")
    ap.add_argument("--limit", type=int, default=320,
                    help="how many players to write (default 320)")
    ap.add_argument("--out", "-o", default="projections.csv")
    ap.add_argument("--backtest", action="store_true",
                    help="score the model against past seasons and exit")
    ap.add_argument("--calibrate", action="store_true",
                    help="check projected big plays against what happened, and exit")
    ap.add_argument("--calibrate-spread", action="store_true",
                    help="rescale points onto the historical distribution by rank. "
                         "Leaves the ordering alone; widens the board, which "
                         "raises the top by roughly 10-15%%")
    args = ap.parse_args()

    cache = fetch(args.season, args.history, Path(args.cache_dir))
    if args.backtest:
        seasons = list(range(args.season - 3, args.season))
        backtest(cache, seasons, args.games)
        return 0
    if args.calibrate:
        calibrate(cache, args.season - 1, args.games)
        return 0

    proj = build(args.season, cache, args.games, args.role_weight)
    if args.calibrate_spread:
        from sportsball.config import load_league

        league = load_league(args.league)
        curve = historical_rank_curve(seasonal_stats(cache), league)
        proj = calibrate_spread(proj, curve, league)
    written = to_csv(proj, args.season, cache, Path(args.out), args.limit)
    rookies = int(proj.rookie.sum()) if 'rookie' in proj else 0
    print(f"wrote {written} players to {args.out} "
          f"({args.season} rosters, {rookies} rookies projected from draft slot)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
