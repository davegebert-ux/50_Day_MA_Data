# General Research Findings

Branch: `historical_backtest_research`.

This is the single running document for research findings on the 50-day
MA bounce system -- ideas tested, what the data said, and what was
decided. One document rather than one file per idea, so that findings
stay findable and the next session has one place to look.

WHAT BELONGS HERE: anything tested and NOT promoted to production, or
tested and withdrawn. Including negative results -- especially negative
results, since the cost of re-testing a dead idea is the same as testing
a live one.

WHAT DOES NOT BELONG HERE: rules that made it into production. Those go
into `Architecture_and_Scope_v1.md` on `main`, with their reasoning, at
the same time the code does.

PROMOTION BARS (agreed 2026-09-14). A finding becomes a rule only if:
  1. it is validated inside the FULL staged pipeline, in production gate
     order -- not on an open sample, and not one criterion at a time;
  2. it survives outlier exclusion (top and bottom 1%) AND reporting with
     each group's top-10 contributing tickers removed;
  3. the reasoning is written into the architecture doc at the same time
     the code goes to main.

Findings that fail a bar stay here. They are not deleted -- a recorded
failure is what stops it being re-discovered and re-tested later.

NEW ENTRIES GO AT THE BOTTOM, each with its own dated heading.

---

## INDEX

- Long-Term Trend Efficiency (LTE) as a screen-level filter -- 2026-09-15
  -- NOT PROMOTED, re-test trigger armed.

(Earlier findings from 2026-09-14 -- the withdrawn 5-7% ADR band, the
"unscorable trades outperform" result, and the RS-only fallback -- were
recorded in the architecture doc and in `Conviction_Sizing_Model_v2.md`
before this document existed. They are not repeated here; see those
files. Future findings of that kind belong in this document.)

---

## Long-Term Trend Efficiency (LTE) as a Screen-Level Filter

Recorded 2026-09-15. Status: **NOT PROMOTED** -- validated in-sample,
failed out-of-sample. Retained as a finding and as a diagnostic.
Re-test trigger defined at the end of this section.

## Origin: the judgement gap

The open item this addresses: the 8-criterion momentum screen admits
names Dave would reject on sight (CLSK, CLOV, NFLX). Asked what he was
actually seeing, Dave supplied a CLSK daily chart and described it as
"a very long lateral consolidation... just chopping up and down on a
very long high time frame," and framed the goal positively: a chart
that "moves from bottom left to upper right."

He also noted, correctly, that this is close to what the overhead
resistance check was reaching for. The distinction matters: overhead
resistance looks only UPWARD from the entry price. It cannot see that
the entire chart is going sideways. On the CLSK touches the nearest
unresolved high was over 5R away -- the gate passed them happily, with
miles of apparent room, on a chart going nowhere.

### Why the momentum screen did not catch it

The screen requires 6-month performance between 30% and 500%. CLSK on
its three touch dates:

    2024-07-01   17.92, 6mo ago 13.11   +36.7%
    2024-07-18   17.07, 6mo ago  7.36   +131.9%
    2024-07-26   17.12, 6mo ago  7.30   +134.4%

It passed comfortably -- up 132% on the middle date. But the 7.30
starting point was the bottom of one of the chop's own downswings. The
criterion samples TWO POINTS, then and now, so a bounce off an interior
low is indistinguishable from genuine progress. Nothing in the screen
looks at the path between them.

All three CLSK touches lost exactly -1R. They were caught anyway, by
other gates: two were unscorable (no clean trend start for MA Respect),
one hit the ADR ceiling at 10.29%. So the loss was avoided incidentally,
not by design.

## The measure

Over a trailing 252-trading-day window ending at the touch date:

    LTE = (Close[t] - Close[t-252]) / sum(|daily close-to-close changes|)

Net displacement divided by total distance travelled. A clean trend
approaches 1.0; a chart that ends where it began approaches 0.

Spot check, computed at the end of the data:

    CLSK  0.0199     CLOV  0.0740
    INCY  0.0965     CAT   0.1111
    NFLX -0.1419

Across the 2,310-event wide-universe sample it computes for 2,143
events (93%). The remainder lack 252 days of prior history.

Observed range is narrow: mean 0.115, SD 0.062, min -0.155, max 0.299.

### Relationship to the demoted Trend Efficiency attribute

This is structurally a cousin of the Trend Efficiency attribute demoted
2026-09-03, and that must be stated plainly rather than discovered later.
The difference is TIMEFRAME: the demoted attribute measured the recent
swing; LTE measures a full year of structure. That difference is the
entire basis for revisiting the idea, and it is not by itself a defence.

## In-sample result: strong

Quintiles of LTE among the 1,023 passing trades (outliers excluded,
n=923 with LTE available). Base: 0.635R, 47.0% win.

    Q1 lowest   n=185  0.311R  41.6%   (LTE -0.119 to 0.073)
    Q2          n=184  0.569R  46.7%
    Q3          n=185  0.868R  45.4%
    Q4          n=184  0.466R  48.4%
    Q5 highest  n=185  0.960R  53.0%   (LTE 0.181 to 0.274)

The R column is noisy but the win rate climbs almost monotonically.

