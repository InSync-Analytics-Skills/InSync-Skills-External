#!/usr/bin/env python3
"""
Earnings Flash internal renderer - Python twin of the internal edition's JS renderer (v11.0.0).

WHY THIS EXISTS. The JS renderer needs Node. Some sandboxes used by other AI assistants run
Python only. This file builds the SAME .docx from the SAME data.json with the Python standard
library alone: it writes the WordprocessingML that the docx 9.6.1 library writes, element for
element, attribute for attribute. An internal-edition parity test renders the same payloads
through both renderers and asserts word/document.xml and word/footer1.xml are byte-identical.

It is internal to run_pipeline.py: it refuses to run without
EF_PIPELINE=1 and it re-checks data.json against computed.json before drawing anything.

Usage (internal): python3 _render_internal.py --input data.json --output out.docx --computed computed.json
"""
import datetime as _dt
import hashlib
import io
import json
import math
import os
import re
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))

if os.environ.get("EF_PIPELINE") != "1":
    sys.stderr.write(
        "[_render_internal.py] REFUSED: this renderer is internal to run_pipeline.py.\n"
        "Do not invoke directly. Use:\n"
        "  python references/run_pipeline.py <input.json> <narrative.json> <output.docx>\n")
    sys.exit(2)


def _js_round(x):
    return int(math.floor(x + 0.5))


def _js_len(s):
    return len(str(s).encode("utf-16-le")) // 2


_FALLBACK_DISCLOSURE = (
    "For informational purposes only. Not investment advice. This document is not an offer to buy "
    "or sell, or a solicitation of an offer to buy or sell, any financial instrument, and it does "
    "not constitute participation in any trading strategy in any jurisdiction. InSync Analytics "
    "does not guarantee the accuracy, completeness, or timeliness of the information presented. "
    "This document does not take into account the investment objectives, financial situation, or "
    "specific needs of any individual investor. InSync Analytics is not a licensed financial "
    "advisor, broker, or dealer, and this document is not intended to provide investment, legal, "
    "accounting, or tax advice. You are solely responsible for your own investment decisions; "
    "InSync Analytics is not liable for any losses or damages arising from the use of this material.")
_FB_COLORS = {
    "orange": "E8850C", "orangeDk": "B25E08", "ink": "1A1A1A", "titleBg": "232526",
    "white": "FFFFFF", "headerBg": "EDEDED", "bandFill": "F0E9D8", "calloutBg": "F6F3EE",
    "mastBorder": "C9C9C9", "beatGreen": "1E7A34", "missRed": "B02A1F", "inlineGray": "6E6E6E",
    "sectionHeader": "1A1A1A", "tableBorder": "DDDDDD", "headerRule": "BBBBBB",
    "rowRule": "EEEEEE", "rowAlt": "F5F0E8", "divider": "F0E9D8", "textDark": "222222",
    "textLight": "555555", "footerBorder": "DDDDDD",
}


def _load_brand():
    fb = {"firmName": "InSync Analytics", "fonts": {"title": "Georgia", "body": "Calibri"},
          "colors": dict(_FB_COLORS), "disclosure": _FALLBACK_DISCLOSURE, "masthead": {}}
    try:
        with open(os.path.join(HERE, "..", "common", "brand.json"), encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return fb
    c = raw.get("colors") or {}
    src = {"titleBg": "navy", "textDark": "grayDark", "textLight": "grayLight"}
    colors = {}
    for k, v in _FB_COLORS.items():
        colors[k] = c.get(src.get(k, k)) or v
    fonts = raw.get("fonts") or {}
    return {
        "firmName": raw.get("firmName") or fb["firmName"],
        "fonts": {"title": fonts.get("title") or "Georgia", "body": fonts.get("body") or "Calibri"},
        "colors": colors,
        "disclosure": raw.get("disclosure") or _FALLBACK_DISCLOSURE,
        "masthead": raw.get("masthead") or {},
    }


BRAND = _load_brand()
C = BRAND["colors"]
F_TITLE = BRAND["fonts"]["title"]
F_BODY = BRAND["fonts"]["body"]

_PROSE_MINUS = re.compile(r"(?<![\w\-])-(?=[\d$])")
_CTRL = re.compile("[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")


def sanitize_text(v):
    if v is None:
        return ""
    if isinstance(v, list):
        v = " ".join(_js_str(x) for x in v)
    return _PROSE_MINUS.sub(_MINUS, _CTRL.sub("", _js_str(v)))


def _js_str(v):
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, list):
        return ",".join("" if x is None else _js_str(x) for x in v)
    if isinstance(v, dict):
        return "[object Object]"
    return str(v)


def render_cell(v):
    if v is None:
        return ""
    return _js_str(v).strip()


