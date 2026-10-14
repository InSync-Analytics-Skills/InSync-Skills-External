# Pipeline contracts — input.json / computed.json / narrative.json

The shapes below were validated end to end against a hand-built portal. To see a
populated example of any file, run the pipeline once and read what it writes.

## input.json

```
{
 "meta": {
  "ticker","ticker_id","company",
  "call_timing",               // 'after_close' (default) | 'before_open' — set from the ET
                               // timestamps on InSync earnings-release filenames (get_document_tree);
                               // wrong/absent for a pre-market reporter = every reaction a day late
  "quarters": { "py_q","prior_q","target_q","next_q" },        // e.g. 2Q25/1Q26/2Q26/3Q26
  "fiscal":   { "fy_prev","fy0","fy1","fy_end_month" },        // "2025","2026","2027"
  "prints":   [ {"q","call_date"} x6, oldest first ],
  "upcoming": { "q","call_date" },
  "asof":     { "estimates","market","peers" },                // ISO dates
  // OPTIONAL, for an issuer that REPORTS in one currency and LISTS in another
  // (e.g. reports CNY, lists HKD). Absent - every USD and every single-currency issuer -
  // nothing converts and output is unchanged.
  // OPTIONAL. THE COMPLETENESS ESCAPE, and the only way to clear a [completeness]
  // warning for data that genuinely does not exist. Without it an operator who re-fetched a
  // window and got nothing has no way to say so, and Step 6's "resolve every [completeness]
  // warning" becomes unsatisfiable - which is a shipping blocker, not a nuisance.
  // Accepts an LTCODE ("SD_P_1035") or an exact "<LTCODE>|<PERIOD>" pair
  // ("IS_P_101|3Q25"). Every completeness site honours it. Use it ONLY after re-probing and
  // getting an empty result, and say in the footer which codes were confirmed absent.
  "absent_confirmed": ["SD_P_1035", "IS_P_101|3Q25"],
  "currency_price": "HKD",            // ISO code the PRICE series is quoted in. Declare it when
                                      // the series carries no token and the issuer is non-USD:
                                      // that is exactly where a listing/reporting split hides.
  "fx_price_to_report": 0.92,         // multiplier turning ONE price-currency unit into the
                                      // reporting currency. Converts price, the 52-week range
                                      // and the daily closes - every price-quoted leg, together.
                                      // Percentage moves are ratios and are unaffected. Market
                                      // cap and EV are NOT converted (the payload never says
                                      // which basis the vendor returns them on); they are
                                      // cross-checked against price x FDS instead.
  "fx_as_of": "2026-09-09",           // rate date - printed on the card
  "fx_source": "...",                 // printed on the card
  "buckets":  { "pre","post","interq" },                       // ISO dates. NOT offsets: each is the
                               // DATE OF THE RECORD §5 selects, which the column header then prints.
                               // pre = last record dated <= prior-print MINUS 1 DAY (the T-2 column, and
                               // that record can be WEEKS older than the fetch window); post = last
                               // record <= prior-print +7d; interq = last record <= midpoint(prior
                               // print, today). §5 is authoritative; this is a DATE, not an offset
                               // (an offset would exclude the legal prior-print-1d record).
  "growth_bases": { "py_q_prior_year","prior_q_prior_year","next_q_prior_year",
                    "py_q_prev_q","prior_q_prev_q","fy_prev_prior" },   // fiscal labels
  "metricset": {                              // <- produced by references/resolve_metricset.py
   "fin_metrics": [ {name,ltcode,fmt} <=5 ],   // fmt: "$M" | "$" (per-share) | "%" | "#" (unit/volume count)
   "top_kpis":    [ {name,ltcode,fmt} <=5 ],
   "eps_ltcode","ocf_ltcode","capex_ltcode","fcf_ltcode","debt_ltcode","cash_ltcode",
   "classification","source":"insync-mcp",
   "multiples": [ {label, ntm_metric, stm_metric} <=3 ],       // from valuation_rules.json
   "gate": { ok, top_kpi_count, fin_count, notes:[] }
  }
 },
 "mcp": {
  "estimates_latest": [rows],   // targetQ/nextQ/FY0/FY1 for metricset codes + FCF ltcode
  "revision_series": [rows],
  "preprint_snapshots": [rows], // last-6-print windows for every Summary Financials code, newest row per code per window is enough
  "actuals": [rows], "guidance": [rows],
  "market": { "current":[], "yr_summary":[], "daily_30d":[], "reactions":[] },
  "valuation": { "current":[], "pe_summary":[], "pe_monthly":[] },
  "balance_sheet": [rows]
  // "peers" group RETIRED (Peer Comps card removed) - do not fetch it;
  // compute tolerates its absence
 }
}
```

