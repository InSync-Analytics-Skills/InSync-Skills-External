# Forecast Reliability Suite

*Version 7.12.1*

## What this skill does

Measures how accurate Street consensus and management's own guidance have actually been
for a company over roughly the last eight reported quarters, and delivers it as an
interactive HTML portal with a matching PDF.

It answers five questions: can I trust consensus on this name; which metrics does the
Street consistently get wrong; does management guide conservatively or aggressively; which
KPIs offer the most forecasting edge; and what should I watch into the next release.

All maths and rendering are done by a bundled Python script, so the figures cannot be
fabricated.

## How to use it

- "/forecast-reliability-suite [TICKER]" (or the legacy "/consensus-suite [TICKER]")
- "how accurate has the Street been on [TICKER]?"
- "does [company] sandbag its guidance?"
- "can I trust consensus on [company]?"

You get three files:

- `{TICKER}_Forecast_Reliability_Suite.html` — the portal
- the same report as `.pdf`
- `{TICKER}_Forecast_Reliability_Suite_metrics.json` — the computed figures, for screening

Notes on reading it:

- The guidance tables show only metrics that are also in the Street tables.
- For hyperscalers (large cloud / AI data-center operators) the report adds Free Cash Flow,
  Total Debt and Cash rows.
- Surprise sizes are absolute, not comparable across sectors: a ±4% surprise means something
  different for a semiconductor name than for a staple, so compare within a name or a peer group.
- The one-day price reaction is reported as a range only; at eight quarters there isn't enough
  data to claim a correlation.

## Requirements

| | |
|---|---|
| **InSync MCP** | Required. Every figure comes from it. |
| **Python 3 + `reportlab`** | Required for the PDF. Pass `--nopdf` to skip it and get the HTML portal only. |
| **Times New Roman** | Optional. Installed, the masthead sets at its intended size. Without it the renderer accepts Liberation Serif as a metric-faithful stand-in, or sizes the company line so it still fits. |
