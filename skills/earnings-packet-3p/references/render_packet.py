#!/usr/bin/env python3
"""earnings-packet-3p, renderer - the Python twin of render_packet.js.

Reads packet_computed.json, packet_narrative.json and peers.json from the CURRENT WORKING
DIRECTORY and writes the .docx named by argv[1] (default earnings-packet.docx) plus
printed_figures.json.  Usage:  python3 references/render_packet.py "<OUT>.docx"

Python standard library only. Every part inside the .docx is byte-identical to what
render_packet.js (npm `docx` 9.7.1) writes, except the two timestamps in docProps/core.xml; the
archive itself is laid out the way JSZip lays it out, so even its size matches.
printed_figures.json, stdout, stderr's first line and the exit code match too, on success and
on every error path. maintainer/port_check_render.py proves it.

PORTING RULES (as in the company-tearsheet port). Function names match the JavaScript so the
two files read side by side. Every read of the input goes through G(), so a missing key is JS
`undefined` (UNDEF) and reading off null throws V8's own TypeError text; truthiness is jt(),
`a || b` is jor(), `a ?? b` is jnc(), String(x) is jstr(), `<`/`>=` on input values are
js_lt()/js_ge(). Regexes spell out JS's \\s class, use [0-9] for \\d and \\Z for `$`. The XML
writer reproduces the `docx` package's element order, attribute order, escaping and number
formatting. An uncaught error prints the same JSON envelope the JavaScript prints.
"""
import datetime
import hashlib
import math
import os
import re
import subprocess
import sys
import traceback
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fmt as F
from fmt import (JSError, type_error, nn, is_num, is_nan, jt, jor, jnc, jstr, to_num, js_lt,
                 js_ge, js_gt, js_eq, js_abs, G, need, jiter, set_items, JSSet,
                 js_sort_strings, read_json_module, u16slice, utf16len, num, cell, gcell, gzero, is_rate, xfmt,
                 setCcy, ccy, setScale, scaleLabel, printed, record, UNDEF)
from jscompat import js_round, js_str, js_to_fixed
from fmt import js_json_wf as js_json

SELF = os.path.join(HERE, 'render_packet.py')

_WS = '\t\n\x0b\x0c\r \xa0                　﻿'
_S = '[\\t\\n\\x0b\\x0c\\r \\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000\\ufeff]'
_NS = '[^\\t\\n\\x0b\\x0c\\r \\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000\\ufeff]'
_LT = '[\\n\\r\\u2028\\u2029]'


def jtrim(s):
    return s.strip(_WS)


def ix(a, i):
    return a[i] if 0 <= i < len(a) else UNDEF


def js_join(a, sep=','):
    return sep.join('' if nn(x) else jstr(x) for x in a)


def js_includes(o, v):
    if isinstance(o, list):
        if is_nan(v):
            return any(is_nan(x) for x in o)
        return any(js_eq(x, v) for x in o)
    return jstr(v) in o


def js_lower(s):
    return s.lower()


def _js_rx(src):
    out, i, n, cls = [], 0, len(src), False
    while i < n:
        ch = src[i]
        if ch == '\\' and i + 1 < n:
            e = src[i + 1]
            if e == 'd':
                out.append('0-9' if cls else '[0-9]')
            elif e == 'D':
                out.append('[^0-9]' if not cls else '\\D')
            elif e == 's':
                out.append(_S[1:-1] if cls else _S)
            elif e == 'S':
                out.append(_NS if not cls else '\\S')
            elif e == 'w':
                out.append('A-Za-z0-9_' if cls else '[A-Za-z0-9_]')
            elif e == 'W':
                out.append('[^A-Za-z0-9_]' if not cls else '\\W')
            else:
                out.append(ch + e)
            i += 2
            continue
        if cls:
            if ch == ']':
                cls = False
            elif ch == '[':
                out.append('\\[')
                i += 1
                continue
            out.append(ch)
        elif ch == '[':
            cls = True
            out.append(ch)
            if i + 1 < n and src[i + 1] == ']':
                out.append('^\\s\\S]')
                cls = False
                i += 2
                out[-2:] = ['(?!)']
                continue
        elif ch == '.':
            out.append('[^\\n\\r\\u2028\\u2029]')
        elif ch == '$':
            out.append('\\Z')
        else:
            out.append(ch)
        i += 1
    return ''.join(out)


_RX_REASON = (('missing ), unterminated subpattern', 'Unterminated group'),
              ('unbalanced parenthesis', "Unmatched ')'"),
              ('unterminated character set', 'Unterminated character class'),
              ('nothing to repeat', 'Nothing to repeat'),
              ('multiple repeat', 'Nothing to repeat'),
              ('bad escape (end of pattern)', '\\ at end of pattern'))


def new_regexp(src, flags):
    try:
        return re.compile(_js_rx(src), re.I if 'i' in flags else 0)
    except re.error as e:
        why = next((j for p, j in _RX_REASON if str(e).startswith(p)), str(e))
        raise JSError('SyntaxError', 'Invalid regular expression: /%s/: %s' % (src, why))


def js_replace(rx, s, rep):
    def sub(m):
        out, i = [], 0
        while i < len(rep):
            if rep[i] == '$' and i + 1 < len(rep):
                c = rep[i + 1]
                if c.isdigit() and c != '0' and int(c) <= rx.groups:
                    out.append(m.group(int(c)) or '')
                    i += 2
                    continue
                if c == '&':
                    out.append(m.group(0))
                    i += 2
                    continue
                if c == '$':
                    out.append('$')
                    i += 2
                    continue
            out.append(rep[i])
            i += 1
        return ''.join(out)
    return rx.sub(sub, s)


def out(s):
    sys.stdout.buffer.write(_utf8(s + '\n'))


def errout(s):
    sys.stderr.buffer.write(_utf8(s + '\n'))


def _utf8(s):
    return s.encode('utf-16', 'surrogatepass').decode('utf-16', 'replace').encode('utf-8')


def _fs_error(e, path):
    if os.path.isdir(path):
        return JSError('Error', "EISDIR: illegal operation on a directory, open '%s'" % path)
    if isinstance(e, FileNotFoundError):
        return JSError('Error', "ENOENT: no such file or directory, open '%s'" % path)
    if isinstance(e, PermissionError):
        return JSError('Error', "EPERM: operation not permitted, open '%s'" % path)
    return JSError('Error', str(e))


def write_file(path, data):
    try:
        with open(path, 'wb') as f:
            f.write(data)
    except OSError as e:
        raise _fs_error(e, path)


_ESC = {'&': '&amp;', '"': '&quot;', "'": '&apos;', '<': '&lt;', '>': '&gt;'}
_ESC_RE = re.compile('[&"<>\']')


def xesc(s):
    return _ESC_RE.sub(lambda m: _ESC[m.group(0)], s)


def _av(v):
    return xesc(v) if isinstance(v, str) else jstr(v)


def _el(name, attrs=(), children=''):
    a = ''.join(' %s="%s"' % (k, _av(v)) for k, v in attrs if v is not UNDEF)
    if children == '':
        return '<%s%s/>' % (name, a)
    return '<%s%s>%s</%s>' % (name, a, children, name)


def _decimal_number(v):
    n = v if is_num(v) else to_num(v)
    if is_nan(n):
        raise JSError('Error', "Invalid value '%s' specified. Must be an integer." % jstr(v))
    return math.floor(n) if not (isinstance(n, float) and math.isinf(n)) else n


def _unsigned(v):
    n = _decimal_number(v)
    if n < 0:
        raise JSError('Error', "Invalid value '%s' specified. Must be a positive integer." % jstr(v))
    return n


def _universal(v):
    unit, amount = u16slice(v, -2), u16slice(v, 0, utf16len(v) - 2)
    return '%s%s' % (js_str(F.js_number(amount)), unit)


def _positive_universal(v):
    out_ = _universal(v)
    from jscompat import js_parse_float
    if js_parse_float(out_) < 0:
        raise JSError('Error', "Invalid value '%s' specified. Expected a positive number." % out_)
    return out_


def _hps(v):
    return _positive_universal(v) if isinstance(v, str) else _unsigned(v)


def _measure_or_pct(v):
    if is_num(v):
        return _decimal_number(v)
    if nn(v):
        raise type_error("Cannot read properties of %s (reading 'slice')" % jstr(v))
    if not isinstance(v, str):
        raise type_error('val.slice is not a function')
    if u16slice(v, -1) == '%':
        return '%s%%' % js_str(F.js_number(u16slice(v, 0, utf16len(v) - 1)))
    return _universal(v)


def _hex(v):
    if v == 'auto':
        return v
    if not isinstance(v, str):
        raise type_error('val.charAt is not a function')
    c = v[1:] if v[:1] == '#' else v
    n = F.js_number('0x' + c)
    if utf16len(c) != 6 or is_nan(n):
        raise JSError('Error', "Invalid hex value '%s'. Expected 6 digit hex value" % c)
    return c


def _onoff(name, val):
    return _el(name) if val is True else _el(name, [('w:val', val)])


class _P(str):
    pass


class _T(str):
    pass


def _text(text):
    if isinstance(text, str):
        return '<w:t xml:space="preserve">%s</w:t>' % xesc(text)
    if text is None:
        raise type_error("Cannot read properties of null (reading 'space')")
    space = G(text, 'space') if isinstance(text, dict) else UNDEF
    space = 'default' if nn(space) else space
    val = G(text, 'text') if isinstance(text, dict) else UNDEF
    if val is UNDEF:
        return _el('w:t', [('xml:space', space)])
    return _el('w:t', [('xml:space', space)], xesc(val) if isinstance(val, str) else jstr(val)) \
        if not (isinstance(val, str) and val == '') else '<w:t xml:space="%s"></w:t>' % _av(space)


_FIELDS = {
    'CURRENT': '<w:fldChar w:fldCharType="begin"/><w:instrText xml:space="preserve">PAGE</w:instrText>'
               '<w:fldChar w:fldCharType="separate"/><w:fldChar w:fldCharType="end"/>',
    'TOTAL_PAGES': '<w:fldChar w:fldCharType="begin"/><w:instrText xml:space="preserve">NUMPAGES</w:instrText>'
                   '<w:fldChar w:fldCharType="separate"/><w:fldChar w:fldCharType="end"/>',
}


def TextRun(text=UNDEF, font=UNDEF, size=UNDEF, bold=UNDEF, italics=UNDEF, color=UNDEF,
            allCaps=UNDEF, children=None):
    pr = ''
    if jt(font):
        pr += _el('w:rFonts', [('w:ascii', font), ('w:cs', font), ('w:eastAsia', font), ('w:hAnsi', font)])
    if bold is not UNDEF:
        pr += _onoff('w:b', bold) + _onoff('w:bCs', bold)
    if italics is not UNDEF:
        pr += _onoff('w:i', italics) + _onoff('w:iCs', italics)
    if allCaps is not UNDEF:
        pr += _onoff('w:caps', allCaps)
    if jt(color):
        pr += _el('w:color', [('w:val', _hex(color))])
    if size is not UNDEF:
        pr += _el('w:sz', [('w:val', _hps(size))])
    if jt(size):
        pr += _el('w:szCs', [('w:val', _hps(size))])
    body = ('<w:rPr>%s</w:rPr>' % pr) if pr else ''
    if children is not None:
        body += ''.join(_FIELDS[c] for c in children)
    elif text is not UNDEF:
        body += _text(text)
    return '<w:r>%s</w:r>' % body if body else '<w:r/>'


def _border(name, b):
    return _el(name, [('w:val', b.get('style', UNDEF)),
                      ('w:color', _hex(b['color']) if b.get('color', UNDEF) is not UNDEF else UNDEF),
                      ('w:sz', _unsigned(b['size']) if b.get('size', UNDEF) is not UNDEF else UNDEF)])


def Paragraph(children=(), alignment=UNDEF, spacing=UNDEF, border=UNDEF, keepNext=UNDEF):
    pr = ''
    if keepNext is not UNDEF:
        pr += _onoff('w:keepNext', keepNext)
    if jt(border):
        bd = ''.join(_border('w:' + s, border[s]) for s in ('top', 'bottom', 'left', 'right', 'between')
                     if jt(border.get(s, UNDEF)))
        if bd:
            pr += '<w:pBdr>%s</w:pBdr>' % bd
    if jt(spacing):
        pr += _el('w:spacing', [('w:' + k, spacing.get(k, UNDEF)) for k in ('after', 'before', 'line', 'lineRule')])
    if jt(alignment):
        pr += _el('w:jc', [('w:val', alignment)])
    body = (('<w:pPr>%s</w:pPr>' % pr) if pr else '') + ''.join(children)
    return _P('<w:p>%s</w:p>' % body if body else '<w:p/>')


