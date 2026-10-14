# Money units — one magnitude per row

`SKILL.md` rule 8 in full. Implemented in `render_docx.py` (`moneyShape`, `moneyRow`,
`deltaShape`, `fmtM`, `fmtSignedM`, `fmtMag`); gated by Y4a / Y4b in the regression bench (internal edition only).

## The rule

A money cell prints **its currency mark, its digits and a magnitude letter** — `₹1.25T`,
`$100.50B`, `€250.75M`. This is the vocabulary the Earnings Wrap (`render_wrap.py`,
`_money` + `_UNIT_RE`) and the Earnings Flash (`run_math.py`, `fmt`) already use, so the
three documents quote one ticker in one scale.

1. **One magnitude per row.** It is chosen once, from the largest absolute value among the
   row's own cells, and applied to **every** column of that row — the quarterly columns,
   the FY columns, any guidance cell and the revisions delta. A row that switched scale
   between columns could not be read across, and its delta would not close against the
   levels it is drawn from. The Flash enforces the same invariant from the other end, in
   `render_gate.magnitude_drift`.
2. **Decimals widen, 2 dp to a cap of 4.** dp grows until the narrowest gap between two
   distinct cells in the row survives the rounding.
3. **A delta keeps its row's magnitude** and widens only its decimals. Re-magnituding a
   delta on its own size puts `+$13.10B` under four cells quoted in T, and a reader cannot
   subtract across magnitudes.
4. **Unchanged:** counts (`#`) print in full with separators and no suffix; per-share (`$`) rows, minor-unit rows at 4 dp, percentages, multiples, bps
   deltas, and every colour decision.
5. **The band** above a block reads `(units as stated per row)` and never names a scale —
   its cells now carry their own, and a band asserting one scale for all of them would
   contradict them.
6. **The valuation card** takes a per-value magnitude: its lines are independent
   quantities, not a row.

Thresholds are on compute's internal `$M` basis, which `parseValue` is unchanged: `1e6` of
them is a trillion, `1e3` a billion.

## Why

Take a lender whose Total AUM is ₹1.25T (illustrative), returned by the MCP in that form.
Printed as `1,250,000.00` under a band reading `(₹M unless stated)`, the number is **right**
and still wrong on the page:

- **It overflows its column.** Ten digits plus separators plus a 2 dp tail does not fit a
  seven-column period grid, so the page wraps mid-number: `1,250,000.0` / `0`.
- **It makes the reader recover a magnitude the database already knew,** in a document whose
  purpose is to be read at a glance.
- **It disagrees with the other InSync documents.** The Wrap and the Flash for the same ticker,
  from the same MCP values, print `₹1.25T`. One line quoted three ways reads as three
  different numbers.

**A figure printed at a scale the reader does not expect is indistinguishable from a figure
that is wrong.**

## Why the widening is not optional

A flat `v/1e6 + 'T'` at fixed 2 dp prints distinct values as one string. The
four Earnings Revisions buckets of one line routinely differ by a tenth of a percent:

| line | buckets (`$M` basis) | flat 2 dp | widened |
|---|---|---|---|
| A lender's Total Consumer AUM | 1,500,400 / 1,502,600 / 1,503,800 | ₹1.50T ×3 | ₹1.500T / ₹1.503T / ₹1.504T |
| An oil producer's EBITDA | 25,000.4 / 25,001.8 | $25.00B ×2 | $25.000B / $25.002B |

Identical cells beside a delta column asserting the line moved is a table at war with
itself — the class of defect this package exists to prevent.

## Gates (regression bench, internal edition only)

- **Y4a** — no rendered row mixes magnitudes (parses the rendered text of a large-cap
  control).
- **Y4b** — a widened revision row still prints four distinct cells.

Both check the **rendered** text, because `computed.json` is right either way. They exist so
that re-freezing the 22 render snapshots cannot bless a regression in this area: a frozen
snapshot pins what happened, not what should.
