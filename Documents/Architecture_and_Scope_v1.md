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

## Daily Automation - Orchestration Design (decided 2026-09-05)

**DECISION: a lightweight orchestrator script, not one big rewritten
script.** The daily automation will be a new, small orchestrator script
that IMPORTS and calls the existing, already-proven pipeline files as-is
- `touch_scan_and_momentum_screen.py`, `overhead_resistance_and_smoothness_checks.py`,
`scorecard.py`, `sim.py` (soon to live together in `pipeline/`, per the
GitHub reorganization above) - rather than duplicating their logic into
one large new file. Rationale: these four files are already validated
end to end (see the MVP pipeline demonstration above); the orchestrator's
only new job is sequencing them for daily/forward use and managing state.

**Daily run logic, four steps, run once per trading day:**
1. **Check existing open positions for exits.** For every ticker
   currently in an open trade (per the open-positions state file below),
   pull its updated price history and re-run `sim.py`'s
   `simulate_trail()` from the original entry point/price/risk-per-share.
   `simulate_trail()` does NOT need to be rewritten for this - it already
   walks forward day by day and returns either an exit (reason + realized
   R) or a "still open" state, so re-running it against updated data
   naturally tells us whether today's bar closed the trade. If it now
   reports an exit, close the trade and append it to the closed-trades
   log. If still open, leave it in the open-positions file and report its
   current unrealized R.
