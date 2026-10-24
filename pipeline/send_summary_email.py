#!/usr/bin/env python3
"""
send_summary_email.py -- sends the daily automated summary email after a
successful orchestrator.py run.

WHAT IT SENDS: new trades opened today, trades closed today (with
results), and the full current open-positions snapshot. "Today" here
means the most recent date the orchestrator actually processed
(state/last_run_date.txt), NOT necessarily the literal calendar date this
script happens to run on -- this matters correctly during a missed-day
replay, where the orchestrator may have just finished processing a day
that isn't today's real date.

STATELESS BY DESIGN: rather than have orchestrator.py track and pass
along "what happened today," this script simply re-reads the two state
files (state/open_positions.csv, state/closed_trades.csv) after the fact
and filters for rows matching the target date -- entry_date for new
trades, exit_date for closed trades. Keeps orchestrator.py's job
narrowly focused on the trading logic itself.

CREDENTIALS: reads EMAIL_ADDRESS, EMAIL_APP_PASSWORD, EMAIL_TO from
environment variables -- populated by the GitHub Actions workflow from
repo secrets (SCORECARD_EMAIL_ADDRESS, SCORECARD_EMAIL_APP_PASSWORD,
SCORECARD_EMAIL_TO). Uses Gmail's SMTP server -- EMAIL_APP_PASSWORD must
be a Gmail App Password (see Google Account > Security > 2-Step
Verification > App Passwords), NOT the regular account password, since
Gmail blocks plain password logins for security.
"""

import os
import smtplib
import sys
from email.mime.text import MIMEText

import pandas as pd

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state")
OPEN_POSITIONS_PATH = os.path.join(STATE_DIR, "open_positions.csv")
CLOSED_TRADES_PATH = os.path.join(STATE_DIR, "closed_trades.csv")
LAST_RUN_PATH = os.path.join(STATE_DIR, "last_run_date.txt")
PENDING_ORDERS_PATH = os.path.join(STATE_DIR, "pending_orders.csv")
ORDERS_SENT_PATH = os.path.join(STATE_DIR, "orders_sent.csv")

# ----------------------------------------------------------------------
# ONE EMAIL PER SESSION (added 2026-10-14)
#
# THE BUG THIS FIXES. The workflow runs this step whenever the run gate
# said should_run AND the job succeeded. It does NOT check whether a
# session was actually processed -- and the orchestrator exits 0 when the
# coverage guard blocks a session, because a block is not an exception. So
# a run that advanced nothing still sent an email, re-reporting whatever
# state/last_run_date.txt still said.
#
# CONFIRMED FROM THE REPO, not inferred. git only records
# state/last_run_date.txt in a commit when its contents change, so its
# history is an exact log of sessions processed -- one commit per trading
# day. On 2026-10-13 Dave received a summary for 2026-10-12 at 19:16 MT
# with no commit anywhere near it, and the real 2026-10-13 summary at
# 22:55 MT, matching that day's only commit to the minute. The 19:16 email
# was a run that did no work.
#
# THE GUARD. state/last_emailed_date.txt records the session last emailed.
# If the session about to be reported has already been sent, this skips.
#
# IT FAILS OPEN, DELIBERATELY. If the stamp file is missing, unreadable or
# unparseable, the email SENDS. This is the only alert channel and the
# whole lesson of this project is that silence reads identical to health:
# a duplicate email is a nuisance, a silently suppressed one is a failure
# you cannot see. Any doubt resolves toward sending.
#
# THE STAMP IS WRITTEN ONLY AFTER A SUCCESSFUL SEND, so an SMTP failure
# does not mark the session as reported and block the retry.
#
# SIDE EFFECT WORTH KNOWING: with duplicates gone, "no email tonight" now
# means "no session was processed" -- a real signal rather than noise. See
# the 2026-10-14 note on sessions processing 6-9 hours after the close.
# ----------------------------------------------------------------------
LAST_EMAILED_PATH = os.path.join(STATE_DIR, "last_emailed_date.txt")


def get_last_emailed_date():
    """The session last successfully emailed, or None. Any problem reading
    it returns None, which means 'send' -- see the fail-open note above."""
    try:
        with open(LAST_EMAILED_PATH) as f:
            v = f.read().strip()
        return v or None
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f"Could not read {os.path.basename(LAST_EMAILED_PATH)} ({e}) "
              f"-- sending anyway rather than risk suppressing a real "
              f"summary.")
        return None


def set_last_emailed_date(target_date):
    try:
        with open(LAST_EMAILED_PATH, "w") as f:
            f.write(str(target_date))
    except Exception as e:
        print(f"WARNING: sent the summary but could not record it in "
              f"{os.path.basename(LAST_EMAILED_PATH)} ({e}). The next run "
              f"may send a duplicate.", file=sys.stderr)

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587


