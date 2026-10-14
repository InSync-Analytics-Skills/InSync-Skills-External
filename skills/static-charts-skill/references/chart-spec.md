# Chart spec contract

A spec is a JSON object. Unknown keys are ignored; invalid keys are rejected with
a message naming the offending field. Validate without drawing:

```bash
python render_chart.py --spec spec.json --validate-only
```

## Top level

| Key | Type | Required | Notes |
|---|---|---|---|
| `chart_type` | string | yes | see `--list`; aliases are resolved |
| `company` | object or string | no | `{"name","ticker","sector"}`; a bare string is treated as a ticker |
| `title` | string | no | overrides the auto title `Name (TICKER) — Chart title` |
| `subtitle` | string | no | `""` suppresses it; omit for the house default |
| `source` | string | no | the data provider. Without it the chart prints "Source not stated" and warns; always set it |
| `report_date` | ISO date | no | defaults to today |
| `as_of` | string | no | data vintage, printed in the source line |
| `periods` | string[] | for time series | x categories, left to right |
| `categories` | string[] | for peer / bridge charts | x or y categories |
| `series` | object[] | usually | see below |
| `matrix` | number[][] | heatmaps | rows × columns, `null` for gaps |
| `row_labels` / `col_labels` | string[] | heatmaps | lengths must match `matrix` |
| `units` | object | no | `{"left": "usd_mn", "right": "pct"}` |
| `axis_labels` | object | no | `{"left","right","x"}`; `""` suppresses |
| `options` | object | no | see options table |
| `output` | object | no | see output table |

## Series

| Key | Type | Notes |
|---|---|---|
| `name` | string | legend label; also the handle other series refer to in `derive` |
| `values` | (number\|null)[] | must match the x-axis length; short series are padded with `null` and a warning |
| `kind` | `bar` \| `line` \| `area` \| `scatter` \| `step` \| `marker` | default `bar` |
| `axis` | `left` \| `right` | default `left`; a right axis is created only if used |
| `unit` | string | see units; defaults to the axis unit |
| `color` | hex string | escape hatch — prefer `focus` |
| `focus` | bool | draw in brand orange: the subject company |
| `estimate_from` | int | index at which periods become forecasts; shades and hatches them |
| `labels` | string[] | explicit per-point labels, overriding the formatter |
| `show_labels` | bool | force data labels on or off for this series |
| `stack_group` | string | bars sharing a group stack; otherwise they sit side by side |
| `derive` | object | compute this series from others, in code — see below |
| `prior` | (number\|null)[] | values for the periods *before* the window, used only to feed `derive` so a YoY line is complete across the displayed periods |
| `alpha`, `linewidth`, `zorder` | number | fine control, rarely needed |
| `meta` | object | `{"role": "x"}` / `{"role": "y"}` for scatter charts |

### Derivations

The engine computes a value only when explicitly told to, and every derivation
is recorded in the manifest warnings so it is visible in the audit trail.

| `op` | Extra keys | Result |
|---|---|---|
| `yoy` | `of`, `periods_per_year` (default 4) | percent change versus n periods back |
| `qoq` | `of` | percent change versus the prior period |
| `margin` | `numerator`, `denominator` | numerator / denominator, in percent |
| `surprise_pct` | `actual`, `estimate` | (actual / \|estimate\| − 1), in percent |
| `rebase` | `base` (default 100) | index the series to its first non-null value |

`of`, `numerator`, `denominator`, `actual` and `estimate` all name other series
in the same spec by their `name`.

## Units

`usd`, `usd_th`, `usd_mn`, `usd_bn`, `usd_ps`, `num`, `num_mn`, `pct`,
`pct_pt`, `bps`, `x`, `days`, `index`, `boepd`, and for other reporting currencies
`<ccy>_mn`, `<ccy>_bn` and `<ccy>_ps` with `<ccy>` one of `eur`, `gbp`, `jpy`,
`chf`, `hkd`, `inr`. Any other unit is refused (it used to draw as a plain number).

Units control the axis label, the tick format and the data-label format. A plain
number is read as already in the unit: pass millions with `usd_mn`, not units with
`usd_mn`. A string carrying its own magnitude (`"$82.886B"`, `"1.2M"`) is converted
into the unit, so `"$82.886B"` on `usd_mn` is 82,886 and on `usd` is 82,886,000,000,
and a warning says so. A value that is present but not a number (`"abc"`,
infinity) is drawn as `n/a` and named in `warnings`.

Compact formatting honours the magnitude the unit declares, so `20543` with
`usd_mn` renders as `$20.5B`, not `$20.5K`. Once a tick carries its own magnitude
suffix the axis title drops the unit's magnitude word — an axis never reads
"US$ mn" above ticks that say "$30B".

On a scatter chart the two axes carry different units. Set `units.x` explicitly;
it takes precedence over the x series' own unit, which otherwise falls back to
the left-axis unit and prints a growth-rate axis in multiples.

### One abbreviation scale per axis

