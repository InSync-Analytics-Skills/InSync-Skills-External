# Static Charts

*Version 1.9.1*

## What this skill does

The shared chart engine for the InSync skills library. One engine, one look — every
static financial chart in every InSync report comes from here, so a revenue chart in an
earnings wrap and a revenue chart in a tear sheet are identical in style, and fixing a bug
once fixes it everywhere.

33 chart types across 8 drawing primitives, rendered to PNG, SVG or PDF. It handles the
arithmetic — year-on-year, margins, surprise percentages — the layout, the colour, and
every unit format from $4.73 per pound to $230bn to 25 basis points.

It deliberately does not write narrative, draw conclusions, rate a stock or recommend
anything. If a request needs analysis, that belongs in the calling skill; only the picture
is made here. The one sentence written under each chart is a computed statement of fact
("Revenue $20.5B in 2Q26; +18.0% YoY"), never an interpretation. The caller knows the
context, the engine knows the house style, and neither does the other's job.

## How to use it

A chart is described by a JSON **spec** — a chart type, a title, and the series to plot.
Pass one to the renderer and you get files back:

```
cd scripts
python render_chart.py --list                             # every chart type
python render_chart.py --spec spec.json --out-dir charts/ # render
python build_gallery.py --dir charts/ --out charts/gallery.html
python ../references/self_check.py                        # regression suite
```

`build_spec.py` reshapes raw InSync MCP rows into a spec, and `build_pack.py` produces a
reproducible chart pack — PDF plus PNG, SVG, a gallery and a manifest.

Other skills call it as a library rather than a CLI; `references/caller-snippet.py` is the
pattern to copy, and `references/integration.md` explains the contract. The chart types
and their required fields are in `references/chart-spec.md`.

## Requirements

**Python 3.9 or later** with `matplotlib`, which also draws the PDF packs. Plotly and
Kaleido are optional.
See `requirements.txt`.

There is no MCP dependency — this skill takes numbers it is given and draws them. The
calling skill is responsible for fetching the data.

Two shared library files govern the look and must not be edited to suit one caller:
`common/brand.json` holds the identity and masthead constants, and
`references/MASTHEAD-STANDARD.md` is the spec they implement. After any change, run
`references/self_check.py` (expect 0 failing) and
`references/check_masthead.py` against a rendered PDF.

Keep the folder structure intact when copying it elsewhere; the logo and reference files
resolve on relative paths.

---

Maintained by InSync Analytics.
