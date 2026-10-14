# InSync MCP recipes — earnings-preview-portal (OPERATIVE)

Every call goes through `insync_assistant` with `mode="execute"` and a `tool_calls` list. Fetch in
the groups SKILL.md names (FETCH IN GROUPS) and save each group's replies to
`portal_work/<TICKER>/` in ONE write, in the same turn as the next group's requests. Paginate
anything with `has_more=true`.

**This file is the RECIPE.** Every rule carries an `[R-x.y]` tag; the measurements behind the
rules are kept in the internal edition only. You do not need them to FOLLOW a rule.

Tools: `search_tickerid`, `search_lineitem`, `get_market_data`, `get_valuation`,
`get_estimates`, `get_actuals`, `get_guidance`, `get_call_date`, `get_document_tree`,
`parse_document_content`.

## §0 Rules that must be obeyed before any fetch

**[R-0.1] ONE SEMANTIC SEARCH PER REQUEST. Keyed getters batch up to 5.** `search_lineitem` and
any `search_tickerid` are SEMANTIC: they contend for a 180s ceiling that is per CALL, not per
tool, so two ~100s searches pass alone and die together and one timeout loses every result in
the batch. The `get_*` family is KEYED, returns in about a second, and may go up to the server
cap of 5 per request, never in the same request as a search. If a request times out, re-send it
as 2 smaller requests.

**[R-0.2] `search_tickerid` GOES ALONE, WITH `ticker` ONLY.** The fuzzy-`company` path hangs to
the 180s ceiling on any string that is not InSync's exact canonical spelling. Send
`{"ticker":"<T>"}`; only if it does not resolve, follow with a SEPARATE `{"company":"<short
name>"}` request. Never batch a resolution call with anything.

**[R-0.3] ONE TICKER PER `get_estimates` / `get_actuals`.** Several ticker_ids converts the
WHOLE response to USD, putting a KRW actual beside a USD estimate in one printed row.

**[R-0.4] CONVERSION IS DECIDED PER LTCODE, so one ticker per call is necessary and NOT
sufficient.** Check every response for `meta.conversion`: present means that payload was
converted, absent means local currency. Pick the LOCAL currency as the document's basis and
multiply a converted figure by the stamped rate before pasting. A code you cannot normalise is
DROPPED from the metric set and named in the footer. `MIN`/`MAX` convert independently of
`VALUE` — ledger all three. compute REFUSES a mixed-basis run (exit 3).

**[R-0.5] ANNUAL PERIODS ARE BARE YEARS.** `"2027"`, never `"FY27"` (rejected outright). The
"FY27 Guide" column header is built from the bare year downstream.

**[R-0.6] `get_estimates` VALIDATES LTCODES ALL-OR-NOTHING.** One code the ticker does not carry
fails the WHOLE request and returns nothing for the codes that were fine. Verified: one code the ticker
does not carry killed a batch that also contained valid probes. Send derived
`-Y`/`-Q`/`-M` probes so an unproved code does not share a request with proved ones.

**[R-0.6b] SERVER LIMITS, verified live.** `search_lineitem` caps at **20**
(`'limit' <= 20. Got: 30` killed a whole no-filter batch); `get_estimates` and `get_actuals` cap
at **100** (`'limit' <= 100. Got: 200` killed the §7 snapshot call — the one call the entire
beat-credibility rubric depends on). A rejected call returns nothing, which reads as missing data
rather than a bad argument. Paginate with `offset`; never raise `limit` past these.

**[R-0.7] AMBIGUITY IS NOT ALWAYS REPORTED.** `search_tickerid` can return two different
companies, each `score: 100`, with `"ambiguous": false`. Carry `match_type` and `score` into
`meta.identity`; `validate_input.py` REFUSES anything but `exact_ticker`. Echo the resolved
company name back before fetching anything else.

**[R-0.8] GUARD EVERY BATCH BEFORE SENDING IT.**
`python references/mcp_guard_epp.py check '<tool_calls>'` — exit 0 send what it printed, 2 send
the groups in order, 3 stderr names the trap. `learn <payload.json>` after each §2 batch so the
guard knows which derived codes the database actually carries. `learn` reads every row carrying
an `ltcode` / `LTCODE` key, so hand it the reply as returned; a compacted file without those keys
learns nothing (`"learned_rows": 0`), and the guard then treats every derived code as unproved. It changes a request's FORM,
never its subject.

## §0b Non-US tickers — five rules

1. **`search_tickerid` CAN SILENTLY RETURN A DIFFERENT LISTING.** A local code can return its US
   ADR, with `region:"US"` and `success`. There is no `exchange` field. Pass the LOCAL code with
   its suffix (`"XXXX SW"`, `"XX FP"`), read back the `ticker` string, and STOP if it is not the
   listing you asked for. Verify by the DATA's currency, not the symbol (a European issuer can
   come back under a bare symbol and still be the European entity). [R-0b.1]
