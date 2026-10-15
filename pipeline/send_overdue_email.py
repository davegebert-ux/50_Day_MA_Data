#!/usr/bin/env python3
"""
send_overdue_email.py -- alerts Dave when a trading session is STUCK.

WHY THIS EXISTS (2026-10-15)
----------------------------
Until today a run that deferred a session still sent the previous day's
summary. That was noise, but it was also an accidental heartbeat: mail
arriving meant the runner was alive. The 2026-10-15 workflow fix removed
that noise, and with it the heartbeat. Silence now covers two very
different situations:

    nothing to report          <- fine
    the automation is dead     <- not fine

That ambiguity was tolerable during development. It stops being
tolerable the moment paper trading starts and positions are being
managed off these mails, so this closes it before that happens.

WHAT IT COMPLEMENTS
-------------------
pipeline/send_failure_email.py fires when a step CRASHES. This fires
when nothing crashed and nothing happened either -- the silent case,
which is the one that looks exactly like a quiet day. Between the two
there is no longer a failure mode that reaches Dave as silence.

WHEN IT FIRES
-------------
The workflow calls this on the MORNING SAFETY-NET fire only (7:17am EST
/ 8:17am EDT), and only when that fire had work to do and still did not
advance state/last_run_date.txt. By mid-morning the previous session's
data is long since published, so a session still unprocessed then is a
real fault, not the ordinary late-data lag that keeps the evening fires
busy. Normal lateness -- the 6:17pm and 8:17pm fires deferring because
only 17 of 80 tickers have a bar yet -- never reaches this script.

IT REPEATS ON PURPOSE
---------------------
A session stuck for three days sends three alerts, one per morning.
There is deliberately no "already warned about this one" stamp: that
would need a file committed back to the repo, and a stuck pipeline is
exactly the situation where the commit path cannot be trusted. An alert
that goes quiet while the fault persists is worse than one that repeats.

SELF-CONTAINED BY DESIGN
------------------------
This imports nothing from the pipeline -- not the orchestrator, not
check_run_window, not parameters. It reads two state files and counts
bars in data/. That is deliberate: this script runs precisely when
something upstream is broken, and the one job it has is to get a mail
out. Anything it imports is another thing that can take it down with the
rest. Every optional section is wrapped, so a failure while building the
diagnostics still sends the alert.
"""

import os
import smtplib
import sys
from datetime import date, datetime, timedelta
from email.mime.text import MIMEText

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(_REPO_ROOT, "state")
DATA_DIR = os.path.join(_REPO_ROOT, "data")

LAST_RUN_PATH = os.path.join(STATE_DIR, "last_run_date.txt")
OPEN_POSITIONS_PATH = os.path.join(STATE_DIR, "open_positions.csv")
SKIP_SESSIONS_PATH = os.path.join(STATE_DIR, "skip_sessions.txt")

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

WIDTH = 68


def get_last_run_date():
    """The session the pipeline last completed, or None if unreadable."""
    try:
        with open(LAST_RUN_PATH) as f:
            return f.read().strip() or None
    except Exception:
        return None


def waiting_on(last_run, today):
    """The session most likely blocking the queue: the first weekday
    strictly after last_run, capped at today.

    Approximate by design -- it does not know market holidays, and it
    does not need to. The workflow has already established that a
    session is unprocessed; this only picks the most likely one so the
    coverage count below has something to count against.
    """
    try:
        d = datetime.strptime(last_run, "%Y-%m-%d").date()
    except Exception:
        return None
    d += timedelta(days=1)
    while d.weekday() >= 5 and d <= today:      # 5=Sat 6=Sun
        d += timedelta(days=1)
    return d if d <= today else None


def weekdays_between(last_run, today):
    """Rough count of weekdays elapsed since the last processed session.
    Holidays inflate it slightly; it is a severity hint, not a metric."""
    try:
        d = datetime.strptime(last_run, "%Y-%m-%d").date()
    except Exception:
        return None
    n = 0
    d += timedelta(days=1)
    while d <= today:
        if d.weekday() < 5:
            n += 1
        d += timedelta(days=1)
    return n


def coverage_for(session):
    """(tickers carrying a bar for `session`, files scanned).

    Reads the raw CSV text rather than loading pandas: this is the
    diagnostic that says 'the feed is short' versus 'something else is
    wrong', and it should not be the thing that fails.
    """
    if session is None or not os.path.isdir(DATA_DIR):
        return None, 0
    stamp = session.isoformat()
    hits = 0
    scanned = 0
    for name in os.listdir(DATA_DIR):
        if not name.endswith("_1d_data.csv"):
            continue
        scanned += 1
        try:
            with open(os.path.join(DATA_DIR, name)) as f:
                if stamp in f.read():
                    hits += 1
        except Exception:
            continue
    return hits, scanned


def open_book():
    """(number of open positions, capital committed). Parsed by hand so a
    pandas import can never be what stops this alert going out."""
    try:
        with open(OPEN_POSITIONS_PATH) as f:
            rows = [r for r in f.read().splitlines() if r.strip()]
        if len(rows) < 2:
            return 0, 0.0
        header = [c.strip() for c in rows[0].split(",")]
        idx = header.index("position_cost")
        total = 0.0
        for r in rows[1:]:
            try:
                total += float(r.split(",")[idx])
            except Exception:
                continue
        return len(rows) - 1, total
    except Exception:
        return None, None


