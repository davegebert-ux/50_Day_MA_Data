"""
touch_scan_and_momentum_screen.py -- Daily 50-day MA touch-scan script,
including a coded reproduction of the momentum screen criteria.

WHAT THIS FILE IS
------------------
This is the script that generated the 456-event historical dataset (tickers
+ dates) that the entire scorecard project is scored and tested against. It
was recovered verbatim from the transcript of the 2026-08-19 large-sample
tuning session, where it was run inline in the sandbox rather than saved as
a standalone file. This is the FIRST time it has been saved as its own file.

It combines two of the three pipeline stages Dave asked about:
  1. A CODED REPRODUCTION of most of the TradingView momentum screen
     criteria (see "MOMENTUM SCREEN COVERAGE" below for the important
     gaps -- three criteria are NOT checked here).
  2. The actual daily 50-day MA touch-detection logic that builds the
     event list.

It assumes it's running against a folder of already-downloaded per-ticker
daily OHLCV CSVs (DATA_DIR below) for tickers that presumably already
cleared a TradingView export/screen of some kind -- this script re-derives
several of the screen's technical conditions from the price data itself
(so they're checked day-by-day, not just as of one screen date), but it is
NOT a from-scratch universe screen; it depends on DATA_DIR already
containing a reasonable candidate list of tickers.

MOMENTUM SCREEN COVERAGE -- WHAT THIS SCRIPT DOES AND DOES NOT CHECK
------------------------------------------------------------------------
The full documented momentum screen (per MA_Respect_Redesign_Notes.txt) is:
  market cap > $100M, TTM revenue growth YoY > 0%, 6-month price
  performance 30-500%, primary listing, SMA100 > SMA200, ADX(50) 20-40,
  avg 10-day volume > 1M shares, price > 50-day MA.

This script checks, day-by-day, from OHLCV data alone:
  - SMA100 > SMA200                              (stack_ok)
  - ADX(50) between 20 and 40                    (adx_ok)
  - avg 10-day volume > 1,000,000                (vol_ok)
  - 6-month price performance between 30-500%    (perf_ok)
  - SMA50 rising (day's SMA50 > SMA50 five trading days ago) -- this is
    an EXTRA condition not in the documented screen list, presumably
    added to approximate "still trending up" at the moment of touch.

This script CANNOT check, and does NOT check, three of the documented
criteria, because they require data this script never loads:
  - market cap > $100M
  - TTM revenue growth YoY > 0%
  - primary listing (i.e. not an ADR / secondary listing / etc.)
These three were most likely applied upstream, in TradingView itself,
when the original candidate ticker list was exported -- i.e. DATA_DIR is
assumed to already only contain tickers that cleared those three checks
in TradingView. If that export process/list isn't available anywhere,
this is a real gap: there's currently no code, anywhere, enforcing those
three conditions on an ongoing basis.

BUG FOUND AND FIXED 2026-09-11: "price > 50-day MA" WAS COMPUTED BUT NEVER
ACTUALLY APPLIED
----------------------------------------------------------------------------
Originally, this script computed `price_above_50ma = row['Close'] >
row['SMA50']` but never included it in the final `if not (...)` filter
condition that decides whether a day passes the screen -- it was dead
code, calculated and then silently ignored. So even though "price > 50-day
MA" is one of the eight documented screen criteria, this script, as
originally written, did not enforce it. Fixed 2026-09-11 as part of the
wide-universe historical backtesting rebuild, at Dave's explicit request,
since letting it through risked counting touches/trades that would not
have actually qualified in real trading, which would have distorted the
backtest statistics. This fix means historical touch counts from this
version will differ (likely be somewhat lower) than any prior run of this
script.

TOUCH DEFINITION
-----------------
A "touch" is any day where the 50-day MA value falls between that day's
Low and High (inclusive) -- i.e. the day's price range crossed the MA
level intraday. This is a looser definition than "closed exactly at the
MA"; it was chosen because the underlying data is daily OHLC only, with no
intraday tick data available to confirm an exact touch-and-hold.

OUTPUT
------
Writes two things:
  - /home/claude/touches_raw.pkl   (pickle, sandbox-local, temporary)
  - Historical_Touches_Raw.csv     (the actual saved deliverable)
Columns: Ticker, Date, Close, SMA50, ADX50, Perf6mo, AvgVol10, SMA100,
SMA200 -- i.e. the raw touch event plus the screen-condition values that
were true on that day, for later spot-checking.
"""

import pandas as pd
import numpy as np
import glob
import os

DATA_DIR = "/home/claude/data_pull/50_Day_MA_Data-claude-historical-stock-data-pull-3udz0v/data"
SPY_PATH = "/mnt/user-data/uploads/SPY_1d_data.csv"


