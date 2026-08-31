"""
50-Day MA Bounce Scorecard - v2
Fully mechanical, pre-entry-only scoring functions.
Each function takes OHLCV data (+ SPY for RS) up to and including the entry date
and returns a raw measurement + a 0-5 point score.

Input data convention: pandas DataFrame with columns
    Date (datetime, ascending order), Open, High, Low, Close, Volume
Entry date = the date the resting limit order at the 50-day MA filled.
All lookbacks are computed using only data on or before the entry date (no lookahead).

v2 CHANGELOG (merged 2026-08-22, see MA_Respect_Redesign_Notes.txt for full
redesign history/validation of each):
  - Trend Efficiency: replaced net-displacement/path-length ratio with
    Gap Check + ATR Variability (min-combined).
  - MA Respect: replaced fixed-60-day/best-of-4-MA/auto-5 logic with
    trend-start-anchored, ADR-normalized, 3-component (depth/smoothness/
    MA50) min-combined scoring.
  - Relative Strength: replaced 10-day endpoint ratio change with 60-day
    pullback-from-peak-ratio.
  - Trend Character, Institutional Distribution, Controlled Arrival, ADX
    Trend Strength, Long-Term Structure, Earnings Proximity are UNCHANGED
    from v1 - not yet reviewed/redesigned in this pass.
"""

import pandas as pd
import numpy as np


def load_ohlcv(path):
    df = pd.read_csv(path)
    df['Date'] = pd.to_datetime(df['Date'])
    df['Volume'] = df['Volume'].astype(str).str.replace(',', '').astype(float)
    df = df.sort_values('Date').reset_index(drop=True)
    return df


def slice_to_entry(df, entry_date, lookback_days=None):
    """Return rows on or before entry_date, optionally limited to last N trading days."""
    entry_date = pd.Timestamp(entry_date)
    sub = df[df['Date'] <= entry_date].copy().reset_index(drop=True)
    if lookback_days is not None:
        sub = sub.tail(lookback_days).reset_index(drop=True)
    return sub


# ============================================================
# 1. EARNINGS PROXIMITY (0-5 pts, avg of 1a runway... reworked per Dave's feedback)
# ============================================================
def score_earnings_proximity(entry_date, prior_earnings_date, next_earnings_date):
    """
    Reworked per Dave's feedback: no longer penalizes pre-earnings drift.
    1a. Timing bucket (informational tag, not scored as better/worse on its own):
        - Pre-earnings drift: prior earnings > ~5 trading days before entry, no post-earnings gap involved
        - Post-earnings gap reaction: entry within 1-2 trading days AFTER prior earnings
        - Unrelated: prior earnings far in the past, no proximity either side
    1b. Runway to next earnings (the actual risk measure - scored):
        >20 trading days runway   = 5 pts
        11-20 trading days        = 3 pts
        6-10 trading days         = 1 pt
        <=5 trading days          = 0 pts
        No next earnings date known = 5 pts (assume ample runway, flagged in notes)
    Score = runway score only. Timing bucket is returned as a tag for review.
    """
    entry_date = pd.Timestamp(entry_date)
    prior = pd.Timestamp(prior_earnings_date) if pd.notna(prior_earnings_date) else None
    nxt = pd.Timestamp(next_earnings_date) if pd.notna(next_earnings_date) else None

    # Tag: timing relative to prior earnings
    tag = "unrelated"
    days_since_prior = None
    if prior is not None:
        days_since_prior = np.busday_count(prior.date(), entry_date.date())
        if 0 <= days_since_prior <= 2:
            tag = "post-earnings gap reaction"
        elif 2 < days_since_prior <= 15:
            tag = "pre-earnings drift window"  # drifting toward the touch not long after last report, still fine
        else:
            tag = "unrelated"

    # Runway score
    if nxt is None:
        runway_days = None
        runway_score = 5  # ample assumption, flagged
    else:
        runway_days = np.busday_count(entry_date.date(), nxt.date())
        if runway_days > 20:
            runway_score = 5
        elif runway_days >= 11:
            runway_score = 3
        elif runway_days >= 6:
            runway_score = 1
        else:
            runway_score = 0

    return {
        'raw_days_since_prior_earnings': days_since_prior,
        'raw_runway_trading_days': runway_days,
        'timing_tag': tag,
        'score': runway_score
    }


