# House format, locked

Every rule here is enforced by `references/render_packet.py` or by
`references/validate_packet.py`. None of it is decided per run.

---

## Page

| | |
|---|---|
| Size | US Letter, 12,240 × 15,840 dxa (**not** A4, docx-js defaults to A4) |
| Margins | 720 dxa (0.50 in) all round |
| Text column | **10,800 dxa** (house geometry v2, the column every skill in the library uses) |
| Font | Calibri for body and tables; **Times New Roman for section headings AND for the masthead** (held in the single `MASTHEAD_FACE` constant). This is a declared exception to the library's Georgia masthead, recorded in `common/brand.json` under `masthead.face_overrides`: the packet's masthead shares the packet's own serif rather than importing a third face. The metric originals are named, not their Linux clones (Carlito, Liberation Serif), so one file renders the same faces on a Linux box and the real ones in Word. Liberation Serif is metric-faithful to Times New Roman in BOTH advance widths and vertical metrics (2048 em, hhea 1825 / -443 / 87, win 1825 / 443), so the alignment constants hold on either box - unlike Georgia, whose Linux stand-in DejaVu Serif has different metrics and moves both masthead edges |
| Body table text | 6.5 pt (size 13 half-points) |
| Sub-rows, sources | 5 pt (size 10) |
| Section heading | 11 pt bold (size 22), Times New Roman, over a 1.25 pt orange rule |
| Masthead | Times New Roman. Title 18 pt bold; brand line 16.5 pt fixed (12 pt on the two-line path); company line 14 pt stepping to a 12 pt floor, then an explicit two-line split at 10 pt; date lines 9 pt |

## The table-width invariant

**Every full-width table is exactly `TW` dxa wide**, and `TW` derives from the page margin rather than being a literal. `T(w, rows, opt)` throws if a width array does
not sum to it, and it checks *after* pruning, so a table that drops a column must have
redistributed that column's width:

```js
const PW=12240, PH=15840, MG=720, CW=PW-MG*2;   // 10,800
const TW=CW;
function T(w,rows,opt={}){
  ({w,rows}=prune(w,rows,opt));                 // may drop columns and rows
  const t=w.reduce((a,b)=>a+b,0);
  if(t!==TW) throw new Error('table width '+t+' != TW '+TW);
  return new Table({columnWidths:w,... rows:mkRows(rows,opt.keep)});}
```

Cells and rows are **descriptors** (`TC` / `R` / `HR` return plain objects), materialised
into docx objects inside `T()`. That indirection is what lets `T()` prune columns and set
`keepNext`, both need the final row count and column set, which only `T()` knows. Two
call sites build a `Table` directly rather than through `T()`, the tile rows and the
two-up risks/catalysts block, and those must call `mkRows()` themselves.

This is not cosmetic. It is what makes the packet read as one column instead of a stack
of boxes, and it is why the two-up risks/catalysts block must be built at `TW` with inner
halves of `(TW-gap)/2` rather than at the full text width. A build that violates it fails
rather than shipping a page where one table overhangs the rest.

Validator pass 2 measures the rendered PDF and requires one left edge and one right edge
across every page. A sub-hairline outlier (≤0.2 pt) on the two-up block is the inner
border's rounding and is tolerated.

## Colour, one meaning only

| Colour | Meaning |
|---|---|
| Green `1E7A34` | a positive change or margin |
| Red `B02A1F` | **any** negative number |
| Black `1A1A1A` | a level that is not negative; every heading; every label |
| Grey `6E6E6E` | sub-row labels, notes, source tags, never a value's sign |
| Orange `E8850C` | the rule under each heading, and in the masthead the `InSync` word, the ticker, the field labels and the rule under the block. **Never a number.** |
| Rule `BBBBBB` | under a header row |
| Rule `EEEEEE` | the hairline between body rows. **There are no vertical rules** |
| Band `EDEDED` | header rows |
| Band `F0E9D8` | in-table group bands |
| Zebra `F5F0E8` | alternate body rows, counted over body rows only so a band never takes a stripe |
| Tile `F6F3EE` in a `DDDDDD` box | the headline tile strip |