2. **ALIGN ON THE `FISCALPERIOD` STRING, NEVER ON `QUARTERENDDATE`.** Some tickers return BOTH
   a half-yearly and a quarterly row for one event, sharing a `QUARTERENDDATE`, one holding six
   months and the other three, both marked `SOURCE_DERIVED:"Financial Release"`. Aligning by
   `QUARTERENDDATE` alone is not safe on these issuers. compute raises `[cadence]`; heed it. [R-0b.2]
3. **A REQUESTED PERIOD THAT DOES NOT EXIST COMES BACK AS SUCCESS WITH THE ROW MISSING.** Never
   zip a requested-period list against returned rows positionally: key every row by its own
   returned `FISCALPERIOD` and diff requested against returned explicitly. [R-0b.3]
4. **`SOURCE_DERIVED` IS NOT A TRUSTWORTHY PROVENANCE FLAG in either direction.** Do not gate
   anything on it; disclose derived quarterly figures in the footer instead. [R-0b.4]
5. **A HALF-YEARLY REPORTER'S QUARTERLY CONSENSUS MAY BE SYNTHETIC** — or absent entirely
   (`data:[]`, not an error). Decide the cadence ONCE, in Step 0, and build the whole document
   on it. [R-0b.5]

**Enforced, not advised:** a listing/currency mismatch is a REFUSAL (exit 6); supply
`meta.fx_price_to_report` + `fx_as_of` + `fx_source` to resolve a reporting/listing split (price,
52-week range and the close series convert together; market cap and EV do not); no quarterly
consensus is a REFUSAL (exit 7) naming the cadence that does have coverage. Set
`meta.currency_price` whenever the price series carries no currency token on a non-USD issuer.
[R-0b.6]

**Operational:** a Brazilian code with a numeric suffix can hang to the ceiling; `get_call_date`
mixes FUTURE scheduled dates in with historical ones and flags neither; `IS_P_101` is not
label-stable (an IFRS filer can return `"Revenue - IFRS"`); a `UIC` never tracks its `LTCODE`;
`CF_P_103` returns zero rows for some ADRs; some fiscal labels lead the calendar by three
quarters. [R-0b.7]

## §0c `meta.absent_confirmed` — clearing a completeness warning honestly

After you have ACTUALLY RE-PROBED a window and got an empty result, add the key to
`meta.absent_confirmed`. It takes an ltcode (`"SD_P_1035"`) or an exact `"<LTCODE>|<PERIOD>"`
pair, and every completeness site honours it. Name the confirmed codes in the footer and say the
section degrades for that reason. **Do NOT use it to silence a warning you have not probed** —
that converts a disclosed gap into an undisclosed one, which is the worst thing this pipeline can
do. [R-0c.1]

## §0d The `[PF]` pro-forma flag — disclose it

`get_estimates` can return a `notes` line asking you to mark values pro-forma. There is no
cell-level mark and inventing one would change every table's geometry, so handle it as a
DISCLOSURE: collect the codes and periods the note names, and say in the footer and in THE SETUP
that the forward consensus is on a pro-forma basis, naming the event if the transcript or guide
text gives it. If the `[PF]` set covers the target quarter, say that Beat History is on the old
basis and the target-quarter column on the new one. If it covers only a few KPI rows, name those
rows. [R-0d.1]

## §1 Ticker and calendar

```json
{"tool_name":"search_tickerid","tool_arguments":{"ticker":"<T>"}}
```
Capture `ticker_id`, company name, `latest_fiscal_quarter`, subindustry. Only if it does not
resolve, a SEPARATE request: `{"company":"<two or three word short name>"}`. [R-0.2]

**`meta.identity` is REQUIRED.** Copy four fields off the accepted `success` row — note the
response calls the symbol `ticker` and the contract calls it `resolved_ticker`:
```json
"identity": { "match_type": "exact_ticker", "score": 100, "input_query": "<what you sent>", "resolved_ticker": "<the row's `ticker`>" }
```
`validate_input.py` exits 8 when `match_type` is anything but `exact_ticker`, or when
`resolved_ticker` disagrees with `meta.ticker`. For a deliberate ADR or local-line substitution
set `meta.identity_override`. [R-1.1]

Call dates — past 6 prints and the upcoming one (a future `end_date` is supported):
```json
{"tool_name":"get_call_date","tool_arguments":{"ticker_id":"<id>","fiscal_period":"<6 past quarters + target, comma-separated>","limit":"20"}}
```
If the upcoming date is not announced, estimate it as "~<prior print date + 1yr week>" and say so
in the footer.

**Reporting cadence.** InSync carries half-yearly (`1H26`) and annual-only (bare year) issuers.
`meta.quarters` must use the issuer's OWN period labels, and the prior-YEAR comparable is one
year back AT THE SAME PERIOD (`1H26` → `1H25`; the prior PERIOD is `2H25` and is a different
thing). `growth_bases` follow the same rule. The §5/§7 windows are print-relative and need no
change. 6 prints is 3 years for a half-yearly filer and 6 for an annual one — say in the read-out
how far back the beat series reaches. A half-yearly reporter often guides ANNUALLY only, in which
case Guidance Evolution is legitimately omitted and the omission must be disclosed. An
annual-only filer has no sequential comparison; the row is still labelled "Seq. Growth". [R-1.2]

