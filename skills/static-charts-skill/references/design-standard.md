# InSync chart design standard

Target register: institutional equity-research house style, as used in
sell-side research and investment banking. A chart should look like it came out of a research system,
not out of a slide template.

Not this: bright colours, heavy gridlines, drop shadows, gradients, 3-D bars,
rounded corners, icons, background fills, consumer-dashboard styling.

This: white background, hairline grid, muted palette, restrained typography,
consistent labelling, the brand present but quiet.

## Colour

Aligned to the InSync Research Masthead Standard v1.0, so charts sit correctly
next to the report header.

| Token | Hex | Use |
|---|---|---|
| Ink | `#1A1A1A` | titles, primary text |
| Orange | `#E8850C` | the 2pt rule, the wordmark, the subject company, the current point |
| Orange dark | `#B25E08` | subject-company labels |
| Grey | `#666666` | subtitles, axis labels, metadata |
| Grey light | `#9A9A9A` | source line, `n/a` |
| Hairline | `#DDDDDD` | internal rules |
| Grid | `#E6E6E6` | gridlines |
| Axis | `#BFBFBF` | spines and ticks |
| Background | `#FFFFFF` | always |

Semantic, deliberately muted for print: positive `#2E6B4F`, negative `#9E3232`,
neutral `#8A8A8A`, waterfall total `#1F3A5F`.

Categorical series: navy, InSync orange, slate, dark orange, muted green,
charcoal, pale slate, taupe — in that order.

Stacked and mix charts use a separate single-hue ramp
(`#1F3A5F → #3E5F80 → #6E8CA8 → #9DB2C4 → #C6D1DB → #E4E9EE`) whose luminance
steps are strictly monotonic, spread evenly across however many segments exist.

Orange is reserved. It means "this is the company you are looking at", or it is
part of the brand furniture. It is never just the second series.

## Greyscale

Charts are printed and photocopied. Colour is therefore never the only channel
carrying meaning:

- lines vary by dash pattern and marker as well as hue;
- grouped bars vary by hatch beyond the second series;
- stacked segments sit on a monotonic luminance ramp;
- beat and miss differ by hatch as well as colour;
- estimate periods carry both a tinted band and a hatch.

`self_check.py` asserts the luminance separation of the stack ramp, so a future
palette change cannot silently break greyscale.

## Typography

Georgia (falling back to Caladea, Liberation Serif, Nimbus Roman, DejaVu Serif)
for titles. Calibri (falling back to Carlito, Liberation Sans, Nimbus Sans,
DejaVu Sans) for everything else. The fallbacks are metric-compatible, so a
chart rendered on a machine without the house fonts has the same geometry.

At the reference width of 6.5in: title 12.5pt, subtitle 8.8pt, axis labels
8.2pt, ticks and legend 8pt, data labels 7.4pt, source line 6.8pt. Other widths
scale by `(width / 6.5) ** 0.55`, clamped to 0.80–1.30, so a thumbnail stays
legible and a wide slide does not get billboard type.

## Layout

Every chart wears the same frame:

```
Company Name (TICKER) — Chart title              <- Georgia bold, ink
Subtitle · Sector · Period range                 <- sans, grey
────────────────────────────────────────────     <- 2pt orange rule
                  [ plot area ]
■ Series one   ■ Series two                      <- legend, only when needed
Source: … | data as of … | prepared …   [l/α] INSYNC ANALYTICS
```

Headings shrink to fit the content width before anything is allowed to clip: a
long company name plus a long chart title overflows a narrow preset otherwise,
and truncating a company's name is worse than setting it a point smaller.

The plot box is positioned *after* drawing, once it is known whether there is a
legend, a second y axis, or angled tick labels. Reserving that space up front is
guesswork; measuring it is not. This is why legends never sit on tick labels at
any preset.

A legend appears from two entries up, and only lists what was actually drawn — a
waterfall with no positive steps does not legend one.

## The trust mark