# ============================================================
# 2. TREND EFFICIENCY v2 (0-5 pts)
# ============================================================
# Replaces the old net-displacement/path-length efficiency ratio, which
# could not tell a genuinely smooth move from a large gap (DOCN/CALY/GME
# pattern) or from a smooth rally-then-pullback round-trip (THC pattern).
# See MA_Respect_Redesign_Notes.txt for the full redesign history.
# Split into two direction-agnostic checks, combined via min() (weakest
# component wins - same pattern used in MA Respect v5 below):
#   - Gap Check: catches a violent single-day move (up or down) anywhere
#     distorting an otherwise fine chart.
#   - ATR Variability: catches erratic/whipsaw candle behavior with no
#     single outlier day (the CXW problem case).
# KNOWN LIMITATION (accepted, not fixed): a single real event (earnings
# gap, repricing) sitting inside either lookback window can drag both
# sub-scores down even when the stock's ongoing behavior is excellent -
# confirmed on 3 independent examples (CORT, CFFN, LXP - see notes).
def score_gap_check(df, entry_date, lookback=40):
    sub = slice_to_entry(df, entry_date, lookback_days=lookback + 1)
    if len(sub) < lookback + 1:
        return None, None
    prior_close = sub['Close'].shift(1)
    gap_pct = (sub['Open'] - prior_close).abs() / prior_close * 100
    max_gap = gap_pct.tail(lookback).max()

    if max_gap < 3:
        score = 5
    elif max_gap < 5:
        score = 4
    elif max_gap < 8:
        score = 3
    elif max_gap < 12:
        score = 2
    elif max_gap < 18:
        score = 1
    else:
        score = 0

    return score, round(float(max_gap), 2)


def score_atr_variability(df, entry_date, lookback=20, atr_period=10):
    sub = slice_to_entry(df, entry_date, lookback_days=lookback + atr_period + 5)
    if len(sub) < lookback + atr_period:
        return None, None
    prior_close = sub['Close'].shift(1)
    tr = pd.concat([
        sub['High'] - sub['Low'],
        (sub['High'] - prior_close).abs(),
        (sub['Low'] - prior_close).abs()
    ], axis=1).max(axis=1)
    atr = tr.rolling(atr_period).mean()
    atr_pct = (atr / sub['Close'] * 100).tail(lookback).dropna()
    if len(atr_pct) < lookback * 0.8:
        return None, None
    cv = atr_pct.std() / atr_pct.mean()

    if cv <= 0.08:
        score = 5
    elif cv <= 0.12:
        score = 4
    elif cv <= 0.16:
        score = 3
    elif cv <= 0.20:
        score = 2
    elif cv <= 0.28:
        score = 1
    else:
        score = 0

    return score, round(float(cv), 3)


def score_trend_efficiency(df, entry_date):
    gap_score, max_gap_pct = score_gap_check(df, entry_date)
    atr_score, atr_cv = score_atr_variability(df, entry_date)

    if gap_score is None or atr_score is None:
        return {
            'gap_check_score': gap_score, 'atr_variability_score': atr_score,
            'score': None, 'note': 'insufficient history'
        }

    score = min(gap_score, atr_score)
    return {
        'raw_max_gap_pct': max_gap_pct,
        'raw_atr_cv': atr_cv,
        'gap_check_score': gap_score,
        'atr_variability_score': atr_score,
        'score': score,
    }