**No peer fetch.** Peer Comps is retired and NOTHING renders peer data. If the user
hands you a peer list, park it in `mcp.peers`, which compute tolerates and nothing reads. [R-1.3]

## §2 Line items → metric-set resolution (the MCP is the KPI authority)

**EIGHT batches, numbered as listed. Every reference in this file uses THESE numbers.** Batch 1
is the top_kpi universe; 2–8 are the financial spine. Send them ONE semantic search per request
[R-0.1].
```json
{"tool_name":"search_lineitem","tool_arguments":{"ticker_id":"<id>","queries":"all","filter":"top_kpi"}}
{"tool_name":"search_lineitem","tool_arguments":{"ticker_id":"<id>","queries":"revenue, gross margin, ebitda, ebit, diluted eps, free cash flow","filter":"standard","limit":"20"}}
{"tool_name":"search_lineitem","tool_arguments":{"ticker_id":"<id>","queries":"operating cash flow, capex, total debt, cash and equivalents","filter":"standard","limit":"20"}}
{"tool_name":"search_lineitem","tool_arguments":{"ticker_id":"<id>","queries":"PPNR, pre-tax income, net income, efficiency ratio FTE, combined ratio, FFO, AFFO, underwriting income","filter":"standard","limit":"20"}}
{"tool_name":"search_lineitem","tool_arguments":{"ticker_id":"<id>","queries":"free cash flow, segment operating profit, segment revenue","limit":"20"}}
{"tool_name":"search_lineitem","tool_arguments":{"ticker_id":"<id>","queries":"FFO per share, AFFO per share, same store revenue, same store NOI, occupancy, net operating income","limit":"20"}}
{"tool_name":"search_lineitem","tool_arguments":{"ticker_id":"<id>","queries":"margin PPNR, margin net income, margin PBT, margin FFO","limit":"8"}}
{"tool_name":"search_lineitem","tool_arguments":{"ticker_id":"<id>","queries":"capex, segment margin, margin segment EBIT, adjusted free cash flow, company defined free cash flow","limit":"20"}}
```
**Batches 5, 6, 7 and 8 OMIT `filter` DELIBERATELY.** `filter:["standard"]` excludes the top
lexical match and the server says so in its own `notes`. It can hide an issuer's own headline FCF
definition (the one management and the Street actually discuss), segment operating-profit lines,
`CF_P_108`, a whole KPI axis, a company-specific capex code and a guided segment margin.
When an issuer's headline FCF is a company-specific definition, put THAT code in `spine.fcf`.
[R-2.1]

**Batch 4's margin term is `efficiency ratio FTE`, not `efficiency ratio`.** The bank ladder
pairs the chosen profit line with its own `-M` twin, else `IS_P_206` (Efficiency Ratio FTE), else
a non-FTE one. The plain query returns `IS_P_206` for some banks and not for others — one query,
two ladders. Naming FTE makes it deterministic. compute raises `[metricset] BASIS
PAIRING` when a profit line and its margin disagree on FTE; `check_invariants` INV-6 holds it.
[R-2.2]

**Batch 7 is the profit line's `-M` twin, UNFILTERED.** Run it whenever the profit slot may
resolve through the bank / insurer / REIT ladder. With `filter:"standard"` the margin twins are
UNREACHABLE. A ticker carrying no `-M` twin returns few or no rows, which is correct and
harmless. [R-2.3]

**Batch 4 is NOT OPTIONAL.** The bank ladder's first two rungs (`IS_P_209`, `IS_P_210`) are
unreachable from batches 2–3 at `limit:"20"`. It also supplies the insurer and REIT margin
candidates. [R-2.4]

**Filing.** `top_kpi_rows` = the `filter=top_kpi` rows. `spine.{revenue, gross_margin, ebit,
ebitda, eps, fcf, ocf, capex, debt, cash}` = the matching rows, each query's rows under the
obvious key. Add `tickerinfo` from §1 — `ticker, ticker_id, company, industry,
latest_fiscal_quarter` and the sector under the MCP's own name, **`insync_subindustry`**.
Banks return NO EBITDA/EBIT/Gross Profit rows: that is correct, not a fetch gap — include what
the searches DID return (PPNR, PBT, Net Income Ex-1x, Margin - PPNR, Efficiency Ratio) and let
the ladder pick. File batch 4's rows under whichever spine key fits, INCLUDING a key of your own
such as `bank_profit`; the resolver pools every `spine` key for the PROFIT and MARGIN slots.
File batch 6's per-share twins under `spine.eps` (`CF_P_203` AFFO/Share is the REIT per-share
slot) and the rest under a key of your own such as `spine.reit_ops`.
`revenue`, `eps`, `debt` and `cash` DO read their own named buckets. A bucket the probe emptied
is passed as an EMPTY ARRAY, never deleted — supplied-empty is what makes `fcf_ltcode`,
`ocf_ltcode` and `capex_ltcode` resolve to null. [R-2.5]

