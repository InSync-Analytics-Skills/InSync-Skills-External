# Dynamic KPI Selection Standard v1.0

The highest-priority objective of the Earnings Wrap. The report is judged on **KPI
relevance**, not document structure. Before any report is generated, resolve four
decisions for the company and let them drive the note.

## The four decisions (resolve FIRST, every time)

1. **Primary KPI** — the single most important operating driver for this business.
2. **Primary Margin Metric** — the profitability read the Street actually watches.
3. **Most Important Guidance Variable** — the forward number that moves the stock.
4. **Core Investment Debate** — the question this print must speak to.

**The Primary KPI comes from the live InSync MCP, resolved by the Step 0 MCP KPI Resolution
Ladder (`mcp_top_kpi` -> `mcp_kpi_tier` -> `mcp_discovery`), never from a local file.**
Decisions 2 to 4 (margin / guidance / debate) come from the sector rules below, keyed on
the `insync_subindustry` returned by `search_tickerid`. The Core Debate is a
sector-templated question — refine it with the quarter's actual variant.

Where several universe entries compete for Primary KPI, the choice is made mechanically by
`python references/kpi_gate.py --pick-primary "name~tags;..."`, never by judgement. The gate
judges relevance against the MCP universe and reads no local file.

## How each decision maps into the report
- **Primary KPI** -> Event Snapshot "Primary KPI" row + first row of the KPI Hierarchy table.
- **Primary Margin** -> Event Snapshot "Operating/Margin" row + the profitability line in Actual-vs-Consensus.
- **Guidance Variable** -> leads the Guidance section (ordered first after Revenue/EPS).
- **Core Debate** -> the Variant View "Potential variant" and the Executive Summary "Key debate".

## Sector rules (classification -> margin / guidance / debate)

**`common/sectors.json` is the source; this table is GENERATED from it.** Edit the JSON, not the markdown; the two must never diverge. One file, read by `sector_resolve.py` and by every sibling skill, exactly as `common/brand.json` is the one source for the masthead.

