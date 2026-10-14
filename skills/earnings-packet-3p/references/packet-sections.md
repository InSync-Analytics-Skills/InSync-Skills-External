# The ten sections, what is on each, and why

Locked order. Every section ships in every packet. Three pages.

---

## 1. Earnings Preview & Key Debate
*the setup, and what the print actually turns on*

Two stat-tile strips of six, then **What matters, five pointers**.

**The tile strip is LOCKED and identical for every sector.** Row 1 is always: consensus
EPS, guided EPS, consensus revenue, consensus EBIT, revenue Y/Y, EBIT Y/Y. Row 2 is
always: the sector's two lead multiples, dividend yield, FCF yield, leverage, market cap.
Only the two multiples change with the sector.

**`leverage` is net debt / LTM adjusted EBITDA.** The strip is locked, so the ratio has to
be locked with it: the word appeared twice in this bundle and nowhere with a definition,
which left the choice between net and gross debt, and between LTM and NTM EBITDA, to
whoever built the packet. Where net debt is negative the tile reads `net cash`, never a
negative multiple. Three sector keys drop both terms of the ratio and take a named
substitute instead: `banking` and `financials_nonbank` use the issuer's own primary capital
measure (CET1 % or equivalent), `insurance` uses debt to total capital. The tile label says
which, so the strip never asserts a ratio it did not compute.

Do not substitute a sector-native headline for one of the six in row 1. A REIT does not
get AFFO tiles here and a bank does not get NII tiles here, because a PM comparing two
packets side by side is reading the same six positions in both. Where the issuer does not
publish one of them, the tile says so: a company that guides AFFO but not EPS shows
`Guided EPS / not guided`, and the sector-native measure appears in the model spine,
the guidance table and the pointers, where it belongs.

**One scripted exception, and it is narrow.** Row 1's `consensus EBIT` and
`EBIT Y/Y` are undefined for `banking`, `financials_nonbank`, `insurance` and `reit`, not
merely unpublished, so on those four keys the pair takes a named substitute from the ladder
in `sector-config.md` B12 and **both tile labels say which measure it is**. Measured on a
money-centre bank: `IS_P_110` and `IS_P_106` return nothing, so under the rule above two of
the six headline positions carried no defined content at all. The paragraph above still
governs everything else: the exception fires only where the measure does not exist for the
sector, only down the written ladder, both tiles take the same rung, and the substitution is
recorded in `warnings`. This is the same shape as the `leverage` tile two paragraphs up, and
for the same reason.

Codes resolve Adjusted first and fall back to Reported, so the same six labels hold
whatever the issuer publishes.

The five pointers are the packet's argument in five lines: the largest gap between
consensus and guide, the most levered assumption, the mix or driver question, the
policy/exogenous variable, and positioning versus own history and the empirical move
band. Each is one line: a debate label and the specific number that settles it.

## 2. Guidance vs. Consensus
*where the street sits against every live guided item*

**Which guided items may be omitted, when all of them will not fit.** "Every live guided
item" and "three pages is the specification" are both hard rules, and on an issuer that
guides twenty-plus lines they conflict. The order of omission, and it is the only order:
GAAP reconciliation items whose adjusted sibling is already shown; then any item whose
target period falls outside the packet window (`hist`, `target`, `next_q`, `fy_prev`,
`fy0`, `fy1`); then a guide restated from a prior period where the current one is shown.
Nothing beyond those three classes drops. **State the count either way**, `N of M live
guided items`, which rule 7 requires and which is what separates a considered subset from a
partial fetch. An item that decides the print is never omitted to make room for one that
does not.

Every live guided item: item, target period, guide as published, parsed low–high,
consensus, and position (Above high / In range / Below low / not comparable).

**Parse a band only where the guide and consensus are genuinely comparable.** A guide
reading "increase approximately $200M to $300M" is not a percentage range, and rendering
it as one is a fabrication. Constant-currency guides against reported consensus need the
FX bridge stated in the note.

## 3. Financial Summary, Reported & Consensus
*the core P&L, cash flow and balance-sheet lines, quarterly and annual*