**`CF_P_108` does NOT come from the `operating cash flow` query** — it arrives from the `free
cash flow` query in batch 5. File it under `spine.ocf` wherever it turns up. [R-2.6]

**Discard the obvious non-candidates BEFORE you probe.** Drop a returned row when BOTH hold:
(a) it is a balance-sheet or tax/working-capital line that is not one of the four REQUIRED slots
(`debt`, `cash`, `BS_P_113`, `IS_P_125`), and (b) no sector rule in this file names it.
Followed literally, the batches can yield over a hundred gated ltcodes including `BS_P_103` Total
Assets, `BS_P_137` Retained Earnings, `BS_P_126` Goodwill and `CF_P_117` Deferred Income Taxes,
costing several extra `get_estimates` calls to prove codes no preview would ever show. When in
doubt KEEP it: a wrongly-dropped row costs a slot, a wrongly-probed row costs a call. [R-2.7]

**Estimate-availability proving, BEFORE the resolver.** The keep/drop gate for EVERY candidate —
KPI universe AND spine — is consensus-ESTIMATE existence, never actuals. Probe all candidate
ltcodes with `get_estimates` (targetQ, nextQ, FY0, FY1; if empty there walk the FIXED ladder
FY+2 → FY+3 → FY+4 → FY+5 and STOP at the first hit). **The broker minimum is ONE:** a
consensus from one broker is a consensus and the row may enter the metric set; a row with no
broker behind any period does not. Never describe coverage depth in the document. Drop any
row with no consensus in ANY period even if the company reports actuals for it; KEEP rows the
Street models but the company never reports (their actual cells render `n/a`). Pharma/pipeline
lines may only be estimated several years out and must be KEPT. [R-2.8]

**The gate is for rows that RENDER AN ESTIMATE COLUMN ONLY.** Gated: `top_kpi_rows` and the P&L /
cash-flow spine keys (`revenue, gross_margin, ebit, ebitda, eps, fcf, ocf, capex`, plus any key of
your own). NOT gated, keep on ACTUALS alone: `debt`, `cash`, `BS_P_113` (Total Equity) and
`IS_P_125` (FDS) — nothing renders an estimate for them and they feed the EV bridge, net debt and
Book Value/Share. Prove those with `get_actuals`. [R-2.9]

**Carry the probe's evidence into every row — `sample` AND `size` are REQUIRED.**
- `sample` = the probed consensus VALUE string exactly as returned (`"1.50%"`, `"$1,000,000M"`,
  `"1,500k"`). The resolver types `fmt` from the value's SHAPE first and falls back to the name
  ladder only when `sample` is absent.
- `size` = the target-quarter consensus as a plain number. Strip the currency symbol, thousands
  separators and SCALE SUFFIX (`T` trillion, `B` billion, `M` million, `k` thousand, `%`), keep
  the digits: `"$80,000M"` → `80000`, `"KRW90.0000T"` → `90.0`, `"3,000k boe/d"` → `3000`.
  Do NOT rescale to `$M` or to native units.
- **When a ticker's rows carry MORE THAN ONE scale suffix within a format, convert them to a
  COMMON scale first** — pick the largest suffix present and express the rest in it
  (`300.0000B` → `0.3` when the block's common scale is `T`). "Do not rescale" means do not
  change UNITS; it never meant keep incomparable magnitudes. The number need only be consistent
  with the other rows carrying the SAME `fmt`. [R-2.10]

**`guided_ltcodes` — the LIVE guide ONLY.** One extra `get_guidance` call lets the resolver give
the margin slot to a margin the company actually GUIDES. Build the list from the LATEST
`SOURCE_PERIOD` only, never the whole 30-month history: compute's own freshness rule is
"live = latest source-period call", so a wider list hands the resolver codes the same run then
refuses to cite. **A guided LEVEL is not a guided margin** — only a literal `-M` code wins the
margin tiebreak. [R-2.11]

**Feed every LTCODE `get_guidance` returns into the candidate pool.** You are already making the
call. Some guided codes are reachable from NO batch above (a guided segment margin, a
company-defined FCF, the correct `capex_ltcode`). Take the `LTCODE` and `LINEITEM` off every guidance row, add any code not
already pooled, and probe it with the rest. [R-2.12]

Then run BOTH, in order:
```
python3 <skill-dir>/references/validate_metricset_input.py metricset_input.json && \
python3 <skill-dir>/references/resolve_metricset.py metricset_input.json metricset.json
```
The first is a HARD GATE: it refuses a revenue bucket that does not OFFER `IS_P_101`, and a
`spine.cash` holding something that is not cash, and warns when a financial/REIT run carries no
`-M` twin (run batch 7, then declare `tickerinfo.margin_twin_probed`). [R-2.13]

**Revenue has ADJUSTED and GROSSED-UP variants too.** A large bank can return THREE consolidated
top lines from the batch-2 `revenue` query: `IS_P_101` Total Revenue, `IS_P_200` Total Revenue (FTE)
and `IS_P_102` Total Revenue (Ex - 1x Item) — and `IS_P_200` ranks ABOVE `IS_P_101` in the
returned list. **File EVERY row the query returns and let the resolver's rule choose** (prefer
`IS_P_101`, or a row named exactly "Total Revenue"). Filing only the row you judged best removes
the choice. **Do NOT hand-pick metrics or KPIs** — the resolver ranks and caps them (≤5 each)
reproducibly. [R-2.14]