| Sector (classification contains) | Primary Margin | Guidance variable | Core debate |
|---|---|---|---|
| banking | NIM (FTE) `IS_P_204` | Net Interest Income (FTE) | NII durability vs credit normalization / capital return |
| institutional financial | pre-tax margin `IS_P_118-M` | markets & fee revenue | markets/fees structural vs cyclical |
| asset management | operating margin `IS_P_110-M` | AUM & net flows | flows & fee-rate pressure |
| specialty finance | pre-tax margin (pre-provision for a lender) `IS_P_118-M` | net interest income, purchase volume, net charge-off rate | credit normalization vs spend growth |
| insurance (underwriter) | combined ratio `SD_P_103` | premium growth & combined ratio | underwriting discipline vs pricing cycle |
| insurance (broker, no `IS_P_403`) | adjusted operating margin `IS_P_110-M` | organic revenue growth + margin | organic growth durability vs fiduciary-income roll-off |
| health care facilities / managed care (payor) | **MLR** `IS_P_903` | MLR / medical cost trend + FY EPS | medical cost trend vs pricing |
| health care facilities (hospital / distributor / CRO, no `IS_P_903`) | adjusted operating margin (gross margin for a distributor) `IS_P_110-M` | segment revenue + FY EPS | volume and mix vs cost, or generic deflation for a distributor |
| medical equipment | operating margin `IS_P_110-M` | organic growth & margin | procedure volumes / new-product cycle |
| biotech and pharma | operating margin `IS_P_110-M` | key-product revenue + FY EPS | key-drug ramp vs pipeline/patent risk |
| semiconductors | **gross margin** `IS_P_106-M` | next-Q revenue + gross margin | AI/data-center structural vs cyclical; inventory |
| technology hardware | gross margin `IS_P_106-M` | next-Q revenue + gross margin | unit demand / mix / cycle |
| software / technology services | **FCF margin** `CF_P_110-M` | revenue + recurring (ARR/bookings) growth | growth durability vs valuation / AI disruption |
| internet media | EBITDA margin `IS_P_114-M` | revenue growth & engagement | monetization compounding vs decel |
| entertainment content | operating margin `IS_P_110-M` | subscriber & revenue growth | subs vs content spend |
| machinery / electrical equip / diversified industrial / aerospace / E&C | **operating margin (ex-1x)** `IS_P_110-M` | FY revenue, segment margin, backlog/orders | backlog structural vs late-cycle |
| transportation & logistics | operating margin `IS_P_110-M` | volume & yield | freight cycle / pricing |
| transportation equipment | **adjusted EBITDA margin** `IS_P_114-M` | segment revenue growth and segment EBITDA | the truck and off-highway cycle versus power-systems and data-centre demand |
| commercial support services | **adjusted EBITDA margin** `IS_P_114-M` | revenue, adjusted EBITDA margin and free cash flow | organic yield versus volume, and the cost of fleet and disposal capex |
| industrial support services | **adjusted EBITDA margin** `IS_P_114-M` | revenue, adjusted EBITDA and net rental capex | fleet productivity and used-equipment values versus the construction cycle |
| automotive | adjusted automotive EBIT margin `IS_P_148-M` | deliveries or wholesale units + segment EBIT | volume/pricing vs cost |
| REIT | FFO margin / same-store NOI `IS_P_177` | FFO/share + same-store NOI | occupancy/rate vs rate-sensitivity |
| real estate services (not REIT) | adjusted EBITDA margin `IS_P_114-M` | segment fee revenue + margin | transaction cycle vs recurring services mix |
| real estate owners and developers (not REIT) | **EBIT margin** `IS_P_109-M` (reported; mark with an asterisk) | segment operating profit, vacancy rate, condominium units and DPS | NAV and capital recycling versus vacancy and rate sensitivity |
| oil & gas producers / integrated / midstream | EBITDA margin `IS_P_114-M` | production & capex | capital discipline vs commodity leverage |
| oil & gas services and equipment | adjusted EBITDA margin by segment `IS_P_114-M` | revenue + segment margin, backlog / bookings | activity and pricing cycle vs cost absorption |
| telecommunications | **adjusted EBITDA margin** `IS_P_114-M` | service revenue growth, free cash flow, and the subscriber guide the issuer actually publishes | subscriber growth versus price and cost discipline; convergence and fixed wireless |
| electric utilities | operating margin `IS_P_110-M` | rate-base & EPS growth | rate-base vs regulatory/rate risk |
| renewable energy | **adjusted EBITDA margin** `IS_P_114-M` | MW or GW sold, revenue and gross profit | bookings and ASP versus cost per watt, and policy or tax-credit dependence |
| chemicals / containers | EBITDA margin `IS_P_114-M` | volume & margin | cycle trough / input costs |
| metals and mining | **adjusted EBITDA margin** `IS_P_114-M` | mined and sold production volumes by metal, plus capex | volume and grade versus realised price, and capital discipline |
| steel | **adjusted EBITDA margin** `IS_P_114-M` | next-quarter earnings direction by segment, plus total shipments | realised price and the metal spread versus shipment volume |
| construction materials | **adjusted EBITDA margin** `IS_P_114-M` | adjusted EBITDA, plus aggregates volume and freight-adjusted price | the price and cost spread versus the volume cycle |
| home construction | gross margin `IS_P_106-M` | orders/closings & margin | rates & affordability vs demand |
| retail (staples/discretionary), e-commerce, wholesale | operating margin `IS_P_110-M` | comparable sales (SSS) + margin | comps durability, traffic vs ticket, margin |
| leisure facilities / consumer services | operating margin `IS_P_110-M` | comps/RevPAR + unit growth | demand resilience / unit economics |
| food / beverages / household products | operating margin `IS_P_110-M` | organic sales + margin | volume vs price, input costs |
| apparel / leisure products | gross margin `IS_P_106-M` | revenue + gross margin | brand demand / inventory |
| default | operating margin (ex-1x) `IS_P_110-M` | revenue + EPS | growth vs valuation |
| oil and gas supply chain | **adjusted EBITDA margin** `IS_P_114-M` | production volumes, unit operating cost and capex | production and unit cost versus the commodity price |
| industrial intermediate products | **gross margin** `IS_P_106-M` | revenue and gross margin | volume and price versus input cost, and acquisition contribution |
| home and office products | **adjusted EBITDA margin** `IS_P_114-M` | segment revenue growth and adjusted EPS, often as a direction rather than a number | volume and price versus input cost across workplace and residential end markets |
| gas and water utilities | **EBIT margin** `IS_P_109-M` (reported; mark with an asterisk) | adjusted EPS, capex and DPS | rate-base growth and regulatory recovery versus capex funding |
| advertising and marketing | **adjusted EBIT margin** `IS_P_110-M` | organic revenue growth | organic growth durability versus client concentration and AI disruption |
| publishing and broadcasting | **adjusted EBITDA margin** `IS_P_114-M` | digital subscription revenue growth and digital advertising growth | subscriber growth and ARPU versus content and cost inflation |
| forestry, paper and wood products | **adjusted EBITDA margin** `IS_P_114-M` | segment EBITDA and segment revenue | product price and volume versus mill cost, and housing demand |
| tobacco and cannabis | **adjusted EBIT margin** `IS_P_110-M` | organic revenue growth and adjusted EPS | the smoke-free transition versus combustible volume decline and pricing |
| cable and satellite | **adjusted EBITDA margin** `IS_P_114-M` | the subscriber and net-add lines the issuer publishes; MANY GUIDE NOTHING, in which case say so and frame off the KPIs | broadband and video subscriber losses versus ARPU and cost discipline |
| consumer services (education, funeral, market research) | **adjusted EBITDA margin** `IS_P_114-M` | revenue, adjusted EBITDA and the volume driver the issuer publishes (enrollments, interments) | enrollment or volume growth versus pricing and cost |
| electric and gas marketing and trading | **adjusted EBITDA margin** `IS_P_114-M` | segment adjusted EBITDA and capex | supply margin normalisation versus infrastructure and optimisation earnings |
| default | operating margin (ex-1x) `IS_P_110-M` | revenue + EPS | growth vs valuation |

