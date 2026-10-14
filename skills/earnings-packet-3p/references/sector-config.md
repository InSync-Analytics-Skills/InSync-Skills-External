# Sector configuration, spine, drivers, valuation lens

One `sector_key`, resolved once at Step 1 from the InSync subindustry, drives three
choices for the whole run. Layout never changes; only content does.

---

## Resolving the key

`search_tickerid` returns TWO classification fields, `industry` and
`insync_subindustry`. **Resolve on `industry` first, then fall through to the
subindustry keyword tests.** The industry label is unambiguous for eight of the
twenty industries in the coverage, and reading it first removes a whole class of
substring accident.

**Step 1, the industry test. First match wins.**

| InSync `industry` | sector_key |
|---|---|
| `Banking` | `banking` |
| `Insurance` | `insurance` |
| `Financial Services` | `financials_nonbank` |
| `Utilities` | `utilities` |
| `Materials` | `energy_materials` |
| `Oil and Gas` | `energy_materials` |
| `Telecommunications` | `telecom` |
| `Industrial Products` | `default` |

`Real Estate` is deliberately absent. That industry carries operators, developers
and services businesses as well as REITs, and none of those three is an FFO
vehicle, so REIT stays a subindustry test.

**Step 2, the subindustry keyword tests**, for any issuer the industry test did not
resolve. First match wins, in this order:

| InSync subindustry contains | sector_key |
|---|---|
| REIT, real estate investment trust | `reit` |
| bank, thrift, diversified financial, capital markets | `banking` |
| insurance, reinsurance, P&C | `insurance` |
| oil, coal, mining, metals, chemicals, paper, steel, packaging, construction materials | `energy_materials` |
| electric, gas utility, water, multi-utility, power | `utilities` |
| software **and** loss-making or revenue growth > 20% | `software_growth` |
| software, otherwise | `software_mature` |
| internet | `internet_platform` |
| IT services **and** loss-making or revenue growth > 20% | `software_growth` |
| everything else | `default` |

When the subindustry is ambiguous, use `default` and say so in the report.
`default` is never wrong, it is the universal spine, it is only sometimes incomplete.

### Two ordering rules that are not optional

**The bare keyword `gas` is gone from the energy list, and it must stay gone.**
InSync labels a regulated gas and water utility `Gas and Water Utilities`, which
contains `gas`, so under a rule that tests the energy keywords before the utility
keywords a water utility resolves to `energy_materials` and is handed a production
volume, realised price and unit cash cost spine with an EV/EBITDA-led lens. The
industry test in step 1 now
catches every utility before any keyword runs, and `gas` no longer appears in the
energy keyword list. Re-adding it reopens the bug.

**`Industrial Products` is in the industry table for the same reason.** Its
`Electrical Equipment` subindustry contains `electric`, so without the industry test
every electrical-equipment maker resolves to `utilities`, is handed a rate-base spine
and a P/B in the lens, and section 7 prints "the valuation lens for utilities" above
its multiples. Removing `electric` from the utility keyword list is NOT the
fix: a genuine electric utility needs it. Catching the industry first is.

**`life` is gone from the insurance list.** It was there to catch a life insurer,
but InSync labels every insurer, broker and reinsurer alike as `Insurance`, so the
industry test resolves all of them and the keyword bought nothing while remaining
free to match an unrelated label.

---

## 1. Model spine (section 3)

**Universal spine, always present, in this order:**

```
Revenue                             $   with a Y/Y % sub-row
Gross profit (Adj)                  $   with a gross margin % sub-row
Operating profit (Adj)              $   with an EBIT margin % sub-row
EBITDA (Adj)                        $   with an EBITDA margin % sub-row
Net income (Adj)                    $
EPS, diluted (Adj)                  $
Operating cash flow                 $
Free cash flow                      $
Net debt                            $
```

Dollar lines carry the margin beneath them, not a second growth row. Y/Y sits on revenue
only: nine rows each with a Y/Y sub-row is eighteen rows and the section stops being
readable. The margin *is* the interesting derivative for a profit line.

**Sector additions**, inserted after EPS and before cash flow:

