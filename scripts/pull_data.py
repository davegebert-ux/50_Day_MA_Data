#!/usr/bin/env python3
"""
Historical Data Pull for 50-Day MA Bounce Scorecard - Larger Sample Build

Pulls daily OHLCV history from the Yahoo Finance chart API (direct HTTPS
requests, no yfinance dependency) for the live TradingView momentum-screen
universe (see get_tradingview_screen_tickers()) UNIONED with any currently
open positions (see get_open_position_tickers()), and writes one CSV per
ticker in the format:

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

# FIX (2026-09-10): open positions must never silently lose price data just
# because a ticker falls out of the screen/index universe on a later day.
# STATE_DIR / OPEN_POSITIONS_PATH mirror the same resolution logic used in
# pipeline/orchestrator.py, so both scripts agree on exactly where this
# file lives regardless of working directory.
STATE_DIR = REPO_ROOT / "state"
OPEN_POSITIONS_PATH = STATE_DIR / "open_positions.csv"

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


def get_tradingview_screen_tickers():
    """
    Pull today's momentum-screen candidate list directly from TradingView,
    via the `tradingview-screener` package (unofficial wrapper around
    TradingView's own /screener API endpoint -- see
    Architecture_and_Scope_v1.md, 2026-09-10 sections, for the live
    prototyping that confirmed this works and documents the exact field
    names used below).

    REPLACES the old get_sp400_sp600_tickers() Wikipedia-scrape approach
    (2026-09-10 decision): the universe is no longer defined by index
    membership at all -- whatever passes this screen IS the universe for
    that day. Mirrors Dave's live manual TradingView screen
    ("Momentum - Top Performers 6M") criterion for criterion:
      - market cap > $100M
      - TTM revenue growth (YoY) > 0%
      - 6-month performance between 30% and 500%
      - primary listing only
      - SMA100 > SMA200
      - average 10-day volume > 1,000,000
      - SMA50 < price
      - ADX(50) between 20 and 40 (confirmed field name `ADX_50` via live
        lookup against TradingView's own field reference, 2026-09-10)
    """
    from tradingview_screener import Query, Column

    query = (
        Query()
        .select(
            'name', 'close', 'market_cap_basic', 'volume',
            'average_volume_10d_calc', 'Perf.6M', 'SMA50', 'SMA100',
            'SMA200', 'is_symbol_primary_listing',
            'total_revenue_yoy_growth_ttm', 'ADX_50',
        )
        .where(
            Column('market_cap_basic') > 100_000_000,
            Column('Perf.6M').between(30, 500),
            Column('SMA50') < Column('close'),
            Column('SMA100') > Column('SMA200'),
            Column('average_volume_10d_calc') > 1_000_000,
            Column('is_symbol_primary_listing') == True,
            Column('total_revenue_yoy_growth_ttm') > 0,
            Column('ADX_50').between(20, 40),
        )
        .limit(2000)  # comfortably above any realistic daily match count
    )

    count, df = query.get_scanner_data()

    universe = {}  # symbol -> (index, security name)
    for _, row in df.iterrows():
        raw_ticker = str(row["ticker"]).strip()  # e.g. "NASDAQ:MU"
        symbol = raw_ticker.split(":")[-1]
        # Yahoo Finance uses '-' for share classes (e.g. BRK-B), TradingView
        # uses '.' (e.g. BRK.B) -- same conversion the old Wikipedia-based
        # function applied.
        yahoo_symbol = symbol.replace(".", "-")
        name = str(row.get("name", "")).strip()
        if yahoo_symbol not in universe:
            universe[yahoo_symbol] = ("TradingView Screen", name, symbol)

    return universe


def get_open_position_tickers():
    """
    Read the current open positions from state/open_positions.csv (if it
    exists) and return the set of tickers held.

    FIX (2026-09-10): an open position can legitimately fall out of the
    S&P 400/600 (or, going forward, out of the live TradingView screen)
    without the trade itself being closed -- momentum fades, fundamentals
    change, index reconstitution happens, etc. If daily data collection
    is gated on index/screen membership alone, that position's price
    history silently goes stale and check_open_positions_for_exits() in
    orchestrator.py loses the ability to detect a stop-out or update a
    trailing stop. This is a correctness requirement independent of
    whatever the universe-scope definition is -- see
    Architecture_and_Scope_v1.md, 2026-09-09 and 2026-09-10 sections.
    """
    if not OPEN_POSITIONS_PATH.exists():
        return set()
    try:
        df = pd.read_csv(OPEN_POSITIONS_PATH)
    except Exception:
        return set()
    if "ticker" not in df.columns or df.empty:
        return set()
    return set(df["ticker"].dropna().astype(str).str.strip())


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
    print("Querying live TradingView momentum screen...")
    universe = get_tradingview_screen_tickers()
    print(f"Screen matched {len(universe)} unique tickers today")

    # FIX (2026-09-10): union in tickers from any currently open position,
    # even if they've since fallen out of today's screen results -- see
    # get_open_position_tickers() docstring for why this matters.
    open_position_tickers = get_open_position_tickers()

    # FIX (2026-09-19): SPY must ALWAYS be pulled, and never was.
    # The relative-strength half of the score is computed against SPY, so
    # orchestrator.py reads data/SPY_1d_data.csv on every scored signal --
    # but the pull universe is defined by the TradingView momentum screen,
    # and SPY is an index ETF that cannot pass a momentum screen by
    # construction. So SPY was never fetched, the file never existed, and
    # EVERY genuine signal died at the no_spy_data gate: confirmed in
    # state/daily_funnel.csv for 2026-09-15 and 2026-09-16, where GEO,
    # NXDR and GEO again detected a real 50-day MA touch, priced the entry
    # correctly, and were then dropped at no_spy_data. The system had
    # produced zero trades since going live for this reason alone.
    # Unioned in unconditionally, the same way open positions are, because
    # like them it is a correctness requirement independent of whatever
    # the screen returns on any given day.
    BENCHMARK_TICKERS = {"SPY"}
    added_tickers = (open_position_tickers | BENCHMARK_TICKERS) - set(universe.keys())
    if added_tickers:
        print(f"Adding {len(added_tickers)} required ticker(s) (benchmark / open position) not in "
              f"today's screen results: {sorted(added_tickers)}")
        for sym in added_tickers:
            universe[sym] = ("Benchmark" if sym in BENCHMARK_TICKERS
                             else "Open Position (outside screen)", "", sym)

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

    # ------------------------------------------------------------------
    # LATEST-BAR SUMMARY (added 2026-09-23)
    #
    # WHY: on 2026-09-22 and 2026-09-23 the pull reported 67 successes /
    # 0 failures while every file it wrote still ended 2026-09-21 --
    # Yahoo returned a valid 900-row payload that simply did not contain
    # the most recent session. "Success" here only means the HTTP call
    # worked and the payload parsed; it says nothing about how RECENT
    # the data is. That distinction was invisible in the log, so the
    # orchestrator was the first thing to notice, one step too late.
    #
    # This prints the distribution of last-bar dates actually received.
    # It is the difference between "Yahoo isn't serving Tuesday" and
    # "Yahoo served Tuesday and we dropped it in parsing" -- two
    # different bugs in two different places.
    # ------------------------------------------------------------------
    end_dates = {}
    for r in successes:
        if r.get("end"):
            end_dates[r["end"]] = end_dates.get(r["end"], 0) + 1

    if end_dates:
        newest = max(end_dates)
        print(f"\nNewest bar received: {newest} "
              f"({end_dates[newest]} of {len(successes)} tickers)")
        print("Last-bar date distribution:")
        for d in sorted(end_dates, reverse=True)[:5]:
            print(f"  {d}: {end_dates[d]} tickers")
        stale = [r["symbol"] for r in successes if r.get("end") != newest]
        if stale:
            print(f"Tickers behind the newest bar ({len(stale)}): "
                  f"{', '.join(sorted(stale)[:15])}"
                  f"{' ...' if len(stale) > 15 else ''}")
    else:
        print("\nNewest bar received: NONE -- no successful pulls.")

    return results, universe


if __name__ == "__main__":
    main()