## Resolving decisions 2 and 3 from LIVE data, before reading the table

The table above is a **fallback, not the authority**. Two of the four decisions can be resolved from the
MCP for the issuer in front of you, and where the live answer exists it WINS. A frozen row cannot know
what a company guides; `get_guidance` does.

**Decision 3, the guidance variable: read it, do not assume it.** Call
`get_guidance(ticker_id, latest=true)` FIRST and take the guidance variable from the metrics the issuer
actually guides, ordered by how often it reguides them. Only fall back to the table row when the call
returns nothing.

This is not hypothetical. The `default` row prescribes "revenue + EPS", yet a telecom issuer may
**guide no revenue line at all**, publishing FY adjusted EPS, OCF, capex, FCF, service revenue growth
and subscriber net adds instead. Leading that wrap on a revenue guide means leading on a number that
does not exist. The same call resolves an airline or an insurance broker without any row being
written for them.

**Decision 2, the primary margin: prove the code before committing to it.** Fire the row's margin LTCODE
with `get_actuals` for the printed quarter. If it returns nothing, the row is describing a different
business inside the same label and you take the next margin down that does return: adjusted EBITDA margin
`IS_P_114-M`, then adjusted EBIT margin `IS_P_110-M`, then gross margin `IS_P_106-M`. Record the
substitution in `kpiCap` in one line.

The three labels where this probe matters most:

| Label | Row fits | Row does NOT fit | Probe |
|---|---|---|---|
| `Insurance` | underwriters | brokers | `IS_P_403` Net Premium Earned |
| `Health Care Facilities and Services` | payors | hospitals, distributors | `IS_P_903` Consolidated MLR |
| `Oil and Gas` | E&P, integrated | services and equipment | `IS_P_172` EBITDAX |

On an insurance broker every insurance code can return zero rows while `IS_P_110` and `IS_P_114`
both populate, so the broker takes an operating-margin read and its own `top_kpi` set, which returns organic revenue growth and
segment revenue. A combined ratio on an insurance broker is a metric the issuer does not report.

**Decision 1 is unaffected.** The Primary KPI is still resolved only by the Step 0 MCP ladder and picked
mechanically by `kpi_gate.py --pick-primary`. Nothing here reintroduces judgement into that choice.

### What proving a row can change

The sector rows are proved the same way: the margin ltcode fired with `get_actuals`, the guidance
variable read from `get_guidance(latest=true)`. Proving them shows three patterns worth keeping:

1. **An issuer can guide no revenue and no EPS.** A miner may publish only mined tonnes by metal
   and capex. The `default` row prescribes a revenue and EPS guide, so a wrap built on it would
   lead on two numbers that do not exist. `sector_gate` blocks exactly this.
2. **An issuer can guide gross profit while its gross-margin ltcode is empty.** Guiding a line and
   the database carrying its margin are different facts. This is why `marginProved` exists: what
   the company says is not what `get_actuals` returns.
3. **A real estate developer can return nothing for the whole ladder** (`IS_P_114-M`,
   `IS_P_110-M`, `IS_P_106-M`) and have an EMPTY `top_kpi` set. Its margin is then the reported
   `IS_P_109-M`, which carries an asterisk under the adjusted-first rule, and its KPIs need Tier 2
   or Tier 3.

**All of this is recorded and gated.** The row you matched, the margin ltcode and its
`get_actuals` proof, whether `get_guidance` returned rows, and every margin substitution go into the
config's `sectorProfile` block. `sector_gate()` in `render_wrap.py` refuses to render an unproved
margin code or a table-sourced guidance variable on an issuer that guides live, and WARNs when no row
matched at all. This table is therefore not a rule someone has to remember to follow: a run
that ignores it either fails or says so on the page.