def ImageRun(width, height):
    cx, cy = js_round(width * 9525), js_round(height * 9525)
    return ('<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
            '<wp:extent cx="%d" cy="%d"/><wp:effectExtent t="0" r="0" b="0" l="0"/>'
            '<wp:docPr id="1" name="" descr="" title=""/><wp:cNvGraphicFramePr>'
            '<a:graphicFrameLocks xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" noChangeAspect="1"/>'
            '</wp:cNvGraphicFramePr><a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:nvPicPr>'
            '<pic:cNvPr id="0" name="" descr=""/><pic:cNvPicPr><a:picLocks noChangeAspect="1" noChangeArrowheads="1"/>'
            '</pic:cNvPicPr></pic:nvPicPr><pic:blipFill><a:blip r:embed="rId8" cstate="none"/><a:srcRect/>'
            '<a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr bwMode="auto"><a:xfrm>'
            '<a:off x="0" y="0"/><a:ext cx="%d" cy="%d"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/>'
            '</a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>'
            % (cx, cy, cx, cy))


def _width(name, size, typ='dxa'):
    return _el(name, [('w:type', typ), ('w:w', _measure_or_pct(size))])


def TableCell(children, width, borders=UNDEF, shading=UNDEF, margins=UNDEF, verticalAlign=UNDEF,
              columnSpan=UNDEF):
    pr = _width('w:tcW', width)
    if jt(columnSpan):
        pr += _el('w:gridSpan', [('w:val', _decimal_number(columnSpan))])
    if jt(borders):
        bd = ''.join(_border('w:' + s, borders[s]) for s in ('top', 'left', 'bottom', 'right')
                     if jt(borders.get(s, UNDEF)))
        if bd:
            pr += '<w:tcBorders>%s</w:tcBorders>' % bd
    if jt(shading):
        pr += _el('w:shd', [('w:fill', _hex(shading['fill']) if shading.get('fill', UNDEF) is not UNDEF else UNDEF),
                            ('w:color', _hex(shading['color']) if shading.get('color', UNDEF) is not UNDEF else UNDEF),
                            ('w:val', shading.get('type', UNDEF))])
    if jt(margins):
        mc = ''.join(_width('w:' + s, margins[s]) for s in ('top', 'left', 'bottom', 'right')
                     if margins.get(s, UNDEF) is not UNDEF)
        if mc:
            pr += '<w:tcMar>%s</w:tcMar>' % mc
    if jt(verticalAlign):
        pr += _el('w:vAlign', [('w:val', verticalAlign)])
    kids = list(children)
    if not kids or not isinstance(kids[-1], _P):
        kids.append(Paragraph())
    return '<w:tc><w:tcPr>%s</w:tcPr>%s</w:tc>' % (pr, ''.join(kids))


def TableRow(cells, cantSplit=UNDEF, tableHeader=UNDEF):
    pr = ''
    if cantSplit is not UNDEF:
        pr += _onoff('w:cantSplit', cantSplit)
    if tableHeader is not UNDEF:
        pr += _onoff('w:tblHeader', tableHeader)
    body = (('<w:trPr>%s</w:trPr>' % pr) if pr else '') + ''.join(cells)
    return '<w:tr>%s</w:tr>' % body if body else '<w:tr/>'


_DEFAULT_BORDER = {'style': 'single', 'size': 4, 'color': 'auto'}


def Table(rows, width, columnWidths, layout=UNDEF, borders=UNDEF):
    pr = _width('w:tblW', width)
    b = borders if borders is not UNDEF else {}
    pr += '<w:tblBorders>%s</w:tblBorders>' % ''.join(
        _border('w:' + x, jnc(b.get(k, UNDEF), _DEFAULT_BORDER)) for x, k in
        (('top', 'top'), ('left', 'left'), ('bottom', 'bottom'), ('right', 'right'),
         ('insideH', 'insideHorizontal'), ('insideV', 'insideVertical')))
    if jt(layout):
        pr += _el('w:tblLayout', [('w:type', layout)])
    grid = ''.join(_el('w:gridCol', [('w:w', _hps_twips(w))]) if w is not UNDEF else '<w:gridCol/>'
                   for w in columnWidths)
    return _T('<w:tbl><w:tblPr>%s</w:tblPr>%s%s</w:tbl>'
              % (pr, '<w:tblGrid>%s</w:tblGrid>' % grid if grid else '<w:tblGrid/>', ''.join(rows)))


def _hps_twips(v):
    return _positive_universal(v) if isinstance(v, str) else _unsigned(v)


LEFT, RIGHT, CENTER = 'left', 'right', 'center'
SINGLE, NONE = 'single', 'none'


def productName(root):
    try:
        with open(os.path.join(root, 'SKILL.md'), 'rb') as f:
            txt = f.read().decode('utf-8', 'replace')
        m = re.search('^name:' + _S + '*([A-Za-z0-9._-]+)' + _S + '*(?=' + _LT + '|\\Z)', txt, re.M)
        return m.group(1) if m else None
    except OSError:
        return None


def jpegSize(buf):
    i = 2
    while i < len(buf):
        if buf[i] != 0xFF:
            i += 1
            continue
        m = buf[i + 1] if i + 1 < len(buf) else None
        if m is not None and 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
            if i + 9 > len(buf):
                raise JSError('RangeError', 'The value of "offset" is out of range.')
            return {'h': int.from_bytes(buf[i + 5:i + 7], 'big'), 'w': int.from_bytes(buf[i + 7:i + 9], 'big')}
        if i + 4 > len(buf):
            raise JSError('RangeError', 'The value of "offset" is out of range.')
        i += 2 + int.from_bytes(buf[i + 2:i + 4], 'big')
    return {'w': 1, 'h': 1}


OR, GY, GR, BL, BD, DK, RD, GN, FT = 'E8850C', '6E6E6E', 'DDDDDD', 'BBBBBB', 'EDEDED', '1A1A1A', 'B02A1F', '1E7A34', 'Calibri'
FS, ZB, BB, CAL, RRC = 'Times New Roman', 'F5F0E8', 'F0E9D8', 'F6F3EE', 'EEEEEE'
PW, PH, MG = 12240, 15840, 720
CW = PW - MG * 2
TW = CW
B_ = {'style': SINGLE, 'size': 3, 'color': BL}
NBB = {'style': NONE}
HRB = {'style': SINGLE, 'size': 5, 'color': BL}
RRB = {'style': SINGLE, 'size': 2, 'color': RRC}
cb = {'top': NBB, 'bottom': RRB, 'left': NBB, 'right': NBB}
hbd = {'top': NBB, 'bottom': HRB, 'left': NBB, 'right': NBB}
boxl = {'style': SINGLE, 'size': 4, 'color': GR}
bx = {'top': boxl, 'bottom': boxl, 'left': boxl, 'right': boxl}
NB = {'top': {'style': NONE}, 'bottom': {'style': NONE}, 'left': {'style': NONE}, 'right': {'style': NONE}}
AL, AC, AR = LEFT, CENTER, RIGHT
KEEP_MAX = 26
MIN_BLOCK = 3
FIGURE_TOK = re.compile('[+–-]?[$€£¥]?\\(?[0-9](?:[0-9,]*[0-9])?(?:\\.[0-9]+)?\\)?(?:%|x|B|M| bps|bps)?')
EMPTY_FALLBACK = ['', '-', '–', '\u2014', '–%', '-%', '–x', 'n/a', 'N/A', 'not coded', 'not disclosed']
MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

GLYPH_PERMILLE = {}


def _put(chars, w):
    for c in chars:
        GLYPH_PERMILLE[c] = w


_put('|\xa6', 220)
_put(' ,.\xa0', 250)
_put("'/\\il\xec\xed\xee\xef", 278)
_put('\xaa\xb2\xb3\xb9', 300)
_put('\xba', 330)
_put('!()-:;[]`fjt\xa1\xa8\xad\xb4\xb7\xb8‘’', 333)
_put('Is\xcc\xcd\xce\xcf', 389)
_put('{}', 394)
_put('\xb0', 400)
_put('cerz\xe7\xe8\xe9\xea\xeb', 444)
_put('#$*0123456789?J_agovxy\xa2\xa3\xa4\xa5\xa7\xab\xaf\xbb\xbf\xe0\xe1\xe2\xe3\xe4\xe5\xf0\xf2\xf3\xf4\xf5\xf6\xf8\xfd\xff–', 500)
_put('~', 520)
_put('\xb6', 540)
_put('\xb1\xf7', 549)
_put('"', 555)
_put('Sbdhknpqu\xdf\xf1\xf9\xfa\xfb\xfc\xfe', 556)
_put('+<=>\xac\xd7−', 570)
_put('\xb5', 576)
_put('^', 581)
_put('FP\xde', 611)
_put('BELTZ\xc8\xc9\xca\xcb', 667)
_put('ACDNRUVXYw\xc0\xc1\xc2\xc3\xc4\xc5\xc7\xd0\xd1\xd9\xda\xdb\xdc\xdd\xe6', 722)
_put('\xa9\xae', 747)
_put('\xbc\xbd\xbe', 750)
_put('GHKOQ\xd2\xd3\xd4\xd5\xd6\xd8', 778)
_put('&m', 833)
_put('@', 930)
_put('M', 944)
_put('%W\xc6\u2014', 1000)
GLYPH_DEFAULT = 584
SUBSTITUTE_WIDTH_FACTOR = 1.12


def _face_scale():
    forced = os.environ.get('INSYNC_FACE_WIDTHS')
    if forced == 'georgia':
        return 1
    if forced == 'substitute':
        return SUBSTITUTE_WIDTH_FACTOR
    want, accepted = 'Georgia', ['Georgia']
    try:
        for up in range(1, 5):
            cand = os.path.normpath(os.path.join(HERE, *(['..'] * up + ['common', 'brand.json'])))
            if not os.path.exists(cand):
                continue
            with open(cand, 'rb') as f:
                bj = F.json.loads(f.read().decode('utf-8', 'replace'))
            m = jor(G(bj, 'masthead'), {})
            root = os.path.dirname(os.path.dirname(cand))
            product = productName(root) or os.path.basename(root)
            want = jor(jor(G(jor(G(m, 'face_overrides'), {}), product), G(m, 'face')), 'Georgia')
            subs = jor(G(jor(G(m, 'face_accepted_substitutes'), {}), want), [])
            accepted = [want] + (subs if isinstance(subs, list) else [subs])
            break
    except Exception:
        pass
    cmd = 'fc-match -f "%%{family}" "%s:bold" 2>%s' % (jstr(want), 'NUL' if os.name == 'nt' else '/dev/null')
    sys.stderr.flush()
    try:
        if os.name == 'nt':
            comspec = os.environ.get('ComSpec') or 'cmd.exe'
            r = subprocess.run('%s /d /s /c "%s"' % (comspec, cmd), stdout=subprocess.PIPE)
        else:
            r = subprocess.run(['/bin/sh', '-c', cmd], stdout=subprocess.PIPE)
    except OSError:
        return 1
    if r.returncode != 0:
        return 1
    resolved = jtrim(r.stdout.decode('utf-8', 'replace'))
    if not resolved:
        return 1
    flat = re.sub(_S + '+', '', resolved).lower()
    for a in accepted:
        if not isinstance(a, str):
            raise type_error('a.replace is not a function')
        if re.sub(_S + '+', '', a).lower() in flat:
            return 1
    errout('[masthead] "%s" is NOT installed on this box - fontconfig resolves it to "%s", which is '
           'not a metric-faithful stand-in. Sizing the company line for the substitute so it cannot '
           'wrap. Install %s to render at the intended size.' % (jstr(want), resolved, jstr(want)))
    return SUBSTITUTE_WIDTH_FACTOR


FACE_SCALE = 1


def textWidthPt(s, pt):
    mille = 0
    for c in jstr(s):
        mille += GLYPH_PERMILLE.get(c, GLYPH_DEFAULT)
    return mille * pt * FACE_SCALE / 1000


def keepFlags(n, mode):
    f = [False] * n
    if mode == 'none' or n < 2:
        return f
    if mode == 'all' or n <= KEEP_MAX:
        for i in range(n - 1):
            f[i] = True
        return f
    for i in range(min(MIN_BLOCK - 1, n - 1)):
        f[i] = True
    for i in range(max(0, n - MIN_BLOCK), n - 1):
        f[i] = True
    return f


def listJoin(a):
    return ' and '.join(a) if len(a) < 3 else ', '.join(a[:-1]) + ' and ' + a[-1]


