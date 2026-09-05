"""
sim.py -- Trailing-stop trade simulator for the 50-day MA pullback system.

WHAT THIS FILE DOES
--------------------
Given one ticker's daily price data and a single entry (a date + entry price +
initial risk-per-share), this simulates how a trade plays out day by day under
one of five different trailing-stop "rules" (10-day MA, 20-day MA, a hybrid of
the two, an ADR-adaptive version, or a ratcheting swing-low stop), and returns
the exit date, exit reason, and the realized result in R-multiples (R = the
initial dollar risk per share).

This is the reconstructed, last-known-good version of the file as it existed
at the end of the 2026-08-17 trail-rule backtest session (after an earlier
grace-period bug had already been fixed in that same session). It was never
saved as a standalone file at the time -- it only lived in that session's
temporary sandbox -- so this copy was rebuilt from the transcript for use in
later work. A real entry-day stop-check bug was found in this version by Dave while
testing 3%/5%/7% max-loss sizing variants against this simulator. That bug
is now FIXED as of 2026-09-03 (see simulate_trail() below) - the loop now
starts at entry_idx itself and explicitly checks the entry day's own Low
against the initial stop before any trail logic can run.

HOW A TRADE IS SIZED (context, not code you need to change)
-------------------------------------------------------------
- Entry price = the 50-day moving average value on the entry date (this
  system enters on pullbacks to the 50 DMA).
- Initial risk-per-share = entry price x min(cap_pct, that ticker's 10-day
  ADR% at entry). ADR = Average Daily Range, a volatility measure. As of
  the 2026-09-03 resweep (on the fixed simulator, across 253 events),
  cap_pct = 7% is the recommended value - it outperformed both the
  original 5% default and a tighter 3% test across the full dataset. See
  Cap_Percent_and_Trail_Rule_Resweep_Findings_v1.md for the full writeup.
- Initial stop = entry price - risk-per-share.
- "R" = one unit of that initial risk. A trade that gains 2R made twice what
  it risked; a trade that hits its initial stop loses 1R (in this simplified
  model, before commissions/slippage).

THE FIVE TRAIL RULES (what get_trail_line() computes each day)
-----------------------------------------------------------------
- '10ma'          : trail stop = the 10-day simple moving average.
- '20ma'          : trail stop = the 20-day simple moving average.
- 'hybrid_tight'  : each day, use whichever of the 10ma/20ma is HIGHER
                    (i.e. tighter to price), so the stop is the more
                    conservative of the two on any given day.
- 'adr_adaptive'  : decided ONCE at entry -- if the ticker's ADR10 at entry
                    was below adr_threshold (10% by default), use the 10ma
                    for the whole trade; otherwise use the 20ma for the
                    whole trade. This choice never updates mid-trade.
- 'swing_low'     : a ratcheting stop at the most recent CONFIRMED pivot
                    low (a low that's lower than the 2 days before AND the
                    2 days after it -- see update_swing_low()). Because
                    confirming a pivot low requires 2 future days of data,
                    this stop only "sees" a new low 2 days after it forms,
                    and it only ever moves up, never down.

WITH-PARTIAL vs NO-PARTIAL (the take_partial flag)
-----------------------------------------------------
- take_partial=True ("with partial"): as soon as the trade's HIGH touches
  1.5R intraday, sell 33% of the position, move the stop to breakeven, and
  let the trailing rule govern the remaining 67%. Until that 1.5R touch
  happens, the ONLY thing that can end the trade is the initial hard stop
  (the trailing rule doesn't apply yet).
- take_partial=False ("no partial"): hold the full position. The trailing
  rule doesn't "arm" until the trade CLOSES (not just touches) at or above
  1.5R (grace_R, default 1.5). Until then, only the initial hard stop can
  end the trade. This grace period was added specifically to stop trades
  from being falsely knocked out by one noisy down-day very early in the
  trade, before the trend had a chance to establish itself.

Across both variants, once the trailing rule is "live" (partial taken, or
grace period cleared), each remaining day checks, in order: (1) has the
close broken below the trail line? if so, exit at that close. (2) otherwise,
has the low touched the hard stop price? if so, exit at the stop. Whichever
happens first on a given day ends the trade.

RETURN VALUE
------------
simulate_trail() returns a dict with:
  - exit_date    : the date the trade closed out (or the last available
                    date, if the trade never exited during the data window)
  - exit_reason  : 'initial_stop' (hit the original hard stop before the
                    trail ever armed), '<rule>_trail' (closed below the
                    trailing line), 'stop' (touched the hard stop after the
                    trail was armed), or 'still_open' (ran out of price data
                    with the trade never exiting)
  - realized_R   : the total result in R-multiples, accounting for any
                    partial already banked
"""

