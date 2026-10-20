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
# FIX (2026-09-22): single source of truth for "which trading day has
# finished". Lives in check_run_window.py (same pipeline/ folder) so the
# workflow gate and the orchestrator can never disagree about it.
from check_run_window import latest_completed_session
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
# ADDED 2026-10-19: the orders to place TONIGHT for the next session.
# Overwritten each run -- it is a standing instruction, not a log. The
# daily email renders it, and when order placement is automated the same
# file is what gets submitted, so nothing here is throwaway.
PENDING_ORDERS_PATH = os.path.join(_STATE_DIR, "pending_orders.csv")
PENDING_ORDERS_COLUMNS = [
    "for_session", "order_type", "ticker", "limit_price", "shares",
    "stop_price", "risk_per_share", "position_cost", "score", "note",
]

SKIP_SCORE_THRESHOLD = 2.5  # MVP decision -- see Conviction_Sizing_Model_v3.md

# SETUP RESET ON RE-ENTRY (added 2026-09-19) -- keep in sync with
# staged_pipeline_backtest.py, which documents the reasoning in full.
# One-position-per-ticker was ALREADY enforced here via already_open_tickers;
# what is new is the cooldown governing when a CLOSED ticker may be taken
# again: price must genuinely leave the 50-day MA and come back, rather than
# hovering along it and re-triggering a touch every other day.
# Chosen by reasoning, not swept for return -- do not tune for profit.
# TRAIL RULE -- changed 2026-09-19 to Option C. grace_R MUST be 0.5 for
# 'atr_sched_c'; the two are a pair. Reasoning in Architecture_and_Scope_v1.md.
TRAIL_RULE = "atr_sched_c"
TAKE_PARTIAL = False
GRACE_R = 0.5

ENFORCE_SETUP_RESET = True
RESET_DEPARTURE_ADR = 1.0      # closes must clear SMA50 by this x ADR10
RESET_PERSISTENCE_DAYS = 2     # ...on this many CONSECUTIVE days

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

# ADDED 2026-09-19: how far BELOW the 50-day MA to place the entry limit,
# in multiples of ATR14 on the touch day. 0.0 reproduces the previous
# at-the-MA behaviour exactly. See the long comment at the entry-price
# calculation in find_new_signals_for_date(), and the architecture doc
# section "Entry Price: Bidding Below the 50-Day MA (2026-09-19)".
#
# NOT YET VALIDATED LIVE -- adopted on backtest + portfolio replay only.
# This is the first parameter in the system set from a portfolio result
# that the trade-level result ARGUES AGAINST (total R falls). If it
# underperforms in paper trading, suspect this asymmetry first.
ENTRY_BID_ATR_MULT = 0.10

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
# CHANGED 2026-09-19: 0.10 -> 0.125, paired with MAX_CONCURRENT_POSITIONS
# 10 -> 8. Rationale in Architecture_and_Scope_v1.md: 8 slots at a 12.5%
# cost cap beat 10 at 10% on CAGR in the portfolio replay at equal drawdown.
# These two numbers are a PAIR -- 8 x 12.5% = 100% of equity, the same full
# deployment 10 x 10% gave. Changing one without the other either under-
# deploys capital or over-concentrates it.
MAX_POSITION_COST_PCT = 0.125
MAX_CONCURRENT_POSITIONS = 8

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
    # run_date   -- the session the ORCHESTRATOR PROCESSED. When the system
    #               ran, in trading-session terms.
    # for_session -- the session the ROW IS ABOUT.
    #
    # They are the same on every row except one: "limit_order_placed",
    # written during session T for an order that trades in session T+1.
    #
    # SPLIT 2026-10-20, the morning after the order list went live. Dave:
    # "we now have the run_date column meaning two things... I think this
    # needs to be two columns so the tracking is clear." He was right, and
    # it was a one-day-old mistake: the bid row was being stamped with the
    # FUTURE session so that a bid and its outcome would line up, which
    # quietly redefined run_date for that one disposition. Naming the two
    # ideas separately keeps the alignment AND keeps run_date honest.
    #
    # Named to match the "for_session" column in state/pending_orders.csv:
    # same concept, same word, two files.
    "run_date", "for_session", "ticker", "dropped_at", "disposition",
    "entry_price", "risk_per_share", "adr10_pct", "overhead_R",
    "score", "shares", "position_cost", "sizing_constraint",
]

