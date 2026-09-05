"""
parameters.py -- Single source of truth for tunable account/sizing
parameters used by the daily automation orchestrator (and, going
forward, a candidate home for other pipeline constants currently
hardcoded across individual files).

Update values here rather than hunting through pipeline scripts.
"""

# Simulated account starting balance (dollars). Purely for tracking
# purposes at this stage -- no live broker connection exists yet.
ACCOUNT_STARTING_BALANCE = 25000.00

# Percent of account equity risked per trade (i.e. what one unit of "R"
# in dollar terms represents). Set 2026-09-05.
RISK_PERCENT_PER_TRADE = 0.01  # 1 percent
