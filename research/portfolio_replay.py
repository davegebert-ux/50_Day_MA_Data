"""
portfolio_replay.py -- Portfolio-level replay of staged pipeline results.
RESEARCH BRANCH ONLY (historical_backtest_research). Not part of the
production pipeline and never imported by it.

WHY THIS FILE EXISTS
--------------------
staged_pipeline_backtest.py scores every touch event INDEPENDENTLY and
reports the average R across all of them. That is the right way to
measure whether a GATE or an ATTRIBUTE adds value, and it is what every
finding in the architecture doc rests on.

But it silently assumes infinite capital. Measured over 2024-09 to
2026-09, a ten-position limit would have turned away 50.5% of all
qualifying signals -- not because too many arrive at once (the median
signal day produces only two), but because positions ACCUMULATE. The
median number of concurrently open positions, uncapped, was 18, with a
peak of 46. So roughly half the trades in the staged results are trades
the account could never actually have taken.

This script replays those same staged results day by day under a fixed
slot limit, taking candidates in the SAME ranked order production uses,
and reports what the account would actually have captured -- plus a
record of everything it had to skip.

WHAT IT DOES NOT DO YET
-----------------------
Dollar returns and compounding. It reports R, slot occupancy and skip
counts only. The account model is still the static-balance one in
parameters.py, and MAX_POSITION_COST_PCT is not applied here -- the cost
cap does not change R multiples, only dollar weight, so it cannot affect
these numbers. This file is the natural place to add both when portfolio
dollar returns are modelled; that is the flagged future work.

SHARED RANKING LOGIC -- IMPORTANT
---------------------------------
The candidate ordering is NOT reimplemented here. It is imported from
orchestrator.order_candidates, the exact function production calls. This is
deliberate and follows the lesson from the momentum screen, which existed
in two copies from 2026-09-11 to 2026-09-14: a fix landed in the backtest
copy and not the production copy, and the two silently disagreed for
three days. If order_candidates() changes, this script changes with it and
cannot drift.

That import is the one and only reason this research-branch file reaches
into the production pipeline. Keep it that way.

USAGE
-----
    python portfolio_replay.py

Reads STAGED_RESULTS_PATH, writes two CSVs next to it: a per-trade record
of what was taken, and a per-trade record of what was skipped for want of
a slot.
"""

import os
import sys

import numpy as np
import pandas as pd

# ------------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------------
STAGED_RESULTS_PATH = "Staged_Pipeline_Results.csv"
PIPELINE_DIR = "pipeline"          # where orchestrator.py lives
MAX_CONCURRENT_POSITIONS = 10      # matches the 10-position basis behind
                                   # MAX_POSITION_COST_PCT = 0.10
EXCLUDE_R_OUTLIER_PCT = 0.01       # top/bottom 1%, same convention as all
                                   # other findings in this project
RANDOM_BASELINE_SEEDS = 30

TAKEN_OUT_PATH = "Portfolio_Replay_Taken.csv"
SKIPPED_OUT_PATH = "Portfolio_Replay_Skipped.csv"


# ------------------------------------------------------------------
# IMPORT THE PRODUCTION RANKING FUNCTION
# ------------------------------------------------------------------
def load_rank_signals():
    """Import order_candidates from the production orchestrator.

    Returns (function, note). If the orchestrator cannot be imported --
    most likely because this is being run from a checkout of the research
    branch that does not carry pipeline/ -- this returns None and the
    caller falls back to a local correlation ranking that is documented
    as a COPY and flagged loudly in the output. The fallback exists so
    the script still runs standalone, NOT as a maintained second
    implementation: if you find yourself editing it, fix the import
    instead.
    """
    for candidate in (PIPELINE_DIR, os.path.join("..", PIPELINE_DIR), "."):
        path = os.path.abspath(candidate)
        if os.path.exists(os.path.join(path, "orchestrator.py")):
            sys.path.insert(0, path)
            try:
                from orchestrator import order_candidates
                return order_candidates, f"imported from {path}"
            except Exception as exc:      # noqa: BLE001
                return None, f"orchestrator found at {path} but import failed: {exc}"
    return None, "orchestrator.py not found on any candidate path"


# ------------------------------------------------------------------
# LOAD
# ------------------------------------------------------------------
def load_staged(path=STAGED_RESULTS_PATH):
    """Passing trades only, outliers trimmed, sorted by entry date."""
    df = pd.read_csv(path)
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["outcome_exit_date"] = pd.to_datetime(df["outcome_exit_date"])

    # dropped_at is null for rows that survived every gate.
    df = df[df["dropped_at"].isna()]
    df = df[df["outcome_realized_R"].notna() & df["outcome_exit_date"].notna()]

    lo, hi = df["outcome_realized_R"].quantile(
        [EXCLUDE_R_OUTLIER_PCT, 1 - EXCLUDE_R_OUTLIER_PCT])
    df = df[(df["outcome_realized_R"] >= lo) & (df["outcome_realized_R"] <= hi)]

    return df.sort_values("entry_date").reset_index(drop=True)


