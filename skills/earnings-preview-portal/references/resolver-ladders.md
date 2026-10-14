# Resolver ladders and row axes

Referenced from SKILL.md Step 0.3. `resolve_metricset.py` is deterministic: ordered rungs,
first hit wins, ltcode preferred with a name regex as fallback. Every ladder below fires ONLY
after the more general path misses, so a ticker it does not apply to resolves byte-identically.

## Profit and per-share slots

**Industrial (default).** `EBITDA (adj) -> EBIT (adj)` by name regex; per-share from
`/\bEPS\b/` (non-basic, adjusted-first) else `IS_P_128 / IS_P_127`.

**Financials ladder** — fires when NO EBITDA/EBIT/Gross Profit line exists, which is correct
for a bank (interest expense is a lender's cost of goods, not a financing line) rather than a
fetch gap. `PPNR (FTE) -> PPNR -> PBT (Ex-1x) -> PBT -> Net Income (Ex-1x)`; margin follows the
chosen line's `-M` variant, else the Efficiency Ratio. On a large bank the first two rungs are
unreachable from the two basic §2 queries, which is why §2 has a third batch.

> **THE `-M` RUNG IS UNREACHABLE WITHOUT §2 BATCH 7.** "margin follows the chosen
> line's `-M` variant, else the Efficiency Ratio" reads as a ladder the resolver walks. It is
> not: with `filter:"standard"` the margin twins are EXCLUDED by the server, which says so in
> the response: *"Top lexical match 'Margin - PPNR' (IS_P_209-M) … is excluded by filter
> ['standard']; retry without filter"*. So the first rung exists only if §2 **batch 7** (the
> UNFILTERED `margin PPNR, margin net income, margin PBT, margin FFO` query) was run. Skip it
> and the ladder silently starts at rung two, and the page shows an Efficiency Ratio where the
> frozen control shows `IS_P_210-M`. That is a FETCH outcome presented as a resolver choice.
> `references/validate_metricset_input.py` warns (W1) when a financial or REIT metric set
> carries a ladder rung and no `-M` row anywhere; a ticker that genuinely has no twin is normal,
> so declare `tickerinfo.margin_twin_probed` once you have run batch 7.

> **An insurer has no rung of its own here.** PPNR/PBT/Net-Income is a bank sequence a P&C
> insurer lands on by default, so an insurer can resolve to PBT, which bundles investment
> gains, rather than `IS_P_406 Underwriting Income` (the insurer-native profit line). The note
> names the ladder honestly so the operator can see the choice rather than believing a bank
> rule was designed for an insurer.

**REIT ladder** — fires on a real-estate classification only:

| slot | rungs |
|---|---|
| profit | `CF_P_114 AFFO -> CF_P_113 FFO -> Normalized FFO` (never a per-share variant) |
| per-share | `CF_P_203 AFFO/Share -> CF_P_202 FFO/Share`, ahead of the ordinary EPS pick |

FFO/AFFO is a REIT's earnings line; GAAP net income and GAAP EPS are depressed by real-estate
depreciation. EBITDAre already wins through the industrial `/^EBIT/` regex, so the ladder
REPLACES it only when a genuine FFO/AFFO line exists — a REIT reporting neither keeps what it
had. **Why it is needed:** the per-share pick tests `/\bEPS\b/` on the name or
`IS_P_128 / IS_P_127` on the code, and "AFFO/Share" matches neither, so **without the ladder no
arrangement of the documented inputs could put it in the slot** — filing `CF_P_203` under
`spine.eps` still fails both tests, and Key Financial Metrics would lead on GAAP Diluted EPS,
whose Y/Y is depreciation and property-gain noise, while the AFFO lines carry consensus and
are often the only lines a REIT guides.

## Row axes

Three independent properties decide what sub-rows a row earns. Each is emitted by the resolver
and each is ABSENT (not false) on a metricset frozen before it existed, so the older prefix
fallbacks still govern those files.

| axis | question | set false for |
|---|---|---|
| `flow` | is this a period FLOW or a point-in-time BALANCE? | balances: gross loans, deposits, backlog, RPO/cRPO, rate base, installed capacity, policies in force |
| `pctrev` | is a share of TOTAL REVENUE a meaningful ratio? | capital deployment and written-premium production (below) |
| `fmt` | `$M` / `$` per-unit / `%` / `#` count | typed from the probed `sample`'s shape first, name ladder second |

**`pctrev` — the deny list, and why it is narrow.** `flow` stops a balance (gross loans,
backlog) being printed as a share of revenue. Being a currency flow is not enough either: the
numerator also has to belong to the income statement for the period:

- **An insurer's** "Net Premiums Written" as a share of revenue: written premium is a PRODUCTION
  measure; revenue contains premiums EARNED plus investment income and fees, so the swing across
  the columns is written-premium seasonality printed as a mix shift.
- **A REIT's** "Acquisition" as a share of revenue: capital DEPLOYED, an investing outflow, divided
  by a quarterly rental revenue flow. Same class of nonsense as a balance, and the
  `RATIO_CAP`/period-skew guards cannot see it: those are magnitude heuristics, and a category
  error landing near 1x revenue is invisible to them.

An EXPENSE or COST line is deliberately still allowed — "Cost and Expense at 168% of revenue"
is a real and useful ratio for a pre-profit biotech, which is why `RATIO_CAP` was raised
to 250% rather than lowered. So the list denies exactly two families (capital deployment;
written/gross/new-business premium) and the only override is a name that says REVENUE outright.
A broader allow list breaks a case: a generic `\bsales\b` token lets "Asset Sales" through as
a revenue component.

**A NEGATIVE share is not a share.** `allNeg` suppresses a row negative in every column; a
MIXED row has its negative cells flagged NM per cell (a residual segment line that swings
negative would otherwise print "−0.3%" beside "+0.2%"). Applied after the cap,
period-skew and all-negative tests have read the true values, so none of the three is blinded.

## KPI ranking

`pctW` (a percent is demoted) → `guidedW` (a KPI the company GUIDES leads — management and the
Street focus on the line management commits to) → `bandCmp` (order of magnitude of `size`, only
BETWEEN rows sharing a `fmt`) → `kindW` (name) → `tagW` → `geoW` → exact `size` → ltcode.

**`size` must be scale-normalised.** On a trillions-denominated issuer, reading the mantissa
alone makes the SMALLER line rank higher (`250.0B` → 250.0 above `120.0T` → 120.0), and a tiny
residual line can then lead the block and drive THE SETUP's opening bullet and the
beat-history series. `sizeOf` re-expresses `size` using the scale suffix in `sample` whenever
the caller's number is that sample's mantissa; a suffix it does not recognise can only be
fixed in §2.

## Guided margin tiebreak

A guided margin wins the margin slot only when `guided_ltcodes` carries the literal `-M` code.
Stripping `-M` and testing the base would let guiding the LEVEL promote its margin twin: an
issuer that guides `IS_P_110` EBIT (Ex-1x) as a DOLLAR range and a segment margin as a PERCENT
would see `IS_P_110-M` take the slot from the only margin it actually guides. InSync returns a
guided margin under its own `-M` code, so requiring the literal code loses no real guide.
