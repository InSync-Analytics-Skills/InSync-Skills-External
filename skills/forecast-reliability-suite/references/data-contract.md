# input.json data contract

One JSON object. All numbers come from the InSync MCP pull for the requested ticker.
See `example_input.json` for a complete, working instance (a made-up company; every figure is invented).

## Top level
| field | type | notes |
|---|---|---|
| `ticker` | str | e.g. "EXMP" |
| `company` | str | full name from search_tickerid |
| `fiscal_quarters` | [str] | the 8 closed quarters, oldest→newest, e.g. "2Q25" |
| `next_quarter` | str | the upcoming, not-yet-reported quarter |
| `as_of` | str | data pull date (YYYY-MM-DD) |
| `metrics` | [Metric] | headline + top KPIs (see below) |
| `guidance` | [Guide] | quantitative guides for ANY metric (revenue, segment, growth, or EPS) |
| `prices` | {q: {prior, report}} | closes for the 1-day reaction |
| `revision_trail` | [Rev] | <=4 snapshots of next_quarter consensus, each with a `label` |
| `next_consensus` | obj | current consensus for next_quarter |
| `guide_next` | obj | EPS guide for next_quarter |
| `hyperscaler` | obj | REQUIRED. `{"is": true/false/"unsure", "reason": str, "source": "override"/"model"}` (SKILL.md Step 2a). When `is` is true, `metrics[]` must carry `fcf`, `debt`, `cash` |
| `data_flags` | [str] | OPTIONAL. Your own run notes: fallbacks, resolved store defects, dropped guides, unit notes from `build_revision_trail.py`. Copied into `_metrics.json.data_flags` as "model note: ...", after the computed flags; never printed on the page |

## Metric
```
{
  "key": "eps",                     // short id, unique
  "label": "EPS (adj)",             // display name (use the MCP LINEITEM name for KPIs)
  "ltcode": "IS_P_128",
  "pct_metric": false,              // true for % / comp metrics -> surprise in ppt; false -> surprise in %
  "headline": true,                 // true only for EPS and Total Revenue
  "actuals":   {"2Q25": 1.05, ...}, // one entry per fiscal quarter (from get_actuals)
  "consensus": {"2Q25": 1.02, ...},// pre-print consensus (from get_estimates VALUE)
  "est_range": {"2Q25": {"min":0.98,"max":1.07,"brokers":15}, ...}  // from get_estimates MIN/MAX/BROKERCOUNT
  "next_range": {"min":1.20,"max":1.32,"cons":1.26,"asof":"2026-09-19"},  // OPTIONAL. Next-quarter panel for the Dispersion card
  "gap_mode": "dollar",             // OPTIONAL. Surprise = actual − consensus in $ (FCF: near zero, flips sign)
  "neutral": true,                  // OPTIONAL. Direction only, no good/bad colour (capex)
}
```
Include Adjusted EPS (`IS_P_128`, headline), Total Revenue (`IS_P_101/102`, headline), the
`top_kpi` line items (up to 5) and the sector metrics the widening search adds (SKILL.md Step 2);
the verified fixtures carry 4 to 9 KPIs. Omit a quarter key if the MCP returns no value for it (script handles gaps).

## guidance (quarterly, ANY guided metric)
```
{"metric":"subs_rev","label":"Subscription & Services rev","fiscal_period":"3Q25",
 "low":600,"high":620,"actual":636,"unit":"$M","guide_date":"2024-08-15"}
```
- `ltcode` / `uic` = the MCP codes of the guided line (REQUIRED). The guide is shown only if the code equals a
  `metrics[]` row's code or that code + `-Y` (reported Y/Y growth). Constant-currency codes never match.
- `metric` = key of the matching row in `metrics[]` (used for the actual lookup, and for code-less outlook notes).
- `low`/`high` = the guided range; `actual` = the realized figure (dollar value OR growth %). If `actual`
  is omitted the renderer falls back to `metrics[metric].actuals[fiscal_period]` (value basis, e.g. EPS).
- `unit` (optional) controls display; `"%"`/`"pp"` = growth basis (deltas shown in pp).
Companies that don't guide quarterly → leave empty. Do NOT force EPS.

## guide.kind (optional, per guide) — outcome vs comparability
```
{"label":"Q Currency Impact on Revenue","fiscal_period":"3Q24","low":-4,"high":-4,
 "actual":-5,"unit":"%","guide_date":"2024-07-23","kind":"comparability"}
```
Set `"kind":"comparability"` when the guide estimates an effect management does NOT control — currency,
acquisitions/divestitures, an extra week, a tax-law change. Anything else is `outcome` (the default).
The distinction is load-bearing: outcome rows are scored on **beating the guided high end** and drive the
conservatism figures; comparability rows are scored on **landing inside the range** and are excluded from
those figures. Pooling them makes an FX-forecasting hit-rate read as a sandbagging score.

