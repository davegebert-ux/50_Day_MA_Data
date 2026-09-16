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

**Status: no ranking key found. Production uses a date-seeded shuffle,
which is arbitrary by design. The underlying question remains OPEN.**

**This entry was rewritten the same day it was written. The first
version reported that a least-correlated-first rule worked, and it was
adopted in production on that basis. It was wrong. The correction is
below and is the most useful part of this entry.**

### The question

When qualifying signals outnumber the capital to take them, which do you
take? Before today the answer was accidental: `find_new_signals_for_date()`
returns candidates in `sorted(glob(...))` order, so selection was
ALPHABETICAL and capital ran out partway down the alphabet.

### How often this actually binds

Over the passing-trade sample, 2024-09 to 2026-09 (n=1001, outliers
trimmed):

- Median signals per day: 2. 75th percentile: 3. Only ONE day in two
  years produced more than ten signals.
- Yet a ten-position limit turned away **roughly half of all qualifying
  signals** (48.7% to 50.5% depending on ordering).
- Uncapped, median concurrent positions 17-18, peak 46, above the limit
  72% of the time.

Not a "too many signals today" problem — a "positions accumulate and the
book stays full" problem. The typical day is *one slot free, two
candidates*. Dave confirmed this matches live experience. The sample
screens the full ~1,591-ticker universe, wider than would be watched in
practice, so the ~50% figure is an upper bound.

### What was tested as a ranking key, and rejected

Each run as an actual ranking rule inside a ten-slot portfolio replay,
against a random-pick baseline over 30 seeds (range **+260 to +328**,
mean +296):

| rule | total R |
|---|---|
| `total_score_v2`, highest first | +294 |
| LTE, highest first | +272 |
| pullback depth, shallowest first | +273 |
| pullback speed / ADR, slowest first | +318 |
| `overhead_R`, highest first | +312 |
| alphabetical (incumbent) | +298 |

**Every rule fell inside the random baseline's own seed-to-seed range.
None beat a coin flip.**

### THE ERROR — least-correlated-first, adopted and reverted same day

A rule preferring the candidate least correlated with the existing book
was written up as capturing **+314R vs a random mean of +296R**, and as
**deterministic** (15 seeds, all exactly +314R). It was pushed to
production on those two claims.

Both were artifacts of a bad test. The correlation matrix behind them
was computed **over the full two-year sample**, so every selection
decision used data from after the decision date. The production function
correctly used only trailing 120 days — which is why running the real
function through the replay produced a different, worse answer and
exposed the problem.

Re-run point-in-time:

| claim | as first reported | corrected |
|---|---|---|
| total R | +314 (fixed) | 279, 290, 312, 321, 345 across 5 arrival orders; mean ~309 |
| deterministic? | yes, all seeds +314 | **no** — spread is pure arrival-order luck |
| book correlation | assumed improved | **0.252**, vs 0.253 random and 0.258 alphabetical |

So it had no edge, no determinism, and **did not even deliver the
diversification it was adopted for** — which was the one argument that
supposedly did not depend on returns.

The reason is structural and should have been predicted from the daily
signal counts already measured in this same entry: **with a median of
two candidates a day, there is almost no choice available to exercise.**
You cannot diversify a book by picking one name out of two.

**Lessons, in order of importance:**

1. **Any correlation, volatility or ranking statistic must be computed
   point-in-time.** A full-sample matrix is lookahead, and it flatters.
2. **Suspiciously clean results are a symptom.** "Every seed returned
   exactly the same total" should have prompted a check, not a write-up:
   it meant the sort key was overriding the randomness, which only
   happens if the key already knows the answer.
3. **Test the production function, not a reimplementation of it.** The
   error surfaced only when `portfolio_replay.py` imported the real
   `order_candidates`. This is the momentum-screen duplication lesson
   arriving a second time.
4. A rule justified on a non-returns basis (diversification, variance
   reduction) still has to be **measured on that basis**.

### What production does now

