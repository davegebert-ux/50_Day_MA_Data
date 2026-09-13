"""
Splits historical_prices_3yr.csv (one combined file, all tickers) into
individual per-ticker OHLCV CSVs, matching the format
touch_scan_and_momentum_screen.py expects (one file per ticker, named
TICKER_1d_data.csv, columns: Date, Open, High, Low, Close, Volume).

This is a one-time (or occasional) local utility for the historical
backtesting research track -- not part of the live production pipeline.

Usage (can run locally or in Colab):
    python3 split_historical_prices.py

Input:  historical_prices_3yr.csv
Output: a folder called historical_data/ containing one CSV per ticker
"""

import os
import pandas as pd

INPUT_FILE = "historical_prices_3yr.csv"
OUTPUT_DIR = "historical_data"

def main():
    df = pd.read_csv(INPUT_FILE, parse_dates=["Date"])
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    tickers = sorted(df["ticker"].unique())
    print(f"Splitting {len(tickers)} tickers into individual files...")

    written = 0
    for ticker in tickers:
        sub = df[df["ticker"] == ticker].sort_values("Date").reset_index(drop=True)
        out = sub[["Date", "Open", "High", "Low", "Close", "Volume"]].copy()
        # Match sim.py's load_ticker() expectation of whole-share integer
        # volume (its own parser does str->int, which fails on the
        # decimal-formatted volume yfinance provides, e.g. "20957900.0")
        out["Volume"] = out["Volume"].round().astype(int)
        out_path = os.path.join(OUTPUT_DIR, f"{ticker}_1d_data.csv")
        out.to_csv(out_path, index=False)
        written += 1

    print(f"Done. Wrote {written} per-ticker files to {OUTPUT_DIR}/")
    print("Next step: point touch_scan_and_momentum_screen.py's DATA_DIR at this folder and rerun the (fixed) scan.")

if __name__ == "__main__":
    main()