**Colour never encodes cheap/expensive, good/bad, or above/below guide.** Colouring a
premium to own-history red and a discount green renders a positive number red: two colour
languages on one page, and readers cannot tell which is in force. One language: sign.

**A negative number is never black.** Level cells use `sc(v) = v<0 ? RD : DK`. Validator
pass 1 reads every numeric span out of the rendered PDF with its RGB and fails on a green
negative, a red positive, or a black negative.

## Numbers

| Type | Rule |
|---|---|
| Money | **$B**, two decimals: `$187.32B`. Negatives in parentheses **and** red. |
| Per share | `$0.742`, three decimals where the consensus spread needs them |
| Percentages | **one decimal**, always: `25.3%`, `4.7%` |
| Change in a percentage | **basis points**: `+18 bps`. A margin's Y/Y is a spread, not a percent of a percent. |
| Multiples | two decimals below `5.00x`, one above: `1.28x`, `36.9x`. **Per value, not per column**: a peer row holding `21.8x` and `2.68x` is correct. **Sections 7 and 8 implement this rule**, in `xf()` and `xmul()`; everywhere else an `x`-formatted cell goes through `cell(v,'x')` in `fmt.py`, which is fixed at two decimals, so a book-to-bill or coverage ratio at or above `5.00x` prints `5.20x` in the section 3 grid and `5.2x` in the peer table. Recorded rather than fixed: `fmt.py` is shared by every table in the document, so changing it moves cells in sections 2, 3, 4 and 6 at once, a wider blast radius than the inconsistency costs, and most `x` lines sit well below `5.00x`. If it is ever changed, change it as its own release with the fixture matrix as the evidence |
| Counts | thousands separators, no decimals |
| Verbatim guidance text | **quoted as published, never re-rounded.** `3.50%` stays `3.50%`. |

Rounding is half-up (finance convention), not banker's. Python validators must use
`ROUND_HALF_UP` or they will disagree with the document on exact `.x25`/`.x5` ties, the
document is right in that disagreement.

## Punctuation

**No em dashes anywhere in the prose the packet renders**, and none in this skill.
Section headings, notes, pointers, risks and catalysts use a colon, a semicolon, a comma
or a full stop instead. The em dash character is reserved: it is one of the empty-cell
markers in `EMPTY_TXT` and one of the characters validator pass 5 scans for, so a stray
em dash in prose is a character the layout machinery is also using as a signal.

En dashes stay. A numeric range reads `$10,695 - $10,845` with an en dash, which is
standard finance typography and is not affected by this rule.

## Labels

- **Every label whose values are percentages carries `%`.** `Y/Y %`, `Dispersion %`,
  `EBIT margin %`, `LTM vs 1-yr %`.
- **Mixed-unit columns carry no unit.** `2Q27E vs 2Q26A` is percent for dollar lines and
  bps for margin lines; `Δ vs pre-print` likewise. Marking them would assert a unit the
  column does not hold.
- **A comparison column names both sides.** `LTM vs 1-yr` rather than `vs 1-yr`, so no
  one assumes the NTM figure beside it is what is being compared.
- **Peer rows lead with their unit** in the Metric column: `$ · Market capitalisation`,
  `x · NTM P/E (Adj)`, `% · FCF yield`, `ratio · NTM P/E ÷ EPS growth`.
- **Header alignment matches body alignment.** A right-aligned column gets a
  right-aligned header. `HR()` takes a per-column alignment array; never leave a table on
  the default.

## The masthead mark

The logo is sized by **height**, and its width follows the asset's own aspect ratio, read
from the file. Both dimensions were hardcoded once and every replacement logo of a
different shape came out stretched. If the asset changes, nothing else needs to.

**The height is a constant.** It was briefly derived from the resolved company-line
point size, which meant a long issuer name shrank the mark to a thumbnail beside an
18 pt title. The mark identifies the house, not the issuer, and its size must not carry
information about how long a company's name happens to be. The brand line above the
company line is fixed at **16.5 pt** for the same reason, per `brand.json`
(`brand_size_half_points: 33`) and the table below. Half a point matters
here: the masthead alignment tolerance IS 0.5 pt.