A **date-seeded shuffle** (`order_candidates()` in `orchestrator.py`).
Selection is arbitrary — nothing tested beats arbitrary — but arbitrary
*without bias*, which alphabetical is not. Alphabetical gives
early-alphabet tickers first refusal on every constrained day forever,
so a chronic underperformer near the front keeps getting bought while
names further down are never reached. Seeding on the date keeps runs
reproducible and missed-day replays identical, while giving every ticker
the same long-run chance. It scores +302R — inside the random range, as
expected, and not a claim of edge.

### Re-test trigger / path out

No tested attribute predicts which of two simultaneous candidates does
better. The expected resolution is a scoring model that genuinely
separates winners, at which point ranking by score becomes correct and
`order_candidates()` should be retired. Re-test when either (a) score-vs-R
correlation rises meaningfully above 0.022, or (b) a per-ticker funnel
log accumulates enough live signals to re-run this on production data
rather than backtest reconstruction.

### Also produced, reusable

- **`portfolio_replay.py`** (research branch) — replays staged results
  day by day under the slot limit, importing the production ordering
  function so the two cannot drift. It is what caught the error above.
  Reports R, occupancy and skips; dollar returns and compounding are the
  flagged next addition.
- **`pullback_shape.csv`** — for 1,009 passing trades: `pb_days` (trading
  days from the 20-day high to the touch), `pb_depth` (% drawdown over
  that span), `pb_speed`, `pb_speed_adr`. Typical pullback: 11 days, ~14%
  deep. **Shallow pullbacks are the most promising unpromoted lead in
  this file**: Q1 depth averages +1.002R at 58.6% wins vs a +0.566R base,
  survives the top-10-ticker test at +0.266R, and holds across the
  Sep-2025 out-of-sample split (45.3% vs 37.4% in-sample; 60.3% vs 45.7%
  out-of-sample). It does not earn a gate — its rejected group still
  returns +0.629R out of sample — and it failed as a ranking key like
  everything else. Worth revisiting when the judgement gap is next
  attacked. Note these measures are computed per-event from price history
  and are point-in-time; unlike the correlation matrix, they are safe.


---

## Dollar Returns, Compounding and the Slot/Cost-Cap Interaction (2026-09-15)

Closes the "flagged next addition" noted above. `portfolio_replay.py` now
carries `replay_dollars()`, `size_position()`, `curve_stats()` and
`report_dollars()`, and writes `Portfolio_Replay_Equity_Curve.csv`.

### Why R alone could not answer the question

R is scale-free. It cannot show a drawdown, cannot show time spent
underwater, and does not know that ten positions at a 10% cost cap
consume the entire account. "0.59R per trade" and "what does the account
do" are different questions, and only the second one is tradeable.

### Result (1,001 passing trades, 2024-06 to 2026-09, 2.19 years)

Starting balance 25,000 dollars, 1% risk, 10% cost cap, 10 slots,
averaged over 5 arrival-order seeds:

| | final | CAGR | max DD | longest underwater | trades |
|---|---|---|---|---|---|
| fixed sizing | 57,925 | 46.8% | -13.7% | 223 days | 511 |
| compounded | 78,690 | 68.9% | -18.2% | 226 days | 506 |

The single-seed figure quoted mid-session was 72.1%; averaging five
arrival orders brings it to 68.9%. **That spread is pure arrival-order
luck** -- the same effect that exposed the least-correlated error.
Report the averaged number.

Roughly two thirds of months are positive, worst month about -9%
compounded. **The 226-day underwater stretch matters more than the
drawdown depth** -- seven months of grinding sideways is what breaks
discipline, not a single bad week.

### [FINDING] The slot limit is not an independent choice

Slot sweep, compounded:

| slots | final | CAGR | max DD | unfunded |
|---|---|---|---|---|
| 4 | 43,778 | 29.2% | -9.9% | 0 |
| 6 | 54,790 | 43.1% | -13.0% | 0 |
| 8 | 65,982 | 55.8% | -16.4% | 0 |
| 10 | 78,690 | 68.9% | -18.2% | 52 |
| 12 | 78,690 | 68.9% | -18.2% | 495 |
| 15 | 78,690 | 68.9% | -18.2% | 495 |
| 20 | 78,690 | 68.9% | -18.2% | 495 |