def main():
    global FACE_SCALE
    F.load(stack=[os.path.join(HERE, 'fmt.py'), SELF])
    C, N = F.C, F.N
    PR = read_json_module(os.path.join(os.getcwd(), 'peers.json'), [SELF])
    logo_path = os.path.normpath(os.path.join(HERE, '..', 'assets', 'insync-logo.jpg'))
    try:
        with open(logo_path, 'rb') as f:
            LOGO = f.read()
    except OSError as e:
        if os.path.isdir(logo_path):
            raise JSError('Error', 'EISDIR: illegal operation on a directory, read')
        raise _fs_error(e, logo_path)
    jpegSize(LOGO)
    OUT = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else 'earnings-packet.docx'
    M = G(C, 'meta')
    setCcy(G(M, 'ccy'))
    setScale(G(M, 'money_scale'))
    HIST, TGT, NXT, FYP, FY0, FY1 = (G(M, 'hist'), G(M, 'target'), G(M, 'next_q'), G(M, 'fy_prev'),
                                     G(M, 'fy0'), G(M, 'fy1'))
    EW = jor(G(C, 'estimate_word'), 'Consensus')
    QK = list(jiter(HIST, 'HIST')) + [TGT]
    QH = [jstr(q) + ' A' for q in need(HIST, 'map', 'HIST.map', True)] + [jstr(TGT) + ' E']
    GK = list(jiter(HIST, 'HIST')) + [TGT, NXT, FYP, FY0, FY1]

    def fyl(y):
        return 'FY' + u16slice(jstr(y), 2) if re.match('^[0-9]{4}\\Z', jstr(y)) else jstr(y)

    GHL = (['Line item'] + [jstr(q) + ' A' for q in need(HIST, 'map', 'HIST.map', True)]
           + [jstr(TGT) + ' E', jstr(NXT) + ' E', fyl(FYP) + ' A', fyl(FY0) + ' E', fyl(FY1) + ' E'])

    def dmy(d):
        m = re.match('^([0-9]{4})-([0-9]{2})-([0-9]{2})\\Z', jstr(d))
        if not m:
            return jstr(d)
        mi = int(m.group(2)) - 1
        return str(int(m.group(3))) + ' ' + (MON[mi] if 0 <= mi < 12 else 'undefined') + ' ' + m.group(1)

    ESTQ = JSSet([TGT, NXT, FY0, FY1])
    PYQ = G(HIST, 0)

    def P(t, o=None):
        o = o or {}
        return Paragraph(alignment=jor(o.get('al', UNDEF), LEFT),
                         spacing={'before': jnc(o.get('bf', UNDEF), 0), 'after': jnc(o.get('af', UNDEF), 40),
                                  'line': jnc(o.get('line', UNDEF), 196)},
                         border=o.get('border', UNDEF),
                         children=[TextRun(text=t, font=FT, size=jor(o.get('sz', UNDEF), 15),
                                           bold=jt(o.get('b', UNDEF)), italics=jt(o.get('i', UNDEF)),
                                           color=jor(o.get('c', UNDEF), DK))])

    def SEC(n, title, sub):
        o = jor(G(jor(G(C, 'section_labels'), {}), jstr(n)), {})
        title = jor(G(o, 'title'), title)
        sub = jor(G(o, 'sub'), sub)
        return [
            Paragraph(spacing={'before': 0 if n == 1 else 118, 'after': 0}, keepNext=True,
                      children=[TextRun(text=jstr(n) + '.  ' + jstr(title), font=FS, size=22, bold=True, color=DK),
                                TextRun(text='    ' + jstr(sub), font=FT, size=13, italics=True, color=GY)]),
            Paragraph(spacing={'before': 16, 'after': 90}, keepNext=True,
                      border={'bottom': {'style': SINGLE, 'size': 10, 'color': OR}},
                      children=[TextRun(text='', font=FT, size=4)])]

    def SB(t):
        return Paragraph(spacing={'before': 100, 'after': 40}, keepNext=True,
                         children=[TextRun(text=t, font=FS, size=17, bold=True, color=DK)])

    NM = jstr

    def sc(v):
        return RD if (not nn(v) and js_lt(v, 0)) else DK

    def NT(t):
        return P(t, {'sz': 13, 'c': GY, 'bf': 18, 'af': 24, 'line': 170})

    def TC(x, o=None):
        o = {} if o is None else o
        t = jstr(x)
        for tok in FIGURE_TOK.findall(t):
            c = jtrim(tok)
            if re.search('[0-9]', c):
                record(c)
        return {'__c': 1, 'x': t, 'o': o}

    def R(cells, split=False, meta=None):
        return {'__r': 1, 'cells': cells, 'split': jt(split), 'band': bool(meta and jt(meta.get('band', UNDEF)))}

    def HR(labels, w, aligns=None, sz=UNDEF):
        if is_num(aligns):
            sz, aligns = aligns, None
        return {'__r': 1, 'header': True, 'split': False,
                'cells': [TC(l, {'w': ix(w, i), 'h': True, 'sh': BD, 'sz': jor(sz, 11),
                                 'al': ix(aligns, i) if aligns else (AL if i == 0 else AC)})
                          for i, l in enumerate(labels)]}

    try:
        v = read_json_module(os.path.normpath(os.path.join(HERE, '..', 'common', 'empty-vocabulary.json')), [SELF])
        ent = jor(G(v, 'entries'), [])
        t = [G(e, 'text') for e in need(ent, 'map', 'map')]
        t = [x for x in t if isinstance(x, str)]
        EMPTY_TXT = set(t if t else EMPTY_FALLBACK)
    except Exception:
        EMPTY_TXT = set(EMPTY_FALLBACK)

    def isEmpty(t):
        return jtrim(jstr(t)) in EMPTY_TXT

    PRUNE_LOG = {}

    def rowLabel(s):
        return re.sub('^' + _NS + '{1,6}' + _S + '*·' + _S + '*', '', jtrim(jstr(s)), count=1)

    def prune(w, rows, opt):
        minFill = jnc(opt.get('minFill', UNDEF), 0.5)
        key = opt.get('key', UNDEF)
        n = len(w)
        if opt.get('prune', UNDEF) is False:
            return w, rows
        if any(len(r['cells']) != n or any(jt(c['o']) and jt(c['o'].get('span', UNDEF)) for c in r['cells'])
               for r in rows):
            return w, rows
        log = {'cols': [], 'rows': [], 'of': 0}
        hdr = next((r for r in rows if r.get('header')), None)

        def lbl(i):
            return jstr(hdr['cells'][i]['x']) if hdr else 'col ' + str(i)

        def dropRow(r):
            return all(isEmpty(c['x']) for c in r['cells'][1:])

        body = [r for r in rows if not r.get('header') and not r.get('band')]
        killed = [r for r in body if dropRow(r)]
        if killed:
            log['rows'] = [rowLabel(r['cells'][0]['x']) for r in killed]
            rows = [r for r in rows if not (not r.get('header') and not r.get('band') and dropRow(r))]
            body = [r for r in rows if not r.get('header') and not r.get('band')]
        rows = [r for i, r in enumerate(rows)
                if not (r.get('band') and not any(not x.get('header') and not x.get('band')
                                                  for x in rows[i + 1:i + 2]))]
        log['of'] = len(body)
        keep = list(range(n))
        if len(body) >= 2:
            keep = []
            for i in range(n):
                if i == 0:
                    keep.append(i)
                    continue
                filled = len([r for r in body if not isEmpty(r['cells'][i]['x'])])
                fill = filled / len(body)
                if fill == 0 or fill < minFill:
                    log['cols'].append({'label': lbl(i), 'filled': filled})
                    continue
                keep.append(i)
        if len(keep) != n:
            removed = 0
            for i, x in enumerate(w):
                removed += 0 if i in keep else x
            w = [w[i] for i in keep]
            share = math.floor(removed / max(1, len(w) - 1))
            for j in range(1, len(w)):
                w[j] += share
            w[0] += TW - sum(w)
            rows = [dict(r, cells=[dict(r['cells'][i], o=dict(r['cells'][i]['o'] or {}, w=w[j]))
                                   for j, i in enumerate(keep)]) for r in rows]
        if jt(key) and (log['cols'] or log['rows']):
            PRUNE_LOG[key] = log
        return w, rows

    def dropped(key, rx):
        l = PRUNE_LOG.get(key)
        return bool(l and any(rx.search(x) for x in l['rows']))

    def dropNote(key):
        l = PRUNE_LOG.get(key)
        if not l:
            return ''
        bits = []
        if l['cols']:
            bits.append('The ' + listJoin([c['label'] + ' column (' + str(c['filled']) + ' of ' + str(l['of']) + ' rows)'
                                           for c in l['cols']])
                        + (' are' if len(l['cols']) > 1 else ' is')
                        + ' not shown: too few lines carry a value for the column to be read.')
        if l['rows']:
            seen = {}
            for r in l['rows']:
                seen[r] = seen.get(r, 0) + 1
            WORD = ['', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten']
            parts = [lab if n == 1 else (WORD[n] if n < len(WORD) else str(n)) + ' ' + lab + ' sub-rows'
                     for lab, n in seen.items()]
            plural = len(l['rows']) > 1
            bits.append(listJoin(parts) + (' are' if plural else ' is') + ' not shown: no column carries a value.')
        return ' ' + ' '.join(bits)

    def mkCell(c, kn):
        o = c['o'] if jt(c['o']) else {}
        g = o.get
        return TableCell(
            width=g('w', UNDEF),
            borders=NB if jt(g('nb', UNDEF)) else (bx if jt(g('bx', UNDEF)) else (hbd if jt(g('h', UNDEF)) else cb)),
            shading={'type': 'clear', 'fill': g('sh'), 'color': 'auto'} if jt(g('sh', UNDEF)) else UNDEF,
            verticalAlign=CENTER, columnSpan=g('span', UNDEF),
            margins={'top': 11, 'bottom': 11, 'left': 52, 'right': 52},
            children=[Paragraph(alignment=jor(g('al', UNDEF), CENTER if jt(g('h', UNDEF)) else LEFT),
                                spacing={'before': 0, 'after': 0, 'line': 147}, keepNext=kn,
                                children=[TextRun(text=jstr(c['x']), font=FT, size=jor(g('sz', UNDEF), 13),
                                                  bold=jt(g('b', UNDEF)) or jt(g('h', UNDEF)),
                                                  italics=jt(g('i', UNDEF)), color=jor(g('c', UNDEF), DK))])])

    def mkRows(rows, mode):
        f = keepFlags(len(rows), mode)
        bi = -1
        res = []
        for i, r in enumerate(rows):
            body = not r.get('header') and not r.get('band')
            if body:
                bi += 1
            z = ZB if (body and bi % 2 == 1) else None
            cells = [mkCell(dict(c, o=dict(c['o'] or {}, sh=z)) if (z and not (jt(c['o']) and jt(c['o'].get('sh', UNDEF))))
                            else c, f[i] or bool(r.get('header'))) for c in r['cells']]
            res.append(TableRow(cells, tableHeader=bool(r.get('header')), cantSplit=not r['split']))
        return res

    def T(w, rows, opt=None):
        opt = opt or {}
        w, rows = prune(w, rows, opt)
        t = sum(w)
        if t != TW:
            raise JSError('Error', 'table width ' + js_str(t) + ' != TW ' + js_str(TW) + '  [' + ','.join(js_str(x) for x in w) + ']')
        return Table(columnWidths=w, width=TW, layout='fixed', rows=mkRows(rows, opt.get('keep', UNDEF)))

    def TWOUP(left, right, gap=200):
        half = math.floor((TW - gap) / 2)
        return Table(columnWidths=[half, gap, half], width=TW, layout='fixed', rows=[TableRow(cantSplit=True, cells=[
            TableCell(width=half, borders=NB, margins={'top': 0, 'bottom': 0, 'left': 0, 'right': 0}, children=left),
            TableCell(width=gap, borders=NB, children=[P('')]),
            TableCell(width=half, borders=NB, margins={'top': 0, 'bottom': 0, 'left': 0, 'right': 0}, children=right)])])

    CORE = [{'c': G(s, 'code'), 'n': G(s, 'name'), 'sub': G(s, 'sub'), 'sl': G(s, 'sl')}
            for s in need(G(C, 'spine'), 'map', 'C.spine.map')]
    DISP_KEEP = G(C, 'disp_keep')

    TILE_W = math.floor(TW / 6)

    def TILE(label, value, accent):
        return TableCell(width=TILE_W, borders=bx, shading={'type': 'clear', 'fill': CAL, 'color': 'auto'},
                         verticalAlign=CENTER, margins={'top': 24, 'bottom': 24, 'left': 72, 'right': 72},
                         children=[
                             Paragraph(spacing={'before': 0, 'after': 14, 'line': 170},
                                       children=[TextRun(text=label, font=FT, size=10, color=GY, allCaps=True)]),
                             Paragraph(spacing={'before': 0, 'after': 0, 'line': 180},
                                       children=[TextRun(text=value, font=FT, size=16, bold=True, color=jor(accent, DK))])])

    def TILEROW(items):
        return Table(columnWidths=[TILE_W] * 6, width=TW, layout='fixed',
                     rows=[TableRow(cantSplit=True, cells=[TILE(ix(x, 0), ix(x, 1), ix(x, 2)) for x in items])])

    FACE_SCALE = _face_scale()

    MASTHEAD_FACE = 'Times New Roman'
    MOR, MDK, MVAL, MBOX, MFT = OR, DK, '555555', GR, MASTHEAD_FACE
    MTOTW, MLOGOW = 10800, 1150
    _mRightNeedPt = max(textWidthPt('Earnings Packet', 18),
                        textWidthPt('Prepared: ' + jstr(jor(G(M, 'prepared'), '')), 9),
                        textWidthPt('Expected Earnings Date: ' + dmy(G(M, 'call_date')), 9))
    MRIGHTW = min(4300, max(2900, math.ceil(_mRightNeedPt * 20) + 340 + 120))
    LW = [MLOGOW, MTOTW - MLOGOW - MRIGHTW, MRIGHTW]
    MBB = {'style': SINGLE, 'size': 4, 'color': MBOX}
    MRL, MRR = 140, 200
    MBOXB = {'top': MBB, 'bottom': MBB, 'left': MBB, 'right': MBB,
             'insideHorizontal': {'style': NONE}, 'insideVertical': {'style': NONE}}

    def mr(t, o=None):
        o = o or {}
        return TextRun(text=t, font=MFT, size=o.get('sz', UNDEF), bold=jt(o.get('b', UNDEF)), color=jor(o.get('c', UNDEF), MDK))

    def mp(kids, o=None):
        o = o or {}
        return Paragraph(alignment=jor(o.get('al', UNDEF), LEFT),
                         spacing={'before': jnc(o.get('bf', UNDEF), 0), 'after': jnc(o.get('af', UNDEF), 0),
                                  'line': jnc(o.get('line', UNDEF), js_round(jor(o.get('sz', UNDEF), 24) * 11.5))},
                         children=kids)

    try:
        _MBJ = jor(G(read_json_module(os.path.normpath(os.path.join(HERE, '..', 'common', 'brand.json')), [SELF]),
                     'masthead'), {})
    except Exception:
        _MBJ = {}
    MBRAND = jor(G(_MBJ, 'brand_size_half_points'), 33)
    MBRAND2 = jor(G(_MBJ, 'brand_size_two_line_half_points'), 24)
    MMW, MMH = 59, 46
    _MSFX = ' (' + jstr(G(M, 'ticker')) + ') - ' + jstr(TGT)
    _MNAME = jor(G(M, 'company'), G(M, 'ticker'))
    _MBUD = (LW[1] - 140 - 60) / 20

    def _mfits(l, h):
        return textWidthPt(l, h / 2) <= _MBUD * 0.97

    MCO_TOP = 28
    MCO = MCO_TOP
    full = jstr(_MNAME) + _MSFX
    for z in (28, 27, 26, 25, 24):
        MCO = z
        if _mfits(full, z):
            break
    MTWO = not _mfits(full, MCO)
    MTWO_SIZE = 20
    MCOL1, MCOL2 = full, None
    if MTWO:
        words = full.split(' ')

        def w_(t):
            return textWidthPt(t, MTWO_SIZE / 2)
        cap = _MBUD * 0.95
        best = None
        for i in range(1, len(words)):
            a, b = ' '.join(words[:i]), ' '.join(words[i:])
            wa, wb = w_(a), w_(b)
            if wa > cap or wb > cap:
                continue
            s_ = abs(wa - wb)
            if best is None or s_ < best[2]:
                best = (a, b, s_)
        if best:
            MCOL1, MCOL2 = best[0], best[1]
        else:
            acc = []
            while words and w_(' '.join(acc + [words[0]])) <= cap:
                acc.append(words.pop(0))
            if not acc:
                acc.append(words.pop(0))
            MCOL1 = ' '.join(acc)
            MCOL2 = ' '.join(words) or ' '
        MCO = MTWO_SIZE
    MBRAND_SZ = MBRAND
    if not MTWO:
        drop = (MCO_TOP - MCO) / 2
        MBRAND_LINE = 370
        MCO_LINE = js_round(372 + 5.5 * drop)
        MMARK_UP = js_round(13 + 5.5 * drop)
    else:
        MBRAND_SZ = MBRAND2
        MBRAND_LINE = 280
        MCO_LINE = 224
        MMARK_UP = 1
    _MTK = '(' + jstr(G(M, 'ticker')) + ')'

    def mcoRuns(t, sz):
        def mk(s, c):
            return mr(s, {'b': True, 'sz': sz, 'c': c})
        i = t.find(_MTK)
        if i < 0:
            return [mk(t, MDK)]
        res = []
        if i > 0:
            res.append(mk(t[:i], MDK))
        res.append(mk(_MTK, MOR))
        if i + len(_MTK) < len(t):
            res.append(mk(t[i + len(_MTK):], MDK))
        return res

    def mcoPara(t, sz):
        return Paragraph(alignment=LEFT, spacing={'before': 0, 'after': 0, 'line': MCO_LINE, 'lineRule': 'exact'},
                         children=mcoRuns(t, sz))

    exp = dmy(G(M, 'call_date'))
    ds = 18
    right_kids = [
        mp([mr('Earnings Packet', {'b': True, 'sz': 36, 'c': MDK})], {'al': RIGHT, 'af': 20}),
        mp([mr('Prepared: ', {'b': True, 'sz': ds, 'c': MOR}), mr(G(M, 'prepared'), {'sz': ds, 'c': MVAL})], {'al': RIGHT}),
        mp([mr('Expected Earnings Date: ', {'b': True, 'sz': ds, 'c': MOR}), mr(exp, {'sz': ds, 'c': MVAL})], {'al': RIGHT})]
    mast_tbl = Table(columnWidths=LW, width=LW[0] + LW[1] + LW[2], layout='fixed', borders=MBOXB, rows=[TableRow(cantSplit=True, cells=[
        TableCell(width=LW[0], verticalAlign='bottom', margins={'top': 70, 'bottom': 70, 'left': 110, 'right': 60},
                  children=[Paragraph(spacing={'before': 0, 'after': MMARK_UP}, children=[ImageRun(MMW, MMH)])]),
        TableCell(width=LW[1], verticalAlign='bottom', margins={'top': 70, 'bottom': 70, 'left': 60, 'right': 80}, children=[
            Paragraph(alignment=LEFT, spacing={'before': 0, 'after': 0, 'line': MBRAND_LINE, 'lineRule': 'exact'},
                      children=[mr('InSync', {'b': True, 'sz': MBRAND_SZ, 'c': MOR}), mr(' Analytics', {'sz': MBRAND_SZ, 'c': MDK})])]
            + ([mcoPara(MCOL1, MCO), mcoPara(MCOL2, MCO)] if MTWO else [mcoPara(MCOL1, MCO)])),
        TableCell(width=LW[2], verticalAlign=CENTER, margins={'top': 70, 'bottom': 70, 'left': MRL, 'right': MRR},
                  children=right_kids)])])
    rule = Paragraph(spacing={'before': 40, 'after': 40}, border={'bottom': {'style': SINGLE, 'size': 12, 'color': MOR}},
                     children=[TextRun(text='', font=MFT, size=4)])
    lr = G(M, 'last_reported')
    fields = [['Quarter ends', dmy(G(M, 'target_qend'))], ['Reports', dmy(G(M, 'call_date'))],
              ['Last reported', jstr(lr) + ' (' + re.sub(' [0-9]{4}\\Z', '', dmy(G(M, 'last_print_date')), count=1) + ')']]
    fields += [[EW, G(M, 'asof_estimates')], ['Valuation', G(M, 'asof_valuation')]]
    cl = G(M, 'ccy_label')
    fields += [['Figures', scaleLabel() if nn(cl) else cl]]
    strip_runs = []
    for i, (k, v) in enumerate(fields):
        if i:
            strip_runs.append(TextRun(text='  ·  ', font=FT, size=12, color=MOR))
        strip_runs.append(TextRun(text=jstr(k) + ': ', font=FT, size=12, bold=True, color=MOR))
        strip_runs.append(TextRun(text=v, font=FT, size=12, color=DK))
    mast = [mast_tbl, rule, Paragraph(spacing={'before': 0, 'after': 54}, children=strip_runs)]

    GWs = [2258, 949, 949, 949, 949, 949, 949, 949, 949, 950]
    GH = GHL

    def gridRows():
        res = [HR(GH, GWs)]
        for sp in CORE:
            r = None
            for x in need(G(C, 'model'), 'find', 'C.model.find'):
                if js_eq(G(x, 'code'), sp['c']):
                    r = x
                    break
            if not jt(r):
                continue
            _sc = (lambda v: DK) if jt(G(r, 'neg_ok')) else sc
            cells = [TC(sp['n'], {'w': GWs[0], 'b': True})]
            for i, k in enumerate(GK):
                s = cell(G(G(r, 'cells'), k), G(r, 'fmt'))
                cells.append(TC(s, {'w': ix(GWs, i + 1), 'al': RIGHT, 'c': _sc(G(G(r, 'cells'), k)), 'i': ESTQ.has(k)}))
            res.append(R(cells))
            if js_eq(sp['sub'], 'yoy'):
                cells = [TC('  Y/Y %', {'w': GWs[0], 'i': True, 'sz': 10, 'c': GY})]
                for i, k in enumerate(GK):
                    v = G(G(r, 'growth'), k)
                    cells.append(TC(gcell(v, False), {'w': ix(GWs, i + 1), 'al': RIGHT, 'sz': 10,
                                                      'c': GY if (nn(v) or gzero(v, False)) else (GN if js_ge(v, 0) else RD)}))
                res.append(R(cells))
            elif jt(sp['sub']):
                m = None
                for x in need(G(C, 'model'), 'find', 'C.model.find'):
                    if js_eq(G(x, 'code'), sp['sub']):
                        m = x
                        break
                cells = [TC(sp['sl'], {'w': GWs[0], 'i': True, 'sz': 10, 'c': GY})]
                for i, k in enumerate(GK):
                    mv = G(G(m, 'cells'), k) if jt(m) else None
                    cells.append(TC(record(num(mv, 1) + '%') if not nn(mv) else '–',
                                    {'w': ix(GWs, i + 1), 'al': RIGHT, 'sz': 10,
                                     'c': GY if nn(mv) else (GN if js_ge(mv, 0) else RD)}))
                res.append(R(cells))
        return res

    def s1():
        o = SEC(1, 'Earnings Preview & Key Debate', 'the setup, and what the print actually turns on')
        TL = []
        for t in need(G(C, 'tiles'), 'map', 'C.tiles.map'):
            lab = G(t, 'label')
            val = record(jstr(G(t, 'value')))
            sg = G(t, 'sign')
            TL.append([lab, val, UNDEF if nn(sg) else (GN if js_ge(sg, 0) else RD)])
        for i in range(0, len(TL), 6):
            o.append(TILEROW(TL[i:i + 6]))
        o.append(SB('What matters: five pointers'))
        dw = [402, 1991, 8407]
        dr = [HR(['#', 'Debate', 'The specific number, and what settles it'], dw, [AC, AL, AL], 11)]
        pts = [[G(x, 'debate'), G(x, 'detail')] for x in need(G(N, 'what_matters'), 'map', 'N.what_matters.map')]
        for i, x in enumerate(pts):
            dr.append(R([TC(str(i + 1), {'w': dw[0], 'al': CENTER, 'b': True}),
                         TC(x[0], {'w': dw[1], 'b': True, 'sz': 11}), TC(x[1], {'w': dw[2], 'sz': 11})]))
        o.append(T(dw, dr, {'prune': False}))
        return o

    def s1b():
        o = SEC(2, 'Guidance vs. Consensus', 'where the street sits against every live guided item')
        g = G(C, 'guide_vs_cons')
        if not jt(g) or not jt(G(g, 'length')):
            o.append(NT(G(G(N, 'notes'), 'guidance')))
            return o
        gw = [3620, 766, 1723, 1723, 1340, 1628]
        gr = [HR(['Guided item', 'Target', 'Guide as published', 'Guide low–high', EW, 'Position'], gw, [AL, AC, AC, AC, AC, AC])]
        for x in jiter(g, 'C.guide_vs_cons'):
            cons = G(x, 'cons')
            ct = '–' if nn(cons) else cell(G(x, 'cons'), G(x, 'unit'))
            if nn(G(x, 'lo')):
                bd = '–'
            elif jt(G(x, 'approx')) or js_eq(G(x, 'lo'), G(x, 'hi')):
                bd = cell(G(x, 'lo'), G(x, 'unit'))
            else:
                bd = cell(G(x, 'lo'), G(x, 'unit')) + ' – ' + cell(G(x, 'hi'), G(x, 'unit'))
            c1 = TC(G(x, 'item'), {'w': gw[0]})
            c2 = TC(G(x, 'target'), {'w': gw[1], 'al': CENTER})
            c3 = TC(G(x, 'text'), {'w': gw[2], 'al': CENTER})
            c4 = TC(bd, {'w': gw[3], 'al': CENTER, 'c': RD if (js_lt(G(x, 'lo'), 0) and js_lt(G(x, 'hi'), 0)) else DK})
            c5 = TC(ct, {'w': gw[4], 'al': CENTER, 'b': True, 'c': sc(G(x, 'cons'))})
            c6 = TC(jor(G(x, 'pos'), 'not comparable'), {'w': gw[5], 'al': CENTER, 'c': DK})
            gr.append(R([c1, c2, c3, c4, c5, c6]))
        o.append(T(gw, gr, {'minFill': 0, 'key': 'guidance'}))
        o.append(NT(jstr(G(G(N, 'notes'), 'guidance')) + dropNote('guidance')))
        return o

    def s1c():
        o = SEC(3, 'Financial Summary: Reported & Consensus', 'the core P&L, cash flow and balance-sheet lines, quarterly and annual')
        o.append(T(GWs, gridRows(), {'minFill': 0, 'key': 'model'}))
        o.append(NT(jstr(G(G(N, 'notes'), 'model')) + dropNote('model')))
        return o

    def s2():
        o = SEC(4, 'Consensus Dispersion', 'where the street agrees on %s, and where it does not' % jstr(TGT))
        w = [4100, 1340, 1340, 1340, 1340, 1340]
        pl = jtrim(jstr(jor(G(G(C, 'meta'), 'prior_label'), '')))
        rows = [HR(['Line item', 'Mean', 'Low', 'High', 'Dispersion %', jstr(TGT) + 'E vs ' + jstr(jor(pl, PYQ)) + 'A'], w,
                   [AL, AR, AR, AR, AR, AR])]
        disp = need(G(C, 'dispersion'), 'filter', 'C.dispersion.filter')
        kept = []
        for y in disp:
            if js_includes(need(DISP_KEEP, 'includes', 'DISP_KEEP.includes'), G(y, 'code')):
                kept.append(y)
        for x in kept:
            f = G(x, 'fmt')
            _sc = (lambda v: DK) if jt(G(x, 'neg_ok')) else sc
            c1 = TC(NM(G(x, 'name')), {'w': w[0]})
            c2 = TC(cell(G(x, 'mean'), f), {'w': w[1], 'al': RIGHT, 'b': True, 'c': _sc(G(x, 'mean'))})
            c3 = TC(cell(G(x, 'min'), f), {'w': w[2], 'al': RIGHT, 'c': _sc(G(x, 'min'))})
            c4 = TC(cell(G(x, 'max'), f), {'w': w[3], 'al': RIGHT, 'c': _sc(G(x, 'max'))})
            c5 = TC('–' if nn(G(x, 'disp')) else record(num(G(x, 'disp'), 1) + '%'), {'w': w[4], 'al': RIGHT})
            c6 = TC(gcell(G(x, 'yoy'), is_rate(G(x, 'fmt'), G(x, 'name'))), {'w': w[5], 'al': RIGHT, 'c': GY if gzero(G(x, 'yoy'), is_rate(G(x, 'fmt'), G(x, 'name'))) else (GN if js_ge(G(x, 'yoy'), 0) else RD)})
            rows.append(R([c1, c2, c3, c4, c5, c6]))
        o.append(T(w, rows, {'minFill': 0, 'key': 'dispersion'}))
        o.append(NT(jstr(G(G(N, 'notes'), 'dispersion')) + dropNote('dispersion')))
        return o

    def s3():
        o = SEC(5, 'KPI & Operating Driver Analysis', 'segment revenue, segment profit, and the drivers the street models')
        sgw = [3140, 1532, 1532, 1532, 1532, 1532]
        sg = [HR(['Segment'] + QH, sgw, [AL, AR, AR, AR, AR, AR])]

        def segBand(t):
            sg.append(R([TC(t, {'w': sgw[0], 'b': True, 'sh': BB, 'sz': 10, 'c': DK})]
                        + [TC('', {'w': sgw[i], 'sh': BB}) for i in (1, 2, 3, 4, 5)], False, {'band': True}))

        def segRows(lst, mg):
            for x in jiter(lst, 'list'):
                cells = [TC(G(x, 'name'), {'w': sgw[0], 'b': True})]
                for i, k in enumerate(QK):
                    s = cell(G(G(x, 'cells'), k), '$M')
                    cells.append(TC(s, {'w': ix(sgw, i + 1), 'al': AR, 'c': sc(G(G(x, 'cells'), k)), 'i': js_eq(k, TGT)}))
                sg.append(R(cells))
                if not mg:
                    cells = [TC('  Y/Y %', {'w': sgw[0], 'i': True, 'sz': 10, 'c': GY})]
                    for i, k in enumerate(QK):
                        g = G(G(x, 'growth'), k)
                        cells.append(TC(gcell(g, False), {'w': ix(sgw, i + 1), 'al': AR, 'sz': 10,
                                                          'c': GY if (nn(g) or gzero(g, False)) else (GN if js_ge(g, 0) else RD)}))
                    sg.append(R(cells))
                if mg:
                    cells = [TC(G(C, 'seg_margin_label'), {'w': sgw[0], 'i': True, 'sz': 10, 'c': GY})]
                    for i, k in enumerate(QK):
                        mk_ = G(G(x, 'margin'), k)
                        txt = '–' if nn(mk_) else record(num(G(G(x, 'margin'), k), 1) + '%')
                        mk2 = G(G(x, 'margin'), k)
                        cells.append(TC(txt, {'w': ix(sgw, i + 1), 'al': AR, 'sz': 10,
                                              'c': GY if nn(mk2) else (GN if js_ge(mk2, 0) else RD)}))
                    sg.append(R(cells))

        corp = G(C, 'corp')
        cr = jor(G(corp, 'cells') if jt(corp) else corp, {})
        segs, ebit = G(C, 'segments'), G(C, 'seg_ebit')
        segsOK = isinstance(segs, list) and len(segs) > 0
        ebitOK = isinstance(ebit, list) and len(ebit) > 0
        if segsOK:
            segBand(G(C, 'seg_rev_label'))
            segRows(G(C, 'segments'), False)
        if ebitOK:
            segBand(G(C, 'seg_profit_label'))
            segRows(G(C, 'seg_ebit'), True)
        o.append(T(sgw, sg, {'minFill': 0.5, 'key': 'segment'}))
        segDropped = JSSet(jstr(c['label']) for c in PRUNE_LOG.get('segment', {}).get('cols', []))

        def colGone(k):
            return segDropped.has(jstr(k) + ' A') or segDropped.has(jstr(k) + ' E') or segDropped.has(k)

        histKept = [k for k in need(HIST, 'filter', 'HIST.filter') if not colGone(k)]
        pick = [ix(histKept, 0), ix(histKept, len(histKept) - 1), None if colGone(TGT) else TGT]
        corpPick = JSSet(x for x in pick if jt(x)).values()
        corpMatters = any(not nn(G(cr, k)) and js_ge(js_abs(G(cr, k)), 5) for k in corpPick)
        if not segsOK or not ebitOK:
            labs = [x for x in [None if segsOK else G(C, 'seg_rev_label'), None if ebitOK else G(C, 'seg_profit_label')] if jt(x)]
            bandNote = (' The ' + ' and the '.join(js_lower(jstr(x)) for x in labs)
                        + ' band is not shown: the issuer publishes no such split, so the section carries '
                        + ('the band it does publish' if (segsOK or ebitOK) else 'the operating drivers') + ' alone.')
        else:
            bandNote = ''
        note = jstr(G(G(N, 'notes'), 'segment')) + bandNote + dropNote('segment')
        if corpMatters:
            note += (' Corporate & eliminations: '
                     + '  ·  '.join((jstr(TGT) + 'E' if js_eq(k, TGT) else jstr(k)) + ' ' + cell(G(cr, k), '$M') for k in corpPick)
                     + '.')
        o.append(NT(note))

        kw = [3140, 1532, 1532, 1532, 1532, 1532]
        kr = [HR(['Operating driver'] + QH, kw, [AL, AR, AR, AR, AR, AR])]
        GROWTH_SUB = JSSet(set_items(jor(G(C, 'kpi_growth_sub'), [])))
        for k in jiter(G(C, 'kpis'), 'C.kpis'):
            cells = [TC(NM(G(k, 'name')), {'w': kw[0], 'b': True})]
            for i, q in enumerate(QK):
                txt = '–' if nn(G(G(k, 'cells'), q)) else cell(G(G(k, 'cells'), q), G(k, 'fmt'))
                cells.append(TC(txt, {'w': ix(kw, i + 1), 'al': RIGHT, 'c': sc(G(G(k, 'cells'), q)), 'i': js_eq(q, TGT)}))
            kr.append(R(cells))
            if GROWTH_SUB.has(G(k, 'code')):
                cells = [TC('  Y/Y %', {'w': kw[0], 'i': True, 'sz': 10, 'c': GY})]
                for i, q in enumerate(QK):
                    g = G(G(k, 'growth'), q)
                    cells.append(TC(gcell(g, False), {'w': ix(kw, i + 1), 'al': RIGHT, 'sz': 10,
                                                      'c': GY if (nn(g) or gzero(g, False)) else (GN if js_ge(g, 0) else RD)}))
                kr.append(R(cells))
        o.append(SB('Operating drivers'))
        o.append(T(kw, kr, {'minFill': 0.5, 'key': 'drivers'}))
        o.append(NT(jstr(G(G(N, 'notes'), 'drivers')) + dropNote('drivers')))
        return o

    def s4():
        o = SEC(6, 'Estimate Revisions', 'how %s consensus has moved since the last print' % jstr(TGT))
        w = [3046, 1321, 1321, 1321, 1321, 1235, 1235]
        RB = jor(G(C, 'rev_buckets'), {})

        def dd(k):
            return ' ' + u16slice(jstr(G(RB, k)), 5) if jt(G(RB, k)) else ''
        RC = G(C, 'rev_cols')
        REV_KEEP = JSSet(set_items(G(C, 'rev_keep')))
        heads = []
        for c in need(RC, 'map', 'RC.map', True):
            lab = G(c, 'label')
            heads.append(jstr(lab) + dd(G(c, 'key')))
        base = None
        for c in need(RC, 'find', 'RC.find'):
            if jt(G(c, 'base')):
                base = c
                break
        base = base if base is not None else G(RC, 0)
        bl = G(base, 'label')
        rows = [HR(['Line item'] + heads + ['Δ vs ' + js_lower(need(bl, 'toLowerCase', '(RC.find(...) || RC[0]).label.toLowerCase')), 'Read'],
                   w, [AL, AR, AR, AR, AR, AR, AC])]
        revs = [x for x in need(G(C, 'revisions'), 'filter', 'C.revisions.filter') if REV_KEEP.has(G(x, 'ltcode'))]
        for r in revs:
            isP = is_rate(G(r, 'fmt'), G(r, 'name'))
            cells = [TC(G(r, 'name'), {'w': w[0]})]
            for i, c in enumerate(RC):
                v = G(r, G(c, 'key'))
                txt = 'n/a' if nn(v) else cell(G(r, G(c, 'key')), G(r, 'fmt'))
                bold = js_eq(G(c, 'key'), 'latest')
                v2 = G(r, G(c, 'key'))
                col = GY if nn(v2) else (DK if jt(G(r, 'neg_ok')) else sc(G(r, G(c, 'key'))))
                cells.append(TC(txt, {'w': ix(w, i + 1), 'al': RIGHT, 'b': bold, 'c': col}))
            dl = G(r, 'delta')
            cells.append(TC('–' if nn(dl) else gcell(G(r, 'delta'), isP),
                            {'w': w[5], 'al': RIGHT, 'c': GY if (nn(G(r, 'delta')) or gzero(G(r, 'delta'), isP)) else (GN if js_ge(G(r, 'delta'), 0) else RD)}))
            tg = G(r, 'tag')
            cells.append(TC(tg, {'w': w[6], 'al': CENTER, 'c': GN if js_eq(G(r, 'tag'), 'Raised') else (RD if js_eq(G(r, 'tag'), 'Cut') else GY)}))
            rows.append(R(cells))
        o.append(T(w, rows, {'minFill': 0, 'key': 'revisions'}))
        o.append(NT(jstr(G(G(N, 'notes'), 'revisions')) + dropNote('revisions')))
        return o

    def s5():
        o = SEC(7, 'Valuation vs. Own History', 'how the stock is priced today against how it has traded')
        vw = [3332, 1245, 1245, 1436, 1436, 1053, 1053]
        xf = xfmt
        vr = [HR(['Multiple', 'NTM', 'LTM', '1-yr avg', '3-yr avg', 'LTM vs 1-yr %', 'LTM vs 3-yr %'], vw, [AL, AR, AR, AR, AR, AR, AR])]
        for v in jiter(G(C, 'valuation'), 'C.valuation'):
            hist = jt(G(v, 'y1'))
            hist3 = jt(G(v, 'y3'))
            c1 = TC(G(v, 'name'), {'w': vw[0], 'b': True})
            c2 = TC(xf(G(v, 'ntm')), {'w': vw[1], 'al': RIGHT})
            c3 = TC(xf(G(v, 'ltm')), {'w': vw[2], 'al': RIGHT})
            c4 = TC(xf(G(G(v, 'y1'), 'mean')) if hist else 'no history', {'w': vw[3], 'al': RIGHT, 'c': DK if hist else GY, 'i': not hist})
            c5 = TC(xf(G(G(v, 'y3'), 'mean')) if hist3 else 'no history', {'w': vw[4], 'al': RIGHT, 'c': DK if hist3 else GY, 'i': not hist3})
            c6 = TC(gcell(G(v, 'vs_1y_mean'), False) if hist else '–',
                    {'w': vw[5], 'al': RIGHT, 'c': GY if (not hist or gzero(G(v, 'vs_1y_mean'), False)) else (GN if js_ge(G(v, 'vs_1y_mean'), 0) else RD)})
            c7 = TC(gcell(G(v, 'vs_3y_mean'), False) if hist3 else '–',
                    {'w': vw[6], 'al': RIGHT, 'c': GY if (not hist3 or gzero(G(v, 'vs_3y_mean'), False)) else (GN if js_ge(G(v, 'vs_3y_mean'), 0) else RD)})
            vr.append(R([c1, c2, c3, c4, c5, c6, c7]))
        o.append(T(vw, vr, {'minFill': 0, 'key': 'valuation'}))
        dn = dropNote('valuation')
        notes = G(N, 'notes')
        s = jtrim(dn) + (' ' if dropNote('valuation') else '') + jstr(G(notes, 'valuation')) \
            + ' Multiples shown are the valuation lens for ' + jstr(G(C, 'val_sector'))
        vx = G(C, 'val_excluded')
        if jt(G(vx, 'length')):
            names = [G(x, 'name') for x in need(G(C, 'val_excluded'), 'map', 'C.val_excluded.map')]
            s += ('; ' + js_join(names, ' and ') + (' are' if js_gt(G(G(C, 'val_excluded'), 'length'), 1) else ' is')
                  + ' computed but not shown'
                  + ((', ' + jstr(G(G(N, 'notes'), 'valuation_excluded_reason')) + '.')
                     if jt(G(G(N, 'notes'), 'valuation_excluded_reason')) else '.'))
        else:
            s += '.'
        s += ' ' + jstr(jor(G(G(N, 'notes'), 'valuation_read'), ''))
        o.append(NT(s))

        o += SEC(8, 'Peer Comparison', '%s · valuation as of %s · consensus as of %s · calendarized'
                 % (jstr(G(PR, 'sector_label')), jstr(G(PR, 'asof_val')), jstr(G(PR, 'asof_est'))))
        _pn = G(G(PR, 'rows'), 'length')
        _pc = 1 + _pn if is_num(_pn) else float('nan')
        if js_eq(_pc, 6):
            cw = [2755, 1609, 1609, 1609, 1609, 1609]
        elif is_num(_pn):
            _lab = 2758
            per = math.floor((TW - _lab) / _pn) if _pn else 0
            cw = [_lab] + [per] * _pn
            cw[-1] += TW - sum(cw)
        else:
            cw = [2758, float('nan')]
        HD = ['Metric'] + [G(r, 't') for r in need(G(PR, 'rows'), 'map', 'PR.rows.map', True)]
        cr = [HR(HD, cw, [AL] + [AC] * _pn, 12)]

        def band(t):
            cr.append(R([TC(t, {'w': cw[0], 'b': True, 'sh': BB, 'sz': 11, 'c': DK})]
                        + [TC('', {'w': w_, 'sh': BB}) for w_ in cw[1:]], False, {'band': True}))

        def metric(label, fn, opts):
            cells = [TC(label, {'w': cw[0], 'b': jt(opts.get('b', UNDEF))})]
            for i, r in enumerate(G(PR, 'rows')):
                v = fn(r)
                cells.append(TC(v['t'], {'w': ix(cw, i + 1), 'al': CENTER, 'b': i == 0, 'c': jor(v['c'], DK)}))
            cr.append(R(cells))

        xmul = xfmt
        PF = {
            'money': lambda v: {'t': record(cell(v, '$M')), 'c': DK if js_ge(v, 0) else RD},
            'mult': lambda v: {'t': record(xmul(v)), 'c': DK if js_ge(v, 0) else RD},
            'pct1': lambda v: {'t': record(num(v, 1) + '%'), 'c': DK if js_ge(v, 0) else RD},
            'pct100': lambda v: {'t': record(num(to_num(v) * 100, 1) + '%'), 'c': DK if js_ge(v, 0) else RD},
            'pctchg': lambda v: {'t': record(gcell(v, False)), 'c': GY if gzero(v, False) else (GN if js_ge(v, 0) else RD)},
            'ratio': lambda v: {'t': record(num(v, 2)), 'c': DK if js_ge(v, 0) else RD},
            'raw': lambda v: ({'t': record(num(v, 2)), 'c': DK if js_ge(v, 0) else RD} if is_num(v)
                              else {'t': record(jstr(v)), 'c': DK}),
        }

        def runBand(label, lst):
            band(label)
            for m in jiter(lst, 'list'):
                def fn(r, m=m):
                    v = G(r, G(m, 'key'))
                    if nn(v):
                        why = G(r, jstr(G(m, 'key')) + '_note')
                        return {'t': jor(why, '–'), 'c': GY}
                    f = PF.get(G(m, 'fmt')) if isinstance(G(m, 'fmt'), str) else None
                    if f is None:
                        raise type_error('PF[m.fmt] is not a function')
                    return f(v)
                metric(G(m, 'label'), fn, {'b': jt(G(m, 'lead'))})

        runBand(jstr(G(PR, 'val_label')) + '  ·  as of ' + jstr(G(PR, 'asof_val')), G(PR, 'val_metrics'))
        runBand(jstr(G(PR, 'growth_label')) + '  ·  consensus as of ' + jstr(G(PR, 'asof_est')), G(PR, 'growth_metrics'))
        runBand(G(PR, 'op_label'), G(PR, 'op_metrics'))
        o.append(T(cw, cr, {'minFill': 0.5, 'key': 'peers'}))

        def shortBasis(t):
            return re.sub(_S + '*\\([^)]*\\)', '', jstr(t))
        cmet = None
        for m in need(jor(G(PR, 'op_metrics'), []), 'find', '(PR.op_metrics || []).find'):
            if jt(G(m, 'comparability')):
                cmet = m
                break
        compKept = False
        if cmet is not None:
            lab = need(G(cmet, 'label'), 'replace', 'cmet.label.replace')
            esc = re.sub('([.*+?^${}()|\\[\\]\\\\])', lambda mm: '\\' + mm.group(1), lab)
            compKept = not dropped('peers', new_regexp(esc, 'i'))
        bases = [shortBasis(G(r, 'comp_basis')) for r in need(G(PR, 'rows'), 'map', 'PR.rows.map')]
        basesDiffer = len(JSSet(bases)) > 1
        notes = G(N, 'notes')
        s = jstr(G(notes, 'peers_read'))
        if compKept and jt(G(G(N, 'notes'), 'peers_comparability')):
            s += '. ' + jstr(G(G(N, 'notes'), 'peers_comparability'))
            if basesDiffer:
                s += ' (' + ' · '.join(jstr(G(r, 't')) + ' ' + bases[i] for i, r in enumerate(G(PR, 'rows'))) + ')'
        if jt(G(G(N, 'notes'), 'peers_caveat')):
            s += '; ' + jstr(G(G(N, 'notes'), 'peers_caveat'))
        s += dropNote('peers')
        o.append(NT(s))

        b = G(C, 'move_band')
        o += SEC(9, 'Earnings Day Reaction Metrics', 'last eight prints versus the market index, adjusted closes'
                 + ('; every release lands after the close, so the pair is the print-date close against the next session'
                    if js_eq(G(M, 'reaction_basis'), 'after_close') else ''))
        rw = [728, 1168, 1034, 1034, 1187, 1168, 1226, 1245, 1187, 823]
        RB2 = ({'pre': 'Close on print date', 'day': 'Close next session'} if js_eq(G(M, 'reaction_basis'), 'after_close')
               else {'pre': 'Close before', 'day': 'Close on print'})
        rr = [HR(['Qtr', 'Print date', RB2['pre'], RB2['day'], 'Print-day move %', 'vs index %', 'After 1 day %', 'After 5 days %',
                  '5-day vs index %', 'Up / Down'], rw, [AC, AC, AR, AR, AR, AR, AR, AR, AR, AC], 11)]
        for p in jiter(G(C, 'reaction'), 'C.reaction'):
            c1 = TC(G(p, 'q'), {'w': rw[0], 'b': True, 'al': CENTER})
            c2 = TC(G(p, 'date'), {'w': rw[1], 'al': CENTER, 'sz': 11})
            c3 = TC(ccy() + num(G(p, 'w_pre'), 2), {'w': rw[2], 'al': RIGHT})
            c4 = TC(ccy() + num(G(p, 'w_day'), 2), {'w': rw[3], 'al': RIGHT})
            c5 = TC(gcell(G(p, 'm0'), False), {'w': rw[4], 'al': RIGHT, 'b': True, 'c': GY if gzero(G(p, 'm0'), False) else (GN if js_ge(G(p, 'm0'), 0) else RD)})
            c6 = TC(gcell(G(p, 'r0'), False), {'w': rw[5], 'al': RIGHT, 'c': GY if gzero(G(p, 'r0'), False) else (GN if js_ge(G(p, 'r0'), 0) else RD)})
            c7 = TC(gcell(G(p, 'm1'), False), {'w': rw[6], 'al': RIGHT, 'c': GY if gzero(G(p, 'm1'), False) else (GN if js_ge(G(p, 'm1'), 0) else RD)})
            c8 = TC(gcell(G(p, 'm5'), False), {'w': rw[7], 'al': RIGHT, 'c': GY if gzero(G(p, 'm5'), False) else (GN if js_ge(G(p, 'm5'), 0) else RD)})
            c9 = TC(gcell(G(p, 'r5'), False), {'w': rw[8], 'al': RIGHT, 'c': GY if gzero(G(p, 'r5'), False) else (GN if js_ge(G(p, 'r5'), 0) else RD)})
            c10 = TC('Flat' if gzero(G(p, 'm0'), False) else ('Up' if js_ge(G(p, 'm0'), 0) else 'Down'), {'w': rw[9], 'al': CENTER, 'c': GY if gzero(G(p, 'm0'), False) else (GN if js_ge(G(p, 'm0'), 0) else RD)})
            rr.append(R([c1, c2, c3, c4, c5, c6, c7, c8, c9, c10]))
        st = ('mean absolute print-day move ' + num(G(b, 'mean_abs_1d'), 1) + '%  ·  median ' + num(G(b, 'median_abs_1d'), 1)
              + '%  ·  range ' + num(G(b, 'min_abs_1d'), 1) + '% to ' + num(G(b, 'max_abs_1d'), 1) + '%  ·  '
              + jstr(G(b, 'up')) + ' up / ' + jstr(G(b, 'down')) + ' down  ·  mean vs index ' + gcell(G(b, 'mean_rel_1d'), False)
              + '  ·  mean absolute 5-day move ' + num(G(b, 'mean_abs_5d'), 1) + '%  ·  empirical base case ±'
              + num(G(b, 'median_abs_1d'), 1) + '%')
        rr.append(R([TC('Stats', {'w': rw[0], 'sh': BD, 'b': True, 'al': CENTER}),
                     TC(st, {'w': sum(rw[1:]), 'span': 9, 'sh': BD, 'b': True, 'al': CENTER, 'sz': 11})]))
        o.append(T(rw, rr, {'prune': False}))
        o.append(NT(G(G(N, 'notes'), 'reaction')))
        return o

    def s7():
        o = SEC(10, 'Key Risks & Catalysts', 'every item sourced to an InSync document; nothing from the web')
        G(N, 'risks')
        RS = [G(x, 'text') for x in need(G(N, 'risks'), 'map', 'N.risks.map')]
        CS = [G(x, 'text') for x in need(G(N, 'catalysts'), 'map', 'N.catalysts.map')]
        risksSrc = [{'source': G(x, 'source')} for x in need(G(N, 'risks'), 'map', 'N.risks.map')]
        catsSrc = [{'source': G(x, 'source')} for x in need(G(N, 'catalysts'), 'map', 'N.catalysts.map')]
        risks5, RS5, cats5, CS5 = risksSrc, RS, catsSrc, CS
        w = [316, 4142, 842]
        tk = jstr(G(M, 'ticker'))

        def shortSrc(t):
            s = jstr(jor(t, ''))
            s = js_replace(new_regexp(tk + '[_ ](\\dQ\\d\\d)[_ ]AnalystsShareholdersMeeting', 'gi'), s, '$1 AGM')
            s = js_replace(new_regexp(tk + ' (\\dQ\\d\\d) Earnings Transcript', 'gi'), s, '$1 CT')
            s = js_replace(new_regexp(tk + ' (\\dQ\\d\\d) Earnings Release', 'gi'), s, '$1 REL')
            s = js_replace(new_regexp(tk + '[_ ](\\dQ\\d\\d)[_ ]?Conference ?Presentation', 'gi'), s, '$1 PRES')
            s = js_replace(new_regexp(tk + ' (\\dQ\\d\\d) (?:SEC )?(10-Q|10-K)', 'gi'), s, '$1 $2')
            s = re.sub(_S + '*/' + _S + '*', '/', s)
            s = re.sub('([0-9]Q[0-9][0-9]) CT/([0-9]Q[0-9][0-9]) CT', '\\1/\\2 CT', s)
            return s

        def col(title, items, short):
            rows = [HR(['#', title, 'Source'], w, [AC, AL, AC])]
            for i, x in enumerate(items):
                rows.append(R([TC(str(i + 1), {'w': w[0], 'al': CENTER, 'b': True}),
                               TC(ix(short, i), {'w': w[1], 'sz': 10}),
                               TC(shortSrc(x['source']), {'w': w[2], 'sz': 9, 'c': GY, 'al': CENTER})], True))
            return [Table(columnWidths=w, width=sum(w), layout='fixed', rows=mkRows(rows, 'all'))]

        o.append(TWOUP(col('Key risks', risks5, RS5), col('Catalysts', cats5, CS5), 200))

        def srcParts(t):
            return [x for x in (jtrim(p) for p in re.split(_S + '*;' + _S + '*|' + _S + '+/' + _S + '+', jstr(jor(t, '')))) if x]
        allsrc = JSSet()
        for x in risks5 + cats5:
            for p in srcParts(x['source']):
                allsrc.add(shortSrc(p))
        srcs = js_sort_strings(allsrc.values())
        WORDS = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten']
        o.append(NT('Top ' + (WORDS[len(RS5)] if len(RS5) < len(WORDS) else 'undefined') + ' of ' + jstr(G(N, 'risks_total'))
                    + ' risks and ' + jstr(G(N, 'catalysts_total')) + ' catalysts on the record, ranked by effect on the '
                    + jstr(TGT) + ' print. Sources: ' + ' · '.join(srcs) + '.'))
        return o

    title = jstr(G(M, 'ticker')) + ' ' + jstr(TGT) + ' Earnings Packet'
    footer = Paragraph(alignment=RIGHT, spacing={'before': 60}, border={'top': {'style': SINGLE, 'size': 4, 'color': GR}},
                       children=[TextRun(text=jstr(G(M, 'ticker')) + ' · ' + jstr(TGT) + ' Earnings Packet · InSync Analytics · Not investment advice · Page ',
                                         font=FT, size=11, color=GY),
                                 TextRun(children=['CURRENT'], font=FT, size=11, color=GY),
                                 TextRun(text=' of ', font=FT, size=11, color=GY),
                                 TextRun(children=['TOTAL_PAGES'], font=FT, size=11, color=GY)])
    children = mast + s1() + s1b() + s1c() + s2() + s3() + s4() + s5() + s7()
    data = build_docx(children, footer, title, LOGO)
    write_file(OUT, data)
    out('WROTE ' + js_to_fixed(len(data) / 1024, 0) + 'KB')
    PFg = printed()
    write_file('printed_figures.json', _utf8(js_json({'printed': PFg}, 1)))
    out('       printed set: ' + str(len(PFg)) + ' distinct strings on the page')
    wn = jor(jor(G(M, 'warnings') if jt(M) else M, G(C, 'warnings')), [])
    if isinstance(wn, list) and len(wn):
        out('WARNINGS (' + str(len(wn)) + '):')
        for i, x in enumerate(wn):
            out('  ' + str(i + 1) + '. ' + jstr(x))
    pl = [(k, l) for k, l in PRUNE_LOG.items() if l['rows'] or l['cols']]
    if pl:
        out('PRUNED (disclosed on the page by dropNote, listed here so the run shows it too):')
        for key, l in pl:
            if l['rows']:
                out('  ' + key + ': row(s) ' + ', '.join(l['rows']) + ' - no column carried a value')
            if l['cols']:
                out('  ' + key + ': column(s) ' + ', '.join(c['label'] + ' (' + str(c['filled']) + ' of ' + str(l['of']) + ')'
                                                         for c in l['cols']))


DOC_HEAD = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document mc:Ignorable="w14 w15 wp14" '
    'xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas" '
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
    'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
    'xmlns:cx="http://schemas.microsoft.com/office/drawing/2014/chartex" '
    'xmlns:cx1="http://schemas.microsoft.com/office/drawing/2015/9/8/chartex" '
    'xmlns:cx2="http://schemas.microsoft.com/office/drawing/2015/10/21/chartex" '
    'xmlns:cx3="http://schemas.microsoft.com/office/drawing/2016/5/9/chartex" '
    'xmlns:cx4="http://schemas.microsoft.com/office/drawing/2016/5/10/chartex" '
    'xmlns:cx5="http://schemas.microsoft.com/office/drawing/2016/5/11/chartex" '
    'xmlns:cx6="http://schemas.microsoft.com/office/drawing/2016/5/12/chartex" '
    'xmlns:cx7="http://schemas.microsoft.com/office/drawing/2016/5/13/chartex" '
    'xmlns:cx8="http://schemas.microsoft.com/office/drawing/2016/5/14/chartex" '
    'xmlns:aink="http://schemas.microsoft.com/office/drawing/2016/ink" '
    'xmlns:am3d="http://schemas.microsoft.com/office/drawing/2017/model3d" '
    'xmlns:w16cex="http://schemas.microsoft.com/office/word/2018/wordml/cex" '
    'xmlns:w16cid="http://schemas.microsoft.com/office/word/2016/wordml/cid" '
    'xmlns:w16="http://schemas.microsoft.com/office/word/2018/wordml" '
    'xmlns:w16sdtdh="http://schemas.microsoft.com/office/word/2020/wordml/sdtdatahash" '
    'xmlns:w16se="http://schemas.microsoft.com/office/word/2015/wordml/symex">'
)

