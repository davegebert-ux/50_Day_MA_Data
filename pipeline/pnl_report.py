"""
pnl_report.py -- current profit-and-loss snapshot for the 50-day MA bounce
scorecard system.

WHAT THIS IS
------------
A read-only report built entirely from files the pipeline already writes:
  state/open_positions.csv   -- the live book
  state/closed_trades.csv    -- every finished trade, append-only
  data/{TICKER}_1d_data.csv  -- the last bar's Close, used as the mark

It changes no strategy, holds no state of its own, and writes nothing. It is
safe to run at any point in the Phase 2 freeze because it is an instrument,
not a tuning knob.

WHY IT EXISTS (2026-10-11)
--------------------------
Dave was hand-summing closed_trades.csv to answer "how is the system doing?".
The pipeline could say what it did each day but had no way to say how it was
performing. This is that instrument.

THE MARK, AND WHY ITS DATE IS PRINTED
-------------------------------------
Unrealized P&L needs a current price. Rather than add a second data source,
the mark is the last Close already sitting at the bottom of each ticker's CSV.
That is free and needs no authorisation -- but it is NOT guaranteed to be
today's close. A ticker that drops out of the TradingView momentum screen
stops being fetched, so its file freezes until it qualifies again; on
2026-10-09, 17 of 80 tickers had no bar for the session, and CXW and GEO had
gone several sessions without a fresh one. Marking a position against a
four-day-old close and presenting it as current would be exactly the kind of
confidently-wrong number this project keeps having to dig out.

So every position prints the DATE of its mark, and any mark older than the
session being reported is tagged STALE MARK with a note underneath. The number
is still shown -- it is the best available -- but it is never passed off as
live.

WHAT "INITIAL RISK LIVE" IS NOT
-------------------------------
It is the sum of each open position's entry-day 1R (shares x risk_per_share).
It is NOT where the trailing stops have actually crawled to, because that
would mean replaying every open trade through sim.simulate_trail() on every
run. For positions whose trail has armed, true risk is lower than this figure
-- sometimes negative, i.e. locked-in profit. It is labelled for what it is
rather than overstated.
"""

import os

import pandas as pd

import parameters

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STATE_DIR = os.path.join(_REPO_ROOT, "state")
_DATA_DIR = os.path.join(_REPO_ROOT, "data")

OPEN_POSITIONS_PATH = os.path.join(_STATE_DIR, "open_positions.csv")
CLOSED_TRADES_PATH = os.path.join(_STATE_DIR, "closed_trades.csv")

WIDTH = 72


def _money(x):
    """Signed dollars, always with a sign so gains and losses never blur."""
    return f"{x:+,.2f}"


def _last_bar(ticker):
    """(close, date) of the newest bar on file, or (None, None)."""
    path = os.path.join(_DATA_DIR, f"{ticker}_1d_data.csv")
    if not os.path.exists(path):
        return None, None
    try:
        df = pd.read_csv(path, usecols=["Date", "Close"], parse_dates=["Date"])
    except Exception:
        return None, None
    df = df.dropna(subset=["Date", "Close"])
    if df.empty:
        return None, None
    row = df.loc[df["Date"].idxmax()]
    return float(row["Close"]), pd.Timestamp(row["Date"]).normalize()


