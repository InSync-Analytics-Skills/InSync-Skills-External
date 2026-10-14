#!/usr/bin/env python3
"""
docx_wrap.py - the Word (.docx) edition of a wrap, for machines where weasyprint cannot run.

  python references/docx_wrap.py <validated.html> <out.docx>

It TYPESETS THE VALIDATED HTML that render_wrap.py wrote - every heading, bullet, table cell,
caption and colour cue, in order - and computes nothing. So the .docx carries exactly the words and
figures the 23 gates and 11 validation layers passed; `text_parity()` proves it on every build and
the build stops if a single character of visible text differs.

Standard library only (html.parser, zipfile), so it runs wherever Python runs. Styling follows the
page's own CSS: Calibri body, Georgia masthead, the house orange rules and bands, green/red cues,
zebra rows, letter paper with 0.5in margins, and the footer line with "Page X of Y".
"""
import base64
import html
import html.parser
import io
import re
import sys
import zipfile

ORANGE, ORANGE_DK, DARK, BODY, GREY, GREY2 = "E8850C", "B25E08", "1A1A1A", "222222", "6E6E6E", "555555"
GREEN, RED = "1E7A34", "B02A1F"
PANEL, HEAD_BG, BAND_BG, ZEBRA = "F6F3EE", "EDEDED", "F0E9D8", "F5F0E8"
HEAD_RULE, ROW_RULE, MAST_RULE, SRC_RULE = "BBBBBB", "EEEEEE", "C9C9C9", "DDDDDD"
TEXT_W = 10800
_HP = lambda pt: str(int(round(pt * 2)))