## Tier 3 discovery keyword sets (`mcp_discovery`)

Used ONLY when the MCP returns zero KPI-tagged line items at every tier for a ticker (a
tagging gap, not a data gap — the operating series exist untagged). Pass the row's keywords
to `search_lineitem` with **no filter**, max 15 keywords per search, then prove each
candidate ltcode with `get_actuals` for the printed quarter AND the year-ago quarter.
Discard anything that does not return both. Cap the surviving universe at 6, ordered by the
Rule 12 sector spine.

| `insync_subindustry` contains | Discovery keywords (no filter) |
|---|---|
| semiconductors | **gross margin** `IS_P_106-M` | next-Q revenue + gross margin | AI/data-center structural vs cyclical; inventory |
| technology hardware | gross margin `IS_P_106-M` | next-Q revenue + gross margin | unit demand / mix / cycle |
| software / technology services | **FCF margin** `CF_P_110-M` | revenue + recurring (ARR/bookings) growth | growth durability vs valuation / AI disruption |
| internet media | EBITDA margin `IS_P_114-M` | revenue growth & engagement | monetization compounding vs decel |
| banking | NIM (FTE) `IS_P_204` | Net Interest Income (FTE) | NII durability vs credit normalization / capital return |
| specialty finance | pre-tax margin (pre-provision for a lender) `IS_P_118-M` | net interest income, purchase volume, net charge-off rate | credit normalization vs spend growth |
| telecommunications | **adjusted EBITDA margin** `IS_P_114-M` | service revenue growth, free cash flow, and the subscriber guide the issuer actually publishes | subscriber growth versus price and cost discipline; convergence and fixed wireless |
| insurance | net premiums earned, combined ratio, catastrophe losses, net investment income, book value per share, policies in force |
| managed care / health care facilities | medical loss ratio, membership, premiums, admissions, scripts |
| biotech and pharma | operating margin `IS_P_110-M` | key-product revenue + FY EPS | key-drug ramp vs pipeline/patent risk |
| machinery / electrical equipment / diversified industrial / aerospace / E&C | orders, backlog, book-to-bill, segment revenue, segment margin, organic growth, capacity utilization |
| transportation & logistics | operating margin `IS_P_110-M` | volume & yield | freight cycle / pricing |
| automotive | adjusted automotive EBIT margin `IS_P_148-M` | deliveries or wholesale units + segment EBIT | volume/pricing vs cost |
| real estate services | fee revenue, segment EBITDA, in-process pipeline, leasing revenue, capital markets revenue |
| oil & gas / midstream | production, volumes, realized price, capex, free cash flow, throughput |
| electric utilities | operating margin `IS_P_110-M` | rate-base & EPS growth | rate-base vs regulatory/rate risk |
| REIT / real estate | FFO, AFFO, same-store NOI, occupancy, leasing spreads, dividend per share |
| retail / e-commerce / wholesale | comparable sales, same-store sales, traffic, average ticket, digital sales, membership, GMV, store count |
| leisure facilities / consumer services | operating margin `IS_P_110-M` | comps/RevPAR + unit growth | demand resilience / unit economics |
| food / beverages / household products | operating margin `IS_P_110-M` | organic sales + margin | volume vs price, input costs |
| metals and mining / steel | sales volume, mined production, realised price, ASP per ton, shipments, cash cost per pound, scrap cost, by-product credits, capex |
| construction materials | **adjusted EBITDA margin** `IS_P_114-M` | adjusted EBITDA, plus aggregates volume and freight-adjusted price | the price and cost spread versus the volume cycle |
| renewable energy | **adjusted EBITDA margin** `IS_P_114-M` | MW or GW sold, revenue and gross profit | bookings and ASP versus cost per watt, and policy or tax-credit dependence |
| transportation equipment | **adjusted EBITDA margin** `IS_P_114-M` | segment revenue growth and segment EBITDA | the truck and off-highway cycle versus power-systems and data-centre demand |
| commercial and industrial support services | organic revenue, average yield, volume growth, fleet productivity, original equipment cost, used-equipment sales, specialty revenue, equipment cost |
| real estate owners and developers | leasable area, vacancy rate, occupancy, condominiums sold, units, land bank, contract progress rate, segment operating profit, NAV, dividend per share |
| apparel / leisure products | gross margin `IS_P_106-M` | revenue + gross margin | brand demand / inventory |
| chemicals / containers & packaging | volume, price, EBITDA margin, segment revenue, utilization |
| home construction | gross margin `IS_P_106-M` | orders/closings & margin | rates & affordability vs demand |
| default | operating margin (ex-1x) `IS_P_110-M` | revenue + EPS | growth vs valuation |

