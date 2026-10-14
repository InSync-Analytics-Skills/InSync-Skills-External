# Consensus Revision Analysis

*Version 2.5.0*

## What this skill does

Builds a one-sheet Excel workbook showing how the InSync consensus estimate has moved since a
company's last earnings print, for the next unreported quarter and the fiscal year it rolls
into.

- **Left, the evidence table.** About 30–55 line items: the income statement, segment revenue,
  key metrics, five balance-sheet rows and cash flow.
  - Each block shows the consensus before the print, at day 10 and day 40 (when those days have
    passed and the numbers differ), and today.
  - Each row has the move since the print in amount and %, YoY and sequential growth, the
    Low/High range and the broker count.
- **Right, the buy-side panel.** Five sections, each a curated read of what the revision means:
  - Executive Summary
  - Estimate Revisions
  - Key Debate: Variant View
  - Earnings Expectations (Base / Extend / Challenge thresholds)
  - Key Monitoring Variables

Every number in the table is computed by the bundled Python scripts from live InSync MCP data.
The model fetches the data and writes the panel's judgment lines. The build checks every figure
in those lines against the computed data and stops on any figure it cannot find.

## How to use it

- "revision analysis for [TICKER]"
- "how have estimates moved since the print for [company]?"
- "consensus changes for [TICKER], in Excel"
- `/revision-analysis [TICKER]`

It covers companies that report quarterly. For a half-year reporter it says so and stops.

You get:

- `{TICKER}_revision_analysis_{quarter}.xlsx`, with one sheet, `Revision Summary`.
- A short summary in chat:
  - the headline moves;
  - anything the scripts flagged that does not print on the sheet, such as thin broker
    coverage or a unit caution.

## Requirements

- **InSync MCP:** required. All data comes from it, and there is no bundled data file.
- **Python 3.8 or later:** required. The skill uses the standard library only: no Node and
  no pip installs.
- **Excel, or any .xlsx viewer:** needed to open the workbook. The Δ columns are live
  formulas, with their values stored, so they also read correctly in viewers that don't
  recalculate.

## What is in the package

- `SKILL.md`: the run instructions the model follows.
- `references/narrative-guide.md`: how the panel's judgment lines are written.
- `references/script-rules.md`: every rule the scripts enforce, for when you want to know
  why the sheet shows something.
- `scripts/`:
  - `prepare_revisions.py`: data to `contract.json`.
  - `build_revision_xlsx.py`: `contract.json` to the workbook.
  - `ingest_raw.py`: saves MCP results verbatim.
  - Shared helpers.
- `references/sanity_check.py`: checks a built workbook against the skill's rules.
- `assets/insync_logo.png`: the masthead logo.
