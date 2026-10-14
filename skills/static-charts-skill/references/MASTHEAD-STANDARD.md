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

When each skill carries its own copy of these constants, tuned by hand, the
copies drift: different brand-line sizes, different long-name behaviours, a logo
that shrinks with the company name. Drift like that goes unseen unless
**something measures the output**. Source review passes happily while the
rendered page is wrong.

## What actually prevents drift

Not this document - `references/check_masthead.py`. It renders nothing and reads no
source; it measures a real PDF and fails the build when the geometry is off.
That is the only check that catches a renderer which hardcodes its own value.

Wire it into the skill's own self-check (in this skill, `references/self_check.py`
already calls it), and pass a rendered PDF in CI:

```
python references/check_masthead.py path/to/rendered.pdf
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

## Word rendering

The alignment offsets in the library were tuned against LibreOffice. Word's
line metrics differ slightly, so verify a masthead in Word as well.
