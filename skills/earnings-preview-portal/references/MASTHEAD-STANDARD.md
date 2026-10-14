# InSync Research Masthead Standard v2.0 - NORMATIVE

Read this before building a new skill or touching an existing masthead.

## The rule

Every InSync product opens with the same three-cell masthead:

```
[ logo ] [ InSync Analytics          ] [ Product Name        ]
         [ Company (TICKER) - Period ] [ Prepared: / dates   ]
```

Institutional, not marketing. **Company first, product second, brand as a trust mark.**

## The four constants - do not retune them per skill

Read from `common/brand.json` → `masthead`:

| Element | Value | Rule |
|---|---|---|
| "InSync Analytics" | **16.5pt** (33 half-points) | FIXED. Never derived from the company line, never shrunk by a long name. |
| Logo mark | **59 × 46 px** | FIXED. Spans the two-line block: top edge meets the brand line, bottom edge meets the company line. |
| Company + ticker | **14pt → 10pt ladder** | SHRINK, never truncate. Step to the first rung that fits. |
| Alignment | **± 0.5pt** | On the rendered PDF, not in source. |

Colour: `InSync` in orange `E8850C`, `Analytics` in ink. Ticker in orange bold.
Period renders short - `2Q26`, never `2Q FY2026`.

## Why this document exists

Constants copied into each skill drift apart: brand-line size, long-name
behaviour, a logo that shrinks with the company name. Source review cannot catch
it, because **it does not measure the output**: it passes while the rendered page
is wrong.

## What actually prevents drift

Not this document - `references/check_masthead.py`. It renders nothing and reads no
source; it measures a real PDF and fails the build when the geometry is off.
That is the only check that catches a renderer which hardcodes its own value.

Run it on a rendered PDF, in CI where there is one:

```
python3 references/check_masthead.py path/to/rendered.pdf
```

Without a PDF the gate reports `skip`, so CI must supply one or the guarantee
is absent.

## What is NOT shared, and why

The per-renderer offsets that achieve the ±0.5pt alignment - Word
`spaceBefore`/`spaceAfter` twips, CSS pixels - are **deliberately not in
brand.json**. The library spans raw OOXML, the `docx` npm library, and
weasyprint, and each carries different line metrics. Sharing an offset would
push three of the four out of alignment.

Share the **target**, tune the **offset** locally, and let the checker prove it.

## Building a new skill

1. Read `common/brand.json`; do not copy constants out of a sibling renderer.
2. Build the masthead; tune your own offset until the checker passes.
3. Add the `check_masthead.py` gate to your skill's self-check.
4. If your product is a genuinely different design language (see
   `masthead.exempt_products`), add it to that list with a reason rather than
   half-complying.

## Known exemption

`forecast-reliability-suite` renders a dark HTML portal header with an uppercase
letterspaced wordmark. It is a deliberate departure and is not held to the
Georgia masthead geometry.

## Georgia must be installed on the machine that makes the PDF

Both alignment assertions are measured on rendered **ink**, and where the ink
sits inside a fixed line box is set by the **font's** ascent/descent - not by the
box. So the offsets are only valid for the face that actually rendered.

A bare Linux box has no Georgia. LibreOffice substitutes DejaVu Serif without
saying so, and every masthead measurement then describes a document nobody will
ever ship. Offsets calibrated in that state are wrong by ~0.3pt the moment the
document opens on a machine that has Georgia.

Rules:

1. **Calibrate on Georgia's metrics** (2048 em, hhea ascent 1878, descent -449),
   never on whatever the converter fell back to.
2. `check_masthead.py` prints the face it measured, against the face brand.json
   says the product uses. Treat a run flagged `SUBSTITUTED` as unverified, not as
   a pass.
3. A masthead that misses by ~1pt and nothing else is a missing font until
   proven otherwise. Install the font and re-measure **before** touching a
   renderer.

## The face policy lives in brand.json, not in the renderers

Three keys under `masthead`:

| key | meaning |
|---|---|
| `face` | the house masthead face. **Georgia.** |
| `face_overrides` | products that declare an exception, by folder name. Currently `earnings-packet-3p` → Times New Roman, because its masthead shares the packet's own section-heading serif rather than importing a third face. |
| `face_accepted_substitutes` | stand-ins that are **metric-faithful** and therefore safe to render and calibrate against. Liberation Serif qualifies for Times New Roman (identical advance widths *and* identical vertical metrics). **Georgia has none**  -  DejaVu Serif is not one. |

## Widths are the OTHER thing the face decides, and a wrap is fatal

The ladder's only input is a per-glyph width estimate. Two rules:

1. **The width table must be the advance widths of the face the masthead is SET in,**
   read out of the font, not fitted by hand. A table for the wrong serif over-measures
   (names step down rungs they did not need to) or under-measures (names overrun and
   the paragraph wraps).
2. **A wrap is not a small error.** The geometry is chosen for a KNOWN line count, so an
   unplanned second line puts the block ~18pt out and strands the period on a line of its
   own. Every renderer therefore carries the same guard: ask fontconfig which face will
   actually lay out the page, and if it is neither the expected face nor an accepted
   substitute, scale every measurement by the measured worst-case substitution ratio
   (1.12, from DejaVu Serif Bold ÷ Georgia Bold over 120 issuer names) so the ladder steps
   down early enough that the SUBSTITUTE still fits  -  and say so on stderr.

   That is the correct way to degrade: a rung smaller is invisible, a wrap is broken
   geometry. `INSYNC_FACE_WIDTHS=georgia|substitute` forces either path for testing.

## Word and LibreOffice lay out differently

The offsets are tuned against LibreOffice's line layout. Word distributes the
slack in a fixed line box differently (it pins the descent; LibreOffice splits by
the ascent/descent ratio), which at these sizes is worth up to ~0.9pt on the foot
and almost nothing on the top. Check the masthead in Word as well.