**No `top_kpi`? automatic fallback.** If batch 1 returns empty the resolver derives KPIs from the
segment/operating lines already in the spine fetch. To enrich the pool you may add a
`filter=kpi` retry and/or segment-revenue queries and pass the rows as `kpi_fallback_rows`. With
no segment lines either, the resolver emits a financials-only metric set and still exits 0. It
exits non-zero only when the financial spine itself is missing. [R-2.15]

**The base-code `-Y`/`-Q`/`-M` derived series are fetched in Step 2 into `mcp.derived_subrows`,
for every `$`-format metricset code.** Sub-row values are DB-FIRST: those series win wherever
they exist, with locally computed cells as a per-cell fallback. [R-2.16]

Paste `metricset.json` verbatim as `input.json` `meta.metricset`. **Adjusted priority** is
applied by the resolver: where Reported and Adjusted variants both exist it takes the
`adjusted`-tagged one for EBITDA / EBIT / EPS / margin. If a company guides GAAP gross margin,
keep the GAAP GM code too so it can win the margin slot under the guidance-aware tiebreak.

## §3 Market data & valuation

Five calls; keyed getters batch up to 5 [R-0.1]:
```json
[
 {"tool_name":"get_market_data","tool_arguments":{"ticker_id":"<id>","start_date":"7d"}},
 {"tool_name":"get_market_data","tool_arguments":{"ticker_id":"<id>","start_date":"45d"}},
 {"tool_name":"get_market_data","tool_arguments":{"ticker_id":"<id>","start_date":"1y","summarize":true}},
 {"tool_name":"get_valuation","tool_arguments":{"ticker_id":"<id>","mode":"NTM","metrics":"Market_Cap,EV,P/E_Adj_Ratio,P/E_Ratio,EV/EBITDA_Adj,EV/EBITDA,FCF_Yield,P/FCF_Ratio,EV/Sales"}},
 {"tool_name":"get_valuation","tool_arguments":{"ticker_id":"<id>","mode":"LTM","metrics":"Dividend_Yield,P/B_Ratio"}}
]
```
Then the P/E history the Earnings Setup card needs:
```json
[
 {"tool_name":"get_valuation","tool_arguments":{"ticker_id":"<id>","mode":"NTM","metrics":"P/E_Adj_Ratio,P/E_Ratio","start_date":"1y","summarize":true}},
 {"tool_name":"get_valuation","tool_arguments":{"ticker_id":"<id>","mode":"NTM","metrics":"P/E_Adj_Ratio,P/E_Ratio","start_date":"1y"}}
]
```
File as `valuation.pe_summary` (summarised) and `valuation.pe_monthly`. The `45d` call populates
`market.daily_30d`; the 7-day series is NOT a substitute (it flips the sign of `move_30d_pct`).
Request BOTH P/E codes and BOTH EV/EBITDA codes. `FCF_Yield` belongs on the NTM call — compute
stores it as `fcf_yield_ntm` and the card prints "FCF Yield (NTM)". [R-3.1]

**`pe_monthly` IS A DAILY SERIES AND IT PAGINATES.** ~500 rows across 6 pages at `limit:"100"`,
newest first. Page one alone holds seven weeks and leaves `earnings_setup.pe_year_ago`
unreachable — it is the observation nearest 365 days back and lives on the LAST page. Either page
with `offset` in steps of `limit` until `has_more:false`, or jump to the far end with
`offset:"400"` and keep the oldest rows. Thinning to ~20 dates spanning the year is fine; say in
the footer that the series is thinned. [R-3.2]

52-wk high/low = the summarize MIN/MAX (adjusted close; label it as such). Total Debt / Cash /
Book Value per share via `get_actuals` on the §2 balance-sheet codes at `"fiscal_period":"latest"`.
Net debt = debt − cash; EV cross-checks the `get_valuation` EV.

**`BS_P_113` (Total Equity) and `IS_P_125` (FDS) are REQUIRED** for Book Value/Share, which
compute gates on. **Also fetch `BS_P_108` (Total Debt, incl-operating-lease) into
`balance_sheet`, always** — the EV bridge needs it and the resolver will not ask for it. Without
it the card shows a bridge that does not add up. [R-3.3]

