# Scorecard Project - End-to-End Architecture (v2)

*Rebuilt 2026-09-03 as a scoping aid before re-validating anything affected by the
sim.py entry-day stop bug. Version 2 - corrects Stage 0/1 based on documentation
found in MA_Respect_Redesign_Notes.txt, which defines the momentum screen and
pre-watchlist checks concretely. Each stage names the actual script/file
responsible where known, and flags whether that stage's OUTPUT depends on
sim.py's buggy Outcome_* columns.*

*Added 2026-09-03: the 8 stages below are grouped into 4 named STEPS, so
everyone (Dave, Claude, future sessions) can refer to "which part of the
pipeline" in the same language instead of just stage numbers. This is
meant to be the template going forward for any new project of this kind -
name the step, name the stage, name the script, name the sim.py
dependency, every time.*

## STEP OVERVIEW

**STEP A - BUILD THE WATCHLIST** (Stages 0-2): everything that happens
BEFORE a trade idea exists - narrowing the entire tradeable universe down
to a small list of candidate tickers worth watching for a 50-day MA pullback,
then actually detecting the pullback touch itself. Nothing in this step
touches sim.py or trade outcomes at all - it's pure price-history
screening. Ends with the 456-event historical dataset (or, going forward,
a live daily watchlist).

**STEP B - SCORE THE CANDIDATE** (Stage 3): once a touch event exists,
score it mechanically on the 3 validated attributes to produce one
combined conviction score per event.

**STEP C - SIMULATE THE TRADE** (Stages 4-7): take a scored event and
mechanically simulate what would have happened - the entry fill, the
initial stop, the trailing exit, and the final realized result. This is
entirely sim.py's territory, and it's where the entry-day stop bug lives.

**STEP D - USE THE RESULTS** (Stage 8): everything that consumes Step
C's output after the fact - validating the scoring attributes themselves,
sizing rules, trail rule selection, and the live pilot rounds.

---

## STEP A - BUILD THE WATCHLIST

### STAGE 0 - Momentum Screen (mechanical, run in TradingView)
**What happens:** A mechanical screen with 8 concrete criteria, run in
TradingView against the tradeable universe:
  - Market cap > $100M
  - TTM revenue growth YoY > 0%
  - 6-month price performance between 30% and 500%
  - Primary listing
  - SMA100 > SMA200
  - ADX(50) between 20 and 40
  - Avg 10-day volume > 1M shares
  - Price > 50-day MA

**Script/tool:** Run natively in TradingView, named "Momentum - Top
Performers 6M". FULLY RESOLVED 2026-09-03 - Dave provided a screenshot of
the actual saved screen configuration, confirming all 8 criteria exactly
as documented: Mkt cap > 100M USD, Revenue growth TTM YoY > 0%, Perf 6M
30% to 500%, Primary listing Yes, SMA100 > SMA200, ADX(50) 20 to 40, Avg
vol 10D > 1M, SMA50 < Price (equivalent to price > 50-day MA). Nothing
outstanding on this stage - screen is fully known and reproducible.

**sim.py dependency:** NONE. Pure screening criteria, no trade outcomes
involved. The 456-event dataset used throughout this project is
downstream of this screen - Stage 0 is not a separate, disconnected step
from the historical research pipeline; it feeds into everything below,
including the 456-event set itself.

**MVP status, confirmed 2026-09-05: 4 of 8 criteria ARE coded, 3 are
NOT, 1 (primary listing) is effectively structural.** `touch_scan_and_momentum_screen.py`
(see Stage 2) re-derives 4 of the 8 criteria directly from price data,
day by day: SMA100>SMA200, ADX(50) 20-40, avg 10-day volume >1M, and
6-month performance 30-500%. It CANNOT check market cap, TTM revenue
growth, or primary listing, because no such data is loaded/available in
the current historical price-only dataset - those 3 were presumably
enforced only once, upstream, whenever the original ticker list was
exported from TradingView, and there is currently no code anywhere
enforcing those 3 conditions on an ongoing basis.

**MVP decision (Dave, 2026-09-05):** the 3 uncoded criteria (market cap,
TTM revenue growth, primary listing) are DEFERRED to Phase 2. They will
require sourcing fundamental/exchange-metadata data for the ticker
universe (not just historical price data), which hasn't been done yet.
For the MVP pipeline run, only the 4 coded criteria are enforced in code;
this is a known, documented gap, not an oversight - any MVP pipeline
results should be read with the understanding that they only reflect 4
of the 8 intended momentum-screen criteria.

