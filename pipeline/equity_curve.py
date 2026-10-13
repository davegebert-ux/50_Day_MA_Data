"""
equity_curve.py -- one row per processed session, so the account's history
becomes a thing you can chart.

WHAT THIS IS
------------
`state/equity_curve.csv`, appended once per session the orchestrator
processes. Deliberately NOT part of the daily email (Dave, 2026-10-13: this
is a monthly-review artefact, not a daily one) and deliberately NOT a
snapshot file per day -- one row, one line, forever.

WHY A SEPARATE FILE AND NOT MORE OF pnl_report.py
--------------------------------------------------
pnl_report.py answers "where do I stand right now?" and holds no state.
This answers "how did I get here?", which requires state. Those are
different jobs and the daily funnel is the cautionary tale about mixing
them: it grew to thousands of rows because it recorded everything about
every ticker every day. This records ONE row per session. At ~250 trading
days a year that is a file you can still open in Excel in a decade.

IDEMPOTENT BY DATE -- unlike the funnel
----------------------------------------
`state/daily_funnel.csv` is append-only and documents that re-processing a
date WILL duplicate its rows. That is the right call there (the funnel is a
log of what happened on each run). It is the WRONG call here: a duplicated
date puts two points on one day and breaks the chart. So this file is
write-or-replace keyed on date -- a replayed session overwrites its old row
with the recomputed one, which is the correct value.

THE MARK, AND ITS DATE
----------------------
Unrealized P&L needs a price per open position per day. This uses the
ticker's own close ON OR BEFORE that date -- an as-of lookup, not the
newest bar, which is what makes historical backfill honest rather than
marking the whole of September at today's price. `stale_marks` records how
many positions were marked at a price older than the row's own date, for
the same reason pnl_report prints the mark date: a ticker that drops out of
the screen stops being refreshed, and a number computed from a four-day-old
close should say so rather than pass as current.

COLUMNS -- chart-ready, no pivot needed
----------------------------------------
    date, equity, realized_pnl_cum, unrealized_pnl, drawdown_pct,
    open_positions, capital_committed, realized_R_cum, trades_closed_cum,
    stale_marks

`date` and `equity` are the two you chart. Select those two columns in
Excel and insert a line chart -- there is nothing to pivot. `drawdown_pct`
is included because it is the second line anyone actually wants and
deriving it by hand each time is a nuisance.

BACKFILL
--------
`backfill_history()` reconstructs every session from the first entry date
forward, using closed_trades.csv, open_positions.csv and the daily closes
in data/. Without it the curve would start the day this file was installed
and the system's first weeks would be missing. Run once; the live append
takes over from there.
"""

import os

import pandas as pd

import parameters

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STATE_DIR = os.path.join(_REPO_ROOT, "state")
_DATA_DIR = os.path.join(_REPO_ROOT, "data")

EQUITY_CURVE_PATH = os.path.join(_STATE_DIR, "equity_curve.csv")
OPEN_POSITIONS_PATH = os.path.join(_STATE_DIR, "open_positions.csv")
CLOSED_TRADES_PATH = os.path.join(_STATE_DIR, "closed_trades.csv")
SPY_PATH = os.path.join(_DATA_DIR, "SPY_1d_data.csv")

COLUMNS = [
    "date", "equity", "realized_pnl_cum", "unrealized_pnl", "drawdown_pct",
    "open_positions", "capital_committed", "realized_R_cum",
    "trades_closed_cum", "stale_marks",
]

_price_cache = {}


def _prices(ticker):
    """(dates, closes) for a ticker, ascending. Cached -- backfill asks for
    the same ticker on many dates."""
    if ticker in _price_cache:
        return _price_cache[ticker]
    path = os.path.join(_DATA_DIR, f"{ticker}_1d_data.csv")
    out = None
    if os.path.exists(path):
        try:
            df = pd.read_csv(path, usecols=["Date", "Close"],
                             parse_dates=["Date"]).dropna()
            df = df.sort_values("Date").reset_index(drop=True)
            if len(df):
                out = (df["Date"].to_numpy(), df["Close"].to_numpy())
        except Exception:
            out = None
    _price_cache[ticker] = out
    return out


def _close_asof(ticker, when):
    """(close, mark_date) using the last bar ON OR BEFORE `when`.

    An as-of lookup, not the newest bar. Marking a September row at
    today's price would turn the curve into fiction.
    """
    p = _prices(ticker)
    if p is None:
        return None, None
    dates, closes = p
    i = dates.searchsorted(pd.Timestamp(when).to_datetime64(), side="right") - 1
    if i < 0:
        return None, None
    return float(closes[i]), pd.Timestamp(dates[i]).normalize()


