#!/usr/bin/env python
"""Post-render gate — assert on the RENDERED document, not on the pipeline's own JSON.

Why this exists
---------------
SKILL.md promises that "a reader's subtraction must work on every row". A gate that reads
`computed.json` checks strings the renderer then re-formats independently, so it can approve
strings that are not the strings on the page. Example (invented figures):

    Unit - ASP  $25.00   $24.00   $24.50   +$1.00K   +4.2%   +2.0%   Beat

The MCP returned "$25.0000k". The level cells lost the magnitude; the delta kept it, so the
row asks a reader to accept $25.00 - $24.00 = +$1.00K. A gate looking upstream of the file
actually produced passes it.

This module opens the .docx, parses the scorecard out of the document XML, and re-derives
each row from the strings a reader will see. It shares `parse_disp` with run_pipeline and
`_lower_is_better` / the grading thresholds with run_math rather than reimplementing them,
so it cannot drift from the logic it is checking. Stdlib only — no new dependency.

Usage:
    python render_gate.py <flash.docx> [--pdf <flash.pdf>] [--computed <computed.json>] [--json]

Exit codes: 0 clean / 1 defects found / 2 bad invocation.
"""
import json
import os
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import run_math
from run_pipeline import parse_disp

SCORECARD_COLS = 8
_BLANK = ("", "-", "--", "—", "N/A", "n/a", "NM", "nm")
_MAG_RE = re.compile(r"(?<=[\d\s])(T|B|M|K)\b", re.I)


def _cell_text(tc_xml):
    body = re.sub(r"<w:tcPr>.*?</w:tcPr>", "", tc_xml, flags=re.S)
    txt = "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", body, re.S))
    return (txt.replace("&amp;", "&").replace("&apos;", "'")
               .replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">")
               .replace("−", "-").strip())


def scorecard_rows(docx_path):
    with zipfile.ZipFile(docx_path) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    rows = []
    for tbl in re.findall(r"<w:tbl>(.*?)</w:tbl>", xml, re.S):
        trs = re.findall(r"<w:tr\b.*?</w:tr>", tbl, re.S)
        if len(trs) < 4:
            continue
        for tr in trs:
            cells = [_cell_text(tc) for tc in re.findall(r"<w:tc>(.*?)</w:tc>", tr, re.S)]
            if len(cells) != SCORECARD_COLS:
                continue
            block = cells[1:4]
            if any("(Actual)" in c or "(Estimate)" in c
                   or re.fullmatch(r"\S+ \([AE]\)", c.strip()) for c in block):
                continue
            if not any(c.strip() for c in block):
                continue
            rows.append(cells)
    return rows


def _mag(s):
    if s.strip() in _BLANK:
        return None
    m = _MAG_RE.search(s.replace("ppt", "").replace("bps", ""))
    return (m.group(1).upper() if m else "")


def check_row(cells, ltcode=None):
    name, p_s, a_s, e_s, d_s, dp_s, y_s, verdict = cells
    out = []
    a, e, p, d = (parse_disp(x) for x in (a_s, e_s, p_s, d_s))
    is_pct = a_s.strip().endswith("%")

    mags = {k: _mag(v) for k, v in
            (("actual", a_s), ("estimate", e_s), ("prior", p_s), ("delta", d_s))}
    present = {k: v for k, v in mags.items() if v is not None}
    if not is_pct and len(set(present.values())) > 1:
        out.append(
            f"UNIT MISMATCH — magnitudes disagree within the row: "
            + ", ".join(f"{k}={v or 'none'}" for k, v in present.items())
            + f" ({a_s} / {e_s} / {p_s} / {d_s}). A reader's subtraction is not wrong here, "
              f"it is impossible.")

    if None not in (a, e, d):
        scale = max(abs(a), abs(e), 1.0)
        if abs((a - e) - d) > scale * 1e-9 + 1e-6:
            out.append(f"DELTA — {a_s} - {e_s} = {a - e:,.6g} but the row prints {d_s}")

    if not is_pct and None not in (a, e) and e and dp_s.strip() not in _BLANK:
        want = (a - e) / abs(e) * 100.0
        got = parse_disp(dp_s)
        if got is not None and abs(want - got) > 0.055:
            out.append(f"DELTA % — {a_s} vs {e_s} is {want:+.2f}% but the row prints {dp_s}")

    if None not in (a, p) and p and y_s.strip() not in _BLANK:
        got = parse_disp(y_s)
        if got is not None:
            if "bps" in y_s:
                want = (a - p) * 100.0
                tol = 1.0
            else:
                if (a >= 0) != (p >= 0):
                    want = got = 0.0
                else:
                    want = (a - p) / abs(p) * 100.0
                tol = 0.055
            if abs(want - got) > tol:
                unit = "bps" if "bps" in y_s else "%"
                out.append(f"YoY — {a_s} against {p_s} is {want:+.2f}{unit} "
                           f"but the row prints {y_s}")

    if verdict in ("Beat", "Miss", "In-Line") and None not in (a, e) and ltcode:
        lb = run_math._lower_is_better(name, ltcode)
        if is_pct:
            diff = a - e
            thr = run_math.PCT_BEAT_MISS_THRESHOLD_PPT
            raw = "Beat" if diff >= thr else ("Miss" if diff <= -thr else "In-Line")
        else:
            dp = (a - e) / abs(e) * 100.0 if e else 0.0
            thr = run_math.BEAT_MISS_THRESHOLD * 100
            raw = "Beat" if dp >= thr else ("Miss" if dp <= -thr else "In-Line")
        want = raw if raw == "In-Line" else (("Miss" if raw == "Beat" else "Beat") if lb else raw)
        if want != verdict:
            out.append(f"VERDICT — {a_s} vs {e_s} (lower_is_better={lb}) reads {want} "
                       f"but the row prints {verdict}")
        if not run_math._polarity_declared(name, ltcode):
            out.append(f"POLARITY UNRESOLVED — {ltcode} has no registry entry and matches no "
                       f"polarity term, so {verdict} rests on the higher-is-better DEFAULT. "
                       f"This gate shares that assumption and cannot test it.")
    return out