## qual_guidance (optional) — directional guidance, verbatim
```
"qual_guidance":[{"label":"Margin outlook","fiscal_period":"2027",
                  "text":"Co. expect continued margin expansion from favorable business mix"}]
```
Guides with no numbers. They fill the Guidance card's *Qualitative* view. A numeric guider that ALSO gives
colour carries both. Do NOT put a numeric guide here — if `GUIDE_TEXT` parses to a number or a range it
belongs in `guidance[]` / `fy_guidance[]` (see SKILL Step 4 on screening the `outlook` array).

## restated_quarters (optional)
```
"restated_quarters": ["3Q24","4Q24"]
```
Quarters whose actuals came from an `R-` (restated) label. Flagged in the output because recast actuals
vs original-basis consensus can overstate the surprise.

## metric.unit (optional, per metric) — v1.10.2
`"unit":"$B"` / `"GWh"` / `"stores"`. Used to LABEL the Estimate Dispersion range so a bare `173–178` is not
left for the reader to decode. `eps` and `rev` derive theirs from `currency` / `rev_unit` and need no field.
Display only — it never rescales a number.

## metric.basis (optional, per metric)
Set `"basis":"reported"` on a metric row when you fell back from Adjusted (`IS_P_102` empty). The label
gets an asterisk + footnote. Default `"adjusted"`.

## fy_guidance (full-year, dated series)
```
{"fiscal_period":"2026","metric":"eps","low":3.10,"high":3.20,"guide_date":"2025-05-15"}
```
A guide stated as a point and a tolerance (in `guidance[]` or `fy_guidance[]`) carries `mid` with
`tol_pct` (relative %) or `tol_abs` (same unit as `mid`) and no `low`/`high`; the renderer fills the
band before scoring (7.12.1). `mid` is written after applying the guide's `SCALE`, in the metric's
own unit (28,400 at `10^6` on a $B line is 28.4). On a % metric "± X%" is ± X ppt: `tol_abs`. A row carrying both `low`/`high` and
`mid`/tolerance is kept only if the two agree (rounding allowed); otherwise it is dropped and named
in `data_flags`. A `mid` with no usable tolerance is flagged and carries no range. Example:
```
{"fiscal_period":"3Q26","metric":"rev","mid":12.40,"tol_pct":2,"unit":"$B","guide_date":"2025-08-21","actual":12.71}
```
In `_metrics.json`, each `guidance.per_metric[]` row carries `median_beat_vs_high` (median of
actual minus the guided high end, as always) and, from 7.12.1, `median_beat`: the median beat in the
direction that counts (below the low end for a cost line), the figure the page shows.

Every dated FY guide (any metric — carry `metric` and a `guide_date`). The renderer groups by
`(fiscal_period, metric)`, sorts by `guide_date`, and counts
**raises / cuts / maintained** from consecutive-midpoint changes. Omit if unavailable (those counts show 0).

## prices
```
"2Q25": {"prior": 54.20, "report": 56.10}
```
`prior` = adjusted close the trading day before the report; `report` = adjusted close on the reaction day.

## metrics[].variant  (optional)

A short consensus VARIANT tag from the pull, e.g. `"PF"` for a pro-forma panel. The MCP returns it
alongside the consensus and asks for it to be shown next to the value; supply it here and the report
renders it after the metric name (`EPS (adj) [PF]`) and explains it once in Methodology. Omit it when
the panel is the ordinary one. Dropping a variant that WAS returned republishes a pro-forma panel as
if it were the standard basis.

## consensus_asof_verified  (emitted, not supplied)

`_metrics.json` carries `consensus_asof_verified`: `true` when `consensus_asof` was supplied and
checked against `report_dates`, `false` when it was absent entirely. When it is `false` the
Methodology card says so on the face of the report rather than asserting a cutoff nothing verified.

## revision_trail  (for next_quarter)
```
{"date":"20-Feb-26","eps":1.08,"rev":42.6}
```
**REQUIRED: carry KPI rows, do not ship an EPS+Revenue-only trail.** The Revision Momentum card always
renders EPS and Revenue; to add the KPI rows the reader expects, each snapshot MUST carry a `kpis` object
and the rows MUST be declared once at top level via `revision_kpis`. Pull the top ~3 KPIs' forward
`next_quarter` history in the SAME estimate pull (see SKILL Step 6) and attach each KPI's nearest
on-or-before value at every snapshot:
```
"revision_kpis":[{"key":"org_rev","label":"Organic Rev Growth","pct":true},
                 {"key":"na_ebit","label":"North America EBIT","pct":false,"unit":"B"},
                 {"key":"gen","label":"Total Generation","unit":"GWh"}],
"revision_trail":[{"date":"16-Feb-26","eps":0.91,"rev":10.85,"kpis":{"org_rev":4.80,"na_ebit":1.52}}, ...]
```
The card renders one row per metric (EPS, revenue, then each `revision_kpis` line) across the snapshot-date
columns, with a Net-move column (pp for `pct:true`, % for value metrics; non-currency units like `GWh`
render as `72,400 GWh`). The ONLY acceptable reason to omit `revision_kpis` is that none of the company's
KPIs have any forward consensus history at all (rare — verify with the pull before falling back). This is
Street **consensus** (analyst estimates), not company guidance.

