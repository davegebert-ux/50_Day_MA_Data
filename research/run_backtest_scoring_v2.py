"""
Batch-scores every touch event in Historical_Touches_WideUniverse_v1.csv
using scorecard.py's score_trade(), then feeds each scored trade through
sim.py's simulate_trail() to get real simulated trade outcomes.

This is the connector between the new wide-universe touch dataset and the
existing scoring/simulation engine -- the actual prerequisite for
revisiting Phase 2 tuning items (trail rules, conviction sizing, 7% cap,
etc.) with a much larger sample than the original 456 events.

Earnings data is NOT available for this wide-universe backtest run --
score_trade()/score_earnings_proximity() already handles this gracefully
(assumes ample runway, scores 5/5, flags it) when passed None for both
prior_earnings and next_earnings, so this is passed through as a known,
accepted limitation rather than a blocker.

Note: scorecard.py and sim.py each have their OWN OHLCV loader
(load_ohlcv vs load_ticker) with slightly different column conventions
(e.g. sim.py's load_ticker() adds ADR10/MA50, scorecard.py's load_ohlcv()
does not). Both loaders are used here, each for its own stage, rather
than trying to force one shared dataframe -- this matches how each file
is actually meant to be used independently.

Inputs (expected in the working directory):
    Historical_Touches_WideUniverse_v1.csv   -- touch events (ticker, date)
    historical_data/TICKER_1d_data.csv        -- per-ticker OHLCV (one per ticker)
    SPY_1d_data.csv                           -- SPY OHLCV for relative strength

Output:
    Historical_Backtest_Scored_Trades_v1.csv  -- one row per touch event,
    with full scorecard breakdown + simulated trade outcome (using the
    recommended rule='20ma', take_partial=False, cap_pct=7%, per
    Cap_Percent_and_Trail_Rule_Resweep_Findings_v1.md)
"""

import os
import sys
import pandas as pd
import numpy as np

sys.path.insert(0, "/mnt/user-data/outputs")

import scorecard as sc
import sim

TOUCHES_FILE = "Historical_Touches_WideUniverse_v1.csv"
DATA_DIR = "historical_data"
SPY_FILE = "SPY_1d_data.csv"
OUTPUT_FILE = "Historical_Backtest_Scored_Trades_v2_with_attributes.csv"

RECOMMENDED_RULE = "20ma"
RECOMMENDED_TAKE_PARTIAL = False
RECOMMENDED_CAP_PCT = sim.RECOMMENDED_CAP_PCT


def load_spy():
    spy = pd.read_csv(SPY_FILE)
    spy["Date"] = pd.to_datetime(spy["Date"])
    spy["Volume"] = spy["Volume"].astype(str).str.replace(",", "").astype(float)
    spy = spy.sort_values("Date").reset_index(drop=True)
    return spy


def main():
    touches = pd.read_csv(TOUCHES_FILE, parse_dates=["Date"])
    spy_df = load_spy()

    print(f"Loaded {len(touches)} touch events across {touches['Ticker'].nunique()} tickers.")

    results = []
    skipped = []

    for i, touch in touches.iterrows():
        ticker = touch["Ticker"]
        entry_date = touch["Date"]
        ohlcv_path = os.path.join(DATA_DIR, f"{ticker}_1d_data.csv")

        if not os.path.exists(ohlcv_path):
            skipped.append((ticker, str(entry_date), "missing OHLCV file"))
            continue

        try:
            # --- Scoring stage: scorecard.py uses its own loader internally ---
            earnings_row = {"prior_earnings": None, "next_earnings": None}
            scored = sc.score_trade(ticker, entry_date, ohlcv_path, spy_df, earnings_row)

            # --- Simulation stage: sim.py's own loader (adds MA50/ADR10) ---
            sim_df = sim.load_ticker(ohlcv_path)
            entry_rows = sim_df.index[sim_df["Date"] == entry_date].tolist()
            if not entry_rows:
                skipped.append((ticker, str(entry_date), "entry date not found in OHLCV"))
                continue
            entry_idx = entry_rows[0]

            entry_price = sim_df.loc[entry_idx, "MA50"]
            adr10_pct = sim_df.loc[entry_idx, "ADR10"]

            if pd.isna(entry_price) or pd.isna(adr10_pct):
                skipped.append((ticker, str(entry_date), "insufficient data for entry/ADR calc"))
                continue

            risk_per_share = sim.compute_risk_per_share(
                entry_price, adr10_pct, cap_pct=RECOMMENDED_CAP_PCT
            )

            outcome = sim.simulate_trail(
                sim_df, entry_idx, entry_price, risk_per_share,
                rule=RECOMMENDED_RULE, take_partial=RECOMMENDED_TAKE_PARTIAL
            )

            row = {
                "ticker": ticker,
                "entry_date": entry_date.date(),
                "total_score": scored["total_score"],
                "n_missing_attributes": scored["n_missing_attributes"],
                "entry_price": entry_price,
                "risk_per_share": risk_per_share,
                "adr10_pct_at_entry": adr10_pct,
            }
            # Flatten individual attribute scores (for ADR-vs-attribute
            # analysis) -- each attribute key in scored is itself a dict
            # with a 'score' field (0-5), except total_score/n_missing/etc.
            _non_attribute_keys = {"ticker", "entry_date", "total_score", "n_missing_attributes"}
            for attr_key, attr_val in scored.items():
                if attr_key in _non_attribute_keys:
                    continue
                if isinstance(attr_val, dict) and "score" in attr_val:
                    row[f"attr_{attr_key}_score"] = attr_val["score"]
            if isinstance(outcome, dict):
                for k, v in outcome.items():
                    row[f"outcome_{k}"] = v
            else:
                row["outcome_raw"] = outcome

            results.append(row)

        except Exception as e:
            skipped.append((ticker, str(entry_date), f"error: {e}"))
            continue

    results_df = pd.DataFrame(results)
    results_df.to_csv(OUTPUT_FILE, index=False)

    print(f"\nScored and simulated {len(results_df)} trades.")
    print(f"Skipped {len(skipped)} touch events.")
    if skipped:
        from collections import Counter
        reasons = Counter(s[2].split(":")[0] for s in skipped)
        print("Skip reason breakdown:")
        for reason, count in reasons.most_common():
            print(f"   {reason}: {count}")
    print(f"\nSaved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