# Gate labels, in production order. "passed" is not a gate -- it means the
# ticker survived all of them.
FUNNEL_GATES = [
    "already_open",         # dedup: ticker already in an open trade
    # SPLIT 2026-10-11 (see load_fetched_symbols): "no_data_for_date" meant
    # two completely different things -- a ticker that quietly fell out of
    # the screen, and a ticker we asked Yahoo for that came back without
    # its bar. The first is routine churn, the second is a data fault. One
    # label for both made the funnel unreadable: on a healthy session it
    # showed the same alarming count as on a broken one.
    "no_longer_fit_screen",   # not in this run's pull -- never requested
    "missing_data_for_date",  # REQUESTED and still has no bar -- REAL FAULT
    "no_data_for_date",       # retained ONLY as "could not classify":
                              # the pull report was absent or stale
    "insufficient_history",
    # ADDED to the list 2026-10-11: both of these already fired in
    # production but were never listed here, despite the docstring above
    # requiring this list to stay in sync with the gates.
    "no_setup_reset",
    "momentum_screen",
    "no_50ma_touch",
    "bid_not_filled",       # ADDED 2026-09-19: touched the MA but never reached our bid
    "adr_ceiling",
    "bad_risk_per_share",
    "overhead_resistance",
    "no_atr_for_entry_bid",   # ADDED 2026-09-19 with ENTRY_BID_ATR_MULT
    "no_spy_data",
    "score_below_threshold",
    "no_slot_available",    # fired in size_and_open_trades(), not the scan
]
# DISPOSITIONS (the other column) -- what happened to a ticker that was NOT
# dropped: "opened", "skipped_zero_shares", "skipped_no_capital", and from
# 2026-10-19 "limit_order_placed".
#
# "not_ordered" (2026-10-19) marks a ticker that cleared every gate and
# whose bid price was reached, but which no order had been placed for --
# the slot limit the night before meant it was never bid on. It is the
# honest record of what the slot cap costs, and it is NOT a rejection by
# the strategy.
#
# limit_order_placed is not a rejection. It records a bid that WAS placed
# and is waiting on the next session. It is dated the session the order is
# FOR, not the night it was written, so a bid and its outcome line up on
# the same date: one "limit_order_placed" row, then the next night either
# "opened" or "bid_not_filled" for that same ticker and date.

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
def _migrate_funnel_for_session():
    """One-time: add the for_session column to a funnel written before
    2026-10-20. Returns True if the file is ready to append to.

    WHY A MIGRATION AND NOT JUST A NEW COLUMN. append_funnel_rows() writes
    the header only when the file does not exist. Add a column and every
    row appended afterwards carries one more field than the header
    declares, so the whole file stops parsing. The column cannot be added
    without rewriting what is already there.

    WHAT IT DOES TO THE ROWS ALREADY IN THE FILE:
      - ordinary rows: for_session = run_date. They always meant the same
        session, so nothing changes in substance.
      - "limit_order_placed" rows: these were stamped with the session the
        order was FOR. That value moves to for_session, and run_date is set
        back to the previous trading day -- the night the order was
        actually written. Only one session's worth of these exists (the
        2026-10-19 run, the first under the order list).

    SAFETY. The original is copied to state/daily_funnel.pre_for_session.csv
    first, the rewrite goes to a temp file and is swapped in atomically, and
    the whole thing is skipped if the column is already there. If anything
    fails, this returns False and the caller skips the append rather than
    writing rows that would corrupt the file -- a one-day gap in a
    diagnostic log is recoverable, a ragged CSV is not.
    """
    if not os.path.exists(FUNNEL_LOG_PATH):
        return True
    try:
        with open(FUNNEL_LOG_PATH) as f:
            header = f.readline().strip()
        if "for_session" in header.split(","):
            return True

        print(f"  Funnel: adding the for_session column (one-time rewrite)")
        df = pd.read_csv(FUNNEL_LOG_PATH, dtype=str).fillna("")
        if "run_date" not in df.columns:
            print(f"  Funnel migration SKIPPED: no run_date column found. "
                  f"Not touching the file.")
            return False

        backup = os.path.join(_STATE_DIR, "daily_funnel.pre_for_session.csv")
        if not os.path.exists(backup):
            df.to_csv(backup, index=False)
            print(f"  Funnel: original copied to {os.path.basename(backup)} "
                  f"-- safe to delete once the new file looks right")

        df["for_session"] = df["run_date"]
        if "disposition" in df.columns:
            bids = df["disposition"] == "limit_order_placed"
            n_bids = int(bids.sum())
            if n_bids:
                moved = 0
                for i in df.index[bids]:
                    prev = previous_trading_day(df.at[i, "run_date"])
                    if prev is not None:
                        df.at[i, "run_date"] = prev.date().isoformat()
                        moved += 1
                print(f"  Funnel: {moved} of {n_bids} limit_order_placed "
                      f"row(s) re-dated to the night they were written")

        cols = [c for c in FUNNEL_COLUMNS if c in df.columns]
        cols += [c for c in df.columns if c not in cols]
        tmp = FUNNEL_LOG_PATH + ".tmp"
        df[cols].to_csv(tmp, index=False)
        os.replace(tmp, FUNNEL_LOG_PATH)
        print(f"  Funnel: rewrote {len(df)} row(s) with the new column")
        return True
    except Exception as e:
        print(f"  Funnel migration FAILED ({type(e).__name__}: {e}). Skipping "
              f"the funnel append for this session rather than writing rows "
              f"the header cannot describe. The session itself is unaffected.")
        return False


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
    if not _migrate_funnel_for_session():
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
    # Defaults to run_date, so every existing call site stays correct and
    # only the bid row has to say otherwise. The **fields loop below
    # overrides it when a caller passes for_session explicitly.
    row["for_session"] = row["run_date"]
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
# WHICH TICKERS DID THIS RUN ACTUALLY ASK FOR?  (added 2026-10-11)
# ============================================================
# scripts/pull_data.py writes pull_report.csv to the repo root listing every
# symbol it attempted this run. The workflow runs the pull immediately before
# this script in the same job, so when things are healthy the file on disk
# describes THIS session's fetch.
#
# WHY IT MATTERS: a ticker with no bar for the session is either (a) not in
# today's screen, so nothing ever requested it -- routine churn, roughly 10-20
# names a day -- or (b) requested and returned without its bar, which is the
# 2026-09-22 null-placeholder failure and a genuine fault. Those were logged
# under one name until today, so the funnel reported the same number on a
# healthy day as on a broken one, and the only instrument Dave actually reads
# could not tell him which he was looking at.
#
# FRESHNESS GUARD -- do not remove. pull_report.csv is written to the repo
# ROOT, and the workflow only commits data/ and state/, so the copy in the
# repository is never updated: on 2026-10-11 it held 1,003 rows every one of
# which ended 2026-08-19, nearly two months stale. Classifying against that
# would have labelled churn as faults and faults as churn with total
# confidence. So the report is trusted only if the newest bar it reports is
# at least as recent as the session being processed. A stale, missing or
# malformed report classifies NOTHING -- those tickers keep the old
# no_data_for_date label, which now means exactly "could not classify" and
# is itself the signal that the report needs looking at.
PULL_REPORT_PATH = os.path.join(_REPO_ROOT, "pull_report.csv")


def load_fetched_symbols(target_date):
    """Symbols this run's pull attempted, or None if that can't be trusted.

    Returns (symbols_or_None, reason) -- reason is a short string for the log
    explaining what was used or why it was rejected.
    """
    if not os.path.exists(PULL_REPORT_PATH):
        return None, f"{os.path.basename(PULL_REPORT_PATH)} not found"
    try:
        pr = pd.read_csv(PULL_REPORT_PATH)
    except Exception as e:
        return None, f"could not read pull report ({e})"
    if "symbol" not in pr.columns or len(pr) == 0:
        return None, "pull report empty or missing the symbol column"

    # Freshness: the report must cover the session being judged.
    if "end" in pr.columns:
        ends = pd.to_datetime(pr["end"], errors="coerce").dropna()
        if len(ends) == 0:
            return None, "pull report has no usable end dates"
        newest = ends.max().normalize()
        tgt = pd.Timestamp(target_date).normalize()
        if newest < tgt:
            return None, (f"pull report is STALE -- newest bar in it is "
                          f"{newest.date()}, older than the session being "
                          f"processed ({tgt.date()})")
    else:
        return None, "pull report has no end column (old schema)"

    return set(pr["symbol"].dropna().astype(str).str.strip()), (
        f"{len(pr)} symbols from pull_report.csv")


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
def check_open_positions_for_exits(open_df, target_date, queued_exits=None):
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
            # CHANGED 2026-09-19: the rule is now passed EXPLICITLY.
            # Previously this relied on simulate_trail()'s defaults, and the
            # comment here still claimed they were '20ma' -- stale since the
            # 2026-09-11 switch to atr_1.0x. Production was silently taking
            # whatever sim.py's signature happened to say. Naming the rule
            # and grace_R here means a future default change cannot alter
            # live behaviour without someone editing this line.
            rule=TRAIL_RULE,
            take_partial=TAKE_PARTIAL,
            grace_R=GRACE_R,
        )

        if result["exit_reason"] in ("still_open",):
            still_open_rows.append(pos)
            continue

        # ================================================================
        # NEXT-OPEN TRAIL EXITS (2026-10-19)
        #
        # simulate_trail() books a trail exit AT the close of the day that
        # breaks the line. Nobody can place that order: you cannot know the
        # close breaks the line until the close has happened. Adopted
        # 2026-10-13 and implemented here.
        #
        # ONLY the trail is translated. The hard stop at entry - 1R is
        # already a resting order at the broker and fills intraday, so it
        # needs no change -- translating it would be wrong, not safer.
        #
        # If the break lands on target_date itself there is no next bar yet,
        # so the position stays OPEN and the next run books it. That is
        # exactly what happens live: the break is seen after the bell, the
        # market-on-open order goes in that evening, and it fills tomorrow.
        # Self-correcting, and it needs no new state file.
        #
        # Cost: +0.613 avgR against +0.594 for the unplaceable baseline
        # (2026-10-13, all 1,404 trades). It is free, and it preserves the
        # right tail that a resting-stop exit clips.
        # ================================================================
        if result["exit_reason"].endswith("_trail"):
            brk = df_upto_target.index[df_upto_target["Date"] ==
                                       pd.Timestamp(result["exit_date"])]
            if len(brk) == 0 or int(brk[0]) + 1 >= len(df_upto_target):
                # No next bar yet -- the sell goes in tonight, fills tomorrow.
                print(f"  {ticker}: trail broke {pd.Timestamp(result['exit_date']).date()}, "
                      f"market-on-open sell queued for the next session")
                if queued_exits is not None:
                    queued_exits.append({
                        "ticker": ticker,
                        "shares": pos["shares"],
                        "reason": result["exit_reason"],
                        "broke_on": pd.Timestamp(result["exit_date"]).date(),
                    })
                still_open_rows.append(pos)
                continue
            nxt = df_upto_target.iloc[int(brk[0]) + 1]
            fill = float(nxt["Open"])
            result = dict(result)
            result["exit_date"] = nxt["Date"]
            result["exit_reason"] = result["exit_reason"] + "_next_open"
            result["realized_R"] = round(
                (fill - pos["entry_price"]) / pos["risk_per_share"], 3)

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
def _last_closed_exit_dates():
    """
    ticker -> most recent exit_date from the closed-trades log, used by the
    setup-reset rule. Returns {} if the log does not exist yet.
    """
    if not os.path.exists(CLOSED_TRADES_PATH):
        return {}
    try:
        cl = pd.read_csv(CLOSED_TRADES_PATH, parse_dates=["exit_date"])
    except Exception:
        return {}
    if len(cl) == 0 or "exit_date" not in cl.columns:
        return {}
    return cl.groupby("ticker")["exit_date"].max().to_dict()


