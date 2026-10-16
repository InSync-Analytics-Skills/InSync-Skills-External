---
name: static-charts
description: "Makes finance charts and graphs in the InSync house style: revenue, margin and EPS trends, segment mix, estimate revisions, results against expectations, valuation history, peer comparisons and cash waterfalls. Charts come out as image files ready for Word, PowerPoint or the web, singly or as a chart pack. The other InSync skills use it for their charts too."
---

# Static Charts — the InSync chart engine

*Version 1.9.1*

## What this skill does, and only this

Input: data plus a chart request. Output: professionally formatted chart files.

This skill draws charts. It does not write narrative, draw conclusions, rate a
stock, or recommend anything. If a request needs analysis, the analysis belongs
in the calling skill; only the picture is made here. If asked to "analyse and
chart", produce the chart and leave the interpretation to the caller.

All arithmetic, colour, layout, and label placement happen in Python. The model
supplies data and a chart type; it never positions an element or computes a
plotted value. That is what makes two runs of the same request produce
byte-identical files on the same machine with the same fonts installed
(the fonts are whatever the machine has, so another machine can differ).

## Quick start

```bash
cd scripts
python render_chart.py --list                              # every chart type
python render_chart.py --spec spec.json --out-dir charts/  # render
python build_gallery.py --dir charts/ --out charts/gallery.html   # review page
python ../references/self_check.py                          # regression suite
```

From Python:

```python
import sys; sys.path.insert(0, "scripts")
from insync_charts import render_chart
render_chart({"chart_type": "revenue_trend", ...}, out_dir="charts/")
```

A minimal spec:

```json
{
  "chart_type": "revenue_trend",
  "company": {"name": "Example Software Co", "ticker": "EXSW", "sector": "Software"},
  "source": "InSync MCP",
  "as_of": "2026-07-28",
  "periods": ["1Q25","2Q25","3Q25","4Q25","1Q26","2Q26","3Q26E","4Q26E"],
  "series": [
    {"name": "Revenue", "kind": "bar", "unit": "usd_mn", "estimate_from": 6,
     "values": [12585, 13102, 13466, 14231, 14873, 15392, 15900, 16450]},
    {"name": "YoY growth", "kind": "line", "axis": "right", "unit": "pct",
     "derive": {"op": "yoy", "of": "Revenue"}}
  ],
  "output": {"basename": "EXSW_revenue_trend", "formats": ["png","svg"], "preset": "word"}
}
```

The full spec contract is in `references/chart-spec.md`. Read it before writing
a spec by hand.

## Workflow

1. **Get the data first.** Fetch from the InSync MCP (or whatever the
   calling skill already holds). Do not open this skill's scripts until the
   numbers exist.
2. **Save the raw rows to JSON**, then reshape them with
   `scripts/build_spec.py --raw raw.json --map map.json --out spec.json`.
   Use this rather than retyping figures into a spec by hand — hand-transcription
   is how a wrong number reaches a chart. `references/data-binding.md` has the
   mapping recipes.
3. **Pre-flight the data before you build a spec. Two checks, both mandatory.**

   ```python
   from insync_charts.preflight import normalise_rows, restated_periods, reconcile

   rows = normalise_rows(mcp_rows)      # R-1Q25 -> 1Q25; CY- kept
   vals = [rows.get((tid, code, q)) for q in quarters]
   reconcile(vals, reported_annual=54.1e9, label="revenue", periods=quarters)
   ```

   The engine draws what it is handed and **cannot tell a gap you invented from
   data that is genuinely absent.** It will mark `n/a`, raise a coverage finding
   and print "6 of 8 periods reported" just as convincingly for a quarter that
   exists and that your lookup missed. Two things reliably cause that: an
   un-normalised `R-` (restated) period label, and a period missing from a
   quarterly query. Both produce wrong charts. `reconcile` raises,
   because a series that does not add up must not reach a chart. Note any restated
   periods in `options.footnote`.
4. **Render:** `python render_chart.py --spec spec.json --out-dir charts/`.
   A JSON manifest listing every output file is printed to stdout; exit code is
   non-zero if any chart failed.
5. **Hand the file paths to the caller.** For a batch, put every chart for a
   report in one spec file under `"charts": [...]`; shared keys at the top level
   (`company`, `source`, `report_date`) are merged into each chart.
6. **Or build a whole pack in one step.** `build_pack.py --spec charts.json
   --out-root packs/` renders the batch into `TICKER_PERIOD_charts/` with
   `png/`, `svg/`, an `index.html` gallery, and a `manifest.json` that embeds
   every chart's spec. `--verify` then re-renders the pack from that manifest
   alone and compares digests, so "these charts came from these numbers" is
   checkable rather than asserted. Filenames are stable — no ordinal prefix — so
   inserting an exhibit never renames another and image links in a drafted
   document keep working. See `references/integration.md`.
7. **Or just a review page.** `build_gallery.py` turns a manifest (or a
   directory of rendered charts) into one self-contained, branded HTML contact
   sheet with family filters, search and click-to-enlarge — useful for checking
   a report's exhibits before they go into the document, or for circulating a set
   of charts without attaching a folder of images. It embeds the images, so the
   single file works offline with no CDN and nothing to unpack.

If a spec is wrong the engine refuses and says why — it never guesses. Fix the
spec; do not work around the error by drawing something else. It refuses an
unknown `unit`, missing or empty `periods`, and an `output.basename` that leaves
the output folder. It draws, but names in `warnings`, a value that is not a number
(drawn as `n/a`), a missing `source` (printed as "Source not stated") and a title
too long for the size (shortened with "…"). Read `warnings` before using a chart.

## Supported charts

Run `python render_chart.py --list` for the authoritative list. V1 covers:

| Family | Chart types |
|---|---|
| Financial performance | `revenue_trend`, `ebitda_trend`, `eps_trend`, `margin_trend`, `metric_trend`, `kpi_trend`, `fcf_trend`, `segment_revenue_mix`, `segment_mix` |
| Consensus & revisions | `estimate_revisions`, `revision_trend`, `consensus_trend`, `surprise_history`, `forecast_accuracy`, `guidance_vs_consensus` |
| Valuation | `pe_history`, `ev_ebitda_history`, `ev_sales_history`, `valuation_history`, `relative_valuation` |
| Peer analysis | `peer_comparison`, `peer_ranking`, `peer_growth_comparison`, `peer_margin_comparison`, `peer_valuation_comparison` |
| Capital allocation | `capital_allocation_waterfall`, `capital_deployment_bridge`, `cash_flow_waterfall`, `shareholder_returns`, `capex_trend` |
| Screening & monitoring | `revision_heatmap`, `ranking_heatmap`, `relative_performance` |

Every type maps onto one of eight drawing primitives, which is why they all look
like members of one family. A genuinely new chart type is a row in
`scripts/insync_charts/registry.py`, not a new drawing routine.

Sector-native metric choices — NII/NIM/ROTCE for banks, production/FCF/capex for
energy, combined ratio/BVPS for insurance, comp sales/traffic/ticket for retail —
are in `references/sector-metrics.md`. The engine is unit-aware, not
sector-aware: pick the right metric, pass the right `unit`, and the formatting
follows.

## House style, in brief

White background, hairline grid, muted palette, Georgia titles, one 2pt orange
rule, and the l/α + "INSYNC ANALYTICS" trust mark bottom-right on the provenance
line — one unit, never separated or recoloured, never dominant, dropped rather
than allowed to overlap data. Charts are sized to drop
straight into Word (`word`, `word_half`, `word_wide`, `word_tall`), PowerPoint
(`slide`, `slide_half`, `slide_full`) or HTML (`web`, `web_wide`, `thumb`).

Colour is never the only channel carrying meaning: line styles, markers, hatches
and a monotonic-luminance ramp keep every chart readable when printed in
greyscale. `references/design-standard.md` states the rules and the reasons.

## Handling the data you are given

A rendered chart is a redistributable copy of licensed market data, and the file
outlives the conversation that produced it. Three consequences:

- **Treat every input as licensed and internal.** Charts built from InSync
  data carry that data. Say so when handing a pack to anyone: the pack's
  own PDF prints a provenance line, but a loose PNG does not.
- **Do not put personal data on a chart.** Names of individuals, client
  identifiers, account numbers and holdings attributable to a person do not
  belong in a title, subtitle, label or footnote. The engine will render whatever
  it is handed; it cannot tell a person's name from a segment name.
- **Check before circulating outside the firm.** A pack that is fine internally
  may not be redistributable. That decision belongs to the caller, not here.

## Rules

- **Never hand-roll a static chart image outside this engine.** If a skill needs
  a PNG or SVG chart, it comes from here. One engine, one look. If the chart you
  need does not exist, add it to the registry here rather than drawing it in the
  calling skill.
  There is one legitimate exception: a **native** chart object — DrawingML in
  Word or PowerPoint, a real Excel chart, or an interactive Chart.js chart in an
  HTML portal — is not a static image and is sometimes the better choice, because
  it stays editable, stays vector, and can respond to hover. `earnings-preview-portal`
  is correct to build a native Word chart rather than embed a PNG. Do not
  "migrate" such a chart to this engine; a static image would be a downgrade.
  The rule targets duplicated *image* rendering, not every drawn thing.
- **Never fabricate a data point.** The engine plots what it is given. `null`
  means not reported and is drawn as a gap labelled `n/a`, not as zero, and not
  interpolated unless the caller explicitly sets `"on_missing": "interpolate"`.
- **Never hardcode a colour, font or size in a spec** unless the house style has
  no answer. `options.highlight` and `series.focus` exist so the subject company
  can be picked out without inventing a palette.
- **Never edit the theme for one chart.** A change to `theme.py` changes every
  chart in the library — that is the point, and the reason it needs care.
- Always set `source` (without it the chart says "Source not stated"), and set
  `as_of` when the data has a vintage. Every chart
  carries its own provenance because charts get separated from their reports.

## Files

```
scripts/render_chart.py       CLI: render, validate, list types, dump tokens
scripts/build_spec.py         reshape raw MCP rows into a spec
scripts/build_pack.py         reproducible chart pack: PDF + PNG + SVG + gallery + manifest
scripts/build_gallery.py      single-file branded HTML contact sheet of a batch
references/self_check.py      regression suite — run after any change
references/check_masthead.py  masthead geometry, measured on a rendered PDF
scripts/insync_charts/
    theme.py                  palette, type, geometry, presets, determinism
    spec.py                   spec schema, validation, missing-data policy
    dataquality.py            coverage, gaps, sparsity, outliers -> findings
    observe.py                the computed Key Observation line
    fmt.py                    units, axis formatters, the only derivations
    chrome.py                 title, rule, legend, source line, layout, export
    primitives.py             the eight drawing primitives
    registry.py               chart type -> primitive + house defaults
    render.py                 public API
    plotly_backend.py         optional interactive backend, same tokens
references/chart-spec.md      the full spec contract
references/data-binding.md    turning MCP rows into specs
references/integration.md     how other skills call this one
references/design-standard.md the visual rules and why they exist
references/sector-metrics.md  what to chart, by sector
references/fixtures/          worked examples for every V1 chart
```

Requires Python 3.9+ and matplotlib. Plotly and Kaleido are optional; the engine
falls back to matplotlib whenever they are absent.