Record the keyword set used, every candidate ltcode, and which ones proved out in the
config's `kpiDiscovery` field. That field is the audit trail; the renderer blocks the build
if provenance is `mcp_discovery` and it is missing.

**`_S_` codes are per-ticker and must never be reused across issuers.** Any ltcode in the
`SD_S_`, `IS_S_`, `CF_S_`, `KM_S_`, `RT_S_` or `BS_S_` family is a per-issuer slot, not a
universal metric: the same `_S_` code can be a subsidiary's capex (a negative cost line) on one
utility and calculated EBITDA (a positive profit line tagged `top_kpi`) on another. Carrying
one across tickers inverts both the sign and the polarity while every downstream check stays
internally consistent, so nothing warns. Only the `_P_` family is stable across issuers.
Re-resolve every `_S_` code through `search_lineitem` for the ticker in front of you. The
Step 0 ladder already does this, which is why it is the authority and these tables are not.

## Data-availability substitution (ALL tiers — "no data -> next KPI")

The prove-then-include discipline is not Tier-3-only. At EVERY tier, prove each selected KPI with
`get_actuals` before it is written into the Event Snapshot or the KPI table, and substitute any KPI
that has no data with the next available one.

- **"Has data" is defined by two anchors, not a quarter count.** A KPI qualifies if it returns a
  value for the **printed quarter AND the year-ago quarter** — the two anchors the Event Snapshot
  Y/Y and the KPI-table trailing Y/Y are computed from. A KPI with a partial history (e.g. 6 of the
  trailing 8 quarters, or a newly disclosed metric with only 6 quarters) is **KEPT**, provided both
  anchors are present; missing intermediate quarters render `n/a` in that single cell only.
- **Substitute only when unusable:** the printed-quarter value is missing, or the year-ago anchor is
  missing (no Y/Y computable). Then drop the KPI and pull the next-ranked member of the SAME universe
  that proves out. Keep walking the ranking until the slot is filled.
- **Primary KPI included:** if the top-ranked KPI is unusable, promote the next proven KPI to Primary
  (still a universe member, so the gate passes). The headline metric is never `n/a`.
- **Exhaust the universe, then descend the ladder** (Tier 2, then Tier 3 discovery). An `n/a` KPI row
  is a true last resort only — every tier dry, no substitute anywhere — and is disclosed in `kpiCap`.
- **Audit trail:** log each swap in the config's `kpiSubstitutions` field
  (`dropped`, `droppedLtcode`, `reason`, `replacement`, `replacementLtcode`).

## Success criterion
A wrap is successful when a sector specialist agrees the four selections are the right
ones for that company that quarter — regardless of layout. A structurally perfect note
with the wrong KPI is a failure; the correct KPI in a plain table is a pass.

## Validation
Machinery, software, banking, managed care, semiconductor, retail, pharma and energy issuers
each resolve to sector-appropriate selections. Re-run the Step 0 MCP ladder for any new ticker
before building its wrap.


---

# Sector spines in full. This is the authoritative text; SKILL.md points here.

## Sector spines (ltcode level)

### Sector spine: Financials and Insurance
Detect the sector from the `insync_subindustry` returned by `search_tickerid`. For Banking and Insurance the revenue/EBITDA/EPS spine does not apply: EBITDA is undefined and EV multiples are meaningless. Swap the financial spine to Net Interest Income (`IS_P_201`), Non-Interest Income (`IS_P_211`), Total Revenue (`IS_P_101`), Provision for Credit Losses (`IS_P_207`), Efficiency Ratio (`IS_P_205`), and CET1 (`BS_P_287`). Value on P/E and P/B, never EV/EBITDA. Reconcile NII plus non-interest income to total revenue. Lead the verdict on NII and pre-provision metrics; treat one-time swings in non-interest income and EPS as non-operational per the EPS-basis guard. **For P&C / insurance specifically**, the spine is Net Premiums Earned (`IS_P_403`), Combined Ratio (`SD_P_103`) and the accident-year ex-cat ratio (`SD_P_108`), Net Investment Income (`IS_P_410`), and Book Value Per Share (`SD_S_962`). Value on P/E and P/B (book value); lead the verdict on the combined ratio (lower is better) and BVPS growth, and treat catastrophe losses as the swing item.

### Sector spine: Utilities (and capital-intensive, regulated, income sectors)
Detect Utilities from `insync_subindustry` (Electric / Gas / Multi / Water Utilities).
For a regulated utility the generic Revenue/EBITDA/EPS spine under-describes the business and revenue
is a weak signal (largely fuel and rate pass-through). The Key Financial Metrics band (Section 4)
and the KPI section (Section 6) MUST carry the utility spine, all of which exist in InSync:
- **Adjusted EPS** (`IS_P_128`) and **segment earnings** — regulated-utility net income plus
  unregulated/renewables net income, or segment adjusted EPS (e.g. FPL `IS_S_539`, NEER `IS_S_706`;
  segment net income such as FPL `SD_S_1091`).
