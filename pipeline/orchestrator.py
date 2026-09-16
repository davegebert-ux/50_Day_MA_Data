"""
orchestrator.py -- Daily automation entry point for the 50-day MA bounce
scorecard system.

WHAT THIS FILE DOES
--------------------
This is the single script Claude Code (or a scheduled GitHub Action) runs
once per trading day to: check existing open positions for exits, scan for
new touch-event signals on the current day, score and filter those
signals, open new trades where warranted, and update the two persistent
state files (open positions, closed trades). It does NOT duplicate the
logic in the other pipeline files -- it imports and calls them.

Design decisions this file implements (see Architecture_and_Scope_v1.md,
"Daily Automation" sections, 2026-09-05, for full rationale on each):
  - Orchestrator-plus-replay: if one or more trading days were missed
    since the last successful run, this replays each missed day IN ORDER,
    one at a time, rather than jumping straight to today or reconciling a
    multi-day gap in one pass.
  - Two separate state files (not one file with a status column):
    OPEN_POSITIONS_PATH (mutated daily) and CLOSED_TRADES_PATH
    (append-only, never rewritten).
  - Dollar sizing uses the STATIC starting balance in parameters.py, not a
    running/compounding balance (deliberate simplification for now -- see
    architecture doc for the flagged future revisit).
  - Touch-scan restriction: signals are only ever evaluated for ONE target
    date per call (the day currently being processed, real or replayed),
    never a fresh full-history scan.

THIS FILE IS SCAFFOLDING, NOT YET PRODUCTION-HARDENED
--------------------------------------------------------
Written 2026-09-05 as the first real implementation of the orchestrator
design scoped this session. It has NOT yet been run end to end against
live/updated data, and the email-sending and GitHub Actions scheduling
pieces (6pm ET run, one retry at 8pm ET, failure-notification email) are
NOT implemented here yet -- those are still open items. Treat this as the
concrete starting point to build/test from, not a finished, deployed
system.
"""

import os
import glob
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo   # FIX (2026-09-13): needed to stamp the
                                # last-run date on the real Eastern clock

import pandas as pd
import numpy as np

import parameters
import sim
import scorecard
from overhead_resistance_and_smoothness_checks import overhead_resistance_check

DATA_DIR = "data"  # per-ticker {TICKER}_1d_data.csv files
SPY_PATH = "data/SPY_1d_data.csv"

# FIX (2026-09-06): state files now live in a dedicated state/ folder at
# the REPO ROOT (not inside pipeline/ with the code), since they are data
# the orchestrator reads/writes every run, not code -- same reasoning as
# keeping open-positions and closed-trades as separate files. Resolved
# relative to this script's own location (one level up from pipeline/,
# then into state/), same fix applied earlier to MARKET_CALENDAR_PATH, so
# this works correctly regardless of the working directory a GitHub
# Action or Claude Code happens to invoke this script from.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STATE_DIR = os.path.join(_REPO_ROOT, "state")
os.makedirs(_STATE_DIR, exist_ok=True)

OPEN_POSITIONS_PATH = os.path.join(_STATE_DIR, "open_positions.csv")
CLOSED_TRADES_PATH = os.path.join(_STATE_DIR, "closed_trades.csv")
LAST_RUN_PATH = os.path.join(_STATE_DIR, "last_run_date.txt")

SKIP_SCORE_THRESHOLD = 2.5  # MVP decision -- see Conviction_Sizing_Model_v3.md

# ADDED 2026-09-14: volatility ceiling. Reject touch events whose ADR10 at
# entry exceeds this. Validated on the 2,310-event wide-universe sample
# (outliers excluded): a 10% ceiling lifted avg R from 0.459 to 0.494 and
# win rate from 41.9% to 43.6% while discarding only ~11% of trades.
# Tighter ceilings (9% down to 5%) added nothing -- all flat near 0.47R --
# so 10% is a wildness cap, NOT a sweet-spot filter. Combined with the
# 2.5 score gate it produced the best cell on the board: 0.568R / 45.1%.
# NOTE: the earlier "5-7% ADR band carries +0.86R" finding did NOT survive
# outlier exclusion and must not be reintroduced as a sizing input.
ADR_CEILING_PCT = 10.0

# ADDED 2026-09-14: overhead-resistance proximity, in units of initial
# risk (R). An unresolved old high is only treated as disqualifying
# resistance if it sits within this distance ABOVE the entry price.
# Validated inside the full staged pipeline on the 2,310-event sample:
# 0.5R gave 0.576R / 45.6% win while keeping 91% of trades, beating both
# the old any-high rule (0.518R / 44.6%, keeping 68%) and no gate at all
# (0.545R / 44.5%). See overhead_resistance_and_smoothness_checks.py.
OVERHEAD_PROXIMITY_R = 0.5