import pandas as pd
import numpy as np


def load_ticker(path):
    """
    Load one ticker's daily OHLCV CSV and compute the moving averages and
    volatility measure the trail rules need.

    Adds four columns to the raw price data:
      - MA10, MA20, MA50 : simple moving averages of the Close price
      - ADR10            : 10-day average daily range, as a percentage
                            ( (High/Low - 1) * 100, averaged over 10 days ).
                            This is the volatility measure used both to size
                            the initial stop and to drive the adr_adaptive
                            trail rule.
    """
    df = pd.read_csv(path)
    df['Date'] = pd.to_datetime(df['Date'])
    df['Volume'] = df['Volume'].astype(str).str.replace(',', '').astype(int)
    df = df.sort_values('Date').reset_index(drop=True)
    df['MA10'] = df['Close'].rolling(10).mean()
    df['MA20'] = df['Close'].rolling(20).mean()
    df['MA50'] = df['Close'].rolling(50).mean()
    df['ADR10'] = ((df['High'] / df['Low'] - 1) * 100).rolling(10).mean()
    return df


def get_trail_line(df, i, rule, entry_idx, adr_threshold, swing_state):
    """
    Return today's trailing-stop LINE (a price level) for the chosen rule.
    This is just "what is the line today" -- it does NOT check whether
    price has broken it; simulate_trail() does that comparison itself.

    rule           : which of the five trail rules to use (see file header)
    entry_idx      : the row index of the entry date (needed by
                      adr_adaptive, which locks in its ADR reading at entry
                      and never re-checks it)
    adr_threshold  : the ADR% cutoff used only by 'adr_adaptive' to decide
                      10ma vs 20ma for the whole trade
    swing_state     : a small dict (see update_swing_low below) tracking the
                      most recent confirmed pivot low, used only by
                      'swing_low'
    """
    row = df.loc[i]
    if rule == '10ma':
        return row['MA10']
    elif rule == '20ma':
        return row['MA20']
    elif rule == 'hybrid_tight':
        # Use whichever MA is higher (i.e. closer to / tighter against
        # price) on this specific day.
        m10, m20 = row['MA10'], row['MA20']
        vals = [v for v in [m10, m20] if not pd.isna(v)]
        return max(vals) if vals else np.nan
    elif rule == 'adr_adaptive':
        # NOTE: this reads the ADR10 value AT ENTRY ONLY, every single day
        # of the trade -- it does not update as the trade progresses.
        adr = df.loc[entry_idx, 'ADR10']
        return row['MA10'] if adr < adr_threshold else row['MA20']
    elif rule == 'swing_low':
        return swing_state.get('confirmed_low', np.nan)