- **DPS and payout** — dividend paid (`CF_P_104`) expressed per share, and payout ratio (`CF_P_107`).
- **Capex** (`SD_S_1177`; segment capex `CF_S_143` / `CF_S_145` where relevant).
- **FCF** (`CF_P_110`); where FCF is structurally negative during a build cycle, show **OCF**
  (`CF_P_108`) and say so.
- **Earned / regulated Return on Equity** (`SD_S_1182` retail, `SD_S_1183` blended).
- **Rate base** (the Closing Rate Base KPI from the MCP universe) and its growth.
Value on P/E and dividend yield; EV/EBITDA only as secondary. Lead the verdict on regulated EPS
growth, rate-base / backlog growth and RoE, not on total revenue (grade revenue cautiously and say
so when the consensus is thin). **This generalizes:** any capital-intensive, regulated or
income-oriented sector (Utilities, REITs, Telecom, Pipelines / Midstream, Energy) MUST surface
Capex, FCF or OCF, and the shareholder-return metric (DPS and payout) in the spine, and value on
the sector cash-flow metric (REITs: FFO / AFFO and P/FFO, never EV/EBITDA alone).

> **Declare the direction of Capex, every time, on every note in these sectors.** `capex`
> is in the renderer's `LOWER_IS_BETTER_CUES` list and deliberately NOT in
> `references/polarity_registry.json`, because its direction is genuinely sector dependent: a
> rate-base build is a regulated utility's growth signal and a rise is good news, while on an
> asset-light issuer the same rise is a cash drain. The registry exists to settle a code once for
> every future note, and capex cannot be settled that way without being wrong half the time.
> Listing it as a CUE instead means the `polarity` gate ASKS, and it only ever asks: an undeclared
> capex row BLOCKS the build with a message, it never guesses a colour. So the note carries one
> explicit declaration, `inv: true` on the `results` row (or `snapshot.inv`) where lower is better
> and `snapshot.noinv` where higher is better, and says which in one line, so two analysts never
> silently choose opposite colours for the same line.

> **P/FFO has to be DERIVED: `get_valuation` does not publish it.** Its price-multiple list is
> P/S, P/E, P/E_Adj, P/FCF, P/OCF, P/B and PEG, with no FFO multiple at all. Both inputs are
> already in hand: take the price from `get_market_data` on the hero as-of date and divide by the Core FFO
> per share the company guides (`CF_P_209` in `get_guidance`, midpoint of the range) or by the
> trailing four reported quarters. Show it as `P/FFO (derived)` and say in `valNote` which two
> figures it came from and on what date. Where neither FFO basis is available, use P/FCF, label
> it as the substitute, and say so in one line. Never publish EV/EBITDA alone for a REIT.

State the metric
basis in one line.

### Sector spine: Telecommunications
Detect Telecom from `insync_subindustry` (Telecommunications, wireless, cable). A telecom is not a
rate-regulated utility: no rate base, no allowed RoE. The Key Financial Metrics band and Section 6
carry **service revenue** growth (the service line, not equipment), **adjusted EBITDA** and its
margin (`IS_P_114` / `IS_P_114-M`), **capex** and capex intensity, **free cash flow** (`CF_P_110`),
and the subscriber KPIs the issuer publishes, from the MCP KPI universe: net adds (postpaid phone,
broadband, fixed wireless), churn (lower is better: declare it) and ARPU. Value on EV/EBITDA and
FCF yield, plus dividend payout where one is paid.

### Sector spine: Asset Management
Detect from `insync_subindustry` (Asset Management). Not a bank: no NII, no CET1. The spine is
**AUM** (period end and average) and **net flows** from the MCP KPI universe, **revenue** and the
**fee rate** it implies against average AUM, **operating margin** (`IS_P_110-M`, the sector row's
margin), **adjusted EPS** (`IS_P_128`), and performance fees shown apart from management fees.
Lead the verdict on flows and the fee rate, not on market-driven AUM moves. Value on P/E. A custody
bank carries both this and the Financials spine: say which one the note leads on.