## next_consensus / guide_next
```
"next_consensus": {"eps":1.12,"rev":44.1,"eps_min":1.07,"eps_max":1.18,"brokers":14,
                   "as_of":"2026-07-28","stale":false}
"guide_next":     {"eps_low":1.10,"eps_high":1.14}
```
`as_of` (optional) is shown on the next-print bar; set `stale:true` if the pulled "latest" panel predates
the most recent print (e.g. the estimate view was mid-rebuild) so the bar is labelled as possibly stale.
Note: `guide_next` is EPS-specific by design — the forward "bar" summarises the next print on adjusted EPS.
(Historical Guidance Credibility, by contrast, works for any guided metric via `guidance[]`.)

## resolution (REQUIRED) — what search_tickerid actually returned

```
"resolution": {"match_type":"exact_ticker", "score":100.0, "ticker_id":"1000"}
```

Copy these straight from the `search_tickerid` row you used (Step 1). `match_type` is the only
discriminator that distinguishes the right issuer from a near neighbour: a rename returns a different
symbol legitimately, and a high score does not mean an exact match. The renderer echoes the block into
`_metrics.json.resolution` as the run's identity audit trail. Nothing downstream can detect a
wrong-company pull — every figure in it is internally consistent — so this is the only record of which
issuer the numbers actually came from.

**This block is REQUIRED, and the gate is not cosmetic.** A `match_type` outside
`exact_ticker` / `exact_company` / `exact_isin` / `exact_cusip` refuses the render at exit 2 and
`--force` cannot override it. An ABSENT block also refuses at exit 2, but that one IS overridable
with `--force`, and a forced report then carries a visible "issuer identity not verified" line — the
absence is a migration path for inputs written before v7.7.0, not a licence to skip it.
`latest_fiscal_quarter` is recorded here too: an EMPTY value means InSync resolved the issuer but
holds no reported periods for it, and that refuses as well (see Step 1). This paragraph and the gate
in `render_consensus_portal.py` are asserted against each other in `self_check.py` (internal edition only).

## currency / rev_unit / report_dates (optional, cross-sector)
```
"currency": "$",            // headline EPS/revenue currency symbol (default "$")
"rev_unit": "B",            // headline revenue scale suffix: "B" or "M" (default "B")
"report_dates": {"3Q26":"2025-11-20", ...}  // earnings dates; guides issued on/after are excluded
```
Set `currency`/`rev_unit` off mega-cap-US names (a mid-cap in millions or a non-USD reporter otherwise
misprints). `fiscal_quarters` must be canonical `NQYY` labels in chronological order (validated).

## consensus_asof (required in practice)
```
"consensus_asof": {"2Q24":"2024-08-20","1Q25":"2025-05-15", ...}   // the UPDATEDATE each quarter's consensus came from
```
Per quarter, the **latest `UPDATEDATE` you kept** across that quarter's metrics. It must be **strictly before**
the matching `report_dates` entry.

| Condition | Renderer behaviour |
|---|---|
| stamp on/after `report_dates[q]` | `INVALID_INPUT`, **exit 2, not overridable by `--force`** — a post-print panel has marked to the actual, so every surprise reads near zero |
| stamp > 21 days before the print | warning in `data_flags` — probably a stale panel; confirm no later pre-print snapshot exists |
| stamp absent for a quarter while others carry one | warning in `data_flags` — that quarter's cutoff is unverified |
| not `YYYY-MM-DD` | critical (gated like any malformed field) |
| block absent entirely | silent; nothing is checked (legacy inputs still render) |

Echoed verbatim into `_metrics.json.consensus_asof` as the run's audit trail. This is the only field in the
contract that can be checked against the outside world, which is why it exists: a consensus pulled from the
wrong snapshot is internally consistent and passes every reconciliation check.

## Reserved keys & CONFIG
Metric keys `eps` (adjusted EPS) and `rev` (total revenue) are reserved — the headline fact and chart
resolve by them. All rating thresholds live in the `CONFIG` block at the top of
`references/render_consensus_portal.py` (beat tolerance, reliability magnitudes, dispersion bands,
Street-accuracy thresholds, outlier multiplier, coverage gate). Edit there to retune; the internal regression suite guards them.

