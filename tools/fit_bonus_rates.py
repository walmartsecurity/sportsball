"""Fit the bonus and first-down constants against real play-by-play data.

The projection pipeline rests on a handful of empirical constants: how often a
40-yard run happens, how much a player's yardage swings from week to week, how
many first downs a reception is worth. Guessing at those was the single largest
source of error in the model. This script replaces the guesses with numbers
fitted to nflverse play-by-play, and prints the diagnostics needed to judge
whether the fits are any good.

    pip install -e '.[fit]'
    python tools/fit_bonus_rates.py --seasons 2021 2022 2023 2024 2025

Output is a YAML fragment ready to paste into a league config, plus calibration
tables. Data is cached, so re-runs are cheap.

Two methodology notes that materially change the answers:

* **Big-play rates are fitted against prior-season efficiency, not the same
  season's.** Yards per carry is partly *caused by* the 40-yard runs being
  predicted, so a same-season fit is circular and inflates the efficiency
  elasticity badly -- 4.66 versus 1.72 for rushing in the data below. Since the
  tool is fed *projected* efficiency at prediction time, the lagged fit is the
  one that matches how the model is actually used.

* **Game yardage is gamma, not lognormal, and its spread shrinks with volume.**
  A 97-yard-per-game back is far steadier than a 33-yard-per-game back
  (coefficient of variation 0.43 against 0.83). Holding it constant, as the
  first version of this model did, overstated 200-yard games by 88-370%.
"""

from __future__ import annotations

import argparse
import glob
import math
import sys
from pathlib import Path
from urllib.request import urlretrieve

try:
    import numpy as np
    import pandas as pd
except ImportError:  # pragma: no cover
    sys.exit("this script needs pandas and numpy: pip install -e '.[fit]'")

PBP_URL = ("https://github.com/nflverse/nflverse-data/releases/download/"
           "pbp/play_by_play_{year}.parquet")
ROSTER_URL = ("https://github.com/nflverse/nflverse-data/releases/download/"
              "rosters/roster_{year}.parquet")

PBP_COLS = [
    'season', 'week', 'game_id', 'season_type', 'complete_pass', 'pass_attempt',
    'rush_attempt', 'passing_yards', 'receiving_yards', 'rushing_yards',
    'first_down_pass', 'first_down_rush', 'passer_player_id',
    'receiver_player_id', 'rusher_player_id',
]

# Minimum volume for a player-season to count. Below these the rates are noise,
# and the population we care about -- players worth an auction dollar -- is
# comfortably above them.
MIN_RECEPTIONS = 20
MIN_CARRIES = 30
MIN_ATTEMPTS = 100
MIN_GAMES = 10


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------

def fetch(seasons, cache: Path):
    cache.mkdir(parents=True, exist_ok=True)
    for year in seasons:
        for url, stem in ((PBP_URL, "pbp"), (ROSTER_URL, "roster")):
            path = cache / f"{stem}_{year}.parquet"
            if not path.exists():
                print(f"  downloading {stem} {year}...", file=sys.stderr)
                urlretrieve(url.format(year=year), path)
    return cache


def load(seasons, cache: Path):
    pbp = pd.concat([
        pd.read_parquet(cache / f"pbp_{y}.parquet", columns=PBP_COLS)
        for y in seasons
    ])
    pbp = pbp[pbp.season_type == "REG"]
    ros = pd.concat([
        pd.read_parquet(cache / f"roster_{y}.parquet",
                        columns=['season', 'gsis_id', 'position'])
        for y in seasons
    ]).dropna(subset=['gsis_id']).drop_duplicates(['season', 'gsis_id'])
    return pbp, ros.set_index(['season', 'gsis_id']).position.to_dict()


# --------------------------------------------------------------------------
# rate model
# --------------------------------------------------------------------------

