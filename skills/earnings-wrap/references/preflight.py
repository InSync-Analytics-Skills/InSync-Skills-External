#!/usr/bin/env python3
"""
preflight.py — environment check for the earnings-wrap skill.

The renderer is deterministic given a config, but the PDF it produces is NOT
environment-independent: page breaks are decided by the metrics of the font the
host actually resolves. A host lacking Calibri (and its metric-compatible clone
Carlito) reflows the body wider and turns the 3-page contract into 4 pages, with
no error anywhere. This script makes that visible BEFORE a note is built.

Usage:  python references/preflight.py
Exit 0 = environment will reproduce the house layout.
Exit 1 = something will differ; each problem prints an actionable fix.
"""
import os, sys, shutil, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))

SANS_OK = {"calibri", "carlito"}
SERIF_OK = {"georgia", "gelasio"}


def _fc_match(family):
    if not shutil.which("fc-match"):
        return None
    try:
        out = subprocess.run(["fc-match", family], capture_output=True, text=True, timeout=10).stdout
        return out.split('"')[1] if '"' in out else out.strip()
    except Exception:
        return None


def main():
    problems, warnings, notes = [], [], []

    try:
        import weasyprint
        notes.append("weasyprint %s" % weasyprint.__version__)
    except (ImportError, OSError) as _e:
        warnings.append("weasyprint cannot run here (%s). The build will write the Word edition "
                        "(.docx), and a PDF from it if LibreOffice or Microsoft Word is installed. For "
                        "the house PDF: pip install weasyprint (Linux/Mac; on "
                        "Windows it also needs the GTK/Pango runtime)." % type(_e).__name__)

    if not os.path.exists(os.path.join(HERE, "..", "assets", "insync-logo-mark.png")):
        problems.append("assets/insync-logo-mark.png is missing. house_asserts will fail on the "
                        "base64-logo check and the masthead will render empty.")

    sans = _fc_match("Calibri")
    serif = _fc_match("Georgia")
    if sans is None:
        warnings.append("fc-match unavailable, so font resolution could not be verified. On a host "
                        "without Calibri or Carlito the note reflows to 4 pages.")
    else:
        notes.append("Calibri resolves to %s" % sans)
        notes.append("Georgia resolves to %s" % serif)
        if not any(k in sans.lower() for k in SANS_OK):
            problems.append(
                "BODY FONT MISMATCH: Calibri resolves to %r, which is not metric-compatible with "
                "Calibri. Text will set wider and the note will run to 4 pages. Fix: install the "
                "metric-compatible clone (Debian/Ubuntu: apt-get install fonts-crosextra-carlito; "
                "Fedora: dnf install google-carlito-fonts)." % sans)
        if serif and not any(k in serif.lower() for k in SERIF_OK):
            warnings.append(
                "Masthead serif resolves to %r rather than Georgia/Gelasio. Cosmetic only (the "
                "masthead sets slightly differently); pagination is unaffected. Fix if you want a "
                "pixel match: install Gelasio." % serif)

    if not shutil.which("pdfinfo"):
        warnings.append("pdfinfo not found, so self_check.py will skip its page-fit assertion. "
                        "The renderer's own page gate still runs. Fix: install poppler-utils.")

    if not problems:
        try:
            import json, tempfile
            sys.path.insert(0, HERE)
            import render_wrap
            cfg = json.load(open(os.path.join(HERE, "sample_config.json"), encoding="utf-8"))
            with tempfile.TemporaryDirectory() as td:
                out = os.path.join(td, "preflight.pdf")
                render_wrap.render(cfg, out)
                try:
                    import pypdf
                    n = len(pypdf.PdfReader(out).pages)
                    notes.append("sample renders in %d pages (expected 3)" % n)
                    if n != 3:
                        problems.append(
                            "The bundled 3-page sample renders as %d pages here, so this host does "
                            "NOT reproduce the house layout. Almost always the body font: see the "
                            "font check above." % n)
                except ImportError:
                    notes.append("pypdf unavailable, sample page count not verified")
        except SystemExit as e:
            problems.append("sample render aborted: %s" % e)
        except Exception as e:
            problems.append("sample render failed: %r" % e)

    for n in notes:
        print("  ok   " + n)
    for w in warnings:
        print("  WARN " + w)
    for p in problems:
        print("  FAIL " + p)
    print("\nPREFLIGHT:", "environment reproduces the house layout" if not problems
          else "%d blocking problem(s) — fix before building a note" % len(problems))
    sys.exit(1 if problems else 0)


_MCP_FLAGS = {
    "--check-mcp": "prove the MCP connection and the tool names",
    "--selftest-mcp": "probe each operation the skill uses",
}

def _parse_args(argv):
    bad = [a for a in argv[1:] if a.startswith("-")]
    for a in bad:
        if a in _MCP_FLAGS:
            sys.stderr.write(
                "preflight.py does not %s, and never did - it is a LOCAL environment check "
                "(fonts, renderer, logo, page fit) with no MCP client in it. Until v7.7.0 this "
                "flag was silently ignored and the run exited 0, which read as a passed MCP "
                "check.\n\nThe MCP is verified by the model, in the run itself: "
                "`search_tickerid` is the first call of every build and its `match_type` is "
                "recorded in `identityProbe`, where the `identity` gate blocks anything that "
                "is not an exact match. That IS the connection test.\n" % _MCP_FLAGS[a])
        else:
            sys.stderr.write("preflight.py takes no arguments; %r is not a flag it has.\n" % a)
        sys.exit(2)
    if len(argv) > 1:
        sys.stderr.write("preflight.py takes no arguments (got %r).\n" % argv[1:])
        sys.exit(2)


if __name__ == "__main__":
    _parse_args(sys.argv)
    main()
