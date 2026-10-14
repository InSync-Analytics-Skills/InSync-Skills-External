---
name: revision-analysis
description: Build a "Consensus Revision Analysis" Excel workbook for any public company that reports quarterly, from InSync MCP data — how the InSync consensus estimate has MOVED for the ONE upcoming (next unreported) quarter, for ~30-55 key line items (segment revenue, main P&L, top KPIs, key BS & CF lines). Earnings-anchored chronological columns (Pre-Print, then T+10 and T+40 once elapsed AND distinct from their neighbours, then Current) with live Δ$/%Δ-since-print formulas, YoY/Seq growth, Low/High/broker-count dispersion, delivered as a SINGLE Revision Summary tab. Use whenever the user asks for a revision analysis / revision sheet / estimate revisions / consensus changes / "how have estimates moved" / "what has the street done since the print" for a ticker, mentions upgrades-downgrades to consensus numbers, or invokes /revision-analysis with a bare ticker. Trigger even on a terse "revisions for [TICKER]" or "[TICKER] revision sheet in excel".
metadata:
  argument-hint: TICKER
  version: 2.5.0
---

# Consensus Revision Analysis Workbook

**Invocation with a ticker:** $ARGUMENTS — a bare ticker (or company name) is the target company. Run the whole pipeline autonomously; the only reasons to stop and ask are (a) the ticker resolves to more than one company, (b) the company has no InSync estimates for its next unreported quarter, or (c) the company reports half-yearly (its periods read `1H26` / `2H26`, not quarters): this skill covers quarterly reporters only, so say so and stop before fetching anything else (the prepare script refuses a half-year period too).

This skill produces ONE Excel workbook with a SINGLE sheet — `Revision Summary` — built as a **90% buy-side product**: the revision data is the evidence, the visible output is investment intelligence. It covers the next unreported QUARTER and the ANNUAL that quarter rolls into.

**What the sheet shows.** Left, the evidence table: a `NEXT QUARTER - {q}` block and a `FY{fy1}` block, each `Pre-Print | [T+10] | [T+40] | Current | Δ$ since print | %Δ since print | YoY % | Seq % | Low | High | Broker cnt`, in the fixed order Income Statement → Segment Revenue → Key Metrics (KPIs) → Balance Sheet → Cash Flow. Right, the buy-side panel: a phase status band and FIVE sections (Executive Summary, Estimate Revisions, Key Debate: Variant View, Earnings Expectations, Key Monitoring Variables). There is no second sheet and no footer.

**The scripts own the table and every computed panel line** — anchors and vintages, stale rows, row selection and trimming, the one-flow income statement, the segment roll-up, polarity and colour, the layout. Those rules are written out in `references/script-rules.md`: read it only when the user asks why the sheet shows (or leaves out) something, and never re-create or override one by hand. The judgment lines on the panel are yours, written to `references/narrative-guide.md` (Step 7).

The pipeline has three parts — keep them separate:
1. **You gather data** from the InSync MCP and drop the raw tool results into a `raw/` folder plus a small `meta.json`.
2. **Two bundled Python scripts do all math and layout** — anchor slicing, selection, flow policy, attribution, the styled `.xlsx`. Every table number comes from the scripts; the only numbers you type are the ones in `narrative.json`, and the builder checks each of them against `contract.json` (item 3).
3. **You write `narrative.json`** — the judgment sections of the right panel (the `Result:` thesis, the Executive Summary bullets, the four Variant View pairs and the verdict, the key debate, the Base / Extend / Challenge outcomes, the monitoring variables), STRICTLY number-fenced: every figure you write must appear in `contract.json` (a row's levels, Δ, %Δ, growth, Low/High, broker counts, the reported actuals `actPrint` / `actYoy` / `actFy0`, or a number in `intel`), Base/Extend/Challenge thresholds included, and the builder checks it (`narrative figure(s) are not in the contract` names each one that is not, and the build stops: correct the figure to the contract's and rebuild). Every causal claim must trace to a computed change or a fetched guidance/transcript statement. The full writing rules are in `references/narrative-guide.md`.