def _read(path, parse=None):
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        return pd.read_csv(path, parse_dates=parse) if parse else pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def _positions_as_of(when, closed_df, open_df):
    """Every position held at the close of `when`.

    A closed trade counts as held on `when` if it was entered on or before
    it and exited strictly after it. A still-open position counts from its
    entry date onward.
    """
    when = pd.Timestamp(when).normalize()
    held = []
    if len(closed_df):
        for _, r in closed_df.iterrows():
            ed, xd = r.get("entry_date"), r.get("exit_date")
            if pd.isna(ed) or pd.isna(xd):
                continue
            if pd.Timestamp(ed).normalize() <= when < pd.Timestamp(xd).normalize():
                held.append(r)
    if len(open_df):
        for _, r in open_df.iterrows():
            ed = r.get("entry_date")
            if pd.isna(ed):
                continue
            if pd.Timestamp(ed).normalize() <= when:
                held.append(r)
    return held


def compute_row(as_of, closed_df=None, open_df=None):
    """One row of the curve, as of the close of `as_of`."""
    as_of = pd.Timestamp(as_of).normalize()
    if closed_df is None:
        closed_df = _read(CLOSED_TRADES_PATH, parse=["entry_date", "exit_date"])
    if open_df is None:
        open_df = _read(OPEN_POSITIONS_PATH, parse=["entry_date"])

    realized = 0.0
    realized_R = 0.0
    n_closed = 0
    if len(closed_df) and "exit_date" in closed_df.columns:
        done = closed_df[pd.to_datetime(closed_df["exit_date"], errors="coerce")
                         .dt.normalize() <= as_of]
        realized = float(pd.to_numeric(done.get("realized_pnl"),
                                       errors="coerce").fillna(0).sum())
        realized_R = float(pd.to_numeric(done.get("realized_R"),
                                         errors="coerce").fillna(0).sum())
        n_closed = len(done)

    unrealized = 0.0
    committed = 0.0
    stale = 0
    held = _positions_as_of(as_of, closed_df, open_df)
    for r in held:
        try:
            shares = int(r["shares"])
            entry = float(r["entry_price"])
        except Exception:
            continue
        committed += float(r.get("position_cost") or 0.0)
        mark, mdate = _close_asof(str(r["ticker"]), as_of)
        if mark is None:
            continue
        if mdate is not None and mdate < as_of:
            stale += 1
        unrealized += (mark - entry) * shares

    equity = float(parameters.ACCOUNT_STARTING_BALANCE) + realized + unrealized
    return {
        "date": as_of.date().isoformat(),
        "equity": round(equity, 2),
        "realized_pnl_cum": round(realized, 2),
        "unrealized_pnl": round(unrealized, 2),
        "drawdown_pct": 0.0,          # filled in by _finalise()
        "open_positions": len(held),
        "capital_committed": round(committed, 2),
        "realized_R_cum": round(realized_R, 3),
        "trades_closed_cum": n_closed,
        "stale_marks": stale,
    }


def _finalise(df):
    """Sort, de-duplicate by date, and recompute drawdown from the running
    peak. Drawdown has to be derived over the WHOLE series, so it is
    recomputed on every write rather than stored per row at write time."""
    if df.empty or "date" not in df.columns:
        # Always hand back the declared shape. Returning a bare empty frame
        # made backfill_history() raise KeyError('date') the moment the
        # trading calendar did not overlap the trades -- which happens
        # whenever SPY's bars lag the first entry date.
        return pd.DataFrame(columns=COLUMNS)
    df = df.drop_duplicates(subset="date", keep="last").sort_values("date")
    eq = pd.to_numeric(df["equity"], errors="coerce")
    peak = eq.cummax()
    df["drawdown_pct"] = ((eq - peak) / peak * 100).round(3)
    return df[COLUMNS].reset_index(drop=True)