STYLES_HEAD = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles mc:Ignorable="w14 w15" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" '
    'xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml"><w:docDefaults><w:rPrDefault><w:rPr>'
)

STYLES_TAIL = (
    '</w:rPr></w:rPrDefault><w:pPrDefault/></w:docDefaults><w:style w:type="paragraph" '
    'w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:next '
    'w:val="Normal"/><w:qFormat/><w:rPr><w:sz w:val="56"/><w:szCs '
    'w:val="56"/></w:rPr></w:style><w:style w:type="paragraph" w:styleId="Heading1"><w:name '
    'w:val="Heading 1"/><w:basedOn w:val="Normal"/><w:next '
    'w:val="Normal"/><w:qFormat/><w:rPr><w:color w:val="2E74B5"/><w:sz w:val="32"/><w:szCs '
    'w:val="32"/></w:rPr></w:style><w:style w:type="paragraph" w:styleId="Heading2"><w:name '
    'w:val="Heading 2"/><w:basedOn w:val="Normal"/><w:next '
    'w:val="Normal"/><w:qFormat/><w:rPr><w:color w:val="2E74B5"/><w:sz w:val="26"/><w:szCs '
    'w:val="26"/></w:rPr></w:style><w:style w:type="paragraph" w:styleId="Heading3"><w:name '
    'w:val="Heading 3"/><w:basedOn w:val="Normal"/><w:next '
    'w:val="Normal"/><w:qFormat/><w:rPr><w:color w:val="1F4D78"/><w:sz w:val="24"/><w:szCs '
    'w:val="24"/></w:rPr></w:style><w:style w:type="paragraph" w:styleId="Heading4"><w:name '
    'w:val="Heading 4"/><w:basedOn w:val="Normal"/><w:next '
    'w:val="Normal"/><w:qFormat/><w:rPr><w:i/><w:iCs/><w:color '
    'w:val="2E74B5"/></w:rPr></w:style><w:style w:type="paragraph" w:styleId="Heading5"><w:name '
    'w:val="Heading 5"/><w:basedOn w:val="Normal"/><w:next '
    'w:val="Normal"/><w:qFormat/><w:rPr><w:color w:val="2E74B5"/></w:rPr></w:style><w:style '
    'w:type="paragraph" w:styleId="Heading6"><w:name w:val="Heading 6"/><w:basedOn '
    'w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:rPr><w:color '
    'w:val="1F4D78"/></w:rPr></w:style><w:style w:type="paragraph" w:styleId="Strong"><w:name '
    'w:val="Strong"/><w:basedOn w:val="Normal"/><w:next '
    'w:val="Normal"/><w:qFormat/><w:rPr><w:b/><w:bCs/></w:rPr></w:style><w:style w:type="paragraph" '
    'w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn '
    'w:val="Normal"/><w:qFormat/></w:style><w:style w:type="character" w:styleId="Hyperlink"><w:name '
    'w:val="Hyperlink"/><w:basedOn w:val="DefaultParagraphFont"/><w:uiPriority '
    'w:val="99"/><w:unhideWhenUsed/><w:rPr><w:color w:val="0563C1"/><w:u '
    'w:val="single"/></w:rPr></w:style><w:style w:type="character" '
    'w:styleId="FootnoteReference"><w:name w:val="footnote reference"/><w:basedOn '
    'w:val="DefaultParagraphFont"/><w:uiPriority '
    'w:val="99"/><w:semiHidden/><w:unhideWhenUsed/><w:rPr><w:vertAlign '
    'w:val="superscript"/></w:rPr></w:style><w:style w:type="paragraph" '
    'w:styleId="FootnoteText"><w:name w:val="footnote text"/><w:basedOn w:val="Normal"/><w:link '
    'w:val="FootnoteTextChar"/><w:uiPriority '
    'w:val="99"/><w:semiHidden/><w:unhideWhenUsed/><w:pPr><w:spacing w:after="0" w:line="240" '
    'w:lineRule="auto"/></w:pPr><w:rPr><w:sz w:val="20"/><w:szCs '
    'w:val="20"/></w:rPr></w:style><w:style w:type="character" w:styleId="FootnoteTextChar"><w:name '
    'w:val="Footnote Text Char"/><w:basedOn w:val="DefaultParagraphFont"/><w:link '
    'w:val="FootnoteText"/><w:uiPriority w:val="99"/><w:semiHidden/><w:unhideWhenUsed/><w:rPr><w:sz '
    'w:val="20"/><w:szCs w:val="20"/></w:rPr></w:style><w:style w:type="character" '
    'w:styleId="EndnoteReference"><w:name w:val="endnote reference"/><w:basedOn '
    'w:val="DefaultParagraphFont"/><w:uiPriority '
    'w:val="99"/><w:semiHidden/><w:unhideWhenUsed/><w:rPr><w:vertAlign '
    'w:val="superscript"/></w:rPr></w:style><w:style w:type="paragraph" '
    'w:styleId="EndnoteText"><w:name w:val="endnote text"/><w:basedOn w:val="Normal"/><w:link '
    'w:val="EndnoteTextChar"/><w:uiPriority '
    'w:val="99"/><w:semiHidden/><w:unhideWhenUsed/><w:pPr><w:spacing w:after="0" w:line="240" '
    'w:lineRule="auto"/></w:pPr><w:rPr><w:sz w:val="20"/><w:szCs '
    'w:val="20"/></w:rPr></w:style><w:style w:type="character" w:styleId="EndnoteTextChar"><w:name '
    'w:val="Endnote Text Char"/><w:basedOn w:val="DefaultParagraphFont"/><w:link '
    'w:val="EndnoteText"/><w:uiPriority w:val="99"/><w:semiHidden/><w:unhideWhenUsed/><w:rPr><w:sz '
    'w:val="20"/><w:szCs w:val="20"/></w:rPr></w:style></w:styles>'
)