**Financials: fetch a PREFERRED-EQUITY code too.** `bvps` is `BS_P_113 / IS_P_125` — TOTAL equity
over diluted shares — while a vendor `P/B_Ratio` rests on COMMON equity, so the card cannot
reconcile to itself for any issuer with preferred stock. Search `preferred equity, preferred
stock` with `filter="standard"` and put the code in `balance_sheet`. compute DISCLOSES the basis
and records `bvps_implied_by_pb`; it does not change the number. [R-3.4]

## §4 Consensus estimates (latest)

Target Q + next Q + FY0 + FY1, all core codes + KPI codes:
```json
{"tool_name":"get_estimates","tool_arguments":{"ticker_id":"<id>","ltcode":"<core+kpi codes>","fiscal_period":"<targetQ>,<nextQ>","latest":{"enabled":true,"limit":1},"limit":"100"}}
```
Render VALUE with SCALE/CURRENCY (50 + 10^6 + $ → $50M; portal tables are in $M). Capture
`BROKERCOUNT` (compute uses it; the document never states it).

## §5 Revision history → time buckets

For target Q and next Q separately, `latest:{enabled:false}` with an updatedate window:
```json
{"tool_name":"get_estimates","tool_arguments":{"ticker_id":"<id>","ltcode":"<core+kpi codes>","fiscal_period":"<Q>","latest":{"enabled":false},"updatedate_start":"<prior print − 10d>","updatedate_end":"today","limit":"100"}}
```
Buckets by UPDATEDATE: **Pre-print (T−2)** = last record ≤ prior-print−1d; **Post-print (T+7)** =
last record ≤ prior-print+7d; **Inter-Q (~T+45)** = last record ≤ midpoint(prior print, today);
**Latest** = newest. Column headers show the actual bucket dates (MM/DD). A bucket with no record
carries the previous bucket's value forward. **A recent print:** when midpoint(prior print, today)
falls before the Post-print record, Inter-Q has no record of its own, so set
`meta.buckets.interq` to the Post-print date (that carry-forward; compute merges the columns and
says so). `validate_input.py` refuses an Inter-Q date earlier than Post-print.

**WIDEN THE WINDOW UNTIL IT REACHES THE BUCKET — EVERY EDGE, NOT JUST `pre`.** "Prior print − 10d"
is not enough on its own: the last pre-print vintage can predate a window opening at prior
print − 10d, producing `[completeness]` warnings that clear only when the window opens earlier,
and an Inter-Q edge window a few days wide can return `data:[]` while a vintage sits a week
before it. Use `min(prior_print − 10d, pre_bucket_record − one refresh cycle)`, or simply
open at the start of the fiscal year. A bucket genuinely empty in its span is a real finding
(compute merges the columns and says so); a bucket empty because the window was three days wide
is a fetch gap wearing the same clothes. [R-5.1]

**Query craft.** `latest:{enabled:true}` combined with `updatedate_start/end` is UNRELIABLE, not
mutually exclusive: row counts vary wildly across tickers and across repeats of one call. Use `latest:{enabled:false}` with
the window, and re-probe without `latest` before ever concluding absence. Prefer one narrow
window per bucket edge over paginating a bulk page and transcribing it. A 5-call batch on a
segment-rich ticker can overflow the tool-result limit and spill to a file — process the spill on
disk, never by retyping through context. [R-5.2]

## §6 Actuals

**Fetch the ANNUAL actuals explicitly** — `fy_prev` is a printed Actual and the growth grid
computes against it. Bare years [R-0.5]:
```json
{"tool_name":"get_actuals","tool_arguments":{"ticker_id":"<id>","ltcode":"<core+kpi codes>","fiscal_period":"<fy_prev>,<fy_prev minus 1>","limit":"100"}}
```
For a December filer previewing 3Q26 that is `"2025,2024"`; the second year is what
`growth_bases.fy_prev_prior` needs for the FY Y/Y cell. [R-6.1]

Last 6 quarters + **the prior-year quarter of EACH of the 6 prints** + growth-base quarters:
```json
{"tool_name":"get_actuals","tool_arguments":{"ticker_id":"<id>","ltcode":"<core+kpi codes>","fiscal_period":"<6 quarters comma-separated>","limit":"100"}}
```
The full prior-year spine is REQUIRED, not optional: `-Y` guidance-evolution blocks derive every
grid column from the base code vs its prior-year actual.

**PASTE RESTATED LABELS AS THEY ARRIVE, `R-` PREFIX INTACT.** An issuer can return `R-2Q25` /
`R-1Q25` alongside unprefixed 3Q25 / 4Q25. compute resolves restatements itself and warns with both
values; stripping the prefix silently suppresses that disclosure. VALUE arrives as formatted
strings — parse for math, display in $M. [R-6.2]

## §7 Pre-print consensus snapshots (beat history)

