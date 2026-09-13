# Conviction-Based Position Sizing - v2 (Rebuilt)

*Rebuilt 2026-09-03, replacing the v1 model, which was built on stale
inputs: the old 3-attribute score (including Trend Efficiency, since
demoted), the old unfiltered 456-event set, and the old buggy outcome
column. This version uses the current 2-attribute score (MA Respect v5 +
Relative Strength v2) and the final, corrected outcome column
(Outcome_Final_v1_P_R, 7 percent cap / 20-day MA trail / no-partial),
across the 253-event overhead-resistance-filtered universe. All results
expressed in R multiples only - no dollar or account-size assumptions.*

---

## MVP decision: flat position sizing, single skip threshold

For the minimum viable pipeline, position sizing is FLAT - every
tradeable signal risks the same single unit of R, with one skip
threshold: signals scoring below 2.5 (on the 0-5, 2-attribute average of
MA Respect + Relative Strength) are not traded at all.

**Rationale (Dave, 2026-09-03):** Position sizing sits at the very end of
the pipeline, downstream of every other component - the attribute
formulas, the touch scan, the trail rule, and the cap percent - all of
which are still evolving and likely to be re-tuned in Phase 2. Building
tiered sizing complexity on top of numbers that are expected to shift
means re-doing the sizing model repeatedly for a marginal gain, and adds
real operational complexity (multiple position sizes to track and manage
day to day) for a benefit that is real but modest (see below). Better to
keep this simple until the upstream pipeline stabilizes.

---

## What the tiered exploration showed (for the record, not adopted)

Grouping the 253 events by the 2-attribute score into natural tiers found
in the data:

- Skip (below 2.5): mean -0.258R, 53 events, 28.3 percent win rate - the
  only tier with a negative average.
- Tier 1 (2.5 to 2.999): mean 0.070R, 33 events, 42.4 percent win rate.
- Tier 2 (3.0 to 3.499): mean 0.384R, 62 events, 43.5 percent win rate.
- Tier 3 (3.5 to 3.999): mean 0.725R, 50 events, 42.0 percent win rate.
- Tier 4 (4.0 and above): mean 1.037R, 55 events, 60.0 percent win rate -
  clearly the best tier, both on average return and win rate.

The relationship is clean and monotonic - each tier up is a real step
better than the one below it, which is a good sign for the underlying
scoring system's validity. However, the standard deviation of R within
each tier is large and broadly similar across tiers (roughly 2 to 2.5R
throughout) - meaning better tiers have a better AVERAGE outcome, but not
meaningfully less variance. A tiered sizing scheme does not reduce the
risk of large individual losses; it only shifts capital allocation toward
buckets with better long-run averages.

A test comparison (flat 1x sizing on Tiers 1-4 vs. a modest tiered scheme
of 0.5x / 1.0x / 1.25x / 1.5x across Tiers 1-4) showed the tiered version
generating 0.697R per unit of risk committed, versus 0.597R per unit for
flat sizing - roughly a 17 percent improvement in capital efficiency.
Real, but modest, and achieved with fairly conservative multipliers (nothing
more aggressive was tested).

---

## Phase 2 - revisit item

**Tiered conviction-based sizing** - logged as a Phase 2 exploration, not
a rejected idea. If the upstream pipeline (attribute formulas, trail
rule, cap percent) stabilizes and stops changing significantly, revisit
whether a tiered structure like the one above is worth the added
complexity. Specific things worth re-testing at that point:
- Whether tighter tier boundaries or different multipliers improve the
  efficiency gain beyond the modest ~17 percent seen here.
- Whether the similar within-tier variance across tiers is a permanent
  feature of this setup or an artifact of the current attribute
  formulas/trail rule that might change once those are revisited.
- Whether it's worth combining this with the also-open Trend Efficiency
  and trail-rule-segmentation Phase 2 items, since all three touch the
  same underlying question of whether different trade conditions call
  for meaningfully different treatment.