Below ten, returns rise with slot count and drawdown rises gently with
it. **At and above ten, every number is identical** -- 10 positions at a
10% cost cap already commit the whole account, so the eleventh signal can
never be funded. The unfunded column jumps from 52 to 495 to show it.

So `MAX_CONCURRENT_POSITIONS` has already been decided by
`MAX_POSITION_COST_PCT`. They are one parameter wearing two names.
Raising the slot count does nothing unless the cost cap falls with it,
and **this sweep cannot say which pairing is better, because the two
always move together** -- that needs a deliberate joint test.

Practical reading: with the current settings the account is fully
deployed at ten positions, so idle slots are pure drag -- but "fully
invested at all times" is also the worst posture for a correlated market
break, and this sample contains none.

### [FINDING] The risk rule is dormant

A margin variant was run out of curiosity (50% margin, 15 slots). Two
versions -- risk measured off equity, and risk measured off buying power
-- returned 117.4% and 118.3% CAGR. **Near-identical, because the cost
cap binds first on essentially every trade**, so changing the risk basis
barely moves share counts. This confirms in dollars what the
`sizing_constraint` column was added to watch for: at 1% risk and a 10%
cost cap, the risk rule almost never decides anything. Margin set aside,
not adopted; it roughly doubles both return and drawdown (-26%) on a
sample with no serious market break in it.

### How these numbers should and should not be used

**Honest:** drawdown depth, time underwater, month-to-month shape, and
whether the slot/cost-cap pairing is sensible. These depend on the SHAPE
of the return stream, not on the edge being exactly the size measured.

**Not honest:** the headline CAGR as a forecast. Compounding one sample's
returns does not validate them, it magnifies them along with survivorship
bias, optimistic gap fills and a kind two-year window. A strategy
genuinely compounding at 69% would attract capital until the edge closed.
Ten-year projections are arithmetic, not prediction: at 68.9%, 25,000
becomes ~5.7M in ten years, which is itself the argument against
believing it. **Halve the edge before planning on it** -- at 30%, ten
years gives ~345,000, and that is the figure to anchor on.

**Floor, not estimate:** equity here is CLOSED equity -- open positions
are not marked to market -- so real intra-trade drawdowns are deeper than
-18.2%.

### Trade frequency and duration (same session)

- Median hold 6 trading days, mean 8.3, max 41. **Winners run ~11 days
  (mean 12.6, +2.785R); losers die in ~3 (mean 4.6, -1.060R).** The exit
  is doing its job: cut fast, let winners breathe.
- R by duration bucket is perfectly monotonic -- 0-1 days -1.021R, 2-3
  -0.837R, 4-5 -0.308R, 6-10 +0.470R, 11-20 +2.572R, 21+ +5.248R at 92.9%
  wins. **This is not predictive.** It is the same fact viewed backwards:
  surviving trades are winning trades, because the stop is what ends them.
  Duration cannot select anything at entry.
- ~464 qualifying signals per year on the 1,591-name universe, but only
  ~230 taken under a 10-slot limit -- about 4-5 trades a week. Slot
  turnover explains the ceiling: an 8-day average hold gives each slot ~30
  turns a year, so ten slots caps out near 300. **Signals are not the
  binding constraint; slots are.** Scale down for a narrower live
  watchlist.


---

## The Unscorable Third: `_find_trend_start()` and the 95% Rule (2026-09-16)

Closes the oldest open item in the architecture doc. **Recommendation:
relax the trend-start threshold from 95% to 85%.** Validated against the
standard bar; see caveats before promoting.

### The problem

Of 2,310 staged touch events, **599 were dropped as unscorable** -- a
quarter of everything the pipeline sees, discarded silently. 593 of the
599 were MA Respect returning None; relative strength accounted for 6.
So this is one function.

`_find_trend_start()` walks back up to 252 days for the day price
reclaimed the MA50, requires it to be at least 40 days ago, and then
requires price to have closed above the MA50 on **at least 95% of days
since**. If no anchor satisfies all three, MA Respect returns None, the
total score is None, and the event is discarded.

### [FINDING] Unscorable does not mean bad

