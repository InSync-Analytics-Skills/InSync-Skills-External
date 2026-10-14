# Build guide — earnings wrap v7.13.1

This file describes the shipped pipeline. The renderer carries its CSS inline; there is no
separate HTML template or header spec to follow.

## Pipeline

You never write HTML. You write a JSON config; the code does the rest.

```bash
pip install weasyprint        # fresh sandboxes lack it
python references/render_wrap.py <config.json> <TICKER>_Earnings_Wrap_<Q>Q<YY>.pdf
```

That one command is the whole build. `render_wrap.py` runs the gates AND the validation layers
itself, so there is no second command to forget:

| Gate | Blocks on |
|------|-----------|
| `kpi_gate` | Primary KPI is not a member of `kpiUniverse`; empty universe; `mcp_discovery` provenance with no `kpiDiscovery`; a Primary KPI that is a single generic word; an ABSENT, empty or whitespace-only `primaryKpi`, and an absent or empty `kpiProvenance`. Neither field is rendered, so without the check a note with no audit trail would look identical to one with it. `classify_mcp` matches by normalised-string containment in either direction (so `Segment A Revenue` finds `Segment A - Revenue`), which is why a bare `Revenue` is refused: it would otherwise match any `... - Revenue` line. A Primary KPI names a driver, so it reads like `Regulated Electric Revenue`, never `Revenue` |
| `consensus_gate` | a Results row never probed; a `c:null` whose probe found a value; a `c:null` with no `evidence`; a graded `c` that does not match the probed `found` |
| `spine_gate` | the revenue ltcode returned a line that does not read as a top line (default-deny), or no Y/Y anywhere in the window; `accepted` + `acceptedReason` downgrades a genuinely exotic top line to a warning and cannot rescue a different concept |
| `sector_gate` | Step 0 decisions 2 to 4 undeclared or unproved: an unreturned margin ltcode, `guidanceSource:"table"` while `get_guidance` returned rows, or no `sectorProfile` at all. A `default` spine warns and self-discloses |
| `broker_count` | a graded Results row with no `BROKERCOUNT`, or a negative one. **The minimum panel is 1** (`BROKER_FLOOR = 1`: a zero-broker row is swapped for the next-ranked KPI, or prints ungraded - it never blocks). Broker counts are internal, so no caption discloses them |
| `broker_disclosure` | The word "broker" or "brokers" in any rendered caption or any prose string in the config. Broker counts cannot be disclosed, so none may reach the page: they live in `consensusProbe`, which this lint skips along with every other audit-trail block. The failure names the field and quotes the text |
| `table_contract` | the `guidanceTable` head is not the four contract columns, or `valTable` lacks an own-average or peer column, or a row is shorter than its head |
| `valuation_sanity` | a Section 10 multiple sitting more than 20x from the company's OWN history average. Only a unit, currency or feed error moves a multiple that far; a real one that merely halves or doubles is left alone |
| `price_basis` | a missing or contradicted `priceBasis` block. `get_market_data` returns a CURRENCY per ROW and does not guarantee one across a window (one pull can mix sessions from two listings in two currencies), so the currency is declared and the gate refuses a note whose printed price disagrees with it |
| `note_contract` | a Section 7 with neither `guidanceTable` nor `forwardTable`, or both; a missing `secondaryKpi` (row 2 of the snapshot was the last unpinned choice in a fixed grid), a `guidanceBasis` outside the closed set (`Prior [G]` means two different things and the reader cannot tell which), or a missing `revisionsScope` (an omitted revision row and an unrevised line look identical) |
| `relevance` | four MUST rules from SKILL.md: the Primary KPI absent from the Results table or the KPI table (matched on token sets, so a reordered name still counts); a KPI table with fewer than 2 company-specific rows, where "company-specific" needs at least TWO tokens or an exact canonical match, matching the minimum in `kpi_gate.classify_mcp`; a capital-intensive or income sector with no capex/FCF/OCF/FFO line or no dividend/payout line anywhere; a `reported` date later than `prepared` |
| `one_currency` | a single table mixing two currencies, detected by symbol AND three-letter code |
| `identity` | `identityProbe` records what `search_tickerid` returned - ticker, company, tickerId, `matchType` - and the masthead must agree with it. Only an exact identifier match may be wrapped. The resolver screens on a similarity score, and a score near 99 can still return a different issuer at status success, so only the match TYPE discriminates. No code can prove the MCP returned the issuer you meant; this makes the claim explicit and checkable, and a human still reads the masthead. |
| `palette` | an unresolved `__TOKEN__` in the stylesheet (a colour written anywhere but `PALETTE`) |
| `page-fit` | more than 4 pages. Warns at 4. Counted BEFORE the PDF is written, so a hard failure leaves no deliverable behind |
| `validation` | any of the eleven layers below. Fails CLOSED: a validator that cannot be loaded, or that raises, blocks the build instead of being skipped |
| `unit` | a `u` token outside the documented vocabulary, or `#` (a count in WHOLE units) carrying a fractional value. NOT downgradable by `INSYNC_GATE_MODE=warn`: an unknown token prints a bare float with no currency and no magnitude, and `#` on a count in millions rounds it to a whole number, so a Results row can read `17 \| 17 \| -0.4%` — two identical cells with a surprise between them |
| `polarity` | a row whose name reads lower-is-better (combined/loss/expense/efficiency ratio, cost of risk, provisions, churn, vacancy, net debt...) that is declared nowhere. An undeclared row colours by SIGN, so a rising combined, loss or expense ratio — bad news — would print in GREEN. Declare once, in `snapshot.inv` or as `inv: true` on the `results` row; `_polarity_set` applies it to every table. Cues are phrases with their sense pinned: on a bank a Tier 1 leverage ratio (capital over exposure) and provision COVERAGE (reserves over bad loans) are higher-is-better, so `HIGHER_IS_BETTER_EXEMPT` wins wherever both match, and `snapshot.noinv` is the escape for a cue that still misfires — it satisfies the gate AND marks the row in the rendered HTML, so `polarity_colour_lint` honours the same declaration while still reading only the delivered document. The cue list covers the cost lines utilities, banks and industrials report: interest expense, capex, capital expenditure, operation and maintenance, fuel, cost of goods/sales/revenue, operating expense, opex, selling general, impairment, catastrophe, accident year, ex cat, days inventory, days payable, restructuring. `capex` is in the CUE LIST and deliberately not in `polarity_registry.json`, because its direction is sector dependent: a rate-base build is a utility's growth signal and an asset-light issuer's cash drain. The gate only ever ASKS, so listing it forces ONE explicit declaration per note instead of two analysts silently choosing opposite colours for the same line |
| `snapshot_contract` | a numeric delta typed into the snapshot's Change column. Raised by `normalise_snapshot` before any other gate, so it is never downgradable. `ref` fixes the Actual cell; a hand-typed Change cell could still put a wrong Y/Y on page 1 against the right one on page 2 |
| `graded_column` | A Results table that SHOWS a Consensus column where not one row carries a consensus value: three columns of dashes on a note that says it grades the print against consensus. `consensus_gate` cannot see it, because every row was probed and every `found: null` carried evidence, so the audit trail is perfect and only the PAGE is wrong. The tier-3 escape is `consensusColumn: false` plus a required `consensusBasis`, and the renderer then genuinely drops the Consensus, &Delta; vs Cons and Dir. columns instead of em-dashing them. Where SOME rows grade and others do not, the ungraded rows are NAMED on stderr and nothing blocks |
| `valuation_sanity` (FX arm) | A price-derived multiple on a DUAL-CURRENCY issuer with no reconciliation recorded in `valFxBasis`. The band arm above is 20x wide and structurally cannot see an FX error: a 100x pence error is caught, a 1.3x FX error is nowhere near. Worse, the current-versus-history check is SELF-CONSISTENT because both sides carry the same error. A price in one currency divided by forward EPS in another gives a plausible-looking multiple; the naive ratio MATCHING the returned multiple is the tell. The gate cannot convert (no FX rate in the payload, and an invented one is a fabricated figure) so it asks for the reconciliation to be done and recorded, or the rows to print `n/a` |
| `note_contract` (growth arm) | A `results` row that is already a Y/Y series and does not say so. `kpiTable` rows carry `kind` (level/rate/growth) and SKILL.md requires the driver KPI in BOTH tables, so an `Organic Net Revenue Growth` row under a column headed plainly `Y/Y` would print the change IN the rate, not a Y/Y. Caught two ways: `kpiTable` declaring the same metric as growth while `results` does not (a note contradicting itself), or a name cue on a `%` row. Declaring `kind: "growth"` marks the row with a dagger and forces one explanatory line into the Section 4 caption |
| `kpi_nesting` | A declared Primary KPI that is a COMPONENT of another KPI row in the same note: materially smaller in the SAME units and sharing that line's distinguishing tokens. A `top_kpi` universe can tag a sub-line (`Segment A - Product X Revenue`) as `driver_kpi` so it wins `pick_primary_kpi` on tag priority over the segment line it sits inside, and the Event Snapshot would then lead with a sub-line above its own parent, inverting the KPI Hierarchy. The gate does NOT re-pick - model-side overriding is the variance the tie-break removes - it only ASKS, and `kpiNesting: {"accepted": true, "reason": "..."}` proceeds |
| `staleness` | A wrap of a period older than `identityProbe.latestFiscalQuarter`. Without it a build relabelled to an older quarter would render clean, the masthead reading `1Q26` while the Section 6 KPI grid still ended at a column headed `2Q26 [A]`. The ±1-year band in `relevance` is deliberately too wide to catch it. **NOT downgradable by `INSYNC_GATE_MODE=warn`**: a wrong period on the page is not a config shape, and there is no migration in which shipping last quarter's wrap is the right answer |
| `table_figures` | A Section 7 or Section 10 cell that carries a FIGURE as a hand-typed string, and a `valTable` Current cell that disagrees with the Event Snapshot on the same metric. A pre-formatted string is owned by no layer, so a fabricated price, multiple or guidance consensus would pass every other check. A figure cell is `{"v": 22.4, "u": "x"}`, or `{"lo": 4.40, "hi": 4.60, "u": "$"}` for a range, with an optional `"note"` for a tolerance band the figure cannot express, and the renderer formats it. Plain text stays legal exactly where it carries no figure: `n/a`, `InSync NTM`, `Jul 23, 2026 close` |