For EACH of the last 6 prints, the consensus that existed going INTO that print:
```json
{"tool_name":"get_estimates","tool_arguments":{"ticker_id":"<id>","ltcode":"<rev,profit,kpi codes>","fiscal_period":"<that quarter>","latest":{"enabled":false},"updatedate_start":"<print − 21d>","updatedate_end":"<print − 1d>","limit":"100"}}
```
One call per print, sent with Group B (six calls: two requests in the same turn); save the six
with the group. Beat% = (actual − snapshot)/snapshot.

**WIDEN UNTIL IT REACHES A VINTAGE.** 21 days is a STARTING window, not a verdict: if empty widen
to 45; if still empty widen to the PRIOR PRINT. A window reaching the prior print that is still
empty means the vintage genuinely does not exist — record it in `meta.absent_confirmed` as
`"<LTCODE>|<PERIOD>"`, say in the footer how wide you went, and let compute print `—`. Do not
widen PAST the prior print. **Expect the terminal rung on a monthly-refresh issuer:** some prints
come back empty at 21 AND 45 days and resolve only at the prior print, to a vintage close to two
months before the release. Budget three probes per print there and
say how old the selected vintage is when it exceeds ~30 days. [R-7.1]

**USE `latest:{"enabled":false}` AND TAKE THE DENSEST VINTAGE IN THE WINDOW.** `enabled:true,
limit:1` MANUFACTURES thin marks: measured on the same code, period and window, it returns a one- or two-broker
mark where the window holds a vintage several times denser, and distorts the beat average built
on it. `enabled:false` returns every vintage
in the window, so PAGINATE (limit 100, then `offset`). Do not pick among them by hand: compute
selects the DENSEST vintage on or before the call date, tie-broken on the later date. If a window
genuinely returns nothing, retry with `latest:{"enabled":true,"limit":1}` and say in the report
which prints used the fallback. [R-7.2]

**KEEP `MIN` AND `MAX` ON EVERY SNAPSHOT ROW**, with `BROKERCOUNT`, pasted verbatim. Compute uses
the range to decide whether a print's beat is a consensus variance at all — a bank's revenue
printed 56,000 against a consensus of 50,000 whose whole range was 47,000–55,000, so the "+12.0%
beat" was a definitional break (figures made up). Drop MIN/MAX and that filter silently cannot run. **Each row keeps
its OWN `UPDATEDATE` and `BROKERCOUNT`** — they are not uniform across a print. [R-7.3]

## §8 Guidance

```json
{"tool_name":"get_guidance","tool_arguments":{"ticker_id":"<id>","start_date":"30m","end_date":"today","limit":"100","offset":"0"}}
```
**Pagination stop.** Step `offset` by the `limit` you sent and STOP at the FIRST of: `has_more` is
false — **trust this one FIRST**, because `row_count` counts the guidance AND outlook arrays
together and so comes back ABOVE `limit`; a page returns fewer than `limit` rows; a page returns
zero rows; every row on a page has a `SOURCE_PERIOD` older than your oldest print; or you have
issued 5 pages. Then de-duplicate on `SOURCE_PERIOD|FISCALPERIOD|LTCODE` keeping the LATEST row —
overlapping pages re-return revised rows and a duplicate silently doubles a guidance cell. Say in
the footer if you stopped on the 5-page ceiling. [R-8.1]

`SOURCE_PERIOD` = where guided, `FISCALPERIOD` = the period targeted. Current guide for the target
Q = rows with FISCALPERIOD = target Q from the latest SOURCE_PERIOD. The `guidance` array is
quantitative (parse GUIDE_TEXT ranges: `" - "` separator, parens = negative, $B→×1000 $M, %/bp
tokens, point guides, qualitative text). The `outlook` array is qualitative `[G]` statements.

**File the outlook array under `mcp.guidance_outlook` (or `mcp.outlook`) — NOT inside
`mcp.guidance`.** compute reads `mcp.guidance.outlook || mcp.outlook`; concatenating the two into
the flat `mcp.guidance` array yields `guidance_live.outlook` = 0 rows and every real outlook
figure refused by the narrative gate. They land as `guidance_live.outlook[]` and their figures
become citable, so management's own outlook can be quoted in Call Commentary and Tone Shift.
[R-8.2]

## §9 Reaction prices

Daily closes bracketing each print (print − 3d → print + 5d covers weekends/holidays), one call
per print:
```json
{"tool_name":"get_market_data","tool_arguments":{"ticker_id":"<id>","start_date":"<print−3d>","end_date":"<print+5d>","limit":"10"}}
```
Pre close = last close ≤ print date; post close = first close after it. Next-day move % =
post/pre − 1.

