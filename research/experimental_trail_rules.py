"""
experimental_trail_rules.py -- three NEW trailing-stop rules Dave proposed,
built as a standalone module (does not modify sim.py) so they can be
tested against the existing 5 rules on equal footing, using the same
entry-day-stop-check and grace-period-arming logic as sim.simulate_trail().

THE THREE NEW RULES
--------------------
1. 'speed_adaptive' -- Dave's original idea was "fast stocks get a
   tighter trail, slow stocks get a looser one." Testing on the
   wide-universe sample found the opposite relationship: it's actually
   the CALM, large-cap, low-ADR names that get hurt by the (relatively)
   slow 20-day MA trail, since 20 days of small moves barely shifts the
   average, leaving the trail lagging far behind price. Volatile names
   didn't show a strong preference among the existing rules. So this
   rule flips the logic from Dave's first instinct: LOWER ADR10 (calmer
   stock) -> tighter trail (10-day MA); HIGHER ADR10 (choppier stock) ->
   looser trail (20-day MA). Threshold re-evaluated ONCE at entry only
   (matching how the existing adr_adaptive rule already works), not
   updated day to day.

2. 'graduated_tighten' -- Dave's "start tight, then loosen once the
   trade proves itself" idea. Uses the 10-day MA as the trail line while
   the trade has NOT yet reached a 1.5R gain (same grace_R threshold
   sim.py already uses to arm the trail at all), then switches to the
   20-day MA once the trade has closed at or above 1.5R. Intuition:
   protect against giving back an unrealized gain early, then give the
   trade more room once it's proven the trend is real.

3. 'atr_multiple' -- the SMB-style ATR trailing stop Dave asked about.
   Stop line = highest CLOSE since entry, minus (multiplier x ATR14).
   ATR14 is Wilder's Average True Range over 14 days (the standard
   period for this kind of stop, distinct from the ADR10 percent-range
   measure already used elsewhere in this codebase). Tested at four
   multiplier values: 1.0, 1.5, 2.0, and 3.0 (the last being the
   commonly-cited default for this style of stop, included as a
   real-world reference point since 1.0-1.25x, what Dave originally
   asked about, is unusually tight compared to standard practice).

All three are implemented to slot into the SAME simulate_trail_extended()
loop below, which is a copy of sim.simulate_trail()'s control flow
(entry-day check, grace-period arming, no-partial mode only -- matching
the current recommended take_partial=False) but calls get_trail_line_extended()
instead, which handles the 5 original rules AND these 3 new ones.
"""

import pandas as pd
import numpy as np
import sys
sys.path.insert(0, "/mnt/user-data/outputs")
import sim  # reuse update_swing_low, compute_risk_per_share, etc.


def compute_atr14(df):
    """Wilder's ATR, 14-day period, standard for ATR-multiple trailing stops."""
    high, low, close = df['High'], df['Low'], df['Close']
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False).mean()
    return atr


def get_trail_line_extended(df, i, rule, entry_idx, swing_state, grace_price,
                              atr_multiplier=2.0, speed_threshold=5.0):
    """
    Extends sim.get_trail_line() with the 3 new rules. Falls back to the
    original 5 rules unchanged for backward compatibility.
    """
    row = df.loc[i]

    if rule == 'speed_adaptive':
        # Evaluated once at entry, like the existing adr_adaptive rule.
        adr_at_entry = df.loc[entry_idx, 'ADR10']
        # LOWER ADR (calmer) -> tighter (10ma). HIGHER ADR (choppier) -> looser (20ma).
        return row['MA10'] if adr_at_entry < speed_threshold else row['MA20']

    elif rule == 'graduated_tighten':
        # Tight (10ma) until the trade has closed at/above the grace price
        # (1.5R gain); loose (20ma) after that.
        has_proven_itself = df.loc[entry_idx:i, 'Close'].max() >= grace_price
        return row['MA20'] if has_proven_itself else row['MA10']

    elif rule.startswith('atr_'):
        # rule format: 'atr_1.0x', 'atr_1.5x', 'atr_2.0x', 'atr_3.0x'
        mult = float(rule.replace('atr_', '').replace('x', ''))
        highest_close_since_entry = df.loc[entry_idx:i, 'Close'].max()
        atr_today = row['ATR14']
        if pd.isna(atr_today):
            return np.nan
        return highest_close_since_entry - (mult * atr_today)

    else:
        # Fall back to the 5 original rules, unchanged.
        return sim.get_trail_line(df, i, rule, entry_idx, 10.0, swing_state)


def simulate_trail_extended(df, entry_idx, entry_price, risk_per_share, rule,
                              grace_R=1.5, atr_multiplier=2.0, speed_threshold=5.0):
    """
    Same control flow as sim.simulate_trail() with take_partial=False
    (the current recommended mode), but dispatches to
    get_trail_line_extended() so the 3 new rules can be tested
    side by side with the 5 original ones under identical mechanics.
    """
    stop = entry_price - risk_per_share
    realized_R = 0.0
    trail_unlocked = False
    swing_state = {}
    grace_price = entry_price + grace_R * risk_per_share

    entry_row = df.loc[entry_idx]
    if entry_row['Low'] <= stop:
        exit_price = stop
        realized_R += (exit_price - entry_price) / risk_per_share
        return dict(exit_date=entry_row['Date'], exit_reason='initial_stop_entry_day',
                    realized_R=round(realized_R, 3))

    for i in range(entry_idx, len(df)):
        row = df.loc[i]
        close, low, high = row['Close'], row['Low'], row['High']

        if rule == 'swing_low':
            sim.update_swing_low(df, i, swing_state)

        if i == entry_idx:
            if not trail_unlocked and close >= grace_price:
                trail_unlocked = True
            continue

        if not trail_unlocked and close >= grace_price:
            trail_unlocked = True
        if not trail_unlocked:
            if low <= stop:
                exit_price = stop
                realized_R += (exit_price - entry_price) / risk_per_share
                return dict(exit_date=row['Date'], exit_reason='initial_stop',
                            realized_R=round(realized_R, 3))
            continue

        trail_line = get_trail_line_extended(df, i, rule, entry_idx, swing_state,
                                               grace_price, atr_multiplier, speed_threshold)
        if not pd.isna(trail_line) and close < trail_line:
            exit_price = close
            realized_R += (exit_price - entry_price) / risk_per_share
            return dict(exit_date=row['Date'], exit_reason=f'{rule}_trail',
                        realized_R=round(realized_R, 3))
        elif low <= stop:
            exit_price = stop
            realized_R += (exit_price - entry_price) / risk_per_share
            return dict(exit_date=row['Date'], exit_reason='stop',
                        realized_R=round(realized_R, 3))

    last_close = df.iloc[-1]['Close']
    realized_R += (last_close - entry_price) / risk_per_share
    return dict(exit_date=df.iloc[-1]['Date'], exit_reason='still_open',
                realized_R=round(realized_R, 3))
