# Capital Allocation Analysis

*Version 2.73.0*

## What this skill does

Produces a neutral, evidence-first capital allocation report (.docx) for any public
company — where capital was actually deployed, by how much, how the mix has shifted, and
whether that is consistent with what management said it would do. It describes; it does
not argue that a given allocation was right or wrong.

Covers the deployment trend quarter by quarter and prior-versus-current LTM, buybacks,
dividends, M&A and capex, leverage, ROIC, TSR, outcome metrics, a validated peer set,
guidance versus consensus, and a weighted 0–10 scorecard whose methodology is disclosed
in Appendix A.

It adapts by sector — the sector registry sets the priorities, weights and leverage metric
for each sector, and Banks, Insurance and REITs have their own outcome module (every other
sector uses the default FCF/ROIC lens), so a bank is not scored on a software company's
yardsticks.

Every figure is computed by bundled Python from live InSync MCP data. The model fetches
the rows and writes the descriptive prose; it never writes a number.

## How to use it

- "do a capital allocation analysis of [company]"
- "capital allocation review for [TICKER]"
- "how has [company]'s allocation shifted?"
- "is [company]'s capital return consistent with its stated strategy?"

A real peer set is required — the scorecard is relative, so the skill will not run
against a company it cannot compare.

You get:

- a PDF report and the matching editable `.docx`, sector-adapted, with Appendix A carrying the
  scorecard methodology (PDF conversion needs LibreOffice; without it you get the `.docx` and
  the run summary says so)

## Requirements

| | |
|---|---|
| **InSync MCP** | Required. Every figure and the peer set come from it; there is no bundled data file. |
| **Python 3 + `python-docx`** | Required, for the Word file. |
| **Georgia** | Optional. Installed, the masthead sets at its intended size. Without it the renderer detects the substitute, sizes the company line so it still fits, and says so on stderr. |
