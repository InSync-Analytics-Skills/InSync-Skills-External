# Metric Basis & Sector Ladder — Forecast Reliability Suite

This file makes the skill **self-contained**: nothing here requires another skill to be loaded first.

The Adjusted-first and calendarization conventions are stated here rather than in a separate pre-load
skill: the SKILL.md format has no `requires:` field, so nothing could verify that a pre-load happened.

**Verification status is marked on every row.** `VERIFIED` means pulled live from the InSync MCP and
asserted by a regression fixture (internal edition only) that renders unforced and reconciles. `DOCUMENTED`
means taken from the InSync metric tables but not exercised by a fixture — a starting point, to be confirmed against the MCP
for the ticker in front of you. Do not promote a DOCUMENTED row to a claim without checking it.

**Why the distinction matters.** The Adjusted/GAAP pairing pattern does not extend to FFO: `CF_P_114` is
**AFFO**, a different metric, not "adjusted FFO", and `IS_P_184` and `IS_P_180` **do not exist** and
hard-error an entire batched `get_actuals` call. Only a live fixture shows errors of this kind.

---

## 1. Core basis rules (all sectors)

| Rule | Value |
|---|---|
| Calendarization | `calendarize=false`. This suite is always single-ticker, so fiscal basis. |
| Fiscal period tokens | Quarters `2Q26`. Annual is a **bare year** `2026` — never `FY26` or `2026FY`. |
| Basis preference | **Adjusted (Non-GAAP) first**, GAAP only as fallback, and the fallback must be LABELLED. |
| Batching | Request the Adjusted and GAAP codes for a metric in ONE call, then prefer Adjusted. Never issue a second call just to fetch the fallback. |
| Call shapes | `get_actuals` wants `fiscal_period` as a comma-joined **string**; `get_call_date` wants the **array** form. `limit` caps at 100 on both. |

### The two reserved headline keys

| Key | Adjusted | GAAP fallback | Notes |
|---|---|---|---|
| `rev` | `IS_P_102` | `IS_P_101` | Fall back without ceremony and set `"basis":"reported"`. |
| `eps` | `IS_P_128` | `IS_P_127` | On fallback set `"basis":"reported"` so the report says "GAAP diluted EPS". **Never feed GAAP numbers under an adjusted label.** |

**The `eps` slot is label-driven, not EPS-only.** The headline display string comes from the metric's own
label, so a REIT puts FFO per share there with `"label":"FFO per Share"` and the report reads "FFO per Share
beat consensus 5 of 8 quarters". VERIFIED on a REIT: zero occurrences of "adjusted EPS" in the output. The
same applies to any sector whose headline earnings line is not EPS.

**Proven end to end.** A second REIT fixture (`spg_fixture.json`, internal edition only; 8 quarters) puts
FFO per Share in `eps`; the report reads "FFO per Share" throughout, and the regression check asserts
that "adjusted EPS" and "EPS (adj)" appear nowhere. It also exercises the thin-panel path: where most
covered quarters are single-broker collapsed ranges, Estimate Dispersion prints "(no range published)"
and `n/a` rather than "Very low", while `_metrics.json` keeps the computed value.

### Which standard codes are EMPTY, by sector — all VERIFIED live

| Code | Bank | Insurer | REIT | Asset mgr | Utility | Managed care |
|---|---|---|---|---|---|---|
| `IS_P_102` adj revenue | empty | empty | empty | **populates** | empty | **populates** |
| `IS_P_106` gross profit | empty | empty | empty | empty | empty | empty |
| `IS_P_110` adj EBIT | empty | empty | empty | **populates** | empty | **populates** |
| `IS_P_114` adj EBITDA | empty | empty | **populates** | empty | empty | **populates** |
| `IS_P_128` adj EPS | populates | populates | actuals, **no estimates** | populates | populates | populates |

Two rules follow. **Never emit a gross-margin row outside the standard-code sectors** — gross profit is
empty in all six. And **a code populating is not the same as it being gradeable**: for the REIT `IS_P_128` has
actuals and no consensus series at all, so using it as the headline would trip the critical-coverage gate.
Check the estimate side, not just the actual.