def page_count(pdf_path):
    if not pdf_path or not os.path.isfile(pdf_path):
        return None
    try:
        from pypdf import PdfReader
        return len(PdfReader(pdf_path).pages)
    except Exception:
        pass
    try:
        import subprocess
        out = subprocess.run(["pdfinfo", pdf_path], capture_output=True, text=True, timeout=30)
        m = re.search(r"^Pages:\s*(\d+)", out.stdout or "", re.M)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    try:
        with open(pdf_path, "rb") as f:
            return len(re.findall(rb"/Type\s*/Page\b", f.read())) or None
    except Exception:
        return None


def _has_actual(m):
    v = (m.get("currentActual") or "").strip()
    return v not in ("", "-", "N/A", "n/a", "--")


def expected_rows(computed_path):
    with open(computed_path, encoding="utf-8") as f:
        c = json.load(f)
    fin = [m for m in (c.get("financialMetrics") or []) if _has_actual(m)]
    ops = [m for m in (c.get("operatingMetrics") or []) if _has_actual(m)][:5]
    return fin + ops


def magnitude_drift(printed, computed_path):
    try:
        want = expected_rows(computed_path)
    except Exception:
        return []
    if len(want) != len(printed):
        return [f"ROW COUNT — computed.json holds {len(want)} renderable row(s) but the "
                f"page prints {len(printed)}."]
    out = []
    cols = (("priorActual", 1), ("currentActual", 2), ("currentEstimate", 3),
            ("deltaAbsolute", 4))
    for w, p in zip(want, printed):
        for key, idx in cols:
            wm, pm = _mag(str(w.get(key, ""))), _mag(p[idx])
            if wm is None or pm is None:
                continue
            if wm != pm:
                out.append(f"{p[0]}: MAGNITUDE DRIFT — {key} computed as "
                           f"'{w.get(key)}' but the page prints '{p[idx]}'. The row may "
                           f"still close and still be wrong by a factor of 1,000.")
    return out


def check(docx_path, pdf_path=None, computed_path=None):
    rows = scorecard_rows(docx_path)
    want = []
    if computed_path and os.path.isfile(computed_path):
        try:
            want = expected_rows(computed_path)
        except Exception:
            want = []
    lts = [str(w.get("ltcode") or "") for w in want] if len(want) == len(rows) else [None] * len(rows)
    defects, notes, na_cells = [], [], 0
    for cells, lt in zip(rows, lts):
        na_cells += sum(1 for c in cells if c.strip() in ("N/A", "n/a"))
        for d in check_row(cells, lt):
            (notes if d.startswith("POLARITY UNRESOLVED") else defects).append(
                f"{cells[0]}: {d}")
    if want:
        defects.extend(magnitude_drift(rows, computed_path))
    verdicts_checked = sum(1 for lt in lts if lt)
    if not rows:
        defects.append("NO SCORECARD — no 8-column data row was found in the rendered "
                       "document. The variance table is the point of the flash.")
    pages = page_count(pdf_path)
    if pages and pages > 1:
        defects.append(f"PAGE COUNT — the rendered PDF is {pages} pages. The flash is a "
                       f"one-page format.")
    return {"rows": len(rows), "defects": defects, "notes": notes, "na_cells": na_cells,
            "pages": pages, "verdicts_checked": verdicts_checked}


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    docx = args[0]
    pdf = None
    if "--pdf" in argv:
        i = argv.index("--pdf")
        pdf = argv[i + 1] if i + 1 < len(argv) else None
    elif docx.lower().endswith(".docx"):
        cand = docx[:-5] + ".pdf"
        pdf = cand if os.path.isfile(cand) else None
    computed = None
    if "--computed" in argv:
        i = argv.index("--computed")
        computed = argv[i + 1] if i + 1 < len(argv) else None
    if not os.path.isfile(docx):
        print(f"render_gate: no such file: {docx}", file=sys.stderr)
        return 2

    res = check(docx, pdf, computed)
    if "--json" in argv:
        print(json.dumps(res, indent=2))
    else:
        print(f"render_gate: {res['rows']} scorecard row(s) re-derived from "
              f"{os.path.basename(docx)}"
              + (f", {res['pages']} page(s)" if res["pages"] else ""))
        if res["rows"] and not res.get("verdicts_checked"):
            print("  note  verdict direction NOT checked — pass --computed <computed.json> so "
                  "polarity can be read by ltcode instead of guessed from a label")
        if res["na_cells"]:
            print(f"  note  {res['na_cells']} N/A cell(s) — each one is a real data gap "
                  f"and must be explained before the file goes out")
        for n in res.get("notes", []):
            print(f"  note  {n}")
        for d in res["defects"]:
            print(f"  FAIL  {d}")
        print("RENDER GATE OK" if not res["defects"]
              else f"RENDER GATE FAILED: {len(res['defects'])} defect(s) on the page")
    return 1 if res["defects"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
