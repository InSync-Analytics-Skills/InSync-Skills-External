# Pipeline contracts

Three files drive the renderer. It reads nothing else and holds no literals: no ticker,
no period, no sector, no line item, no multiple, no sentence. Anything that appears on
the page and is not page furniture arrives through one of these files.

`references/check_contract.py` verifies a working directory against this contract before a
build. Run it whenever you change the compute step.

**Two files are written BY the pipeline rather than read by it**, and both are inputs to a
gate rather than to the page:

| file | written by | read by | if absent |
|---|---|---|---|
| `narrative_figures` (a key inside `packet_computed.json`) | `compute.py --figures` | pass 3 | pass 3 REFUSES to run and names the step |
| `printed_figures.json` | `render_packet.py`, from inside the formatter | pass 3's precision check | the check reports that it did not run; under `--strict-precision` it FAILS |

Neither is a hand-authored file and neither belongs in version control with a packet. They
exist because a gate cannot check a claim it has no record of: the figure set records what
the computed file CONTAINS, the printed set records what the page SHOWS, and the two answer
different questions. An absent one is always reported, never quietly skipped.

---

## `packet_computed.json`

### Read directly by the renderer

```
meta            see below
spine[]         the model spine, in print order          section 3
model[]         the data for the spine and its margins   section 3
segments[]      segment revenue                          section 5
seg_ebit[]      segment profit                           section 5
corp            {cells}   corporate and eliminations     section 5 note
kpis[]          operating drivers                        section 5
kpi_growth_sub  codes whose driver row carries a Y/Y sub-row
dispersion[]    consensus spread on the target quarter   section 4
disp_keep[]     which dispersion codes print, in order
revisions[]     target-quarter consensus by vintage      section 6
rev_keep[]      which revision ltcodes print
rev_buckets     {bucket -> real ISO date}
rev_cols[]      the vintage columns, in display order
guide_vs_cons[] every live guided item                   section 2
reaction[]      the last eight prints                    section 9
move_band       the empirical move statistics            section 9 stats row
valuation[]     the sector lens, in sector order         section 7
val_sector      display name used in the section 7 note
val_excluded[]  computed but not shown, with reasons
tiles[]         the headline strip, 12 entries           section 1
seg_rev_label   band label above segment revenue
seg_profit_label band label above segment profit
seg_margin_label sub-row label under each segment profit row
section_labels  OPTIONAL heading overrides, keyed by section number
estimate_word   OPTIONAL, default "Consensus", the word for the forward number
```

**Sub-object field names are exact.** `move_band` must supply `mean_abs_1d`,
`median_abs_1d`, `min_abs_1d`, `max_abs_1d`, `mean_rel_1d`, `mean_abs_5d`, `up`, `down`.
The renderer reads them through a local alias, so a wrong name does not throw; it renders
a dash in the reaction stats row and validator pass 5 will not fire on a single row.
`check_contract.py` resolves the alias and fails the build on any of them.

`guide_vs_cons` may be an **empty array**, and that is the coded path for an issuer that
publishes no numeric guidance: section 2 then renders its heading and `notes.guidance`
alone, with no table. Do not substitute inferred guidance to fill the grid.

**`segments` and `seg_ebit` may each be an empty array too**, and an empty one suppresses its
BAND LABEL rather than printing the words over nothing. An issuer that discloses revenue by
geography and no segment profit is the ordinary case. The renderer appends the absence to `notes.segment` itself,
the way `dropNote()` appends a prune, so the authored note supplies the read and not the
inventory.

`section_labels` is `{"<n>": {"title": "...", "sub": "..."}}`; either key may be omitted
and the renderer keeps that default. `estimate_word` replaces the masthead field label and
the section 2 column header in one place. Both exist so a heading never asserts something
the data does not support; with a single estimate contributor, `Consensus Dispersion` and
`where the street agrees` are both false and both must be overridden.

### Retained for provenance, not rendered