## Empty, dropped and not-meaningful

Three different things, three different renderings, and the difference is the whole point
of the prune:

| the cell is | renders as | counts as empty |
|---|---|---|
| absent from the feed | an en dash | yes, it can prune the row or column |
| a multiple dropped for a negative or absent denominator | its reason, in grey: `negative book` | no, it is an assertion |
| a statistic that exists but is not interpretable | `not meaningful` | no |
| a fact the packet is asserting | `none`, `no history`, `not comparable` | no |

A dropped multiple is never printed as a negative number and never as `n/m`. The reason
travels with the data, on the row, so the renderer prints it without knowing what it
means. See `pipeline-contracts.md`, `<key>_note`.

A growth rate off a negative or zero base is the fourth case and belongs in the first row
of the table: it is genuinely absent, an en dash, and it can prune the row. It is not
`not meaningful`, because there is no statistic to qualify. Where several such cells sit
in one band, the section note says how many and why; a reader who sees six empty Y/Y
cells and no explanation assumes the fetch failed.

### The prune sentence

`dropNote()` builds prose from the surviving table, so two things must hold at the point
the labels are written:

- **A row label's unit prefix is stripped.** `x  ·  NTM P/E` renders correctly in the
  table and wrongly in a sentence, so `dropNote()` removes a short leading token before a
  middle dot. Do not work around this by dropping the prefix from the table.
- **Three or more dropped items are comma-separated**, not joined by repeated `and`.
  `listJoin()` does this; a list built any other way will read as a stammer.

### The masthead

Three cells in one row **inside the house outline box**, which `self_check.py` asserts on
the rendered file ("masthead carries the house outline box").

The three widths are **computed, not fixed**, and the literals `1,201 / 5,588 / 4,491` that
stood here came from a build two revisions old. The mark cell is 1,150; the right cell is
sized to the width its own longest line needs, clamped to 2,900-4,300 twips; the company
cell takes the remainder. The total is always 10,800, the text column, and that is the only
invariant to hold onto: a measured build renders 1,150 / 6,315 / 3,335. A flat right cell
is wrong: its content is right-aligned, so the surplus opens a dead hole to the LEFT of
"Prepared:" that the company line can never reach, sending ordinary issuer names onto a
second line.

Widths
summing to the same `TW` (10,800 dxa) every other full-width table holds:

| cell | holds |
|---|---|
| left | the mark |
| middle | `InSync Analytics` over `Company (TICKER) - Quarter` |
| right | `Earnings Packet` over `Prepared:` and `Expected Earnings Date:`, right-aligned |

An `E8850C` rule sits under the block, and the field strip sits under the rule. That rule is
the only division: with no box the masthead reads as a banner rather than a framed panel.
**The masthead is set in Times New Roman**, the packet's own serif - a declared exception to the Georgia masthead used by every other product in
the library. Times New Roman is the packet's own serif and is used for the section headings,
and nowhere else. The masthead face lives in one named constant, `MASTHEAD_FACE`, so it is
reversible in a single edit - but the two alignment constants below are calibrated against
this face's vertical metrics and must be re-measured if the face changes.

Three sizes inside the box are decided independently:

| element | rule |
|---|---|
| mark | constant 59 x 46 px, never autofit. The asset is 512 x 400 (AR 1.2800) against 59/46 = 1.2826, a 0.2% difference, so the fixed size does not visibly distort it |
| brand line | 16.5 pt on the one-line path, 12 pt on the two-line path. Both sizes come from `common/brand.json` (`brand_size_half_points`, `brand_size_two_line_half_points`), never from a local literal |
| title | 18 pt |
| company line | 14 pt, stepping 14 / 13.5 / 13 / 12.5 / 12, and **stopping at the 12 pt floor** |
| a name that will not fit at 12 pt | an explicit, code-chosen **two-line split at 10 pt** - never an elision |
| date lines | 9 pt, fixed |

