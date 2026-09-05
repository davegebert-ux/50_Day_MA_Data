"""
overhead_resistance_and_smoothness_checks.py -- The two pre-watchlist
screening checks between the TradingView momentum screen and the daily
50-day MA touch scan.

WHAT THIS FILE IS
------------------
Real, working code DOES exist for both of these checks -- it just, like
sim.py and the touch-scan script before it, only ever lived inside a
sandbox during the sessions where it was built and validated, and was
never saved as a standalone file. This is that code, recovered verbatim
from two transcripts:
  - 2026-08-20-21-37-30-trend-eff-relstrength-screening-redesign.txt
    (overhead-resistance check, all iterations through the final version)
  - 2026-08-21-00-56-42-ma-respect-redesign-smoothness.txt
    (smoothness check, all iterations through the final version, plus the
    112-name validation run whose exact numbers appear in
    MA_Respect_Redesign_Notes.txt)

Both functions below are the FINAL, locked-in versions -- i.e. the exact
code whose output matches the validation numbers already written up in
MA_Respect_Redesign_Notes.txt. Earlier iterations (percentage-drop
thresholds at 50%/40%/25% for overhead-resistance; ATR-variability for
smoothness) were tried and rejected along the way -- see the notes file
for the full story of why. Only the adopted, final logic is reproduced
here as clean functions; the rejected attempts are not included.

WHERE THESE FIT IN THE PIPELINE
---------------------------------
momentum screen (TradingView) -> OVERHEAD-RESISTANCE CHECK -> SMOOTHNESS
CHECK -> weekly watchlist -> daily 50-day MA touch scan -> scorecard

Both checks are pre-watchlist screens, not scorecard attributes, and not
part of the daily touch-detection step. They formalize two pieces of
Dave's manual visual pass over the momentum-screen output: "is this
actually broken out, or still under an old high" (overhead-resistance),
and "did this climb in a fairly direct bottom-left-to-top-right path, or
via repeated big up/down cycles" (smoothness). A third piece of that
visual pass -- avoiding moves that were mostly one big gap -- was
evaluated and deliberately NOT added as a separate check at this stage;
see the DECISION note at the bottom of this file.

IMPORTANT CAVEAT ON THE 456-EVENT DATASET
--------------------------------------------
Neither check was ever run against the full ~1,002-ticker universe, and
neither was ever run against the 456 historical touch events that the
whole scorecard project is scored against. That means the 456-event
dataset does NOT reflect either of these filters -- it includes touch
events from tickers that one or both of these checks would have excluded.
This was flagged in the notes as a likely source of some of the scoring
noise found earlier in the project (e.g. UNM). Running both checks
against the 456-event set (or rebuilding it with these checks applied
upstream) is still an open TODO, not something that's already been done.
"""

import pandas as pd
import numpy as np
import os
import random

DATA_DIR = "/home/claude/data_pull/50_Day_MA_Data-claude-historical-stock-data-pull-3udz0v/data"


def load(ticker):
    """Load one ticker's daily OHLCV CSV, sorted by date."""
    p = pd.read_csv(os.path.join(DATA_DIR, f"{ticker}_1d_data.csv"))
    p['Date'] = pd.to_datetime(p['Date'])
    return p.sort_values('Date').reset_index(drop=True)


# =========================================================================
# CHECK 1: OVERHEAD-RESISTANCE / ALL-TIME-HIGH CHECK
# =========================================================================
def overhead_resistance_check(df, as_of_date, lookback_years=2, grace_days=60):
    """
    FINAL, locked-in version (v2). Excludes a ticker if there is any
    unresolved old high above its current price within the lookback
    window -- no percentage-drop threshold, no minimum drawdown required.

    Logic:
      1. Look back `lookback_years` (2 years) from as_of_date.
      2. Within that window, find the highest CLOSE that occurred more
         than `grace_days` (~2 months) ago -- the "eligible" high. The
         grace period exists so a stock that was simply a bit higher 3-6
         weeks ago (a normal, healthy pullback into its current entry
         zone) isn't penalized as "old resistance."
      3. If there's no eligible history yet (ticker too new / doesn't
         have 2 years of data before the grace cutoff), PASS by default.
      4. If today's close is AT OR BELOW that eligible old high, EXCLUDE
         -- there's still an unresolved old high overhead, so this isn't
         a genuine breakout yet, regardless of how deep or shallow the
         drawdown in between was.
      5. If today's close is ABOVE that eligible old high (a fresh 2-year
         high, reclaimed/broken out), PASS.

    Rejected earlier approach (NOT used here): a straight percentage-drop
    threshold from the old high (tried at 50%, 40%, then 25%) -- at every
    threshold, some visually-still-choppy/range-bound names slipped
    through as false passes (e.g. SSD: a strong 2023 uptrend followed by
    nearly two years of sideways chop between ~150-210, which the 6-month
    momentum screen happened to catch mid-upswing within that same tired
    range). A pure "is there ANY unresolved old high" rule, with no
    threshold, was adopted instead because it doesn't depend on getting a
    drawdown-percentage cutoff exactly right.

    Returns a dict with 'verdict' ('pass', 'pass (no eligible history
    yet)', or 'EXCLUDE'), plus the old high price/date and current price
    for reference, or None if there isn't even 100 days of history to
    evaluate.
    """
    as_of_date = pd.Timestamp(as_of_date)
    window_start = as_of_date - pd.Timedelta(days=lookback_years * 365)
    grace_cutoff = as_of_date - pd.Timedelta(days=grace_days)
    hist = df[(df['Date'] >= window_start) & (df['Date'] <= as_of_date)].copy()
    if len(hist) < 100:
        return None
    eligible = hist[hist['Date'] <= grace_cutoff]  # highs older than the grace period
    current_price = hist['Close'].iloc[-1]
    if len(eligible) == 0:
        return {'verdict': 'pass (no eligible history yet)'}
    old_high = eligible['Close'].max()
    old_high_date = eligible.loc[eligible['Close'].idxmax(), 'Date']
    verdict = 'EXCLUDE' if current_price <= old_high else 'pass'
    return {'verdict': verdict, 'old_high': round(old_high, 2), 'old_high_date': old_high_date.date(),
            'current_price': round(current_price, 2)}


