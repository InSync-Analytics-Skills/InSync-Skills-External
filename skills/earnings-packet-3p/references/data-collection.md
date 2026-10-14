# Data collection, exact calls, in order

`insync_assistant` accepts at most 5 tool calls per request. Paginate anything with
`has_more: true`.

## 0. Fetch order: searches alone, then groups

A search (`search_tickerid`, `search_lineitem`) goes alone, one per turn. Every keyed request
belongs to a group, sent as up to 3 PARALLEL requests in ONE turn (12 tool calls at most; a
bigger group takes two turns). **Save each group's replies in ONE write, in the same turn as the
next group's requests**, never one reply per turn; only the last group is saved in a turn of
its own. A request whose arguments come from a reply waits for that reply.

| turn | what | waits for |
|---|---|---|
| 1 | `search_tickerid` (section 1), alone | - |
| 2 | `sector.py`, then `search_lineitem` (section 2), alone | industry from turn 1 |
| 3 | `get_call_date` + `get_document_tree` | ticker id |
| 4 | the probe: `get_actuals` + `get_estimates` over the real grid (section 2) | codes, grid |
| 5 | the index search (section 7) and each peer's `search_tickerid`, alone, one per turn | - |
| Group A | valuation (section 8) · market-data windows + index (section 7) · guidance page 1 + the revision series (sections 5, 6) | sector lens, print dates |
| Group B | remaining actuals (section 3) · `fy0`/`fy1` estimates with `MIN`/`MAX`/`BROKERCOUNT` (section 4) · peers (section 9) | probe codes, peer ids |
| then | document extracts (section 10, at most 2 per request), further guidance pages, any split or retried call | the reply each needs |

**Server error (502, 5xx): wait 30, 60, 120, then 240 seconds before each retry, never
longer.** The conversation cache expires after about five minutes, and a longer wait pays to
rewrite the whole context. After the fourth retry, stop and report. On "Rate limit exceeded",
stop and report.

Fiscal period grammar: `1Q25`, `2026`, `1H25`, `9M25`. **`FY26` is rejected**, an annual
period is the bare 4-digit year.

---

## 1. Ticker, calendar, sector

```json
{"tool_name":"search_tickerid","tool_arguments":{"ticker":"<T>","company":"<T or name>"}}
```

**No `mode` on this call, deliberately.** `mode: "company"` is the FALLBACK, used only
after the returned-ticker guard fails, because under it there is no requested ticker to
compare the response against. See section 6. Then:

```
{"tool_name":"get_call_date","tool_arguments":{"ticker_id":"<id>","fiscal_period":"<8 past quarters + target>","limit":"20"}}
```

Capture `ticker_id`, `latest_fiscal_quarter` and **subindustry**, the subindustry
resolves `sector_key`, which drives everything downstream.

**A restated issuer's `latest_fiscal_quarter` is stale.** The pointer stops at the last
period before the `R-` band, so in the restatement fixture the pointer is years old while actuals run to the current quarter. Resolve the
grid from the calendar this call returns plus one actuals probe: `last_reported` is the
newest period whose call date has passed and which returns actuals, and the target is the
earliest period whose call date has not. `references/restatement.py` implements it.

```json
{"tool_name":"get_actuals","tool_arguments":{"ticker_id":"<id>","ltcode":"<revenue code>","fiscal_period":"<newest passed call date>","limit":"20"}}
```

## 2. Line items, resolve, then PROBE

```json
{"tool_name":"search_lineitem","tool_arguments":{"ticker_id":"<id>","queries":"<kw>, <kw>","limit":"6"}}
{"tool_name":"search_lineitem","tool_arguments":{"ticker_id":"<id>","queries":"all","filter":"top_kpi","limit":"20"}}
```

**The parameter is `queries`, not `search_term`**, and it is required; comma-separated,
max 15 keywords, `limit` max 20 and applied per keyword. `queries:"all"` lists only
KPI-tagged items, so a specific keyword search with NO filter is what finds the rest.
**If `queries:"all", filter:"top_kpi"` returns an empty set, the issuer has no tagged KPI
universe and you must fall back to sector keyword search**; it comes back empty for some
issuers, so an empty result is not a reason to stop.

Start with `queries:"all", filter:"top_kpi"` to see what the company itself leads on,
then search keywords for the spine and segment codes.