Requires: InSync MCP connected, and **Python 3.8+** (standard library only: no Node, no pip installs). Verify with `python --version`; where only `python3` exists, use it in every command below.

## Hard rules (read once, obey always)

⛔ **This skill is SELF-CONTAINED — invoke NO other skill.** Do not load any MCP-usage or helper skill before or during a run, even if one is listed as a "mandatory pre-read" elsewhere: client installs often carry ONLY this skill, and a run must produce the identical workbook there. Everything the pipeline needs from the InSync MCP is in the **InSync MCP essentials** section below; the steps carry the exact tool arguments.

⛔ **NEVER write, port, or inline the prepare/builder logic in the conversation.** Both scripts ship with this skill. If Python is missing, STOP and tell the user — do not reimplement the workbook by hand.

⛔ **NEVER hand-type data rows into a file.** A run pulls thousands of estimate snapshots; transcribing them through your own output corrupts numbers. Data must flow: MCP result → saved verbatim into `raw/` → `prepare_revisions.py`. If you catch yourself typing value rows, stop — you are doing it wrong.

⛔ **History must be fetched with `latest:{enabled:false}` and a date range.** The MCP does NOT support "estimate as of a past date" via `updatedate_end` alone (verified — it returns empty). Every anchor (Pre-Print, T+10, T+40) is sliced from full history by the prepare script. Do not try to shortcut with as-of calls.

⛔ **Fetch `KM_*` Key-Metric codes in their OWN small call — never inside a large mixed batch, for actuals or estimates.** In a large batch of monetary codes, a natural-unit KM code can come back with a currency+scale decoration that does not fit it (a headcount shown as `$…B` instead of `…k`). The mantissa is always right; only the decoration differs. Because the prepare script learns each KM code's true unit from its **actuals** rendering, a clean KM-only actuals call is what keeps every KM level correct. The same call also settles the unit class: a `$` prefix means the metric is monetary (rendered in `$M` like the rest of the sheet), no prefix means a count (people, subs, units — shown in natural units).

⛔ **Page every history call to completion.** When `meta.has_more` is true, keep fetching with `offset += limit` until false, saving EVERY page into `raw/`. A missing page silently truncates the history matrix and corrupts anchor values.

⛔ **Only line items carrying an InSync estimate appear; the scripts pick and trim the row set** (main P&L whitelist, SD revenue rows, KM KPIs, key BS/CF codes; seg/KPI buckets trimmed to 15 by broker count). Your job is to fetch broadly, not to filter.

⛔ **Segment Revenue shows ONLY rows that ROLL UP to Total Revenue — or the honest business split when the feed cannot roll up.** The prepare script decides which segment rows survive: first the split that adds up to revenue exactly in the reported quarters fetched at Step 4b, else the consensus sum-check against `IS_P_101` (duplicates, sibling families, the coverage fallback; the full rule is in `references/script-rules.md`). Still fetch ALL SD revenue codes — the filter needs the full group to judge the roll-up; relay the script's dropped-rows log in your summary if any were dropped.

## InSync MCP essentials (self-contained — do not load any other skill for this)

Single gateway tool: `insync_assistant(mode, task_query, tool_calls)`. Everything below is verified against the live server; it is the complete set of mechanics this pipeline uses.