# ADDED 2026-09-14: maximum position COST as a fraction of the account,
# independent of the risk rule. Reasoning (see architecture doc, "Position
# Sizing: Two Constraints", 2026-09-14):
#   Sizing off risk alone caps what a trade can LOSE but says nothing about
#   what it COSTS. On tight-stop names (e.g. FBP 2026-09-02, ADR 1.60%) the
#   risk maths yields a position costing ~60% of the account. Across the
#   1,023 passing trades the median cost was ~22% of account, the 95th
#   percentile ~46%, the worst ~93%. Two such trades commit the whole
#   account and the third signal cannot be taken -- so WITHOUT this cap the
#   number of positions the system can hold is decided by accident.
#   A cost cap is NOT a drag on edge: return per dollar risked is unchanged.
#   It only lowers how much is risked on the tightest-stop names, and in
#   exchange buys a guaranteed minimum number of concurrent positions.
#   10% chosen to match Dave's live practice (max ~10 concurrent positions);
#   it is a concurrency decision, not a backtest-optimised number.
# REJECTED ALTERNATIVE: flooring the stop distance (e.g. min 2%). It fixes
# cost as a side effect but spends the strategy's core edge -- a tight stop
# is what makes a large R multiple possible -- so it was ruled out.
MAX_POSITION_COST_PCT = 0.10

# ADDED 2026-09-15: per-ticker daily funnel log. Append-only record of what
# the pipeline did to EVERY ticker it looked at each day, not just the ones
# that became signals. Written because the counts alone ("1,591 scanned, 2
# signals") do not let you spot-check a name you expected to see and find
# out why it was cut. Deliberately a persistent file to look back through,
# NOT a line in the daily email.
#
# The gate names in dropped_at are the production gate ORDER, and must stay
# in sync with find_new_signals_for_date(). A ticker gets exactly one row
# per day, stamped with the FIRST gate it failed; tickers that pass every
# gate get dropped_at empty and a disposition recording what then happened
# to the signal -- including "skipped_no_capital", which is invisible
# anywhere else in the system today and is exactly the raw material any
# future work on candidate selection needs.
FUNNEL_LOG_PATH = os.path.join(_STATE_DIR, "daily_funnel.csv")

FUNNEL_COLUMNS = [
    "run_date", "ticker", "dropped_at", "disposition",
    "entry_price", "risk_per_share", "adr10_pct", "overhead_R",
    "score", "shares", "position_cost", "sizing_constraint",
]

# Gate labels, in production order. "passed" is not a gate -- it means the
# ticker survived all of them.
FUNNEL_GATES = [
    "already_open",         # dedup: ticker already in an open trade
    "no_data_for_date",
    "insufficient_history",
    "momentum_screen",
    "no_50ma_touch",
    "adr_ceiling",
    "bad_risk_per_share",
    "overhead_resistance",
    "no_spy_data",
    "score_below_threshold",
]

OPEN_POSITIONS_COLUMNS = [
    "ticker", "entry_date", "entry_price", "risk_per_share",
    "score_at_entry", "shares", "position_cost",
    # ADDED 2026-09-14: which of the two constraints actually determined
    # share count on this trade -- "risk", "cost", or "both". Logged
    # because at the current settings (1% risk, 10% cost) the cost cap is
    # expected to bind on essentially every trade, leaving the risk rule
    # dormant. This column makes it visible at a glance if that ever
    # changes (e.g. if max concurrent positions is revised to 8).
    "sizing_constraint",
]
CLOSED_TRADES_COLUMNS = OPEN_POSITIONS_COLUMNS + [
    "exit_date", "exit_reason", "exit_price", "realized_R", "realized_pnl",
]



# ============================================================
# DAILY FUNNEL LOG (append-only, one row per ticker per day)
# ============================================================
def append_funnel_rows(rows):
    """Append funnel rows for one processed day.

    Append-only and never rewritten, same discipline as closed_trades.csv.
    A replayed missed day appends its rows exactly as a live day would, so
    the file is complete regardless of whether the automation ran on time.

    Re-processing a date that is already in the file WILL duplicate it --
    the orchestrator's last-run-date guard is what prevents that, and this
    function deliberately does not second-guess it. If a date ever needs
    reprocessing, drop its rows first.
    """
    if not rows:
        return
    df = pd.DataFrame(rows, columns=FUNNEL_COLUMNS)
    header = not os.path.exists(FUNNEL_LOG_PATH)
    df.to_csv(FUNNEL_LOG_PATH, mode="a", header=header, index=False)
    print(f"  Funnel log: appended {len(df)} rows to {FUNNEL_LOG_PATH}")


