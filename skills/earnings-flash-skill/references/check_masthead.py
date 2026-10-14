#!/usr/bin/env python3
"""
InSync library — masthead geometry check.

WHY THIS EXISTS
---------------
When every skill carries its OWN copy of the masthead constants, tuned by hand,
the copies drift: brand-line sizes, long-name behaviour and logo size diverge. The
drift is invisible unless something measures the OUTPUT — source review passes
happily while the rendered page is wrong.

So this checker does not read source. It renders a document, measures the PDF, and
fails on what the reader actually sees. That is the only check that catches a
renderer which hardcodes its own value instead of reading common/brand.json.

WHAT IT ASSERTS (from common/brand.json -> masthead)
  1. "InSync Analytics" is exactly brand_size_half_points / 2 pt
  2. the logo mark is mark_px, converted at 0.75 pt/px
  3. |logo_top    - brand_top|      <= alignment_tolerance_pt
  4. |logo_bottom - company_bottom| <= alignment_tolerance_pt
  5. the company/ticker line sits on a rung of company_size_ladder_half_points
  6. the company line is NOT truncated (no trailing '..' before the ticker)

It also REPORTS which face actually rendered the masthead, against the one brand.json says
this product uses (masthead.face, or masthead.face_overrides for a declared exception).
Assertions 3 and 4 are ink measurements, so they move with the face's vertical metrics; a run
where the face was substituted by something that is not metric-faithful is not measuring the
shipped design, and a sub-1.5pt miss on those two lines is far more often a missing font than
a broken renderer.

USAGE
    python3 check_masthead.py <rendered.pdf> [--brand ../../common/brand.json]

Exit 0 = pass, 1 = fail, 2 = could not measure (treated as a failure by callers,
because "I could not find the masthead" is not a pass).

DEPENDENCY
    pdfplumber. If it is absent the check SKIPS with exit 0 and a loud notice
    rather than blocking a build on a missing dev dependency — but CI should
    install it, or the guarantee is not there.
"""
import json
import os
import sys

PT_PER_PX = 0.75


def load_masthead(brand_path):
    with open(brand_path, "r", encoding="utf-8") as fh:
        return json.load(fh)["masthead"]


def find_brand_path(explicit):
    if explicit:
        return explicit
    here = os.path.dirname(os.path.abspath(__file__))
    for up in range(0, 6):
        cand = os.path.join(here, *([".."] * up), "common", "brand.json")
        cand = os.path.normpath(cand)
        if os.path.exists(cand):
            return cand
    return None


def measure(pdf_path, brand_size_pt, brand_size_alt_pt=None, co_min_pt=10.0):
    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        band = 200

        logos = [i for i in page.images if i["top"] < band]
        if not logos:
            return None
        logo = min(logos, key=lambda i: i["top"])

        sizes = [brand_size_pt] + ([brand_size_alt_pt] if brand_size_alt_pt else [])
        brand = [
            c for c in page.chars
            if c["top"] < band and c["text"] == "I"
            and any(abs(c["size"] - bs) < 0.6 for bs in sizes)
        ]
        if not brand:
            return None
        brand = min(brand, key=lambda c: c["top"])

        comp = [
            c for c in page.chars
            if c["top"] < band
            and c["top"] > brand["top"] + 2
            and (co_min_pt - 0.4) <= c["size"] < brand["size"] + 0.6
            and abs(c["x0"] - brand["x0"]) < 420
            and c["x0"] >= brand["x0"] - 8
        ]
        if not comp:
            return None
        comp_top = min(c["top"] for c in comp)
        comp_line = [c for c in comp if abs(c["top"] - comp_top) < 2.0]
        comp_line.sort(key=lambda c: c["x0"])
        tops = sorted({round(c["top"], 1) for c in comp})
        block_tops = [comp_top]
        for t in tops:
            if t <= block_tops[-1] + 2.0:
                continue
            prev = [c for c in comp if abs(c["top"] - block_tops[-1]) < 2.0]
            if t - max(c["bottom"] for c in prev) <= 6.0:
                block_tops.append(t)
            else:
                break
        comp_last = [c for c in comp if abs(c["top"] - block_tops[-1]) < 2.0]
        return {
            "logo": logo,
            "brand": brand,
            "company_size": comp_line[0]["size"],
            "company_bottom": max(c["bottom"] for c in comp_last),
            "company_text": "".join(c["text"] for c in comp_line),
            "company_font": comp_line[0].get("fontname", "?"),
            "brand_font": brand.get("fontname", "?"),
        }


