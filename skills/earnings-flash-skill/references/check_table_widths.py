#!/usr/bin/env python3
"""
check_table_widths.py - the table-geometry gate (v11 Python port of check_table_widths.js v2,
so it runs where Node does not).

  python3 check_table_widths.py <file.docx> [...more.docx]

Catches (A) OVERFLOW - a table wider than its container - and (B) GRID vs CELLS - <w:tblGrid>
saying one width while a row's <w:tcW> say another. Handles nesting and w:gridSpan.
Exit 0 = sound. Exit 1 = at least one table disagrees with its container or itself.
"""
import os
import re
import sys
import zipfile

TOL = 12
_TOK = re.compile(r"<w:tbl>|</w:tbl>|<w:tr>|</w:tr>|<w:tc>|</w:tc>|<w:tcW[^>]*>|<w:tblW[^>]*>|"
                  r"<w:gridCol[^>]*>|<w:gridSpan[^>]*>|<w:tblGrid>|</w:tblGrid>")


def _attr(tag, name):
    m = re.search(name + r'="(\d+)"', tag)
    return int(m.group(1)) if m else None


def _column(xml):
    sz, mar = re.search(r"<w:pgSz[^>]*>", xml), re.search(r"<w:pgMar[^>]*>", xml)
    if not sz or not mar:
        return None
    w, l, r = _attr(sz.group(0), "w:w"), _attr(mar.group(0), "w:left"), _attr(mar.group(0), "w:right")
    return None if None in (w, l, r) else w - l - r


def audit(xml, column):
    tbls, stack, cells = [], [], []
    idx, row = 0, None
    for t in _TOK.findall(xml):
        if t == "<w:tbl>":
            idx += 1
            stack.append({"index": idx, "depth": len(stack),
                          "container": column if not stack else (cells[-1] if cells else None),
                          "grid": [], "tblW": None, "inGrid": False, "rows": []})
        elif t == "</w:tbl>":
            if stack:
                tbls.append(stack.pop())
        elif t == "<w:tblGrid>":
            if stack:
                stack[-1]["inGrid"] = True
        elif t == "</w:tblGrid>":
            if stack:
                stack[-1]["inGrid"] = False
        elif t.startswith("<w:gridCol"):
            w = _attr(t, "w:w")
            if stack and stack[-1]["inGrid"] and w is not None:
                stack[-1]["grid"].append(w)
        elif t.startswith("<w:tblW"):
            w = _attr(t, "w:w")
            if stack and stack[-1]["tblW"] is None and w is not None:
                stack[-1]["tblW"] = w
        elif t == "<w:tr>":
            row = {"sum": 0, "cells": 0, "span": 0}
        elif t == "</w:tr>":
            if stack and row and row["cells"]:
                stack[-1]["rows"].append(row)
            row = None
        elif t == "<w:tc>":
            cells.append(None)
        elif t == "</w:tc>":
            if cells:
                cells.pop()
        elif t.startswith("<w:tcW"):
            w = _attr(t, "w:w")
            if cells and w is not None:
                cells[-1] = w
                if row is not None and len(cells) == len(stack):
                    row["sum"] += w
                    row["cells"] += 1
        elif t.startswith("<w:gridSpan"):
            v = _attr(t, "w:val")
            if row is not None and v:
                row["span"] += v - 1
    return tbls


def main(files):
    if not files:
        print("usage: python3 check_table_widths.py <file.docx> [...]", file=sys.stderr)
        return 2
    failed = 0
    for f in files:
        if not os.path.exists(f):
            print("MISSING  %s" % f, file=sys.stderr)
            failed += 1
            continue
        try:
            with zipfile.ZipFile(f) as z:
                xml = z.read("word/document.xml").decode("utf-8")
        except Exception as e:
            print("UNREADABLE  %s: %s" % (f, e), file=sys.stderr)
            failed += 1
            continue
        column = _column(xml)
        if not column:
            print("NO SECTPR  %s" % f, file=sys.stderr)
            failed += 1
            continue
        tbls = audit(xml, column)
        probs = []
        for t in tbls:
            if not t["grid"]:
                continue
            g = sum(t["grid"])
            what = "text column" if t["depth"] == 0 else "parent cell"
            if t["container"] is not None and g - t["container"] > TOL:
                probs.append("  OVERFLOW  table #%d (depth %d): grid %d exceeds %s %d by %d"
                             % (t["index"], t["depth"], g, what, t["container"], g - t["container"]))
            elif t["depth"] == 0 and t["container"] is not None and t["container"] - g > TOL:
                probs.append("  SHORT     table #%d: grid %d is %d narrower than the text column"
                             % (t["index"], g, t["container"] - g))
            if t["tblW"] is not None and abs(t["tblW"] - g) > TOL:
                probs.append("  DECLARED  table #%d: w:tblW %d != grid %d" % (t["index"], t["tblW"], g))
            for r in t["rows"]:
                if r["cells"] + r["span"] != len(t["grid"]):
                    continue
                if abs(r["sum"] - g) > TOL:
                    probs.append("  CELLS     table #%d: a row's w:tcW sum %d != grid %d" % (t["index"], r["sum"], g))
                    break
        nested = len([t for t in tbls if t["depth"] > 0])
        if probs:
            print("FAIL  %s\n      column %d dxa | %d tables (%d nested)" % (f, column, len(tbls), nested))
            for p in probs:
                print(p)
            failed += 1
        else:
            print("ok    %s - column %d dxa, %d tables (%d nested), grid+cells sound" % (f, column, len(tbls), nested))
    if failed:
        print("\n%d file(s) failed the table-width invariant." % failed, file=sys.stderr)
        return 1
    print("\nTABLE WIDTH INVARIANT PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