Ten columns: four reported quarters, target Q, next Q, prior FY, FY0, FY1. Estimates in
italic. The sector spine from `sector-config.md`. Y/Y sub-row on revenue only; profit
lines carry their margin instead.

## 4. Consensus Dispersion
*where the street agrees on the target quarter, and where it does not*

Mean, low, high, dispersion, and target-vs-prior-year-actual for every line with a
published consensus.

**Dispersion = (high − low) ÷ |mean|.** It is a range statistic, not a distribution one:
one outlier broker sets it. Percentage lines look wildly dispersed because the
denominator is small, compare percentage lines only to each other. A negative low (free
cash flow, typically) makes the ratio close to meaningless; say so rather than letting it
read as a red flag.

The comparison column is headed `<target>E vs <prior>A` so the base is on the page.

## 5. KPI & Operating Driver Analysis
*segment revenue, segment profit, and the drivers the street models*

Segment table with two bands whose labels come from `seg_rev_label` and
`seg_profit_label`, **never from a literal**: revenue rows carry a Y/Y % sub-row, profit
rows carry a margin % sub-row. A hardcoded `REVENUE` and `ADJUSTED EBIT` is
wrong for any issuer that publishes neither: an insurer returns nothing on
operating profit and needs `segment premium` over `segment underwriting result`, a bank
needs `net interest income`. SKILL.md Step 5 requires the band to name what was actually
built, and rates a heading that misdescribes its own table as worse than a missing note. Then the operating drivers, where
dollar-denominated drivers carry a Y/Y % sub-row and percentage drivers do not.

**State the margin basis in the note.** Where segments sum to a revenue subtotal rather
than total revenue, that is normal and must be disclosed, never forced. Corporate and
eliminations are stated so the reconciliation is checkable.

**Both tables here run at `minFill: 0.5`.** These columns are evidential rather than
structural: a target column where fewer than half the segments or drivers carry a
consensus is scanned past rather than read, and whatever it does hold already appears in
section 4, which prints every line with a published consensus. The prune removes it and
the note names it, *"The 2Q27 E column (4 of 9 rows) is not shown"*. A segment with
actuals but no target-quarter consensus keeps its row; only the column goes.

## 6. Estimate Revisions
*how target-quarter consensus has moved since the last print*

**The vintages the feed actually published, NOT four fixed ones.** The canonical set assumes a cadence the feed does not always have, and
forcing a fourth column with no vintage behind it produces either a duplicated column or an
empty bucket. Order the columns by real date, label each from the bucket it holds, mark the
comparison column with `base: true`, and say in the note where a bucket is absent. The
delta runs from the `base` column to the newest by date and `compute.py` R8 re-derives it
from exactly those two, so the columns are the contract rather than a convention.

The canonical four are pre-print, post-print, inter-quarter,
latest, plus Δ versus pre-print and a Raised / Cut / Unchanged read. Percent-format
lines show the delta in basis points.

A blank bucket means one of two different things and the compute step must distinguish
them: a fetch gap (blocking) or a line brokers had not yet published (a coverage note).

## 7. Valuation vs. Own History
*how the stock is priced today against how it has traded*

The sector's multiples: NTM, LTM, 1-yr average, 3-yr average, LTM vs each average.

Averages are the mean of the daily LTM reading over the trailing window, state that in
the note, not the header. **Do not print the min–max range beside each average**: it
doubles two column widths, forces them off right-alignment, and endpoints set by single
days are weaker evidence than the mean beside them. Spread belongs in section 4.

## 8. Peer Comparison
*valuation, growth, and operating comparison, calendarized*

**The valuation band carries the sector lens and nothing else**, the same three or fewer
multiples shown in section 7, so a bank peer set never shows an EV multiple and a REIT
peer set never shows a sales multiple. See `sector-config.md` section 4.

One transposed table, metrics as rows, tickers as columns, with `VALUATION`, `GROWTH`
and `OPERATING & CALENDAR` bands. Lead ticker bold.