def update_swing_low(df, i, swing_state):
    """
    Check whether the price 2 days ago (i - lookback) qualifies as a
    CONFIRMED pivot low -- i.e. its Low was less than or equal to the Low
    of the 2 days before it AND the 2 days after it. If so, and if it's
    higher than the current confirmed low being tracked, ratchet the
    tracked stop up to this new pivot low.

    Because confirming a pivot low needs 2 days of price AFTER the
    candidate day, a new low doesn't become visible to the simulator until
    2 trading days after it actually printed. The stop only ever moves up
    (or stays the same) -- it never moves back down once ratcheted.

    This function mutates swing_state in place; it has no return value.
    """
    lookback = 2
    if i - lookback < 0:
        return
    candidate_idx = i - lookback
    candidate_low = df.loc[candidate_idx, 'Low']
    left = df.loc[max(0, candidate_idx - lookback):candidate_idx - 1, 'Low']
    right = df.loc[candidate_idx + 1:candidate_idx + lookback, 'Low']
    if len(left) == lookback and len(right) == lookback:
        if candidate_low <= left.min() and candidate_low <= right.min():
            current = swing_state.get('confirmed_low', -np.inf)
            if candidate_low > current:
                swing_state['confirmed_low'] = candidate_low


def simulate_trail(df, entry_idx, entry_price, risk_per_share, rule='20ma', take_partial=False,
                          adr_threshold=10.0, grace_R=1.5):
    """
    Walk a single trade forward day by day from entry and determine how and
    when it exits, under the chosen trail rule and partial/no-partial mode.

    FIX APPLIED 2026-09-03 (confirmed final): the loop now starts at
    entry_idx itself (not entry_idx + 1), and the entry day is explicitly
    checked against the initial hard stop BEFORE any trail logic runs -
    the trail can never be armed on the entry day itself, since arming
    requires either a 1.5R touch or a 1.5R close, neither of which is
    knowable "as of" entry. This replaces the ORIGINAL buggy version
    (which silently skipped the entry day and could miss a same-day
    stop-out) used to generate the pre-2026-09-03 Outcome_* columns.

      df               : the ticker's DataFrame, as returned by load_ticker()
      entry_idx         : row index of the entry date within df
      entry_price       : the entry fill price (50-day MA value at entry)
      risk_per_share     : initial risk in dollars/share (defines both the
                           initial stop distance and what "1R" means) -
                           computed by the CALLER as entry_price times the
                           lesser of (cap_pct, 10-day ADR pct at entry).
                           Recommended cap_pct = 0.07 (7 percent), per the
                           2026-09-03 resweep - see
                           Cap_Percent_and_Trail_Rule_Resweep_Findings_v1.md.
                           Recommended rule = '20ma', take_partial = False.
      rule              : one of '10ma','20ma','hybrid_tight',
                           'adr_adaptive','swing_low'
      take_partial       : True = take a 33% partial at 1.5R touch; False =
                           hold full size with a 1.5R-close grace period
                           before the trail arms (recommended: False)
      adr_threshold      : only used by the adr_adaptive rule
      grace_R            : the close-based R threshold that arms the trail
                           in no-partial mode (default 1.5R)
    """
    stop = entry_price - risk_per_share
    partial_taken = False
    remaining = 1.0
    realized_R = 0.0
    trail_unlocked = False
    swing_state = {}
    T1_price = entry_price + 1.5 * risk_per_share
    grace_price = entry_price + grace_R * risk_per_share

    # --- FIX: explicit entry-day check ---
    entry_row = df.loc[entry_idx]
    if entry_row['Low'] <= stop:
        exit_price = stop
        realized_R += (exit_price - entry_price) / risk_per_share
        return dict(exit_date=entry_row['Date'], exit_reason='initial_stop_entry_day',
                    realized_R=round(realized_R, 3))
    # entry day can still touch 1.5R for a partial in with_partial mode, or
    # close above grace for no_partial mode - handle those same as any other
    # day by including entry_idx in the main loop below.

    for i in range(entry_idx, len(df)):
        if i == entry_idx:
            # already checked the hard stop above; only check
            # partial/grace-arming logic on the entry day itself, no trail
            # checks yet (nothing to trail from until arming happens)
            row = df.loc[i]
            close, low, high = row['Close'], row['Low'], row['High']
            if rule == 'swing_low':
                update_swing_low(df, i, swing_state)
            if take_partial:
                if not partial_taken and high >= T1_price:
                    realized_R += 0.33 * 1.5
                    remaining = 0.67
                    partial_taken = True
                    stop = entry_price
                    trail_unlocked = True
            else:
                if not trail_unlocked and close >= grace_price:
                    trail_unlocked = True
            continue

        row = df.loc[i]
        close, low, high = row['Close'], row['Low'], row['High']

        if rule == 'swing_low':
            update_swing_low(df, i, swing_state)

        if take_partial:
            if not partial_taken and high >= T1_price:
                realized_R += 0.33 * 1.5
                remaining = 0.67
                partial_taken = True
                stop = entry_price
                trail_unlocked = True
            if not partial_taken:
                if low <= stop:
                    exit_price = stop
                    realized_R += (exit_price - entry_price) / risk_per_share
                    return dict(exit_date=row['Date'], exit_reason='initial_stop',
                                realized_R=round(realized_R, 3))
                continue
        else:
            if not trail_unlocked and close >= grace_price:
                trail_unlocked = True
            if not trail_unlocked:
                if low <= stop:
                    exit_price = stop
                    realized_R += (exit_price - entry_price) / risk_per_share
                    return dict(exit_date=row['Date'], exit_reason='initial_stop',
                                realized_R=round(realized_R, 3))
                continue

        trail_line = get_trail_line(df, i, rule, entry_idx, adr_threshold, swing_state)
        if not pd.isna(trail_line) and close < trail_line:
            exit_price = close
            frac = remaining if take_partial else 1.0
            realized_R += frac * (exit_price - entry_price) / risk_per_share
            return dict(exit_date=row['Date'], exit_reason=f'{rule}_trail',
                        realized_R=round(realized_R, 3))
        elif low <= stop:
            exit_price = stop
            frac = remaining if take_partial else 1.0
            realized_R += frac * (exit_price - entry_price) / risk_per_share
            return dict(exit_date=row['Date'], exit_reason='stop',
                        realized_R=round(realized_R, 3))

    last_close = df.iloc[-1]['Close']
    frac = 1.0 if (take_partial and not partial_taken) else (remaining if take_partial else 1.0)
    realized_R += frac * (last_close - entry_price) / risk_per_share
    return dict(exit_date=df.iloc[-1]['Date'], exit_reason='still_open',
                realized_R=round(realized_R, 3))