### Sector spine: REITs
Detect REITs from `insync_subindustry` (REIT / Real Estate). GAAP EPS and EBITDA are
uninformative for REITs because of heavy depreciation, so the Key Financial Metrics band leads with
**FFO** (`CF_P_113`) and **AFFO** (`CF_P_114`), or per share (`CF_P_202` / `CF_P_203`); plus
**same-store NOI** and **occupancy** (the company-specific KPI from the MCP universe); and **DPS and
payout** (dividend `CF_P_104`, payout `CF_P_107`). Value on **P/FFO and dividend yield**, not P/E or
EV/EBITDA. Lead the verdict on FFO / AFFO per share and same-store NOI, not net income.


## Polarity of the spine codes

The spine codes named above carry a DECLARED direction in
`references/polarity_registry.json`, so a note that grades one of them is coloured from a
declaration rather than from the sign of the change. Three of these stable `_P_` codes are
lower-is-better lines whose LABEL carries no cue the gate could
ever have seen:

| code | line | direction |
|---|---|---|
| `IS_P_205` | Efficiency ratio (bank) | lower is better |
| `IS_P_207` | Provision for credit losses | lower is better |
| `SD_P_108` | Accident-year ex-catastrophe ratio (insurance) | lower is better |

The rest are higher-is-better spine lines (`IS_P_201` net interest income, `IS_P_211`
non-interest income, `IS_P_410` net investment income, `BS_P_287` CET1, the FFO and AFFO codes,
`CF_P_110` free cash flow, and so on), declared for the same reason: a resolver that can only
answer True or False cannot tell a metric DECLARED higher-is-better from one that merely missed
every lookup, so nothing downstream could warn about the second.

Only `_P_` codes belong in the registry. **`_S_` codes are per-ticker and must never be added**:
the same segment code is a cost line on one issuer and a profit line on another.

## Industry-specific KPI framework (per-sector driver map)

- **Industry-specific KPI framework (dynamic, mandatory).** Determine the primary earnings driver
  for the sector and surface it explicitly. If a KPI is the primary driver of earnings for the
  sector, it MUST appear in BOTH the Actual-versus-Consensus table (Section 3) and the KPI table
  (Section 5). Sector map (extend as needed):
  - **Managed care / health insurers:** Consolidated Medical Loss Ratio / Medical Care Ratio
    (`IS_P_903`) is MANDATORY as the top KPI in both tables; plus MA membership, commercial
    membership, pharmacy scripts. A managed-care wrap without MLR is INCOMPLETE, not acceptable.
  - **Software:** ARR, Net Revenue Retention (NRR), RPO, customer count, cloud/subscription revenue.
  - **Semiconductors:** end-market segment revenue, gross margin, inventory days.
  - **Internet:** DAU/MAU, ARPU, advertising revenue, cloud revenue.
  - **Banks:** Net Interest Income / NIM, loan growth, deposits, provisions/reserves, CET1
    (see the Financials sector spine above).
  - **Industrials:** backlog, orders, capacity utilization, segment margins.
  - **Consumer / retail:** comparable sales, traffic, average ticket, unit volume.
  - **Utilities:** rate base and rate-base growth, earned / regulated RoE, Capex and FCF or OCF,
    DPS and payout, and segment earnings (regulated versus unregulated). Adjusted EPS and DPS are
    mandatory. See the Utilities sector spine above.
  - **REITs:** FFO / AFFO, same-store NOI, occupancy, DPS and payout; value on P/FFO.
  Capital-intensive and income sectors (Utilities, REITs, Telecom, Energy, Midstream) MUST always
  surface Capex, FCF or OCF, and DPS / payout in the spine, not only revenue and EPS.

## Worked Tier 3 discovery example

A semiconductor-equipment maker returns zero rows at Tiers 1 and 2, every `tags` field empty.
Subindustry Semiconductors yields the keywords bookings, backlog, system sales, installed base,
gross margin. `get_actuals` then proves Bookings Value `KM_P_1011`, Backlog Value `KM_P_1015`,
System Revenue `SD_P_1046` and two issuer-specific `_S_` segment lines across five consecutive
quarters, so the KPI table carries a value plus a Y/Y on every row with no `n/a`. Provenance `mcp_discovery`, disclosed in `kpiCap`.

## The resolution ladder in full

SKILL.md carries the four-tier summary and the prove-then-include rule; this is the full text.

### MCP KPI Resolution Ladder (deterministic, MCP-only, four tiers)
Descend ONLY on an empty result. Never skip a tier, never blend two tiers into one universe, and
never substitute judgement for a tier that returned rows.

