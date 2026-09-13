"""
Step 2 of the historical backtesting sample build.

Takes wide_universe_snapshot.csv (1,593 tickers pulled live from
TradingView on 2026-09-10) and bulk-downloads 3 years of daily OHLCV
for every ticker via yfinance, saving one combined CSV.

This is a ONE-TIME (or occasional) research pull -- not part of the
live production pipeline. Run this in Colab, where yfinance has real
internet access.

Usage in Colab:
    1. Upload wide_universe_snapshot.csv to the Colab session.
    2. pip install yfinance (if not already installed)
    3. Run this script.
    4. Download the resulting historical_prices_3yr.csv the same way
       you downloaded the universe snapshot.
"""

import time
import pandas as pd
import yfinance as yf

UNIVERSE_FILE = "wide_universe_snapshot.csv"
OUTPUT_FILE = "historical_prices_3yr.csv"
YEARS_BACK = 3
CHUNK_SIZE = 60          # tickers per yfinance batch call
PAUSE_SECONDS = 2        # pause between batches to avoid rate limiting

def tv_ticker_to_yahoo(tv_ticker: str) -> str:
    """Convert 'NASDAQ:BRK.B' style TradingView ticker to Yahoo style 'BRK-B'."""
    raw_symbol = tv_ticker.split(":")[-1]
    return raw_symbol.replace(".", "-")

def main():
    universe = pd.read_csv(UNIVERSE_FILE)
    tickers = [tv_ticker_to_yahoo(t) for t in universe["ticker"]]
    tickers = sorted(set(tickers))  # dedupe just in case
    print(f"Loaded {len(tickers)} unique tickers from {UNIVERSE_FILE}")

    end_date = pd.Timestamp.today().normalize()
    start_date = end_date - pd.DateOffset(years=YEARS_BACK)
    print(f"Pulling daily data from {start_date.date()} to {end_date.date()}")

    all_frames = []
    failures = []

    for i in range(0, len(tickers), CHUNK_SIZE):
        chunk = tickers[i:i + CHUNK_SIZE]
        batch_num = i // CHUNK_SIZE + 1
        total_batches = (len(tickers) + CHUNK_SIZE - 1) // CHUNK_SIZE
        print(f"Batch {batch_num}/{total_batches}: {len(chunk)} tickers...")

        try:
            raw = yf.download(
                chunk,
                start=start_date,
                end=end_date,
                group_by="ticker",
                threads=True,
                progress=False,
                auto_adjust=False,
            )
        except Exception as e:
            print(f"  Batch failed entirely: {e}")
            failures.extend(chunk)
            time.sleep(PAUSE_SECONDS)
            continue

        for ticker in chunk:
            try:
                if len(chunk) == 1:
                    df = raw.copy()
                else:
                    df = raw[ticker].copy()
                df = df.dropna(how="all")
                if df.empty:
                    failures.append(ticker)
                    continue
                df = df.reset_index()
                df["ticker"] = ticker
                all_frames.append(df)
            except (KeyError, Exception):
                failures.append(ticker)

        time.sleep(PAUSE_SECONDS)

    combined = pd.concat(all_frames, ignore_index=True)
    combined.to_csv(OUTPUT_FILE, index=False)

    print(f"\nDone.")
    print(f"Successfully pulled: {len(tickers) - len(failures)} tickers")
    print(f"Failed / no data: {len(failures)} tickers")
    if failures:
        print(f"Failed tickers: {failures}")
    print(f"Saved combined price history to {OUTPUT_FILE}")
    print(f"Total rows: {len(combined)}")

if __name__ == "__main__":
    main()