def main(argv):
    if len(argv) < 2:
        print("usage: check_masthead.py <rendered.pdf> [--brand path/to/brand.json]")
        return 2
    pdf_path = argv[1]
    explicit = None
    if "--brand" in argv:
        explicit = argv[argv.index("--brand") + 1]

    brand_path = find_brand_path(explicit)
    if not brand_path:
        print("  FAIL  common/brand.json not found — the library has no shared masthead spec")
        return 1
    m = load_masthead(brand_path)

    try:
        import pdfplumber
    except ImportError:
        print("  SKIP  pdfplumber not installed; masthead geometry NOT verified.")
        print("        install it (pip install pdfplumber) or this guarantee is absent.")
        return 0

    brand_pt = m["brand_size_half_points"] / 2.0
    tol = m["alignment_tolerance_pt"]
    want_w = m["mark_px"]["w"] * PT_PER_PX
    want_h = m["mark_px"]["h"] * PT_PER_PX
    ladder_pt = [h / 2.0 for h in m["company_size_ladder_half_points"]]

    alt_hp = m.get("brand_size_two_line_half_points")
    got = measure(pdf_path, brand_pt, alt_hp / 2.0 if alt_hp else None, min(ladder_pt))
    if not got:
        print("  FAIL  could not locate the masthead (logo + brand line + company line) on page 1")
        return 2

    logo, brand = got["logo"], got["brand"]
    fails = []

    product = os.path.basename(os.path.dirname(os.path.dirname(os.path.abspath(brand_path))))
    want_face = m.get("face_overrides", {}).get(product, m.get("face", "Georgia"))
    accepted = [want_face] + list(m.get("face_accepted_substitutes", {}).get(want_face, []))
    fonts = "%s / %s" % (got["brand_font"], got["company_font"])
    flat = fonts.replace(" ", "").lower()
    substituted = not any(a.replace(" ", "").lower() in flat for a in accepted)
    print("  info  masthead face: expected %s%s, rendered in %s%s"
          % (want_face,
             " (or %s)" % ", ".join(accepted[1:]) if len(accepted) > 1 else "",
             fonts,
             "   <== NOT THE EXPECTED FACE, SUBSTITUTED" if substituted else ""))

    def ok(cond, msg):
        print(("  ok    " if cond else "  FAIL  ") + msg)
        if not cond:
            fails.append(msg)

    ok(abs(brand["size"] - brand_pt) < 0.6 or (alt_hp and abs(brand["size"] - alt_hp / 2.0) < 0.6),
       'brand line "InSync Analytics" is %.1fpt%s (got %.1fpt)'
       % (brand_pt, " (or %.1fpt on a two-line masthead)" % (alt_hp / 2.0) if alt_hp else "", brand["size"]))

    ok(abs(logo["width"] - want_w) < 1.0 and abs(logo["height"] - want_h) < 1.0,
       "logo mark is %.1f x %.1fpt (got %.1f x %.1f)"
       % (want_w, want_h, logo["width"], logo["height"]))

    d_top = logo["top"] - brand["top"]
    ok(abs(d_top) <= tol,
       "logo TOP meets the brand line within %.1fpt (got %+.2f)" % (tol, d_top))

    d_bot = logo["bottom"] - got["company_bottom"]
    ok(abs(d_bot) <= tol,
       "logo BOTTOM meets the company line within %.1fpt (got %+.2f)" % (tol, d_bot))

    on_rung = any(abs(got["company_size"] - r) < 0.15 for r in ladder_pt)
    ok(on_rung,
       "company line sits on a ladder rung (got %.1fpt; ladder %s)"
       % (got["company_size"], "/".join("%.1f" % r for r in ladder_pt)))

    txt = got["company_text"]
    truncated = ".." in txt.split("(")[0] if "(" in txt else ".." in txt
    ok(not truncated, "company name is not truncated (got %r)" % txt[:48])

    print("")
    if fails:
        print("MASTHEAD CHECK FAILED — %d problem(s)." % len(fails))
        if substituted:
            print("NOTE: %s was NOT available to the PDF converter, so the masthead was laid"
                  % want_face)
            print("      out with substitute metrics. Install the face and re-run BEFORE")
            print("      touching the renderer — a sub-1.5pt miss here is usually only that.")
        print("Fix the renderer to match common/brand.json; do not edit brand.json to match the renderer.")
        return 1
    if substituted:
        print("MASTHEAD CHECK UNVERIFIED — every geometry assertion above passed, but they "
              "were measured in %s, not %s." % (fonts, want_face))
        print("      Ink positions move with the font's vertical metrics, so these numbers "
              "do not describe the shipped page.")
        print("      Install %s and re-run before treating this masthead as checked." % want_face)
        return 2
    print("MASTHEAD CHECK PASSED — logo, brand line and alignment match the library spec.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