- **Execute directly.** `mode:"execute"` with `tool_calls` = one dict or a list of dicts, each `{"tool_name": "...", "tool_arguments": {...}}`. **Hard cap: 5 tool calls per request**; the same tool may repeat with different args. `mode:"discover"` (+ `task_query`) exists for schema discovery but this skill's steps already carry the exact arguments — go straight to execute.
- **Parameters are FLAT strings.** Batch by comma-separating values inside one parameter (`ltcode:"IS_P_101,IS_P_128"`, `fiscal_period:"1Q27,2Q26"`, `ticker_id:"6687,IDX0001"`). Never wrap parameters in `queries:[...]`-style lists. Never mix quarterly and annual periods in one `fiscal_period`.
- **Pagination:** every result carries `meta` (`row_count`, `limit`, `offset`, `has_more`, `remaining_items`). When `has_more` is true, refetch with `offset += limit` until false. Set `limit:"100"` explicitly on data pulls (the server rejects a `limit` above 100).
- **Tools used here:** `search_tickerid` (ticker/company → `ticker_id`, `latest_fiscal_quarter`); `search_lineitem` (`ticker_id`, `queries` — max 15 comma-separated keywords, or `"all"` with `filter:"kpi"`; `filter:"standard"` is not allowed with `"all"`); `get_estimates` (`ticker_id`, `ltcode`, `fiscal_period`; `latest:{enabled:true,limit:1}` for the current consensus with `MIN`/`MAX`/`BROKERCOUNT`, or `latest:{enabled:false}` + `updatedate_start`/`updatedate_end` for revision history — never combine date filters with `latest.enabled:true`); `get_actuals` (`ticker_id`, `ltcode`, explicit `fiscal_period` — never `"latest"`, which can resolve to an ANNUAL period); `get_call_date` (`ticker_id`, `fiscal_period`, `start_date`/`end_date` — future dates supported); `get_market_data` (`ticker_id` incl. `IDX0001` = the market index, `start_date`/`end_date`, returns `ADJUSTED_CLOSE`).
- **Identifiers:** LTCODEs are `XX_[P/S]_<num>` (`IS_`/`BS_`/`CF_` statements, `SD_` supplemental/segments, `KM_` key metrics; `-Y`/`-Q`/`-M` suffixes are growth/margin variants — fetch base codes only). Never invent an LTCODE: a code that exists in the vendor's dictionary but not for this company comes back empty (harmless, and a `MISSING DATA` note names it), but a code that does not exist at all fails the WHOLE call with `LTCODE(s) not found in database` (e.g. `SD_P_570`), so request only codes the listings or this skill's fixed lists carry.
- **Values arrive pre-formatted** (`"$1.2500B"`, `"25.03%"`, `"1.24x"`) — the prepare script parses them; you never convert units.
- Calls can be slow (>60s). Packing a request to the full 5 tool calls (Step 4a) is the DEFAULT; only after a request TIMES OUT, split it into smaller requests rather than resending it unchanged (a timeout is not a rate limit, and the same request fails the same way). Never repeat an identical successful call.
- **An empty result is a question, not an answer.** Before treating data as unavailable, make exactly two checks: re-run ONE of its codes on its own (same period, same date window), and run the same request for one neighbouring period. Only if both return nothing is the data unavailable; say which query shape returned nothing. Never fill gaps from memory.

## Step 1 — Resolve ticker, periods, and the print anchor

