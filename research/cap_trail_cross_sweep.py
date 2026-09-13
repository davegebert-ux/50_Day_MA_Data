"""
Full cross-sweep of cap_pct x trail_rule on the wide-universe sample.

Motivation: the 2026-09-03 cap_pct conclusion (7 percent beats 5 and 3)
was reached using ONLY the 20-day MA trail rule. Since that session's
own trail-rule resweep just showed 20ma is no longer the best-performing
rule on the larger wide-universe sample, the cap_pct conclusion needs to
be re-checked against the rules that now look more competitive, not just
re-confirmed on the one rule that turned out to be weak.

Sweeps: 3 cap percentages (3%, 5%, 7%) x 6 trail rules (the original
20ma and 10ma, plus the 3 candidates that held up best on the
outlier-cleaned check: atr_1.0x, atr_1.5x, hybrid_tight, swing_low) =
18 combinations, each run across all 2,310 wide-universe events.
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
OUTPUT_FILE = "/mnt/user-data/outputs/CapPct_TrailRule_CrossSweep_v1.csv"

CAP_PCTS = [0.03, 0.05, 0.07]
RULES = ['10ma', '20ma', 'hybrid_tight', 'swing_low', 'atr_1.0x', 'atr_1.5x']
EXTENDED_RULES = {'atr_1.0x', 'atr_1.5x', 'speed_adaptive', 'graduated_tighten'}

# The 5 known outlier tickers found in the atr_3.0x investigation --
# tracked separately here too so we can report both with and without them.
OUTLIER_TICKERS = ['COGT', 'VNET', 'NB', 'SSRM', 'RCAT']


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

        row = {
            "ticker": ticker,
            "entry_date": entry_date.date(),
            "is_outlier_ticker": ticker in OUTLIER_TICKERS,
        }

        for cap_pct in CAP_PCTS:
            risk_per_share = sim.compute_risk_per_share(entry_price, adr10_pct, cap_pct=cap_pct)
            for rule in RULES:
                cap_label = f"{int(cap_pct*100)}pct"
                col_name = f"{cap_label}_{rule}_R"
                if rule in EXTENDED_RULES:
                    outcome = ext.simulate_trail_extended(df_t, entry_idx, entry_price, risk_per_share, rule=rule)
                else:
                    outcome = sim.simulate_trail(df_t, entry_idx, entry_price, risk_per_share, rule=rule, take_partial=False)
                row[col_name] = outcome.get("realized_R")

        results.append(row)

    results_df = pd.DataFrame(results)
    results_df.to_csv(OUTPUT_FILE, index=False)

    print(f"\nSimulated {len(results_df)} events across {len(CAP_PCTS)}x{len(RULES)}={len(CAP_PCTS)*len(RULES)} combinations. Skipped {skipped}.")
    print(f"Saved to {OUTPUT_FILE}")

    clean_df = results_df[~results_df['is_outlier_ticker']]

    print("\n=== FULL SAMPLE (all 2,310 events) ===")
    summary = []
    for cap_pct in CAP_PCTS:
        cap_label = f"{int(cap_pct*100)}pct"
        for rule in RULES:
            col = f"{cap_label}_{rule}_R"
            mean_r = results_df[col].mean()
            win_rate = (results_df[col] > 0).mean()
            summary.append((cap_label, rule, mean_r, win_rate))
    summary.sort(key=lambda x: -x[2])
    for cap_label, rule, mean_r, win_rate in summary:
        print(f"  cap={cap_label:5s} rule={rule:15s} mean_R={mean_r:+.3f}  win_rate={win_rate:.1%}")

    print("\n=== OUTLIER-EXCLUDED (5 known outlier tickers removed) ===")
    summary2 = []
    for cap_pct in CAP_PCTS:
        cap_label = f"{int(cap_pct*100)}pct"
        for rule in RULES:
            col = f"{cap_label}_{rule}_R"
            mean_r = clean_df[col].mean()
            win_rate = (clean_df[col] > 0).mean()
            summary2.append((cap_label, rule, mean_r, win_rate))
    summary2.sort(key=lambda x: -x[2])
    for cap_label, rule, mean_r, win_rate in summary2:
        print(f"  cap={cap_label:5s} rule={rule:15s} mean_R={mean_r:+.3f}  win_rate={win_rate:.1%}")


if __name__ == "__main__":
    main()