2. **Scan for new signals** using the touch-scan/momentum-screen logic,
   restricted to touches occurring on the current trading day only (not
   the full history - that's already accounted for).
3. **Filter today's new signals** through the overhead-resistance check
   and `scorecard.py`'s scoring, same logic as the backtest, applied only
   to today's touches.
4. **Open new trades** for any signal scoring >= 2.5 (the MVP skip
   threshold) on a ticker that is NOT already in an open position (same
   overlap/dedup rule used to build the clean 226-trade backtest result -
   see the MVP demonstration section above). Add newly opened trades to
   the open-positions state file.

**New persistent state required (does not exist yet, to be built next
session):**

**DECISION (Dave, 2026-09-05): TWO separate files, not one file with a
status column.** Considered combining open and closed trades into a
single file with an open/closed status column, but decided against it.
Rationale: open positions and closed trades behave completely
differently in practice. Open positions is small and "hot" - read AND
modified every single day as the orchestrator checks each live trade for
an exit. Closed trades should be a pure append-only historical log - once
a trade closes it is written once and never touched again, since it's the
permanent record used for week-over-week forward validation. A single
file with a mutable status column would mean "closing a trade" = finding
and editing an existing row in place, which is both riskier (a crash or
multi-day catch-up replay could corrupt an already-closed trade's row)
and produces messy day-to-day GitHub diffs, versus a true append-only log
where closing a trade is just adding a new line and existing history is
never rewritten.

- An **open-positions file** - ticker, entry date, entry price, risk per
  share, current status - for every trade currently live. Read and
  updated by every daily run. When `simulate_trail()` reports an exit for
  a position, that row is REMOVED from this file (not marked closed in
  place).
- A **closed-trades log** - append-only running history of every trade
  once it exits (ticker, entry date, exit date, exit reason, realized R,
  score at entry). A new row is APPENDED here the moment a position closes
  out of the open-positions file above. This is the record Dave will use
  for the week-over-week forward validation.

Note: partial-fill / partial-taken states are a non-issue for this
design under the current MVP no-partial decision (full size in, full
size out - see Stage 6 / Conviction_Sizing_Model_v2.md) - trades are
strictly open or closed, no in-between state to track. If tiered/partial
sizing is ever revisited in Phase 2, this two-file design would need a
fresh look at that time.

## Daily Automation - Market Calendar Built and Wired In (2026-09-05)

**DECISION: a static, hand-generated NYSE market-calendar CSV, not a
library dependency.** Dave's suggestion: rather than pull in an external
market-calendar library (e.g. `pandas_market_calendars`, which was also
unavailable to install in this sandbox), generate a fixed CSV of NYSE
open/closed dates covering the next several years, extend it as needed
over time, and revisit automating that generation only if it ever becomes
a real burden. Rationale: simpler, no new dependency risk, and easy for
Dave to visually spot-check if something ever looks wrong.

**Built `nyse_market_calendar_2026_2029.csv`** - one row per calendar day
from 2026-01-01 through 2029-12-31, with columns `date`, `market_open`
(True/False), and `reason_closed` (holiday name, "Weekend", or blank).
Standard NYSE holiday rules were computed directly (New Year's Day, MLK
Day, Presidents Day, Good Friday, Memorial Day, Juneteenth, Independence
Day, Labor Day, Thanksgiving, Christmas, each with weekend-observed-date
adjustment applied) and cross-checked against published 2026 NYSE holiday
sources - all dates matched exactly, including Good Friday (April 3,
2026) and Independence Day observed on Friday July 3 (since July 4 falls
on a Saturday in 2026). Covers 1,003 total market-open days across the
4-year span (~250/year, as expected). File saved to
`/mnt/user-data/outputs/nyse_market_calendar_2026_2029.csv`, pending
upload to `pipeline/` in GitHub alongside the other pipeline files.

**FLAGGED for the future**: this file will need to be regenerated/
extended before it runs out at the end of 2029. Not urgent - gives years
of runway - but worth remembering it's a static file with a hard edge,
not a self-updating source.

**`orchestrator.py` updated to use this calendar.** `get_trading_days_to_
process()` now filters the missed-day replay window against
`load_market_open_dates()` (reads the CSV above) instead of counting
plain calendar days, so weekends and holidays are correctly excluded from
the "days to replay" list rather than being wrongly treated as missed
trading days. Verified with two test scenarios: (1) a gap spanning only a
weekend plus Labor Day correctly resolved to zero missed trading days,
processing just the current day; (2) a gap including two genuine missed
trading days (Thursday and Friday before a weekend-plus-Labor-Day stretch)
correctly identified exactly those two days plus the days after the
holiday, in chronological order, correctly skipping the weekend and
Labor Day itself.

**Repo placement + path-resolution fix**: this file belongs in
`pipeline/` alongside `orchestrator.py`, `parameters.py`, and the other
pipeline scripts - same category as `parameters.py`, a static,
code-adjacent config file the code depends on to run, fitting the
architecture-and-code-only repo rule the same way. While confirming this,
caught and fixed a real fragility: `orchestrator.py` originally looked
for this CSV using a plain relative path, which would only resolve
correctly if the script happened to be run from exactly the right working
directory - a real risk for a GitHub Action or Claude Code invocation.
Fixed by resolving `MARKET_CALENDAR_PATH` relative to the script file's
own location (`os.path.dirname(os.path.abspath(__file__))`) instead of
the current working directory. Verified by running the script's calendar
loader from a different directory entirely (`/tmp` rather than the
pipeline folder) and confirming it still correctly found and loaded all
1,003 open trading dates.


## Daily Automation - GitHub Actions Scheduling Built (2026-09-06)

**State files relocated to a dedicated `state/` folder at the repo
root**, separate from `pipeline/` (code). `orchestrator.py` updated:
`OPEN_POSITIONS_PATH`, `CLOSED_TRADES_PATH`, `LAST_RUN_PATH` now resolve
via `os.path.dirname(os.path.abspath(__file__))` (one level up from
pipeline/, into state/), same fix pattern as `MARKET_CALENDAR_PATH`, so
this works correctly regardless of the working directory a GitHub Action
invokes the script from. Verified via a mock repo structure (pipeline/,
data/, scripts/, state/ folders) run from the repo root: `state/` folder
auto-created correctly, and a forced real trading day (2026-08-19)
correctly opened the OKTA trade and wrote both `open_positions.csv` and
`last_run_date.txt` into the new `state/` location.

**`orchestrator.py` `main()` now exits with a proper process exit code**
(0 on success, 1 on any unhandled exception, with a printed traceback to
stderr) - required for GitHub Actions to detect success vs. failure at
all; previously a silent Python exception and a clean run looked
identical from the workflow's point of view. Verified both paths
directly: a forced exception correctly produced exit code 1 with a clean
traceback, and a normal run correctly exited 0.

**Built `pipeline/check_run_window.py`** - solves the fact that GitHub
Actions "schedule" triggers only run on UTC, while US Eastern time shifts
between UTC-4 (EDT) and UTC-5 (EST) twice a year. Rather than hardcode
one offset and have the run silently drift an hour off twice a year, the
workflow defines FOUR cron triggers (6pm EDT, 6pm EST, 8pm EDT retry, 8pm
EST retry - one for each real-clock-time possibility), and this script
checks the actual, current Eastern time at run time (using `zoneinfo`,
which correctly handles the EDT/EST transition automatically) to decide
whether THIS specific trigger should do anything. On any given day, two
of the four triggers are legitimate and two are no-ops. Also decides
whether the 8pm trigger counts as a real "retry": it checks whether
`state/last_run_date.txt` already reflects today's date - if the 6pm run
already succeeded today, the 8pm trigger has nothing to do and skips
itself. Verified with six scenarios: 6:05pm Eastern correctly matches
only the 6pm window; 8:10pm Eastern correctly matches only the 8pm
window; 10:00pm Eastern (a "wrong season" duplicate trigger) correctly
matches neither; and the already-ran check correctly returns false with
no state file, true with a matching date, false with a different date.

**Built `.github/workflows/daily_scorecard_automation.yml`** (saved as
`daily_scorecard_automation.yml` in outputs, destined for
`.github/workflows/` in the repo). Sequence per trigger: check out repo,
install dependencies, run `check_run_window.py` to gate whether to
proceed, if yes run `scripts/pull_data.py` then `pipeline/orchestrator.py`,
then (this is the key missing piece caught and fixed this session) commit
the updated `data/` and `state/` folders back into the repository so the
next run has the correct starting point - GitHub Actions runs start from
a clean checkout every time, so without this commit-back step the
orchestrator's state and the freshly-pulled data would be silently
discarded at the end of every run and each day would start from scratch
with no memory of prior days. Finally, sends a failure-notification email
only if this was the 8pm retry slot and it still failed, or a daily
summary email on success. YAML syntax validated.

**NOT yet built (explicitly still pending, next session): the two email
scripts this workflow references** - `pipeline/send_summary_email.py`
(daily summary: new trades, closed trades with results, current open
status) and `pipeline/send_failure_email.py` (failure notification). The
scheduling and data-refresh half of this workflow is built and tested,
but it will not run end-to-end successfully in GitHub yet since those two
steps currently point at scripts that don't exist. Flagged clearly as a
hard dependency before this workflow can go live.


## Daily Automation - Email Scripts Built + Credentials Configured (2026-09-06)

**Built `pipeline/send_summary_email.py`.** Sends the daily summary email
referenced by the workflow. Stateless by design -- rather than have
`orchestrator.py` track and pass along "what happened today," this
script re-reads `state/open_positions.csv` and `state/closed_trades.csv`
after the fact and filters for rows matching the target date (the most
recent date `orchestrator.py` actually processed, per
`state/last_run_date.txt` -- not necessarily today's literal calendar
date, which matters correctly during a missed-day replay). Reports new
trades opened, trades closed with results, and the full current
open-positions snapshot with total capital committed. Verified the
content-building logic directly against real state data from the mock
repo test: correctly picked up the last-processed date, correctly showed
zero new/closed trades for that date, and correctly listed the one open
OKTA position with accurate totals.

**Built `pipeline/send_failure_email.py`.** Deliberately minimal --
sends a simple alert (not an automated diagnosis) directing Dave to check
the GitHub Actions run logs directly, since if something failed badly
enough to reach this point, that's the safer path than trusting an
automated guess at the cause. Per the workflow's condition, this only
fires if BOTH the 6pm primary run and the 8pm retry fail for the same
day -- a single failed 6pm attempt does not trigger this on its own,
since the 8pm retry might still succeed. Verified: correctly exits with
code 1 and a clear error message when credentials aren't set, rather
than failing unpredictably.

Both scripts use Gmail's SMTP server and read `EMAIL_ADDRESS`,
`EMAIL_APP_PASSWORD`, `EMAIL_TO` from environment variables, populated by
the workflow from the three repo secrets (see below).

**GitHub repo secrets configured (Dave, 2026-09-06).** Three repository
secrets added under Settings > Secrets and variables > Actions >
Repository secrets, matching exactly what
`daily_scorecard_automation.yml` expects: `SCORECARD_EMAIL_ADDRESS`,
`SCORECARD_EMAIL_APP_PASSWORD`, `SCORECARD_EMAIL_TO`. Dave is using his
own Gmail address as both sender and recipient (sending the daily summary
to himself), authenticated via a Gmail App Password (generated under
Google Account > Security > 2-Step Verification > App Passwords) rather
than his real account password, since Gmail blocks plain password logins
for third-party scripts. Security note discussed and Dave's decision
logged: an app password grants send/possibly-read access to the Gmail
account it's tied to, but is scoped, GitHub-secret-encrypted, never
printed in logs, and instantly revocable from the Google account without
affecting the main password or any other app passwords -- Dave considered
a separate dedicated sending-only Gmail account as an extra-cautious
alternative but chose to proceed with his main account for now, given the
easy revocability.

**Files uploaded to GitHub `pipeline/` folder this session (Dave,
2026-09-06):** `send_summary_email.py`, `send_failure_email.py`, plus
(per earlier in this session) `nyse_market_calendar_2026_2029.csv`,
`orchestrator.py` (updated), `touch_scan_and_momentum_screen.py`
(bug-fixed version), `check_run_window.py`, and
`.github/workflows/daily_scorecard_automation.yml` in its required
special location. `Architecture_and_Scope_v1.md` also re-uploaded to
`Documents/`.

**STATUS: the daily automation build is now believed complete end to
end** -- data pull, orchestration, state tracking, missed-day replay
against a real market calendar, GitHub Actions scheduling across the
Eastern-time DST boundary, and both email notifications, are all built,
individually tested in a sandbox, and now live in the repo with
credentials configured. **NOT yet done: a real, live end-to-end test of
the actual GitHub Actions workflow** -- either waiting for the next
scheduled 6pm Eastern trigger to fire naturally, or manually triggering
it via the workflow's `workflow_dispatch` option in the GitHub Actions
UI, to confirm the whole chain (data pull, orchestrator, git commit-back,
email send) works correctly in the real GitHub environment, not just
against sandboxed test data. This is the natural next-session starting
point.


## Daily Automation - First Live Workflow Test + Manual Force-Run Added (2026-09-06)

**First-ever live run of the actual GitHub Actions workflow** (Dave,
manually triggered via the Actions tab's "Run workflow" button). Result:
Success, in 20 seconds. Correctly revealed that the manual trigger was
being gated by the exact same Eastern-hour check as the automatic
schedule -- since the manual click happened mid-afternoon, nowhere near
6pm or 8pm Eastern, `check_run_window.py` correctly determined it wasn't
a legitimate run window and set `should_run=false`, so every downstream
step (data pull, orchestrator, commit-back, both emails) correctly
skipped itself rather than running. This was the correct, intended
behavior, but revealed the manual trigger wasn't useful for on-demand
testing as originally wired.

**FIX: added a manual force-run override.** `daily_scorecard_
automation.yml`'s `workflow_dispatch` trigger now takes a `force_run`
boolean input (default false), passed through to `check_run_window.py`
as a `FORCE_RUN` environment variable. When `FORCE_RUN=true`,
`check_run_window.py` bypasses the Eastern-hour check entirely and
returns `should_run=true` immediately -- giving Dave an on-demand way to
run the real pipeline any time via the Actions UI, for testing, while
leaving the automatic scheduled triggers' behavior completely unchanged
(they never set `FORCE_RUN`, so they still only fire within the real
6pm/8pm Eastern windows). Verified directly: with `FORCE_RUN=true`, the
script correctly bypasses the clock and returns `should_run=true`
regardless of actual time (tested at 1:17pm Eastern); with `FORCE_RUN`
unset, behavior is unchanged from before -- correctly returns
`should_run=false` outside the real windows.

**Also discussed and confirmed for Dave: the 20-minute tolerance window**
on the 6pm/8pm checks (already built, see the "Market Calendar Built and
Wired In" section above) exists specifically to absorb both GitHub
Actions' own scheduling imprecision (scheduled triggers can fire a few
minutes late during high load) and any minor clock misalignment -- a
run would need to be delayed by more than 20 minutes to be incorrectly
skipped.

**NEXT STEP (unchanged): Dave to upload the updated `check_run_window.py`
and `daily_scorecard_automation.yml` to GitHub, then either use the new
`force_run` manual option to see a real full pipeline run end-to-end
(data pull, orchestrator, commit-back, and an actual summary email
landing in his inbox), or simply let it fire naturally at the next real
6pm Eastern trigger tonight.**


**SECOND LIVE TEST, using the new force_run checkbox -- FAILED, real bug
found and fixed.** With `force_run` checked, the time-gate correctly
passed and the real pipeline attempted to run for the first time. Failed
at the "Pull fresh market data" step: `pull_data.py` uses
`pd.read_html()` to scrape the S&P 400 / S&P 600 constituent lists from
Wikipedia, which requires the `lxml` package as a parsing backend --
`ImportError: Import lxml failed. Use pip or conda to install the lxml
package.` The workflow's "Install dependencies" step only installed
`pandas`, `numpy`, and `requests`, missing this one dependency.

**Fix applied**: added `lxml` to the pip install line in
`daily_scorecard_automation.yml`. Cross-checked `pull_data.py`'s full
import list to confirm no other missing dependencies -- everything else
it imports (`csv`, `datetime`, `json`, `random`, `time`,
`concurrent.futures`, `io`, `pathlib`) is part of Python's standard
library and needs no separate installation.

**THIRD LIVE TEST -- FAILED again, a different real bug found and
fixed.** With the `lxml` fix in place, "Pull fresh market data" and "Run
orchestrator" both succeeded this time. Failed at the "Commit updated
data and state files back to the repo" step: `fatal: pathspec 'state/'
did not match any files`, exit code 128. Cause: today (Sunday, September
6) is not an actual trading day, so the orchestrator correctly did
nothing and the `state/` folder, while created, stayed completely empty
-- git does not track empty directories, so `git add data/ state/` threw
a hard error trying to add a folder with nothing in it, rather than
treating "nothing to add" as a harmless no-op.

**Fix applied**: `git add data/ state/` now has `|| true` appended so a
failed add (e.g. an empty directory) doesn't halt the step, and the
subsequent commit-and-push logic is wrapped in an explicit if/else on
`git diff --cached --quiet` -- only actually commits and pushes inside
the "else" branch, i.e. only when something was genuinely staged.
Verified locally against both scenarios in a real git repo: an empty
state folder (reproducing the exact failure) now completes cleanly with
a "nothing to commit" message instead of erroring out; and, separately,
real new file content in both folders correctly still triggers an actual
commit.

**NEXT STEP (updated again): Dave to upload the corrected
`daily_scorecard_automation.yml` and re-run with `force_run` checked
once more.** Given today isn't a real trading day, this next run is
expected to succeed but produce an empty/no-op result from the
orchestrator itself (no trades, no state changes) -- that's expected,
not a bug. A cleaner full functional test (a real trading day, actual
signals, an actual commit, and an actual summary email) will only be
possible once the automation is tested on or after this data's actual
next trading day, or Dave could temporarily test against a known
historical trading day if he wants to see the full chain fire for
real sooner.

**FOURTH LIVE TEST -- SUCCESS (empty/no-op result, as expected).** With
the commit-step fix in place, a force_run triggered on a non-trading day
(Sunday, September 6) completed with every step green, including the
summary email step. Confirmed this is the whole mechanical chain working
correctly end to end: data pull, orchestrator, commit-back, and the
conditional email send all fired in the right order with the right
conditions. The summary email correctly did NOT get sent with real
content -- `send_summary_email.py` found no `state/last_run_date.txt`
yet (this being the very first real run ever, on a day that isn't an
actual trading day) and correctly, deliberately bailed out with "nothing
to summarize, skipping email" rather than sending something confusing or
blank. This is correct defensive behavior, not a bug.

**Attempted, NOT completed: a manual test-override to point the
orchestrator at a specific real past trading day** (e.g. Friday,
September 4) so the full pipeline could be exercised against genuine
signal/sizing activity and produce a real summary email. Started adding
a `TEST_OVERRIDE_DATE` environment variable to `orchestrator.py`'s
`main()` (test-only, not used by the real scheduled triggers), but the
edit did not get successfully saved to the output file before the
session ended -- **this is not sitting ready in outputs and was never
uploaded.** Treat as not-yet-attempted if picked up again.

**DECISION: rather than force a synthetic historical test, Dave opted
to simply let the automation run for real** at the next actual trading
day. Monday, September 7, 2026 is Labor Day (market closed, confirmed
in the NYSE calendar), so the first genuine live trading-day run will be
Tuesday, September 8, 2026 at the real scheduled 6pm Eastern trigger --
no manual action needed, it should just fire on its own. This will be
the first true end-to-end proof: a real signal scan, possible new
trade(s) or exits, a real state commit, and a real summary email
landing in Dave's inbox.

**NEXT SESSION: check whether Tuesday's real 6pm run succeeded** (GitHub
Actions tab, or the summary email itself if it arrived) and review
whatever it actually did -- this is the natural starting point next
time.


## Daily Automation - First Test Run + Bug Fix (2026-09-05)

**Ran `orchestrator.py` for the first time**, against the historical
1,002-ticker dataset (data only extends through 2026-08-19). Two real
findings, one fixed immediately, others logged for next session:

**FIXED: `wilder_adx` import triggered the entire `touch_scan_and_
momentum_screen.py` script as a side effect.** That file was originally
written as a standalone top-level script - all its scan logic sat at
module level, not inside a function. `orchestrator.py` does
`from touch_scan_and_momentum_screen import wilder_adx` to reuse that one
calculation, but because Python executes a module's top-level code on
import, this silently ran the ENTIRE historical scan (against that file's
own hardcoded, stale DATA_DIR) every time it was imported - confirmed via
the test run's unexpected "Found 0 ticker files" output. It failed
silently in testing only because that hardcoded path doesn't exist here;
in a real deployment this could error out or silently duplicate a full
historical rescan on every daily run.

**Fix applied**: wrapped all of that top-level scan logic inside a new
`run_full_historical_scan()` function, with a `if __name__ == "__main__":`
guard so it still runs standalone when the file is executed directly, but
importing anything from the file (like `wilder_adx`) no longer triggers
it. Verified: (1) `from touch_scan_and_momentum_screen import wilder_adx`
now imports cleanly with no side-effect output, (2) `orchestrator.py`
re-run end to end with no errors, (3) `find_new_signals_for_date()`
re-tested directly against 2026-08-19 and still correctly finds the OKTA
signal (score 3.5) matching one of the 4 known-open trades from the MVP
pipeline demonstration - confirms the underlying signal logic still works
correctly after the fix.

**NOT fixed yet, logged for next session (per the test run's other
findings, discussed in conversation this session):**
- Missed-day replay uses plain calendar days, not a real trading-day
  calendar - needs a market-calendar library (e.g.
  `pandas_market_calendars`) substituted in before production use, so
  weekends/holidays aren't wrongly treated as missed trading days needing
  catch-up.
- No live/fresh daily data feed exists yet - today's test run correctly
  found zero new signals for "today" (2026-09-05) simply because local
  data doesn't extend past 2026-08-19. This isn't a bug, it's the
  expected state until the daily `pull_data.py` run is actually wired up
  and running on schedule.
- Email sending, GitHub Actions scheduling (6pm ET / 8pm ET retry /
  failure email) still not implemented - `orchestrator.py` currently only
  prints progress to the console.

## Daily Automation - orchestrator.py Written (first pass, 2026-09-05)

**A real, working `orchestrator.py` has been written** implementing the
full design above: loads/saves the two state files, checks open positions
for exits by re-running `sim.simulate_trail()`, scans for new signals
restricted to a single target date, filters through overhead-resistance
and `scorecard.score_total_v2()`, sizes and opens new trades off the
static account balance in `parameters.py`, and flags if total committed
capital exceeds the account balance. It also implements missed-day
replay: it tracks the last successful run date in a small `last_run_date.txt`
file and, on each run, processes every day from the day after that
through today, in order, one at a time.

**This is a first-pass scaffold, not yet production-ready. Known open
gaps, to be addressed before this runs unattended for real:**
- It has NOT been run/tested end to end yet against live or refreshed
  data.
- Email sending (the daily summary and the failure-notification email)
  is NOT implemented yet - `orchestrator.py` currently only prints
  progress to the console.
- The 6pm ET / 8pm ET retry / GitHub Actions scheduling itself is not
  implemented yet - this file is just the script that would get run by
  that schedule.
- Missed-day replay currently uses plain calendar days, NOT a real market
  holiday/weekend calendar - it will currently treat weekends as "missed
  trading days" needing catch-up, which is wrong. A market-calendar
  library (e.g. `pandas_market_calendars`) should be substituted in
  before production use.
- `find_new_signals_for_date()` reimplements the touch-scan and
  momentum-screen conditions restricted to one date, rather than calling
  `touch_scan_and_momentum_screen.py` directly, because that file is
  written as a standalone top-level script (module-level code that runs
  a full scan on import), not as a callable function. If that file is
  ever refactored into an importable function, the orchestrator should
  call it directly instead of maintaining a parallel reimplementation of
  the same logic.

File saved to `/mnt/user-data/outputs/orchestrator.py`, pending upload to
`pipeline/orchestrator.py` in GitHub alongside the other pipeline files.

## Daily Automation - Touch-Scan Restriction (decided 2026-09-05)

**DECISION: restrict `touch_scan_and_momentum_screen.py` to evaluate only
ONE target date per call, not a full history scan.** As originally built
(and as run in the MVP end-to-end pipeline demonstration above), this
script scans an entire multi-year date range per ticker and returns every
qualifying touch event found across the whole window (1,476 touches
across the full historical run). For daily/forward use this is both
wasteful and wrong to keep as-is - every prior day's touches are already
known and already correctly acted on (either they became a trade now
sitting in the open-positions file, or they didn't qualify and were
correctly ignored), so re-scanning full history every day would just mean
building extra logic to figure out which of the returned results are
actually new.

Instead, the script needs a mode where it is told a specific target date
and evaluates ONLY whether that single date is a touch event per ticker -
still loading whatever trailing price history it needs behind that date
to compute the 50-day MA comparison, but only emitting a result for the
one target date, not the whole lookback window.

**This target date is NOT hardcoded to literally "today."** It ties
directly into the missed-day replay design above: on a normal day the
orchestrator calls this with target date = today, but during a catch-up
replay after a gap, the orchestrator calls it once per missed day, in
order, each time with that day's date as the target - never trying to
open a trade retroactively "as of" a past date in a way that wasn't
actually evaluated in sequence. Dave's own framing: logically, you can't
go back and place a trade for yesterday, so each day (real or replayed)
must be evaluated in its own right, in order.

## Daily Automation - State File Schemas (decided 2026-09-05)

**Open-positions file columns:**
- `ticker`
- `entry_date`
- `entry_price`
- `risk_per_share` (dollars - the stop distance `simulate_trail()` needs
  to re-run this position forward each day)
- `score_at_entry` (the `score_total_v2` value that triggered the trade)
- `shares` (position size in shares)
- `position_cost` (dollars - `shares * entry_price`; summed across all
  rows to get total capital currently committed, checked against the
  account balance in `parameters.py`)

**Closed-trades log columns:** everything in the open-positions file,
plus:
- `exit_date`
- `exit_reason`
- `exit_price`
- `realized_R`
- `realized_pnl` (dollars)

This is the append-only record used for week-over-week forward
validation.

**DECISION: dollar risk per trade is calculated off the STATIC starting
balance, not a running/compounding balance.** I.e. `risk_per_share` and
`position_cost` are computed using `RISK_PERCENT_PER_TRADE` (1 percent)
applied to the fixed `ACCOUNT_STARTING_BALANCE` (25,000 dollars) in
`parameters.py`, every time, regardless of how the running total of
closed-trade wins/losses has actually moved account equity up or down.
Rationale (Dave, 2026-09-05): simpler to start with, and meaningfully
easier to troubleshoot while the system is still being validated, since
every trade's dollar sizing is independently checkable against one fixed
number rather than a constantly shifting running balance. **Flagged as a
likely future revisit**: once the system is trusted and running smoothly,
switching to sizing off a running/updating equity balance would be more
realistic to how a real account is actually traded - not urgent, logged
here as the known next step if/when it comes up.

## Daily Automation - Schedule and Failure Handling (decided 2026-09-05)

**DECISION: daily run time is 6:00 PM Eastern.** Chosen to sit safely
after market close and after Yahoo Finance's daily OHLCV data for the
session is fully settled and available, with real buffer built in.

**DECISION: one retry, two hours later, then a failure email.** If the
6:00 PM Eastern run fails outright (a genuine error - API outage, bug,
etc. - not to be confused with the separate "missed day" catch-up/replay
logic covered above, which handles the orchestrator simply not running on
a given day at all), the orchestrator retries once at 8:00 PM Eastern. If
that retry also fails, send a failure notification email so Dave knows
immediately rather than only noticing by the absence of the normal daily
summary email.

## Daily Automation - Delivery, Account Tracking, and parameters.py (decided 2026-09-05)

**DECISION: fully automatic, unattended daily trigger.** The orchestrator
runs on its own schedule (e.g. a scheduled GitHub Action) with no manual
kickoff needed day to day. Rationale (Dave): at this stage the system
only ever writes results to a file - no broker order placement exists yet
(that remains a possible future step, likely a few months out at the
earliest, only after the forward-tracked results build enough of a track
record) - so there is no real-money risk in letting it run itself daily.

**DECISION: daily results delivered by email.** At the end of each daily
run, a summary email is sent covering: new trades opened today, trades
closed today (with exit reason and realized R), and current status of all
still-open positions. Mechanically this uses a standard app-password-based
email send (Gmail/Outlook or similar), with the app password stored as a
GitHub Actions secret - a solved, standard pattern, not a research item.

**DECISION: add dollar-based account tracking on top of the existing
R-multiple system.** The scoring/sizing system currently expresses
everything in R multiples only, with no dollar or account-size
assumption anywhere (see Conviction_Sizing_Model_v2.md). Dave wants the
daily email to also surface real dollar figures - specifically, total
dollar cost currently committed across all open positions, so it's
obvious if the simulated system would ever try to commit more capital
than a real account of this size could actually support. This requires
exactly two new inputs to convert R into dollars:
- **Account starting balance**: 25,000 dollars (simulated - no live
  broker connection exists yet).
- **Risk per trade**: 1 percent of account equity per trade - i.e. what
  one unit of R represents in dollar terms.
With these two numbers, the orchestrator can compute dollar risk per
trade, total dollar cost of all currently open positions, and flag if
total capital committed ever exceeds the assumed account balance.

**DECISION: new `parameters.py` config file** (Dave's suggestion) added
to the repo, in `pipeline/` alongside the other pipeline scripts (fits
the architecture-and-code-only rule - this is a code-adjacent config
file, not a findings doc). Holds `ACCOUNT_STARTING_BALANCE` (25,000) and
`RISK_PERCENT_PER_TRADE` (0.01) today, and is the designated future home
for other tunable constants currently hardcoded across individual
pipeline files (cap percent, trail-rule settings, the 2.5 skip-score
threshold, etc.) as those come up for revisit, rather than editing values
inside scattered script files directly. File saved to
`/mnt/user-data/outputs/parameters.py`, pending upload to
`pipeline/parameters.py` in GitHub alongside the other pipeline files
already slated for that folder.

**DECISION: missed days are replayed, not skipped.** If the orchestrator
doesn't run on a given day (market holiday, a crash, simply not run), the
next time it runs it must catch up by replaying each missed trading day
IN ORDER, one day at a time, exactly as if it had run live that day -
checking open positions for exits and scanning for new signals on day 1
of the gap, then day 2, etc. - rather than jumping straight to the
present or trying to reconcile a multi-day gap in one pass. Rationale
(Dave): skipping days risks missing a trade's actual exit trigger or
otherwise producing an inaccurate forward record.

**FIRST STEP for that next session (Dave, 2026-09-05):** before scoping
the automation build itself, review what is CURRENTLY in the GitHub repo
already, and reconcile/retain anything from this project's artifacts that
isn't in there yet. Do the inventory first, don't assume anything is
missing.

**DATA RETENTION REQUIREMENT for the daily pull (confirmed 2026-09-05):**
The daily data-pull step must maintain a minimum ROLLING 2-YEAR (730
calendar day) window of daily OHLCV history per ticker, refreshed every
day. This is a hard floor, not a rough guess - it comes directly from
`overhead_resistance_and_smoothness_checks.py`'s `overhead_resistance_check()`,
which is the single longest lookback anywhere in the pipeline:
  - It looks back `lookback_years=2` (2 years) from the as-of date.
  - Within that window it applies a `grace_days=60` buffer - i.e. it
    excludes the most recent ~2 months of that window when determining
    whether an "old high" is still unresolved overhead resistance.
  - It requires a minimum of 100 days of history in the window just to
    return a verdict at all (returns None / skips silently below that).
Every other stage's lookback need is comfortably shorter than this
(SMA200 for the momentum screen = 200 trading days, the smoothness
check's R-squared window = 126 trading days). So 2 years is the true
floor for how much history must be kept available per ticker at all
times - NOT the 3 years used for the original historical backtest (that
long a window was only needed to validate across multiple market
cycles, not to run the system forward day to day). Recommend keeping
some margin above the bare 2-year floor (e.g. maintaining roughly 2
years plus a few weeks of buffer) rather than cutting it exactly at 730
days, so the check never silently degrades to "no eligible history yet"
for a ticker sitting right at the edge.

Practically, this means the daily pull script Claude Code builds should:
append each new day's OHLCV bar per ticker to its existing history file
(the same per-ticker CSV format already used in `data/`), and does NOT
need to re-pull 3+ years from scratch each day - only fetch what's new
since the last successful pull, while ensuring at least ~2 years plus
buffer of trailing history remains available locally per ticker at all
times.

**DECISION (Dave, 2026-09-05): existing `pull_data.py` adopted as-is for
daily use, full re-pull design, NOT incremental.** On reviewing the
existing `scripts/pull_data.py` (already in the GitHub repo, used to
build the original 1,002-ticker dataset), it turns out it already
satisfies the 2-year floor above with real margin - it pulls a rolling
3.6-year window (`LOOKBACK_DAYS = int(3.6 * 365)`) fresh from Yahoo
Finance every time it runs, completely overwriting each ticker's CSV
rather than appending just the new day's bar. Because `START_TS`/`END_TS`
are both computed from "now" on every run, the window slides forward
together each day - one day gained at the front, one day lost off the
back - so the total width stays constant at ~3.6 years indefinitely.
That's a full re-pull, not an incremental daily append.

Dave elected to keep this design and run it as-is once a day, rather
than build a lighter incremental version, for now. Rationale: 1,002
tickers is not a large enough pull to cause real bandwidth/API concern,
the extra width (3.6 years vs. the 2-year floor) gives useful slack while
the system is still being tuned, and a full-repull approach is naturally
self-healing (no gap/drift risk from missed incremental runs) and simpler
to reason about. If bandwidth, Yahoo rate-limiting, or run-time ever
becomes a real problem at daily cadence, the documented fallback is to
redesign this script to do a true incremental append (fetch/append only
the newest day(s) per ticker) instead of a full re-pull - not urgent or
expected to be needed in the near term, but logged here as the known
next step if it ever is.

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


**PHASE 2 ITEM (NEW, 2026-09-09): Universe scope expansion + TradingView-sourced screening -- planning discussion, no code yet**

Revisited the current universe scope (S&P 400 + S&P 600 only, pulled via
Wikipedia scrape in `pull_data.py`'s `get_sp400_sp600_tickers()`), which
was a deliberate "get something running with real data" simplicity
choice early on, NOT a permanent design commitment. Dave's live
TradingView screen ("Momentum - Top Performers 6M") has no such index
restriction, and Dave wants to close that gap.

**Confirmed gap: three of the eight documented momentum-screen criteria
are not enforced ANYWHERE in code today** -- market cap > $100M, TTM
revenue growth YoY > 0%, and primary listing status. These were
historically covered implicitly, by Dave's manual TradingView export
already having filtered for them before tickers ever reached our
pipeline. That implicit coverage disappears entirely once ticker
selection is automated with no TradingView step in between, so this is a
real, currently-unenforced gap, not just a nice-to-have expansion.

**Yahoo Finance option investigated:** `yfinance`/Yahoo's ticker `.info`
endpoint does expose market cap and revenue growth (same underlying,
unofficial Yahoo endpoint already used for price history, so no new
data source needed). Two caveats: (1) it's a per-ticker lookup, not
bulk, so screening a much larger universe means a much larger number of
individual calls than today's price pull, with real rate-limit risk if
not throttled carefully; (2) revenue growth reflects Yahoo's most
recently reported figure, which can lag actual current reality by a
quarter or more depending on filing timing -- a general fundamentals
data-lag reality, not something specific to Yahoo.

**TradingView-sourced option investigated, and preferred lead:** the
`tradingview-screener` Python package (unofficial, but talks directly to
TradingView's own screener backend -- same numbers Dave already sees
live in the tool, not a separately-sourced approximation) supports
querying 3,000+ fields including exactly the criteria needed --
`market_cap_basic`, revenue growth, 6-month performance, SMA100/SMA200,
ADX, average volume -- in a SINGLE bulk call returning a full filtered
table, not one-ticker-at-a-time. This could plausibly replace both the
universe-scope problem AND parts of the existing technical re-derivation
logic in `touch_scan_and_momentum_screen.py` (the SMA-stack and ADX
checks currently reimplemented from raw price data could instead come
directly from TradingView's own calculation). Caveats: unofficial/
reverse-engineered interface (same risk category as the existing Yahoo
scrape -- could break if TradingView changes something, and sits in a
gray area re: their terms of service for automated pulls); package docs
explicitly warn to be mindful of server load / potential bans on large
pulls, so daily full-universe queries would need deliberate throttling,
not naive unrestricted pulls. NOT YET INSTALLED OR TESTED -- this
session's sandbox had no general package-install/internet access to
verify live; a real test against actual TradingView data is the
required first step of the dedicated follow-up session.

**Proposed sequencing for that follow-up session (agreed with Dave):**
(1) decide final universe scope (e.g. add S&P 500 back in for S&P 1500
coverage, vs. going broader); (2) prototype `tradingview-screener`
live -- confirm field availability/naming, confirm bulk-query mechanics,
and confirm throttling needs before relying on it; (3) if it works,
relocate the SMA-stack/ADX checks from reactive touch-time
reimplementation into a proper upfront universe screen, and source
market cap / revenue growth / primary listing from it directly,
retiring the Wikipedia S&P 400/600 scrape; (4) if it does NOT pan out,
fall back to the Yahoo `.info` per-ticker approach with explicit
throttling built in from the start.

**IMPORTANT ADDITIONAL REQUIREMENT surfaced by Dave, applies regardless
of which data-source approach is chosen:** whatever the final screened
candidate list is for a given day, the data-pull step must fetch price
data for the UNION of (screen-passing tickers) and (all currently open
positions) -- never gate data collection on the screen result alone. An
open position can legitimately fall out of the screen (momentum fades,
fundamentals change) or, under the current system, fall out of S&P 400/
600 index membership, without the trade itself being closed. If data
collection stops for a ticker the moment it drops off the
screen/index, that open position's price history goes stale and
`check_open_positions_for_exits()` can no longer correctly detect a
stop-out or update the trail -- a silent, dangerous gap. This is
actually a LATENT bug in the current system too (S&P 400/600 membership
can change), not just a future risk introduced by the TradingView
migration -- worth fixing as its own small, immediate correctness fix in
`pull_data.py` / `get_sp400_sp600_tickers()`'s caller, independent of
whether/when the broader universe-expansion work happens.

---

## 2026-09-09 (continued) -- Local Prototyping Plan, and New Phase 3: Trade Execution Automation

**Local prototyping approach for `tradingview-screener` confirmed.** This
session's sandbox environment has no live internet/pip access (pip
install and direct PyPI fetch both failed -- confirmed again this
session), so live testing of the `tradingview-screener` package cannot
happen inside this Claude conversation. Dave does not currently have
Claude Code (the local command-line coding tool) installed on his
machine -- he has only been using the web-based Claude interface, which
has no terminal/package-install/internet access of its own. Plan: Dave
will install Claude Code locally (setup deferred to a future session
when he has more time), which WILL have real internet and pip access
on his own machine. The follow-up session's first concrete step is
installing `tradingview-screener` via Claude Code and running a live
test query against Dave's actual screen criteria (market cap, revenue
growth, 6-month performance, SMA100/SMA200 stack, ADX, average volume)
to confirm field names, bulk-query mechanics, and throttling behavior
against real TradingView data.

**Important clarification: local testing does not change the production
architecture.** Claude Code running locally is purely a one-time
exploration/prototyping tool to confirm the TradingView screener
package works and to learn the correct API calls. Once confirmed, that
logic gets written into the actual pipeline files that already live in
the GitHub repo, and GitHub Actions continues to run the full pipeline
on its existing automated schedule, independent of Dave's laptop. Local
prototyping and the production pipeline remain fully separate; there is
no conflict or migration of the production system to "local."

**NEW: Phase 3 defined -- Trade Execution Automation (sim first, then
live).** Dave explicitly framed the project's phased roadmap during
this session:
- **Phase 1 (current/complete-ish):** signal detection and daily
  summary emails -- the pipeline detects setups and notifies Dave, but
  does not touch a broker or place any trades.
- **Phase 2 (near-term, in progress):** tuning and updating the model
  itself -- universe scope expansion, fundamentals data sourcing
  (the TradingView-screener vs. Yahoo `.info` decision above), and the
  other model-refinement items already tracked in this document
  (Trend Efficiency redesign, trail rule segmentation, smoothness
  metric revisit, tiered conviction sizing revisit, etc.).
- **Phase 3 (future, newly defined this session):** automating actual
  trade placement -- first in a simulator/paper environment, then,
  once proven, in a live-money environment. This is a materially new
  body of work distinct from Phase 2: it requires integrating with a
  broker's order-execution layer (handling order submission, fills,
  error handling around real capital), not just refining detection
  logic. Not to be started until Phase 2 model-tuning work is settled
  and the detection side is fully proven.

**Phase 3 execution-path note: Dave's existing TradingView-to-
TradeStation bridge.** Dave currently places trades manually today via
TradingView's built-in integration with his broker,

TradeStation
: he uses TradingView's long/short drawing tool to
construct an order (entry, quantity, stop, profit targets), adjusts it
as needed, then hits "place trade," which sends the order directly to
TradeStation -- landing in either the live or simulated account
depending on which one he's logged into. Dave reports this manual
workflow already works nicely and wants to keep leveraging it if
possible, rather than necessarily building a from-scratch broker API
integration for Phase 3. Flagged as an option worth investigating when
Phase 3 planning begins: whether TradingView's TradeStation bridge (or
underlying order-submission mechanism) can be driven programmatically,
which could be a substantially simpler execution path than a raw
TradeStation/other-broker API integration built from zero. Not
evaluated or researched yet this session -- purely logged as a lead for
the dedicated Phase 3 planning session.


---

## 2026-09-10 -- `tradingview-screener` Live Prototyping: CONFIRMED WORKING

**Environment used:** Google Colab (colab.research.google.com), chosen
over Claude Code as the local-testing environment because Dave has not
installed Claude Code and is not comfortable with a terminal-based
workflow. Colab requires no installation -- Dave opened a new notebook
in-browser, pasted code cells, and ran them with Shift+Enter. This
worked well and gave real internet/pip access, resolving the sandbox
limitation from the prior two sessions.

**Result: the full 8-criterion momentum screen has been confirmed
achievable in a single live bulk query against real TradingView data.**
This directly closes the gap flagged on 2026-09-09 (market cap, TTM
revenue growth, and primary listing were previously unenforced in
code).

**Package confirmed:** `tradingview-screener` v3.2.1, installed via
`pip install tradingview-screener` in Colab. Real API surface confirmed
as `from tradingview_screener import Query, Column`, with `.select()`,
`.where()`, `.limit()`, and `.get_scanner_data()` returning
`(count, dataframe)`.

**Field-name discovery process:** Two of the eight fields initially
guessed incorrectly (came back as blank `None` columns on the first
live test: 215 matches, but `primary_listing` and
`revenue_yoy_growth_ttm` both empty). Rather than guessing again,
Dave's live Colab session was used to fetch and regex-search
TradingView's own published field-name reference page
(`https://shner-elmo.github.io/TradingView-Screener/fields/stocks.html`)
directly -- this is the correct way to resolve unknown/uncertain field
names going forward, since guessing from the package's general naming
convention is unreliable given 3000+ fields. This surfaced the correct
names:
- **`is_symbol_primary_listing`** (boolean) -- for the primary-listing
  filter.
- **`total_revenue_yoy_growth_ttm`** -- for trailing-twelve-month
  year-over-year revenue growth, matching Dave's screen criterion
  exactly.

**Confirmed working query, mapping all 8 of Dave's live TradingView
screen criteria in one bulk call:**

```python
from tradingview_screener import Query, Column

query = (
    Query()
    .select(
        'name', 'close', 'market_cap_basic', 'volume', 'average_volume_10d_calc',
        'Perf.6M', 'SMA50', 'SMA100', 'SMA200', 'ADX',
        'is_symbol_primary_listing', 'total_revenue_yoy_growth_ttm'
    )
    .where(
        Column('market_cap_basic') > 100_000_000,
        Column('Perf.6M').between(30, 500),
        Column('SMA50') < Column('close'),
        Column('SMA100') > Column('SMA200'),
        Column('average_volume_10d_calc') > 1_000_000,
        Column('is_symbol_primary_listing') == True,
        Column('total_revenue_yoy_growth_ttm') > 0,
    )
    .limit(50)
)

count, df = query.get_scanner_data()
```

**Live test result (2026-09-10, during market hours):** 154 matches
returned (vs. 215 with only the technical filters, and 49 in an
earlier narrower test), all fields populated with real, distinct,
correct-looking values -- `is_symbol_primary_listing` = True across the
board, `total_revenue_yoy_growth_ttm` showing a real spread from ~1%
up to 460%+, ADX/SMA-stack/volume/market-cap all matching the earlier
confirmed-working technical fields. No blank/None columns remained.
Sample tickers returned: MU, AMD, INTC, DELL, MUFG, SFTBY, SNDK, ANET,
C, SAN, CRWD, MRVL, STX, QCOM, SMFG, MFG, SNOW, VLO, NET, BNY, MPC,
EQNR, PSX, ING, ABNB, ELV, ASX, LITE, and others.

**One caveat noted, not yet resolved:** `.limit(50)` was used during
testing; the real pipeline will need to either raise the limit or
paginate to make sure ALL qualifying tickers are captured on a given
day, not just the first 50 by whatever default sort order the API
uses. Needs explicit handling before this goes into production code.

**Conclusion / recommended path forward per the 2026-09-09 sequencing
plan:** Step 2 (prototype live) is now DONE and successful. Recommend
proceeding to steps 3 and 4 in a dedicated follow-up session: relocate
the SMA-stack/ADX checks out of
`touch_scan_and_momentum_screen.py`'s reactive re-derivation-from-raw-
price-data logic and into this upfront TradingView query instead (since
TradingView's own calculated values are now confirmed retrievable
directly), source market cap/revenue growth/primary listing from this
same query rather than leaving them unenforced, and retire the
Wikipedia S&P 400/600 scrape in favor of whatever universe-scope
decision is made (still open: S&P 1500 vs. broader, per the
2026-09-09 sequencing item #1, not yet decided). The open-positions-
union requirement (data pull must cover screen-passing tickers UNION
open positions, never gate on screen result alone) remains a must-have
in whatever implementation replaces the current logic. Throttling
behavior on repeated/larger daily pulls was not yet stress-tested this
session (only a couple of manual one-off queries were run) -- worth
deliberately testing before relying on this in the daily automated
pipeline.


---

## 2026-09-10 (continued) -- Universe Scope: DECIDED

**Decision (Dave, confirmed):** The ticker universe will no longer be
defined by index membership (not S&P 400/600, not S&P 1500, not
NASDAQ-only, etc.) at all. Instead, **the TradingView screen itself
IS the universe** -- whatever set of tickers passes all 8 live screen
criteria (market cap, TTM revenue growth, 6-month performance,
primary listing, SMA50/SMA100/SMA200 stack, ADX, average volume) on a
given day becomes that day's candidate list, full stop. This
resolves/closes the "S&P 1500 vs. broader" open question from the
2026-09-09 sequencing plan (step 1) -- no separate index-membership
filter layer is needed at all once the TradingView-screener migration
happens. This also better matches how Dave has always traded manually
-- his live TradingView screen was never index-restricted either.

**Still firmly required regardless of this decision:** the
open-positions-union rule stands unchanged and is not superseded by
this -- daily data collection must still be the UNION of
(that day's screen-passing tickers) and (all currently open
positions), never gated on the screen result alone. An open position
can legitimately fall out of the screen on a later day (momentum
fades, a filter threshold is no longer met) without the trade itself
being closed; losing price data for it would silently break
`check_open_positions_for_exits()`. This remains a standalone,
independent fix that should happen even before/separately from the
full TradingView migration -- see next section.



---

## 2026-09-11 -- Wide-Universe Historical Backtesting Sample: BUILT, plus OPEN ITEM found and fixed

**Context:** Dave flagged a real structural problem with forward-testing
the new TradingView-screener-as-universe pipeline live: at an observed
rate of roughly 49 tickers/day passing the screen but only a handful
actually producing scorecard-eligible touches, getting a usable sample
size for Phase 2 tuning (trail rules, conviction sizing, 7% cap, etc.)
via pure forward-testing would take 6+ months. Falling back to S&P
400/600 for backtesting purposes was rejected, since index membership
itself silently encodes assumptions (min market cap, profitability,
etc.) that the wider TradingView screen deliberately does not require --
using it would reintroduce the very bias being removed.

**Decision:** Build a one-time (or occasionally-rerun) historical
research sample instead, covering 5 of the 8 live screen criteria that
can be computed accurately from historical daily OHLCV alone (SMA50 <
Close, SMA100 > SMA200, ADX(50) 20-40, avg 10-day volume > 1,000,000,
6-month performance 30-500%), plus a STATIC approximation for market cap
(today's market cap > $100M used as a rough proxy across all historical
dates, per Dave: "something that has a $5M market cap is really not
applicable to us at all... it doesn't have to be exactly right, it's
just kind of the idea that we don't want to be taking trades on things
that are super small and illiquid"). Primary-listing and TTM revenue
growth YoY are NOT included historically (too unreliable/unavailable
going back years across a wide universe). Tickers with incomplete
3-year price history (recent IPOs, etc.) are excluded entirely from the
sample, per Dave's explicit decision, to keep the sample clean rather
than mixing partial-history tickers in.

**This work is intentionally isolated from the live TradingView-screener
branch** -- it's a standalone research script, not part of the
production daily pipeline, and lives on its own separate branch
(`historical-universe-backtest` or similar), branched off the main
working branch, not off `tradingview-screener-integration`. No GitHub
Actions involvement -- run on demand in Colab.

**Pipeline built and run this session (all in /mnt/user-data/outputs/):**
1. `build_wide_universe.py` -- live TradingView query, broad universe
   (market cap > $100M, avg 10-day volume > 1M, primary listing only,
   NO trend/momentum filters). Run live in Colab: **1,593 tickers**
   matched (roughly 30x wider than the S&P 400/600-constrained
   approach). Saved as `wide_universe_snapshot.csv`.
2. `pull_historical_universe_prices.py` -- bulk yfinance download, 3
   years of daily OHLCV for all 1,593 tickers, chunked (60 tickers/
   batch) to avoid rate limiting. Run live in Colab: **1,591 of 1,593
   tickers succeeded** (2 failures: `PCG/PX` and `ORCL/PD`, both
   preferred-stock tickers with slash-formatted symbols yfinance
   doesn't recognize -- acceptable, not worth chasing). Saved as
   `historical_prices_3yr.csv`, ~1.13M rows.
3. `build_historical_sample.py` -- computes rolling SMA50/100/200,
   Wilder ADX(50), 10-day avg volume, and 6-month performance from the
   price history, drops the 138 tickers with <700 rows of history
   (incomplete 3-year window), then checks the 5 historical criteria +
   static market cap filter at monthly snapshot dates (first trading
   day of each month). Result: **1,453 tickers retained**, checked
   across **37 monthly snapshots** (2023-09 through 2026-09), producing
   **2,024 ticker-month candidate hits**. Monthly candidate counts
   range from 8 (May 2025, quiet month) to 135 (October 2025, strong
   month), averaging roughly 75-80/month -- confirms Dave's instinct
   that the S&P 400/600 constraint was severely starving the system
   (was producing something like ~4 candidates/month by comparison).
   Saved as `historical_candidates_sample.csv`.

**OPEN ITEM FOUND AND FIXED this session:** while reconnecting this new
candidate sample to the existing touch-detection/scoring pipeline,
re-discovered and this time actually FIXED a previously-flagged-but-left
bug in `touch_scan_and_momentum_screen.py`: the script computed
`price_above_50ma = row['Close'] > row['SMA50']` but never included it
in the actual filter condition deciding which days pass the screen --
dead code, silently ignored. Since "price > 50-day MA" is one of the 8
documented screen criteria, this meant the touch-detection script could
have been counting touches/trades on days that would not have actually
qualified in real trading, which would distort backtest statistics
(win rate, average gain/loss, etc.) -- exactly the numbers this whole
historical-sample effort exists to produce reliably. Dave confirmed
this needed to be fixed rather than left for consistency with prior
runs: "if it's having us take trades that wouldn't have happened in
reality, then we probably need to get that corrected." Fixed 2026-09-11
by adding `price_above_50ma` into the filter condition. This means
historical touch counts from any future run of this script will differ
(likely be somewhat lower) than prior runs, including the original
456-event dataset this project was originally tuned against --
**this is a live open item**: existing scorecard/sim tuning that was
validated against the old (buggy) touch dataset may need to be
re-validated once the full pipeline is rerun against the corrected
touch logic. Flagging this explicitly rather than letting it pass
quietly, since it could affect the credibility of prior tuning results.

**Next steps (not yet done):**
- Regenerate per-ticker OHLCV files from `historical_prices_3yr.csv`
  (currently one combined file; touch-scan script expects one CSV per
  ticker) so the FIXED `touch_scan_and_momentum_screen.py` can be rerun
  against the full wide-universe 3-year dataset.
- Rerun the (now-fixed) touch scan against the new wide-universe price
  data to regenerate a much larger touch-event dataset than the
  original 456 events.
- Feed the resulting touch events through `scorecard.py` and `sim.py`
  to get real simulated trade outcomes at scale -- this is the actual
  prerequisite for revisiting the tabled Phase 2 items (Trend
  Efficiency redesign, trail rule segmentation, smoothness metric,
  conviction sizing, 7% cap confirmation, VSAT/HL/ICHR outlier subset).
- Decide whether/how to re-validate prior tuning conclusions that were
  based on the old (buggy) 456-event dataset, now that the underlying
  touch logic has changed.


---

## 2026-09-11 (continued) -- Wide-Universe Backtest Pipeline: CONNECTOR BUILT, FIRST FULL RUN, and ADR/VOLATILITY FINDING

**Context:** Continuing directly from the wide-universe historical
candidate/touch-event work above. This session built the actual
connector wiring `Historical_Touches_WideUniverse_v1.csv` (2,310 touch
events, 567 tickers, produced by the FIXED touch-scan script) into the
existing `scorecard.py` scoring engine and `sim.py` trade simulator, ran
it end to end for the first time at this scale, and used the result to
investigate a live open question: does entry-time volatility (ADR10 percent)
predict trade outcome, and if so, how does it relate to the existing
attribute scores?

**Data format fix required:** `sim.py`'s `load_ticker()` expects
comma-formatted whole-integer volume strings (its parser does
`str -> int`). The wide-universe per-ticker CSVs (produced by
`split_historical_prices.py` from `historical_prices_3yr.csv`, sourced
via yfinance) had decimal-formatted volume (e.g. "20957900.0"), which
crashed that parser. Fixed in `split_historical_prices.py` by rounding
and casting volume to integer at split time, rather than touching
`sim.py` itself -- keeps the core simulator file untouched. All 1,591
per-ticker files regenerated with this fix.

**Connector script:** `run_backtest_scoring.py` (and a variant,
`run_backtest_scoring_v2.py`, which additionally captures each
individual attribute's 0-5 score, not just the total, for the
attribute-level analysis below). For each touch event: loads the
ticker's OHLCV via `scorecard.load_ohlcv()` and scores it with
`scorecard.score_trade()` (earnings data unavailable for this
wide-universe run -- passed as `None`/`None`, which `score_earnings_proximity()`
already handles gracefully, assuming ample runway and scoring 5/5,
flagged as a known limitation, not a blocker); separately loads via
`sim.load_ticker()` (which adds the MA50/ADR10 columns `sim.py` needs)
and simulates the trade with `sim.simulate_trail()` using the
recommended settings (`rule='20ma'`, `take_partial=False`,
`cap_pct=0.07`, i.e. the CURRENT 7 percent cap, confirmed still in use
this session, not the older 5 percent default).

**First full run result: all 2,310 trades scored and simulated
successfully, 0 skipped.** Headline numbers across the full sample:
mean realized outcome +0.58R per trade, median -1.0R, win rate ~40.7
percent (consistent with a trend-following system where losers are
capped at -1R but winners run well past +1R). Exit reason breakdown:
1,065 exited via the 20-day MA trail, 1,062 hit the initial stop on a
later day, 152 hit the initial stop on the ENTRY DAY itself (~6.6
percent of all trades), 27 still open at end of data, 4 an edge-case
"stop" exit reason.

**Entry-day stop investigation:** Spot-checked several of the 152
entry-day-stop trades directly against raw OHLC bars -- confirmed these
are genuine, mechanically correct outcomes, not a bug: the day's low
price actually traded through the calculated initial stop (entry price
minus risk-per-share) intraday, meaning a resting limit order at the
50-day MA would have filled and then been stopped out in the very same
session on a sufficiently volsilatile/wide-range day. This is an
inherent, real risk of MA-pullback limit entries on volatile names, not
a data or simulator defect -- and is a case the entry-day-stop-check fix
from 2026-09-03 (see `sim.py` changelog) is correctly catching, which an
older buggy version would have silently missed.

**ADR/volatility-vs-outcome finding (the main result of this session):**
Dave's framing question -- do the high-volatility names that get stopped
out more often actually make up for it when they win, or should they be
screened out -- was tested directly by bucketing all 2,310 trades by
ADR10 percent at entry:
  - Low ADR (0-3 percent): 357 trades, 39.2 percent win rate, +0.42R mean
  - Moderate ADR (3-5 percent): 680 trades, 41.8 percent win rate, +0.47R mean
  - **Elevated ADR (5-7 percent): 547 trades, 42.8 percent win rate,
    +0.86R mean -- clearly the best-performing bucket**
  - High ADR (7 percent+): 726 trades, 39.0 percent win rate, +0.56R
    mean -- still net positive, and with the highest entry-day-stop rate
    (~9.8 percent, vs ~5-6 percent for the calmer buckets), but not the
    standout performer

**Conclusion (Dave, confirmed):** Neither of the two hypothesized
answers is quite right on its own. High-ADR names should NOT be
screened out (they remain net profitable), but they are also not simply
"the cost of doing business" bought back by equal upside -- the real
edge concentrates specifically in the 5-7 percent ADR band, which
outperforms both calmer and more extreme volatility names. **Decision:
do not narrow the screen** (Dave: "I don't want to cut off the screen...
I don't think we should be in the business of cutting out profitable
setups") -- instead this becomes an input to CONVICTION-BASED POSITION
SIZING (larger size for higher-conviction setups), not a pass/fail
filter. This directly extends the existing (currently shelved/MVP-flat)
conviction sizing work in `Conviction_Sizing_Model_v2.md`, adding ADR
band as a second, independent sizing signal alongside the existing
2-attribute score.

**Score-tier x ADR-band interaction (important nuance, not a clean
stacking effect):** Cutting the sample by both total scorecard score
(quartiles) AND the 5-7 percent ADR band at once showed the two signals
do NOT simply combine additively. In the bottom two score quartiles,
being in the 5-7 percent ADR band produces a large, consistent
improvement (e.g. Q2: +1.31R in-band vs +0.33R outside-band -- the best
cell in the whole table). But in the TOP score quartile, the pattern
inverts -- trades outside the 5-7 percent band actually outperform
those inside it (+0.61R vs +0.43R). Working theory discussed with Dave:
his scorecard's "smoothness"-flavored attributes (Trend Efficiency, MA
Respect, ATR Variability) might be structurally penalizing volatile
names, meaning a high-ADR name that STILL scores well overall is a
rarer, different kind of setup (strong on other attributes despite the
smoothness penalty) than a merely-decent-scoring volatile name.

**Attribute-level test of that theory (this ran, and partially
confirmed / partially refined it):** Compared every individual
attribute's mean score in-band vs outside-band -- found NO meaningful
difference for most attributes, including, notably, MA Respect itself
(3.01 vs 3.02) and most other attributes. The broad "smoothness
attributes generally penalize volatility" theory does NOT hold up as
originally framed. However, a correlation pass (ADR10 percent vs each
attribute score, full sample) found two clear exceptions:
  - **Relative Strength: -0.48 correlation with ADR** (the strongest
    relationship found)
  - **Trend Efficiency: -0.44 correlation with ADR**
  - All other attributes: weak correlations, mostly under 0.19 in
    magnitude, several near zero (MA Respect: -0.06, Trend Character:
    -0.01, ADX Trend Strength: +0.01)

**Refined finding, splitting each of those two attributes by
high/low (median split) and ADR band:**
  - Relative Strength: ADR band helps regardless of whether Relative
    Strength is high or low (+0.35 to +0.38R improvement in-band either
    way) -- behaves like a genuinely independent, additive signal.
  - Trend Efficiency: behaves very differently. Trades with a LOW Trend
    Efficiency score that are ALSO in the 5-7 percent ADR band actually
    performed BEST of all four cells tested (+0.95R), outperforming even
    the high-Trend-Efficiency/in-band cell (+0.82R). This suggests ADR
    band membership may be capturing much of the same real signal Trend
    Efficiency is trying to measure, and may be compensating for (or
    partly duplicating) it, rather than the two being independent.

**Working conclusion, not yet finalized:** Relative Strength and ADR
band look like two separate, stacking edges -- worth combining them
directly in a future conviction-sizing scheme. Trend Efficiency and ADR
band look like overlapping signals -- this is a new, concrete data
point for the already-open "Trend Efficiency metric redesign" Phase 2
item, and should be considered directly alongside that redesign rather
than as a separate question.

**Files produced this session (all in /mnt/user-data/outputs/):**
- `split_historical_prices.py` -- splits the combined 3-year price file
  into 1,591 per-ticker CSVs matching `sim.py`'s expected format
  (fixed for integer volume, see above).
- `run_wide_universe_touch_scan.py` -- one-off runner that points the
  FIXED `touch_scan_and_momentum_screen.py` at the new wide-universe
  per-ticker data without modifying that file's own hardcoded defaults.
- `Historical_Touches_WideUniverse_v1.csv` -- 2,310 touch events, 567
  tickers, 2024-06-27 through 2026-09-10 (output of the fixed touch scan
  against the wide universe).
- `run_backtest_scoring.py` / `run_backtest_scoring_v2.py` -- the
  scorecard/sim connector scripts described above.
- `Historical_Backtest_Scored_Trades_v1.csv` -- 2,310 scored + simulated
  trades, total score only.
- `Historical_Backtest_Scored_Trades_v2_with_attributes.csv` -- same
  2,310 trades, with each of the 9 individual attribute scores broken
  out as separate columns (used for the ADR/attribute analysis above).

**Next steps (not yet done):**
- Design and test an actual conviction-sizing scheme that incorporates
  ADR band as a second signal alongside the existing scorecard total (or
  Relative Strength specifically, given it stacks cleanly) -- current
  conviction sizing model (`Conviction_Sizing_Model_v2.md`) predates this
  finding entirely and only considers the 2-attribute score.
- Revisit the Trend Efficiency redesign (already an open Phase 2 item)
  with this session's finding in hand -- specifically, check whether
  Trend Efficiency's formula is implicitly penalizing exactly the kind
  of volatility that the 5-7 percent ADR band captures as a positive.
- The score-tier x ADR-band interaction (clean additive help in low/mid
  score tiers, inversion in the top tier) is not yet explained and
  should be dug into further before finalizing any sizing scheme.
- Still outstanding from the prior entry: re-validate old tuning
  conclusions (cap percent, trail rule choice) that were based on the
  original 456-event dataset now that a much larger, bug-fixed sample
  exists -- this session's runs already used the current recommended
  settings (7 percent cap, 20-day MA trail, no partial) but did not
  yet re-test whether those specific choices still win on this larger
  sample versus the alternatives originally tested against 253/456
  events.


---

## 2026-09-11 (continued further) -- Trail Rule Resweep on Wide-Universe Sample: 3 New Rules Tested, ATR-Multiple Outlier Check

**Context:** Dave questioned whether the 20-day MA trail (the standing
recommendation from the 2026-09-03 resweep, chosen because it beat all
4 alternatives on the 253-event sample at every cap level tested) is
really still correct now that a much larger, wider-universe sample
exists. Two specific instincts prompted this: (1) faster-moving stocks
might need a tighter trail than slower ones, or vice versa, and (2) a
trade might benefit from starting with a tight trail early on, then
loosening once it has proven itself. Separately, Dave also asked about
testing an ATR-multiple trailing stop (a technique referenced by SMB
Capital-style trading educators), initially proposing a tight 1x to
1.25x multiplier.

**Step 1 -- re-ran the 5 EXISTING trail rules on the full 2,310-event
wide-universe sample** (`rerun_all_trail_rules.py`), all at the current
recommended cap_pct=7 percent, take_partial=False, for a clean baseline
before testing anything new:

  - 10ma:           mean +0.611R, 41.9% win rate
  - 20ma (current recommendation): mean +0.584R, 40.7% win rate
  - hybrid_tight:   mean +0.603R, 42.9% win rate
  - adr_adaptive:   mean +0.601R, 41.6% win rate
  - swing_low:      mean +0.635R, 30.7% win rate (highest average, but
    much lower win rate -- fewer, bigger wins)

**Notable: 20-day MA, the clear historical winner on the 253-event
sample, is now the WEAKEST of the 5 original rules on this 5x-larger,
much wider (more small/mid-cap) sample.** This on its own justified
Dave's instinct to question it.

**Step 2 -- tested whether the 10ma vs 20ma gap is explained by market
cap / "speed."** Dave's own hypothesis, offered explicitly as "just an
idea, we don't need to prove it": since the wide universe now includes
many more small/mid-caps than the old S&P 400/600-constrained approach,
and smaller caps tend to move faster, maybe that's why 20ma no longer
wins outright. Bucketed all 2,310 trades by market cap (via
`wide_universe_snapshot.csv`) and compared all 5 rules within each
bucket:

  - Small cap (under $2B, n=634, mean ADR10 ~8.2%): all 5 rules
    clustered tightly (+0.46R to +0.52R) -- no rule dominates.
  - Mid cap ($2B-$10B, n=825, mean ADR10 ~6.0%): swing_low notably
    best (+1.06R); other 4 rules clustered +0.77R to +0.86R.
  - Large cap ($10B+, n=851, mean ADR10 ~4.6%, the calmest group):
    **20ma is clearly the WORST rule here (+0.37R)**, while 10ma and
    adr_adaptive are the best (+0.54R each).

**Refined conclusion (confirmed with Dave):** the mechanism is real but
inverted from Dave's first guess. It is not that fast/volatile small
caps need a tighter trail -- those names don't show a strong rule
preference at all. It's that CALM, low-volatility, large-cap names are
specifically hurt by the slower 20-day MA, because on a low-volatility
stock the 20-day average barely moves day to day, so the trail lags far
behind price and gives back more profit before finally triggering. This
is a genuinely actionable, mechanistically-explained finding, not
speculation.

**Step 3 -- built and tested 3 new trail rules Dave proposed**, in a
new standalone module `experimental_trail_rules.py` (does not modify
`sim.py`; mirrors its exact entry-day-stop-check and grace-period-arming
control flow via a new `simulate_trail_extended()` so results are
directly comparable):

  1. **speed_adaptive** -- per the refined finding above, LOWER ADR10 at
     entry (calmer stock) -> tighter trail (10ma); HIGHER ADR10 ->
     looser trail (20ma). Threshold checked once at entry only (5%
     threshold used), matching how the existing `adr_adaptive` rule
     already works.
  2. **graduated_tighten** -- Dave's "start tight, loosen once proven"
     idea: uses 10ma as the trail until the trade has closed at/above
     1.5R gain (the same grace_R threshold `sim.py` already uses to arm
     the trail at all), then switches to 20ma after that point.
  3. **atr_multiple** (4 variants: 1.0x, 1.5x, 2.0x, 3.0x) -- stop line =
     highest CLOSE since entry, minus (multiplier x ATR14, Wilder's
     14-day Average True Range -- the standard "Chandelier Exit" style
     construction). Web research pulled in before building this: Dave's
     originally-proposed 1x-1.25x multiplier is notably TIGHTER than
     common published practice, which typically uses 2x-3.5x for swing
     trading (3x is the most commonly cited default) -- flagged to Dave
     as a reference point, not a reason to avoid testing his own number.

**First-pass full-sample result (before outlier check) -- ALL 11 rules
(5 existing + 6 new/variants), full 2,310-event sample, sorted best to
worst:**

  - atr_3.0x:          +0.754R, 26.5% win rate (NEW) -- best average
  - atr_1.5x:           +0.649R, 36.3% win rate (NEW)
  - swing_low:          +0.635R, 30.7% win rate
  - atr_2.0x:           +0.631R, 32.0% win rate (NEW)
  - atr_1.0x:           +0.625R, 42.0% win rate (NEW)
  - speed_adaptive:     +0.611R, 41.2% win rate (NEW)
  - 10ma:               +0.611R, 41.9% win rate
  - hybrid_tight:       +0.603R, 42.9% win rate
  - adr_adaptive:       +0.601R, 41.6% win rate
  - 20ma:               +0.584R, 40.7% win rate
  - graduated_tighten:  +0.584R, 40.7% win rate (NEW) -- statistically
    identical to plain 20ma; as built, does not appear to add value
    over the simpler rule

**Outlier check on the apparent atr_3.0x winner (Dave specifically
requested this, drawing on the same lesson from the earlier
cap-percent-resweep outlier caveat):** confirmed atr_3.0x's headline
result is substantially outlier-driven. The top 10 of 2,310 trades
contribute 17.5 percent of its total R; the top 20 contribute 31.9
percent, concentrated in just 5 tickers (COGT, VNET, NB, SSRM, RCAT --
VNET alone appears 8 times in the top 15, from the touch-scan
re-triggering repeatedly during one sustained run). **Re-computing all
rules with these 5 tickers excluded entirely (2,259 remaining trades)
collapses atr_3.0x to a middling +0.488R, 25.7% win rate -- no longer
distinguishable from 10ma/20ma/hybrid_tight/swing_low, which all cluster
+0.48R to +0.50R on this cleaned subset.**

**On the same cleaned (outlier-excluded) subset, the TIGHTER ATR
multiples actually come out AHEAD, and hold up as broad, not
outlier-driven, results:**
  - atr_1.5x: +0.535R (best on the cleaned subset)
  - atr_1.0x: +0.528R
  - atr_2.0x: +0.506R
  - swing_low: +0.501R
  - 10ma: +0.497R
  - hybrid_tight: +0.488R
  - atr_3.0x: +0.488R (fell from 1st to tied-last once outliers removed)
  - 20ma: +0.479R

**Conclusion (this session, not yet final):** the honest finding
inverts the naive first read of the data. The wide, "textbook standard"
3x ATR multiplier is fragile and driven by a handful of huge individual
runners, not a broad edge. The tighter 1x-1.5x ATR multiples Dave
originally proposed -- closer to what he recalled from SMB-style
trading discussions -- hold up BETTER as a broad, reliable improvement
over the current 20ma default once outliers are excluded. This is a
genuinely promising, evidence-backed candidate to replace the 20ma
default, though it has not yet been cross-checked against varying the
cap_pct simultaneously (all of this session's trail-rule testing used
the fixed 7 percent cap; the two were not re-swept together).

**Files produced this session (all in /mnt/user-data/outputs/):**
- `rerun_all_trail_rules.py` -- baseline resweep of the 5 existing rules
  on the full wide-universe sample.
- `Trail_Rule_Resweep_WideUniverse_v1.csv` -- output of the above.
- `experimental_trail_rules.py` -- standalone module implementing the 3
  new rules (does not modify `sim.py`).
- `test_new_trail_rules.py` -- runner testing all 11 rules together.
- `Trail_Rule_Extended_Test_v1.csv` -- output of the above; used for the
  outlier check.

**Next steps (not yet done):**
- Re-sweep cap_pct (3/5/7 percent, or a finer grid) together with the
  best-performing trail rules found here (1.0x-1.5x ATR, 10ma,
  hybrid_tight), since the 2026-09-03 cap_pct conclusion was reached
  using 20ma as the trail and may not generalize now that 20ma itself
  no longer looks optimal.
- Decide whether `graduated_tighten` deserves a second attempt with a
  different design (e.g. a different loosening trigger than the 1.5R
  grace threshold) before concluding it adds no value, since the first
  version tested was a fairly direct, simple implementation of Dave's
  idea.
- If 1.0x-1.5x ATR is adopted as a new candidate default, decide whether
  to formally add it into `sim.py` itself (as a 6th named rule) rather
  than keeping it in the standalone experimental module.
- This trail-rule work and the earlier-this-session ADR-band conviction
  sizing finding both touch position/trade management -- worth
  eventually reconciling into one coherent Phase 2 tuning pass rather
  than two separate threads.


---

## 2026-09-11 (continued further) -- Cap Percent x Trail Rule Cross-Sweep: NEW RECOMMENDATION CONFIRMED

**Context:** Directly addressing Dave's repeated concern that the
2026-09-03 cap_pct recommendation (7 percent) might not be trustworthy,
since it was originally validated using ONLY the 20-day MA trail rule --
and this session already showed 20ma is no longer the best-performing
trail rule on the larger wide-universe sample. Ran a full cross-sweep:
3 cap percentages (3 percent, 5 percent, 7 percent) x 6 trail rules
(10ma, 20ma, hybrid_tight, swing_low, atr_1.0x, atr_1.5x -- the original
plus the strongest candidates from the outlier-cleaned trail-rule test
earlier this session) = 18 combinations, each run across all 2,310
wide-universe events. Script: `cap_trail_cross_sweep.py`. Output:
`CapPct_TrailRule_CrossSweep_v1.csv`. Results reported both on the full
sample and with the 5 known outlier tickers (COGT, VNET, NB, SSRM, RCAT
-- see the atr_3.0x outlier finding earlier this session) excluded, for
an honest, non-outlier-driven read.

**Result -- clean and consistent across both views:**

Outlier-excluded (the trustworthy view), top of the table:
  - cap=7%, atr_1.5x: +0.535R, 35.7% win rate -- best average return
  - cap=7%, atr_1.0x: +0.528R, 41.5% win rate -- best win rate among
    the top performers
  - cap=3%, atr_1.5x: +0.525R, 25.5% win rate
  - cap=7%, swing_low: +0.501R, 30.1% win rate
  - cap=7%, 10ma: +0.497R, 41.3% win rate
  - ... 20ma (the OLD default) trails the pack at every cap level tested
    on this view: best case (5 percent cap) only +0.485R.

Full sample (outliers included) shows the same top combination winning
again (cap=7%, atr_1.5x: +0.677R at 3% cap was marginally higher here,
but 7%+atr_1.5x is a close second and the more broadly consistent
performer across both views) -- importantly, 7 percent cap is the best
cap level for nearly every rule on BOTH tables, so unlike the trail-rule
question, **the cap_pct piece of the 2026-09-03 recommendation holds up
and is NOT overturned by this larger sample.**

**FINAL DECISION (Dave, confirmed):** Keep the 7 percent cap (unchanged
from 2026-09-03). Replace the trail rule: retire the 20-day MA default
in favor of an ATR-based trailing stop, specifically **1.0x ATR(14)**
(stop = highest close since entry, minus 1.0 x 14-day Wilder ATR).
Chosen over the marginally-higher-average 1.5x ATR variant specifically
for its notably better win rate (41.5 percent vs 35.7 percent) --
Dave's own reasoning: a steadier, more frequently-winning rule is
preferable given his stated preference for disciplined, rules-based
frameworks that are simple to run day to day, and the average-return gap
between 1.0x and 1.5x was judged too small to be worth the extra
variance.

**NEW RECOMMENDED CONFIGURATION (supersedes the 2026-09-03 recommendation):**
  - cap_pct = 7 percent (UNCHANGED)
  - trail rule = 1.0x ATR(14), highest-close-since-entry basis (CHANGED
    from 20-day MA)
  - take_partial = False (UNCHANGED, not re-tested this session but no
    reason found to revisit it)

**Not yet done:**
- The 1.0x ATR rule currently only exists in the standalone
  `experimental_trail_rules.py` module, not in `sim.py` itself. Formally
  promoting it into `sim.py` as a 6th named rule (with its own ATR14
  column added to `load_ticker()`) is the next mechanical step before
  this can be used as the actual production default anywhere.
- take_partial was not re-swept against the new ATR-based rule -- the
  2026-09-03 finding that no-partial beats with-partial was established
  under 20ma and has not been re-confirmed under atr_1.0x specifically.
- This new trail rule recommendation should be reconciled with the
  earlier-this-session ADR-band conviction-sizing finding and the
  Trend-Efficiency-vs-ADR overlap finding -- all three are now open,
  related Phase 2 threads from this same session's backtesting work.


---

## 2026-09-12 -- New Open Item: Trade Duration / Holding-Period Analysis (not yet run)

**Context:** Raised by Dave as a next testing priority, separate from the
R-multiple and win-rate work done so far. All of this session's and prior
sessions' backtest analysis (trail-rule sweeps, cap-percent sweeps,
ADR-band conviction sizing, score-tier cross-cuts) has focused on
per-trade R-multiple outcomes and win rates, but has NOT yet looked at
how long trades typically stay open -- i.e. capital efficiency /
holding-period duration.

**Proposed next analysis (not yet built):**
- Compute holding period (calendar days and/or trading days) from entry
  to exit for every trade in the wide-universe scored/simulated dataset
  (Historical_Backtest_Scored_Trades_v1.csv or v2, 2,310 trades).
- Report average and median holding time overall, and worth breaking
  down by exit_reason (does an atr_1.0x_trail exit take longer to
  trigger than an initial_stop exit, for instance?), and potentially by
  the same ADR-band and score-tier cuts already used elsewhere in this
  research, to see whether faster- or slower-resolving trades cluster
  with any of the same variables already investigated.
- Natural output: mean/median days held, and possibly a distribution
  (e.g. histogram or simple bucket counts) -- useful both for its own
  sake (capital efficiency, how many concurrent positions realistically
  needed) and as a possible new input to conviction sizing alongside the
  ADR-band finding already on file.

**Status:** Not yet started. Logged here as an explicit open item ahead
of the GitHub branch/upload work this session, to be picked up in an
upcoming session.
