#!/usr/bin/env python3
"""
check_run_window.py -- decides whether a given GitHub Actions cron
trigger should actually do anything.

REWRITTEN 2026-09-22: "is the work done?" instead of "what time is it?"

WHY THE OLD DESIGN FAILED: this script used to accept a run only if it
landed within 20 minutes of 6pm or 8pm Eastern. GitHub does not fire
scheduled workflows on time -- its own documentation warns they can be
delayed or dropped under load. On Monday 2026-09-21 the two triggers
that did fire landed at 8:30pm and 1:51am Eastern, both outside the
windows, so both skipped every real step and Monday was never processed.
A clock-window gate turns every late fire into a lost day.

THE NEW RULE: find the most recent NYSE trading session that has
finished (see latest_completed_session() below). If
state/last_run_date.txt is older than that session, run. Otherwise do
nothing. The workflow now fires many times each evening plus once the
next morning; whichever fire lands first does the work, and every later
fire finds the work done and exits in seconds. Firing late, firing
twice, or missing some triggers entirely no longer matters.

state/last_run_date.txt therefore means "the last SESSION processed",
NOT "the calendar date a run happened on". A run at 1:51am Tuesday that
processes Monday must stamp Monday -- stamping Tuesday would make
Tuesday evening's run believe Tuesday was already done.

latest_completed_session() is the SINGLE source of truth for "which day
is finished". orchestrator.py imports it rather than keeping its own
copy -- two copies of a date rule is exactly the kind of drift that has
bitten this project before.

Writes should_run, is_retry_slot and target_session to GITHUB_OUTPUT.
is_retry_slot keeps its old name so the workflow's email step still
works, but now means "late enough that a failure should email Dave":
true from 8pm Eastern onward. Earlier failures stay quiet because the
next hourly fire retries automatically.
"""

import os
import sys
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

EASTERN = ZoneInfo("America/New_York")

# When a trading day counts as "finished" and its daily bar can be
# pulled. Market closes 4:00pm; this leaves time for final prints. The
# old design ran at 6pm with a 20-minute early tolerance (5:40pm), so
# 5:30pm is no earlier than bars have already been pulled in practice.
SESSION_READY_TIME = time(17, 30)

# A failed run only emails if it happens this many hours or more after
# the session became ready (5:30pm + 2.5h = 8:00pm Eastern). Before
# that, a failure is left to the next hourly retry.
FAILURE_EMAIL_AFTER_HOURS = 2.5

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state")
LAST_RUN_PATH = os.path.join(STATE_DIR, "last_run_date.txt")

# The same static NYSE calendar the orchestrator uses.
MARKET_CALENDAR_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "nyse_market_calendar_2026_2029.csv",
)


def load_open_dates():
    """
    Set of datetime.date values on which the NYSE is open, from the
    static calendar. If the file is missing or unreadable, falls back to
    plain Monday-Friday with a loud warning: a stray holiday run is a far
    cheaper failure than silently skipping real trading days.
    """
    try:
        cal = pd.read_csv(MARKET_CALENDAR_PATH, parse_dates=["date"])
        return set(cal[cal["market_open"] == True]["date"].dt.date), False
    except Exception as e:
        print(f"WARNING: could not read market calendar ({e}) -- "
              f"falling back to plain Monday-Friday.")
        return None, True


def latest_completed_session(now=None):
    """
    Returns the most recent NYSE trading day (as a pandas Timestamp at
    midnight) whose session has finished as of `now` (Eastern).

    Today counts only if today is a trading day AND it is past
    SESSION_READY_TIME. Otherwise it is the previous trading day.
      Mon 6:17pm  -> Mon
      Tue 1:51am  -> Mon   (Tuesday's market hasn't opened)
      Tue 12:00pm -> Mon   (Tuesday's bar is still partial)
      Sat any     -> Fri
    Never returns a day whose market hasn't closed, so a run at any hour
    can never process a partial bar or a bar that doesn't exist yet.
    """
    if now is None:
        now = datetime.now(EASTERN)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=EASTERN)
    else:
        now = now.astimezone(EASTERN)

    open_dates, fallback = load_open_dates()

    def is_open(d):
        return d.weekday() < 5 if fallback else d in open_dates

    d = now.date()
    if not (is_open(d) and now.time() >= SESSION_READY_TIME):
        d -= timedelta(days=1)

    # Walk back to the nearest open day. Longest real NYSE closure is a
    # few days; if nothing turns up in 14 days the calendar has run out
    # (it covers 2026-2029) -- fail loudly rather than no-op forever.
    for _ in range(14):
        if is_open(d):
            return pd.Timestamp(d)
        d -= timedelta(days=1)
    raise RuntimeError(
        f"No NYSE trading day found in the 14 days before {now.date()}. "
        f"The market calendar ({MARKET_CALENDAR_PATH}) has probably run "
        f"out and needs extending -- see Architecture_and_Scope_v1.md."
    )


def get_last_run_date():
    if not os.path.exists(LAST_RUN_PATH):
        return None
    with open(LAST_RUN_PATH) as f:
        txt = f.read().strip()
    return pd.Timestamp(txt) if txt else None


def decide(now=None, last_run=None):
    """
    Pure decision function, separated out so it can be tested at any
    simulated time. Returns (should_run, is_retry_slot, target_session).
    """
    if now is None:
        now = datetime.now(EASTERN)
    target = latest_completed_session(now)
    should_run = last_run is None or last_run < target

    ready_at = datetime.combine(target.date(), SESSION_READY_TIME, tzinfo=EASTERN)
    hours_late = (now.astimezone(EASTERN) - ready_at).total_seconds() / 3600
    is_retry_slot = hours_late >= FAILURE_EMAIL_AFTER_HOURS
    return should_run, is_retry_slot, target


def main():
    now = datetime.now(EASTERN)
    last_run = get_last_run_date()

    # Manual force-run escape hatch (FIX 2026-09-06), kept. It no longer
    # bypasses much: the orchestrator still only processes finished
    # sessions, so a midday force-run can never touch today's partial bar.
    force_run = os.environ.get("FORCE_RUN", "false").lower() == "true"

    should_run, is_retry_slot, target = decide(now, last_run)
    if force_run:
        should_run = True
        is_retry_slot = False

    print(f"Current Eastern time: {now.isoformat()}")
    print(f"Latest finished trading session: {target.date()}")
    print(f"Last session processed (state/last_run_date.txt): "
          f"{last_run.date() if last_run is not None else 'none'}")
    if force_run:
        print("FORCE_RUN=true -- running regardless of whether work is pending.")
    elif should_run:
        print("Work pending -- running.")
    else:
        print("Already up to date -- nothing to do.")
    print(f"should_run: {should_run}, is_retry_slot: {is_retry_slot}")

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"should_run={'true' if should_run else 'false'}\n")
            f.write(f"is_retry_slot={'true' if is_retry_slot else 'false'}\n")
            f.write(f"target_session={target.date()}\n")


if __name__ == "__main__":
    main()
