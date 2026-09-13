"""
Tests Dave's 3 proposed trail rules (speed_adaptive, graduated_tighten,
atr_multiple at 4 multipliers) against the full 2,310-event wide-universe
sample, alongside the 5 existing rules for direct comparison.
"""

import os
import sys
import pandas as pd
import numpy as np

sys.path.insert(0, "/mnt/user-data/outputs")
import sim
import experimental_trail_rules as ext

TOUCHES_FILE = "/mnt/user-data/outputs/Historical_Touches_WideUniverse_v1.csv"
DATA_DIR = "/mnt/user-data/outputs/historical_data"
OUTPUT_FILE = "/mnt/user-data/outputs/Trail_Rule_Extended_Test_v1.csv"

CAP_PCT = 0.07
EXISTING_RULES = ['10ma', '20ma', 'hybrid_tight', 'adr_adaptive', 'swing_low']
NEW_RULES = ['speed_adaptive', 'graduated_tighten', 'atr_1.0x', 'atr_1.5x', 'atr_2.0x', 'atr_3.0x']
ALL_RULES = EXISTING_RULES + NEW_RULES


def main():
    touches = pd.read_csv(TOUCHES_FILE, parse_dates=["Date"])
    print(f"Loaded {len(touches)} touch events.")

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
                df_t = sim.load_ticker(ohlcv_path)
                df_t['ATR14'] = ext.compute_atr14(df_t)
                ticker_cache[ticker] = df_t

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
        }

        for rule in EXISTING_RULES:
            outcome = sim.simulate_trail(df_t, entry_idx, entry_price, risk_per_share,
                                          rule=rule, take_partial=False)
            row[f"{rule}_realized_R"] = outcome.get("realized_R")

        for rule in NEW_RULES:
            outcome = ext.simulate_trail_extended(df_t, entry_idx, entry_price, risk_per_share, rule=rule)
            row[f"{rule}_realized_R"] = outcome.get("realized_R")

        results.append(row)

    results_df = pd.DataFrame(results)
    results_df.to_csv(OUTPUT_FILE, index=False)

    print(f"\nSimulated {len(results_df)} events across {len(ALL_RULES)} rules. Skipped {skipped}.")
    print(f"Saved to {OUTPUT_FILE}")

    print("\nSummary (mean R, win rate) per rule, sorted best to worst:")
    summary = []
    for rule in ALL_RULES:
        col = f"{rule}_realized_R"
        mean_r = results_df[col].mean()
        win_rate = (results_df[col] > 0).mean()
        summary.append((rule, mean_r, win_rate))
    summary.sort(key=lambda x: -x[1])
    for rule, mean_r, win_rate in summary:
        tag = " (NEW)" if rule in NEW_RULES else ""
        print(f"  {rule:20s}  mean_R={mean_r:+.3f}   win_rate={win_rate:.1%}{tag}")


if __name__ == "__main__":
    main()
