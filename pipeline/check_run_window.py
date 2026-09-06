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

EASTERN = ZoneInfo("America/New_York")

# how close (in minutes) the real Eastern clock needs to be to 6pm or 8pm
# for this to count as a legitimate trigger, rather than the "wrong
# season" duplicate cron firing at some other real Eastern hour.
TOLERANCE_MINUTES = 20

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state")
LAST_RUN_PATH = os.path.join(STATE_DIR, "last_run_date.txt")


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

    is_six_pm_window = minutes_from(18, now) <= TOLERANCE_MINUTES
    is_eight_pm_window = minutes_from(20, now) <= TOLERANCE_MINUTES

    should_run = False
    is_retry_slot = False

    if is_six_pm_window:
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
