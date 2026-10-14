# Inflection Monitor: methodology

Read this when you need to defend a ranking, explain a flag to a PM, or change a
threshold. Every constant named here is a named constant in `scripts/compute.py` and is
printed into the note's own methodology appendix, so a reader can audit a ranking without
reading code.

## Contents

1. [Why acceleration and not growth](#1-why-acceleration-and-not-growth)
2. [Why year-over-year and never sequential](#2-why-year-over-year-and-never-sequential)
3. [Levels versus ratios](#3-levels-versus-ratios)
4. [The self-referenced z-score](#4-the-self-referenced-z-score)
5. [Gates: what is excluded and why](#5-gates-what-is-excluded-and-why)
6. [Comp effects: earned versus handed over](#6-comp-effects-earned-versus-handed-over)
7. [Streaks](#7-streaks)
8. [Universe screening](#8-universe-screening)
9. [Divergence rules](#9-divergence-rules)
10. [The heatmap](#10-the-heatmap)
10b. [Guidance corroboration](#10b-guidance-corroboration)
10c. [The forward bar](#10c-the-forward-bar)
10d. [Source provenance](#10d-source-provenance)
10f. [The two-pass universe](#10f-the-two-pass-universe)
10g. [Management commentary](#10g-management-commentary)
10h. [Provenance](#10h-provenance)
10e. [Where the boundary sits](#10e-where-the-boundary-sits)
11. [Constants](#11-constants)
12. [Known failure modes](#12-known-failure-modes)

---

## 1. Why acceleration and not growth

A growth rate tells you the level of momentum. It does not tell you that anything changed.
A business compounding at thirty percent for eight straight quarters has a high growth
rate and no news in it. A business going from ten percent to twenty-five percent has news
in it even though it is still growing more slowly than the first.

What an analyst is scanning for, between prints, is the change: the point where a series
departs from its own trajectory. That is the first difference of the growth rate, which is
the second difference of the level, and it is what this engine ranks on.

    growth_t        = (v_t - v_{t-4}) / |v_{t-4}|
    acceleration_t  = growth_t - growth_{t-1}

Acceleration is signed and reported in percentage points for levels, basis points for
ratios. A negative acceleration on a still-growing line means growth is slowing, not that
the line shrank; the note's tables show growth and prior growth alongside acceleration so
that distinction is always visible.

## 2. Why year-over-year and never sequential

Sequential (quarter-over-quarter) change is contaminated by seasonality. Rank a retailer
on sequential acceleration and the fourth quarter tops the list every single year, which
is a fact about the calendar and not about the business. Year-over-year comparison places
each quarter against the same quarter a year earlier, so the seasonal pattern cancels out
of both terms.

The cost is latency: a year-over-year frame takes a quarter longer to register a turn than
a sequential one, and needs five quarters of data before it produces its first reading.
That is a price worth paying, because the alternative is a ranking that is mostly
seasonality. Sequential figures may appear as context in a note; they are never ranked.

**Which quarter is "a year earlier".** Each observation is placed on a regular
quarterly grid by its `QUARTERENDDATE`: its slot is the number of quarters before the
series' latest observation (days / 91.31, rounded), and it must land within 20 days of
that slot. A quarter the feed did not return - or returned in a form the parser could
not read - is an empty slot, so the year-ago comparison against it is undefined: the
line is held out with a reason naming the missing quarter, never compared with the
quarter before it. Counting four rows back instead would let one missing value move
every later comparison by a quarter. The independent check (`scripts/ground_truth.py`) finds the
year-ago quarter by fiscal-label arithmetic instead, so the two agree only when the
dates and the labels describe the same quarters.

The engine therefore needs `4 + 1 = 5` quarters for a first growth rate, `6` for a first
acceleration, and rather more than that to say whether an acceleration is unusual (see
section 4).

## 3. Levels versus ratios

A percentage cannot sensibly grow by a percentage. If gross margin goes from 70 percent to
75 percent, the honest statements are "up 500 basis points" and, at a stretch, "up 7.1
percent of itself" - and the second is never what anybody means. So the engine splits the
universe:

| Kind | Detection | Growth measured as | Acceleration in |
|---|---|---|---|
| Level | everything else | percent change | percentage points |
| Ratio | value carries `%`, or name matches margin/rate/yield/ratio/percent/share-of, or name starts with `%` | change in basis points | basis points |

Detection leans first on the raw value, because the MCP writes a `%` into `VALUE` for
every ratio series it stores. Name matching is the fallback for the few ratios stored
without one. The decision is made once per series from the whole history, never per
observation, so a single series can never mix conventions midway.

**Percent scaling.** The MCP may store a ratio as `75.02%` (points) or, in other tables,
as a fraction. The engine decides from the series' own maximum magnitude: at or below 1.5
it is treated as a fraction and multiplied by 100, otherwise it is already in points.
Deciding per series rather than per row is what keeps a margin history internally
consistent.

## 4. The self-referenced z-score

This is the central design decision, and the one that makes a single ranked list possible.

The problem: after computing acceleration for eighty line items, they are not comparable.
A hypergrowth segment's acceleration is routinely tens of percentage points; a mature
utility revenue line moves by fractions. Sort the raw accelerations and you have sorted by
which metric happens to have volatile arithmetic, which the analyst already knew.

The reframe: the useful question is not "which metric moved most" but **"which metric's
move is most unusual for that metric"**. A gross margin that has not shifted more than 40
basis points in three years moving 180 basis points is a genuine event. A segment that
swings 30 points every quarter moving 30 points is Tuesday.

So each series' latest acceleration is standardised against its own prior accelerations:

    z = (acceleration_latest - mean(acceleration_history)) / stdev(acceleration_history)

where `acceleration_history` excludes the latest observation, so the latest move is scored
against a baseline it did not itself set. Population standard deviation is used, which is
the right choice when the prior accelerations are the complete history rather than a
sample from a wider one.

This makes the score scale-free and unit-free. A subscriber count, a margin and a cash
flow line all land on one axis, and the axis means "standard deviations away from how this
line normally behaves".

**Consequences worth knowing.** A metronomic series produces a small standard deviation
and therefore ranks on a small absolute move; this is correct and is the point, but it is
why the tables always print the raw acceleration next to the z-score, so a reader can see
that a `+2.4` z-score corresponds to a 60 basis point shift and judge for themselves. A
series with zero acceleration variance yields no z-score and is held out rather than
ranked at infinity.

## 4b. Reporting the z-score to a reader

The score is kept and the label is not. Tables head the column **How unusual** and print a
word beside the figure: Notable from 0.75 standard deviations, Unusual from 1.5, Very unusual
from 2.5.

Two reasons. A bare "Z" is jargon in a document whose promise is five minutes, and worse, a
large unexplained number reads as a large piece of good news, which is precisely the
conflation the Implication column exists to break. The band word carries the magnitude; the
Implication column carries the meaning; neither borrows the other's job.

## 5. Gates: what is excluded and why

Ranked before the ranking. Each gate exists because of a specific way an unfiltered
acceleration ranking goes wrong.

**History length.** Nine quarters minimum to be scored, eleven for full confidence. Below
nine there are too few prior accelerations for a standard deviation to mean anything. Nine
and ten are ranked but carry a `short history` marker.

**Minimum acceleration observations.** At least four prior acceleration points before a
z-score is computed at all.

**Unstable base.** A year-over-year growth rate whose denominator is a near-zero number is
arithmetic noise wearing a signal's clothes: a line going from 0.3 to 4.1 is "up 1,267
percent" and means nothing. Any observation whose year-ago base is below one percent of
that series' own peak absolute value is discarded, and the series is flagged
`base_unstable` and held out of the ranking entirely.

**Staleness.** A ranked line must carry an observation for the scan quarter itself. A series
with a clean nine-quarter history whose last reading is two quarters old cannot answer "what
changed this quarter", and live testing found exactly that: a membership count last reported
two quarters before the scan sitting in a ranked table and supplying one leg of the only
divergence. It was auditable from the provenance span, which is not the same as being
correct. Held out with the reason stated.

**Step series.** A line that sits on one value for eight quarters and then moves twice is
being redefined or restated, not inflecting. The reversion afterwards scores a large z
against a near-zero standard deviation, and the materiality floor does not catch it because
a step is large in absolute terms. Detection is blunt and reliable: if at least sixty
percent of sequential changes are exactly zero, the series is a step function and is held
out. Live testing surfaced this as a ratio topping a decelerating table on what was almost
certainly a definitional change.

**Sign flips.** If the current and year-ago values straddle zero, the growth rate is
uninterpretable, not merely large. Discarded on the same footing.

**Materiality, on two axes.** A z-score answers "is this move unusual for this line". It
does not answer "is this move big enough to care about", and those are different questions.
A share count that has barely varied in three years has a minuscule standard deviation, so
a four-hundredths-of-a-point wobble scores above one and outranks a real demand signal.
Statistical unusualness without economic significance produces a ranked table full of
metronomic artefacts, which is a worse failure than missing a marginal signal.

So a row must clear both: absolute z at or above 0.75, AND an absolute acceleration of at
least one percentage point for a level or a pre-computed growth rate, or twenty-five basis
points for a margin or ratio. Rows failing either are reported as "no material inflection"
and left out Not hidden: counted in coverage and available in the JSON. A table
padded out to a fixed five rows with metrics that barely twitched trains the reader to stop
reading the table, which destroys more value than the omitted rows create.

**Sign agreement.** A row appears in the Accelerating table only if its z-score AND its
raw acceleration are both positive, and in the Decelerating table only if both are
negative. A line whose acceleration is negative can score a positive z when its own
history is usually more negative; that is a genuine observation and it stays in the JSON,
but a reader scanning a table headed Accelerating and finding a negative number in it
stops trusting the table, which costs more than the row is worth.

**Duplicates.** Two series with identical value histories are the same series wearing two
codes: a geography roll-up equal to total revenue, a segment alias. The first by code
order (income-statement codes preferred) is kept and the other is recorded as a duplicate.
Otherwise one inflection occupies two rows and pushes something genuinely new off the
table.

Everything held out is listed with its reason, and the note prints the reason counts. A
scan that silently narrows its own universe is not auditable.

## 6. Comp effects: earned versus handed over

`acceleration_t = growth_t - growth_{t-1}`. A large positive acceleration can therefore
arise with nothing improving at all, if `growth_{t-1}` was unusually depressed. The mirror
case makes a deceleration look worse than the business deserves.

Distinguishing the two is normally an analyst judgement call. The engine makes it
mechanical by comparing the base growth rate against the quartiles of the series' own
growth history:

- `easy_base` - acceleration is positive and `growth_{t-1}` sits at or below the first
  quartile of that series' growth history. Part of the move was handed over by the
  comparison.
- `hard_base` - acceleration is negative and `growth_{t-1}` sits at or above the third
  quartile. Part of the deceleration is the comparison, not deterioration.

Quartiles are computed by linear interpolation on the sorted history, with no external
dependency, so the value is reproducible.

The flag does not change the ranking. It changes what the note is allowed to conclude, and
the narrative contract requires the prose to address it where it is set. Two inflections
of identical magnitude, one on an easy base and one on a hard base, support opposite
conclusions.

## 7. Streaks

The count of consecutive most-recent quarters whose acceleration carries the same sign.

This is the cheapest available correction for the biggest weakness of a single-quarter
signal. One quarter of deceleration is as likely to be noise as trend. Three consecutive
quarters of deceleration is a trajectory, and it is often a more important finding than a
larger single-quarter move elsewhere on the list. The streak sits in its own column so a
reader can weigh persistence against magnitude directly.

## 8. Universe screening

`search_lineitem(queries="all")` returns everything KPI-tagged, which for a large company
runs to a couple of hundred rows. A meaningful fraction of those are not raw series:

| Screened out | Example | Why |
|---|---|---|
| Pre-derived growth and delta companions | `Y/Y - Total Revenue`, `Q/Q - Opex`, `Change in bps (Y/Y) - Gross Profit`, `Incremental Margin (Y/Y) - EBIT`, `% FX (Y/Y) - Total Revenue` | Already a difference. Accelerating a difference of a difference produces a meaningless number that ranks highly because it is volatile |
| Accounting identities and market artefacts | `Total Liabilities & Shareholders Equity`, `Stock Split Multiplier`, `Effect of Foreign Exchange on Cash`, `Average Share Price` | Cannot inflect in a way that says anything about the business |
| Reported twin of an adjusted line | `EBIT` where `EBIT (Ex-1x Items)` exists | Same line measured twice. One inflection, two rows |
| Monthly or weekly sub-period slices | `Sales - Month 3`, `Four Weeks Ended` | A slice of a line already read quarterly. No independent signal, and on a 52/53-week calendar the trading-week count and holiday placement shift year over year, so a monthly growth rate can accelerate on the calendar alone while the quarterly comparison absorbs it |

Detection is by code suffix (`-Y`, `-Q`, `-BY`, `-M1`) and by name pattern, so it holds up
as the dictionary grows. Reported/adjusted collapsing normalises the name by stripping the
adjustment token and prefers the member tagged `adjusted`.

**The exception that matters.** A `-Y` companion whose underlying level is NOT disclosed
anywhere in the universe is the company's only window onto that metric. Several widely
followed KPIs are published as a growth rate and nothing else. Dropping them on the
general rule would make the line a reader looks for first structurally invisible, so the
engine keeps them, tags them `pre_growth`, and treats the reported value as the growth
rate itself: acceleration is then a single first difference rather than a difference of a
difference. Their heatmap rows are therefore one differencing step shallower than the rest
of the note, which is the honest trade for having the metric at all.

**Share-of-revenue twins.** `% Revenue - R&D` and `R&D` are both legitimate series, so
this is a ranking question rather than a screening one. When both clear materiality in the
same direction they are one finding in two rows, and the weaker is held out. When they
point in opposite directions, both are kept: a line growing while its share of revenue
falls is a real divergence.

This screening is correctness, not tidiness. Without it, a first run on a large company
fills the top of both ranked tables with second-difference artefacts.

## 9. Divergence rules

A fixed, finite rule table, evaluated by the engine. The model neither selects the rules
nor writes their headlines, so the same inputs always produce the same divergence set.

| Rule | Condition | What it is asking |
|---|---|---|
| `revenue_up_margin_down` | revenue z above materiality, gross margin z below negative materiality | Is the growth being bought? |
| `revenue_down_margin_up` | the mirror | Is mix or pricing improving as volume fades? |
| `revenue_up_opmargin_down` | revenue accelerating, operating margin decelerating | Is operating leverage going the wrong way? |
| `eps_outpacing_revenue` | EPS z exceeds revenue z by more than the strong threshold | Is the earnings move arising below the revenue line? |
| `cash_diverging_from_revenue` | revenue accelerating, free or operating cash flow decelerating | Is the revenue converting to cash? |
| `working_capital_outgrowing_revenue` | an inventory or receivables line at or above ten percent of revenue, growing more than ten percentage points faster than revenue | Quality of growth |
| `segment_divergence` | at least one segment above and one below the strong threshold, across genuine segment LEVEL lines only | Is the headline hiding two opposite stories? |

Each rule emits a headline and the participating metrics with their z-scores and
accelerations. The prose interprets; it does not choose.

`working_capital_outgrowing_revenue` carries a size condition for a reason learned in
testing: inventory at a software company is a rounding error tracking hardware, and firing
a quality-of-growth flag on it produces a confident-sounding false positive. A note that
has to talk its reader down from its own finding is worse than one that never made it.

**The banking set.** Five of the general rules hinge on gross margin or operating margin,
neither of which a bank reports, so on a bank the general set alone leaves the section empty
of anything but a tautology. Five banking rules cover it: loan balances accelerating while spread income
decelerates, balance growth accelerating as margin compresses, provisioning running ahead of
realised losses, realised losses running ahead of provisioning, and loan growth outpacing
deposit growth. They fire only when the sub-industry matches a financial classifier, which
is also the switch that disables the working-capital rule: a derivative receivable is a
trading mark, not a collections cycle, and neither receivables nor inventory are working
capital at a bank.

`segment_divergence` is restricted to segment revenue levels, excluding share-of-revenue
and currency-contribution lines. Those are two views of one phenomenon rather than two
segments disagreeing, and firing the rule on them asserts a conflict that does not exist.

## 10. The heatmap

For each ranked line item, the last eight quarters of acceleration, each expressed as a
z-score against that series' full acceleration history and binned into seven colour
buckets. Bin edges are fixed at ±0.5, ±1 and ±2 standard deviations.

Note the difference from the ranked tables: heatmap cells score every quarter against the
*whole* history including the latest, so the row reads as one internally consistent
sequence. The headline z-score in the tables excludes the latest observation from its own
baseline. Both are stated where they appear.

Each cell prints the **year-over-year growth rate** and is shaded by the acceleration
z-score. Printing the z-score alone gives a grid of numbers that mean nothing without the
methodology; printing growth alone loses which of those moves was unusual. Together, a row
reads as the metric's actual trajectory with the surprising quarters lit up.

The purpose is persistence at a glance. A row that is red across six cells is a different
proposition from a row that is red once, and no ranking column conveys that as fast as a
band of colour.

## 10b. Guidance corroboration

An acceleration measured from reported history is a fact about the past. The question that
follows is always whether it persists, and the cheapest evidence available is that the
company has already told you what it expects next quarter.

The test needs no interpretation. A guide targeting the next fiscal quarter implies a
growth rate against a year-ago base that is already on the books, and that implied rate
either continues the direction the line just moved in or reverses it:

    guide_implied_growth = (guide_midpoint - value[t-3]) / |value[t-3]|      (levels)
                         = (guide_midpoint - value[t-3]) x 100               (ratios, bps)
    guide_implied_accel  = guide_implied_growth - growth_latest

    corroborates : sign(guide_implied_accel) == sign(acceleration_latest)
    contradicts  : signs differ and |guide_implied_accel| exceeds the tolerance
    neutral      : |guide_implied_accel| below the tolerance

Contradictions sort first. A line that just accelerated into a guide implying deceleration
is the most informative row the scan can produce, because the data and the company
disagree and one of them is going to be wrong.

Three parsing hazards, all handled in the engine because all three are easy to get wrong:

- **`SCALE` is a separate field from `GUIDE_TEXT`.** A guide of `"$100000 - +/- 2%"` at
  `SCALE` `10^6` is a hundred billion dollars. Parsed from the string alone it is
  a hundred thousand, and every comparison built on it is wrong by six orders of
  magnitude.
- **Qualitative guides are not numbers.** `"Low 50s"`, `"Mid 70s"` and `"Upper 40s"` are
  guides a company genuinely gives. Extracting the digits from them invents a precision
  management withheld, so they are marked unparsed and reported as unparsed.
- **Horizon must match.** Only guides targeting the next fiscal quarter are scored. Annual
  and long-term guides are listed for context and never compared against a quarterly base.

Qualitative outlook notes are reproduced verbatim as data. They are quoted, never followed
as instructions, and never turned into a figure.

## 10b2. Deep-dive slot allocation

The three write-ups are chosen by absolute z, at most one per concept family (the ranked
tables may show a family twice, as corroboration).

The reason is an effect seen live: a gain on strategic investments lifted earnings per
share, net income margin, pre-tax profit and adjusted pre-tax profit in one quarter, and
all four ranked near the top. Every one of those rankings is arithmetically right. Spending
the write-ups on them is still wrong, because the reader learns one thing and loses the
chance to learn others.

Families are coarse by design and matched on the line-item name: earnings, operating
profit, revenue, gross margin, cash, working capital, opex, balance sheet, obligations,
plus spread income, credit, capital, balances, fee income and insurance for financials and
volumes and realisation for energy, healthcare and retail.

**The fallback matters as much as the table.** Keying it on a fixed number of leading
characters would make the cap inert: "Other Income" and "Other Income (FTE)" differ inside
such a window, so they would read as two concepts and one event could take several slots.
The fallback groups on the first two significant words before any separator, so a line
buckets with its own variants. The
aim is to stop redundancy, not to model accounting. Where two members of one family do
appear, they read as corroboration of a single finding rather than as two findings.

A guidance verdict is held to the same materiality floor as a ranking, for the same reason:
a guide implying a one-point change in a share count is not contradicting anything, and a
contradiction table that lists it alongside a genuine reversal trains the reader to skim.
Within each verdict, rows sort by the size of the implied reversal, because that is the row
worth reading and alphabetical order buries it.

## 10c. The forward bar

Every deep dive states next quarter's comp as arithmetic rather than as a worry. The
year-ago base for the next quarter is `value[t-3]`, which is already reported, so two
numbers follow directly:

    level_to_hold_growth  = value[t-3] x (1 + growth_latest)     (levels)
    growth_if_level_flat  = (value[t] - value[t-3]) / |value[t-3]|
    comp_contribution     = growth_if_level_flat - growth_latest

The first is the bar: what next quarter must deliver for the current growth rate to
survive. The second is what happens if the business does nothing at all and simply repeats
this quarter's level. The gap between them is precisely how much of next quarter's growth
the comparison supplies on its own, which is what every argument about durability is
actually about, usually without anyone putting a number on it.

Where a guide exists for the same line, the two sit side by side: a guide below the
hold-the-rate bar is management saying the deceleration continues, quantified, before a
word of the call has been interpreted.

## 10d. Source provenance

The MCP reports where each observation came from: a financial release, an SEC filing, a
transcript, or a provider calculation. A segment figure taken from spoken remarks on a call
is a softer fact than one taken from a filed statement, and an inflection resting on it
deserves to be read with that in mind.

Each series is tagged with the **weakest** source class anywhere in its history, not the
most common one. If two of thirteen quarters came from a call, the series is only as hard
as those two quarters, and averaging that away would be exactly the wrong summary.

## 10a. Scanned and quiet

Materiality discipline creates a reporting gap: the metric a reader always checks may
simply not have inflected, and a note that says nothing about it reads as a note that
forgot it. The engine therefore collects the headline and driver-tier lines (`top_kpi`,
`earnings_flash_kpi`, `company_kpi`, `driver_kpi`) that were scanned, cleared the data
gates, and did not clear materiality, and lists up to five of them by descending
absolute z with their growth and acceleration.

This costs nothing in rigour and closes the gap: absence from the ranked tables becomes an
explicit finding rather than an unexplained silence.

## 10f. The two-pass universe

`search_lineitem(queries="all")` returns KPI-TAGGED items only. That is documented MCP
behaviour, and taking it as the universe is the single largest coverage error this skill
could make, because the untagged tail is not marginal. A live dictionary for a large
semiconductor company hides all of the following behind an empty `tags` field:

| Untagged line | Example |
|---|---|
| Unit volumes by product generation | five separate unit lines, one per product |
| Revenue from individually disclosed large customers | two named-customer lines |
| Revenue by product line | four product-line revenue lines |
| Headcount by function | R&D and S&M employees |

Per-SKU volumes and customer concentration are not obscure extras for a semiconductor
analyst; they are close to the centre of the debate. A scan blind to them is not a scan of
everything.

So the universe is built in two passes: the tag listing, plus a keyword sweep with no
filter. Three universal banks (financial, volume and pricing, operating) run for every
company, and a fourth is chosen by matching the MCP's own `insync_subindustry` string
against a fixed sector map, so a bank choice is data-driven rather than a judgement call.

Two properties matter more than breadth. The banks are **fixed in the engine**, so two
scans of one company examine the same universe and their rankings are comparable; a
universe improvised per run makes the skill unrepeatable. And keyword search is
**relevance-ranked and noisy**, which is harmless here: anything the tag listing already
held dedupes away on code, and anything genuinely irrelevant fails the history and
materiality gates later. What survives the intersection is the untagged tail.

The cost is a handful of extra fetch calls. The alternative is a skill whose central claim
is false.

## 10g. Management commentary

The engine measures. It cannot explain, and the company generally did explain, on the
record, in the same week the numbers landed. So after the ranking is fixed, the transcript
and release for that quarter are parsed with a prompt that names each deep-dive metric
explicitly, and the speaker's own words are quoted beside each row.

This is the step that most often reverses a reading rather than decorating it. In live
testing a receivables line accelerating far faster than revenue read as a deteriorating
collection cycle until the transcript supplied the reason: extended payment terms on
multi-quarter shipments to investment-grade customers. Same number, opposite conclusion.
The engine found the anomaly and could not have resolved it.

Three constraints hold this material in place:

- **Quoted, not paraphrased.** A paraphrased quotation is not a quotation, and rewriting a
  chief executive into the note's register quietly converts their claim into ours.
- **Data, not instruction.** Transcript text is an external document. A directive inside it
  is ignored; a claim inside it is attributed, never asserted.
- **Never an engine figure.** A number management speaks stays inside the quotation marks.
  It does not enter a table, a ranking, or the model's prose.

The house style rules on advisory language, punctuation and currency symbols are scoped to
the note's own voice for the same reason. A speaker quoting a dollar figure in billions or saying
"we would" has not broken our style guide, and censoring them to satisfy it would be
the wrong trade in both directions.

## 10h. Provenance

Every ranked figure is listed with its LTCODE, its UIC, its quarter span, and the document
classes it came from counted by quarter. The purpose is auditability at the level of one
number: a reader who doubts a single row can pull that exact line item instead of having to
trust or re-derive the whole scan.

The ranking deliberately does not adjust for source quality. A line carrying transcript
quarters is a softer fact than one sourced wholly from filings, and that judgement belongs
to the reader, who now has what they need to make it.

## 10e. Where the boundary sits

Every skill claims its numbers come from code. The question is where the boundary actually
runs, because a boundary that leaks in one place leaks entirely. In this skill the model
does exactly four things, none of them arithmetic:

1. resolves the ticker and reads back `latest_fiscal_quarter`
2. issues the MCP calls the engine's `plan` command specifies
3. writes the response files to disk unmodified
4. writes prose containing no digits

Everything else is a command. Which quarters to request, which codes, whether the fetch
covered the plan, whether the quarter is reported, every growth rate, acceleration,
z-score, streak, flag, rank, divergence, heatmap cell, forward bar, guidance verdict, and
the chat summary itself.

Three gates exist specifically to hold that boundary rather than to check the maths:

- **`verify`** audits coverage before anything is computed. Left to the model, "did I fetch
  everything" is an eyeball judgement over a thousand rows, and a batch lost to a timeout
  looks exactly like a company that does not disclose that line.
- **`format_lint --chat`** holds the chat summary to the same standard as the page. The
  rendered note was always gated; the sentence the analyst reads first was not.
- **The direction check** requires each deep dive to state, in words, the direction the
  engine found for its row. This is the one error class arithmetic cannot catch: a
  narrative written against the wrong row passes every numeric test, because its figures
  all trace correctly.

## 11. Constants

| Constant | Value | Meaning |
|---|---|---|
| `LOOKBACK_QUARTERS` | 13 | Quarters requested. Yields eight acceleration observations |
| `MIN_Q_FOR_RANK` | 9 | Below this, appendix only |
| `MIN_Q_FOR_FULL_CONF` | 11 | Below this, ranked with a `short history` marker |
| `MIN_ACCEL_OBS_FOR_Z` | 4 | Prior acceleration points needed for a z-score |
| `BASE_FLOOR_FRAC` | 0.01 | Year-ago base below this fraction of series peak is unstable |
| `MATERIAL_Z` | 0.75 | Below this absolute z, no material inflection |
| `MIN_ABS_ACCEL_PP` | 1.0 | Economic-significance floor for levels and pre-computed growth rates |
| `MIN_ABS_ACCEL_BPS` | 25.0 | Economic-significance floor for margins and ratios |
| `STRONG_Z` | 1.0 | Threshold used by the divergence rules |
| `MAX_STALE_QUARTERS` | 0 | A ranked line must report in the scan quarter itself |
| `STEP_ZERO_DELTA_FRAC` | 0.6 | Share of exactly-zero sequential changes that marks a step series |
| `HEATMAP_QUARTERS` | 8 | Heatmap width |
| `TOP_N` | 5 | Maximum rows per ranked table |
| `DEEP_DIVE_N` | 3 | Deep dives, chosen by absolute z across both directions |
| `QUIET_ROSTER_N` | 5 | Headline lines listed as scanned and quiet |
| `GUIDE_TOLERANCE_PP` | 1.0 | Below this implied change, a guide is neither corroboration nor contradiction |

Changing any of these changes published rankings, so a change bumps the skill version.

## 12. Known failure modes

**Restatements and segment re-cuts.** When a company re-segments, the new series begins
with a short history and the old one stops. The engine sees two series, both correctly, and
holds the short one out. That is the right behaviour but it means a genuine inflection in a
newly disclosed segment is invisible for two years.

**A single distorted quarter poisons two readings.** A one-off charge that craters a
margin for one quarter produces an outsized acceleration when it happens and an outsized
opposite acceleration a year later. The `hard_base` and `easy_base` flags catch the second
of these, which is the more misleading one. Read a flagged row with that in mind.

**Metronomic series are sensitive.** A tiny standard deviation makes a small absolute move
score highly. The economic-significance floor removes the worst of this, and the raw
acceleration is always printed beside the z-score so a reader can judge the rest.

**Guidance covers few lines.** Companies guide revenue, a margin and perhaps opex. The
great majority of the scanned universe has no guide to be tested against, so the
corroboration section is necessarily narrow. It is narrow and real rather than broad and
inferred.

**Small lines rank equally.** The engine does not weight by size. A minor disclosed metric
inflecting sharply outranks a large one inflecting mildly. This is deliberate, since size
weighting would rebuild exactly the blind spot the scan exists to remove, but it means the
reader supplies the materiality judgement. Rather than weight by size, flow lines below one percent of revenue are TAGGED. The reader
gets the materiality context; the ranking stays blind to it. Balance-sheet stocks are not
tagged at all, because a stock is not comparable to one quarter of a flow and a
share-of-revenue column would invite exactly that comparison.
