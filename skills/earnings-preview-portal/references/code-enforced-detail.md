# Code-enforced detail

Full text of the rules the scripts enforce whatever the model does, and the explanations
behind them. **Not read on a normal run**: open it only when a gate refuses or a warning needs
explaining. Each section names the reference file it belongs with.

## From `references/pipeline-contracts.md`

### code-enforced content rules and what compute computes

### Rules 1-2: Summary Financials and Revisions rows

~~~~text
1. **Summary Financials** (Key Financial Metrics + Top Operating KPIs) comes STRICTLY from
   the MCP-resolved metric set (`meta.metricset`, produced by
   `references/resolve_metricset.py` in Step 0) — no assumptions, no substitutions, no
   extra rows. **Key Financial Metrics = `metricset.fin_metrics` ONLY (<=5); Top
   Operating KPIs = `metricset.top_kpis` ONLY (<=5).** The resolver never emits
   supplementary KPIs and caps each list at 5. A Free Cash Flow row exists when the
   resolved `fcf_ltcode` is present (defaults to CF_P_110; the resolver emits `null` —
   and the row is omitted — when the Step 0 estimate probe proved no FCF consensus
   exists for the ticker, e.g. banks; never render an all-dash FCF row).
2. **Earnings Revisions** covers EXACTLY the Summary Financials line items — same
   rows, same order, nothing else.
~~~~

### Rule 3: Guidance Evolution, annual-only guiders, ltcode mismatch

~~~~text
3. **Guidance Evolution** exists ONLY for guided line items that are Summary Financials
   rows (incl. -Y variants). If none qualify the section is OMITTED — never rendered empty,
   and the same holds for Management Tone Shift. KNOWN LIMIT: built from
   TARGET-QUARTER guides only, so an annual-only guider (many banks and retailers) gets no
   card even when its FY-guided items ARE Summary Financials rows; state that omission in the
   footer and cover the FY guide history in Tone Shift and call commentary. (FY guides still
   land in the FY Guide column per Rule 7.) A guide whose ltcode differs from the row's is
   reported by a `[guidance] SAME CONCEPT, DIFFERENT LTCODE` warning, never auto-bound.
~~~~

### Rule 7: guidance cells and guide columns