```
margin_basis    {line -> denominator}   WHICH revenue each margin uses
val_raw         every multiple pulled, in the lens or not
val_sector_key  the sector key the lens came from
warnings[]      coded degradation notes
```

These are not optional. They are how a reviewer checks the packet without re-running it,
and `margin_basis` in particular is the answer to the most common question a reader has
about section 5.

### `meta`

```
ticker, company, ticker_id
target          target quarter, e.g. "3Q26"
target_qend     ISO date of the quarter end
call_date       ISO date of the upcoming print
last_reported   the most recent reported quarter
last_print_date ISO date of that print
prepared, asof_estimates, asof_valuation    display strings for the masthead
fy_end          display string, e.g. "31 Dec"
hist[4]         the four reported quarters, oldest first
next_q          the quarter after the target
fy_prev, fy0, fy1     annuals as BARE 4-digit years, never "FY26"
sector_key      drives spine, drivers and lens
periodicity     OPTIONAL, 'quarterly' (default), 'semi_annual' or 'annual'
reaction_basis  OPTIONAL, 'intraday' (default) or 'after_close', drives the section 9
                price column headers and the section subtitle
ccy             OPTIONAL currency SYMBOL for the three money formats, default '$'
ccy_label       OPTIONAL masthead Figures field, default derived from money_scale
money_scale     OPTIONAL, 'B' (default) or 'M', the magnitude of every '$M' cell
prior_label     OPTIONAL, the period the section 4 comparison column names
```

**The five optional meta fields, and when each is not optional in practice.** With all
five absent the packet renders exactly as it did before they existed, which is why a
large-cap USD quarterly issuer needs none of them.

| field | supply it when | what goes wrong without it |
|---|---|---|
| `ccy` | the issuer does not report in dollars | every money cell is stamped `$` |
| `ccy_label` | as above | the masthead states the scale in dollars |
| `money_scale` | the issuer's spine lines run below roughly `$1,000M` | every `$M` cell prints inside two decimal places of a `$B` string, rows collide, and a movement table shows no movement |
| `prior_label` | `hist` is not four quarters | the section 4 header names `hist[0]`, two years before the target on a half-year axis, while the figure beneath it is a one-year growth rate |
| `periodicity` | the axis is not quarterly | nothing downstream knows the grid is not four quarters |
| `reaction_basis` | the release lands after the close, which is most US issuers | the column headed `Close on print` holds the NEXT session's close, and rule 8 calls a heading that misdescribes its table worse than a missing note |

**`money_scale`, and why it is not cosmetic.** `'B'` divides a `$M` value by 1000 and prints
two decimals with a `B`; `'M'` prints it as held, one decimal, with an `M`. On a small
issuer at `'B'` scale **many money cells print a string identical to another cell in the
same row**: two consecutive quarters of `$30.0M` and `$34.0M` both print `$0.03B`, a
`$2.0M` gross profit prints `$0.00B`, a revisions table prints the same string in every
vintage column beside a non-zero delta, and a guidance band can print beside a consensus
that is above its high end, which pass 7 reports. `ccy` sets the symbol and `ccy_label` relabels the masthead; neither ever
touched the divisor. One scale for the whole document, exactly as with the symbol, because a
packet that mixes magnitudes down a column is what pass 7's V4 exists to catch.
`setScale()` THROWS on any value other than `'B'` or `'M'`, and `check_contract.py` both
validates a supplied value and NOTES an absent one where the spine's largest money level
would print below `$1.00B`.

`ccy` is a symbol, not a code: `'€'`, not `'EUR'`. It sets the symbol for `'$M'`, `'$'`
and `'$3'` for the whole document; there is deliberately no per-row override, because a
packet that mixes symbols is worse than one that states a single symbol in the field
strip. **`tiles[].value` is pre-formatted text and carries its own symbol**, so a
non-dollar packet must write the symbol into the twelve tile values as well.

The renderer derives every column header from `hist`, `target`, `next_q`, `fy_prev`,
`fy0` and `fy1`. It adds the `FY` prefix for display. Supplying `"FY26"` as an input
produces `FYFY26`.