Rows are raw MCP response objects (VALUE strings like "$197.1785B", "52.28%",
"(0.1%)" are parsed by the script; envelopes/nesting tolerated — rows are found by
signature: LTCODE / metric / DATE+ADJUSTED_CLOSE / GUIDE_TEXT / MEAN+MAX).

**MARKET ROWS ALSO NEED `TICKER_ID`.** The signature above is what compute uses to FIND a
market row, but `indexMarket` then buckets it by `String(r.TICKER_ID)`. Compute falls back to
the subject ticker for an unlabelled row (the skill is ONE TICKER PER CALL by rule, so an
unlabelled close can only be the subject's), so a missing field does not null the reactions or
raise a false `[completeness]` warning. Paste
`TICKER_ID` through anyway when the MCP returns it: it is what makes a stray peer row
detectable.
Estimates rows need UPDATEDATE; actuals rows must NOT have one. If a base actual is
only available as a DB growth field, back-solve it and mark `SOURCE_DERIVED`.

## computed.json (script output — the only legal source for portal numbers)

Sections: `valuation`, `earnings_setup`, `summary_financials` (rows + growth +
guidance_target_q + guidance_fy + show_fy_guide_column), `revisions`, `beat_history`
(prints, avgs, whisper), `guidance_evolution` (blocks + reactions), `guidance_live`
(incl. `stale` list = freshness-rule rejects, commentary-only), `peer_comps`
(rows + stats), `market_expectations` (two_year + irr grid), `warnings`.

## narrative.json (LLM prose; HTML inline tags <b>/<i> allowed; numbers must exist in computed.json)

`header.print_note`,
`setup_summary[2-3]` — THE SETUP bar: short factual bullets, major things ONLY,
KPI-DRIVEN: bullet 1 = the KPI swing factor(s) the print turns on (comp sales,
segment revenue, operational metric vs its guide); revenue/EPS consensus vs guide
comes second; beat/reaction record + stock position third. A small summary, not a
grid or thesis, and driven by KPIs, not revenue/EPS.
`thesis` (one-paragraph fallback if setup_summary absent; keep both),
`summary_bullets[]`, `setup_bullets[]`, `expectations_bullets[]` (split ideas into
short bullets rather than writing long ones), `beat_note`, `revisions_note`,
`evolution_note`, `call_commentary{bullets, red_note}`,
`tone_shift{tag, header_prev, header_latest, rows[{dim,prev,latest}], note, note_color}`,
`sell_side{rows[{theme,concern,pattern}], watch[]}`, `footer`.
RETIRED — do not write: `setup_groups`, `market_notes`, `peer_footnote` (their cards
were removed; nothing renders them). The footer must not reference peer
comps or the IRR model.

Style: FACTUAL institutional register — report what the
data says, no inferences, no jargon, no invented framing; neutral
third person (never "we"); no em-dashes anywhere in prose; one idea per sentence,
sentences under ~15 words where possible; no bullet over ~3 rendered lines (~160
chars) — split instead.

## Resolving the metric set from the MCP (replaces the company master xlsx)

There is NO company master file. `meta.metricset` is produced by
`references/resolve_metricset.py`, which the LLM feeds a `metricset_input.json`:

```
{
 "tickerinfo": { ticker, ticker_id, company, subindustry, industry, latest_fiscal_quarter },
  // The SECTOR field has several spellings: search_tickerid returns `insync_subindustry`;
  // `subindustry` and `industry` are also read. resolve_metricset.py reads ALL of them
  // (`sectorOf()`), so either spelling works - but prefer `insync_subindustry`, the name the MCP
  // actually returns. A miss here silently downgrades the document to the DEFAULT lens set.
 "top_kpi_rows": [ {name, ltcode, tags, sample, size} ],   // search_lineitem filter=top_kpi, queries=all
 "spine": {                                        // search_lineitem filter=standard, per query
    "revenue":[rows], "gross_margin":[rows], "ebit":[rows], "ebitda":[rows],
    "eps":[rows], "fcf":[rows], "ocf":[rows], "capex":[rows], "debt":[rows], "cash":[rows]
    // a bucket SUPPLIED EMPTY means "probe proved no consensus": fcf:[] -> fcf_ltcode null
 }
}
```
Each row is `{name, ltcode, tags, sample?, size?}`; the RESOLVER then emits
`{name, ltcode, fmt, polarity, flow?, pctrev?}` per metricset row. `flow` is
stock-vs-flow and gates the share-of-revenue sub-row plus the growth rows; **`pctrev`** is the second
axis — whether a SHARE OF REVENUE is a meaningful ratio for the
line at all — and is emitted as `false` only for capital-deployment and written-premium
families (see `PCTREV_DENY` in resolve_metricset.py). An expense or cost line is
deliberately still allowed a share of revenue: a cost line above 100% of revenue is a
real pre-profit cost ratio. Both are absent on a metricset frozen before they existed, and
their absence reproduces the prior behaviour exactly. `guided_ltcodes` (optional,
passed to the resolver not carried in the metricset) is the list of ltcodes the company
currently guides; it lets a GUIDED margin win the margin slot and a GUIDED KPI lead the KPI
block, and it is a TIEBREAK — it re-ranks rows the Step 0.2 searches already returned and
can do nothing for a code none of them found.
 MCP envelopes/nesting are tolerated
(the resolver digs for arrays of such rows anywhere in the object). `sample` is the probed
consensus VALUE string for the row ("1.50%", "$1,000,000M") — the
resolver types `fmt` from its shape before falling back to the name ladder; `size`
is the target-quarter consensus as a plain number and ranks KPI rows by ORDER OF
MAGNITUDE on EVERY path, not only the segment fallback. Write `size` in the row's OWN reported units with symbols,
separators and the scale suffix (`T`/`B`/`M`/`k`/`%`) stripped
(`"$80,000M"` → `80000`, `"KRW90.0000T"` → `90.0`, `"3,000k boe/d"` → `3000`); do not
rescale UNITS, but DO express rows on a common scale when one format's rows carry different
suffixes, or the smaller line outranks the larger. The resolver normalises a
recognised suffix itself as a backstop. Magnitude bands are compared only between
rows sharing a `fmt`, so a mixed-unit block is safe. Both optional; omitting
them falls back to name-based typing, which is less reliable. Then:
```
python3 <skill-dir>/references/resolve_metricset.py metricset_input.json metricset.json
```
Paste `metricset.json` verbatim as `meta.metricset`. Note `computed.json`'s own
`meta` block additionally carries **`seq_label`** — compute's label for the sequential
sub-row (`Q/Q Growth`, `H/H Growth`, `Seq. Growth`), derived from the target period's shape.
The renderer reads it; you never supply it. The resolver is deterministic:

- **top_kpis** — every `top_kpi`-tagged row, deduped by ltcode, ranked revenue/sales
  drivers first, then profit lines, then by tag weight (driver_kpi > earnings_flash_kpi
  > industry_kpi) then ltcode ascending; capped at 5.
- **fin_metrics** — canonical spine: Total Revenue -> adjusted EBITDA (else EBITDA / adj
  EBIT / EBIT) -> adjusted EPS -> headline margin (EBIT margin, else gross margin) -> FCF.
  Adjusted (Non-GAAP) variants win via the `adjusted` tag. Banks/financials: when no EBITDA/EBIT line exists, the profit slot falls back to the ordered
  bank ladder `PPNR (FTE) -> PPNR -> PBT (Ex-1x) -> PBT -> Net Income (Ex-1x)`, and the
  margin slot takes the chosen profit line's `-M` variant, else the Efficiency Ratio.
  Fires only after the industrial regexes miss; non-bank output is byte-identical.
- **fmt** — inferred deterministically: the `sample` VALUE's shape first (ends "%" ->
  `%`; currency WITH an M/B scale mark -> `$M`; currency WITHOUT one -> per-unit `$`;
  bare magnitude, no currency mark -> never `$M`); else the ltcode/name ladder:
  ltcode ending `-M`/`-Y`/`-Q` or a Margin/%/Ratio/NIM/ROTCE/ROA/ROE/ROIC/ETR/Growth
  name -> `%`; EPS / per-share codes -> `$`; unit/area vocab -> `#`; else `$M`.