def funnel_row(run_date, ticker, dropped_at=None, disposition="", **fields):
    """Build one funnel row, leaving unknown fields empty.

    Fields are filled in progressively as a ticker survives gates, so a
    ticker cut at the momentum screen has no entry_price and a ticker cut
    at scoring has everything except shares.
    """
    row = {c: "" for c in FUNNEL_COLUMNS}
    row["run_date"] = pd.Timestamp(run_date).date().isoformat()
    row["ticker"] = ticker
    row["dropped_at"] = dropped_at if dropped_at else ""
    row["disposition"] = disposition
    for k, v in fields.items():
        if k in row and v is not None:
            row[k] = v
    return row

def _log(funnel_rows, run_date, ticker, dropped_at=None, disposition="", **fields):
    """Append a funnel row if logging is enabled. No-op when it isn't."""
    if funnel_rows is None:
        return
    funnel_rows.append(funnel_row(run_date, ticker, dropped_at, disposition, **fields))


# ============================================================
# STATE FILE HELPERS
# ============================================================
def load_open_positions():
    if os.path.exists(OPEN_POSITIONS_PATH):
        return pd.read_csv(OPEN_POSITIONS_PATH, parse_dates=["entry_date"])
    return pd.DataFrame(columns=OPEN_POSITIONS_COLUMNS)


def save_open_positions(df):
    df.to_csv(OPEN_POSITIONS_PATH, index=False)


def append_closed_trade(row_dict):
    """Append-only write -- never rewrites existing closed-trade rows."""
    file_exists = os.path.exists(CLOSED_TRADES_PATH)
    row_df = pd.DataFrame([row_dict], columns=CLOSED_TRADES_COLUMNS)
    row_df.to_csv(CLOSED_TRADES_PATH, mode="a", header=not file_exists, index=False)


def get_last_run_date():
    if os.path.exists(LAST_RUN_PATH):
        with open(LAST_RUN_PATH) as f:
            return pd.Timestamp(f.read().strip())
    return None


def set_last_run_date(date):
    with open(LAST_RUN_PATH, "w") as f:
        f.write(str(pd.Timestamp(date).date()))


# ============================================================
# STEP 1: CHECK OPEN POSITIONS FOR EXITS (as of target_date)
# ============================================================
def check_open_positions_for_exits(open_df, target_date):
    """
    For every currently-open position, re-run sim.simulate_trail() from
    the original entry point against updated price data, and see whether
    an exit has now occurred ON OR BEFORE target_date. simulate_trail()
    itself does not need to be modified for this -- it already walks
    forward day by day and reports either an exit or 'still_open'.
    """
    still_open_rows = []
    for _, pos in open_df.iterrows():
        ticker = pos["ticker"]
        path = os.path.join(DATA_DIR, f"{ticker}_1d_data.csv")
        if not os.path.exists(path):
            still_open_rows.append(pos)  # can't evaluate, leave as-is
            continue

        df = sim.load_ticker(path)
        df_upto_target = df[df["Date"] <= pd.Timestamp(target_date)].reset_index(drop=True)
        entry_matches = df_upto_target.index[df_upto_target["Date"] == pd.Timestamp(pos["entry_date"])]
        if len(entry_matches) == 0:
            still_open_rows.append(pos)  # entry date not found in refreshed data yet
            continue
        entry_idx = entry_matches[0]

        result = sim.simulate_trail(
            df_upto_target,
            entry_idx=entry_idx,
            entry_price=pos["entry_price"],
            risk_per_share=pos["risk_per_share"],
            # rule / take_partial deliberately left at simulate_trail()'s
            # own defaults ('20ma', False) -- the recommended, locked-in
            # configuration. See sim.py's RECOMMENDED CONFIGURATION note.
        )

        if result["exit_reason"] in ("still_open",):
            still_open_rows.append(pos)
            continue

        # Trade closed -- append to closed log, do NOT keep in open positions
        exit_price = pos["entry_price"] + result["realized_R"] * pos["risk_per_share"]
        realized_pnl = result["realized_R"] * pos["risk_per_share"] * pos["shares"]
        closed_row = {
            **{c: pos[c] for c in OPEN_POSITIONS_COLUMNS},
            "exit_date": result["exit_date"],
            "exit_reason": result["exit_reason"],
            "exit_price": round(exit_price, 4),
            "realized_R": result["realized_R"],
            "realized_pnl": round(realized_pnl, 2),
        }
        append_closed_trade(closed_row)
        print(f"  CLOSED {ticker}: {result['exit_reason']}, {result['realized_R']}R, "
              f"{realized_pnl:.2f} dollars")

    return pd.DataFrame(still_open_rows, columns=OPEN_POSITIONS_COLUMNS)