### Row shapes

```
spine[]         {code, name, sub, sl}
                sub = 'yoy'        -> a Y/Y % sub-row
                sub = '<code>-M'   -> a margin sub-row reading that code
                sub = null         -> no sub-row
                sl                 -> the sub-row label, when sub is a code
model[]         {code, name, fmt, cells{period->value}, growth{period->value}, neg_ok?}
                one entry per spine code, PLUS one per margin code named in sub
segments[]      {code, name, cells, growth}
seg_ebit[]      {code, name, cells, growth, margin}
kpis[]          {code, name, fmt, cells, growth}
dispersion[]    {code, name, fmt, mean, min, max, disp, n, upd, prior, yoy, neg_ok?}
revisions[]     {ltcode, name, fmt, <one key per rev_cols entry>, delta, tag}
rev_cols[]      {key, label, base?}    base:true marks the delta comparison column
guide_vs_cons[] {item, target, text, lo, hi, cons, unit, approx, pos, sign, date, src}
reaction[]      {q, date, w_pre, w_day, m0, r0, m1, m5, r5}
valuation[]     {name, key, ntm, ltm, y1{mean,median,min,max,n}, y3{...},
                 vs_1y_mean, vs_3y_mean}
val_excluded[]  {name, key, reason}
tiles[]         {label, value, sign?}   value is ALREADY FORMATTED text
```

**`neg_ok: true`** marks a line whose negative value is not bad news, and renders it in
ink rather than red. Net debt below zero is net CASH, and the sign rule painted a strong
balance sheet in the colour reserved for a loss. Set it only where the sign genuinely
carries no bad news: on net debt, net cash and a net-liability line the issuer runs
negative by design. Absent, the row colours on sign exactly as before.

`fmt` is one of `'$M' | '$' | '$3' | '%' | '%2' | 'x' | 'k' | '#B'`. **`'%2'` is a rate at
TWO decimals**, for a line that moves in basis points: a bank's net interest margin, an
insurer's combined ratio, a REIT's occupancy, a platform's take rate. Measured on a
money-centre bank, a nine-quarter NIM series holding seven distinct values printed three
strings at `'%'`, so every cell in the row shared a printed string with another cell.
`check_contract.py` NOTES any `'%'` row where two materially different values collide, and
names it, which is how an operator finds the rows that want `'%2'`. Both formats are
rates, so a year-on-year change on either is in basis points. **`'$M'` values are held in
MILLIONS and rendered as `$B`.** `'k'` values are held in thousands and rendered with a
separator, which is how a count of 505 is carried as `0.505`.

### The fields that carry a decision

**`tiles[]` is the headline strip and is LOCKED.** Twelve entries, rendered as two rows
of six. Row 1 is always consensus EPS, guided EPS, consensus revenue, consensus EBIT,
revenue Y/Y, EBIT Y/Y. Row 2 is always the sector's two lead multiples, dividend yield,
FCF yield, leverage, market cap. Only the two multiples change with the sector. `value`
is pre-formatted text because the tile is the one place the packet shows a figure without
a column to inherit a format from.

**`sign` is REQUIRED on any tile whose value prints a negative number, and optional
everywhere else.** This is a gate in `check_contract.py`, not a convention. The renderer
colours a tile only from `sign`, and validator pass 1 requires that no negative number
renders black anywhere in the document, so on a loss-making issuer the four negative tiles
(consensus EPS, consensus EBIT, an EBIT Y/Y, FCF yield) fail pass 1 with `negative
rendered black` without it. On a tile derived through a `from` spec, write `"sign": "auto"` in `plan.json`
and the builder sets it from the resolved value. A tile whose value is a word (`not
guided`, `net cash`, `none`) is exempt.