- **multiples** — from `assets/valuation_rules.json`, matched on `insync_subindustry`
  (or `industry`), else the `default` rule. Order = primary, secondary, monitor.
- **gate** — non-zero exit (no top_kpi universe, or < 3 spine metrics) is the refusal.

Exit non-zero => STOP; do not build a portal without KPI authority.

## compute_portal.py exit codes

A non-zero exit is a deliberate REFUSAL, not a crash. Nothing is written to
`computed.json`, and the stderr message names the block at fault, what depends on it, and
which step produces it. Never patch around one; fix the input it names.

| exit | meaning | what to do |
|---|---|---|
| `0` | computed | read the warnings; resolve every one before Step 5 |
| `1` | **usage** — the script was called without its required arguments | every bundled script takes explicit paths; nothing defaults to a filename |
| `2` | **input contract** — `input.json` is not valid JSON, or a required `meta` block is absent (`metricset`, `metricset.fin_metrics`, `quarters`, `fiscal`, `growth_bases`, `buckets`, `prints`, `upcoming`, `ticker_id`) | the message names each missing block and its step; a document built without any of them is wrong in ways no completeness warning can describe |
| `3` | **currency basis** — one line item arrived in two currencies, which happens when several ticker_ids are batched into one `get_estimates`/`get_actuals` call | re-fetch one ticker per call (§0 of `data-collection.md`) |
| `4` | **top-line slot** — `meta.metricset.fin_metrics[0]` is a balance-sheet or deferred item | `fin_metrics[0]` is the denominator of every "% of Total Revenue" sub-row and the base of the whole growth grid, the beat series and the whisper; re-run Step 0 with the issuer's consolidated top line first |
| `8` | **pre-flight input errors** (`validate_input.py` only — compute never returns 8) | run `python3 references/validate_input.py input.json [metricset_input.json]` BEFORE compute. It checks the SEMANTIC contract compute cannot see — compute gates only on PRESENCE of nine `meta` blocks, so it asks "is `growth_bases` there?" and never "are those the right periods?". Checks: period coherence (`py_q` one year back AT THE SAME PERIOD, `growth_bases` agreeing with `quarters`, a uniform cadence, the FY ladder), the four legal `fmt` values and polarity, the ≤5 caps, foreign `TICKER_ID` rows in the four indexed arrays, per-code target-period coverage, bucket order and future dates, mixed 1H/2Q labels, price-basis declaration on a non-USD issuer, `absent_confirmed` entries that still have data, and print ordering/future dates. Optionally pass `metricset_input.json` for the `sample`/`size` evidence checks — those fields live on the resolver's INPUT, so they are invisible from `input.json` |
| `6` | **wrong listing** — `meta.ticker` carries an exchange suffix whose market prices in one currency while the fetched price series is in another, i.e. `search_tickerid` resolved a DIFFERENT LISTING than the one requested | it does this silently and still reports `success`: a local code can return its US ADR on a fuzzy match, with no `exchange` field in the response to catch it and `region` no help. The substitute prices in a different currency from the fundamentals it serves, so every per-share and valuation figure is out by the FX cross in a document that looks entirely normal. Re-resolve with the local code AND its suffix and verify by the DATA's currency, not the symbol; or build the ADR deliberately, with `meta.ticker` set to the ADR symbol so the check passes honestly |
| `7` | **no consensus at this cadence** — not one metricset row has a consensus estimate for `meta.quarters.target_q` | seen on issuers that carry quarterly ACTUALS but only half-yearly and annual ESTIMATES: the call returns `data:[]` — an empty result, not an error — for a quarterly period. The refusal names the cadence that DOES have coverage; rebuild `meta.quarters` and `meta.growth_bases` on it and say so in the footer. Without the refusal the run would build a blank consensus column, no beat history, and completeness warnings chasing estimates that do not exist. A document where SOME rows lack a target-quarter estimate is normal and still builds; only a TOTAL absence refuses |
| `5` | **scope** — the target period has already reported (actuals exist for BOTH the top line and the profit line) | this skill builds a PREVIEW; use `earnings-review` for the post-print grid, `earnings-wrap` for the narrative, `earnings-flash-skill` for a quick read. If the quarter has NOT reported, the actuals are a mislabelled restatement: fix `meta.quarters.target_q` or the actuals fetch |

