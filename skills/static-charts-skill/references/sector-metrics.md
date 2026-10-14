# What to chart, by sector

The engine is unit-aware, not sector-aware. It formats whatever it is given.
Choosing the right metric is the caller's job; this table is the house default
so that two analysts charting the same bank produce the same exhibit.

For every sector the four core exhibits are the same shape — a top-line trend, a
profitability trend, a valuation history and a peer comparison. Only the metric
inside them changes.

## Technology (software, semis, internet, hardware)

| Exhibit | Chart type | Metric | Unit |
|---|---|---|---|
| Top line | `revenue_trend` | revenue + YoY | `usd_mn`, `pct` |
| Profitability | `margin_trend` | gross, operating, FCF margin | `pct` |
| Earnings | `eps_trend` | adjusted diluted EPS vs consensus | `usd_ps` |
| Valuation | `ev_sales_history` or `pe_history` | forward multiple | `x` |
| Mix | `segment_revenue_mix` | segment or product revenue | `usd_mn` |
| KPI | `kpi_trend` | ARR, NRR, bookings, RPO, backlog, wafer starts | `usd_mn`, `pct` |

## Banks

| Exhibit | Chart type | Metric | Unit |
|---|---|---|---|
| Top line | `metric_trend` | net interest income + YoY | `usd_mn`, `pct` |
| Spread | `margin_trend` | net interest margin | `pct` |
| Returns | `margin_trend` | ROTCE, ROE, ROA | `pct` |
| Credit | `metric_trend` | provisions, NCO ratio, NPL ratio | `usd_mn`, `pct` |
| Capital | `metric_trend` | CET1 ratio | `pct` |
| Valuation | `valuation_history` | P/TBV, P/E | `x` |
| Peers | `peer_comparison` | ROTCE or CET1 | `pct` |

EV/EBITDA is meaningless for a bank. Use P/TBV and P/E.

## Energy

| Exhibit | Chart type | Metric | Unit |
|---|---|---|---|
| Volume | `metric_trend` | production | `boepd` |
| Cash | `fcf_trend` | free cash flow + FCF yield | `usd_mn`, `pct` |
| Reinvestment | `capex_trend` | capex and capex / CFO | `usd_mn`, `pct` |
| Returns | `shareholder_returns` | buybacks and dividends | `usd_mn` |
| Allocation | `capital_allocation_waterfall` | sources and uses | `usd_mn` |
| Valuation | `ev_ebitda_history` | forward EV/EBITDA or EV/DACF | `x` |

## Insurance

| Exhibit | Chart type | Metric | Unit |
|---|---|---|---|
| Top line | `metric_trend` | gross and net written premiums | `usd_mn` |
| Underwriting | `margin_trend` | combined ratio, loss ratio, expense ratio | `pct` |
| Book value | `metric_trend` | book value per share, BVPS growth | `usd_ps`, `pct` |
| Returns | `margin_trend` | ROE | `pct` |
| Valuation | `valuation_history` | P/BV | `x` |

A combined ratio below 100 is good — say so in the subtitle; the chart cannot.

## Retail and consumer

| Exhibit | Chart type | Metric | Unit |
|---|---|---|---|
| Comps | `metric_trend` | comparable-store sales | `pct` |
| Drivers | `combo` via `metric_trend` | traffic and average ticket | `pct` |
| Channel | `segment_revenue_mix` | e-commerce vs store | `usd_mn` or `pct` |
| Profitability | `margin_trend` | gross and EBIT margin | `pct` |
| Footprint | `metric_trend` | store count, square footage | `num` |
| Valuation | `pe_history` or `ev_ebitda_history` | forward multiple | `x` |

## Healthcare

Pharma: product revenue by drug (`segment_revenue_mix`), R&D as a percentage of
sales (`margin_trend`), pipeline-driven `revenue_trend` with `estimate_from`.
Managed care: medical loss ratio and membership (`metric_trend`, `pct` / `num_mn`).
Medtech: organic growth and gross margin.

## Industrials

Organic growth, book-to-bill and backlog (`metric_trend`), incremental margin
(`margin_trend`), free cash flow conversion (`fcf_trend`), `ev_ebitda_history`.

## REITs

FFO and AFFO per share (`eps_trend`, `usd_ps`), same-store NOI growth
(`metric_trend`, `pct`), occupancy (`metric_trend`, `pct`), P/FFO
(`valuation_history`, `x`), dividend coverage (`shareholder_returns`).

## Financial exchanges, payments, asset managers

Payments: total payment volume (`metric_trend`, `usd_bn`), take rate (`pct`).
Asset managers: AUM and net flows (`metric_trend`, `usd_bn`), fee rate (`bps`).

## General rules

- Use adjusted / non-GAAP figures and say so in the subtitle.
- Keep the fiscal calendar the company reports on; calendarise only when the
  chart is a peer comparison, and label it `CY26E`.
- Eight quarters is the default history for a trend, twelve for a valuation
  band, five years for capital allocation.
- A multiple that is meaningless for a sector should be left out, not shown as
  `N/M`.
