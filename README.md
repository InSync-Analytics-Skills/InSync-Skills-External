# InSync Analytics — Claude Skills Plugin

## What this is

Eleven research and earnings-analysis skills for InSync Analytics, packaged as a Claude
plugin. Each one turns a plain-English request into a finished, branded deliverable — a
Word document, a PDF, an Excel workbook, an HTML portal or a chart — built from live
InSync MCP data.

Every figure in every output is pulled from the InSync MCP at runtime and computed by
bundled Python code, never written by the model. Two runs of the same request produce the
same numbers.

| Skill | Ask for | You get |
|---|---|---|
| [company-tearsheet-skill](skills/company-tearsheet-skill) | a tear sheet / deep dive / one-pager | 1-page Word doc + PDF |
| [earnings-preview-portal](skills/earnings-preview-portal) | a preview *before* a company reports | Word doc |
| [earnings-packet-3p](skills/earnings-packet-3p) | a dense pre-earnings packet, read in one pass | 3-page Word doc + PDF |
| [earnings-flash-skill](skills/earnings-flash-skill) | a quick first read *minutes after* a print | 1-page Word doc + PDF |
| [earnings-wrap](skills/earnings-wrap) | the full story of a reported quarter | ~3-page branded PDF |
| [earnings-review](skills/earnings-review) | a graded review of a reported quarter | 4-5 page branded PDF |
| [forecast-reliability-suite](skills/forecast-reliability-suite) | how accurate consensus and guidance have been | HTML portal + PDF |
| [revision-analysis](skills/revision-analysis) | how consensus has moved since the last print | Excel workbook |
| [inflection-monitor](skills/inflection-monitor) | what changed this quarter, and what to watch | 2-4 page PDF |
| [capital-allocation-strategy](skills/capital-allocation-strategy) | where capital went, and whether it matches the stated strategy | Word doc |
| [static-charts-skill](skills/static-charts-skill) | a chart of a company's figures | PNG / SVG charts |

The earnings skills differ mainly by timing and depth: **before** the print use the preview
or the packet; **just after** use the flash for a quick read, the wrap for the full
narrative or the review for a graded scorecard. The tear sheet is for a whole-company
picture with no particular quarter in view, and capital-allocation-strategy for how a
company has deployed cash over time. The chart engine is also used by the other skills.

## How to use it

Install once:

```
/plugin marketplace add InSync-Analytics-Skills/InSync-Skills-External
/plugin install insync-external-skills
```

Then just ask for what you want — "tear sheet on NVDA", "recap the Microsoft print",
"revisions for AAPL", "earnings preview for AMZN". You never need to name the skill. Each skill
folder has its own README with example prompts and its exact requirements.

## Requirements

**An active InSync MCP connection is required by every skill.** It is where every number
comes from; without it no skill can produce a real deliverable.

**Python 3.9 or later** runs every skill (3.10 for earnings-review). No Node.js is needed. Each skill folder that needs
a package has a `requirements.txt`.

Required packages:

- **earnings-wrap** — `weasyprint` (draws the PDF)
- **inflection-monitor** — a PDF engine: `weasyprint`, Chromium or `wkhtmltopdf` (the PDF is the only deliverable)
- **forecast-reliability-suite** — `reportlab` for the PDF (`--nopdf` gives the HTML portal alone)
- **capital-allocation-strategy** — `python-docx`
- **static-charts-skill** — `matplotlib`
- **earnings-packet-3p** — `pymupdf` and `pdfplumber` for the PDF checks (the build itself needs nothing)
- **earnings-review** — `pymupdf`, plus Edge, Chrome or Chromium for the headless PDF print

Nothing to install: company-tearsheet-skill, earnings-flash-skill, earnings-preview-portal,
revision-analysis. Several skills can use `pdfplumber` for an optional masthead check on a
rendered PDF.

Optional:

- **LibreOffice** — only for the PDF twin of a Word output
- **Georgia**, or **Times New Roman** for earnings-packet-3p and forecast-reliability-suite.
  Installed, each masthead sets at its intended size. Without it the renderer detects the
  substitute, sizes the company line so it still fits, and says so. Install it everywhere or
  nowhere: a mixed estate renders the same company at different sizes.

Keep each skill's folder structure intact when copying it elsewhere; logos and reference
files resolve on relative paths.

## License

The skill files are licensed under the [Apache License 2.0](LICENSE). Data from the
InSync MCP is not covered by that licence; it is governed by your InSync subscription
agreement.

---

Maintained by InSync Analytics.