A single early actual, a pre-announcement or a partial print does NOT refuse: it raises a
`[scope]` warning and still renders, because a partial print must not block a preview an
analyst still needs.

## `computed.json` → `citable`

`compute_portal.py` publishes
`citable: { values: [...], b_ok: [...], pct: [...], counts: [...], count: n }` — every number
this document PRINTS. `validate_narrative.py` checks the prose against this and nothing else;
`b_ok` is the >=1000 subset that may be quoted in billions, and it is the ONLY scale
conversion the gate allows. `pct` and `counts` are the unit-narrowed sets: a
percentage may only be cited against `pct`, a physical-unit figure only against `counts`.

**`citable.guide_prior`.** A SEPARATE set holding the parsed figures of
`guidance_live.stale` — the prior calls' guides. It is NOT part of `values` / `pct` / `counts`
and does not loosen the freshness rule: `validate_narrative.py` admits it for `tone_shift`
paths and nowhere else. It exists because Management Tone Shift's `prev` column is defined to
hold the prior call's guide, and without a citable home the only way past the gate was to wrap
a data figure in quotation marks and borrow the transcript-quote exemption.

**Precision.** The index does NOT hold rendered values: it holds the STORED figures —
`-20.3254382886` for a cell printed `−20.3%`. The gate matches a citation against those, and
a rounded citation passes because the rounded form of a stored figure is compared numerically,
so for a MONEY or a COUNT citation both precisions still validate. **Cite at the precision the
document shows.** **For a PERCENTAGE that is ENFORCED:**
a `%` citation is compared at a MINIMUM of 1 dp, the precision the renderer prints. Comparing
at the CITATION's own precision would let a percentage written at 0 dp collapse the index into
101 buckets, so a fabricated figure lands in one far more often. The cost is that idiomatic
rounding does not pass: "up 98% year over year" is refused when the page prints 98.2%. The
refusal NAMES the printed neighbour, so the fix is one edit. A figure that is genuinely management's
words rather than a cell - a guided tax rate, a gross-margin framing - belongs in quotation
marks, which is what Step 4 requires of a transcript quote anyway.** Adding
the rounded forms to the array would make the set dense enough that a fabricated figure
validates, and a looser gate is a worse gate.