def load_ticker(fpath):
    """Load one ticker's raw daily OHLCV CSV, sorted by date."""
    df = pd.read_csv(fpath)
    df['Date'] = pd.to_datetime(df['Date'])
    df = df.sort_values('Date').reset_index(drop=True)
    return df


def wilder_adx(df, period=50):
    """
    Compute Wilder's ADX (Average Directional Index) over the given period
    (50 days, to match the "ADX(50)" screen criterion). This is the
    standard textbook Wilder smoothing formula: directional movement (+DM,
    -DM) and true range are Wilder-smoothed, turned into +DI/-DI, then DX
    is derived from their normalized difference, and ADX is a further
    Wilder-smoothed average of DX. Returns an array aligned to df's rows,
    with NaN until enough history has accumulated (roughly 2x period).
    """
    high = df['High'].values
    low = df['Low'].values
    close = df['Close'].values
    n = len(df)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    tr = np.zeros(n)
    for i in range(1, n):
        up_move = high[i] - high[i - 1]
        down_move = low[i - 1] - low[i]
        plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0.0
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))

    def wilder_smooth(arr, period):
        sm = np.zeros(len(arr))
        sm[:] = np.nan
        if len(arr) <= period:
            return sm
        sm[period] = np.sum(arr[1:period + 1])
        for i in range(period + 1, len(arr)):
            sm[i] = sm[i - 1] - (sm[i - 1] / period) + arr[i]
        return sm

    tr_sm = wilder_smooth(tr, period)
    plus_dm_sm = wilder_smooth(plus_dm, period)
    minus_dm_sm = wilder_smooth(minus_dm, period)

    plus_di = 100 * (plus_dm_sm / tr_sm)
    minus_di = 100 * (minus_dm_sm / tr_sm)
    dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di)

    adx = np.full(n, np.nan)
    first_valid = period * 2
    if first_valid < n:
        adx[first_valid] = np.nanmean(dx[period + 1:first_valid + 1])
        for i in range(first_valid + 1, n):
            adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period
    return adx


# =========================================================================
# THE MOMENTUM SCREEN -- SINGLE SOURCE OF TRUTH  (ADDED 2026-09-14)
# =========================================================================
# Until today these eight criteria were written out TWICE: once in
# run_full_historical_scan() below, and again inline in orchestrator.py's
# find_new_signals_for_date(). That duplication is exactly how the
# price_above_50ma bug survived -- it was fixed here on 2026-09-11, but the
# orchestrator carried its own copy and went on running the unfixed screen
# in production until 2026-09-14. The backtest enforced a rule that live
# trading did not.
#
# Both callers now go through passes_momentum_screen(). There is one copy
# of the rules. Do not reintroduce a second.
#
# The caller is responsible for producing a row that already has SMA50,
# SMA100, SMA200, AvgVol10, ADX50, Perf6mo and SMA50_prior5 computed --
# how those columns get there differs between the full historical sweep
# (vectorised over the whole frame) and the single-date live check
# (trailing window up to target_date), and that difference is legitimate.
# The SCREEN ITSELF is what must not differ.

MOMENTUM_SCREEN_COLUMNS = ["SMA50", "SMA100", "SMA200", "AvgVol10",
                           "ADX50", "Perf6mo", "SMA50_prior5"]

# Screen thresholds -- named so they can be referenced in the architecture
# doc and changed in exactly one place.
ADX50_MIN, ADX50_MAX = 20, 40
AVG_VOL10_MIN = 1_000_000
PERF6MO_MIN, PERF6MO_MAX = 30, 500


def momentum_screen_detail(row):
    """
    Evaluate the 8-criterion momentum screen for a single prepared row.
    Returns a dict of criterion name -> bool. Useful for diagnosing why a
    given ticker/date passed or failed (e.g. the CLSK/NFLX/CLOV
    verification run).
    """
    return {
        "sma50_rising":    bool(row["SMA50"] > row["SMA50_prior5"]),
        "price_above_50ma": bool(row["Close"] > row["SMA50"]),
        "stack_ok":        bool(row["SMA100"] > row["SMA200"]),
        "adx_ok":          bool(ADX50_MIN <= row["ADX50"] <= ADX50_MAX),
        "vol_ok":          bool(row["AvgVol10"] > AVG_VOL10_MIN),
        "perf_ok":         bool(PERF6MO_MIN <= row["Perf6mo"] <= PERF6MO_MAX),
    }


def screen_inputs_ready(row):
    """True if every column the screen depends on is present and non-NaN."""
    return not pd.isna([row[c] for c in MOMENTUM_SCREEN_COLUMNS]).any()