# ============================================================
# 3. MOVING AVERAGE RESPECT v5 (0-5 pts)
# ============================================================
# Replaces the old fixed-60-day-window / best-of-4-MA (incl. MA100) /
# "never violated = automatic 5" logic, which rewarded mere ABSENCE of a
# violation the same as genuine disciplined respect (EXEL/M/AHR-type chop-
# without-touching and GME-type gap-away scored identically to real clean
# trend-followers). See MA_Respect_Redesign_Notes.txt for full history.
#
# New approach: MA100 dropped. MA10/MA20 are treated as LEADING INDICATORS
# for how MA50 (the line actually traded) will likely be respected, not as
# independent best-of-N alternatives. Window is anchored to the actual
# TREND START (most recent MA50 reclaim held >=95% of the time since, with
# a 40-day floor) instead of a fixed lookback - this correctly excludes
# pre-trend history (see GEO case) while "no clean trend-start found" is a
# valid, expected result for genuinely choppy names (see AHR case), not a
# bug. Final score = min() across three components - a name can't score
# well by being good on only one dimension:
#   1. Fast-line depth: worst ADR-normalized violation depth (10%-capped,
#      so extremely volatile names like GME can't buy a pass via their own
#      volatility) across MA10/MA20.
#   2. Trend-window smoothness: R^2 of daily closes over the anchored
#      window (same formula as the pre-watchlist smoothness screen).
#   3. MA50 violation profile: ADR-normalized depth/frequency of actual
#      MA50 violations since the anchor.
def _find_trend_start(df, entry_date, max_lookback=252, min_days_since=40):
    df = df.copy()
    df['MA50'] = df['Close'].rolling(50).mean()
    entry_date = pd.Timestamp(entry_date)
    sub = df[df['Date'] <= entry_date].tail(max_lookback).dropna(subset=['MA50']).reset_index(drop=True)
    if len(sub) < min_days_since:
        return None

    above = sub['Close'] > sub['MA50']
    reclaim_idxs = []
    for i in range(1, len(above)):
        if above.iloc[i] and not above.iloc[i - 1]:
            reclaim_idxs.append(i)
    if len(above) > 0 and above.iloc[0]:
        reclaim_idxs.insert(0, 0)

    best = None
    for idx in reversed(reclaim_idxs):
        days_since = len(sub) - 1 - idx
        if days_since < min_days_since:
            continue
        post = sub.iloc[idx:]
        pct_above = (post['Close'] > post['MA50']).mean() * 100
        if pct_above >= 95:
            best = {'date': sub['Date'].iloc[idx], 'days_since': days_since,
                    'pct_time_above_since': round(float(pct_above), 1)}
            break
    return best


def _adr_normalized_violation_profile(df, start_date, entry_date, ma_period, adr_cap=10.0):
    df = df.copy()
    df['DailyRange_pct'] = (df['High'] - df['Low']) / df['Close'] * 100
    df['ADR10_pct'] = df['DailyRange_pct'].rolling(10).mean()
    df['MA'] = df['Close'].rolling(ma_period).mean()
    start_date = pd.Timestamp(start_date)
    entry_date = pd.Timestamp(entry_date)
    recent = df[(df['Date'] >= start_date) & (df['Date'] <= entry_date)].dropna(subset=['MA', 'ADR10_pct']).reset_index(drop=True)
    if len(recent) == 0:
        return None

    below = recent['Close'] < recent['MA']
    violations = []
    i = 0
    n = len(recent)
    while i < n:
        if below.iloc[i]:
            max_depth_adr = 0.0
            j = i
            while j < n and below.iloc[j]:
                pct_below = (recent['MA'].iloc[j] - recent['Close'].iloc[j]) / recent['MA'].iloc[j] * 100
                adr_that_day = min(recent['ADR10_pct'].iloc[j], adr_cap)
                depth_in_adr = pct_below / adr_that_day if adr_that_day > 0 else 0.0
                max_depth_adr = max(max_depth_adr, depth_in_adr)
                j += 1
            violations.append({'depth_adr_x': max_depth_adr})
            i = j
        else:
            i += 1

    n_violations = len(violations)
    max_depth_adr_x = max([v['depth_adr_x'] for v in violations]) if violations else 0.0
    return {'n_violations': n_violations, 'max_depth_adr_x': round(float(max_depth_adr_x), 2)}