def _x(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


class _Node(object):
    __slots__ = ("tag", "attrs", "kids", "parent")

    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.kids, self.parent = tag, dict(attrs), [], parent

    def cls(self):
        return (self.attrs.get("class") or "").split()


class _Parser(html.parser.HTMLParser):
    VOID = {"img", "br", "col", "meta", "link", "hr", "input"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {}, None)
        self.cur = self.root
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script", "head", "title"):
            self.skip += 1
            return
        if self.skip:
            return
        n = _Node(tag, attrs, self.cur)
        self.cur.kids.append(n)
        if tag not in self.VOID:
            self.cur = n

    def handle_startendtag(self, tag, attrs):
        if not self.skip:
            self.cur.kids.append(_Node(tag, attrs, self.cur))

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head", "title"):
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or tag in self.VOID:
            return
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        if not self.skip and data:
            self.cur.kids.append(data)


def parse(html_text):
    p = _Parser()
    p.feed(html_text)
    return p.root


def _find(n, pred):
    if isinstance(n, str):
        return None
    if pred(n):
        return n
    for k in n.kids:
        r = _find(k, pred)
        if r is not None:
            return r
    return None


def _text(n):
    if isinstance(n, str):
        return n
    return "".join(_text(k) for k in n.kids)


def _runs(n, base):
    out = []

    def walk(x, st):
        if isinstance(x, str):
            t = re.sub(r"\s+", " ", x)
            if t:
                out.append((t, st))
            return
        s = dict(st)
        c = x.cls()
        if x.tag in ("b", "strong"):
            s["b"] = True
        if x.tag in ("i", "em"):
            s["i"] = True
        m = re.search(r"color:\s*#([0-9A-Fa-f]{6})", x.attrs.get("style", ""))
        if m:
            s["color"] = m.group(1).upper()
        if "up" in c:
            s.update(color=GREEN, b=True)
        if "dn" in c:
            s.update(color=RED, b=True)
        if "mid" in c and x.tag == "span":
            s["color"] = GREY
        if "tag" in c:
            s.update(color=GREY, sz=6.3, b=False)
        if "mflab" in c:
            s.update(color=ORANGE_DK, b=True)
        if "sn" in c:
            s["color"] = ORANGE
        if "tk" in c or "i" in c:
            s["color"] = ORANGE
        if "a" in c:
            s["color"] = DARK
        for k in x.kids:
            walk(k, s)
    for k in (n.kids if not isinstance(n, str) else [n]):
        walk(k, dict(base))
    if out:
        out[0] = (out[0][0].lstrip(), out[0][1])
        out[-1] = (out[-1][0].rstrip(), out[-1][1])
    return [(t, s) for t, s in out if t]


def _rxml(runs):
    parts = []
    for t, s in runs:
        rpr = []
        font = s.get("font")
        if font:
            rpr.append('<w:rFonts w:ascii="%s" w:hAnsi="%s" w:cs="%s"/>' % (font, font, font))
        if s.get("b"):
            rpr.append("<w:b/>")
        if s.get("i"):
            rpr.append("<w:i/>")
        if s.get("color"):
            rpr.append('<w:color w:val="%s"/>' % s["color"])
        if s.get("sz"):
            rpr.append('<w:sz w:val="%s"/><w:szCs w:val="%s"/>' % (_HP(s["sz"]), _HP(s["sz"])))
        parts.append('<w:r><w:rPr>%s</w:rPr><w:t xml:space="preserve">%s</w:t></w:r>'
                     % ("".join(rpr), _x(t)))
    return "".join(parts)


def _p(runs, align=None, before=0, after=0, keep_next=False, border_bottom=None, border_top=None,
       shade=None, left_border=None, ind_left=None, numbered=False, line=None, keep_lines=False):
    ppr = []
    if numbered:
        ppr.append('<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>')
    if keep_next:
        ppr.append("<w:keepNext/>")
    if keep_lines:
        ppr.append("<w:keepLines/>")
    b = []
    if border_top:
        b.append('<w:top w:val="single" w:sz="%d" w:space="3" w:color="%s"/>' % border_top)
    if left_border:
        b.append('<w:left w:val="single" w:sz="%d" w:space="6" w:color="%s"/>' % left_border)
    if border_bottom:
        b.append('<w:bottom w:val="single" w:sz="%d" w:space="1" w:color="%s"/>' % border_bottom)
    if b:
        ppr.append("<w:pBdr>%s</w:pBdr>" % "".join(b))
    if shade:
        ppr.append('<w:shd w:val="clear" w:color="auto" w:fill="%s"/>' % shade)
    sp = '<w:spacing w:before="%d" w:after="%d"' % (before, after)
    if line:
        sp += ' w:line="%d" w:lineRule="auto"' % line
    ppr.append(sp + "/>")
    if ind_left is not None:
        ppr.append('<w:ind w:left="%d"/>' % ind_left)
    if align:
        ppr.append('<w:jc w:val="%s"/>' % align)
    return "<w:p><w:pPr>%s</w:pPr>%s</w:p>" % ("".join(ppr), _rxml(runs))


class _Doc(object):
    def __init__(self):
        self.body = []
        self.logo = None


def _cell(content_xml, w, fill=None, bottom=None, top=None, left=None, right=None, span=1, valign=None,
          margins=(40, 110, 40, 110)):
    tcpr = ['<w:tcW w:w="%d" w:type="dxa"/>' % w]
    if span > 1:
        tcpr.append('<w:gridSpan w:val="%d"/>' % span)
    bd = []
    for side, v in (("top", top), ("left", left), ("bottom", bottom), ("right", right)):
        if v:
            bd.append('<w:%s w:val="single" w:sz="%d" w:space="0" w:color="%s"/>' % (side, v[0], v[1]))
        else:
            bd.append('<w:%s w:val="nil"/>' % side)
    tcpr.append("<w:tcBorders>%s</w:tcBorders>" % "".join(bd))
    if fill:
        tcpr.append('<w:shd w:val="clear" w:color="auto" w:fill="%s"/>' % fill)
    t, l, b, r = margins
    tcpr.append('<w:tcMar><w:top w:w="%d" w:type="dxa"/><w:left w:w="%d" w:type="dxa"/>'
                '<w:bottom w:w="%d" w:type="dxa"/><w:right w:w="%d" w:type="dxa"/></w:tcMar>' % (t, l, b, r))
    if valign:
        tcpr.append('<w:vAlign w:val="%s"/>' % valign)
    return "<w:tc><w:tcPr>%s</w:tcPr>%s</w:tc>" % ("".join(tcpr), content_xml or "<w:p/>")


def _tbl(rows_xml, widths, borders=None):
    grid = "".join('<w:gridCol w:w="%d"/>' % w for w in widths)
    bd = borders or ""
    return ('<w:tbl><w:tblPr><w:tblW w:w="%d" w:type="dxa"/><w:tblLayout w:type="fixed"/>%s'
            '<w:tblCellMar><w:left w:w="0" w:type="dxa"/><w:right w:w="0" w:type="dxa"/></w:tblCellMar>'
            '</w:tblPr><w:tblGrid>%s</w:tblGrid>%s</w:tbl>' % (sum(widths), bd, grid, rows_xml))


def _tr(cells_xml, header=False, cant_split=True):
    trpr = []
    if cant_split:
        trpr.append("<w:cantSplit/>")
    if header:
        trpr.append("<w:tblHeader/>")
    return "<w:tr><w:trPr>%s</w:trPr>%s</w:tr>" % ("".join(trpr), cells_xml)


def _masthead(doc, n):
    tds = [k for k in n.kids if not isinstance(k, str)]
    tr = next((k for k in tds if k.tag == "tr"), n)
    cells = [k for k in tr.kids if not isinstance(k, str) and k.tag == "td"]
    widths = [int(TEXT_W * f) for f in (0.10, 0.5524, 0.3476)]
    out = []
    for i, td in enumerate(cells[:3]):
        paras = []
        if "logo" in td.cls():
            img = _find(td, lambda x: x.tag == "img")
            src = (img.attrs.get("src") if img else "") or ""
            m = re.match(r"data:image/png;base64,(.*)$", src, re.S)
            if m:
                doc.logo = base64.b64decode(m.group(1))
                paras.append(_logo_para())
        else:
            for d in [k for k in td.kids if not isinstance(k, str)]:
                c = d.cls()
                m = re.search(r"font-size:\s*([\d.]+)pt", d.attrs.get("style", ""))
                if "brand" in c:
                    paras.append(_p(_runs(d, {"font": "Georgia", "sz": float(m.group(1)) if m else 16.5}), after=0))
                elif "co" in c:
                    paras.append(_p(_runs(d, {"font": "Georgia", "sz": float(m.group(1)) if m else 14,
                                              "b": True, "color": DARK}), after=0))
                elif "ew" in c:
                    paras.append(_p(_runs(d, {"font": "Georgia", "sz": 18, "b": True, "color": DARK}),
                                    align="right", after=20))
                elif "pr" in c:
                    rr = [(t, dict(s, color=ORANGE) if s.get("b") else s)
                          for t, s in _runs(d, {"font": "Georgia", "sz": 9, "color": GREY2})]
                    paras.append(_p(rr, align="right", after=0))
        rule = (6, MAST_RULE)
        out.append(_cell("".join(paras) or "<w:p/>", widths[i], top=rule, bottom=rule,
                         left=rule if i == 0 else None, right=rule if i == 2 else None,
                         valign="top" if i == 2 else "bottom", margins=(60, 100, 60, 100)))
    doc.body.append(_tbl(_tr("".join(out)), widths))
    doc.body.append(_p([], border_bottom=(14, ORANGE), before=40, after=110))


def _logo_para():
    cx, cy = 59 * 9525, 46 * 9525
    return ('<w:p><w:pPr><w:spacing w:before="0" w:after="0"/></w:pPr><w:r><w:drawing>'
            '<wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="%d" cy="%d"/>'
            '<wp:docPr id="1" name="logo"/><a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            '<pic:nvPicPr><pic:cNvPr id="1" name="logo.png"/><pic:cNvPicPr/></pic:nvPicPr>'
            '<pic:blipFill><a:blip r:embed="rIdLogo"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            '<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="%d" cy="%d"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic>'
            '</wp:inline></w:drawing></w:r></w:p>' % (cx, cy, cx, cy))


def _bullets(n, sz=9.3):
    out = []
    for li in [k for k in n.kids if not isinstance(k, str) and k.tag == "li"]:
        out.append(_p(_runs(li, {"sz": sz, "color": BODY}), numbered=True, after=30, line=250, keep_lines=True))
    return out


def _panel(doc, n, kind):
    paras = []
    for k in n.kids:
        if isinstance(k, str):
            if k.strip():
                paras.append(_p(_runs(k, {"sz": 9.3, "color": BODY}), after=20))
            continue
        c = k.cls()
        if k.tag == "h3":
            paras.append(_p(_runs(k, {"sz": 9, "b": True, "color": DARK}), after=60))
        elif "res" in c:
            paras.append(_p(_runs(k, {"sz": 8, "color": BODY}), after=60))
        elif "exlabel" in c:
            paras.append(_p(_runs(k, {"sz": 7, "b": True, "color": ORANGE_DK}), before=80, after=30, keep_next=True))
        elif k.tag == "ul":
            paras.extend(_bullets(k))
        else:
            paras.append(_p(_runs(k, {"sz": 9.3, "color": BODY}), after=20))
    if kind == "take":
        paras = [_p(_runs(n, {"sz": 9.3, "color": BODY}), after=0)]
    cell = _cell("".join(paras), TEXT_W, fill=PANEL, left=(20, ORANGE), margins=(90, 200, 100, 200))
    doc.body.append(_tbl(_tr(cell), [TEXT_W]))
    doc.body.append(_p([], after=60))


def _data_table(doc, n):
    cols = [c for c in _iter(n) if not isinstance(c, str) and c.tag == "col"]
    pcts = []
    for c in cols:
        m = re.search(r"width:\s*([\d.]+)%", c.attrs.get("style", ""))
        pcts.append(float(m.group(1)) if m else None)
    rows = [r for r in _iter(n) if not isinstance(r, str) and r.tag == "tr"]
    ncol = max([sum(int(c.attrs.get("colspan", 1)) for c in r.kids if not isinstance(c, str)
                    and c.tag in ("td", "th")) for r in rows] + [len(pcts), 1])
    if len(pcts) != ncol or any(p is None for p in pcts):
        pcts = [100.0 / ncol] * ncol
    widths = [int(round(TEXT_W * p / 100.0)) for p in pcts]
    widths[-1] += TEXT_W - sum(widths)
    out = []
    for r in rows:
        cells = [c for c in r.kids if not isinstance(c, str) and c.tag in ("td", "th")]
        header = bool(cells) and all(c.tag == "th" for c in cells)
        band = "band" in r.cls()
        zebra = "z" in r.cls()
        xs, col = [], 0
        for c in cells:
            span = int(c.attrs.get("colspan", 1))
            w = sum(widths[col:col + span])
            col += span
            cc = c.cls()
            align = "right" if "r" in cc else "left"
            if header:
                st = {"sz": 7, "b": True, "color": DARK}
                fill, bottom = HEAD_BG, (6, HEAD_RULE)
            elif band:
                st = {"sz": 7.4, "b": True, "color": DARK}
                fill, bottom = BAND_BG, None
            else:
                st = {"sz": 8.0 if "sm" in cc else 8.3, "color": BODY}
                if "mname" in cc:
                    st.update(b=True, color=DARK)
                if "up" in cc:
                    st.update(color=GREEN, b=True)
                if "dn" in cc:
                    st.update(color=RED, b=True)
                if "mid" in cc:
                    st["color"] = GREY
                fill, bottom = (ZEBRA if zebra else None), (2, ROW_RULE)
            para = _p(_runs(c, st), align=align, after=0, keep_next=True)
            right = 60 if col == span else 120
            xs.append(_cell(para, w, fill=fill, bottom=bottom, span=span,
                            valign="bottom" if header else "top", margins=(55, 120, 55, right)))
        if xs:
            out.append(_tr("".join(xs), header=header))
    doc.body.append(_tbl("".join(out), widths))


def _iter(n):
    for k in n.kids:
        yield k
        if not isinstance(k, str):
            for x in _iter(k):
                yield x


def _blocks(doc, n):
    for k in n.kids:
        if isinstance(k, str):
            if k.strip():
                doc.body.append(_p(_runs(k, {"sz": 9.7, "color": BODY}), after=40))
            continue
        c = k.cls()
        if k.tag == "table" and "mast" in c:
            _masthead(doc, k)
        elif "orule" in c:
            continue
        elif "ex" in c:
            _panel(doc, k, "ex")
        elif "take" in c:
            _panel(doc, k, "take")
        elif k.tag == "h2":
            doc.body.append(_p(_runs(k, {"sz": 12.5, "b": True, "color": DARK}), before=150, after=70,
                               keep_next=True, border_bottom=(12, ORANGE)))
        elif "sub" in c:
            doc.body.append(_p(_runs(k, {"sz": 8.5, "b": True, "color": GREY}), before=100, after=30,
                               keep_next=True))
        elif "cap" in c:
            doc.body.append(_p(_runs(k, {"sz": 6.8, "i": True, "color": GREY}), before=40, after=90))
        elif "src" in c:
            doc.body.append(_p(_runs(k, {"sz": 6.5, "color": GREY}), before=140, after=0,
                               border_top=(4, SRC_RULE)))
        elif k.tag == "ul":
            doc.body.extend(_bullets(k))
        elif k.tag == "table" and "data" in c:
            _data_table(doc, k)
        elif k.tag in ("div", "section", "body", "main") and ("keep" in c or not c):
            _blocks(doc, k)
        else:
            doc.body.append(_p(_runs(k, {"sz": 9.7, "color": BODY}), after=40))


_NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
       'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
       'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
       'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
       'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"')


