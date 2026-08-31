50-Day MA Bounce Scorecard — Project Plan

Last updated: 2026-08-31

Goal

Formalize and validate Dave's intuitive visual judgment for pullback-to-50-day-MA setups into a systematic, multi-attribute scorecard, validated against a 456-event historical dataset and Dave's own real-money trades.

Current Status
Merged into scorecard.py (live, validated)
Attribute	Approach
Trend Efficiency	Gap Check (40d) + ATR Variability/whipsaw (20d), min-combined
MA Respect	Trend-start-anchored, ADR-normalized, 3-component (fast-line depth / smoothness R2 / MA50 violations), min-combined
Relative Strength	60-day pullback from peak stock/SPY ratio

All three re-validated against the full 456-event sample with zero regressions vs. the pre-merge validated versions.

Pre-watchlist screening layer (separate from scorecard, gates entry to watchlist)
Overhead Resistance check (2yr lookback, fresh-high requirement)
Smoothness check (126-day R2 >= 0.85)
Not pursued further (re-confirmed weak on fresh review, not permanently closed)
Trend Character - previously excluded (12 configs tested, no signal); the specific gap that prompted revisiting it turned out to already be covered by the merged ATR Variability check
Institutional Distribution - previously excluded (9 configs tested, no signal)
Controlled Arrival - rebuilt as an MVP this session; weak/flat raw correlation (+0.01) and directly contradicted by known trades (ARMK, CORT winners scored as "bad" under this metric)