# ============================================================
# STEP 2+3: SCAN FOR NEW SIGNALS ON target_date, FILTER, SCORE
# (touch-scan + momentum-screen + overhead-resistance + scorecard,
#  restricted to a SINGLE target date -- not a full-history scan)
# ============================================================
def find_new_signals_for_date(target_date, already_open_tickers, funnel_rows=None):
    """
    Evaluates ONLY target_date as a possible touch event per ticker (still
    loading whatever trailing history is needed to compute the moving
    averages / ADX / etc. behind that one day's check). Returns a list of
    dicts, one per qualifying new signal, with score attached.

    NOTE: this reproduces the momentum-screen + touch-detection logic from
    touch_scan_and_momentum_screen.py, restricted to one date, rather than
    importing that file directly -- it was written as a standalone script
    (module-level code, not a callable function), so this is a deliberate
    single-date reimplementation of its logic, not a duplicate universe
    scan. If touch_scan_and_momentum_screen.py is refactored into an
    importable function later, this should call that function instead of
    reimplementing the checks here.
    """
    from touch_scan_and_momentum_screen import (
        wilder_adx, passes_momentum_screen, touched_50ma)

    target_date = pd.Timestamp(target_date)
    spy_df = sim.load_ticker(SPY_PATH) if os.path.exists(SPY_PATH) else None

    signals = []
    files = sorted(glob.glob(os.path.join(DATA_DIR, "*_1d_data.csv")))
    for fpath in files:
        ticker = os.path.basename(fpath).replace("_1d_data.csv", "")
        if ticker in already_open_tickers:
            # dedup rule -- don't re-signal a ticker already in a trade
            _log(funnel_rows, target_date, ticker, "already_open")
            continue

        df = sim.load_ticker(fpath)
        if target_date not in set(df["Date"]):
            # ticker didn't trade / no data for this date
            _log(funnel_rows, target_date, ticker, "no_data_for_date")
            continue
        if len(df[df["Date"] <= target_date]) < 260:
            # insufficient history, same floor as the historical scan
            _log(funnel_rows, target_date, ticker, "insufficient_history")
            continue

        df = df[df["Date"] <= target_date].reset_index(drop=True)
        df["SMA50"] = df["Close"].rolling(50).mean()
        df["SMA100"] = df["Close"].rolling(100).mean()
        df["SMA200"] = df["Close"].rolling(200).mean()
        df["AvgVol10"] = df["Volume"].rolling(10).mean()
        df["ADX50"] = wilder_adx(df, period=50)
        df["Perf6mo"] = (df["Close"] / df["Close"].shift(126) - 1) * 100
        df["SMA50_prior5"] = df["SMA50"].shift(5)

        row = df.iloc[-1]

        # REFACTORED 2026-09-14: this function used to re-implement the
        # eight screen criteria inline, which is how the price_above_50ma
        # fix reached the backtest on 2026-09-11 but not production until
        # 2026-09-14. The screen now lives in exactly one place.
        if not passes_momentum_screen(row):
            _log(funnel_rows, target_date, ticker, "momentum_screen")
            continue

        touched = touched_50ma(row)
        if not touched:
            _log(funnel_rows, target_date, ticker, "no_50ma_touch")
            continue

        # REORDERED 2026-09-14: entry price, ADR and risk-per-share are now
        # computed BEFORE the overhead-resistance check, because the v3
        # proximity rule needs entry_price and risk_per_share to express
        # overhead distance in R. Calling it without them silently falls
        # back to the v2 "any unresolved high" rule, which was shown on
        # 2026-09-14 to REMOVE value (rejected trades averaged +0.483R vs
        # +0.410R for those it kept).
        entry_price = row["SMA50"]
        adr10_pct = ((df["High"] / df["Low"] - 1) * 100).rolling(10).mean().iloc[-1]

        # ADDED 2026-09-14: volatility ceiling -- see ADR_CEILING_PCT above.
        if pd.isna(adr10_pct) or adr10_pct > ADR_CEILING_PCT:
            _log(funnel_rows, target_date, ticker, "adr_ceiling",
                 entry_price=round(float(entry_price), 4),
                 adr10_pct=None if pd.isna(adr10_pct) else round(float(adr10_pct), 4))
            continue

        risk_per_share = sim.compute_risk_per_share(entry_price, adr10_pct)
        if risk_per_share <= 0:
            _log(funnel_rows, target_date, ticker, "bad_risk_per_share",
                 entry_price=round(float(entry_price), 4),
                 adr10_pct=round(float(adr10_pct), 4))
            continue

        # Overhead-resistance pre-watchlist screen (v3, proximity-based:
        # excludes only when an unresolved old high sits within
        # OVERHEAD_PROXIMITY_R above the entry).
        or_result = overhead_resistance_check(
            df, target_date,
            entry_price=float(entry_price),
            risk_per_share=float(risk_per_share),
            proximity_R=OVERHEAD_PROXIMITY_R,
        )
        if or_result is None or or_result["verdict"] == "EXCLUDE":
            _log(funnel_rows, target_date, ticker, "overhead_resistance",
                 entry_price=round(float(entry_price), 4),
                 risk_per_share=round(float(risk_per_share), 4),
                 adr10_pct=round(float(adr10_pct), 4),
                 overhead_R=None if or_result is None else or_result.get("overhead_R"))
            continue

        # Score
        if spy_df is None:
            _log(funnel_rows, target_date, ticker, "no_spy_data",
                 entry_price=round(float(entry_price), 4),
                 risk_per_share=round(float(risk_per_share), 4),
                 adr10_pct=round(float(adr10_pct), 4))
            continue
        score_result = scorecard.score_total_v2(df, target_date, spy_df)
        total_score = score_result.get("total_score_v2")
        if total_score is None or total_score < SKIP_SCORE_THRESHOLD:
            _log(funnel_rows, target_date, ticker, "score_below_threshold",
                 entry_price=round(float(entry_price), 4),
                 risk_per_share=round(float(risk_per_share), 4),
                 adr10_pct=round(float(adr10_pct), 4),
                 overhead_R=or_result.get("overhead_R"),
                 score=total_score)
            continue

        signals.append({
            "ticker": ticker,
            "entry_date": target_date,
            "entry_price": round(float(entry_price), 4),
            "risk_per_share": round(float(risk_per_share), 4),
            "score_at_entry": total_score,
            # carried for the funnel log only -- not used in sizing
            "adr10_pct": round(float(adr10_pct), 4),
            "overhead_R": or_result.get("overhead_R"),
        })

    return signals