---

### STAGE 1 - Pre-Watchlist Screening Layer

**UPDATED 2026-09-03 - validated against the full 456-event dataset, see
`Prewatchlist_Check_Validation_Findings_v1.md` for the full write-up.**

**What happens (MVP decision):** ONE mechanical gate is active in the MVP
pipeline, applied to whatever survives Stage 0:
  - **Overhead Resistance check** (2-year lookback, 2-month grace period;
    exclude if current close <= highest close more than 2 months old) -
    ACTIVE, promoted into the MVP pipeline after validation.

The Smoothness check (126-day/6-month R-squared of a linear regression on
daily closes, PASS if R-squared >= 0.85) was tested at full scale and SET
ASIDE - not part of the MVP screening process. See "Validation result"
below for why, and see the Future Enhancements section for its
post-MVP status.

**Validation result (2026-09-03):** Both checks were run against all 456
historical touch events (first time either has been run at this scale).
Overhead resistance alone retains 55.5 percent of events and achieves
0.414R mean outcome / 51.4 percent win rate - nearly identical to the
combined two-check filter (0.417R / 49.7 percent win rate, but only 36.6
percent of events retained). Smoothness alone barely beats the unfiltered
baseline (0.171R), and events that pass smoothness while FAILING overhead
resistance are actively bad (-0.385R, 25.7 percent win rate) - worse than
no filter at all. Conclusion: overhead resistance is doing essentially all
the real discriminating work; smoothness has no demonstrated standalone
value. Decision: keep the MVP pipeline simpler and higher-volume by using
overhead resistance only, and revisit smoothness later with more testing
rather than accepting a 63 percent cut to an already-infrequent setup for
minimal additional benefit.

**Script:** RECOVERED IN FULL 2026-09-03, both functions saved as
`overhead_resistance_and_smoothness_checks.py`. Recovered verbatim from
the original sessions that built and validated them (2026-08-20-21-37-30
for overhead-resistance, 2026-08-21-00-56-42 for smoothness). Small-sample
validation numbers on record in `MA_Respect_Redesign_Notes.txt`: 13
excludes / 2 passes on the 15-name overhead-resistance sample, and 21 of
112 names passing the 0.85 R-squared cutoff on the smoothness sample -
both since superseded by the full 456-event validation above.

The script also documents a decision already made and closed: whether to
add a third check at this layer for large single-day gaps/repricing
events. Tested and explicitly rejected by Dave - the 0.85 smoothness
cutoff already lets some legitimate earnings-driven gaps through (e.g.
GEO, FA, VSTS, all names Dave has personally traded), and he confirmed
these are normal-magnitude news pops within a healthy uptrend, not the
extreme overnight-repricing scenario (100%+) the check would exist to
catch. A separate, existing 40-day gap check inside the Trend Efficiency
scorecard attribute is a different concern at a different horizon and
wasn't touched by this decision.

Locked formulas, for reference:
- Overhead-resistance (ACTIVE in MVP): lookback 2 years, grace period 2
  months; eligible high = max(close) over [today minus 2 years, today
  minus 2 months]; if no eligible history yet (ticker too new) then PASS;
  if current close <= eligible high then EXCLUDE; else PASS.
