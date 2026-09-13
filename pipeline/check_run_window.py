#!/usr/bin/env python3
"""
check_run_window.py -- decides whether a given GitHub Actions cron
trigger should actually do anything.

WHY THIS EXISTS: GitHub Actions "schedule" triggers only run on UTC, and
US Eastern time shifts between UTC-4 (EDT, summer) and UTC-5 (EST,
winter) twice a year. The workflow defines FOUR cron triggers (6pm EDT,
6pm EST, 8pm EDT retry, 8pm EST retry) so that the correct one always
exists no matter the time of year -- but that means on any given day,
TWO of those four triggers fire at a real clock time that doesn't
actually correspond to 6pm or 8pm Eastern (the "wrong season" ones).
This script checks the real, current Eastern time and decides whether
THIS run is a legitimate 6pm-ish or 8pm-ish Eastern trigger, or a
no-op that should be skipped entirely.

Also decides whether the 8pm run should count as a "retry" (i.e. whether
the 6pm run for today either failed or never ran), by checking whether
state/last_run_date.txt already reflects today's date -- if it does,
the 6pm run for today already succeeded, so the 8pm trigger has nothing
to do and skips itself.

Writes results to the GITHUB_OUTPUT file so later workflow steps can
read them via steps.check_time.outputs.should_run and
steps.check_time.outputs.is_retry_slot.
"""

import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

EASTERN = ZoneInfo("America/New_York")

# how close (in minutes) the real Eastern clock needs to be to 6pm or 8pm
# for this to count as a legitimate trigger, rather than the "wrong
# season" duplicate cron firing at some other real Eastern hour.
TOLERANCE_MINUTES = 20

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state")
LAST_RUN_PATH = os.path.join(STATE_DIR, "last_run_date.txt")

# FIX (2026-09-13): the same static NYSE calendar the orchestrator uses.
# Single source of truth for "is today a trading day" -- see
# is_trading_day() below.
MARKET_CALENDAR_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "nyse_market_calendar_2026_2029.csv",
)


def is_trading_day(now_eastern):
    """
    FIX (2026-09-13): this script previously checked ONLY the Eastern
    clock hour, with no notion of what day of the week it was -- so the
    weekend crons sailed straight through, the orchestrator found no
    trading days to process, and a summary email went out anyway
    restating Friday's numbers. (Observed Sat 2026-09-12: two identical
    "Friday" summaries.)

    Rather than hardcoding Sat/Sun, this reuses the SAME static NYSE
    calendar file the orchestrator already reads, so weekends AND market
    holidays are excluded from one source of truth and the two cannot
    drift apart.

    Fails OPEN (returns True) if the calendar file is missing or
    unreadable: a stray weekend email is a far cheaper failure than
    silently skipping a real trading day.
    """
    try:
        cal = pd.read_csv(MARKET_CALENDAR_PATH, parse_dates=["date"])
        open_days = set(cal[cal["market_open"] == True]["date"].dt.date)
    except Exception as e:
        print(f"WARNING: could not read market calendar ({e}) -- "
              f"assuming today IS a trading day and proceeding.")
        return True

    today = now_eastern.date()
    if today not in open_days:
        print(f"{today} is not an NYSE trading day per the market calendar.")
        return False
    return True


def minutes_from(hour, now):
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    return abs((now - target).total_seconds()) / 60


def already_ran_today(now_eastern):
    if not os.path.exists(LAST_RUN_PATH):
        return False
    with open(LAST_RUN_PATH) as f:
        last_run = f.read().strip()
    return last_run == now_eastern.date().isoformat()


def main():
    now = datetime.now(EASTERN)

    # FIX (2026-09-06): manual force-run escape hatch. The scheduled
    # workflow's manual "Run workflow" button was originally gated by
    # this same Eastern-hour check as the automatic schedule triggers --
    # meaning clicking it outside the 6pm/8pm windows correctly, but
    # unhelpfully, skipped every real step, making it useless for
    # on-demand testing. FORCE_RUN (set via a workflow_dispatch input,
    # passed through as an env var) bypasses the time check entirely when
    # explicitly requested, while leaving the automatic scheduled
    # triggers' behavior completely unchanged.
    force_run = os.environ.get("FORCE_RUN", "false").lower() == "true"

    if force_run:
        should_run = True
        is_retry_slot = False
        print(f"Current Eastern time: {now.isoformat()}")
        print("FORCE_RUN=true -- bypassing Eastern-hour check for manual test run.")
        print(f"should_run: {should_run}, is_retry_slot: {is_retry_slot}")
    else:
        is_six_pm_window = minutes_from(18, now) <= TOLERANCE_MINUTES
        is_eight_pm_window = minutes_from(20, now) <= TOLERANCE_MINUTES

        should_run = False
        is_retry_slot = False

        # FIX (2026-09-13): weekend/holiday gate, checked BEFORE the hour
        # windows so a Saturday or holiday trigger is a clean no-op.
        trading_day = is_trading_day(now)

        if not trading_day:
            should_run = False
            is_retry_slot = is_eight_pm_window
        elif is_six_pm_window:
            should_run = True
            is_retry_slot = False
        elif is_eight_pm_window:
            is_retry_slot = True
            # only run the 8pm retry if today hasn't already been recorded as
            # a successful run (i.e. the 6pm run either failed or never
            # triggered)
            should_run = not already_ran_today(now)

        print(f"Current Eastern time: {now.isoformat()}")
        print(f"should_run: {should_run}, is_retry_slot: {is_retry_slot}")

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"should_run={'true' if should_run else 'false'}\n")
            f.write(f"is_retry_slot={'true' if is_retry_slot else 'false'}\n")


if __name__ == "__main__":
    main()