**The floor is the point, and NOTHING is ever truncated.** Holding one line at any cost (an
8 pt company line) and cutting the name short are both wrong. A reader must never be shown a cut issuer name, so past
the 12 pt floor the name runs to a SECOND line at 10 pt and the brand line yields the
vertical room by stepping to 12 pt. No elision path survives in the renderer, and
`self_check.py` asserts its absence.

**The two-line break is chosen in code, never by the layout engine.** The split minimises
`|width(a) - width(b)|` with both halves capped at 95% of the column. A greedy split can put
the whole string on line 1 and leave a blank line 2, which throws the measured bottom out by
a full line height. The `(TICKER)` token stays orange on whichever line the split puts it.

**THE ALIGNMENT RULE, and how it is held.** The text block spans the mark EXACTLY: the top of
`InSync Analytics` meets the mark's top, and the bottom of the LAST company line meets the
mark's bottom. Three things together make that land, and dropping any one loses an edge:

1. **An EXACT line rule on every line of the block**, so the block height is a known
   quantity rather than a function of the resolved face size.
2. **`vAlign BOTTOM` on the mark cell and the brand cell.** `vAlign BOTTOM` alone pins only
   the floor and lets the top float; a `vAlign TOP` plus spacing-offset construction
   lets it drift by several points on the rendered PDF.
3. **Two calibration knobs, solved as a 2x2 against measured sensitivities.** The company
   line box (`MCO_LINE`) and the mark's lift (`MMARK_UP`, `spacing.after` under the image).
   Both edges are measured on rendered INK, so both move with the FONT's ascent and descent.

Measured on this renderer (docx npm -> LibreOffice -> PDF), per twip:

| knob | one-line d(top) | one-line d(foot) | two-line d(top) | two-line d(foot) |
|---|---|---|---|---|
| `MCO_LINE` | +0.050 | +0.010 | +0.100 | +0.010 |
| `MMARK_UP` | -0.050 | -0.050 | -0.050 | -0.050 |

`MCO_LINE` moves the top at double rate on the two-line path because the exact rule applies
to two company paragraphs, which is why the two paths carry separately solved constants.
Solved: one-line `MCO_LINE = 372 + 5.5 * drop`, `MMARK_UP = 13 + 5.5 * drop`; two-line
`MCO_LINE = 224`, `MMARK_UP = 1`.

**Descent compensation** is the `drop` term (points below the 14 pt top rung). The descent
scales with the face but an exact line box does not, so at a fixed box the foot drifted
+0.22 pt per point of drop (-0.02 / +0.20 / +0.31 / +0.42 at the 14 / 13 / 12.5 / 12 pt
rungs). Adding the same slope to BOTH constants moves the top by 0.050s - 0.050s = 0 and the
foot by 0.010s - 0.050s = -0.04s, so s = 5.5 flattens the foot without disturbing the top.

**Date lines are pinned at 9 pt.** Two reasons, the second load-bearing:
`Expected Earnings Date: 19 Nov 2026` sat exactly on the old 10 pt overflow threshold; and
`check_masthead.py` finds the company line by SIZE inside a 420 pt window of the brand
line's left edge, where a right-aligned 10 pt `Prepared:` run lands ~415 pt out - inside the
window, so the gate measured the date line instead of the company line and reported a 10 pt
company face on a 14 pt render. At 9 pt the date runs are below the gate's size floor.

The title carries a 200 dxa right inset so it can never touch the box rule.

`Reports:` in the field strip and `Expected Earnings Date:` in the box are the same date.
The repetition is intentional: the box is the reference layout and the strip is the
packet's own, and neither is the place to drop a date a reader looks for.

### Clauses that must earn their place

Three clauses in the section notes are conditional in the renderer, and each condition
exists because the unconditional version reads wrongly:

| clause | prints only when |
|---|---|
| corporate and eliminations | some period carries at least $5M, the smallest figure that is non-zero at `$B` to 2dp |
| the peer comparability caveat | the row it describes survived the prune |
| the per-peer comparability basis list | the bases actually differ across the set |

## Structure

