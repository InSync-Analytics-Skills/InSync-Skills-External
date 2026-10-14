# Earnings Preview Portal

**Version 9.0.5**

Builds a buy-side **Earnings Preview** for a quarter a company has **not yet reported** —
what the setup looks like going into the print, and where the risk sits versus what the
Street is carrying.

> **Source of truth:** `SKILL.md` defines the behaviour and is authoritative. This README
> is orientation for humans — install, run, troubleshoot, maintain. Where the two ever
> disagree, `SKILL.md` wins and this file is the bug.

---

## Output

**Every run produces exactly one file:**

```
<TICKER> <Q>Q<YY> Preview Portal vN.docx
```

There is **no HTML portal**; no environment produces one.
`render_docx.py` builds the `.docx` with zero dependencies everywhere, claude.ai
included. Runs are **never overwritten**: the renderer scans the output folder and writes
`vN+1`, preserving the audit trail.

Default output folder is `outputs/` under the current working directory (created if
missing); on claude.ai it writes to the session outputs folder. Generated documents never
go into the skill's own `assets/` folder.

**Ten sections, locked order:** Valuation · Summary Financials · Earnings Setup · Earnings
Revisions · Earnings Expectations · Guidance Evolution · Call Commentary · Tone Shift ·
Sell-Side Focus · Beat History.

Peer Comps and Market Expectations are **retired** — they must not return. The
beat-history chart is a native Word chart object (clustered bars plus reaction line), so
it stays editable in Word and needs no browser or image capture.

---

## How to run it

Ask for it in plain language — a ticker is enough, a target quarter helps:

- "preview portal for [TICKER]"
- "earnings preview on [Company] for 2Q26"
- "pre-print note on [TICKER]"
- "how is [Company] set up into the print?"

Also triggers on: **EPP**, preview dashboard, earnings setup, pre-print note, ahead of the
print, curtain raiser, `/earnings-preview-portal`.

**Pre-earnings only.** Once the company has reported, use **earnings-wrap** for the
narrative recap, or **earnings-flash-skill** for a fast first read on the print.

---

## Requirements

| | |
|---|---|
| **InSync MCP** | Required. Every figure in the document comes from it. |
| **Python 3** | Required (3.9+). Every script in the run path is Python with **zero** external packages, so it runs in any AI sandbox. Node.js is not needed. |
| **Microsoft Word** | Not required to build. Only to read the output. |
| **LibreOffice / pandoc / html-docx** | Never used. Do not introduce them. |
| **Georgia** | Optional. Installed, the masthead sets at its intended size. Without it the renderer detects the substitute, sizes the company line so it still fits, and says so on stderr. |

The package alone is complete for Steps 0–6; the install smoke test and the regression
bench are internal edition only.

---

## Why the numbers can be trusted

The pipeline is deterministic ("Path B"). **The model never computes a number and never
places a number in a table.** It has four jobs: resolve the metric set from the MCP, fetch
raw data, write prose, run the scripts.

| Step | What happens | Who does it |
|---|---|---|
| 0 | Resolve the metric set — MCP is the KPI authority | `resolve_metricset.py` |
| 1 | Resolve ticker and calendar | model (MCP) |
| 2 | Fetch raw data → `input.json` | model (MCP) |
| 3 | Compute everything → `computed.json` | `compute_portal.py` |
| 4 | Write fenced prose → `narrative.json` | model |
| 5 | Render the document | `render_docx.py` |
| 6 | Verify against `computed.json` and the docx | model |

`computed.json` is the **only** legal source for any figure in the document. If a number
is not in `computed.json`, it may not appear in the prose. Metrics and KPIs come strictly
from the MCP-resolved metric set — never chosen by hand, never substituted, capped at five
financial metrics and five operating KPIs.

The run degrades rather than guesses: no guidance company drops the guide columns, a
missing adjusted metric falls back to GAAP with an asterisk, a mis-scaled consensus
vintage becomes a null plus a warning, an unestimated line auto-hides its consensus rows.
It stops for only two reasons — an ambiguous ticker, or a ticker with no financial spine
in the MCP.

Missing **vocabulary** is not missing **data**. A bank with no line item literally named
"EBITDA" is not a stop case; the resolver's bank ladder fills the profit slot (PPNR, PBT,
adjusted net income) and the run proceeds. An absent `top_kpi` tag is likewise fine — the
resolver falls back to segment and operating lines, and failing that produces a
financials-only document. The resolver has been swept across 12 sectors, including
insurance, REITs, utilities, E&P, telecom, airlines, semis, asset managers, homebuilders
and mining; none are refused.

---

## What's in the package

```
SKILL.md                              behaviour spec — authoritative
README.md                             this file
requirements.txt                      nothing to install (standard library only)
assets/
  insync_logo.png                     title-band logo
  valuation_rules.json                sector classification → valuation multiples
  polarity_registry.json              which direction is good for each line item
common/
  brand.json                          house identity and masthead constants
references/
  data-collection.md                  MCP fetch recipes, §1–§10 + transcripts
  pipeline-contracts.md               input.json / computed.json / narrative.json schemas
  *.md                                rule detail each script enforces (read on demand)
  mcp_guard_epp.py, mcp_guard.py      batch guard for MCP requests
  validate_metricset_input.py         Step 0 — gate in front of the resolver
  resolve_metricset.py                Step 0 — metric-set resolution + sector typing
  validate_input.py                   Step 2 — input pre-flight
  compute_portal.py                   Step 3 — all maths, all tables
  validate_narrative.py               Step 4b — narrative gate
  render_docx.py                      Step 5 — zero-dependency .docx writer
  check_invariants.py                 Step 5 — invariant check on computed.json
  check_masthead.py                   Step 6 — masthead geometry on a rendered PDF
  polarity.py, jscompat.py            shared helpers
```

---

## Troubleshooting

| Symptom | Where to look |
|---|---|
| "insufficient financials in InSync for `<ticker>`" | Genuine spine-less ticker — no estimated revenue, no profitability line of any kind, no EPS. Not a bug. |
| Completeness warnings from `compute_portal.py` | Resolve by **re-fetching**, never by ignoring. Every revisions row needs its four buckets; every past evolution column with an actual needs a consensus. |
| Guidance Evolution section missing entirely | Expected for annual-only guiders (many banks and retailers) — the card is built from target-quarter guides only. Known limit. The omission must be stated in the footer and the FY guide history covered in Tone Shift. |
| A KPI typed as money when it is a count (or vice versa) | Supply a `sample` value per the Step 0 recipe — the value, not more name vocabulary, is the discriminator. |
| Output file under 50KB, or won't unzip | Render failed. Re-run Step 5; do not attempt a converter. |
| Stray tickers in the document | Template remnant. Grep the docx text and re-run — do not hand-edit the output. |

---

## Maintaining this skill

- Bump the version in every place it is stated: `SKILL.md` frontmatter, `SKILL.md` body
  header and this README's header. Versions that drift apart leave the README describing
  behaviour the skill no longer has.
- An org's skill registry keeps whatever description was uploaded, so after a release check
  that the installed description matches this SKILL.md's.
- Keep operational detail out of this README. Anything a run depends on belongs in
  `SKILL.md` or `references/`, so there is only one copy to update.

---

## Scope and disclaimer

Output is a **draft for analyst review**. A human validates the document before any wider
circulation, and the rendered footer carries the not-investment-advice line. It is not
advice and not an externally publishable research report.
