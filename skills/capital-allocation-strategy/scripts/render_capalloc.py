#!/usr/bin/env python3
"""
render_capalloc.py - branded InSync Analytics DOCX renderer for the Capital Allocation
ANALYSIS. Consumes the MERGED payload (computed.json fields + descriptive narrative +
sector profile) produced by run_pipeline.py.

Register (v2.0): a descriptive, rule-derived analysis. The prose DESCRIBES where capital
was allocated, by how much, how the mix changed, and whether it is consistent with stated
strategy. It does not editorialise. Every ratings judgment lives only in the rule-based
scorecard, and every score is shown with its supporting figures and its band (Appendix A).
All numeric cells come from the computed layer; no math here.

Usage (normally invoked by run_pipeline.py):
    python render_capalloc.py merged.json out.docx
"""
import re
import datetime as _dt
import json
import os
import sys

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

if HERE not in sys.path:
    sys.path.insert(0, HERE)
import compute_capalloc as CC

_xr = CC._xr
_sep = CC._sep


def _rsign(v, d):
    t = _xr(v, d)
    return t if t.startswith("-") else "+" + t



def _resolve_logo():
    for fn in ("insync-logo-mark.png", "insync_logo_v2.png", "insync_logo.png"):
        p = os.path.join(ROOT, "assets", fn)
        if os.path.exists(p):
            return p
    return os.path.join(ROOT, "assets", "insync-logo-mark.png")


LOGO = _resolve_logo()


def _logo_aspect(path):
    try:
        with open(path, "rb") as f:
            head = f.read(33)
        if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
            w = int.from_bytes(head[16:20], "big")
            h = int.from_bytes(head[20:24], "big")
            if h:
                return w / h
    except Exception:
        pass
    return 1.0


LOGO_ASPECT = _logo_aspect(LOGO)
LOGO_IS_LOCKUP = LOGO_ASPECT > 2.5

_QC_FULL_SET = 9

def _BRAND_JSON_PATH():
    for up in range(1, 4):
        cand = os.path.normpath(os.path.join(HERE, *([".."] * up), "common", "brand.json"))
        if os.path.exists(cand):
            return cand
    return os.path.join(HERE, "..", "common", "brand.json")


def _brand_colors():
    try:
        with open(_BRAND_JSON_PATH(), encoding="utf-8") as fh:
            return (json.load(fh).get("colors") or {})
    except Exception:
        return {}


_BRAND = _brand_colors()


def _hex_rgb(key, fallback):
    v = str(_BRAND.get(key) or fallback).lstrip("#")
    try:
        return RGBColor(int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
    except Exception:
        return RGBColor(int(fallback[0:2], 16), int(fallback[2:4], 16), int(fallback[4:6], 16))


def _hex_str(key, fallback):
    return str(_BRAND.get(key) or fallback).lstrip("#").upper()


ORANGE = _hex_rgb("orange", "E8850C")
GOLD = _hex_rgb("orange", "E8850C")
NAVY = _hex_rgb("navy", "232526")
SLATE = _hex_rgb("slate", "3D4550")
DARK = _hex_rgb("ink", "1A1A1A")
GREY = _hex_rgb("grayMid", "5A5A5A")
LIGHT = _hex_str("headerBg", "EDEDED")
ROWALT = _hex_str("rowAlt", "F5F0E8")
PANEL = _hex_str("calloutBg", "F6F3EE")
BAND = _hex_str("bandFill", "F0E9D8")
ACCENT = _hex_str("orange", "E8850C")
RULE = _hex_str("headerRule", "BBBBBB")
HAIR = _hex_str("rowRule", "EEEEEE")
GRID = _hex_str("tableBorder", "DDDDDD")
FRAME = _hex_str("mastBorder", "C9C9C9")
GOOD = _hex_rgb("beatGreen", "1E7A34")
BAD = _hex_rgb("missRed", "B02A1F")
MUTED = _hex_rgb("inlineGray", "6E6E6E")
FONT = "Georgia"
SANS = "Calibri"


def _dir_color(text, invert=False):
    s = str(text or "").strip()
    if not s or s in (DASH, "n/a", "-", "--"):
        return None
    neg = s.startswith("-") or s.startswith("\u2212") or s.startswith("(")
    pos = s.startswith("+")
    if not (neg or pos):
        return None
    favourable = neg if invert else pos
    return GOOD if favourable else BAD

DASH = "—"


def _score_band(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ("n/a", "n/a")
    if v >= 8:
        return ("8.0-10.0", "8.0-10.0")
    if v >= 6.5:
        return ("6.5-7.9", "6.5-7.9")
    if v >= 5:
        return ("5.0-6.4", "5.0-6.4")
    return ("below 5.0", "below 5.0")


def _score_color(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return GREY
    if v >= 8:
        return _hex_rgb("scoreStrong", "1E7A34")
    if v >= 6.5:
        return _hex_rgb("scoreSolid", "2E7D32")
    if v >= 5:
        return _hex_rgb("scoreAdequate", "8A6D1E")
    return _hex_rgb("scoreWeak", "B02A1F")


def _shade(cell, hex_fill):
    sh = OxmlElement("w:shd")
    sh.set(qn("w:val"), "clear")
    sh.set(qn("w:fill"), hex_fill)
    cell._tc.get_or_add_tcPr().append(sh)


def _cell_fill(cell):
    tcPr = cell._tc.find(qn("w:tcPr"))
    if tcPr is None:
        return None
    shd = tcPr.find(qn("w:shd"))
    if shd is None:
        return None
    v = shd.get(qn("w:fill"))
    return v.upper() if v else None


def _zebra(table):
    rows = table.rows
    if len(rows) < 3 or _cell_fill(rows[0].cells[0]) != LIGHT.upper():
        return
    seen = []
    for i in range(1, len(rows)):
        if i % 2 == 0:
            for cell in rows[i].cells:
                tc = cell._tc
                if any(tc is s_ for s_ in seen):
                    continue
                seen.append(tc)
                if _cell_fill(cell) is None:
                    _shade(cell, ROWALT)


def _cell_border(cell, edge, color, sz=4):
    tcPr = cell._tc.get_or_add_tcPr()
    b = tcPr.find(qn("w:tcBorders"))
    if b is None:
        b = OxmlElement("w:tcBorders"); tcPr.append(b)
    el = OxmlElement(f"w:{edge}"); el.set(qn("w:val"), "single")
    el.set(qn("w:sz"), str(sz)); el.set(qn("w:space"), "0"); el.set(qn("w:color"), color)
    b.append(el)


def _pin_plain(table, ncols):
    _fixed_layout(table)
    _set_grid(table, _fit([1] * ncols))


def _set_borders(table, color=GRID, sz=4):
    tbl = table._tbl
    borders = OxmlElement("w:tblBorders")
    edges = {"top": ("single", 6, RULE), "bottom": ("single", 6, RULE),
             "insideH": ("none", 0, color),
             "left": ("none", 0, color), "right": ("none", 0, color),
             "insideV": ("none", 0, color)}
    for edge, (val, esz, ecol) in edges.items():
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), val)
        el.set(qn("w:sz"), str(esz))
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), ecol)
        borders.append(el)
    tbl.tblPr.append(borders)


def _set_outer_borders(table, color=FRAME, sz=6):
    tbl = table._tbl
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), str(sz))
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), color)
        borders.append(el)
    for edge in ("insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "none")
        borders.append(el)
    tbl.tblPr.append(borders)


def _tblpr_insert(tblPr, el, tag):
    order = ["w:tblStyle", "w:tblpPr", "w:tblOverlap", "w:bidiVisual", "w:tblStyleRowBandSize",
             "w:tblStyleColBandSize", "w:tblW", "w:jc", "w:tblCellSpacing", "w:tblInd",
             "w:tblBorders", "w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook"]
    for ex in tblPr.findall(qn(tag)):
        tblPr.remove(ex)
    idx = order.index(tag)
    for child in tblPr:
        ctag = child.tag.split("}")[-1]
        cq = "w:" + ctag
        if cq in order and order.index(cq) > idx:
            child.addprevious(el)
            return
    tblPr.append(el)


def _pin_table(table, total_in=None):
    tblPr = table._tbl.tblPr
    if total_in is None:
        cols = table._tbl.findall(qn("w:tblGrid") + "/" + qn("w:gridCol"))
        total_in = (sum(int(c.get(qn("w:w"))) for c in cols) / 1440.0) if cols else CONTENT_W
    w = OxmlElement("w:tblW")
    w.set(qn("w:w"), str(int(round(total_in * 1440))))
    w.set(qn("w:type"), "dxa")
    _tblpr_insert(tblPr, w, "w:tblW")
    _left = 108
    _mar = tblPr.find(qn("w:tblCellMar"))
    if _mar is not None:
        _l = _mar.find(qn("w:left"))
        if _l is not None and _l.get(qn("w:w")) is not None:
            _left = int(_l.get(qn("w:w")))
    ind = OxmlElement("w:tblInd")
    ind.set(qn("w:w"), str(_left))
    ind.set(qn("w:type"), "dxa")
    _tblpr_insert(tblPr, ind, "w:tblInd")


def _fixed_layout(table):
    table.autofit = False
    table.allow_autofit = False
    tblPr = table._tbl.tblPr
    lay = OxmlElement("w:tblLayout")
    lay.set(qn("w:type"), "fixed")
    _tblpr_insert(tblPr, lay, "w:tblLayout")


_FLOW_ROWS = 8


def _page_break(doc):
    from docx.enum.text import WD_BREAK
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(0)
    p.add_run().add_break(WD_BREAK.PAGE)


def _tight_cell_margins(table, top=0, bottom=0):
    tblPr = table._tbl.tblPr
    for old in tblPr.findall(qn("w:tblCellMar")):
        tblPr.remove(old)
    mar = OxmlElement("w:tblCellMar")
    for edge, val in (("top", top), ("bottom", bottom)):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:w"), str(val)); el.set(qn("w:type"), "dxa")
        mar.append(el)
    tblPr.append(mar)


def _keep_together(table):
    rows = table.rows
    n = len(rows)
    for i, row in enumerate(rows):
        trPr = row._tr.get_or_add_trPr()
        if not trPr.findall(qn("w:cantSplit")):
            trPr.append(OxmlElement("w:cantSplit"))
        if i < n - 1:
            for cell in row.cells:
                for p in cell.paragraphs:
                    p.paragraph_format.keep_with_next = True


def _cell_lines(cell, lines, size=7.5, fill=None, bold_first_token=False, space=1):
    cell.text = ""
    if fill:
        _shade(cell, fill)
    for i, ln in enumerate(lines):
        p = cell.paragraphs[0] if i == 0 else cell.add_paragraph()
        p.paragraph_format.space_after = Pt(space); p.paragraph_format.space_before = Pt(space)
        if bold_first_token and "  " in ln:
            head, rest = ln.split("  ", 1)
            r = p.add_run(head + "  "); r.bold = True; r.font.name = SANS; r.font.size = Pt(size); r.font.color.rgb = DARK
            r2 = p.add_run(rest); r2.font.name = SANS; r2.font.size = Pt(size); r2.font.color.rgb = DARK
        else:
            r = p.add_run(ln); r.font.name = SANS; r.font.size = Pt(size); r.font.color.rgb = DARK


PAGE_MARGIN_IN = 0.50
CONTENT_W = 8.5 - 2 * PAGE_MARGIN_IN


def _fit(parts, frac=1.0):
    total = CONTENT_W * frac
    scale = total / float(sum(parts))
    return [p * scale for p in parts]


def _as_in(w):
    return w.inches if hasattr(w, "inches") else float(w)


def _set_grid(table, widths_in):
    tbl = table._tbl
    for g in tbl.findall(qn("w:tblGrid")):
        tbl.remove(g)
    grid = OxmlElement("w:tblGrid")
    for w in widths_in:
        gc = OxmlElement("w:gridCol")
        gc.set(qn("w:w"), str(int(w * 1440)))
        grid.append(gc)
    tbl.tblPr.addnext(grid)
    for row in table.rows:
        for c, w in zip(row.cells, widths_in):
            c.width = Inches(w)
    _pin_table(table, sum(widths_in))


def _orange_rule(doc, color="E8850C", sz=20):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(4)
    pPr = p._p.get_or_add_pPr()
    pbdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), str(sz))
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), color)
    pbdr.append(bottom)
    pPr.append(pbdr)
    return p


def _cell(cell, text, bold=False, color=DARK, size=9, align="left", font=SANS, fill=None):
    cell.text = ""
    p = cell.paragraphs[0]
    p.alignment = {"left": WD_ALIGN_PARAGRAPH.LEFT, "center": WD_ALIGN_PARAGRAPH.CENTER,
                   "right": WD_ALIGN_PARAGRAPH.RIGHT}[align]
    s = str(text)
    s = s if s == DASH else _clean(s)
    r = p.add_run(s)
    r.bold = bold
    r.font.size = Pt(size)
    r.font.name = font
    r.font.color.rgb = color
    if fill:
        _shade(cell, fill)
    for pp in cell.paragraphs:
        pp.paragraph_format.space_after = Pt(0.5)
        pp.paragraph_format.space_before = Pt(0.5)


def _outcome_direction(label):
    lab = str(label or "").lower()
    if "share count" in lab:
        return "down"
    if any(k in lab for k in ("roic", "roe", "total shareholder return", "growth",
                              "cagr", "capital returned")):
        return "up"
    return None


def _cell_dir(cell, text, invert=False, size=8.5, align="right", bold=False):
    col = _dir_color(text, invert=invert)
    _cell(cell, text, bold=bold, size=size, align=align, color=col or DARK)


def heading(doc, text, num=None):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(1)
    p.paragraph_format.keep_with_next = True
    p.paragraph_format.keep_together = True
    if num:
        rn = p.add_run(f"{num}   "); rn.bold = True; rn.font.name = SANS; rn.font.size = Pt(12); rn.font.color.rgb = NAVY
    rt = p.add_run(_clean(text)); rt.bold = True; rt.font.name = SANS; rt.font.size = Pt(12); rt.font.color.rgb = NAVY
    pPr = p._p.get_or_add_pPr()
    pbdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "8")
    bottom.set(qn("w:space"), "3")
    bottom.set(qn("w:color"), ACCENT)
    pbdr.append(bottom)
    pPr.append(pbdr)
    return p