**A Y/Y tile READS ITS RATE through `from`.** `from` takes
`model:<code>:<period>` for a cell and **`growth:<code>:<period>` for the derived rate**, so
both Y/Y tiles resolve like every other tile and neither is typed. A verbatim `value` still works where no code
carries the rate; `sign: "auto"` lets the builder set the colour from the resolved value.
Where the prior-year base is a loss, the derived rate is positive by arithmetic and says
nothing a reader can use: write what happened (`loss widens`) rather than the rate.

**`guide_vs_cons[].approx`** is true where the company published a point approximation
such as `~4%` rather than a band. An approximate guide is not rendered as a band, and
`sign` is set from a tolerance equal to the disclosure's precision rather than from a
strict inequality, so a consensus six basis points from a `~4%` guide reads `At guide`
and not `Above high`. **The tolerance is one full unit of the guide's last stated digit**:
`~4%` is 3 to 5, `about 70%` is 69 to 71, `about $9.10` is 9.09 to 9.11, read
from the number in `text` at the row's scale. A consensus exactly on the edge is `At guide`.
A one-value guide (`lo` equals `hi`) is graded the same way and prints one value, not a
band.

**A "centre +/- tolerance" guide**, such as `$31.5B +/- 2%`, goes into
`raw_mcp.json` as `mid` with `tol_pct` (per cent of mid) or `tol_abs` (in the row's unit)
instead of `lo` and `hi`; the builder derives the band, so the model never works the edges
out itself. Give one tolerance, not both. On a percentage row `+/- 1%` is one point: write
`tol_abs`, because `tol_pct` there is refused as ambiguous. A row that carries `lo`/`hi`
AND `mid` with a tolerance is kept when the two agree and dropped with a `warnings` line
when they do not. `sign` is `1` when consensus sits above the guide, `-1` below, `0`
within; the renderer colours from `sign`, never from the `pos` text.

**`rev_cols[]` exists because the four canonical buckets are not always there.** Order
the columns by real date, label them from the bucket they hold, and mark the comparison
column with `base:true`. If the feed published nothing between the print and today, say
so in the note rather than duplicating a column.

**Two key names carry behaviour, so they are reserved.** `base: true` marks the column the
delta is measured FROM, and `compute.py` R8 reads it. And the renderer bolds the
column whose key is exactly `latest`, so a bucket named `current` or `now` silently gets no
bolded column. Name the newest bucket `latest`, mark the comparison column `base: true`,
and give `rev_buckets` a real ISO date for every key: R8 measures from `base` to the newest
BY DATE, so the dates are what make the delta checkable.

**`valuation[]` is the sector lens and nothing else**, in sector order. A multiple that
is computed but outside the lens goes to `val_excluded` with a reason that reaches the
page, and stays in `val_raw`. A multiple with no forward variant carries `ntm: null`.
**A multiple must never be silently absent**: if it is not in `valuation` it is in
`val_excluded`.

**`val_excluded` lists MULTIPLES, not adjustment bases.** `P/E` and `P/E (Adj)` are
one multiple on two bases, and rule 3 already governs which basis a packet uses: adjusted
first, GAAP with an asterisk and a note. Putting the GAAP sibling of a shown multiple into
`val_excluded` makes the section 7 note read "P/E, unadjusted and EV/EBITDA, unadjusted ...
are computed but not shown" beside a table showing P/E and EV/EBITDA - a distinction the
reader cannot act on, over a multiple the packet does show. Exclude a multiple the LENS does
not carry: P/B on an internet platform, EV/EBITDA on a bank.

`vs_1y_mean` is `(ltm / y1.mean - 1) x 100`, LTM against the trailing average, never NTM.
The column header says so.

---

## `peers.json`

**Built, not typed.** `build_computed.py --build` writes this file from `plan.peers` plus
`raw_mcp.json`'s `peers` block. The split is the same as everywhere else:

```
plan.peers      the band labels, the lens, val_metrics / growth_metrics / op_metrics,
                and `rows` carrying IDENTITY ONLY: {t, name, id, lead} per ticker
raw.peers       {"<TICKER>": {the feed's own levels, plus comp_basis and any <key>_note}}
```

Derived here, and **refused if the raw block supplies them**, because a ratio in the raw
file is a ratio somebody computed by hand: `rev_growth`, `eps_growth`, the lead multiple's
`<stem>_vs_1y`, and `peg`. PEG additionally applies R17's rule at build time rather than
reporting it afterwards: on a near-zero or negative growth rate the cell carries
`not meaningful` instead of a number, which is every row of a loss-making set.

Four other refusals, all naming the field: a `rows` entry the raw block does not carry, a
`comp` populated on some rows and not others (the like-for-like row is all-or-nothing), an
empty `rows`, and a `rows` with no `lead`. A metric key that no row can fill is reported as
a build note rather than refused, because a genuinely uncoded peer metric is a real fact
about the set and the prune already discloses it.

```
asof_val, asof_est     display strings
sector_label           printed in the section 8 subtitle
val_label, growth_label, op_label      band labels
growth_metric          the earnings measure the growth band uses, e.g. "AFFO/share"
lens[]                 the multiple names, for the report
val_metrics[]          {label, key, fmt, lead?}
growth_metrics[]       {label, key, fmt, lead?}
op_metrics[]           {label, key, fmt, lead?}
rows[]                 one per ticker, lead company first
```

`fmt` is one of `money | mult | pct1 | pct100 | pctchg | ratio | raw`. `pct100` takes a
decimal and prints a percentage; `pct1` takes percentage points. `mult` applies the house
multiple rule per value: two decimals below `5.00x`, one above.

### Row fields

```
t, name, id, lead
mcap
pe_ntm, pe_ltm, pe1y_mean          the lead multiple and its history, POSITIONAL names
evebitda, evsales, pb              other lens multiples, as the lens requires
divy, fcfy, ebitm                  decimals: 0.0141 prints 1.4%  (fmt pct100)
comp, comp_basis                   same-store or organic, where a common basis exists
cy26_rev, cy27_rev                 LEVELS, in millions, never growth rates
cy26_eps, cy27_eps                 LEVELS, per share in the reporting currency
next, next_q
pe_vs_1y, eps_growth, rev_growth, peg      COMPUTED HERE, not in the renderer
                                   the three growth fields are PERCENTAGE POINTS
```

**`cy26_*` is the CURRENT calendar year and `cy27_*` the NEXT** (whatever the year: the band
labels take their year from `meta.prepared`). **The four `cy26_*` / `cy27_*` fields are LEVELS, not growth rates**, although the peer band
labels read "CY26 revenue growth %", which invites populating them as percentages. `compute.py` R14 and R15 re-derive
`eps_growth` and `rev_growth` from them as `(cy27 / cy26 - 1) * 100`, so a percentage in
those fields both puts a wrong number in section 8 and produces two audit findings per peer
that read as false alarms. If you want a growth row on the page, put the levels in these
four and display `rev_growth` / `eps_growth`.

**`pe_ntm`, `pe_ltm`, `pe1y_mean` and `pe_vs_1y` are POSITIONS, not P/E.** They hold
whichever multiple the sector lens leads with, so on a `software_growth` issuer they hold
EV/Sales. The names are historical and they are a
trap: nothing enforces them, so an author who names the lead keys `evsales_ltm` passes
`check_contract.py` and silently loses R16, the only check on the peer LTM-versus-average
comparison. `compute.py` resolves those keys from the `val_metrics` entry
carrying `lead: true`, deriving the LTM / 1-year / comparison names from the lead key's own
stem and falling back to the `pe_*` triple, so either naming audits correctly. Prefer
naming them for the multiple they hold and marking `lead: true`; the fallback exists so an
older input file keeps working, not as the recommended shape.

**`val_metrics` is built from the same sector lens as `valuation[]`.** The lead multiple,
first in sector order, additionally carries LTM, the 1-year average and the LTM-vs-1-year
comparison. Later entries carry NTM only, except a multiple with no forward variant such
as P/B, which carries LTM. Market capitalisation and the two yields are not multiples and
appear for every sector.

**A dropped cell carries its reason.** Where a value is null because the denominator was
negative or absent, set `<key>_note` on that row to a short phrase: `negative book`,
`not meaningful`. The renderer prints the phrase in grey in place of the number. A null
with no note prints an en dash. Never print a negative multiple.

**Every peer `fmt` colours on sign.** `raw` formats through `num(v, 2)` and is a plain
two-decimal number with the house minus sign; use it for a per-share level, which is what it
is for.

**On a loss-making set, `eps_growth` is a change measured off a negative base**, so it is
signed by `pct_change()` (SKILL.md rule 11) and a deepening loss reads negative and red.
Say so in `peers_caveat`: the revenue growth column is
the comparable measure on a pre-profit set.

**`calendarize: true` on every peer pull.** Without it, peers with different fiscal
year-ends are silently compared across different economic periods.

`comp_basis` is mandatory and is printed in the note. Where the metric is not coded on a
common basis across the set, set `comp` to null on every row: the prune removes the row,
`dropNote()` states why, and the like-for-like caveat is suppressed with it.

---

## `packet_narrative.json`

```
what_matters[5] {debate, detail}              the five pointers of section 1
risks[5]        {text, source}                one line each, source verbatim from InSync,
                                              and naming exactly ONE document
catalysts[5]    {text, source}
risks_total     how many were on the record before ranking to five
catalysts_total same
notes {
  guidance, model, dispersion, segment, drivers, revisions,
  valuation, valuation_read, valuation_excluded_reason,
  peers_read, peers_comparability, peers_caveat, reaction
}
document_figures[]   OPTIONAL, see below
```

All thirteen note keys are required. Notes are plain strings. Three of them are completed
at render time from computed data: the segment note gains the corporate and eliminations
figures, the valuation note gains the sector name and the excluded multiples, and the
peer note gains the like-for-like caveat when the comp row survives. The narrative
supplies the prose; the renderer supplies the numbers.

**`peers_comparability`** may be an **empty string** where the
peer set has no common comparability basis; the renderer then suppresses the clause and
the parenthesised basis list with it, so an empty string is a statement rather than a gap.

**There is no `mix` field and no mix column.** Never write prose about one: the reader cannot
see it.

### `document_figures`, the second-rung source declaration

```
document_figures[]   OPTIONAL, a list of figures a note, pointer OR SECTION 10 ITEM quotes
                     from a parsed InSync document rather than from packet_computed.json
```

Rule 1 says every number in your prose must exist in the computed file. There is one
legitimate exception the rule does not cover: a figure the structured feed does not carry
and the note quotes from a transcript, release or 10-Q, which rule 2's source ladder
explicitly permits.

Declare the figure exactly as the prose prints it. Every entry must also carry its source
inside the note or the item, which rule 2 requires anyway, so this adds a machine-readable
record of what came from where and is **not** an escape hatch for a number you cannot place.

**It covers section 10, and it normalises a unit word.** Pass 3 scans the ten section 10
risk and catalyst items as well as `notes` and `what_matters`, and both sides of the test go
through one normaliser that strips `million`, `billion`, `trillion`, `thousand`, `bn`, `mn`, `mm`,
`bps` and `k`, so write the entry naturally.

**A day of month is not a figure.** 'a pricing action effective 1 August' needs no
declaration: the scanner exempts day-then-month and month-then-day, and a four-digit year
was already exempt.

**Declare the form the PAGE shows, not the underlying value.** The figure set holds display
forms of computed values, so `3277` from a `$M` cell is in it while `$3.28B`, the string the
table actually prints, is not. A section 10 item that writes the raw value passes the gate
and disagrees with the table beside it, which is the one thing this section is for.

### Punctuation and precision

**No em dashes.** See rule 12 in `SKILL.md`.

**Ten of the thirteen notes close themselves; three are fragments.** `dropNote()` appends
its sentence with a single leading space, so a standalone note that ends without terminal
punctuation runs straight into the prune disclosure. But `valuation_excluded_reason`,
`peers_read` and `peers_comparability` are spliced into a longer sentence by the renderer,
which supplies the punctuation around them, so those three must NOT carry a terminal stop.
The rule per note:

| note | rendered as | ends with a stop | first character |
|---|---|---|---|
| `guidance`, `model`, `dispersion`, `segment`, `drivers`, `revisions`, `reaction` | its own sentence, then `dropNote()` | yes | **capital** |
| `valuation`, `valuation_read` | own sentences inside the section 7 note | yes | **capital** |
| `peers_read` | opens section 8's paragraph, followed by a `. ` or a `; ` | **no** | **capital** |
| `peers_comparability` | after `peers_read` and a `. `, then a parenthesised basis list | **no** | **capital** |
| `peers_caveat` | after a `; `, then `dropNote()` | yes | **lowercase** |
| `valuation_excluded_reason` | after `are computed but not shown, ` and before a `.` | **no** | **lowercase** |

`check_contract.py` enforces both directions: a missing stop on a standalone note and a
stray stop on a fragment.

**Leading case follows the splice position, and it is gated.** A note's first character is
not an authoring preference: the renderer decides whether that note opens a paragraph,
follows a full stop, or continues a sentence after a `; ` or a `, `. Only the last case takes
a lowercase opener, and only two notes are in it, so the rule is: **`peers_caveat` and
`valuation_excluded_reason` start lowercase; every other note starts with a capital** (a
leading digit, currency symbol or quarter literal counts as a capital).

`peers_read` OPENS section 8's paragraph, so it takes a capital even though it takes no stop;
`peers_comparability` splices after a `. `, so it takes a capital too. The terminal-stop set and the leading-case set are
different sets over the same notes, which is why one set could not express both:
`peers_read` takes no stop AND takes a capital.

`check_contract.py` now fails any note whose first character disagrees with its splice
position, naming each one and which way it is wrong.

**Quote numbers at the precision the table prints.** The tables render percentages to one
decimal. A note reading `0.96%` beside a cell reading `1.0%` reads as two figures.

### Notes and the prune

A note is written *before* the renderer knows which columns and rows survive `prune()`.
Two consequences the narrative step must respect:

- **Never write a note clause that assumes a specific column is on the page.** The
  renderer appends the prune disclosure itself via `dropNote()`; your sentence supplies
  the basis and the read, not the inventory.
- **A clause that depends on a prunable row must be gated in the renderer**, the way the
  peer note's like-for-like caveat is gated on the comp row surviving. If you add a note
  that leans on one row, add the gate in the same change, otherwise the packet ships a
  caveat about a row the reader cannot see.

### The no-fabrication gate

**Every number written in `notes` and `what_matters` must exist in
`packet_computed.json`.** Validator pass 3 tokenises both, and for each numeric token
searches the computed file at every rounding the packet uses and at `$B` scale. A number
you cannot source is a build failure, not a style note.

`risks` and `catalysts` are scanned by pass 3 like every other prose surface. A figure they quote from an InSync
document is declared in `document_figures`, and each item names that document in
`source`, which is why every one of them must have a source.

**`source` names exactly ONE document, and `check_contract.py` enforces it.** Packing two
into one field (`"<T> 2Q26 Earnings Transcript; <T> 2Q26 10-Q"`) makes the section 10
Source column wrap mid-token: the column is 842 dxa, 0.58 inch, so a two-document string
breaks and splits `10-Q` across two lines as `10-` and `Q`. It also defeats the source
sentence's de-duplication, because the joined string is unique and survives the set.

Where a risk genuinely rests on two documents, either **rank it as two items** or **name
the document that carries the quantified figure**, which is the one a reader would open. Do
not join them. The renderer still splits a joined string defensively when building the
summary sentence, so an older input degrades rather than breaks, but the per-item cell
cannot be rescued that way and the contract is the place to stop it.
