"""
One-off runner: reruns the FIXED touch_scan_and_momentum_screen.py logic
against the new wide-universe historical dataset (1,591 tickers, 3 years)
instead of the original small hardcoded dataset.

Does not modify touch_scan_and_momentum_screen.py itself -- just points
its DATA_DIR at the new historical_data/ folder before invoking it, and
saves the output under a new filename so the original
Historical_Touches_Raw.csv (456-event dataset) is preserved for
comparison.
"""

import os
import sys

sys.path.insert(0, "/mnt/user-data/outputs")

import touch_scan_and_momentum_screen as tscan

# Point at the new wide-universe per-ticker data
tscan.DATA_DIR = "/mnt/user-data/outputs/historical_data"

# Run the (now-fixed) scan
tscan.run_full_historical_scan()

# The function hardcodes its own output filename inside itself
# (Historical_Touches_Raw.csv) -- rename the result afterward so we don't
# clobber the original 456-event file.
import shutil
src = "/mnt/user-data/outputs/Historical_Touches_Raw.csv"
dst = "/mnt/user-data/outputs/Historical_Touches_WideUniverse_v1.csv"
if os.path.exists(src):
    shutil.move(src, dst)
    print(f"Moved output to {dst}")
