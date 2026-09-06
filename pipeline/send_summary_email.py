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


def build_summary_body(target_date):
    open_df = safe_read_csv(OPEN_POSITIONS_PATH)
    closed_df = safe_read_csv(CLOSED_TRADES_PATH)

    new_trades = open_df[open_df["entry_date"].astype(str) == str(target_date)] if len(open_df) else open_df
    closed_today = closed_df[closed_df["exit_date"].astype(str) == str(target_date)] if len(closed_df) else closed_df

    lines = []
    lines.append(f"Scorecard Daily Summary -- {target_date}")
    lines.append("=" * 50)
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

    body = build_summary_body(target_date)
    subject = f"Scorecard Daily Summary -- {target_date}"

    try:
        send_email(subject, body)
        print("Summary email sent successfully.")
    except Exception as e:
        print(f"Failed to send summary email: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