def run_overhead_resistance_15name_validation():
    """
    Reproduces the exact 15-name validation run recorded in
    MA_Respect_Redesign_Notes.txt:
      EXCLUDE: MAN, PPC, TRU, MZTI, FLR, KD, PK, ANIP, RES, EFC, SSD,
               CTRE, CNM
      PASS:    CDP, FCF
    as of 2026-08-19.
    """
    all_15 = ['MAN', 'PPC', 'TRU', 'MZTI', 'FLR', 'KD', 'PK', 'ANIP', 'RES',
              'CDP', 'EFC', 'FCF', 'SSD', 'CTRE', 'CNM']
    as_of = '2026-08-19'
    print(f"{'Ticker':6s} {'Verdict':10s} old_high  old_high_date   current")
    for t in all_15:
        df = load(t)
        r = overhead_resistance_check(df, as_of)
        print(f"{t:6s} {r['verdict']:10s} {r.get('old_high', '-')}   "
              f"{r.get('old_high_date', '-')}   {r.get('current_price', '-')}")


# =========================================================================
# CHECK 2: SMOOTHNESS CHECK (bottom-left-to-top-right path quality)
# =========================================================================
def r_squared_trend(df, as_of_date, lookback=126):
    """
    FINAL, locked-in smoothness measure. Computes the R-squared of an
    ordinary linear regression fit to the daily closing price over the
    lookback window (126 trading days, ~6 months -- matching the momentum
    screen's own 6-month performance window).

    Mechanically: x = trading-day index (0, 1, 2, ... within the window),
    y = daily close. R2 = (correlation of x and y)^2. A value near 1.0
    means the closes fall almost exactly on a straight trend line (a
    clean, direct climb); a low value means the price path wandered a lot
    relative to any single straight-line trend (repeated big up/down
    cycles).

    This is explicitly NOT measuring whether net progress was made over
    the window (the 6-month 30-500% performance filter upstream already
    guarantees that) -- purely the QUALITY/DIRECTNESS of the path that
    produced the gain. Dave's own words on what this is meant to catch:
    stocks with a pattern of "large run-up / large run-down / large
    run-up" cycles, which are "very hard to manage risk on... and get a
    clean entry" -- as opposed to a fairly steady bottom-left-to-top-right
    climb.

    Rejected earlier approach (NOT used here): reusing the ATR-variability
    formula from the Trend Efficiency whipsaw check, just stretched from a
    20-day to a 126-day lookback, on the theory it might be "the same
    check at a larger horizon." This failed a direct test: SSD (Dave's
    known bad 2-year chopper) actually scored the LOWEST (best/calmest)
    ATR variability of the test group, even better than a known-good name
    -- because ATR variability measures day-to-day candle jumpiness, not
    multi-month directional consistency. A stock can have very calm daily
    candles while still going nowhere in a 2-year sideways range. R-squared
    was adopted instead because it directly measures path-straightness
    over the whole window, not daily jumpiness.

    Returns None if there isn't at least ~70% of the lookback window's
    worth of history available (e.g. a very new ticker).
    """
    as_of_date = pd.Timestamp(as_of_date)
    hist = df[df['Date'] <= as_of_date].tail(lookback)
    if len(hist) < lookback * 0.7:
        return None
    y = hist['Close'].values
    x = np.arange(len(y))
    corr = np.corrcoef(x, y)[0, 1]
    return round(corr ** 2, 3)