DOC_RELS_HEAD = (
    '<?xml version="1.0" encoding="UTF-8"?><Relationships '
    'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
    'Target="styles.xml"/><Relationship Id="rId2" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" '
    'Target="numbering.xml"/><Relationship Id="rId3" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" '
    'Target="footnotes.xml"/><Relationship Id="rId4" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/endnotes" '
    'Target="endnotes.xml"/><Relationship Id="rId5" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" '
    'Target="settings.xml"/><Relationship Id="rId6" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments" '
    'Target="comments.xml"/><Relationship Id="rId7" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" '
    'Target="footer1.xml"/>'
)

REL = ('<Relationship Id="%s" '
       'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/%s" Target="%s"/>')

CORE = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties '
    'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
    'xmlns:dcmitype="http://purl.org/dc/dcmitype/" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>%s</dc:title><dc:creator>InSync Analytics</dc:creator>'
    '<cp:lastModifiedBy>Un-named</cp:lastModifiedBy><cp:revision>1</cp:revision><dcterms:created '
    'xsi:type="dcterms:W3CDTF">%s</dcterms:created><dcterms:modified '
    'xsi:type="dcterms:W3CDTF">%s</dcterms:modified></cp:coreProperties>'
)

FTR_HEAD = (
    '<?xml version="1.0" encoding="UTF-8"?><w:ftr '
    'xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas" '
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
    'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
)

ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8"?><Relationships '
    'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/><Relationship Id="rId2" '
    'Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" '
    'Target="docProps/core.xml"/><Relationship Id="rId3" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" '
    'Target="docProps/app.xml"/><Relationship Id="rId4" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties" '
    'Target="docProps/custom.xml"/></Relationships>'
)