- Smoothness (SET ASIDE, post-MVP): lookback 126 trading days (about 6
  months, matching the momentum screen's own 6-month window); R-squared
  of a linear regression of daily closes over that window; PASS if
  R-squared >= 0.85, else EXCLUDE.

**What's still genuinely outstanding:** overhead resistance has now been
validated against the 456-event historical set, but has NOT yet been run
against the full ~1,002-ticker universe for live/ongoing watchlist
generation (as opposed to backtested history). Also, the 456-event
dataset itself still does not have overhead resistance applied upstream -
it includes many touch events (44.5 percent) that this check would
exclude. Rebuilding the event dataset with the check applied upstream,
and re-validating Step B/C/D against that cleaner set, remains open.

**Result:** ~4.8 percent of the full universe passes both checks combined
(48 of ~996 screenable tickers) - this older figure predates the 2026-09-03
validation and combined-filter analysis above; a fresh full-universe run
using overhead-resistance-only has not yet been done.

**sim.py dependency:** NONE. Pure price-shape filter, no trade outcomes
involved.

---

### STAGE 2 - Daily 50-Day MA Touch Scan -> Event Universe

**UPDATED 2026-09-03 - event universe rebuilt with Stage 1's overhead
resistance filter applied. See `Event_Universe_v3_OverheadResistanceFiltered_253Events.csv`,
now the current working event set (253 events, 152 unique tickers),
superseding the original unfiltered 456-event set for all Step B/C/D
work going forward.**

**What happens:** Tickers that pass Stage 0 (momentum screen) and Stage 1
(overhead resistance) are scanned daily for 50-day MA touches (price
reaching the 50-day moving average, the entry trigger for this whole
system). Each qualifying touch becomes one "event."

**Script:** RECOVERED IN FULL 2026-09-03 - `touch_scan_and_momentum_screen.py`
(saved to outputs). This is real, working code that generated the
original 456-event dataset; it lived only in a sandbox during the
original 2026-08-19 large-sample tuning session and was never saved as a
file until now.

Touch definition used: any day where the 50-day MA value falls between
that day's Low and High inclusive (price crossed the MA level intraday) -
a looser definition than "closed exactly at the MA," chosen because the
data is daily OHLC only.

**Confirmed decision on the touch definition (re-confirmed 2026-09-03):**
Dave's real execution is a resting limit order that fills the instant
price touches the 50-day MA intraday - at entry there's no way to know
yet whether the day will close above or below. The straddle-only touch
definition (Low <= SMA50 <= High) is therefore correct and sufficient as
the entry mechanic and does not need to change. The script separately
computes `price_above_50ma = Close > SMA50` but never includes it in the
final filter (dead code) - this is NOT a bug needing a fix, since the
touch definition alone is the agreed entry condition. Whether
close-above-vs-below carries separate signal value as a filter or scored
attribute (evaluated after the fact, not as an entry gate) remains a
Future Enhancement (post-MVP) question, not a blocker.

**Event universe history:**
- Original run (2026-08-19): 456 events, unfiltered by overhead
  resistance or smoothness. Saved as
  `Full_456_Rerun_v2_Scores_vs_Outcomes.csv`.
- Current working set (2026-09-03): 253 events, 152 unique tickers, after
  applying Stage 1's overhead resistance filter (see Stage 1 for full
  validation detail). Saved as
  `Event_Universe_v3_OverheadResistanceFiltered_253Events.csv`. This is
  the set to use for all Step B (scoring) and Step C/D (simulation and
  downstream) re-validation going forward.

**sim.py dependency:** NONE at this stage - this only identifies WHEN a
touch/entry opportunity occurred, not what happened afterward.

---

## STEP B - SCORE THE CANDIDATE

### STAGE 3 - Attribute Scoring

**UPDATED 2026-09-03 - rerun against the new 253-event overhead-resistance-
filtered universe, and Trend Efficiency v2 demoted out of the active MVP
score. See detail below.**

**What happens:** Every ticker that clears Stage 1 gets scored on 2
validated mechanical attributes actively used in the MVP score (of 9
total defined; 6 are unreviewed/legacy v1 and not in active use, and 1 -
Trend Efficiency v2 - was validated but demoted, see below):
  - MA Respect v5 (trend-start-anchored, ADR-normalized, 3-component)
  - Relative Strength v2 (60-day pullback-from-peak stock/SPY ratio)

Combined as a simple 2-attribute average (MA Respect + Relative Strength).

**Trend Efficiency v2 - DEMOTED to Phase 2 (2026-09-03):** Originally a
3rd attribute in the combined score alongside MA Respect and Relative
Strength. Re-checked against the new 253-event overhead-resistance-
filtered set and found to have essentially no correlation with outcome
(0.034 with Outcome_20ma_P_R) - confirmed this was not an artifact of the
new filter, since it was already near-zero (0.011) on the original
unfiltered 456-event set, and confirmed it wasn't secretly doing overhead
resistance's job either (correlation with overhead-resistance pass/fail:
-0.018). Dave's read: rather than being neutral, a near-zero-correlation
attribute sitting inside an averaged score actively dilutes the signal
from the attributes that DO work. Tested directly: dropping Trend
Efficiency and averaging only MA Respect + Relative Strength improved the
correlation with outcome from 0.186 to 0.238, and sharpened the top-half
vs. bottom-half split from (0.642R / 0.065R) to (0.696R / -0.133R) - the
2-attribute version's bottom half is cleanly negative, meaning it
actually identifies bad setups, where the 3-attribute version's bottom
half was still marginally positive. Decision: Trend Efficiency v2 is
REMOVED from the active MVP combined score effective now (not deferred as
"still in but weak" - fully out). Flagged as surprising given the amount
of redesign work that went into the Gap Check + ATR Variability formula
(see `MA_Respect_Redesign_Notes.txt`) and goes against Dave's intuition.
Sent to Phase 2 for a real second look: whether the formula needs
redesign, whether it's measuring something poorly correlated with
20-day-forward outcomes specifically (vs. some other horizon), or whether
it should stay dropped for good.