def _footer_text(html_text):
    m = re.search(r'@bottom-left\{content:"([^"]*)"', html_text)
    return m.group(1) if m else ""


def _footer_xml(left):
    fld = lambda code: ('<w:r><w:rPr><w:color w:val="%s"/><w:sz w:val="13"/></w:rPr><w:fldChar w:fldCharType="begin"/></w:r>'
                        '<w:r><w:rPr><w:color w:val="%s"/><w:sz w:val="13"/></w:rPr><w:instrText xml:space="preserve"> %s </w:instrText></w:r>'
                        '<w:r><w:rPr><w:color w:val="%s"/><w:sz w:val="13"/></w:rPr><w:fldChar w:fldCharType="separate"/></w:r>'
                        '<w:r><w:rPr><w:color w:val="%s"/><w:sz w:val="13"/></w:rPr><w:t>1</w:t></w:r>'
                        '<w:r><w:rPr><w:color w:val="%s"/><w:sz w:val="13"/></w:rPr><w:fldChar w:fldCharType="end"/></w:r>'
                        % (GREY, GREY, code, GREY, GREY, GREY))
    t = lambda s: ('<w:r><w:rPr><w:color w:val="%s"/><w:sz w:val="13"/></w:rPr><w:t xml:space="preserve">%s</w:t></w:r>'
                   % (GREY, _x(s)))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:ftr %s><w:p><w:pPr>'
            '<w:tabs><w:tab w:val="right" w:pos="%d"/></w:tabs><w:spacing w:before="0" w:after="0"/></w:pPr>'
            '%s<w:r><w:tab/></w:r>%s%s%s%s</w:p></w:ftr>'
            % (_NS, TEXT_W, t(left), t("Page "), fld("PAGE"), t(" of "), fld("NUMPAGES")))