# ============================================================
# STEP 3b: ORDER CANDIDATES WHEN THERE ARE MORE SIGNALS THAN SLOTS
# ============================================================
# ADDED 2026-09-15. REVISED SAME DAY -- read the whole comment before
# changing this, including the part about what was wrong the first time.
#
# THE PROBLEM. Signals routinely outnumber the capital available to take
# them. Over the 2024-09 to 2026-09 sample, a ten-position limit would
# have turned away roughly half of all qualifying signals. This is NOT a
# "too many signals today" problem -- the median signal day produces two
# candidates and only one day in two years produced more than ten. It
# binds because open positions ACCUMULATE: uncapped, the median number of
# concurrent positions was 17-18 and the peak 46. On a typical day the
# real question is "one slot free, two candidates".
#
# WHAT WAS TESTED AND REJECTED. Every one of these was run as an actual
# ranking key inside a ten-slot portfolio replay (n=1001, outliers
# excluded), compared against a random-pick baseline over 30 seeds:
# total_score_v2 highest-first, LTE highest-first, pullback depth
# shallowest-first, pullback speed/ADR slowest-first, overhead_R
# highest-first, and alphabetical. EVERY ONE landed inside the random
# baseline's own seed-to-seed range. None beat a coin flip.
#
# Score deserves a specific note because ranking on it is the intuitive
# answer. Above the 2.5 gate the score does not grade: expectancy by
# score value runs 2.5 -> +0.434R, 3.0 -> +0.745R, 3.5 -> +0.316R,
# 4.0 -> +0.919R, 4.5 -> +0.304R. That is noise, not a gradient.
# Correlation between score and realized R is 0.022. There is also very
# little spread to rank WITH: over half of all passing trades score 3.0
# or below and only 3 trades ever scored 5.0. The score is a good GATE
# and a bad RANKING KEY, and those are different jobs.
#
# LEAST-CORRELATED-FIRST WAS ADOPTED AND THEN REVERTED, SAME DAY.
# The first version of this function preferred whichever candidate was
# least correlated with the existing book. It was written up as capturing
# +314R against a random mean of +296R, and as being deterministic
# (15 seeds, all exactly +314R). BOTH CLAIMS WERE ARTIFACTS OF A BAD
# TEST: the correlation matrix behind them was computed over the FULL
# two-year sample, so every decision was made using data from after the
# decision date. Re-run point-in-time (trailing 120 days as of the
# decision, which is what any honest implementation must use):
#   - total R across 5 candidate-arrival orders: 279, 290, 312, 321, 345.
#     Mean ~309, inside the random range, and NOT deterministic -- the
#     spread comes purely from arrival order.
#   - mean pairwise correlation of the held book: 0.252, against 0.253
#     for random picking and 0.258 for alphabetical. It did not even
#     deliver the diversification it was adopted for.
# The reason is structural: with a median of two candidates a day, there
# is almost no choice available to exercise. You cannot diversify a book
# by picking one name out of two.
#
# WHAT THIS DOES NOW. A date-seeded shuffle. Selection is arbitrary --
# nothing tested beats arbitrary -- but it is arbitrary WITHOUT BIAS,
# which alphabetical is not. sorted(glob(...)) hands back candidates in
# alphabetical order, so early-alphabet tickers get first refusal on
# every constrained day, permanently. If one of them is a chronic
# underperformer the account keeps buying it and keeps skipping the names
# further down. Seeding on the date keeps runs reproducible and replays
# of missed days identical, while giving every ticker the same long-run
# chance of being reached.
#
# THIS IS A STOPGAP. The honest position is that no tested attribute
# predicts which of two simultaneous candidates does better. The expected
# path out is not a cleverer tiebreak but a scoring model that actually
# separates winners, at which point ranking by score becomes correct and
# this function should be retired. Full write-up, including the corrected
# numbers above, is in General_Research_Findings.md on the
# historical_backtest_research branch.


