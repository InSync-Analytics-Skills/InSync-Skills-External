# Output layout and method (reference)

Long-form reference. Not needed to run the skill; SKILL.md and references/narrative-guide.md are what a run reads, and they win where this text differs.

## Movement is not implication

The most important separation in this product. Statistical direction and business meaning
are different questions and they live in different columns.

Accelerating revenue and accelerating receivables sit on the same side of the z-axis and
read opposite ways for the business. Without this layer, receivables can rank as a strong
accelerator with nothing to signal a quality concern, which is the exact failure this layer
removes.

Every displayed row carries:

| Field | What it answers | How it is set |
|---|---|---|
| Direction, acceleration, Z, trend | what moved, how much, how unusual, how long | arithmetic |
| **Read on pace** | Favourable / Unfavourable / Ambiguous, on the CHANGE IN PACE | sector-aware polarity library plus a quality-of-growth override |
| **Level** | growing / falling / widening / narrowing / rising / improving, "still ..." when it disagrees with the read | the sign of the line's own year-over-year change and its kind |
| **Confidence** | High / Moderate / Low / Withheld | history length, gaps, filing versus transcript source, definition stability, base sensitivity |
| **Tier** | 1 primary driver, 2 supporting KPI, 3 contextual | the MCP's own tags plus the financial spine |
| **Driver assessment** | durable, base-sensitive or unresolved | see below |

Three fields, each doing one job, and none of them blended. A line that grew 50%
after growing 200% reads "Decelerating | Unfavourable | still growing": the pace slowed,
that is unfavourable for a higher-is-better line, and the level is still up. The read
grades the pace because pace is what this monitor measures; the Earnings Wrap judges the
level. "Positive level, slower pace" as a single verdict was considered and rejected - it
is a "Mixed" state under another name, one word doing two jobs.

`Ambiguous` is a real answer, not a gap. Production, capital expenditure, headcount and
capital ratios are genuinely context-dependent, and a monitor that assigns them a direction
is inventing one.

## Display order is a published composite, not a z-score

Ranking on z alone put a restructuring line at the top of a live software scan and a
securities-gain-driven segment revenue swing at the top of a live money-centre bank scan. Both were
arithmetically correct; neither was the most important thing that happened.

Order is a weighted composite of statistical unusualness, business tier, financial
materiality and data confidence. The weights are published in the engine and **every
component is recorded per row in the JSON**, so any ranking can be reconstructed and argued
with. That matters: a composite that cannot be audited is a worse trade than a crude score
that can, and this one is built to be attacked.

## Driver assessment: only what the data can support

The engine can show that a comparison is doing the work, that a definition moved, or that a
disclosed acquisition or currency line is material in the period. It **cannot** separate
price from volume from mix without a disclosed decomposition, so it does not claim to.

Labels it will assign: Base-sensitive (with the quartile evidence shown), Accounting or
definition change, Acquisition activity material in the period, A disclosed currency
contribution is material, Unresolved.

Labels it will NOT assign from arithmetic alone: price-driven, volume-driven, mix-driven,
organic. Those need a disclosed decomposition or verified management attribution.
`Unresolved: no disclosed decomposition supports a driver` is an honest answer and appears
often. Guessing here would be the same error as calling a comparison "easy" without showing
why, which the engine also refuses to do.

## Cash-conversion cross-check

When revenue accelerates, a fixed panel runs automatically: receivables, inventory,
inventory days, deferred revenue, operating cash flow, free cash flow, capital expenditure.
Each is compared against revenue growth and reported as outgrowing, broadly tracking, or
growing more slowly.

The wording is deliberately mute. The note says receivables grew faster than revenue and
that cash conversion has not yet been demonstrated on this quarter's growth. It does not say
growth is being bought, because that asserts an intent no ratio can evidence, and the linter
blocks the phrase.

## What the engine computes, and why that way

Read `references/methodology.md` for the full derivation. The four decisions worth knowing
before you interpret an output:

**Year-over-year, always.** Growth is measured against the same quarter a year earlier, in
percent for levels and in basis points for margins and ratios. This is seasonality-neutral
by construction, which is why the engine never ranks on sequential change: a retailer's
fourth quarter would top every list every year.

