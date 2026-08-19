#!/usr/bin/env python3
"""
Historical Data Pull for 50-Day MA Bounce Scorecard - Larger Sample Build

Pulls daily OHLCV history from the Yahoo Finance chart API (direct HTTPS
requests, no yfinance dependency) for the S&P 400 (mid-cap) + S&P 600
(small-cap) constituent universe, and writes one CSV per ticker in the
format:

    Date, Open, High, Low, Close, Volume

as {TICKER}_1d_data.csv under data/.

Also writes a machine-readable pull_report.csv and a human-readable
PULL_SUMMARY.md summarizing successes, failures, and the actual date
range achieved per ticker.
"""

import csv
import datetime as dt
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# ~3.6 years of calendar days -> gives a healthy buffer beyond the "at
# least 2, ideally 3 years" requirement, so the earliest usable entry
# date still has 200+ trading days of lookback behind it.
LOOKBACK_DAYS = int(3.6 * 365)
END_TS = int(time.time())
START_TS = END_TS - LOOKBACK_DAYS * 86400

MAX_WORKERS = 8
MAX_RETRIES = 5


def get_sp400_sp600_tickers():
    """Pull current S&P 400 and S&P 600 constituent tickers from Wikipedia."""
    urls = {
        "S&P 400": "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
        "S&P 600": "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies",
    }
    universe = {}  # symbol -> (index, security name)
    for index_name, url in urls.items():
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        table = pd.read_html(StringIO(r.text))[0]
        for _, row in table.iterrows():
            raw_symbol = str(row["Symbol"]).strip()
            name = str(row.get("Security", "")).strip()
            # Yahoo Finance uses '-' for share classes, Wikipedia uses '.'
            yahoo_symbol = raw_symbol.replace(".", "-")
            if yahoo_symbol not in universe:
                universe[yahoo_symbol] = (index_name, name, raw_symbol)
    return universe


def fetch_chart(symbol):
    """Fetch raw chart JSON for one symbol, with retry/backoff on 429s."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {
        "period1": START_TS,
        "period2": END_TS,
        "interval": "1d",
        "events": "div,splits",
        "includeAdjustedClose": "true",
    }
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=25)
        except requests.RequestException as e:
            last_err = f"request error: {e}"
            time.sleep((2 ** attempt) + random.uniform(0, 0.5))
            continue

        if resp.status_code == 200:
            return True, resp.json(), None
        if resp.status_code == 404:
            try:
                desc = resp.json().get("chart", {}).get("error", {}).get(
                    "description", "no data"
                )
            except Exception:
                desc = "no data (404)"
            return False, None, desc
        if resp.status_code == 429:
            last_err = "rate limited (429)"
            time.sleep((2 ** attempt) * 2 + random.uniform(0, 1))
            continue
        # other 4xx/5xx - retry a couple times then give up
        last_err = f"HTTP {resp.status_code}"
        time.sleep((2 ** attempt) + random.uniform(0, 0.5))

    return False, None, last_err or "unknown error"


def parse_chart_json(payload):
    """Convert Yahoo chart JSON into a clean Date/O/H/L/C/Volume DataFrame."""
    result = payload["chart"]["result"][0]
    timestamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]

    df = pd.DataFrame(
        {
            "Date": pd.to_datetime(timestamps, unit="s", utc=True).date,
            "Open": quote.get("open"),
            "High": quote.get("high"),
            "Low": quote.get("low"),
            "Close": quote.get("close"),
            "Volume": quote.get("volume"),
        }
    )

    # Drop rows with no real trading data (holidays / partial-day artifacts)
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    df = df[df["Volume"].notna()]

    df["Volume"] = df["Volume"].round().astype("int64")
    for col in ["Open", "High", "Low", "Close"]:
        df[col] = df[col].round(4)

    df = df.drop_duplicates(subset="Date").sort_values("Date").reset_index(drop=True)
    return df


def process_ticker(symbol):
    ok, payload, err = fetch_chart(symbol)
    if not ok:
        return {"symbol": symbol, "status": "FAILED", "reason": err, "rows": 0,
                "start": None, "end": None}

    try:
        df = parse_chart_json(payload)
    except Exception as e:
        return {"symbol": symbol, "status": "FAILED", "reason": f"parse error: {e}",
                "rows": 0, "start": None, "end": None}

    if df.empty:
        return {"symbol": symbol, "status": "FAILED", "reason": "no usable rows after cleaning",
                "rows": 0, "start": None, "end": None}

    out_path = DATA_DIR / f"{symbol}_1d_data.csv"
    df.to_csv(out_path, index=False, date_format="%Y-%m-%d")

    return {
        "symbol": symbol,
        "status": "OK",
        "reason": "",
        "rows": len(df),
        "start": str(df["Date"].iloc[0]),
        "end": str(df["Date"].iloc[-1]),
    }


def main():
    print("Fetching S&P 400 / S&P 600 constituent lists from Wikipedia...")
    universe = get_sp400_sp600_tickers()
    print(f"Universe size: {len(universe)} unique tickers "
          f"({sum(1 for v in universe.values() if v[0] == 'S&P 400')} S&P 400 + "
          f"{sum(1 for v in universe.values() if v[0] == 'S&P 600')} S&P 600)")

    symbols = sorted(universe.keys())

    results = []
    print(f"Pulling daily OHLCV for {len(symbols)} tickers "
          f"({MAX_WORKERS} concurrent workers)...")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(process_ticker, sym): sym for sym in symbols}
        done = 0
        for fut in as_completed(futures):
            res = fut.result()
            results.append(res)
            done += 1
            if done % 50 == 0 or done == len(symbols):
                elapsed = time.time() - t0
                print(f"  {done}/{len(symbols)} done ({elapsed:.0f}s elapsed)")

    elapsed = time.time() - t0
    print(f"Pull finished in {elapsed:.0f}s")

    # Write machine-readable report
    report_path = REPO_ROOT / "pull_report.csv"
    with open(report_path, "w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["symbol", "index", "security", "status", "reason",
                           "rows", "start", "end"]
        )
        writer.writeheader()
        for r in sorted(results, key=lambda x: x["symbol"]):
            index_name, name, _raw = universe[r["symbol"]]
            row = dict(r)
            row["index"] = index_name
            row["security"] = name
            writer.writerow(row)

    successes = [r for r in results if r["status"] == "OK"]
    failures = [r for r in results if r["status"] != "OK"]

    print(f"\nSuccesses: {len(successes)}")
    print(f"Failures: {len(failures)}")
    for f in sorted(failures, key=lambda x: x["symbol"]):
        print(f"  {f['symbol']}: {f['reason']}")

    return results, universe


if __name__ == "__main__":
    main()