EMPTY_RELS = (
    '<?xml version="1.0" encoding="UTF-8"?><Relationships '
    'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'
)

CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?><Types '
    'xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default '
    'ContentType="image/png" Extension="png"/><Default ContentType="image/jpeg" '
    'Extension="jpeg"/><Default ContentType="image/jpeg" Extension="jpg"/><Default '
    'ContentType="image/bmp" Extension="bmp"/><Default ContentType="image/gif" '
    'Extension="gif"/><Default ContentType="image/svg+xml" Extension="svg"/><Default '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml" '
    'Extension="rels"/><Default ContentType="application/xml" Extension="xml"/><Default '
    'ContentType="application/vnd.openxmlformats-officedocument.obfuscatedFont" '
    'Extension="odttf"/><Override '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml" '
    'PartName="/word/document.xml"/><Override '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml" '
    'PartName="/word/styles.xml"/><Override '
    'ContentType="application/vnd.openxmlformats-package.core-properties+xml" '
    'PartName="/docProps/core.xml"/><Override '
    'ContentType="application/vnd.openxmlformats-officedocument.custom-properties+xml" '
    'PartName="/docProps/custom.xml"/><Override '
    'ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml" '
    'PartName="/docProps/app.xml"/><Override '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml" '
    'PartName="/word/numbering.xml"/><Override '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml" '
    'PartName="/word/footnotes.xml"/><Override '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.endnotes+xml" '
    'PartName="/word/endnotes.xml"/><Override '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml" '
    'PartName="/word/settings.xml"/><Override '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml" '
    'PartName="/word/comments.xml"/><Override '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.fontTable+xml" '
    'PartName="/word/fontTable.xml"/><Override '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml" '
    'PartName="/word/footer1.xml"/></Types>'
)