def record_session(as_of):
    """Write (or replace) the row for `as_of`. Called once per processed
    session by the orchestrator.

    SELF-INSTALLING: the first time this runs, state/equity_curve.csv does
    not exist and the account already has weeks of history behind it. Rather
    than start the curve from whatever day this file happened to be
    installed -- losing the system's first weeks, which cannot be
    reconstructed later once data/ files age out of the 10-day prune -- the
    first call backfills the whole history from the state files, then
    records today on top. There is no manual step: the install is the paste,
    and the next scheduled run builds the past.
    """
    if not os.path.exists(EQUITY_CURVE_PATH):
        print("  Equity curve: no history file yet -- backfilling from "
              "closed_trades.csv and open_positions.csv before recording "
              "this session.")
        try:
            backfill_history(through=as_of)
        except Exception as e:
            print(f"  Equity curve: backfill failed ({type(e).__name__}: {e}); "
                  f"starting the curve from this session instead.")

    row = compute_row(as_of)
    existing = _read(EQUITY_CURVE_PATH)
    if len(existing) and "date" in existing.columns:
        existing = existing[existing["date"].astype(str) != row["date"]]
        df = pd.concat([existing, pd.DataFrame([row])], ignore_index=True)
    else:
        df = pd.DataFrame([row])
    df = _finalise(df)
    os.makedirs(_STATE_DIR, exist_ok=True)
    df.to_csv(EQUITY_CURVE_PATH, index=False)
    return row


def _trading_days(start, end):
    """Session dates between start and end. Uses SPY's own bars as the
    calendar when available -- it is the one ticker always fetched -- and
    falls back to the union of dates across data/ otherwise."""
    start, end = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
    p = _prices("SPY")
    if p is not None:
        d = pd.DatetimeIndex(p[0]).normalize()
    else:
        import glob
        all_dates = set()
        for fp in glob.glob(os.path.join(_DATA_DIR, "*_1d_data.csv")):
            try:
                all_dates |= set(pd.read_csv(fp, usecols=["Date"],
                                        parse_dates=["Date"])["Date"])
            except Exception:
                continue
        d = pd.DatetimeIndex(sorted(all_dates)).normalize()
    return [x for x in d if start <= x <= end]


def backfill_history(through=None):
    """Rebuild the whole curve from the first entry date to `through`.

    Run once when installing this file, so the curve covers the system's
    history instead of starting from the day it was switched on. Safe to
    re-run: it rewrites the file from the state files, which are the
    source of truth.
    """
    closed_df = _read(CLOSED_TRADES_PATH, parse=["entry_date", "exit_date"])
    open_df = _read(OPEN_POSITIONS_PATH, parse=["entry_date"])

    entries = []
    for d in (closed_df.get("entry_date"), open_df.get("entry_date")):
        if d is not None and len(d):
            entries += list(pd.to_datetime(d, errors="coerce").dropna())
    if not entries:
        print("Equity curve: no trades yet -- nothing to backfill.")
        return pd.DataFrame(columns=COLUMNS)

    start = min(entries)
    if through is None:
        ends = list(pd.to_datetime(closed_df.get("exit_date"),
                                   errors="coerce").dropna()) if len(closed_df) else []
        spy = _prices("SPY")
        through = max([start] + ends + ([pd.Timestamp(spy[0][-1])] if spy else []))

    days = _trading_days(start, through)
    if not days:
        # No session dates in range. Rather than write an empty file, fall
        # back to the dates we know are real: the entry and exit dates in
        # the state files themselves.
        print(f"Equity curve: WARNING -- the price calendar covers no "
              f"sessions between {pd.Timestamp(start).date()} and "
              f"{pd.Timestamp(through).date()}. Falling back to trade dates "
              f"only; check that data/SPY_1d_data.csv is current.")
        days = sorted({pd.Timestamp(d).normalize() for d in entries} |
                      {pd.Timestamp(d).normalize()
                       for d in pd.to_datetime(closed_df.get("exit_date"),
                                               errors="coerce").dropna()}
                      if len(closed_df) else
                      {pd.Timestamp(d).normalize() for d in entries})
    rows = [compute_row(d, closed_df, open_df) for d in days]
    df = _finalise(pd.DataFrame(rows))
    if df.empty:
        print("Equity curve: nothing to write.")
        return df
    os.makedirs(_STATE_DIR, exist_ok=True)
    df.to_csv(EQUITY_CURVE_PATH, index=False)
    print(f"Equity curve: backfilled {len(df)} session(s), "
          f"{df['date'].iloc[0]} to {df['date'].iloc[-1]}.")
    return df


if __name__ == "__main__":
    # Standalone: rebuild the file from the state files and print it.
    out = backfill_history()
    if len(out):
        print()
        print(out.to_string(index=False))