SMOOTHNESS_CUTOFF = 0.85  # PASS if R2 >= 0.85, EXCLUDE if R2 < 0.85 -- locked in


def pct_change(df, as_of_date, lookback=126):
    """6-month (126-trading-day) percent price change, used only to
    reproduce the momentum screen's own 20-500% performance filter when
    building a validation sample below -- not part of the smoothness
    check's own pass/fail logic."""
    as_of_date = pd.Timestamp(as_of_date)
    hist = df[df['Date'] <= as_of_date].tail(lookback)
    if len(hist) < lookback * 0.7:
        return None
    return round((hist['Close'].iloc[-1] / hist['Close'].iloc[0] - 1) * 100, 1)


def run_smoothness_112name_validation():
    """
    Reproduces the exact large-sample validation run recorded in
    MA_Respect_Redesign_Notes.txt: random.seed(23), a random 400-ticker
    draw from the full universe, filtered down to names already passing
    a 6-month 20-500% performance band (a loosened stand-in for the
    momentum screen's own 30-500% band, used here just to build a
    representative test sample -- not a change to the real screen).

    Recorded result (as of 2026-08-19), reproduced by this exact code:
      total qualifying names: 112
      ABOVE 0.85 (n=21): top 5 by R2 were GEO (0.951), CORT (0.932),
        ARMK (0.931), SCSC (0.919), MSM (0.918)
      0.70-0.85 near-miss zone (n=34): top 5 were MAC (0.839),
        CVLT (0.836), RAL (0.832), HQY (0.830), RS (0.829)
      fraction passing 0.85 cutoff: 21/112 = 18.8%
    This is the run Dave reviewed and confirmed the 0.85 cutoff against --
    notably, GEO, CORT, and ARMK were all tickers Dave was actively
    holding as live trades at the time, giving real-world confirmation
    that the cutoff placement made sense.
    """
    files = [f.replace('_1d_data.csv', '') for f in os.listdir(DATA_DIR) if f.endswith('_1d_data.csv')]

    random.seed(23)
    as_of = '2026-08-19'
    sample_tickers = random.sample(files, 400)

    results = []
    for t in sample_tickers:
        try:
            df = load(t)
            r2 = r_squared_trend(df, as_of)
            chg = pct_change(df, as_of)
            if r2 is not None and chg is not None and 20 < chg < 500:
                results.append({'ticker': t, 'r2': r2, 'pct_chg_6mo': chg})
        except Exception:
            continue

    res_df = pd.DataFrame(results)
    print("total qualifying names:", len(res_df))

    above = res_df[res_df['r2'] >= SMOOTHNESS_CUTOFF].sort_values('r2', ascending=False)
    just_below = res_df[(res_df['r2'] >= 0.70) & (res_df['r2'] < SMOOTHNESS_CUTOFF)].sort_values('r2', ascending=False)

    print()
    print("ABOVE 0.85 (n=%d):" % len(above))
    print(above.head(5).to_string(index=False))
    print()
    print("0.70-0.85, just missed (n=%d):" % len(just_below))
    print(just_below.head(5).to_string(index=False))
    print()
    print(f"fraction passing 0.85 cutoff: {len(above)}/{len(res_df)} = {len(above) / len(res_df) * 100:.1f}%")


# =========================================================================
# DECISION: NO SEPARATE GAP/REPRICING CHECK AT THIS SCREENING STAGE
# =========================================================================
# Tested whether the 0.85 R-squared smoothness check already implicitly
# catches large single-day gaps. Result: NO -- of 25 sample names passing
# R2>=0.85, 6 still contained a single-day gap >10%, 3 contained a gap
# >15% (VSTS 26.7%, FA 21.6%, GEO 15.5%). Mechanically this makes sense: a
# single sharp one-day jump barely dents R-squared if the rest of the
# window trends smoothly around it.
#
# However, Dave reviewed these specific names (GEO, FA, VSTS are all names
# he has personally traded) and explicitly does NOT want a separate gap
# check added at this screening-layer horizon. His distinction: these are
# earnings/news-driven pops of a normal magnitude (~15-27%) within an
# otherwise healthy uptrend -- fundamentally different from what he
# actually intends to screen out, a repricing event (e.g. a stock
# re-rating from $10 to $25 overnight, more like 100%+ overnight). That
# extreme-repricing scenario did not appear in this sample. Dave does not
# want this layer over-engineered further.
#
# DECISION: the pre-watchlist screening layer is considered COMPLETE with
# exactly these two checks (overhead-resistance + smoothness). No
# gap/repricing check was added at this stage. (A separate, existing
# 40-day gap check inside the Trend Efficiency scorecard attribute is a
# different concern at a different horizon and was not touched here.)