def order_candidates(signals, target_date):
    """Shuffle the day's candidates deterministically, seeded on the date.

    Same date always produces the same order, so a replayed missed day
    behaves identically to a live one. Different dates produce unrelated
    orders, so no ticker holds a standing advantage.

    This is NOT a ranking function and must not be dressed up as one. If
    something is ever found that genuinely predicts which candidate to
    prefer, it replaces this outright -- and it has to clear the bar the
    least-correlated rule failed: tested point-in-time, inside the slot
    limit, against a random baseline's full seed range.
    """
    if len(signals) <= 1:
        return list(signals)

    d = pd.Timestamp(target_date)
    seed = d.year * 10000 + d.month * 100 + d.day
    rng = np.random.RandomState(seed)

    ordered = list(signals)
    rng.shuffle(ordered)
    return ordered


# ============================================================
# STEP 4: OPEN NEW TRADES (dollar sizing off the STATIC account balance)
# ============================================================
def size_and_open_trades(signals, open_df, funnel_rows=None, target_date=None):
    """Size each signal under BOTH constraints and open the position.

    Two independent limits, added 2026-09-14 (see MAX_POSITION_COST_PCT):
      RISK limit -- shares such that shares * risk_per_share <= 1% of account.
                    Caps what the trade can LOSE.
      COST limit -- shares such that shares * entry_price <= 10% of account.
                    Caps what the trade TIES UP, and therefore guarantees the
                    account can hold ~10 concurrent positions.
    Share count is the LESSER of the two, always rounded down, so neither
    limit can be breached. Both are kept even though the cost cap currently
    binds first on virtually every trade: they express different things, and
    if max concurrent positions is ever revised the risk rule is already in
    place. Which one bound is recorded in "sizing_constraint".
    """
    dollar_risk_per_trade = parameters.ACCOUNT_STARTING_BALANCE * parameters.RISK_PERCENT_PER_TRADE
    max_position_cost = parameters.ACCOUNT_STARTING_BALANCE * MAX_POSITION_COST_PCT

    # ADDED 2026-09-15: capital available for NEW positions today, so a
    # signal that cannot be funded is recorded as skipped rather than
    # silently opened. Before this, sizing never consulted the account at
    # all -- check_capital_committed() only printed a warning AFTER the
    # fact, so the system could and did open positions it had no money
    # for. The funnel log made that visible, which is the point of it.
    committed = open_df["position_cost"].sum() if len(open_df) else 0.0
    capital_available = parameters.ACCOUNT_STARTING_BALANCE - committed

    new_rows = []
    for sig in signals:
        shares_by_risk = int(dollar_risk_per_trade / sig["risk_per_share"]) if sig["risk_per_share"] > 0 else 0
        shares_by_cost = int(max_position_cost / sig["entry_price"]) if sig["entry_price"] > 0 else 0
        shares = min(shares_by_risk, shares_by_cost)

        if shares_by_cost < shares_by_risk:
            constraint = "cost"
        elif shares_by_risk < shares_by_cost:
            constraint = "risk"
        else:
            constraint = "both"

        # A signal can size to zero shares if one share alone would exceed
        # the cost cap (a very high-priced stock). Skip rather than open a
        # zero-share row -- this is the sizing analogue of Dave's live
        # decision to pass on DELL because the share price did not suit the
        # position size.
        if shares <= 0:
            print(f"  SKIPPED {sig['ticker']}: sizes to 0 shares "
                  f"(entry {sig['entry_price']:.2f} vs cost cap {max_position_cost:.2f})")
            _log(funnel_rows, target_date, sig["ticker"], None, "skipped_zero_shares",
                 entry_price=sig["entry_price"], risk_per_share=sig["risk_per_share"],
                 adr10_pct=sig.get("adr10_pct"), overhead_R=sig.get("overhead_R"),
                 score=sig["score_at_entry"])
            continue

        position_cost_check = shares * sig["entry_price"]
        if position_cost_check > capital_available:
            print(f"  SKIPPED {sig['ticker']}: needs {position_cost_check:.2f} dollars, "
                  f"only {capital_available:.2f} available")
            _log(funnel_rows, target_date, sig["ticker"], None, "skipped_no_capital",
                 entry_price=sig["entry_price"], risk_per_share=sig["risk_per_share"],
                 adr10_pct=sig.get("adr10_pct"), overhead_R=sig.get("overhead_R"),
                 score=sig["score_at_entry"], shares=shares,
                 position_cost=round(position_cost_check, 2),
                 sizing_constraint=constraint)
            continue

        position_cost = round(shares * sig["entry_price"], 2)
        actual_risk = round(shares * sig["risk_per_share"], 2)
        new_rows.append({
            "ticker": sig["ticker"],
            "entry_date": sig["entry_date"],
            "entry_price": sig["entry_price"],
            "risk_per_share": sig["risk_per_share"],
            "score_at_entry": sig["score_at_entry"],
            "shares": shares,
            "position_cost": position_cost,
            "sizing_constraint": constraint,
        })
        capital_available -= position_cost
        print(f"  OPENED {sig['ticker']}: score {sig['score_at_entry']}, "
              f"{shares} shares, {position_cost:.2f} dollars committed, "
              f"{actual_risk:.2f} dollars at risk (limited by {constraint})")
        _log(funnel_rows, target_date, sig["ticker"], None, "opened",
             entry_price=sig["entry_price"], risk_per_share=sig["risk_per_share"],
             adr10_pct=sig.get("adr10_pct"), overhead_R=sig.get("overhead_R"),
             score=sig["score_at_entry"], shares=shares,
             position_cost=position_cost, sizing_constraint=constraint)

    if new_rows:
        new_df = pd.DataFrame(new_rows, columns=OPEN_POSITIONS_COLUMNS)
        open_df = pd.concat([open_df, new_df], ignore_index=True)
    return open_df


