
"""
Step 3 of the historical backtesting sample build.

Takes historical_prices_3yr.csv (3 years of daily OHLCV for 1,591
tickers) and wide_universe_snapshot.csv (today's market cap per
ticker), computes rolling indicators, and checks 5 of Dave's 8 live
screen criteria plus a static current-market-cap filter at monthly
snapshot dates (first trading day of each month) across the full
3-year window.

Criteria checked historically (5 of 8):
    - SMA50 < Close
    - SMA100 > SMA200
    - ADX(50) between 20 and 40
    - Average 10-day volume > 1,000,000
    - 6-month performance between 30% and 500%

Static filter (approximation, not historically accurate):
    - Current (today's) market cap > $100,000,000

NOT included (too unreliable/unavailable historically):
    - is_symbol_primary_listing
    - total_revenue_yoy_growth_ttm

Tickers with incomplete 3-year history (recent IPOs etc.) are
excluded entirely, per Dave's decision, to keep the sample clean.

This is a ONE-TIME (or occasional) research script -- not part of
the live production pipeline. Run in Colab.

Usage in Colab:
    1. Upload historical_prices_3yr.csv and wide_universe_snapshot.csv
    2. Run this script.
    3. Download historical_candidates_sample.csv
"""

import numpy as np
import pandas as pd

PRICES_FILE = "historical_prices_3yr.csv"
UNIVERSE_FILE = "wide_universe_snapshot.csv"
OUTPUT_FILE = "historical_candidates_sample.csv"

MIN_ROWS_REQUIRED = 700   # ~3 years of trading days; drop short-history tickers
MARKET_CAP_MIN = 100_000_000
ADX_MIN, ADX_MAX = 20, 40
VOLUME_MIN = 1_000_000
PERF_6M_MIN, PERF_6M_MAX = 30, 500


def compute_adx(df, period=50):
    """Compute Average Directional Index (Wilder's method) from OHLC data."""
    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.ewm(alpha=1 / period, adjust=False).mean()
    return adx


def tv_ticker_to_yahoo(tv_ticker: str) -> str:
    raw_symbol = tv_ticker.split(":")[-1]
    return raw_symbol.replace(".", "-")


def main():
    prices = pd.read_csv(PRICES_FILE, parse_dates=["Date"])
    universe = pd.read_csv(UNIVERSE_FILE)
    universe["yahoo_ticker"] = universe["ticker"].apply(tv_ticker_to_yahoo)
    market_cap_lookup = dict(zip(universe["yahoo_ticker"], universe["market_cap_basic"]))

    # Drop tickers with incomplete history
    counts = prices.groupby("ticker").size()
    full_history_tickers = counts[counts >= MIN_ROWS_REQUIRED].index
    dropped = len(counts) - len(full_history_tickers)
    print(f"Dropping {dropped} tickers with incomplete history "
          f"(< {MIN_ROWS_REQUIRED} rows). Keeping {len(full_history_tickers)}.")
    prices = prices[prices["ticker"].isin(full_history_tickers)].copy()

    # Monthly snapshot dates: first trading day of each month present in the data
    all_dates = pd.Series(sorted(prices["Date"].unique()))
    all_dates_df = pd.DataFrame({"Date": all_dates})
    all_dates_df["year_month"] = all_dates_df["Date"].dt.to_period("M")
    snapshot_dates = all_dates_df.groupby("year_month")["Date"].min().tolist()
    print(f"Identified {len(snapshot_dates)} monthly snapshot dates from "
          f"{snapshot_dates[0].date()} to {snapshot_dates[-1].date()}")

    results = []

    for ticker, group in prices.groupby("ticker"):
        group = group.sort_values("Date").reset_index(drop=True)

        group["SMA50"] = group["Close"].rolling(50).mean()
        group["SMA100"] = group["Close"].rolling(100).mean()
        group["SMA200"] = group["Close"].rolling(200).mean()
        group["AvgVol10d"] = group["Volume"].rolling(10).mean()
        group["ADX50"] = compute_adx(group, period=50)
        group["Perf6M"] = (group["Close"] / group["Close"].shift(126) - 1) * 100

        mkt_cap = market_cap_lookup.get(ticker, np.nan)

        date_index = group.set_index("Date")

        for snap_date in snapshot_dates:
            if snap_date not in date_index.index:
                continue
            row = date_index.loc[snap_date]

            if pd.isna(row[["SMA50", "SMA100", "SMA200", "AvgVol10d", "ADX50", "Perf6M"]]).any():
                continue

            passes = (
                row["SMA50"] < row["Close"]
                and row["SMA100"] > row["SMA200"]
                and ADX_MIN <= row["ADX50"] <= ADX_MAX
                and row["AvgVol10d"] > VOLUME_MIN
                and PERF_6M_MIN <= row["Perf6M"] <= PERF_6M_MAX
                and (not pd.isna(mkt_cap) and mkt_cap > MARKET_CAP_MIN)
            )

            if passes:
                results.append({
                    "snapshot_date": snap_date.date(),
                    "ticker": ticker,
                    "close": row["Close"],
                    "sma50": row["SMA50"],
                    "sma100": row["SMA100"],
                    "sma200": row["SMA200"],
                    "adx50": row["ADX50"],
                    "avg_vol_10d": row["AvgVol10d"],
                    "perf_6m_pct": row["Perf6M"],
                    "current_market_cap": mkt_cap,
                })

    results_df = pd.DataFrame(results)
    results_df.to_csv(OUTPUT_FILE, index=False)

    print(f"\nDone. Found {len(results_df)} ticker-month candidate hits "
          f"across {len(snapshot_dates)} snapshot dates.")
    if len(results_df) > 0:
        print("\nCandidates per snapshot month:")
        print(results_df.groupby("snapshot_date").size())
    print(f"\nSaved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