| sector_key | add |
|---|---|
| `banking` | Net interest income, provisions, NIM %, CET1 %, gross loans, deposits. **Drop EBITDA and net debt**, neither is meaningful for a bank. |
| `insurance` | Net earned premium, combined ratio %, reserve development, book value per share |
| `reit` | FFO, AFFO, NOI, occupancy %. Prefer FFO to EPS as the headline earnings line. |
| `energy_materials` | Production volume, realised price per unit, unit cash cost |
| `utilities` | Rate base, allowed ROE %, regulated vs unregulated EBIT |
| `software_growth` | ARR, net revenue retention %, stock-based comp, FCF margin % |
| `financials_nonbank` | Net revenues, fee-related earnings, book value per share, ROE or ROTE %. **Drop EBITDA and net debt**, as for a bank. Add AUM and net flows for an asset manager, net interest income and provisions for a lending-led issuer |
| `telecom` | Service revenue, subscriber base, ARPU, churn %, capex, capex/sales % |
| `software_mature` | ARR or RPO, whichever the company publishes, net revenue retention % where published, stock-based comp, FCF margin % |
| `internet_platform` | The published volume measure (bookings, gross bookings, GMV, paid members, trips), revenue by segment, stock-based comp, capex |
| `default` | nothing |

Keep the spine to 9–11 rows. With margin sub-rows that is already 14–16 table rows,
which is one third of a page.

---

## 2. Driver KPIs (section 5)

Section 5 is two tables: **segment revenue and segment profit**, then **operating
drivers**. The drivers are whatever the company itself publishes and the street models, resolve them from the transcript and the KPI feed, never from a fixed list.

Rules that hold for every sector:

- **Dollar-denominated drivers carry a Y/Y % sub-row.** Percentage drivers do not: they
  are already growth rates, and a growth rate of a growth rate is noise.
- **Segment revenue carries Y/Y; segment profit carries margin.** For revenue the
  question is how fast it grows; for profit it is what margin it earns.
- Where a driver is published to one decimal by the company, render it to one decimal.
  Two decimals on a number management gives as a round figure implies a precision the
  disclosure does not have.

Typical driver sets:

| sector_key | drivers |
|---|---|
| `banking` | NIM, cost/income, NPL ratio, loan growth, deposit growth, fee income mix |
| `insurance` | Combined ratio, loss ratio, expense ratio, retention, cat load, rate change |
| `reit` | Same-store NOI, occupancy, leasing spreads, development yield |
| `energy_materials` | Volumes, realised price, unit cash cost, reserve life |
| `utilities` | Rate base growth, load growth, capex, allowed vs earned ROE |
| `software_growth` | ARR, NRR, gross retention, billings, magic number, headcount |
| `financials_nonbank` | AUM, net flows, fee rate bps, net revenue mix, credit costs, NCO rate, book value per share |
| `telecom` | Postpaid and prepaid net adds, ARPU, churn, service revenue growth, capex intensity |
| `software_mature` | ARR or RPO, cRPO, billings, renewal rate, segment revenue growth |
| `internet_platform` | Bookings or GMV, users or paid members, take rate, engagement, ad pricing and impressions |
| `default` | same-store sales, average ticket, traffic, eCommerce, units, stores, pricing |

---

## 3. Valuation lens (sections 7 AND 8)

Only multiples that are **economically meaningful for the sector** are shown. Everything
is computed and retained in `val_raw`; anything outside the lens goes to `val_excluded`
with a reason, and that reason reaches the page.

**This one list drives section 7 and section 8 both.** It is written to `valuation[]` in
sector order and to `peers.val_metrics`. The renderer names no multiple of its own, so
adding a sector here is the only edit needed to change what both sections show.

| sector_key | multiples shown, in order |
|---|---|
| `banking`, `insurance` | P/E, P/B |
| `reit` | P/E, EV/EBITDA, P/B  (no P/FFO; the feed carries no FFO-based metric, rule 5 below) |
| `energy_materials` | EV/EBITDA, P/E, P/B |
| `utilities` | P/E, EV/EBITDA, P/B |
| `software_growth` | EV/Sales, EV/EBITDA, P/E |
| `financials_nonbank` | P/E, P/B |
| `telecom` | EV/EBITDA, P/E |
| `software_mature` | P/E, EV/EBITDA, EV/Sales |
| `internet_platform` | EV/EBITDA, P/E, EV/Sales |
| `default` | P/E, EV/EBITDA |

