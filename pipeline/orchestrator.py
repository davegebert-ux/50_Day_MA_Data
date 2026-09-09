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

OPEN_POSITIONS_COLUMNS = [
    "ticker", "entry_date", "entry_price", "risk_per_share",
    "score_at_entry", "shares", "position_cost",
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
    from touch_scan_and_momentum_screen import wilder_adx

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
        if pd.isna(row[["SMA50", "SMA100", "SMA200", "ADX50", "AvgVol10",
                         "Perf6mo", "SMA50_prior5"]]).any():
            continue

        sma50_rising = row["SMA50"] > row["SMA50_prior5"]
        stack_ok = row["SMA100"] > row["SMA200"]
        adx_ok = 20 <= row["ADX50"] <= 40
        vol_ok = row["AvgVol10"] > 1_000_000
        perf_ok = 30 <= row["Perf6mo"] <= 500
        if not (sma50_rising and stack_ok and adx_ok and vol_ok and perf_ok):
            continue

        touched = row["Low"] <= row["SMA50"] <= row["High"]
        if not touched:
            continue

        # Overhead-resistance pre-watchlist screen
        or_result = overhead_resistance_check(df, target_date)
        if or_result is None or or_result["verdict"] == "EXCLUDE":
            continue

        # Score
        if spy_df is None:
            continue
        score_result = scorecard.score_total_v2(df, target_date, spy_df)
        total_score = score_result.get("total_score_v2")
        if total_score is None or total_score < SKIP_SCORE_THRESHOLD:
            continue

        entry_price = row["SMA50"]
        adr10_pct = ((df["High"] / df["Low"] - 1) * 100).rolling(10).mean().iloc[-1]
        risk_per_share = sim.compute_risk_per_share(entry_price, adr10_pct)

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
    dollar_risk_per_trade = parameters.ACCOUNT_STARTING_BALANCE * parameters.RISK_PERCENT_PER_TRADE
    new_rows = []
    for sig in signals:
        shares = int(dollar_risk_per_trade / sig["risk_per_share"]) if sig["risk_per_share"] > 0 else 0
        position_cost = round(shares * sig["entry_price"], 2)
        new_rows.append({
            "ticker": sig["ticker"],
            "entry_date": sig["entry_date"],
            "entry_price": sig["entry_price"],
            "risk_per_share": sig["risk_per_share"],
            "score_at_entry": sig["score_at_entry"],
            "shares": shares,
            "position_cost": position_cost,
        })
        print(f"  OPENED {sig['ticker']}: score {sig['score_at_entry']}, "
              f"{shares} shares, {position_cost:.2f} dollars committed")

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
        for day in get_trading_days_to_process():
            process_single_day(day)
    except Exception as e:
        print(f"ORCHESTRATOR FAILED: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