The TWELVE config-shape gates (`spine_gate`, `sector_gate`, `broker_count`, `table_contract`,
`valuation_sanity`, `price_basis`, `note_contract`, `relevance`, `one_currency`, `polarity`,
`identity`, `kpi_nesting`) are the only ones
`INSYNC_GATE_MODE=warn` reaches, and only so a config saved before the block existed can
be migrated. Everything else blocks in both modes: `kpi_gate`, `consensus_gate`,
`snapshot_contract`, `unit`, `palette`, `page-fit`, `validation`, `graded_column`,
`table_figures` and `staleness`.

`_enforce` reads `GATE_MODE_SCOPE`: a gate has to be named in that list to be downgradable at
all, so anything added later blocks by default.

`validate_wrap.py` runs ELEVEN layers over the emitted HTML and exits non-zero on any failure:
`house_asserts`, `format_lint`, `wrap_eval`, `numbers_lint`, `dedup_lint`, `content_lint`,
`palette_lint`, `crosstable_lint`, `derived_lint`, `polarity_colour_lint`, `prose_precision_lint`.
The renderer calls them for you; run the file by hand only to iterate on a failure without
re-rendering:

```bash
python references/validate_wrap.py <TICKER>_Earnings_Wrap_<Q>Q<YY>.html
```

Never loosen a threshold to pass — fix the note.

**Advisories are not gates.** A thin broker count, a `default` sector spine, a prose money figure
within 1% of a printed Actual, and a cued prose percentage far from every printed percentage all
print on stderr and still render. Read them. Prose figures are the one part of the note no layer can
prove, so a delta stated in a bullet must be COPIED from the table, never computed by hand.

Two more advisories. **`graded_column` names every Results row printing a dash** in the Consensus
column, because a dash is only honest if the probe PROVED that line unconsensused and nothing else
in the run can tell a proven gap from a line nobody asked about. And **a Primary KPI that is a cost
line raises `WARN primary_kpi`**: the tie-break ranks on tag count, so a heavily tagged cost line
such as "Net Interest Expense" can come back as the Primary KPI of a regulated utility. It does NOT block, and the answer must
NOT be overridden: an override reintroduces the cross-model variance the tie-break removes. But that
KPI leads the Event Snapshot and the KPI hierarchy, so Section 6 has to say in one line why this
cost is the driver of the quarter. If it is not, the tagged universe was thin: record the tier in
`kpiProvenance` and say so in `kpiCap`.