def _trend_window_r2(df, start_date, entry_date):
    start_date = pd.Timestamp(start_date)
    entry_date = pd.Timestamp(entry_date)
    w = df[(df['Date'] >= start_date) & (df['Date'] <= entry_date)].reset_index(drop=True)
    if len(w) < 10:
        return None
    x = np.arange(len(w))
    y = w['Close'].values
    r = np.corrcoef(x, y)[0, 1]
    return round(float(r ** 2), 3)


def score_ma_respect(df, entry_date):
    entry_date = pd.Timestamp(entry_date)
    ts = _find_trend_start(df, entry_date)
    if ts is None:
        return {'score': None, 'note': 'no clean trend-start found'}

    r10 = _adr_normalized_violation_profile(df, ts['date'], entry_date, 10)
    r20 = _adr_normalized_violation_profile(df, ts['date'], entry_date, 20)
    r50 = _adr_normalized_violation_profile(df, ts['date'], entry_date, 50)
    r2 = _trend_window_r2(df, ts['date'], entry_date)
    if r10 is None or r20 is None or r50 is None or r2 is None:
        return {'score': None, 'note': 'insufficient data since trend start'}

    fast_x = max(r10['max_depth_adr_x'], r20['max_depth_adr_x'])
    ma50_viol = r50['n_violations']
    ma50_x = r50['max_depth_adr_x']

    if fast_x < 1.7:
        fast_cap = 5
    elif fast_x < 2.1:
        fast_cap = 4
    elif fast_x < 2.5:
        fast_cap = 3
    elif fast_x < 3.0:
        fast_cap = 2
    elif fast_x < 4.0:
        fast_cap = 1
    else:
        fast_cap = 0

    if r2 >= 0.85:
        r2_cap = 5
    elif r2 >= 0.70:
        r2_cap = 4
    elif r2 >= 0.50:
        r2_cap = 3
    elif r2 >= 0.30:
        r2_cap = 2
    else:
        r2_cap = 1

    if ma50_viol == 0:
        ma50_score = 5
    elif ma50_x < 0.5:
        ma50_score = 4
    elif ma50_x < 1.5:
        ma50_score = 3
    elif ma50_x < 3.0:
        ma50_score = 2
    else:
        ma50_score = 1

    score = min(fast_cap, r2_cap, ma50_score)
    return {
        'raw_trend_start_date': ts['date'],
        'raw_trend_days_since': ts['days_since'],
        'raw_fast_line_adr_x': fast_x,
        'raw_trend_r2': r2,
        'raw_ma50_n_violations': ma50_viol,
        'raw_ma50_adr_x': ma50_x,
        'fast_line_depth_score': fast_cap,
        'trend_smoothness_score': r2_cap,
        'ma50_respect_score': ma50_score,
        'score': score,
    }


# ============================================================
# 4. TREND CHARACTER - Higher Highs / Higher Lows (0-5 pts)
# ============================================================
def _find_pivots(sub, bars=2):
    highs = sub['High'].values
    lows = sub['Low'].values
    n = len(sub)
    pivot_highs = []
    pivot_lows = []
    for i in range(bars, n - bars):
        window_h = highs[i - bars:i + bars + 1]
        if highs[i] == window_h.max() and np.argmax(window_h) == bars:
            pivot_highs.append((i, highs[i]))
        window_l = lows[i - bars:i + bars + 1]
        if lows[i] == window_l.min() and np.argmin(window_l) == bars:
            pivot_lows.append((i, lows[i]))
    return pivot_highs, pivot_lows