---

## 2. `pct_metric` — get this right or the surprise maths is wrong

`pct_metric:true` makes a surprise read in **percentage points**; `false` makes it a **percent change**.

**Do not key off the name starting with `%`.** Verified across every sector: at consolidated level the
InSync names end in `(%)` or omit it entirely — `Combined Ratio (%)`, `NIM (FTE)`, `ROTCE`,
`Consolidated Medical Loss Ratio (MLR)`, `Total % Base Fee Rate`, `ETR`. Only *segment* variants use a
leading `%`.

Set `true` for any rate or ratio: NIM, efficiency ratio, ROTCE, ROE, CET1, combined / loss / expense ratio,
MLR / MCR, occupancy, rent change, fee rate, operating margin, ETR, capacity factor, comp sales, tax rate.
Set `false` for levels: revenue, EPS, premiums, deposits, loans, AUM, membership, store counts, volumes,
absolute expenses.

---

## 3. `lower_is_better` — polarity

For a cost, expense, charge-off or efficiency ratio, coming in **below** consensus is a **beat**. Declare it:

```json
{ "key": "eff", "label": "Efficiency Ratio", "pct_metric": true, "lower_is_better": true }
```

Honoured on metrics AND on `guidance[]` / `fy_guidance[]` rows (a guide row inherits from a metric of the
same key). It affects: the beat/miss tally, the "Beat the guide" test, the FY-grid raise/cut colour, the
surprise-grid colour, and the `beat_all_estimates` / `below_all_estimates` screener fields. It deliberately
does **not** affect the printed surprise sign, Direction, or Bias Strength — those state where the *Street*
sat, and were already correct.

**Three verified cases of what happens without it:**

| Sector | Metric | Untreated | Correct |
|---|---|---|---|
| Bank | Efficiency Ratio | 0 beats / 7 misses | **7 beats / 1 miss** |
| Insurer | Combined Ratio | 1 beat / 7 misses | **7 beats / 1 miss** |
| Managed care | Consolidated MLR | 2 beats / 4 misses | **4 beats / 2 misses** |

Declare on: any expense or cost line, NCO / charge-offs / loss rate, efficiency ratio, combined / loss /
expense ratio, MLR / MCR, catastrophe losses, prior-period development, provisions, impairments, medical
costs, opex, COGS, net interest expense, ETR, shrink, churn, attrition, delinquency, DSO / DPO.
Do **NOT** declare on: revenue, premiums earned, EPS, FFO, NII, NIM, ROTCE, ROE, **CET1** (more capital is
better), AUM, net flows, fee rate, operating margin, membership, occupancy, rent spreads, rate base,
capacity, backlog, customers, **days claims payable** (higher DCP is conservative reserving).

**Two genuinely arguable cases — declare them explicitly rather than letting them default.** `capex` for a
regulated utility: under-spending scores as a beat, but capex *is* the rate-base engine, so "spent less than
modelled" may read as a negative. And `Days Claims Payable`. Both are now caught by the undeclared-polarity
warning, so neither can default silently.

The renderer warns (`data_flags`) when a metric or guide whose NAME reads like a cost line carries no
explicit polarity. It deliberately does **not** auto-flip: inferring polarity from a label is a silent guess,
and a wrong guess inverts a headline statistic.

---

## 4. `top_kpi` is NEVER sufficient — verified 7 for 7

`search_lineitem(filter="top_kpi")` returned **exactly 5 rows for every one of the seven tickers tested**,
and every time omitted metrics the sector is judged on. Step 2's filter must always be widened with a second
unfiltered `search_lineitem` on sector keywords, then re-gated on **estimate** coverage, not actuals coverage.

