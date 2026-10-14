# Tearsheet Workflow — Detailed Execution Steps

## Call construction — run `mcp_guard.py` before every execute that can hang

```bash
python references/mcp_guard.py check '<the tool_calls JSON you are about to send>'
```

| exit | meaning | do |
|---|---|---|
| 0 | safe | send the JSON on **stdout** (it may have been rewritten — the reason is on stderr) |
| 2 | **must split** | stdout is a LIST of groups; send them one after another, not batched |
| 3 | **refused** | do not send. stderr says what to fix |

**A timeout is the one defect class that cannot be caught after the fact.** Every other gate in
this package inspects data that arrived; a timeout leaves nothing behind to inspect. It has to
be stopped at call construction or not at all — which is why this is a script and not a rule in
prose. It issues no network calls; it rewrites or refuses an argument dict.

The traps that apply to THIS skill, all measured:

| trap | cost |
|---|---|
| `search_tickerid(ticker=X, company=Y)` | can hang to the 180 s timeout. The ticker alone returns in seconds; adding `company=` is what hangs |
| `search_tickerid(company=…)` with no ticker | can hang to 180 s on any name that is not InSync's exact spelling (a missing hyphen is enough) |
| two semantic searches in one execute | the 180 s ceiling is **per call, not per tool**. Two ~100 s searches pass alone and die together |
| `country="US"`, `region=…` on `mode="explore"` | 180 s; `region="US"` also means the AMERICAS and pulls foreign-currency issuers into a domestic screen |
| more than 5 calls in one execute | hard server cap |

`search_lineitem` (Step 3, and the widening call) is **semantic**. `get_actuals`,
`get_estimates`, `get_market_data`, `get_valuation`, `get_call_date` and `get_guidance` are
keyed and batch freely — the guard passes a keyed batch through byte-identical. So run it only
on an execute that contains `search_tickerid`, `search_lineitem` or `parse_document_content`;
send a keyed-only batch (at most 5 calls) directly.

`mcp_guard.py learn` records canonical `insync_subindustry` labels for an explore-mode screen.
This skill never runs one (peers are resolved by ticker), so it needs no `learn` step.

The guard never changes WHICH issuer, period, ltcode or metric you ask for — only the FORM of
the request. State lives in the temp directory; set `MCP_GUARD_STATE` per run if you build two
tickers concurrently.

## API Rate-Limit Pacing

The InSync API has a rate limit of ~30 requests per minute. Insert `sleep 3` (via bash_tool) between MCP call groups.

**Mandatory pause points:**
- After Step 3 (search_lineitem filter: "top_kpi") → pause → Step 4 (get_actuals)
- After Step 4 (get_actuals) → pause → Step 5 (get_estimates)
- After Step 5 (get_estimates) → pause → Step 6 (get_guidance)
- After Step 7 (get_market_data) → pause → Step 8 (get_valuation)
- After Step 8 (get_valuation) → pause → Step 9 (peer data)

## Inputs
- **Company**: Company name or ticker symbol (required)
- **Peers** (optional): Comma-separated peer tickers. If not provided, AI selects 5 relevant comparables.

---

## Phase 1: Data Collection (InSync MCP)

### Step 1: Resolve Company
```python
insync_assistant(
    mode="execute",
    tool_calls={"tool_name": "search_tickerid", "tool_arguments": {"ticker": "[TICKER]"}}
)
```
**Capture:** `ticker_id`, `company_name`, `ticker`, `latest_fiscal_quarter`, **`industry`**,
**`match_type`**, **`score`**, **`ambiguous`**

**Resolve by exact ticker where you can.** `match_type`, `score` and `ambiguous` go into
`meta` and are stamped into the document footer (`identity: exact_ticker (score 100)`).
`search_tickerid` screens on a similarity score, and scores of 98.5, 98.83 and 99.14 have each
returned a DIFFERENT issuer at `status: success` — so an inconsistent sheet is not the failure
mode here; a perfectly consistent sheet about the wrong company is. Compute warns on anything
that is not `exact_ticker` / `exact_company`; if `ambiguous` is true, re-resolve by ticker
rather than picking a candidate.

**`industry`, NOT `insync_subindustry`.** `assets/sector_profiles.json` is keyed on the
`industry` field (the MCP's industry level: `Health Care`, `Tech Hardware and Semiconductors`,
`Financial Services`). `insync_subindustry` is one level finer (`Biotech and Pharma`,
`Semiconductors`, `Institutional Financial Services`) and matches almost nothing, which
silently drops the sector's financial rows and valuation suppression. Write it into
`raw_data.json` as `meta.industry`; `compute_tearsheet.py` warns if it is absent.