1. `search_tickerid` with the ticker (and company name if ambiguous). The parameters are `ticker` and `company` — NOT `query`/`queries` (both are rejected). Exact shape: `{"tool_name":"search_tickerid","tool_arguments":{"ticker":"EXMP","company":"Example Company"}}`. Capture `ticker_id`, `company_name`, `insync_subindustry`, `latest_fiscal_quarter`. ⛔ If `latest_fiscal_quarter` is a half-year label (`1H26`, `2H26`), STOP here and tell the user this skill covers quarterly reporters only; make no further calls.
2. ⚠️ **Confirm the last reported quarter via call dates, NOT via `fiscal_period:"latest"`.** That parameter can resolve to the most recent ANNUAL period, which would silently build the whole workbook on the wrong period. Instead call `get_call_date` for `latest_fiscal_quarter` and the quarter after it: if the later quarter's call date has already passed, the just-reported quarter is `latest_fiscal_quarter + 1` (metadata lags on and just after earnings day). The confirmed value is `printPeriod`, and its `CALL_DATE` is `printDate`. Sanity-check with one `get_actuals` call on `IS_P_101` for the explicit `printPeriod` label — it must return a value. ⛔ **PRINT-DAY RULE: a print counts as reported only if its call date is STRICTLY before today, or a consensus snapshot dated on/after the call date exists.** Running on the morning of the call must not roll the workbook onto the next quarter's cycle before the event; use the previous quarter as `printPeriod` in that case. The prepare script enforces it: a `printDate` after `prepared` is refused, and a `printDate` equal to `prepared` is refused unless a snapshot dated on/after it exists.
3. Derive periods by fiscal arithmetic: **`q`** = printPeriod + 1 (e.g. `2Q26`→`3Q26`; `4Q25`→`1Q26`), **`q2`** = q + 1, **`fy1`** = fiscal year containing q (4-digit), **`fy2`** = fy1 + 1, **`yoyQ`** = q one year earlier. q2/fy1/fy2 feed ONLY the forward-curve strip — the detail tables stay quarter-only.
4. `get_call_date` with `fiscal_period` = the last 5 reported quarters (comma-separated, printPeriod first) → **`printDate`** (printPeriod's call date, ISO) and the four prior print dates for the historical-context cycles. If a call date is missing, fall back to the latest `UPDATEDATE` of that period's estimates and note it.

## Step 2 — Enumerate the candidate line-item universe

Three `search_lineitem` calls (run together):
- `queries:"all", filter:"kpi"` — the KPI / segment / operating-metric universe (`SD_*`, `KM_*`). ⛔ **This listing carries KPI-TAGGED rows only** (the server says so in its notes): an untagged segment row — sometimes the company's own reported split — is invisible to it, so the sweep below is required.
- `queries:"revenue, sales, segment revenue, brand revenue, direct, wholesale, corporate revenue, division, geographic, product revenue, services revenue, subscription revenue, license revenue, other revenue, revenue by", limit:"20"` with **NO filter** — the segment-revenue sweep that reaches untagged `SD_*` rows (the server returns the 20 best lexical matches per keyword; keep every `SD_*` row whose name is a revenue/segment line and skip `%`, EBIT, margin, unit and per-share rows). Union it with the KPI listing; duplicates are harmless. Expect it to add sub-splits (channel × region, product × brand) — the prepare script's roll-up filter is built to sort them out (see the segment hard rule).
- `queries:"revenue, cost of goods sold, gross profit, operating expenses, EBITDA, EBIT, net income, EPS, shares outstanding, income tax, operating cash flow, free cash flow, capex, cash, total debt", filter:"standard"` — the primary financials. ⚠️ The server caps `queries` at 15 comma-separated keywords and REJECTS longer lists (a validation error). This string is exactly 15; "interest expense" and "net debt" are deliberately omitted because their LTCODEs (`IS_P_141/142/157/158/1003/1004`, `BS_P_109/115`) are already hard-coded in the fetch list below — the search loses nothing.

Assemble candidate **base** codes (no `-Y`/`-Q`/`-M` suffix): the main-IS pairs (fetch BOTH GAAP and Adjusted twins — 101/102, 103/104, 105/106, 107/108, 109/110, 113/114, **115/116**, 117/118, 119/120, 121/122, **123/124**, 125/126, **127/128**, 1003/1004, plus the interest components 141/142 and 157/158 — `IS_P_102` (Total Adjusted Revenue) is live on only some tickers (a software name, a semiconductor name) and leads the statement there, so always request it; `IS_P_252` (Adjusted Basic EPS) is dropped by design, diluted only — AND any secondary adjusted twins the KPI listing shows, e.g. `IS_S_656` Net Income (Ex-SBC) / `IS_S_657` EPS (Ex-SBC) for internet-platform companies), all `SD_*` revenue codes, all `KM_*` codes, and the key CF codes (`CF_P_108/109/110/101/104/105/106`; for a **REIT** subindustry also `CF_P_113/114/202/203`, FFO and AFFO in total and per share, which the prepare script keeps only when `meta.subindustry` names a REIT). **Banks and insurers** carry their P&L in their own standardized codes, so for a financial name (the `insurance`/`bank` subindustry from Step 1, or these codes in the Step 2 listings) add them: banks `IS_P_200/201/202/207/209/210/211/212` (FTE revenue, NII, provisions, PPNR, non-interest income), insurers `IS_P_402/403/406/410/443` (premiums written/earned, underwriting income, investment income and yield); on any other name they come back empty, so including them when unsure is harmless. Balance-sheet codes ARE fetched: `BS_P_101/141/118/103/111/114/115/108/109` — all three cash variants and BOTH lease bases for debt, because the selection rules need the full candidate set to compare broker depth — plus, for banks and insurers, the financial rows `BS_P_250/251/252/254/276/278/262/113` (loans, deposits, BVPS, CET1, %NCO, equity), which enter only when the sheet resolves no cash line and no debt basis (the four deepest-covered are kept). The prepare script then trims to the final five (or the financial set). Codes the company lacks (but the dictionary has) come back empty — harmless.

⛔ **Do not add opex-component or other unlisted IS codes to the fetch** (S&M `129/130`, G&A `131/132`, SG&A `133/134`, R&D `135/136`, D&A `111/112`, SBC `151`, Depreciation `153`, Amortization `155`, …). The statement is a REGISTERED SPINE: an IS code with no registered position cannot be placed in the P&L and is dropped by the prepare script's spine guard (logged), never appended. Component detail already reaches the reader through the Opex total, the margin takeaways and the EPS bridge.

## Step 3 — Latest estimates for the upcoming quarter (defines the rows)

`get_estimates` with `latest:{enabled:true,limit:1}`, `fiscal_period = q`, `limit:"100"`, the non-KM codes in batches of ≈15–25 plus the KM-only call. These results are SMALL however they are batched (one row per code), so never fill a request with latest calls alone: put two or three of them in a request and fill the remaining slots with the Step 4a forward-curve history calls (`q2`, `fy1`, `fy2`; their code list is fixed and known before this step), which make the combined result large enough to be saved to file (see Saving raw results). These rows carry `MIN`/`MAX`/`BROKERCOUNT` — the dispersion columns of the quarter block (the annual block's dispersion comes from the last FY1 snapshot in the Step 4 history, so every dispersion figure is period-specific by construction). **Save the result verbatim into `raw/`.**

The codes that return a value for `q` are the **selected set** — history is fetched only for those (typically 30–55 codes; a name with a broad segment sweep can reach 70, most of which the roll-up then drops). If the latest result still arrives inline rather than saved to file, do not transcribe it: re-issue the latest calls in a request padded with the first Step 4 history calls.

## Step 4 — Full revision history for the selected codes (period `q` only)

`get_estimates` with `latest:{enabled:false}`, `fiscal_period = q`, `updatedate_start` = printDate − 45 days, `updatedate_end:"today"`, `limit:"100"`, batched ≈10 ltcodes per call so pages stay manageable; up to 5 calls per MCP request. Keep `KM_*` codes in their own batch here too. **Page until `has_more` is false. Save every page verbatim into `raw/`.**

**Then repeat the WHOLE history fetch for `fiscal_period = fy1`** — the annual block needs the same anchors for the same codes. Same window, same batching, same pagination. This roughly doubles the history calls; budget for it. Also fetch `get_actuals` for `fiscal_period = fy0` (the last reported fiscal year) so the annual block's YoY column has a base; `fy0` is derived as `fy1 − 1` unless you set it in `meta.json`.

The 45-day lookback before the print exists so the Pre-Print anchor (last update ≤ printDate−1) is captured even for names that update infrequently. Do not shrink it.

## Step 4a — Intelligence-layer pulls (one or two MCP requests, all saved verbatim into `raw/`)

1. **Forward curve**: `get_estimates` history (`latest:{enabled:false}`, same 45-day-lookback window) for the revenue flavours `IS_P_101,IS_P_102,IS_P_200` (plus `IS_P_402,IS_P_403` for insurers — the curve reads whichever leads the statement, and empty codes are harmless) + the EPS codes (`IS_P_127` and the adjusted twin, e.g. `IS_S_657`), once per extra period: `q2`, `fy1`, `fy2`. (fy1/fy2 rows may already be in `raw/` if fetched before — the prepare script dedupes.)
2. **Historical cycles**: for each of the 4 prior prints, `get_estimates` history for the SAME code list as item 1 (every revenue flavour `IS_P_101,IS_P_102,IS_P_200,IS_P_402,IS_P_403` and every EPS flavour on the sheet, e.g. `IS_P_127,IS_P_128,IS_S_657`) with `fiscal_period` = that cycle's then-next quarter (the quarter AFTER the print), **`updatedate_start` = that print date − 10 days** (so the cycle's PRE-PRINT vintage is captured), `updatedate_end` = print date + (days elapsed since the CURRENT print, capped at 85). The prepare script measures every cycle exactly as it measures the current one — last pre-print vintage → print + the same elapsed days — so the comparison is like-for-like. A cycle with no pre-print vintage falls back to a post-only basis and is tagged; the Cycle rank line and the readthrough sentence print only when every cycle sits on the same basis (`intel.history.uniformBasis`). Each prior cycle is measured on the SAME code as the current headline row (the statement's lead revenue and EPS flavour), with no fallback to another flavour, so a GAAP-flow sheet is ranked against prior GAAP revisions: fetch every flavour, the prepare script picks.
3. **Prices**: `get_market_data` for the ticker AND the market index in ONE call (`ticker_id:"<id>,IDX0001"`), `start_date` = printDate − 7 days, `end_date:"today"`, `limit:"100"`. Parameters are `start_date`/`end_date`; **there is no `market_type` argument** (the server rejects it). `IDX0001` = the market index; resolve other indices with short codes only (`SPY`, `QQQ`) since long names time out. The sheet compares the stock with ONE index: `IDX0001` unless `meta.indexId` names another index's `ticker_id` (Step 5); price rows for any other series are ignored and named in the build log. A window over 100 rows across both tickers comes back in pages (`has_more: true`): page it with `offset` like history and save every page; nothing is dropped.

⛔ **One company per `raw/` folder.** Start every run with a NEW, EMPTY `raw/` folder (and its own `meta.json` and `contract.json`), and never run two tickers in one folder, in sequence or at the same time. The prepare script stops when any estimate or actual row carries a `TICKER_ID` other than `meta.tickerId`, naming the file: a leftover page from another company would otherwise merge into this one's consensus. The only reuse allowed is the same-ticker refresh described under Re-runs below.

**Saving raw results — ONE command shape for the whole run.** Large responses are auto-saved by the harness to a `tool-results/*.txt` file; ingest each one with the bundled helper, never with ad-hoc `cp`/`jq` chains:

```
python <skill>/scripts/ingest_raw.py  <tool-results-file>  raw/  <short_name.json>
```

It copies the file verbatim (no data passes through your output), prints `rows / has_more / remaining` for every result at any nesting depth, and warns when pages remain. It never overwrites: re-ingesting an identical file is a no-op, and a different file under a name already taken is saved beside it as `<name>_2.json` with a warning, so a naming slip cannot silently drop a history page. One command shape means one "Always allow" covers the whole run. Small inline responses (not auto-saved) are written into `raw/` verbatim with the Write tool, which never prompts. ⚠️ Whether a result is saved to file is decided by the app running the skill, not by the skill: in Claude's desktop app a result of about 50k characters or more is saved, anything smaller arrives inline and would have to be hand-copied — the transcription risk this pipeline exists to avoid. Packing more calls of the SAME small kind does not help: latest estimates, actuals, the prior-cycle pulls and prices stay under the threshold even five at a time. Pair each small kind with history calls in the same request (Step 3 with the forward-curve history, Step 4b with history pages, the prior cycles and prices with the `fy1` history), and after any hand-copied file, sanity-check it against the prepare script's per-file row counts before trusting the build. If the environment still prompts per command, tell the user once that allowing `python` commands (or this script's path) makes the rest of the run fully automatic.

## Step 4b — Actuals for the growth columns and the segment history

The batched latest-estimate calls do NOT return YoY/QoQ growth, so the prepare script computes it from actuals: Seq% vs the just-reported quarter (`printPeriod`), YoY% vs the year-ago quarter (`yoyQ`, e.g. `3Q26`→`3Q25`).

One MCP request, five `get_actuals` calls (`limit:"100"`), all saved verbatim into `raw/` (rows without `BROKERCOUNT` are recognised as actuals). A restated quarter can come back labelled `R-1Q25`; save it as it comes.
1. the selected **non-KM** codes for `fiscal_period = printPeriod`,
2. the same codes for `= yoyQ`,
3. the selected **`KM_*` codes only**, `fiscal_period = "<printPeriod>,<yoyQ>"` — a dedicated call, per the KM hard rule above. This one is what fixes KM units for the whole workbook, so never fold it into calls 1–2.
4. **the segment history:** `IS_P_101` plus every selected `SD_*` revenue code, `fiscal_period` = the last 8 reported quarters ending at `printPeriod` (comma-separated, e.g. `"1Q27,4Q26,3Q26,2Q26,1Q26,4Q25,3Q25,2Q25"`),
5. the same call with `offset:"100"` (a segment pool of 20 codes is 168 rows); if call 5 still says `has_more`, page on in the next request.

The segment history is what lets the prepare script recognise the company's own segment split: a reported split adds up to revenue exactly in every quarter, a double-counted set does not. Without it the script falls back to the consensus roll-up, which can keep a segment beside its own sub-rows. Calls 4–5 also make this request large enough to be saved to file.

## Step 5 — Write `meta.json`

```json
{
  "company": "Example Company Inc", "ticker": "EXMP", "tickerId": "1234", "subindustry": "Interactive Media",
  "q": "3Q26", "q2": "4Q26", "fy1": "2026", "fy2": "2027",
  "printPeriod": "2Q26", "printDate": "2026-07-22", "fy0": "2025",
  "nextPrintDate": "2026-10-28",
  "yoyQ": "3Q25",
  "prepared": "<today ISO>",
  "pastCycles": [
    {"label": "1Q26 print (29 Apr 26)", "printDate": "2026-04-29", "targetFp": "2Q26"},
    {"label": "4Q25 print (4 Feb 26)",  "printDate": "2026-02-04", "targetFp": "1Q26"},
    {"label": "3Q25 print (29 Oct 25)", "printDate": "2025-10-29", "targetFp": "4Q25"},
    {"label": "2Q25 print (23 Jul 25)", "printDate": "2025-07-23", "targetFp": "3Q25"}
  ]
}
```
(`updated` is computed by the prepare script — don't set it.) The prepare script checks this file before anything else: `tickerId`, `printDate`, `printPeriod`, `q`, `fy1` and `prepared` are required; dates must be ISO (`2026-09-25`), quarters like `2Q27`, years four digits. A missing or malformed field stops the run with an `Input error:` line naming it: fix `meta.json` and rerun. A missing `yoyQ` is derived from `q` (noted in the log). Optional `indexId`: the `ticker_id` of the index to compare the stock with, when it is not the market index (`IDX0001`, the default).

⛔ **`nextPrintDate` is required** — it decides the earnings-cycle phase, which decides the framing of the whole panel. Get it from the same `get_call_date` call as Step 1 by including the NEXT unreported quarter in `fiscal_period` (companies announce call dates well in advance), or call `get_call_date` with `start_date:"today"`, `end_date:"<+8 months>"`. If genuinely unavailable, omit the key — the phase then falls back to days-since-print alone and a pre-print report will be mis-framed as late-cycle, so say so in your summary.

## Step 6 — Prepare the contract

```
python scripts/prepare_revisions.py  <raw_dir>  <meta.json>  <contract.json>
```

It prints a summary — sanity-check the counts (a large-cap typically yields 35–55 line items, 15–25 history dates) and the `Anchors (cut -> vintage)` line. An `Input error:` line names a `meta.json` field to fix; a stop naming a file means `raw/` holds something wrong (another company's rows, a half-year period): fix the folder, never the script.

## Step 7 — Write `narrative.json`

Read `references/narrative-guide.md` now, every run, then read `contract.json` (`intel.phase.framing`, `intel.dataState` and `intel.thesisSpine` first) and write `narrative.json` to that guide. Every figure must come from `contract.json`. Never skip this file: without it the builder falls back to flat template sentences.

## Step 8 — Build and check the workbook

```
python scripts/build_revision_xlsx.py  <contract.json>  "<OUTPUT>/{TICKER}_revision_analysis_{Q}.xlsx"  <narrative.json>
python references/sanity_check.py  <contract.json>  "<OUTPUT>/{TICKER}_revision_analysis_{Q}.xlsx"
```
(Script paths are relative to this skill — use absolute paths when calling.) The second command checks the built workbook against the skill's invariants; any `FAIL` is a defect to fix before delivery, a `WARN` is a prompt to look.

The build prints a ⚠ line for every narrative weakness its audit finds (repeated claims, unhedged mechanisms, banned words, text cut at the length cap, figures not in the contract). **Treat any ⚠ as a rewrite instruction, not a note**: fix `narrative.json` and rebuild. A figure the contract does not hold stops the build.

## Step 9 — Deliver

Default `<OUTPUT>` folder: the user's `Revision Analysis` folder when one is connected or named (create it if missing); otherwise the working directory, and send the file to the user. If the file is open in Excel and the write fails with `EBUSY`, append `_v2` to the filename.

Tell the user where the file was saved and give the headline read — which way the next-quarter and FY1 consensus has drifted since the print and the top 2–3 movers each way, using the script-computed movers; do not re-derive numbers yourself.

Relay in the chat summary, since nothing of this kind prints on the sheet: every `Audit notes` / `Excluded from the table` line, the segment-coverage disclosure and any unit or growth caution from `contract.scaleNotes`, the dropped segment rows, broker counts below 5 on the latest `Broker cnt` (rows under 3 brokers stay visible but never feed a conclusion), a fresh print with no broker refresh yet, and a missing `nextPrintDate`. When the summary quotes figures, it closes with `Source: InSync MCP.`

**Re-runs / refresh (general rule, added 2026-08-31).** When the skill is re-invoked for a ticker whose `raw/`, `meta.json` and `contract.json` already exist in this session, do NOT repeat the full fetch. Probe first: one `get_estimates` `latest:{enabled:true,limit:1}` call on 4–6 headline codes (`IS_P_101`, the EPS pair, EBITDA, FCF) for `q`, plus one narrow `get_market_data` call for the last few days. If the max `UPDATEDATE` equals the latest vintage already in `raw/` and no newer close exists, the dataset is unchanged: update `meta.prepared` to today, rerun prepare + build from the existing `raw/`, deliver, and say the consensus is unchanged since that vintage. If a NEWER vintage exists, fetch incremental history for all selected codes with `updatedate_start` = the last saved vintage date and `updatedate_end` = today (both periods, same batching and KM separation), save the new pages into `raw/` alongside the old ones (the prepare script dedupes by update date), refresh the latest-estimate and price pulls, then rebuild. Never re-issue a call identical to one already saved.