CUSTOM = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Properties '
    'xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties" '
    'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"/>'
)

APP = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Properties '
    'xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
    'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"/>'
)

_NS_STD = (
    'xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas" '
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
    'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" mc:Ignorable="w14 '
    'w15 wp14">'
)


def _notes(kind, ref):
    return ('<?xml version="1.0" encoding="UTF-8"%s?><w:%ss ' % (' standalone="yes"' if kind == 'footnote' else '', kind)
            + _NS_STD
            + '<w:%s w:type="separator" w:id="-1"><w:p><w:pPr><w:spacing w:after="0" '
              'w:line="240" w:lineRule="auto"/></w:pPr><w:r><w:rPr><w:rStyle '
              'w:val="%sReference"/></w:rPr><w:%sRef/></w:r><w:r><w:separator/></w:r></w:p></w:%s><w:%s '
              'w:type="continuationSeparator" w:id="0"><w:p><w:pPr><w:spacing w:after="0" w:line="240" '
              'w:lineRule="auto"/></w:pPr><w:r><w:rPr><w:rStyle '
              'w:val="%sReference"/></w:rPr><w:%sRef/></w:r><w:r><w:continuationSeparator/></w:r></w:p></w:%s></w:%ss>'
            % (kind, ref, kind, kind, kind, ref, kind, kind, kind))


