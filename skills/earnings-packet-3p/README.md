# Earnings Packet

*Version 7.12.4*
## What this skill does

A dense **three-page** buy-side pre-earnings packet (.docx + .pdf) for any public
company in any sector. Ten locked sections. InSync MCP is the only data source; bundled
scripts do all math and layout, so numbers cannot be fabricated.

Standalone, and read in a single pass.

## How to use it

Write `<V>` as the FULL three-part version from the SKILL.md frontmatter, so this build
names its files `... Earnings Packet v7.12.4.docx`. Mixed spellings across runs of one
version make a folder of packets impossible to sort.

```
python3 references/self_check.py
python3 references/check_contract.py
python3 references/render_packet.py        "<TICKER> <Q>Q<YY> Earnings Packet v<V>.docx"
python3 references/check_table_widths.py   "<TICKER> <Q>Q<YY> Earnings Packet v<V>.docx"
#   convert to PDF: LibreOffice headless, or Word COM on Windows
python3 references/validate_packet.py      "<TICKER> <Q>Q<YY> Earnings Packet v<V>.pdf"
python3 references/validate_rules_style.py "<TICKER> <Q>Q<YY> Earnings Packet v<V>.pdf"
python3 references/verify_delivered.py     "<TICKER> <Q>Q<YY> Earnings Packet v<V>.docx"
python3 references/self_check.py           "<TICKER> <Q>Q<YY> Earnings Packet v<V>.pdf"
```

**All seven are required.**
`check_table_widths.py` is the only gate that catches a table wider than its container or a
grid disagreeing with its own rows. `verify_delivered.py` is the only pass that opens the
document a reader receives and re-derives what its cells claim. And the SECOND
`self_check.py`, the one with the PDF, is what runs the masthead geometry check and the
determinism test; invoked without a PDF, as the first line does, it reports `skip` for
both.

Setup: `pip install pymupdf pdfplumber` (for the PDF checks; the build itself is standard-library
Python, no Node). The first
`self_check.py` probes for all three and names whichever is missing.

Regression (internal edition only): the fixture matrix builds every fixture, runs every
gate, and diffs against the expected results.

Run from a working directory holding `packet_computed.json`, `packet_narrative.json`
and `peers.json`.

- `SKILL.md`, the pipeline, the rules, the degradation matrix
- `references/sector-config.md`, spine, drivers and valuation lens by sector
- `references/packet-sections.md`, what is on each of the ten sections
- `references/house-format.md`, page geometry, colour, number and label rules
- `references/data-collection.md`, exact InSync calls, limits and failure modes
- `references/pipeline-contracts.md`, the three input file contracts

## Requirements

| | |
|---|---|
| **InSync MCP** | Required. The only data source; there is no bundled data file. |
| **Python 3.8+** | Required. Every script, the Word file included, is standard library only: no Node, no npm. |
| **Times New Roman** | Optional. Installed, the masthead and section headings set at their intended size. Without it the renderer accepts Liberation Serif as a metric-faithful stand-in. |

## Changelog

Release notes are kept in the internal edition only.
