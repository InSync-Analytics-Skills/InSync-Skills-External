# Estimate-store edge cases and how to handle them

Read this when SKILL.md Step 3 sends you here: the two `get_estimates` query modes disagree on a
period, or the adjusted and reported consensus look crossed. Every example figure below is
invented; only the shapes matter.

Contents
1. The two query modes can disagree by 10x (split re-basing)
2. Resolving the disagreement: R1 to R4
3. Crossed consensus line items
4. Reporting what you resolved

## 1. The two query modes can disagree by 10x

The windowed pull (`latest={"enabled":false}` + `updatedate_start`/`updatedate_end`) and the plain
pull (no window) can return the same period and the same `UPDATEDATE` on DIFFERENT scales, e.g.
0.31 windowed vs 3.12 plain (10x apart), and a single row can carry both scales at once (MAX on one
scale, MIN on the other). Taking the wrong one reports surprises near +900% on a report that passes
every gate.

**DIAGNOSED: the gap is a SPLIT RE-BASING difference between the two query modes.** The ratio between the
two modes equals a stock-split factor in that ticker's `get_stock_split` table (any factor, including
fractional ones). The direction is NOT consistent: either series can be the wrong one. So the split
table explains the shape of the gap but cannot say which side is right; and a row whose MAX and MIN
sit on different scales is itself contaminated.

**RESOLVE IT WITH PRICE. Never with the actual.** Choosing the candidate closer to the reported
actual selects the input by the answer and biases every surprise toward zero. Price never touches
reported EPS, so it cannot contaminate the surprise it is used to compute.

## 2. Resolving the disagreement: R1 to R4

**R1: disqualify contaminated CANDIDATE ROWS, not whole periods.** Pull `get_stock_split(ticker_id)`
once per ticker. Test EACH candidate row: if that row's own `MAX / MIN` matches a factor in the
ticker's split table (within 15%), the row carries two scales and no basis repairs it: drop that
candidate. Then:
- one candidate survives: it is the consensus, subject to R3;
- both survive: continue to R2/R3;
- neither survives: **withhold the period and name it**.

Scope this to the ROW. Withholding the whole period because one candidate is contaminated throws
away a clean candidate that prices correctly. A thin two-broker panel with a wide range whose ratio
matches no split factor is not contamination: keep it.

**R2: price the two candidates.** `get_market_data(ticker_id, start_date=<vintage date - 5d>,
end_date=<vintage date>)`, one call per ticker covering every vintage in the window.

    implied_PE = ADJUSTED_CLOSE / (candidate x periods_per_year)
    periods_per_year = 4 for quarterly filers, 2 for semi-annual filers

The reference band is the ticker's OWN implied P/E on the periods where the two modes agree. Those
periods are undisputed, so the band calibrates itself and nothing is hand-supplied.

**R3: decide.** The candidate whose implied P/E sits within **1.5x** of that band is the consensus.
If neither does, withhold the period. Where the true value was known, the right pick landed within
1.09x of its band and the nearest wrong one 1.72x out, so 1.5x has clear air on both sides. The winner is NOT always the plain call, so never apply a blanket "prefer
the plain call" rule.

**R4: FORWARD periods have no actual, so R2/R3 cannot run. Guard them on dispersion.**

    hist_ratio = MAX / MIN on each CLOSED period of that metric
    if forward MAX / MIN  >  1.5 x max(hist_ratio):
        -> contaminated panel. Walk BACK through the snapshots to the most recent one whose
           MAX / MIN is inside the historical range, use it, and flag both values with dates.

Example shape: closed halves run 1.04 to 1.12, while the forward panel reads MIN 2.20 / MAX 4.70
(2.14), a full-year figure sitting in a half-year panel. If it entered on 2026-07-28, when the
consensus jumped in one step, the last clean snapshot before that date is the one to use, and
`data_flags` says the current panel is contaminated and from when.

**The price test does not apply, so WITHHOLD, when:** the metric is not a per-share earnings line;
the actual or either candidate is negative or near zero (P/E carries no signal); the ticker has
fewer than two agreeing periods to build a band from; or `get_market_data` returns a currency that
differs from the consensus currency and no FX conversion is available. Check `CURRENCY` on the price row against the consensus currency before
dividing (a price row can come back in a different currency from the EPS consensus).

## 3. Crossed consensus line items

⛔ **THE CONSENSUS LINE ITEMS CAN BE CROSSED.** The adjusted and reported consensus series can be
filed under each other's LTCODE while the ACTUALS are labelled correctly. Example shape (invented
figures):

| Line | Actual | Consensus | Surprise as labelled |
|---|---|---|---|
| `IS_P_127` EPS (GAAP) | $1.00 | $1.65 | -39% |
| `IS_P_128` EPS Ex-1x Items | $1.70 | $0.98 | +73% |

Cross them and both surprises land near +2%. This is more dangerous than the scale defect: the
crossed GAAP consensus implies a normal P/E, so the price test passes it and a large miss that never
happened is published. Two free checks:
- **Ordering inversion.** Adjusted EPS normally sits at or above reported. Check each quarter where
  `sign(adjusted - reported)` differs between the ACTUALS and the CONSENSUS. That quarter is crossed
  if, with the two consensus codes swapped, BOTH surprises fall below half their labelled size (in
  the shape above, both labelled surprises become about +2%). A small adjusted-vs-GAAP gap that merely
  flips sign does not shrink the surprises and is not a crossing.
- **Broker-depth inversion.** The headline metric carries the deeper panel; a headline line whose
  panel has under half the secondary line's broker count (e.g. 4 against 20) is backwards.

On either signal: STOP and report the crossing, naming the crossed quarters. Do not auto-swap: name both codes, both panel depths
and both surprises, and let a human confirm which series belongs to which line before the report is
built.

## 4. Reporting what you resolved

Every period where the two modes disagreed goes into `data_flags` with both candidates, the split
factor, the implied P/Es and which was taken. A resolved period is published; a withheld one is
named. Do NOT silently substitute and say nothing: the disagreement is reported in the output.
