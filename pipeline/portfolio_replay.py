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

DOLLAR RETURNS AND COMPOUNDING (added 2026-09-15)
-------------------------------------------------
The R-based replay above answers "what multiple of risk did the rules
capture". It cannot answer "what did the ACCOUNT do", because R is
scale-free: it does not know that ten positions at a 10% cost cap
consume the entire account, and it cannot show a drawdown or an
underwater stretch.

replay_dollars() adds that layer. It sizes every trade exactly as
orchestrator.size_and_open_trades() does -- shares = min(risk-based,
cost-cap-based), MAX_POSITION_COST_PCT applied -- tracks capital
committed by open positions, refuses entries it cannot fund, and
compounds realised P&L back into the balance.

READ THE OUTPUT AS A STRESS TEST OF THE RULES, NOT A FORECAST.
Compounding one sample's returns does not validate them, it magnifies
whatever is already in there, including the known flaws: survivorship
bias in the ticker universe, optimistic gap fills, and a two-year window
containing no serious market break. What the equity curve DOES tell you
honestly is structural -- drawdown depth, time underwater, and whether
the slot count and cost cap are a sensible pairing -- because those
depend on the SHAPE of the return stream rather than on the edge being
exactly the size measured.

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

# NOTE (2026-09-19): the equity curve built here counts CLOSED equity only --
# open positions are not marked to market. Measured on the 10yr sample, this
# UNDERSTATES peak drawdown by roughly 4 percentage points (-22.5% closed vs
# -26.6% marked-to-market under the old trail rule). Quote drawdown from this
# script with that caveat, or mark open positions to market before reporting.
#
# ALSO UNRESOLVED: a research harness reproducing this logic produced final
# equity near $1.0M on the 10yr sample where this script produced roughly
# $274k. The per-configuration COMPARISONS agreed; the absolute levels did
# not. Until that is reconciled, treat absolute dollar figures from either
# source as unverified. See Architecture_and_Scope_v1.md, 2026-09-19.
PIPELINE_DIR = "pipeline"          # where orchestrator.py lives
MAX_CONCURRENT_POSITIONS = 8       # CHANGED 2026-09-19 (was 10). Matches
                                   # the 8-position basis behind
                                   # # CHANGED 2026-09-19: 0.10 -> 0.125, paired with MAX_CONCURRENT_POSITIONS
# 10 -> 8. MUST match orchestrator.py -- size_position() below is a SECOND
# implementation of the orchestrator's sizing logic, and is the one known
# place where the replay can silently drift from production. If you edit
# sizing in one file, edit it in the other in the same commit.
MAX_POSITION_COST_PCT = 0.125
EXCLUDE_R_OUTLIER_PCT = 0.01       # top/bottom 1%, same convention as all
                                   # other findings in this project
RANDOM_BASELINE_SEEDS = 30

TAKEN_OUT_PATH = "Portfolio_Replay_Taken.csv"
SKIPPED_OUT_PATH = "Portfolio_Replay_Skipped.csv"
EQUITY_CURVE_OUT_PATH = "Portfolio_Replay_Equity_Curve.csv"

# --- dollar model (added 2026-09-15) ---
# These mirror pipeline/parameters.py and orchestrator.py. They are
# duplicated rather than imported because this script must run from a
# research-branch checkout that may not carry pipeline/ -- but they are
# only ACCOUNT settings, not logic. The sizing rule itself is
# reimplemented in size_position() below and must be kept in step with
# orchestrator.size_and_open_trades(); see the warning there.
START_BALANCE = 25000.00
RISK_PERCENT_PER_TRADE = 0.01
# CHANGED 2026-09-19: 0.10 -> 0.125, paired with MAX_CONCURRENT_POSITIONS
# 10 -> 8. MUST match orchestrator.py -- size_position() below is a SECOND
# implementation of the orchestrator's sizing logic, and is the one known
# place where the replay can silently drift from production. If you edit
# sizing in one file, edit it in the other in the same commit.
MAX_POSITION_COST_PCT = 0.125
DOLLAR_SEEDS = 5                   # arrival-order seeds to average over


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

    report_dollars(df)

    taken.to_csv(TAKEN_OUT_PATH, index=False)
    skipped.to_csv(SKIPPED_OUT_PATH, index=False)
    print(f"\nWrote {TAKEN_OUT_PATH} ({len(taken)} rows) and "
          f"{SKIPPED_OUT_PATH} ({len(skipped)} rows).")



