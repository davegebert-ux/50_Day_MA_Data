#!/usr/bin/env python3
"""
send_orders_to_traderspost.py -- dispatch tonight's orders to TradersPost,
which forwards them to the TradeStation account its subscription points at.

READ THIS BEFORE CHANGING ANYTHING. This is the only file in the project
that can cause money to move. Everything else records, measures or reports.

-----------------------------------------------------------------------
IT DEFAULTS TO DRY RUN AND STAYS THERE UNTIL TOLD OTHERWISE
-----------------------------------------------------------------------
TRADERSPOST_MODE must equal the exact string "live" for anything to be
sent. Any other value -- unset, empty, "true", "1", "LIVE ", a typo --
is a dry run. The asymmetry is deliberate: a mistake must fail toward
sending nothing.

Dry run builds the real payloads, prints them, and records them, so the
only untested thing when the switch flips is the network call.

WHY THE SWITCH EXISTS. Dave is entering these orders by hand in the same
simulated account during the first paper week. If this went live while
that continued, every order would be placed twice.

-----------------------------------------------------------------------
WHAT A DRY RUN CANNOT TELL YOU
-----------------------------------------------------------------------
It verifies OUR payloads. It verifies nothing about TradersPost.

The open question is timing. This runs when the orchestrator does, which
has been between 00:17 and 08:17 Eastern -- the market is shut. The
TradersPost docs say orders are queued when the market is closed and
released at the next open, but do not confirm a signal at that hour
qualifies; it depends on whether the broker would reject it outright.
Until one real order has gone into the SIM account and been seen the
next morning, treat overnight delivery as unverified.

-----------------------------------------------------------------------
HTTP 200 DOES NOT MEAN THE ORDER WAS ACCEPTED (learned 2026-10-24)
-----------------------------------------------------------------------
The first live send returned HTTP 200 with {"success":true,"id":...} for
all four orders. TradersPost then REJECTED all four -- the strategy's
"Allowed IP addresses" was still set to TradingView -- and said so in a
separate email, minutes later.

So the 2xx acknowledges the WEBHOOK. Validation happens afterwards and is
reported out of band. This file recorded "sent" and the daily email
reported "4 sent, 0 failed" for four orders that never reached the broker.

That is the same mistake this project has already written down twice: the
green workflow that did no work, and the success counter that measured
HTTP status. It got rebuilt here anyway.

The status is therefore "delivered", never "sent", and every message says
acceptance is unconfirmed. TradersPost publishes no API for querying a
signal's outcome, so there is nothing better to key on from here.

THE DURABLE FIX IS RECONCILIATION, NOT A BETTER STATUS CODE: compare the
system's open positions against the broker's. That catches a rejected
order, a partial fill, a wrong quantity and a manual intervention, all of
which a delivery receipt is blind to. It is the Tier 2 fidelity check
already planned for the paper phase (2026-10-17).

-----------------------------------------------------------------------
TWO SUBSCRIPTION SETTINGS THIS DEPENDS ON
-----------------------------------------------------------------------
Both live in TradersPost, not here, and both silently change what gets
traded if they are wrong:

  1. "Allow signal override" for the entry price MUST be enabled, or
     limitPrice is ignored and the order becomes a market buy at
     whatever the stock opens at. The whole edge of this strategy is the
     fill price (measured 2026-10-17: entering at the close instead of
     the dip turns +833R into -193R), so a silently-marketed entry does
     not degrade the system, it inverts it.
  2. Quantity must come FROM THE SIGNAL, not a fixed size set in
     TradersPost. The docs say an absent quantity "is calculated
     dynamically or defaults to 1".

-----------------------------------------------------------------------
SAFETY RAILS
-----------------------------------------------------------------------
  - Will not send twice for the same session (state/last_orders_sent.txt)
  - Will not send orders for a session at or before the one just
    processed -- that would mean a stale pending_orders.csv
  - Refuses outright above MAX_ORDERS_PER_RUN, which no legitimate night
    can exceed with 8 slots
  - Never prints the webhook URL; anyone holding it can trade the account
  - Every attempt is appended to state/orders_sent.csv whether it is sent
    or not, which is the evidence for the fidelity check

A failure here exits 0 and reports. The orchestrator has already
finished and the session is already correct; an undispatched order is a
missed trade, a crashed workflow step is a missed session.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

import pandas as pd

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STATE_DIR = os.path.join(_REPO_ROOT, "state")
PENDING_ORDERS_PATH = os.path.join(_STATE_DIR, "pending_orders.csv")
ORDERS_SENT_PATH = os.path.join(_STATE_DIR, "orders_sent.csv")
LAST_SENT_PATH = os.path.join(_STATE_DIR, "last_orders_sent.txt")
LAST_RUN_PATH = os.path.join(_STATE_DIR, "last_run_date.txt")

MAX_ORDERS_PER_RUN = 12          # 8 slots plus exits; anything more is a fault
HTTP_TIMEOUT_SECONDS = 20

# The exit is the least certain part of this file. We send a plain market
# order and rely on TradersPost queueing it to the next open, rather than
# tagging it "opg" (at-the-open) as well -- one mechanism instead of two.
# If the sim shows exits filling at the wrong time, "opg" is the next
# thing to try, and this is the line to change.
EXIT_TIME_IN_FORCE = None        # None -> let the subscription default apply

ORDERS_SENT_COLUMNS = [
    "sent_utc", "mode", "for_session", "order_type", "ticker",
    "expected_shares", "limit_price", "stop_price", "status", "detail",
]


def resend_selection():
    """What this run is resending, or None for a normal nightly dispatch.

    TRADERSPOST_RESEND holds a comma-separated ticker list ("CAKE, AMC") or
    the word "all". Set only by the manual Resend workflow.

    WHY A MANUAL LIST AND NOT AN AUTOMATIC RETRY. TradersPost returns
    HTTP 200 and then rejects out of band (see the note above), so nothing
    here can tell which orders actually failed -- the only place that
    knowledge exists is the rejection email in Dave's inbox. An automatic
    retry would therefore have to guess, and guessing wrong means
    duplicate positions. Naming the tickers puts the decision where the
    information is.
    """
    raw = os.environ.get("TRADERSPOST_RESEND", "").strip()
    if not raw:
        return None
    if raw.lower() == "all":
        return "ALL"
    picked = {t.strip().upper() for t in raw.replace(";", ",").split(",") if t.strip()}
    return picked or None


def prior_status(for_session, ticker):
    """What orders_sent.csv last recorded for this ticker and session."""
    try:
        sent = pd.read_csv(ORDERS_SENT_PATH)
        mine = sent[(sent["for_session"].astype(str) == str(for_session)) &
                    (sent["ticker"].astype(str).str.upper() == ticker.upper())]
        return str(mine["status"].iloc[-1]) if len(mine) else None
    except Exception:
        return None


def is_live():
    return os.environ.get("TRADERSPOST_MODE", "").strip() == "live"


def _read_text(path):
    try:
        with open(path) as f:
            return f.read().strip() or None
    except Exception:
        return None


def build_payload(row):
    """One pending_orders.csv row -> one TradersPost webhook body.

    Field names and shapes per docs.traderspost.io/docs/webhooks.
    Deliberately minimal: every field present is one we mean, and an
    absent field falls back to the subscription setting, which is
    visible to Dave in the UI.
    """
    ticker = str(row["ticker"]).strip().upper()
    kind = str(row["order_type"]).strip()

    if kind == "BUY_LIMIT":
        payload = {
            "ticker": ticker,
            "action": "buy",
            "orderType": "limit",
            "limitPrice": round(float(row["limit_price"]), 2),
            "quantity": int(row["shares"]),
            # The bid is good for ONE session, matching the backtest's
            # same-day-only rule. A bid resting across days was explicitly
            # rejected (it readmits the Stage 6b duplication), so this must
            # not become "gtc" for convenience.
            "timeInForce": "day",
            "stopLoss": {
                "type": "stop",
                "stopPrice": round(float(row["stop_price"]), 2),
            },
        }
        return payload

    if kind == "SELL_MOO":
        # "exit" closes the WHOLE position, whatever size the broker
        # actually holds -- not the size we think we hold. That is the
        # right behaviour (it reconciles to reality rather than leaving a
        # stub), and our expected share count is recorded separately in
        # orders_sent.csv so a divergence is visible rather than absorbed.
        payload = {
            "ticker": ticker,
            "action": "exit",
            "orderType": "market",
        }
        if EXIT_TIME_IN_FORCE:
            payload["timeInForce"] = EXIT_TIME_IN_FORCE
        return payload

    raise ValueError(f"unknown order_type {kind!r}")


def post(url, payload):
    """POST one order. Returns (ok, detail). Never raises, never logs url."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as r:
            code = r.status
            text = r.read().decode("utf-8", "replace")[:300]
        return (200 <= code < 300), f"HTTP {code}: {text}"
    except urllib.error.HTTPError as e:
        try:
            text = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            text = ""
        return False, f"HTTP {e.code}: {text}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def record(rows):
    if not rows:
        return
    df = pd.DataFrame(rows, columns=ORDERS_SENT_COLUMNS)
    header = not os.path.exists(ORDERS_SENT_PATH)
    df.to_csv(ORDERS_SENT_PATH, mode="a", header=header, index=False)
    print(f"  Recorded {len(df)} order(s) in state/{os.path.basename(ORDERS_SENT_PATH)}")