def setup_has_reset(df, exit_date, target_date):
    """
    True if, strictly between exit_date and target_date, the ticker printed
    RESET_PERSISTENCE_DAYS consecutive closes at or above
    SMA50 + RESET_DEPARTURE_ADR * ADR10 -- i.e. price genuinely left the
    50-day MA rather than chopping along it and re-triggering a touch.

    MUST stay in sync with the identically-named function in
    staged_pipeline_backtest.py, which carries the full reasoning. If one is
    edited and the other is not, the backtest stops describing production.
    ADR10 is a PERCENT here, so it is converted to a price distance.
    """
    window = df[(df["Date"] > pd.Timestamp(exit_date)) &
                (df["Date"] < pd.Timestamp(target_date))]
    if len(window) < RESET_PERSISTENCE_DAYS:
        return False
    run = 0
    for _, r in window.iterrows():
        ma50, adr10, close = r.get("MA50"), r.get("ADR10"), r.get("Close")
        if pd.isna(ma50) or pd.isna(adr10) or pd.isna(close):
            run = 0
            continue
        if close >= ma50 + RESET_DEPARTURE_ADR * (adr10 / 100.0) * ma50:
            run += 1
            if run >= RESET_PERSISTENCE_DAYS:
                return True
        else:
            run = 0
    return False