Then one `get_actuals` and one `get_estimates` over the candidate codes. **Drop every
code that returns no rows for either before going further.** Report what you dropped.
A missing code does not error: the call succeeds and `notes` carries a
`MISSING DATA: LTcode(s) ...` line. Read it every time.

## 3. Actuals

```json
{"tool_name":"get_actuals","tool_arguments":{"ticker_id":"<id>","ltcode":"<codes>","fiscal_period":"<2 periods per call>","limit":"100"}}
```

Cover: the four `hist` quarters, each of their prior-year comparables (the Y/Y bases),
and `fy_prev`. Two fiscal periods per call keeps you under the row limit.

**Read the `FISCALPERIOD` the response returns, not the one you asked for.** A restated
period comes back prefixed, `R-2Q25` for a request of `2Q25`, and **an annual period carries
an index rather than a bare prefix, `R1-2025` for a request of `2025`**, so match `^R\d*-`
and not `^R-`; the bare form misses the fiscal-year label and takes the whole `fy_prev`
column of section 3 with it. The VALUE is correct. Key the
cell on the period you REQUESTED, strip a leading `R-` if you key on the response, and list
the restated periods in `warnings`. **Where BOTH labels arrive for one line item and period,
the unprefixed row wins.** `4Q26` and `R-4Q26` together means `4Q26` is the cell; the restated
row is used only when it is the only row for that period. Left to payload order this is a
silent choice between two different numbers, so make it explicitly. Record the pairs that
arrived both ways and check their Y/Y against the feed's `YOY_GROWTH`: if the rule took a
clean prior-year base while the current quarter exists only restated, that growth spans two
bases and the feed's own figure will not agree with it.

**The flag is per line item, not per period:** in the restatement fixture,
`Total RPO` returns a clean `3Q25` and `1Q25` while every other code returns those same
quarters prefixed, so normalise row by row and never conclude "this period is restated" and
apply it across the grid. Keying on the raw label loses the cell in silence, and
the loss is doubled: the restated band usually spans a full fiscal year, so it takes out
history columns AND the prior-year bases of the quarters after it, which empties both a
level and a growth row on the same line item. In the restatement fixture that is 12 of 16
cells across two line items, none of them an error.

Cross-check the repair for free: `get_actuals` ships `YOY_GROWTH` on each row, computed
against the restated base. A derived Y/Y that matches it confirms the right base was used;
a null derived Y/Y against a populated `YOY_GROWTH` is the signature of a base lost to a
label.

## 4. Estimates

```json
{"tool_name":"get_estimates","tool_arguments":{"ticker_id":"<id>","ltcode":"<codes>","fiscal_period":"<target>,<next>","latest":{"enabled":true,"limit":1},"limit":"100"}}
{"tool_name":"get_estimates","tool_arguments":{"ticker_id":"<id>","ltcode":"<codes>","fiscal_period":"<fy0>,<fy1>","latest":{"enabled":true,"limit":1},"limit":"100"}}
```

`VALUE` is the mean; there is no separate `MEAN` field. `MIN`, `MAX` and `BROKERCOUNT`
drive section 4, a run without them produces a page of dashes.

**There is no InSync house estimate.** `get_estimates` returns sell-side consensus.
Never label a section "vs our estimates" unless the user supplies house numbers.

## 5. Revision series

```json
{"tool_name":"get_estimates","tool_arguments":{"ticker_id":"<id>","ltcode":"<codes>","fiscal_period":"<target>","latest":{"enabled":false},"updatedate_start":"<prior print −10d>","updatedate_end":"<today>","limit":"100"}}
```

**Paginate fully, and widen the window before you decide what the buckets are.** The
canonical four, pre-print, post-print, inter-quarter, latest, assume a vintage cadence
the feed does not always have. Pull from the print BEFORE last, list the distinct
`UPDATEDATE` values, and then choose. If nothing was published between the last print and
today, there is no separate inter-quarter bucket: use the mid-cycle vintage from the
previous quarter, order the columns by real date, and say so in the note. Record the real
dates in `rev_buckets` and the display order in `rev_cols`; the section header prints
them.

## 6. Guidance

```json
{"tool_name":"get_guidance","tool_arguments":{"ticker_id":"<id>","limit":"100"}}
```

Paginated fully, 12–24 months. Apply the latest-Guide-Date rule per
(LTCODE, FISCALPERIOD). Both the quantitative `guidance` array and the qualitative
`outlook` array are used.