def get_target_date():
    if not os.path.exists(LAST_RUN_PATH):
        return None
    with open(LAST_RUN_PATH) as f:
        return f.read().strip()


def safe_read_csv(path):
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_csv(path)
    return df


def build_orders_section():
    """The actionable block: what to enter at the broker tonight."""
    L = []
    orders = safe_read_csv(PENDING_ORDERS_PATH)
    if not len(orders):
        L.append("ORDERS TO PLACE TONIGHT: none")
        L.append("")
        return L

    for_session = str(orders["for_session"].iloc[0])
    sells = orders[orders["order_type"] == "SELL_MOO"]
    buys = orders[orders["order_type"] == "BUY_LIMIT"]

    L.append("=" * 72)
    L.append(f"ORDERS TO PLACE TONIGHT  --  for the {for_session} session")
    L.append("=" * 72)
    L.append("")

    L.append(f"SELL, market-on-open ({len(sells)}):")
    if len(sells):
        for _, r in sells.iterrows():
            L.append(f"    {str(r['ticker']):<7}{int(r['shares']):>7} shares"
                     f"     ({r['note']})")
    else:
        L.append("    (none)")
    L.append("")

    L.append(f"BUY, limit -- DAY ORDERS, good for {for_session} only ({len(buys)}):")
    if len(buys):
        L.append(f"    {'ticker':<8}{'limit':>9}{'shares':>8}{'stop':>9}"
                 f"{'cost':>11}{'score':>7}")
        total = 0.0
        for _, r in buys.iterrows():
            total += float(r["position_cost"])
            L.append(f"    {str(r['ticker']):<8}{float(r['limit_price']):>9.2f}"
                     f"{int(r['shares']):>8}{float(r['stop_price']):>9.2f}"
                     f"{float(r['position_cost']):>11,.2f}{float(r['score']):>7.1f}")
        L.append("")
        L.append(f"    Capital committed if every bid fills: {total:,.2f}")
        L.append("    The stop is NOT placed now -- enter it only if the buy fills.")
    else:
        L.append("    (none)")
    # ------------------------------------------------------------------
    # DISPATCH STATUS (added 2026-10-22)
    #
    # One line saying whether these orders actually went to TradersPost or
    # were only prepared. Without it the list above is ambiguous: in dry
    # run it looks identical to a live night, and "did the robot send
    # these, or am I still entering them by hand?" is exactly the question
    # that must never be guessed at while both are possible.
    # ------------------------------------------------------------------
    try:
        L.extend(_dispatch_line(for_session))
    except Exception as e:
        L.append(f"    Dispatch status unavailable ({type(e).__name__}: {e})")

    L.append("")
    L.append("=" * 72)
    L.append("")
    return L


def _dispatch_line(for_session):
    sent = safe_read_csv(ORDERS_SENT_PATH)
    if not len(sent) or "for_session" not in sent.columns:
        return ["", "    TradersPost: no dispatch record -- orders NOT sent."]
    mine = sent[sent["for_session"].astype(str) == str(for_session)]
    if not len(mine):
        return ["", f"    TradersPost: nothing dispatched for {for_session} "
                    f"-- these orders were NOT sent."]
    modes = set(mine["mode"].astype(str))
    # "sent" is the pre-2026-10-24 label for the same thing; both mean the
    # webhook was accepted, NOT that the order was.
    delivered = mine["status"].isin(["delivered", "sent"])
    n_del = int(delivered.sum())
    n_fail = int(mine["status"].isin(["failed", "build_error"]).sum())
    n_dry = int((mine["status"] == "dry_run").sum())
    out = [""]
    if modes == {"dry_run"}:
        out.append(f"    TradersPost: DRY RUN -- {n_dry} order(s) prepared, "
                   f"NOTHING SENT. Enter these by hand.")
        return out

    # WORDING MATTERS HERE (2026-10-24). This line previously read
    # "N sent, 0 failed" and said exactly that on a night when all four
    # orders were rejected by TradersPost after it had already returned
    # HTTP 200. The daily mail is the only thing Dave reads every morning,
    # so it must not claim an outcome the system cannot observe.
    out.append(f"    TradersPost: {n_del} delivered, {n_fail} failed to deliver.")
    if n_del:
        out.append("    DELIVERED IS NOT ACCEPTED. TradersPost returns success")
        out.append("    on receipt and validates afterwards; a rejection comes")
        out.append("    as a separate email from them. Check for one before")
        out.append("    assuming these orders are working.")
    if n_fail:
        out.append("    FAILED ORDERS NEVER REACHED TRADERSPOST and are never")
        out.append("    retried -- place them by hand or skip them.")
        for _, r in mine[~delivered].iterrows():
            out.append(f"      {r['ticker']}: {str(r['detail'])[:90]}")
    return out