### B10. The pre-profit ladder, when the lens loses its denominators

A lens is a list of multiples, and a multiple with a negative denominator is dropped with a
reason (`val_excluded`) and never printed. On a **pre-profit** issuer that removes most
sector lenses: `software_growth`'s EV/EBITDA and P/E both go, leaving EV/Sales alone, and
`default` loses both of its two. Without a ladder,
sections 7 and 8 carry a single multiple and the substitution is an undocumented operator
decision. Measured on a loss-making small-cap: adjusted EBITDA and adjusted earnings are negative in every
period of the window, so the three-multiple lens reduced to one.

**Where a lens multiple is dropped for a negative denominator, fill from this ladder, in
order, taking only as many as the sector lens originally held:**

```
1  EV/Sales                 a positive denominator for any issuer with revenue
2  EV/Gross profit (Adj)    the next line down the P&L that is still positive
3  P/B                      no forward variant, so the NTM cell stays empty and is footnoted
4  P/Sales                  only where EV is unavailable or meaningless (a bank, an insurer)
5  EV/Invested capital      last, and only where the three above are also unavailable
```

Skip any rung already in the lens, and skip any rung whose own denominator is negative
(book value can be, and a negative-book issuer takes `negative book` in the cell rather
than a number). **A bank or an insurer never takes rungs 1, 2 or 5**, because enterprise
value is undefined for them: on `banking`, `insurance` and `financials_nonbank` the ladder
is P/B then P/Sales and stops there.

Two things this is not. It is **not** a licence to print a multiple the sector would not
use in the good case: the ladder fires only for a multiple actually dropped, and only up to
the count the sector lens held, so a profitable software name still shows exactly EV/Sales,
EV/EBITDA and P/E. And it does **not** change rule 5: whatever the ladder produces is one
lens, in one order, in sections 7 and 8 both. Record the substitution in `warnings`, list
every dropped multiple in `val_excluded` with its reason, and let
`notes.valuation_excluded_reason` carry the reason to the page.

Where the ladder cannot fill to the sector's count, the section prints what exists and the
note says how many multiples the lens holds and why. One multiple with a stated reason is
honest; one multiple with no explanation is the failure.

**Insurance follows banking, for the same reason.** Reserves and float are the raw
material of an insurer, not a financing choice, so enterprise value is not a meaningful
numerator and EV/Sales and EV/EBITDA are excluded outright there too. An insurer usually
returns nothing on `IS_P_106`, `IS_P_110` and `IS_P_114`, so gross profit, operating
profit and EBITDA leave the spine entirely. **Revenue has no fixed insurer code:** take the
one SKILL.md rule 3's test picks, the code carrying BOTH actuals and target-period consensus.
Measured on one insurer: `IS_P_200` empty, `IS_P_101` actuals but no consensus, only
`IS_P_102` both; other insurers have used `IS_P_101`. The insurance scaffold leaves revenue
as a placeholder for that reason. The four insurance additions fill that space:
gross and net premium written, net premium earned, underwriting income with the combined
ratio beside it, and book value per share with return on equity beside it.

**Why banking forces the rule.** Enterprise value is undefined for a bank: deposits and
borrowings are raw material, not financing. EV/EBITDA and EV/Sales are therefore excluded
outright. Book value is the capital the franchise earns on, so P/B read against ROE is
the core lens. Printing EV/EBITDA on a bank is not a harmless extra column, it is a
wrong number on the page.

**Non-bank financials follow banking, for the third time the same reason.** InSync
puts brokers, exchanges, asset managers, alternative managers, card issuers and
consumer lenders under one industry, `Financial Services`, and none of them has a
meaningful enterprise value: for an exchange or an asset manager the balance sheet
is float and regulatory capital, and for a card issuer or a consumer lender deposits
and funding are raw material exactly as they are for a bank. Several of them are
bank holding companies. EV multiples are
therefore excluded outright for `financials_nonbank`, and P/B read against ROE or
ROTE is the core lens, as it is for `banking`.

Within the key, let the issuer's own disclosure choose the spine. An asset manager
leads on AUM, net flows and fee rate; an exchange or a broker leads on net revenue
by line and on volumes; a card issuer or a consumer lender leads on net interest
income, purchase volume and the net charge-off rate. **A mortgage REIT arrives here,
not under `reit`:** InSync classifies mortgage REITs as
`Specialty Finance`, so where a `Specialty Finance` issuer leads on book value per
share and net interest spread rather than on fee or card revenue, put P/B first in
the lens and carry book value per share, net interest spread and distributable
earnings in the spine, with EPS secondary.