If weasyprint cannot run, the build writes the Word edition instead (see "Portability: where
weasyprint cannot run"); deliver what it wrote, never the HTML. Never swap in another renderer or
hand-write CSS: the layout is owned by `render_wrap.py` and that is what makes output identical
across environments.

## The skeleton the code actually enforces

`wrap_eval` checks these titles, in this order (Executive Summary is a box, not a numbered section):

Executive Summary → 2 Variant View → 3 Event Snapshot → 4 Results versus Expectations →
5 What Drove the Quarter → 6 KPI Hierarchy and Performance → 7 Guidance and Estimate Revisions →
8 Management Commentary → 9 Key Monitoring Variables → 10 Valuation Context

Section numbering starts at 2 because the Executive Summary occupies the first slot. Do not rename,
reorder, add or drop sections. Sector flavor lives INSIDE sections.

## Tables the renderer draws

All emit `class="data"`; `wrap_eval` rejects any other table class. You supply rows, never format.

| Table | Built by | Notes |
|-------|----------|-------|
| Event Snapshot | `eventSnapshot()` | `Metric \| Actual \| Change (Y/Y)`, 7 fixed rows; needs the `(Primary KPI)` and `(Key KPI)` tags |
| Results versus Expectations | `resultsTable()` | banded; computes Y/Y, the sequential change and Δ vs consensus, plus the arrow, from `{a,c,py,pq,u,inv}`. The sequential column is headed by CADENCE: `Q/Q` quarterly, `H/H` half-yearly, `Prior FY` annual |
| KPI Performance | `kpiHistoryTable()` | 5 consecutive quarters, printed last, plus an always-present Y/Y column |
| Guidance | `tbl()` | mandatory whenever the company guides; section 7 must contain a table |
| Estimate Revisions | `revisionsTable()` | `{name,before,after,u}`; computes the change and direction |
| Valuation | `tbl()` | at least `Metric \| Current \| 3Y Avg \| Peer Median`, plus a `Basis` column in house style; shape enforced by `table_contract` |

## The house precision policy

**Rounding is Excel's, not Python's.** `house_round` and `house_fmt` convert through
`Decimal(repr(v))`, the shortest decimal that round-trips the float, and quantise with
ROUND_HALF_UP, which the decimal module defines as ties going away from zero. That is exactly
what Excel's `ROUND()` does. Python's `%.2f` and `round()` use round-half-to-even on the binary
double instead, so `0.25` would print `0.2` and `2.675` would print `2.67`; here they print `0.3`
and `2.68`. This skill is read next to a model built in Excel, and a renderer that disagrees with
Excel on the last digit is a renderer that gets queried on every note.

It is ROUND, not ROUNDUP. `74.41%` stays `74.4%`; nothing inflates to `74.5%`.

**One precision per kind of figure**, so two rows of the same kind never print at different
precisions on one page:

| kind | decimals | example |
|---|---|---|
| money, at EVERY magnitude | 2 | `$39.31B`, `$617.10M`, `EUR 4.72B`, `GBp 83.37M` |
| per-share | 2 | `$1.87`. Per-share is money with no magnitude, so the money rule covers it |
| percentages | 1, always | `74.9%`, `+2.6pp` |
| multiples | 2 | `24.80x` |
| counts with a magnitude | 2 | `16.72M`, `149.25K`, `3.72B` |
| bare `#` | whole units | `1,482`. Refuses a fractional value |
| bare numbers (`2d`) | 2 | `16.72` |

**Two unit tokens were retired**, and a config carrying either gets a migration message naming
its replacement rather than the generic "not in the vocabulary" list:

| retired | use | why |
|---|---|---|
| `%2` | `%` | percentages print ONE decimal under the policy (74.9%, not 74.85%) |
| `1d` | `2d` | bare numbers print TWO decimals (64.44, not 64.4) |

**Unit tokens are CASE SENSITIVE.** The magnitude is `B`, `T` or `M` in upper case, so `$b` is
not `$B`; a wrong-case token gets the `UNIT GATE FAIL` message that names the legal tokens.
Subunits are matched case-sensitively for the same reason: `GBp` is pence and `GBP` is pounds, and folding them renders
a pence figure as pounds, 100x too high, with nothing on the page to show it.

**Nothing that reaches the page may call `round()` or a bare `%.Nf`.** Every formatter in
`render_wrap.py` goes through `house_fmt`. It is a rule you have to keep by hand: a new `%.2f`
added to a formatter would print Python's rounding beside Excel's with nothing failing. Section 7
and Section 10 obey it too, because `table_figures` refuses a hand-typed figure in `valTable` or
`guidanceTable`.

## Config contract

**`references/config_skeleton.json` is what a build starts from**: every required key, in
section order, with its exact shape and its contract written into the placeholder it replaces. It is
annotated JSON, so the shape rules arrive with the keys the model is already filling rather than as
prose it has to hold in context. The renderer ignores unknown keys, so the `_help` block and the
`_`-prefixed notes can be left in place or deleted.

`references/sample_config.json` is the authoritative POPULATED example; read it when you need to see a filled note rather than a form. Required
fields beyond the prose: `kpiUniverse`, `kpiProvenance`, `consensusProbe`, `sectorProfile`,
`spineProbe`, plus `kpiDiscovery` when provenance is `mcp_discovery` and `kpiSubstitutions` when a
KPI was swapped for lack of data.

SKILL.md carries the JUDGEMENT (what each section's writing has to do) and this file plus the
skeleton carry the MECHANICS.

The sample is the worked example, so it AGREES with SKILL.md, guidance head included, and
`table_contract` checks both hand-headed tables.

### The three edge cases

The edge cases SKILL.md points here for: a non-guiding issuer, no peer set, a half-yearly filer.

**A half-yearly filer.** Everything works:
- `quarter` takes the half, `"2H26"`. That is LEGAL against `identityProbe.latestFiscalQuarter` of
  `"4Q26"`, which is what `search_tickerid` returns for a June year-end: `_period_key` maps
  `"2H26"` and `"4Q26"` both to `(2026, 4)`, so `staleness` is satisfied. This
  is the single thing a half-yearly build most needs told.
- `_year_ago("2H26")` resolves to `"2H25"`, and `kpiTable.quarters` takes five HALVES
  (`["2H24","1H25","2H25","1H26","2H26"]`), still exactly five entries.
- The renderer heads the sequential column **`H/H`**, not `Q/Q`, from the period labels alone. Do
  not try to force it.
- Guidance is usually annual on these issuers; see `guidanceBasis` below.

**No peer set.** `table_contract` requires `valTable` to carry a peer column named `Peer Median` or
`Peer Range`. Where no peer set can be screened on ONE basis - the common case for a non-USD issuer,
whose US-screened comps are built on a different currency basis - **keep the column**, put `"n/a"` in
every peer cell as `n/a` plain text, and say in `valNote` why. (`n/a` is the UNAVAILABLE case. An EMPTY cell means the comparison is MEANINGLESS, as on the Price and 52-week range rows.) Do NOT compute a median yourself:
the note never carries a figure the renderer did not draw, and a peer median you averaged is exactly
the kind of number no layer can check. Where a peer set exists on one basis, pass the
peers' own multiples from `get_valuation` and let the renderer take the median:
`{"peers": {"PEER1": 21.4, "PEER2": 18.2, "PEER3": 25.1}, "u": "x"}` (names are provenance).
Under 3 valid values it prints `n/a`. Give each peer in the Current cell's unit: a yield as a percentage
(`get_valuation` returns FCF_Yield 0.0497; the cell takes 4.97).

**A non-guiding issuer.** Where `get_guidance` returns ZERO rows, delete `guidanceTable` and
`guidanceTableCap` and add `forwardTable` with the head exactly
`["Forward Metric", "Consensus [E]", "Basis"]`. Exactly one of the two tables is present, never
both, never neither. `guidanceBasis` is still required and still describes what Prior means.

## Gotchas

- **Section 4 computes from the DISPLAYED figures, and that is deliberate.** earnings-flash-skill
  computes from the values as displayed too, so identical data publishes identical surprises in both
  notes; computing from unrounded values instead can move a surprise by a tenth of a point. Three
  things carry the rule and none of them can be removed alone:
  1. `resultsTable` quantises `a`/`c`/`py`/`pq` through `results_dp_map` **before** computing.
  2. `row_dp` widens a row (money to 4dp, rates to 2dp) when the house precision would erase its
     gap or flip its direction. Without it a flat 2dp could print a multi-billion miss on a
     trillion-scale balance as `$1.20T | $1.20T | +$0.00T | +0.0% | In-Line`: it reconciles
     perfectly and is factually inverted.
  3. `change()` returns `(a-b)/abs(b)`, not `a/b - 1`, so a negative base is signed correctly at
     source. `deltaCons` applies no separate sign flip on top, which would invert every negative
     row twice.
  **`results_dp_map` is the only place precision is decided.** `basis_band_rows` MUST read it:
  `numbers_lint` block (d) recomputes the 40% basis test from the PRINTED cells and fails the build
  when a banded row is not named in the Section 4 caption, so raw-versus-quantised straddling of
  that band breaks the render outright. Deliberately NOT shared with earnings-flash-skill: its verdict bands
  (1% / 0.25pp - wrap's arrow is a weaker claim than the word "Beat" and bands tighter at
  0.5 / 0.15), and its rounding mode (`%.1f`, ties-to-even; wrap keeps Excel's ties-away-from-zero).
  `_flash_verdict` exists ONLY to decide widening, so the two skills widen the same rows.
  Out of scope and still computed raw: the revisions grid, and the KPI table's Y/Y column for any
  metric Section 4 does not print. A KPI-table or dict-snapshot row whose metric
  Section 4 also prints (same `crosstable_lint` label key, same `u`) computes its Y/Y from its OWN
  cells quantised to Section 4's `results_dp_map` precision, via `shared_results_dp`, so the two
  tables print the same Y/Y from the same figures. A real `py` vs `vals` conflict still blocks,
  because each table still uses its own operands.
- `IS_P_102` (adjusted revenue) is frequently empty; `IS_P_101` is often the only series.
- **The adjusted margin codes are often the ones with no data, and the fallback is the rule, not a
  footnote.** On some issuers `IS_P_102`, `IS_P_106-M` and `IS_P_110-M` return ZERO rows on both
  actuals and estimates, while `IS_P_105-M` and `IS_P_109-M` carry complete pre-print consensus
  histories. Ask for the adjusted code; when it returns nothing, take the reported twin and carry
  the asterisk. They are still different
  concepts and the note must say which one it printed.
- Pre-print consensus needs the as-of idiom (`latest={"enabled": false}` plus an update-date window
  ending the day before the report). **The naive latest pull is not RELIABLY post-print consensus,
  which is worse than if it always were.** `latest:{enabled:true}` over an already-reported period
  can return the pre-print consensus when the series never revised after the print. Use the as-of
  idiom anyway: nothing in the payload tells you which
  case you are in.
- Probe consensus for EVERY Results row in one batched call, not just revenue and EPS. Margins and
  segment KPIs usually DO have a pre-print mean.
- Keep Basis and Metric cells terse.
- Date commas count toward `format_lint`'s one-comma cap. Restructure the sentence, never loosen
  the lint.
- **Period labels are normalised in one place (`_norm_period`).** It strips InSync's
  `R-` / `R1-` restatement prefix and accepts a lower-case quarter, so `R-2Q25` and `2q26` both
  resolve. `_year_ago`, `_cadence`,
  `_seq_head` and `_period_key` all read the same normaliser, so they cannot disagree about what a
  label means.
- Target ~3 pages, never more than 4. `render()` page-checks YOUR note: warns at 4, hard-fails
  above 4. The count happens BEFORE the PDF is written, so a hard failure leaves no
  finished-looking file next to the error message.
- **Capital-intensive sectors have far less page slack.** The Energy/Utility/REIT/Telecom spine adds
  a whole Results band (capex, OCF or FCF, payout) on top of revenue, EPS and margins, which is three
  to four rows more than a tech note, and such notes often first render at 4 pages. Budget for it
  up front — hold `results` at about 11 rows, the KPI table at 5, and keep `kpiCap` and
  `resultsCap` to two sentences each (`valCap` is retired and Section 10 has no caption). Trim the
  note, never the gate.
- A KPI whose ltcode returns no pre-print estimate (an energy producer's EBITDAX or gas
  realization, an automaker's quarterly deliveries) belongs in the KPI table, NOT in `results` with
  an empty consensus. Record the windows you tried as `evidence` in `consensusProbe` either way; an
  unprobed row and a genuinely unconsensused row must never look alike.
- Rate KPIs (retail comparable sales, margins) grade as a **pp** gap, not a percent gap. Set `u:"%"`
  and the renderer does it; the surprise column then reads differently from the $ rows in the same
  table, which is correct and worth one line in `resultsCap`.
- `inv: true` on a row flips the favourability sign. Use it for capex, payout and unit-cost rows so
  that coming in BELOW consensus reads green rather than red.

## Portability: where weasyprint cannot run

weasyprint needs the GTK/Pango libraries. Linux and macOS usually have them; Windows usually does not,
and a pip-installed weasyprint without them raises `OSError` at import. There the build does not stop
at the HTML: `render_wrap.py` writes the **Word edition** with `references/docx_wrap.py` (standard
library only), then a PDF from it with the first converter present - LibreOffice (`soffice`), Word
on Windows (via PowerShell), or the `docx2pdf` package.

- **Nothing is recomputed.** `docx_wrap.py` typesets the validated HTML element by element (masthead,
  summary box, section heads, bullets, tables with bands, zebra rows and green/red cues, captions,
  sources, the footer with Page X of Y). It reads the page's own CSS palette and sizes.
- **Text parity is a gate.** `docx_wrap.text_parity` compares every visible character of the .docx with
  the validated HTML, in order; any difference raises `DOCX PARITY FAIL` and nothing is delivered.
- **The page-fit gate applies.** Above 4 pages in the converted PDF the build fails and removes both
  files; at 4 it warns. With no converter the page count is not measured and the build says so.
- **Where weasyprint runs, nothing changes**: the same HTML, the same PDF path.

## Portability: fonts decide pagination

The renderer is deterministic given a config; the PDF is not host-independent. Page breaks follow the
metrics of the font the host resolves, and **only Carlito is metric-compatible with Calibri**. On a
host with neither, the body sets wider and the 3-page note becomes 4.

```bash
python references/preflight.py     # run this first on any new machine
apt-get install fonts-crosextra-carlito    # Debian/Ubuntu fix
dnf install google-carlito-fonts           # Fedora fix
```

Georgia (masthead only) falls back to Gelasio or Liberation Serif; that does not move page
breaks, but it is NOT purely cosmetic: the masthead's two alignment edges are measured on
rendered ink and the geometry in `render_wrap.py` is solved from Georgia's own vertical metrics
(see `MASTHEAD-STANDARD.md`), so a substituted face puts the logo/brand/company alignment off spec and
`check_masthead.py` reports the substitution. `render_wrap.py` warns at 4 pages and hard-fails
above 4.

## Verify an install offline

```bash
python references/preflight.py     # environment, and it takes no arguments
python references/kpi_gate.py --selftest-mcp
python references/mcp_guard.py selftest
```
The full install suite (`self_check.py`, internal edition only) additionally renders the bundled
sample, proves every gate and layer runs, and re-renders for a SHA-256 determinism check. The
renderer stamps `build <version>` into the page footer, so a note in circulation can be traced
back to a build. `validate_wrap.py` IMPORTS the polarity cue tuples from `render_wrap` rather
than keeping a hand-copied duplicate, so the two cannot drift apart.

## The gates, layer by layer

SKILL.md names the gates and states the contract; this is what each one blocks on.

- **house_asserts** — punctuation, brand, base64 logo, print-color-adjust portability guard.
- **format_lint** — sentence/bullet length caps, one-comma rule, bullet dominance.
- **wrap_eval** — fixed 10-section skeleton and order; table whitelist; Event Snapshot
  `Metric | Actual | Change` grid with the (Primary KPI) and (Key KPI) tags; KPI history
  `KPI ... Y/Y` value+growth grid (every value row also shows a Y/Y); mandatory Guidance table;
  no advisory lexicon (buy/sell/overweight/price target...). `\bbuy\b` and `\bsell\b` exclude
  `-side`, so "buy-side" and "sell-side" pass.
- **crosstable_lint** — every metric printed in more than one Actual column must print the
  SAME value. It reads the rendered HTML, not the config, which is the only way to see a
  disagreement between two tables. Give a snapshot row a 4th element naming the Results row
  it restates and the renderer formats both cells from the same figure, so they cannot differ.
- **numbers_lint** — INDEPENDENTLY recomputes every consensus surprise, beat/miss verdict and
  estimate-revision change from the raw figures; a mislabeled or fabricated number fails here.
- **dedup_lint** — each fact stated once (the stock move appears once).
- **content_lint** — evidence-based writing (Rules 1-6): quantify every movement claim, no
  unsupported opinions, one home section per figure, Management Commentary must be management
  statements (not restated results), structured Metric/Fact/Monitor monitoring.
- **kpi_gate** — the declared Primary KPI must be a member of the LIVE MCP KPI universe resolved
  by the Step 0 ladder; a fabricated or irrelevant KPI BLOCKS the build.
- **consensus_gate** — every Results row must be probed for pre-print consensus; an unprobed row,
  an unevidenced `c:null`, or a graded number that does not match the probe BLOCKS the build.
- **spine_gate** — the revenue ltcode must be proved to RETURN a revenue line. `spineProbe`
  records the LINEITEM `get_actuals` actually returned and whether Y/Y came back. It is
  DEFAULT-DENY: a line that does not read as a top line BLOCKS the build, as does one with no Y/Y
  anywhere in the window. Sector top lines are recognised (net interest income, premiums, FFO,
  rental income, fee and commission income). For anything genuinely unusual, set
  `spineProbe.revenue.accepted` true with an `acceptedReason` — that downgrades it to a warning
  and leaves the justification in the config. The override cannot rescue a line that names a
  different concept (net income, a margin, equity): those block regardless. On some issuers
  `IS_P_101` returns a line such as "Total Net Income". This is the only failure mode in the skill that is otherwise SILENT: the number is
  present and internally consistent, so `numbers_lint` cannot see it.
- **valuation_sanity** — a Section 10 multiple more than 20x from the company's OWN history
  average BLOCKS. Only a unit, currency or feed error moves a multiple that far; one that merely
  halves or doubles is untouched. For a non-USD issuer `get_valuation` can return a current
  multiple orders of magnitude away from its own three-year mean, with a market capitalisation to
  match; a note built from that payload would print an absurd multiple on a page that looks perfect.
- **price_basis** — `priceBasis` is REQUIRED and its declared currency must match the prices the
  note prints. `get_market_data` returns a CURRENCY per ROW and does NOT guarantee one across a
  window: a single pull can mix sessions from two listings in two currencies, so "the latest
  close" can come from the wrong listing and a move computed across that boundary is meaningless.
- **broker_count** — every graded Results row must carry the `BROKERCOUNT` behind its consensus.
  A missing count BLOCKS (incomplete audit trail). **The minimum panel is 1**
  (`BROKER_FLOOR = 1`: a zero-broker row is swapped for the next-ranked KPI, or prints ungraded - it never blocks).
  Broker counts are never disclosed on the page. A mean with no broker behind it is not a consensus, so a
  zero-broker row is not graded (`ungrade_thin_panels`); a one-broker panel grades, and `consensusProbe` shows how thin it is.
- **broker_disclosure** — Scans every rendered caption and every prose string in the
  config for the word "broker"/"brokers" and fails the build on a hit, naming the field and
  quoting the text. The audit-trail blocks are exempt, because `BROKERCOUNT` belongs in
  `consensusProbe` and nowhere else. One known false positive: an issuer whose own sub-industry
  is insurance broking, so the declared `sectorProfile.subindustry` string is subtracted from a
  caption before matching.
- **sector_gate** — Step 0 decisions 2 to 4 must be declared in `sectorProfile` and PROVED: a
  margin ltcode that `get_actuals` never returned, a guidance variable taken from the table while
  `get_guidance` returned rows, or a missing block at all BLOCKS the build. A `default` spine does
  not block; it WARNs on stderr and discloses itself in the Section 6 caption.
- **graded_column** - the Consensus column must GRADE something. Set every Results row's `c` to
  null, which is the honest recording when the probe genuinely found nothing, and without this gate
  the note would render with the Consensus, Δ-vs-Cons and Dir. columns dashed on every row and
  every layer passing: a wrap that grades nothing. A wholly empty column BLOCKS, and the way out is
  SKILL.md's tier-3 branch: `consensusColumn: false` plus a written `consensusBasis` naming what
  you graded against instead (the guidance midpoint, or a cited preview). The renderer then draws
  four columns (`Metric | Actual [A] | Y/Y | <cadence>`) rather than seven with three of them
  empty. `consensusColumn: false` with a graded row still present also blocks, because dropping
  the column would hide a number the note has. A PARTLY dashed column is normal and does not
  block; the ungraded rows are named on stderr so the author can confirm each dash was PROVED
  empty rather than never asked.
- **staleness** - the wrapped `quarter` is compared with `identityProbe.latestFiscalQuarter` and
  an older period BLOCKS. Without it a build relabelled to an older quarter would render with no
  warning, the masthead reading 1Q26 against a Section 6 KPI grid ending at `2Q26 [A]`. The check is cheap
  because the resolver already told us. A missing `latestFiscalQuarter`, or a label this skill
  cannot read (`2Q26`, `1H26`, `2026`), blocks too: leaving the check unable to run is not a pass.
- **table_figures** - A pre-formatted string in `valTable`, `guidanceTable` or `forwardTable`
  is owned by NO layer, so a fabricated price, multiple or guidance consensus typed there would
  pass every other gate and layer. Any cell carrying a figure must be a VALUE plus
  a unit token, `{"v": 18.2, "u": "x"}` or `{"lo": 6.55, "hi": 6.80, "u": "$"}`, with an optional
  `"note"` for an annotation the figure cannot carry (a company's own `(±2%)` band, or `for 2Q26`).
  The renderer formats it, so these tables obey the house precision policy.
  Plain text is still right where the cell carries no figure at all: `n/a`, `InSync NTM`,
  `Jul 23, 2026 close`. The gate also blocks a `valTable` Current cell that disagrees with the
  Event Snapshot on the same metric, compared as NUMBERS so a pre-formatted snapshot string and a
  renderer-formatted Section 10 cell are not a false failure.

## Audit-trail blocks

SKILL.md lists the blocks and what each one is for; these are the full field rules.

- **`consensusProbe`** — REQUIRED: the audit trail proving Section 4's consensus column is complete.
  `{asOf:"YYYY-MM-DD", window:"...", probed:[{name, ltcode, found: <number>|null, brokers, evidence?}]}`.
  `brokers` is the `BROKERCOUNT` the probe returned and is REQUIRED on every graded row.
  One entry per Results row, matched on `name`. `found` is the pre-print mean the probe returned and
  must equal the row's `c`; `found:null` requires a non-empty `evidence` string naming the query
  windows that came back empty. Checked by `consensus_gate()` at render time.
- **`spineProbe`** — REQUIRED: `{revenue: {ltcode, lineitem, yoyNonNull}, eps: {...}}`, where
  `lineitem` is copied VERBATIM from the `get_actuals` payload. Add `accepted: true` plus
  `acceptedReason` only when the issuer's top line genuinely is not a recognisable revenue line. It is what proves the spine code
  means what its name suggests for this issuer. Checked by `spine_gate()` at render time.
- **`priceBasis`** — REQUIRED: `{currency, asOf, reactionSession, currencyConsistent: true, note?}`.
  The audit trail for every price the note prints. Read the `CURRENCY` field on EVERY session you
  use, drop any that disagrees, and set `currencyConsistent` true only once you have. Checked by
  `price_basis()` at render time, which also refuses a printed price whose symbol contradicts the
  declaration.
- **`sectorProfile`** — REQUIRED: the audit trail for Step 0 decisions 2 to 4 (sector spine,
  primary margin ltcode and its proof, guidance variable and its source, core debate, and any margin
  substitution). Shape and rules in Step 0 above. Checked by `sector_gate()` at render time.
- **`kpiUniverse`** — the live MCP KPI list (names) resolved by the Step 0 ladder; the Primary KPI
  must be a member (checked by the gate). This maps KPI relevance to the MCP, not a local file.
- **`kpiProvenance`** — REQUIRED, one of `mcp_top_kpi`, `mcp_kpi_tier`, `mcp_discovery`; the ladder
  tier that produced `kpiUniverse`. The gate reads it, and `mcp_kpi_tier` / `mcp_discovery` must be
  disclosed in `kpiCap` and `sources`.
- **`kpiDiscovery`** — REQUIRED when `kpiProvenance` is `mcp_discovery`:
  `{keywords:[...], candidates:[{name, ltcode, proved: true|false}]}`. It is the audit trail showing
  each KPI was recovered by an MCP keyword search and proved against `get_actuals`. Unproved
  candidates stay listed as `proved: false` and are excluded from the KPI table.
- **`kpiSubstitutions`** — REQUIRED whenever the Data-availability substitution rule (Step 0) dropped
  a KPI for lack of data and pulled another in its place:
  `[{dropped, droppedLtcode, reason:"no printed value" | "no year-ago anchor", replacement,
  replacementLtcode}]`. This is the audit trail for the "no data -> next KPI" swap, mirroring
  `kpiDiscovery`. Empty or omitted when every selected KPI proved out on the first pass. A KPI kept
  despite a partial history (printed and year-ago anchors present, some intermediate quarter `n/a`)
  is NOT a substitution and is not logged here.

## Rules 9-14 in full

## Deterministic KPI architecture (Rules 9-14) — enforced by the renderer + `wrap_eval`
KPI selection, ordering and formatting cannot vary by run or environment. Writing may vary slightly;
the information architecture may not.

- **Rule 9 - Event Snapshot is standardized (3-column grid).** A `Metric | Actual | Change` grid with
  the same 7 rows, in this order, for every company: (1) Primary KPI `(Primary KPI)`, (2) a second
  Key KPI `(Key KPI)`, (3) Key Margin `(Key Margin)`, (4) Revenue, (5) Adj EPS or the sector bottom
  line, (6) Stock reaction, (7) Current price. Never reorder by company.
- **Rule 10 - Every KPI shows BOTH a value AND a Y/Y.** Never a percentage alone, never an absolute
  alone. `kpiHistoryTable` appends the Y/Y column itself, so the model cannot choose the format.
- **Rule 11 - KPI history is 5 consecutive quarters, printed quarter last,** with the trailing Y/Y.
  `wrap_eval` FAILS a row showing a printed value with no Y/Y (a `n/a` Y/Y is legal only when the
  printed value is itself unreported).
- **Rule 12 - Sector KPI spines are fixed,** not model discretion, so two runs pick the same KPIs.
  The spine per sector is in `references/kpi-selection-standard.md`; the ranking within it comes
  from the MCP ladder, never from judgement.
- **Rule 13 - Determinism test.** Would five analysts in five environments produce the same Event
  Snapshot, KPI selection, ordering and formatting? If not, it is not done. Verified by cross-model
  runs; the Primary-KPI tie-break exists for this reason.
- **Rule 14 - Writing style.** Internal PM-note voice: short sentences (<=25 words), one observation
  each, fact first then interpretation, bullet-driven.

## sectorProfile

### Decisions 2 to 4 are declared and proved, not assumed (`sectorProfile`, REQUIRED)
Decision 1 has `kpiUniverse` + `kpiProvenance` and a gate. Decisions 2 to 4 carry the same
burden, in one **REQUIRED** config block. The point is that a sector spine chosen wrongly, or
silently defaulted, is invisible on a finished page: the note looks perfect and reads generic.

```json
"sectorProfile": {
  "subindustry": "<insync_subindustry from search_tickerid, verbatim>",
  "row": "<the kpi-selection-standard row that matched, or 'default'>",
  "resolution": "table" | "live" | "default",
  "marginLtcode": "IS_P_106-M",
  "marginProved": true,
  "guidanceLive": true,
  "guidanceSource": "get_guidance" | "table",
  "guidanceVariable": "next-quarter revenue and gross margin",
  "coreDebate": "<the sector debate, refined by this quarter's variant>",
  "substitutions": [{"tried": "IS_P_110-M", "used": "IS_P_114-M", "reason": "no rows"}]
}
```

**Do not fill this block by hand.** `python references/sector_resolve.py <subindustry> <get_actuals.json> <get_guidance.json>` emits it: it matches the row against `common/sectors.json`, walks the margin ladder to the first code that RETURNED, logs every skipped code as a substitution, and takes the guidance variable from what the issuer actually guides. Prose rules get followed unevenly across chats; this is the same reasoning as `kpi_gate.py --pick-primary` for the Primary KPI.

- **`resolution`** — `table` a named row matched; `live` the spine was proved entirely from MCP
  probes; `default` NO row matched and the generic spine is in use.
- **`marginProved`** — `true` only after the ltcode returned a value from `get_actuals` for the
  printed quarter. If it returned nothing, walk `IS_P_114-M` → `IS_P_110-M` → `IS_P_106-M` and log
  the swap in `substitutions`. An unproved code BLOCKS the build.
- **`guidanceLive`** — `true` when `get_guidance(latest=true)` returned rows. The call is not
  optional. Setting `guidanceSource` to `table` while live guidance exists BLOCKS the build: the
  `default` row may prescribe a revenue guide for an issuer that guides no revenue line at all.
- **`default` does not block.** A generic note beats no note. It WARNs on stderr naming the
  subindustry, and the renderer forces a disclosure line into the Section 6 caption, so the reader
  is told and the gap list for `references/kpi-selection-standard.md` builds itself from real runs.

## Common LTCODEs

Common LTCODEs: revenue `IS_P_102` (adjusted) but this is **frequently empty; `IS_P_101` is often
the only revenue series, so use it without ceremony when `IS_P_102` returns nothing**, adj EPS
`IS_P_128`, reported EPS `IS_P_127`, adj gross margin `IS_P_106-M`, adj EBIT margin `IS_P_110-M`,
adj opex `IS_P_108`, buyback `CF_P_101`, dividend `CF_P_104`. Segment codes are company-specific
(`search_lineitem`). Valuation (NTM): `Market_Cap`, `EV`, `P/E_Adj_Ratio`, `EV/EBITDA_Adj`,
`P/S_Ratio`, `FCF_Yield`. **Net cash = Market_Cap less EV.**

**The reported twin is the FALLBACK, and it is a rule.** `IS_P_109-M` is the
reported EBIT margin and `IS_P_105-M` the reported gross margin. On some issuers the adjusted
codes return zero rows on both actuals and estimates while the reported twins carry complete
pre-print consensus histories. Ask adjusted first, fall back to the reported twin, carry the asterisk, and say in one line
which basis the note printed.

## Section and probe detail

- **3 Event Snapshot** — `snapshot` = a standardized grid, never a prose blob:
  `{head:["Metric","Actual","Change"], rows:[7 fixed rows], w:[...]}`. Rows in fixed order (Rule 9):
  (1) Primary KPI `(Primary KPI)`, (2) second Key KPI `(Key KPI)`, (3) Key Margin `(Key Margin)`,
  (4) revenue, (5) Adj EPS or the sector bottom line, (6) Stock reaction (Actual = 1-day % move,
  Change = muted print-date context), (7) Current price (Actual = price, Change = muted as-of date).
  A row that restates a Results line MUST carry a 4th element naming that row (for example
  `["Total revenue", "", "+85.2%", "Total Revenue"]`): the Actual is then formatted from the
  Results row's own figure, so the two printings cannot disagree. Rows with no counterpart
  (stock reaction, current price) carry a literal string.
  Each fundamental row carries one Change (Y/Y) column, rates in pp, as a green-up / red-down delta;
  the beat/miss surprise lives in Section 4. Labels adapt by sector, the grid and row order do not.
  `wrap_eval` FAILS the build if the snapshot lacks the `(Primary KPI)` or `(Key KPI)` tag, or is not
  the Metric|Actual|Change grid.

- **7 Guidance and Estimate Revisions** — `guidanceTone`, `guidance` bullets (close with a
  Read-Through), a **mandatory `guidanceTable`** and, when pre/post consensus exists, a `revisions`
  table. The **`guidanceTable` is required whenever the company gives guidance** (bullets alone are
  not enough): `{head:["Guide Metric","Prior [G]","New [G]","Consensus [E]"], rows:[...], w:[...]}`,
  one row per guided metric (revenue, EPS or EBITDA, FCF, segment growth, and the Primary KPI where
  guided). **The head is exact and enforced** by the table-contract gate: `Prior [G]` is the guide
  the company had given FOR the printed quarter (the bar it set), `New [G]` is the guide issued ON
  the call, `Consensus [E]` is the post-print Street number for the guided period, or `n/a` where
  the probe found none. An Actual column does NOT belong here: grading an actual against an
  expectation is Section 4's job, and an Actual column in Section 7 prints the same figure a second
  time for `crosstable_lint` to reconcile. `revisions` =
  before/after/change rows with `revisionsWindow` and both dates in `revisionsCap`; if consensus has
  not revised since the print, say so in one line and omit only the revisions grid, never the
  `guidanceTable`. `wrap_eval` FAILS the build if Section 7 contains no table.

### Consensus probe (MANDATORY, "no consensus" must be PROVEN not assumed)
The fallback chain above governs WHERE consensus comes from. This rule governs WHICH LINES you must
ask for. **Query every line that will appear in Section 4, not just the headline three.** The
failure this prevents is silent: an unqueried line and a genuinely unconsensused line both render as
`—`, so an incomplete pull looks identical to a real data gap and `numbers_lint` cannot see it (it
recomputes the surprises that are present, never the one that is missing).

Mirror the Step 0 prove-then-include discipline, on estimates instead of actuals:
1. **Build the probe list BEFORE writing the config.** It is every ltcode already resolved for the
   Results table: the financial spine (revenue, EPS, each margin) plus EVERY KPI in `kpiUniverse`
   that earns a row, including segment lines, rate lines and cost lines such as fuel or interest expense.
2. **Probe them in ONE batched as-of call.** `get_estimates` with the pre-print idiom
   (`latest={"enabled": false}` plus an update-date window ending the day before the report date),
   passing the whole ltcode list at once. This costs one call, not one per line.
3. **Grade everything the probe returns.** A row is set `c:null` ONLY when the probe came back
   empty for that ltcode. Margins and segment KPIs frequently DO carry consensus even when the
   headline pull did not request them; assuming otherwise is the single most common defect in this
   note.
4. **Prove an empty result before publishing it as empty.** `latest={"enabled": true}` plus a
   date window can return a FALSE zero on an annual period where `latest:false` returns a full
   history. Re-run the ltcode alone over a wide window (quarter start to the
   day before the report), then over `latest={"enabled": true}` **with no date window at all**.
   Then try one further shape, dropping `fiscal_period` altogether, before you believe the
   emptiness: a KPI the Street forecasts at annual cadence only can come back empty on all three
   prescribed attempts and return FY and Q4 rows on this one. The honest record is then `found: null` with
   `reason: "consensus exists at FY/Q4 cadence only"`, not a bare null. Record every window and
   shape you tried as `evidence`.
5. **Record the whole probe** in the config's `consensusProbe` field. It is the audit trail that a
   `—` was earned, exactly as `kpiSubstitutions` is the audit trail for a dropped KPI.

**Enforced at build time.** `render_wrap.py` runs `consensus_gate()` before drawing anything and
REFUSES to render when a Results row was never probed, when a row is ungraded although the probe
found a value, when a graded number does not match the probed value, or when a `c:null` carries no
`evidence`. A note whose consensus column is thin because of an incomplete pull cannot ship.

## Section field detail, part 2

- **2 Variant View** — `variant` = FOUR labelled pairs in order: Street View / What Changed /
  Potential Variant / Variant Assessment (verdict Supported, Partially Supported or Not Supported,
  plus one evidence line). Potential Variant frames what the market may MISUNDERSTAND, not a
  restatement of results; every claim carries a number or a sourced quote. No Key Evidence pair, and
  **no "Stock Reaction" pair** — the move is stated once, in the Executive Summary, or `dedup_lint`
  fails the build.

- **4 Results versus Expectations** — `results` = banded groups (Key Financial Metrics, Top
  Operating KPIs); each row `{name,a,c,py,pq,u,inv?}`. The renderer computes Δ versus consensus
  (favourability-aware via the note-wide `inv` declaration for lower-is-better lines), Y/Y, the
  cadence-headed sequential change and the direction arrow. A row
  sets `c:null` (renders "—") ONLY when the consensus probe came back empty for that ltcode and the
  emptiness is recorded in `consensusProbe` with `evidence`; an unqueried line is a build failure,
  not a "—". Never grade a quarter against a full-year guide. Follow with a one-line `takeaway`.

- **6 KPI Hierarchy and Performance** — `kpiHier` (Primary / Secondary / Supporting) + a STRUCTURED
  `kpiTable` = `{quarters:[5 consecutive, printed last], rows:[{name, u, kind, vals:[5]}], note}`.
  The renderer (`kpiHistoryTable`) draws the value series AND an always-present trailing **Y/Y**
  column, computed from the year-ago quarter (col 1) — level KPIs show a % change, rate/growth KPIs
  a pp change. `u` is the value unit (`$B`, `$T`, `$M`, `$`, `%`, `x`, `2d`, `bn`, `#`, `#K`, `#M`, `#B`).
  `%2` and `1d` are RETIRED and tokens are case sensitive. **For a non-USD reporter, put the
  issuer's currency in place of the `$`**: `EURB`, `£M`, `CHFB`, `¥T`, `BRL`. A symbol or a
  three-letter code both work, and the currency you write is the currency that prints; `kind` is `level` (absolute),
  `rate` (a %-level like margin/take-rate/combined-ratio) or `growth` (an already-Y/Y series like
  comps). NEVER hand-format KPI cells and NEVER emit a values-only or percent-only KPI table.


## MCP wrapper notes — the behaviour behind the rules in SKILL.md

The rules stay in SKILL.md; the detail is here.

## Call defaults

Every one of these is a DEFAULT that, left unstated, returns a truncated or empty result a reader
takes for an absent fact.

| Call | Parameter | Default | What it costs you |
|---|---|---|---|
| `get_actuals`, `get_estimates` | `limit` | **25** (max 100) | 28 quarters requested come back as 25 rows with `has_more: true, remaining_items: 3`. Every delta still closes on the rows that survived. |
| `get_call_date` | `limit` | **50** (max 100) | as above |
| `get_call_date` | `start_date` / `end_date` | **`30d` / `today`** | Bare, it answers "did this issuer report in the last month": `ticker_id` alone returns a recent call, and a month later the same call returns none, which reads exactly like an unreported quarter. Backward-only by default; future dates ARE supported, and that is the trap below. |
| `get_guidance` | `limit` | **50** | `row_count` can come back **100** against `limit` 50, because the limit applies to the `guidance` and `outlook` arrays SEPARATELY. |
| `search_lineitem` | `limit` | **10** (max 20) | per query keyword; ignored when `queries="all"` |
| `search_tickerid` (explore) | `limit` | **20** (max 100) | |
| `get_market_data` | `limit` | **50** (max **100**) | A limit of 300 is REJECTED by the server; page a year of closes with `offset`. `mcp_guard` clamps it and says so. |
| `get_valuation` | `limit` | **100** (max 500) | |
| `get_document_tree` | `limit` | **none: the tool takes no `limit`** | Passing one fails the whole batch. `mcp_guard` drops it. |
| every tool | `ltcode`, `fiscal_period`, `metrics`, `ticker_id` | **comma-separated TEXT** (`"IS_P_101,IS_P_128"`) | A JSON list is rejected; only `parse_document_content`'s `file_paths` is a list. `mcp_guard` joins a list and says so. |
| `get_estimates` | `latest` | **`{"enabled": true, "limit": 1}`** | the naive pull is POST-print consensus |
| `get_estimates` | `updatedate_start` | **`90d` back from TODAY** when `latest.enabled=false` | The trap. The harm is a TRUNCATED window, not zero rows: unset, it can return a handful of rows where the full history is ten times longer. Zero is loud and the probe machinery catches it; four rows look like valid data with the revision history gutted. Always pass it. |
| `get_estimates` | `latest` true vs false | ordering | `latest:false` returns the revision history **OLDEST FIRST**, so `data[0]` can be a mark from more than a year before the print and wildly different from the pre-print consensus. Take the LAST row, or use `latest:true` bounded by the same window. |

**Pagination is NOT uniform.** The default limits above hold; the `meta` block does not:

- `search_lineitem` returns **no pagination block at all**: no `row_count`, no `limit`, no
  `has_more`. "Page until `has_more` is false" is therefore unexecutable on the Step 0 KPI call,
  which is the one call in the build where a truncated universe is most expensive.
- `get_guidance` can return `row_count` **100** against `limit` 50, because the limit is applied to
  the `guidance` and `outlook` arrays separately.
- a zero-row `get_estimates` returns `meta: {"row_count": 0}` with **no `has_more` key** at all, so
  code that reads `meta["has_more"]` raises on exactly the empty result it was written to handle.

Where `meta` IS complete it carries `row_count`, `limit`, `offset`, `has_more` and
`remaining_items`; page until `has_more` is false, and treat a truncation as a stop, not a note.
Everywhere else, count the rows you got against the rows you asked for.

**Batching.** Two `search_lineitem queries="all"` calls batched in one `execute` take about 1.3x
the time of one, not 2x. They run in PARALLEL, and `discover` marks such steps
`[PARALLEL]` in its own plan, so batching does not add their latencies.

What is true, and what the rule rests on: the `execute` is bounded by its SLOWEST member, so a
batch is only as fast as the worst call in it, and **one failed member kills the whole batch**. A
validation error does that as surely as a timeout, which is worth knowing because a malformed
parameter is far more common than a slow call. That is why two calls that can each approach the
ceiling, discovery and semantic searches, should not be bundled: not because the budgets add, but
because one failure loses both results. Batch cheap related pulls freely.

A timeout is not a rate limit: re-running it unchanged fails the same way. Split it, or narrow it.

### The latest-quarter proof

Passing an `end_date` in the FUTURE and counting the calls after the period's end is not enough on
its own: InSync publishes announced future call dates, so the result can hold the reported call
plus several SCHEDULED future calls that have not happened. The check only works with an added
filter:

```
get_call_date(ticker_id, end_date=<a date in the future>)   # then keep CALL_DATE <= today
```

Count the rows that survive that filter, not the rows the call returned. The `staleness` gate is
the backstop: `identityProbe.latestFiscalQuarter` is what the resolver already knows, and the gate
blocks a wrap of an older period.

### How `get_estimates` query shapes behave

- **The naive latest pull is not always POST-print consensus.** `latest:{enabled:true}` on an
  already-reported period can return the pre-print consensus when the series never revised after
  the print.
- **`latest:true` plus a date window** returns rows at the quarterly level, but can return **zero
  on an ANNUAL period** where `latest:false` returns a full history. Never use that shape alone to
  PROVE a consensus empty: the retry could manufacture the very emptiness it was meant to disprove.

### `latest:{enabled:true}` returns a MIXED-DATE basket

"Latest" resolves **per UIC**, not per call. One call can return UPDATEDATEs spanning months, so a
consensus column built from a single such pull silently mixes as-of dates, and the note's own `consensusProbe.asOf` is then a date
that applies to none of the rows. Capture `UPDATEDATE` per probed row alongside `BROKERCOUNT` and
record the spread.

### Read the `notes` field

Every payload with a missing ltcode already carries a diagnostic that NAMES it, for example:

```
"Next Action: MISSING DATA: LTcode(s) IS_P_102, IS_P_110-M, IS_P_106-M returned no results.
 ACTION: Use search_lineitem to find alternatives and re-run the query."
```

One field read replaces the three-query ritual, and it does something the ritual cannot: it
distinguishes "the vendor has no data for this line" from "this ltcode does not exist for this
issuer". Read `notes` on every payload before deciding a line is unavailable.

### `get_actuals` growth fields, and `R-` revision rows

- **No growth fields on the consensus-probe call shape.** `latest:false` returns neither
  `YOY_GROWTH` nor `QOQ_GROWTH`. "Growth is database-first" holds for `get_actuals` and is
  UNSATISFIABLE on the probe, so say which call a growth figure came from; the renderer computes
  Y/Y from the five-quarter series either way.
- **`R-` revision rows are substituted silently.** A long request can return rows labelled
  `R-3Q24`, `R-2Q22` and so on **instead of** the plain labels, consuming slots against the 25-row
  default. `_norm_period` strips the prefix so `R-2Q25` still resolves to `2Q25`, but the
  row-count effect is real: ask for more rows than you need.

### `get_guidance` `FISCALPERIOD` is not always a period

Values include `"2027-2030"`, `"2026-2034"` and `"Long-Term"`. A naive period match will
crash on the ranges or, worse, treat `"Long-Term"` as a fiscal year and grade a quarter against it.
Filter to the labels this skill reads (`2Q26`, `1H26`, `2026`) before matching, and put a long-term
target in prose if it belongs in the note at all.

### Call guard (`references/mcp_guard.py`)

Every timeout trap in this package is written down, and prose is easy to miss at the moment a
model writes `tool_calls`. This is the same reasoning as `kpi_gate.py --pick-primary`: the model
fetches, the script decides. A timeout is also the ONE defect class no gate can catch
afterwards - every other layer inspects data that arrived, and a timeout leaves nothing behind.

```bash
python references/mcp_guard.py learn <search_tickerid payload>.json   # once per run
python references/mcp_guard.py check '<tool_calls array>'             # before every execute
python references/mcp_guard.py selftest                               # offline checks
```

It refuses `company=` alongside `ticker=`, refuses a bare `company=` search, rewrites short
country forms to the canonical name, refuses `region=` on an explore, refuses an
`insync_subindustry` the database has not returned this run, splits a batch holding two semantic
searches, caps a batch at 5, and notes a `parse_document_content` target over 40 MB (a warning, never
a drop: large releases parse clean). It also corrects a call's FORM to the server's own rules
(limit ranges, no `limit` on `get_document_tree`, comma text not lists).

It never alters the SUBJECT of a request - ticker, period, ltcode, metric all pass through
untouched - so the data that comes back is identical. A replay regression (internal edition
only) asserts that every call which worked reaches the server byte-identical, and every call
which timed out is stopped first.

#### The `insync_subindustry` trap

| `insync_subindustry=` | Result |
|---|---|
| the label `search_tickerid` actually returns (e.g. `"Software"`) | **seconds** |
| a more specific industry-classification guess (e.g. `"Application Software"`) | **180s TIMEOUT** |
| the same guess with `company=` as well | **180s TIMEOUT** |

Batching is not the cause. It is the same failure shape as `country="US"` below: the label the
analyst types looks MORE precise than the one the database uses, not less. Take the string from the payload, verbatim, always.
`sector_brief.py` and `sector_resolve.py` already do; a peer screen is where it gets typed.

### `country="US"` times out. `region="US"` is a different trap

On a peer screen:

| argument | result |
|---|---|
| `country="United States of America"` | **seconds** |
| `country="United States"` | **seconds**, normalised, with a note in the payload saying so |
| `country="US"` | **180s TIMEOUT** |
| `region="US"` | seconds, and wrong |

The obvious fix is the trap: `region="US"` means **the Americas**, so a US screen can return a
Latin American issuer reporting in another currency. That lands straight on the `one_currency` gate and, if it survives
into Section 10, on the peer comps. **Peer screens use `country="United States of America"`.**

### `parse_document_content` takes `file_paths` and `user_prompt`, nothing else

`{"ticker_id": <id>, "fiscal_period": "2Q26"}` returns
`validation_error: Missing required parameter(s): file_paths, user_prompt`. The file paths come
from `get_document_tree`, which lists the transcript among the quarter's documents, so a transcript
IS fetchable: tree first, then parse the path it gives (SKILL.md "MCP data protocol").

`get_market_data` takes `ticker_id`, `start_date`, `end_date` (ISO or relative) and `limit`, and
returns `DATE`, `ADJUSTED_CLOSE` and `CURRENCY` per session. `CURRENCY` is per-row and NOT
guaranteed constant across a window: a single pull can mix sessions from two listings in two
currencies. This is why `price_basis` demands a
declared `priceBasis` and one currency per series.

`get_valuation` can return a corrupt current snapshot for a non-USD issuer: a current multiple
orders of magnitude from its own three-year mean, a market cap to match, and no EV/EBITDA at all.
This is why `valuation_sanity` compares every multiple to the company's
own average on a 20x ratio band — wide enough that a real multiple doubling or halving never trips
it, narrow enough that a unit or currency error always does.

`IS_P_102` (adjusted revenue) is frequently empty and `IS_P_101` is often the only revenue series.
The adjusted margin codes can be empty for a whole sector, in which case the reported twin carries
an asterisk; `sector_resolve.py` walks that ladder row-first, then by twin, then generically.

**`get_valuation` NTM carries history.** `mode=NTM, metrics=P/E_Adj_Ratio, start_date=3y,
summarize=true` returns one row holding the mean, median, min and max over the whole history,
with its `data_points` count, and `aggregation=quarterly` returns a row per quarter. Do not read
the single summary row as a single observation, and do not pair an NTM current multiple with an
LTM-derived average: that is a BASIS MISMATCH in the one table `valuation_sanity` polices.
Take the average from NTM `summarize` and record `data_points` in `valProvenance`, never in `valNote` (the renderer fails a `valNote` that describes how figures were fetched; `valCap` is retired).


## `derived_lint` — layer 9, the independent net

Every other check in this package reads the config or the HTML the renderer just wrote — the
producer's own opinion of itself. A systematic arithmetic error in the renderer (a percentage
helper scaled wrong, say) would pass every one of them.

`derived_lint` re-derives each printed delta FROM THE PRINTED CELLS BESIDE IT, with arithmetic
written in `validate_wrap.py` and shared with nothing. It never reads the config. Because a printed
cell is rounded, the error bound on a ratio re-derived from two of them is computed per row rather
than compared against a fixed tolerance — otherwise the layer would report its own arithmetic.

It blocks on: a delta that does not reconcile with its own two cells; two cells that print
identically under a non-zero surprise; a surprise printed to a precision its cells cannot support;
a ratio percent change printed across a sign boundary; two magnitudes inside one comparison or one
period series; and a history Y/Y that its own five-quarter series refutes.


## `polarity_colour_lint` — layer 10

The `polarity` gate forces a lower-is-better row to be DECLARED; this layer verifies the
declaration was APPLIED, so a deteriorating combined ratio can never print in GREEN. It reads only the rendered HTML — the label gives the polarity, the cell text
the direction, the span class what the reader actually sees — and its cue list is written in
`validate_wrap.py`, shared with nothing in `render_wrap.py`, so a regression in either place is
caught by the other.

Blocks on: a metric whose name reads lower-is-better printing a RISE in the favourable colour.


## `prose_precision_lint` — layer 11

It blocks a Takeaway reading "gross margin 1.95pp" three lines under a table printing
`+2.0pp`. `prose_percentage_advisory` exists to catch a FABRICATED prose percentage and its band is
deliberately wide — within 2pp of any printed figure counts as the same figure, rounded — so it
cannot see a precision mismatch at all, and it is non-blocking besides.

Scope is DELTA columns only (Y/Y, the sequential column under whichever cadence heading it carries,
vs-Cons, Change), and the restriction is the point. A level
or a consensus mean quoted precisely in prose is normal and honest — "a mean of 41.37%" beside a
table printing 41.4% misleads nobody. A delta is different: it is the renderer's own computed
output, so prose quoting one at a precision the renderer did not print means the author computed it
themselves, which is the Path-B violation the skill exists to prevent.

Direction matters too. Prose may round a printed delta COARSER ("about 20%"), which is normal
English; quoting it FINER is always wrong and always the author's to fix. Trailing zeros are not
finer — `0.10pp` and `0.1pp` are the same number.

## Positive tests

Proving a layer RUNS and reports ok on a clean note cannot tell a working layer from one that
would pass anything. The install suite (internal edition only) therefore hands each checker an
input it MUST reject and asserts it returns something, so a neutered checker fails immediately.


## Entry points and flags

Every flag the package accepts, so no escape hatch is used without understanding what it
disables.

| entry point | what it does |
|---|---|
| `render_wrap.py <config.json> <out.pdf>` | the build. Runs all 23 gates and all 11 layers, fails closed |
| `validate_wrap.py <note.html>` | re-runs the layers on an emitted note, to iterate on prose without re-rendering |
| `self_check.py` | the install suite, including the positive tests (internal edition only) |
| `kpi_gate.py --pick-primary` / `--pick-secondary` | choose a KPI mechanically from the tagged universe via TAG_RANK, instead of by judgement |
| `kpi_gate.py --check-mcp "<Primary KPI>" "<name1;name2;...>" [provenance]` | grade one declared Primary KPI against a pasted universe. It REFUSES a single generic word |
| `kpi_gate.py --selftest-mcp` | the ladder's own offline regression cases. No file and no MCP call anywhere in the loop |
| `preflight.py` | the local environment check: fonts, renderer, logo, page fit. It takes **no arguments** |
| `check_masthead.py <rendered.pdf> [--brand <name>\|<path/to/brand.json>]` | re-measure the masthead. `--brand` takes EITHER a brand name to measure or a path to a brand spec to read: an existing file is a spec, anything else is the brand line |
| `sector_resolve.py <subindustry.txt> <get_actuals.json> <get_guidance.json>` | emit the `sectorProfile` block. **Three file arguments**, not one: the subindustry label, the `get_actuals` payload and the `get_guidance` payload |
| `sector_sweep.py <universe.csv>` | maintainer tool (internal edition only): sector-row coverage over a ticker universe |

**`preflight.py` has no MCP check.** It is a LOCAL environment check and cannot get an MCP
client: the MCP is reached through the model's own tool calls, not from a script. `--check-mcp`
and `--selftest-mcp` are REFUSED by name with the place the verification actually happens. **That place is the run itself**: `search_tickerid`
is the first call of every build, its `match_type` is recorded in `identityProbe`, and the
`identity` gate blocks anything that is not an exact match. That IS the connection test.

**`INSYNC_GATE_MODE=warn`** downgrades the TWELVE config-shape gates to stderr warnings so a config
saved before a block existed still renders. A migration aid, never a way to ship. It cannot reach
`kpi_gate`, `consensus_gate`, `snapshot_contract`, `unit`, `palette`, `page-fit`, `validation`,
`graded_column`, `table_figures` or `staleness`.

**`INSYNC_FACE_WIDTHS`** overrides the font metric table. It exists only so the pagination tests can
run on a box without Carlito installed. Never set it for a real build: the page-fit gate would then
be measuring a font the renderer is not using, and a note that fits in the test would overflow in
the PDF.