def poisson_rate_fit(counts, exposure, efficiency, eff_base, fixed_elasticity=None):
    """Fit E[count] = exposure * rate * (eff/eff_base)**elasticity by Newton.

    Poisson regression with an offset of log(exposure). With
    ``fixed_elasticity`` only the base rate is estimated, which is how the
    final rates are set: elasticity from the lagged fit, level from the full
    sample.
    """
    y = np.asarray(counts, float)
    n = np.asarray(exposure, float)
    x = np.log(np.asarray(efficiency, float) / eff_base)
    keep = (n > 0) & np.isfinite(x) & np.isfinite(y)
    y, n, x = y[keep], n[keep], x[keep]

    if fixed_elasticity is not None:
        offset = n * np.exp(fixed_elasticity * x)
        return float(y.sum() / offset.sum()), float(fixed_elasticity), 0.0

    b = np.array([math.log(max(y.sum() / n.sum(), 1e-9)), 0.0])
    for _ in range(100):
        mu = n * np.exp(b[0] + b[1] * x)
        grad = np.array([(y - mu).sum(), (x * (y - mu)).sum()])
        hess = np.array([[-mu.sum(), -(x * mu).sum()],
                         [-(x * mu).sum(), -(x * x * mu).sum()]])
        step = np.linalg.solve(hess, grad)
        b = b - step
        if np.max(np.abs(step)) < 1e-10:
            break
    se = math.sqrt(np.linalg.inv(-hess)[1, 1])
    return float(np.exp(b[0])), float(b[1]), float(se)


def lagged_elasticity(frame, idcol, exposure, eff, count, minexp, label):
    """Elasticity of big-play rate on *prior* season efficiency.

    This is the number that matters: it answers "given a projected efficiency,
    how many big plays?", which is exactly the question the tool asks.
    """
    prev = frame[[idcol, 'season', eff, exposure]].copy()
    prev['season'] += 1
    prev = prev.rename(columns={eff: 'prev_eff', exposure: 'prev_exp'})
    m = frame.merge(prev, on=[idcol, 'season'], how='inner')
    m = m[(m[exposure] >= minexp) & (m.prev_exp >= minexp)]
    eff_base = m['yds'].sum() / m[exposure].sum()

    same = poisson_rate_fit(m[count], m[exposure], m[eff], eff_base)
    lag = poisson_rate_fit(m[count], m[exposure], m.prev_eff, eff_base)
    print(f"\n{label}  (n={len(m)} lagged player-season pairs)")
    print(f"    same-season efficiency : elasticity {same[1]:6.3f}   <- circular")
    print(f"    prior-season efficiency: elasticity {lag[1]:6.3f} "
          f"(+/-{lag[2]:.3f})   <- used")

    pred = m[exposure] * lag[0] * (m.prev_eff / eff_base) ** lag[1]
    flat = m[exposure] * (m[count].sum() / m[exposure].sum())
    m = m.assign(pred=pred, flat=flat,
                 bucket=pd.qcut(m.prev_eff, 4, labels=['Q1 low', 'Q2', 'Q3', 'Q4 high']))
    tbl = m.groupby('bucket', observed=True).apply(lambda gg: pd.Series({
        'actual': gg[count].sum(),
        'flat_err': gg.flat.sum() / max(gg[count].sum(), 1) - 1,
        'fitted_err': gg.pred.sum() / max(gg[count].sum(), 1) - 1,
    }), include_groups=False)
    print("    calibration by prior-season efficiency quartile:")
    for bucket, row in tbl.iterrows():
        print(f"      {bucket:8s} actual {int(row.actual):5d}   "
              f"flat rate {row.flat_err:+7.1%}   fitted {row.fitted_err:+7.1%}")
    return lag[1]


# --------------------------------------------------------------------------
# variance model
# --------------------------------------------------------------------------

def gamma_tail(mean: float, cv: float, threshold: float) -> float:
    """P(X >= threshold) for a gamma with the given mean and CV."""
    if mean <= 0 or cv <= 0 or threshold <= 0:
        return 0.0
    shape = 1.0 / (cv * cv)
    return _gammaincc(shape, threshold / (mean / shape))


def _gammaincc(a: float, x: float) -> float:
    """Regularised upper incomplete gamma Q(a, x)."""
    if x <= 0:
        return 1.0
    if x < a + 1.0:
        total = term = 1.0 / a
        for n in range(1, 500):
            term *= x / (a + n)
            total += term
            if abs(term) < abs(total) * 1e-13:
                break
        return 1.0 - total * math.exp(-x + a * math.log(x) - math.lgamma(a))
    tiny = 1e-300
    b, c, d = x + 1.0 - a, 1.0 / tiny, 1.0 / (x + 1.0 - a)
    h = d
    for i in range(1, 500):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-13:
            break
    return h * math.exp(-x + a * math.log(x) - math.lgamma(a))