| Sector | What `top_kpi` omitted |
|---|---|
| Bank | Efficiency Ratio, ROTCE, CET1, deposits — the whole capital / returns / efficiency layer |
| Insurer | Expense ratio, BVPS, net investment income, catastrophe losses, ROE, and *total* premiums earned (it returns only a segment line) |
| REIT | FFO, FFO/share, AFFO, NOI, same-store NOI, cap rates, vacancy |
| Asset mgr | Operating margin, and **effective fee rate carries no tag at all** — so the one metric defining an asset manager's pricing power is invisible to any tag filter |
| Utility | The opposite failure: 3 of its 5 `top_kpi` rows have only 2 of 8 quarters of consensus, so building off the tag yields a hollow report |
| Managed care | The entire cost-ratio, membership-total, reserve-adequacy and opex-ratio layer; and one tagged row (`KM_S_221`) has no data at all |

---

## 5. Sector ladder

### 5a. Banking — VERIFIED (a large US bank, 8 quarters)

Top line `IS_P_101` — banks do report a total revenue line. **But check its ESTIMATE side before you
make it the headline.** One large bank can carry a full consensus series while another has
`IS_P_102` at 2 of 8 quarters and `IS_P_101` at 2 of 8 as well, so BOTH revenue codes trip the
critical-coverage gate and the render stops. Nothing warns you in advance — the codes populate on the
actuals side, which is exactly the trap section 3 describes. Analysts model NII, NIM and fees for a bank,
not a consolidated revenue line, so thin revenue consensus is normal rather than a data defect. When it
happens, drop revenue from `metrics` and let the KPI stack carry the report: NIM (FTE), Efficiency
Ratio, ROTCE, % NCO and Average Gross Loans typically cover all 8 quarters. A bank report with no revenue row and five
full KPI rows is correct; a gated render is not.

| LTCODE | Exact MCP name | pct | lower_is_better |
|---|---|---|---|
| `IS_P_202` | Net Interest Income (FTE) | no | no |
| `IS_P_204` | NIM (FTE) | **yes** | no |
| `IS_P_205` | Efficiency Ratio | **yes** | **yes** |
| `IS_P_211` | Non Interest Income | no | no |
| `IS_P_213` | Non Interest Expense | no | **yes** |
| `RT_P_107` | ROTCE | **yes** | no |
| `BS_P_287` | CET 1 Capital Ratio - Standardized | **yes** | no |
| `BS_P_250` | Total Gross Loans | no | no |
| `BS_P_261` | NCO | no | **yes** |
| `SD_S_177` | Total Investment Banking Fees | no | no |