def main():
    live = is_live()
    resend = resend_selection()
    mode = ("live" if live else "dry_run") + ("_resend" if resend else "")
    print(f"TradersPost dispatch -- MODE: {mode.upper()}")
    if resend:
        print(f"  RESEND requested: "
              f"{'every order in the current list' if resend == 'ALL' else ', '.join(sorted(resend))}")
    if not live:
        print("  (dry run: payloads are built and recorded, nothing is sent. "
              "Set TRADERSPOST_MODE to exactly 'live' to send.)")

    if not os.path.exists(PENDING_ORDERS_PATH):
        print("  No state/pending_orders.csv -- nothing to dispatch.")
        return
    try:
        orders = pd.read_csv(PENDING_ORDERS_PATH)
    except Exception as e:
        print(f"  Could not read pending_orders.csv ({e}) -- nothing sent.")
        return
    if not len(orders):
        print("  Order list is empty -- nothing to dispatch.")
        return

    for_session = str(orders["for_session"].iloc[0]).strip()

    # STALE-FILE GUARD. pending_orders.csv is rewritten every run, so its
    # session should always be AHEAD of the one just processed. If it is
    # not, the orchestrator did not write a fresh list and these are
    # yesterday's orders.
    last_run = _read_text(LAST_RUN_PATH)
    if last_run and for_session <= last_run:
        print(f"  REFUSING: order list is for {for_session} but the last "
              f"processed session is {last_run}. A fresh list should be for "
              f"a LATER session, so this file is stale. Nothing sent.")
        return

    # ONE DISPATCH PER SESSION -- deliberately bypassed on a resend, which
    # exists precisely because the first dispatch did not take. The stamp
    # is also left UNCHANGED by a resend: it records that the nightly step
    # ran for this session, which stays true either way.
    already = _read_text(LAST_SENT_PATH)
    if already == for_session and not resend:
        print(f"  Orders for {for_session} were already dispatched "
              f"(state/last_orders_sent.txt) -- skipping.")
        return

    if len(orders) > MAX_ORDERS_PER_RUN:
        print(f"  REFUSING: {len(orders)} orders for {for_session} exceeds the "
              f"{MAX_ORDERS_PER_RUN} cap. With 8 slots this cannot be "
              f"legitimate. Nothing sent -- investigate pending_orders.csv.")
        return

    url = os.environ.get("TRADERSPOST_WEBHOOK_URL", "").strip()
    if live and not url:
        print("  REFUSING: TRADERSPOST_MODE is live but "
              "TRADERSPOST_WEBHOOK_URL is not set. Nothing sent.")
        return

    if resend and resend != "ALL":
        have = {str(t).strip().upper() for t in orders["ticker"]}
        missing = sorted(resend - have)
        if missing:
            print(f"  NOT IN THE CURRENT ORDER LIST, skipped: {', '.join(missing)}")
            print(f"  (the list is for {for_session}; a ticker from an older "
                  f"session cannot be resent from here -- enter it by hand)")
        orders = orders[orders["ticker"].astype(str).str.strip().str.upper().isin(resend)]
        if not len(orders):
            print("  Nothing left to resend. Nothing sent.")
            return

    # A resend of something already DELIVERED is usually right -- delivered
    # does not mean accepted -- but it is the one way to end up with two
    # positions in the same ticker, so it is called out per order.
    if resend and live:
        for t in orders["ticker"].astype(str):
            ps = prior_status(for_session, t)
            if ps in ("delivered", "sent"):
                print(f"  WARNING {t}: a previous attempt was DELIVERED. If "
                      f"TradersPost actually accepted it, resending will open "
                      f"a SECOND position. Resend only what you have "
                      f"confirmed was rejected.")

    print(f"  {len(orders)} order(s) for session {for_session}")
    rows, n_ok, n_fail = [], 0, 0
    for _, o in orders.iterrows():
        try:
            payload = build_payload(o)
        except Exception as e:
            print(f"    {o.get('ticker','?')}: COULD NOT BUILD ({e}) -- skipped")
            rows.append({
                "sent_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "mode": mode, "for_session": for_session,
                "order_type": o.get("order_type", ""), "ticker": o.get("ticker", ""),
                "expected_shares": o.get("shares", ""), "limit_price": o.get("limit_price", ""),
                "stop_price": o.get("stop_price", ""), "status": "build_error",
                "detail": str(e)[:200],
            })
            n_fail += 1
            continue

        if live:
            ok, detail = post(url, payload)
            # "delivered", NOT "sent". See the HTTP 200 note at the top of
            # this file: a 2xx means TradersPost took the webhook, not that
            # it accepted the trade.
            status = "delivered" if ok else "failed"
            n_ok += int(ok)
            n_fail += int(not ok)
        else:
            ok, status = True, "dry_run"
            detail = json.dumps(payload)
            n_ok += 1

        print(f"    {status.upper():<9} {payload['ticker']:<7} "
              f"{payload['action']}/{payload.get('orderType','')}"
              + (f" @ {payload['limitPrice']}" if "limitPrice" in payload else "")
              + (f" x{payload['quantity']}" if "quantity" in payload else "")
              + (f" stop {payload['stopLoss']['stopPrice']}" if "stopLoss" in payload else "")
              + ("" if ok else f"  -- {detail}"))

        rows.append({
            "sent_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "mode": mode, "for_session": for_session,
            "order_type": o.get("order_type", ""), "ticker": payload["ticker"],
            "expected_shares": o.get("shares", ""), "limit_price": o.get("limit_price", ""),
            "stop_price": o.get("stop_price", ""), "status": status,
            "detail": detail[:300],
        })

    record(rows)

    # The stamp is written in BOTH modes. A dry run that ran is a dispatch
    # decision already taken for that session: re-running the step must not
    # suddenly send live orders for a session Dave already placed by hand.
    # A RESEND does not touch it -- see the note where it is read.
    if resend:
        print(f"  (resend: state/{os.path.basename(LAST_SENT_PATH)} left "
              f"unchanged)")
        print(f"  Done: {n_ok} delivered, {n_fail} failed, mode {mode}")
        return
    try:
        with open(LAST_SENT_PATH, "w") as f:
            f.write(for_session)
    except Exception as e:
        print(f"  WARNING: dispatched but could not write "
              f"{os.path.basename(LAST_SENT_PATH)} ({e}). A re-run of this "
              f"step could dispatch the same session again.", file=sys.stderr)

    if live:
        print(f"  Done: {n_ok} delivered (ACCEPTANCE NOT CONFIRMED), "
              f"{n_fail} failed to deliver, mode {mode}")
        if n_ok:
            print("  A delivered order can still be REJECTED by TradersPost "
                  "after the fact. Check for rejection emails from them, or "
                  "the TradersPost dashboard, before assuming these worked.")
    else:
        print(f"  Done: {n_ok} prepared, {n_fail} failed, mode {mode}")
    if n_fail and live:
        print("  NOTE: TradersPost never retries a failed order. Anything "
              "marked failed above did NOT reach the broker.", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Never fail the workflow. The session is already complete and
        # committed by the time this runs.
        print(f"Order dispatch failed ({type(e).__name__}: {e}) -- the "
              f"session itself is unaffected.", file=sys.stderr)