def passes_momentum_screen(row):
    """
    The screen itself. Returns False if the inputs aren't ready yet
    (insufficient history for one of the moving averages), so callers can
    use this as a single guard.
    """
    if not screen_inputs_ready(row):
        return False
    return all(momentum_screen_detail(row).values())


def touched_50ma(row):
    """Touch definition: the day's range contains the 50-day MA."""
    return bool(row["Low"] <= row["SMA50"] <= row["High"])


def run_full_historical_scan():
    """
    FULL multi-year historical scan -- reproduces the original 456/1,476-event
    touch-detection run (loops over the whole DATA_DIR universe and the whole
    date range of each ticker). This is the ORIGINAL top-level script logic,
    wrapped into a callable function on 2026-09-05 so that importing this
    module (e.g. to reuse wilder_adx() elsewhere, such as in orchestrator.py)
    no longer triggers this full scan as a side effect of import.

    BUG FIX (2026-09-05): previously, this scan's logic lived directly at
    module level (not inside any function), which meant simply writing
    `from touch_scan_and_momentum_screen import wilder_adx` anywhere else in
    the codebase would silently execute this entire historical scan against
    this file's own hardcoded DATA_DIR -- confirmed during orchestrator.py's
    first test run, where it printed "Found 0 ticker files" (failing
    silently, since that hardcoded path doesn't exist in the test
    environment) as an unwanted side effect of an unrelated import. Wrapping
    this in a function, callable only when explicitly invoked (including via
    the `if __name__ == "__main__":` guard below when this file is run
    directly), fixes that -- importing wilder_adx (or anything else) from
    this module is now side-effect-free.

    NOT used by the daily orchestrator -- orchestrator.py's own
    find_new_signals_for_date() reimplements this same logic restricted to a
    single target date (see that function's docstring for why). This
    function remains for reproducing/re-running the original full historical
    scan on demand.
    """
    # Load SPY -- kept for potential relative-strength / 6mo-performance
    # context, but not actually required by any of the screen conditions
    # below as written.
    spy = load_ticker(SPY_PATH) if os.path.exists(SPY_PATH) else None

    files = sorted(glob.glob(os.path.join(DATA_DIR, "*_1d_data.csv")))
    print(f"Found {len(files)} ticker files")

    all_touches = []
    skipped_tickers = []

    for fi, fpath in enumerate(files):
        ticker = os.path.basename(fpath).replace("_1d_data.csv", "")
        df = load_ticker(fpath)
        if len(df) < 260:  # need enough history for SMA200 etc plus some runway
            skipped_tickers.append((ticker, "insufficient history", len(df)))
            continue

        df['SMA50'] = df['Close'].rolling(50).mean()
        df['SMA100'] = df['Close'].rolling(100).mean()
        df['SMA200'] = df['Close'].rolling(200).mean()
        df['AvgVol10'] = df['Volume'].rolling(10).mean()
        df['ADX50'] = wilder_adx(df, period=50)

        # 6-month performance ~ 126 trading days
        df['Perf6mo'] = (df['Close'] / df['Close'].shift(126) - 1) * 100

        # SMA50 slope: rising if higher than 5 days ago
        df['SMA50_prior5'] = df['SMA50'].shift(5)

        n = len(df)
        for i in range(200, n):
            row = df.iloc[i]

            # REFACTORED 2026-09-14: the eight criteria were written out
            # here AND again inline in orchestrator.py. Both now call
            # passes_momentum_screen() above -- one copy of the rules.
            # (This also subsumes the NaN-readiness guard and the
            # 2026-09-11 price_above_50ma fix.)
            if not passes_momentum_screen(row):
                continue

            # Touch definition: low of day <= SMA50 <= high of day (price
            # touched the MA intraday).
            touched = touched_50ma(row)

            if touched:
                all_touches.append({
                    'Ticker': ticker,
                    'Date': row['Date'],
                    'Close': row['Close'],
                    'SMA50': row['SMA50'],
                    'ADX50': row['ADX50'],
                    'Perf6mo': row['Perf6mo'],
                    'AvgVol10': row['AvgVol10'],
                    'SMA100': row['SMA100'],
                    'SMA200': row['SMA200'],
                })

    touches_df = pd.DataFrame(all_touches)
    print(f"Total qualifying touches found: {len(touches_df)}")
    print(f"Unique tickers with at least one touch: {touches_df['Ticker'].nunique() if len(touches_df) else 0}")
    print(f"Tickers skipped for insufficient history: {len(skipped_tickers)}")

    touches_df.to_pickle('/home/claude/touches_raw.pkl')
    touches_df.to_csv('/mnt/user-data/outputs/Historical_Touches_Raw.csv', index=False)
    print("saved")


if __name__ == "__main__":
    run_full_historical_scan()