FOOTNOTES = _notes('footnote', 'Footnote')
ENDNOTES = _notes('endnote', 'Endnote')

SETTINGS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:settings ' + _NS_STD
    + '<w:displayBackgroundShape/><w:evenAndOddHeaders '
    'w:val="false"/><w:compat><w:compatSetting w:val="15" w:name="compatibilityMode" '
    'w:uri="http://schemas.microsoft.com/office/word"/></w:compat></w:settings>'
)

COMMENTS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:comments '
    'xmlns:cx="http://schemas.microsoft.com/office/drawing/2014/chartex" '
    'xmlns:cx1="http://schemas.microsoft.com/office/drawing/2015/9/8/chartex" '
    'xmlns:cx2="http://schemas.microsoft.com/office/drawing/2015/10/21/chartex" '
    'xmlns:cx3="http://schemas.microsoft.com/office/drawing/2016/5/9/chartex" '
    'xmlns:cx4="http://schemas.microsoft.com/office/drawing/2016/5/10/chartex" '
    'xmlns:cx5="http://schemas.microsoft.com/office/drawing/2016/5/11/chartex" '
    'xmlns:cx6="http://schemas.microsoft.com/office/drawing/2016/5/12/chartex" '
    'xmlns:cx7="http://schemas.microsoft.com/office/drawing/2016/5/13/chartex" '
    'xmlns:cx8="http://schemas.microsoft.com/office/drawing/2016/5/14/chartex" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
    'xmlns:aink="http://schemas.microsoft.com/office/drawing/2016/ink" '
    'xmlns:am3d="http://schemas.microsoft.com/office/drawing/2017/model3d" '
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
    'xmlns:w16cex="http://schemas.microsoft.com/office/word/2018/wordml/cex" '
    'xmlns:w16cid="http://schemas.microsoft.com/office/word/2016/wordml/cid" '
    'xmlns:w16="http://schemas.microsoft.com/office/word/2018/wordml" '
    'xmlns:w16sdtdh="http://schemas.microsoft.com/office/word/2020/wordml/sdtdatahash" '
    'xmlns:w16se="http://schemas.microsoft.com/office/word/2015/wordml/symex" '
    'xmlns:wpg="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup" '
    'xmlns:wpi="http://schemas.microsoft.com/office/word/2010/wordprocessingInk" '
    'xmlns:wne="http://schemas.microsoft.com/office/word/2006/wordml" '
    'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"/>'
)

FONT_TABLE = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:fonts '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" '
    'xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml" '
    'xmlns:w16cex="http://schemas.microsoft.com/office/word/2018/wordml/cex" '
    'xmlns:w16cid="http://schemas.microsoft.com/office/word/2016/wordml/cid" '
    'xmlns:w16="http://schemas.microsoft.com/office/word/2018/wordml" '
    'xmlns:w16sdtdh="http://schemas.microsoft.com/office/word/2020/wordml/sdtdatahash" '
    'xmlns:w16se="http://schemas.microsoft.com/office/word/2015/wordml/symex" mc:Ignorable="w14 w15 '
    'w16se w16cid w16 w16cex w16sdtdh"/>'
)

_NUM_NS = (
    'xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas" '
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
    'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"'
)
_BULLETS = '●○■●○■●●●'
NUMBERING = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:numbering mc:Ignorable="w14 w15 wp14" '
    + _NUM_NS + '><w:abstractNum w:abstractNumId="1" w15:restartNumberingAfterBreak="0">'
    '<w:multiLevelType w:val="hybridMultilevel"/>'
    + ''.join('<w:lvl w:ilvl="%d" w15:tentative="1"><w:start w:val="1"/><w:numFmt w:val="bullet"/>'
              '<w:lvlText w:val="%s"/><w:lvlJc w:val="left"/><w:pPr><w:ind w:left="%d" w:hanging="360"/>'
              '</w:pPr></w:lvl>' % (i, _BULLETS[i], 720 * (i + 1)) for i in range(9))
    + '</w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="1"/><w:lvlOverride w:ilvl="0">'
      '<w:startOverride w:val="1"/></w:lvlOverride></w:num></w:numbering>'
)


def build_docx(children, footer, title, logo):
    sect = ('<w:sectPr><w:footerReference w:type="default" r:id="rId7"/>'
            '<w:pgSz w:w="%d" w:h="%d" w:orient="portrait"/>'
            '<w:pgMar w:top="%d" w:right="%d" w:bottom="%d" w:left="%d" w:header="708" w:footer="708" w:gutter="0"/>'
            '<w:pgNumType/><w:docGrid w:linePitch="360"/></w:sectPr>' % (PW, PH, MG, MG, MG, MG))
    document = DOC_HEAD + '<w:body>' + ''.join(children) + sect + '</w:body></w:document>'
    styles = (STYLES_HEAD + _el('w:rFonts', [('w:ascii', FT), ('w:cs', FT), ('w:eastAsia', FT), ('w:hAnsi', FT)])
              + '<w:sz w:val="15"/><w:szCs w:val="15"/>' + STYLES_TAIL)
    media = 'media/%s.jpg' % hashlib.sha1(logo).hexdigest()
    rels = (DOC_RELS_HEAD + REL % ('rId8', 'image', media) + REL % ('rId9', 'fontTable', 'fontTable.xml')
            + '</Relationships>')
    now = datetime.datetime.now(datetime.timezone.utc)
    stamp = now.strftime('%Y-%m-%dT%H:%M:%S.') + '%03dZ' % (now.microsecond // 1000)
    parts = [
        ('word/', None), ('word/_rels/', None),
        ('word/_rels/document.xml.rels', rels),
        ('word/document.xml', document),
        ('word/styles.xml', styles),
        ('docProps/', None),
        ('docProps/core.xml', CORE % (xesc(title), stamp, stamp)),
        ('word/numbering.xml', NUMBERING),
        ('_rels/', None),
        ('_rels/.rels', ROOT_RELS),
        ('word/_rels/footer1.xml.rels', EMPTY_RELS),
        ('word/footer1.xml', FTR_HEAD + footer + '</w:ftr>'),
        ('[Content_Types].xml', CONTENT_TYPES),
        ('docProps/custom.xml', CUSTOM),
        ('docProps/app.xml', APP),
        ('word/footnotes.xml', FOOTNOTES),
        ('word/_rels/footnotes.xml.rels', EMPTY_RELS),
        ('word/endnotes.xml', ENDNOTES),
        ('word/_rels/endnotes.xml.rels', EMPTY_RELS),
        ('word/settings.xml', SETTINGS),
        ('word/comments.xml', COMMENTS),
        ('word/_rels/comments.xml.rels', EMPTY_RELS),
        ('word/fontTable.xml', FONT_TABLE),
        ('word/_rels/fontTable.xml.rels', EMPTY_RELS),
        ('word/media/', None), ('word/' + media, logo),
    ]
    return jszip(parts, now)


def _le(v, n):
    return int(v).to_bytes(n, 'little')


def jszip(parts, when):
    t = (when.hour << 11) | (when.minute << 5) | (when.second // 2)
    d = ((when.year - 1980) << 9) | (when.month << 5) | when.day
    body, central = bytearray(), bytearray()
    for name, content in parts:
        nm = name.encode('utf-8')
        if content is None:
            method, crc, data, usize, ext = 0, 0, b'', 0, 0x10
        else:
            raw = content if isinstance(content, bytes) else _utf8(content)
            co = zlib.compressobj(6, zlib.DEFLATED, -15)
            data = co.compress(raw) + co.flush()
            method, crc, usize, ext = 8, zlib.crc32(raw) & 0xFFFFFFFF, len(raw), 0
        common = (b'\x0a\x00' + _le(0, 2) + _le(method, 2) + _le(t, 2) + _le(d, 2) + _le(crc, 4)
                  + _le(len(data), 4) + _le(usize, 4) + _le(len(nm), 2) + _le(0, 2))
        offset = len(body)
        body += b'PK\x03\x04' + common + nm + data
        central += b'PK\x01\x02' + _le(20, 2) + common + _le(0, 2) + b'\x00\x00\x00\x00' + _le(ext, 4) + _le(offset, 4) + nm
    end = (b'PK\x05\x06' + b'\x00\x00\x00\x00' + _le(len(parts), 2) + _le(len(parts), 2) + _le(len(central), 4)
           + _le(len(body), 4) + _le(0, 2))
    return bytes(body + central + end)


def _uncaught(e):
    if isinstance(e, JSError):
        head, msg = '%s: %s' % (e.name, e.message), e.message
    elif isinstance(e, RecursionError):
        head, msg = 'RangeError: Maximum call stack size exceeded', 'Maximum call stack size exceeded'
    else:
        head, msg = '%s: %s' % (type(e).__name__, e), str(e) or type(e).__name__
    tb = ''.join('    at ' + ln.strip() + '\n' for ln in traceback.format_tb(e.__traceback__) if ln.strip())
    sys.stderr.buffer.write(_utf8(head + '\n' + tb))
    sys.stderr.flush()
    out(js_json({'status': 'error', 'where': 'render_packet.js', 'message': msg if msg else jstr(head)}))
    sys.stdout.flush()
    sys.exit(1)


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except BaseException as _e:
        _uncaught(_e)
    sys.stdout.flush()