**Script:** `scorecard.py` (present, recovered, in hand). Note:
`score_total_v2()` inside the script still averages all 3 attributes
including Trend Efficiency - this function has NOT yet been edited to
reflect the 2-attribute MVP decision above; that's a small pending code
change, not just a documentation one.

**sim.py dependency:** INDIRECT BUT SERIOUS. MA Respect v5 and Relative
Strength v2 were VALIDATED by correlating their scores against
Outcome_20ma_P_R and Outcome_HybridTight_P_R across historical samples.
Those outcome columns were generated by the buggy sim.py. The scoring
formulas themselves don't call sim.py, but the evidence used to
accept/reject each formula design does. This is the most consequential
item in scope - it reaches the foundational scoring engine.

---

## STEP C - SIMULATE THE TRADE

### STAGE 4 - Entry Rule
**What happens:** Entry = resting limit order at the 50-day moving average;
fills if price touches it intraday on the entry date. Entry price = that
MA50 value.

**Script:** entry logic lives inside `sim.py` (`entry_price` param / caller
logic) - not a separately named file.

**sim.py dependency:** DIRECT. This is defined inside sim.py itself, but the
entry mechanic (the fill logic) is NOT the buggy part - the bug is what
happens immediately after entry, not the fill itself. Confirmed separately
as valid (would-fill-intraday checked at 100 percent in earlier analysis).

---

### STAGE 5 - Initial Stop Sizing

**RESOLVED 2026-09-03.** Initial risk-per-share = entry price times the
lesser of a cap percent and the ticker's 10-day ADR percent at entry.
Initial stop = entry price minus risk-per-share.

**Cap percent = 7 percent (recommended, replaces the old 5 percent
default).** Full resweep run on the fixed simulator across all 253 events
in the current event universe, testing 3, 5, and 7 percent. Result: 7
percent outperformed both alternatives across the full dataset (0.418R
average / 43.5 percent win rate, vs. 0.390R/37.2 percent at 3 percent and
0.355R/39.5 percent at the old 5 percent default). Mechanism confirmed:
a tighter cap is binding (forces a tighter-than-natural stop) on 77.9
percent of events at 3 percent, but only 19.8 percent of events at 7
percent - the tighter cap was mostly just causing premature stop-outs
from ordinary volatility, not reducing real risk. Full detail, including
an important caveat about a small outlier-driven subset within the data,
in `Cap_Percent_and_Trail_Rule_Resweep_Findings_v1.md`.

**Script:** `sim.py` - FINAL version saved to outputs 2026-09-03. The
entry-day stop-check bug (see Stage 6) is now fixed directly inside
`simulate_trail()`. IMPORTANT (updated same day, after Dave flagged a real
gap): the 7 percent cap, 20-day MA rule, and no-partial recommendation are
now ENFORCED IN CODE, not just described in comments - `simulate_trail()`'s
defaults are `rule='20ma'`, `take_partial=False`, and a new helper function
`compute_risk_per_share(entry_price, adr10_pct_at_entry, cap_pct=0.07)`
computes risk-per-share the correct way with the 7 percent cap built in as
the default. A brand new conversation can call these functions with no
extra arguments and automatically get the recommended configuration -
nothing about this depends on remembering this conversation.

**sim.py dependency:** RESOLVED. The fix is in the saved `sim.py`, and the
cap percent question that motivated the whole resweep is answered (7
percent, pending the follow-ups below).

