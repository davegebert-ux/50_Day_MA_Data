"""
staged_pipeline_backtest.py -- THE backtest harness.

WHY THIS EXISTS (2026-09-14, Dave's call)
------------------------------------------
Every backtest before this one tested a single criterion against an
open, unfiltered sample of touch events. That measures the criterion;
it does not measure the SYSTEM. It also produces findings that do not
survive contact with the real pipeline -- e.g. "unscorable trades
outperform scorable ones" (true on raw touches, FALSE once overhead
resistance runs first), and "the 5-7% ADR band carries +0.86R" (an
outlier artifact).

Rule adopted: build the sample ONCE at the top of the funnel, then
apply the gates IN PRODUCTION ORDER, measuring after each one. Every
number is then conditional on everything upstream, which is the only
way to read it honestly.

STAGE ORDER (mirrors orchestrator.py's live path)
--------------------------------------------------
  0. Universe          -- the ticker pool (see KNOWN BIAS below)
  1. Momentum screen   -- the 8 TradingView criteria, point-in-time
  2. Touch detection   -- Low <= SMA50 <= High on the day
  3. Overhead resistance -- no unresolved 2yr high above current price
  4. ADR ceiling       -- ADR10 at entry <= ADR_CEILING_PCT
  5. Score gate        -- total_score_v2 >= SKIP_SCORE_THRESHOLD
  6. Simulate          -- entry at SMA50, atr_1.0x trail, R-multiple

Stages 1 and 2 are performed by touch_scan_and_momentum_screen.py and
are assumed already done (its output CSV is this script's input).
Stages 3-6 are applied here, in order, with a report after each.

KNOWN BIAS -- SURVIVORSHIP
---------------------------
The 1,591-ticker universe is a CURRENT snapshot. Names that were
delisted, acquired, or fell below the liquidity floor never enter the
sample, even if they would have passed the screen historically. The
8 criteria themselves ARE evaluated point-in-time, so this is not
lookahead on the rules -- it is a hole in the ticker list. Estimated
impact is modest (roughly -0.1R or less) because the stop caps
per-trade downside, but it is not zero and it flatters results.
A 27-snapshot point-in-time reconstruction exists in
historical_candidates_sample.csv (664 tickers) if a cross-check is
ever wanted.

KNOWN GAP -- OVERNIGHT GAP RISK IS NOT MODELLED
------------------------------------------------
sim.py assumes a stop fills at the stop price. Real overnight gaps
fill at the open, which is worse. Dave took a -5R gap loss in live
trading in Sept 2026. The backtest cannot see this. Treat every
expectancy figure here as slightly optimistic.

USAGE
-----
    python3 staged_pipeline_backtest.py

Inputs (same folder):
    Historical_Touches_WideUniverse_v1.csv
    SPY_1d_data.csv
    historical_data/{TICKER}_1d_data.csv
Outputs:
    Staged_Pipeline_Results.csv   -- every event, with a stage column
                                      recording where it dropped out
    printed stage-by-stage funnel report
"""

import os
import sys
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import scorecard as sc
import sim
from overhead_resistance_and_smoothness_checks import overhead_resistance_check

TOUCHES_FILE = "Historical_Touches_WideUniverse_v1.csv"
SPY_FILE = "SPY_1d_data.csv"
DATA_DIR = "historical_data"
OUTPUT_FILE = "Staged_Pipeline_Results.csv"

# Gate parameters -- keep in sync with orchestrator.py
ADR_CEILING_PCT = 10.0
SKIP_SCORE_THRESHOLD = 2.5
OVERHEAD_PROXIMITY_R = 0.5   # v3 overhead rule -- keep in sync with orchestrator.py

TRAIL_RULE = "atr_1.0x"
TAKE_PARTIAL = False
CAP_PCT = sim.RECOMMENDED_CAP_PCT

OUTLIER_LO, OUTLIER_HI = 0.01, 0.99


def load_spy():
    spy = pd.read_csv(SPY_FILE)
    spy["Date"] = pd.to_datetime(spy["Date"])
    spy["Volume"] = spy["Volume"].astype(str).str.replace(",", "").astype(float)
    return spy.sort_values("Date").reset_index(drop=True)


def report(label, df, prev_n=None):
    """One line of the funnel report, on trades that have outcomes."""
    f = df.dropna(subset=["outcome_realized_R"])
    if len(f) == 0:
        print(f"  {label:<28} n=    0")
        return
    lo, hi = f["outcome_realized_R"].quantile([OUTLIER_LO, OUTLIER_HI])
    e = f[(f["outcome_realized_R"] >= lo) & (f["outcome_realized_R"] <= hi)]
    kept = "" if prev_n is None else f"  ({100*len(f)/prev_n:5.1f}% of prev)"
    print(f"  {label:<28} n={len(f):5}  avgR={e['outcome_realized_R'].mean():6.3f}  "
          f"win={100*(e['outcome_realized_R']>0).mean():5.1f}%  "
          f"totalR={e['outcome_realized_R'].sum():8.1f}{kept}")
    return len(f)