**Freshness.** Superseded guides (`guidance_live.stale`) and
EVERY outlook row (`guidance_live.outlook`, from any call, the latest included) are deliberately
NOT citable — the gate's premise is that you may only cite what the document prints, and the
document prints neither (compute says so in its `[guidance-freshness]` warning), which keeps several superseded
figures for one item from all validating as current. To reference one,
attribute it as a quote naming the call ("on the 2Q26 call management guided $2.40"); quoted
spans are exempt from the number scan and listed for review.

What it covers: the valuation card (with `dividend_yield` scaled to the percent the card
shows), `earnings_setup`, every Summary Financials cell and sub-row cell, the dedicated
revenue and EPS growth rows, every guide bound, every revisions bucket and delta, every
evolution cell and reaction, every beat cell and average, the whisper, and the figures
inside the text of the guides the document prints. A $M cell in a row printed in billions
(`money-units.md`) may be cited in that form, e.g. "$0.29B".

Why it exists: walking every leaf of `computed.json` and adding v, v/1000, v*1000 and |v|
for each gives thousands of admissible numbers for a document printing a few hundred. It lets
a fabricated "$600 million" through on a `reaction_pct` of 0.6, and at the same time refuses
"0.75%" that the valuation card prints, because the fraction 0.0075 is what the file stores.

## `meta.identity` (REQUIRED)

`{ match_type, score, input_query, resolved_ticker }`, copied from the `search_tickerid`
row the operator accepted (§1 of data-collection.md). It records HOW the ticker resolved,
which nothing else in input.json does.

`validate_input.py`: absent -> WARNING; `match_type` other than `exact_ticker` -> ERROR
(exit 8); `resolved_ticker` disagreeing with `meta.ticker` -> ERROR. `meta.identity_override`
(a non-empty string) is the escape hatch for a deliberate ADR or local-line substitution.

Why it exists: a document can be internally perfect and about the wrong company. The server
can return a different issuer at `status: success` on a fuzzy match, even at scores near 99,
and a legitimate rename also changes the symbol - so
only the match TYPE discriminates.