# ============================================================
# ACCOUNT-LEVEL CHECK (flag if total committed capital > account balance)
# ============================================================
def check_capital_committed(open_df):
    total_committed = open_df["position_cost"].sum() if len(open_df) else 0.0
    over_committed = total_committed > parameters.ACCOUNT_STARTING_BALANCE
    print(f"  Total capital committed: {total_committed:.2f} dollars "
          f"(account balance: {parameters.ACCOUNT_STARTING_BALANCE:.2f})")
    if over_committed:
        print("  WARNING: total capital committed exceeds account starting balance.")
    return total_committed, over_committed


# ============================================================
# PROCESS ONE TRADING DAY (real day or a single day of a missed-day replay)
# ============================================================
def process_single_day(target_date):
    print(f"\n=== Processing {pd.Timestamp(target_date).date()} ===")
    open_df = load_open_positions()

    open_df = check_open_positions_for_exits(open_df, target_date)

    already_open_tickers = set(open_df["ticker"]) if len(open_df) else set()

    # ADDED 2026-09-15: collected across the whole day and written once at
    # the end, so a crash midway leaves no half-day of rows in the log.
    funnel_rows = []

    signals = find_new_signals_for_date(target_date, already_open_tickers,
                                        funnel_rows=funnel_rows)

    # ADDED 2026-09-15: order candidates before sizing. Nothing tested
    # predicts which candidate to prefer, so this is a date-seeded
    # shuffle -- arbitrary, but without alphabetical's standing bias
    # toward early-alphabet tickers. See order_candidates().
    signals = order_candidates(signals, target_date)

    open_df = size_and_open_trades(signals, open_df,
                                   funnel_rows=funnel_rows,
                                   target_date=target_date)

    save_open_positions(open_df)
    check_capital_committed(open_df)
    append_funnel_rows(funnel_rows)
    set_last_run_date(target_date)

    # TEMP DEBUG (2026-09-09): tracing down a mystery where the orchestrator
    # step completes successfully and prints its normal output, but the very
    # next workflow step (git commit) reports the state/ folder as having no
    # files in it at all. Printing exactly where this process THINKS it's
    # writing, and confirming immediately afterward whether those files are
    # actually sitting on disk where expected. Remove once resolved.
    print(f"  [DEBUG] _STATE_DIR resolves to: {_STATE_DIR}")
    print(f"  [DEBUG] OPEN_POSITIONS_PATH: {OPEN_POSITIONS_PATH} -- exists: {os.path.exists(OPEN_POSITIONS_PATH)}")
    print(f"  [DEBUG] LAST_RUN_PATH: {LAST_RUN_PATH} -- exists: {os.path.exists(LAST_RUN_PATH)}")
    try:
        print(f"  [DEBUG] Contents of _STATE_DIR: {os.listdir(_STATE_DIR)}")
    except Exception as e:
        print(f"  [DEBUG] Could not list _STATE_DIR: {e}")
    print(f"  [DEBUG] Current working directory: {os.getcwd()}")


