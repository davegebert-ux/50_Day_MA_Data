"""
Re-runs sim.simulate_trail() for all 5 existing trail rules against the
same wide-universe touch dataset (2,310 events), using the current
recommended cap_pct=7 percent and take_partial=False, to get a clean,
larger-sample baseline BEFORE testing any new graduated/hybrid rule
Dave is proposing (start tight, loosen as the trade proves itself;
also segment by how fast the stock is moving).

This does not modify sim.py -- just calls its existing simulate_trail()
multiple times per event with a different `rule` argument.
"""

import os
import sys
import pandas as pd
import numpy as np

sys.path.insert(0, "/mnt/user-data/outputs")
import sim

TOUCHES_FILE = "/mnt/user-data/outputs/Historical_Touches_WideUniverse_v1.csv"
DATA_DIR = "/mnt/user-data/outputs/historical_data"
OUTPUT_FILE = "/mnt/user-data/outputs/Trail_Rule_Resweep_WideUniverse_v1.csv"

CAP_PCT = 0.07
RULES = ['10ma', '20ma', 'hybrid_tight', 'adr_adaptive', 'swing_low']


def main():
    touches = pd.read_csv(TOUCHES_FILE, parse_dates=["Date"])
    print(f"Loaded {len(touches)} touch events.")

    # Cache loaded per-ticker dataframes so we don't reload from disk 5x per event
    ticker_cache = {}
    results = []
    skipped = 0

    for i, touch in touches.iterrows():
        ticker = touch["Ticker"]
        entry_date = touch["Date"]
        ohlcv_path = os.path.join(DATA_DIR, f"{ticker}_1d_data.csv")

        if ticker not in ticker_cache:
            if not os.path.exists(ohlcv_path):
                ticker_cache[ticker] = None
            else:
                ticker_cache[ticker] = sim.load_ticker(ohlcv_path)

        df_t = ticker_cache[ticker]
        if df_t is None:
            skipped += 1
            continue

        entry_rows = df_t.index[df_t["Date"] == entry_date].tolist()
        if not entry_rows:
            skipped += 1
            continue
        entry_idx = entry_rows[0]

        entry_price = df_t.loc[entry_idx, "MA50"]
        adr10_pct = df_t.loc[entry_idx, "ADR10"]
        if pd.isna(entry_price) or pd.isna(adr10_pct):
            skipped += 1
            continue

        risk_per_share = sim.compute_risk_per_share(entry_price, adr10_pct, cap_pct=CAP_PCT)

        row = {
            "ticker": ticker,
            "entry_date": entry_date.date(),
            "entry_price": entry_price,
            "adr10_pct_at_entry": adr10_pct,
            "risk_per_share": risk_per_share,
        }

        for rule in RULES:
            outcome = sim.simulate_trail(
                df_t, entry_idx, entry_price, risk_per_share,
                rule=rule, take_partial=False
            )
            row[f"{rule}_exit_reason"] = outcome.get("exit_reason")
            row[f"{rule}_realized_R"] = outcome.get("realized_R")

        results.append(row)

    results_df = pd.DataFrame(results)
    results_df.to_csv(OUTPUT_FILE, index=False)

    print(f"\nSimulated {len(results_df)} events across all 5 rules. Skipped {skipped}.")
    print(f"Saved to {OUTPUT_FILE}")

    print("\nSummary (mean R, win rate) per rule:")
    for rule in RULES:
        col = f"{rule}_realized_R"
        mean_r = results_df[col].mean()
        win_rate = (results_df[col] > 0).mean()
        print(f"  {rule:15s}  mean_R={mean_r:+.3f}   win_rate={win_rate:.1%}")


if __name__ == "__main__":
    main()