def _esc_text(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def _esc_attr(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def run(text, size=None, font=None, color=None, bold=None, italics=None, char_spacing=None):
    rp = []
    if font:
        rp.append('<w:rFonts w:ascii="%s" w:cs="%s" w:eastAsia="%s" w:hAnsi="%s"/>' % ((_esc_attr(font),) * 4))
    if bold is True:
        rp.append("<w:b/><w:bCs/>")
    elif bold is False:
        rp.append('<w:b w:val="false"/><w:bCs w:val="false"/>')
    if italics is True:
        rp.append("<w:i/><w:iCs/>")
    elif italics is False:
        rp.append('<w:i w:val="false"/><w:iCs w:val="false"/>')
    if color:
        rp.append('<w:color w:val="%s"/>' % color)
    if char_spacing is not None:
        rp.append('<w:spacing w:val="%d"/>' % char_spacing)
    if size is not None:
        rp.append('<w:sz w:val="%s"/><w:szCs w:val="%s"/>' % (size, size))
    rpr = ("<w:rPr>%s</w:rPr>" % "".join(rp)) if rp else ""
    return '<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r>' % (rpr, _esc_text(text))


def _spacing(sp):
    if not sp:
        return ""
    attrs = []
    for k, name in (("after", "w:after"), ("before", "w:before"), ("line", "w:line"), ("lineRule", "w:lineRule")):
        if k in sp and sp[k] is not None:
            attrs.append('%s="%s"' % (name, sp[k]))
    return "<w:spacing %s/>" % " ".join(attrs) if attrs else ""


def _bdr(side, style, color, size, space=None):
    s = '<w:%s w:val="%s" w:color="%s" w:sz="%s"' % (side, style, color, size)
    if space is not None:
        s += ' w:space="%s"' % space
    return s + "/>"


def para(runs=(), spacing=None, align=None, keep_next=False, bullet=False,
         border_bottom=None, border_top=None, tab_right=None):
    pp = []
    if bullet:
        pp.append('<w:pStyle w:val="ListParagraph"/><w:numPr><w:ilvl w:val="0"/><w:numId w:val="2"/></w:numPr>')
    if keep_next:
        pp.insert(0, "<w:keepNext/>") if not bullet else pp.append("<w:keepNext/>")
    if border_top or border_bottom:
        b = ""
        if border_top:
            b += _bdr("top", *border_top)
        if border_bottom:
            b += _bdr("bottom", *border_bottom)
        pp.append("<w:pBdr>%s</w:pBdr>" % b)
    if tab_right is not None:
        pp.append('<w:tabs><w:tab w:val="right" w:pos="%s"/></w:tabs>' % tab_right)
    sp = _spacing(spacing)
    if sp:
        pp.append(sp)
    if align:
        pp.append('<w:jc w:val="%s"/>' % align)
    ppr = ("<w:pPr>%s</w:pPr>" % "".join(pp)) if pp else ""
    return "<w:p>%s%s</w:p>" % (ppr, "".join(runs))


def _tc_borders(b):
    out = []
    for side in ("top", "left", "bottom", "right"):
        if side not in b:
            continue
        v = b[side]
        if v == "none":
            out.append('<w:%s w:val="none"/>' % side)
        else:
            out.append(_bdr(side, *v))
    return "<w:tcBorders>%s</w:tcBorders>" % "".join(out)


def cell(children, width=None, borders=None, shading=None, margins=None, valign=None, span=None):
    tp = []
    if span:
        tp.append('<w:gridSpan w:val="%d"/>' % span)
    if width is not None:
        tp.append('<w:tcW w:type="dxa" w:w="%d"/>' % width)
    if borders:
        tp.append(_tc_borders(borders))
    if shading:
        tp.append('<w:shd w:fill="%s" w:val="clear"/>' % shading)
    if margins:
        t, l, b_, r = margins
        tp.append('<w:tcMar><w:top w:type="dxa" w:w="%d"/><w:left w:type="dxa" w:w="%d"/>'
                  '<w:bottom w:type="dxa" w:w="%d"/><w:right w:type="dxa" w:w="%d"/></w:tcMar>' % (t, l, b_, r))
    if valign:
        tp.append('<w:vAlign w:val="%s"/>' % valign)
    return "<w:tc><w:tcPr>%s</w:tcPr>%s</w:tc>" % ("".join(tp), "".join(children))


_DEFAULT_TBL_BORDERS = ('<w:tblBorders><w:top w:val="single" w:color="auto" w:sz="4"/>'
                        '<w:left w:val="single" w:color="auto" w:sz="4"/>'
                        '<w:bottom w:val="single" w:color="auto" w:sz="4"/>'
                        '<w:right w:val="single" w:color="auto" w:sz="4"/>'
                        '<w:insideH w:val="single" w:color="auto" w:sz="4"/>'
                        '<w:insideV w:val="single" w:color="auto" w:sz="4"/></w:tblBorders>')


def table(rows, col_widths, width=10800, borders_xml=None):
    return ('<w:tbl><w:tblPr><w:tblW w:type="dxa" w:w="%d"/>%s<w:tblLayout w:type="fixed"/></w:tblPr>'
            '<w:tblGrid>%s</w:tblGrid>%s</w:tbl>') % (
        width, borders_xml or _DEFAULT_TBL_BORDERS,
        "".join('<w:gridCol w:w="%d"/>' % w for w in col_widths), "".join(rows))


def row(cells):
    return "<w:tr>%s</w:tr>" % "".join(cells)


def _parse_args(argv):
    a = {"input": None, "output": None, "computed": None}
    i = 0
    while i < len(argv):
        k = argv[i]
        if k in ("--input", "--output", "--computed") and i + 1 < len(argv):
            a[k[2:]] = argv[i + 1]
            i += 1
        i += 1
    if not a["input"] or not a["output"]:
        sys.stderr.write("Usage (internal - invoke via run_pipeline.py): python3 _render_internal.py "
                         "--input data.json --output out.docx --computed <scratch>/computed.json\n")
        sys.exit(1)
    return a


def _jdump(v):
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def _cross_check(data, computed_path):
    errs = []
    if not computed_path:
        return ["EF_CROSSCHECK_FAILED: --computed argument is required."]
    try:
        with open(computed_path, encoding="utf-8") as f:
            computed = json.load(f)
    except Exception as e:
        return ["EF_CROSSCHECK_FAILED: cannot read %s: %s" % (computed_path, e)]
    if computed.get("_computed_by") != "run_math":
        return ["EF_CROSSCHECK_FAILED: %s is missing _computed_by marker - was it actually produced "
                "by run_math.py?" % computed_path]
    checks = [
        ("financialMetrics", computed.get("financialMetrics"), data.get("financialMetrics")),
        ("operatingMetrics", computed.get("operatingMetrics"), data.get("operatingMetrics")),
        ("marketPrices.lastClosePrice", (computed.get("marketPrices") or {}).get("lastClosePrice"),
         data.get("lastClosePrice")),
    ]
    for name, want, got in checks:
        if _jdump(want) != _jdump(got):
            errs.append('EF_CROSSCHECK_FAILED: data.json "%s" does not match computed.json "%s".' % (name, name))
            errs.append("  Expected (from run_math.py): %s" % _jdump(want)[:300])
            errs.append("  Got (in your data.json):     %s" % _jdump(got)[:300])
            return errs
    return errs


_SNAKE_OK = {"dataPath", "toolsUsed", "_computed_by", "_computed_at", "_built_by", "_built_at",
             "kpi_source", "kpi_count", "prior_period"}


def _validate(d):
    if d.get("_computed_by") != "run_math":
        return ["EF_GATE_FAILED: Payload was not produced by references/run_math.py."]
    errs = []
    for k in ("companyName", "ticker", "currentPeriod", "currentPeriodDisplay", "priorPeriodDisplay",
              "earningsDate", "reportDate"):
        v = d.get(k)
        if not v or not isinstance(v, str) or not v.strip():
            errs.append('Missing or empty required field: "%s"' % k)
    fm = d.get("financialMetrics")
    if not isinstance(fm, list) or len(fm) < 1:
        errs.append("financialMetrics must be a non-empty array (>=1 metric)")
    for i, m in enumerate(fm or []):
        for f in ("name", "currentActual", "currentEstimate", "priorActual", "deltaAbsolute",
                  "deltaPercent", "yoyChange", "beatMiss"):
            if m.get(f) is None:
                errs.append('financialMetrics[%d] missing field: "%s"' % (i, f))
    if "stockPrice" in d:
        errs.append('Schema error: use the flat field "lastClosePrice" - not a nested "stockPrice" object')
    if "reactionPrice" in d or "reactionPercent" in d:
        errs.append('Schema error: "reactionPrice"/"reactionPercent" were removed in v7.6.0.')
    for k in d.keys():
        if "_" in k and k not in _SNAKE_OK:
            errs.append('Unexpected snake_case key: "%s" - use camelCase' % k)
    for i, m in enumerate(fm or []):
        if m.get("beatMiss") and m.get("deltaPercent"):
            try:
                delta = float(re.sub(r"[+%]", "", _js_str(m["deltaPercent"])))
            except ValueError:
                continue
            lb = m.get("lowerIsBetter") is True
            beat_wrong = delta > 1 if lb else delta < -1
            miss_wrong = delta < -1 if lb else delta > 1
            if m["beatMiss"] == "Beat" and beat_wrong:
                errs.append('financialMetrics[%d] "%s": beatMiss is "Beat" but delta is %s' % (i, m.get("name"), m["deltaPercent"]))
            if m["beatMiss"] == "Miss" and miss_wrong:
                errs.append('financialMetrics[%d] "%s": beatMiss is "Miss" but delta is %s' % (i, m.get("name"), m["deltaPercent"]))
    return errs


COLW = 10800
SCORECARD_WIDTHS = [3105, 1050, 1050, 1050, 1050, 1050, 1395, 1050]
NO_SIDE = ("none", "FFFFFF", 0)
CELL_BORDERS = {"top": NO_SIDE, "bottom": ("single", C["rowRule"], 2), "left": NO_SIDE, "right": NO_SIDE}
HEAD_BORDERS = {"top": NO_SIDE, "bottom": ("single", C["headerRule"], 5), "left": NO_SIDE, "right": NO_SIDE}

_MINUS = "−"


def normalize_sign(text):
    t = "" if text is None else _js_str(text)
    return t.replace("-", _MINUS, 1) if re.match(r"^-[\d$(]", t.strip()) else t


def sign_color(text):
    t = ("" if text is None else _js_str(text)).strip()
    if re.match(r"^\+[\d$(]", t):
        return C["beatGreen"]
    if re.match("^" + _MINUS + r"[\d$(]", t) or re.match(r"^\(\$?[\d,][\d,.]*\)", t):
        return C["missRed"]
    return None


def beat_miss_color(status):
    if status == "Beat":
        return C["beatGreen"]
    if status == "Miss":
        return C["missRed"]
    return C["inlineGray"]


def move_color(text, lower_is_better):
    base = sign_color(text)
    if not base:
        return None
    if not lower_is_better:
        return base
    return C["missRed"] if base == C["beatGreen"] else C["beatGreen"]


D = {}


def make_cell(text, bold=False, align="left", shading=None, width=None, color=None, borders=None):
    shown = normalize_sign(text or "")
    r = run(sanitize_text(_js_str(shown)), size=D["cell"], font=F_BODY, bold=bold,
            color=color or sign_color(shown) or C["ink"])
    p = para([r], spacing={"after": 0, "before": 0}, align=align)
    return cell([p], width=width, borders=borders or CELL_BORDERS, shading=shading,
                margins=(D["cellMargin"], 70, D["cellMargin"], 70), valign="center")


def short_name(n):
    s = _js_str(n or "")
    s = re.sub(r"\(Ex\s*-?\s*1x Items & SBC\)", "(adj, ex-SBC)", s, count=1, flags=re.I)
    s = re.sub(r"\(Ex\s*-?\s*1x Items & Amortization\)", "(adj, ex-amort)", s, count=1, flags=re.I)
    s = re.sub(r"\(Ex\s*-?\s*1x Items\)", "(adj)", s, count=1, flags=re.I)
    return s


def has_actual(m):
    v = (m.get("currentActual") or "")
    v = _js_str(v).strip()
    return v not in ("", "-", "N/A", "n/a", "--")


def variance_table(data):
    heads = ["", "%s (A)" % data.get("priorPeriodDisplay"), "%s (A)" % data.get("currentPeriodDisplay"),
             "%s (E)" % data.get("currentPeriodDisplay"), "Delta", "Delta %", "YoY% (BPS)", "Beat/Miss"]
    aligns = ["left", "right", "right", "right", "right", "right", "right", "center"]
    rows = [row([make_cell(h, bold=True, align=aligns[i], shading=C["headerBg"],
                           width=SCORECARD_WIDTHS[i], borders=HEAD_BORDERS) for i, h in enumerate(heads)])]

    def divider(text):
        p = para([run(text, size=18, font=F_BODY, color=C["sectionHeader"], bold=True)])
        return row([cell([p], borders=CELL_BORDERS, shading=C["divider"], margins=(40, 80, 40, 80), span=8)])

    def data_row(m, alt):
        W = SCORECARD_WIDTHS
        sh = C["rowAlt"] if alt else None
        lb = bool(m.get("lowerIsBetter"))
        d_abs = render_cell(m.get("deltaAbsolute"))
        d_pct = m.get("deltaPercent")
        yoy = m.get("yoyChange")
        return row([
            make_cell(short_name(m.get("name")), width=W[0], shading=sh),
            make_cell(render_cell(m.get("priorActual")), align="right", width=W[1], shading=sh, color=C["ink"]),
            make_cell(render_cell(m.get("currentActual")), align="right", width=W[2], shading=sh, color=C["ink"]),
            make_cell(render_cell(m.get("currentEstimate")), align="right", width=W[3], shading=sh, color=C["ink"]),
            make_cell(d_abs, align="right", width=W[4], shading=sh, color=move_color(normalize_sign(d_abs), lb)),
            make_cell(d_pct, align="right", width=W[5], shading=sh, color=move_color(normalize_sign(d_pct), lb)),
            make_cell(yoy, align="right", width=W[6], shading=sh, color=move_color(normalize_sign(yoy), lb)),
            make_cell(m.get("beatMiss"), align="center", width=W[7], shading=sh, bold=True,
                      color=beat_miss_color(m.get("beatMiss"))),
        ])

    fin = [m for m in (data.get("financialMetrics") or []) if has_actual(m)]
    if fin:
        rows.append(divider("Key Financial Metrics"))
        rows += [data_row(m, i % 2 == 1) for i, m in enumerate(fin)]
    op = [m for m in (data.get("operatingMetrics") or []) if has_actual(m)][:5]
    if op:
        rows.append(divider("Top Operating Metrics"))
        rows += [data_row(m, i % 2 == 1) for i, m in enumerate(op)]
    return table(rows, SCORECARD_WIDTHS)


def model_sourced_note(data):
    rows = [m for m in (data.get("financialMetrics") or []) + (data.get("operatingMetrics") or [])
            if m and m.get("modelSourced") and has_actual(m)]
    if not rows:
        return []
    seen, prov = set(), []
    for m in rows:
        s = m.get("sourceDerived")
        if s and s not in seen:
            seen.add(s)
            prov.append(s)
    names = ", ".join(re.sub(r"\s*†\s*$", "", short_name(m.get("name"))) for m in rows)
    text = ("† %s — actual is model-sourced (%s), not company-reported; "
            "graded against consensus for comparison only." % (names, "; ".join(prov)))
    return [para([run(text, size=max(12, (D.get("disc") or 8) + 4), font=F_BODY,
                      color=C["textLight"], italics=True)], spacing={"after": 0, "before": 30})]


def orange_rule(before=40, after=140):
    return para([run("", size=2)], spacing={"after": after, "before": before},
                border_bottom=("single", C["orange"], 12, 2))


_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def format_long_date(v):
    if not v:
        return "N/A"
    s = _js_str(v).strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if not m:
        return s
    mi = int(m.group(2)) - 1
    if mi < 0 or mi > 11:
        return s
    return "%s %d, %s" % (_MONTHS[mi], int(m.group(3)), m.group(1))


_GLYPH = {}
for _chars, _w in [
    ("  ", 254), ("'‘’", 269), (",.", 328), ("·", 338), ("l", 344), ("j", 346),
    ("iìíîï", 354), (":;", 367), ("!¡", 376), ("-­", 379), ("|¦", 388),
    ("f", 393), ("t", 397), ("°", 420), ("IÌÍÎÏ", 446), ("()[]", 447), ("/\\", 472),
    ("*", 482), ("1", 490), ("`{}¨´¸", 500), ('"', 510), ("s", 513), ("“”", 519),
    ("r", 520), ("z", 525), ("cç", 531), ("?¿", 548), ("¶", 549),
    ("ª²³¹º", 552), ("7", 554), ("yýÿ", 562), ("§", 563), ("v", 567),
    ("eèéêë", 572), ("g", 577), ("x", 588), ("J", 595),
    ("aàáâãäå", 596), ("5", 599), ("¢", 605), ("«»", 610),
    ("3", 625), ("2", 626), ("k", 632), ("oòóôõöø", 636), ("ð", 638),
    ("$", 641), ("þ", 645), ("b", 646), ("69q", 648), ("4S", 649), ("pß", 658), ("d", 663),
    ("µ", 668), ("F", 671), ("8", 676), ("uùúûü", 677), ("h", 680), ("T", 684),
    ("L", 686), ("Z", 689), ("n£ñ", 690), ("0P", 701),
    ("#+<=>^_~¤¬¯±×÷–−", 703), ("Þ", 708),
    ("CÇ€", 715), ("EÈÉÊË", 721), ("Y¥Ý", 732), ("B", 757),
    ("AÀÁÂÃÄÅ", 758), ("V", 762), ("R", 797), ("&", 799), ("G", 807),
    ("X", 809), ("K", 817), ("OQÒÓÔÕÖØ", 820), ("UÙÚÛÜ", 833),
    ("DÐ", 834), ("NÑ", 839), ("æ", 857), ("w", 863), ("%", 879), ("H", 913),
    ("—", 928), ("©®", 942), ("@", 967), ("m", 1016), ("M", 1023), ("Æ", 1070),
    ("¼½¾", 1071), ("W", 1126)]:
    for _c in _chars:
        _GLYPH[_c] = _w
_GLYPH_DEFAULT = 669
SUBSTITUTE_WIDTH_FACTOR = 1.12


def _face_scale():
    forced = os.environ.get("INSYNC_FACE_WIDTHS")
    if forced == "georgia":
        return 1
    if forced == "substitute":
        return SUBSTITUTE_WIDTH_FACTOR
    want, accepted = "Georgia", ["Georgia"]
    try:
        for up in range(1, 5):
            cand = os.path.join(HERE, *([".."] * up), "common", "brand.json")
            if not os.path.exists(cand):
                continue
            with open(cand, encoding="utf-8") as f:
                m = json.load(f).get("masthead") or {}
            product = os.path.basename(os.path.dirname(os.path.dirname(cand)))
            want = (m.get("face_overrides") or {}).get(product) or m.get("face") or "Georgia"
            accepted = [want] + list((m.get("face_accepted_substitutes") or {}).get(want) or [])
            break
    except Exception:
        pass
    try:
        resolved = subprocess.run(["fc-match", "-f", "%{family}", want + ":bold"], capture_output=True,
                                  text=True, timeout=10).stdout.strip()
    except Exception:
        return 1
    if not resolved:
        return 1
    flat = re.sub(r"\s+", "", resolved).lower()
    if any(re.sub(r"\s+", "", a).lower() in flat for a in accepted):
        return 1
    sys.stderr.write('[masthead] "%s" is NOT installed on this box - fontconfig resolves it to "%s", '
                     "which is not a metric-faithful stand-in. Sizing the company line for the substitute "
                     "so it cannot wrap. Install %s to render at the intended size.\n" % (want, resolved, want))
    return SUBSTITUTE_WIDTH_FACTOR


FACE_SCALE = None


def text_width_pt(s, pt):
    mille = sum(_GLYPH.get(ch, _GLYPH_DEFAULT) for ch in _js_str(s))
    return mille * pt * FACE_SCALE / 1000


def _ceil(x):
    return int(math.ceil(x))


def title_block(data):
    now = _dt.date.today()
    prepared = format_long_date(os.environ.get("EF_PREPARED_DATE") or
                                "%04d-%02d-%02d" % (now.year, now.month, now.day))
    DATE_SIZE, EF_SIZE = 18, 32
    box = ("single", C["mastBorder"], 8)
    right_pad = 120
    right_need = max(text_width_pt("Earnings Flash", EF_SIZE / 2),
                     text_width_pt("Prepared: " + prepared, DATE_SIZE / 2),
                     text_width_pt("Reported: " + format_long_date(data.get("earningsDate")), DATE_SIZE / 2))
    RIGHTW = min(3460, max(2600, _ceil(right_need * 20) + 220 + right_pad))
    MIDW = COLW - 1150 - RIGHTW
    CO_SIZE, BRAND_SIZE = 28, 33
    BUDGET_PT = (MIDW - 220 - 60) / 20

    def fits(line, sz):
        return text_width_pt(line, sz / 2) <= BUDGET_PT * 0.97

    suffix = " (%s) - %s" % (_js_str(data.get("ticker")), _js_str(data.get("currentPeriod")))
    co_name = _js_str(data.get("companyName"))
    co_size = CO_SIZE
    for s in (28, 27, 26, 25, 24):
        co_size = s
        if fits(co_name + suffix, s):
            break
    two_line = not fits(co_name + suffix, co_size)
    TWO_SIZE = 20
    l1, l2 = co_name, None
    if two_line:
        words = (co_name + suffix).split(" ")

        def w(t):
            return text_width_pt(t, TWO_SIZE / 2)
        cap = BUDGET_PT * 0.95
        best = None
        for i in range(1, len(words)):
            a, b = " ".join(words[:i]), " ".join(words[i:])
            wa, wb = w(a), w(b)
            if wa > cap or wb > cap:
                continue
            score = abs(wa - wb)
            if best is None or score < best[2]:
                best = (a, b, score)
        if best:
            l1, l2 = best[0], best[1]
        else:
            acc = []
            while words and w(" ".join(acc + [words[0]])) <= cap:
                acc.append(words.pop(0))
            if not acc:
                acc.append(words.pop(0))
            l1, l2 = " ".join(acc), " ".join(words) or " "
    brand_size = BRAND_SIZE
    if not two_line:
        drop = (CO_SIZE - co_size) / 2
        brand_twips = 370
        co_line = _js_round(372 + 5.5 * drop)
    else:
        brand_size = (BRAND.get("masthead") or {}).get("brand_size_two_line_half_points") or 24
        brand_twips = 280
        co_line = 226
        co_size = TWO_SIZE
    mark_h, mark_w = 46, 59
    mark_up = 3 if two_line else _js_round(13 + 5.5 * ((CO_SIZE - co_size) / 2))
    margins = (70, 110, 70, 110)

    emu_w, emu_h = mark_w * 9525, mark_h * 9525
    drawing = (
        '<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="%d" cy="%d"/>'
        '<wp:effectExtent t="0" r="0" b="0" l="0"/><wp:docPr id="1" name="" descr="" title=""/>'
        '<wp:cNvGraphicFramePr><a:graphicFrameLocks xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" noChangeAspect="1"/></wp:cNvGraphicFramePr>'
        '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:nvPicPr><pic:cNvPr id="0" name="" descr=""/>'
        '<pic:cNvPicPr><a:picLocks noChangeAspect="1" noChangeArrowheads="1"/></pic:cNvPicPr></pic:nvPicPr>'
        '<pic:blipFill><a:blip r:embed="rId8" cstate="none"/><a:srcRect/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
        '<pic:spPr bwMode="auto"><a:xfrm><a:off x="0" y="0"/><a:ext cx="%d" cy="%d"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic>'
        '</wp:inline></w:drawing></w:r>') % (emu_w, emu_h, emu_w, emu_h)
    logo_p = "<w:p><w:pPr>%s<w:jc w:val=\"left\"/></w:pPr>%s</w:p>" % (_spacing({"after": mark_up}), drawing)
    logo = cell([logo_p], width=1150, borders={"top": box, "left": box, "bottom": box, "right": "none"},
                margins=margins, valign="bottom")

    tk = "(%s)" % _js_str(data.get("ticker"))

    def co_runs(text, sz):
        def mk(t, c):
            return run(t, size=sz, font=F_TITLE, color=c, bold=True)
        i = text.find(tk)
        if i < 0:
            return [mk(text, C["ink"])]
        out = []
        if i > 0:
            out.append(mk(text[:i], C["ink"]))
        out.append(mk(tk, C["orange"]))
        if i + len(tk) < len(text):
            out.append(mk(text[i + len(tk):], C["ink"]))
        return out

    def co_para(text, sz):
        return para(co_runs(text, sz), spacing={"after": 0, "line": co_line, "lineRule": "exact"})

    brand_p = para([run("InSync", size=brand_size, font=F_TITLE, color=C["orange"], bold=True),
                    run(" Analytics", size=brand_size, font=F_TITLE, color=C["ink"])],
                   spacing={"after": 0, "line": brand_twips, "lineRule": "exact"})
    mid_kids = [brand_p] + ([co_para(l1, co_size), co_para(l2, co_size)] if two_line
                            else [co_para(co_name + suffix, co_size)])
    mid = cell(mid_kids, width=MIDW, borders={"top": box, "left": "none", "bottom": box, "right": "none"},
               margins=margins, valign="bottom")
    right = cell([
        para([run("Earnings Flash", size=EF_SIZE, font=F_TITLE, color=C["ink"], bold=True)],
             spacing={"after": 30}, align="right"),
        para([run("Prepared: ", size=DATE_SIZE, font=F_TITLE, color=C["orange"], bold=True),
              run(prepared, size=DATE_SIZE, font=F_TITLE, color=C["textLight"])], align="right"),
        para([run("Reported: ", size=DATE_SIZE, font=F_TITLE, color=C["orange"], bold=True),
              run(format_long_date(data.get("earningsDate")), size=DATE_SIZE, font=F_TITLE,
                  color=C["textLight"])], align="right"),
    ], width=RIGHTW, borders={"top": box, "left": "none", "bottom": box, "right": box},
        margins=margins, valign="top")
    b = '<w:%s w:val="single" w:color="%s" w:sz="8"/>'
    tb = ("<w:tblBorders>" + "".join(b % (s, C["mastBorder"]) for s in ("top", "left", "bottom", "right"))
          + '<w:insideH w:val="none"/><w:insideV w:val="none"/></w:tblBorders>')
    return table([row([logo, mid, right])], [1150, MIDW, RIGHTW], borders_xml=tb)


def header_section(data):
    close = _js_str(data.get("lastClosePrice") or "").strip()
    out = []
    val = data.get("valuationLine")
    if close and close != "N/A":
        out.append(para([run("Last Close: ", size=18, font=F_BODY, color=C["textDark"], bold=True),
                         run(close, size=18, font=F_BODY, color=C["textDark"])],
                        spacing={"after": 10 if val else 70}, align="right"))
    if val and _js_str(val).strip():
        out.append(para([run("Valuation: ", size=16, font=F_BODY, color=C["orange"], bold=True),
                         run(sanitize_text(val), size=16, font=F_BODY, color=C["textLight"])],
                        spacing={"after": 70}, align="right"))
    return out


def section_header(text):
    return para([run(text, size=D["headSize"], font=F_BODY, color=C["ink"], bold=True)],
                spacing={"after": D["headAfter"], "before": D["headBefore"]}, keep_next=True,
                border_bottom=("single", C["orange"], 12, 3))


def bullet(text):
    return para([run(sanitize_text(text), size=D["bullet"], font=F_BODY, color=C["textDark"])],
                spacing={"after": D["bulletAfter"]}, bullet=True)


def _truthy_str(v):
    return bool(v) and bool(_js_str(v).strip())


def verdict_block(v):
    lines = []
    if isinstance(v, dict):
        lines = [(lbl, val, col) for lbl, val, col in (
            ("Positives", v.get("positives"), C["beatGreen"]),
            ("Negatives", v.get("negatives"), C["missRed"]),
            ("Net", v.get("net"), C["orangeDk"])) if _truthy_str(val)]
    elif v is not None and not isinstance(v, list) and _truthy_str(v):
        lines = [(None, _js_str(v).strip(), None)]
    if not lines:
        return []
    body = []
    for i, (lbl, text, col) in enumerate(lines):
        rs = []
        if lbl:
            rs.append(run(lbl + ":  ", size=D["verdict"], font=F_BODY, color=col, bold=True))
        rs.append(run(sanitize_text(_js_str(text)), size=D["verdict"], font=F_BODY, color=C["ink"],
                      bold=not lbl))
        body.append(para(rs, spacing={"after": 0 if i == len(lines) - 1 else 22}))
    hair = ("single", C["tableBorder"], 4)
    label = para([run("FLASH VERDICT", size=D["vlabel"], font=F_BODY, color=C["orangeDk"], bold=True,
                      char_spacing=12)], spacing={"after": 16})
    c = cell([label] + body, borders={"top": hair, "left": ("single", C["orange"], 24), "bottom": hair,
                                      "right": hair},
             shading=C["calloutBg"], margins=(D["verdictMargin"], 180, D["verdictMargin"], 180))
    return [table([row([c])], [COLW]), para(spacing={"after": 40})]


def investment_read(text):
    txt = sanitize_text(_js_str(text or "")).strip()
    if not txt:
        return []
    o8, o24 = ("single", C["orange"], 8), ("single", C["orange"], 24)
    m = _js_round(D["verdictMargin"] * 0.8)
    c = cell([
        para([run("INVESTMENT READ", size=D["vlabel"], font=F_BODY, color=C["orangeDk"], bold=True,
                  char_spacing=12)], spacing={"after": 10}),
        para([run(txt, size=D["readSize"], font=F_BODY, color=C["ink"], bold=True)]),
    ], borders={"top": o8, "left": o24, "bottom": o8, "right": o8}, shading=C["bandFill"],
        margins=(m, 180, m, 180))
    return [table([row([c])], [COLW]), para(spacing={"after": 40})]


def variant_view(vv):
    if not isinstance(vv, dict):
        return []
    uses_new = any(_truthy_str(vv.get(k)) for k in ("street", "expectationGap", "evidence", "whyItMatters"))
    rows = ([("Street", vv.get("street")), ("Expectation Gap", vv.get("expectationGap")),
             ("Evidence", vv.get("evidence")), ("Why It Matters", vv.get("whyItMatters"))] if uses_new else
            [("Street Expected", vv.get("streetExpected")), ("What Changed", vv.get("whatChanged")),
             ("Why the Stock Is Moving", vv.get("whyMoving")), ("Potential Variant", vv.get("potentialVariant"))])
    rows = [(l, t) for l, t in rows if _truthy_str(t)]
    if not rows:
        return []
    out = [section_header("Variant View")]
    for lbl, t in rows:
        out.append(para([run(lbl + ": ", size=D["variant"], font=F_BODY, color=C["orange"], bold=True),
                         run(sanitize_text(t), size=D["variant"], font=F_BODY, color=C["textDark"])],
                        spacing={"after": D["variantAfter"]}, bullet=True))
    return out


PRESETS = {
    "comfortable": dict(bullet=17, bulletAfter=6, headBefore=20, headAfter=15, headSize=25, verdict=22, vlabel=15,
                        readSize=20, verdictMargin=80, variant=17, variantAfter=18, cell=16, cellMargin=18, disc=8,
                        discLine=135, tableAfter=200),
    "tight": dict(bullet=16, bulletAfter=4, headBefore=14, headAfter=10, headSize=23, verdict=20, vlabel=14,
                  readSize=18, verdictMargin=60, variant=16, variantAfter=12, cell=15, cellMargin=13, disc=8,
                  discLine=125, tableAfter=120),
    "ultra": dict(bullet=15, bulletAfter=2, headBefore=10, headAfter=6, headSize=22, verdict=19, vlabel=14,
                  readSize=17, verdictMargin=50, variant=15, variantAfter=8, cell=14, cellMargin=10, disc=7,
                  discLine=115, tableAfter=70),
}
MARGIN_V, FOOTER_RESERVE = 720, 680
PAGE_BUDGET = 15840 - MARGIN_V * 2 - FOOTER_RESERVE
FIT_SAFETY = 1.10


def _cpl(half):
    return max(42, _js_round(95 * (COLW / 9840) * 17 / half))


def _lh(half):
    return _js_round(half * 11.5)


def _lines(text, half):
    return max(1, _ceil(_js_len(_js_str(text or "")) / _cpl(half)))


def _as_list(v):
    if isinstance(v, list):
        return v
    return [v] if v else []


def estimate_body(data):
    h = 900
    h += _lh(18) + (_lh(16) if data.get("valuationLine") else 0) + 130
    v = data.get("flashVerdict")
    vt = []
    if isinstance(v, dict):
        vt = [v.get("positives"), v.get("negatives"), v.get("net")]
    elif v is not None and not isinstance(v, list) and _truthy_str(v):
        vt = [v]
    vt = [x for x in vt if _truthy_str(x)]
    if vt:
        h += _lh(D["vlabel"]) + D["verdictMargin"] * 2 + 120
        for t in vt:
            h += _lines(t, D["verdict"]) * _lh(D["verdict"]) + 24
    if _truthy_str(data.get("investmentRead")):
        h += (_lh(D["vlabel"]) + 10 + _lines(data["investmentRead"], D["readSize"]) * _lh(D["readSize"])
              + _js_round(D["verdictMargin"] * 1.6) + 100)

    def HH():
        return _lh(D["headSize"]) + D["headBefore"] + D["headAfter"] + 40

    def bullets(arr, cap=None):
        nonlocal h
        arr = arr or []
        for b in (arr if cap is None else arr[:cap]):
            h += _lines(b, D["bullet"]) * _lh(D["bullet"]) + D["bulletAfter"] + 20

    wm = data.get("whatMatters") if isinstance(data.get("whatMatters"), list) else []
    if wm:
        h += HH()
        bullets(wm, 5)
    fin = len([m for m in (data.get("financialMetrics") or []) if has_actual(m)])
    op = min(5, len([m for m in (data.get("operatingMetrics") or []) if has_actual(m)]))
    rows = 1 + (1 + fin if fin else 0) + (1 + op if op else 0)
    h += rows * (_lh(D["cell"]) + D["cellMargin"] * 2 + 44) + D["tableAfter"]
    vv = data.get("variantView") or {}
    if not isinstance(vv, dict):
        vv = {}
    new_keys = ["street", "expectationGap", "evidence", "whyItMatters"]
    keys = new_keys if any(_truthy_str(vv.get(k)) for k in new_keys) else \
        ["streetExpected", "whatChanged", "whyMoving", "potentialVariant"]
    vtexts = [vv.get(k) for k in keys if _truthy_str(vv.get(k))]
    if vtexts:
        h += HH()
        for t in vtexts:
            h += _lines(t, D["variant"]) * _lh(D["variant"]) + D["variantAfter"] + 10
    g = _as_list(data.get("guidance"))
    fwd = _as_list(data.get("forwardView"))
    slot = g if g else fwd
    if slot:
        h += HH()
        bullets(slot)
    w = data.get("nextWatchItems") if isinstance(data.get("nextWatchItems"), list) else []
    if w:
        h += HH()
        bullets(w, 3)
    h += 5 * _lh(D["disc"]) + 220
    return h


def choose_density(data):
    global D
    forced = os.environ.get("EF_DENSITY")
    if forced and forced in PRESETS:
        D = PRESETS[forced]
        return forced
    for name in ("comfortable", "tight", "ultra"):
        D = PRESETS[name]
        if estimate_body(data) * FIT_SAFETY <= PAGE_BUDGET:
            return name
    D = PRESETS["ultra"]
    return "ultra"


_NS = ('xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas" '
       'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
       'xmlns:o="urn:schemas-microsoft-com:office:office" '
       'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
       'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" '
       'xmlns:v="urn:schemas-microsoft-com:vml" '
       'xmlns:wp14="http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing" '
       'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
       'xmlns:w10="urn:schemas-microsoft-com:office:word" '
       'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
       'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" '
       'xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml" '
       'xmlns:wpg="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup" '
       'xmlns:wpi="http://schemas.microsoft.com/office/word/2010/wordprocessingInk" '
       'xmlns:wne="http://schemas.microsoft.com/office/word/2006/wordml" '
       'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"')
_NS_DOC_EXTRA = (' xmlns:cx="http://schemas.microsoft.com/office/drawing/2014/chartex"'
                 ' xmlns:cx1="http://schemas.microsoft.com/office/drawing/2015/9/8/chartex"'
                 ' xmlns:cx2="http://schemas.microsoft.com/office/drawing/2015/10/21/chartex"'
                 ' xmlns:cx3="http://schemas.microsoft.com/office/drawing/2016/5/9/chartex"'
                 ' xmlns:cx4="http://schemas.microsoft.com/office/drawing/2016/5/10/chartex"'
                 ' xmlns:cx5="http://schemas.microsoft.com/office/drawing/2016/5/11/chartex"'
                 ' xmlns:cx6="http://schemas.microsoft.com/office/drawing/2016/5/12/chartex"'
                 ' xmlns:cx7="http://schemas.microsoft.com/office/drawing/2016/5/13/chartex"'
                 ' xmlns:cx8="http://schemas.microsoft.com/office/drawing/2016/5/14/chartex"'
                 ' xmlns:aink="http://schemas.microsoft.com/office/drawing/2016/ink"'
                 ' xmlns:am3d="http://schemas.microsoft.com/office/drawing/2017/model3d"'
                 ' xmlns:w16cex="http://schemas.microsoft.com/office/word/2018/wordml/cex"'
                 ' xmlns:w16cid="http://schemas.microsoft.com/office/word/2016/wordml/cid"'
                 ' xmlns:w16="http://schemas.microsoft.com/office/word/2018/wordml"'
                 ' xmlns:w16sdtdh="http://schemas.microsoft.com/office/word/2020/wordml/sdtdatahash"'
                 ' xmlns:w16se="http://schemas.microsoft.com/office/word/2015/wordml/symex"')


def build_document_xml(data):
    kids = [title_block(data), orange_rule(30, 80)]
    kids += header_section(data)
    kids += verdict_block(data.get("flashVerdict"))
    kids += investment_read(data.get("investmentRead"))
    wm = data.get("whatMatters") if isinstance(data.get("whatMatters"), list) else []
    if wm:
        kids.append(section_header("What Matters"))
        kids += [bullet(b) for b in wm[:5]]
    kids.append(variance_table(data))
    kids += model_sourced_note(data)
    kids.append(para(spacing={"after": D["tableAfter"]}))
    kids += variant_view(data.get("variantView"))
    g = _as_list(data.get("guidance"))
    fwd = _as_list(data.get("forwardView"))
    if g:
        kids.append(section_header("Guidance Snapshot"))
        kids += [bullet(x) for x in g]
    elif fwd:
        kids.append(section_header("Forward View — company does not guide"))
        kids += [bullet(x) for x in fwd]
    nw = data.get("nextWatchItems") if isinstance(data.get("nextWatchItems"), list) else []
    if nw:
        kids.append(section_header("Next Key Watch Items"))
        kids += [bullet(x) for x in nw[:3]]
    kids.append(para([run("", size=2)], spacing={"after": 10}))
    kids.append(para([run("General Disclosure", size=D["disc"], font=F_BODY, color=C["textLight"], bold=True),
                      run(": " + BRAND["disclosure"], size=D["disc"], font=F_BODY, color=C["textLight"])],
                     spacing={"after": 0, "before": 0, "line": D["discLine"], "lineRule": "atLeast"},
                     border_top=("single", C["footerBorder"], 4, 4)))
    sect = ('<w:sectPr><w:footerReference w:type="default" r:id="rId7"/>'
            '<w:pgSz w:w="12240" w:h="15840" w:orient="portrait"/>'
            '<w:pgMar w:top="720" w:right="720" w:bottom="720" w:left="720" w:header="708" w:footer="708" w:gutter="0"/>'
            '<w:pgNumType/><w:docGrid w:linePitch="360"/></w:sectPr>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document mc:Ignorable="w14 w15 wp14" '
            + _NS + _NS_DOC_EXTRA + "><w:body>" + "".join(kids) + sect + "</w:body></w:document>")


def build_footer_xml(data):
    text = "%s  |  %s, fiscal · InSync MCP  |  %s %s Earnings Flash" % (
        BRAND["firmName"], data.get("basis") or "Adjusted (Non-GAAP)", _js_str(data.get("ticker")),
        _js_str(data.get("currentPeriod")))
    f = '<w:rFonts w:ascii="%s" w:cs="%s" w:eastAsia="%s" w:hAnsi="%s"/>' % ((F_BODY,) * 4)
    rp = '<w:rPr>%s<w:color w:val="%s"/><w:sz w:val="14"/><w:szCs w:val="14"/></w:rPr>' % (f, C["textLight"])
    fld = lambda instr: ('<w:fldChar w:fldCharType="begin"/><w:instrText xml:space="preserve">%s</w:instrText>'
                         '<w:fldChar w:fldCharType="separate"/><w:fldChar w:fldCharType="end"/>' % instr)
    p = ('<w:p><w:pPr><w:pBdr><w:top w:val="single" w:color="%s" w:sz="6" w:space="4"/></w:pBdr>'
         '<w:tabs><w:tab w:val="right" w:pos="%d"/></w:tabs><w:spacing w:before="80"/></w:pPr>'
         '<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r>'
         '<w:r><w:rPr><w:sz w:val="14"/><w:szCs w:val="14"/></w:rPr><w:t xml:space="preserve">\t</w:t></w:r>'
         '<w:r>%s<w:t xml:space="preserve">Page </w:t>%s<w:t xml:space="preserve"> of </w:t>%s</w:r></w:p>') % (
        C["footerBorder"], COLW, rp, _esc_text(text), rp, fld("PAGE"), fld("NUMPAGES"))
    return '<?xml version="1.0" encoding="UTF-8"?><w:ftr ' + _NS + ">" + p + "</w:ftr>"


_ZIP_ORDER = [
    "word/_rels/document.xml.rels", "word/document.xml", "word/styles.xml", "docProps/core.xml",
    "word/numbering.xml", "_rels/.rels", "word/_rels/footer1.xml.rels", "word/footer1.xml",
    "[Content_Types].xml", "docProps/custom.xml", "docProps/app.xml", "word/footnotes.xml",
    "word/_rels/footnotes.xml.rels", "word/endnotes.xml", "word/_rels/endnotes.xml.rels",
    "word/settings.xml", "word/comments.xml", "word/_rels/comments.xml.rels", "word/fontTable.xml",
    "word/_rels/fontTable.xml.rels",
]


def write_docx(data, out_path):
    tpl = os.path.join(HERE, "docx_template")
    parts = {}
    for name in _ZIP_ORDER:
        p = os.path.join(tpl, *name.split("/"))
        if os.path.exists(p):
            with open(p, "rb") as f:
                parts[name] = f.read()
    with open(os.path.join(HERE, "..", "assets", "insync-logo-mark.png"), "rb") as f:
        logo = f.read()
    media = "media/%s.png" % hashlib.sha1(logo).hexdigest()
    rels = parts["word/_rels/document.xml.rels"].decode("utf-8")
    rels = re.sub(r'Target="media/[0-9a-f]+\.png"', 'Target="%s"' % media, rels)
    parts["word/_rels/document.xml.rels"] = rels.encode("utf-8")
    parts["word/document.xml"] = build_document_xml(data).encode("utf-8")
    parts["word/footer1.xml"] = build_footer_xml(data).encode("utf-8")
    now = _dt.datetime.now(_dt.timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (now.microsecond // 1000)
    parts["docProps/core.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties '
        'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        "<dc:creator>Un-named</dc:creator><cp:lastModifiedBy>Un-named</cp:lastModifiedBy><cp:revision>1</cp:revision>"
        '<dcterms:created xsi:type="dcterms:W3CDTF">%s</dcterms:created>'
        '<dcterms:modified xsi:type="dcterms:W3CDTF">%s</dcterms:modified></cp:coreProperties>' % (stamp, stamp)
    ).encode("utf-8")
    buf = io.BytesIO()
    seen_dirs = set()

    def _put(z, name, blob):
        segs = name.split("/")[:-1]
        for i in range(1, len(segs) + 1):
            d = "/".join(segs[:i]) + "/"
            if d not in seen_dirs:
                seen_dirs.add(d)
                z.writestr(zipfile.ZipInfo(d), b"")
        z.writestr(name, blob)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in _ZIP_ORDER:
            if name in parts:
                _put(z, name, parts[name])
        _put(z, "word/" + media, logo)
    with open(out_path, "wb") as f:
        f.write(buf.getvalue())


def main(argv):
    global FACE_SCALE
    a = _parse_args(argv)
    try:
        with open(a["input"], encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        sys.stderr.write("Error reading input: %s\n" % e)
        return 1
    errs = _cross_check(data, a["computed"])
    if errs:
        sys.stderr.write(json.dumps({"status": "validation_error", "stage": "crosscheck", "errors": errs}) + "\n")
        return 1
    errs = _validate(data)
    if errs:
        sys.stderr.write(json.dumps({"status": "validation_error", "stage": "input", "errors": errs}) + "\n")
        return 1
    FACE_SCALE = _face_scale()
    density = choose_density(data)
    try:
        write_docx(data, a["output"])
    except Exception as e:
        sys.stderr.write(json.dumps({"status": "error", "message": str(e)}) + "\n")
        return 1
    print(json.dumps({"status": "success", "output_file": a["output"], "document_type": "earnings_flash",
                      "company": data.get("companyName"), "ticker": data.get("ticker"),
                      "period": data.get("currentPeriod"), "density": density,
                      "est_body_twips": estimate_body(data), "page_budget": PAGE_BUDGET,
                      "renderer": "python", "data_path": data.get("dataPath") or "A",
                      "tools_used": data.get("toolsUsed") or []}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