An axis is read as a set, so the magnitude suffix is chosen **once from the axis
peak** and applied to every tick. Deciding per tick produces
`0 · 500,000 · 1M · 1.5M · 2M` — each label defensible alone, the axis incoherent.
A 0–2.5m count therefore reads `0 · 0.5M · 1M · 1.5M · 2M · 2.5M`, while a 0–1,000
count reads `0 · 200 · 800 · 1,000` because its peak does not warrant abbreviating
at all. Zero never carries a suffix. Decimal places follow the same rule: one
count for the whole axis, not widened for a single small tick.

### A narrow-band series belongs on a line, not a bar

Bars start at zero because bar length encodes magnitude, and truncating the
baseline overstates change. That is correct, and it means a series whose variation
is small relative to its level — a realised price moving $4.12 to $4.73, occupancy
between 95% and 97% — looks flat as a bar and legible as a line, where the axis
zooms to the data. Choose `kind: "line"` for a level in a narrow band; keep bars
for quantities whose size is the point.

## Options

| Key | Default | Effect |
|---|---|---|
| `on_missing` | `gap` | `gap` (hole in the line, skipped bar, `n/a` mark) \| `zero` \| `interpolate` |
| `data_labels` | `auto` | `auto` labels the primary series when it will fit |
| `label_dp` | unit default | decimal places on data labels |
| `compact_labels` | `true` | 12.4K / 3.1B style abbreviation |
| `legend` | `auto` | `auto` shows a legend from two entries up; `false` never |
| `footnote` | — | appended to the source line, or wrapped onto its own line above it when too long. A footnote is disclosure and is never shed to make room; the provenance line drops `prepared <date>` first |
| `wordmark` | `true` | the l/α tile + "INSYNC ANALYTICS" lockup, bottom-right |
| `logo` | `true` | `false` keeps the wordmark but drops the tile |
| `highlight` | the ticker | which category to pick out in orange |
| `positive_color`, `negative_color` | house green / red | surprise and waterfall charts only: colours for up and down bars. Swap them for a lower-is-better metric (a cost, a ratio) so a fall is not drawn as bad news |
| `sort` | `desc` | `desc` \| `asc` \| `none`, peer bars |
| `reference_line` | `median` | `median` \| `mean` \| `false`, peer bars |
| `observation` | computed | the Key Observation line: a string overrides it, `false` suppresses it |
| `endpoint_labels` | `true` | label the last reported point of every line series |
| `mark_late_start` | `true` | annotate on-plot where a late-starting primary series begins |
| `stat_bands` | `true` | reference band on valuation history |
| `robust_bands` | `true` | median + percentile band; `false` reverts to mean ± 1 s.d. |
| `band_percentiles` | `[10, 90]` | the percentile band bounds when `robust_bands` is on |
| `mean_surprise_line` | `true` | average-surprise reference line on a surprise chart |
| `abs_beat_from` | — | `{"actual": "<series>", "estimate": "<series>"}`; labels each surprise bar with the absolute beat as well as the percent. Named series are inputs and are not plotted |
| `mark_outliers` | `true` | ring and label robust outliers on a valuation history |
| `expected_periods` | len(periods) | intended history length, for the completeness percentage |
| `mark_current` | `true` | label the latest point on valuation history |
| `hit_rate` | `true` | beat-rate annotation on surprise charts |
| `total_indices` | `[]` | which waterfall bars are subtotals |
| `first_is_total` / `last_is_total` | `true` | treat the ends of a bridge as totals |
| `normalise` | by chart type | 100%-stacked mix instead of absolute |
| `segment_labels` | n ≤ 8 | in-segment values on stacked bars |
| `hatch_from` | 6 | stack index at which hatching starts |
| `color_scheme` | by chart type | `diverging` \| `sequential`, heatmaps |
| `quadrants` | `true` | median cross-hairs on scatter charts |
| `estimate_label` | `Estimates` | caption over the forecast band |
| `backend` | `matplotlib` | `plotly` where supported, else falls back |
| `emit_html` | `false` | Plotly backend only: also write an interactive HTML file |

## Output

| Key | Default | Notes |
|---|---|---|
| `basename` | `TICKER_charttype` | file name without extension |
| `formats` | `["png","svg"]` | `png`, `svg`, `pdf` |
| `preset` | `word` | `word`, `word_wide`, `word_half`, `word_tall`, `slide`, `slide_half`, `slide_full`, `web`, `web_wide`, `thumb` |
| `width_in`, `height_in`, `dpi` | from preset | override individually |
| `format_subdirs` | `false` | `true` writes each format to `<out-dir>/<ext>/`; a `basename` containing `/` also works, and parent directories are created either way |

## Batch form

```json
{
  "company": {"name": "Example Software Co", "ticker": "EXSW", "sector": "Software"},
  "source": "InSync MCP",
  "report_date": "2026-07-30",
  "charts": [ {"chart_type": "revenue_trend", "...": "..."},
              {"chart_type": "eps_trend", "...": "..."} ]
}
```