def build_body(last_run, today):
    session = waiting_on(last_run, today)
    behind = weekdays_between(last_run, today)

    L = []
    L.append("=" * WIDTH)
    L.append("SCORECARD ALERT -- SESSION OVERDUE")
    L.append("=" * WIDTH)
    L.append("")
    L.append("The morning safety-net run found a trading session still")
    L.append("unprocessed. By this hour the data has long been published, so")
    L.append("this is not the ordinary late-data lag -- something is stuck.")
    L.append("")
    L.append(f"  Last processed session:  {last_run or 'UNKNOWN -- state/last_run_date.txt unreadable'}")
    if session is not None:
        L.append(f"  Waiting on (likely):     {session.isoformat()}")
    if behind is not None:
        L.append(f"  Weekdays elapsed since:  {behind}   (holidays not excluded)")
    L.append(f"  Alert generated:         {today.isoformat()}")

    # ---- what the feed looks like, which separates the two main causes
    L.append("")
    L.append("DATA COVERAGE")
    L.append("-" * WIDTH)
    try:
        hits, scanned = coverage_for(session)
        if session is None:
            L.append("  (could not work out which session to check)")
        elif scanned == 0:
            L.append("  No ticker files found in data/ -- the data pull itself may")
            L.append("  be failing. Check the 'Pull fresh market data' step.")
        else:
            L.append(f"  {hits} of {scanned} ticker files carry a bar for {session.isoformat()}.")
            L.append("")
            if hits == 0:
                L.append("  Nothing at all arrived for that session. Look at the data")
                L.append("  pull first -- the feed, the universe query, or the run")
                L.append("  itself never produced bars.")
            elif scanned and hits < scanned * 0.5:
                L.append("  Short of the coverage guard's threshold, so the orchestrator")
                L.append("  is correctly refusing to process a partial universe. If the")
                L.append("  feed simply never published this session, the escape hatch is")
                L.append(f"  to add the line '{session.isoformat()}' to state/skip_sessions.txt,")
                L.append("  which marks it done and lets the queue behind it drain.")
            else:
                L.append("  Coverage looks adequate, which makes this the more")
                L.append("  interesting case: the data is there and the session still")
                L.append("  did not process. Read the orchestrator step's output in")
                L.append("  the most recent Actions run.")
    except Exception as e:
        L.append(f"  (coverage check failed: {type(e).__name__}: {e})")

    # ---- what is exposed while the pipeline is blind
    L.append("")
    L.append("OPEN BOOK (as last recorded)")
    L.append("-" * WIDTH)
    try:
        n, committed = open_book()
        if n is None:
            L.append("  state/open_positions.csv unreadable.")
        elif n == 0:
            L.append("  No open positions -- nothing is exposed while this is stuck.")
        else:
            L.append(f"  {n} open position(s), {committed:,.2f} dollars committed.")
            L.append("  Exits are NOT being checked while the session is unprocessed.")
    except Exception as e:
        L.append(f"  (open book check failed: {type(e).__name__}: {e})")

    # ---- the escape hatch, stated plainly
    L.append("")
    L.append("WHAT TO DO")
    L.append("-" * WIDTH)
    L.append("  1. Open the most recent run in GitHub Actions and read the")
    L.append("     'Run orchestrator' step. It states its own reason for")
    L.append("     deferring, in plain English.")
    L.append("  2. If the session is never going to publish, add its date to")
    L.append("     state/skip_sessions.txt and re-run. That marks it done and")
    L.append("     drains the queue behind it.")
    L.append("  3. This alert repeats every weekday morning until the queue")
    L.append("     moves, so it will not go quiet on its own.")
    try:
        with open(SKIP_SESSIONS_PATH) as f:
            skipped = [s.strip() for s in f.read().splitlines()
                       if s.strip() and not s.strip().startswith("#")]
        if skipped:
            L.append("")
            L.append(f"  Already skipped: {', '.join(skipped[-5:])}")
    except Exception:
        pass

    L.append("")
    L.append("=" * WIDTH)
    return "\n".join(L)


def send_email(subject, body):
    from_addr = os.environ["EMAIL_ADDRESS"]
    app_password = os.environ["EMAIL_APP_PASSWORD"]
    to_addr = os.environ["EMAIL_TO"]

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(from_addr, app_password)
        server.sendmail(from_addr, [to_addr], msg.as_string())


def main():
    today = date.today()
    last_run = get_last_run_date()
    body = build_body(last_run, today)

    session = waiting_on(last_run, today) if last_run else None
    label = session.isoformat() if session is not None else today.isoformat()
    subject = f"SCORECARD ALERT -- session {label} overdue"

    print(body)

    try:
        send_email(subject, body)
        print("\nOverdue alert sent.")
    except Exception as e:
        print(f"\nFailed to send overdue alert: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
