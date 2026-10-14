# Rules the scripts enforce

**Read §8R before writing prose or choosing peers (SKILL.md §8); read the rest when a warning
names a rule.** Every rule below is applied by `references/compute_tearsheet.py` and reported in
a warning at the moment it fires, with the real figure in it. `SKILL.md` carries what YOU must
do; this file carries what the code decides for you.

---

### 7.7 Row availability substitution
A prescribed row the MCP does not populate degrades to its nearest equivalent rather than
vanishing (`FIN_FALLBACK`): Gross Profit (Adj) → Gross Profit; EBIT (Adj) → EBIT;
EPS (Adj, ex-SBC) → EPS (Adj) → EPS (GAAP); EBITDA (Adj) → EBITDA → **Net Income**;
Net Income (Adj) → Net Income.

The map is declared in both directions, but the prescriptions are adjusted codes (SKILL.md
§9.1), so the walk runs adjusted → reported; the basis policy is enforced by the `BASIS:` audit
in `compute_tearsheet.py`. The Net Income tail matters for balance-sheet-led issuers where the MCP
populates no gross profit, EBIT or EBITDA at all. A THIN FINANCIALS
warning fires below `min_fin_rows` (default 5, `MIN_FIN_ROWS`).


### 7.9 PEG needs a positive, non-trivial denominator

The slot prints `n/a` **with the reason**, never a bare dash and never a number, when:

| FY1 consensus EPS growth | slot | why |
|---|---|---|
| negative | `n/a` | a negative PEG is not a ratio anyone can read |
| zero | `n/a` | no denominator |
| positive, PEG above `PEG_MAX` (10x) | `n/a` | near zero growth the ratio is set by the denominator, not the valuation |
| positive, PEG rounds below `PEG_MIN` (0.0x at one decimal) | `n/a` | at very high growth it collapses the same way |
| positive, PEG between 0.1x and 10x | `1.0x` | quoted normally |

**The bound is on the printed PEG, not on the growth rate**, because it is the number on the
page that has to be defensible. Suppression is NOT a QC failure: compute suppresses on odd data
and warns, and the `qc` block asserts only the post-condition compute guarantees — `no
out-of-bound PEG reached the payload`, which fails on a code regression and cannot fail on
data. The data conditions are warnings.


### 7.10 A Y/Y needs a positive base

Where the prior period is zero or negative there is no growth rate: the cell prints `n/m`, with
the direction in a warning.

| prior | current | cell | why |
|---|---|---|---|
| positive | positive | `+27.7%` | a real rate |
| **positive** | **negative** | `-150.0%` | **still a real rate** — the BASE must be positive, not the result |
| negative | positive | `n/m` | loss to profit: no percentage exists between them |
| negative | negative, loss narrowing | `n/m` | an improvement, not growth |
| negative | negative, loss widening | `n/m` | the magnitude is not a rate |
| zero | any | `n/m` | no base to grow from |
| missing | any | `—` | no data (§1) |

A dash and an `n/m` are not interchangeable: a dash says "we have nothing", an `n/m` says "we
looked, and this comparison does not exist". The Y/Y column is about seven characters wide, so
`n/m` is all it holds; the warning names what actually happened and that is what prose should
quote. `n/m` renders in body grey via the renderer's single `NEUTRAL_CELL` test, which
`check_render.py` (internal edition only) asserts. `GROWTH_MAX` (1,000%) bounds the extreme tail.


### 7.11 The target's row in `peers`
Mark the target with `"is_target": true` and put the row **wherever you like**. Row order
affects no figure: the peer table renders in the order you supply, the rank letters follow each
company, and the `vs Peer Median` slot is built from the rows whose flag is false.


### 7.3 What compute decides for you — the rules themselves

Each is applied automatically and reported in a warning when it fires. `SKILL.md`
§7.3 keeps only what each one asks of the operator.

| compute handles |
|---|
| negative and loss-making multiples (per-basis bound, **P/E 1-200x**, `n/a` with the reason, no average across a sign change) |
| choosing the peer basis from the data — P/E, then EV/EBITDA, then EV/Sales, then `P/B` last |
| REITs: P/E and PEG suppressed everywhere, FFO-based rows, `P/FFO` leading the strip |
| the PEG bound (`PEG_MAX` 10x, `PEG_MIN` 0.0x at one decimal, `n/a` with the reason, not a QC failure) |
| `n/m` where a Y/Y has no positive base, in body grey, with the direction in a warning (`GROWTH_MAX` 1,000% caps the tail) |
| dropping a valuation slot whose value AND context are both the missing-data glyph |
| filling optional slots last, in the declared order `P/B (NTM)` → `Div Yield` → `vs Peer Median`, and naming any that did not fit |
| warning when a `top_kpi` code appears in neither `financial_rows` nor `kpi_code_whitelist` |
| applying a sector's `lower_is_better_codes` even when the payload omits the flag |


