# Scoring methodology - the thresholds the engine applies

**This file is generated** from `SCORE_RULES` in `scripts/compute_capalloc.py`,
the same structure the scorer reads every threshold from, so the numbers here are
the numbers applied.

Each dimension is scored 0-10 and weighted by sector; the weighted average is the
**Overall**. Weights are printed in the report's Scorecard and Appendix A for the
company in question. There is no qualitative overlay and no forward view.

**Score bands** (a label over the result, not part of the scoring): 8.0+ strong * 6.5-7.9 solid * 5.0-6.4 adequate * below 5.0 weak

Most dimensions start from a **base score** and adjust it; a few **set** the score
outright. Which applies is stated per dimension. A dimension whose required input
is unavailable is UNSCORED and carries no weight - it is never given a mid score.

## Buybacks

Base score **5**, adjusted by the rules below.

*Measured:* Buyback yield; Share-count change; Average repurchase price vs current price; Authorisation remaining.

*Scope:* Only when buybacks were executed; otherwise the base score stands.

- **+2** - net share count reduced by more than 2.0% over the window
- **-2** - share count flat or higher despite buybacks (dilution offset)
- **+2** - average repurchase price more than 5% below the current price
- **-1** - average repurchase price more than 10% above the current price
- **+1** - buyback yield above 3.0% of market capitalisation

## Dividends

Base score **5**, adjusted by the rules below.

*Measured:* Payout of earnings; Free-cash-flow cover; Dividend growth.

*Scope:* Only when a dividend was paid. Midstream issuers are scored on distributable cash flow instead - see the midstream rules below.

- **+2** - payout below 60% of earnings
- **-2** - payout above 90% of earnings (limited cushion)
- **+2** - free cash flow covers the dividend more than 1.5x
- **-3** - free cash flow does not cover the dividend
- **+1** - dividend growth above 5% a year

**Midstream (distributions from distributable cash flow)**

- **+3** - distributions covered 1.5x or better by distributable cash flow
- **+1** - distributions covered 1.1x to 1.5x
- **-3** - distributions not covered by distributable cash flow

## M&A

Base score **5**. The rules below SET the score rather than adjust it.

*Measured:* Spend vs cumulative operating cash flow; Realised return trend (ROIC); Deal-return disclosure.

*Scope:* Scored only where acquisition spend exceeds 15% of cumulative operating cash flow. Below that it is immaterial and UNSCORED (no weight). Where the acquisition line is absent from the source it is UNSCORED - never read as 'no acquisitions occurred'.

- materiality floor: acquisitions above 15% of cumulative operating cash flow
- **sets 8** - sets the score to 8 where ROIC expanded more than 1pp over the period
- **sets 3** - sets the score to 3 where ROIC contracted more than 3pp AND acquisitions took more than 40% of operating cash flow
- the acquisition-share condition attached to the rule above
- **sets 5** - otherwise 5: ROIC change reflects the whole capital base, and deal-level attribution needs transaction data the feed does not carry

## Capex

Returns **6** for every company - see below.

*Measured:* Investment intensity (capex / revenue); Reinvestment rate (capex / operating cash flow); Realised return trend (ROIC).

*Scope:* NOT CURRENTLY GRADED. The dimension returns its base score of 6 for every company - an issuer at 1% capex intensity and one at 41% both score 6 - while still carrying its sector weight. The intensity and growth figures below are measured and printed, and a descriptive assessment is derived from them, but no threshold moves the score. Earlier versions of this document published a four-band table here, which described grading that does not happen. Whether to grade it is an open methodology decision; until it is taken, this says what the engine does.

- flagged as capital-heavy above 15% of revenue (no score effect)
- flagged as accelerating above 20% CAGR (no score effect)
- below 3% of revenue reads as lower capital intensity; for a capital-intensive sector it is reported as a data-completeness signal (no score effect)
- below 2% of revenue AND declining is described as under-investing (no score effect)

## Balance sheet

Base score **6**. The rules below SET the score rather than adjust it.

*Measured:* Net debt / EBITDA (or debt / total capital where that is the sector measure); Interest cover.

*Scope:* Banks and insurers are re-scored by their sector module on regulatory capital instead. Where the net-debt figure is withheld over a source conflict the dimension carries half weight.

- **sets 9** - sets 9 - a net cash position (leverage below zero)
- **sets 8** - sets 8 - below 1.5x net debt / EBITDA
- **sets 6** - sets 6 - from 1.5x to 3.0x
- **sets 3** - sets 3 - above 3.0x
- caps the dimension at 4 where interest cover is below 3.0x

**Insurance (debt / total capital, per cent)**

- **sets 8** - sets 8 - below 25%
- **sets 7** - sets 7 - from 25% to 35%
- **sets 5** - sets 5 - from 35% to 45%
- **sets 3** - sets 3 - above 45%

## Capital discipline

Base score **5**, adjusted by the rules below.

*Measured:* Reinvestment rate within a sustainable band; Leverage; Buyback price discipline.

*Scope:* The result is averaged with the value-creation score, so the outcome feeds back into the process grade. Reported PROVISIONAL at half weight unless the funding bridge (gross debt issued and repaid) is available, which the fetched set does not carry.

- **+1** - reinvestment rate between 5% and 60% of operating cash flow
- the upper bound of the band above
- **+1** - leverage below 2.0x
- **+1** - buybacks executed below the current price
- **-2** - buybacks not reducing the share count (dilution offset)
- the subtotal is then averaged with the value-creation score

## Value creation (outcome)

Base score **5**, adjusted by the rules below.

*Measured:* ROIC vs cost of capital; Free cash flow per share growth; EPS growth; Total shareholder return; Share-count change; Capital returned vs free cash flow; Book value per share.

*Scope:* Graded on the OUTCOME, not the process. Sector modules replace this with their own lens (REIT on AFFO, banks on ROTCE and CET1, insurance on ROE).

- **+1** - ROIC more than 3pp above the assumed cost of capital
- **-1** - ROIC below the assumed cost of capital
- **+1.5** - free cash flow per share compounding above 8% a year
- **+0.5** - free cash flow per share growing but below 8%
- **-1** - free cash flow per share not growing
- **+1** - EPS compounding above 8% a year
- **-1** - EPS declining
- **+1.5** - total shareholder return 12% a year or better
- **+0.5** - total shareholder return 8% to 12%
- **-1** - total shareholder return below zero
- **+1** - share count reduced more than 3.0%
- **-1** - buybacks executed without reducing the share count
- **+0.5** - 60% or more of free cash flow returned to holders
- **-1** - book value per share declining

## Sector lenses

Sector modules replace the dimensions where the default rules do not transfer,
keeping the same 0-10 scale and band labels. See `references/SECTOR_MODULES.md`.

- **REIT** - re-scores dividends, balance sheet, capital discipline and value
  creation on the AFFO lens (AFFO payout and coverage, AFFO/share growth,
  leverage, equity-funded-growth dilution watch).
- **Banks** - re-scores balance sheet on CET1 headroom rather than net debt /
  EBITDA, dividends on payout of net income, and capital discipline and value
  creation on ROTCE against cost of equity and tangible book value per share
  growth. Capex is not scored (weight 0).
- **Insurance** - ROE, book value growth and debt to total capital; capex
  suppressed.