All three remain fully documented (formulas, test history, this session's re-tests) in the notes file - nothing is lost if a future need reopens one of these as a "phase 2" enhancement.

Not yet reviewed at all (still old v1 logic, no attention yet)
ADX Trend Strength
Long-Term Structure
Earnings Proximity (blocked - no earnings-date source for full ~1,002-ticker universe)

Decision (2026-08-22): attribute expansion is closed for now. Dave flagged real overfitting risk (validated - see Trend Efficiency v2 missing 32.5% of top-quartile winners) and chose to stop adding attributes in favor of stress-testing what's already built.

Recent Validation Milestones
45-ticker spot-check (screened tickers) reviewed by Dave - strong real-world confirmation, including several live/past trades landing where expected.
Full 456-event historical rerun through the updated scorecard: MA Respect and Relative Strength both show improved correlation with trade outcomes; Trend Efficiency's apparent correlation drop was investigated and attributed to the old score's fragile, near-empty top-tail sample rather than a real decline.
Key learning: trade outcomes (P/R) are fat-tailed - every score band has the same median (-1.0), so any attribute will show weak/noisy linear correlation with mean P/R regardless of true quality. Correlation numbers should be read as directional, not definitive.
Repository / Retention Practices (new 2026-08-31)

As of 2026-08-31, this project's key artifacts are now version-controlled in the existing GitHub repo (originally set up for the Yahoo Finance historical data pull), organized as follows:

data/ - raw OHLCV pulls
scripts/ - one-off pull/utility scripts
scorecard/ - scorecard.py (the scoring system itself)
Results/ - validation outputs, e.g. Full_456_Rerun_v2_Scores_vs_Outcomes.csv
Documents/ - this project plan, MA_Respect_Redesign_Notes.txt, and future reasoning/decision-history documents

Going forward, validated milestones should be committed to this repo (code + supporting data + a short changelog note) rather than relying solely on conversation memory, so a fresh conversation thread can reconstruct full context from the repo alone.

Stress-Testing Log
Stress-Test Finding: score_total_v2 Bottom-Quartile 2026 Anomaly (Investigated, Closed) - 2026-08-31

Trigger: Broader stress-testing of the new score_total_v2 combined score (additive mean of Trend Efficiency v2, MA Respect v5, Relative Strength v2) surfaced meaningful disagreement rates between score and outcome: top-quartile trades still hit full stop-loss 45.8% of the time (49/107), and bottom-quartile trades were big winners (P/R >= 1.0) 25.9% of the time (35/135) - both within the expected range for a fat-tailed outcome distribution, but worth investigating for a systematic cause.

Observation: Bottom-quartile-score trades showed a year-over-year improvement in mean outcome that looked like a possible trend: 2024 mean -0.08, 2025 mean -0.18, 2026 mean +0.10 (all medians still -1.0, consistent with the known fat-tail pattern).

Hypothesis tested #1 - broad market regime (SPY above/below its own 50-day MA): Rejected. Regime mix was similar across all three years (67-70% of events occurred while SPY > its 50-day MA in 2024, 2025, and 2026 alike - no meaningful shift). Splitting bottom-quartile trades by regime showed no clean pattern either (SPY-below periods actually outperformed SPY-above periods slightly, opposite of the "strong market bails out weak setups" theory).

Hypothesis tested #2 - outlier concentration: Confirmed. Removing just the 3 best- performing 2026 bottom-quartile trades (ICHR_0, PTEN_0, ICHR_1 - P/R of 4.79, 4.00, and 2.40 respectively) flips the 2026 bottom-quartile mean from +0.10 back to -0.09, in line with 2024 and 2025. The remaining 56 trades show the same fat-tailed shape already documented elsewhere in this project (majority at exactly -1.0 P/R, long thin tail of smaller winners).

Conclusion: The apparent 2026 bottom-quartile improvement is sample noise from a small number of outlier winners in a modest sample (n=59), not a real shift in what a low score means and not a market-regime effect. No scorecard change warranted. Consistent with the project's existing "fat-tailed outcomes, read correlation/means as directional not definitive" learning - this is a fresh empirical instance of that same principle, this time on the upside rather than the downside.

Status: Closed, no action. Logged for future reference in case a similar pattern reappears with a larger sample and deserves reconsideration.

Open Items

Near-term

Continue broader/deeper stress-testing of score_total_v2 beyond the check above - e.g. out-of-sample testing on events outside the 456-event set, sensitivity to small parameter perturbations, and further investigation of the known gap/earnings single-event distortion (CORT/CFFN/LXP pattern) at the combined-score level.

Completed this session (2026-08-22)

Ran both pre-watchlist screens against the full ~1,002-ticker universe (996 screenable). Result: 4.8% pass both (48 tickers) - confirms the earlier partial-sample number was already the true full-universe result, not a lucky sample. Full breakdown saved to Full_Universe_Prewatchlist_Screen_Results.csv.
Combined-scoring architecture decided: additive (simple average), not veto. Tested empirically against the 456-event sample - additive gave a much cleaner good/bad separation (0.474 spread vs. 0.278 for veto). Veto underperforms because requiring all 3 attributes to be strong is an overly harsh bar with only 3 attributes - one unlucky reading disqualifies an otherwise-great trade. Caveat: this is based on only the 3 currently-validated attributes - revisit if more attributes are added later, since additive could let a future noisy/broken attribute quietly drag down good trades unnoticed.
score_total_v2() coded into scorecard.py - additive mean of the 3 validated attributes, kept separate from the legacy score_trade() (which still sums all 9, including the weak/unreviewed ones). Validated with 0 mismatches across all 350 fully-scored historical events.

Known accepted limitations (not being chased further)

Single real events (gaps/earnings) inside a fixed lookback window can drag down Gap Check / ATR Variability even when a stock's ongoing behavior is excellent (confirmed on CORT, CFFN, LXP).
Small individual-name calibration gaps (e.g. SLM, TXRH) accepted as noise rather than over-fit to.

Lower priority / longstanding

CWEN-A ticker pull failure; a few old TradeZella data gaps.
Trail rule #6 (ATR-multiple) never coded; trail-rule analysis not extended to the full ~139-row hand backtest set.
Time-based stop-widening idea untested.
Full execution automation.
Visual-filter hypothesis test not yet run.
