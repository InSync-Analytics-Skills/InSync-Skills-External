# Inflection Monitor

*Version 4.59.0*

## What this skill does

It shows what changed in a company's reported momentum this quarter. It reads everything the company discloses: financial, operating, segment, geographic and ratio lines. For each line it scores how the latest change in its growth rate compares with that line's own history, and it reports only the lines that inflected.

The output is one file: a branded PDF note of two to four pages (the page budget scales with how much inflected). The note separates what moved from what it means. It keeps the company's formal guidance apart from management commentary and from InSync's continuation levels, and it ends with what to watch next quarter.

Every figure comes from the InSync MCP and is computed by the bundled Python engine, so two runs of the same data produce the same note. The note does not compare results with consensus, valuation or share prices.

## How to use it

- "what inflected at [TICKER] this quarter?"
- "inflection monitor on [company]"
- "what's accelerating and what's slowing at [TICKER]?"
- "momentum shift at [company]"

You can name the quarter ("inflection [TICKER] 2Q26") or leave it out to get the latest reported one.

You get one file:

- `{TICKER}_Inflection_Monitor_{Q}.pdf`: the note, two to four pages.

Plus a short chat summary (every figure produced by the engine) with the note's QC status in one line: Hold for Data Validation, Client Ready with Disclosures, or Client Ready. The HTML, the computed JSON and the QC summary are still produced internally (the PDF is rendered from the HTML and every check reads the JSON) but are not delivered; they are available on request.

## Requirements

| | |
|---|---|
| **InSync MCP** | Required. Every figure comes from it; there is no local data file. |
| **Python 3.9+** | Required. Standard library only; no packages to install. |
| **A PDF engine** | Required. One of `weasyprint`, Chromium or `wkhtmltopdf`. The PDF is the only deliverable, so without one the run stops. |
| **`pdftotext`** (poppler-utils) | Optional. It lets the linter confirm that every figure on the page also appears in the PDF. Without it, the QC check records that the check did not run and does not award Client Ready. |

Before a first run, `python3 scripts/compute.py check` confirms the scripts are installed, that the engine version matches this README and SKILL.md, and reports what the environment can produce. If the scripts are missing (for example, when only `SKILL.md` was shared), or no PDF engine is available, it exits non-zero with an instruction to stop. The skill does not produce figures without its engine, and does not ship the HTML in place of the PDF.

Keep the folder structure intact: `SKILL.md`, `scripts/`, `references/` and `assets/` (and `tests/` in the internal edition).

## How a run is organised

Each run gets its own folder, `scratch/<TICKER>_<Q>_<runid>/`, holding the MCP responses exactly as returned. `python3 scripts/run_pipeline.py <run> plan` prints every request still to send, ready to paste, and `... file <saved-result>` places each saved result; `... prepare` ingests, verifies and builds them, reading the company's identity from the saved resolver response; `... publish` renders the PDF, writes the chat summary and runs every check once. The engine refuses, and names the cause, when a response belongs to another ticker, a page was truncated, a newer quarter has already been reported, or the ticker was matched only loosely.

## Checking the skill

`python3 scripts/compute.py check` confirms the installation before a first run. Every run's `publish` stage then checks the note it produces: every gate, an independent recomputation of every published figure, and the QC summary. A note that fails a gate is not delivered. The test suite and packaging scripts are not part of the client edition.
