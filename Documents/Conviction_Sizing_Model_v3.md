# Conviction-Based Position Sizing - v3 (Rewritten)

*Rewritten 2026-09-16. v2 (2026-09-03) rested on a 253-event sample and
recommended flat sizing while logging tiered sizing as a promising Phase
2 item worth ~17% in capital efficiency. On the current 1,001-trade
staged sample that recommendation is unchanged but its REASONING has
collapsed: the monotonic score-tier relationship v2 was built on does not
exist any more. This version records why, and what would have to be true
before tiered sizing is worth revisiting.*

---

## Decision: flat sizing. Unchanged, for entirely different reasons.

Every tradeable signal risks the same unit of R. Signals scoring below
2.5 are skipped. `SKIP_SCORE_THRESHOLD = 2.5` stays.

v2 kept flat sizing as a pragmatic simplification -- tiered sizing looked
genuinely better, but the upstream pipeline was still moving and the gain
was modest. **v3 keeps flat sizing because the evidence for tiered sizing
is gone.**

---

## What changed: the tier ladder was a small-sample artifact

v2's tier table, 253 events, clean and monotonic:

| tier | v2 mean R | v2 win |
|---|---|---|
| T1 2.5-2.99 | 0.070 | 42.4% |
| T2 3.0-3.49 | 0.384 | 43.5% |
| T3 3.5-3.99 | 0.725 | 42.0% |
| T4 4.0+ | 1.037 | 60.0% |

The same boundaries on the current 1,001-trade sample:

| tier | n | mean R | win | sd |
|---|---|---|---|---|
| T1 2.5-2.99 | 252 | 0.434 | 42.9% | 2.33 |
| T2 3.0-3.49 | 290 | 0.745 | 47.2% | 2.65 |
| T3 3.5-3.99 | 231 | 0.316 | 43.7% | 1.95 |
| T4 4.0+ | 228 | 0.791 | 50.9% | 2.53 |

**The ladder is now a zigzag.** T2 beats T3 by more than T3 beats T1.
The correlation between score and realised R across the full sample is
**0.022** -- indistinguishable from zero. Four times the data turned a
clean monotonic relationship into noise, which is the ordinary fate of
clean relationships in small samples.

One thing v2 observed that DID survive: within-tier standard deviation is
large (roughly 2 to 2.7R) and broadly similar across tiers. Better tiers
never meant less variance, only better averages -- and now they do not
reliably mean better averages either.

---

## The tiered scheme, re-tested

v2's exact multipliers (0.5x / 1.0x / 1.25x / 1.5x across T1-T4),
re-run on the current sample:

| test | flat | tiered | gain |
|---|---|---|---|
| full sample (n=1,001) | +0.578R | +0.604R | +4.5% |
| top-10 tickers removed (n=926) | +0.285R | +0.322R | +13.0% |
| in-sample, pre Sep-2025 (n=355) | +0.339R | +0.413R | +22.0% |
| out-of-sample, Sep-2025 on (n=646) | +0.710R | +0.715R | **+0.8%** |

The headline gain fell from 17% to 4.5%. More damning: **the gain is
concentrated entirely in the older data**. In-sample it looks worth
having at +22%; out-of-sample it is +0.8%, which is nothing. That is the
signature of a rule fitted to the period it was discovered in.

Note it does survive the top-10-ticker test -- the gain is not one lucky
name. It simply is not a gain that persists forward in time.

---

## Why this matters beyond sizing

The same finding killed candidate selection by score (see
`General_Research_Findings.md`, 2026-09-15). **Score is a good GATE and
a bad DIAL.** Trades below 2.5 really are worse, which is why the
threshold earns its place. But among trades that pass, the score does not
rank them, does not size them, and does not predict them.

Both conclusions rest on the same 0.022 correlation, so they stand or
fall together.

---

## What would reopen this

Tiered sizing is not rejected in principle -- the principle is sound, and
it is what any trader means by conviction. It is blocked on one number.

**Re-test when score-vs-R correlation rises meaningfully above 0.022**,
which requires the scoring system itself to improve: a redesigned Trend
Efficiency, a third attribute that predicts, or a reworked MA Respect or
Relative Strength. When that happens, re-run the four tests above
together. The bar is set in advance, and deliberately: full sample, with
top-10 tickers removed, and split in and out of sample. A result that
only appears in the first two does not count.

**Do not re-test by adjusting the tier boundaries or the multipliers
against the current score.** Searching for a split of a variable that
correlates 0.022 with the outcome will eventually find one, and it will
not hold. This is the same failure the least-correlated ranking rule
demonstrated on 2026-09-15.

---

## A live constraint v2 did not know about

Dollar modelling on 2026-09-15 showed that **at 1% risk and a 10% cost
cap, the risk rule is already dormant** -- the cost cap determines share
count on essentially every trade, and changing the risk basis barely
moved returns (117.4% vs 118.3% CAGR under a margin variant).

The practical consequence for this document: a tiered RISK multiplier
would have little effect at current settings even if the score justified
one, because risk is not what decides position size today. Any future
conviction-sizing work has to act on the cost cap, or change the
relationship between the two caps -- not just scale `RISK_PERCENT_PER_TRADE`
up and down by tier. See the architecture doc, "Portfolio Dollar Returns
- Pointer".