_STYLES = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri" '
           'w:eastAsia="Calibri"/><w:color w:val="222222"/><w:sz w:val="19"/><w:szCs w:val="19"/>'
           '<w:lang w:val="en-US"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="0" '
           'w:line="245" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
           '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
           '<w:style w:type="table" w:default="1" w:styleId="TableNormal"><w:name w:val="Normal Table"/>'
           '<w:tblPr><w:tblInd w:w="0" w:type="dxa"/><w:tblCellMar><w:top w:w="0" w:type="dxa"/>'
           '<w:left w:w="0" w:type="dxa"/><w:bottom w:w="0" w:type="dxa"/><w:right w:w="0" w:type="dxa"/>'
           '</w:tblCellMar></w:tblPr></w:style></w:styles>')

_NUMBERING = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              '<w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="singleLevel"/>'
              '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/>'
              '<w:lvlJc w:val="left"/><w:pPr><w:ind w:left="300" w:hanging="200"/></w:pPr>'
              '<w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:color w:val="E8850C"/></w:rPr></w:lvl>'
              '</w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>')


def build(html_text, out_docx):
    doc = _Doc()
    root = parse(html_text)
    body = _find(root, lambda x: x.tag == "body") or root
    _blocks(doc, body)
    sect = ('<w:sectPr><w:footerReference w:type="default" r:id="rIdFooter"/>'
            '<w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="720" w:right="720" w:bottom="720" '
            'w:left="720" w:header="360" w:footer="360" w:gutter="0"/></w:sectPr>')
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document %s><w:body>%s%s'
                '</w:body></w:document>' % (_NS, "".join(doc.body), sect))
    rels = ['<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>',
            '<Relationship Id="rIdNum" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>',
            '<Relationship Id="rIdFooter" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>']
    if doc.logo:
        rels.append('<Relationship Id="rIdLogo" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/logo.png"/>')
    files = {
        "[Content_Types].xml": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                                '<Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/>'
                                '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                                '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
                                '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
                                '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
                                '</Types>'),
        "_rels/.rels": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                        '</Relationships>'),
        "word/_rels/document.xml.rels": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                                         '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">%s</Relationships>'
                                         % "".join(rels)),
        "word/document.xml": document,
        "word/styles.xml": _STYLES,
        "word/numbering.xml": _NUMBERING,
        "word/footer1.xml": _footer_xml(_footer_text(html_text)),
    }
    with zipfile.ZipFile(out_docx, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, files[name].encode("utf-8"))
        if doc.logo:
            info = zipfile.ZipInfo("word/media/logo.png", date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, doc.logo)
    return out_docx


