# Company Tear Sheet

*Version 8.1.0*
`SKILL VERSION` banner in every file under `references/`.

## What this skill does

Builds a branded one-page tear sheet as a Word document with a matching PDF. The page
covers the market snapshot, key financials, operating KPIs, valuation against 5Y averages,
the bull and bear case, key debates, peer comps, supply chain, catalysts, and monitoring
signals.

Every figure comes from the InSync MCP and all arithmetic is done by bundled code, so the
numbers cannot be invented. The one-page limit is verified on the rendered PDF, not assumed.

## How to trigger it

Ask in your own words. Any of these will route here:

| | |
|---|---|
| **Core** | "tearsheet", "tear sheet", "tear sheet on [TICKER]" |
| **Deep dive** | "company deep dive", "deep dive on [company]" |
| **One-pager** | "one pager", "one-pager for [TICKER]", "one-page summary", "company one-pager" |
| **Investment framing** | "investment tear sheet", "stock tear sheet", "investment profile" |
| **Overview framing** | "company profile", "company fact sheet", "factsheet", "company overview", "stock overview", "company briefing", "company rundown" |
| **Conversational** | "brief me on [company]", "give me the full picture on [TICKER]" |
| **Explicit** | `/company-tearsheet-skill` |

## What it is not for

| Request | Use instead |
|---|---|
| A single reported quarter, beat/miss, variance | `earnings-flash-skill` (quick) or `earnings-wrap` (full) |
| A quick post-print take | `earnings-flash-skill` |
| The story of a reported quarter | `earnings-wrap` |
| Ahead of a print | `earnings-preview-portal` |
| A peer comparison workbook | a separate skill outside this plugin |

## What you get

- `{TICKER}_TEAR_SHEET_{YYMMDD}.docx`
- `{TICKER}_TEAR_SHEET_{YYMMDD}.pdf` — same stem, generated from the Word file

Both are deliverables and are presented together.

## Sector coverage

Runs on all 20 InSync sectors. Eight need a sector profile because their P&L does not fit
the default template — Banking, Financial Services, Insurance, Real Estate, Health Care,
Oil and Gas, Utilities, and Tech Hardware and Semiconductors; all eight ship with one.
Industrial Services ships a keywords-only profile. The rest fall back cleanly to the default
income-statement template.

## Requirements

| | |
|---|---|
| **InSync MCP** | Required. Every figure and the KPI selection come from it. |
| **Python 3.8+** | Required. Every script, the Word file included, is **stdlib only**: no Node, no npm, no pip. |
| **LibreOffice (`soffice`)** | Required for the PDF twin and the one-page check. Without it the `.docx` is still valid, but the page count is unverified. |
| **`pdfplumber`** | Optional, and needed ONLY by `check_masthead.py`, which measures the masthead on a rendered PDF. Without it that one gate reports `skip ... NOT verified` and exits 3; every other gate is unaffected. A skip is not a pass — install it (`pip install pdfplumber`) before a release build. |
| **Georgia** | Optional. Installed, the masthead sets at its intended size. Without it the renderer detects the substitute, sizes the company line so it still fits, and says so on stderr. |

## Files

| Path | Purpose |
|---|---|
| `SKILL.md` | The specification only — workflow, sector rules, schema, verification checklist |
| `references/rules.md` | The detail of what the scripts decide for you |
| `references/tearsheet-workflow.md` | Step-by-step MCP execution detail |
| `references/masthead-geometry.md` | Universal InSync masthead spec |
| `assets/sector_profiles.json` | Per-sector financial rows, KPI hints, valuation suppression |
| `assets/insync_logo.png` | Brand mark; replace this one file to rebrand |
| `common/brand.json` | Colour palette; built-in fallbacks if absent |
| `references/` | Compute, eval, generate, render, and the table-width and masthead checks |

## Before you ship a sheet

```bash
python references/compute_tearsheet.py --selftest    # prints SELF-TEST SKIPPED and exits 2
```

This copy ships without the regression test bench, so SKIPPED is the expected result here,
not a failure.

Then work through the verification checklist in `SKILL.md` §14. A run that completes
without errors is not evidence it ran as designed.

## Change control

Formatting, compute logic and the eval gate must not be altered without explicit approval
from InSync Analytics.