~~~~text
7. **Guidance cells:** a guidance cell renders in ITS ROW's
   fmt AND magnitude — per-share $X.XX–$X.XX, %-rows X.X%, money at the scale of the
   estimate beside it (`£X.XXB–£X.XXB`), never the old magnitude heuristic. And a guide column sits BESIDE the period it targets: target-Q
   guide right of the target-quarter estimate, FY0 guide right of FY0E ("FY26
   Guide"), FY+1 guide right of FY1E ("FY27 Guide") — never at the far end of the
   table. Each guide column appears only when a live guide maps to a Summary
   Financials row.
~~~~

### input.json assembly

~~~~text
Assemble `input.json` per the contract in `references/pipeline-contracts.md`:
`meta` (quarters, fiscal, prints, buckets, metricset block, growth bases, as-of dates) +
`mcp` groups with **raw response rows pasted verbatim** (envelopes tolerated — the
compute script extracts rows generically and parses formatted value strings itself).
~~~~

### Compute refusals, no hardcoding, what compute computes, WARN triage

~~~~text
**A non-zero exit is a REFUSAL, not a crash**: `2` input contract, `3` mixed
currency, `4` `fin_metrics[0]` is not a top line, `5` the quarter already reported. Fix what
the message names; table in `references/pipeline-contracts.md`.

**Never hardcode a figure anywhere.** You do no arithmetic: every delta, growth rate, margin
and total is re-derived by `compute_portal.py` from the raw rows, and every rendered cell is
linked to its `computed.json` field. A number typed by hand into `narrative.json` that is not
already in `computed.json` is a defect, not a shortcut.

Computes: valuation card (EV = InSync basis, reconciliation check), 30d setup facts,
summary financials grid (metricset fin metrics + a merged Free Cash Flow row + top KPIs
× quarterly + FY−1A/FY0E/FY+1E + Y/Y & Q/Q sub-rows; every $-denominated KPI also
gets Y/Y and Q/Q sub-rows, revenue/sales KPIs get "% of Total Revenue", and an
EBIT/profit KPI gets a "Margin" sub-row — %-format KPIs get none. **Sub-row values
are DB-FIRST: the derived_subrows series (-Y/-Q/-M) win wherever they exist, with
locally computed cells as per-cell fallback; -M items are classified by the DB's own
line-item name ("Margin - X" → Margin, "% Revenue - X" → % of Total Revenue). Fin
rows beyond Revenue/EPS gain sub-rows ONLY where the DB carries the item (e.g. EBITDA
Margin, Capex % of Revenue)**), revision buckets + deltas,
pre-print beats + whisper + reactions (beat series per STRICT RULE 4: a relative
"beat" on a percentage metric is meaningless), guidance parsing with the **freshness
rule** (live = latest source-period call only; stale guides flagged as
commentary-only warnings) and **conditional guide columns** (FY guide cells only for
guides targeting FY0; BOTH the Guidance and FY Guide columns drop entirely unless a
live guide maps to a table row — a non-guider never shows an all-dash
column), guidance-evolution blocks per STRICT RULE 3 — ordered P-before-S /
IS>SD>KM>BS>CF / code-ascending, -Y items derived from base code vs prior-year
actuals, negative-base %-deltas nulled (NM), and all-empty Consensus / vs-Cons rows
auto-hidden. Peer stats and the IRR block still exist in computed.json for reference
but are NOT rendered (retired cards).
**Triage every WARN line before proceeding** — warnings are coded degradation rules
(bad vintages nulled, stale guides, EV basis drift), not noise.
~~~~

### What the narrative gate refuses; citable

~~~~text
Must print `NARRATIVE OK`. It refuses the prose when a number in it is not one the document
PRINTS, when a bullet carries an em-dash, exceeds ~160 characters, uses a banned adjective or
first person outside a quotation, or when the text or a retired key names a removed card.
The gate checks `computed.json`'s **`citable` index**,
`citable.b_ok` the >=$1bn subset quotable in billions (contract:
`pipeline-contracts.md`). **A verbatim transcript quote in quotation marks is exempt from the
voice and adjective rules** — Step 4 requires such quotes and the speaker's words are
evidence, not the portal's view. VOICE, ADJECTIVE **and the NUMBER SCAN** are all lifted inside
one: a quoted figure is reported as a `[quoted-figures]` advisory for YOU to verify against the
transcript, never checked against `citable`. A quote is the one place an unprinted number may
appear, which is why you must verify it. Fix the prose and
re-run; never render past a failure.
~~~~

## From `references/verification.md`

### completeness gate detections and Step 6 text

### COMPLETENESS GATE, with the structural detections

~~~~text
### COMPLETENESS GATE (mandatory, a hard rule)

`[completeness]` warnings mean a value that EXISTS in InSync is missing from the
table only because it was not fetched — a fetch gap, never acceptable to ship. No
derivable Y/Y, Q/Q, Margin, % of Revenue or any other available
number may be blank. The compute script detects gaps structurally:
- a Summary Financials row with a live target-quarter estimate but missing
  py_q / prior_q / FY−1 actuals (these cells also feed Margin and %-of-Revenue
  sub-rows, so this check guarantees those too);
- Revenue, EPS or any $-KPI whose growth-base actuals (prior-year quarters,
  prev-quarters, FY−2) are missing while the numerator side exists — blank
  Y/Y / Q/Q cells;
- a revisions row whose latest value is present but whose Pre/Post/Inter-Q buckets
  are empty;
- an evolution row with a printed Actual but no Consensus;
- a beat-history print with an Actual but no pre-print consensus.
Every such warning names the exact ltcode, quarter and fetch window.

On ANY `[completeness]` warning you MUST:
1. Go back to Step 2 and re-fetch the named codes for the named windows
   (revision_series across prior-print−10d..today paginated fully; preprint_snapshots
   per named quarter at print−21d..print−1d). Add the rows to input.json.
2. Re-run compute. Repeat until zero `[completeness]` warnings remain.
3. Only if a value is TRULY absent from InSync after re-fetching (the tool returns no
   rows) may a "—" stand — and you must say so explicitly in the final report.

NEVER build the DOCX while a `[completeness]` warning is open. Blank cells for
data that InSync actually has are not acceptable in the output.
~~~~

### Step 6 verify paragraph

~~~~text
## Step 6 — Verify (from computed.json + the docx)

No browser needed. The full checklist is `references/verification.md`. In short: compute
exited 0 with a clean WARN list — `[completeness]`, `[currency]`, `[valuation]` and
`[guidance-freshness]` resolved by RE-FETCH; `[check]`, `[coverage]`, `[valuation] BASIS UNIFIED`, `[valuation] LENS SCOPE` and
`[valuation] BOOK VALUE BASIS` resolved by re-fetch or explicit disclosure; no nulls in revisions or
evolution cells that should hold data; the docx text carries the company name, the target
quarter and all 10 section headings MINUS any card Rules 3/4 legitimately omitted, that
omission disclosed; no retired-card
headings, no stray tickers; file
> 50KB and unzips as valid OOXML. `python references/check_masthead.py <rendered.pdf>` also checks masthead geometry when you can produce a PDF; the normative spec is `references/MASTHEAD-STANDARD.md`, measurements in `references/masthead-geometry.md`. **Printed precision:** per-share and money 2 dp, percentages 1 dp, counts 0 dp, multiples 1 dp and 2 dp below 1x.
~~~~

## From `references/valuation-card.md`

### valuation card scope as stated in SKILL.md

### Valuation card scope

~~~~text
**VALUATION CARD SCOPE (the slots are the SECTOR LENS SET).** Multiple slots
come from `meta.metricset.multiples` (from `assets/valuation_rules.json`), Dividend Yield and a
positive FCF Yield appended when a rule omits them so no card sheds a line; adjusted-first per
lens, basis recorded, a lens InSync lacks DROPPED not dashed, and each one cross-checked
against the same ratio recomputed from the page's own cells — `NM` on an order-of-magnitude
break. `NM` also for a negative FCF Yield or a Dividend Yield of exactly 0. `Net Debt / EBITDA`
shows when net debt is positive and EBITDA exists. Always request BOTH `P/E_Ratio` and
`P/E_Adj_Ratio`. The printed Enterprise Value is the BRIDGE (market cap + net debt) so the
card's own lines reconcile; the vendor EV and the gap are recorded as `ev_insync` / `ev_gap`
and warned above max(0.1%, $50M). Detail: `references/valuation-card.md`.
~~~~

## From `references/beat-history-rules.md`

### STRICT RULE 4 as stated in SKILL.md

### Rule 4: Beat History & Reaction

~~~~text
4. **Beat History & Reaction** uses revenue + the 1st BEAT-ELIGIBLE Top Operating KPI.
   Eligible = absolute AND directional: skip %-format AND `polarity: 'neutral'`
   rows — a loan book above consensus could be growth or risk, so printing it as a beat
   asserts a view the data does not carry. None eligible: revenue only; compute
   substitutes and warns. **The AVERAGE and the whisper rest only on prints where the beat
   is a genuine consensus variance** — five ordered tests, all in code: broker floor,
   vintage coherence, range breach, two population guards on that breach, and an outlier test
   against the issuer's own median. Excluded prints keep their cell (grey, asterisked,
   captioned with the all-prints figure) but leave the average. Full rule and disclosure
   fields: `references/beat-history-rules.md`. Needs `MIN`/`MAX`/`BROKERCOUNT` on every
   snapshot row, fetched per §7 WITHOUT `latest`.
