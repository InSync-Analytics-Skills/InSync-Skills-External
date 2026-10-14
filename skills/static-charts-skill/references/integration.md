# Calling the chart engine from another skill

One chart engine. A skill that needs a static chart image should call this one
instead of rendering its own. Duplicated chart code is how a library ends up with
six subtly different bar charts. Other skills can call this engine; this page
is how.

**Scope of that rule.** It covers static images — PNG, SVG, PDF. It does not
cover *native* chart objects: DrawingML in Word or PowerPoint, a real Excel
chart, or an interactive Chart.js chart in an HTML portal. Those stay editable
and vector and can respond to hover, so for some deliverables they are the better
answer and replacing them with a PNG would be a downgrade.
`earnings-preview-portal` builds a native Word chart and an interactive HTML
chart on purpose; leave it alone. Reach for this engine when what you actually
need is an image.

## Resolving the engine's path

Copy `references/caller-snippet.py` into the calling skill verbatim. It resolves
this engine from a sibling skill directory, honours an `INSYNC_CHARTS_SCRIPTS`
override, and degrades to "no exhibits" rather than failing the caller's whole
run when the engine is absent. Do not re-invent it per skill.

## The contract

The calling skill owns the data and the words. This skill owns the picture.

- Caller: fetches from the MCP, decides which exhibits the note needs, writes the
  narrative, places the images in the document.
- Engine: turns a spec into files, and returns their paths.

The engine never reads the caller's document, and the caller never reads the
engine's internals.

## Path 1 — shell out (preferred)

Zero coupling, works in every environment, easy to log.

```bash
python "$CHARTS/scripts/render_chart.py" \
    --spec "$WORK/charts.json" --out-dir "$WORK/charts" --quiet
```

Parse the manifest on stdout and use `charts[i].files.png`. Exit code is
non-zero if any chart failed; the failing chart's `error` says why.

Batch every exhibit for one report into a single spec file. It is one process
launch instead of eight, and the shared `company` / `source` / `report_date`
block guarantees the exhibits agree with each other and with the masthead.

## Path 2 — import

```python
import sys
sys.path.insert(0, f"{CHARTS_SKILL}/scripts")
from insync_charts import render_chart, render_many, validate

manifest = render_many(specs, out_dir="charts/")
png = manifest["charts"][0]["files"]["png"]
```

`render_many` never aborts a batch on one bad spec unless `strict=True`, so a
report still builds with the exhibits that worked. Check `manifest["failed"]`.

## Placing the output

**Word (python-docx).** Presets are sized for US Letter with 0.5in margins.

```python
doc.add_picture(png_path, width=Inches(6.5))   # preset "word"
doc.add_picture(png_path, width=Inches(3.2))   # preset "word_half", two up
```

Match the preset to the placement width. Do not render at `word` and scale down
in Word — the type ramp is chosen for the rendered width, and rescaling breaks it.

**PowerPoint (python-pptx).** Use `slide` or `slide_full`.

```python
slide.shapes.add_picture(png_path, Inches(0.6), Inches(1.3), width=Inches(9.0))
```

**HTML portals.** Use the SVG: it stays sharp at any zoom and prints cleanly.
For an interactive chart, import the shared palette instead of re-deriving it:

```bash
python render_chart.py --tokens > tokens.json
```

or, in Python, `from insync_charts import token_map`. Plotly users can register
the matching template with `plotly_backend.insync_template()`.

**PDF.** Request `"formats": ["pdf"]` for vector output, or use the PNG at
300 dpi — both print correctly.

**A whole chart pack.** For a deliverable set of exhibits, `build_pack.py` does
the render, the gallery and the manifest in one step:

```bash
python build_pack.py --spec charts.json --out-root packs/
python build_pack.py --verify packs/EXSW_3Q26_charts/manifest.json
```

```
EXSW_3Q26_charts/
  index.html      the gallery — what a human opens
  manifest.json   machine index, provenance, and the spec of every exhibit
  png/EXSW_revenue_trend.png
  svg/EXSW_revenue_trend.svg
```

Three properties are worth knowing, because callers depend on them:

* **The directory carries the ticker and the period**, taken from `period` (or
  the last entry in `periods`). Re-running after the next print writes a new
  pack rather than destroying the last one.
* **Filenames are stable.** `TICKER_charttype`, no ordinal prefix, so inserting
  or reordering an exhibit never renames another one — image links in a drafted
  document keep working. Exhibit order lives in the manifest. Two exhibits of
  the same type are disambiguated from their titles, applied to *both*, so the
  names do not depend on position.
* **The manifest embeds each spec**, so `--verify` re-renders the pack from the
  manifest alone and compares SHA-256 digests. It fails on a modified image, a
  deleted file, or a spec that no longer matches its own digest. That turns "this
  pack came from these numbers" into something checkable rather than asserted.

**A review page on its own.** If you only want the contact sheet,
`build_gallery.py` produces one from a manifest or a directory:

```bash
python render_chart.py --spec charts.json --out-dir charts/ > manifest.json
python build_gallery.py --manifest manifest.json --out charts/gallery.html \
    --title "EXSW 3Q26 earnings wrap — exhibits" --source "InSync MCP"
```

Feed it the manifest rather than the directory when you have one: the manifest
carries the chart type, title, ticker and warnings, so the cards are labelled
properly and any missing-data notes surface on the card. It is a review and
circulation artefact, not a deliverable — the charts themselves go in the
document.

## Which chart for which note

| Calling skill | Typical exhibits |
|---|---|
| Earnings Preview | `revenue_trend`, `eps_trend`, `surprise_history`, `revision_trend`, `valuation_history` |
| Earnings Wrap | `revenue_trend` with `estimate_from`, `surprise_history`, `margin_trend`, `segment_revenue_mix`, `revision_trend` (pre vs post) |
| Consensus / forecast accuracy | `revision_trend`, `estimate_revisions`, `revision_heatmap`, `forecast_accuracy` |
| Tearsheet | `revenue_trend` (`word_half`), `margin_trend` (`word_half`), `valuation_history`, `peer_comparison` |
| Capital Allocation | `capital_allocation_waterfall`, `shareholder_returns`, `capex_trend`, `peer_comparison` on ROIC |
| Peer comparison | `peer_valuation_comparison`, `peer_growth_comparison`, `peer_margin_comparison`, `relative_valuation` |
| Research Note / Packet | any of the above; batch them in one spec file |

## House rules for callers

1. Always pass `company`, `source` and `as_of`. A chart travels separately from
   its report and has to carry its own provenance.
2. Pass `null` for a figure the database did not return. Never substitute zero,
   never carry the prior period forward, never estimate it.
3. Mark forecast periods with `estimate_from` rather than putting "E" only in
   the period label. The shading is what a reader actually notices.
4. Use `focus` / `options.highlight` for the subject company instead of picking
   a colour.
5. Keep `basename` stable across runs of the same report so regenerating a note
   overwrites rather than accumulates.
6. If a chart you need does not exist, add a registry row here — do not draw it
   in the calling skill.