# ============================================================
# RECOMMENDED CONFIGURATION (enforced in code, not just comments)
# ============================================================
# Resweep completed 2026-09-03 across 253 events (see
# Cap_Percent_and_Trail_Rule_Resweep_Findings_v1.md for full detail):
#   - cap_pct    = 0.07 (7 percent) -- beat both 0.03 and the old 0.05
#                  default across the full dataset.
#   - rule       = '20ma' -- best performer at every cap level tested.
#   - take_partial = False -- no-partial beat with-partial on nearly
#                  every rule/cap combination tested.
# These are now the DEFAULTS in simulate_trail() above (rule and
# take_partial), and RECOMMENDED_CAP_PCT / compute_risk_per_share() below
# enforce the cap_pct recommendation in actual code, not just a comment,
# so a new conversation picking up this file does not need to remember
# any of this from a prior discussion.

RECOMMENDED_CAP_PCT = 0.07


def compute_risk_per_share(entry_price, adr10_pct_at_entry, cap_pct=RECOMMENDED_CAP_PCT):
    """
    Computes risk_per_share the correct, standard way: entry_price times
    the LESSER of cap_pct and the ticker's 10-day ADR percent at entry
    (adr10_pct_at_entry is expected as a percent, e.g. 4.5 for 4.5 percent,
    matching the ADR10 column produced by load_ticker()).

    Use this instead of hand-computing risk_per_share, so the cap
    percent recommendation is applied consistently rather than
    re-derived (or mis-remembered) each time this file is used.
    """
    adr_frac = adr10_pct_at_entry / 100.0
    risk_pct = min(cap_pct, adr_frac)
    return entry_price * risk_pct
