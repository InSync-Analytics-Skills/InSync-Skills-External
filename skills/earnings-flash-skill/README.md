# Earnings Flash

*Version 11.3.0*

## What this skill does

Makes the fast first read on a company's quarter: a one-page buy-side flash you can have within
minutes of a release. The layout is fixed every time: Flash Verdict, Investment Read, What
Matters, Scorecard, Variant View, Guidance Snapshot, and Next Key Watch Items.

Every number is computed by bundled Python from live InSync MCP data, never written by the model.

## How to use it

- "earnings flash on [TICKER]"
- "quick take on the [company] print"
- "brief me on [TICKER]'s earnings"

You get `{TICKER}_DOC_EARNINGS_FLASH_{YYMMDD_HHMMSS}.docx`, and a `.pdf` twin when LibreOffice is
available.

## Requirements

| | |
|---|---|
| **InSync MCP** | Required. Every figure comes from it. |
| **Python 3.9+** | Required. Standard library only. |
| **Node.js** | Not needed. The Python renderer draws the page. InSync's internal copy can also draw it with Node and its bundled docx library; the page is the same. |
| **LibreOffice** | Optional, for the PDF twin and a measured one-page fit. |

## Works with other LLMs

The skill runs under any LLM that can call the InSync MCP and run Python: Claude, and other AI
assistants with code execution. `references/flash.py` prints every MCP call and tells the model
what to do next, so the model does not need to remember long rules. For a custom assistant or a
system prompt, paste `SKILL.md` as the instructions and attach the whole folder.

## Check an install

```
python3 references/self_check.py
```