# ------------------------------------------------------------------
# DOLLAR / COMPOUNDING REPLAY (added 2026-09-15)
# ------------------------------------------------------------------
def size_position(entry_price, risk_per_share, basis):
    """Shares for one trade under the two-constraint rule.

    MUST MATCH orchestrator.size_and_open_trades(). It is reimplemented
    here rather than imported because that function is welded to the
    open-positions DataFrame and the funnel log, neither of which exist
    in a replay. That makes this the one place in this file where drift
    is possible -- if the production sizing rule changes, change it here
    and re-run, or the equity curve quietly stops describing the system.

    basis: the account figure both constraints are measured against.
    Passing CURRENT equity compounds; passing START_BALANCE does not.
    """
    if entry_price <= 0 or risk_per_share <= 0:
        return 0
    shares_by_risk = (basis * RISK_PERCENT_PER_TRADE) / risk_per_share
    shares_by_cost = (basis * MAX_POSITION_COST_PCT) / entry_price
    return int(min(shares_by_risk, shares_by_cost))


def replay_dollars(df, slots=MAX_CONCURRENT_POSITIONS, start=START_BALANCE,
                   compound=True, seed=0):
    """Walk forward day by day tracking real dollars.

    Same day ordering as replay(): exits first, then entries, mirroring
    the orchestrator. Candidates are shuffled per day -- arrival order is
    arbitrary by design (see order_candidates), and averaging over
    DOLLAR_SEEDS shows how much of the result is arrival-order luck.

    Equity is CLOSED equity: realised P&L only, marked when a position
    exits. Open positions are not marked to market, so the curve
    understates intra-trade swings -- real drawdowns are deeper than the
    ones reported here. Stated plainly because it cuts the wrong way for
    comfort.

    Returns (final_equity, taken_df, curve_df, skipped_slot, skipped_cash).
    """
    rng = np.random.RandomState(seed)
    equity = start
    open_positions = []
    taken, curve = [], []
    skipped_slot = skipped_cash = 0

    for date, day in df.groupby("entry_date", sort=True):
        still_open = []
        for pos in open_positions:
            if pos["exit_date"] <= date:
                equity += pos["risk_dollars"] * pos["R"]
            else:
                still_open.append(pos)
        open_positions = still_open

        committed = sum(p["cost"] for p in open_positions)
        available = equity - committed
        basis = equity if compound else start

        candidates = day.to_dict("records")
        rng.shuffle(candidates)

        for row in candidates:
            if len(open_positions) >= slots:
                skipped_slot += 1
                continue
            shares = size_position(row["entry_price"], row["risk_per_share"], basis)
            if shares <= 0:
                continue
            cost = shares * row["entry_price"]
            if cost > available:
                # Cannot fund it. This is the dollar equivalent of the
                # funnel log's skipped_no_capital disposition.
                skipped_cash += 1
                continue
            risk_dollars = shares * row["risk_per_share"]
            open_positions.append({"exit_date": row["outcome_exit_date"],
                                   "cost": cost,
                                   "risk_dollars": risk_dollars,
                                   "R": row["outcome_realized_R"],
                                   "ticker": row["ticker"]})
            available -= cost
            taken.append({**row, "shares": shares, "cost": cost,
                          "risk_dollars": risk_dollars,
                          "pnl": risk_dollars * row["outcome_realized_R"]})

        curve.append({"date": date, "equity": equity, "committed": committed,
                      "open_positions": len(open_positions)})

    for pos in open_positions:
        equity += pos["risk_dollars"] * pos["R"]

    return equity, pd.DataFrame(taken), pd.DataFrame(curve), skipped_slot, skipped_cash