**`meta.call_timing` is REQUIRED.** `'after_close'` (default; pre = close ON the call date) ·
`'before_open'` (pre = the day BEFORE — the print-day session IS the reaction) · `'unknown'`
(NON-US listings). Establish it from EVIDENCE: the ET timestamps InSync stamps on earnings-release
filenames in `get_document_tree` (`..._06.06.00_ET.pdf` = before the 09:30 open →
`'before_open'`). Hard-coding after-hours measures every pre-market reporter a day late and
flips most signs. If timing cannot be established on a US listing, omit the field
and say so in the report. **Set `'unknown'` for a non-US listing with no ET-stamped release:**
compute calculates BOTH readings, shows the after-close one, publishes the other as
`guidance_evolution.reactions_alt`, and warns with each print that changes sign. [R-9.1]

## §10 Peers — RETIRED

Do NOT fetch peer market data, peer valuations, or calendarized CY revenue; nothing renders them
and compute tolerates the absence of the `peers` group entirely.

## §Transcripts

```json
{"tool_name":"get_document_tree","tool_arguments":{"ticker_id":"<id>","fiscal_period":"<last 4 quarters>"}}
```
Use exact file paths from the tree. `parse_document_content` takes a **`file_paths` array**
(plural), one path per element even for a single document.

**How many passes, and which are mandatory.**
- **A (latest call) and B (prior call) are MANDATORY — 1 call each.** A feeds Call Commentary and
  the guidance framing; B is the ONLY source for the Tone Shift table, so without it that section
  must be omitted under the degradation rule, not filled from A.
- **C is 4 calls, one per transcript**, REQUIRED for the "Watch on this call" bullets. Fewer than
  4 transcripts in the tree is a degradation, not an error: run one pass per transcript that
  exists, synthesize across what you got, and state the count in the footer.
- **6 calls total on a full run. Never batch them** — `parse_document_content` parses each file
  independently and a multi-file request returns per-file non-answers. [R-T.1]

**The passes are defined over TRANSCRIPT files.** A release, a presentation or a 10-Q carries no
Q&A and no management commentary, so parsing one in place of a transcript builds Tone Shift from
a press release. Documents over ~5 MB time out or elide in-band with NO error — check
`size_bytes` from the tree. Measured across 10 trees: transcripts top out at 2.89 MB; the files
that breach 5 MB are presentations and 10-Qs. [R-T.2]

**A. Latest call — short-term & structural** (transcript file):
user_prompt: "From this earnings call extract: (1) every forward guidance number with its exact framing (revenue, margins, opex, tax rate, segments) for the next quarter and fiscal year; (2) management's explanation of demand, supply constraints, pricing, and product-cycle timing for the next 1-2 quarters; (3) anything stated for the FIRST time on this call (new targets, new disclosures, new programs) vs long-standing repeated themes; (4) topics management deflected, declined to quantify, or answered indirectly, with the question asked; (5) notable cost/opex/capex commentary. Quote key numbers exactly. Structure as bullet lists under headings 1-5."

**B. Prior call — tone comparison** (transcript file):
user_prompt: "Extract this call's guidance numbers and management's characterization of: revenue outlook, margin trajectory, demand environment, key geographic/regulatory exposures, and opex/investment plans. Short bullets with exact numbers — this will be compared side-by-side against the following quarter's call."
Tone-shift dimensions = the 5 rows that best differentiate the two calls for THIS company
(revenue guide / margin outlook / demand / a company-specific axis / opex is the usual set).

**C. Last 4 calls' Q&A — ONE CALL PER FILE, then synthesize yourself:**
user_prompt: "From the Q&A section only: list the analyst question themes on this call (theme, what analysts are really worried about, and how management responds or deflects), the numbers management declined to quantify, and any first-time disclosure. Short bullets, exact numbers."
Then across the four returns: recurring themes, what appeared for the FIRST time on the most
recent call, and where management's answer pattern changed. "Watch on this call" bullets = the
open items: unquantified deflections, numbers due for update, first-time disclosures likely to be
probed.

If `get_document_tree` returns nothing usable, apply the degradation rule in SKILL.md — never
substitute web knowledge.

## §11 Paste hygiene

**NEVER paste a peer batch into `estimates_latest`, `revision_series`, `preprint_snapshots` or
`actuals`.** Those four arrays are indexed `LTCODE|FISCALPERIOD` with no ticker discriminator by
design. One foreign row can put another issuer's figure in a target-quarter revenue cell. compute drops
rows whose `TICKER_ID` is not `meta.ticker_id` and names them, but the currency gate cannot help
here — a US peer shares the subject's currency. Peer data belongs in `mcp.peers`. [R-11.1]

**PASTE RESTATED ROWS AS THEY ARRIVE, `R-` PREFIX INTACT.** Do not strip the prefix and do not
drop the original. Stripping turns a disclosed restatement into an undisclosed one decided by
array order. [R-11.2]

**PASTE `GUIDE_TEXT` VERBATIM.** Do not tidy a guide into a dash range. A hand-tidied guide loses
the issuer's own words and can silently change the figure (a `+/- 2%` tolerance rewritten as a
dash range prints a nonsense band). [R-11.3]