def fit_cv_curve(stats, label):
    """Fit cv = cv_ref * (mean / ref)**slope by least squares in log space."""
    mean = stats['mean'].to_numpy(float)
    cv = (stats['std'] / stats['mean']).to_numpy(float)
    ok = (mean > 0) & (cv > 0) & np.isfinite(cv)
    mean, cv = mean[ok], cv[ok]
    ref = float(np.median(mean))
    slope, intercept = np.polyfit(np.log(mean / ref), np.log(cv), 1)
    cv_ref = float(math.exp(intercept))
    print(f"    {label:10s} cv = {cv_ref:.3f} * (yds_per_game / {ref:.1f}) ^ "
          f"{slope:+.3f}   (n={len(mean)})")
    return cv_ref, ref, float(slope)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seasons", type=int, nargs="+",
                    default=[2021, 2022, 2023, 2024, 2025])
    ap.add_argument("--cache-dir", default=".nflverse-cache")
    ap.add_argument("--emit", default=None,
                    help="write the fitted YAML fragment to this path")
    args = ap.parse_args()

    cache = fetch(args.seasons, Path(args.cache_dir))
    pbp, pos_map = load(args.seasons, cache)
    print(f"loaded {len(pbp):,} regular season plays from {args.seasons}")

    def positions(seasons, ids):
        return pd.Series([pos_map.get((s, i)) for s, i in zip(seasons, ids)],
                         index=ids.index)

    # ---- per player-season aggregates ----
    rec = pbp[(pbp.complete_pass == 1) & pbp.receiver_player_id.notna()].copy()
    rec['y'] = rec.receiving_yards.fillna(0.0)
    recg = rec.groupby(['season', 'receiver_player_id']).agg(
        receptions=('y', 'size'), yds=('y', 'sum'),
        big=('y', lambda s: int((s >= 20).sum())), fd=('first_down_pass', 'sum'),
    ).reset_index()
    recg['position'] = positions(recg.season, recg.receiver_player_id)
    recg['eff'] = recg.yds / recg.receptions
    recg = recg[recg.position.isin(['WR', 'TE', 'RB'])]

    rush = pbp[(pbp.rush_attempt == 1) & pbp.rusher_player_id.notna()].copy()
    rush['y'] = rush.rushing_yards.fillna(0.0)
    rushg = rush.groupby(['season', 'rusher_player_id']).agg(
        carries=('y', 'size'), yds=('y', 'sum'),
        big=('y', lambda s: int((s >= 40).sum())), fd=('first_down_rush', 'sum'),
    ).reset_index()
    rushg['position'] = positions(rushg.season, rushg.rusher_player_id)
    rushg['eff'] = rushg.yds / rushg.carries

    pas = pbp[(pbp.pass_attempt == 1) & pbp.passer_player_id.notna()].copy()
    pas['y'] = pas.passing_yards.fillna(0.0)
    pasg = pas.groupby(['season', 'passer_player_id']).agg(
        att=('y', 'size'), yds=('y', 'sum'),
        big=('y', lambda s: int((s >= 40).sum())),
    ).reset_index()
    pasg['position'] = positions(pasg.season, pasg.passer_player_id)
    pasg['eff'] = pasg.yds / pasg.att
    pasg = pasg[pasg.position == 'QB']

    print("\n" + "=" * 72)
    print("BIG PLAY RATES")
    print("=" * 72)
    rec_el = lagged_elasticity(recg, 'receiver_player_id', 'receptions', 'eff',
                               'big', 25, "20+ yard receptions")
    rb = rushg[rushg.position == 'RB']
    rush_el = lagged_elasticity(rb, 'rusher_player_id', 'carries', 'eff',
                                'big', 50, "40+ yard runs (running backs)")
    pass_el = lagged_elasticity(pasg, 'passer_player_id', 'att', 'eff',
                                'big', 150, "40+ yard pass plays")

    # Levels from the full sample, elasticity pinned to the lagged estimate.
    r = recg[recg.receptions >= MIN_RECEPTIONS]
    rec_eff_base = r.yds.sum() / r.receptions.sum()
    rec_rate, _, _ = poisson_rate_fit(r.big, r.receptions, r.eff, rec_eff_base, rec_el)

    u = rb[rb.carries >= MIN_CARRIES]
    rush_eff_base = u.yds.sum() / u.carries.sum()
    rush_rate, _, _ = poisson_rate_fit(u.big, u.carries, u.eff, rush_eff_base, rush_el)

    q = pasg[pasg.att >= MIN_ATTEMPTS]
    pass_eff_base = q.yds.sum() / q.att.sum()
    pass_rate, _, _ = poisson_rate_fit(q.big, q.att, q.eff, pass_eff_base, pass_el)

    # Quarterback runs break the single-rate model: their yards per carry comes
    # from scrambles, not breakaways, so efficiency over-predicts 40-yard runs.
    qb_rush = rushg[(rushg.position == 'QB') & (rushg.carries >= MIN_CARRIES)]
    qb_pred = (qb_rush.carries * rush_rate
               * (qb_rush.eff / rush_eff_base) ** rush_el).sum()
    qb_mult = float(qb_rush.big.sum() / max(qb_pred, 1e-9))
    print(f"\nquarterback rushing: {int(qb_rush.big.sum())} actual 40+ runs against "
          f"{qb_pred:.1f} predicted -> multiplier {qb_mult:.2f}")

    # ---- first downs ----
    print("\n" + "=" * 72)
    print("FIRST DOWN RATES  (touchdowns are already flagged as first downs)")
    print("=" * 72)
    # Require a real sample before trusting a positional rate: tight ends
    # carry the ball perhaps twice a decade, and a two-season rate is noise.
    MIN_PLAYER_SEASONS = 15

    def rate_by_position(frame, numerator, denominator):
        out = {}
        for pos, gg in frame.groupby('position'):
            if len(gg) < MIN_PLAYER_SEASONS or pos not in ('QB', 'RB', 'WR', 'TE'):
                continue
            out[pos] = gg[numerator].sum() / gg[denominator].sum()
        return pd.Series(out)

    fd_rec = rate_by_position(r, 'fd', 'receptions')
    fd_rush = rate_by_position(rushg[rushg.carries >= MIN_CARRIES], 'fd', 'carries')
    print("    per reception:", {k: round(v, 4) for k, v in fd_rec.items()})
    print("    per carry    :", {k: round(v, 4) for k, v in fd_rush.items()})
    print(f"    (positions with fewer than {MIN_PLAYER_SEASONS} qualifying "
          "player-seasons are omitted and keep their defaults)")

    # ---- game-to-game variance ----
    print("\n" + "=" * 72)
    print("GAME-TO-GAME VARIANCE")
    print("=" * 72)
    rec_games = rec.groupby(['season', 'game_id', 'receiver_player_id']).y.sum()
    rec_games = rec_games.reset_index().rename(columns={'receiver_player_id': 'pid',
                                                        'y': 'rec_y'})
    rush_games = rush.groupby(['season', 'game_id', 'rusher_player_id']).y.sum()
    rush_games = rush_games.reset_index().rename(columns={'rusher_player_id': 'pid',
                                                          'y': 'rush_y'})
    games = rec_games.merge(rush_games, on=['season', 'game_id', 'pid'], how='outer')
    games = games.fillna({'rec_y': 0.0, 'rush_y': 0.0})
    games['scrimmage'] = games.rec_y + games.rush_y
    games['position'] = [pos_map.get((s, p)) for s, p in zip(games.season, games.pid)]

    print("  scrimmage yards:")
    cv_fits = {}
    for pos in ['RB', 'WR', 'TE', 'QB']:
        grp = games[games.position == pos]
        stats = grp.groupby(['season', 'pid']).scrimmage.agg(['count', 'mean', 'std'])
        stats = stats[(stats['count'] >= MIN_GAMES) & (stats['mean'] >= 25)]
        if len(stats) < 20:
            continue
        cv_fits[pos] = fit_cv_curve(stats, pos)

    pass_games = pas.groupby(['season', 'game_id', 'passer_player_id']).agg(
        py=('y', 'sum'), att=('y', 'size')).reset_index()
    pass_games = pass_games[pass_games.att >= 10]
    pstats = pass_games.groupby(['season', 'passer_player_id']).py.agg(
        ['count', 'mean', 'std'])
    pstats = pstats[(pstats['count'] >= MIN_GAMES) & (pstats['mean'] >= 150)]
    print("  passing yards:")
    pass_cv = fit_cv_curve(pstats, "QB")

    # ---- distribution calibration ----
    print("\n  threshold calibration (gamma vs lognormal):")
    _calibrate(games, cv_fits, pass_games, pstats, pass_cv)

    yaml_text = _emit_yaml(
        rec_rate, rec_eff_base, rec_el,
        rush_rate, rush_eff_base, rush_el, qb_mult,
        pass_rate, pass_eff_base, pass_el,
        cv_fits, pass_cv, fd_rec, fd_rush, args.seasons,
    )
    print("\n" + "=" * 72)
    print("FITTED CONFIG")
    print("=" * 72)
    print(yaml_text)
    if args.emit:
        Path(args.emit).write_text(yaml_text)
        print(f"written to {args.emit}")
    return 0