**Data overrides, applied after the sector map, in order:**

1. **Negative or null denominator drops the multiple.** Negative adjusted EPS kills P/E;
   negative adjusted EBITDA kills EV/EBITDA. Never print a negative multiple and never
   print `n/m`, remove the row and record the reason in `val_excluded`.
2. **No InSync history keeps the row.** If the `1y` / `3y` summarise calls return
   nothing, print NTM and LTM and put *no history* in the average columns.
3. **P/B has no forward variant.** `get_valuation` in NTM mode returns the LTM value,
   because book value is not forecast. Footnote it or show P/B in one column. Never
   present NTM P/B as forward-looking.
4. **Cap the section at three multiples.** Beyond three it stops being readable at a
   glance. The order above is the priority order.
5. **A lens entry the feed does not carry falls through to the next.** The REIT row
   prefers P/FFO on the analytics, but InSync's `get_valuation` has no FFO- or AFFO-based
   metric at all, so **P/E leads for a REIT, and the lens table above says so with no
   conditional attached.** Do not add one such as "P/FFO ahead of P/E where `CF_P_114`
   exists": it describes a fallback that can never fire, because no metric exists to
   satisfy it. If an FFO metric is ever added to the feed, change this rule, the
   lens table and `sector_config.json` together; `self_check.py` compares the last two and
   fails if they disagree.

   **Do not synthesise the missing multiple** from price and a per-share estimate: section 7
   needs 1-year and 3-year daily averages, and a derived series has no history to average.
   State what was unavailable in the note and let the FFO and AFFO lines in the model spine
   carry that read.

InSync metric names, adjusted first per `insync-financial-data`: `P/E_Adj_Ratio`,
`EV/EBITDA_Adj`, `EV/Sales`, `P/B_Ratio`. History is the same call with
`summarize: true` and `start_date` of `1y` then `3y`, **`mode: "LTM"`** so the trailing
series is comparable to the LTM column. The NTM figure is a separate, unsummarised call.

---

## 4. Peer set (section 8)

3–5 comparables, `calendarize: true` **mandatory**, peers with different fiscal
year-ends are otherwise compared across different economic periods.

**The peer valuation band is built from the SAME sector lens as section 7.** A multiple
outside the lens does not appear cross-sectionally either. A bank peer set therefore
carries no EV multiple at all, and a REIT peer set carries no sales multiple: if the lens
says P/E and P/B, those are the only two multiples on the peer table, plus market
capitalisation and the two yields, which are not multiples.

The lead multiple, the first in the sector order, additionally carries its LTM, its 1-yr
average and the LTM-vs-1-yr comparison, so the peer table answers both "how do these
companies price against each other" and "where does each sit against its own history".
Later lens entries carry their NTM figure only, except a multiple with no forward variant
such as P/B, which carries LTM.

EV/Sales does not enter the peer table as a comparability check when it is outside the
lens. Two multiple vocabularies on
one page, one for the lead company and one for the peers, is a reader asking which of the
two the packet believes. One lens, both sections.

**A negative or absent denominator drops the cell, not the row.** Negative book equity is
common among tower and infrastructure issuers that have bought back more than their book,
so a peer P/B row will often be partly filled. Print the reason in the cell, `negative
book`, and let the rest of the row stand. Never print a negative multiple.

Disclose, always: differing same-store definitions, differing quarter lengths (a 16-week
retail quarter against a 13-week one), and any metric unavailable for part of the set.
An undisclosed definitional difference is worse than a missing row.

---

## 5. Heterogeneous subindustries: one key, several business models

Six InSync subindustries carry issuers that model differently from one another. The
key is still right, because it chooses the lens and the spine correctly for all of
them, but **the driver KPIs must come from what the issuer itself publishes**, which
is already this skill's rule. These are the six where following that rule matters
most, because a plausible-looking driver set can be simply the wrong business.