def score_trend_character(df, entry_date, lookback=85):
    sub = slice_to_entry(df, entry_date, lookback_days=lookback)
    if len(sub) < 20:
        return {'raw': None, 'score': None, 'note': 'insufficient history'}

    pivot_highs, pivot_lows = _find_pivots(sub, bars=2)
    if len(pivot_highs) < 2 or len(pivot_lows) < 2:
        return {'raw': None, 'score': None, 'note': 'insufficient pivots'}

    last_high, prev_high = pivot_highs[-1][1], pivot_highs[-2][1]
    last_low, prev_low = pivot_lows[-1][1], pivot_lows[-2][1]

    higher_high = last_high > prev_high
    higher_low = last_low > prev_low
    roughly_equal_high = abs(last_high - prev_high) / prev_high <= 0.02

    if higher_high and higher_low:
        score = 5
    elif higher_low and roughly_equal_high:
        score = 3
    elif not higher_high and higher_low:
        score = 1
    else:
        score = 0

    return {
        'raw_last_high': last_high, 'raw_prev_high': prev_high,
        'raw_last_low': last_low, 'raw_prev_low': prev_low,
        'higher_high': higher_high, 'higher_low': higher_low,
        'score': score
    }


# ============================================================
# 5. RELATIVE STRENGTH VS SPY v2 (0-5 pts)
# ============================================================
# Replaces the old 10-day endpoint-to-endpoint stock/SPY ratio change,
# which was too short/noisy: it rewarded LYFT's spike-and-fade and GME's
# post-event gap (score of 5 despite Dave saying "I would have never
# accepted this chart") and unfairly zeroed INDV (a normal, healthy
# pullback within a longer good trend). See MA_Respect_Redesign_Notes.txt
# for full history including why a long+short dual-window approach was
# tried and rejected first.
#
# New approach (Dave's own TradingView RS-pane framework, formalized):
# within a 60-day lookback, compute the daily stock/SPY ratio, find the
# PEAK ratio reached anywhere in that window, and score based on how far
# TODAY's ratio sits below that peak (% pullback from high). Small
# pullback = still near relative-strength highs = good, regardless of how
# strong the raw endpoint-to-endpoint number still looks. This is
# specifically pullback in the ratio (losing/holding ground vs SPY), not
# a price pullback.
def score_relative_strength(stock_df, spy_df, entry_date, lookback=60):
    s_sub = slice_to_entry(stock_df, entry_date, lookback_days=lookback + 1)
    m_sub = slice_to_entry(spy_df, entry_date, lookback_days=lookback + 1)
    if len(s_sub) < lookback * 0.8 or len(m_sub) < lookback * 0.8:
        return {'raw': None, 'score': None, 'note': 'insufficient history'}

    merged = pd.merge(s_sub[['Date', 'Close']], m_sub[['Date', 'Close']],
                       on='Date', suffixes=('_stock', '_spy'))
    if len(merged) < lookback * 0.8:
        return {'raw': None, 'score': None, 'note': 'date mismatch'}

    merged['ratio'] = merged['Close_stock'] / merged['Close_spy']
    peak = merged['ratio'].max()
    current = merged['ratio'].iloc[-1]
    pullback_pct = (current - peak) / peak * 100  # <= 0

    # sanity floor: 60-day net change vs SPY must be positive, else 0
    # (should essentially always pass given the upstream momentum screen)
    net_change_positive = merged['ratio'].iloc[-1] > merged['ratio'].iloc[0]
    if not net_change_positive:
        return {'raw_pullback_from_high_pct': round(float(pullback_pct), 2), 'score': 0,
                'note': 'net RS change vs SPY over lookback is negative'}

    if pullback_pct >= -3:
        score = 5
    elif pullback_pct >= -8:
        score = 4
    elif pullback_pct >= -15:
        score = 3
    elif pullback_pct >= -25:
        score = 2
    elif pullback_pct >= -40:
        score = 1
    else:
        score = 0

    return {'raw_pullback_from_high_pct': round(float(pullback_pct), 2), 'score': score}