~~~~

## From `references/subrow-rules.md`

### STRICT RULE 5 as stated in SKILL.md

### Rule 5: database-first sub-rows

~~~~text
5. **Sub-rows are DATABASE-FIRST:** the DB's own -Y/-Q/-M derived items win wherever
   InSync carries them and their consensus values are authoritative; FIXED importance
   defaults are computed only where the DB lacks them (Revenue & EPS -> Y/Y + seq;
   EBITDA/EBIT -> Y/Y + Margin; every currency KPI -> Y/Y, seq and % of Total Revenue).
   A computed "% of Total Revenue" NEVER appears on a non-currency (`#`), a per-unit (`$`),
   a balance-sheet stock (`flow: false`), or a row the resolver marks `pctrev: false` —
   capital deployment and written-premium production. A negative share renders NM.
   %-format rows get no sub-rows. Full rule: `references/subrow-rules.md`,
   `references/resolver-ladders.md`.
~~~~

## From `references/money-units.md`

### STRICT RULE 8 as stated in SKILL.md

### Rule 8: money units

~~~~text
8. **Money units: ONE MAGNITUDE PER ROW, carried on the cell** (`₹1.25T`) — the Wrap/Flash vocabulary; decimals widen 2→4 dp so a row's own cells stay distinct. Rule, rationale and gates: `references/money-units.md`.
~~~~

## From `references/reporting-frequency.md`

### reporting-frequency paragraph

### Reporting frequency

~~~~text
## Reporting frequency (read before Step 1)