| subindustry | key | what varies inside it |
|---|---|---|
| `Oil and Gas Producers` | `energy_materials` | Integrateds, pure E&P, refiners, midstream and liquefaction all land here. The production volume, realised price and unit cash cost triplet fits an E&P. For a refiner take throughput, capture rate and crack; for midstream take throughput, tariff and contracted volumes; for liquefaction take trains in service and contracted capacity. |
| `Oil and Gas Services and Equipment` | `energy_materials` | Not a volume and realisation business at all. Take backlog, pricing, rig or fleet activity and margin by segment. |
| `REIT` | `reit` | Towers, data centres, hotels, self-storage, healthcare, office, retail and industrial are one label. Same-store NOI and occupancy hold everywhere; the third driver does not. Towers lead on organic tenant billings and churn, hotels on RevPAR, data centres on booked-not-billed and leasing. |
| `Specialty Finance` | `financials_nonbank` | Card issuers, consumer lenders, commercial finance and mortgage REITs. See the mortgage-REIT rule above. |
| `Health Care Facilities and Services` | `default` | Payors, hospital operators, drug distributors and CROs. A payor leads on medical loss ratio and membership; a hospital on admissions, acuity and payor mix; a distributor on revenue per script and generic deflation; a CRO on backlog and net new business. |
| `Technology Services` | `default` | Payment networks, IT services and data and analytics vendors. Payment networks lead on payment volume, cross-border volume and processed transactions; IT services firms on bookings, book-to-bill and headcount; data and analytics vendors on subscription revenue and price/volume. `default` is correct for all three: none is an ARR business in the software sense, and P/E with EV/EBITDA is the lens the street uses. |

`Technology Services` deliberately stays on `default`. It is tempting to route the
whole `Software and Tech Services` industry to a software key, and it is wrong: a
payment network priced on EV/Sales against a software peer set is a category error.
Only the `Software` subindustry itself takes a software key.

---

## 6. Ticker resolution, before any of this

**Compare the ticker the response RETURNS against the ticker you REQUESTED.**
Checking `match_type` is not enough. In testing, a request for one ticker came back as
an unrelated oilfield services company with
`match_type: "exact_ticker"` and `score: 100`. A `fuzzy_ticker` match substitutes too,
returning an unrelated issuer under a near-miss ticker. Every
one of these arrives as a complete, plausible record.

The guard that actually works is one line: **the returned `ticker` must equal the
requested ticker, and the returned `company_name` must be the company you meant.**
Where either fails, resolve by full company name in `mode: "company"`, confirm the
`ticker_id`, and say so in the report. A packet built on the wrong `ticker_id` is
internally consistent and passes all six validator passes, which makes this the
second invisible failure in the skill after the restatement pointer.

---

## 7. Coverage limits of the map, stated plainly

The map was tested against 261 issuers spanning 53 distinct
`industry` / `insync_subindustry` pairs across 20 industries, pulled live. It is not
the whole taxonomy. Three things a run should expect:

**The taxonomy is global and consistent.** Issuers in the `Europe` and `Asia (NJA)`
regions carry the same industry and subindustry labels as US issuers, so the map holds
outside the US without a regional branch.

**A subindustry the map has never seen resolves to `default`, by design.** That is the
correct behaviour, not a gap to be alarmed by, but say in the report which label you
saw and that it fell through. Two examples found in testing sit outside every other
industry in this file: `Renewable Energy / Renewable Energy` is its own industry rather
than a subindustry of `Utilities`, and it stays on `default` because the issuers there
are manufacturers rather than rate-regulated. A renewable independent power producer
under that label would be better served by `utilities`; resolve it by what the issuer
publishes and say which you chose.

**A subindustry label can contain punctuation.** `Forestry;Paper and Wood Products`
carries a semicolon. Do not split a label on a delimiter to test it; test the whole
string with a substring match.

### One `software_growth` misfire the growth gate cannot see

The gate admits an issuer that is loss-making OR growing revenue above 20%. Neither
condition establishes that the issuer is a recurring-revenue business. An issuer
labelled `Software` and loss-making resolves to `software_growth` and is handed an ARR
and net-revenue-retention spine, even when its business is in fact a crypto-asset
treasury. **Before committing `software_growth`, confirm at
the Step 2 probe that the issuer returns rows for a recurring-revenue code.** Where it
does not, fall back to `software_mature` if the issuer is a software business at all,
and to `default` if it is not, and record the fallback in `warnings`. The same check
protects `internet_platform` from a label that carries no volume measure.