| group | n | avg R | win |
|---|---|---|---|
| passing (score >= 2.5) | 1,001 | +0.578 | 46.2% |
| **unscorable** | 587 | **+0.644** | 44.5% |
| scored below 2.5 | 250 | -0.136 | 34.4% |

The unscorable group performed slightly BETTER than the trades the
system actually takes, and nothing like the genuinely low-scoring group.
It survives both standard checks: with the top 10 contributing tickers
removed the two groups converge (+0.285 passing vs +0.224 unscorable),
and out-of-sample the unscorable group leads (+0.741 vs +0.710).

These trades are **indistinguishable from the ones being taken**. The
system was discarding a quarter of its candidates on a technicality, not
on merit.

### [FINDING] 95% is a cliff, not a slope

Re-running `_find_trend_start()` over the 599 unscorable events at
relaxed thresholds:

| pct_above required | events recovered |
|---|---|
| 95% (current) | 4 (1%) |
| 90% | 277 (46%) |
| 85% | 451 (75%) |
| 80% | 539 (90%) |
| 75% | 572 (95%) |

Nearly half return at 90%, three quarters at 85%. A single percentage
point of strictness was doing enormous work, and 95 appears to have been
chosen by intuition rather than measurement.

### [FINDING] Recovered trades are properly sorted by the existing gate

The point is not to wave these through -- it is to SCORE them so the 2.5
gate can judge them. Rescored with the real production `score_total_v2()`
at an 85% threshold (426 of 572 testable events scored):

| test | passes gate | fails gate |
|---|---|---|
| full sample | +0.830 (n=225) | +0.544 (n=191) |
| top-10 tickers removed | **+0.216 (n=197)** | **-0.052 (n=170)** |
| in-sample (pre Sep-2025) | +0.618 (n=106) | +0.246 (n=79) |
| out-of-sample | +1.019 (n=119) | +0.754 (n=112) |

**The separation holds in all four.** The top-10-removed row is the
important one -- it is where five previous findings died -- and here the
gate still splits positive from negative. At 90% the separation is
similar (+0.884 vs +0.681) but recovers only 265 events; 85% recovers
more and still sorts.

### Caveats before promoting

1. **Not yet run inside the full staged pipeline in production gate
   order.** This rescored the unscorable subset in isolation. The
   promotion bar requires the whole pipeline; that run has not happened.
2. **The recovered trades change portfolio composition.** Roughly 225
   extra qualifying signals over two years, against a slot limit that
   already turns away ~50% of signals. More candidates competing for the
   same ten slots may not raise account returns at all -- that needs
   `portfolio_replay.py`, not the staged average.
3. **By-score monotonicity is absent** in the recovered group (2.5 ->
   +1.126, 3.0 -> +0.562, 3.5 -> +0.033). Consistent with the known
   0.022 score-vs-R correlation: the gate works, the dial does not.
4. Two sandbox data faults were hit and fixed during this work (an
   unsorted SPY file, and a SPY file ending 2026-08-18 while trades run
   to 2026-09). 146 events could not be tested for lack of SPY coverage.
   Re-run on complete data before promoting.

### Recommended next step

Run the full staged pipeline end to end with `_find_trend_start()` at
85%, in production gate order, and then feed the result through
`portfolio_replay.py` to see whether the extra candidates actually
improve the ACCOUNT rather than the average trade. Only then change
`scorecard.py`.


### Follow-up: does recovering them help the ACCOUNT? (2026-09-16)

The staged average said the recovered trades are as good as the ones
being taken. That is not the same as saying they make money, because
they compete for the same ten slots. Run through the portfolio replay
(10 slots, 10% cap, 1% risk, 20 arrival-order seeds, both pools cut at
2026-08-18 where SPY coverage ends, so the comparison is like for like):

| test | current | + recovered | delta |
|---|---|---|---|
| full sample | 75.5% CAGR | 90.5% | +15.0 |
| top-10 tickers removed | 30.9% | 49.4% | +18.5 |
| in-sample (pre Sep-2025) | 30.4% | 45.5% | +15.1 |
| out-of-sample | 167.0% | 200.0% | +33.0 |

