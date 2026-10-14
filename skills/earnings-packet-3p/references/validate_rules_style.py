#!/usr/bin/env python3
"""Passes 2, 5 and 6 re-derived for a rules-only table style.

The bundled validator finds tables by their VERTICAL borders. Suite styling draws none,
so passes 2 (geometry), 5 (dash columns) and 6 (pagination) go blind: they report one
table and pass trivially. This script restores the same three guarantees by detecting a
table from its HORIZONTAL rules instead, so the checks keep working rather than merely
keep passing.

    python3 validate_rules_style.py "<the built>.pdf"

2  geometry     every table shares one left edge and one right edge, on every page
5  dash columns no rendered column is majority em-dash
6  pagination   no table fragment shorter than MIN_ROWS rows sits at a page boundary
"""
import sys, collections

MIN_ROWS = 3
DASH_MIN = 3
DASH_MAX = 0.50
_DASH_FALLBACK = ["", "-", "–", "—", "–%", "-%", "–x", "n/a", "N/A"]
def _dash_vocabulary():
    import json as _json, os as _os
    here = _os.path.dirname(_os.path.abspath(__file__))
    for cand in (_os.path.join(here, "..", "common", "empty-vocabulary.json"),
                 _os.path.join(here, "common", "empty-vocabulary.json")):
        try:
            with open(cand, encoding="utf8") as fh:
                v = _json.load(fh)
            got = {e["text"] for e in v.get("entries", []) if e.get("renders_as_dash")}
            if got:
                return got
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return set(_DASH_FALLBACK)
DASHES = _dash_vocabulary()
import re
NUM = re.compile(r'^[+–\-]?\(?[$€£₹]?[0-9][0-9,]*(\.[0-9]+)?([%xBM])?\)?( bps)?$')
TOPGAP = 12
FOOTGAP = 70


ORANGE = (0.9098, 0.5216, 0.0471)
ORANGE_TOL = 0.02


def _is_orange(c):
    return c is not None and len(c) >= 3 and all(
        abs(float(a) - b) <= ORANGE_TOL for a, b in zip(c[:3], ORANGE))


def rules(page):
    out = []
    for d in page.get_drawings():
        r = d["rect"]
        if d.get("color") is None:
            continue
        if _is_orange(d.get("color")):
            continue
        if r.width > 50 and r.height < 2:
            out.append((round(r.y0, 1), round(r.x0, 1), round(r.x1, 1)))
    return sorted(set(out))


