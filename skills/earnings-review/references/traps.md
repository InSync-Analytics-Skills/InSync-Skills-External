# Traps: symptom → fix (read only when a symptom appears)

Every example here is generic or invented; no real company figures ship in this package.

| Symptom | Fix |
|---|---|
| Estimate values overwrite actuals | one tool per raw file |
| build_dataset: "has_more … truncated" | the response was paged: refetch with `offset` until `has_more` is false (never trust a 25/50/100-row default) |
| KPI consensus months old or from one or two contributors | expected for KPI series; the latest dated snapshot ≤ cut-off is used and the verdict carries † |
| Estimate row has no UPDATEDATE | it is ignored (cannot be shown to be pre-print) and listed in build warnings |
| "BASIS: consensus exists only on CODE_A … actual CODE_B" | InSync's pre-print consensus is on a different line item from the reported actual (e.g. adjusted EPS actual, GAAP-coded consensus). The line is not graded; do not force it. If the company's own reconciliation shows both are the same measure, add `basis_override` with the reason |
| "forward … not comparable: pre-print on A, post-print on B" | revisions would mix two definitions; the line is left out of the revisions note |
| "POLARITY UNDECLARED" | add `"better": "higher" \| "lower"` to metric_map (costs, ratios, charge-offs, capex as spend are lower), or add the code to `assets/polarity_registry.json` with its source |
| Prior-quarter adjusted EPS ≠ this release's comparative | the adjusted definition changed: restated history from the release via `overrides.json → values`; dash the rest |
| Guide tagged to the wrong line or period, null LTCODE | read GUIDE_TEXT, never the tag; place via `overrides.json → guides` |
| Guidance in the database differs from the release | the release wins: `overrides.json → guides` with the release text; every shown guide needs `guide_verified` or an override |
| Guide only in a presentation or on the call | verify against that document |
| Doc parse invents headings or figures | ask for verbatim text; quote only what is visible; store quotes in `data.json → quotes` |
| Value off by 10^3, wrong suffix (units labelled "B", a value labelled "M") | `suffix_override` in metric_map (applies to estimates too); a count ≥ 1 billion is refused until fixed |
| Release table figure "in thousands" or full dollars | `overrides.json` entry needs `"scale": "thousands"` / `"units"`; bare figures ≥ 1,000 in money lines are refused without it |
| Corrupt or contradictory value (a buyback of a few thousand dollars; a figure the text contradicts) | exclude; say so in Source |
| Outflow shows negative / estimate sign flipped | `sign: -1` in metric_map: actuals, estimates and guides are all shown as absolute spend |
| Series name misleads (total comp vs store comp; a "GAAP" code whose estimates are adjusted) | tie every KPI to the release |
| Restated and original rows for one period | the highest revision wins (`R3-` > `R2-` > `R-` > original); collisions are printed |
| Periods on an old basis (spin-off) | `overrides.json → exclude`; cash flow from the release when InSync does not tie |
| Old segment basis in the year-ago | `overrides.json → values` with the restated figure, or `stated_change` `mode: replace` |
| Guide stored as a midpoint or as words without a code | `overrides.json → guides` (FY rows created automatically) |
| Worded guide ("mid-to-high teens") | `guide_vs_frozen` `mode: "growth"`; check the parsed range in tokens.py output |
| Management cites a pro forma base | `stated_change` `mode: alongside` → `{id.alt_yoy}` |
| Segment consensus under a different code | add as fallback code in metric_map (the basis lock still picks one code for every side) |
| EV/EBITDA_Adj in `metrics_failed` | row omitted; banks use P/B |
| Consensus high/low implausible | do not quote it |
| Call date = release date | after-close reporter: take the call date from the calendar-events data |
| `get_actuals` rejects `R-2Q25` / `FY26` | request `2Q25` / `2026` (rejected immediately, no timeout) |
| `search_tickerid` hangs to the 180 s ceiling | a near-miss ticker or name; do not retry unchanged; pass the exact SYMBOL and official parent name, or ask the user |
| build_dataset exit 3: a later period has reported | the database may lag the company: ask the user which quarter to review; `--confirm-stale "<reason>"` only on their choice |
| build_dataset exit 4: identity | the search match is not exact or a payload row belongs to another issuer: re-resolve the ticker, refetch |
| Valuation a day older than estimates | build_dataset reads its DATE (or `--valuation-date`) |
| Units in trillions (banks) | parsed automatically; a row reaching $1T prints in T |
| Non-USD reporter or prices in pence | currency is read from the values; prices file `"currency": "GBPence"` is scaled to pounds; mixed currencies are refused |