### 7.1 Which sectors need a profile
A profile is required where the default template (Revenue / Gross Profit / EBIT / EBITDA /
EPS) does not fit the P&L. These sectors declare their own `financial_rows` and must have an
entry — `self_check.js` (internal edition) fails the build if this list and `assets/sector_profiles.json` disagree
in either direction:

**Banking, Financial Services, Insurance, Real Estate, Health Care, Oil and Gas, Utilities,
Tech Hardware and Semiconductors.**

Notes that matter when editing profiles: an E&P's earnings line is **EBITDAX** (`IS_P_172`),
not EBITDA (`IS_P_113`), which returns nothing; **Utilities keep P/E** (regulated earnings are
real earnings) and add **Interest Expenses** (`IS_P_141`); **Industrial Services** is
keywords-only on purpose, because its `top_kpi` set is company-specific segment lines and a
whitelist would encode one company's segments as the sector's.

Most operating sectors need no entry and fall back cleanly; an absent profile is an
informational warning, not an error. A profile that exists but ignores a `top_kpi` metric also
warns (§7.3).


### 7.2 What a profile can set

| Key | Effect | Read by |
|---|---|---|
| `financial_rows` | Overrides the default income-statement template | compute |
| `suppress_valuation` | Removes inappropriate multiples. Reaches the WHOLE sheet: the valuation slot, the peer column, the rank letter and the headline peer-median basis. Matched against both label shapes (`NTM P/E (GAAP)` and `PEG (NTM)`); a metric this build cannot remove from the peer table is reported as a warning rather than half-applied | compute |
| `suppress_gross_margin` | Drops the Gross Margin row | compute |
| `prefer_valuation` | Warns if the sector's primary multiple was not fetched | compute |
| `computed_valuation` | Multiples the MCP does NOT calculate, computed from data already held: `{label, per_share_code}` produces `price ÷ FY1 consensus of that per-share code`. Real Estate uses it for `P/FFO` (`CF_P_202`). The slot leads the strip, carries NO average (no historical series exists) and states the per-share figure and period in its context. Missing consensus suppresses the slot with a warning naming the code to fetch, never a silent dash | compute |
| `min_fin_rows` | Achievable row floor for the THIN FINANCIALS warning. Compute honours it and emits it in `computed.json` so `eval_tearsheet.py` uses the sector's floor rather than a hardcoded 5 | compute, then eval via the payload |
| `lower_is_better_codes` | LTCODEs whose polarity is INVERTED (efficiency ratio, combined ratio, MLR, churn, charge-offs, cost lines): a lower value beats the estimate, and a rise is coloured as bad news. Applied even when `raw_data.json` omits the per-KPI flag, with a warning | compute + renderer |
| `lower_is_better_rows` | **Read as the same list:** compute takes the UNION of the two keys, for both the Key Financials and the KPI path. Either key works; prefer `lower_is_better_codes` for new entries | compute + renderer |
| `kpi_keywords`, `kpi_code_whitelist` | **Advisory only** — guide the model's KPI selection | model |


