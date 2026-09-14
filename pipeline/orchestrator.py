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

SKIP_SCORE_THRESHOLD = 2.5  # MVP decision -- see Conviction_Sizing_Model_v2.md

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
def find_new_signals_for_date(target_date, already_open_tickers):
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
            continue  # dedup rule -- don't re-signal a ticker already in a trade

        df = sim.load_ticker(fpath)
        if target_date not in set(df["Date"]):
            continue  # ticker didn't trade / no data for this date
        if len(df[df["Date"] <= target_date]) < 260:
            continue  # insufficient history, same floor as the historical scan

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
            continue

        touched = touched_50ma(row)
        if not touched:
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
            continue

        risk_per_share = sim.compute_risk_per_share(entry_price, adr10_pct)
        if risk_per_share <= 0:
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
            continue

        # Score
        if spy_df is None:
            continue
        score_result = scorecard.score_total_v2(df, target_date, spy_df)
        total_score = score_result.get("total_score_v2")
        if total_score is None or total_score < SKIP_SCORE_THRESHOLD:
            continue

        signals.append({
            "ticker": ticker,
            "entry_date": target_date,
            "entry_price": round(float(entry_price), 4),
            "risk_per_share": round(float(risk_per_share), 4),
            "score_at_entry": total_score,
        })

    return signals


# ============================================================
# STEP 4: OPEN NEW TRADES (dollar sizing off the STATIC account balance)
# ============================================================
def size_and_open_trades(signals, open_df):
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
        print(f"  OPENED {sig['ticker']}: score {sig['score_at_entry']}, "
              f"{shares} shares, {position_cost:.2f} dollars committed, "
              f"{actual_risk:.2f} dollars at risk (limited by {constraint})")

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
    signals = find_new_signals_for_date(target_date, already_open_tickers)

    open_df = size_and_open_trades(signals, open_df)

    save_open_positions(open_df)
    check_capital_committed(open_df)
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