**Open follow-ups:** confirm this holds on the Outcome_HybridTight_P_R
metric too, consider testing intermediate cap values (e.g. 6 percent),
and regenerate the Outcome_* columns across the 253-event set using the
final 7 percent / 20-day-MA / no-partial configuration (see Stage 7).

---

### STAGE 6 - Trailing Stop / Exit Rule

**RESOLVED 2026-09-03.** Once a trade is past its initial stop check, one
of 5 trailing rules governs the exit: 10-day MA, 20-day MA, hybrid-tight
(tighter of the two), ADR-adaptive (locked at entry), or ratcheting
swing-low (confirmed pivot lows only, 2-day-both-sides confirmation).
Combined with with-partial (33 percent off at 1.5R touch, stop to
breakeven) or no-partial (full size, grace-period arm at 1.5R close)
logic.

**Recommended configuration: 20-day MA trail, NO-PARTIAL.** This was the
best performer at every cap percent level tested in the 2026-09-03
resweep (see Stage 5 and `Cap_Percent_and_Trail_Rule_Resweep_Findings_v1.md`
for full detail). No-partial beat with-partial on nearly every rule/cap
combination in the full 30-way sweep - taking the 33 percent partial
profit at 1.5R and moving the stop to breakeven appears to cost more
upside than it protects.

**Script:** `sim.py` (`get_trail_line()`, `update_swing_low()`,
`simulate_trail()`) - FINAL version saved to outputs 2026-09-03, entry-day
bug fixed directly in `simulate_trail()` (loop now starts at entry_idx
itself, entry day explicitly checked against the initial hard stop before
any trail logic can run).

**sim.py dependency:** RESOLVED. Both stages were computed inside the
same buggy loop; the fix is now in the saved final file. The previously
"finalized" 3-stage hybrid trail and swing-low pivot-window conclusions
that were built on the OLD buggy simulator are superseded by this
resweep's result (20-day MA / no-partial) and should be considered
outdated.

---

### STAGE 7 - Outcome / Result

**RESOLVED 2026-09-03.** Each simulated trade resolves to a realized
R-multiple and an exit reason, using the FINAL `sim.py` and its enforced
default configuration (7 percent cap, 20-day MA trail, no-partial - see
Stage 5/6). Regenerated across all 253 events in the current event
universe by calling `simulate_trail()` and `compute_risk_per_share()`
with NO extra arguments (pure defaults), confirming those defaults work
correctly end to end for a fresh caller.

**New outcome column:** `Outcome_Final_v1_P_R`, saved in
`Event_Universe_v4_Final_Outcomes_253Events.csv` (253 events, 13 columns
- includes the prior `Outcome_20ma_P_R` / `Outcome_HybridTight_P_R`
columns for reference/comparison, plus the new final column). Mean R =
0.418 across all 253 events, matching the resweep's headline number
exactly (see `Cap_Percent_and_Trail_Rule_Resweep_Findings_v1.md`) - a
good consistency check that the regeneration is correct.

**Script:** `sim.py`, FINAL version (see Stage 5/6) - `simulate_trail()`
and `compute_risk_per_share()`, called with pure defaults.

**sim.py dependency:** RESOLVED. This is the corrected, final output,
superseding both the original buggy `Outcome_20ma_P_R` /
`Outcome_HybridTight_P_R` columns and the intermediate 456-event dataset.
`Event_Universe_v4_Final_Outcomes_253Events.csv` is now the current
working dataset for all Stage 8 downstream work.

---

## STEP D - USE THE RESULTS

### STAGE 8 - Downstream Uses of Stage 7's Output

**UPDATED 2026-09-03 - re-validation against the final 253-event
dataset (`Event_Universe_v4_Final_Outcomes_253Events.csv`) is now
substantially complete. See detail below.**