The `latest_fiscal_quarter` (e.g., "3Q26") is the most recently REPORTED quarter — use this as the anchor for actuals.

### Step 2: Get Earnings Call Dates
```python
insync_assistant(
    mode="execute",
    tool_calls={"tool_name": "get_call_date", "tool_arguments": {
        "ticker_id": "[ticker_id]",
        "fiscal_period": "[latest_q],[next_q],[next_next_q]"
    }}
)
```
**Capture:** Next earnings date, recent earnings dates

**Period calculation:** If latest_fiscal_quarter is "3Q26", query for "3Q26,4Q26,1Q27"

### Step 3: Discover Company KPIs
```python
insync_assistant(
    mode="execute",
    tool_calls={"tool_name": "search_lineitem", "tool_arguments": {
        "ticker_id": "[ticker_id]", "queries": "all", "filter": "top_kpi"
    }}
)
```
**Selection: follow SKILL.md §9.2, not a rule of thumb here.** Any code the MCP returns in the
`top_kpi` universe qualifies whatever its prefix, and for financials that list is largely
`IS_P_*` and `RT_P_*` (an insurer's set can include `IS_P_402` Net Premiums Written), so
excluding them strips the sector's real KPIs. §9.2 is the only selection authority; what
belongs here is the call, not a second opinion about its results.

**If that list looks thin, widen it** with a second call — sector keywords, NO filter, and an
explicit limit. `limit` defaults to **10** and caps at **20**, and it applies PER KEYWORD:

```python
insync_assistant(
    mode="execute",
    tool_calls={"tool_name": "search_lineitem", "tool_arguments": {
        "ticker_id": "[ticker_id]", "queries": "[sector keyword, ...]", "limit": "20"
    }}
)
```

`limit` is **ignored when `queries: "all"`**, which is why the Step 3 call above does not carry
one and this call must. Apply the has-data gate (SKILL.md §9.2) to whatever comes back.

**Capture:** LTCODE values for use in Step 4

`sleep 3`

### Step 4: Retrieve Actuals (Latest Quarter + YoY)
```python
insync_assistant(
    mode="execute",
    tool_calls={"tool_name": "get_actuals", "tool_arguments": {
        "ticker_id": "[ticker_id]",
            "ltcode": "IS_P_101,IS_P_105,IS_P_109,IS_P_114,IS_P_128,IS_P_127,[kpi_ltcodes]",
            "fiscal_period": "[latest_q],[prior_year_q]",
            "limit": "100"
    }}
)
```
**Period calculation:** If latest_fiscal_quarter is "3Q26", prior_year_q is "3Q25".

**Restated periods:** the MCP returns restated prior-year rows tagged `R-3Q25`, not `3Q25`.
Paste the rows exactly as returned and set `meta.prior_year_quarter` to either form —
`compute_tearsheet.py` normalises the `R-` prefix on both sides. Do NOT hand-edit the
FISCALPERIOD values. If every Y/Y column comes back blank, this is the cause.

**Capture for key_financials:**

| Metric | LTCODE | Format |
|--------|--------|--------|
| Revenue | IS_P_101 | $XXX,XXXM |
| Gross Profit | IS_P_105 | $XX,XXXM |
| EBIT | IS_P_109 | $XX,XXXM |
| EBITDA (Adj) | IS_P_114 | $XX,XXXM |
| EPS (Adj) | IS_P_128 | $X.XX |
| EPS (GAAP) | IS_P_127 | $X.XX |

**YoY, vs-estimate, and Gross Margin are computed by `compute_tearsheet.py`** — paste the raw rows into raw_data.json; do not calculate by hand.

**Then make a SECOND, one-code call for `YOY_GROWTH` (v7.2.27).** The MCP attaches
`YOY_GROWTH` / `QOQ_GROWTH` only to a small request, and the multi-code call above is not one.
Measured live on a semiconductor issuer's 2Q26: 16 ltcodes returned no growth field and no note;
`ltcode: "IS_P_101"` with `fiscal_period: "2Q26"` returned a `"YOY_GROWTH"` value (e.g. `"12.34%"`). Paste that enriched row in place
of the wide call's plain one (same code, same period, same value, so it is not a duplicate) —
it is what makes the headline revenue Y/Y QC check real instead of `QC NOT RUN`.

**Capture for operational_kpis:** KPI actuals for the latest quarter + YoY comparison

`sleep 3`

### Step 5: Retrieve Estimates
```python
# Latest quarter consensus (for vs_est comparison)
insync_assistant(
    mode="execute",
    tool_calls={"tool_name": "get_estimates", "tool_arguments": {
        "ticker_id": "[ticker_id]",
            "ltcode": "IS_P_101,IS_P_114,IS_P_128,[kpi_ltcodes]",
            "fiscal_period": "[latest_q]",
            "latest": {"enabled": true, "limit": 1},
            "limit": "100"
    }}
)

# Forward annual estimates (for FY+1, FY+2 EPS and Revenue)
insync_assistant(
    mode="execute",
    tool_calls={"tool_name": "get_estimates", "tool_arguments": {
        "ticker_id": "[ticker_id]",
            "ltcode": "IS_P_101,IS_P_128",
            "fiscal_period": "2025,2026,2027",  # ANNUAL tokens are BARE YEARS — never '2026FY'/'FY26'
                                            # INCLUDE FY0: compute derives FY1 growth from it
                                            # when the MCP returns a null YOY_GROWTH (v2.3.0).
            "latest": {"enabled": true, "limit": 1},
            "limit": "100"
    }}
)
```

**Compute vs_est:** Actual - Estimate for each metric. Format as "+$XXM" or "-$XXM" for absolute, or "Beat"/"Miss" for KPIs.

**Capture forward estimates** for:
- market_snapshot: FY+1 EPS growth rate and estimate value
- operational_kpis: Forward Revenue and EPS estimates

`sleep 3`

### Step 6: Get Guidance
```python
insync_assistant(
    mode="execute",
    tool_calls={"tool_name": "get_guidance", "tool_arguments": {
        "ticker_id": "[ticker_id]",
        "ltcode": "IS_P_101,IS_P_128",
        "fiscal_period": "[next_q]"
    }}
)
```
**Use for:** Context in bull/bear thesis, catalysts section

`sleep 3`

### Step 7: Get Market Data (Price + 52W Range)
```python
insync_assistant(
    mode="execute",
    tool_calls=[
      # Call 1 - today's price
      {"tool_name": "get_market_data", "tool_arguments": {
          "ticker_id": "[ticker_id]", "start_date": "1d", "end_date": "today", "limit": "1"}},
      # Call 2 - 52W range. NOTE: summarize cannot be combined with aggregation="daily";
      # the MCP rejects that pair with a validation_error. Omit aggregation entirely.
      {"tool_name": "get_market_data", "tool_arguments": {
          "ticker_id": "[ticker_id]", "start_date": "1y", "end_date": "today", "summarize": True}}
    ]
)
```
**Capture:**
- Latest price (most recent ADJUSTED_CLOSE)
- 52-week high and low
- Market cap (compute from price × shares, or use from search_tickerid if available)

`sleep 3`

### Step 8: Get Valuation (Current + Historical)
```python
# NTM P/E, EV/EBITDA, EV/Sales — single batched call
insync_assistant(
    mode="execute",
    tool_calls={"tool_name": "get_valuation", "tool_arguments": {
        "ticker_id": "[ticker_id]", "mode": "NTM",
        "metrics": "P/E_Adj_Ratio,EV/EBITDA_Adj,EV/Sales,Dividend_Yield",  # adjusted-first; refetch GAAP variants only if they land in metrics_failed
        # Banking / Insurance / Financial Services / Real Estate: ADD "P/B_Ratio" and drop the EV metrics
        "start_date": "5y", "end_date": "today", "aggregation": "monthly",
        "limit": "200", "offset": "0"
    }}
)
```

**Payload size:** monthly aggregation keeps the response under the tool-result cap. If the
result still overflows to a file, feed that file's rows straight into `compute_tearsheet.py`
(raw_data.json "valuation" array) — never eyeball-summarize a truncated payload.

**Pagination:** 200 rows is one page, not the whole series. At 5 metrics a 5Y monthly history
is ~305 rows, so check `meta.has_more` and increment `offset` by the limit until it is false.
Stopping after page one silently turns a 5Y average into a shorter one.

**Computed BY SCRIPT (not by the model):** current value, 5-year average, PEG, market cap —
all from `compute_tearsheet.py`. The model only pastes raw rows into raw_data.json.

**Valuation metrics slots (6):**
1. NTM P/E — current vs the history average
2. NTM EV/EBITDA — current vs the history average
3. NTM EV/Sales — current vs the history average

The average's WINDOW is named from the series the MCP actually returned, per metric:
`2Y Avg` / `3Y Avg` / `5Y Avg`. A series can cover well under five years, so the label
follows the span rather than assuming it.
4. PEG (NTM) — emitted by compute_tearsheet.py (NTM P/E ÷ FY+1 EPS growth)
5. Next quarter EPS estimate — from Step 5 forward estimates; context = "+X.X% YoY"
6. Dividend yield — ONLY if annualized dividend supplied to compute_tearsheet.py; otherwise the slot is omitted (never estimated from memory)

`sleep 3`

### Step 9: Peer Data Collection

If user didn't specify peers, select 5 relevant comparables:
- Use `insync_subindustry` from Step 1
- Use Claude's knowledge to pick the most relevant peers (not just same subindustry — think about how an analyst would build a comp set)
- Always include the target company as the first row (marked `is_target: true`)

For each peer, fetch:
```python
# Peer ticker resolution
insync_assistant(
    mode="execute",
    tool_calls={"tool_name": "search_tickerid", "tool_arguments": {
        "ticker": "[PEER1],[PEER2],[PEER3],[PEER4],[PEER5]"
    }}
)

# Peer valuations (NTM P/E for each)
insync_assistant(
    mode="execute",
    tool_calls={"tool_name": "get_valuation", "tool_arguments": {
        "ticker_id": "[peer_ticker_id]", "mode": "NTM", "metrics": "P/E_Adj_Ratio",
        "start_date": "30d", "end_date": "today", "aggregation": "weekly",
        "limit": "5", "offset": "0"
    }}
)

# Repeat for EV/EBITDA
# Peer revenue growth: ONE call PER PEER, one ltcode, that peer's own latest fiscal quarter.
# get_actuals attaches YOY_GROWTH only to a narrow request - it is absent on a multi-code
# call AND on a multi-ticker/calendarize call, with no note either way (measured v7.2.27).
#   {"tool_name": "get_actuals", "tool_arguments": {
#       "ticker_id": "[peer_ticker_id]", "ltcode": "IS_P_101",
#       "fiscal_period": "[that peer's latest_fiscal_quarter]", "limit": "3"}}
# Note each peer reports on its own calendar: one peer's 2Q26 can sit beside another's 2Q27.
```

**FOREIGN-LISTED PEERS.** Some non-US lines return nothing from `get_valuation`, and some
return a multiple computed in the listing's MINOR unit — a price in pence over earnings in
pounds gives a P/E about a hundred times too big.

Judge this by the SYMPTOM, not by the ticker. Two guards already catch it and both name a
number, so use theirs:

* a target multiple outside **1.0x-300.0x** trips the VALUATION UNIT CHECK and is suppressed
  with a warning naming the metric and the value
* a peer multiple outside its per-basis sanity bound is discarded from the table, the rank
  and the peer median: ceiling **200x** for every basis, floors per basis — **P/E 1-200x**, EV/EBITDA 0.5x, EV/Sales 0.05x, P/B 0.05x. A small POSITIVE multiple under the floor is
  almost always a currency scale error — get_valuation returned a London-listed peer's P/E as about **0.1**
  (GBpence against GBP, a factor of 100), which printed as "0.1x" and took rank P1 under
  a legend reading "1=best/cheapest of set"

So the rule is: if a multiple comes back implausible, or the series comes back empty, retry
with the US ADR `ticker_id`. If it is still unusable, drop the peer, or use a model-knowledge
approximation prefixed `~` in the comp table and disclosed in the final response.
Target-company data must ALWAYS come from InSync MCP.

Do not add a list of specific broken tickers here: a named-ticker warning goes stale silently
and teaches the reader to skip warnings. The numeric thresholds above do not go stale; the
internal edition's `self_check.js` checks them against `compute_tearsheet.py`.

**Assemble comparable_companies array:**

```json
[
  { "name": "Target (TICK)", "pe": "XX.Xx", "ev_ebitda": "XX.Xx", "ev_sales": "XX.Xx",
    "pb": "X.Xx", "rev_growth": "+X.X%", "is_target": true },
  { "name": "Peer 1 (TICK)", "pe": "XX.Xx", "ev_ebitda": "XX.Xx", "ev_sales": "XX.Xx",
    "pb": "X.Xx", "rev_growth": "+X.X%", "is_target": false },
  ...
]
```

Supply all four bases where the MCP has them — `pe`, `ev_ebitda`, `ev_sales` (v7.2.15) and
`pb` (v7.2.16) — because the headline basis is chosen from the data, not from the sector: a
loss-making peer has no P/E and no EV/EBITDA and EV/Sales is the only basis left, and a
PRE-REVENUE peer has no EV/Sales either, leaving P/B.

**What is actually enforced:** a row where all four are blank is refused by Step 3, with
`no usable multiple — pe, ev_ebitda, ev_sales AND pb are…`, because it contributes nothing to
the table. Omitting an individual field is NOT refused. So write the fields you have and do not
manufacture `"—"` placeholders; for a sector whose EV metrics are suppressed sheet-wide
(Banking, Insurance, Financial Services) supplying them is pointless anyway.

Fetch `P/B_Ratio` in the same `get_valuation` call as the other multiples — it costs nothing
extra and a pre-revenue name cannot be compared without it.


---

## Phase 2: AI Prose Synthesis

Using all collected data, Claude generates these sections. Each must be grounded in the actual data — no generic filler.

### Business Overview (3-4 sentences)
- What the company does, revenue mix by segment
- Key growth drivers and profit engine
- Geographic or market diversification
- Use Claude's knowledge + financial data context

### Bull Case (4 bullets)
- State the evidence supporting the positive view and let the reader conclude.
  Do NOT write "go long", "go short", "must-own" or similar: `eval_tearsheet.py`
  REJECTS recommendation language and the run will fail the gate.
- Each bullet cites specific data (revenue growth rate, margin trend, valuation context)
- Focus on: growth durability, margin expansion, competitive moat, estimate momentum

### Bear Case (4 bullets)
- State the evidence supporting the risk view. Same language rule as the bull case.
- Flag specific risks visible in the data (operating leverage failure, valuation premium, deceleration, guidance risk)
- Include at least one valuation-based bear point (citing current vs historical multiple)

### Key Debates (3 debates)
- Each has a `question` (concise investment question) and `rationale` (2-3 sentences with data)
- Highlight tensions: premium valuation vs growth, margin story vs actual results, near-term risk vs long-term thesis
- At least one debate should reference a specific data anomaly or trend from the actuals

### Supply Chain & Ecosystem
- Key suppliers, customers, technology partners
- Quantify where possible (e.g., "~X% of revenue from...")
- Note supply chain risks if relevant

### Catalysts (4 events)
- Always include next earnings date from Step 2
- Add 3 more: industry events, regulatory dates, product launches, macro events
- Format: `{ "date": "Mon DD", "event": "Description" }`

### Recent News & Signals (4 items)
- Data-driven observations from the financial data itself
- Examples: "Revenue growth accelerated from X% to Y%", "Stock X% from 52W high", "EPS estimate revised up/down X% in 90d"
- Do NOT fabricate news headlines — derive signals from the data

---

## Phase 3: Document Assembly

### 3a: Deterministic compute (MANDATORY)
Paste every raw MCP row into `{work_dir}/raw_data.json` (schema in the compute script
docstring), then:
```bash
python {skill_dir}/references/compute_tearsheet.py {work_dir}/raw_data.json {work_dir}/computed.json
```
Review the printed warnings and surface them to the user.

### 3b: Build the payload (MANDATORY — do not hand-merge)

**Do not assemble `tearsheet_data.json` yourself.** Write your prose to
`{work_dir}/prose.json` — the shape is defined in full in SKILL.md §12 — and run:

```bash
python {skill_dir}/references/merge_payload.py {work_dir}/computed.json \
                                              {work_dir}/prose.json \
                                              {work_dir}/tearsheet_data.json
```

The script starts from the whole of `computed.json`, applies the prose fields on top, and
places `trend` inside each `operational_kpis` row (it is the one model-authored field that
is not top-level, which is why a flat merge cannot do this). It writes nothing if the result
would fail the eval gate, and names what is missing.

`prose.json` may carry ONLY the prose fields listed in SKILL.md §12. A numeric section
written there is **refused, not copied** — every figure on the sheet comes from
`computed.json`. If a merge is refused, fix `prose.json` and re-run this step; do not build
the payload by hand to get past it.

### 3c: Pre-flight eval (MANDATORY — do not skip)
```bash
python {skill_dir}/references/eval_tearsheet.py {work_dir}/tearsheet_data.json
```
Runs after 3b builds the payload and BEFORE 3d. Enforces word/bullet caps and the payload
schema, and prints advisories (thin financial table, all-blank Y/Y). Exit 1 = do not render.

### 3d: Generate the document
```bash
# Run IN PLACE from the skill's references directory — do not copy scripts out.
cd {library_root}/skills/company-tearsheet-skill/references
python generate_tearsheet.py \
  --input {work_dir}/tearsheet_data.json \
  --output {output_dir}/{TICKER}_TEAR_SHEET_{YYMMDD}.docx
```

### 3e: PDF twin + one-page gate (MANDATORY)

```bash
python {skill_dir}/references/render_pdf.py {output_dir}/{TICKER}_TEAR_SHEET_{YYMMDD}.docx
```

Converts the docx to PDF with LibreOffice and counts pages on the RENDERED file. Both the
.docx and the .pdf are deliverables; present them together.

| Exit | Meaning | Action |
|------|---------|--------|
| 0 | One page | Deliver both files |
| 1 | Two or more pages | DO NOT DELIVER. Trim the payload, re-run 3a-3f |
| 2 | LibreOffice unavailable | Deliver the docx; state that the PDF is missing and the page count unverified |

3c's eval caps word and bullet counts before layout. This step measures the artifact,
so it catches overflow the eval structurally cannot: long KPI or company names, a sixth KPI
row, or a wide peer table.

### 3f: Table geometry gate (MANDATORY)

```bash
python {skill_dir}/references/check_table_widths.py {output_dir}/{TICKER}_TEAR_SHEET_{YYMMDD}.docx
```

Must print **TABLE WIDTH INVARIANT PASSED**. It is the only check that reads the table grid
and cell widths out of the finished document, so a column that overflows the 10,800 dxa text
column is invisible to every other step here — including the PDF page count, which a wide
table can pass while the right edge is clipped.

### 3g: Verify output
- Confirm file was created and has non-zero size
- The document should fit on one page — if the business overview or bull/bear bullets are too long, they may push content to page 2. Keep:
  - Business overview: 3-4 sentences max
  - Bull/bear bullets: ~20 words each max
  - Key debates rationale: 2-3 sentences max
  - Supply chain: 3-4 sentences max

### 3h: Present to the user
Deliver BOTH the .docx and the .pdf from the output directory. If `render_pdf.py`
exited 2, say that the PDF is missing and the one-page guarantee is unverified.

---

## Metric LTCODE Reference

| Metric | LTCODE | Notes |
|--------|--------|-------|
| Revenue | IS_P_101 | Total revenue |
| Gross Profit | IS_P_105 | Revenue minus COGS |
| EBIT | IS_P_109 | Operating income |
| D&A | IS_P_111 | Depreciation & Amortization |
| EBITDA (Adj) | IS_P_114 | Adjusted EBITDA |
| EPS (GAAP) | IS_P_127 | Diluted EPS |
| EPS (Adj) | IS_P_128 | Adjusted EPS |

KPIs vary by company — discover them with the Step 3 call, `search_lineitem (queries: "all",
filter: "top_kpi")`. **Selection is governed by SKILL.md §9.2 and nothing here.** Prefixes seen
often, NOT a whitelist:
- `KM_S_*` — Key metrics (e.g., same-store sales, subscriber count)
- `SD_S_*` — Segment data (e.g., segment revenue, segment EBIT)
- `IS_P_*` / `RT_P_*` — the bulk of the `top_kpi` set for financials; these QUALIFY

> Use `filter: "top_kpi"`, not `"kpi"`. `"kpi"` is a REAL accepted filter value meaning
> *any tier*, so it returns a different list rather than erroring: nothing fails, the wrong
> KPIs simply arrive.

---

## One-Page Fit Guidelines

The tear sheet MUST fit on a single US Letter page (8.5" × 11") with 0.5" margins. To ensure this:

1. **Business overview:** 3-4 sentences. No more than 4 lines of text at 14pt.
2. **Key financials:** 7 rows maximum.
3. **Operational KPIs:** 6 rows maximum.
4. **Bull case:** 4 bullets, each ≤25 words.
5. **Bear case:** 4 bullets, each ≤25 words.
6. **Key debates:** 3 debates. Question ≤15 words. Rationale ≤40 words.
7. **Peer comps:** 6 rows (target + 5 peers).
8. **Supply chain:** 3-4 sentences max.
9. **Catalysts:** 4 events max.
10. **Recent news:** 4 items max, each ≤20 words.

If content is too long, prioritize cutting supply chain text and recent news items first.
