# Turning MCP rows into a chart spec

Do not retype figures from an MCP response into a spec. Save the response, write
a small mapping, and let `build_spec.py` do the reshaping. Hand-transcription is
the single most likely way a wrong number reaches a chart.

## Read this first: two checks before you build anything

Static Charts is Path B. The engine draws what it is handed and **cannot detect a
gap you invented** — it will faithfully mark `n/a`, raise a coverage finding, and
print "6 of 8 periods reported" for a quarter that exists and that your lookup
simply missed. Disclosure of a defect is not detection of a defect.

Skipping either of the following produces a wrong chart.

**1. Normalise period labels.** The MCP returns a restated quarter as `R-1Q25`.
Key a lookup on the raw string and it will not match `1Q25`:

```python
from insync_charts.preflight import normalise_rows, restated_periods

rows  = normalise_rows(mcp_rows)          # strips R-, keeps CY-
notes = restated_periods(mcp_rows)        # ['1Q25', '2Q25'] — say so in the footnote
values = [rows.get((tid, code, q)) for q in quarters]
```

A restated quarter is not a problem; silently losing it is. Put it in
`options.footnote` — the reader should know which figures were revised.

**2. Reconcile the quarters against the reported year, and refuse if they differ.**

```python
from insync_charts.preflight import reconcile
reconcile(values, reported_annual=54.1e9, label="revenue", periods=quarters)
```

`reconcile` **raises**. Four quarters summing to half the reported year is a 50%
discrepancy that one line of arithmetic catches before anything
is drawn. Pass the annual figure in explicitly — never look it up from the same
structure you are checking, or the guard can pass on exactly the data it exists to
police.

Also count your rows. A quarterly query can return fewer periods than requested;
`expect_rows(rows, tickers * metrics * quarters)` catches a short result.

The engine will reject a spec whose `periods` still contain `R-` labels, but that
only catches the visible half of the mistake. If the raw label was used as a
lookup key, the period list arrives clean with a `None` beside it and looks exactly
like missing data.

```bash
python build_spec.py --raw raw.json --map map.json --out spec.json
python render_chart.py --spec spec.json --out-dir charts/
```

`build_spec.py` prints a summary to stderr — chart type, number of x values,
number of series, count of non-null data points, and any warnings. Check the
data-point count against what you expected before rendering.

## The mapping file

```json
{
  "chart_type": "revenue_trend",
  "company": {"name": "Example Software Co", "ticker": "EXSW", "sector": "Software"},
  "source": "InSync MCP",
  "as_of": "2026-07-28",

  "rows_path": "data.rows",
  "period_field": "fiscal_period",
  "periods": ["1Q25","2Q25","3Q25","4Q25","1Q26","2Q26"],

  "series": [
    {"name": "Revenue", "value_field": "value", "where": {"metric": "SALES"},
     "kind": "bar", "unit": "usd_mn"},
    {"name": "YoY growth", "kind": "line", "axis": "right", "unit": "pct",
     "derive": {"op": "yoy", "of": "Revenue"}}
  ],
  "output": {"basename": "EXSW_revenue_trend"}
}
```

- `rows_path` — dotted path to the list of records. Omit it and the builder takes
  the longest list of objects it can find, which is usually right for a simple
  response and never worth relying on for a complex one.
- `period_field` / `category_field` — which field is the x axis. Give exactly one.
- `periods` / `categories` — pin the order explicitly. Omit them and the order of
  first appearance is used, which follows however the MCP sorted its rows.
- `value_field` — which field holds the number.
- `where` — filter rows for this series. Values may be a scalar or a list.
- `period_strip` — prefixes to strip from the period label, default `["R-"]`.
  InSync marks restated periods `R-3Q25`; stripping puts the restatement in the
  same bucket as the original, and first-match-wins keeps whichever the response
  listed first.
- Everything else is copied straight into the spec.

## Display strings are parsed, not rejected

`get_actuals` returns values as formatted strings — `"$82.8860B"`, `"18.3%"`,
`"(1,204)"`. The engine parses them: currency symbols, thousands separators,
percent signs and parenthesised negatives are stripped, and `K` / `M` / `B` / `T`
suffixes are expanded. `"$82.8860B"` becomes `82886000000.0`.

A value written with `B`, `M` or `K` is converted into the series unit, so
`"$82.8860B"` is 82,886 on `usd_mn` and 82,886,000,000 on `usd`, and the axis
abbreviates either to `$82.9B`. A plain number is read as already in the unit.

There is one worked example in the InSync row format in `references/fixtures/`:
`example_raw_rows.json` plus `example_map.json`, made-up figures in the shape
`get_actuals` returns (restated `R-` labels, `"$..B"` strings).

A period listed in `periods` with no matching row becomes `null`, and is drawn
as a gap labelled `n/a`. That is the correct outcome: the database did not
report it.

## Common shapes

**Long / tidy rows** — one record per period per metric. The example above.

**One series per metric code:**

```json
"series": [
  {"name": "Gross margin", "value_field": "value", "where": {"metric": "GM_PCT"},
   "kind": "line", "unit": "pct"},
  {"name": "Operating margin", "value_field": "value", "where": {"metric": "OPM_PCT"},
   "kind": "line", "unit": "pct"}
]
```

**Actual versus estimate on one chart:**

```json
"series": [
  {"name": "Reported", "value_field": "actual", "kind": "bar", "unit": "usd_ps"},
  {"name": "Consensus", "value_field": "estimate", "kind": "line", "unit": "usd_ps"},
  {"name": "Surprise", "kind": "bar", "axis": "right",
   "derive": {"op": "surprise_pct", "actual": "Reported", "estimate": "Consensus"}}
]
```

**Peer comparison** — use `category_field` instead of `period_field`:

```json
{
  "chart_type": "peer_margin_comparison",
  "category_field": "ticker",
  "categories": ["EXSW","PEERA","PEERB","PEERC","PEERD","PEERE"],
  "series": [{"name": "CY26E operating margin", "value_field": "opm",
              "unit": "pct"}],
  "options": {"highlight": "EXSW"}
}
```

**Heatmap** — pivot two fields into a grid:

```json
{
  "chart_type": "revision_heatmap",
  "category_field": "ticker",
  "matrix_from": {"row_field": "ticker", "col_field": "horizon",
                  "value_field": "chg_pct"},
  "units": {"left": "pct"},
  "options": {"highlight": "EXSW"}
}
```

## Marking estimates

Set `estimate_from` to the index of the first forecast period, counting from
zero in the `periods` list. With eight quarters where the last two are estimates,
that is `6`. The engine shades and hatches the forecast region — which is what a
reader notices, unlike an "E" suffix in the tick label.

## Fetching the data

This skill does not call the MCP. The calling agent does, using whatever it
already has — the InSync MCP tool or data the parent skill
fetched for its tables. Write the raw response to JSON and point `--raw` at it.

Charting the same figures the note's tables were built from is not incidental:
it is how a chart and its table stay consistent. Prefer reusing the parent
skill's fetched rows over issuing a fresh query for the chart.