**Acceleration is the second derivative.** The change in the growth rate versus the prior
quarter. A line growing thirty percent for eight quarters has not inflected; a line going
from ten to twenty-five percent has, even though it is still growing more slowly.

**The z-score is against the metric's own history, not the peer set.** This is the piece
that makes one list out of a subscriber count, a gross margin and a cash flow line.
Ranking on raw acceleration would just sort by which metric has the biggest numbers. The
question an analyst actually needs answered is: how unusual is this move *for this
metric*. So each latest acceleration is scored against the mean and standard deviation of
that same series' prior accelerations. A volatile KPI has to move a great deal to rank; a
metronomic one only has to twitch.

**Gates before ranking.** A series needs nine quarters to be scored and eleven for full
confidence, and it must carry an observation in the scan quarter itself: a clean history
whose last reading is two quarters old cannot describe what changed this quarter. A line
that sits on one value for years and then steps is being redefined rather than inflecting,
and is held out too. Where a line and its share of revenue both clear the bar in the same
direction, only the stronger is ranked: that is one finding, and showing it twice costs a
slot that could have carried something new. When they move in opposite directions both are
kept, because that disagreement is itself the finding. A year-ago base below one percent of the series' own peak is treated as
unstable, because a growth rate off a near-zero base is arithmetic noise dressed as a
signal. A sign flip is unstable for the same reason. Materiality is tested on two axes, because
statistical unusualness and economic significance are different questions: a row must be
unusual for its own history (absolute z at or above the threshold) AND have moved enough
to matter in its own units (at least a percentage point for a level, twenty-five basis
points for a ratio). A row must also agree with itself: a line whose acceleration is
negative never appears in the Accelerating table even if its z-score is positive because
its history is usually more negative still. "Less bad than usual" is a real observation,
but a reader who finds a negative number under a heading that says Accelerating stops
trusting the table. Without the second test, a share count that has never varied scores
highly on a rounding-error wobble and outranks a real demand signal. Rows failing either
test are left out of the ranked tables entirely: a table padded to five rows with metrics
that barely twitched teaches the reader to ignore the table.

## Output

ONE deliverable from `out/` - the PDF - and a hard page budget the renderer enforces
rather than hopes for.

**The final page is a CLIENT reference page, not an audit appendix.** In
`standard` and `minimal` provenance it is one page titled *Sources, Guidance and
Methodology*: key sources (derived from what the note concludes from, never a
hard-coded metric list), guided-versus-delivered for the reported quarter, the
company's outlook for the next, a methodology box and one limitations paragraph.
The per-line table, the document list, the raw guidance rows, the parse
diagnostics and the held-out reasons print only under `--provenance full`, which
is the audit build. Nothing is lost from the client note: all of it is in the
JSON, and gate 53 asserts the removal holds in both directions.

**Deliver ONE file, every time: `<TICKER>_Inflection_Monitor_<Q>.pdf`.** Nothing else
is sent to the user - no HTML, no `<TICKER>_inflection_computed.json`, no QC summary,
no chat.txt, lint or ground-truth files. The pipeline still writes all of those to
`out/` because the PDF is rendered from the HTML and every gate reads the JSON; they are
working files that stay in the run folder. Copy only the PDF to the outputs
folder. The page refers to "the audit record for this run" rather than a JSON that
ships with the note, so it makes no claim about a file the reader does not receive; if
a reviewer asks for the audit JSON or the QC summary, provide it then, on request.