def _lognormal_tail(mean, cv, t):
    if mean <= 0 or cv <= 0:
        return 0.0
    s2 = math.log(1 + cv * cv)
    s = math.sqrt(s2)
    mu = math.log(mean) - s2 / 2
    return 0.5 * (1 + math.erf(((mu - math.log(t)) / s) / math.sqrt(2)))


def _calibrate(games, cv_fits, pass_games, pstats, pass_cv):
    for pos, (cv_ref, ref, slope) in cv_fits.items():
        grp = games[games.position == pos]
        stats = grp.groupby(['season', 'pid']).scrimmage.agg(['count', 'mean'])
        stats = stats[(stats['count'] >= MIN_GAMES) & (stats['mean'] >= 25)]
        joined = grp.merge(stats[['mean']], left_on=['season', 'pid'], right_index=True)
        means = joined['mean'].to_numpy(float)
        for t in (100, 200):
            actual = int((joined.scrimmage >= t).sum())
            g_pred = sum(gamma_tail(m, cv_ref * (m / ref) ** slope, t) for m in means)
            l_pred = sum(_lognormal_tail(m, cv_ref * (m / ref) ** slope, t)
                         for m in means)
            print(f"    {pos} {t}+ yds: actual {actual:5d}   "
                  f"gamma {g_pred / max(actual, 1) - 1:+7.1%}   "
                  f"lognormal {l_pred / max(actual, 1) - 1:+7.1%}")

    cv_ref, ref, slope = pass_cv
    joined = pass_games.merge(pstats[['mean']],
                              left_on=['season', 'passer_player_id'], right_index=True)
    means = joined['mean'].to_numpy(float)
    for t in (300, 400):
        actual = int((joined.py >= t).sum())
        g_pred = sum(gamma_tail(m, cv_ref * (m / ref) ** slope, t) for m in means)
        l_pred = sum(_lognormal_tail(m, cv_ref * (m / ref) ** slope, t) for m in means)
        print(f"    QB {t}+ pass yds: actual {actual:5d}   "
              f"gamma {g_pred / max(actual, 1) - 1:+7.1%}   "
              f"lognormal {l_pred / max(actual, 1) - 1:+7.1%}")