def blocks(page):
    rs = rules(page)
    if not rs:
        return []
    by_span = collections.defaultdict(list)
    for y, x0, x1 in rs:
        by_span[(x0, x1)].append(y)
    out = []
    for (x0, x1), ys in by_span.items():
        ys = sorted(ys)
        if len(ys) < 2:
            continue
        gaps = [b - a for a, b in zip(ys, ys[1:])]
        med = sorted(gaps)[len(gaps) // 2]
        cur = [ys[0]]
        for a, b in zip(ys, ys[1:]):
            if (b - a) > max(3 * med, med + 8):
                if len(cur) >= 2:
                    out.append({"x0": x0, "x1": x1, "ys": cur})
                cur = [b]
            else:
                cur.append(b)
        if len(cur) >= 2:
            out.append({"x0": x0, "x1": x1, "ys": cur})
    return out


def spans(page):
    return [(s["text"].strip(), (s["bbox"][0] + s["bbox"][2]) / 2,
             (s["bbox"][1] + s["bbox"][3]) / 2)
            for b in page.get_text("dict")["blocks"] for l in b.get("lines", [])
            for s in l["spans"] if s["text"].strip()]


FULL_WIDTH_MIN = 0.85
EDGE_TOL = 1.0


def pass_geometry(doc):
    found = [b for p in doc for b in blocks(p)]
    if not found:
        return [], "0 tables"
    widest = max(b["x1"] - b["x0"] for b in found)
    full = [b for b in found if (b["x1"] - b["x0"]) >= FULL_WIDTH_MIN * widest]
    inner = [b for b in found if (b["x1"] - b["x0"]) < FULL_WIDTH_MIN * widest]

    def cluster(vals):
        out = []
        for v in sorted(vals):
            if out and v - out[-1][-1] <= EDGE_TOL:
                out[-1].append(v)
            else:
                out.append([v])
        return out

    lefts = collections.Counter(b["x0"] for b in full)
    rights = collections.Counter(b["x1"] for b in full)
    lc, rc = cluster(lefts), cluster(rights)
    bad = []
    if len(lc) > 1:
        bad.append(("full-width tables do not share one left edge",
                    [round(min(g), 1) for g in lc], dict(lefts)))
    if len(rc) > 1:
        bad.append(("full-width tables do not share one right edge",
                    [round(min(g), 1) for g in rc], dict(rights)))
    spread = lambda g: (max(g) - min(g)) if g else 0
    info = (f"{len(full)} full-width table(s), 1 left edge "
            f"(spread {spread(lc[0]):.1f}pt), 1 right edge "
            f"(spread {spread(rc[0]):.1f}pt)" if len(lc) == 1 and len(rc) == 1
            else f"{len(full)} full-width table(s), left={dict(lefts)} right={dict(rights)}")
    if inner:
        iw = sorted({round((b["x1"] - b["x0"]) / widest, 2) for b in inner})
        info += f"; {len(inner)} nested block(s) excluded by width {iw}"
    return bad, info


def pass_dashes(doc):
    bad, ncols = [], 0
    for pno, p in enumerate(doc, 1):
        sp = spans(p)
        for b in blocks(p):
            y0, y1 = min(b["ys"]) - 12, max(b["ys"]) + 2
            pts = [(t, cx) for t, cx, cy in sp
                   if y0 <= cy <= y1 and b["x0"] - 2 <= cx <= b["x1"] + 2
                   and (t in DASHES or NUM.match(t))]
            if not pts:
                continue
            xs = sorted(set(round(cx, 0) for _, cx in pts))
            cuts, prev = [], xs[0]
            for x in xs[1:]:
                if x - prev > 26:
                    cuts.append((prev + x) / 2)
                prev = x
            edges = [b["x0"] - 3] + cuts + [b["x1"] + 3]
            bins = [{"dash": 0, "val": 0} for _ in range(len(edges) - 1)]
            for t, cx in pts:
                for i in range(len(edges) - 1):
                    if edges[i] <= cx < edges[i + 1]:
                        if t in DASHES:
                            bins[i]["dash"] += 1
                        else:
                            bins[i]["val"] += 1
                        break
            for i, c in enumerate(bins):
                tot = c["dash"] + c["val"]
                if not tot:
                    continue
                ncols += 1
                if c["dash"] >= DASH_MIN and c["dash"] / tot > DASH_MAX:
                    bad.append((f"page {pno}", f'column {i+1} of the table at '
                                f'y={round(min(b["ys"]))}: {c["dash"]} dashes of {tot} cells'))
    return bad, f"{ncols} columns scanned"


def pass_pagination(doc):
    bad = []
    for pno, p in enumerate(doc, 1):
        bs = blocks(p)
        if not bs:
            continue
        top = min(min(b["ys"]) for b in bs)
        for b in bs:
            rows = len(b["ys"]) - 1
            head = pno > 1 and abs(min(b["ys"]) - top) < TOPGAP
            tail = max(b["ys"]) > p.rect.y1 - FOOTGAP
            if rows and rows < MIN_ROWS and (head or tail):
                bad.append((f"page {pno}",
                            f'{rows}-row table fragment at y={round(min(b["ys"]))}'))
    return bad, f"{sum(len(blocks(p)) for p in doc)} table blocks"


def main():
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    if len(sys.argv) < 2:
        print(__doc__ or "usage: validate_rules_style.py <rendered.pdf>")
        print("\nsupply the rendered PDF")
        return 2
    doc = fitz.open(sys.argv[1])
    fails = 0
    for name, (bad, info) in [("2 geometry", pass_geometry(doc)),
                              ("5 dashes", pass_dashes(doc)),
                              ("6 pagination", pass_pagination(doc))]:
        print(f'pass {name:12} {"PASS" if not bad else "FAIL"}   ({info})')
        for b in bad[:20]:
            print("        ", b)
        fails += bool(bad)
    print("\nRULES-STYLE VALIDATION", "PASSED" if not fails else f"FAILED ({fails})")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