# ============================================================
# MAIN ENTRY POINT -- handles missed-day replay
# ============================================================
# Resolved relative to THIS FILE's own location, not the current working
# directory -- so this correctly finds the calendar file regardless of
# where the script is invoked FROM (e.g. a GitHub Action or Claude Code
# running it from the repo root rather than from inside pipeline/).
# Expects nyse_market_calendar_2026_2029.csv to live in the same folder
# as this script (pipeline/), same as the other pipeline files.
MARKET_CALENDAR_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "nyse_market_calendar_2026_2029.csv",
)


def load_market_open_dates():
    """
    Loads the static NYSE market-calendar CSV (nyse_market_calendar_2026_2029.csv)
    and returns a set of pd.Timestamp dates on which the market was actually
    open. FIX (2026-09-05): this replaces the earlier plain-calendar-day
    approach, which wrongly treated weekends/holidays as "missed trading
    days" needing replay. This is a static, hand-generated file (NYSE
    holiday rules computed directly and cross-checked against published
    NYSE sources for 2026), covering 2026-2029 -- a deliberate simplicity
    choice (Dave, 2026-09-05) over an external market-calendar library
    dependency. It will need to be regenerated/extended before it runs out
    at the end of 2029 -- see the note in Architecture_and_Scope_v1.md.
    """
    cal = pd.read_csv(MARKET_CALENDAR_PATH, parse_dates=["date"])
    open_days = cal[cal["market_open"] == True]["date"]
    return set(open_days)


def get_trading_days_to_process(today=None):
    """
    Returns the list of TRADING days to process this run, in order. If the
    orchestrator has run before, replays every trading day from the day
    after the last successful run through today (inclusive), one at a
    time -- this is the missed-day catch-up behavior. If it has never run
    before, processes only today (if today is itself a trading day).

    FIX (2026-09-05): now filters against the static NYSE market calendar
    (see load_market_open_dates()) so weekends/holidays are correctly
    skipped rather than counted as missed trading days needing catch-up.
    """
    today = pd.Timestamp(today) if today else pd.Timestamp(datetime.now().date())
    market_open_dates = load_market_open_dates()
    last_run = get_last_run_date()

    if last_run is None:
        return [today] if today in market_open_dates else []

    days = []
    d = last_run + timedelta(days=1)
    while d <= today:
        if d in market_open_dates:
            days.append(d)
        d += timedelta(days=1)
    return days


def main():
    """
    Runs the daily pipeline. FIX (2026-09-05): now wrapped so that any
    unhandled exception causes the process to exit with a non-zero exit
    code, and success exits 0. This is required for the GitHub Actions
    scheduling workflow to correctly detect success vs. failure -- a
    silent Python exception with no exit-code signal would otherwise look
    identical to success from the workflow's point of view, and the
    6pm-fail / 8pm-retry logic depends on being able to tell the
    difference.
    """
    import sys
    try:
        days = get_trading_days_to_process()
        for day in days:
            process_single_day(day)

        # FIX (2026-09-13): stamp today's date even when there were NO
        # trading days to process. Previously set_last_run_date() was
        # only called inside process_single_day(), so on a quiet day
        # (weekend, holiday, or a day already fully processed) the state
        # file kept an older date -- which made check_run_window.py
        # believe the 6pm run had never happened, so the 8pm retry ran
        # and sent a SECOND identical summary email. Observed
        # Sat 2026-09-12: two identical "Friday" summaries, with
        # state/last_run_date.txt still reading 2026-09-11.
        #
        # Recording the date on every completed run makes "has today's
        # run already succeeded?" answerable regardless of whether there
        # was any market activity to report. Note this writes the real
        # Eastern calendar date, matching how check_run_window.py
        # compares it.
        if not days:
            today_eastern = pd.Timestamp(
                datetime.now(ZoneInfo("America/New_York")).date()
            )
            print(f"No trading days to process -- recording "
                  f"{today_eastern.date()} as last successful run "
                  f"(no market activity).")
            set_last_run_date(today_eastern)
    except Exception as e:
        print(f"ORCHESTRATOR FAILED: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