def _clean(text):
    if not isinstance(text, str):
        return text
    t = text
    for dash in ("—", "–"):
        t = t.replace(" " + dash + " ", ", ")
        t = t.replace(dash, "-")
    t = t.replace(" ,", ",").replace(",,", ",").replace(", ,", ",")
    return t


def body(doc, text, size=9, italic=False, color=DARK, space=1.0):
    if not text:
        return
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(space)
    r = p.add_run(_clean(text))
    r.font.name = SANS
    r.font.size = Pt(size)
    r.italic = italic
    r.font.color.rgb = color
    return p


_DIM_LABEL = {"buybacks": "Buybacks", "dividends": "Dividends", "capex": "Capex",
              "m_and_a": "M&A", "balance_sheet": "Balance sheet",
              "capital_discipline": "Capital discipline", "value_creation": "Value creation"}


_WORDS = {1:"One",2:"Two",3:"Three",4:"Four",5:"Five",6:"Six",7:"Seven",8:"Eight",9:"Nine"}


def _andlist(items):
    items = [str(x) for x in items if str(x).strip()]
    return (items[0] if len(items) < 2
            else " and ".join([", ".join(items[:-1]), items[-1]]))


def _spell(n, cap=False):
    w = _WORDS.get(int(n)) if n is not None else None
    return (w if cap else (w.lower() if w else None)) or str(n)


def _fmtw(v):
    t = ("%g" % round(float(v), 4))
    return (t + ".0") if "." not in t else t


def ctrl_note(doc, label, text, size=7.5, space=1.0):
    if not text:
        return
    para = doc.add_paragraph()
    para.paragraph_format.space_after = Pt(space)
    r = para.add_run(_clean(label) + " ")
    r.font.name = SANS; r.font.size = Pt(size); r.bold = True; r.font.color.rgb = SLATE
    r = para.add_run(_clean(text))
    r.font.name = SANS; r.font.size = Pt(size); r.italic = True; r.font.color.rgb = SLATE
    return para


def _dedupe(text, seen):
    if not text:
        return ""
    keep = []
    for sent in re.split(r"(?<=[.;])\s+", str(text)):
        k = re.sub(r"[^a-z0-9]+", "", sent.lower())
        if not k or k in seen:
            continue
        seen.add(k); keep.append(sent.strip())
    return " ".join(keep)


def bullet(doc, text, size=9.5, lead=None):
    p = doc.add_paragraph(style=None)
    p.paragraph_format.left_indent = Inches(0.18)
    p.paragraph_format.space_after = Pt(0.5)
    if lead:
        r0 = p.add_run("•  ")
        r0.font.name = SANS; r0.font.size = Pt(size); r0.font.color.rgb = DARK
        rl = p.add_run(_clean(lead) + " ")
        rl.bold = True; rl.font.name = SANS; rl.font.size = Pt(size); rl.font.color.rgb = DARK
        r = p.add_run(_clean(text))
    else:
        r = p.add_run("•  " + _clean(text))
    r.font.name = SANS
    r.font.size = Pt(size)
    r.font.color.rgb = DARK
    return p


def kv_line(doc, label, value, vcolor=DARK, bold_value=False):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(2)
    r1 = p.add_run(label + ": ")
    r1.bold = True
    r1.font.name = SANS
    r1.font.size = Pt(9.5)
    r2 = p.add_run(str(value))
    r2.bold = bold_value
    r2.font.name = SANS
    r2.font.size = Pt(9.5)
    r2.font.color.rgb = vcolor
    return p


def subhead(doc, text, color=None):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(3)
    p.paragraph_format.space_after = Pt(1)
    p.paragraph_format.keep_with_next = True
    r = p.add_run(text)
    r.bold = True
    r.font.name = SANS
    r.font.size = Pt(9.5)
    r.font.color.rgb = color or SLATE
    return p


def _grey_bullet(doc, text, size=9):
    p = doc.add_paragraph(style=None)
    p.paragraph_format.left_indent = Inches(0.18)
    p.paragraph_format.space_after = Pt(0.5)
    r0 = p.add_run("•  "); r0.font.name = SANS; r0.font.size = Pt(size); r0.font.color.rgb = ORANGE
    r = p.add_run(_clean(text))
    r.font.name = SANS; r.font.size = Pt(size); r.font.color.rgb = DARK
    return p


def note(doc, text, space=1, max_bullets=3):
    if not text:
        return
    if isinstance(text, list):
        items = [str(x).strip() for x in text if str(x).strip()]
    else:
        s = str(text)
        parts, depth, buf = [], 0, ""
        for i, ch in enumerate(s):
            if ch in "([":
                depth += 1
            elif ch in ")]":
                depth = max(0, depth - 1)
            buf += ch
            nxt = s[i + 1] if i + 1 < len(s) else " "
            if depth == 0 and ch in ".;" and nxt == " " and len(buf.strip()) > 3:
                parts.append(buf.strip()); buf = ""
        if buf.strip():
            parts.append(buf.strip())
        items = [p.rstrip(".; ").strip() for p in parts if p.rstrip(".; ").strip()]
    for it in items[:max_bullets]:
        _grey_bullet(doc, it)
    return


def dtable(doc, headers, rows, widths, hsize=8, rsize=8, right_from=1, direction=None):
    t = doc.add_table(rows=1, cols=len(headers))
    t.alignment = WD_TABLE_ALIGNMENT.LEFT
    _set_borders(t)
    _fixed_layout(t)
    _win = [_as_in(w) for w in widths]
    _set_grid(t, _win)
    for i, h in enumerate(headers):
        _cell(t.rows[0].cells[i], h, bold=True, size=hsize, fill=LIGHT,
              align="right" if i >= right_from else "left")
    for ri, row in enumerate(rows):
        c = t.add_row().cells
        shade = ROWALT if ri % 2 == 1 else None
        _dir = direction(row[0]) if (direction and row) else None
        for i, val in enumerate(row):
            _col = None
            if _dir and i >= right_from:
                _col = _dir_color(val, invert=(_dir == "down"))
            _cell(c[i], val, size=rsize, align="right" if i >= right_from else "left",
                  fill=shade, color=_col or DARK)
    for rr in t.rows:
        for j, w in enumerate(_win):
            rr.cells[j].width = Inches(w)
    _keep_together(t)
    return t


def panel(doc, label, rows):
    t = doc.add_table(rows=0, cols=1); _pin_plain(t, 1)
    _set_outer_borders(t, color=BAND, sz=6)
    head = t.add_row().cells[0]
    _shade(head, PANEL)
    hp = head.paragraphs[0]; hp.paragraph_format.space_after = Pt(2)
    r = hp.add_run(label.upper()); r.bold = True; r.font.name = SANS; r.font.size = Pt(8); r.font.color.rgb = ORANGE
    for lab, val in rows:
        p = head.add_paragraph(); p.paragraph_format.space_after = Pt(1)
        if lab:
            rl = p.add_run(lab + ": "); rl.bold = True; rl.font.name = SANS; rl.font.size = Pt(9); rl.font.color.rgb = DARK
        rv = p.add_run(val); rv.font.name = SANS; rv.font.size = Pt(9); rv.font.color.rgb = DARK
    t.rows[0].cells[0].width = Inches(7.2)
    return t


def stat_strip(doc, items, value_size=15, label_size=7.5, top="med", bottom="med"):
    n = len(items)
    t = doc.add_table(rows=1, cols=n)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    _fixed_layout(t)
    _set_grid(t, _fit([1] * n))
    edge_spec = {"med": ("single", 6, RULE), "thin": ("single", 4, HAIR), "none": ("none", 0, "0")}
    tbl = t._tbl; borders = OxmlElement("w:tblBorders")
    spec = {"top": edge_spec[top], "bottom": edge_spec[bottom],
            "insideV": ("none", 0, "0"), "left": ("none", 0, "0"),
            "right": ("none", 0, "0"), "insideH": ("none", 0, "0")}
    for edge, (val, sz, col) in spec.items():
        el = OxmlElement(f"w:{edge}"); el.set(qn("w:val"), val)
        el.set(qn("w:sz"), str(sz)); el.set(qn("w:space"), "0"); el.set(qn("w:color"), col)
        borders.append(el)
    tbl.tblPr.append(borders)
    for i, (val, lab) in enumerate(items):
        c = t.rows[0].cells[i]; c.text = ""
        p = c.paragraphs[0]; p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(4); p.paragraph_format.space_after = Pt(0)
        r = p.add_run(val); r.bold = True; r.font.name = SANS; r.font.size = Pt(value_size); r.font.color.rgb = NAVY
        p2 = c.add_paragraph(); p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p2.paragraph_format.space_before = Pt(1); p2.paragraph_format.space_after = Pt(4)
        r2 = p2.add_run(lab); r2.font.name = SANS; r2.font.size = Pt(label_size); r2.font.color.rgb = GREY
    _keep_together(t)
    return t


def kpi_cards(doc, items):
    n = len(items)
    t = doc.add_table(rows=1, cols=n)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    _fixed_layout(t)
    _set_grid(t, _fit([1] * n))
    tbl = t._tbl; borders = OxmlElement("w:tblBorders")
    for edge in ("top", "bottom", "left", "right", "insideV"):
        el = OxmlElement(f"w:{edge}"); el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "4"); el.set(qn("w:space"), "0"); el.set(qn("w:color"), GRID)
        borders.append(el)
    e = OxmlElement("w:insideH"); e.set(qn("w:val"), "none"); borders.append(e)
    tbl.tblPr.append(borders)
    for i, (val, lab) in enumerate(items):
        c = t.rows[0].cells[i]; c.text = ""; _shade(c, PANEL)
        p = c.paragraphs[0]; p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(4); p.paragraph_format.space_after = Pt(0)
        r = p.add_run(val); r.bold = True; r.font.name = SANS
        r.font.size = Pt(12.5); r.font.color.rgb = NAVY
        p2 = c.add_paragraph(); p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p2.paragraph_format.space_before = Pt(1); p2.paragraph_format.space_after = Pt(4)
        r2 = p2.add_run(lab); r2.font.name = SANS; r2.font.size = Pt(7.5); r2.font.color.rgb = GREY
    _keep_together(t)
    return t


def kpi_boxes(doc, items):
    return kpi_cards(doc, items)


def focal_takeaways(doc, obs):
    t = doc.add_table(rows=1, cols=1); _fixed_layout(t); _set_grid(t, _fit([7.1]))
    tbl = t._tbl; bd = OxmlElement("w:tblBorders")
    for edge, val, sz, col in (("left", "single", 24, ACCENT), ("top", "single", 4, GRID),
                               ("bottom", "single", 4, GRID), ("right", "single", 4, GRID),
                               ("insideH", "none", 0, "0"), ("insideV", "none", 0, "0")):
        el = OxmlElement(f"w:{edge}"); el.set(qn("w:val"), val); el.set(qn("w:sz"), str(sz))
        el.set(qn("w:space"), "0"); el.set(qn("w:color"), col); bd.append(el)
    tbl.tblPr.append(bd)
    c = t.rows[0].cells[0]; _shade(c, PANEL); c.text = ""
    lp = c.paragraphs[0]; lp.paragraph_format.space_after = Pt(4); lp.paragraph_format.left_indent = Inches(0.1)
    r = lp.add_run("THREE KEY TAKEAWAYS"); r.bold = True; r.font.name = SANS; r.font.size = Pt(9); r.font.color.rgb = NAVY
    for i, o in enumerate(obs[:3]):
        lead = o.get("lead", "") if isinstance(o, dict) else ""
        txt = o.get("text", "") if isinstance(o, dict) else str(o)
        p = c.add_paragraph(); p.paragraph_format.space_after = Pt(4); p.paragraph_format.left_indent = Inches(0.1)
        rn = p.add_run(f"{i+1}   "); rn.bold = True; rn.font.name = SANS; rn.font.size = Pt(10.5); rn.font.color.rgb = ORANGE
        if lead:
            rl = p.add_run(_clean(lead) + " "); rl.bold = True; rl.font.name = SANS; rl.font.size = Pt(10); rl.font.color.rgb = NAVY
        rt = p.add_run(_clean(txt)); rt.font.name = SANS; rt.font.size = Pt(9.5); rt.font.color.rgb = DARK
    _keep_together(t)
    return t


def style_badge(doc, style_text, detail):
    subhead(doc, "Allocation style")
    t = doc.add_table(rows=1, cols=1); _fixed_layout(t); _set_grid(t, _fit([1], 3.1 / 7.1))
    _set_outer_borders(t, color=_hex_str("navy", "232526"), sz=6)
    c = t.rows[0].cells[0]; _shade(c, _hex_str("navy", "232526")); c.text = ""
    p = c.paragraphs[0]; p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(2); p.paragraph_format.space_after = Pt(2)
    r = p.add_run(style_text.upper()); r.bold = True; r.font.name = SANS; r.font.size = Pt(10.5)
    r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    _keep_together(t)
    if detail:
        body(doc, detail, size=8.5, italic=True, color=SLATE, space=3)


def callout(doc, label, text):
    t = doc.add_table(rows=1, cols=1); _fixed_layout(t); _set_grid(t, _fit([7.1]))
    tbl = t._tbl; bd = OxmlElement("w:tblBorders")
    for edge, val, sz, col in (("top", "single", 12, ACCENT), ("bottom", "single", 4, GRID),
                               ("left", "single", 4, GRID), ("right", "single", 4, GRID),
                               ("insideH", "none", 0, "0"), ("insideV", "none", 0, "0")):
        el = OxmlElement(f"w:{edge}"); el.set(qn("w:val"), val); el.set(qn("w:sz"), str(sz))
        el.set(qn("w:space"), "0"); el.set(qn("w:color"), col); bd.append(el)
    tbl.tblPr.append(bd)
    c = t.rows[0].cells[0]; _shade(c, PANEL); c.text = ""
    lp = c.paragraphs[0]; lp.paragraph_format.space_after = Pt(2); lp.paragraph_format.left_indent = Inches(0.08)
    r = lp.add_run(label.upper()); r.bold = True; r.font.name = SANS; r.font.size = Pt(8.5); r.font.color.rgb = ORANGE
    p = c.add_paragraph(); p.paragraph_format.left_indent = Inches(0.08); p.paragraph_format.space_after = Pt(2)
    rt = p.add_run(_clean(text)); rt.font.name = SANS; rt.font.size = Pt(9.5); rt.font.color.rgb = DARK
    _keep_together(t)
    return t