- Section heading in black over an orange rule, both spanning the full text column.
- Notes are 6 pt grey italic, one to three lines, directly under their table.
- **A note must say what the table cannot**, the basis, the caveat, or the read. Never
  restate a number the table shows without adding a consequence.
- Rows do not split across pages (`cantSplit`) except long prose rows, which may.
- Header rows repeat when a table spans a page break.

## Pagination, a table is a block, not a stream of rows

**A table is laid out whole or it is not laid out.** `T()` sets `keepNext` on every row
but the last, so a table that does not fit in the space remaining moves down entire
rather than spilling its tail onto the next page. Two constants govern it:

| constant | value | meaning |
|---|---|---|
| `KEEP_MAX` | 26 | a table of this many rows or fewer is held on one page outright |
| `MIN_BLOCK` | 3 | a table too long to hold leaves at least this many rows either side of the break |

Beyond `KEEP_MAX` the table is allowed to split, but `keepFlags()` binds the first
`MIN_BLOCK - 1` and the last `MIN_BLOCK - 1` rows, so neither side of the break is ever
a stub. The header row always carries `keepNext` **and** `tableHeader`, so it repeats on
the far side and never sits alone at the foot of a page.

This exists because a single orphan row at the top of a page is unreadable, it inherits
no header, no heading and no context, and a reader who lands on it cannot tell which
table it belongs to. **Validator pass 6 fails any table fragment shorter than
`MIN_BLOCK` rows sitting at the top or bottom of a page.**

Word and LibreOffice both ignore `keepNext` when the block genuinely exceeds a full page,
so the rule degrades to normal flow rather than to an infinite loop or a blank page.

## Empty columns and empty rows

**A column of dashes is a layout failure, not a disclosure.** `prune()` runs inside
`T()` before any width check and removes:

| what | when |
|---|---|
| a body row | every one of its data cells is empty |
| a data column | fewer than `minFill` of the body rows carry a value |

`minFill` is set per table, and the split is deliberate:

| table | `minFill` | why |
|---|---|---|
| financial summary, guidance, dispersion, revisions, valuation | `0` | the columns are **structural**, the period grid is the point of the table, so a column goes only when it is entirely empty |
| segment, operating drivers, peer comparison | `0.5` | the columns are **evidential**, a target column filled below half is noise the reader has to scan past, and the values it does hold appear in the dispersion section |
| debate, earnings-day reaction | pruning off | prose table, and a spanned row, where the column model does not hold |

Cells reading `none`, `no history` and `not comparable` are **assertions the packet is
making** and never count as empty. `–`, `,`, `-`, `–%`, `n/a` and `not coded` do.

Widths are redistributed across the surviving columns and the label column absorbs the
residual, so **the `TW` invariant survives the prune**, pass 2 still measures one
left edge and one right edge.

Every prune reaches the page. `dropNote(key)` appends a sentence to that section's note
naming what went and its denominator, *"The 2Q27 E column (4 of 9 rows) is not shown:
too few lines carry a value for the column to be read."* This is rule 6, no silent
truncation, applied to layout. **Validator pass 5 bins every span into a real column
using the table's own vertical borders and fails any column that is more than half
em-dash.**
- **Two adjacent `<w:tbl>` elements with no paragraph between them are MERGED by Word and
  LibreOffice.** This silently renders the second table's rows under the first table's
  header, the most dangerous bug in this layout, because every value is still present
  and only the column meaning is wrong. Either separate tables with a paragraph, or build
  one table with banded group rows. Validator pass 2 scans for adjacent tables.

## Compression, in the order to try it

Three pages is the specification. When a build runs long:

1. Transpose a wide table (peers read better as metric-rows × ticker-columns).
2. Merge two related tables into one with banded group rows.
3. Fold a two-column block to one column, two text columns force the row to the height
   of the taller.
4. Allow prose rows to split across pages.
5. Tighten leading before shrinking type. Leading 147–168 is comfortable at 6.5 pt.
6. Only then reduce the type size.

**Never compress by deleting a section or a data row.** If it still will not fit, say so
and name what you would cut, rather than cutting it silently.