def find_new_signals_for_date(target_date, already_open_tickers, funnel_rows=None,
                              mode="fill"):
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
        wilder_adx, passes_momentum_screen, touched_50ma,
        momentum_screen_detail, screen_inputs_ready)

    # ----------------------------------------------------------------
    # PLACEABLE SCREEN (2026-10-19)
    #
    # The screen is now evaluated on the PRIOR bar, because the bid it
    # authorises is placed that evening. One criterion is dropped:
    # price_above_50ma (Close > SMA50).
    #
    # WHY IT IS DROPPED, and this is not a casual change: applied to the
    # prior close it blocks every RECLAIM -- a name that sat below the 50
    # yesterday and touches it today. Measured 2026-10-17 on the 1,389
    # filled trades, reclaims are 442 trades at +1.364 avgR and 64.5% win,
    # carrying +602.8 R, which is 54.2% of ALL profit. The trades the
    # criterion keeps average +0.538 R, BELOW the 0.594 baseline.
    #
    # It is the same finding as B2 (2026-10-12), which rejected the
    # multi-session version of this filter for the same reason. Keeping
    # the criterion here would have quietly halved the system.
    #
    # Cost of dropping it: the watchlist now includes names below their
    # 50, which produce "knife-catch" fills on days price never recovers
    # above the MA. Measured through the full screen: 0.98 per setup at
    # +0.020 avgR -- near-zero expectancy, not harmful per trade, but they
    # do compete for slots. See the 2026-10-17 sections of
    # Architecture_and_Scope_v1.md.
    # ----------------------------------------------------------------
    def passes_screen_placeable(row):
        if not screen_inputs_ready(row):
            return False
        detail = momentum_screen_detail(row)
        detail.pop("price_above_50ma", None)
        return all(detail.values())

    target_date = pd.Timestamp(target_date)
    spy_df = sim.load_ticker(SPY_PATH) if os.path.exists(SPY_PATH) else None

    last_exits = _last_closed_exit_dates() if ENFORCE_SETUP_RESET else {}

    # Which tickers this run actually requested -- decides whether a missing
    # bar is churn or a fault. None means we could not tell; see
    # load_fetched_symbols().
    fetched_symbols, fetch_note = load_fetched_symbols(target_date)
    if fetched_symbols is None:
        print(f"  WARNING: cannot classify missing bars -- {fetch_note}. "
              f"Tickers with no bar will be logged as 'no_data_for_date' "
              f"(unclassified) rather than split into churn vs fault.")
    else:
        print(f"  Missing-bar classifier: {fetch_note}")

    n_churn = 0
    n_fault = 0
    n_unclassified = 0

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
            # No bar for this session. WHICH of the two reasons decides
            # whether this is routine or an alarm -- see load_fetched_symbols().
            if fetched_symbols is None:
                gate = "no_data_for_date"          # could not classify
                n_unclassified += 1
            elif ticker in fetched_symbols:
                gate = "missing_data_for_date"     # asked for it, got no bar
                n_fault += 1
            else:
                gate = "no_longer_fit_screen"      # never requested today
                n_churn += 1
            _log(funnel_rows, target_date, ticker, gate)
            continue
        if len(df[df["Date"] <= target_date]) < 260:
            # insufficient history, same floor as the historical scan
            _log(funnel_rows, target_date, ticker, "insufficient_history")
            continue

        # SETUP RESET (2026-09-19): a ticker we have already traded may not
        # be re-entered until price has genuinely left the 50-day MA and
        # come back. Checked BEFORE the momentum screen because it is the
        # cheaper test and the more fundamental objection.
        if ENFORCE_SETUP_RESET and ticker in last_exits:
            if not setup_has_reset(df, last_exits[ticker], target_date):
                _log(funnel_rows, target_date, ticker, "no_setup_reset")
                continue

        df = df[df["Date"] <= target_date].reset_index(drop=True)
        df["SMA50"] = df["Close"].rolling(50).mean()
        df["SMA100"] = df["Close"].rolling(100).mean()
        df["SMA200"] = df["Close"].rolling(200).mean()
        df["AvgVol10"] = df["Volume"].rolling(10).mean()
        df["ADX50"] = wilder_adx(df, period=50)
        df["Perf6mo"] = (df["Close"] / df["Close"].shift(126) - 1) * 100
        df["SMA50_prior5"] = df["SMA50"].shift(5)

        # ================================================================
        # PLACEABLE ENTRY (2026-10-19) -- read this before changing anything
        # below it.
        #
        # THE RULE: everything that DECIDES the bid comes from the PRIOR
        # bar, because the order is placed that evening. Only the FILL test
        # touches today's bar, and only its Low and Open.
        #
        # What this replaces: the old code read today's row, tested
        # Low <= SMA50(today) <= High for a touch, and set the bid from
        # SMA50(today). Today's SMA50 includes today's close, so that bid
        # could not be placed by anyone -- it needed the day to be over.
        # Measured 2026-10-13: ~15% of the trade population does not exist
        # under a placeable rule and fills drop from 77.1% to 64.7%.
        #
        # The touch test is GONE, and nothing replaces it. A resting limit
        # either fills or it does not; "the range contained the MA" was
        # only ever a proxy for that. The fill IS the signal.
        # ================================================================
        if len(df) < 52:
            _log(funnel_rows, target_date, ticker, "insufficient_history")
            continue

        # mode="fill"  -- evaluate the orders placed LAST night against the
        #                 session being processed. prior = T-1, today = T.
        # mode="order" -- generate the orders to place TONIGHT, for the next
        #                 session. prior = T, and there is no "today" yet,
        #                 so no fill test. Same code path deliberately: the
        #                 bid you are told to place and the bid that is
        #                 later scored against must be the same arithmetic,
        #                 and the momentum-screen incident above is what a
        #                 second implementation costs.
        k = 1 if mode == "fill" else 0
        prior = df.iloc[-(1 + k)]
        today = df.iloc[-1] if mode == "fill" else None

        if not passes_screen_placeable(prior):
            _log(funnel_rows, target_date, ticker, "momentum_screen")
            continue

        # PROJECTED SMA50 for today, from last night's data.
        #   SMA50(t) = SMA50(t-1) + (Close(t) - Close(t-50)) / 50
        # The only unknown is Close(t); the bar dropping OUT is known
        # exactly. Assuming Close(t) ~ Close(t-1) gives a projection whose
        # median error against the real SMA50 is -0.0004 ATR, with mean
        # absolute error 0.0134 ATR (2026-10-17, 1,404 trades). Five times
        # tighter than simply reusing yesterday's SMA50, and unbiased.
        sma50_prior = prior["SMA50"]
        close_dropping_out = df["Close"].iloc[-(50 + k)]
        if pd.isna(sma50_prior) or pd.isna(close_dropping_out):
            _log(funnel_rows, target_date, ticker, "insufficient_history")
            continue
        projected_sma50 = (float(sma50_prior)
                           + (float(prior["Close"]) - float(close_dropping_out)) / 50.0)

        # REORDERED 2026-09-14: entry price, ADR and risk-per-share are now
        # computed BEFORE the overhead-resistance check, because the v3
        # proximity rule needs entry_price and risk_per_share to express
        # overhead distance in R. Calling it without them silently falls
        # back to the v2 "any unresolved high" rule, which was shown on
        # 2026-09-14 to REMOVE value (rejected trades averaged +0.483R vs
        # +0.410R for those it kept).
        # CHANGED 2026-09-19: the entry limit is now placed
        # ENTRY_BID_ATR_MULT * ATR14 BELOW the 50-day MA, not at it.
        #
        # WHY: tested on the clean 1,404-trade post-Stage-6b sample. Bidding
        # below the MA fills less often (77% of touch days at 0.10 ATR,
        # because median penetration below the MA is 0.25 ATR) but every
        # per-trade statistic improves: avgR +0.594 -> +0.723, win 50.1% ->
        # 52.5%, immediate-stop rate 26.4% -> 21.9%. Total R FALLS (+833 ->
        # +781) because the misses outweigh the quality gain, so on total R
        # alone this change would be rejected.
        #
        # It is adopted on the PORTFOLIO result, which is what the account
        # actually experiences: 23.3% -> 26.9% CAGR over 20 tie-break seeds,
        # with median closed drawdown improving from -8.9% to -7.1%. The
        # reason the two disagree is that at 8 slots the baseline already
        # turns away ~300 of 1,374 signals it cannot fund; the trades a lower
        # bid gives up are disproportionately trades never taken anyway.
        #
        # 0.10 and not 0.20 (which scored highest at 28.0%): 0.20's
        # neighbours at 0.15 and 0.25 both print ~26.2%, making it a
        # single-cell spike between two lower values. 0.05-0.25 is a broad
        # plateau and 0.10 sits inside it. Picking a sweep's best cell is
        # how a sweep gets overfitted.
        #
        # Set ENTRY_BID_ATR_MULT = 0.0 to restore the at-the-MA behaviour.
        # See Architecture_and_Scope_v1.md, "Entry Price: Bidding Below the
        # 50-Day MA (2026-09-19)".
        # ATR14 is computed by sim.load_ticker() (Wilder's 14-day ATR in
        # dollars) and is already on df -- the same column the backtest used.
        # Do NOT recompute it here: a second implementation is exactly the
        # drift that put the momentum screen in two files for three days.
        # CHANGED 2026-10-19: ATR and ADR now read the PRIOR bar. Both feed
        # the order, so both must be knowable when the order is placed.
        atr14_at_entry = prior["ATR14"]
        if pd.isna(atr14_at_entry) or atr14_at_entry <= 0:
            # No usable ATR means no defensible offset. Fail loudly into the
            # funnel rather than silently falling back to the MA, which would
            # make two different entry rules indistinguishable in the log.
            _log(funnel_rows, target_date, ticker, "no_atr_for_entry_bid")
            continue

        # The bid itself. ENTRY_BID_ATR_MULT is UNCHANGED at 0.10 and is
        # deliberately still a parameter.
        #
        # A 2026-10-17 sweep found bidding AT the projection beats bidding
        # under it by +150 R on TOTAL R. That is the wrong metric here and
        # the comment above says why: this offset was adopted in the first
        # place on the PORTFOLIO result, because at 8 slots total R counts
        # trades the account could never have funded. The placeable design
        # binds slots harder, not less -- average occupancy moves from
        # 2.70-2.90 of 8 to 5.19 (2026-10-17) -- so the portfolio argument
        # is stronger now than when it was made. Changing the offset wants
        # its own portfolio test, not a total-R comparison.
        entry_price = projected_sma50 - ENTRY_BID_ATR_MULT * float(atr14_at_entry)
        adr10_pct = ((df["High"] / df["Low"] - 1) * 100).rolling(10).mean().iloc[-(1 + k)]

        # ADDED 2026-09-19, with ENTRY_BID_ATR_MULT: a bid BELOW the MA is a
        # limit that may never fill. The setup was valid and we wanted it --
        # price simply never traded down to our price. That is a materially
        # different event from "no signal today", and if both collapse into
        # a smaller signal count the log cannot tell a quiet market from a
        # bid set too low.
        #
        # Backtested miss rate at 0.10 ATR is 23.1% of touch days (median
        # penetration below the MA is 0.25 ATR). If the live rate drifts far
        # from that, the offset is wrong for the current regime -- that is
        # the number this gate exists to expose.
        #
        # NOTE this is still SAME-DAY only: the bid is placed for one
        # session and cancelled at the close. A bid resting across multiple
        # days was explicitly REJECTED as a measurement: it readmits the
        # repeat-entry duplication Stage 6b removed. Do not "improve" this
        # into a multi-day resting order without re-deriving the Stage 6b
        # interaction first.
        #
        # CHANGED 2026-10-19: the test is now unconditional. Under the old
        # at-the-MA rule a zero offset meant the bid sat exactly on the MA
        # and a touch guaranteed a fill, so the check was skipped when
        # ENTRY_BID_ATR_MULT was 0. There is no touch test any more -- the
        # bid is a projection and may miss at any offset including zero --
        # so the fill must always be checked.
        if mode == "fill" and float(today["Low"]) > entry_price:
            _log(funnel_rows, target_date, ticker, "bid_not_filled",
                 entry_price=round(float(entry_price), 4),
                 adr10_pct=None if pd.isna(adr10_pct) else round(float(adr10_pct), 4))
            continue

        # FILL PRICE. A limit buy fills at the limit, or better if the stock
        # opens below it -- a gap down through a resting bid fills at the
        # open, not at the bid. Measured 2026-10-17: fills below the
        # projection are the BEST trades in the sample (+2.3 avgR beyond
        # 0.50 ATR below), which is why no gap safeguard is applied here.
        # An earlier draft proposed one; it was measured and withdrawn.
        if mode == "fill":
            entry_price = min(float(entry_price), float(today["Open"]))

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
        # CHANGED 2026-10-19: evaluated as of the PRIOR bar. This decides
        # whether to place the order, so it cannot see the session the
        # order trades in.
        or_result = overhead_resistance_check(
            df, prior["Date"],
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
        # CHANGED 2026-10-19: scored as of the PRIOR bar, for the same
        # reason as the overhead check -- the score gates the ORDER.
        score_result = scorecard.score_total_v2(df, prior["Date"], spy_df)
        total_score = score_result.get("total_score_v2")
        if total_score is None or total_score < SKIP_SCORE_THRESHOLD:
            _log(funnel_rows, target_date, ticker, "score_below_threshold",
                 entry_price=round(float(entry_price), 4),
                 risk_per_share=round(float(risk_per_share), 4),
                 adr10_pct=round(float(adr10_pct), 4),
                 overhead_R=or_result.get("overhead_R"),
                 score=total_score)
            continue

        # ADDED 2026-10-19: how far the last close sits above the bid, in
        # ATRs. Used to order the nightly bid list -- see write_pending_orders.
        _dist = ((float(prior["Close"]) - float(entry_price)) / float(atr14_at_entry)
                 if atr14_at_entry else float("inf"))

        signals.append({
            "ticker": ticker,
            "entry_date": target_date,
            "distance_to_bid_atr": round(_dist, 3),
            "entry_price": round(float(entry_price), 4),
            "risk_per_share": round(float(risk_per_share), 4),
            "score_at_entry": total_score,
            # carried for the funnel log only -- not used in sizing
            "adr10_pct": round(float(adr10_pct), 4),
            "overhead_R": or_result.get("overhead_R"),
        })

    # One line that answers "is the data healthy today?" without opening the
    # funnel. n_fault is the number that matters: anything above zero means a
    # ticker we requested came back without its bar.
    if n_churn or n_fault or n_unclassified:
        print(f"  Missing bars: {n_churn} no_longer_fit_screen (routine), "
              f"{n_fault} missing_data_for_date"
              f"{' <-- INVESTIGATE' if n_fault else ''}"
              f"{f', {n_unclassified} unclassified' if n_unclassified else ''}")

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
def shares_for(entry_price, risk_per_share):
    """Share count under BOTH caps, and which one bound.

    Extracted 2026-10-19 so the nightly order list and the fill evaluation
    size identically. They must: the share count printed in tonight's email
    is the one Dave types into the broker, and the one the system books
    tomorrow. Two implementations would drift, which is precisely what the
    momentum screen did for three days in September.
    """
    dollar_risk = parameters.ACCOUNT_STARTING_BALANCE * parameters.RISK_PERCENT_PER_TRADE
    max_cost = parameters.ACCOUNT_STARTING_BALANCE * MAX_POSITION_COST_PCT
    by_risk = int(dollar_risk / risk_per_share) if risk_per_share > 0 else 0
    by_cost = int(max_cost / entry_price) if entry_price > 0 else 0
    if by_cost < by_risk:
        constraint = "cost"
    elif by_risk < by_cost:
        constraint = "risk"
    else:
        constraint = "both"
    return min(by_risk, by_cost), constraint


def size_and_open_trades(signals, open_df, funnel_rows=None, target_date=None,
                         max_new=None):
    """Size each signal under BOTH constraints and open the position.

    Two independent limits, added 2026-09-14 (see MAX_POSITION_COST_PCT):
      RISK limit -- shares such that shares * risk_per_share <= 1% of account.
                    Caps what the trade can LOSE.
      COST limit -- shares such that shares * entry_price <= 12.5% of account
                    (was 10% before 2026-09-19). Caps what the trade TIES UP,
                    and therefore guarantees the account can hold ~8
                    concurrent positions.
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

    # ADDED 2026-09-19: the slot cap is now enforced EXPLICITLY. Previously
    # it was only implied by the cost cap (10 x 10% = 100% of equity), so
    # "max concurrent positions" was a arithmetic coincidence rather than a
    # rule. With 8 x 12.5% the arithmetic still happens to work out, but any
    # future change to either number would have silently changed the slot
    # count. State the limit instead of inferring it.
    slots_used = len(open_df) if open_df is not None else 0

    # ADDED 2026-10-19: max_new is how many bids could have been placed
    # last night, counted before today's exits freed anything. It is the
    # binding limit; the slot check below stays as a second guard so this
    # function is still safe if called without it.
    new_rows = []
    for sig in signals:
        if max_new is not None and len(new_rows) >= max_new:
            print(f"  SKIPPED {sig['ticker']}: only {max_new} bid(s) could "
                  f"have been placed last night")
            _log(funnel_rows, target_date, sig["ticker"], "no_slot_available")
            continue
        if slots_used + len(new_rows) >= MAX_CONCURRENT_POSITIONS:
            print(f"  SKIPPED {sig['ticker']}: all {MAX_CONCURRENT_POSITIONS} "
                  f"position slots in use")
            _log(funnel_rows, target_date, sig["ticker"], "no_slot_available")
            continue
        shares, constraint = shares_for(sig["entry_price"], sig["risk_per_share"])

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
# PRE-FLIGHT DATA CHECK (added 2026-09-22)
# ============================================================
# ------------------------------------------------------------------
# SESSION COVERAGE GUARD (2026-09-24)
#
# The original guard below refused a session only when ZERO tickers
# carried a bar for it. That is too weak. On 2026-09-22 EIGHT tickers
# carried the bar and on 2026-09-23 FIVE did, against a healthy count of
# 67 -- so both sessions cleared the zero test, were processed against
# almost no data, and were stamped done. They cannot be revisited,
# because state/last_run_date.txt now says they were handled.
#
# The fix is a relative test rather than an absolute one. The screen
# universe changes size daily, so there is no fixed number to compare
# against; instead we compare today's coverage with the BEST coverage
# seen over the previous fortnight of weekdays. Taking the best rather
# than the most recent matters -- otherwise one thin day that slips
# through lowers the bar for the next one, and the standard ratchets
# down a day at a time.
# ------------------------------------------------------------------
MIN_COVERAGE_FRACTION = 0.50   # need half the recent best to proceed
COVERAGE_LOOKBACK_DAYS = 20    # calendar days scanned for a reference
LIVE_FILE_STALENESS_DAYS = 10  # a file this far behind is not being refreshed

# CORRECTION (2026-09-24, same day): the first version of this guard
# counted every file in data/. That directory still holds ~993 orphan
# files from the pre-2026-09-10 S&P 400+600 universe, which pull_data.py
# no longer refreshes; they are frozen with bars ending around
# 2026-09-09. So for any target in late September the 20-day reference
# window reaches back into dates where ALL ~1,060 files have data, the
# reference comes out at ~1,057 instead of ~67, and the threshold lands
# near 528 -- which blocks a perfectly healthy session. Reproduced: a
# complete 2026-09-21 with 67 of 67 screen tickers was refused.
#
# The reference therefore has to be drawn from the same population that
# actually gets refreshed. A file counts only if its own last bar is
# within LIVE_FILE_STALENESS_DAYS of the session being judged; the
# orphans fail that test at every date in the window, so they drop out
# of both the numerator and the denominator.
SKIP_SESSIONS_PATH = os.path.join(_STATE_DIR, "skip_sessions.txt")


def coverage_for_dates(target_dates, live_as_of=None):
    """
    One pass over data/ returning ({date: n_files_holding_that_date},
    n_files). Deliberately a light scan -- reads ONLY the Date column,
    not the full OHLCV load sim.load_ticker() does. Counting several
    dates in a single pass rather than calling the old one-date helper
    repeatedly keeps this at one read of each file no matter how wide
    the reference window is.
    """
    counts = {pd.Timestamp(d).normalize(): 0 for d in target_dates}
    n_files = 0
    n_live = 0
    live_cutoff = None
    if live_as_of is not None:
        live_cutoff = (pd.Timestamp(live_as_of).normalize()
                       - pd.Timedelta(days=LIVE_FILE_STALENESS_DAYS))

    for fpath in sorted(glob.glob(os.path.join(DATA_DIR, "*_1d_data.csv"))):
        n_files += 1
        try:
            dates = pd.read_csv(fpath, usecols=["Date"], parse_dates=["Date"])
        except Exception:
            continue
        have = set(pd.to_datetime(dates["Date"]).dt.normalize())
        if not have:
            continue
        if live_cutoff is not None and max(have) < live_cutoff:
            continue          # frozen orphan -- not part of the live universe
        n_live += 1
        for d in counts:
            if d in have:
                counts[d] += 1
    return counts, n_files, n_live


def count_tickers_with_bar(target_date):
    """
    Counts how many ticker files in data/ contain a bar for target_date.

    WHY THIS EXISTS (2026-09-22): the 2026-09-22 run completed green and
    stamped the session as processed while holding ZERO bars for that
    date. Yahoo had not yet published the daily bar -- every fetch
    "succeeded" and returned a valid file ending 2026-09-21 -- so
    find_new_signals_for_date() logged all 1,060 tickers as
    no_data_for_date and process_single_day() marked the day done
    anyway. Under the work-based run gate that silently loses the
    session forever. See Architecture_and_Scope_v1.md, 2026-09-22.

    Retained as a thin wrapper over coverage_for_dates() so any other
    caller keeps working.
    """
    counts, _, _ = coverage_for_dates([target_date])
    return counts[pd.Timestamp(target_date).normalize()]


def load_skipped_sessions():
    """
    Sessions an operator has declared permanently unavailable, one
    ISO date per line in state/skip_sessions.txt ('#' starts a comment).

    WHY (2026-09-24): the coverage guard refuses to process a session
    whose data never arrived, and main() breaks rather than continues,
    so every later session queues behind it. That is correct while the
    data might still turn up and a permanent stall once it will not.
    This is the release valve, and it is deliberately MANUAL: a session
    is only ever written off by a human decision, recorded in a file in
    the repo, where it can be read back later. Nothing auto-skips.
    """
    out = set()
    try:
        with open(SKIP_SESSIONS_PATH) as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                try:
                    out.add(pd.Timestamp(line).normalize())
                except Exception:
                    print(f"  WARNING: unparseable date in "
                          f"skip_sessions.txt: {line!r} -- ignored")
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"  WARNING: could not read skip_sessions.txt ({e})")
    return out


# ============================================================
# PROCESS ONE TRADING DAY (real day or a single day of a missed-day replay)
# ============================================================
def process_single_day(target_date):
    """
    Returns True if the day was processed and may be marked done, False if
    the run bailed out because the price data for target_date had not
    arrived yet (see count_tickers_with_bar). A False return MUST leave
    state/last_run_date.txt untouched so a later run retries the session.
    """
    print(f"\n=== Processing {pd.Timestamp(target_date).date()} ===")

    # PRE-FLIGHT (2026-09-22): refuse to process a session we have no
    # price data for. Without this the day gets stamped as done while
    # every ticker falls out at the no_data_for_date gate, and the
    # work-based run gate never comes back for it.
    tgt = pd.Timestamp(target_date).normalize()

    # OPERATOR OVERRIDE: a session written off by hand is stamped done
    # and skipped, so the queue behind it drains. No signals are
    # evaluated -- there is no data to evaluate -- and no exits are
    # checked, which is the honest treatment of a session we simply do
    # not have.
    if tgt in load_skipped_sessions():
        print(f"  SKIPPED BY OPERATOR: {tgt.date()} is listed in "
              f"state/skip_sessions.txt as permanently unavailable. "
              f"Marking it done and moving on WITHOUT processing it. "
              f"No exits were checked and no entries were taken for "
              f"this session.")
        set_last_run_date(target_date)
        return True

    window = [tgt - pd.Timedelta(days=i)
              for i in range(1, COVERAGE_LOOKBACK_DAYS + 1)]
    window = [d for d in window if d.weekday() < 5]
    counts, n_files, n_live = coverage_for_dates([tgt] + window,
                                                 live_as_of=tgt)

    n_with_bar = counts[tgt]
    reference = max([counts[d] for d in window], default=0)
    threshold = int(reference * MIN_COVERAGE_FRACTION)

    print(f"  Data check: {n_with_bar} of {n_live} live ticker files have a "
          f"bar for {tgt.date()} ({n_files} files in data/, "
          f"{n_files - n_live} frozen/orphaned and excluded)")
    if reference:
        print(f"  Reference coverage (best weekday in the previous "
              f"{COVERAGE_LOOKBACK_DAYS} days, live files only): "
              f"{reference} tickers; need at least {threshold} to proceed")

    if n_with_bar == 0 or (reference and n_with_bar < threshold):
        if n_with_bar == 0:
            print(f"  NO DATA for {tgt.date()} -- the daily bars have not "
                  f"been published.")
        else:
            print(f"  PARTIAL DATA for {tgt.date()} -- only {n_with_bar} "
                  f"tickers carry this bar against a recent best of "
                  f"{reference}. Processing it now would consume the "
                  f"session against an unrepresentative slice of the "
                  f"universe and stamp it done permanently.")
        print(f"  Leaving this session UNPROCESSED so a later run picks it "
              f"up. state/last_run_date.txt is unchanged.")
        print(f"  If this session is never going to publish, add the line "
              f"'{tgt.date()}' to state/skip_sessions.txt and re-run -- it "
              f"will then be marked done and the queue behind it will "
              f"drain.")
        return False

    open_df = load_open_positions()

    # ------------------------------------------------------------------
    # BID-TIME SLOT COUNT (2026-10-19)
    #
    # Counted BEFORE exits are processed, and that ordering is the whole
    # point. Orders go in the evening before, when the only thing known is
    # how many slots were occupied at that night's close. A position that
    # exits during the session frees a slot the bids could not have known
    # about.
    #
    # The old code capped at fill time, after exits, which quietly let the
    # system place more orders than it had room for and then keep the ones
    # that happened to be convenient. A real resting bid cannot be taken
    # back: if twelve are working and nine fill, nine positions open.
    #
    # This matters more under the placeable design than it did before --
    # candidate flow roughly doubles and average occupancy moves from
    # 2.70-2.90 of 8 to 5.19 (2026-10-17), so the cap now binds often
    # rather than rarely.
    # ------------------------------------------------------------------
    slots_free_at_bid_time = max(0, MAX_CONCURRENT_POSITIONS - len(open_df))
    print(f"  Slots free when tonight's bids would have gone in: "
          f"{slots_free_at_bid_time} of {MAX_CONCURRENT_POSITIONS}")

    # Collected so tonight's order list can carry the market-on-open sells
    # alongside the bids -- one file, one place to look.
    queued_exits = []
    open_df = check_open_positions_for_exits(open_df, target_date,
                                             queued_exits=queued_exits)

    already_open_tickers = set(open_df["ticker"]) if len(open_df) else set()

    # ADDED 2026-09-15: collected across the whole day and written once at
    # the end, so a crash midway leaves no half-day of rows in the log.
    funnel_rows = []

    signals = find_new_signals_for_date(target_date, already_open_tickers,
                                        funnel_rows=funnel_rows)

    # ------------------------------------------------------------------
    # ONLY ORDERS THAT WERE ACTUALLY PLACED CAN FILL (2026-10-19)
    #
    # If last night wrote a bid list for this session, that list IS the
    # set of things that could have filled. A candidate the system never
    # bid on cannot open, however good it looks in hindsight.
    #
    # Falls back to the full scan when no list applies -- the first run
    # after this change, or a replay of a session whose orders were never
    # written -- and says so, because a silent fallback here would look
    # exactly like working correctly.
    # ------------------------------------------------------------------
    pending = load_pending_bids(target_date)
    if pending is None:
        print("  No bid list for this session (first run under the new rules, "
              "or a replay) -- evaluating the full candidate scan instead.")
        # ADDED 2026-09-15: order candidates before sizing. Nothing tested
        # predicts which candidate to prefer, so this is a date-seeded
        # shuffle -- arbitrary, but without alphabetical's standing bias
        # toward early-alphabet tickers. See order_candidates().
        signals = order_candidates(signals, target_date)
    else:
        bid_on = [s for s in signals if s["ticker"] in pending]
        for s_ in signals:
            if s_["ticker"] not in pending:
                _log(funnel_rows, target_date, s_["ticker"], None, "not_ordered",
                     entry_price=s_["entry_price"],
                     risk_per_share=s_["risk_per_share"],
                     adr10_pct=s_.get("adr10_pct"),
                     overhead_R=s_.get("overhead_R"),
                     score=s_["score_at_entry"])
        # Keep last night's ordering; it is the order the bids went in.
        signals = sorted(bid_on, key=lambda x: pending[x["ticker"]])
        print(f"  Bid list for {pd.Timestamp(target_date).date()}: "
              f"{len(pending)} order(s) placed, {len(signals)} filled")

    open_df = size_and_open_trades(signals, open_df,
                                   funnel_rows=funnel_rows,
                                   target_date=target_date,
                                   max_new=slots_free_at_bid_time)

    save_open_positions(open_df)
    check_capital_committed(open_df)

    # ------------------------------------------------------------------
    # TONIGHT'S ORDERS (2026-10-19)
    #
    # Runs AFTER the book is settled, so free slots and available capital
    # are the real end-of-session numbers.
    #
    # Wrapped for the same reason the equity curve is: this is a reporting
    # and instruction artefact, and a failure to produce it must never
    # take down a session that otherwise completed. If it fails, the
    # session is still correct and the email simply has no order list.
    # ------------------------------------------------------------------
    try:
        write_pending_orders(target_date, open_df, queued_exits, funnel_rows)
    except Exception as e:
        print(f"  Order list failed ({type(e).__name__}: {e}) -- continuing; "
              f"the session itself is complete.")

    append_funnel_rows(funnel_rows)

    # ------------------------------------------------------------------
    # EQUITY CURVE (added 2026-10-13)
    #
    # One row per processed session in state/equity_curve.csv, so the
    # account has a history that can be charted. Deliberately NOT in the
    # daily email -- Dave reviews this monthly, not daily -- and
    # deliberately one row per session rather than a snapshot file per
    # day: the funnel already demonstrated what append-everything does to
    # a state folder.
    #
    # Placed here, after exits and entries are both settled and
    # open_positions.csv is written, so the row reflects the end state of
    # the session rather than a half-processed one. BEFORE
    # set_last_run_date(), so that a failure here cannot leave the session
    # stamped done with no curve row -- the next run would not revisit it.
    #
    # The import is inside the try for the same reason it is in
    # send_summary_email.py: if pipeline/equity_curve.py is missing or
    # mispasted, a top-level import would take down the whole daily run
    # for the sake of a reporting file. A curve row is never worth a
    # failed session.
    # ------------------------------------------------------------------
    try:
        import equity_curve
        _eq = equity_curve.record_session(target_date)
        print(f"  Equity curve: {_eq['date']} equity {_eq['equity']:,.2f} "
              f"(realized {_eq['realized_pnl_cum']:+,.2f}, "
              f"unrealized {_eq['unrealized_pnl']:+,.2f}, "
              f"{_eq['open_positions']} open"
              + (f", {_eq['stale_marks']} stale mark(s)" if _eq['stale_marks'] else "")
              + ")")
    except Exception as e:
        print(f"  Equity curve update failed ({type(e).__name__}: {e}) -- "
              f"continuing; the session is otherwise complete.")

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

    return True


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


def load_pending_bids(target_date):
    """The BUY_LIMIT orders written last night FOR target_date, in the order
    they were listed, or None if there is no list for this session.

    WHY THIS EXISTS (2026-10-19). Without it the system tells Dave to place
    three specific bids and then, next session, rescans the whole universe,
    reshuffles, and books whichever three happened to fill. Caught in the
    sandbox on 2026-03-16: the order list said ALB, FTI, PR and the run
    opened PL, DD, MRK. Entered by hand at a broker those two sets diverge
    on day one, and the fidelity check -- do my fills match the system's --
    becomes meaningless.

    A real resting order can only fill if it was placed. This enforces that.

    Returns None (not an empty dict) when no list applies, which is the
    signal to fall back to the full scan. That happens in exactly two
    legitimate cases: the first run after this change, and a catch-up
    replay of a session whose orders were never written. Both are logged.
    """
    if not os.path.exists(PENDING_ORDERS_PATH):
        return None
    try:
        po = pd.read_csv(PENDING_ORDERS_PATH)
    except Exception as e:
        print(f"  Could not read pending orders ({e}) -- falling back to the "
              f"full scan for this session.")
        return None
    if not len(po) or "for_session" not in po.columns:
        return None
    tgt = pd.Timestamp(target_date).date().isoformat()
    mine = po[(po["for_session"].astype(str) == tgt) &
              (po["order_type"] == "BUY_LIMIT")]
    if not len(mine):
        # A list exists but is for another session -- it says nothing about
        # this one, so it must not be treated as "no bids were placed".
        if str(po["for_session"].iloc[0]) != tgt:
            return None
        return {}          # list IS for today and genuinely held no bids
    return {str(r["ticker"]): i for i, (_, r) in enumerate(mine.iterrows())}


def write_pending_orders(target_date, open_df, queued_exits, funnel_rows=None):
    """Write the orders to place TONIGHT, for the next session.

    This is the piece that turns the system from a record of what would
    have happened into something that can actually be traded. Everything
    here is decided on data through target_date's close, because that is
    all that exists when the orders go in.

    Three kinds of order:
      SELL_MOO   -- market-on-open, for positions whose trail broke today
      BUY_LIMIT  -- the day bids, at the projected 50-day MA less the offset
      (the protective stop is carried ON the BUY_LIMIT row rather than
       being its own order, because it is only placed if the buy fills)

    Capital is reserved bid by bid as the list is built. Queued SELL_MOOs
    will free capital tomorrow, but deliberately are NOT counted: that
    money is not available until the sell actually fills, and sizing
    against it would be sizing against a forecast.
    """
    for_session = next_trading_day(target_date)
    if for_session is None:
        print("  No next trading day in the calendar -- no orders written.")
        return

    rows = []
    for q in (queued_exits or []):
        rows.append({
            "for_session": for_session.date().isoformat(),
            "order_type": "SELL_MOO", "ticker": q["ticker"],
            "limit_price": "", "shares": q["shares"], "stop_price": "",
            "risk_per_share": "", "position_cost": "", "score": "",
            "note": f"trail broke {q['broke_on']}",
        })

    free = max(0, MAX_CONCURRENT_POSITIONS - len(open_df))
    already = set(open_df["ticker"]) if len(open_df) else set()
    committed = open_df["position_cost"].sum() if len(open_df) else 0.0
    capital = parameters.ACCOUNT_STARTING_BALANCE - committed

    placed = 0
    if free > 0:
        sigs = find_new_signals_for_date(target_date, already,
                                         funnel_rows=None, mode="order")

        # ------------------------------------------------------------------
        # ORDER THE LIST BY REACHABILITY, NOT BY SHUFFLE (2026-10-19)
        #
        # The old flow tested for a touch BEFORE the slot cap, so the
        # candidates competing for slots were already names sitting at their
        # 50-day MA. There is no touch test any more -- it cannot be
        # placeable -- so the screen alone passes names trading far above
        # their MA, whose bid could not possibly be hit tomorrow.
        #
        # Measured in the sandbox before this change: 2 fills from 16 bids.
        # The shuffle was spending the night's handful of orders on names
        # several ATRs away from the bid while the actual setups went unbid.
        #
        # THIS IS NOT A RANKING IN THE SENSE order_candidates() FORBIDS.
        # That prohibition is about predicting which candidate will PERFORM
        # better, and it still stands -- nothing here claims a close name is
        # a better trade. This orders by whether the order can FILL AT ALL. A
        # bid five ATRs below the market is not an order, it is a placeholder.
        #
        # Nothing is excluded, only ordered, so no candidate is unreachable
        # on a day with slots to spare.
        # ------------------------------------------------------------------
        sigs = sorted(sigs, key=lambda x: (x.get("distance_to_bid_atr", float("inf")),
                                           x["ticker"]))
        for sig in sigs:
            if placed >= free:
                break
            shares, constraint = shares_for(sig["entry_price"], sig["risk_per_share"])
            if shares <= 0:
                continue
            cost = round(shares * sig["entry_price"], 2)
            if cost > capital:
                continue
            capital -= cost
            stop = round(sig["entry_price"] - sig["risk_per_share"], 4)
            rows.append({
                "for_session": for_session.date().isoformat(),
                "order_type": "BUY_LIMIT", "ticker": sig["ticker"],
                "limit_price": round(sig["entry_price"], 2),
                "shares": shares, "stop_price": round(stop, 2),
                "risk_per_share": sig["risk_per_share"],
                "position_cost": cost, "score": sig["score_at_entry"],
                "note": f"day order; {sig.get('distance_to_bid_atr', '?')} ATR "
                        f"below last close; sized by {constraint}",
            })
            _log(funnel_rows, target_date, sig["ticker"], None,
                 "limit_order_placed",
                 for_session=for_session.date().isoformat(),
                 entry_price=round(sig["entry_price"], 4),
                 risk_per_share=sig["risk_per_share"],
                 adr10_pct=sig.get("adr10_pct"),
                 overhead_R=sig.get("overhead_R"),
                 score=sig["score_at_entry"], shares=shares,
                 position_cost=cost, sizing_constraint=constraint)
            placed += 1

    pd.DataFrame(rows, columns=PENDING_ORDERS_COLUMNS).to_csv(
        PENDING_ORDERS_PATH, index=False)
    n_sell = sum(1 for r in rows if r["order_type"] == "SELL_MOO")
    print(f"  Orders for {for_session.date()}: {placed} limit bid(s) "
          f"({free} slot(s) free), {n_sell} market-on-open sell(s) "
          f"-> state/pending_orders.csv")


def previous_trading_day(before):
    """The last session the market was open before `before`, or None."""
    try:
        opens = sorted(d for d in load_market_open_dates()
                       if pd.Timestamp(d) < pd.Timestamp(before))
    except Exception:
        return None
    return pd.Timestamp(opens[-1]) if opens else None


def next_trading_day(after):
    """The next session the market is open after `after`, or None if the
    static calendar has run out (it covers 2026-2029 -- see
    load_market_open_dates)."""
    try:
        opens = sorted(d for d in load_market_open_dates()
                       if pd.Timestamp(d) > pd.Timestamp(after))
    except Exception as e:
        print(f"  Could not read the market calendar ({e}) -- cannot date "
              f"tonight's orders.")
        return None
    return pd.Timestamp(opens[0]) if opens else None


def get_trading_days_to_process(through=None):
    """
    Returns the list of TRADING days to process this run, in order. If the
    orchestrator has run before, replays every trading day from the day
    after the last successful run through the latest FINISHED session
    (inclusive), one at a time -- this is the missed-day catch-up
    behavior. If it has never run before, processes only that session.

    FIX (2026-09-05): now filters against the static NYSE market calendar
    (see load_market_open_dates()) so weekends/holidays are correctly
    skipped rather than counted as missed trading days needing catch-up.

    FIX (2026-09-22): the end date used to be datetime.now().date() --
    the GitHub runner's clock, which is UTC. From 8pm Eastern onward the
    UTC date is already TOMORROW, so a late run would "process" a day
    whose market hadn't opened: find_new_signals_for_date() skips every
    ticker with no bar for the date, yet process_single_day() still
    stamps that date as done -- silently losing it forever. A midday
    run would instead process a PARTIAL bar. Both are now impossible:
    the end date comes from check_run_window.latest_completed_session(),
    the single source of truth for which day has finished, which never
    returns a day whose market hasn't closed.
    """
    through = pd.Timestamp(through) if through is not None else latest_completed_session()
    market_open_dates = load_market_open_dates()
    last_run = get_last_run_date()

    if last_run is None:
        return [through] if through in market_open_dates else []

    days = []
    d = last_run + timedelta(days=1)
    while d <= through:
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
            # STOP ON MISSING DATA (2026-09-22): process_single_day()
            # returns False when the bars for that session have not been
            # published yet. Break rather than continue -- days must be
            # processed in order, since each one's exits and open
            # positions feed the next, so skipping ahead to a later day
            # would corrupt the sequence. Leaving the loop here leaves
            # state/last_run_date.txt pointing at the last COMPLETED
            # session, which is exactly what makes the next scheduled
            # run come back and retry.
            if not process_single_day(day):
                print(f"Stopping here -- {pd.Timestamp(day).date()} will be "
                      f"retried on the next scheduled run.")
                break

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
        #
        # SUPERSEDED (2026-09-22): the date stamp below is gone. The
        # 2026-09-13 duplicate-email bug it fixed came from the old
        # clock-window design, where the 8pm retry needed proof that
        # "a run happened today". check_run_window.py now asks a
        # different question -- has the latest FINISHED session been
        # processed? -- and state/last_run_date.txt means "last session
        # processed". Stamping today's calendar date here is now actively
        # harmful: a midday manual run with nothing to do would stamp
        # today, and that evening's run would then skip today as done.
        # A quiet run now leaves the file untouched; the duplicate-email
        # case can't recur because the workflow only runs when a session
        # is actually pending.
        if not days:
            last = get_last_run_date()
            print(f"No trading days to process -- already up to date "
                  f"through {last.date() if last is not None else 'nothing yet'}.")
    except Exception as e:
        print(f"ORCHESTRATOR FAILED: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