_MAST_BRAND_PT = 16.5
_MAST_BRAND_2L = 12.0
_MAST_LADDER = (14.0, 13.5, 13.0, 12.5, 12.0)
_MAST_TWO_PT = 10.0
_MARK_W_PT = 59 * 0.75
_MARK_H_PT = 46 * 0.75

_GEORGIA_BOLD_MILLE_SRC = [
    (254, ' \xa0'),
    (269, "'‘’"),
    (328, ',.'),
    (338, '·'),
    (344, 'l'),
    (346, 'j'),
    (354, 'iìíîï'),
    (367, ':;'),
    (376, '!¡'),
    (379, '-\xad'),
    (388, '|¦'),
    (393, 'f'),
    (397, 't'),
    (420, '°'),
    (446, 'IÌÍÎÏ'),
    (447, '()[]'),
    (472, '/\\\\'),
    (482, '*'),
    (490, '1'),
    (500, '`{}¨´¸'),
    (510, '\\"'),
    (513, 's'),
    (519, '“”'),
    (520, 'r'),
    (525, 'z'),
    (531, 'cç'),
    (548, '?¿'),
    (549, '¶'),
    (552, 'ª²³¹º'),
    (554, '7'),
    (562, 'yýÿ'),
    (563, '§'),
    (567, 'v'),
    (572, 'eèéêë'),
    (577, 'g'),
    (588, 'x'),
    (595, 'J'),
    (596, 'aàáâãäå'),
    (599, '5'),
    (605, '¢'),
    (610, '«»'),
    (625, '3'),
    (626, '2'),
    (632, 'k'),
    (636, 'oòóôõöø'),
    (638, 'ð'),
    (641, '$'),
    (645, 'þ'),
    (646, 'b'),
    (648, '69q'),
    (649, '4S'),
    (658, 'pß'),
    (663, 'd'),
    (668, 'µ'),
    (671, 'F'),
    (676, '8'),
    (677, 'uùúûü'),
    (680, 'h'),
    (684, 'T'),
    (686, 'L'),
    (689, 'Z'),
    (690, 'n£ñ'),
    (701, '0P'),
    (703, '#+<=>^_~¤¬¯±×÷–−'),
    (708, 'Þ'),
    (715, 'CÇ€'),
    (721, 'EÈÉÊË'),
    (732, 'Y¥Ý'),
    (757, 'B'),
    (758, 'AÀÁÂÃÄÅ'),
    (762, 'V'),
    (797, 'R'),
    (799, '&'),
    (807, 'G'),
    (809, 'X'),
    (817, 'K'),
    (820, 'OQÒÓÔÕÖØ'),
    (833, 'UÙÚÛÜ'),
    (834, 'DÐ'),
    (839, 'NÑ'),
    (857, 'æ'),
    (863, 'w'),
    (879, '%'),
    (913, 'H'),
    (928, '—'),
    (942, '©®'),
    (967, '@'),
    (1016, 'm'),
    (1023, 'M'),
    (1070, 'Æ'),
    (1071, '¼½¾'),
    (1126, 'W'),
]
GLYPH_MILLE = {c: v for v, chars in _GEORGIA_BOLD_MILLE_SRC for c in chars}
GLYPH_DEFAULT = 669


_SUBSTITUTE_WIDTH_FACTOR = 1.12