# ============================================================
# 6. INSTITUTIONAL DISTRIBUTION (0-5 pts)
# ============================================================
def score_institutional_distribution(df, entry_date, window=25, vol_avg_period=50, vol_mult=1.5):
    sub = slice_to_entry(df, entry_date)
    if len(sub) < vol_avg_period + window:
        return {'raw': None, 'score': None, 'note': 'insufficient history'}
    sub = sub.copy()
    sub['VolAvg50'] = sub['Volume'].rolling(vol_avg_period).mean()
    recent = sub.tail(window).reset_index(drop=True)

    dist_days = []
    for i in range(1, len(recent)):
        is_red = recent['Close'].iloc[i] < recent['Close'].iloc[i - 1]
        vol_avg = recent['VolAvg50'].iloc[i]
        if pd.isna(vol_avg):
            continue
        is_heavy = recent['Volume'].iloc[i] >= vol_mult * vol_avg
        if is_red and is_heavy:
            # absorption check: did price make a new swing low in next 3 days (using full df)
            full_idx = sub.index[sub['Date'] == recent['Date'].iloc[i]]
            absorbed = None
            if len(full_idx) > 0:
                idx = full_idx[0]
                day_low = sub['Low'].iloc[idx]
                fwd = sub.iloc[idx + 1: idx + 4]
                if len(fwd) > 0:
                    absorbed = not (fwd['Low'].min() < day_low)
            dist_days.append({'date': recent['Date'].iloc[i], 'absorbed': absorbed})

    n = len(dist_days)
    n_followthrough = sum(1 for d in dist_days if d['absorbed'] is False)

    if n <= 1:
        score = 5
    elif n <= 3 and n_followthrough == 0:
        score = 4
    elif n <= 3 and n_followthrough >= 1:
        score = 2
    elif n >= 4 and n_followthrough == 0:
        score = 2
    else:
        score = 0

    return {
        'raw_n_distribution_days': n,
        'raw_n_with_followthrough': n_followthrough,
        'score': score
    }


# ============================================================
# 7. CONTROLLED ARRIVAL TO THE 50MA (0-5 pts)
# ============================================================
def score_controlled_arrival(df, entry_date, min_window=6, lookback_search=30):
    sub = slice_to_entry(df, entry_date, lookback_days=lookback_search)
    if len(sub) < min_window + 2:
        return {'raw': None, 'score': None, 'note': 'insufficient history'}

    closes = sub['Close'].values
    # find local swing high before the touch (max close in the search window, excluding last 2 bars)
    search_region = closes[:-2] if len(closes) > 2 else closes
    if len(search_region) < min_window:
        return {'raw': None, 'score': None, 'note': 'insufficient approach'}
    peak_idx = int(np.argmax(search_region))
    approach = closes[peak_idx:]
    if len(approach) < min_window:
        approach = closes[-min_window:]

    n = len(approach)
    half = n // 2
    first_half = approach[:half]
    second_half = approach[half:]

    def avg_daily_decline(seg):
        diffs = np.diff(seg)
        declines = -diffs[diffs < 0]
        return np.mean(declines) if len(declines) > 0 else 0.0001

    first_decline = avg_daily_decline(first_half)
    second_decline = avg_daily_decline(second_half)
    ratio = second_decline / first_decline if first_decline > 0 else 1.0

    if ratio <= 0.5:
        score = 5
    elif ratio <= 0.75:
        score = 4
    elif ratio <= 1.0:
        score = 3
    elif ratio <= 1.3:
        score = 1
    else:
        score = 0

    return {'raw_deceleration_ratio': round(ratio, 3), 'score': score}


