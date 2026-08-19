# Historical Data Pull Summary

**Task:** Pull daily OHLCV history for the 50-Day MA Bounce Scorecard larger-sample build.
**Date pulled:** 2026-08-19
**Source:** Yahoo Finance chart API (`query1.finance.yahoo.com/v8/finance/chart/{symbol}`), fetched directly via HTTPS requests.
**Universe:** Full current S&P 400 (mid-cap) + S&P 600 (small-cap) constituent lists, scraped from Wikipedia on the pull date. This is a live/current snapshot, not a point-in-time historical membership list (per the brief, "a reasonably current snapshot list is good enough").
**Date range requested:** ~3.6 years back from the pull date to the most recent trading day, to satisfy the "at least 2, ideally 3 years" requirement with a buffer for indicators needing 200+ trading days of lookback.

## 1. How many tickers were successfully pulled

| | Count |
|---|---|
| Universe (S&P 400 + S&P 600, deduplicated) | 1,003 |
| **Successfully pulled** | **1,002 (99.9%)** |
| — S&P 400 | 400 / 400 |
| — S&P 600 | 602 / 603 |
| Failed | 1 |
| Files with a full ~3.4+ year history (≥850 trading days) | 968 |
| Files with shorter history (recent IPO/spinoff) | 34 |

Every successful file runs through **2026-08-19**, the most recent trading day at pull time.

## 2. Failures

| Ticker | Index | Security | Reason |
|---|---|---|---|
| `CWEN-A` | S&P 600 | Clearway Energy, Inc. (Class A) | Yahoo Finance has no data under `CWEN-A` (or `CWEN.A`) for this symbol; Yahoo's search only resolves the combined `CWEN` ticker for this issuer. Rather than substitute a different share class's price history under the `CWEN-A` filename (Class A and Class C shares can trade at different levels), this ticker was left out. `CWEN` (Class C) data is *not* included as a substitute — flagging this for a manual decision if Class A history specifically is needed. |

No other tickers failed — no rate-limit stalls, no other delistings/renames hit during this pull.

## 3. Actual date range achieved per ticker

Full per-ticker detail (status, exact start/end date, row count, reason for any failure) is in **`pull_report.csv`** at the repo root. Highlights:

- **968 tickers** have the full requested range: **2023-01-13 → 2026-08-19** (~902 trading days).
- **34 tickers** have less history because they IPO'd, spun off, or were newly formed after 2023-01-13. These are legitimate short histories, not pull errors:

| Ticker | Security | History starts | Trading days |
|---|---|---|---|
| ADIG | ADI Global Distribution | 2026-07-30 | 15 |
| MBGL | Mobility Global, Inc. | 2026-07-23 | 19 |
| MFP | Midera Food Processing, Inc. | 2026-06-26 | 38 |
| VGNT | Versigent PLC | 2026-03-27 | 97 |
| VSNT | Versant Media Group, Inc. | 2025-12-15 | 167 |
| SOLS | Solstice Advanced Materials | 2025-10-20 | 207 |
| RAL | Ralliant Corp | 2025-06-25 | 287 |
| KRMN | Karman Holdings | 2025-02-13 | 377 |
| MRP | Millrose Properties, Inc. | 2025-02-05 | 384 |
| ECG | Everus Construction Group, Inc. | 2024-10-28 | 450 |
| SARO | StandardAero | 2024-10-02 | 468 |
| CURB | Curbline Properties Corp. | 2024-09-26 | 472 |
| AMTM | Amentum | 2024-09-24 | 475 |
| CON | Concentra Group Holdings Parent, Inc. | 2024-07-25 | 519 |
| WAY | Waystar Holding Corp | 2024-06-07 | 548 |
| LIF | Life360 | 2024-06-06 | 549 |
| ULS | UL Solutions | 2024-04-12 | 587 |
| AHR | American Healthcare REIT | 2024-02-07 | 632 |
| BTSG | BrightSpring Health Services | 2024-01-26 | 643 |
| WS | Worthington Steel | 2023-11-28 | 680 |
| NATL | NCR Atleos | 2023-10-11 | 713 |
| VSTS | Vestis | 2023-09-27 | 723 |
| CART | Maplebear Inc. (Instacart) | 2023-09-19 | 729 |
| SEZL | Sezzle | 2023-09-13 | 734 |

These names will have less (or no) usable lookback for scorecard attributes that need 200+ trading days of history before the earliest possible entry date — worth filtering out at the sample-construction stage if they don't have enough pre-entry runway, rather than treated as pull failures.

## Output format

One file per ticker at `data/{TICKER}_1d_data.csv`, columns `Date, Open, High, Low, Close, Volume`, oldest-to-newest, `Date` as plain `YYYY-MM-DD`, `Volume` as plain integer. `Close` is the actual traded close (not adjusted close) per the brief; Yahoo's adjusted-close field was fetched but discarded.

Tickers with a `.` share-class suffix in the S&P index tables (`CWEN.A`, `MOG.A`) were mapped to Yahoo's `-` convention (`CWEN-A`, `MOG-A`) for the lookup; the output filename uses the same `-` form.

## Reproducing / re-running

`scripts/pull_data.py` does the full pull: fetches the current S&P 400 + S&P 600 lists from Wikipedia, downloads each ticker's OHLCV from Yahoo Finance with retry/backoff on rate limits, writes `data/{TICKER}_1d_data.csv`, and regenerates `pull_report.csv`. Re-run it any time to refresh the dataset to a later "most recent trading day."

## Not addressed in this task (flagged in the brief for later discussion)

The brief's addendum on Dave's manual visual-filter step (rejecting V-shaped recoveries and gap/reappraisal moves) and the follow-up validation ideas for Trend Efficiency / Trend Character as mechanical proxies are analysis-design questions, not data-sourcing ones — this pull only sources the raw OHLCV inputs those attributes would be computed from. No filtering, screening, or attribute scoring was done here.