def build_summary_body(target_date):
    open_df = safe_read_csv(OPEN_POSITIONS_PATH)
    closed_df = safe_read_csv(CLOSED_TRADES_PATH)

    new_trades = open_df[open_df["entry_date"].astype(str) == str(target_date)] if len(open_df) else open_df
    closed_today = closed_df[closed_df["exit_date"].astype(str) == str(target_date)] if len(closed_df) else closed_df

    lines = []
    lines.append(f"Scorecard Daily Summary -- {target_date}")
    lines.append("=" * 50)
    lines.append("")

    # ------------------------------------------------------------------
    # ORDERS TO PLACE TONIGHT (added 2026-10-19)
    #
    # FIRST in the mail, because it is the only part that needs doing.
    # Everything below it is a report on what already happened.
    #
    # Read straight from state/pending_orders.csv, which orchestrator.py
    # writes at the end of each run. This script does not recompute
    # anything: the share count Dave types into the broker is the one the
    # system booked, because it is literally the same number from the same
    # file. When placement is automated, the automation reads that file
    # too, so this section and the robot never disagree.
    #
    # Wrapped, like the P&L block: a missing or malformed order file costs
    # a section of the mail, never the mail.
    # ------------------------------------------------------------------
    try:
        lines.extend(build_orders_section())
    except Exception as e:
        lines.append(f"ORDERS: could not be read ({type(e).__name__}: {e})")
        lines.append("")

    lines.append(f"NEW TRADES OPENED TODAY ({len(new_trades)}):")
    if len(new_trades):
        for _, r in new_trades.iterrows():
            lines.append(
                f"  {r['ticker']}: entry {r['entry_price']}, "
                f"{r['shares']} shares, score {r['score_at_entry']}, "
                f"{r['position_cost']} dollars committed"
            )
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append(f"TRADES CLOSED TODAY ({len(closed_today)}):")
    if len(closed_today):
        for _, r in closed_today.iterrows():
            lines.append(
                f"  {r['ticker']}: exit {r['exit_price']} ({r['exit_reason']}), "
                f"realized R = {r['realized_R']}, "
                f"realized P/L = {r['realized_pnl']} dollars"
            )
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append(f"CURRENT OPEN POSITIONS ({len(open_df)}):")
    if len(open_df):
        for _, r in open_df.iterrows():
            lines.append(
                f"  {r['ticker']}: entry {r['entry_price']} on {r['entry_date']}, "
                f"{r['shares']} shares, score {r['score_at_entry']}"
            )
        total_committed = open_df["position_cost"].sum()
        lines.append(f"  Total capital committed: {total_committed:.2f} dollars")
    else:
        lines.append("  (none)")

    # ------------------------------------------------------------------
    # P&L SNAPSHOT (added 2026-10-11)
    #
    # Appended to the bottom of the daily mail so the question "how is the
    # system actually doing?" is answered where Dave already looks, rather
    # than requiring him to open closed_trades.csv and sum it by hand.
    #
    # as_of is the PROCESSED SESSION, not the calendar date -- the same
    # distinction this file already makes everywhere else. It drives the
    # stale-mark flag in the report, so during a missed-day replay the
    # staleness test refers to the session being reported rather than to
    # whatever day the runner happens to be awake on.
    #
    # TWO DEFENSIVE CHOICES, both deliberate:
    #
    # 1. The import is INSIDE the try, not at module level. If
    #    pipeline/pnl_report.py is missing -- not yet pasted, renamed,
    #    pasted into the wrong folder (which has happened) -- a top-level
    #    import would raise before main() ever runs and the email would
    #    never be sent at all.
    # 2. The whole block is wrapped. This email is the alert channel; it
    #    is the last thing in the system that may break. A P&L snapshot
    #    that cannot be built costs one line of the mail, never the mail.
    # ------------------------------------------------------------------
    lines.append("")
    lines.append("")
    try:
        import pnl_report
        lines.append(pnl_report.build_pnl_report(as_of=target_date))
    except Exception as e:
        lines.append(f"P&L snapshot unavailable ({type(e).__name__}: {e})")

    return "\n".join(lines)


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
    target_date = get_target_date()
    if target_date is None:
        print("No last_run_date.txt found -- nothing to summarize, skipping email.")
        return

    already = get_last_emailed_date()
    if already is not None and str(already) == str(target_date):
        print(f"Session {target_date} has already been emailed "
              f"(state/last_emailed_date.txt) -- skipping. This run did not "
              f"advance the session, so there is nothing new to report.")
        return

    body = build_summary_body(target_date)
    subject = f"Scorecard Daily Summary -- {target_date}"

    try:
        send_email(subject, body)
        print("Summary email sent successfully.")
        set_last_emailed_date(target_date)
    except Exception as e:
        print(f"Failed to send summary email: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
