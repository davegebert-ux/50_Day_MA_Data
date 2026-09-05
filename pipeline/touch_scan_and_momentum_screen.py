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

FOUND BUG WHILE RECOVERING THIS SCRIPT: "price > 50-day MA" IS COMPUTED
BUT NEVER ACTUALLY APPLIED
----------------------------------------------------------------------------
Look at the loop below: it computes `price_above_50ma = row['Close'] >
row['SMA50']` but this variable is NEVER included in the final `if not
(...)` filter condition that decides whether a day passes the screen. It's
dead code -- calculated and then silently ignored. So even though "price >
50-day MA" is one of the eight documented screen criteria, THIS SCRIPT, as
actually run, does not enforce it. In practice this may not have mattered
much for touch DETECTION specifically, since the touch definition itself
(`row['Low'] <= row['SMA50'] <= row['High']`) already requires price to be
straddling the 50-day MA that day, which is a related but not identical
condition to closing above it. Still, this is worth deciding on
deliberately rather than leaving as an accidental gap when this pipeline
gets rebuilt/rerun end to end.

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
        if pd.isna(row['SMA50']) or pd.isna(row['SMA100']) or pd.isna(row['SMA200']) or pd.isna(row['ADX50']) or pd.isna(row['AvgVol10']) or pd.isna(row['Perf6mo']) or pd.isna(row['SMA50_prior5']):
            continue

        # Condition: SMA50 rising
        sma50_rising = row['SMA50'] > row['SMA50_prior5']
        # Condition: price > 50MA (screen requirement, checked at screen
        # time - general uptrend context)
        # *** FOUND BUG: this is computed but NEVER included in the filter
        # below -- see "FOUND BUG" note in the file header. As written,
        # this condition is NOT enforced. ***
        price_above_50ma = row['Close'] > row['SMA50']
        # Condition: SMA100 > SMA200 (long-term stack)
        stack_ok = row['SMA100'] > row['SMA200']
        # Condition: ADX(50) between 20 and 40
        adx_ok = 20 <= row['ADX50'] <= 40
        # Condition: avg 10-day volume > 1,000,000
        vol_ok = row['AvgVol10'] > 1_000_000
        # Condition: 6-month perf between 30% and 500%
        perf_ok = 30 <= row['Perf6mo'] <= 500

        # NOTE: price_above_50ma is deliberately left OUT of this
        # condition in the recovered code, exactly as it was actually
        # run. Flagging rather than silently fixing it -- see file header.
        if not (sma50_rising and stack_ok and adx_ok and vol_ok and perf_ok):
            continue

        # Touch definition: low of day <= SMA50 <= high of day (price
        # touched the MA intraday), OR close within a small tolerance
        # band of the MA (since we only have OHLC, not exact touch
        # confirmation)
        touched = (row['Low'] <= row['SMA50'] <= row['High'])

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
