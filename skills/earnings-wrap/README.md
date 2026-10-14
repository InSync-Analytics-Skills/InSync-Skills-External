# Earnings Wrap

*Version 7.13.1*

## What this skill does

Tells the story of a quarter that has already been reported — what happened, what beat or
missed, what changed, and what matters now. Delivered as a branded PDF of roughly three
pages: an Executive Summary plus nine numbered sections, 2 to 10.

Every figure comes from the InSync MCP. Pre-print consensus is probed for every graded
line, and the print is graded against consensus, guidance, and post-print estimate
revisions. Bundled Python computes every delta and draws every table, so two runs of the
same request produce an identical document.

## How to use it

- "earnings wrap on [TICKER]"
- "recap the [TICKER] print"
- "how did [TICKER] report?"
- "write up [TICKER]'s quarter"

You can name the quarter — "wrap [TICKER] 2Q26" — or leave it out for the most recent one.

You get:

- `{TICKER}_Earnings_Wrap_{Q}Q{YY}.pdf` — around three pages, branded

## Requirements

| | |
|---|---|
| **InSync MCP** | Required. There is no local data file of any kind; without the MCP the skill cannot run. |
| **Python 3.9+ with `weasyprint`** | Required. Install with `pip install weasyprint` |
| **A Calibri-metric font** | Required for correct pagination. Only Calibri or Carlito match; without one the note reflows to four pages. Debian/Ubuntu: `apt-get install fonts-crosextra-carlito` · Fedora: `dnf install google-carlito-fonts` |
| **Georgia or Gelasio** | Masthead only, and optional. Neither one installed does not move a page break, but the masthead's two alignment edges are solved from Georgia's own metrics, so a substituted face sets the header slightly off spec. Debian/Ubuntu: `apt-get install fonts-gelasio` |

Before a first run, `python references/preflight.py` checks the renderer, logo and fonts,
and prints the exact install command if the font is wrong — the most common problem by far.