def curve_stats(curve):
    """Max drawdown (%) and longest underwater stretch (calendar days)."""
    equity = curve["equity"]
    drawdown = (equity - equity.cummax()) / equity.cummax()
    longest = 0
    peak_value = -np.inf
    peak_date = curve["date"].iloc[0]
    for date, value in zip(curve["date"], equity):
        if value >= peak_value:
            peak_value, peak_date = value, date
        else:
            longest = max(longest, (date - peak_date).days)
    return drawdown.min() * 100, longest


def report_dollars(df):
    """Print the dollar/compounding section of the report."""
    years = (df["entry_date"].max() - df["entry_date"].min()).days / 365.25
    print("\n" + "=" * 62)
    print("DOLLAR RETURNS AND COMPOUNDING")
    print("=" * 62)
    print(f"Starting balance ${START_BALANCE:,.0f}  |  risk "
          f"{RISK_PERCENT_PER_TRADE * 100:.0f}%  |  cost cap "
          f"{MAX_POSITION_COST_PCT * 100:.0f}%  |  {years:.2f} years\n")

    for compound in (False, True):
        finals, dds, uws, counts, cash = [], [], [], [], []
        for seed in range(DOLLAR_SEEDS):
            final, taken, curve, s_slot, s_cash = replay_dollars(
                df, compound=compound, seed=seed)
            max_dd, underwater = curve_stats(curve)
            finals.append(final); dds.append(max_dd); uws.append(underwater)
            counts.append(len(taken)); cash.append(s_cash)
        mean_final = np.mean(finals)
        cagr = ((mean_final / START_BALANCE) ** (1 / years) - 1) * 100
        label = "compounded" if compound else "fixed sizing"
        print(f"  {label:<14} final ${mean_final:>10,.0f}  "
              f"CAGR {cagr:5.1f}%  maxDD {np.mean(dds):6.1f}%  "
              f"underwater {np.mean(uws):4.0f}d  trades {np.mean(counts):.0f}  "
              f"unfunded {np.mean(cash):.0f}")

    print("\n  Slot sweep (compounded), to show what the slot limit buys:")
    for slots in (4, 6, 8, 10, 12, 15, 20):
        finals, dds, cash = [], [], []
        for seed in range(DOLLAR_SEEDS):
            final, taken, curve, s_slot, s_cash = replay_dollars(
                df, slots=slots, seed=seed)
            max_dd, _ = curve_stats(curve)
            finals.append(final); dds.append(max_dd); cash.append(s_cash)
        cagr = ((np.mean(finals) / START_BALANCE) ** (1 / years) - 1) * 100
        print(f"    slots={slots:<3d} final ${np.mean(finals):>10,.0f}  "
              f"CAGR {cagr:5.1f}%  maxDD {np.mean(dds):6.1f}%  "
              f"unfunded {np.mean(cash):.0f}")

    print("\n  READ THIS BEFORE QUOTING THE SLOT SWEEP: above 10 slots every")
    print("  number is IDENTICAL, because 10 positions at a 10% cost cap")
    print("  already consume the whole account -- the unfunded column jumps")
    print("  to show it. The slot limit is therefore not an independent")
    print("  choice; MAX_POSITION_COST_PCT has already made it. Raising the")
    print("  slot count only does something if the cost cap falls with it,")
    print("  and this sweep cannot tell you which PAIRING is better because")
    print("  the two move together.")
    print("\n  Also note the headline CAGR is not a forecast. A strategy that")
    print("  truly compounded at that rate would attract capital until the")
    print("  edge closed. Halve it before believing it, and treat the")
    print("  drawdown as a floor: equity here is CLOSED equity, so real")
    print("  intra-trade drawdowns are deeper.")

    final, taken, curve, _, _ = replay_dollars(df, compound=True, seed=0)
    curve.to_csv(EQUITY_CURVE_OUT_PATH, index=False)
    print(f"\n  Wrote {EQUITY_CURVE_OUT_PATH} ({len(curve)} rows, seed 0).")


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