def _read(path, parse=None):
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        return pd.read_csv(path, parse_dates=parse) if parse else pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def build_pnl_report(as_of=None):
    """Return the snapshot as plain text.

    as_of is the session being reported -- the processed trading day, not the
    calendar date. It is what the stale-mark test compares against, so during
    a missed-day replay the staleness check still refers to the right session.
    """
    as_of = pd.Timestamp(as_of).normalize() if as_of is not None else None

    open_df = _read(OPEN_POSITIONS_PATH, parse=["entry_date"])
    closed_df = _read(CLOSED_TRADES_PATH, parse=["entry_date", "exit_date"])

    L = []
    L.append("=" * WIDTH)
    L.append("P&L SNAPSHOT" + (f"  (as of session {as_of.date()})" if as_of is not None else ""))
    L.append("=" * WIDTH)

    # ---------------- OPEN BOOK ----------------
    L.append("")
    L.append("OPEN POSITIONS")
    L.append("-" * WIDTH)

    unrealized = 0.0
    committed = 0.0
    initial_risk = 0.0
    any_stale = False
    no_mark = []

    if open_df.empty:
        L.append("  (none)")
    else:
        L.append(f"  {'Ticker':<7}{'Entry':>9}{'Shares':>8}{'Mark':>9}"
                 f"{'Mark date':>12}{'Open $':>11}{'R':>7}")
        for _, p in open_df.iterrows():
            tkr = str(p["ticker"])
            entry = float(p["entry_price"])
            shares = int(p["shares"])
            rps = float(p["risk_per_share"])
            committed += float(p["position_cost"])
            initial_risk += shares * rps

            mark, mdate = _last_bar(tkr)
            if mark is None:
                no_mark.append(tkr)
                L.append(f"  {tkr:<7}{entry:>9.2f}{shares:>8}{'n/a':>9}"
                         f"{'no file':>12}{'-':>11}{'-':>7}")
                continue

            pnl = (mark - entry) * shares
            r = (mark - entry) / rps if rps > 0 else float("nan")
            unrealized += pnl

            stale = as_of is not None and mdate < as_of
            any_stale = any_stale or stale
            L.append(f"  {tkr:<7}{entry:>9.2f}{shares:>8}{mark:>9.2f}"
                     f"{str(mdate.date()):>12}{_money(pnl):>11}{r:>+7.2f}"
                     + ("   STALE MARK" if stale else ""))

        L.append("-" * WIDTH)
        L.append(f"  Unrealized P&L:        {_money(unrealized):>14}")
        L.append(f"  Capital committed:     {committed:>14,.2f}")
        L.append(f"  Initial risk live:     {initial_risk:>14,.2f}"
                 f"   ({len(open_df)} position(s))")
        L.append("    note: initial risk is entry-day 1R summed, NOT where the")
        L.append("    trailing stops now sit -- armed trails risk less than this.")

    if any_stale:
        L.append("")
        L.append("  STALE MARK = that ticker's newest bar predates this session,")
        L.append("  so its file stopped being refreshed (it left the screen, or")
        L.append("  the fetch returned no bar). The P&L shown is as of the date")
        L.append("  printed, not the session date.")
    if no_mark:
        L.append(f"  NO PRICE FILE for: {', '.join(no_mark)} -- excluded from"
                 f" unrealized P&L.")

    # ---------------- REALIZED ----------------
    L.append("")
    L.append("REALIZED (all time)")
    L.append("-" * WIDTH)

    realized_pnl = 0.0
    if closed_df.empty:
        L.append("  (no closed trades yet)")
    else:
        pnl_s = pd.to_numeric(closed_df["realized_pnl"], errors="coerce").dropna()
        r_s = pd.to_numeric(closed_df["realized_R"], errors="coerce").dropna()
        realized_pnl = float(pnl_s.sum())
        wins = pnl_s[pnl_s > 0]
        losses = pnl_s[pnl_s < 0]
        n = len(pnl_s)

        L.append(f"  Trades closed:         {n:>14}")
        L.append(f"  Realized P&L:          {_money(realized_pnl):>14}")
        L.append(f"  Realized R:            {r_s.sum():>+14.2f}")
        L.append(f"  Win rate:              {(100.0 * len(wins) / n if n else 0):>13.1f}%"
                 f"   ({len(wins)}W / {len(losses)}L)")
        L.append(f"  Average winner:        {_money(wins.mean()) if len(wins) else 'n/a':>14}")
        L.append(f"  Average loser:         {_money(losses.mean()) if len(losses) else 'n/a':>14}")
        if n:
            L.append(f"  Average R per trade:   {r_s.mean():>+14.2f}")

    # ---------------- ACCOUNT ----------------
    start = float(parameters.ACCOUNT_STARTING_BALANCE)
    equity = start + realized_pnl + unrealized

    L.append("")
    L.append("ACCOUNT")
    L.append("-" * WIDTH)
    L.append(f"  Starting balance:      {start:>14,.2f}")
    L.append(f"  + Realized:            {_money(realized_pnl):>14}")
    L.append(f"  + Unrealized:          {_money(unrealized):>14}")
    L.append(f"  = Current equity:      {equity:>14,.2f}"
             f"   ({100.0 * (equity - start) / start:+.2f}%)")
    L.append("=" * WIDTH)

    return "\n".join(L)


if __name__ == "__main__":
    # Standalone: `python3 pipeline/pnl_report.py` prints the current picture
    # without touching the daily automation.
    print(build_pnl_report())
