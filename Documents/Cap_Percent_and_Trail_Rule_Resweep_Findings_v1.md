# Cap Percent and Trail Rule Resweep Findings (v1)

*Written 2026-09-03, using the fixed simulator (entry-day stop bug
corrected) run against the 253-event overhead-resistance-filtered
universe. Purpose: resolve whether the initial-stop cap percent (min of
cap percent or 10-day ADR percent at entry) should stay at 5 percent, or
move to 3 percent or 7 percent, and confirm which trail rule performs
best under the corrected simulator. All results expressed in R multiples
only, no dollar or account-size assumptions.*

---

## What was tested

Full sweep: 3 cap percentages (3, 5, 7) times 5 trail rules (10-day MA,
20-day MA, hybrid tight, ADR-adaptive, swing low) times 2 partial-profit
modes (with partial at 1.5R, no partial) = 30 combinations, each run
across all 253 events. 7,590 total simulated trades, zero errors/nulls.

Entry price = 50-day MA value on the entry date (not eyeballed). Initial
stop = entry price minus risk-per-share, where risk-per-share = entry
price times the minimum of (cap percent, 10-day ADR percent at entry) -
this is the "cap" being tested.

## Headline result

The 20-day MA trail rule, NO-PARTIAL variant, was the best performer at
every cap level tested:
- 7 percent cap: 0.418R average, 43.5 percent win rate
- 3 percent cap: 0.390R average, 37.2 percent win rate
- 5 percent cap (current): 0.355R average, 39.5 percent win rate

7 percent cap is the best of the three tested, and 5 percent (the current
default) actually underperforms both 3 percent and 7 percent - it looks
like it may be sitting in a weak middle ground rather than being an
optimal choice.

No-partial beat with-partial on nearly every combination in the full
30-way sweep - taking the 33 percent partial profit at 1.5R and moving
the stop to breakeven appears to cost more upside than it protects, at
least under the 20-day MA trail.

## Why wider caps outperformed (initial surprise, then explained)

Dave's expectation going in was that a tighter cap (3 percent) would
perform better, since it caps risk more tightly. The data went the other
way. Digging into why:

- At a 3 percent cap, the cap is the BINDING constraint (stock's real
  10-day ADR is wider than 3 percent) on 77.9 percent of all events - so
  a 3 percent cap isn't really "tighter risk control," it's overriding
  the stock's actual volatility on the vast majority of trades, forcing
  an artificially tight stop that gets hit by ordinary noise before the
  trade has a chance to work.
- At 5 percent, the cap binds on 37.9 percent of events.
- At 7 percent, the cap binds on only 19.8 percent of events - most
  trades are just using their natural, unclipped ADR-based stop.
- Confirms the mechanism: win rate and full-stopout rate move in lockstep
  with cap width. At 3 percent, 62 percent of trades hit a full stop-out;
  at 7 percent, only 54.5 percent do.

## Caveat: the binding-at-7-percent subset is outlier-driven, not broad

When isolating just the 50 events (29 unique tickers) where even the 7
percent cap was still binding (i.e. the stock's ADR exceeded 7 percent),
that subset alone showed a very strong 0.813R average and 52 percent win
rate - initially looked like evidence that ultra-high-volatility names
are especially good trades under a wide cap.

Digging further: this subset is NOT broadly strong. 42 percent of the 50
trades (21 of 50) landed at approximately -1R, a full stop-out. The
strong average is being carried by a small number of large outlier
winners - just 3 tickers (VSAT, HL, ICHR) account for 79.6 percent of the
subset's total R. This specific finding should NOT be read as reliable
evidence that ultra-high-volatility names are especially good setups; it
is unresolved and worth further, larger-sample testing before acting on
it.

## Recommendation

Move the cap percent from 5 percent to 7 percent, paired with the 20-day
MA trail rule and the no-partial variant. This recommendation is grounded
in the FULL 253-event result at each cap level (broad, not outlier-driven)
- 7 percent outperforms both 3 percent and the current 5 percent across
the whole dataset, and the mechanism (letting stops track real volatility
instead of getting artificially clipped) is well understood and sensible.

The narrower question of whether extremely high-volatility names deserve
even more room (effectively an even higher or uncapped stop) is flagged
as a separate, NOT yet resolved question - the evidence for it right now
is thin and concentrated in a handful of tickers.

## Open follow-ups

1. Confirm this recommendation holds using the OTHER outcome metric
   (Outcome_HybridTight_P_R) and cross-check against Round 1/Round 2 live
   trade results already on record.
2. Consider testing cap percentages between 5 and 7 (e.g. 6 percent) to
   see if there's a smoother optimum, rather than only the three
   originally requested values.
3. Revisit the high-ADR outlier subset with a larger sample (more tickers,
   longer history) before drawing any conclusion about whether very
   high-volatility names deserve special treatment.
4. Once cap percent and trail rule are finalized, regenerate the
   Outcome_* columns across the 253-event set and re-validate Stage 8
   downstream uses (attribute validation evidence, conviction sizing
   tiers, trail rule doc).