Max drawdown is unchanged to slightly better throughout (-18.3% ->
-18.1% full sample). Signals rise from 974 to 1,198; trades actually
taken rise only from 482 to 535, because slots bind -- **the gain comes
from better candidates filling the same slots, not from more trades.**

**This passes the standard bar in all four tests, including the top-10
removal that killed five previous findings.** It is the strongest result
in this file.

### Why it is still not promoted

1. **Still not run inside the full staged pipeline in gate order.** The
   recovered events were rescored in isolation and merged into the pool.
   The promotion bar requires the real pipeline run; that is the
   remaining work.
2. The out-of-sample CAGRs (167%, 200%) are inflated by a short window
   and should not be read as returns -- only the DELTA between columns
   is meaningful.
3. 146 of 572 events could not be tested at all (SPY coverage). The true
   effect size is unknown, though the untested events are unlikely to
   differ systematically.
4. Arrival-order spread widens with the larger pool (seed sd 5.7 -> 8.8),
   as expected with more candidates competing.

### Recommended change, when promoted

In `scorecard.py`, `_find_trend_start()`: `pct_above >= 95` becomes
`pct_above >= 85`. One number. Everything downstream -- the 2.5 gate,
sizing, the funnel log -- is unchanged and already handles these events
correctly. Note the funnel log will show a sharp drop in the
`score_below_threshold` and unscorable counts from the day it ships,
which is the expected signature, not a fault.


### PROMOTION RUN: full staged pipeline at 85% (2026-09-16)

The isolation test above has now been repeated the right way: both
thresholds run through `staged_pipeline_backtest.py` end to end, in
production gate order, from the same 2,310 touch events. The 95% run
reproduced the historical funnel exactly (n=1,023, +0.578R), confirming
the two runs differ only in the one constant.

**Staged funnel, 95% vs 85%:**

| stage | 95% | 85% |
|---|---|---|
| raw touch events | 2,310 @ +0.436 | same |
| + overhead resistance | 2,121 @ +0.450 | same |
| + ADR ceiling | 1,878 @ +0.501 | same |
| + scorable | 1,279 @ +0.442 | **1,724 @ +0.486** |
| + score >= 2.5 | 1,023 @ +0.578 | **1,255 @ +0.613** |

Unscorable drops from 599 to 154. The passing pool grows by 23% AND its
average trade improves -- more trades at a better average, which is the
opposite of the usual trade-off.

**Account replay (10 slots, 10% cap, 20 seeds):**

| test | 95% | 85% | delta |
|---|---|---|---|
| full sample | 68.8% | 87.8% | +19.0 |
| top-10 tickers removed | 29.6% | 49.4% | +19.8 |
| in-sample (pre Sep-2025) | 28.0% | 47.2% | +19.2 |
| out-of-sample | 157.7% | 183.5% | +25.7 |

Max drawdown is slightly BETTER at every row. The improvement is
strikingly consistent across all four -- around +19 points everywhere,
including the top-10-removed row where five previous findings died.

**One number worth flagging:** the trades rejected by the 2.5 gate go
from -0.136R at 95% to +0.146R at 85%, which looks like the gate
weakening. It is not -- with the top 10 tickers removed the rejected
group is -0.175R, still properly negative. A handful of large winners
were flattering the reject pile.

### [RECOMMENDED FOR PROMOTION]

`scorecard.py`: `pct_above >= 95` becomes `pct_above >= 85`, exposed as
`TREND_START_PCT_ABOVE = 85` so it is visible and sweepable rather than
buried in a conditional. Nothing else changes.

Both promotion-bar conditions are now met: validated inside the full
staged pipeline in production gate order, and it survives outlier
exclusion and the top-10-ticker test. This is the largest validated
improvement in the file.

**Remaining honest caveats:** 85 was chosen from a recovery-rate table,
not swept for an optimum -- 80% and 90% were not run through the full
pipeline, and the true best value is unknown. All the usual sample
limits still apply: one universe snapshot, optimistic gap fills, no
serious market break, closed equity only. The +19 points is the
measured delta on this sample, not a forecast.


### Threshold sweep: was 85 the right pick? (2026-09-16)