- **Tier 1 `mcp_top_kpi` (default).** `search_lineitem` with `queries='all'`, `filter='top_kpi'`.
  The returned list (names + ltcodes) IS the KPI universe; the rest are secondary / supporting KPIs
  and their ltcodes drive the KPI table series. **The Primary KPI is picked mechanically, never by
  judgement** (two models can split when two entries carry `driver_kpi`): candidates
  are the entries carrying the highest-priority tag present, ranked `driver_kpi` >
  `earnings_flash_kpi` > `company_kpi` > `industry_kpi`; among those the one with the MOST tags
  wins; any remaining tie breaks on the MCP's return order. Run
  `python references/kpi_gate.py --pick-primary "name~tags;name~tags;..."` and use what it returns.
  Where the mechanical answer disagrees with house view, fix the tagging in InSync rather than
  overriding here: overriding reintroduces exactly the variance this removes.
- **Tier 2 `mcp_kpi_tier`.** If Tier 1 returns zero rows, re-run `queries='all'` with **no filter**
  (the tool then spans every KPI tier: `top_kpi, company_kpi, driver_kpi, industry_kpi,
  earnings_flash_kpi`). If rows come back, that is the universe. Choose the Primary KPI by the
  documented tier priority `top_kpi > company_kpi > driver_kpi > industry_kpi > earnings_flash_kpi`,
  reading each row's pipe-joined `tags` field. Name the tier actually used in `kpiCap`.
- **Tier 3 `mcp_discovery`.** If Tier 2 also returns zero rows, the ticker carries no KPI tagging in
  InSync (a tagging gap, not a data gap: the operating series still exist untagged). Recover them
  from the MCP by keyword, never from a file and never from memory:
  1. Take the `insync_subindustry` returned by `search_tickerid` and load its keyword set from
     `references/kpi-selection-standard.md`.
  2. Run `search_lineitem` with those keywords and **no filter** (max 15 keywords per search;
     retry narrower on a ~180s timeout).
  3. **Prove every candidate before it is allowed in.** Run `get_actuals` on each candidate ltcode
     for the printed quarter and the four prior quarters. A candidate enters `kpiUniverse` ONLY if
     it returns a value for the printed quarter AND for the year-ago quarter. An ltcode that
     returns nothing is DISCARDED, never carried as `n/a`. This is what keeps Rule 11 satisfiable.
  4. Universe = the proven names, capped at 6, ordered by the sector spine in Rule 12.
  Disclose the tier in `kpiCap` and in `sources` with one line: "KPI series resolved by InSync
  line-item discovery; [TICKER] carries no KPI-tier tagging in InSync." Record the keyword set, the
  candidate ltcodes, and which ones proved out in the config as `kpiDiscovery`.
- **Tier 4 stop.** If discovery also proves nothing, say so and stop. Do not publish around the gap.

A Tier 3 walkthrough (zero rows at Tiers 1 and 2, five KPIs recovered by keyword and proved
against `get_actuals`) is under "Worked Tier 3 discovery example" above.

Provenance is disclosed, never hidden. Tiers 2 and 3 are a widening of WHICH MCP endpoint supplies
the KPIs, not a relaxation of the MCP-only rule, so the anti-hallucination guarantee is unchanged:
every name and every number still traces to a live MCP call recorded in the config.

### Data-availability substitution (ALL tiers, MANDATORY — "no data -> next KPI")
Prove-then-include applies at EVERY tier, not just discovery. Before a KPI enters the Event Snapshot
or the KPI Hierarchy, run `get_actuals` on its ltcode for the printed quarter, the year-ago quarter
and the trailing four.

- **"Has data" means the printed quarter AND the year-ago quarter both return a value.** Those are
  the two anchors the note structurally needs. A short or gappy history is KEPT: a missing
  intermediate quarter renders `n/a` in that one cell while the row still carries a real value and a
  computable Y/Y, which satisfies Rule 11. Do NOT swap out a usable short-history KPI.
- **Drop only when genuinely unusable**: no printed value, or no year-ago anchor. Then take the
  NEXT-ranked member of the SAME universe and prove that one. Keep walking the ranking.
- **This applies to the Primary KPI too.** If the top-ranked KPI is unusable, PROMOTE the next proven
  KPI. It is still a universe member, so `kpi_gate` still passes and the headline is never `n/a`.
- **Exhaust the current universe before descending the ladder.** An `n/a` KPI row is a last resort
  only, every tier dry, and must be disclosed in one line in `kpiCap`.
- **Log every swap** in `kpiSubstitutions`: `[{dropped, droppedLtcode, reason:"no printed value" |
  "no year-ago anchor" | "zero-broker consensus", replacement, replacementLtcode}]`. A KPI
  whose pre-print consensus rests on 0 brokers is swapped the same way, for the next-ranked KPI that
  has a consensus from 1 or more; if none has, it is kept and prints ungraded. Keep a minimum of 2 company-specific KPIs
  after substitution. A KPI kept despite a partial history is NOT a substitution and is not logged.