Everything below CONSUMES the Stage 7 outcome columns rather than
generating them:

  1. **Scorecard attribute validation** (Stage 3's formulas) - RESOLVED.
     The 2-attribute score (MA Respect v5 + Relative Strength v2)
     re-checked against `Outcome_Final_v1_P_R` (the final, corrected
     outcome column): correlation 0.196, top-half mean 0.701R vs.
     bottom-half mean -0.132R (n=253) - holds up well under the final
     simulator settings, consistent with the intermediate checks done
     earlier in Stage 3/Trend-Efficiency-demotion work.

  2. **Conviction-based position sizing** - RESOLVED. Old
     `Conviction_Sizing_Model_v1.md` (built on the 3-attribute score, the
     unfiltered 456-event set, and the old buggy outcome column) is
     SUPERSEDED by `Conviction_Sizing_Model_v2.md`. MVP decision: FLAT
     position sizing (single R unit per trade), with one skip threshold -
     do not trade signals scoring below 2.5 (on the 0-5 2-attribute
     scale). A tiered sizing scheme was explored and showed a real but
     modest ~17 percent gain in R generated per unit of risk committed
     versus flat sizing - logged as a Phase 2 revisit item (see
     `Conviction_Sizing_Model_v2.md`), not adopted now. Rationale: sizing
     sits at the very end of the pipeline, downstream of components
     (attribute formulas, trail rule, cap percent) still expected to
     change in Phase 2 - added complexity now would likely need to be
     redone once those upstream pieces are re-tuned. `conviction_tiers_chart.png`
     (built on the old model) is now stale/superseded.

  3. **Trail rule selection** - RESOLVED. See Stage 6: 20-day MA,
     no-partial, is the MVP default (enforced in `sim.py`), based on the
     2026-09-03 resweep. `Trail_Stop_Logic_Finalized_v1.md` (the old
     5-rule comparison and "finalized" hybrid trail) is now SUPERSEDED by
     this resweep and should be considered outdated. Flagged as an
     MVP-adopted-but-not-fully-settled choice - see Future Enhancements
     item 0a for the Phase 2 revisit angle (whether the best rule varies
     by trade conditions).

  4. **Max-loss cap percent** - RESOLVED. See Stage 5: 7 percent is the
     MVP default (enforced in `sim.py` via `compute_risk_per_share()`),
     based on the 2026-09-03 resweep across 3/5/7 percent. Full findings
     in `Cap_Percent_and_Trail_Rule_Resweep_Findings_v1.md`.

  5. **Original trail-rule pilot/live-trade rounds** - DESCOPED
     (Dave, 2026-09-05). Round 1 was a small, hindsight-biased pilot (30
     hand-picked winning trades from early manual backtesting, run
     through the OLD buggy sim.py); Round 2 was live real-money trades
     with no simulator involved at all. Neither is a clean apples-to-apples
     reference point against the final pipeline - both predate the current
     rules and Round 1's sample was cherry-picked winners only. Decision:
     not worth reconciling against the final simulator/outcome column.
     The real validation going forward will come from running the actual
     finished MVP pipeline forward and studying ITS OWN results (which
     tickers made the list and why, which didn't and why, which trades
     won or lost) - a far richer feedback loop than comparing to these
     two early, non-comparable reference rounds.

---

## Summary: what's clean vs. what's reopened vs. what's missing

**Clean (no sim.py involvement):**
Stage 0 (momentum screen), Stage 1 (pre-watchlist screens), Stage 2 (touch
scan/event identification), Stage 3's formulas themselves (the math of the
3 attributes), Stage 4's fill mechanic, Round 2 live trades.

**Reopened / needs re-validation (sim.py-dependent):**
Stage 5 (initial stop sizing conclusions - first fixed-sim pass done,
needs full write-up), Stage 6 (trailing rule conclusions - partial
fixed-sim pass done), Stage 7 (the outcome columns themselves - not yet
regenerated in full), and everything in Stage 8 - attribute validation
evidence, conviction sizing tiers, finalized trail rule doc, max-loss cap
percent conclusion, Round 1 pilot trades.

**Missing entirely, updated 2026-09-03 (see
`Missing_Pipeline_Items_Response.md` for full detail):**
- Stage 0: RESOLVED - Dave provided a screenshot of the actual saved
  TradingView screen ("Momentum - Top Performers 6M"), confirming all 8
  criteria exactly as documented. Nothing outstanding here.
- Stage 1: RESOLVED - real code recovered for both checks, saved as
  `overhead_resistance_and_smoothness_checks.py`; reproduces the exact
  validation numbers already on record. What's genuinely outstanding is
  new work, not recovery: neither check has been run against the full
  ~1,002-ticker universe or against the 456-event dataset itself.
- Stage 2: RESOLVED - fully recovered as working code
  (`touch_scan_and_momentum_screen.py`), with one real bug found in the
  process (price > 50-day MA computed but not enforced - see Stage 2
  above for detail).

---