def _resolve_face_scale():
    forced = os.environ.get("INSYNC_FACE_WIDTHS")
    if forced == "georgia":
        return 1.0
    if forced == "substitute":
        return _SUBSTITUTE_WIDTH_FACTOR
    want, accepted = "Georgia", ["Georgia"]
    try:
        for up in range(1, 4):
            cand = os.path.normpath(os.path.join(HERE, *([".."] * up), "common", "brand.json"))
            if not os.path.exists(cand):
                continue
            with open(cand, encoding="utf-8") as fh:
                m = json.load(fh).get("masthead", {})
            product = os.path.basename(os.path.dirname(os.path.dirname(cand)))
            want = m.get("face_overrides", {}).get(product) or m.get("face", "Georgia")
            accepted = [want] + list(m.get("face_accepted_substitutes", {}).get(want, []))
            break
    except Exception:
        pass
    try:
        import subprocess
        got = subprocess.run(["fc-match", "-f", "%{family}", want + ":bold"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return 1.0
    if not got:
        return 1.0
    flat = got.replace(" ", "").lower()
    if any(a.replace(" ", "").lower() in flat for a in accepted):
        return 1.0
    sys.stderr.write(
        "[masthead] %r is NOT installed on this box - fontconfig resolves it to %r, which is "
        "not a metric-faithful stand-in. Sizing the company line for the substitute so it "
        "cannot wrap. Install %s to render at the intended size.\n" % (want, got, want))
    return _SUBSTITUTE_WIDTH_FACTOR


_FACE_SCALE = _resolve_face_scale()


def _text_w_pt(text, pt):
    return sum(GLYPH_MILLE.get(c, GLYPH_DEFAULT) for c in text) * pt * _FACE_SCALE / 1000.0


def _exact_line(pf, pts):
    pf.line_spacing = Pt(pts)
    pf.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)


def _split_two(text, budget_pt):
    cap = budget_pt * 0.98
    words = text.split(" ")
    best = None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        wa, wb = _text_w_pt(a, _MAST_TWO_PT), _text_w_pt(b, _MAST_TWO_PT)
        if wa > cap or wb > cap:
            continue
        if best is None or abs(wa - wb) < best[0]:
            best = (abs(wa - wb), a, b)
    if best is None:
        for i in range(1, len(text)):
            a, b = text[:i], text[i:]
            wa, wb = _text_w_pt(a, _MAST_TWO_PT), _text_w_pt(b, _MAST_TWO_PT)
            if wa > cap or wb > cap:
                continue
            if best is None or abs(wa - wb) < best[0]:
                best = (abs(wa - wb), a, b)
    return (best[1], best[2]) if best else (text, "")


def title_band(doc, meta, subtitle=""):
    period = f"FY{meta.get('period_first','')}-FY{meta.get('period_latest','')}"
    company = str(meta.get("company_name", "") or "")
    ticker = str(meta.get("ticker", "") or "")
    suffix = f" ({ticker})" if ticker else ""

    _prep = meta.get("prepared_date") or _dt.date.today().isoformat()
    right_need = max(_text_w_pt("Capital Allocation", 14.0),
                     _text_w_pt("Analysis", 14.0),
                     _text_w_pt("Prepared: " + str(_prep), 8.5),
                     _text_w_pt("Analysis period: " + period, 8.5)) / 72.0
    logo_w = (_MARK_W_PT + 14.0) / 72.0
    right_w = min(2.75, max(1.85, right_need + 0.18))
    mid_w = CONTENT_W - logo_w - right_w
    budget = (mid_w - 0.10) * 72.0

    fits = lambda t, pt: _text_w_pt(t, pt) <= budget * 0.97
    co_pt = _MAST_LADDER[-1]
    for _pt in _MAST_LADDER:
        co_pt = _pt
        if fits(company + suffix, _pt):
            break
    two = not fits(company + suffix, co_pt)
    l1, l2 = company + suffix, None
    if two:
        co_pt = _MAST_TWO_PT
        l1, l2 = _split_two(company + suffix, budget)
    brand_pt = _MAST_BRAND_2L if two else _MAST_BRAND_PT

    drop = _MAST_LADDER[0] - co_pt
    brand_lead = _MAST_BRAND_2L * 1.34 if two else 20.4
    co_lead = 10.965 if two else (18.09 + 0.28 * drop)
    mark_up = 0.0 if two else (0.53 + 0.2867 * drop)

    t = doc.add_table(rows=1, cols=3)
    t.alignment = WD_TABLE_ALIGNMENT.LEFT
    _set_outer_borders(t, color=FRAME, sz=8)
    _fixed_layout(t)
    _set_grid(t, [logo_w, mid_w, right_w])
    logo, brand, right = t.rows[0].cells
    logo.vertical_alignment = WD_ALIGN_VERTICAL.BOTTOM
    brand.vertical_alignment = WD_ALIGN_VERTICAL.BOTTOM
    right.vertical_alignment = WD_ALIGN_VERTICAL.CENTER

    lp = logo.paragraphs[0]
    lp.alignment = WD_ALIGN_PARAGRAPH.LEFT
    lp.paragraph_format.space_before = Pt(0)
    lp.paragraph_format.space_after = Pt(mark_up)
    _drew_mark = False
    if os.path.exists(LOGO):
        try:
            lp.add_run().add_picture(LOGO, width=Pt(_MARK_W_PT), height=Pt(_MARK_H_PT))
            _drew_mark = True
        except Exception:
            _drew_mark = False
    if not _drew_mark:
        _wm = lp.add_run("IS")
        _wm.bold = True
        _wm.font.name = FONT
        _wm.font.size = Pt(_MARK_H_PT * 0.62)
        _wm.font.color.rgb = ORANGE

    brand.text = ""
    b1 = brand.paragraphs[0]
    b1.alignment = WD_ALIGN_PARAGRAPH.LEFT
    _exact_line(b1.paragraph_format, brand_lead)
    r = b1.add_run("InSync"); r.bold = True; r.font.name = FONT
    r.font.size = Pt(brand_pt); r.font.color.rgb = ORANGE
    r = b1.add_run(" Analytics"); r.font.name = FONT
    r.font.size = Pt(brand_pt); r.font.color.rgb = DARK

    def _co_para(text):
        p = brand.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        _exact_line(p.paragraph_format, co_lead)
        tok = f"({ticker})" if ticker else ""
        idx = text.find(tok) if tok else -1
        segs = ([(text, DARK)] if idx < 0 else
                [(text[:idx], DARK), (tok, ORANGE), (text[idx + len(tok):], DARK)])
        for seg, col in segs:
            if not seg:
                continue
            rr = p.add_run(seg); rr.bold = True; rr.font.name = FONT
            rr.font.size = Pt(co_pt); rr.font.color.rgb = col
        return p

    _co_para(l1)
    if two:
        _co_para(l2)

    right.text = ""
    rp = right.paragraphs[0]
    rp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    rp.paragraph_format.space_after = Pt(2)
    r = rp.add_run("Capital Allocation"); r.bold = True; r.font.name = FONT
    r.font.size = Pt(14); r.font.color.rgb = DARK
    r.add_break()
    r2 = rp.add_run("Analysis"); r2.bold = True; r2.font.name = FONT
    r2.font.size = Pt(14); r2.font.color.rgb = DARK
    for lab, val in (("Prepared: ", str(_prep)), ("Analysis period: ", period)):
        p = right.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        p.paragraph_format.space_after = Pt(0)
        r = p.add_run(lab); r.bold = True; r.font.name = FONT
        r.font.size = Pt(8.5); r.font.color.rgb = SLATE
        r = p.add_run(val); r.font.name = FONT
        r.font.size = Pt(8.5); r.font.color.rgb = MUTED

    _orange_rule(doc, color="E8850C", sz=16)


def _methodology_dims(C=None):
    order = ["buybacks", "dividends", "m_and_a", "capex", "balance_sheet",
             "capital_discipline", "value_creation"]
    out = []
    for key in order:
        d = CC.SCORE_RULES[key]
        style = d.get("style", "additive")
        _tmpl = ((C or {}).get("routing") or {}).get("template") or ""
        _skey = ((C or {}).get("meta") or {}).get("sector_key") or ""
        _v = d.get("variant") or {}
        _vlabel = str(_v.get("label", "")).lower()
        _applies = (("midstream" in _vlabel and _tmpl == "ENERGY_MIDSTREAM")
                    or ("insurance" in _vlabel and (_skey == "Insurance"
                                                    or _tmpl.startswith("INSURANCE"))))
        rules = list(d["rules"]) + (list(_v.get("rules", [])) if _applies else [])

        def _fmt(r):
            if r["delta"] is None:
                return r["text"]
            if style == "set":
                return re.sub(r"^sets\s+[-\d.]+\s*-\s*", "%s: " % r["delta"],
                              str(r["text"])).strip()
            dl = _rsign(r["delta"], 1) if (isinstance(r["delta"], float)
                                            and r["delta"] != int(r["delta"])) else \
                 "%+d" % r["delta"]
            return "%s %s" % (dl, r["text"])

        if style == "ungraded":
            raises = ["Not graded - returns %s for every company" % d["base"]]
            lowers = ["Nothing moves the score; the figures are measured and printed only"]
        elif style == "set":
            _pos = [r for r in rules if isinstance(r["delta"], (int, float))
                    and r["delta"] >= d["base"]]
            _neg = [r for r in rules if isinstance(r["delta"], (int, float))
                    and r["delta"] < d["base"]]
            raises = [_fmt(r) for r in _pos] or ["n/a"]
            lowers = ([_fmt(r) for r in _neg]
                      + [r["text"] for r in rules if r["delta"] is None]) or ["n/a"]
        else:
            raises = [_fmt(r) for r in rules
                      if isinstance(r["delta"], (int, float)) and r["delta"] > 0] or ["n/a"]
            lowers = [_fmt(r) for r in rules
                      if isinstance(r["delta"], (int, float)) and r["delta"] < 0] or ["n/a"]
        out.append((d["label"], key, list(d["measures"]),
                    ["Higher: " + "; ".join(raises), "Lower: " + "; ".join(lowers)]))
    return out


METHODOLOGY_DIMS = _methodology_dims()


def _score_txt(v, suffix="/10"):
    return "Withheld" if v is None else f"{v}{suffix}"



_NUM_RX = re.compile(r"-?\d[\d,]*\.?\d*\s*(?:%|x|pp|B|M|bn|m)?", re.I)



def _as_dict(v):
    return v if isinstance(v, dict) else {}


def _as_list(v):
    return v if isinstance(v, list) else []


def _norm_fig(v):
    return re.sub(r"[\s,$+\-]", "", str(v)).lower()


def _drop_restatements(text, table_values):
    if not text:
        return text
    def _norm(v):
        return re.sub(r"[\s,$+\-]", "", str(v)).lower()

    seen = {_norm(v) for v in table_values if v not in (None, "")}
    if not seen:
        return text
    kept = []
    for sent in re.split(r"(?<=[.;])\s+", str(text)):
        nums = [_norm(n) for n in _NUM_RX.findall(sent)]
        nums = [n for n in nums if any(c.isdigit() for c in n)]
        if len(nums) >= 2 and all(n in seen for n in nums):
            continue
        kept.append(sent)
    return " ".join(kept).strip()


def _sharechg(s):
    if s is None:
        return "n/a"
    return str(s).strip()


def _reduction(s):
    return _sharechg(s)


def _reduction_legacy(s):
    if s is None:
        return "n/a"
    s = str(s).strip()
    if s.startswith("-"):
        return s[1:]
    if s.startswith("+"):
        return s[1:] + " (increase)"
    return s


_MATERIALITY_WORDING = {
    "cumulative operating cash flow": ("Acquisition spend as % of operating cash flow",
                                       "Cumulative OCF"),
    "cumulative capital deployed":    ("Acquisition spend as % of capital deployed "
                                       "(materiality test)", "Cumulative capital deployed"),
}


def render(m, out_path):
    C = m["computed"]
    N = m.get("narrative", {})
    S = m.get("sector", {})
    meta = C["meta"]
    weights = m.get("weights", {})
    overall = m.get("overall", "n/a")
    rating = m.get("rating", "")
    band = m.get("band", "")
    period = f"FY{meta.get('period_first','')}-FY{meta.get('period_latest','')}"

    doc = Document()
    _normal = doc.styles["Normal"]
    _normal.font.name = SANS; _normal.font.size = Pt(9)
    _npf = _normal.paragraph_format
    _npf.line_spacing = 1.0
    _npf.space_before = Pt(0); _npf.space_after = Pt(0)
    sec = doc.sections[0]
    sec.top_margin = Inches(PAGE_MARGIN_IN); sec.bottom_margin = Inches(PAGE_MARGIN_IN)
    sec.left_margin = Inches(PAGE_MARGIN_IN); sec.right_margin = Inches(PAGE_MARGIN_IN)

    mq = C.get("monitor", {})
    if mq.get("available") and mq.get("quarters"):
        meta["latest_quarter"] = mq["quarters"][-1].get("label", "")
    title_band(doc, meta, subtitle=S.get("label", ""))

    peers = [x.get("name", "") for x in C["peers"].get("rows", [])] if C["peers"].get("available") else []
    _vs = "versus stated strategy and peers" if peers else "versus stated strategy"
    _pline = ("Peers: " + ", ".join(peers) if peers else "Peer comparison not performed")
    scope = N.get("scope") or (
        f"Scope: describes where {meta.get('company_name','the company')} allocated capital over {period}, by how "
        f"much, and how the mix changed, {_vs}. Reported and derived figures only; no "
        f"recommendation or forecast. {_pline}. Source: InSync.")
    body(doc, scope, size=8, italic=True, color=SLATE, space=3)

    ms = C.get("market_snapshot", {})
    b = C["buybacks"]; dv = C["dividends"]; bs = C["balance_sheet"]
    cl = C["classification"]; vc = C["value_creation"]
    lev_label = (bs.get("leverage_metric_resolved")
                 or S.get("leverage_metric") or "Net debt / EBITDA")
    _fin_key = C.get("meta", {}).get("sector_key")
    _fin_noun = "bank" if _fin_key == "Banks" else "insurance" if _fin_key == "Insurance" else None

    def _na_sec(v):
        return (f"not applicable ({_fin_noun})"
                if (_fin_noun and str(v).strip().lower().startswith("n/a")) else v)

    nscored = sum(1 for k in weights if weights.get(k))

    heading(doc, "Executive summary", 1)

    _hv = str(N.get("headline_verdict", "") or "").strip()
    if _hv:
        _hp = doc.add_paragraph()
        _hp.paragraph_format.space_before = Pt(1); _hp.paragraph_format.space_after = Pt(3)
        _hr = _hp.add_run(_clean(f"{meta.get('ticker','')}: {_hv}"))
        _hr.bold = True; _hr.font.name = SANS; _hr.font.size = Pt(10.5); _hr.font.color.rgb = NAVY

    if vc.get("sector_dashboard"):
        kpi_boxes(doc, list(vc["sector_dashboard"]))
    else:
        kpi_boxes(doc, [
            (cl["reinvestment_pct"], "Reinvestment of capital deployed"),
            (vc["roic"], f"ROIC vs {vc['cost_of_capital']} hurdle"),
            (ms.get("total_shareholder_yield", "n/a"), "Total shareholder yield"),
            (_score_txt(overall, " / 10") + (" (provisional)" if
                 (C.get("score_coverage") or {}).get("publication_status") == "provisional"
                 and overall is not None else ""),
             ("Descriptive capital-allocation score (band %s)" % band) if overall is not None
             else "Descriptive capital-allocation score: Withheld"),
        ])

    hp = doc.add_paragraph(); hp.paragraph_format.space_before = Pt(8); hp.paragraph_format.space_after = Pt(3)
    hp.paragraph_format.keep_with_next = True
    pPr = hp._p.get_or_add_pPr(); pbdr = OxmlElement("w:pBdr")
    for edge in ("top", "bottom"):
        e = OxmlElement(f"w:{edge}"); e.set(qn("w:val"), "single"); e.set(qn("w:sz"), "8")
        e.set(qn("w:space"), "4"); e.set(qn("w:color"), ACCENT); pbdr.append(e)
    pPr.append(pbdr)
    r = hp.add_run("Market snapshot" + (" (%s)" % meta["price_date"]
                                        if meta.get("price_date") else ""))
    r.bold = True; r.font.name = SANS; r.font.size = Pt(10.5); r.font.color.rgb = ORANGE

    _snap = vc.get("sector_snapshot") or {}
    _yld_label = _snap.get("yield_label", "FCF yield")
    _yld_value = _snap.get("yield_value", ms.get("fcf_yield", "n/a"))
    _fin_sector = (S.get("label", "").startswith("Banks") or C.get("meta", {}).get("sector_key") in ("Banks", "Insurance"))
    _na_yield = "not applicable (bank)" if _fin_sector else "n/a"
    _yv = _yld_value if not str(_yld_value).lower().startswith("n/a") else _na_yield
    _lev_label = _snap.get("capital_label") or S.get("leverage_metric") or "Leverage (ND/EBITDA)"
    _lev_val = _snap.get("capital_value") or bs.get("leverage_nd_ebitda", "n/a")
    if _fin_sector and str(_lev_val).lower().startswith("n/a"):
        _lev_val = "not applicable (bank)"
    msr = [("Share price", ms.get("price", "n/a"), "Dividend yield", dv.get("dividend_yield", "n/a")),
           ("Market cap", meta.get("market_cap", "n/a"), "Buyback yield", b.get("buyback_yield", "n/a")),
           (_yld_label, _yv, _lev_label, _lev_val)]
    mt = doc.add_table(rows=len(msr), cols=4)
    _fixed_layout(mt); _set_grid(mt, _fit([1.55, 1.75, 1.95, 1.85]))
    tbl = mt._tbl; bd = OxmlElement("w:tblBorders")
    for edge, val, sz, col in (("top", "none", 0, "0"), ("bottom", "single", 8, ACCENT),
                               ("insideH", "single", 4, HAIR), ("left", "none", 0, "0"),
                               ("right", "none", 0, "0"), ("insideV", "none", 0, "0")):
        el = OxmlElement(f"w:{edge}"); el.set(qn("w:val"), val); el.set(qn("w:sz"), str(sz))
        el.set(qn("w:space"), "0"); el.set(qn("w:color"), col); bd.append(el)
    tbl.tblPr.append(bd)
    for i, (a, av, c2, cv) in enumerate(msr):
        row = mt.rows[i].cells
        _cell(row[0], a, size=8.5, bold=True, color=DARK)
        _cell(row[1], av, size=8.5, align="right", color=DARK)
        _cell(row[2], c2, size=8.5, bold=True, color=DARK)
        _cell(row[3], cv, size=8.5, align="right", color=DARK)
        _cell_border(row[1], "right", GRID, 4)
    _keep_together(mt)

    ap = doc.add_paragraph(); ap.paragraph_format.space_before = Pt(6); ap.paragraph_format.space_after = Pt(1)
    r = ap.add_run("ALLOCATION STYLE   "); r.bold = True; r.font.name = SANS; r.font.size = Pt(8.5); r.font.color.rgb = SLATE
    r = ap.add_run(cl["label"]); r.bold = True; r.font.name = SANS; r.font.size = Pt(11); r.font.color.rgb = NAVY
    if str((C.get("m_and_a") or {}).get("acq_total_5y", "n/a")).lower().startswith("n/a") \
            and str(cl.get("label", "")).lower() != "withheld":
        import data_coverage as _DCR
        _cb = (C.get("data_coverage") or {}).get("corroboration_basis") or {}
        _trace = " " + _DCR.absence_trace_sentence(_cb)
        body(doc, "Acquisitions are not reported, so they sit outside these percentages. "
                  "M&A is unscored and its weight removed, not assumed." + _trace,
             size=8, italic=True, color=SLATE, space=2)
    if str(cl.get("label", "")).lower() == "withheld" and cl.get("basis"):
        body(doc, cl["basis"], size=8.5, italic=True, color=SLATE, space=3)
    else:
        _dr = str(cl.get("debt_reduction_pct") or "").strip().lower()
        _dr_clause = ("" if (not _dr or _dr.startswith("n/a"))
                      else f" and {cl['debt_reduction_pct']} debt reduction")
        body(doc, f"Reinvestment {_na_sec(cl['reinvestment_pct'])} of capital deployed vs "
                  f"{cl['shareholder_return_pct']} shareholder returns{_dr_clause} (Section 2).",
             size=8.5, italic=True, color=SLATE, space=3)

    focal_takeaways(doc, N.get("key_observations") or [])

    _tk_vals = []
    for _o in (N.get("key_observations") or [])[:3]:
        _tk_vals.append(_o.get("text", "") if isinstance(_o, dict) else str(_o))
        if isinstance(_o, dict) and _o.get("lead"):
            _tk_vals.append(_o["lead"])
    _tk_nums = [n for _t in _tk_vals for n in _NUM_RX.findall(_t)]
    _concl = (N.get("overall_conclusion", "") or "").strip()
    _cn = [_norm_fig(n) for n in _NUM_RX.findall(_concl)]
    _cn = [n for n in _cn if any(ch.isdigit() for ch in n)]
    _tn = {_norm_fig(n) for n in _tk_nums}
    _derivative = bool(_cn) and len(_cn) >= 2 and all(n in _tn for n in _cn)
    if _concl and not _derivative:
        callout(doc, "Overall conclusion", _concl)

    heading(doc, "Allocation mix and classification", 2)
    _amt = dtable(doc, ["Use of capital (%s)" % cl.get("period_basis", "cumulative"),
                        "Amount", "Share of capital deployed"], [
        ["Reinvestment (capital expenditure only; acquisition line not reported)"
         if str((C.get("m_and_a") or {}).get("acq_total_5y", "n/a")).lower().startswith("n/a")
         else "Reinvestment (capital expenditure and acquisitions)",
         _na_sec(cl.get("reinvestment_amt", "n/a")), _na_sec(cl["reinvestment_pct"])],
        ["      of which: capital expenditure",
         _na_sec(cl.get("capex_amt", "n/a")), ""],
        ["      of which: acquisitions",
         _na_sec(cl.get("acquisition_amt", "n/a")), _na_sec(cl["acquisition_pct"])],
        ["Shareholder returns (buybacks and dividends)",
         cl.get("shareholder_return_amt", "n/a"), cl["shareholder_return_pct"]],
        ["      of which: buybacks", cl.get("buyback_amt", "n/a"), ""],
        ["      of which: dividends", cl.get("dividend_amt", "n/a"), ""],
        ["Debt reduction", cl.get("debt_reduction_amt", "n/a"), cl["debt_reduction_pct"]],
        ["Total capital deployed, cumulative (excl. unreported acquisitions)"
         if str((C.get("m_and_a") or {}).get("acq_total_5y","n/a")).lower().startswith("n/a")
         else "Total capital deployed, cumulative", cl.get("total_deployed_amt", "n/a"),
         cl.get("total_deployed_pct", "100.0%")],
    ], [Inches(w) for w in _fit([3.5, 1.5, 2.2])], right_from=1)
    if _amt is not None:
        for _ri, _row in enumerate(_amt.rows):
            _lbl = _row.cells[0].text.strip()
            if _lbl.startswith("of which"):
                for rn in _row.cells:
                    for pp in rn.paragraphs:
                        for rr in pp.runs:
                            rr.italic = True; rr.font.color.rgb = SLATE
        for rn in _amt.rows[-1].cells:
            for pp in rn.paragraphs:
                for rr in pp.runs:
                    rr.bold = True; rr.font.color.rgb = NAVY
    body(doc, "Classification rule: " + cl.get("rule", ""), size=8, italic=True, color=SLATE, space=2)

    heading(doc, "Deployment trend and mix shift", 3)
    sn = C["snapshot"]
    _ocf_raw = sn["operating_cash_flow"].get("raw")
    _ocf_txt = str(sn["operating_cash_flow"]["value"]).lower()
    _ocf_missing = _ocf_raw in (None, 0) or _ocf_txt.startswith(("n/a", "not applicable"))
    dep_raw = sn["total_deployed"].get("raw") or 0
    cur = meta.get("currency", "$")
    _skey_dep = C.get("meta", {}).get("sector_key")
    _fin_regime = _skey_dep in ("Banks", "Insurance")

    _ocf_negative = (not _ocf_missing) and _ocf_raw is not None and _ocf_raw < 0

    def _pofo(x):
        return (f"{_xr(x/_ocf_raw*100, 1)}%"
                if not (_ocf_missing or _ocf_negative) else "n/a")

    def _usd(v):
        return CC.fmt_usd(v, cur) if v is not None else "n/a"

    if _fin_regime or _ocf_missing:
        _noun = ("bank" if _skey_dep == "Banks"
                 else "insurance" if _skey_dep == "Insurance" else None)
        subhead(doc, f"FY{sn['period']} capital deployed")
        dtable(doc, ["FY deployment", "Value", "Basis"], [
            ["Total capital deployed, latest FY", sn["total_deployed"]["value"],
             "Cash uses, latest fiscal year"],
            ["Capital expenditure", _na_sec(C["capex"]["capex_latest"]), "Latest fiscal year"],
            ["Retained capital",
             ("Not available under the current %s data framework." % _noun) if _noun
             else "Not available: operating cash flow is not reported for the period.",
             "Requires a validated capital-generation measure"],
        ], [Inches(w) for w in _fit([3.5, 2.2, 1.5])], right_from=1)
        if _noun:
            body(doc, "Operating cash flow is not a measure of distributable capital for this "
                      "sector, so deployment is not expressed as a percentage of it. Capital "
                      "generation is assessed through earnings and regulatory capital below.",
                 size=8, italic=True, color=SLATE, space=1)
    else:
        retained = _ocf_raw - dep_raw
        subhead(doc, f"FY{sn['period']} deployment against cash generated")
        _dep_rows = [
            ["Operating cash flow", sn["operating_cash_flow"]["value"], "100.0%"],
            ["Total cash uses, latest FY%s" % (" (including cash build)"
                                               if sn.get("cash_built") else ""),
             sn["total_deployed"]["value"], _pofo(dep_raw)],
        ]
        _USE_LABEL = {"Capex": "Capital expenditure", "Buybacks": "Buybacks",
                      "Dividends": "Dividends", "M&A": "Acquisitions",
                      "Debt Reduction": "Debt reduction", "Cash Build": "Cash build"}
        for _u in sn.get("rows", []):
            if _u.get("raw"):
                _dep_rows.append(["   of which: " + _USE_LABEL.get(_u["use"], _u["use"]),
                                  _u["value"], _pofo(_u["raw"])])
        dtable(doc, ["FY deployment", "Value", "% of operating cash flow"], _dep_rows + [
            [("Funding gap (capital deployed less operating cash flow)"
              if (retained is not None and retained < 0) else
              "Retained (operating cash flow less capital deployed)"),
             _usd(abs(retained) if (retained is not None and retained < 0) else retained),
             _pofo(abs(retained) if (retained is not None and retained < 0) else retained)],
        ], [Inches(w) for w in _fit([3.5, 1.7, 2.0])], right_from=1)
        if _ocf_negative:
            body(doc, "Operating cash flow was negative in the latest fiscal year, so each use is "
                      "shown as a dollar amount only: a share of a cash outflow inverts the sign "
                      "and reads as the opposite of what it measures.",
                 size=8, italic=True, color=SLATE, space=1)
        _dt = doc.tables[-1]
        for _row in _dt.rows:
            if _row.cells[0].text.strip().startswith("of which"):
                for _cl in _row.cells:
                    for _pp in _cl.paragraphs:
                        for _rr in _pp.runs:
                            _rr.italic = True
                            _rr.font.color.rgb = SLATE
        note(doc, N.get("deployment_commentary", ""))

    mon = C.get("monitor", {})
    if mon.get("available"):
        subhead(doc, "Quarterly deployment monitor")
        _ocf_ratio_cols = mon.get("ocf_ratios", True)
        _hdrs = ["Quarter", "Operating cash flow", "Capex", "Buybacks", "Dividends"]
        _keys = ("ocf", "capex", "bb", "div")
        if _ocf_ratio_cols:
            _hdrs.append("Capex / OCF")
            _keys = _keys + ("reinv",)
        t = doc.add_table(rows=1, cols=len(_hdrs)); _set_borders(t); _pin_plain(t, len(_hdrs))
        for i, h in enumerate(_hdrs):
            _cell(t.rows[0].cells[i], h, bold=True, size=8, fill=LIGHT, align="left" if i == 0 else "right")
        for q in mon["quarters"][-6:]:
            c = t.add_row().cells
            _cell(c[0], q["label"], size=8)
            for j, k in enumerate(_keys, start=1):
                _cell(c[j], q[k], size=8, align="right")
        _set_grid(t, _fit([1.0, 1.5, 1.2, 1.2, 1.2, 1.1][:len(_hdrs)]))

        if mon.get("ocf_ratio_note"):
            body(doc, mon["ocf_ratio_note"], size=8, italic=True, color=SLATE, space=1)
        note(doc, N.get("quarterly_commentary", ""))
        subhead(doc, f"Twelve-month comparison (prior LTM {mon['prior_label']} vs current LTM {mon['ltm_label']})")
        t = doc.add_table(rows=1, cols=4); _set_borders(t); _pin_plain(t, 4)
        for i, h in enumerate(["Use of operating cash flow", "Prior LTM", "Current LTM", "Change"]):
            _cell(t.rows[0].cells[i], h, bold=True, size=8.5, fill=LIGHT, align="left" if i == 0 else "right")
        for row in mon["rows"]:
            c = t.add_row().cells
            _cell(c[0], row["metric"], size=8.5)
            _cell(c[1], row["prior"], size=8.5, align="right")
            _cell(c[2], row["ltm"], size=8.5, align="right")
            _m = str(row.get("metric", "")).lower()
            if any(k in _m for k in ("operating cash", "free cash", "cash generated")):
                _cell_dir(c[3], row["chg"])
            elif any(k in _m for k in ("net debt", "leverage", "payout")):
                _cell_dir(c[3], row["chg"], invert=True)
            else:
                _cell(c[3], row["chg"], size=8.5, align="right")
        _set_grid(t, _fit([2.8, 1.5, 1.5, 1.4]))
        note(doc, N.get("shift_commentary", ""))

    heading(doc, "Shareholder returns", 4)
    pm = (C["peers"].get("median") or {}) if C["peers"].get("available") else {}
    HAS_PEERS = bool(C["peers"].get("available") and pm)

    _SIGNED = {"share_count_change"}

    def _pm_pct(key):
        v = pm.get(key)
        if v is None:
            return DASH
        return (f"{_rsign(v*100, 1)}%" if key in _SIGNED else f"{_xr(v*100, 1)}%")

    def _pcols(headers, rows, widths, drop_last=True):
        if HAS_PEERS or not drop_last:
            return dtable(doc, headers, rows, widths, right_from=1)
        _h = headers[:-1]
        _r = [r[:-1] for r in rows]
        _wi = [_as_in(w) for w in widths]
        _extra = _wi[-1] / max(1, len(_wi) - 1)
        _w = [w + _extra for w in _wi[:-1]]
        return dtable(doc, _h, _r, _w, right_from=1)
    subhead(doc, "Buybacks")
    if not HAS_PEERS:
        body(doc, "Peer comparison not performed: no validated peer set was supplied for this "
                  "report, so peer-median columns are omitted rather than shown empty.",
             size=8, italic=True, color=SLATE, space=1)

    _bb_nil = bool(b.get("nil_corroborated_by_share_count"))
    _bb_latest = "None reported" if _bb_nil else b.get("latest", "n/a")
    _bb_yield = DASH if _bb_nil else b["buyback_yield"]
    _bb_avgpx = DASH if _bb_nil else b["avg_rep_vs_current"]
    _pcols(["Buybacks", "Value", "Period / denominator", "Peer median"], [
        ["Repurchases, cumulative", b["total_5y"], period, DASH],
        ["Repurchases, latest", _bb_latest, "LTM", DASH],
        ["Buyback yield", _bb_yield, "LTM repurchases / market cap", _pm_pct("buyback_yield")],
        ["Share count change", _sharechg(b["share_count_change"]),
         (b.get("share_count_period") or period), _pm_pct("share_count_change")],
        ["Average repurchase price vs current", _bb_avgpx, "Weighted average vs current price", DASH],
    ] + ([["Remaining authorisation", N["buyback_authorization"], "As disclosed", DASH]] if N.get("buyback_authorization") else []),
        [Inches(w) for w in _fit([2.5, 1.3, 2.4, 1.0])])
    note(doc, N.get("buyback_commentary", ""))

    subhead(doc, "Dividends")
    if (C.get("dividends") or {}).get("not_applicable"):
        body(doc, (C["dividends"].get("not_applicable_reason")
                   or "No dividend was paid in any period of the analysis window.")
             + " Shareholder returns were made through repurchases alone; the dividend weighting "
               "is carried by the buyback dimension rather than withdrawn.",
             size=9, space=2)
        note(doc, N.get("dividend_commentary", ""))
    else:
     _dcov = vc.get("sector_div_coverage") or {}
     _payout_value = _dcov.get("payout_value", dv["payout_ratio"])
     _payout_basis = _dcov.get("payout_basis", "Dividends / net income")
     _cov_label = _dcov.get("label", "Free cash flow cover")
     _cov_value = _dcov.get("value", dv["fcf_coverage"])
     _cov_basis = _dcov.get("basis", "Free cash flow / dividends")
     dtable(doc, ["Dividends", "Value", "Period / denominator"], [
        ["Payout ratio", _payout_value, _payout_basis],
        [_cov_label, _cov_value, _cov_basis],
        ["Total dividends paid, growth (CAGR)", dv["dividend_5y_cagr"], period],
        ["Dividend per share, growth (CAGR)", dv.get("dividend_per_share_cagr", "n/a"), period],
        ["Dividend yield (declared rate)", dv["dividend_yield"],
         "Declared annual rate / share price"],
     ], [Inches(w) for w in _fit([2.5, 1.3, 3.4])], right_from=1)
     note(doc, N.get("dividend_commentary", ""))

    _reinv = vc.get("sector_reinvestment") or {}
    heading(doc, _reinv.get("heading", "Reinvestment: capital expenditure and acquisitions"), 5)
    if _reinv.get("note"):
        body(doc, _reinv["note"], size=9, italic=True, color=SLATE, space=2)
    cx = C["capex"]; ma = C["m_and_a"]; mna_n = _as_dict(N.get("m_and_a"))
    _sector_reinv = bool(vc.get("sector_reinvestment"))
    _skey = C.get("meta", {}).get("sector_key")
    _is_reit = (_skey == "REIT")
    _is_finl = _skey in ("Banks", "Insurance")

    def _capex_block(primary=True):
        subhead(doc, "Capital expenditure" if primary else "Maintenance capital expenditure")
        if primary:
            rows = [
                ["Capex", cx["capex_latest"], f"FY{sn['period']}", DASH],
                ["Capex growth (CAGR)", cx["capex_5y_cagr"], period, DASH],
                ["Capex intensity", cx["capex_intensity"], "Capex / revenue", _pm_pct("capex_intensity")],
                ["Reinvestment rate", cx["reinvestment_rate"], "Capex / operating cash flow", DASH],
            ]
            if cx.get("rnd_intensity", "n/a") != "n/a":
                rows.append(["R&D intensity", cx["rnd_intensity"], "R&D / revenue", DASH])
                rows.append(["Reinvestment intensity", cx.get("reinvestment_intensity", "n/a"),
                             "(R&D + capex) / revenue", DASH])
            rows.append(["Revenue growth", cx.get("revenue_growth_yoy", "n/a"), "Year on year (growth funded)", DASH])
            _pcols(["Reinvestment", "Value", "Period / denominator", "Peer median"], rows,
                   [Inches(w) for w in _fit([2.4, 1.2, 2.6, 1.0])])
        elif _is_finl:
            body(doc, "Physical capital expenditure is not a material capital-allocation lever "
                      "for this sector; reinvestment is measured through the balance sheet and "
                      "is covered above.", size=8.5, italic=True)
        else:
            dtable(doc, ["Maintenance capex", "Value", "Period / denominator"], [
                ["Capex (maintenance)", cx["capex_latest"], f"FY{sn['period']}"],
                ["Capex intensity", cx["capex_intensity"], "Capex / revenue (immaterial)"],
            ], [Inches(w) for w in _fit([2.6, 1.4, 3.2])], right_from=1)
        if C.get("capex", {}).get("framework"):
            body(doc, C["capex"]["framework"], size=8, italic=True, color=SLATE, space=1)
        if C.get("capex", {}).get("capex_vs_depreciation", "n/a") != "n/a":
            body(doc, "Capital expenditure to depreciation: %s (above 1.0x indicates the asset base "
                      "is being expanded rather than only maintained)."
                      % C["capex"]["capex_vs_depreciation"], size=8, color=DARK, space=1)
        note(doc, N.get("capex_commentary", ""))
        if primary and cx.get("adequacy_basis"):
            note(doc, cx["adequacy_basis"])

    def _strategic_bridge():
        sc = (N.get("strategic_consideration") or {})
        deals = sc.get("transactions") or []
        if not deals:
            return
        subhead(doc, "Strategic Capital Commitment")
        body(doc, "Cash deployment records what was paid out; strategic capital commitment records "
                  "what was committed, including consideration settled in stock or assumed debt. "
                  "A stock-funded acquisition is absent from the first view and central to the "
                  "second. Neither view alone describes total capital allocation.",
             size=8, italic=True, color=SLATE, space=1)
        _FLD = [("Cash consideration", "cash_consideration"),
                ("Stock consideration", "stock_consideration"),
                ("Shares issued", "shares_issued"),
                ("Debt assumed", "debt_assumed"),
                ("Contingent consideration", "contingent_consideration"),
                ("Transaction costs", "transaction_costs"),
                ("Integration costs", "integration_costs"),
                ("Total transaction consideration", "total_consideration"),
                ("Goodwill created", "goodwill_created"),
                ("Share-count impact", "share_count_impact"),
                ("Net-debt impact", "net_debt_impact")]
        for d in deals[:2]:
            rows = [[lab, str(d.get(k) or "Not disclosed in available sources")]
                    for lab, k in _FLD]
            rows.append(["Source and filing date",
                         str(d.get("source") or "Not stated")])
            dtable(doc, [str(d.get("name") or "Transaction"), "Amount / basis"], rows,
                   [Inches(w) for w in _fit([3.4, 3.8])], right_from=1)
        body(doc, "Where a component reads \"Not disclosed in available sources\", it is absent "
                  "from the data, not zero. Until total consideration is captured and reconciled, "
                  "the allocation style, the acquisition share and the descriptive score are "
                  "withheld.", size=7.5, italic=True, color=SLATE, space=2)

    def _acq_block(primary=False):
        subhead(doc, "Property acquisitions and development" if _is_reit else "Acquisitions")
        _acq_na = str(ma.get("acq_total_5y", "n/a")).lower().startswith("n/a")
        _mat_label, _mat_prefix = _MATERIALITY_WORDING[ma.get("materiality_basis",
                                                              "cumulative operating cash flow")]
        acq_rows = [
            ["Acquisition spend, cumulative", ma["acq_total_5y"], period],
            [_mat_label, ma["acq_pct_of_ocf"],
             ("%s %s" % (_mat_prefix, ma.get("cumulative_ocf"))
              if ma.get("cumulative_ocf") and not str(ma.get("cumulative_ocf")).startswith("n/a")
              else "Cumulative")],
            ["Acquisition spend as % of capital deployed", cl["acquisition_pct"], "Cumulative"],
        ]
        _deals_all = [d for d in mna_n.get("major_transactions", []) if isinstance(d, dict)]
        for d in _deals_all[:3]:
            acq_rows.append([d.get("name", ""), d.get("value", ""), d.get("period", "")])
        if len(_deals_all) > 3:
            acq_rows.append(["%d further named transaction%s not shown"
                             % (len(_deals_all) - 3, "" if len(_deals_all) - 3 == 1 else "s"),
                             "", "Table lists the three largest supplied"])
        if _is_reit:
            _affo_cagr = (vc.get("sector_dashboard") or [["n/a"]])[0][0]
            acq_rows.append(["Memo: AFFO per share change over the same period", _affo_cagr, "Coincident accretion read"])
        else:
            acq_rows.append(["Memo: ROIC change over the same period", ma["roic_change_bps"], "Coincident, not attributed"])
        dtable(doc, ["Property acquisitions" if _is_reit else "Acquisitions", "Value", "Period / denominator"],
               acq_rows, [Inches(w) for w in _fit([3.2, 1.4, 2.4])], right_from=1)
        _SENTINEL = ("none material", "not material", "n/a", "none", "no material",
                     "no transaction", "no acquisition", "no deal", "not captured",
                     "none identified", "none disclosed", "not disclosed", "no consideration",
                     "not reported")
        _named = [d for d in mna_n.get("major_transactions", []) if isinstance(d, str) and d.strip()
                  and not any(d.strip().lower().startswith(s) for s in _SENTINEL)]
        if _named:
            if _acq_na:
                body(doc, "The acquisition cash-flow line is not reported in the source data, so no "
                          "cash consideration is shown. The transactions below are taken from "
                          "filings; whether they were cash- or stock-funded is not established here.",
                     size=8, italic=True, color=SLATE, space=1)
            subhead(doc, "Major transactions")
            note(doc, _named)
        if mna_n.get("strategic_rationale"):
            subhead(doc, "Strategic rationale")
            note(doc, mna_n["strategic_rationale"], max_bullets=2)
        if mna_n.get("integration_status"):
            subhead(doc, "Integration status")
            note(doc, mna_n["integration_status"], max_bullets=2)
        if _is_reit:
            body(doc, "Deal-level acquisition yields are not disclosed; AFFO per-share is shown as a coincident "
                      "read on whether external growth has been accretive.", size=8, italic=True, color=SLATE, space=1)
        else:
            body(doc, "Return attribution is limited: the company does not disclose deal-level returns, so the ROIC "
                      "change over the same period is shown only as a coincident memo item and is not attributed to "
                      "the acquisitions.", size=8, italic=True, color=SLATE, space=1)
        note(doc, N.get("acq_commentary", "") or mna_n.get("value_creation_assessment", ""))

    if _sector_reinv:
        _acq_block(primary=True)
        _capex_block(primary=False)
    else:
        _capex_block(primary=True)
        _acq_block(primary=False)
    _strategic_bridge()

    heading(doc, "Balance sheet and leverage", 6)
    _bs_rows = [
        ["Total debt", bs.get("total_debt", "n/a"), "Latest reported quarter", DASH],
        ["Cash and equivalents", bs["cash"], "Latest reported quarter", DASH],
    ]
    _ndb = bs.get("net_debt_bridge") or []
    if _ndb:
        dtable(doc, ["Net debt bridge", "Amount"], [[a, b] for a, b in _ndb],
               [Inches(w) for w in _fit([3.9, 2.4])], right_from=1)
        if bs.get("net_debt_formula"):
            body(doc, "Formula as applied: " + bs["net_debt_formula"], size=7.5, italic=True,
                 color=SLATE, space=1)
    if bs.get("short_term_investments") and not bs.get("net_debt_via_investments"):
        _bs_rows.append(["Memo: " + bs.get("short_term_investments_label", "short-term investments"),
                         bs["short_term_investments"],
                         "Latest reported quarter (not netted in net debt%s)"
                         % ("" if bs.get("short_term_investments_liquid")
                            else "; not a liquid balance"), DASH])
    _nd_denom = "Latest reported quarter (%s)" % (
        bs.get("net_debt_formula", "total debt less cash and equivalents")
        .rstrip(".").replace("Total debt less cash and equivalents, as reported by the source",
                             "total debt less cash"))
    _finl = C.get("meta", {}).get("sector_key") in ("Banks", "Insurance")
    _bs_rows += [["Net debt", bs["net_debt"], _nd_denom, DASH]]
    _is_bank = C.get("meta", {}).get("sector_key") == "Banks"
    _is_ins = C.get("meta", {}).get("sector_key") == "Insurance"
    if _is_bank:
        _dash = {lab: val for val, lab in (vc.get("sector_dashboard") or [])}
        _bs_rows += [
            ["CET1 ratio", _dash.get("CET1 ratio", "n/a"), "Regulatory capital vs ~11% requirement", DASH],
            ["Tangible book value / share", _dash.get("TBV/share (CAGR)", "n/a"), f"CAGR, {period}", DASH],
        ]
    elif _is_ins:
        _dash = {lab: val for val, lab in (vc.get("sector_dashboard") or [])}
        _bs_rows += [
            ["Debt / total capital", bs.get("leverage_nd_ebitda", "n/a"),
             "Total debt / (total debt + equity)", DASH],
            ["Book value / share", _dash.get("BVPS (CAGR)", _dash.get("Book value/share (CAGR)", "n/a")),
             f"CAGR, {period}", DASH],
            ["Statutory capital and risk-based capital", "not reported in source",
             "Requires statutory filings", DASH],
        ]
    else:
        _bs_rows += [
            ["Leverage", bs["leverage_nd_ebitda"], lev_label, (f"{_xr(pm.get('leverage'), 2)}x" if pm.get("leverage") is not None else DASH)],
            ["Interest coverage", bs["interest_coverage"], "EBIT / interest expense, LTM", DASH],
        ]
    _pcols(["Balance sheet", "Value", "Period / denominator", "Peer median"], _bs_rows,
           [Inches(w) for w in _fit([2.3, 1.6, 2.3, 1.0])])
    if bs.get("short_term_investments"):
        body(doc, ("Net debt is total debt less cash and equivalents less %s: the source nets "
                   "marketable securities, and this is the formula that reconciles to its "
                   "reported figure."
                   % str(bs.get("short_term_investments_label") or "short-term investments").lower())
                  if bs.get("net_debt_via_investments") else
                  "Net debt is total debt less cash and equivalents. Short-term investments are "
                  "shown as a memo item and are not netted, matching the source definition.",
             size=8, italic=True, color=SLATE, space=1)
    if not bs.get("cash_available", True) or not bs.get("debt_available", True):
        body(doc, "Blank components are unavailable from source; net debt is reported directly and drives leverage.",
             size=8, italic=True, color=SLATE, space=1)
    note(doc, N.get("balance_sheet_commentary", ""))

    heading(doc, "Forward indicators as reported", 7)
    body(doc, "As published: company guidance and InSync-carried consensus. No InSync forecast.", size=9, space=2)
    fo = _as_dict(N.get("forward_outlook"))
    rows = _as_list(fo.get("rows"))
    if rows:
        t = doc.add_table(rows=1, cols=5); _set_borders(t); _pin_plain(t, 5)
        for i, h in enumerate(["Item", "Company guidance", "FY1E consensus", "FY2E consensus", "Basis note"]):
            _cell(t.rows[0].cells[i], h, bold=True, size=8, fill=LIGHT, align="left" if i in (0, 4) else "right")
        for r in rows:
            c = t.add_row().cells
            _cell(c[0], r.get("item", ""), size=8)
            _cell(c[1], r.get("guided", DASH), size=8, align="right")
            _cell(c[2], r.get("fy1", DASH), size=8, align="right")
            _cell(c[3], r.get("fy2", DASH), size=8, align="right")
            _cell(c[4], r.get("basis", r.get("read", "")), size=7.5)
        _set_grid(t, _fit([1.3, 1.6, 1.0, 1.0, 2.3]))
        body(doc, "Guided and consensus bases differ; direction, not level, is comparable.",
             size=7.5, italic=True, color=SLATE, space=1)
    else:
        body(doc, "No forward capital-allocation guidance or consensus supplied for this run.",
             italic=True, color=SLATE, space=1)

    if N.get("management_philosophy"):
        subhead(doc, "Management's stated priorities")
        note(doc, N["management_philosophy"])
    for q in _as_list(N.get("management_commentary"))[:1]:
        p_ = doc.add_paragraph(); p_.paragraph_format.left_indent = Inches(0.18); p_.paragraph_format.space_after = Pt(2)
        r1 = p_.add_run("“" + _clean(q.get("quote", "").strip("“”\"")) + "”")
        r1.italic = True; r1.font.name = SANS; r1.font.size = Pt(9); r1.font.color.rgb = DARK
        if q.get("attribution"):
            r2 = p_.add_run("   " + _clean(q["attribution"])); r2.font.name = SANS; r2.font.size = Pt(8); r2.font.color.rgb = SLATE
    if N.get("consistency_check"):
        subhead(doc, "Consistency check")
        note(doc, N["consistency_check"])

    heading(doc, "Peer comparison", 8)
    pr = C["peers"]
    if pr.get("available"):
        body(doc, "Peer set: " + ", ".join(x["name"] for x in pr.get("rows", [])) +
             ". Median, equal-weighted; differences in the unit shown at right.", size=8.5, italic=True, color=SLATE, space=1)
        order_keys = ("buyback_yield", "dividend_yield_proxy", "total_shareholder_yield",
                      "share_count_change", "capex_intensity", "reinvestment_rate",
                      "capital_return_fcf", "roic", "leverage")
        _drop = set()
        if vc.get("sector_reinvestment"):
            _drop = {"capex_intensity", "reinvestment_rate", "capital_return_fcf", "roic"}
        keys = [k for k in order_keys if k in pr["metric_keys"] and k not in _drop]
        labels = {"buyback_yield": "Buyback yield (cash / market cap)",
                  "dividend_yield_proxy": "Dividend yield (cash / market cap)",
                  "total_shareholder_yield": "Total shareholder yield", "share_count_change": "Share count change",
                  "capex_intensity": "Capex intensity", "reinvestment_rate": "Reinvestment rate (capex/OCF)",
                  "capital_return_fcf": "Capital returned / FCF", "roic": "ROIC",
                  "leverage": ((C.get("balance_sheet") or {}).get("leverage_metric_resolved")
                               or "Leverage (net debt / EBITDA)")}
        units = {"buyback_yield": "pp", "dividend_yield_proxy": "pp", "total_shareholder_yield": "pp",
                 "share_count_change": "pp", "capex_intensity": "pp", "reinvestment_rate": "pp",
                 "capital_return_fcf": "pp", "roic": "pp", "leverage": "turns"}
        tgt, med = pr.get("target") or {}, pr.get("median") or {}
        PCT = ("buyback_yield", "dividend_yield_proxy", "total_shareholder_yield", "share_count_change",
               "capex_intensity", "reinvestment_rate", "capital_return_fcf")
        t = doc.add_table(rows=1, cols=5); _set_borders(t); _pin_plain(t, 5)
        for i, h in enumerate(("Metric", meta.get("ticker", "Co"), "Peer median", "Difference", "Unit of difference")):
            _cell(t.rows[0].cells[i], h, bold=True, size=8.5, fill=LIGHT, align="left" if i in (0, 4) else "right")
        for k in keys:
            tv, mv = tgt.get(k), med.get(k)
            def disp(v):
                if v is None:
                    return "n/a"
                if k in PCT:
                    return (f"{_rsign(v*100, 1)}%" if k in _SIGNED else f"{_xr(v*100, 1)}%")
                if k == "roic":
                    return f"{_xr(v, 1)}%"
                return f"{_xr(v, 2)}x"
            if tv is not None and mv is not None:
                if k in PCT:
                    diff = _rsign(float(_xr(tv*100, 1)) - float(_xr(mv*100, 1)), 1)
                elif k == "roic":
                    diff = _rsign(float(_xr(tv, 1)) - float(_xr(mv, 1)), 1)
                else:
                    diff = _rsign(float(_xr(tv, 2)) - float(_xr(mv, 2)), 2)
            else:
                diff = "n/a"
            c = t.add_row().cells
            _cell(c[0], labels[k], size=8.5)
            _cell(c[1], disp(tv), size=8.5, align="right")
            _cell(c[2], disp(mv), size=8.5, align="right")
            _cell(c[3], diff, size=8.5, align="right")
            _cell(c[4], units[k], size=8)
        _set_grid(t, _fit([2.3, 1.1, 1.2, 1.0, 1.6]))
        doc.add_paragraph().paragraph_format.space_after = Pt(2)
        _warns = pr.get("peer_warnings", [])
        if _warns:
            body(doc, "Validation: " + _warns[0], size=8, italic=True, color=BAD, space=1)
        _dn_drop = ("fcf", "roic", "capex", "reinvestment", "free cash flow") if vc.get("sector_reinvestment") else ()
        _dn = [d for d in pr.get("deviation_notes", []) if not (_dn_drop and any(t in d.lower() for t in _dn_drop))]
        if _dn:
            body(doc, "Note: " + _dn[0], size=8, italic=True, color=SLATE, space=1)
        if vc.get("sector_reinvestment"):
            body(doc, "Basis: yields on current market cap; leverage = net debt / EBITDA.",
                 size=7.5, italic=True, color=SLATE, space=1)
        else:
            body(doc, "Basis: yields on current market cap; reinvestment = capex / OCF; capital returned / FCF cumulative "
                      "over the window. Applied identically to company and peers.",
                 size=7.5, italic=True, color=SLATE, space=1)
    else:
        body(doc, pr.get("note", "No peer data supplied."), italic=True, color=SLATE)
    note(doc, N.get("peer_commentary", ""), max_bullets=2)

    heading(doc, "Outcome metrics", 9)
    _outcome = vc.get("allocation_outcome")
    body(doc, "Realised results over the period, separate from the allocation mix above.",
         size=8, italic=True, color=GREY, space=1)
    if _outcome:
        _osc = vc.get("allocation_outcome_score")
        _ostate = (C.get("outcome_coverage") or {}).get("state")
        _op = doc.add_paragraph()
        _op.paragraph_format.space_before = Pt(2); _op.paragraph_format.space_after = Pt(1)
        if _osc is not None:
            _r2 = _op.add_run("Outcome score (this section)"
                              + (", provisional:   " if _ostate == "Provisional" else ":   "))
            _r2.bold = True; _r2.font.name = SANS; _r2.font.size = Pt(9); _r2.font.color.rgb = SLATE
            _r3 = _op.add_run(f"{_osc}/10")
            _r3.bold = True; _r3.font.name = SANS; _r3.font.size = Pt(12); _r3.font.color.rgb = _score_color(_osc)
            _r5 = _op.add_run("      ")
            _r5.font.name = SANS; _r5.font.size = Pt(9)
        elif _ostate == "Unscored":
            _r2 = _op.add_run("Outcome score (this section):   not scored   ")
            _r2.bold = True; _r2.font.name = SANS; _r2.font.size = Pt(9); _r2.font.color.rgb = SLATE
        _r1 = _op.add_run(_outcome)
        _r1.font.name = SANS; _r1.font.size = Pt(9); _r1.font.color.rgb = SLATE
        _con = vc.get("allocation_outcome_constraints") or []
        if _con:
            bullet(doc, _clean("; ".join(_con)), size=8.5, lead="Key constraints:")
    vc = C["value_creation"]
    if vc.get("sector_outcome"):
        dtable(doc, ["Outcome metric", "Value", "Period / denominator", "Reference point"],
               vc["sector_outcome"], [Inches(w) for w in _fit([2.6, 1.1, 1.7, 1.8])], right_from=1)
        if vc.get("sector_notes"):
            body(doc, vc["sector_notes"], size=8, italic=True, color=SLATE, space=1)
    else:
        dtable(doc, ["Outcome metric", "Value", "Period / denominator", "Reference point"], [
            ["ROIC", vc["roic"], f"FY{sn['period']}", f"{vc['cost_of_capital']} assumed cost of capital"],
            ["ROE", vc["roe"], f"FY{sn['period']}", DASH],
            ["Total shareholder return, annualised", vc["tsr_annualized"],
             (vc.get("tsr_period_label") or period), DASH],
            ["EPS growth (CAGR)", vc["eps_cagr"], period, DASH],
            ["Free cash flow per share (CAGR)", vc["fcf_per_share_cagr"], period, DASH],
            ["Share count change", _sharechg(vc["share_count_change"]),
             (vc.get("share_count_period") or period),
             (f"Peer median {_pm_pct('share_count_change')}" if HAS_PEERS else period)],
            ["Capital returned", vc["capital_returned_period"], period,
             ((f"{vc['capital_return_pct_fcf']} of cumulative FCF "
               f"{vc.get('cumulative_fcf') or ''}").strip()
              if not str(vc.get("capital_return_pct_fcf", "n/a")).lower().startswith("n/a")
              else (f"Cumulative FCF {vc.get('cumulative_fcf')}; a share-of-FCF ratio is not "
                    f"published against a negative total"
                    if str(vc.get("cumulative_fcf") or "").startswith("-")
                    else f"Cumulative FCF {vc.get('cumulative_fcf') or DASH}"))],
        ], [Inches(w) for w in _fit([2.6, 1.1, 1.7, 1.8])], right_from=1,
            direction=_outcome_direction)
    _rs = vc.get("return_series") or []
    if len([r for r in _rs if r.get("roic") and not str(r["roic"]).startswith("n/a")]) >= 3:
        _lbl = vc.get("return_series_label") or "Return on invested capital"
        _hdr = [_lbl] + [r["period"] for r in _rs]
        _rows = [["Reported", *[r["roic"] for r in _rs]],
                 ["Spread vs %s hurdle" % vc.get("cost_of_capital", "hurdle"),
                  *[r["spread"] for r in _rs]]]
        _w = [Inches(w) for w in _fit([2.0] + [1.0] * len(_rs))]
        dtable(doc, _hdr, _rows, _w, right_from=1)

    _otv = [vc.get("roic"), vc.get("roe"), vc.get("tsr_annualized"), vc.get("eps_cagr"),
            vc.get("fcf_per_share_cagr"), vc.get("share_count_change"),
            vc.get("capital_returned_period"), vc.get("capital_return_pct_fcf"),
            vc.get("cost_of_capital")]
    for _row in (vc.get("sector_outcome") or []):
        if isinstance(_row, (list, tuple)) and len(_row) > 1:
            _otv.append(_row[1])
    note(doc, _drop_restatements(
        N.get("outcome_commentary", "") or N.get("value_creation_commentary", ""), _otv))

    _qf = C.get("quality_flags", [])
    subhead(doc, "Capital-allocation quality flags")
    if _qf:
        for f in _qf[:6]:
            _sv = f.get("severity", "Monitoring")
            bullet(doc, f.get("text", ""), size=9, lead=f"[{_sv}] {f.get('flag','')}:")
    else:
        body(doc, "None triggered on the measures assessed (payout vs cash flow, debt-funded returns, buyback "
                  "price discipline, reinvestment adequacy).", size=9, color=DARK, space=2)
    if N.get("data_limitations"):
        body(doc, "Not all tests could be run: " + N["data_limitations"],
             size=8, italic=True, color=SLATE, space=2)

    _tc = N.get("through_cycle")
    _m = C.get("meta", {}) or {}
    _idtext = " ".join(str(_m.get(k, "")) for k in
                       ("industry", "classification", "sector_key")).lower()
    _regulated = ("utilit" in _idtext) or ("reit" in _idtext) or ("real estate" in _idtext)
    _commodity = any(k in _idtext for k in ("oil", "gas", "energy", "mining", "metals",
                                            "coal", "commodity chemical"))
    _industrial_cycle = (not _regulated) and (not _commodity) and any(
        k in _idtext for k in ("industrial", "transport", "logistics", "machinery", "chemical",
                               "materials", "aerospace", "railroad", "construction"))
    if _tc or C.get("cyclical") or _regulated:
        subhead(doc, "Through-cycle assessment" if not _regulated
                     else "Assessment across the regulatory and project cycle")
        if _tc:
            note(doc, _tc)
        elif _industrial_cycle:
            _rail = any(k in _idtext for k in ("railroad", "rail ", "transportation", "logistics"))
            body(doc, ("Assess capital allocation across the network cycle, focusing on volumes "
                       "(carloads and revenue ton-miles), operating ratio, network velocity and "
                       "terminal dwell, network sustaining capital expenditure, free-cash-flow "
                       "generation and leverage." if _rail else
                       "Assess capital allocation across the project cycle, focusing on backlog "
                       "conversion, contracted project returns, free-cash-flow generation, project "
                       "execution and leverage."), size=8.5, color=DARK, space=1)
        elif _regulated:
            body(doc, "Assess capital allocation across the regulatory and project cycle, focusing on "
                      "rate-base growth, regulatory recovery, funding costs, equity requirements, "
                      "project execution and credit metrics.", size=8.5, color=DARK, space=1)
        else:
            body(doc, "Cyclical sector: read the figures across the cycle, not on a single year - dividend "
                      "resilience at trough prices, buyback pace against the cycle, and capex and leverage "
                      "against a mid-cycle plan.", size=8.5, color=DARK, space=1)

    heading(doc, "What to monitor", 10)
    body(doc, "Variables that would move the figures above, with the level at which each becomes visible.", size=9, space=2)
    items = C.get("monitor_thresholds") or N.get("monitor_items")
    if not items:
        items = [x for x in (C.get("risks_computed", []) + _as_list(N.get("key_questions"))) if x]
    for it in items[:5]:
        if isinstance(it, dict):
            bullet(doc, it.get("text", ""), size=9.5, lead=it.get("lead"))
        else:
            bullet(doc, it, size=9.5)

    heading(doc, "Scorecard", 11)
    _pubx = C.get("publication") or {}
    _fails = C.get("failed_controls") or []
    _covx = C.get("score_coverage") or {}
    if _pubx.get("withheld"):
        subhead(doc, "Descriptive capital-allocation score: Withheld")
        body(doc, (_pubx.get("statement") or
                   "Reason: a mandatory methodology or reconciliation control failed.")
                  .replace("Descriptive capital-allocation score: Withheld. ", "Reason: ")
                  .replace("Reason: Reason:", "Reason:"),
             size=9, color=DARK, space=1)
        if _fails:
            _ft = doc.add_table(rows=1, cols=4); _set_borders(_ft); _pin_plain(_ft, 4)
            _set_grid(_ft, _fit([2.6, 2.2, 1.0, 1.1]))
            for i, h in enumerate(("Failed control", "Affected metric or section", "Severity",
                                   "Score withheld")):
                _cell(_ft.rows[0].cells[i], h, bold=True, size=8, fill=LIGHT,
                      align="left" if i < 2 else "center")
            for f in _fails:
                c = _ft.add_row().cells
                _cell(c[0], str(f.get("control") or ""), size=7.5)
                _cell(c[1], str(f.get("affected") or ""), size=7.5)
                _cell(c[2], str(f.get("severity") or "Mandatory"), size=7.5, align="center")
                _cell(c[3], "Yes", size=7.5, align="center")
            body(doc, "No overall score is published for this company. The individual lines below "
                      "are shown for information and do not combine into an assessment.",
                 size=7.5, italic=True, color=SLATE, space=1)
    _rows = []
    _ocx = C.get("outcome_coverage") or {}
    if N.get("data_limitations"):
        _rows.append(("Data gaps", N["data_limitations"]))
    _dcv = C.get("data_coverage") or {}
    if _dcv.get("missing"):
        _rows.append(("Not returned by the source",
                      "%s. Requested but returned empty; anything depending on these is reported "
                      "as not available, never as zero."
                      % ", ".join(m["concept"] for m in _dcv["missing"])))
    if _dcv.get("corroboration_conflicts"):
        _rows.append(("Corroboration", " ".join(_dcv["corroboration_conflicts"])))
    _bdx = (_covx or {}).get("basis_disagreement") or {}
    if _bdx:
        _rows.append((
            "Coverage basis",
            "Two coverage measures disagree. The published basis is the scorecard ladder (%s, %s). "
            "The template-required set reads %s (%s%s). The ladder is authoritative; the gap is "
            "shown rather than resolved silently."
            % (_bdx.get("published_coverage", "n/a"), _bdx.get("published_basis", "n/a"),
               _bdx.get("second_coverage", "n/a"), _bdx.get("second_basis", "n/a"),
               (", missing %s" % ", ".join(_bdx["second_missing"]))
               if _bdx.get("second_missing") else "")))
    if _covx:
        _status = ("Withheld"
                   if _pubx.get("withheld")
                   else {"publish": "Published", "suppress": "Withheld",
                         "provisional": "Provisional"}.get(
                             _covx.get("publication_status"),
                             str(_covx.get("publication_status") or "n/a").capitalize()))
        _nsc = len(_covx.get("scored_dimensions") or []) or None
        _rows.append(("Score status",
                      "%s. %s%s of the normal sector weighting could be scored (%s of %s points)."
                      % (_status,
                         ("%s dimensions covering " % _spell(_nsc, cap=True)) if _nsc else "",
                         _covx.get("retained_coverage", "n/a"),
                         _fmtw(_covx.get("valid_weight") or 0),
                         _fmtw(_covx.get("requested_weight") or 0))
                      + (" No score is published; the individual lines below are shown for information."
                         if _pubx.get("withheld") else
                         " The score is the weighted average of those lines only.")))
        _why = {}
        for _a in (C.get("weight_adjustments") or []):
            _r = str(_a.get("reason") or "").strip()
            if _ocx.get("state_reason") and _ocx["state_reason"] in _r:
                _r = _r.replace(_ocx["state_reason"], "").strip(" ;.")
            if _ocx.get("coverage") and str(_ocx["coverage"]) in _r:
                _r = "see the results check below"
            _why[_DIM_LABEL.get(_a["dimension"], _a["dimension"])] = _r
        _items, _short = [], 0.0
        for d in (_covx.get("decomposition") or []):
            _rsn, _x = str(d.get("reason") or ""), _why.get(d["dimension"])
            if _x and "partial weight applied" in _rsn:
                _rsn = re.sub(r",?\s*so th\w+ [^,]*\bis (provisional|incomplete)\.?$", "",
                              _x.strip(), flags=re.I).rstrip(" .;,")
                if len(_rsn) > 1 and _rsn[1:2].islower():
                    _rsn = _rsn[0].lower() + _rsn[1:]
            elif _rsn.lower().startswith("not applicable - "):
                _rsn = _rsn.split(" - ", 1)[1]
            elif "required input unavailable" in _rsn.lower():
                _rsn = "the required input is not reported"
            try:
                _ap, _rq = float(d["applied"]), float(d["requested"])
            except (TypeError, ValueError):
                _ap, _rq = None, None
            _raw = str(d.get("reason") or "").lower()
            _nss = _raw.startswith("not separately scored")
            _na = (not _nss) and (_raw.startswith("not applicable")
                                  or "no material transaction" in _rsn.lower())
            if _nss:
                _rsn = _rsn.split(" - ", 1)[1] if " - " in _rsn else _rsn
            _items.append("%s %s: %s."
                          % (d["dimension"],
                             (("not separately scored" if _nss else
                               "not applicable" if _na else "not scored") if _ap == 0 else
                              "%s of %s points" % (_fmtw(_ap), _fmtw(_rq)))
                             if _ap is not None else "not scored", _rsn))
            if _ap is not None:
                _short += _rq - _ap
        for _mg in (_covx.get("weight_merges") or []):
            _items.append("%s: its %s points move to %s, where this sector template assesses "
                          "that spending instead."
                          % (_mg["from"], _fmtw(_mg["amount"]), _mg["into"]))
        _auth = None
        try:
            _auth = float(_covx["requested_weight"]) - float(_covx["valid_weight"])
        except (TypeError, ValueError, KeyError):
            pass
        if _items:
            _txt = " ".join(_items)
            if _auth is not None and _auth > 0:
                _txt += (" In total %s of the %s points go unused."
                         % (_fmtw(_auth), _fmtw(_covx.get("requested_weight") or 0)))
                if abs(_auth - _short) > 0.011:
                    _txt += (" These lines add to %s, which does not tie to that total."
                             % _fmtw(_short))
            _rows.append(("What is missing", _txt))
    if _ocx:
        _miss, _av = _ocx.get("missing") or [], _ocx.get("available") or []
        _rows.append(("Results check",
                      "%s%s %s"
                      % (("%s of %s measures of realised outcome are available; %s %s missing."
                          % (_spell(len(_av), cap=True), _spell(len(_av) + len(_miss)),
                             _andlist([m.lower() for m in _miss]),
                             "is" if len(_miss) == 1 else "are"))
                         if _miss else
                         "All %s measures of realised outcome are available." % _spell(len(_av)),
                         (" Coverage is %s (%s)." % (_ocx.get("coverage"),
                                                     str(_ocx.get("state") or "").lower()))
                         if _ocx.get("state_reason") else "",
                         "Total shareholder return is %s." % _ocx.get("tsr"))))
    if _covx:
        _appr = [re.sub(r"[:,]?\s*approved by the methodology owner[^.]*\.?$", "", _x).strip()
                 for _x in (_covx.get("approved_rules") or [])]
        _has_thr = any("threshold" in _x.lower() for _x in _appr)
        _plain = ("A score is published when 80% or more of the sector weighting can be scored, "
                  "called provisional between 60% and 79%, and withheld below 60%.")
        _half = ("A line that can only be part-assessed carries half its sector weight; one that "
                 "cannot be assessed carries none.")
        _txt = ((_plain + " ") if (_has_thr or _covx.get("thresholds")) else "")
        if any("half" in _x.lower() for _x in _appr) or (C.get("weight_adjustments") or []):
            _txt += _half + " "
        if _txt:
            _rows.append(("Scoring rules",
                          _txt + "These rules are fixed and applied identically to "
                          "every company."))
        for _u in (_covx.get("unapproved_rules") or []):
            _rows.append(("Pending approval", _u))
    _rows.append(("How to read it",
                  "Each line is scored 0-10 from the figures shown and weighted by how much it "
                  "matters in this sector; the score is the weighted average. The bands are "
                  "8.0-10.0, 6.5-7.9, 5.0-6.4 and below 5.0. No label such as strong or weak is "
                  "attached to them, because the thresholds have not been tested across enough "
                  "sectors and market cycles to carry one."))
    _seen = set()
    _ct = doc.add_table(rows=0, cols=2); _fixed_layout(_ct); _set_grid(_ct, _fit([1.15, 5.95]))
    _set_borders(_ct)
    for _lbl, _val in _rows:
        _val = _dedupe(_val, _seen)
        if not _val:
            continue
        c = _ct.add_row().cells
        _cell(c[0], _lbl, bold=True, size=7.5, color=SLATE)
        _cell(c[1], _val, size=7.5, color=DARK)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    comp = C["scorecard"]["components"]; ev = C["scorecard"].get("evidence", {})
    if _pubx.get("withheld"):
        ev = {k: re.sub(r"^Scored\s+(\d{1,2})\s*/\s*10\s+because\b",
                        r"Provisional observation (\1/10, not published) because",
                        str(v)) if isinstance(v, str) else v
              for k, v in ev.items()}
    order = [("Buybacks", "buybacks"), ("Dividends", "dividends"), ("Capex", "capex"),
             ("M&A", "m_and_a"), ("Balance sheet", "balance_sheet"),
             ("Capital discipline", "capital_discipline"), ("Value creation (outcome)", "value_creation")]
    body(doc, "InSync capital-allocation score: a descriptive framework score built from the weights "
              "and thresholds set out in Appendix A and the reported data available. It is not a "
              "rating of management and not an investment recommendation.",
         size=8, italic=True, color=SLATE, space=2)
    t = doc.add_table(rows=1, cols=4); _set_borders(t); _pin_plain(t, 4)
    _set_grid(t, _fit([1.3, 4.3, 0.75, 0.55]))
    _sc_hdr = ("Score" if not _pubx.get("withheld") else "Provisional observation")
    for i, h in enumerate(("Dimension", "Supporting figures", _sc_hdr, "Weight")):
        _cell(t.rows[0].cells[i], h, bold=True, size=8.5, fill=LIGHT, align="left" if i in (0, 1) else "center")
    _wadj = {a["dimension"]: a for a in (C.get("weight_adjustments") or [])}
    _req_w = ((m.get("sector") or {}).get("requested_weights")
              or {k: weights.get(k) for k in weights})
    for label, key in order:
        w = weights.get(key)
        if not w:
            _ev = str(ev.get(key) or "").strip()
            if not _ev:
                continue
            c = t.add_row().cells
            _cell(c[0], label, size=8.5)
            _cell(c[1], _ev, size=8)
            _cell(c[2], ("Not applicable"
                         if (_ev.lower().startswith("not applicable")
                             or _ev.lower().startswith("no dividend was paid"))
                         else "Unscored"), size=8, align="center")
            _cell(c[3], "0x", size=8, align="center")
            continue
        if key in _wadj:
            label = label + " (provisional)"
        v = comp.get(key)
        _, br = _score_band(v)
        c = t.add_row().cells
        _cell(c[0], label, size=8.5)
        _cell(c[1], ev.get(key, ""), size=8)
        _cell(c[2], f"{v}/10" if v is not None else "n/a", size=8.5, align="center")
        if v is not None:
            c[2].paragraphs[0].runs[0].font.color.rgb = _score_color(v)
        _cell(c[3], f"{w:g}x", size=8.5, align="center")
    orow = t.add_row().cells
    _cell(orow[0], "Descriptive score (weighted)", bold=True, size=9, fill=LIGHT)
    _prov = ((C.get("score_coverage") or {}).get("publication_status") == "provisional"
             and overall is not None)
    _cell(orow[1], (("PROVISIONAL. " if _prov else "")
                    + f"Weight-averaged across the scored dimensions; lands in the {band} band."
                    if overall is not None else
                    ((_pubx.get("statement") or "Withheld.").replace(
                        "Descriptive capital-allocation score: Withheld. ", "")
                     + " Component observations do not form a valid overall assessment.")),
          size=8, fill=LIGHT)
    _cell(orow[2], _score_txt(overall), bold=True, size=9, align="center", fill=LIGHT)
    orow[2].paragraphs[0].runs[0].font.color.rgb = _score_color(overall)
    _cell(orow[3], f"{sum(v for v in weights.values() if v):g}x", bold=True, size=9, align="center", fill=LIGHT)
    _score_tbl = t
    try:
        _cmp2 = C["scorecard"]["components"]
        _c2 = [(k, v, weights.get(k) or 0) for k, v in _cmp2.items() if v is not None and (weights.get(k) or 0) > 0]
        if len(_c2) >= 2 and not _pubx.get("withheld"):
            _l2 = {"buybacks": "buybacks", "dividends": "dividends", "capex": "capex", "m_and_a": "M&A",
                   "balance_sheet": "balance sheet", "capital_discipline": "capital discipline",
                   "value_creation": "value creation"}
            _hi2 = max(_c2, key=lambda x: x[1]); _lo2 = min(_c2, key=lambda x: x[1])
            body(doc, f"Lifted most by {_l2.get(_hi2[0], _hi2[0])} ({_hi2[1]}/10 at {_hi2[2]:g}x weight); "
                      f"held back most by {_l2.get(_lo2[0], _lo2[0])} ({_lo2[1]}/10 at {_lo2[2]:g}x).",
                 size=8, italic=True, color=SLATE, space=1)
    except Exception:
        pass


    subhead(doc, "How the scores are set")
    _base_dims = _methodology_dims(C)
    _sector_dims = C["value_creation"].get("sector_methodology") or []
    _by_key = {r[1]: r for r in _sector_dims}
    _method_dims = [_by_key.get(r[1], r) for r in _base_dims]
    _seen = {r[1] for r in _method_dims}
    _method_dims += [r for r in _sector_dims if r[1] not in _seen]
    _mrows = []
    for label, key, measured, thresholds in _method_dims:
        w = weights.get(key)
        if not w:
            continue
        _named = [t for t in thresholds
                  if re.match(r"^(higher|lower|incomplete)\s*:", str(t).strip(), re.I)]

        def _clause(t, n=2):
            t = str(t).strip().rstrip(".")
            _, _, rest = t.partition(":")
            rest = rest or t
            return ", ".join([c.strip() for c in rest.split(",")][:n])

        if _named:
            _p = {}
            for t in _named:
                h, _, _r = str(t).partition(":")
                _p[h.strip().lower()] = _clause(t)
            _hi, _lo = _p.get("higher", "n/a"), _p.get("lower", "n/a")
        else:
            _hi = _clause(re.sub(r"^[\d.\-<>=]+\s*", "", str(thresholds[0])))
            _lo = _clause(re.sub(r"^[\d.\-<>=]+\s*", "", str(thresholds[-1])))
        _mrows.append([re.sub(r"\s*\(outcome\)\s*", " ", str(label)).strip(),
                       "%gx" % w, _clean(_hi), _clean(_lo)])
    if _mrows:
        dtable(doc, ["Dimension", "Weight", "Scores high when", "Scores low when"], _mrows,
               [Inches(w) for w in _fit([1.25, 0.55, 2.65, 2.65])],
               hsize=7.5, rsize=7.5, right_from=1)
        _mt = doc.tables[-1]
        _fixed_layout(_mt); _set_grid(_mt, _fit([1.25, 0.55, 2.65, 2.65]))
        for _rr in doc.tables[-1].rows:
            for _j in (2, 3):
                for _pp in _rr.cells[_j].paragraphs:
                    _pp.alignment = WD_ALIGN_PARAGRAPH.LEFT
    _appx_tbl = None

    for _w in (C.get("warnings") or []):
        if str(_w).startswith("WITHHELD DATA:"):
            ctrl_note(doc, "Withheld data.", str(_w).split(":", 1)[1].strip(), size=7)
    _DATA_CURRENCY_PREFIXES = ("ADVISORY: the newest quarter in the data ends",
                               "ADVISORY: no earnings calendar was supplied")
    for _w in (C.get("warnings") or []):
        _ws = str(_w)
        if any(_ws.startswith(_p) for _p in _DATA_CURRENCY_PREFIXES):
            ctrl_note(doc, "Data currency.", _ws.split(":", 1)[1].strip(), size=7)
    fp = doc.add_paragraph()
    fp.paragraph_format.space_before = Pt(2)
    _val = C.get("validation", {})
    fr = fp.add_run(
        "Source: InSync Analytics. All figures are calculated from the company's reported data "
        "under the methodology set out in Appendix A; reported and derived figures only, with "
        "no investment recommendation. "
        + ("All %d consistency checks on the figures passed%s. "
           % ((C.get("qc", {}).get("total") or 0),
              ("" if (C.get("qc", {}).get("total") or 0) >= _QC_FULL_SET
               else " (%d of the standard %d checks could run; the rest need inputs the source did "
                    "not return)" % ((C.get("qc", {}).get("total") or 0), _QC_FULL_SET)))
           if (C.get("qc", {}).get("passed") == C.get("qc", {}).get("total")
               and _val.get("passed") == _val.get("total"))
           else "%d of %d consistency checks did not pass - see the Scorecard section. "
                % ((C.get("qc", {}).get("total") or 0) - (C.get("qc", {}).get("passed") or 0),
                   (C.get("qc", {}).get("total") or 0)))
        + ("Score withheld - see the Scorecard section for the reason."
           % () if False else "Score withheld - see the Scorecard section for the reason."
           if (C.get("publication") or {}).get("withheld")
           else ("Descriptive score: provisional (effective coverage %s)."
                 % (C.get("score_coverage") or {}).get("retained_coverage", "n/a")
                 if (C.get("score_coverage") or {}).get("publication_status") == "provisional"
                 else "Descriptive score: published.")))
    fr.font.name = SANS; fr.font.size = Pt(7); fr.italic = True; fr.font.color.rgb = GREY

    for tbl in doc.tables:
        _tight_cell_margins(tbl, top=6, bottom=6) if tbl is _appx_tbl else _tight_cell_margins(tbl)
        _zebra(tbl)
        if tbl is _appx_tbl or len(tbl.rows) > _FLOW_ROWS:
            if tbl.rows:
                _hp = tbl.rows[0]._tr.get_or_add_trPr()
                if not _hp.findall(qn("w:tblHeader")):
                    _th = OxmlElement("w:tblHeader"); _th.set(qn("w:val"), "true")
                    _hp.append(_th)
            for row in tbl.rows:
                trPr = row._tr.get_or_add_trPr()
                if not trPr.findall(qn("w:cantSplit")):
                    trPr.append(OxmlElement("w:cantSplit"))
        else:
            _keep_together(tbl)

    doc.save(out_path)
    return out_path


def main():
    if len(sys.argv) < 3:
        sys.exit("Usage: python render_capalloc.py merged.json out.docx")
    with open(sys.argv[1]) as f:
        m = json.load(f)
    print(json.dumps({"status": "ok", "output": render(m, sys.argv[2])}))


if __name__ == "__main__":
    main()