Deposit lines are **trillion**-scale (a large bank's deposits come back as e.g. `$1.2140T`): encode in billions with `"unit":"$B"`.

### 5b. Insurance — VERIFIED (a large P&C insurer, 8 quarters)

**The top line is `IS_P_403` Net Premium Earned**, not `IS_P_101`. Premiums earned has its own code; use it.

| LTCODE | Exact MCP name | pct | lower_is_better |
|---|---|---|---|
| `IS_P_403` | Net Premium Earned | no | no |
| `IS_P_402` | Net Premiums Written | no | no |
| `SD_P_103` | Combined Ratio (%) | **yes** | **yes** |
| `SD_P_101` | Loss Ratio (%) | **yes** | **yes** |
| `SD_P_102` | Expense Ratio (%) | **yes** | **yes** |
| `SD_P_104` | Catastrophe Losses | no | **yes** |
| `SD_P_105` | Catastrophe Losses Ratio (%) | **yes** | **yes** |
| `SD_P_108` | Accident Year Combined Ratio (AYCR) (%) | **yes** | **yes** |
| `SD_P_109` | Accident Year Loss Ratio (AYLR) (%) | **yes** | **yes** |
| `SD_P_106` | Prior Period Development | no | **yes** |
| `IS_P_406` | Underwriting Income (Loss) | no | no |
| `IS_P_410` | Net Investment Income | no | no |
| `SD_S_564` | Policies in Force - Consolidated | no | no |
| `BS_S_528` | Book Value Per Share - Month 3 | no | no |
| `RT_P_102` | Return on Equity (ROE) | **yes** | no |

**BVPS trap:** use `BS_S_528`. `SD_S_962` "Book Value Per Common Share" has **no estimates** and its actual
returns mis-scaled (a ~$48 book value printed as `$48.2100M`). `SD_S_1030` Total Net Investment Income has no estimates either, and
`IS_P_410` can have consensus in only 1 of 8 quarters. **Catastrophe losses are lumpy by nature** — half
the quarters can exceed the anomaly threshold and are excluded, and the flag text blames "collapsed range /
near-zero base / restatement", which misdiagnoses normal weather. Report it; do not code around it. Some
insurers give **zero** quantitative guidance: the `outlook` rows read "Company is not providing guidance".

### 5c. Real Estate / REIT — VERIFIED (a large industrial REIT, 8 quarters)

**Headline is FFO per share via the label-driven `eps` slot.** `CF_P_202` has pre-print consensus in all 8.

| LTCODE | Exact MCP name | notes |
|---|---|---|
| `CF_P_113` | FFO | **not** paired with `CF_P_114` |
| `CF_P_114` | **AFFO** | a DIFFERENT metric, not "adjusted FFO" |
| `CF_P_136` | Core FFO | |
| `CF_P_202` | FFO/Share | the gradeable headline |
| `CF_P_203` | **AFFO/Share** | again, not "adjusted FFO/share" |
| `CF_P_209` | Core FFO/Share | the `top_kpi` line AND the line management guides |
| `IS_P_161` | Net Operating Income (NOI) | `-M` variant is the margin |
| `IS_P_177` | Same Store NOI (%) | **no estimate series at all** — cannot be graded |
| `KM_S_327` / `371` / `373` | % Average Occupancy - Company Share / Average Occupancy / Period Ending Occupancy | `pct_metric` |
| `KM_S_328` / `358` / `360` | % Net Effective Rent Change / Rent Change (Net Effective) / Rent Change (Cash) | `pct_metric` |
| `IS_P_179` | Property Maintenance & Repair Expenses | 8/8 actuals + full consensus, `lower_is_better` |

**`IS_P_184` and `IS_P_180` DO NOT EXIST.** `get_actuals` returns `LTCODE(s) not found in database` and the
whole batched call dies. `IS_P_183`, `IS_P_281`/`282` and `BS_P_254`/`255` are valid but can be empty. Cap
rates (`KM_P_134`/`135`, `KM_S_702`) are tagged `industry_kpi` and can carry no data. **Any REIT per-share line
other than `eps` needs `"unit":"$"`** — only reserved keys inherit `currency`. Lumpy capital-deployment
lines (development starts, acquisitions, dispositions) belong in `fy_guidance`, not quarterly graded
metrics: starts can vary several-fold quarter to quarter against a smoothed Street split, so most quarters trip the
anomaly filter. REITs guide FY only, and REIT panels are often thin, so dispersion is structurally thin.

### 5d. Financial Services / asset management — VERIFIED (a large asset manager, 8 quarters)

`IS_P_102` and `IS_P_110` both populate, so an asset manager gets an adjusted revenue headline and a real
EBIT margin. Only gross profit and EBITDA are absent.

| LTCODE | Exact MCP name | pct |
|---|---|---|
| `KM_P_301` | AUM - EOP | no |
| `KM_P_303` | Net Flows | no |
| `KM_S_387` | Long Term - Base Fee | no |
| `IS_P_306` | Investment Advisory & Administration Fees - Revenue | no |
| `IS_S_114` | Technology & Risk Management Revenue | no |
| `IS_S_113` | Performance Fees - Revenue | no |
| `IS_P_110-M` | Margin - EBIT (Ex- 1x Items) | **yes** |
| `KM_S_375` | Total % Base Fee Rate | **yes** — **untagged** |

AUM is trillion-scale: encode in billions. **Fee rate is unusable at the renderer's 1-decimal percentage
precision** — it is ~15bp, so it renders "0.1–0.2%" and the signal is erased; the MCP also pre-rounds it to
2dp, so bps cannot be recovered even with a better formatter. Ship it knowing that, or omit it. Searching
"assets under management" returns junk — only the literal token "AUM" surfaces those lines. "Client assets"
is a broker concept and does not exist for an asset manager. `get_guidance` can return quarterly guide rows
for a company that does not guide quarterly; where that happens, treat its quarterly guide statistics as suspect.

### 5e. Utilities — VERIFIED (a large US utility, 8 quarters)

**Check headline coverage first.** A utility whose consensus panel is absent for 5 of 8 quarters (headline
coverage 37.5%, under the `cov_critical` 0.50 gate) cannot render without `--force`.

| LTCODE | Exact MCP name | pct | lower_is_better |
|---|---|---|---|
| `SD_S_144` | Regulated Electric - Revenue | no | no |
| `IS_P_107` | Total Opex | no | **yes** |
| `IS_P_103` | COGS | no | **yes** |
| `IS_P_141` | Net Interest Expense | no | **yes** |
| `IS_P_121` | ETR | **yes** | **yes** |
| `KM_P_1251` | Total Electric Sales (GWh) | no | no |
| `CF_P_109` | Less: Capex | no | declare explicitly |

**Rate base and allowed ROE are actuals-only.** Some utilities carry `SD_S_1180`/`1181` (closing / average rate base)
and `SD_S_1182`/`1183` (regulatory / blended ROE) but **not one quarter of Street consensus on any of
them**, so the two most important regulated-utility drivers can never appear in a forecast-accuracy report.
They belong in a tear sheet. Others have no rate base, allowed ROE, capacity factor or heat rate line at all.

Three traps. Utility ROE codes can **store decimals** (e.g. `0.105` = 10.5%) and must be ×100 on ingest.
`SD_S_1099`/`1104`/`1109`/`1116` "Capacity Factor - …" and `SD_S_1157`–`1172` "Capacity - …" are **stale
sell-side model scaffolding**, not reported data — identical across five consecutive quarters, `$`-prefixed,
and absent for the latest quarter. And `CF_S_143` (a segment capex line) can pair a **quarterly actual with an annual-scale
estimate**, producing a fabricated ~275% surprise, while its sibling `CF_S_145` is scale-consistent — so
check per code, not per segment. Utilities guide FY only, routinely reaffirm everything (often no raises
or cuts, only maintains), and issue multi-year guides (`2026-2034`) that `fy_guidance` cannot represent.

### 5f. Health Care — managed care — VERIFIED (a large managed-care company, 8 quarters)

**Managed care sits under `industry = "Health Care"`, NOT `Insurance`.** Managed care hides inside the largest industry
bucket alongside pharma and devices, which behave completely differently. You cannot infer treatment from
the industry label — check `insync_subindustry` ("Health Care Facilities and Services") and the KPI universe.

| LTCODE | Exact MCP name | pct | lower_is_better |
|---|---|---|---|
| `IS_P_903` | Consolidated Medical Loss Ratio (MLR) | **yes** | **yes** |
| `IS_P_449-M` | % Medical Care Ratio - Medical Costs | **yes** | **yes** |
| `IS_P_449` | Medical Costs | no | **yes** |
| `IS_P_108-M` | % Revenue - Operating Expenses (Ex-1x Items) | **yes** | **yes** |
| `BS_P_302` | Days Claims Payable/DCP | no | no (higher = conservative) |
| `KM_P_1730` | Total Membership | no | no |
| `SD_P_1761` | Premium - Revenue | no | no |

`IS_S_563` Adjusted MLR and `KM_S_221` Commercial ASO - PMPM can have no data. There is no line named
"operating cost ratio": it is `IS_P_108-M` (adjusted, has estimates) or `IS_P_107-M` (GAAP, the code a
guide may land on). Restated quarters are common here — set `restated_quarters`. The FY MLR guide splits
across two LTCODEs (`IS_P_903` and `IS_P_449-M`), so the FY grid shows two single-print rows; merged they
would read as a 70bp cut, but merging is an inference, so do not.

### 5g. Standard-code sectors — no ladder needed

Consumer Discretionary Products and Services, Consumer Staple Products, Industrial Products, Materials,
Media, Retail & Wholesale (Discretionary and Staples), Software and Tech Services, Tech Hardware and
Semiconductors, Telecommunications. Revenue genuinely is the top line, EPS genuinely is the headline
earnings metric, and their KPIs are growth and volume measures where higher is better, so none of the three
failure modes applies. Retail & Wholesale – Staples is VERIFIED (two large US retailers).

### 5h. Still DOCUMENTED, not verified

**Oil and Gas** — EBITDAX `IS_P_172`/`171`; lease & well `IS_P_186`/`185`, transportation `IS_P_188`/`187`,
gathering & processing `IS_P_190`/`189`, exploration `IS_P_192`/`191`, all `lower_is_better`. Production
volumes carry their unit in the row name (MBoe/d, MMcf/d): set `unit` explicitly.
**Industrial Services / transport** — operating ratio `IS_P_1102`/`1101` (pct, `lower_is_better`).
**Restaurants** — restaurant margin `IS_P_502`/`501` (pct), profit `IS_P_504`/`503`.
**Airlines** — `IS_P_702`–`IS_P_716` cost lines, all `lower_is_better`.
**Cruise** — `IS_P_804`–`808`, net cruise cost `KM_P_847`/`840`.
**Automotive** — `IS_P_144`–`148`, `IS_P_232`–`238`.
Confirm each against the MCP before relying on it. The REIT precedent shows these paired-code assumptions
can be wrong in both directions, including naming codes that do not exist.

---

## 5h. Sectors verified live but NOT fixture-backed

These were confirmed against the MCP (KPI discovery and 8-quarter consensus coverage) but ship no
fixture, so they are not covered by the regression suite. Treat the ladder as a starting point, not a guarantee.

| Sector | Revenue code that works | Headline KPIs, all 8/8 consensus |
|---|---|---|
| Semiconductors | `IS_P_101` (`IS_P_102` **empty**) | segment revenue and segment EBIT lines |
| Oil and Gas | `IS_P_101` (`IS_P_102` **empty**) | Avg Daily Production, Refinery Throughput, Crude Realized Price |
| Automotive | `IS_P_101` | Total Deliveries, Automotive Revenue, Revenue/Unit, Adj EBITDA |
| Consumer Staples | `IS_P_102` **populates** | % Organic, % Price Mix, Volume Growth, regional Revenue and EBIT |
| Restaurants | `IS_P_101` | % SSS, Franchised Revenue, System Wide Sales, Restaurant Profit, Restaurant Count |
| Telecom | `IS_P_101` | Postpaid Subs, Postpaid Net Adds, Phone Churn %, Prepaid ARPU |
| Airlines | `IS_P_102` **populates** | ASM, PRASM, TRASM, CASM ex-fuel, Load Factor — **see the `USCents` trap in section 6** |
| Industrial Products | `IS_P_101` | headline verified; segment price/volume KPIs not coverage-tested |
| Retail-Discretionary | `IS_P_101` (`IS_P_102` **empty**) | rendered end-to-end; % SSS, Comp Ticket, Comp Transactions, GM, EBIT margin |

Two patterns hold across all nine. `IS_P_102` is empty far more often than it populates, so the
`IS_P_102 → IS_P_101` fallback in section 2 is the normal path, not the exception. And `filter="top_kpi"`
returned the correct sector-defining metrics for **every one of them** — see the note in SKILL.md Step 2.

---

## 6. Unit and scale traps — all VERIFIED

### Reading MCP values into input.json

| MCP string | Number in input.json | Note |
|---|---|---|
| `$142.6000B`, `$540.0000M`, `$1.2140T` | in the metric's own unit: 142.6 at `$B`, 540 at `$M` | `rev` follows `rev_unit` |
| `$0.7400` | 0.74 | per-share lines carry `unit:"$"` |
| `4.40%`, `(6.0%)` | 4.4, -6.0 | parentheses are a negative; set `pct_metric:true` |
| `11.0000k`, `10,639` | 11000, 10639 | counts take their own unit (`"stores"`) |
| `48.3000M` members | 48.3 | with `unit:"M members"` |
| `USCents0.1800` | 18.00 | strip the prefix, x100, `unit:"c"` (below) |
| `2% - 3%`, `(6.0%) - (5.0%)` | `low` 2, `high` 3; `low` -6, `high` -5 | a guide range |
| `$28400 - +/- 2%` (`SCALE` 10^6, metric in $B) | `mid` 28.4, `tol_pct` 2 | apply `SCALE` first; `mid` is in the metric's unit |
| `68.0% - +/- 0.5%` on a % metric | `mid` 68.0, `tol_abs` 0.5 | on a % metric "± X%" is ± X ppt: `tol_abs`, never `tol_pct` |
| `63.5% - +/- 50bps` | `mid` 63.5, `tol_abs` 0.5 | bps / 100 |
| `approx. 4%`, `about $3B` | `low` = `high` = the point | a true point guide; say so in `data_flags` |

**Guide `SCALE`.** `get_guidance` rows carry a `SCALE` field: `10^6` (also written `1000000` or
`1e6`) means the guide is in millions, `10^3` / `1000` / `1e3` in thousands, `10^9` in billions.
Convert to the metric's unit before writing `low` / `high` ($12,400 at `10^6` is $12.4B). If the
guide TEXT names a different unit (e.g. "$30bn" on a row stored at `10^6`), the text wins, and the
difference is named in `data_flags`. A band guide is written as `mid` + tolerance with NO `low`/`high`;
a row carrying both is kept only if they agree, otherwise the renderer drops it and names it.

- **Trillion-scale** values (bank deposits, AUM): encode in billions with `"unit":"$B"`.
- **Per-share lines other than `eps`** need explicit `"unit":"$"`; only reserved keys inherit `currency`.
- **Volume KPIs return with a spurious currency prefix** — `KM_P_1251` prints e.g. `52.1000k` and
  `KM_S_163` e.g. `$28.9000B` where the true values are 52,100 and 28,900 **GWh**. Set `unit:"GWh"` and
  `pct_metric:false` and they render correctly.
- **Cents-denominated codes return a `USCents` prefix and a DECIMAL value** — an airline's `KM_P_705`
  PRASM prints e.g. `USCents0.1800` and `KM_P_707` CASM ex-fuel e.g. `USCents0.1500`, where the true
  values are **18.00 and 15.00 cents**. Transcribe the decimal as-is and every surprise on the row is out by 100x
  while still looking plausible. Strip the prefix, multiply by 100, and set `unit:"c"`. Verified live on
  an airline; assume it applies to any per-unit revenue or cost metric quoted in cents.
- **Some ratio codes store decimals** (e.g. a regulatory ROE of `0.105` = 10.5%). Multiply by 100 on ingest.
- **Check estimate scale per code, not per segment** — one sibling can be quarterly and another annual.
- Thin panels are the norm outside mega-cap tech: utilities, REITs and asset managers often carry only a
  handful of brokers, against far deeper panels at the largest banks and managed-care names. Report panel thinness as a data note rather than coding around it.

---

## 7. Checklist before assembling `input.json`

1. `calendarize=false`; quarters `2Q26`, annual bare years.
2. `rev` and `eps` resolved with any fallback labelled; for a non-EPS headline, set the metric's own label.
3. Every rate or ratio has `pct_metric:true`, whether or not its name starts with `%`.
4. Every cost, expense, charge-off or efficiency line has `lower_is_better:true`, on the metric AND on any
   guide row for it. If the render emits a polarity `data_flag`, you missed one.
5. `top_kpi` widened with a keyword search and re-gated on ESTIMATE coverage. Never rely on the tag alone.
6. No trillion-scale value in native units; per-share non-`eps` lines carry `"unit":"$"`.
7. The render exits 0 without `--force` and `reconciliation.all_pass` is `true`.