## MVP End-to-End Pipeline Demonstration (2026-09-05)

The full pipeline was run start to finish as real, chained code (momentum
screen partial -> touch scan -> overhead resistance -> scoring -> skip
threshold -> simulation) against the full 1,002-ticker universe,
independent of the previously curated 253-event set. Result: 226
realistic, non-overlapping trades from Oct 2023 to Aug 2026, 0.511R mean
(closed trades), 44.6% win rate, and - notably - the same clean,
monotonic score-tier separation found in the curated dataset reproduced
on this independently-built trade list. Full report:
`MVP_End_to_End_Pipeline_Demonstration_v1.md`. Trade-level detail:
`MVP_Pipeline_Run_226_Trades_v1.csv`. This is genuine evidence the
end-to-end MVP pipeline works and that its scoring signal is not an
artifact of the curated dataset.

## NEXT SESSION - Daily Automation Build (planned, not yet started, 2026-09-05)

Following the successful MVP end-to-end pipeline demonstration above, the
next planned phase is DAILY AUTOMATION: running the pipeline once a day
(intended to be run via Claude Code) to surface new trade signals and
report on existing open trades that closed, validated week over week
against real, forward, non-historical results.

Known scope items to work through at the start of that session, not yet
decided or built:
- A live/fresh daily data feed for the ticker universe (everything run
  so far uses the existing historical price file dump, not a live feed).
- Consolidating the separate pipeline scripts (touch scan, overhead
  resistance, scorecard, sim) into one clean, single script Claude Code
  can run unattended end to end.
- A persistent open-positions tracker (state carried day to day), so the
  daily run knows which tickers are already in an open trade (does not
  re-signal them) and can correctly detect and report when an open
  trade's trail stop actually triggers a close.
- Standing requirement (per the code-enforcement principle above):
  everything - scripts, state, decisions - must be retained as real
  artifacts, then pushed to GitHub for version control, so the whole
  system can be handed off to and executed by a new conversation or
  Claude Code with zero additional explanation needed.

**FIRST STEP for that next session (Dave, 2026-09-05):** before scoping
the automation build itself, review what is CURRENTLY in the GitHub repo
already, and reconcile/retain anything from this project's artifacts that
isn't in there yet. Do the inventory first, don't assume anything is
missing.

## Future Enhancements and Open Questions (Post-MVP)

*Deliberately deferred - not blocking the minimum viable end-to-end build.
Revisit once the pipeline is running start to finish.*

**0b. Momentum screen (Stage 0) - 3 of 8 criteria not codeable yet;
revisit in Phase 2 (NEW, 2026-09-05)**

4 of the 8 momentum-screen criteria (SMA100>SMA200, ADX(50) 20-40, avg
10-day volume >1M, 6-month performance 30-500%) are coded and enforced in
`touch_scan_and_momentum_screen.py`. The other 3 (market cap > 100M
dollars, TTM revenue growth YoY > 0 percent, primary listing) require
fundamental/exchange-metadata data not present in the current historical
price-only dataset, and are NOT currently enforced anywhere in code -
deferred to Phase 2. Needed: source fundamental data (market cap, revenue
growth, listing status) for the ticker universe, then fold those 3
checks into the screen so all 8 original criteria are enforced
end-to-end, not just 4.

**0a. Trail rule choice (20-day MA) - MVP-adopted but NOT considered fully settled; revisit in Phase 2 (NEW, 2026-09-03)**

The 2026-09-03 cap percent/trail rule resweep found the 20-day MA trail
(no-partial) to be the best performer, on average, across all 253 events
at every cap percent tested, and it is now the enforced default in
`sim.py`'s `simulate_trail()`. However, Dave flagged this as an
aggregate/blanket conclusion that may not hold uniformly - it's plausible
that different trail rules perform better for different trade
conditions (e.g. volatility regime, how fast/strong the initial move was,
or other event-level characteristics) rather than one rule being
universally best. Good enough to adopt as the single MVP default for now,
but should be revisited in Phase 2 with a more granular look - e.g.
does performance change materially when segmenting events by ADR,
by how the trade behaved shortly after entry, or by other attributes -
before treating "20-day MA always wins" as a settled, permanent
conclusion.


**0. Trend Efficiency v2 - weak/near-zero correlation with outcome, surprising, revisit in Phase 2 (NEW, 2026-09-03)**