def _norm(s):
    return re.sub(r"\s+", " ", s.replace(" ", " ")).strip()


def html_visible_text(html_text):
    root = parse(html_text)
    body = _find(root, lambda x: x.tag == "body") or root
    words = []

    def walk(n):
        if isinstance(n, str):
            words.append(n)
            return
        for k in n.kids:
            walk(k)
        if n.tag in ("p", "div", "li", "td", "th", "tr", "h2", "h3", "table", "ul"):
            words.append(" ")
    walk(body)
    return _norm("".join(words))


def docx_visible_text(docx_path):
    with zipfile.ZipFile(docx_path) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    texts = re.findall(r"<w:t(?:\s[^>]*)?>([^<]*)</w:t>", xml)
    return _norm(" ".join(html.unescape(t) for t in texts))


def text_parity(html_text, docx_path):
    a, b = html_visible_text(html_text).replace(" ", ""), docx_visible_text(docx_path).replace(" ", "")
    if a == b:
        return True, "identical (%d characters)" % len(a)
    i = next((k for k in range(min(len(a), len(b))) if a[k] != b[k]), min(len(a), len(b)))
    return False, "differs at character %d: html %r / docx %r" % (i, a[max(0, i - 40):i + 40], b[max(0, i - 40):i + 40])


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    with open(argv[0], encoding="utf-8") as f:
        h = f.read()
    build(h, argv[1])
    ok, why = text_parity(h, argv[1])
    print(("DOCX OK: %s -> %s, text %s" if ok else "DOCX TEXT PARITY FAIL: %s -> %s, %s") % (argv[0], argv[1], why))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
