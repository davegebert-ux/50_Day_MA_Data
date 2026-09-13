"""
Batch-scores every touch event in Historical_Touches_WideUniverse_v1.csv
using scorecard.py's score_total_v2() -- the VALIDATED 2-attribute MVP
score -- then feeds each scored trade through sim.py's simulate_trail()
to get real simulated trade outcomes.

FIX (2026-09-13) -- WRONG SCORER. Both this script and its v1 predecessor
called scorecard.score_trade(), which sums NINE attributes on a 0-45
scale. That is NOT the validated score. The MVP score adopted on
2026-09-03 is score_total_v2(): the average of MA Respect v5 and
Relative Strength v2 only, on a 0-5 scale. score_trade() still includes
Trend Efficiency (demoted 2026-09-03 for near-zero outcome correlation),
six further attributes never individually validated, and -- in this
wide-universe run specifically -- a CONSTANT 5/5 earnings-proximity
score, because earnings data is unavailable and score_earnings_proximity()
assumes ample runway when passed None.

Consequence: every score-tier cut previously drawn from this output
(including the unexplained top-quartile inversion in the ADR-band
analysis) rests on a retired scorer and must be re-run against
total_score_v2 before any conviction-sizing work is built on it. The
sizing skip threshold of 2.5 is on the 0-5 scale, so it is only
meaningful against total_score_v2.

score_trade() is still called alongside, purely so the nine individual
attribute scores stay available for attribute-level research. Its sum is
written as legacy_total_score_9attr and must NOT be used for tiering.

This is the connector between the new wide-universe touch dataset and the
existing scoring/simulation engine -- the actual prerequisite for
revisiting Phase 2 tuning items (trail rules, conviction sizing, 7% cap,
etc.) with a much larger sample than the original 456 events.

Earnings data is NOT available for this wide-universe backtest run. That
is harmless for total_score_v2 (which does not use earnings at all) but
is precisely why the legacy 9-attribute total is unusable for tiering:
one of its nine components is pinned at 5/5 for every single event.

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
    Historical_Backtest_Scored_Trades_v3_scorev2.csv -- one row per touch
    event, with total_score_v2 (0-5, THE tiering column), its two
    component attribute scores, the legacy 9-attribute total for
    reference only, and the simulated trade outcome (rule='atr_1.0x',
    take_partial=False, cap_pct=7%, per
    Cap_Percent_and_Trail_Rule_Resweep_Findings_v1.md).
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
OUTPUT_FILE = "Historical_Backtest_Scored_Trades_v3_scorev2.csv"

# FIX (2026-09-13): was "20ma". The locked production trail rule is
# atr_1.0x (adopted after the resweep: +0.528R, 41.5% win rate, outliers
# excluded) and is already the default in sim.simulate_trail(). Pinning
# "20ma" here silently backtested a retired rule.
RECOMMENDED_RULE = "atr_1.0x"
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
            # --- Scoring stage ---
            # FIX (2026-09-13): total_score_v2 is now THE score. It needs
            # the dataframe itself, so load via scorecard's own loader
            # (score_trade() did that internally; score_total_v2() does
            # not).
            score_df = sc.load_ohlcv(ohlcv_path)
            scored_v2 = sc.score_total_v2(score_df, entry_date, spy_df)

            # Legacy 9-attribute scorer, retained ONLY to keep the
            # individual attribute breakdowns available for research.
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
                # THE tiering column (0-5). May be None when either
                # component attribute could not be computed.
                "total_score_v2": scored_v2.get("total_score_v2"),
                "ma_respect_score": scored_v2.get("ma_respect_score"),
                "relative_strength_score": scored_v2.get("relative_strength_score"),
                "score_v2_note": scored_v2.get("note"),
                # Reference only -- do NOT tier on this. See module
                # docstring.
                "legacy_total_score_9attr": scored["total_score"],
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
    if len(results_df):
        n_null = results_df["total_score_v2"].isna().sum()
        print(f"total_score_v2 unavailable for {n_null} of them "
              f"(excluded from any score-tier analysis).")
        valid = results_df["total_score_v2"].dropna()
        if len(valid):
            print(f"total_score_v2 range: {valid.min():.2f} to "
                  f"{valid.max():.2f}, mean {valid.mean():.2f} (0-5 scale).")
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