Attribute scoring was re-checked against the new 253-event overhead-
resistance-filtered universe (`Event_Universe_v3_OverheadResistanceFiltered_253Events.csv`).
The combined 3-attribute score (Trend Efficiency v2 + MA Respect v5 +
Relative Strength v2, averaged) still separates outcomes well on this
cleaner set - top half by score averages 0.642R vs. 0.065R for the bottom
half (n=253). However, broken out individually: Relative Strength v2 is
now the strongest single driver (correlation 0.219 with Outcome_20ma_P_R),
MA Respect v5 is modest (0.139), and Trend Efficiency v2 is essentially
flat (0.034).

This was double-checked against the ORIGINAL unfiltered 456-event set
(before overhead resistance was applied) and Trend Efficiency v2's
correlation with outcome was already near-zero there too (0.011) - so
this is not an artifact of the overhead-resistance filtering; the
formula's real-world predictive power appears to have been weak all
along, independent of the recent filter change. Also confirmed Trend
Efficiency's score has no meaningful relationship with overhead-resistance
pass/fail (correlation -0.018), ruling out the theory that it was
indirectly doing overhead resistance's job.

Dave flagged this as counter-intuitive given the amount of redesign work
that went into the Gap Check + ATR Variability formula (see
`MA_Respect_Redesign_Notes.txt`), and it goes against his intuition about
what should be predictive here. Decision: table for now, do not touch the
MVP scoring blend, but revisit in Phase 2 - worth digging into whether the
formula needs redesign, whether it's measuring something too weakly
correlated with 20-day-forward outcomes specifically (vs. some other
horizon), or whether it should be dropped/reweighted in the combined
score.


**1. Close-above-50-day-MA as a separate filter/attribute (not an entry condition)**

Clarified 2026-09-03: Dave's real execution is a resting limit order that
fills the instant price touches the 50-day MA intraday - at the moment of
entry, there's no way to know yet whether the day will close above or
below the average. So requiring a close-above condition as part of the
ENTRY trigger itself would be looking into the future relative to how the
system is actually traded; the touch-only definition already used in
`touch_scan_and_momentum_screen.py` is correct as an entry mechanic and
should NOT be changed.

The open question is separate: does whether a touch day closes above vs.
below the 50-day MA carry useful signal as a FILTER on which touches
qualify as candidates, or as a scored attribute alongside the existing 3
(Trend Efficiency, MA Respect, Relative Strength) - evaluated after the
fact, not as a same-day entry gate. Worth testing empirically against the
456-event outcomes once the simulator and event universe are both on
solid footing.

**2. Overhead-resistance and smoothness checks - run at full scale**

RESOLVED 2026-09-03 - both checks were run against the full 456-event
dataset (first time at this scale). Result: overhead resistance was
PROMOTED into the active MVP pipeline (Stage 1); smoothness was SET ASIDE
- it showed no meaningful standalone discriminating power (0.171R alone,
barely above baseline) and was actively misleading when overhead
resistance wasn't also applied (-0.385R for events passing smoothness but
failing overhead resistance). Full analysis in
`Prewatchlist_Check_Validation_Findings_v1.md`. This directly confirmed
the original suspicion that some earlier scorecard noise (e.g. UNM) was
coming from events that either check would have excluded.

**2a. Smoothness - revisit post-MVP (NEW, 2026-09-03)**

Not dead, just deferred. Worth re-testing once the MVP pipeline is
running end-to-end - possible angles: different R-squared cutoffs or
lookback windows, combining it differently with overhead resistance
rather than as an independent AND-gate, or reframing it as a scored
attribute (like Trend Efficiency) rather than a hard pass/fail filter.

**2b. Overhead resistance - still needs a full-universe run (NEW, 2026-09-03)**

Validated against the 456-event historical set, but not yet run against
the live ~1,002-ticker universe for ongoing/daily watchlist generation.
Also, the 456-event dataset itself still doesn't have this check applied
upstream - rebuilding it with the filter applied at the source, and
re-validating Step B/C/D against the cleaner set, remains open (see Stage
1 for detail).

**3. TradingView momentum screen source-of-truth - RESOLVED 2026-09-03**

Dave provided a screenshot of the actual saved screen ("Momentum - Top
Performers 6M"), confirming all 8 criteria exactly as documented. No
longer an open item - kept here only as a record of resolution.