# ============================================================
# COMBINED SCORE (v2 attributes only) - additive
# ============================================================
# Decided 2026-08-22 (see MA_Respect_Redesign_Notes.txt): tested additive
# (mean) vs. veto (min) empirically against the 456-event historical
# sample. Additive won clearly on every measure - higher correlation with
# outcomes, and a much cleaner separation between top-third and bottom-
# third trades (0.474 spread vs. 0.278 for veto). Veto underperforms here
# because requiring all 3 attributes to be strong is an overly harsh bar
# with only 3 attributes - one unlucky reading (e.g. a single real gap/
# earnings event distorting Trend Efficiency - see CORT/CFFN/LXP) can
# disqualify an otherwise-great trade. Additive lets two strong scores
# reasonably outweigh one weak one.
#
# IMPORTANT CAVEAT (do not remove this comment without re-reading the
# notes): this conclusion is based on combining only these 3 CURRENTLY-
# VALIDATED attributes. It intentionally does NOT include the other 6
# attributes (3 re-confirmed weak: Trend Character, Institutional
# Distribution, Controlled Arrival; 3 never reviewed: ADX Trend Strength,
# Long-Term Structure, Earnings Proximity - see score_trade() below for the
# legacy all-9-attribute view). If more attributes are validated and added
# to this combined score in a future phase, RE-TEST additive vs. veto again
# rather than assuming additive is still correct - a future noisy/broken
# attribute could quietly drag down good trades inside an average in a way
# a veto/floor would have caught.
def score_total_v2(df, entry_date, spy_df):
    te = score_trend_efficiency(df, entry_date)
    ma = score_ma_respect(df, entry_date)
    rs = score_relative_strength(df, spy_df, entry_date)

    te_score, ma_score, rs_score = te.get('score'), ma.get('score'), rs.get('score')
    scores = {'trend_efficiency': te_score, 'ma_respect': ma_score, 'relative_strength': rs_score}
    missing = [k for k, v in scores.items() if v is None]

    if missing:
        return {
            'trend_efficiency_score': te_score,
            'ma_respect_score': ma_score,
            'relative_strength_score': rs_score,
            'total_score_v2': None,
            'note': f"missing: {', '.join(missing)}",
        }

    total = round((te_score + ma_score + rs_score) / 3, 2)
    return {
        'trend_efficiency_score': te_score,
        'ma_respect_score': ma_score,
        'relative_strength_score': rs_score,
        'total_score_v2': total,
    }


# ============================================================
# MASTER SCORER
# ============================================================
def score_trade(ticker, entry_date, ohlcv_path, spy_df, earnings_row):
    df = load_ohlcv(ohlcv_path)

    r1 = score_earnings_proximity(entry_date, earnings_row.get('prior_earnings'), earnings_row.get('next_earnings'))
    r2 = score_trend_efficiency(df, entry_date)
    r3 = score_ma_respect(df, entry_date)
    r4 = score_trend_character(df, entry_date)
    r5 = score_relative_strength(df, spy_df, entry_date)
    r6 = score_institutional_distribution(df, entry_date)
    r7 = score_controlled_arrival(df, entry_date)
    r8 = score_adx_trend_strength(df, entry_date)
    r9 = score_long_term_structure(df, entry_date)

    scores = [r1['score'], r2['score'], r3['score'], r4['score'], r5['score'], r6['score'], r7['score'], r8['score'], r9['score']]
    total = sum(s for s in scores if s is not None)
    n_missing = sum(1 for s in scores if s is None)

    return {
        'ticker': ticker,
        'entry_date': entry_date,
        'earnings_proximity': r1,
        'trend_efficiency': r2,
        'ma_respect': r3,
        'trend_character': r4,
        'relative_strength': r5,
        'institutional_distribution': r6,
        'controlled_arrival': r7,
        'adx_trend_strength': r8,
        'long_term_structure': r9,
        'total_score': total,
        'n_missing_attributes': n_missing,
    }