**Where "paginate fully" collides with the five-call budget, stop at the newest vintage.**
A request is capped at 5 tool calls and a page at 100 rows (60 is safer), so an issuer with
300-plus guidance rows needs four or five pages for guidance alone, inside a five-call
request, to fetch history the latest-Guide-Date rule then discards. The rule only ever
keeps the NEWEST guide per (LTCODE, FISCALPERIOD), so once every live pair in the packet
window has its newest vintage, further pages cannot change the answer. Stop there.
**Report the offset you reached and the row count**, so a reader can tell a deliberate stop
from a truncated fetch; that distinction is the whole reason the rule is written down
rather than left to judgement.

## 7. Market data, the reaction section

```json
{"tool_name":"search_tickerid","tool_arguments":{"mode":"index","company":"<benchmark index name>"}}
{"tool_name":"get_market_data","tool_arguments":{"ticker_id":"<id>,<index_id>","start_date":"<t-4d>","end_date":"<t+10d>","limit":"60"}}
```

Adjusted closes. You need t-1, t, t+1 and t+5 around each of the last eight print dates,
plus the index on the same dates.

**`limit` on this tool caps at 100, not 500.** A single call spanning two years for two
tickers is rejected outright. Fetch one window per print date instead, roughly t-4 to
t+10 calendar days, which is about 22 rows for two tickers, and run four windows per
request.

**The index resolves to a tracker, not the raw index.** `mode:"index"` for the US market index
returns an `IDX` id whose rows come back with an ETF ticker and `TYPE: "ETF"`. That is
the series to use; say in the note that the comparison is against the tracker and is a
raw difference, not beta-adjusted.

### The benchmark is a DECISION, not a constant

The US market index is the default for a US issuer and **wrong for everyone else**. A Hong Kong
name measured against a US tracker tells the reader almost nothing about whether it moved
with its market, and the note then asserts a comparison the packet did not really make.

**Resolve the local benchmark first, and fall back in this order:**

```
1  the issuer's own home index          the main index of its home market
2  a regional index                     where the home index returns nothing
3  the US market index, DISCLOSED AS A PROXY   last, and say so in notes.reaction
```

Try the index's full name, its short name and its abbreviation before
concluding it is absent. **Where you land on rung 3, `notes.reaction` must say which
benchmark was used and that the local one was unavailable** - a relative column headed
`vs` a US index on a Shanghai issuer is true about the arithmetic and misleading about the
question. Record the rung in `warnings`.

Coverage varies: the US market index resolves; some home indexes return `No matches found`
under every name tried. An absent local index is an InSync coverage gap, not a reason to leave the benchmark
undisclosed.

## 8. Valuation

```json
{"tool_name":"get_valuation","tool_arguments":{"ticker_id":"<id>","mode":"NTM","metrics":"<lens metrics>","start_date":"<-10d>","end_date":"<asof>","limit":"40"}}
{"tool_name":"get_valuation","tool_arguments":{"ticker_id":"<id>","mode":"LTM","metrics":"<lens metrics>","start_date":"<-3d>","end_date":"<asof>","limit":"20"}}
{"tool_name":"get_valuation","tool_arguments":{"ticker_id":"<id>","mode":"LTM","metrics":"<lens metrics>","start_date":"1y","end_date":"<asof>","summarize":true,"limit":"20"}}
{"tool_name":"get_valuation","tool_arguments":{"ticker_id":"<id>","mode":"LTM","metrics":"<lens metrics>","start_date":"3y","end_date":"<asof>","summarize":true,"limit":"20"}}
```

Adjusted metrics first: `P/E_Adj_Ratio`, `EV/EBITDA_Adj`. `EV/Sales` and `P/B_Ratio`
have no adjusted variant. History must be `mode: "LTM"` so it is comparable to the LTM
column.

**`end_date` must be a real date, not "today"**, an end date before the implicit start
date is a validation error.

**An adjusted metric with no data for the issuer can take the whole call down with it.**
`P/E_Adj_Ratio` is reported in `metrics_calculated` and does not appear in
`metrics_failed`, yet returns nothing, and on some server builds every other metric in
the same request returns nothing too. The symptom is `row_count: 0` with a healthy-looking
`meta`. Diagnose by requesting one metric, or one category, at a time:

```json
{"tool_name":"get_valuation","tool_arguments":{"ticker_id":"<id>","mode":"NTM","metrics":"Price Multiples","start_date":"5d","limit":"50"}}
{"tool_name":"get_valuation","tool_arguments":{"ticker_id":"<id>","mode":"NTM","metrics":"Enterprise Value Multiples","start_date":"5d","limit":"50"}}
{"tool_name":"get_valuation","tool_arguments":{"ticker_id":"<id>","mode":"LTM","metrics":"Shareholder Returns,Market Valuation","start_date":"5d","limit":"60"}}
```

**Category first, then by explicit NAME on an empty return.** Requesting by category is
still the right default for the reason below, but an empty category is not evidence of no
coverage: measured, `Shareholder Returns` and `Market Valuation` returned zero rows on an
issuer whose market capitalisation, EV, FCF yield and dividend yield all exist, and the
same four requested as `Market_Cap,EV,FCF_Yield,Dividend_Yield` returned them. Four of the
twelve locked tiles come out of those two categories, so an unretried empty category lands
on the headline strip. Retry by name, then record which form succeeded in `warnings`.

Requesting by category is the robust default: it returns whatever exists and silently
omits what does not. Then fall back to the non-adjusted metric for the one that is dead,
mark the row with an asterisk, and disclose it.

**The metric is `Market_Cap`, with an underscore.** `Market Cap` is rejected as an invalid
metric even though the response labels the column `Market Cap`.

**`P/B_Ratio` in `mode:"NTM"` returns the LTM value**, because book value is not forecast.
Do not present it as forward-looking: leave the NTM cell empty and footnote it.

## 9. Peers

```json
{"tool_name":"get_estimates","tool_arguments":{"ticker_id":"<lead>,<peers>","ltcode":"<codes>","fiscal_period":"<CY periods>","calendarize":true,"latest":{"enabled":true,"limit":1},"limit":"100"}}
```

`<CY periods>` is the current and the next calendar year (they fill `cy26_*` and `cy27_*`).
`calendarize: true` is **mandatory**. Capture each peer's same-store definition and
quarter length for the caveat.

## 10. Documents, risks and catalysts

```json
{"tool_name":"get_document_tree","tool_arguments":{"ticker_id":"<id>","fiscal_period":"<last 4 quarters>"}}
{"tool_name":"parse_document_content","tool_arguments":{"file_paths":"<file_path from the tree>","user_prompt":"<what to extract>"}}
```

**`parse_document_content` takes `file_paths` and `user_prompt`, both required.** There is
no `document_id`: pass the `file_path` string from the document tree verbatim. **One wrong
character is a hard error, not an empty result**, and it costs a call out of the five, so
copy the string rather than reconstructing it, and never normalise its separators or case.
The
`user_prompt` is what the extraction is steered by, so name the lines you care about,
the segments, the guide items and the specific figures; a vague prompt returns a vague
summary and you will have nothing quantified to rank.

Read the last transcript, earnings release and **the periodic filing the issuer actually
files** at minimum. `10-Q` is the US form and the tree carries no such thing for a foreign
filer: a Hong Kong issuer returns Earnings Release, Earnings Presentation and Earnings
Transcript, and a UK or European filer returns a half-year or annual report. Take what the
tree offers rather than looking for a form that does not exist, and name the documents you
read in the report. The release carries the
outlook table as published, which **outranks the structured guidance feed when the two
disagree**; disclose the disagreement rather than silently picking one. **Every risk and
catalyst carries the document and date it came from.** Nothing from the web.

---

## Call-format note

`tool_calls` takes a **single JSON object** for one call, or an array for several. The
single-object form is the safer default; some server builds reject a one-element array
with a pydantic validation error.

## Limits and failure modes, at a glance

| tool | `limit` cap | notes |
|---|---|---|
| `search_lineitem` | 20, per keyword | `queries` required, max 15 keywords |
| `get_actuals` | 100 | 4 periods x 11 codes fits comfortably |
| `get_estimates` | 100 | `latest:{enabled:false}` plus an update-date window for revisions |
| `get_guidance` | 100 | paginate on `has_more`; a 60-row page is safer than a 100-row one |
| `get_market_data` | **100** | one window per print date |
| `get_valuation` | 500 | request by CATEGORY to survive a dead metric |
| `get_call_date` | 20 | returns the upcoming print too |

A request carrying five heavy calls can exceed the 60-second tool timeout. If a batch
times out, it is not necessarily a bad request: split it and retry before rewriting it.