Top-level keys are merged into every chart; per-chart keys win. One spec file
per report keeps provenance consistent across its exhibits.

## Manifest

`render_chart.py` prints:

```json
{
  "count": 2, "rendered": 2, "failed": 0, "out_dir": "/abs/path",
  "charts": [
    {"ok": true, "chart_type": "revenue_trend", "primitive": "combo_trend",
     "ticker": "EXSW", "title": "...",
     "files": {"png": "...", "svg": "..."},
     "observation": "Total revenue $15.9B in 3Q26; +28.5% vs 4Q24; …",
     "data_quality": "warn",
     "findings": [
       {"code": "coverage", "severity": "warn", "series": "InSync consensus",
        "message": "3 of 8 periods reported", "detail": {"reported": 3}}
     ],
     "plot_fraction": 0.5519,
     "warnings": ["series 'YoY growth' computed in-engine via derive.yoy"],
     "error": null}
  ]
}
```

Read `warnings` — padded series, missing points and in-engine derivations all
surface there.

## Data quality

Every chart is validated before it is drawn. `data_quality` is the worst severity
found, and `findings` carries the detail.

| Code | Severity | Meaning |
|---|---|---|
| `coverage` | warn | some periods have no reported value |
| `late_start` | warn | the series begins after the start of the x axis |
| `interior_gap` | warn | missing points sit inside the reported range |
| `trailing_gap` | warn | the series stops before the end of the x axis |
| `sparse` | error | under half the periods reported — too thin to read as a trend |
| `short_history` | warn | fewer than 4 reported points |
| `band_history_short` | warn | fewer than 8 points, so a statistical band is not meaningful |
| `outlier` | warn | robust (median/MAD) outliers present that will stretch the axis |
| `single_period` | error | one period cannot show a trend |
| `empty` | error | the series has no reported values at all |

The engine still renders a flawed series — refusing would push the caller into
drawing it somewhere worse — but never silently.

**What appears on the chart itself:** coverage, late starts, interior gaps,
trailing gaps, outliers, and every **error-severity** finding (`single_period`,
`short_history`, `sparse`, `empty`). Errors are disclosed on the image
deliberately: a chart is circulated as a loose PNG far more often than as part of
a pack, and the more serious the problem the more the image has to carry it. An
earlier version put warnings on the chart and left errors in the manifest, which
is the wrong way round.

Everything else stays in the manifest for the caller. **Treat
`data_quality == "error"` as a prompt to narrow the period range rather than as
something to publish.**

### Completeness

Each chart also carries a `validation` block: expected versus reported periods per
series, a completeness percentage, first and last reported period, whether the
series is truncated or derived, how much `prior` history was supplied, the peer
count, and the data vintage. `complete` is true only at 100% — a 7-of-8 series is
never quietly treated as full history.

`build_pack.py` renders this as a **Data Validation page** at the end of the PDF,
so a circulated pack states its own coverage rather than asking the reader to
trust it.

### Derived series and missing history

A `yoy` derivation needs four earlier periods. Without them the first four points
of the line are undefined and the line appears to start a third of the way across
the chart — which looks like missing data but is only a short fetch. Two things
follow:

* Supply `prior` on the source series and the derived line is complete across the
  whole window. The manifest records that pre-window history was used.
* Omit it and the engine says so explicitly in the warnings, naming the series and
  the number of periods needed. It never leaves the gap unexplained.

A derived series is never annotated as "late-starting" on the plot, because a
year-on-year line starting four quarters in is arithmetic, not a data gap.

### When a derived line cannot reach the latest period

A year-on-year value needs the quarter four periods back. If *that* quarter is
missing, the growth line stops before the right-hand edge of the chart even
though the bars continue — and a line that simply ends is a more dangerous kind
of gap than one with a visible break in it, because the last plotted value reads
as the current one.

Three things happen, and a caller should not have to ask for any of them:

* a `trailing_gap` finding records where the series stops and which periods are
  blank, and the footer says `nothing after 4Q25`;
* the endpoint label carries its period — `18.0% (4Q25)` — so the number cannot
  be read as this quarter's growth;
* the Key Observation omits the year-on-year clause entirely rather than quoting
  a stale one.

## What the Key Observation will and will not claim

The line is computed, so it is also constrained. Three rules a caller can rely on:

* **It anchors on the last reported period, never on a forecast.** A series with
  `estimate_from` has its tail excluded from the trend and named separately:
  "Adjusted EPS $1.38 in 2Q26; … ; consensus $1.51 by 2Q27" (made-up figures).
* **A year-on-year figure is quoted only from a `yoy` derivation whose last point
  lands on the anchor period.** Where the year-ago period is missing the growth
  line stops early and the observation says nothing about year-on-year rather
  than borrowing an older value. A right-hand axis carrying an unrelated ratio is
  not read as growth.
* **On a surprise chart the surprise is identified by what it is** — a
  `surprise_pct` derivation, else a focused percent series — not by its position
  in `series`. The bars, the on-chart average and the observation all use the
  same series, so the caption cannot contradict the chart.