**One table, not three stacked ones.** Adjacent tables merge in Word and render the
second table's rows under the first table's header, which is undetectable by a
value-presence check and destroys the meaning of every cell.

Show growth, not levels, where both would otherwise appear: a CY26→27 EPS growth row
makes the two EPS level rows redundant. Every row leads with its unit.

**A metric no peer codes is dropped, not printed as a row of `not coded`.** Same-store
sales is the usual casualty: definitions differ so widely that the feed often codes none
of them, and five identical `not coded` cells spend a row asserting nothing. The prune
removes the row and the note states why. **When it goes, the like-for-like caveat goes
with it**, the renderer suppresses that clause via `dropped('peers', /same-store/i)`,
because a note explaining that definitions are not comparable, above a table with no
comparison on it, reads as an error. Any note clause that depends on a prunable row must
be gated the same way.

## 9. Earnings Day Reaction Metrics
*last eight prints versus the index, adjusted closes*

**The reaction-day convention, which depends on when the release lands.** `w_pre` and
`w_day` were undefined here, and an after-close reporter makes "close before" and "close on
print" ambiguous: the market cannot react to a release that arrives after the bell until
the next session. The print-day pair is therefore defined by the first session that can
trade the news:

```
release BEFORE the open       w_pre = prior session close,   w_day = call-date close
release DURING the session    w_pre = prior session close,   w_day = call-date close
release AFTER the close       w_pre = call-date close,       w_day = NEXT session close
```

`m1` and `m5` count sessions from `w_day`, not from the call date, so the windows stay
comparable across a set of issuers that report at different times of day.

**Declare the branch in `meta.reaction_basis`** (`'intraday'`, the default, or
`'after_close'`). The two price column headers derive from it: under
`after_close` they read `Close on print date` and `Close next session`, and the section
subtitle says so. Fixed `Close before` / `Close on print` headers would be untrue for an
after-close reporter, and
`section_labels` overrides a section title and subtitle only, never a column header.

**And supply the CLOSES, not the rates.** `build_computed.py` derives all five rates from
`pre`, `day`, `d1`, `d5`, `spre`, `sday` and `s5`, so no numeric column is hand-computed and
the R7 move band is never re-derived from an unaudited input. Where a set mixes
branches, record which applied per print in `warnings`: a comparison that silently mixes an
after-close reporter with a pre-open one is comparing two different things.
`compute.py` R6 re-derives `m0` from `w_day / w_pre`, so a mis-assigned pair reports as a
disagreement rather than passing.

Per print: close before, close on print, print-day move, print-day vs index, after 1 day,
after 5 days, 5-day vs index, up/down. Then a stats row: mean and median absolute
print-day move, range, up/down count, mean vs index, mean absolute 5-day move, and the
empirical base-case band.

All columns measured from the close **before** the print, so the 1-day and 5-day figures
are cumulative, the round trip, not the drift. The index column is a raw difference, not
beta-adjusted. Say both.

## 10. Key Risks & Catalysts
*every item sourced to an InSync document; nothing from the web*

Two side-by-side tables, five each, one line per item, each with its source document.

Ranked, not truncated: quantified or embedded in the guide beats unquantified; hits the
line that decides the print beats general; resolves at this print beats long-dated.
The note states the denominator, *top five of N on the record*, and lists the sources
actually used, generated from the retained items rather than hardcoded.

---

## Page budget

| page | sections |
|---|---|
| 1 | 1, 2, 3, 4 |
| 2 | 5, 6, 7, 8 |
| 3 | 9, 10 |

Exact breaks shift with the issuer. What must not shift: ten sections, three pages, and
**no page boundary falling inside a table.** A table is held whole, so the
break lands between tables. The practical effect is that pages 1 and 2 pack to whatever
fits and page 3 carries the remainder with white space at the foot. That white space is
the price of never orphaning a row, and it is the right price, a single row at the top
of a page inherits no header and no heading, and the reader cannot tell what it is. Do
not reclaim it by loosening the pagination rule; reclaim it, if you must, through the
compression ladder in `house-format.md`.