QUARTERLY, HALF-YEARLY (`1H26`) and ANNUAL-only (bare year) issuers all run — see
`references/reporting-frequency.md` whenever `latest_fiscal_quarter` is not `nQyy`. **The
prior-YEAR comparable is one year back AT THE SAME PERIOD** (`1H26` → `1H25`, not `2H25`); 6 prints is THREE YEARS for a half-yearly filer. `[frequency]` and `validate_input.py` check it.
~~~~

## From `references/resolver-ladders.md`

### stop conditions and Step 0 resolver detail

### Stop conditions and the financial spine

~~~~text
Run autonomously. Stop only if (a) the ticker resolves ambiguously, or (b) **the MCP has no
financial spine for the ticker** (`resolve_metricset.py` exits non-zero) —
that means the ticker lacks an estimated REVENUE line, an
estimated PROFITABILITY line **of any kind** (EBITDA/EBIT for industrials; PPNR / PBT
/ adjusted net income for financials, FFO/AFFO for a REIT — the resolver's ladders match
these automatically), AND/OR an estimated EPS line, leaving fewer than 3 core
metrics. **Missing line-item VOCABULARY is not missing DATA:** a bank with no line item
literally named "EBITDA" is NOT a stop case — a ladder fills the profit slot and the run
proceeds. Only when every ladder also matches nothing — a genuinely spine-less ticker — report
"insufficient financials in InSync for <ticker>" and stop. A MISSING top_kpi tag is NOT
a stop condition: many names (e.g. several Asian tickers) carry no `top_kpi` tag at all. In that case the resolver falls back
to segment/operating line items for KPIs, and if there are none it degrades to a
financials-only portal (see Step 0). Only an absent financial spine blocks the run.
~~~~

### Step 0.1: top_kpi universe and fallback

~~~~text
1. **Top Operating KPIs** — `search_lineitem` (`queries="all"`, `filter="top_kpi"`).
   The returned rows (name + ltcode + tags) ARE the KPI universe. **If it comes back EMPTY** (some tickers, notably
   several Asian names, are un-tagged), the resolver falls back automatically: it
   derives KPIs from the segment/operating line items in the financial-spine fetch
   (Step 0.2) — e.g. product-line or geography revenue. You
   may also pass `kpi_fallback_rows` (a `filter="kpi"` retry, or extra segment-revenue
   `filter="standard"` queries) to enrich the pool. If no segment/operating lines exist
   either, the portal is built financials-only (no KPI rows) rather than refused.
~~~~

### Step 0.3-0.4: resolver ranking, fmt inference, multiples, GATE

~~~~text
`top_kpi_rows` + `spine.{revenue,gross_margin,ebit,ebitda,eps,fcf, ocf,capex,debt,cash}`) **filing EVERY row each query returned, not the row you judged best — the resolver ranks, you do not**, then run BOTH in order: ``` python3 <skill-dir>/references/validate_metricset_input.py metricset_input.json && python3 <skill-dir>/references/resolve_metricset.py metricset_input.json metricset.json ``` The first is a **hard gate**: it refuses a revenue bucket that does not OFFER `IS_P_101`, and a `spine.cash` holding something that is not cash, and warns when a financial/REIT run carries no `-M` twin (run §2 batch 7, then declare `tickerinfo.margin_twin_probed`). Filing one pre-chosen row per bucket leaves the resolver nothing to rank and can put the document on the wrong top line.
``` The resolver is **deterministic**: it ranks the top_kpi universe and builds the canonical
spine (Revenue -> adj EBITDA/EBIT -> adj EPS -> headline margin -> FCF), falling back to the
FINANCIALS ladder when no EBITDA/EBIT line exists and to the REIT ladder (AFFO/FFO, and
AFFO/Share for the per-share slot) on a real-estate classification — each fires only after the
more general path misses, so other sectors are byte-identical. Rungs, the `flow` / `pctrev` row
axes and the KPI ranking: **`references/resolver-ladders.md`**), infers each row's `fmt` (`$M`
/ `$` per-share / `%` / `#` unit-volume count, e.g. GWh, units, subscribers) from the supplied
`sample` VALUE's shape first (a value ending "%" is `%`; a bare magnitude with no currency mark
is never `$M`), falling back to the ltcode + name ladder when no sample is given, and selects
the valuation multiples from `assets/valuation_rules.json` keyed on the ticker's
`insync_subindustry`. Same MCP responses -> same metric set, every run. 4. **GATE:**
`resolve_metricset.py` exits non-zero ONLY when the financial spine is too thin (fewer than 3
core metrics). A missing `top_kpi` tag does NOT fail the gate — the resolver records
`kpi_source` = `top_kpi-tag` | `segment-fallback` | `none` and emits a NOTE. Read
`metricset.gate.notes`; report it if KPIs came from fallback or are absent.
~~~~