def main():
    touches = pd.read_csv(TOUCHES_FILE, parse_dates=["Date"])
    spy_df = load_spy()
    print(f"STAGE 0-2: {len(touches)} touch events across "
          f"{touches['Ticker'].nunique()} tickers (momentum screen + touch "
          f"detection already applied point-in-time).\n")

    rows = []
    cache = {}
    for _, t in touches.iterrows():
        ticker, entry_date = t["Ticker"], t["Date"]
        path = os.path.join(DATA_DIR, f"{ticker}_1d_data.csv")
        if not os.path.exists(path):
            continue
        if ticker not in cache:
            # sim.load_ticker() adds MA10/MA20/MA50/ADR10/ATR14, which the
            # trail rules require -- a bare read_csv() is not enough.
            cache[ticker] = sim.load_ticker(path)
        df_full = cache[ticker]
        df = df_full[df_full["Date"] <= entry_date].reset_index(drop=True)

        rec = {"ticker": ticker, "entry_date": entry_date,
               "dropped_at": None}

        # --- STAGE 4 inputs computed first: the v3 overhead rule needs
        #     entry_price and risk_per_share to express distance in R.
        #     Mirrors the same reordering made in orchestrator.py. ---

        # --- STAGE 4: ADR ceiling ---
        adr10 = df_full["ADR10"].iloc[len(df) - 1]
        rec["adr10_pct_at_entry"] = round(float(adr10), 3) if pd.notna(adr10) else None

        entry_price_pre = float(df_full["MA50"].iloc[len(df) - 1])
        rps_pre = (sim.compute_risk_per_share(entry_price_pre, float(adr10), CAP_PCT)
                   if pd.notna(adr10) and pd.notna(entry_price_pre) else None)

        # --- STAGE 3: overhead resistance (v3, proximity in R) ---
        try:
            orr = overhead_resistance_check(
                df, entry_date,
                entry_price=entry_price_pre,
                risk_per_share=rps_pre,
                proximity_R=OVERHEAD_PROXIMITY_R,
            )
            rec["or_verdict"] = None if orr is None else orr.get("verdict")
            rec["overhead_R"] = None if orr is None else orr.get("overhead_R")
        except Exception:
            rec["or_verdict"] = "ERROR"
            rec["overhead_R"] = None

        # --- STAGE 5: score ---
        try:
            s = sc.score_total_v2(df_full, entry_date, spy_df)
            rec["total_score_v2"] = s.get("total_score_v2")
            rec["ma_respect_score"] = s.get("ma_respect_score")
            rec["relative_strength_score"] = s.get("relative_strength_score")
            rec["score_note"] = s.get("note")
        except Exception as ex:
            rec["total_score_v2"] = None
            rec["score_note"] = f"error: {ex}"

        # --- STAGE 6: simulate (ALWAYS run, so dropped events still
        #     carry a counterfactual outcome for gate evaluation) ---
        entry_price = float(df_full["MA50"].iloc[len(df) - 1])
        try:
            # simulate_trail() wants the INTEGER ROW INDEX of the entry day
            # within df_full, not the date itself.
            entry_idx = len(df) - 1
            rps = sim.compute_risk_per_share(entry_price, float(adr10), CAP_PCT)
            out = sim.simulate_trail(df_full, entry_idx, entry_price, rps,
                                     rule=TRAIL_RULE, take_partial=TAKE_PARTIAL)
            rec["entry_price"] = round(entry_price, 4)
            rec["risk_per_share"] = round(rps, 4)
            rec["outcome_exit_date"] = out.get("exit_date")
            rec["outcome_exit_reason"] = out.get("exit_reason")
            rec["outcome_realized_R"] = out.get("realized_R")
        except Exception as ex:
            rec["outcome_realized_R"] = None
            rec["sim_error"] = str(ex)

        # record the FIRST gate that would have rejected it
        if rec.get("or_verdict") != "pass":
            rec["dropped_at"] = "3_overhead_resistance"
        elif rec["adr10_pct_at_entry"] is None or rec["adr10_pct_at_entry"] > ADR_CEILING_PCT:
            rec["dropped_at"] = "4_adr_ceiling"
        elif rec["total_score_v2"] is None:
            rec["dropped_at"] = "5_unscorable"
        elif rec["total_score_v2"] < SKIP_SCORE_THRESHOLD:
            rec["dropped_at"] = "5_score_below_threshold"

        rows.append(rec)

    res = pd.DataFrame(rows)
    res.to_csv(OUTPUT_FILE, index=False)

    # ---------- THE FUNNEL ----------
    print("=" * 78)
    print("STAGED FUNNEL -- each line is conditional on every line above it")
    print("(avgR/win/totalR computed with top & bottom 1% of outcomes excluded)")
    print("=" * 78)
    n = report("0-2. raw touch events", res)
    s3 = res[res["or_verdict"] == "pass"]
    n = report("3. + overhead resistance", s3, n)
    s4 = s3[s3["adr10_pct_at_entry"] <= ADR_CEILING_PCT]
    n = report(f"4. + ADR ceiling {ADR_CEILING_PCT}%", s4, n)
    s5a = s4[s4["total_score_v2"].notna()]
    n = report("5a. + scorable", s5a, n)
    s5b = s5a[s5a["total_score_v2"] >= SKIP_SCORE_THRESHOLD]
    report(f"5b. + score >= {SKIP_SCORE_THRESHOLD}", s5b, n)

    # ---------- WHAT EACH GATE THREW AWAY ----------
    print()
    print("=" * 78)
    print("WHAT EACH GATE REJECTED (the counterfactual -- did it earn its keep?)")
    print("=" * 78)
    for stage in ["3_overhead_resistance", "4_adr_ceiling",
                  "5_unscorable", "5_score_below_threshold"]:
        report(f"rejected by {stage}", res[res["dropped_at"] == stage])

    print()
    print(f"Saved per-event detail to {OUTPUT_FILE}")
    print("Column 'dropped_at' records the FIRST gate that rejected each event;")
    print("blank means it passed the full pipeline.")


if __name__ == "__main__":
    main()
