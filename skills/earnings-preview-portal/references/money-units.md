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
2. **Always 2 dp, rounded once, half-up** (house rule R2/R4, v9.0.7; the 2→4 dp widening is
   gone: two close cells may print alike, and the delta beside them says they moved).
3. **A delta keeps its row's magnitude.** Re-magnituding a delta on its own size puts
   `+$13.10B` under four cells quoted in T, and a reader cannot subtract across magnitudes.
4. **Other figures:** counts (`#`) print in full below a million, else M/B at 2 dp; per-share
   (`$`, minor-unit rows included) and multiples 2 dp; percentages and pp 1 dp, 2 dp on a rate
   row whose largest |value| is under 5%; bps whole; negatives `−$1.20B` / `−1.2%` (U+2212).
   Colour and sign follow the PRINTED figure (grey when it prints as zero).
4a. **Printed changes come from printed levels** (R1, compute `_as_printed`): a Y/Y, Q/Q,
   margin, % of revenue, revisions Δ or vs-guide % whose two levels are both on the page is
   computed from them as printed, so the reader can re-derive it; on a base under $1bn or
   1.00 per share the full values are used. bps come from the full rates. Beat % and the
   beat-history credibility tests stay on full values (their levels are not printed).
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

## Gates (regression bench, internal edition only)

- **Y4a** — no rendered row mixes magnitudes (parses the rendered text of a large-cap
  control).
- **Y4b** — retired in v9.0.7 (no widening); refresh the bench in the check phase.

Both check the **rendered** text, because `computed.json` is right either way. They exist so
that re-freezing the 22 render snapshots cannot bless a regression in this area: a frozen
snapshot pins what happened, not what should.
