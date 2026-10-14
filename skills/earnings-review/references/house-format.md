# House format (PDF)

Shared with the other InSync skills (earnings-wrap, company-tearsheet-skill, earnings-preview-portal). Constants come from
`common/brand.json`: `build_html.py` reads the colours, fonts, masthead sizes, the company-size ladder, the mark size and the logo
file from it (a built-in copy is used only when the file is missing, with a warning).

| Element | Rule |
|---|---|
| Page | US Letter, 0.5in side and top margins, 0.62in bottom (footer stamped by `render_pdf.py`) |
| Body | Calibri (Carlito/Arial fallback) 9.7pt, line-height 1.28, ink `1A1A1A` |
| Masthead | three cells in a `C9C9C9` box: logo 59×46px · "InSync Analytics" 16.5pt Georgia with orange `InSync` · "Company (TICKER) - nQyy" Georgia bold on brand.json's size ladder (14pt down to 10pt, measured; below the floor the line wraps and the brand line takes the two-line size; never truncated), ticker orange · right: product 18pt Georgia bold, "Prepared:" / "Reported:" 9pt with orange labels; 1.75pt orange rule under the block |
| Section heading | 12.5pt bold Calibri, 1.5pt orange rule beneath, 20px above, 7px below, never orphaned from the block that follows |
| Sub-label | "Share Price Reaction and Valuation": 9.5pt bold black |
| Callout boxes | THE PRINT and INITIAL THOUGHTS ON RESULTS: fill `F6F3EE`, 2.5pt orange left border, 9pt uppercase title, 9.3pt bullets with orange markers |
| Note bars | fill `F6F3EE`, 2.75pt left border: orange = revisions, green = guidance read, dark = data note; 9pt |
| Tables | header band `EDEDED` 7.4pt bold with 6.6pt grey sub-heads "(Actual)" "(Consensus)"; body 8.3pt; hairline row rules `EEEEEE`; no vertical rules; band rows `F0E9D8` 7.4pt bold; zebra `F5F0E8` over body rows only; indented sub-rows 7.8pt grey; metric names 600 weight |
| Colour | sign only: green `1E7A34` for a positive change or beat, red `B02A1F` for any negative number or miss; never good/bad, never cheap/expensive |
| Continuity | short tables (reaction, What Changed, tone) kept whole; long tables repeat the header on each page, never split a row, never leave a header or band row at a page foot |
| Footer | stamped grey 6.5pt: "InSync Analytics | TICKER nQyy Earnings Review | Source: … | InSync consensus as of YYYY-MM-DD, market YYYY-MM-DD" left, "Page X of Y" right; to fit, the source list is shortened first, never the dates; if needed two lines |
| Numbers | one display scale per row (counts ≥ 10M in M, money ≥ $1T in T) and one precision per row (per-share actual and consensus at the same decimals) |
| Source block | 6.2pt grey, hairline above, ends with the house disclosure from brand.json |

Renderer: headless Chromium `--print-to-pdf` ($BROWSER_PATH, then Edge, Chrome, Chromium on Windows, macOS and Linux; Edge occasionally exits silently and the next engine is tried) then PyMuPDF for the footer.
Chromium ignores CSS `@page` margin boxes, which is why the footer is stamped.