### 8R. Document sections - per-section content and sources
| Section | Content | Source |
|---------|---------|--------|
| Masthead | **FORMAT LOCKED** — gray outline box (#C9C9C9) on white; logo 59×46px left; "InSync Analytics" at 16.5pt + "Company (TKR) - Period" in Georgia; product name large Georgia bold right with orange "Prepared:" and "Expected Earnings Date:" labels; full-width 2.5pt orange rule below. Do NOT alter geometry, fonts, sizes or colors without explicit approval. Payload slots: `product_name`, `period` | payload + assets |
| Market Snapshot | Price (as-of), Mkt Cap, 52W Position, Next Earnings, FY1+FY2 EPS Gr. Multiples live ONLY in the Valuation bar | **computed.json** |
| Business Overview | 3-4 sentences | AI prose |
| Key Financials | Up to 7 rows incl. Gross Margin | **computed.json** |
| Operational KPIs | Up to 6 rows with Tier (T1 or S), vs Est, Y/Y, Trend | **computed.json** + model trend labels |
| Valuation Context | 6 slots: multiples with premium/(discount) vs the history average (window named from the series), PEG (§7.9), P/E vs peer median | **computed.json** |
| Investment Thesis | Bull (4) vs Bear (4) — evidence framing, no recommendation language | AI prose on computed data |
| Key Debates | 3 debates, each naming its falsifier | AI prose |
| Peer Comps | Target + 5 peers, Rank column (G=growth, P=P/E, E=EV/EBITDA, S=EV/Sales, B=P/B; 1=best/cheapest). Ranks span the whole group **including** the target; the `vs Peer Median` slot excludes it. Row order is free (§7.11) | **computed.json** |
| Supply Chain | Suppliers, distribution, partners | AI prose |
| Catalysts | 4 events incl. next earnings date | `get_call_date` + AI |
| Monitoring & Signals | 2 monitoring variables + 2 data signals | **computed.json `signals`** + AI phrasing |



### 10R. Build-step exit codes and the one-page levers
| exit | Step 1 `compute_tearsheet.py` | Action |
|---|---|---|
| 0 | Computed, zero QC failures | Continue to Step 2 |
| 1 | One or more QC checks FAILED | DO NOT DELIVER — a figure the sheet would print is wrong. `computed.json` and `run_manifest.json` are still written so you can find the bad row; fix `raw_data.json` and re-run Step 1 |

Step 2 refuses such a payload as well, so a lost exit code cannot get past it. There is
deliberately **no override flag**: unlike a two-page render, a wrong revenue figure is
invisible on a clean-looking page.

| exit | Step 5 `render_pdf.py` | Action |
|---|---|---|
| 0 | One page | Continue to Step 6 |
| 1 | Two or more pages | DO NOT DELIVER — trim the payload, re-run steps 1-6 |
| 2 | LibreOffice unavailable | Run Step 6 on the docx; deliver the docx and state that the PDF is missing and the page count unverified |

| exit | Step 6 `check_table_widths.py` | Action |
|---|---|---|
| 0 | `TABLE WIDTH INVARIANT PASSED` | Deliver both files |
| 1 | Grid/cell mismatch, or a table wider than the text column | DO NOT DELIVER — the renderer changed a grid without its cells; fix the renderer, do not hand-edit the docx |

**Steps 5 and 6 are not optional, and neither substitutes for the other.** Step 3 caps word and
bullet counts on the payload, a proxy for the page budget checked BEFORE layout. Step 5 measures
the RENDERED document and catches overflow the eval structurally cannot (long KPI or company
names, a sixth KPI row, a wide peer table). Step 6 catches the one class Step 5 cannot see: a
table whose `<w:tblGrid>` disagrees with its rows' `<w:tcW>`, which distorts columns WITHOUT
changing the page count.

**There is no fixed word budget, and this section deliberately does not print one.** The
per-field caps are necessary but NOT sufficient for one page: five things compete for it.

| lever | documented maximum |
|---|---|
| `key_financials` rows | 7 (4 by construction for Financial Services and Insurance) |
| `operational_kpis` rows | 6 |
| `comparable_companies` rows | 6 (target + 5 peers) |
| `valuation_metrics` slots | 6 |
| total prose words across all blocks | the per-field caps sum to ~500 |

Every one of those at its maximum does not fit. Step 3 prints a `LAYOUT` line with the five
numbers for the payload in front of you, and **Step 5 decides** by rendering the real document.
If Step 5 reports two pages, read that LAYOUT line and reduce whichever lever you can most
afford: a table row costs more page than a dozen words, and prose overflow is cumulative across
every block, so trimming one block alone is the usual mistake. A long peer display name wraps
and costs a whole line; dropping a peer basis (`ev_sales` / `pb`) narrows the table and is not
one of the five levers eval reports.

**Overflow order.** On two pages drop KPI rows: `S` without an estimate, any `S`, `T2` without,
any `T2`, `T1` without, then the last row; stop at 4 rows, then trim prose. Never drop a Key
Financials row.

**`--allow-multipage` (diagnosis only, never delivery).** `render_pdf.py` accepts this flag,
which prints the overflow message but exits 0 anyway, so you can inspect a two-page render while
trimming. A run that used it has NOT passed the one-page gate: never deliver its output, and
never wire it into a script.



### 14R. What self_check.js's six checkers assert (internal edition only)
    | checker | must report |
    |---|---|
    | `check_render.py` | all 12 polarity paths covered — the only gate that reads a rendered cell's COLOUR, and the only one that checks the footer |
    | `check_gates.py` | GATE SELF-TEST PASSED — checks the *checkers*, by feeding each gate something it must reject |
    | `check_preprofit.py` | PRE-PROFIT GATE PASSED — loss-making and pre-revenue issuers, and the widest peer table on one page |
    | `check_merge.py` | MERGE GATE PASSED — Step 2 accepts the documented path and refuses every bad `prose.json` |
    | `check_reit.py` | REIT DOCUMENT GATE PASSED — read off the finished `.docx`, because a correct profile and a wrong page are compatible |
    | `check_masthead.py` | MASTHEAD CHECK PASSED — needs the Step 5 PDF as an argument; without it, or without `pdfplumber`, it reports **skip**, which is not a pass |