**Page 1.** The house header: the InSync logo (the render stops without it),
*[Company] ([Ticker]) / Inflection Monitor / Reported Financial & Operating Momentum |
[Quarter]*, then five fields, each fact once: Sector (the InSync sub-industry), Quarter
End with the fiscal quarter ("30 Jun 2026 (fiscal 2Q26)", from the source's
QUARTERENDDATE), Earnings Date (raw.json's report date, else the date on the guidance
issued with the quarter - never the fetch date; "not in source" otherwise), Comparison
and Prepared. Company, ticker and quarter are in the title and the data source is the
footer's source line, so none is repeated. Then the purpose statement: "Identifies changes in
reported financial and operating momentum relative to each metric's own historical
pattern. Uses reported company data and does not compare results with consensus
estimates, valuation, or share-price performance." Gate 92 holds all of it. Then the
analyst's one-paragraph setup (the headline conclusion), then **the quarter
strip**: revenue, EPS (with the company's own exclusion where one is filed), the sector's
key metric and the full-year guide change - company data, no consensus. A REIT's strip is
Core FFO per share, same-store NOI, occupancy (the company's tagged basis) and the Core FFO
guide; its per-share net earnings are "Diluted EPS (net earnings)", never "Adjusted"
(gate 98). Then the executive summary, in a fixed order: **Primary inflections**
(the top table's first three), **Context** (up to two: the lead cross-metric
relationship, the company's own exclusion, a rate KPI), **Key research debate** (the
narrative's `debate`, one question) and **Next-quarter proof points** (the monitor
table's first three continuation levels). Then **Key Changes In Growth Pace**: Metric |
Latest | Current YoY Change | Prior Quarter's YoY Change | Change in YoY Growth Pace |
Momentum Direction | Read on Pace | Current Level Direction | Historical Unusualness,
ordered by what the sector is read on. Current Level Direction is neutral: a level is
growing or declining, a margin widening or narrowing, any other ratio higher or lower
y/y (an improving medical care ratio is "Lower y/y"; Read on Pace says whether that is
good). Gate 108 holds it to the sign of the y/y change in the same row.

**Company-reported first.** Where the company reports a ratio that an InSync
calculation also measures (same concept family, within half a point over the window),
the company's figure is scored and ranked and the calculation is shown beside it in the
dive ("InSync-Calculated Adjusted Medical Care Ratio: 85.00%, ... up to 50 bps apart").
Gate 106. A measure InSync constructs itself - its own adjustment, EBITDA, an average of
period-end counts, a formula ratio whose numerator is not a reported line - is named
"InSync-Calculated ..." everywhere it prints; a plain ratio of reported lines keeps the
"*" (calculated) mark. A story built on an InSync construct does not lead the summary, and an
InSync-calculated EBITDA carries its bridge (EBIT + depreciation + amortization,
reconciled to 0.5%). InSync-calculated levels that are neither a sector KPI nor a spine
line (a calculated tax charge, depreciation) are not ranked. A guide is always the
company's and never carries the InSync label.

**Weak comparisons in figures.** When the year-ago quarter's own y/y was in the
bottom quartile of the line's history (or, for a level, -20% or worse), the dive and the
summary give the two-year view: "Weak year-ago comparison: 2Q25 was -40.0% y/y. Against
2Q24 ($5.00), 2Q26 is -10.0%." Placeholders `{{ddN.ya_growth}}`, `{{ddN.two_year}}`,
`{{ddN.two_year_q}}`.

**Guidance in three parts.** A. Formal company guidance: one table, each guide
once, with "Change this year" against the guide it replaced (worded guides included and
quoted as given; a guide the latest release did not repeat is marked). B. Management
commentary on the outlook. C. InSync continuation levels - arithmetic, not guidance - live
only in What to Monitor Next under that column name; gate 105 keeps `{{ddN.threshold}}`
out of the guidance bullets. One-time items are sized in bps of revenue, the size of the
margin move they can explain. Under the table sit only the notes
that change what a number means - the basis of adjusted and calculated rows, one-time
items, the period proof of a cash-flow row, the source line of a very large move, and the
marks. What the columns mean, the lines that could not be scored, and the scan's counts
are in the methodology box, not on page one.

**"vs own history", not "Z".** Each cell is a band word and the size in standard
deviations, unsigned, written "SD" as buy-side notes write it: "Unusual (2.1 SD)". Notable from 0.75, unusual from 1.5, very unusual
from 2.5; below that, "Normal range". The sign is left off because the score is measured
from the line's own average pace change, so a -34 pp slowdown on a line that usually slows
by more scored +0.6 and read as a bug. The direction is in the Direction and Pace change
columns beside it. Pace change is not coloured: colour belongs to the read, and a red
"-150 bps" beside a Favourable cost ratio would tell the reader the opposite.

**Rate KPIs have their own table.** Organic growth, unit-case volume growth,
constant-currency volume growth, same-store NOI, comparable sales, rent change and
price/volume contributions are already rates. They are typed as such (`kind:
pre_growth`) and never sit in a table whose columns say "Y/Y now": a REIT's "Net
Effective Rent Change 30.0%, Y/Y now +30.0%" reads as growth OF the KPI. The
**Rate KPIs** table under Key Changes reads Latest | Last Q | Year ago | vs last Q | vs
year ago | Read on change | vs own history - "30.0% (1Q26: 25.0%; 2Q25: 40.0%): +5.0 pp vs
last quarter, -10.0 pp vs a year ago" - and the readings are unsigned (gate 93). The deep
dive header, the monitor row ("above 30.0% extends the pickup; 3Q25 read 35.0%") and the
heatmap ("(rate)") use the same form.

**What a ranked table may show (gate 96).** A line whose unit is unresolved, a
generic balance-sheet line (payables, current liabilities, PP&E, raw materials, debt),
a schedule bucket ("2028 - Net Effective Rent", "Beginning Construction Industries"), a
segment balance no sector reads, a margin InSync calculated for a REIT, and a cash-flow
line that cannot prove it is a quarter (gate 94) are all held out of the ranked tables;
they stay in the JSON. Each ranked table carries at most two margins, a segment's copy of
a consolidated margin gives way to it, and the same measure on two ownership bases (Owned
& Managed versus Company Share) is one row - the company's own tagged basis.

**Direction, for a ratio, in words.** "Decelerating" beside a managed-care insurer's medical
care ratio, which fell 200 bps y/y after 100 bps, reads as a worsening. A ratio's Direction
cell says what its y/y change did: Rising faster, Rising less, Falling faster, Falling
less, Turned up, Turned down.

**Sections flow; they are not forced onto separate pages.** The four-page structure is a
section ORDER and a budget, not a page-per-section rule. Forcing a break before each section
guarantees the section starts clean and equally guarantees the previous page ends in white
space, which on a monitor is the wrong trade: a reader wants density and sequence, not a
title page per heading. Blocks carry `page-break-inside: avoid` so nothing splits awkwardly,
and the renderer still measures the result against the budget. Removing the forced breaks
took the reference run from four compact pages to four pages at full type size.

**Page 2, Scorecard and map.** Up to five accelerating and five decelerating rows with
Latest, Year ago, Y/Y now, Y/Y last Q, Pace change, Streak and Read on pace. Then an eight-quarter heatmap
of at most eight metrics. **Colour encodes statistical direction only.** Green and red would
tell the reader that accelerating is good, which is the mistake the Implication column exists
to prevent, so the ramp is blue to brown and the legend says so in bold.

**Page 3, What is driving the change.** Up to three deep dives, each written paragraph at
most 45 words (two sentences), set in one fixed shape the engine fills around it: what
changed, driver assessment, management explanation where fully attributed, next-quarter
test.

**Page 4, Cross-metric signals, guidance and provenance.** At most three divergences, the
cash-conversion panel, the guidance continuation test, up to five monitorable variables with
a current fact and a next proof point, then a compact provenance table and a short method
note.

`render.py` measures the produced PDF against its budget: two pages when fewer than five
lines inflected materially, three for five to eight, four above that (one more when the
note lists metrics it could not read), never above `--max-pages` (default four). Over
budget it re-renders in a compact layout - tighter type and spacing only; the dives and
quotations are unchanged - and, if that is not enough, trims contextual rows one at a
time in a fixed order, naming each dropped row on the page and in the run summary.
`format_lint` fails the run above the four-page ceiling. A note that fits four pages but
still exceeds its tighter budget is reported as `page_budget_breach` in the run summary
and listed on the QC summary, rather than passing in silence.

The coverage statement is exact and modest, and it lives in the methodology box: metrics
retrieved, how many had enough history to score, and what was excluded and why. A
genuinely partial scan still carries its banner on page one. **Do not claim every
disclosed metric was scanned.** The tag listing is
tag-only, the sweep is relevance-ranked, and neither is a certified inventory.

## When nothing inflected

A monitor run across a coverage list will often find nothing material, and that is a result.
The engine sets the overall direction to `No material inflection`, the ranked tables render
as a sentence rather than an empty grid, and the note is short. Do not pad it and do not
lower the bar to fill it: a monitor that always finds something teaches the reader that
finding something means nothing.

## Guidance is a continuation test, not a verdict

Labelling a guide that implies growth of about ninety percent, against a hundred percent just
reported, a "contradiction" is wrong: growth at a slower rate is ordinary decay. Guidance is
classified as Continues acceleration,
Continues at a slower rate, Stabilises, Turns negative, or Not comparable, and full-year
guides are held out of the quarterly comparison entirely rather than forced into it.

Three things stay distinct and are never blended: company guidance, consensus, and the
arithmetic continuation threshold. The threshold is what the level must reach for the current
growth rate to survive against a year-ago base already on the books. It is arithmetic, and
the note says so wherever it appears, because it is the single figure here most likely to be
misread as a forecast.

## Known limitations

- Line-item names are rendered exactly as the MCP returns them, including verbose forms
  like "GM (Ex-1x Items) - Gross Profit (Ex-1x Items)". Renaming a returned line item is
  prohibited by the data-source rules, so the note wears the dictionary's phrasing.
- The keyword banks are broad but finite. A company reporting something no bank anticipates
  and that carries no KPI tag remains invisible. Adding a keyword to a bank is a version
  bump, not a per-run decision, because a universe that changes between runs makes two
  scans incomparable.
- The scan sees only what a company discloses quarterly. A metric reported annually, or
  first disclosed recently, will be held out for want of history.
- Guidance covers only the handful of lines a company actually guides, usually revenue,
  margin and opex. Most of the scanned universe has no guide against which to test it.
- Many companies guide the fiscal year only (common among pharmaceutical companies, machinery makers and REITs).
  The forward continuation test then has nothing to score; the client page lists the
  annual guides for ranked lines as context and says so.
- No template exists for every sector (twenty-seven are defined; the restaurant template
  has been run on synthetic data only). A company outside them routes to no template:
  correct units, polarity and ranking, but no sector-specific ordering of what matters
  most, and the header carries the provider's classification rather than a template. A
  classification whose proposed template finds none of its own lines (pharma proposing
  managed care) is treated the same way, and the audit build says so.
- No cross-company or sector-relative context. The z-score is self-referenced by
  design; a peer-relative version is a different skill.
- Where a company discloses a metric ONLY as a pre-computed year-over-year growth rate and
  never as a level, the engine keeps that series and differences it once rather than
  twice. That recovers the metric, but its heatmap row and its acceleration are one
  differencing step shallower than the rest of the note.
- The divergence rule table carries a general set plus a banking set. On a bank, five of
  the general rules are structurally dead because gross and operating margin are not
  reported, which is why the banking rules exist; on an insurer or an energy name the
  general set is thinner than it looks and the section may be honestly empty.
- **Guidance scoring assumes a quarterly guide.** Large banks and many industrials guide
  the full year, so `guidance_check.scored` comes back empty and every row lands in
  `other_horizon`. The engine says so rather than mixing periods, but it means the most
  informative row the scan can produce is unreachable for those issuers. Testing a
  full-year guide against the year-to-date run rate implied by reported quarters would
  recover the section without mixing horizons; the engine does not do this.
- The divergence rule table is fixed and finite. It catches the classic disagreements
  between revenue, margin, earnings, cash and segments. It is not an exhaustive search for
  every possible inconsistency.

## As of, and what would prove it wrong

**Every note carries its own vintage.** The masthead prints what the note
monitors ("Quarterly Earnings & KPI Trend Changes"), the fiscal reporting
period, the two quarters it compares against (year ago and last quarter), and
the preparation date (gate 92); Methodology prints the window the scan covers,
from its first quarter, and the reporting currency; the footer carries the
generation stamp. Nothing on the page is undated, because a momentum reading
without an as-of is not a reading.

**Every displayed move ships with the figure that would falsify it.** The
monitoring table's proof point is a continuation threshold computed against a
base already reported: the level the line has to reach next quarter for the
move to have been a trend rather than a quarter. A reader disagreeing with the
note does not have to argue with the ranking - they can wait one quarter and
check the number.

This monitor deliberately does **not** take a view. It reports what moved, how
unusual that is against the line's own history, and what it would mean if it
holds; it does not tell the reader to buy anything, and the implication column
is an observation rather than a recommendation. Where the data does not support
a conclusion the note says so rather than reaching for one.
