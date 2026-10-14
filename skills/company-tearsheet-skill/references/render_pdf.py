#!/usr/bin/env python3
"""
render_pdf.py — PDF twin + hard one-page gate for company-tearsheet-skill.

The tear sheet is specified as a ONE PAGE document. `eval_tearsheet.py` enforces that
budget indirectly, by capping word and bullet counts on the payload. Those caps are a
proxy: they are checked BEFORE rendering, so a payload can pass the eval and still
overflow once laid out (long KPI or company names, six KPI rows instead of four, a wide
peer table). This script closes that gap by measuring the RENDERED artifact.

It converts the generated .docx to .pdf with LibreOffice and counts the pages. More than
one page is a hard failure: the exit code is non-zero and the message names the sheet.

Usage:
    python render_pdf.py OUT.docx [--outdir DIR] [--allow-multipage]

Exit codes:
    0  converted, exactly one page
    1  converted, MORE THAN ONE PAGE (gate failure)
    2  conversion unavailable or failed (docx is still valid; PDF twin not produced)

Stdlib only, matching compute_tearsheet.py and eval_tearsheet.py. Page counting reads the
PDF page tree directly; pypdf is used instead when importable, purely as a cross-check.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys


def find_soffice():
    for name in ("soffice", "libreoffice"):
        path = shutil.which(name)
        if path:
            return path
    for path in ("/usr/bin/soffice", "/usr/lib/libreoffice/program/soffice",
                 "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        if os.path.exists(path):
            return path
    return None


def count_pages(pdf_path):
    try:
        from pypdf import PdfReader
        return len(PdfReader(pdf_path).pages)
    except Exception:
        pass
    with open(pdf_path, "rb") as fh:
        data = fh.read()
    for pattern in (rb"/Type\s*/Pages\b[^>]*?/Count\s+(\d+)", rb"/Count\s+(\d+)\s*/Kids"):
        hits = [int(x) for x in re.findall(pattern, data, re.S)]
        if hits:
            return max(hits)
    hits = re.findall(rb"/Type\s*/Page\b(?!s)", data)
    return len(hits) or None


def convert(docx_path, outdir):
    soffice = find_soffice()
    if not soffice:
        return None, ("LibreOffice not found — PDF twin skipped. The .docx is unaffected. "
                      "Install libreoffice, or convert manually, to enable the one-page gate.")
    os.makedirs(outdir, exist_ok=True)
    try:
        subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir",
                        outdir, docx_path],
                       check=True, capture_output=True, timeout=180)
    except subprocess.TimeoutExpired:
        return None, (
            "LibreOffice TIMED OUT after 180s. It is installed but did not return, so the "
            "one-page gate did NOT run. This is usually a stale profile lock, not this "
            "document: kill any running soffice process and remove the lock "
            "(~/.config/libreoffice/4/.lock on Linux/macOS, "
            "%APPDATA%\\LibreOffice\\4\\.lock on Windows), then re-run. Re-running the same "
            "conversion unchanged fails the same way.")
    except subprocess.CalledProcessError as exc:
        return None, f"LibreOffice conversion failed: {exc}"
    pdf = os.path.join(outdir, os.path.splitext(os.path.basename(docx_path))[0] + ".pdf")
    if not os.path.exists(pdf):
        return None, "LibreOffice reported success but produced no PDF"
    return pdf, None


def main():
    ap = argparse.ArgumentParser(description="Render the PDF twin and enforce one page.")
    ap.add_argument("docx", help="generated .docx tear sheet")
    ap.add_argument("--outdir", default=None, help="PDF destination (default: alongside the docx)")
    ap.add_argument("--allow-multipage", action="store_true",
                    help="report overflow but exit 0 — for diagnosis only, never for delivery")
    args = ap.parse_args()

    if not os.path.exists(args.docx):
        print(f"ERROR: no such file: {args.docx}", file=sys.stderr)
        return 2

    outdir = args.outdir or os.path.dirname(os.path.abspath(args.docx))
    pdf, err = convert(args.docx, outdir)
    if not pdf:
        print(f"WARN: {err}", file=sys.stderr)
        return 2

    pages = count_pages(pdf)
    name = os.path.basename(pdf)
    if pages is None:
        print(f"WARN: {name} written but page count could not be determined", file=sys.stderr)
        return 2
    if pages == 1:
        print(f"OK — {name}: 1 page")
        return 0

    print(f"ONE-PAGE GATE FAILED — {name} is {pages} pages.\n"
          f"  The tear sheet is specified as one page. Shorten the payload and regenerate.\n"
          f"  Re-run eval_tearsheet.py first and read its LAYOUT line: it prints the five\n"
          f"  numbers competing for the page for THIS payload. Reduce whichever you can most\n"
          f"  afford — they trade off against each other, so there is no single right cut:\n"
          f"    - prose words: trim a little from EVERY block (business_overview, bull/bear\n"
          f"      bullets, debate rationales, recent news). Cutting one block alone is the\n"
          f"      usual mistake — the overflow is cumulative, not caused by one field.\n"
          f"    - financial rows, KPI rows, peer rows, valuation slots: each row costs more\n"
          f"      page than a dozen words, so dropping one is the fastest fix when the\n"
          f"      tables are at their maximum.\n"
          f"    - long KPI, company or peer display names wrap and cost a whole line each.\n"
          f"  Then re-run generate_tearsheet.py and this script again.",
          file=sys.stderr)
    if not args.allow_multipage:
        _stem, _ext = os.path.splitext(pdf)
        _rej = "%s__REJECTED_%dPAGES%s" % (_stem, pages, _ext)
        try:
            if os.path.exists(_rej):
                os.remove(_rej)
            os.replace(pdf, _rej)
            print("  The failed render has been moved to %s so it cannot be mistaken for a\n"
                  "  deliverable. Open it to see what overflowed, then delete it."
                  % os.path.basename(_rej), file=sys.stderr)
        except OSError as exc:
            print("  WARNING: could not rename the rejected PDF (%s). It is still at %s — do\n"
                  "  NOT deliver it." % (exc, name), file=sys.stderr)
        return 1
    print("ONE-PAGE GATE BYPASSED (--allow-multipage): %s is %d pages and has NOT passed the "
          "one-page gate. DO NOT DELIVER THIS OUTPUT." % (name, pages))
    return 0


if __name__ == "__main__":
    sys.exit(main())