The one caveat left open by the promotion run. All five values run end
to end through `staged_pipeline_backtest.py`, then through the account
replay (10 slots, 10% cap, 8 arrival seeds).

| threshold | passing n | avg R | CAGR | max DD | **CAGR, top-10 removed** |
|---|---|---|---|---|---|
| 75 | 1,243 | +0.635 | 83.6% | -19.9% | 46.7% (sd 7.2) |
| 80 | 1,249 | +0.631 | 94.6% | -19.5% | 48.0% (sd 2.5) |
| **85 (shipped)** | 1,229 | +0.613 | 84.2% | -18.1% | **48.7% (sd 4.2)** |
| 90 | 1,164 | +0.612 | 78.7% | -17.6% | 44.8% (sd 5.4) |
| 95 (old) | 1,001 | +0.578 | 69.0% | -18.3% | 29.3% (sd 4.3) |

### [FINDING] 75 to 85 is a plateau, not a peak

**No change recommended. 85 stays.**

The large gain is entirely in the step down from 95. Everything from 75
to 85 is the same result within noise -- 46.7, 48.0 and 48.7 on the
top-10-removed measure, against seed standard deviations of 2.5 to 7.2.
The differences are smaller than the arrival-order noise.

The full-sample column looks like it favours 80 (94.6% vs 84.2%), but
that row is **non-monotonic** -- 80 beats both its neighbours, which a
real effect would not do. It is seed luck, and it disappears under the
top-10 test. Picking 80 on that basis would be exactly the error the
least-correlated ranking rule taught us to avoid.

What does move monotonically: drawdown improves as the threshold rises
(-19.9% at 75 to -17.6% at 90), and average R per trade falls. Looser
thresholds admit more trades of slightly lower quality, which is the
expected shape. 85 sits where the drawdown has mostly improved but the
trade quality has not yet decayed.

**Conclusion: the choice of 85 was luckier than it was principled, but
it lands on a flat region, so nothing needs changing. The threshold is
not a sensitive parameter anywhere in 75-85 -- which is itself the
useful result.** Below 90 the gate stops being the binding constraint;
the 2.5 score gate takes over, as it should.

### Process note

A run-ordering error cost time here: the constant was reverted by a
cleanup line and two "80%" runs silently reproduced 95% output. Caught
because the numbers were identical to four decimal places. **When
sweeping a constant, assert the value inside the run and echo it with
the results** -- identical output across supposedly different
configurations is the symptom to watch for.


## [NO ACTION] Funnel log check: zero-signal days explained (2026-09-16)

**Question:** do zero-signal days reflect a real absence of setups, or a pipeline fault?

**Answer: real absence. No action required. Do not re-investigate.**

Evidence, from `state/daily_funnel.csv`:

A normal day (2026-03-09, 169 tickers watched):

| gate | rejected | remaining |
|---|---|---|
| momentum_screen | 132 | 37 |
| insufficient_history | 3 | 34 |
| no_data_for_date | 3 | 31 |
| no_50ma_touch | 8 | 23 |
| overhead_resistance | 2 | 21 |
| score_below_threshold | 5 | 16 |
| **passed all gates** | | **16** |

Three consecutive zero-signal days (2026-06-01 to 06-03, 151 tickers watched each day) were identical to each other: 140 failed the momentum screen, only 5 names reached the touch scan, and none of those 5 were touching their 50-day MA. Nothing reached the scorer at all.

**[FINDING] Dry spells originate at the momentum screen, not at scoring or sizing.** When few names are in qualifying uptrends, the funnel empties at stage 0 and everything downstream is correctly idle. This is consistent with the earlier result that zero-signal weeks are normal (9 of 116, 7.8%, longest dry run 5 weeks).

**[SECONDARY OBSERVATION] Live log confirms the cost cap binds.** On the March day, 16 signals qualified but only 10 opened; 6 were `skipped_no_capital`, and `sizing_constraint` was `cost` on every single row — the risk rule never bound. This is the dormant-risk-rule finding showing up in production data rather than in replay.

**Expected future signature:** from the day the 85% trend-start change ships, `unscorable` counts should collapse and `score_below_threshold` should rise. That is the intended effect, not a fault.