### guided_ltcodes, metricset paste, fmt authority

~~~~text
**`guided_ltcodes` is the LIVE guide ONLY** — the LATEST `SOURCE_PERIOD` `get_guidance` returns, never the whole history (compute already treats an older guide as stale; the two readings can give different LEAD KPIs, and the lead KPI drives THE SETUP's opening bullet and the beat series). A guided LEVEL is not a guided margin: only a literal `-M` code wins that slot.
Paste the resolver's `metricset.json` verbatim as `meta.metricset` in `input.json`.
Everything downstream (Summary Financials, Revisions, Evolution, Beat History) is
driven by this block. The `fmt` field is authoritative for
percent-vs-dollar; never guess format from a display name. Cross-check
`latest_fiscal_quarter` from `search_tickerid` when setting the target quarter.
~~~~

## From `references/MASTHEAD-STANDARD.md`

### Step 5 house format and section order

### Step 5: renderer, house format v1, section order, native chart, converters

~~~~text
## Step 5 — Build the Word document (the ONLY output)

```
python3 <skill-dir>/references/render_docx.py computed.json narrative.json "<assets dir>/<TICKER> <Q>Q<YY> Preview Portal vN.docx"
```

Output dir: `outputs/` under the current working directory, or the session outputs folder on
claude.ai — create it if missing.
Generated portals ONLY, never the skill source.
**Versioning:** scan the outputs folder for the highest existing `<TICKER> <Q>Q<YY> Preview
Portal v*.docx` and write vN+1. **Never overwrite or delete a version** (audit trail).

`render_docx.py` builds the .docx DIRECTLY from computed.json + narrative.json — pure Python,
ZERO dependencies, no Word, no LibreOffice, no browser, so it runs everywhere including
claude.ai. BYTE-identical output also needs Georgia installed; the renderer warns when it is
absent and sizes the masthead for the substitute. It carries the
InSync PDF house format v1 (shared with the other InSync skills): masthead in a #C9C9C9 outline box (logo top-left,
big serif "Earnings Preview" + Prepared / Expected Earnings Date top-right, company (TICKER)
- quarter centre) over a single #E8850C rule; BLACK serif section headings over #E8850C
rules; tables with HORIZONTAL RULES ONLY (#BBBBBB under a header row, #EEEEEE hairline
between body rows, no vertical gridlines and no outer box), #EDEDED header bands, #F5F0E8
zebra on alternate body rows, #F6F3EE callout blocks; SIGN COLOUR on a CHANGE cell that renders
an explicit sign (#1E7A34 +, #B02A1F −) while every LEVEL stays ink #1A1A1A **including a
parenthesised negative**, and a change rounding to
zero at the printed precision takes #6E6E6E and NO sign glyph;
(Estimate)/(Actual) sub-labels on Summary Financials, Calibri numbers, THE SETUP
bar, the 10 sections in locked order (Valuation, Summary Financials, Earnings Setup,
Earnings Revisions, Earnings Expectations, Guidance Evolution, Call Commentary, Tone
Shift, Sell-Side Focus, Beat History — Peer Comps and Market Expectations are retired
and must NOT come back), guide columns beside their target periods. The beat-history
chart is a NATIVE Word chart object (clustered bars + reaction line) drawn from the
data — NO chart image, NO browser capture needed; it renders and is editable in Word
everywhere.

Then `python3 <skill-dir>/references/check_invariants.py computed.json` — must print `INVARIANTS OK`. Validate per Step 6. NEVER use LibreOffice / pandoc / html-docx converters:
`render_docx.py` is the only writer.
~~~~

## From `references/degradation-matrix.md`

### degradation paragraph

### Degradation matrix summary

~~~~text
## Degradation matrix (coded in compute; never stop, never fabricate)

Twelve conditions, each a coded rule rather than a judgement call: no `top_kpi` universe,
no guidance, a missing adjusted metric, a mis-scaled vintage, a guided item with no table
row, no published consensus, fewer than 6 prints, a transcript that will not parse, broker
junk, an untrustworthy pre-print vintage, a balance-sheet stock in the KPI block, a
non-quarterly filer. The full table, with the behaviour for each:
`references/degradation-matrix.md`. Read it before reporting a section as degraded.
~~~~