# ------------------------------------------------------------------
# THE REPLAY
# ------------------------------------------------------------------
def replay(df, rank_fn, slots=MAX_CONCURRENT_POSITIONS, order="ranked", seed=0):
    """Walk forward day by day under a fixed slot limit.

    order:
        "ranked"       -- use the production ordering function
        "random"       -- shuffle each day's candidates, for a baseline
        "alphabetical" -- the pre-2026-09-15 incumbent, for comparison

    A position occupies a slot from its entry_date until its
    outcome_exit_date. Exits are processed BEFORE entries on each date,
    so a position closing today frees its slot for today's candidates --
    which mirrors the orchestrator, where check_open_positions_for_exits()
    runs before find_new_signals_for_date().

    Returns (taken_df, skipped_df).
    """
    rng = np.random.RandomState(seed)
    open_positions = []        # list of dicts with exit_date + ticker
    taken, skipped = [], []

    for date, day in df.groupby("entry_date", sort=True):
        # 1. Free up slots for anything that exited on or before today.
        open_positions = [p for p in open_positions if p["exit_date"] > date]

        # 2. Order today's candidates.
        candidates = day.to_dict("records")
        if order == "random":
            rng.shuffle(candidates)
        elif order == "alphabetical":
            candidates.sort(key=lambda r: r["ticker"])
        else:
            candidates = rank_fn(candidates, date)

        # 3. Fill slots in that order; everything after is a skip.
        for row in candidates:
            if len(open_positions) < slots:
                open_positions.append({"exit_date": row["outcome_exit_date"],
                                       "ticker": row["ticker"]})
                row["slots_in_use_at_entry"] = len(open_positions)
                taken.append(row)
            else:
                row["reason_skipped"] = "no_slot_available"
                skipped.append(row)

    return pd.DataFrame(taken), pd.DataFrame(skipped)


def summarise(label, taken, total_signals):
    r = taken["outcome_realized_R"]
    print(f"  {label:<26} n={len(r):4d} "
          f"({len(r) / total_signals * 100:4.1f}% of signals)  "
          f"avgR={r.mean():+.3f}  win={(r > 0).mean() * 100:4.1f}%  "
          f"totalR={r.sum():+8.1f}")
    return r.sum()


# ------------------------------------------------------------------
# MAIN
# ------------------------------------------------------------------
def main():
    rank_fn, note = load_rank_signals()
    print(f"order_candidates: {note}")
    using_production_rule = rank_fn is not None
    if not using_production_rule:
        print("  WARNING: orchestrator.order_candidates could not be imported, so")
        print("  candidates are NOT being ranked -- the fallback preserves the")
        print("  order given, which is alphabetical. The 'ranked' row below is")
        print("  therefore NOT production behaviour. Fix the import; do not")
        print("  read these numbers as a result.")
        rank_fn = _fallback_rank_signals

    df = load_staged()
    total = len(df)
    print(f"\nStaged passing trades (outliers trimmed): {total}")
    print(f"Slot limit: {MAX_CONCURRENT_POSITIONS}\n")

    # How bad is the constraint, before any rule is applied?
    events = []
    for _, row in df.iterrows():
        events.append((row["entry_date"], 1))
        events.append((row["outcome_exit_date"], -1))
    events.sort()
    cur, occupancy = 0, []
    for _, delta in events:
        cur += delta
        occupancy.append(cur)
    occ = pd.Series(occupancy)
    print("Uncapped concurrent positions: "
          f"median={occ.median():.0f}  max={occ.max():.0f}  "
          f"pct of time above limit={(occ > MAX_CONCURRENT_POSITIONS).mean() * 100:.1f}%\n")

    print("Replay results:")
    taken, skipped = replay(df, rank_fn, order="ranked")
    summarise("date-seeded shuffle (prod)" if using_production_rule
              else "UNRANKED FALLBACK -- invalid", taken, total)

    alpha_taken, _ = replay(df, rank_fn, order="alphabetical")
    summarise("alphabetical (incumbent)", alpha_taken, total)

    sums = []
    for s in range(RANDOM_BASELINE_SEEDS):
        rnd, _ = replay(df, rank_fn, order="random", seed=s)
        sums.append(rnd["outcome_realized_R"].sum())
    print(f"  {'random baseline':<26} {RANDOM_BASELINE_SEEDS} seeds: "
          f"mean totalR={np.mean(sums):+8.1f}  "
          f"range {min(sums):+.1f} .. {max(sums):+.1f}")
    print("\n  NOTE: the production rule is EXPECTED to land inside the random")
    print("  range -- it is a date-seeded shuffle, not a ranking rule. Nothing")
    print("  tested beats arbitrary selection. If a future change makes this")
    print("  row beat the random range, verify the test is point-in-time")
    print("  before believing it -- that is exactly how the least-correlated")
    print("  rule was wrongly adopted on 2026-09-15. See")
    print("  General_Research_Findings.md.")

    print(f"\nSkipped for want of a slot: {len(skipped)} "
          f"({len(skipped) / total * 100:.1f}% of signals)")
    if len(skipped):
        sr = skipped["outcome_realized_R"]
        print(f"  Those skipped trades would have returned "
              f"avgR={sr.mean():+.3f}, totalR={sr.sum():+.1f}")

    taken.to_csv(TAKEN_OUT_PATH, index=False)
    skipped.to_csv(SKIPPED_OUT_PATH, index=False)
    print(f"\nWrote {TAKEN_OUT_PATH} ({len(taken)} rows) and "
          f"{SKIPPED_OUT_PATH} ({len(skipped)} rows).")


# ------------------------------------------------------------------
# FALLBACK ONLY -- see load_rank_signals(). Do not develop this.
# ------------------------------------------------------------------
def _fallback_rank_signals(signals, target_date):
    """NOT an ordering rule. Returns the order it was given, so that the
    script still completes when the orchestrator import fails. Every
    caller path that reaches this prints a warning and relabels its
    output as invalid. Do not develop this into a second implementation
    of order_candidates -- that is exactly the drift the import exists to
    prevent."""
    return signals


if __name__ == "__main__":
    main()
