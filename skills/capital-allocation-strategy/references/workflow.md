# Detailed workflow & data contract

## LTCODE map (verified 2026-07-22)

Cash flow: OCF `CF_P_108`, FCF `CF_P_110`, Capex `CF_P_109` (reported negative), Buyback$ `CF_P_101`,
Buyback# `CF_P_102`, Avg Repurchase Px `CF_P_103`, Dividends `CF_P_104`, Payout% `CF_P_107`,
Acquisitions `CF_P_129`, Purchases of Investments `CF_P_130`, Net CFI `CF_P_111`, Net CFF `CF_P_112`,
Debt Issuance `CF_P_133`, Change in WC `CF_P_106`, Net Change in Cash `CF_P_135`.

Balance sheet: Cash `BS_P_141`/`BS_P_101`, Total Debt `BS_P_114`/`BS_P_108`, Net Debt `BS_P_115`/`BS_P_109`,
LT Debt `BS_P_107`, ST Debt `BS_P_106`, Total Equity `BS_P_113`, Total Assets `BS_P_103`, Intangibles `BS_P_127`.

Income: Revenue `IS_P_101`, EBIT `IS_P_109`, EBITDA(Adj) `IS_P_114`/`IS_P_113`, Net Income `IS_P_124`/`IS_P_123`,
EPS `IS_P_128`/`IS_P_127`, Diluted Shares `IS_P_125`, Basic Shares `IS_P_250`, Interest `IS_P_141`,
ETR `IS_P_121`, R&D `IS_P_135`, D&A `IS_P_111`, SBC `IS_P_151`.

## Computed formulas (compute_capalloc.py)

- FCF = OCF - |Capex| (or reported `CF_P_110`).
- Total capital deployed = |Capex| + Buybacks + Dividends + M&A + Debt reduction + Cash build.
  Debt reduction = max(0, TotalDebt_prior - TotalDebt_latest); Cash build = max(0, Cash_latest - Cash_prior).
- Strategy classification: reinvest% = (capex+M&A)/pool; shret% = (buyback+dividend)/pool. Rules -
  Debt Reduction >40% -> Balance Sheet Repair; Acquisitions >35% -> Acquisition-Led;
  reinvest >=60% -> Growth-Oriented; shret >=60% -> Dividend-Led (dividends >=65% of returns),
  Buyback-Led (<=35%) or Return-Oriented; reinvest >=50% and largest -> Reinvestment-Led
  (Growth-Leaning); shret >=50% and largest -> Return-Leaning; else Balanced.
- Buyback yield = latest buybacks / market cap. Avg repurchase price = sum(buyback$)/sum(buyback#).
  Share-count reduction = (shares_first - shares_last)/shares_first.
- Dividend: payout = dividends / GAAP net income `IS_P_123`; without it the feed's `CF_P_107`; without
  both, dividends / the net-income ladder. FCF coverage = FCF/dividends; DPS = dividends/diluted shares.
- ROIC = EBIT x (1 - ETR) / (Total Debt + Total Equity - Cash). ROE = Net Income / Total Equity.
- Leverage = Net Debt / EBITDA. Interest coverage = EBIT / |Interest|.
- TSR (annualized) = ((price_end + cumulative DPS) / price_start)^(1/years) - 1 (needs `tsr` block).
- Scorecard 0-10 per dimension -> weighted Overall using the sector `scorecard_weights` (weight 0 = excluded).
  Rating: the band of the Overall - Band 8.0-10.0, 6.5-7.9, 5.0-6.4, below 5.0.

## Files
- `scripts/ltcodes.py` - the single source of truth for the fetch list + code names.
- `scripts/fetch_plan.py` - offline planner (ltcodes + sector profile + periods).
- `scripts/compute_capalloc.py` - deterministic math + ratings + QC. `compute(raw)->dict`.
- `scripts/render_capalloc.py` - branded python-docx renderer. `render(merged, out)->path`.
- `scripts/run_pipeline.py` - orchestrator (compute -> sector weights -> eval -> render). ONLY entry point.
- `assets/capital_allocation_sectors.json` - sector adaptation registry.
- Company master: not bundled. Set `INSYNC_COMPANY_MASTER=/abs/path/to/company_master.csv`
  to supply one. Without it every ticker takes the generic path: resolve ticker_id,
  industry and latest_quarter with `search_tickerid`, then fetch peers explicitly.