**It survived the top-10-ticker concentration test** -- notable, since
three findings died on that test the previous day. Removing each
quintile's own 10 largest R contributors:

    Q1  n=151  -0.314R  30.5%
    Q2  n=149  -0.263R  34.9%
    Q3  n=159   0.101R  38.4%
    Q4  n=164  -0.034R  42.7%
    Q5  n=144   0.287R  44.4%

Win rate is now perfectly monotonic: 30.5 / 34.9 / 38.4 / 42.7 / 44.4.
The bottom quintile is not merely weaker, it is a LOSING group once the
lucky names are removed. That is Dave's eye, quantified.

## As a screen-level gate

Applied BEFORE the other gates (it is a property of the ticker, not of
the individual touch, and barely moves day to day -- so it belongs at
the screen, not as a per-touch check). Threshold sweep, full pipeline
run downstream of it, outliers excluded:

    thr | kept n | kept R | kept excl10 | rejected n | rejected excl10
    0.04 |  833 | 0.644 | 0.312 / 42.7 |  88 | -0.315 / 33.3
    0.05 |  807 | 0.660 | 0.317 / 43.3 | 114 | -0.368 / 29.6
    0.06 |  783 | 0.704 | 0.354 / 44.5 | 138 | -0.464 / 24.8
    0.07 |  757 | 0.722 | 0.375 / 44.8 | 164 | -0.377 / 29.6
    0.08 |  715 | 0.692 | 0.363 / 45.1 | 206 | -0.264 / 31.0
    0.09 |  682 | 0.696 | 0.355 / 44.9 | 239 | -0.190 / 33.7
    0.10 |  642 | 0.671 | 0.343 / 44.1 | 279 | -0.060 / 37.4
    0.12 |  555 | 0.773 | 0.423 / 46.1 | 366 | -0.141 / 35.8

No gate at all: 921 trades, 0.642R, 47.1% win (0.327R excl top-10).

0.06 through 0.09 form a PLATEAU rather than a spike -- the result does
not balance on one lucky number. 0.07 was chosen as the midpoint, where
drift in either direction costs nothing. Cost is roughly a fifth of all
touches.

## Out-of-sample: FAILED

Split at 2025-09-01 (sample spans 2024-09-11 to 2026-09-10):

    IN-SAMPLE (pre Sep-2025), passing n=276, base 0.471R / 41.7%
      keep (LTE >= 0.07)  n=230   0.648R  45.7%
      reject              n= 46  -0.412R  21.7%

    OUT-OF-SAMPLE (Sep-2025 on), passing n=645, base 0.715R / 49.5%
      keep (LTE >= 0.07)  n=527   0.754R  49.7%
      reject              n=118   0.539R  48.3%

In the earlier period the filter is excellent: what it rejects loses
money at a 21.7% win rate. In the recent year what it rejects still
makes +0.539R and wins 48.3% -- nearly as good as what it keeps. The
gate is close to worthless there.

This fails promotion bar (1): validated inside the full staged pipeline
AND holding up outside the window it was found in.

## Why it may still be real: regime

Strategy expectancy by quarter, passing trades, outliers excluded:

    2024Q3  n= 68   0.219R  35.3%
    2024Q4  n= 95   0.827R  52.6%
    2025Q1  n=137   0.100R  35.8%
    2025Q2  n= 14  -0.045R  35.7%
    2025Q3  n= 78   0.797R  47.4%
    2025Q4  n=178   0.970R  56.7%
    2026Q1  n=182   0.757R  47.8%
    2026Q2  n=147   0.726R  50.3%
    2026Q3  n= 94  -0.125R  35.1%

The out-of-sample window is a markedly easier tape: expectancy jumped
from ~0.1-0.2R in early 2025 to ~0.7-1.0R from 2025Q3 through 2026Q2.
The plausible reading is that LTE is a filter which MATTERS IN HARD
MARKETS AND IDLES IN EASY ONES -- when everything works, chop works too.

This is a hypothesis, not a defence of the finding. It is stated here
because it is falsifiable.

## RE-TEST TRIGGER

2026Q3 has turned: -0.125R, 35.1% win, the worst quarter since 2025Q2.
If that weakness persists, re-run the in/out-of-sample split with the
boundary moved forward and ask specifically:

**Does LTE's rejected group return to negative expectancy in the harder
tape?**

If yes, the regime hypothesis is supported and LTE becomes a candidate
for promotion -- possibly as a regime-conditional filter rather than an
always-on gate, which would be a first for this system and should be
scrutinised accordingly. If no, the finding is dead and should be
recorded as such.

## Secondary use, available NOW

Even unpromoted, LTE is a useful DIAGNOSTIC. A low reading explains WHY
a name looks wrong -- it is the thing Dave's eye was doing unaided. It
is worth computing and logging alongside the per-ticker funnel data
(see the daily funnel open item) so that the judgement gap can be
inspected case by case, without the value gating anything.

## Reproduction

Working file: `staged_with_lte.csv` -- `Staged_Pipeline_Results.csv`
with an `lte` column added. Computed from the per-ticker daily CSVs by
searching the entry date, taking the 252 prior closes, and dividing net
change by summed absolute daily changes. No network or external data
required.
