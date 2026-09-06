#!/usr/bin/env python3
"""
send_failure_email.py -- sends a failure-notification email when the
daily automation fails.

WHEN THIS RUNS: only triggered by the GitHub Actions workflow when the
8pm Eastern retry slot ALSO fails (i.e. both the 6pm primary attempt and
the 8pm retry failed for the same trading day) -- see
.github/workflows/daily_scorecard_automation.yml's "failure()" condition
on this step. A single failed 6pm run does NOT trigger this email on its
own, since the 8pm retry might still succeed.

WHAT IT SENDS: a simple alert -- this is intentionally minimal, no
attempt to parse or summarize what went wrong beyond flagging that it
happened, since if something failed badly enough to reach this point,
the safest assumption is that Dave should just go look at the GitHub
Actions run logs directly rather than trust an automated diagnosis.

CREDENTIALS: same as send_summary_email.py -- reads EMAIL_ADDRESS,
EMAIL_APP_PASSWORD, EMAIL_TO from environment variables, populated by the
GitHub Actions workflow from repo secrets. Uses Gmail's SMTP server --
EMAIL_APP_PASSWORD must be a Gmail App Password, not the regular account
password.
"""

import os
import smtplib
import sys
from datetime import datetime
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587


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
    now_eastern = datetime.now(ZoneInfo("America/New_York"))
    subject = f"SCORECARD AUTOMATION FAILED -- {now_eastern.date().isoformat()}"
    body = (
        f"The daily scorecard automation failed today.\n\n"
        f"Both the 6pm Eastern primary run and the 8pm Eastern retry\n"
        f"did not complete successfully.\n\n"
        f"Detected at: {now_eastern.isoformat()}\n\n"
        f"Check the GitHub Actions run logs directly for the actual\n"
        f"error details -- go to the repository's Actions tab and open\n"
        f"the most recent failed 'Daily Scorecard Automation' run.\n\n"
        f"No trades were opened or closed automatically today until this\n"
        f"is resolved."
    )

    try:
        send_email(subject, body)
        print("Failure notification email sent successfully.")
    except Exception as e:
        # if even the failure email fails to send, there's nothing further
        # to do but print it to the workflow logs
        print(f"Failed to send failure notification email: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