# ============================================================
# 8. ADX TREND STRENGTH AT ENTRY (0-5 pts) [50-day ADX, matching Dave's screen]
# ============================================================
def _wilder_adx(df, period=50):
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

    def wilder_smooth(x, period):
        smoothed = np.zeros_like(x)
        smoothed[period] = x[1:period + 1].sum()
        for i in range(period + 1, len(x)):
            smoothed[i] = smoothed[i - 1] - (smoothed[i - 1] / period) + x[i]
        return smoothed

    if n < period * 2:
        return None

    smoothed_tr = wilder_smooth(tr, period)
    smoothed_plus_dm = wilder_smooth(plus_dm, period)
    smoothed_minus_dm = wilder_smooth(minus_dm, period)

    with np.errstate(divide='ignore', invalid='ignore'):
        plus_di = 100 * (smoothed_plus_dm / smoothed_tr)
        minus_di = 100 * (smoothed_minus_dm / smoothed_tr)
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di)

    dx = np.nan_to_num(dx, nan=0.0)

    adx = np.zeros_like(dx)
    start = period * 2
    if start >= n:
        return None
    adx[start] = dx[period:start].mean()
    for i in range(start + 1, n):
        adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period

    return adx


def score_adx_trend_strength(df, entry_date, period=50, lookback_peak=20, slope_window=10):
    sub = slice_to_entry(df, entry_date)
    if len(sub) < period * 2 + lookback_peak + 5:
        return {'raw_ADX': None, 'score': None, 'note': 'insufficient history'}

    adx = _wilder_adx(sub, period=period)
    if adx is None:
        return {'raw_ADX': None, 'score': None, 'note': 'insufficient history for ADX calc'}

    current_adx = adx[-1]
    recent_peak = adx[-lookback_peak:].max()

    if 20 <= current_adx <= 40:
        level_score = 5
    elif 15 <= current_adx < 20 or 40 < current_adx <= 50:
        level_score = 3
    elif 10 <= current_adx < 15 or 50 < current_adx <= 60:
        level_score = 1
    else:
        level_score = 0

    faded = recent_peak > 0 and (current_adx < recent_peak * 0.8)
    score = max(0, level_score - 1) if faded else level_score

    return {
        'raw_ADX_current': round(float(current_adx), 2),
        'raw_ADX_recent_peak': round(float(recent_peak), 2),
        'faded_from_peak': bool(faded),
        'score': score
    }


# ============================================================
# 9. LONGER-TERM TREND STRUCTURE AT ENTRY (0-5 pts)
# ============================================================
def score_long_term_structure(df, entry_date, slope_lookback=20):
    sub = slice_to_entry(df, entry_date)
    if len(sub) < 200 + slope_lookback:
        return {'raw': None, 'score': None, 'note': 'insufficient history'}

    sub = sub.copy()
    sub['SMA100'] = sub['Close'].rolling(100).mean()
    sub['SMA200'] = sub['Close'].rolling(200).mean()

    sma100_now = sub['SMA100'].iloc[-1]
    sma200_now = sub['SMA200'].iloc[-1]
    sma200_prior = sub['SMA200'].iloc[-1 - slope_lookback]

    if pd.isna(sma100_now) or pd.isna(sma200_now) or pd.isna(sma200_prior):
        return {'raw': None, 'score': None, 'note': 'insufficient history for SMA200'}

    stack_ok = sma100_now > sma200_now
    slope_pct = (sma200_now - sma200_prior) / sma200_prior * 100

    if stack_ok and slope_pct > 0.1:
        score = 5
    elif stack_ok and abs(slope_pct) <= 0.1:
        score = 3
    elif stack_ok and slope_pct < -0.1:
        score = 1
    else:
        score = 0

    return {
        'raw_SMA100': round(float(sma100_now), 2),
        'raw_SMA200': round(float(sma200_now), 2),
        'raw_SMA200_slope_pct_20d': round(float(slope_pct), 3),
        'stack_ok': bool(stack_ok),
        'score': score
    }
