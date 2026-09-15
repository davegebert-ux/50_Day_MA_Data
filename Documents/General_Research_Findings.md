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


---

## 2026-09-15 — Candidate selection when signals exceed capacity: NULL RESULT

**Status: no predictive ranking key found. A non-predictive rule
(least-correlated-first) was adopted in production as a stopgap. The
underlying question remains OPEN.**

### The question

When there are more qualifying signals than there is capital to take
them, which ones do you take? Before today the pipeline had no answer:
`find_new_signals_for_date()` returns candidates in `sorted(glob(...))`
order, so selection was ALPHABETICAL and capital ran out partway down
the alphabet. Not neutral — it favours the same early-alphabet names
every time.

### How often this actually binds

Measured over the passing-trade sample, 2024-09 to 2026-09:

- Median signals per day: 2. 75th percentile: 3. Only ONE day in the
  entire sample produced more than ten signals.
- But with a ten-position limit, **50.5% of all qualifying signals had
  to be turned away.**
- Uncapped, the median number of concurrently open positions was 18,
  with a peak of 46.

So this is not a "too many signals today" problem, it is a "positions
accumulate and the book stays full" problem. On a typical day the real
question is *there is one slot free and two candidates*. Dave confirmed
this matches what he sees live.

Caveat: the sample screens the full ~1,591-ticker universe, wider than
what would be watched in practice, so the 50.5% figure is an upper
bound on how often it bites. The shape of the problem is unaffected.

### What was tested, and how

Each key was run as an ACTUAL RANKING RULE inside a ten-slot simulation
over the passing-trade sample (n=1001, top/bottom 1% of R excluded),
not merely as a quintile split — and compared against a random-pick
baseline run over 30 seeds. Total R captured:

| rule | total R |
|---|---|
| random baseline, 30 seeds | mean +296, range +259 .. +319 |
| alphabetical (the incumbent) | +298 |
| `total_score_v2`, highest first | +294 |
| LTE, highest first | +272 |
| pullback depth, shallowest first | +273 |
| pullback speed / ADR, slowest first | +318 |
| `overhead_R`, highest first | +312 |

**Every rule fell inside the random baseline's own seed-to-seed range.
None beat a coin flip.**

### Why the quintile evidence was misleading

Two keys looked genuinely promising on quintile analysis and still
failed as ranking rules:

- **Pullback depth** (new measure, built this session: % drawdown from
  the 20-day high to the touch-day low). Q1 — the shallowest pullbacks —
  averaged +1.002R at a 58.6% win rate against a +0.566R / 45.7% base,
  and SURVIVED the top-10-ticker test at +0.266R. It also held across
  the Sep-2025 out-of-sample split (in-sample 45.3% vs 37.4% win;
  out-of-sample 60.3% vs 45.7% at a 10% threshold) — which is more than
  LTE managed. But its rejected group still returned +0.629R out of
  sample, so it does not earn a gate, and ranking on it captured LESS
  total R than random.
- **`overhead_R`** is humped, not monotonic — the middle quintiles rank
  best and both extremes are poor. That is the signature of a gate,
  which is how it is already used, not a ranking key.

The lesson worth keeping: **a quintile edge does not survive contact
with the actual constraint.** The constraint never asks "is this a good
trade", it asks "is this better than the other candidate competing for
this specific slot today". Those are different questions and the
evidence for one is not evidence for the other.

### Score specifically — the intuitive answer, and why it fails

Ranking by score is the obvious move and it does not work. Above the
2.5 gate the score does not grade:

| score | n | expectancy | win rate |
|---|---|---|---|
| 2.5 | 252 | +0.434R | 42.9% |
| 3.0 | 290 | +0.745R | 47.2% |
| 3.5 | 231 | +0.316R | 43.7% |
| 4.0 | 177 | +0.919R | 52.0% |
| 4.5 | 48 | +0.304R | 47.9% |
| 5.0 | 3 | +1.000R | 33.3% |

That zigzag is noise, not a gradient. Correlation between score and
realized R is **0.022**. There is also very little spread to rank with:
over half of all passing trades score 3.0 or below, and only 3 trades
in the entire sample ever scored 5.0.

**The score is a good gate and a bad ranking key.** Those are different
jobs and it currently only does the first one.

### What was adopted, and on what grounds

**Least-correlated-first**: prefer the candidate whose trailing 120-day
daily returns are least correlated with the positions already open,
chosen greedily so that simultaneous candidates are also decorrelated
from each other.

It captured +314R against the random mean of +296R — but +314 **still
sits inside random's range**, so this is explicitly NOT a claim of
edge and must not be cited as one later. It was adopted because:

1. **It is deterministic.** All 15 seeds returned exactly +314R; the
   correlation sort overrides the random tiebreak completely. Random
   picking swings +259R to +319R on the same rules and the same data,
   a 23% spread in outcome from nothing but luck of the draw.
   Eliminating that is worth having by itself.
2. **It prevents accidental concentration.** Mean pairwise correlation
   across the 363 tickers in the sample is 0.168, so there is real
   spread to exploit, and nothing else in the pipeline currently looks
   at concentration at all.
3. It is strictly better than the incumbent, which was alphabetical.

### Re-test trigger / path out

The honest summary is that **no tested attribute predicts which of two
simultaneous candidates does better.** The expected resolution is not a
better ranking key bolted on the side, but a scoring model that
actually separates winners — at which point ranking by score becomes
correct and `rank_signals()` should be reconsidered or retired.

Re-test when either: (a) the scoring model is revised such that
score-vs-R correlation rises meaningfully above 0.022, or (b) a
per-ticker funnel log accumulates enough live data to re-run this
comparison on out-of-sample production signals rather than backtest
reconstruction.

### Also produced, reusable

`pullback_shape.csv` — for 1,009 passing trades: `pb_days` (trading
days from the 20-day high to the touch), `pb_depth` (% drawdown over
that span), `pb_speed` (depth per day) and `pb_speed_adr` (speed
normalised by ADR10). Typical pullback: 11 days, ~14% deep. None of
these are used in production; the shallow-pullback result above is the
most promising unpromoted lead in the file and is the natural thing to
revisit if the judgement gap is attacked again.
