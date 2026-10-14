# InSync Research Masthead Standard v1.0

Universal report header for all InSync Analytics products. One geometry, reused everywhere.
Applies to: Earnings Wrap, Earnings Preview, Earnings Flash, Tear Sheet, Peer Comps,
News Impact, Morning Brief, Sector Monitor.

## Design principles

Institutional, not marketing. Company first, product second, brand as a trust mark.
White background, one thin orange rule, no gradients, no large dark blocks.
Identical geometry in PDF (docx renderers) and HTML distribution.

## Geometry (US Letter, 0.5" margins; HTML max-width 794px)

```
+--------------------------------------------------------------------------+
|  COMPANY NAME (TICKER)                                    [logo] INSYNC  |
|  Product Name  |  Period  |  Prepared date                    ANALYTICS  |
|                                                       Sector . As-of     |
|==========================================================================|  <- 2pt orange rule
```

Company-level products (Wrap, Preview, Flash, Tear Sheet, Peer Comps, News Impact):
line 1 is the company name. Portfolio products (Morning Brief, Sector Monitor):
line 1 is the edition title (e.g. "TMT Morning Brief"), same scale.

## Hierarchy and type scale

| Rank | Element | Size | Weight | Color |
|---|---|---|---|---|
| 1 | Company name | 22-24pt | Bold, serif (Georgia) | Ink #1A1A1A |
| 1b | (Ticker) after name | 12-13pt | Bold | Orange #E8850C |
| 2 | Product name | 10-11pt | Bold, CAPS, letter-spaced | Orange #E8850C |
| 3 | Period + prepared date | 10-11pt | Regular | Gray #666666 |
| 4 | Sector + as-of metadata | 8-9pt | Regular, right-aligned | Gray #666666 |
| 5 | Brand trust mark | logo 30-34px + 10pt wordmark | | top-right |

## Branding rules

The l/alpha logo and the wordmark "INSYNC ANALYTICS" always appear together, top-right,
as a single trust-mark unit. "INSYNC" in orange, "ANALYTICS" in ink. The brand is never
the largest element on the page. The logo ships as `insync_logo.png` (dark tile version,
high contrast on white); never stretch, recolor, or separate it from the wordmark.

## Color tokens

| Token | Hex | Use |
|---|---|---|
| Ink | #1A1A1A | company name, primary text |
| Orange | #E8850C | product name, ticker, rule, INSYNC wordmark |
| Gray | #666666 | period, dates, metadata |
| Hairline | #DDDDDD | optional secondary rules below the masthead |
| Background | #FFFFFF | always white |

## Separator rule

A single 2pt (docx: 24 eighths; HTML: 2px) solid Orange rule spans the full content width
directly under the masthead. No second heavy rule; use Hairline for internal dividers.

## Field slots (identical keys across all products)

| Slot | Example (Tear Sheet) | Example (Morning Brief) |
|---|---|---|
| title | Example Corp | TMT Morning Brief |
| ticker | EXMP | (empty) |
| product_name | TEAR SHEET | MORNING BRIEF |
| period | 3Q FY26 | July 20, 2026 |
| prepared_date | July 20, 2026 | 6:30 AM ET |
| sector | Software | TMT |
| asof | Data as of 2026-07-17 | Coverage: overnight notes |

## Dark variant (optional, non-default)

The standard masthead is LIGHT (white background). A dark variant exists for
presentation contexts; enable per report with payload slot `masthead_theme: "dark"`.
Same geometry, same slots. Dark tokens: band #17191C (matches the logo tile, so the
logo sits seamlessly), company name #FFFFFF, ticker/product/INSYNC wordmark #F59E3B
(brighter orange for contrast), metadata #B8BCC2, ANALYTICS wordmark white. The 2pt
orange rule below the band is unchanged. Never use dark for the default distribution.

## Implementations

- HTML and DOCX reference implementations are kept in the internal edition only; this
  skill's `render_docx.py` implements the same table geometry.
- Footer pairing: every product footer carries the wordmark, product name, date, and the
  data-provenance line ("Data as of ... | Source: InSync MCP").

## Adoption checklist for a skill

1. Ship `insync_logo.png` in the skill's `assets/`.
2. Render the masthead from the seven field slots above; never hardcode company text.
3. White background; single orange rule; no navy or black bands in the header.
4. Brand top-right only; company name is the largest text on the page.
5. Keep the provenance footer consistent with the masthead metadata.