## What the renderer computes (do NOT precompute)
Surprise (% for $ metrics, ppt for % metrics), beat/miss/in-line counts, average surprise, Street-bias
label, forecast-edge rating, median dispersion width, Street-vs-Management conservatism, guide-high-end
beat rate (per metric), the Street-accuracy pattern + median absolute error, the beat-every-analyst /
below-every-analyst counts from `est_range` MIN/MAX (collapsed ranges excluded), revision direction,
per-metric **confidence** tag, the 0–100 **Consensus Predictability Score** (historical), and the
templated Executive Summary. All appear in `_metrics.json` for screening. NOTE: the confidence tag, the bias
strength/direction, the Predictability Score and the Street-accuracy read are exported to the JSON but are NOT rendered in the
report (SKILL.md, Output contract).


## pred_weights (optional, client-configurable) — v1.6.0
```
"pred_weights": {"surprise":0.40,"direction":0.20,"dispersion":0.25,"revision":0.15}
```
Overrides the Consensus Predictability Score component weights for this run. Any subset of the four keys
(`surprise`, `direction`, `dispersion`, `revision`) is accepted; omitted keys keep their defaults
(0.35 / 0.20 / 0.25 / 0.20) and the set is re-normalised over whatever components have data. Values must be
numeric and ≥ 0; if the block is not a dict, has only zero/negative values, or names no known key, the
renderer falls back to defaults and records a QC warning. `_metrics.json.predictability` echoes the
`weights` actually used and a `weights_custom` boolean.

## Broker count — never displayed; one narrow internal use
Broker counts are never rendered. Estimate dispersion (range width, Disagreement band) is derived from
`min`/`max` only, and the "thin panel" flag means a collapsed range (MIN=MAX in ≥ half the ranged quarters).
`revision_trail[].brokers` and `next_consensus.brokers`, if supplied, are ignored.

`est_range[q].brokers` is OPTIONAL and read in exactly one place: the collapsed-range dispersion gate.
A collapsed band measures nothing about disagreement, so those rows render the consensus level with
`(no range published)` and `n/a` for Range width / Disagreement instead of a "Very low" verdict. Where the
field is present and the median across the window is ≥ 2, the zero width is treated as genuine unanimity
and the row keeps its scored verdict; where it is absent or < 2, the row is left unscored. Supplying it is
therefore optional and only ever *narrows* the gate — never widens it. It is read on already-collapsed rows
only, so a missing value on a normally-ranged line has no effect.

The gate is DISPLAY-ONLY. `_metrics.json.metrics[].dispersion`, `thin_panel`, `range_quarters` and the
Predictability Score are computed exactly as before and are unchanged by it, so screener consumers keyed on
those fields need no migration.

## fy_actuals (optional) — realized full-year outcome, v1.9.6
```
"fy_actuals": {"2026": {"eps": 3.15, "organic": 4.8, "capex": -1640}}
```
Keyed by `fiscal_period` (the YEAR), then by the SAME free metric key used in `fy_guidance`. Any metric,
any sector, any unit: the value is scored in that guide's own unit. When supplied for the year the FY grid
renders, the grid gains an **"Actual vs final guide"** column, scored against the LAST guide of that year
(the number management stood behind at year end): above its high end prints green, below its low end red.
Omit it and the column does not appear at all (never an empty column). Only meaningful for a COMPLETED
year, which is exactly the `FY_FALLBACK` case; if the grid resolves to a completed year and no
`fy_actuals` are supplied, the QC block says so. Malformed input degrades to "no actuals" and is flagged.

## Guidance display caps (v1.9.6) — nothing is ever silently dropped
`CONFIG` gains `qg_max_cols` (quarterly grid column cap, default **8**) and `fy_max_rows` (FY grid row cap,
default 8) alongside the existing `chg_max_cols` (FY print columns, default 4).
- Quarterly columns beyond the cap are NAMED in the note and remain in every statistic, so "Beat the guide"
  and the summary percentages stay full-sample.
- FY rows are ordered by information content (rows whose guide MOVED first, then rows carrying an actual).
  Only rows that never moved AND have no actual can collapse, and they are listed by name.

## Screener guidance counters (v1.9.6)
`_metrics.json.guidance` now carries `fy_scope` (the fiscal year the grid renders) with `fy_raises` /
`fy_cuts` / `fy_maintains` scoped to THAT year, so the screener row and the report agree. The wider
multi-year history moves to `fy_all_years: {years, raises, cuts, maintains}`.


## revision_kpis — labels & coverage (v1.10.0)
`revision_kpis[].key` MUST match a `metrics[].key`. The renderer resolves the display label from `metrics[]`,
so a line item can never be named two different things in two cards; `revision_kpis[].label` is a fallback
only. Carry a row for EVERY metric that has forward `next_quarter` consensus history — the old "~3 KPIs"
cap is gone. Any metric with no trail is NAMED in the card note rather than silently omitted.