### B9. The probe SATISFIES the gate with a named code, and the spine follows that code

**The gate is a two-outcome test in this file and a three-outcome test in practice**, and
the missing third outcome is the common one. `software_growth`'s spine calls for **ARR and
net revenue retention**. A confirmed recurring-revenue issuer that publishes NEITHER passes
the gate and is then handed two rows it cannot fill: an operator who confirms on remaining
performance obligation and commits `software_growth` inherits an ARR row of dashes.
Measured on a hardware issuer with software revenue: RPO, billings and deferred revenue all return rows, ARR and NRR return
nothing.

**Record WHICH code satisfied the gate, and take the spine from that code.** The ladder,
first match wins:

| the code that satisfied the probe | spine substitution | key |
|---|---|---|
| ARR (and NRR where published) | ARR, net revenue retention % | `software_growth` |
| RPO or cRPO | **remaining performance obligation**, with a Y/Y sub-row, in place of ARR | `software_growth` |
| billings or current billings | **billings**, with a Y/Y sub-row, in place of ARR | `software_growth` |
| deferred revenue alone | deferred revenue moves to the DRIVERS, and the spine takes no recurring line | `software_mature` |
| none of the above | not a recurring-revenue business | `software_mature`, or `default` if not software |

Stock-based compensation and the FCF margin stay in the spine in every row of the ladder:
they are what the key is really for on a pre-profit issuer, and neither depends on the
recurring measure. **Net revenue retention is dropped, not substituted**, where the issuer
does not publish it: there is no second-best NRR, and a retention row filled with a
different metric is worse than no retention row. Name the satisfying code and the
substitution in `warnings`, and say in `notes.model` which recurring measure the spine
carries, because a reader who expects ARR needs to be told what stands in its place.

The same ladder shape applies to `internet_platform`: the published volume measure that
satisfied the probe (gross bookings, GMV, trips, paid members) is the one the spine
carries, and where none does, the key falls back to `software_mature` or `default`.

### B12. The row 1 profit tile, where the sector has no EBIT

Row 1 of the tile strip is locked at six positions and two of them are **consensus EBIT**
and **EBIT Y/Y**. For four sector keys those two measures do not exist. Measured on a large
bank: `IS_P_110` and
`IS_P_106` return nothing, EBIT and EBITDA both leave the model spine, and **two of the six
headline positions had no defined content** on a packet whose whole purpose is that a PM
reads the same six positions in every packet.

Both available readings of the existing rules are bad. Printing `not reported` twice spends
a third of row 1 on absence, on every bank, insurer and REIT packet the skill will ever
build. Substituting by judgement puts an unnamed measure in a locked position, which is the
one thing the locked strip exists to prevent.

**So the substitution is scripted, and the tile label names the measure.** This is the
leverage tile's rule applied to the profit pair, and the distinction from
`packet-sections.md`'s "do not substitute a sector-native headline" is exact: that rule
forbids an ad-hoc swap and forbids substituting where the issuer merely does not *publish* a
measure that exists for its sector. This ladder fires only where the measure is **undefined
for the sector**, and only down a written list.

| sector key | ladder, first rung the feed returns a consensus for |
|---|---|
| `banking`, `financials_nonbank` | pre-provision net revenue → net income (Adj) |
| `insurance` | underwriting result → net income (Adj) |
| `reit` | FFO → AFFO → net income (Adj) |
| every other key | no substitution: EBIT is defined and the tile carries it |

Three rules bind the substitution:

**The label carries the measure, both tiles, in the same words as the spine.** `Consensus
net income` and `Net income Y/Y`, not `Consensus EBIT` over a net income figure. A tile that
prints one measure under another measure's label is worse than a blank tile, because it is
not visibly wrong.

**Both tiles take the SAME rung.** A consensus level from one rung beside a Y/Y from
another is two measures in one pair, and the pair is read as one.

**Name the substitution in `warnings`, and state the rung.** A reader comparing a bank
packet with an industrial one is comparing net income against EBIT, and that is legitimate
only while it is disclosed.

Where no rung returns a consensus, the tiles read `not covered` and the absence goes in
`warnings`. That is rare enough to be worth a line in the report when it happens.
