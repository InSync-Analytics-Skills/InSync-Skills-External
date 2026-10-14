# Pipeline contracts (fields the model writes; scripts write the rest)

Workdir: `raw/` · `metric_map.json` · `data.json` · `overrides.json` · `review_config.json` · `prose.json` → `computed.json` → HTML/PDF.

## metric_map.json
`{"<id>": {"label", "fmt", "kind": "level"|"rate", "ltcode": [preferred, fallback…], "unit"}}`, e.g.
`"gm": {"label": "Gross Margin on Home Sales", "fmt": "pct1", "kind": "rate", "ltcode": ["SD_S_141", "SD_S_143"], "unit": "pct"}`.
unit: m ($M) · b · count · k ($k per unit) · pct · num. fmt: bn m int k knum pct1 pct2 usd2 usd3 num1 num2 days x.
Optional: `guide_ltcode` (must be the same line item as the actual) · `native_yoy_ltcode` · `sign: -1` (outflows: absolute spend on
every side) · `better: "higher" | "lower"` (REQUIRED for a graded line whose code is not declared in `assets/polarity_registry.json`, and for every
company-specific `_S_` code of an issuer the registry entry does not list in `ticker_ids`;
any other value is an error) · `inline_band` (% levels, bps rates; default 1.0 / 10) · `forward: true` + `forward_key` (revisions
lines; revenue and EPS are forward unless `forward: false`) · `fy_guide: false` · `exclude_periods [...]` · `suffix_override {"B": "k"}`
· `large_count_ok: true` (a real count ≥ 1 billion) · `guide_basis_ok` (reason, when the guide is deliberately on another line item).

## data.json — hand-filled fields only
`company {name, fiscal_year_end, insync_subindustry}` · `period.quarter_end` · `release {date, display, timing}` · `call {date, display}`
· `prepared` · `reaction {pre_date, post_date, day2_date}` (asof is automatic) · `facts {key: "verbatim string"}` ({f.key}) ·
`not_graded {id: reason}` · `quotes {key: {text, speaker, source}}` ({q.key}; quoted prose must match a stored quote) ·
`guide_verified {"<id>.<field>": {source, checked}}` (every guide shown) · `basis_override {id: reason}` · `polarity_override {id: reason}` ·
`guidance_extra_rows [{label, prior, consensus, actual, vs_guide, vs_cons, new, consensus_next}]` ·
per metric when needed: `actual_text`, `vs_guide_text`, `guide_*_text`. Script-written (do not edit): metrics values,
resolved_code, basis, better, consensus*, forward, consensus_probe, kpi_universe, valuation (+history), fy_guide, overrides_log,
provenance, build_warnings.

## overrides.json (verbatim text + `source`; parsed in code; disclosed in Source)
- `values [{metric, period, text, source, reason, scale?}]` — a period's figure ("$142 million", "14.5%", or a sentence containing it).
  A bare figure ≥ 1,000 in a money line needs `scale`: `units` | `thousands` | `millions` | `billions` (the table's unit line); a value
  more than 50x from the InSync value it replaces is refused unless `magnitude_ok: true`. A replaced period clears InSync's native Y/Y.
- `exclude [{metric, periods[], reason}]` — periods on another basis → dashes.
- `guides [{metric, field, text, source, tag?, display?, label?, fmt?, ytd_text?}]` — field guide_prior | guide_new |
  guide_fy_prior | guide_fy_new; ranges parsed, worded text kept; FY fields create the FY row; `display` shortens a cell.
- `stated_change [{metric, text, basis, mode, source}]` — "an increase of 250 basis points" / "$0.12" / "decreased 3%";
  `replace` re-bases the year-ago, `alongside` adds `{id.alt_yago|alt_yoy|alt_change|alt_label}`; rates need bps.

## review_config.json
`primary_kpi` (+ `primary_kpi_reason`) · `scorecard.bands [{label, rows[ids]}]` · `evolution.bands [{label, rows[{id, yoy:
true|"native"|false}]}]` · `guidance {rows, eps_row, revenue_row {id, implied?, implied_label?}, band_label, fy_band_label,
col_prior, col_prior_sub, col_new, col_new_sub, frozen_tag, post_tag}` · `guide_vs_frozen {metric, forward}` or a list, or
`{metric, mode: "growth", forward: "rev_fy0", base: "fy_prior"}` for worded guides · `implied [{id, a, b, scale, fmt}]` ·
`derived [{id, expr, fmt}]` over `vp(id, '3Q26')` (by label, preferred), `cur`, `prior_year(label)`, `prior_period(label)`, `v(id, j)`,
`b(id, j)`, `pct`, `bp`, `mid`, `min`, `max`, `fy(id, 'yy')`, `M[id]['consensus'…]`,
`F['rev_next_q']['pre']` (never hand-placed consensus fields); fmt pcn | pcn0 | bps | a display fmt · `footer`.

## Tokens (`python scripts/tokens.py <workdir> [prefix]` lists them with values)
`<id>.` actual prior yago yoy seq (=qoq; Q/Q or H/H, label in {period.seq_label}) surprise verdict guide_pos consensus consensus_n consensus_low consensus_high guide_prior
guide_new guide_new_mid vs_guide consensus_next new_mid_vs_cons_next v.<period> yoy.<period> alt_* · `reaction.` pre_price
post_price chg index_chg day2 since peer.<SYM> · `val.` mcap ev pe_ntm ev_ebitda_ntm hist.<pe|ev_ebitda|pb>.<ltm|avg1y|avg3y|vs3y>
· `fwd.<key>.` pre post chg pre_n post_n label · `guide.` mid_vs_frozen high_vs_frozen mid_vs_post frozen (growth mode:
implied_growth implied_growth_post guide_range pos pos_post) · `fy.`/`fy2.` prior new change direction consensus
new_mid_vs_cons ytd · `d.<id>` · `i.<id>` · `f.<key>` · `co.name` `period` `period.next` `period.fy` `consensus.frozen_date`.
`q.<key>` (a stored quote, printed in quotation marks) · `period.seq_label`.
`{token|c}` colours by sign. Verdicts BEAT / MISS / IN LINE / Not comparable, † when ≤2 contributors. Relative changes divide by |base|;
a change across zero prints `n.m.`.

## prose.json
`the_print [str]` · `summary [{lead, text}]` · `scorecard_comments {id}` · `scorecard_note` · `what_changed [{change, facts,
mgmt, view}]` · `guidance_read` · `revisions_tail` · `tone {prior_label, this_label, rows [{dim, prior, this}], note}` ·
`monitor [{lead, text}]` · `source`.