def _emit_yaml(rec_rate, rec_eff, rec_el, rush_rate, rush_eff, rush_el, qb_mult,
               pass_rate, pass_eff, pass_el, cv_fits, pass_cv, fd_rec, fd_rush,
               seasons):
    def cvblock(key, index):
        return "\n".join(
            f"    {pos}: {cv_fits[pos][index]:.3f}" for pos in sorted(cv_fits)
        )
    span = f"{min(seasons)}-{max(seasons)}"
    return f"""# Fitted against nflverse play-by-play, {span} regular season.
# Regenerate with: python tools/fit_bonus_rates.py
bonuses:
  distribution: gamma
  # Big-play rates, per opportunity at league-average efficiency. Elasticities
  # come from prior-season efficiency, so they describe what a projection can
  # actually predict rather than same-season circularity.
  rec_20_rate: {rec_rate:.5f}
  rec_20_ypr_base: {rec_eff:.3f}
  rec_20_elasticity: {rec_el:.3f}
  rush_40_rate: {rush_rate:.5f}
  rush_40_ypc_base: {rush_eff:.3f}
  rush_40_elasticity: {rush_el:.3f}
  # Quarterback yards per carry comes from scrambles rather than breakaway
  # speed, so the shared rate model badly over-predicts their 40-yard runs.
  rush_40_position_multiplier:
    QB: {qb_mult:.2f}
  pass_40_rate: {pass_rate:.5f}
  pass_40_ypa_base: {pass_eff:.3f}
  pass_40_elasticity: {pass_el:.3f}
  # Game-to-game spread, and how it shrinks as volume rises.
  scrimmage_cv:
{cvblock('scrimmage_cv', 0)}
  scrimmage_cv_ref:
{cvblock('scrimmage_cv_ref', 1)}
  scrimmage_cv_slope:
{cvblock('scrimmage_cv_slope', 2)}
  pass_yds_cv: {pass_cv[0]:.3f}
  pass_yds_cv_ref: {pass_cv[1]:.1f}
  pass_yds_cv_slope: {pass_cv[2]:.3f}

first_downs:
  # Touchdowns are already counted as first downs in these rates.
  touchdowns_count_as_first_down: false
  per_reception:
{chr(10).join(f"    {p}: {v:.3f}" for p, v in sorted(fd_rec.items()))}
  per_rush:
{chr(10).join(f"    {p}: {v:.3f}" for p, v in sorted(fd_rush.items()))}
"""


if __name__ == "__main__":
    raise SystemExit(main())