Per the InSync Research Masthead Standard, the l/α tile and the wordmark
"INSYNC ANALYTICS" are a single unit. They are never separated, never
recoloured, never stretched, and never the loudest thing on the chart. `INSYNC`
is orange, `ANALYTICS` is ink, and the tile ships as `assets/insync_logo.png`
(the dark-tile version, high contrast on white).

On a chart the lockup sits bottom-right on the provenance line, not top-right:
the chart's own title occupies the top-left and the orange rule spans the full
width, so the footer is where the standard's "footer pairing" rule applies.

The tile is sized from the *measured* height of the wordmark, not from a point
size, and is capped so it always stays inside the footer band. Sizing it off
points let it overrun the band on short presets and collide with the x tick
labels. If a preset is too narrow to fit the tile, the wordmark is kept and the
tile is dropped — the brand degrades gracefully rather than overlapping data.

Set `options.logo: false` for the wordmark alone, or `options.wordmark: false`
to suppress the mark entirely (for an exhibit that already sits under a branded
masthead, where repeating the mark is noise).

## The chart must state its own limits

A chart is read away from its manifest, so anything that changes how it should be
read belongs on the chart:

- **Partial coverage is printed.** "InSync consensus: 3 of 8 periods reported"
  appears in the footer. A line that starts mid-chart is a coverage boundary, and
  the primary series gets an on-plot "data from 2Q25" marker so it cannot be
  mistaken for the start of a trend.
- **A derived series is exempt.** A year-on-year line must start four quarters in
  — that is arithmetic, not a gap — so it is never marked as late-starting.
  Labelling it would assert a data problem that does not exist.
- **Outliers are reported, never removed.** An extreme value stays on the chart;
  the manifest flags it and the reference band is computed robustly so it is not
  dragged around by it.

## Key Observation

One computed line under every chart: latest value, change, spread, rank,
coverage. It states what the plotted numbers *are*.

It is deliberately not narrative. "EBIT margin 46.3% in 3Q26, +3.2pp vs 4Q24" is
a fact a reader can check against the chart; "margins are expanding strongly" is
an interpretation, and interpretation belongs to the calling skill, which has the
context to make it. Computing the line also keeps it deterministic — the same
spec yields the same sentence — and keeps this skill inside its charter of
drawing charts rather than drawing conclusions.

## Robust reference bands

Valuation history uses the **median and a P10–P90 band**, not mean ± 1 s.d.

A single 186x P/E pulls the mean far above every other observation and inflates
the standard deviation enough to swallow the outlier itself, so the "normal
range" the band implies is wrong precisely when the series contains something
extreme. Percentiles do not move. `robust_bands: false` restores mean ± s.d. for
a caller who needs the older convention.

Outlier detection uses the modified z-score on median/MAD for the same reason:
mean and standard deviation are not robust to the thing being detected.

## Data integrity

- `null` means not reported. It is drawn as a gap and labelled `n/a`, and the
  source line gains "n/a = not reported". It is never zero.
- Interpolation happens only when the caller asks for it, and is recorded in the
  manifest warnings.
- Derived series (growth, margin, surprise, rebase) are computed in `fmt.py` and
  flagged in the warnings, so an audit can see which numbers came from the
  database and which the engine calculated.
- Axes are not truncated silently: a bar chart's y axis includes zero unless the
  caller overrides it, because a truncated bar chart misleads.

## Determinism

Same spec in, byte-identical files out — across runs and sessions on one machine.
Across machines the files match only when the same fonts are installed (Georgia and
Calibri, else Caladea, Carlito or Liberation), because glyphs come from the machine.
Achieved by pinning `SOURCE_DATE_EPOCH` and `PYTHONHASHSEED`, fixing the SVG
hash salt, rendering SVG text as paths, stripping creation timestamps from the
file metadata, and never letting a chart depend on ambient state. `self_check.py`
renders the whole gallery twice and compares digests.

## Adding a chart type

1. Can an existing primitive draw it with different defaults? Then it is one row
   in `registry.py` and nothing else.
2. If it genuinely needs new visual grammar, add a primitive — and add it to the
   fixture gallery in the same change, so `self_check.py` covers it.
3. Never add a colour, font size or margin that only one chart uses.
