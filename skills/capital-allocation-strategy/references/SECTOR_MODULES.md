# Sector-module architecture (Base + Modules)

Capital allocation does not mean the same thing in every industry, so the report keeps ONE shared
structure (the spine) and swaps only the sector-specific *judgement layer* through pluggable modules.

## The spine (identical across sectors)
Executive Summary · Allocation Mix · Capital Deployment · Shareholder Returns · Reinvestment ·
Balance Sheet · Forward Indicators · Peer Comparison · **Outcome Metrics** · Monitoring ·
**Scorecard** · Appendix A. The spine never changes; sectors plug into the bold sections.

## The module contract  (`scripts/sector_modules.py`)
Each sector is a `SectorModule` with one method:

```python
def outcome(self, ctx) -> dict | None
```
- Returns `None`  → the tech/FCF default is used (the tech path is therefore byte-identical).
- Returns a dict → the engine adopts the sector lens. Keys:
  - `rows`      : list of `[metric, value, period/denominator, reference]` for the Outcome table (Section 9).
  - `verdict`, `verdict_basis`, `score` : the sector value-creation read.
  - `scorecard_overrides` : `{dimension: 0-10}` — re-scores the dimensions where tech bands do not transfer.
  - `evidence_overrides`  : `{dimension: str}` — matching "Supporting figures" text so the scorecard stays coherent.
  - `dashboard` : four `(value, label)` tiles for the page-1 executive dashboard (sector-native KPIs).
  - `reinvestment` : `{heading, note}` reframing Section 5 (REIT → acquisitions/development; bank → RWA/organic).
  - `methodology` : list of `(label, key, measured[], thresholds[])` for the Appendix A scoring table.
  - `notes` : one neutral sentence explaining the sector lens.

So a module now drives four surfaces, all sector-native: the **page-1 dashboard**, the **Reinvestment framing**,
the **Outcome table**, the **Scorecard scores + evidence**, and the **Appendix methodology** — while the report
spine (section order, layout, pagination) is shared and unchanged.

`ctx` is a dict of already-parsed series + formatters handed in by `compute()`, so a module never
parses raw rows itself and never invents a number. Dispatch is on `meta["sector_key"]`
(resolved from the sector registry in `run_pipeline` before `compute`). Unmapped sectors → Default (no-op).

Wiring points: `compute_capalloc.compute()` builds `ctx`, calls the module, and applies
`scorecard_overrides` / `evidence_overrides` additively; `render_capalloc` Section 9 renders
`value_creation["sector_outcome"]` when present. Nothing else in the pipeline changes.

## Implemented
- **Default (tech / FCF)** — ROIC vs hurdle, FCF/share, EPS, TSR, share-count, capital-returned/FCF. Unchanged.
- **REIT** — dashboard + outcome + methodology on AFFO/share growth, AFFO payout & coverage, dividends as %
  of cumulative AFFO, leverage, equity-funded-growth (dilution) watch; Reinvestment reframed to property
  acquisitions/development. Re-scores dividends / balance sheet / capital discipline / value creation. Validated
  on a REIT fixture (internal edition).
- **Banks** — dashboard + outcome + methodology on ROTCE vs cost of equity, tangible book value/share growth,
  CET1 ratio & headroom, total payout (buyback+div / net income); Reinvestment reframed to RWA/organic growth;
  capex not scored (weight 0). Re-scores balance sheet (CET1, not ND/EBITDA), dividends (payout of NI),
  capital discipline, value creation. Live KPI codes: ROTCE=RT_P_107, tangible BVPS=BS_P_256. Validated on
  a bank fixture (internal edition).
- **Insurance** — outcome lens on ROE, book value and debt-to-capital (see `scripts/sector_modules.py`).

Known minor item: the Section 6 Balance-sheet *row* still prints the generic ND/EBITDA figure (n/a for a bank)
under the sector leverage label; CET1 is shown authoritatively in the dashboard, outcome table and scorecard.
A later pass can route the sector leverage value into that row too.

## Roadmap — modules not yet implemented (Asset Managers, Energy)
Each is a new `SectorModule` subclass + a fixture; the spine and tech path are untouched.

| Sector | Outcome lens (Section 9 rows) | Re-scored dimensions | Extra KPIs to fetch |
|---|---|---|---|
| **Banks** ✅ | ROTCE vs cost of equity; TBV/share growth; CET1 ratio & headroom vs requirement; total payout (buyback+div / net income) | value_creation (ROTCE/TBV), balance_sheet (CET1, not ND/EBITDA), dividends (payout of NI), capital_discipline; capex weight 0 | ROTCE=RT_P_107, tangible BVPS=BS_P_256, CET1 |
| **Insurance** ✅ | Book-value/share growth; combined ratio; reserve development; total capital returned | value_creation (BV growth), balance_sheet (solvency), capex→n/a | combined_ratio, reserve development, book value |
| **REIT** ✅ | AFFO payout & coverage; AFFO/share; acquisitions/dispositions; leverage & maturities | dividends, balance_sheet, capital_discipline, value_creation | AFFO / FFO |
| **Asset Managers** | AUM growth (organic vs market); fee-related earnings (FRE) margin; capital deployment; capital returns | value_creation (FRE/AUM), capex→light | AUM, FRE, net flows |
| **Energy** | Production growth; maintenance vs growth capex split; reserve replacement; FCF breakeven; capital returns | capex (maint vs growth), value_creation (reserve replacement + FCF) | production, reserve replacement, maintenance capex |

Notes carried over from the current engine that each module should honour:
- `leverage_metric` label already swaps by sector (e.g. "CET1 Ratio" for Banks) — the module must also
  swap the underlying **score** (CET1 headroom, not Net Debt/EBITDA), as the REIT module does for leverage.
- The `suppress` list in the sector registry is advisory today; real suppression is a `scorecard_weights: 0`.
- Peer benchmarks are still generic (FCF-style). Sector-native peer metrics are the next layer after the
  outcome/scorecard lens (fetch the sector KPIs for peers too, then extend `_peer_metrics`).

## Stress-test status
Deterministic fixtures (software, energy on the default lens, and a REIT on its sector lens) are validated
end-to-end in the internal edition; tech/energy outputs are unchanged by the module layer. A cross-sector
stress set is run per module on live InSync data, checking that the weighted Overall stays intuitive vs
sector norms.
