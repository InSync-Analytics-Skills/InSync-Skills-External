#!/usr/bin/env python3
"""
generate_tearsheet.py - the Tear Sheet Word renderer (InSync Analytics branded one-pager).

Usage: python3 generate_tearsheet.py --input data.json --output output.docx

Python standard library only (3.8+): no Node, no npm, no pip, so the skill runs in any AI
sandbox. It replaced an earlier JavaScript renderer in 8.0.0 and writes the same .docx parts,
except the two timestamps in docProps/core.xml.

PORTING RULES. Function names match the JavaScript so the two files read side by side. Values
from the payload are read with G() so a missing key is JS `undefined` (UNDEF), which the
renderer treats differently from JSON null in a few places. Truthiness is jt(), `a || b` is
jor(), String(x) is jstr(), Math.round is js_round. Regexes spell out JS's \\s class and use
\\Z for `$`. The XML writer reproduces the `docx` package's element order, attribute order,
escaping and number formatting.
"""
import datetime
import hashlib
import math
import os
import re
import shutil
import subprocess
import sys
import zipfile
from decimal import Decimal, ROUND_HALF_UP

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from jscompat import (js_str, js_json, js_number, js_round, js_parse_float, js_to_fixed,
                      js_stdio, _js_read_json, _JSErr, UNDEFINED as UNDEF)

_JS_WS = ('\t\n\x0b\x0c\r \xa0          '
          '      　﻿')
_S = '[\\t\\n\\x0b\\x0c\\r \\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000\\ufeff]'
_DOT = '[^\\n\\r\\u2028\\u2029]'


def nn(x):
    return x is None or x is UNDEF


def is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def jt(x):
    if x is None or x is UNDEF or x is False:
        return False
    if x is True:
        return True
    if is_num(x):
        return x == x and x != 0
    if isinstance(x, str):
        return len(x) > 0
    return True


def jor(a, b):
    return a if jt(a) else b


def jstr(x):
    if isinstance(x, str):
        return x
    if x is UNDEF:
        return 'undefined'
    if isinstance(x, list):
        return ','.join('' if nn(e) else jstr(e) for e in x)
    if isinstance(x, dict):
        return '[object Object]'
    return js_str(x)


def jtrim(s):
    return s.strip(_JS_WS)


def utf16len(s):
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in s)


def G(o, k):
    if nn(o):
        raise TypeError("Cannot read properties of %s (reading '%s')" % (jstr(o), k))
    if isinstance(o, dict):
        return o.get(k, UNDEF)
    if k == 'length' and isinstance(o, (list, str)):
        return len(o) if isinstance(o, list) else utf16len(o)
    return UNDEF


def jarr(x, what='map'):
    if isinstance(x, list):
        return x
    raise TypeError('%s.%s is not a function' % (jstr(x), what))


def jtrim_of(x):
    if isinstance(x, str):
        return jtrim(x)
    raise TypeError('%s.trim is not a function' % jstr(x))


def jincludes(x, sub):
    if isinstance(x, str):
        return sub in x
    raise TypeError('%s.includes is not a function' % jstr(x))


def js_max(vals):
    vals = list(vals)
    if not vals:
        return float('-inf')
    if any(isinstance(v, float) and math.isnan(v) for v in vals):
        return float('nan')
    return max(vals)


def js_min(vals):
    vals = list(vals)
    if not vals:
        return float('inf')
    if any(isinstance(v, float) and math.isnan(v) for v in vals):
        return float('nan')
    return min(vals)


def err(msg):
    sys.stderr.write(msg + '\n')


def die(msg):
    err(msg)
    sys.stdout.flush()
    sys.stderr.flush()
    sys.exit(1)


js_stdio()
args = sys.argv[1:]
inputFile = None
outputFile = None
i = 0
while i < len(args):
    if args[i] == '--input' and i + 1 < len(args) and args[i + 1]:
        inputFile = args[i + 1]
        i += 1
    elif args[i] == '--output' and i + 1 < len(args) and args[i + 1]:
        outputFile = args[i + 1]
        i += 1
    i += 1
if not inputFile or not outputFile:
    die('Usage: python3 generate_tearsheet.py --input data.json --output output.docx')

try:
    data = _js_read_json(inputFile)
except _JSErr as e:
    die('Error: %s' % e)


def validateInput(d):
    errors = []
    for k in ('ticker', 'company_name'):
        v = G(d, k)
        if not jt(v) or not isinstance(v, str) or not jtrim(v):
            errors.append('Missing or empty required field: "%s"' % k)
    v = G(d, 'business_overview')
    if not jt(v) or not isinstance(v, str) or not jtrim(v):
        errors.append('Missing or empty required field: "business_overview"')
    v = G(d, 'bull_case')
    if not isinstance(v, list) or len(v) == 0:
        errors.append('bull_case must be a non-empty array (investment thesis long side)')
    v = G(d, 'bear_case')
    if not isinstance(v, list) or len(v) == 0:
        errors.append('bear_case must be a non-empty array (investment thesis short side)')
    kf = G(d, 'key_financials')
    if not isinstance(kf, list) or len(kf) == 0:
        errors.append('key_financials must be a non-empty array (>= 1 row with metric/value)')
    for i, row in enumerate(jarr(jor(kf, []), 'forEach')):
        if not jt(G(row, 'metric')):
            errors.append('key_financials[%d] missing field: "metric"' % i)
        if not jt(G(row, 'value')):
            errors.append('key_financials[%d] missing field: "value"' % i)
    v = G(d, 'market_snapshot')
    if v is not UNDEF and not isinstance(v, list):
        errors.append('market_snapshot must be an array if provided')
    v = G(d, 'valuation_metrics')
    if v is not UNDEF and not isinstance(v, list):
        errors.append('valuation_metrics must be an array if provided')
    if G(d, 'companyName') is not UNDEF:
        errors.append('Schema error: use "company_name" not "companyName"')
    if G(d, 'businessOverview') is not UNDEF:
        errors.append('Schema error: use "business_overview" not "businessOverview"')
    if G(d, 'investmentThesis') is not UNDEF:
        errors.append('Schema error: use "bull_case" and "bear_case" arrays, not nested "investmentThesis" object')
    if G(d, 'financials') is not UNDEF:
        errors.append('Schema error: use "key_financials" array, not "financials" object')
    if G(d, 'keyFinancials') is not UNDEF:
        errors.append('Schema error: use "key_financials" not "keyFinancials"')
    if G(d, 'bullCase') is not UNDEF:
        errors.append('Schema error: use "bull_case" not "bullCase"')
    if G(d, 'bearCase') is not UNDEF:
        errors.append('Schema error: use "bear_case" not "bearCase"')
    return errors


inputErrors = validateInput(data)
if inputErrors:
    die(js_json({'status': 'validation_error', 'stage': 'input', 'errors': inputErrors}))

BRAND = {}
_cands = [os.path.normpath(os.path.join(HERE, '..', 'common', 'brand.json')),
          os.path.normpath(os.path.join(HERE, '..', '..', '..', 'common', 'brand.json'))]
_hit = next((f for f in _cands if os.path.exists(f)), None)
if _hit:
    try:
        BRAND = _js_read_json(_hit)
    except _JSErr as e:
        err('Warning: common/brand.json unreadable (%s); using built-in InSync fallbacks' % e)
else:
    err('Warning: common/brand.json not found in skill or library root; using built-in InSync fallbacks')
BC = jor(G(BRAND, 'colors'), {})


def _bc(key, fallback):
    return jor(G(BC, key), fallback)


class _Slots(object):
    def __init__(self, **kw):
        self.__dict__.update(kw)


C = _Slots(
    NAVY=_bc('navy', '232526'),
    NAVY_DARK=_bc('navyDark', '121415'),
    GOLD=_bc('orange', 'E8850C'),
    WHITE=_bc('white', 'FFFFFF'),
    BLACK=_bc('ink', '1A1A1A'),
    GRAY_D=_bc('grayDark', '222222'),
    GRAY_L=_bc('grayLight', '555555'),
    DATE_GREY=_bc('dateGrey', '555555'),
    BORDER=_bc('tableBorder', 'DDDDDD'),
    BG=_bc('calloutBg', 'F6F3EE'),
    BEAT_GREEN=_bc('beatGreen', '1E7A34'),
    MISS_RED=_bc('missRed', 'B02A1F'),
    ROW_ALT=_bc('rowAlt', 'F5F0E8'),
    LIGHT_NAVY=_bc('bandFill', 'F0E9D8'),
    LIGHT_GOLD=_bc('targetRow', 'FDF1E5'),
    HEADBG=_bc('headerBg', 'EDEDED'),
    RULE_H=_bc('headerRule', 'BBBBBB'),
    RULE_R=_bc('rowRule', 'EEEEEE'),
)


def _and(a, key):
    return G(a, key) if jt(a) else a


FONTS = _Slots(
    TITLE=jor(_and(G(BRAND, 'fonts'), 'title'), 'Georgia'),
    BODY=jor(_and(G(BRAND, 'fonts'), 'body'), 'Calibri'),
)
LOGO = jor(G(BRAND, 'logo'), 'INSYNC ANALYTICS')

_li = G(BRAND, 'logoImage')
LOGO_PATH = jor(os.path.abspath(os.path.join(HERE, _li)) if jt(_li) else _li,
                os.path.normpath(os.path.join(HERE, '..', 'assets', 'insync_logo.png')))
LOGO_IMG = None
try:
    with open(LOGO_PATH, 'rb') as _f:
        LOGO_IMG = _f.read()
except (OSError, TypeError):
    err('Note: logo image not found (%s); using text wordmark only' % LOGO_PATH)

_CTRL = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def sanitize(inp):
    if nn(inp):
        return ''
    if isinstance(inp, list):
        inp = ' '.join('' if nn(e) else jstr(e) for e in inp)
    if not isinstance(inp, str):
        inp = jstr(inp)
    return _CTRL.sub('', inp)


_ESC = {'&': '&amp;', '"': '&quot;', "'": '&apos;', '<': '&lt;', '>': '&gt;'}
_ESC_RE = re.compile('[&"<>\']')


def xesc(s):
    return _ESC_RE.sub(lambda m: _ESC[m.group(0)], s)


def _attr_val(v):
    return xesc(v) if isinstance(v, str) else jstr(v)


def _el(name, attrs=(), children=''):
    a = ''.join(' %s="%s"' % (k, _attr_val(v)) for k, v in attrs if v is not UNDEF)
    if children == '':
        return '<%s%s/>' % (name, a)
    return '<%s%s>%s</%s>' % (name, a, children, name)


def _decimal_number(v):
    n = js_number(v) if not is_num(v) else v
    if isinstance(n, float) and math.isnan(n):
        raise ValueError("Invalid value '%s' specified. Must be an integer." % jstr(v))
    return math.floor(n) if not (isinstance(n, float) and math.isinf(n)) else n


def _unsigned(v):
    n = _decimal_number(v)
    if n < 0:
        raise ValueError("Invalid value '%s' specified. Must be a positive integer." % jstr(v))
    return n


def _universal(v):
    unit, amount = v[-2:], v[:-2]
    return '%s%s' % (js_str(js_number(amount)), unit)


def _hps(v):
    if isinstance(v, str):
        out = _universal(v)
        if js_parse_float(out) < 0:
            raise ValueError("Invalid value '%s' specified. Expected a positive number." % out)
        return out
    return _unsigned(v)


def _hex(v):
    if v == 'auto':
        return v
    if not isinstance(v, str):
        raise TypeError('val.charAt is not a function')
    c = v[1:] if v[:1] == '#' else v
    n = js_number('0x' + c)
    if utf16len(c) != 6 or (isinstance(n, float) and math.isnan(n)):
        raise ValueError("Invalid hex value '%s'. Expected 6 digit hex value" % c)
    return c


def _onoff(name, val):
    return _el(name) if val is True else _el(name, [('w:val', val)])


def _text(text):
    if isinstance(text, str):
        return '<w:t xml:space="preserve">%s</w:t>' % xesc(text)
    return '<w:t xml:space="default"/>'


def TextRun(text, font=UNDEF, size=UNDEF, color=UNDEF, bold=UNDEF, italics=UNDEF):
    pr = ''
    if jt(font):
        pr += _el('w:rFonts', [('w:ascii', font), ('w:cs', font), ('w:eastAsia', font),
                               ('w:hAnsi', font)])
    if bold is not UNDEF:
        pr += _onoff('w:b', bold) + _onoff('w:bCs', bold)
    if italics is not UNDEF:
        pr += _onoff('w:i', italics) + _onoff('w:iCs', italics)
    if jt(color):
        pr += _el('w:color', [('w:val', _hex(color))])
    if size is not UNDEF:
        pr += _el('w:sz', [('w:val', _hps(size))])
    if jt(size):
        pr += _el('w:szCs', [('w:val', _hps(size))])
    return '<w:r>%s%s</w:r>' % (('<w:rPr>%s</w:rPr>' % pr) if pr else '', _text(text))


class _P(str):
    pass


def Paragraph(children=(), alignment=UNDEF, spacing=None):
    pr = ''
    if spacing:
        pr += _el('w:spacing', [(('w:' + k), spacing.get(k, UNDEF))
                                for k in ('after', 'before', 'line', 'lineRule')])
    if jt(alignment):
        pr += _el('w:jc', [('w:val', alignment)])
    body = (('<w:pPr>%s</w:pPr>' % pr) if pr else '') + ''.join(children)
    return _P('<w:p>%s</w:p>' % body if body else '<w:p/>')


def ImageRun(data, width, height):
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


def _border(name, b):
    return _el(name, [('w:val', b.get('style', UNDEF)),
                      ('w:color', _hex(b['color']) if 'color' in b else UNDEF),
                      ('w:sz', _unsigned(b['size']) if 'size' in b else UNDEF)])


def _width(name, size):
    return _el(name, [('w:type', 'dxa'), ('w:w', _decimal_number(size))])


def TableCell(children, width, borders, shading, vAlign, margins):
    pr = _width('w:tcW', width)
    pr += '<w:tcBorders>%s</w:tcBorders>' % ''.join(
        _border('w:' + s, borders[s]) for s in ('top', 'left', 'bottom', 'right') if borders.get(s))
    if shading is not UNDEF:
        pr += _el('w:shd', [('w:fill', _hex(shading['fill'])), ('w:val', shading['type'])])
    pr += '<w:tcMar>%s</w:tcMar>' % ''.join(
        _width('w:' + s, margins[s]) for s in ('top', 'left', 'bottom', 'right') if s in margins)
    pr += _el('w:vAlign', [('w:val', vAlign)])
    kids = list(children)
    if not kids or not isinstance(kids[-1], _P):
        kids.append(Paragraph())
    return '<w:tc><w:tcPr>%s</w:tcPr>%s</w:tc>' % (pr, ''.join(kids))


def TableRow(cells, cantSplit=False):
    body = ('<w:trPr><w:cantSplit/></w:trPr>' if cantSplit else '') + ''.join(cells)
    return '<w:tr>%s</w:tr>' % body if body else '<w:tr/>'


_NONE6 = ''.join('<w:%s w:val="none" w:color="auto" w:sz="0"/>' % s
                 for s in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'))


def Table(width, columnWidths, rows):
    grid = ''.join(_el('w:gridCol', [('w:w', _unsigned(w))]) for w in columnWidths)
    return ('<w:tbl><w:tblPr>%s<w:tblBorders>%s</w:tblBorders></w:tblPr>%s%s</w:tbl>'
            % (_width('w:tblW', width), _NONE6,
               '<w:tblGrid>%s</w:tblGrid>' % grid if grid else '<w:tblGrid/>', ''.join(rows)))


LEFT, RIGHT, CENTER = 'left', 'right', 'center'
V_CENTER, V_TOP, V_BOTTOM = 'center', 'top', 'bottom'
SINGLE, NONE = 'single', 'none'

thinBorder = {'style': SINGLE, 'size': 2, 'color': C.RULE_R}
headRule = {'style': SINGLE, 'size': 5, 'color': C.RULE_H}
boxBorder = {'style': SINGLE, 'size': 1, 'color': C.BORDER}
borders = {'top': boxBorder, 'bottom': boxBorder, 'left': boxBorder, 'right': boxBorder}
noBorders = {'top': {'style': NONE}, 'bottom': {'style': NONE}, 'left': {'style': NONE}, 'right': {'style': NONE}}
bottomOnly = {'top': {'style': NONE}, 'bottom': thinBorder, 'left': {'style': NONE}, 'right': {'style': NONE}}
goldLeftBorder = {'top': {'style': NONE}, 'bottom': {'style': NONE},
                  'left': {'style': SINGLE, 'size': 6, 'color': C.GOLD}, 'right': {'style': NONE}}


def NEUTRAL_CELL(t):
    s = jtrim(jstr('' if nn(t) else t)).lower()
    return s in ('', '—', '–', '-', 'n/m', 'n/a', 'nm', 'na', 'unavail')


PAGE_W = 12240
PAGE_H = 15840
MARGIN = 720
CONTENT_W = PAGE_W - (2 * MARGIN)
PAGE_SPLIT = 5400


def cell(content, width, borders=UNDEF, shading=UNDEF, vAlign=UNDEF, margins=UNDEF):
    children = content if isinstance(content, list) else [content]
    return TableCell(children, width,
                     borders if borders is not UNDEF else globals()['borders'],
                     {'fill': shading, 'type': 'clear'} if jt(shading) else UNDEF,
                     jor(vAlign, V_CENTER),
                     margins if margins is not UNDEF else {'top': 40, 'bottom': 40, 'left': 80, 'right': 80})


def p(text, size=UNDEF, color=UNDEF, bold=UNDEF, italics=UNDEF, font=UNDEF, align=UNDEF, spacing=None):
    return Paragraph([TextRun(sanitize(text), font=jor(font, FONTS.BODY), size=jor(size, 16),
                              color=jor(color, C.BLACK), bold=jor(bold, False),
                              italics=jor(italics, False))],
                     alignment=jor(align, LEFT), spacing=spacing or {'before': 0, 'after': 0})


def multiRun(runs, align=UNDEF, spacing=None):
    return Paragraph([TextRun(sanitize(jor(r.get('text', UNDEF), '')), font=jor(r.get('font', UNDEF), FONTS.BODY),
                              size=jor(r.get('size', UNDEF), 16), color=jor(r.get('color', UNDEF), C.BLACK),
                              bold=jor(r.get('bold', UNDEF), False), italics=jor(r.get('italics', UNDEF), False))
                      for r in runs],
                     alignment=jor(align, LEFT), spacing=spacing or {'before': 0, 'after': 0})



GLYPH_PERMILLE = {}


def _put(chars, w):
    for c in chars:
        GLYPH_PERMILLE[c] = w


_put(' \xa0', 254)
_put("'\u2018\u2019", 269)
_put(',.', 328)
_put('\xb7', 338)
_put('l', 344)
_put('j', 346)
_put('i\xec\xed\xee\xef', 354)
_put(':;', 367)
_put('!\xa1', 376)
_put('-\xad', 379)
_put('|\xa6', 388)
_put('f', 393)
_put('t', 397)
_put('\xb0', 420)
_put('I\xcc\xcd\xce\xcf', 446)
_put('()[]', 447)
_put('/\\', 472)
_put('*', 482)
_put('1', 490)
_put('`{}\xa8\xb4\xb8', 500)
_put('"', 510)
_put('s', 513)
_put('\u201c\u201d', 519)
_put('r', 520)
_put('z', 525)
_put('c\xe7', 531)
_put('?\xbf', 548)
_put('\xb6', 549)
_put('\xaa\xb2\xb3\xb9\xba', 552)
_put('7', 554)
_put('y\xfd\xff', 562)
_put('\xa7', 563)
_put('v', 567)
_put('e\xe8\xe9\xea\xeb', 572)
_put('g', 577)
_put('x', 588)
_put('J', 595)
_put('a\xe0\xe1\xe2\xe3\xe4\xe5', 596)
_put('5', 599)
_put('\xa2', 605)
_put('\xab\xbb', 610)
_put('3', 625)
_put('2', 626)
_put('k', 632)
_put('o\xf2\xf3\xf4\xf5\xf6\xf8', 636)
_put('\xf0', 638)
_put('$', 641)
_put('\xfe', 645)
_put('b', 646)
_put('69q', 648)
_put('4S', 649)
_put('p\xdf', 658)
_put('d', 663)
_put('\xb5', 668)
_put('F', 671)
_put('8', 676)
_put('u\xf9\xfa\xfb\xfc', 677)
_put('h', 680)
_put('T', 684)
_put('L', 686)
_put('Z', 689)
_put('n\xa3\xf1', 690)
_put('0P', 701)
_put('#+<=>^_~\xa4\xac\xaf\xb1\xd7\xf7\u2013\u2212', 703)
_put('\xde', 708)
_put('C\xc7\u20ac', 715)
_put('E\xc8\xc9\xca\xcb', 721)
_put('Y\xa5\xdd', 732)
_put('B', 757)
_put('A\xc0\xc1\xc2\xc3\xc4\xc5', 758)
_put('V', 762)
_put('R', 797)
_put('&', 799)
_put('G', 807)
_put('X', 809)
_put('K', 817)
_put('OQ\xd2\xd3\xd4\xd5\xd6\xd8', 820)
_put('U\xd9\xda\xdb\xdc', 833)
_put('D\xd0', 834)
_put('N\xd1', 839)
_put('\xe6', 857)
_put('w', 863)
_put('%', 879)
_put('H', 913)
_put('\u2014', 928)
_put('\xa9\xae', 942)
_put('@', 967)
_put('m', 1016)
_put('M', 1023)
_put('\xc6', 1070)
_put('\xbc\xbd\xbe', 1071)
_put('W', 1126)
GLYPH_DEFAULT = 669

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
            m = jor(G(_js_read_json(cand), 'masthead'), {})
            product = os.path.basename(os.path.dirname(os.path.dirname(cand)))
            want = jor(jor(G(jor(G(m, 'face_overrides'), {}), product), G(m, 'face')), 'Georgia')
            subs = jor(G(jor(G(m, 'face_accepted_substitutes'), {}), jstr(want)), [])
            accepted = [want] + (subs if isinstance(subs, list) else [subs])
            break
    except Exception:
        pass
    if os.name == 'nt':
        return 1
    exe = shutil.which('fc-match')
    if not exe:
        return 1
    try:
        r = subprocess.run([exe, '-f', '%{family}', '%s:bold' % jstr(want)],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return 1
    if r.returncode != 0:
        return 1
    resolved = jtrim(r.stdout.decode('utf-8', 'replace'))
    if not resolved:
        return 1
    flat = re.sub(_S + '+', '', resolved).lower()
    for a in accepted:
        if not isinstance(a, str):
            raise TypeError('a.replace is not a function')
        if re.sub(_S + '+', '', a).lower() in flat:
            return 1
    err('[masthead] "%s" is NOT installed on this box - fontconfig resolves it to "%s", which is '
        'not a metric-faithful stand-in. Sizing the company line for the substitute so it cannot '
        'wrap. Install %s to render at the intended size.' % (jstr(want), resolved, jstr(want)))
    return SUBSTITUTE_WIDTH_FACTOR


FACE_SCALE = _face_scale()


def textWidthPt(s, pt):
    mille = 0
    for c in jstr(s):
        mille += GLYPH_PERMILLE.get(c, GLYPH_DEFAULT)
    return mille * pt * FACE_SCALE / 1000


TS_CO_LINE_BASE = 372
TS_MARK_UP_BASE = 13
TS_CO_LINE_2L = 225
TS_MARK_UP_2L = 2

_RE_NEXT = re.compile('NEXT EARNINGS', re.I | re.ASCII)
_RE_P1 = re.compile('^(\\d)Q' + _S + '*FY(?:\\d{2})?(\\d{2})\\Z', re.I | re.ASCII)
_RE_P2 = re.compile(_S + '*FY(?:\\d{2})?(\\d{2})\\Z', re.I | re.ASCII)


def _u16(s):
    return s.encode('utf-16-le', 'surrogatepass')


def _u16slice(b, a, z=None):
    return b[2 * a:(2 * z if z is not None else None)].decode('utf-16-le', 'surrogatepass')


def _G2(o, *keys):
    v = o
    for k in keys:
        if not jt(v):
            return v
        v = G(v, k)
    return v


def buildTitleBar():
    boxLine = {'style': SINGLE, 'size': 6, 'color': 'C9C9C9'}
    none = {'style': NONE}
    bLeft = {'top': boxLine, 'bottom': boxLine, 'left': boxLine, 'right': none}
    bMid = {'top': boxLine, 'bottom': boxLine, 'left': none, 'right': none}
    bRight = {'top': boxLine, 'bottom': boxLine, 'left': none, 'right': boxLine}
    logoW = 1150
    productName = jor(G(data, 'product_name'), 'Tear Sheet')
    _ms = jarr(jor(G(data, 'market_snapshot'), []), 'filter')
    _hits = [G(x, 'value') for x in _ms if _RE_NEXT.search(jstr(jor(G(x, 'label'), '')))]
    nextEarn = jor(_hits[0] if _hits else UNDEF, '')
    prepared = jor(G(data, 'prepared_date'), '')
    TS_RIGHT_PAD = 120
    tsRightNeedPt = js_max([
        textWidthPt(productName, 17),
        textWidthPt('Prepared: ' + jstr(prepared), 9.5),
        textWidthPt('Expected Earnings Date: ' + jstr(nextEarn), 9.5) if jt(nextEarn) else 0,
    ])
    rightW = min(4536, max(3000, math.ceil(tsRightNeedPt * 20) + 140 + TS_RIGHT_PAD))
    leftW = CONTENT_W - logoW - rightW

    TS_BRAND_SIZE = jor(_G2(BRAND, 'masthead', 'brand_size_half_points'), 33)
    TS_BRAND_SIZE_2L = jor(_G2(BRAND, 'masthead', 'brand_size_two_line_half_points'), 24)
    tsPeriod = _RE_P2.sub(' \\1', _RE_P1.sub('\\1Q\\2', jstr(jor(G(data, 'period'), '')), 1), 1)
    tsTicker = jor(G(data, 'ticker'), '')
    tsSuffix = ' (%s)%s' % (jstr(tsTicker), (' - ' + tsPeriod) if tsPeriod else '')
    tsBudgetPt = (leftW - 40 - 60) / 20

    def tsFits(line, halfPt):
        return textWidthPt(line, halfPt / 2) <= tsBudgetPt * 0.97

    TS_CO_SIZE = 28
    tsCoName = jor(G(data, 'company_name'), '')
    tsCoSize = TS_CO_SIZE
    for s in (28, 27, 26, 25, 24):
        tsCoSize = s
        if tsFits(jstr(tsCoName) + tsSuffix, s):
            break
    tsTwoLine = not tsFits(jstr(tsCoName) + tsSuffix, tsCoSize)
    TS_TWO_SIZE = 20
    tsL1, tsL2 = jstr(tsCoName) + tsSuffix, None
    if tsTwoLine:
        words = (jstr(tsCoName) + tsSuffix).split(' ')

        def w(t):
            return textWidthPt(t, TS_TWO_SIZE / 2)
        cap = tsBudgetPt * 0.95
        best = None
        for i in range(1, len(words)):
            a, b = ' '.join(words[:i]), ' '.join(words[i:])
            wa, wb = w(a), w(b)
            if wa > cap or wb > cap:
                continue
            score = abs(wa - wb)
            if best is None or score < best[2]:
                best = (a, b, score)
        if best is None:
            full = jstr(tsCoName) + tsSuffix
            fu = _u16(full)
            tok = '(%s)' % jstr(tsTicker)
            pi = full.find(tok) if jt(tsTicker) else -1
            tkAt = utf16len(full[:pi]) if pi >= 0 else -1
            tkEnd = -1 if tkAt < 0 else tkAt + utf16len(jstr(tsTicker)) + 2
            for i in range(1, len(fu) // 2):
                if tkAt >= 0 and tkAt < i < tkEnd:
                    continue
                a, b = _u16slice(fu, 0, i), _u16slice(fu, i)
                wa, wb = w(a), w(b)
                if wa > cap or wb > cap:
                    continue
                score = abs(wa - wb)
                if best is None or score < best[2]:
                    best = (a, b, score)
        if best is not None:
            tsL1, tsL2 = best[0], best[1]
        else:
            acc = []
            while words and w(' '.join(acc + [words[0]])) <= cap:
                acc.append(words.pop(0))
            if not acc:
                acc.append(words.pop(0) if words else None)
            tsL1 = ' '.join('' if x is None else x for x in acc)
            tsL2 = ' '.join(words) or ' '

    tsBrandSize = TS_BRAND_SIZE
    if not tsTwoLine:
        drop = (TS_CO_SIZE - tsCoSize) / 2
        tsBrandTwips = 370
        TS_CO_LINE = js_round(TS_CO_LINE_BASE + 5.5 * drop)
        tsMarkUp = js_round(TS_MARK_UP_BASE + 5.5 * drop)
    else:
        tsBrandSize = TS_BRAND_SIZE_2L
        tsBrandTwips = 280
        TS_CO_LINE = TS_CO_LINE_2L
        tsMarkUp = TS_MARK_UP_2L
        tsCoSize = TS_TWO_SIZE
    tsMarkH = jor(_G2(BRAND, 'masthead', 'mark_px', 'h'), 46)
    tsMarkW = jor(_G2(BRAND, 'masthead', 'mark_px', 'w'), 59)

    tkTok = '(%s)' % jstr(tsTicker)

    def tsCoRuns(text, sz):
        def mk(t, c):
            return TextRun(sanitize(t), font='Georgia', size=sz, color=c, bold=True)
        i = text.find(tkTok) if jt(tsTicker) else -1
        if i < 0:
            return [mk(text, C.BLACK)]
        out = []
        if i > 0:
            out.append(mk(text[:i], C.BLACK))
        out.append(mk(tkTok, C.GOLD))
        if i + len(tkTok) < len(text):
            out.append(mk(text[i + len(tkTok):], C.BLACK))
        return out

    def tsCoPara(text, sz):
        return Paragraph(tsCoRuns(text, sz),
                         spacing={'before': 0, 'after': 0, 'line': TS_CO_LINE, 'lineRule': 'exact'})

    logoCell = cell(
        [Paragraph([ImageRun(LOGO_IMG, tsMarkW, tsMarkH)], alignment=LEFT,
                   spacing={'before': 0, 'after': tsMarkUp})] if LOGO_IMG is not None
        else [Paragraph()],
        logoW, borders=bLeft, vAlign=V_BOTTOM,
        margins={'top': 170, 'bottom': 70, 'left': 100, 'right': 60})

    brandCell = cell([
        Paragraph([TextRun('InSync', font='Georgia', size=tsBrandSize, color=C.GOLD, bold=True),
                   TextRun(' Analytics', font='Georgia', size=tsBrandSize, color=C.BLACK)],
                  spacing={'before': 0, 'after': 0, 'line': tsBrandTwips, 'lineRule': 'exact'}),
    ] + ([tsCoPara(tsL1, tsCoSize), tsCoPara(tsL2, tsCoSize)] if tsTwoLine
         else [tsCoPara(tsL1, tsCoSize)]),
        leftW, borders=bMid, vAlign=V_BOTTOM,
        margins={'top': 170, 'bottom': 70, 'left': 0, 'right': 40})

    rightKids = [
        Paragraph([TextRun(productName, font='Georgia', size=34, color=C.BLACK, bold=True)],
                  alignment=RIGHT, spacing={'before': 0, 'after': 0}),
        Paragraph([TextRun('Prepared: ', font='Georgia', size=19, color=C.GOLD, bold=True),
                   TextRun(prepared, font='Georgia', size=19, color=C.DATE_GREY)],
                  alignment=RIGHT, spacing={'before': 30, 'after': 0}),
    ]
    if jt(nextEarn):
        rightKids.append(Paragraph(
            [TextRun('Expected Earnings Date: ', font='Georgia', size=19, color=C.GOLD, bold=True),
             TextRun(nextEarn, font='Georgia', size=19, color=C.DATE_GREY)],
            alignment=RIGHT, spacing={'before': 20, 'after': 0}))
    rightCell = cell(rightKids, rightW, borders=bRight, vAlign=V_CENTER,
                     margins={'top': 60, 'bottom': 60, 'left': 40, 'right': 100})

    mastheadRow = Table(CONTENT_W, [logoW, leftW, rightW],
                        [TableRow([logoCell, brandCell, rightCell], cantSplit=True)])
    orangeRule = Table(CONTENT_W, [CONTENT_W], [TableRow([
        cell([Paragraph()], CONTENT_W,
             borders={'top': {'style': SINGLE, 'size': 20, 'color': C.GOLD},
                      'bottom': none, 'left': none, 'right': none},
             margins={'top': 0, 'bottom': 0, 'left': 0, 'right': 0})])])
    return [mastheadRow, spacer(60), orangeRule]


def buildMarketBar():
    items = jarr(jor(G(data, 'market_snapshot'), []), 'slice')
    colCount = min(len(items), 6)
    cols = []
    if colCount > 0:
        colW = CONTENT_W // colCount
        cols = [colW] * colCount
        cols[colCount - 1] = CONTENT_W - (colW * (colCount - 1))
    cells = [cell([
        p(jor(G(item, 'label'), ''), size=11, color=C.GOLD, bold=True, font=FONTS.BODY, align=CENTER),
        p(jor(G(item, 'value'), ''), size=17, color=C.BLACK, bold=True, font=FONTS.TITLE, align=CENTER),
        p(jor(G(item, 'context'), ''), size=10, color=C.GRAY_D, align=CENTER),
    ], cols[i], shading=C.WHITE,
        borders={'top': {'style': NONE}, 'bottom': {'style': SINGLE, 'size': 3, 'color': C.GOLD},
                 'left': {'style': NONE}, 'right': {'style': NONE}},
        margins={'top': 50, 'bottom': 50, 'left': 40, 'right': 40})
        for i, item in enumerate(items[:colCount])]
    return Table(CONTENT_W, cols, [TableRow(cells)])


FIN_MAG = {'M': 1e6, 'B': 1e9, 'T': 1e12}
_MONEY_RE = re.compile('^(' + _DOT + '*?)(-?[\\d,]+(?:\\.\\d+)?)' + _S + '*([MBT])\\Z', re.ASCII)


def _locale_fixed(v, dp):
    if math.isnan(v):
        return 'NaN'
    if math.isinf(v):
        return '∞'
    q = Decimal(repr(float(v))).quantize(Decimal(1).scaleb(-dp), rounding=ROUND_HALF_UP)
    ip, _, fp = format(q, 'f').partition('.')
    ip = '{:,}'.format(int(ip))
    return ip + ('.' + fp if dp > 0 else '')


def resolveFinUnit(rows):
    parsed = []
    for r in rows:
        m = _MONEY_RE.match(jtrim(jstr(jor(G(r, 'value'), ''))))
        if not m:
            parsed.append(None)
            continue
        parsed.append({'sym': m.group(1),
                       'abs': js_parse_float(m.group(2).replace(',', '')) * FIN_MAG[m.group(3)]})
    money = [x for x in parsed if x is not None]
    if not money:
        return {'header': 'Latest', 'values': [jor(G(r, 'value'), '') for r in rows]}
    mx = js_max(abs(x['abs']) for x in money)
    unit = 'T' if mx >= 1e12 else ('B' if mx >= 1e11 else 'M')
    smallAbs = js_min(abs(x['abs']) for x in money if jt(x['abs']))
    STEPS = ['T', 'B', 'M']
    while unit != 'M' and smallAbs / FIN_MAG[unit] < 0.001:
        unit = STEPS[STEPS.index(unit) + 1]
    div = FIN_MAG[unit]
    if unit == 'M':
        dp = 0
    else:
        small = js_min(abs(x['abs']) / div for x in money if jt(x['abs']))
        intDigits = len(js_str(float(math.floor(small)) if math.isfinite(small) else small)) if small >= 1 else 0
        dp = max(1, min(3, 3 - intDigits))
    sym = next((x['sym'] for x in money if x['sym']), '')

    def fmt(v):
        return ('-' if v < 0 else '') + _locale_fixed(abs(v / div), dp)
    st = jtrim(sym)
    return {
        'header': 'Latest (%s%s%s)' % (st, ' ' if utf16len(st) > 1 else '', unit),
        'values': [fmt(parsed[i]['abs']) if parsed[i] is not None else jor(G(r, 'value'), '')
                   for i, r in enumerate(rows)],
    }


def buildBusinessAndFinancials():
    leftW = 5800
    rightW = CONTENT_W - leftW

    leftContent = [
        p('BUSINESS OVERVIEW', size=13, color=C.BLACK, bold=True, font=FONTS.TITLE, spacing={'after': 40}),
        p(jor(G(data, 'business_overview'), ''), size=14, color=C.GRAY_D, spacing={'after': 40}),
    ]

    kf = jarr(jor(G(data, 'key_financials'), []))
    finUnit = resolveFinUnit(kf)

    def yoyColour(row):
        t = jtrim_of(jor(G(row, 'yoy'), ''))
        if NEUTRAL_CELL(t) or jt(G(row, 'ungraded')):
            return C.GRAY_D
        down = t.startswith('-')
        if jt(G(row, 'lower_is_better')):
            return C.BEAT_GREEN if down else C.MISS_RED
        return C.MISS_RED if down else C.BEAT_GREEN

    finRows = []
    for i, row in enumerate(kf):
        bg = C.WHITE if i % 2 == 0 else C.ROW_ALT
        finRows.append(TableRow([
            cell([p(jor(G(row, 'metric'), ''), size=14, color=C.BLACK)], 2100, shading=bg, borders=bottomOnly,
                 margins={'top': 30, 'bottom': 30, 'left': 60, 'right': 40}),
            cell([p(finUnit['values'][i], size=14, color=C.BLACK, bold=True, align=RIGHT)], 1400, shading=bg,
                 borders=bottomOnly, margins={'top': 30, 'bottom': 30, 'left': 40, 'right': 60}),
            cell([p(jor(G(row, 'yoy'), ''), size=13, color=yoyColour(row), align=RIGHT)], 1100, shading=bg,
                 borders=bottomOnly, margins={'top': 30, 'bottom': 30, 'left': 40, 'right': 60}),
        ]))

    FIN_COLS = [2100, 1400, 1100]
    finTable = Table(sum(FIN_COLS), FIN_COLS, [
        TableRow([
            cell([p('KEY FINANCIALS', size=12, color=C.BLACK, bold=True)], 2100, shading=C.HEADBG,
                 borders=noBorders, margins={'top': 30, 'bottom': 30, 'left': 60, 'right': 40}),
            cell([p(finUnit['header'], size=11, color=C.BLACK, bold=True, align=RIGHT)], 1400, shading=C.HEADBG,
                 borders=noBorders, margins={'top': 30, 'bottom': 30, 'left': 40, 'right': 60}),
            cell([p('YoY', size=11, color=C.BLACK, bold=True, align=RIGHT)], 1100, shading=C.HEADBG,
                 borders=noBorders, margins={'top': 30, 'bottom': 30, 'left': 40, 'right': 60}),
        ]),
    ] + finRows)

    return Table(CONTENT_W, [leftW, rightW], [TableRow([
        cell(leftContent, leftW, borders=noBorders, vAlign=V_TOP,
             margins={'top': 80, 'bottom': 80, 'left': 80, 'right': 120}),
        cell([finTable], rightW, borders=noBorders, vAlign=V_TOP,
             margins={'top': 80, 'bottom': 80, 'left': 120, 'right': 80}),
    ])])


_RE_MISS = re.compile('\\bMiss\\b', re.I | re.ASCII)
_RE_BEAT = re.compile('\\bBeat\\b', re.I | re.ASCII)


def buildKPIs():
    kpis = jarr(jor(G(data, 'operational_kpis'), []), 'slice')[:6]
    if not kpis:
        return p('')

    hasTier = any(jt(G(k, 'tier')) for k in kpis)
    colWidths = [3000, 900, 1500, 1700, 1600, 2100] if hasTier else [3600, 1800, 1800, 1800, 1800]
    o = 1 if hasTier else 0
    headerRow = TableRow([
        cell([p('OPERATIONAL KPIs', size=12, color=C.BLACK, bold=True)], colWidths[0], shading=C.HEADBG, borders=noBorders),
    ] + ([cell([p('Tier', size=11, color=C.BLACK, bold=True, align=CENTER)], colWidths[1], shading=C.HEADBG, borders=noBorders)]
         if hasTier else []) + [
        cell([p('Latest Q', size=11, color=C.BLACK, bold=True, align=RIGHT)], colWidths[1 + o], shading=C.HEADBG, borders=noBorders),
        cell([p('vs Est', size=11, color=C.BLACK, bold=True, align=RIGHT)], colWidths[2 + o], shading=C.HEADBG, borders=noBorders),
        cell([p('YoY', size=11, color=C.BLACK, bold=True, align=RIGHT)], colWidths[3 + o], shading=C.HEADBG, borders=noBorders),
        cell([p('Trend', size=11, color=C.BLACK, bold=True, align=CENTER)], colWidths[4 + o], shading=C.HEADBG, borders=noBorders),
    ])

    def sign(t):
        if NEUTRAL_CELL(t):
            return C.GRAY_D
        if _RE_MISS.search(jstr(t)):
            return C.MISS_RED
        if _RE_BEAT.search(jstr(t)):
            return C.BEAT_GREEN
        return C.MISS_RED if jincludes(t, '-') else C.BEAT_GREEN

    dataRows = []
    for i, kpi in enumerate(kpis):
        bg = C.WHITE if i % 2 == 0 else C.ROW_ALT
        vsTxt = jor(G(kpi, 'vs_est'), '')
        yoyTxt = jor(G(kpi, 'yoy'), '')
        surpriseColor = C.GRAY_D if jt(G(kpi, 'ungraded')) else sign(vsTxt)
        if NEUTRAL_CELL(yoyTxt) or jt(G(kpi, 'ungraded')):
            yoyColor = C.GRAY_D
        elif jt(G(kpi, 'lower_is_better')):
            yoyColor = C.BEAT_GREEN if jtrim_of(yoyTxt).startswith('-') else C.MISS_RED
        else:
            yoyColor = sign(yoyTxt)
        dataRows.append(TableRow([
            cell([p(jor(G(kpi, 'name'), ''), size=14, color=C.BLACK)], colWidths[0], shading=bg, borders=bottomOnly),
        ] + ([cell([p(jor(G(kpi, 'tier'), ''), size=12, color=C.GRAY_D, align=CENTER)], colWidths[1], shading=bg,
                   borders=bottomOnly)] if hasTier else []) + [
            cell([p(jor(G(kpi, 'value'), ''), size=14, color=C.BLACK, bold=True, align=RIGHT)], colWidths[1 + o],
                 shading=bg, borders=bottomOnly),
            cell([p(jor(G(kpi, 'vs_est'), ''), size=13, color=surpriseColor, align=RIGHT)], colWidths[2 + o],
                 shading=bg, borders=bottomOnly),
            cell([p(jor(G(kpi, 'yoy'), ''), size=13, color=yoyColor, align=RIGHT)], colWidths[3 + o],
                 shading=bg, borders=bottomOnly),
            cell([p(jor(G(kpi, 'trend'), ''), size=13, color=C.GRAY_D, align=CENTER)], colWidths[4 + o],
                 shading=bg, borders=bottomOnly),
        ]))

    return Table(CONTENT_W, colWidths, [headerRow] + dataRows)


def buildValuationBar():
    items = jarr(jor(G(data, 'valuation_metrics'), []), 'slice')
    colCount = min(len(items), 6)
    if colCount == 0:
        return p('')
    colW = CONTENT_W // colCount
    colWidths = [colW] * colCount
    colWidths[colCount - 1] = CONTENT_W - (colW * (colCount - 1))

    cells = [cell([
        p(jor(G(item, 'label'), ''), size=11, color=C.GRAY_D, align=CENTER),
        p(jor(G(item, 'current'), ''), size=18, color=C.BLACK, bold=True, font=FONTS.TITLE, align=CENTER),
        p(jor(G(item, 'context'), ''), size=11, color=C.GRAY_L, align=CENTER),
    ], colWidths[i], shading=C.LIGHT_NAVY,
        borders={'top': {'style': NONE}, 'bottom': {'style': NONE},
                 'left': {'style': NONE} if i == 0 else {'style': SINGLE, 'size': 1, 'color': C.BORDER},
                 'right': {'style': NONE}},
        margins={'top': 50, 'bottom': 50, 'left': 40, 'right': 40})
        for i, item in enumerate(items[:colCount])]
    return Table(CONTENT_W, colWidths, [TableRow(cells)])


def buildThesis():
    leftW = CONTENT_W // 2
    rightW = CONTENT_W - leftW

    bullPoints = [multiRun([
        {'text': '• ', 'size': 13, 'color': C.BEAT_GREEN, 'bold': True},
        {'text': pt, 'size': 13, 'color': C.GRAY_D},
    ], spacing={'before': 20, 'after': 20}) for pt in jarr(jor(G(data, 'bull_case'), []))]

    bearPoints = [multiRun([
        {'text': '• ', 'size': 13, 'color': C.MISS_RED, 'bold': True},
        {'text': pt, 'size': 13, 'color': C.GRAY_D},
    ], spacing={'before': 20, 'after': 20}) for pt in jarr(jor(G(data, 'bear_case'), []))]

    return Table(CONTENT_W, [leftW, rightW], [
        TableRow([
            cell([p('BULL THESIS', size=12, color=C.WHITE, bold=True)], leftW,
                 shading=C.BEAT_GREEN, borders=noBorders, margins={'top': 40, 'bottom': 40, 'left': 80, 'right': 80}),
            cell([p('BEAR THESIS', size=12, color=C.WHITE, bold=True)], rightW,
                 shading=C.MISS_RED, borders=noBorders, margins={'top': 40, 'bottom': 40, 'left': 80, 'right': 80}),
        ]),
        TableRow([
            cell(bullPoints, leftW, borders=dict(borders, right={'style': SINGLE, 'size': 1, 'color': C.BORDER}),
                 vAlign=V_TOP, margins={'top': 40, 'bottom': 60, 'left': 80, 'right': 80}),
            cell(bearPoints, rightW, borders=dict(borders, left={'style': NONE}), vAlign=V_TOP,
                 margins={'top': 40, 'bottom': 60, 'left': 80, 'right': 80}),
        ]),
    ])


def buildKeyDebates():
    debates = jarr(jor(G(data, 'key_debates'), []), 'forEach')
    if not debates:
        return p('')

    content = [
        p('KEY INVESTMENT DEBATES', size=13, color=C.BLACK, bold=True, font=FONTS.TITLE, spacing={'after': 40}),
    ]
    for i, d in enumerate(debates):
        content.append(multiRun([
            {'text': '%d. ' % (i + 1), 'size': 13, 'color': C.GOLD, 'bold': True},
            {'text': jor(G(d, 'question'), ''), 'size': 13, 'color': C.BLACK},
        ], spacing={'before': 25, 'after': 10}))
        content.append(p(jor(G(d, 'rationale'), ''), size=12, color=C.GRAY_D, spacing={'after': 25}))

    return Table(CONTENT_W, [CONTENT_W], [TableRow([
        cell(content, CONTENT_W, borders=goldLeftBorder, vAlign=V_TOP,
             margins={'top': 60, 'bottom': 60, 'left': 120, 'right': 80})])])


_RE_RANKKEY = [(re.compile(k + '\\d', re.ASCII), label) for k, label in
               (('G', 'G=growth'), ('P', 'P=P/E'), ('E', 'E=EV/EBITDA'), ('S', 'S=EV/Sales'), ('B', 'B=P/B'))]


def buildCompsAndSupplyChain():
    leftW = PAGE_SPLIT
    rightW = CONTENT_W - leftW

    comps = jarr(jor(G(data, 'comparable_companies'), []), 'some')
    hasRank = any(jt(G(c, 'rank')) for c in comps)

    def notDash(v):
        return jt(v) and not (isinstance(v, str) and v in ('—', '-')) and jtrim(jstr(v)) != ''

    def countOf(k):
        return sum(1 for c in comps if notDash(G(c, k)))
    hasPE = countOf('pe') >= 2
    hasEV = countOf('ev_ebitda') >= 2
    hasES = countOf('ev_sales') >= 2
    hasPB = countOf('pb') >= 2
    COMPS_W = leftW - 200

    def fitTo(arr, target):
        src = sum(arr)
        out = [js_round(x * target / src) for x in arr]
        diff = target - sum(out)
        if diff != 0:
            wi = 0
            for i in range(1, len(out)):
                if out[i] > out[wi]:
                    wi = i
            out[wi] += diff
        return out
    compCols = [{'key': 'name', 'head': 'PEER COMPS', 'w': 1800}]
    if hasPE:
        compCols.append({'key': 'pe', 'head': 'NTM P/E', 'w': 950})
    if hasEV:
        compCols.append({'key': 'ev_ebitda', 'head': 'EV/EBITDA', 'w': 1000})
    if hasES:
        compCols.append({'key': 'ev_sales', 'head': 'EV/Sales', 'w': 1000})
    if hasPB:
        compCols.append({'key': 'pb', 'head': 'P/B', 'w': 800})
    compCols.append({'key': 'rev_growth', 'head': 'Rev Gr%', 'w': 950, 'growth': True})
    RANK_DXA_PER_CHAR, RANK_PAD, RANK_BASE = 54, 90, 800
    NAME_DXA_PER_CHAR, NAME_PAD = 50, 250
    rankNeed = 0
    if hasRank:
        widest = 4
        for c in comps:
            v = G(c, 'rank') if jt(c) else c
            widest = max(widest, utf16len(jstr(jor(v, ''))))
        rankNeed = widest * RANK_DXA_PER_CHAR + RANK_PAD
        compCols.append({'key': 'rank', 'head': 'Rank', 'rank': True, 'w': RANK_BASE})
    widestName = 10
    for c in comps:
        v = G(c, 'name') if jt(c) else c
        widestName = max(widestName, utf16len(jstr(jor(v, ''))))
    nameNeed = widestName * NAME_DXA_PER_CHAR + NAME_PAD
    compColWidths = fitTo([c['w'] for c in compCols], COMPS_W)
    if hasRank and compColWidths[-1] < rankNeed:
        rest = fitTo([c['w'] for c in compCols[:-1]], COMPS_W - rankNeed)
        if rest[0] >= nameNeed:
            compColWidths = rest + [rankNeed]
        else:
            err('[peer table] %d columns need %d dxa for the Rank column and %d for the longest peer '
                'name, but only %d dxa is available; the widths were left proportional and a cell may '
                'wrap onto a second line. If render_pdf.py reports two pages, shorten the longest peer '
                'display name or drop a peer basis (ev_sales / pb) — neither is one of the five '
                'levers eval reports.' % (len(compCols), rankNeed, nameNeed, COMPS_W))
    compRows = []
    for i, c in enumerate(comps):
        bg = C.WHITE if i % 2 == 0 else C.ROW_ALT
        cells = []
        for ci, col in enumerate(compCols):
            isLast = ci == len(compCols) - 1
            txt = jor(G(c, col['key']), '')
            if col.get('growth'):
                colour = C.GRAY_D if NEUTRAL_CELL(txt) else (C.MISS_RED if '-' in jstr(txt) else C.BEAT_GREEN)
            else:
                colour = C.GRAY_D if col.get('rank') else C.BLACK
            cells.append(cell([p(txt, size=11 if col.get('rank') else 13, color=colour,
                                 align=UNDEF if ci == 0 else RIGHT)], compColWidths[ci],
                              shading=bg, borders=bottomOnly,
                              margins={'top': 25, 'bottom': 25, 'left': 60 if ci == 0 else 40,
                                       'right': 60 if isLast else 40}))
        compRows.append(TableRow(cells))

    compsTable = Table(leftW - 200, compColWidths, [
        TableRow([cell([p(col['head'], size=11 if ci == 0 else 10, color=C.BLACK, bold=True,
                          align=UNDEF if ci == 0 else RIGHT)], compColWidths[ci],
                       shading=C.HEADBG, borders=noBorders,
                       margins={'top': 25, 'bottom': 25, 'left': 60 if ci == 0 else 40, 'right': 40})
                  for ci, col in enumerate(compCols)]),
    ] + compRows)
    rankKeys = [label for rx, label in _RE_RANKKEY
                if any(rx.search(jstr(jor(G(c, 'rank'), ''))) for c in comps)]
    rankLegend = (p('Rank: ' + ', '.join(rankKeys) + '; 1=fastest/cheapest of set', size=8, color=C.GRAY_D,
                    spacing={'before': 30})
                  if hasRank and rankKeys else None)

    supplyContent = [
        p('SUPPLY CHAIN & ECOSYSTEM', size=12, color=C.BLACK, bold=True, font=FONTS.TITLE, spacing={'after': 60}),
        p(jor(G(data, 'supply_chain'), ''), size=13, color=C.GRAY_D, spacing={'after': 40}),
    ]

    return Table(CONTENT_W, [leftW, rightW], [TableRow([
        cell([compsTable, rankLegend] if rankLegend else [compsTable], leftW, borders=noBorders, vAlign=V_TOP,
             margins={'top': 60, 'bottom': 60, 'left': 20, 'right': 80}),
        cell(supplyContent, rightW, borders=noBorders, vAlign=V_TOP,
             margins={'top': 60, 'bottom': 60, 'left': 80, 'right': 80}),
    ])])


def buildCatalystsAndNews():
    leftW = PAGE_SPLIT
    rightW = CONTENT_W - leftW

    catContent = [
        p('UPCOMING CATALYSTS', size=12, color=C.BLACK, bold=True, font=FONTS.TITLE, spacing={'after': 40}),
    ]
    for cat in jarr(jor(G(data, 'catalysts'), []), 'forEach'):
        catContent.append(multiRun([
            {'text': jstr(jor(G(cat, 'date'), '')) + '  ', 'size': 13, 'color': C.GOLD, 'bold': True},
            {'text': jor(G(cat, 'event'), ''), 'size': 13, 'color': C.GRAY_D},
        ], spacing={'before': 20, 'after': 20}))

    newsContent = [
        p('MONITORING & SIGNALS', size=12, color=C.BLACK, bold=True, font=FONTS.TITLE, spacing={'after': 40}),
    ]
    for n in jarr(jor(G(data, 'recent_news'), []), 'forEach'):
        newsContent.append(multiRun([
            {'text': '• ', 'size': 13, 'color': C.GOLD},
            {'text': n, 'size': 13, 'color': C.GRAY_D},
        ], spacing={'before': 15, 'after': 15}))

    return Table(CONTENT_W, [leftW, rightW], [TableRow([
        cell(catContent, leftW, borders=noBorders, vAlign=V_TOP,
             margins={'top': 40, 'bottom': 40, 'left': 80, 'right': 80}),
        cell(newsContent, rightW, borders=noBorders, vAlign=V_TOP,
             margins={'top': 40, 'bottom': 40, 'left': 80, 'right': 80}),
    ])])


def buildFooter():
    if not isinstance(LOGO, str):
        raise TypeError('LOGO.split is not a function')
    parts = LOGO.split(' ')
    kids = [multiRun([
        {'text': parts[0] + ' ', 'font': FONTS.TITLE, 'size': 11, 'color': C.GOLD},
        {'text': ' '.join(parts[1:]), 'font': FONTS.TITLE, 'size': 11, 'color': C.GRAY_L},
        {'text': '  |  %s Tear Sheet  |  %s' % (jstr(jor(G(data, 'ticker'), '')), jstr(jor(G(data, 'prepared_date'), ''))),
         'size': 11, 'color': C.GRAY_L},
    ], align=CENTER)]
    prov = G(data, 'provenance')
    kids.append(p((jstr(prov) + '  ·  ' if jt(prov) else '') + 'Not investment advice.', size=9,
                  color=C.GRAY_L, align=CENTER))
    return ''.join(kids)


def spacer(h=80):
    return Paragraph(spacing={'before': h, 'after': 0})


thinRule = Table(CONTENT_W, [CONTENT_W], [TableRow([
    cell([Paragraph()], CONTENT_W,
         borders={'top': {'style': SINGLE, 'size': 1, 'color': C.BORDER}, 'bottom': {'style': NONE},
                  'left': {'style': NONE}, 'right': {'style': NONE}},
         margins={'top': 0, 'bottom': 0, 'left': 0, 'right': 0})])])

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

REL = (
    '<Relationship Id="%s" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/%s" Target="%s"/>'
)

CORE = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties '
    'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
    'xmlns:dcmitype="http://purl.org/dc/dcmitype/" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:creator>Un-named</dc:creator><cp:lastModifiedBy>Un-named</cp:lastModifiedBy><cp:revision>1</cp:revision><dcterms:created '
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

FOOTNOTES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:footnotes '
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
    'w15 wp14"><w:footnote w:type="separator" w:id="-1"><w:p><w:pPr><w:spacing w:after="0" '
    'w:line="240" w:lineRule="auto"/></w:pPr><w:r><w:rPr><w:rStyle '
    'w:val="FootnoteReference"/></w:rPr><w:footnoteRef/></w:r><w:r><w:separator/></w:r></w:p></w:footnote><w:footnote '
    'w:type="continuationSeparator" w:id="0"><w:p><w:pPr><w:spacing w:after="0" w:line="240" '
    'w:lineRule="auto"/></w:pPr><w:r><w:rPr><w:rStyle '
    'w:val="FootnoteReference"/></w:rPr><w:footnoteRef/></w:r><w:r><w:continuationSeparator/></w:r></w:p></w:footnote></w:footnotes>'
)

ENDNOTES = (
    '<?xml version="1.0" encoding="UTF-8"?><w:endnotes '
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
    'w15 wp14"><w:endnote w:type="separator" w:id="-1"><w:p><w:pPr><w:spacing w:after="0" '
    'w:line="240" w:lineRule="auto"/></w:pPr><w:r><w:rPr><w:rStyle '
    'w:val="EndnoteReference"/></w:rPr><w:endnoteRef/></w:r><w:r><w:separator/></w:r></w:p></w:endnote><w:endnote '
    'w:type="continuationSeparator" w:id="0"><w:p><w:pPr><w:spacing w:after="0" w:line="240" '
    'w:lineRule="auto"/></w:pPr><w:r><w:rPr><w:rStyle '
    'w:val="EndnoteReference"/></w:rPr><w:endnoteRef/></w:r><w:r><w:continuationSeparator/></w:r></w:p></w:endnote></w:endnotes>'
)

SETTINGS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:settings '
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
    'w15 wp14"><w:displayBackgroundShape/><w:evenAndOddHeaders '
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

NUMBERING = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:numbering mc:Ignorable="w14 w15 '
    'wp14" xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas" xmlns'
    ':mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:o="urn:schemas-mic'
    'rosoft-com:office:office" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/r'
    'elationships" xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" xmlns:v'
    '="urn:schemas-microsoft-com:vml" xmlns:wp14="http://schemas.microsoft.com/office/word/2010'
    '/wordprocessingDrawing" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordpro'
    'cessingDrawing" xmlns:w10="urn:schemas-microsoft-com:office:word" xmlns:w="http://schemas.'
    'openxmlformats.org/wordprocessingml/2006/main" xmlns:w14="http://schemas.microsoft.com/off'
    'ice/word/2010/wordml" xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml" xml'
    'ns:wpg="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup" xmlns:wpi="http'
    '://schemas.microsoft.com/office/word/2010/wordprocessingInk" xmlns:wne="http://schemas.mic'
    'rosoft.com/office/word/2006/wordml" xmlns:wps="http://schemas.microsoft.com/office/word/20'
    '10/wordprocessingShape"><w:abstractNum w:abstractNumId="1" w15:restartNumberingAfterBreak='
    '"0"><w:multiLevelType w:val="hybridMultilevel"/><w:lvl w:ilvl="0" w15:tentative="1"><w:sta'
    'rt w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="\u25cf"/><w:lvlJc w:val="left"/><w:pP'
    'r><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl><w:lvl w:ilvl="1" w15:tentative="1"'
    '><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="\u25cb"/><w:lvlJc w:val="left"'
    '/><w:pPr><w:ind w:left="1440" w:hanging="360"/></w:pPr></w:lvl><w:lvl w:ilvl="2" w15:tenta'
    'tive="1"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="\u25a0"/><w:lvlJc w:va'
    'l="left"/><w:pPr><w:ind w:left="2160" w:hanging="360"/></w:pPr></w:lvl><w:lvl w:ilvl="3" w'
    '15:tentative="1"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="\u25cf"/><w:lv'
    'lJc w:val="left"/><w:pPr><w:ind w:left="2880" w:hanging="360"/></w:pPr></w:lvl><w:lvl w:il'
    'vl="4" w15:tentative="1"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="\u25cb'
    '"/><w:lvlJc w:val="left"/><w:pPr><w:ind w:left="3600" w:hanging="360"/></w:pPr></w:lvl><w:'
    'lvl w:ilvl="5" w15:tentative="1"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText '
    'w:val="\u25a0"/><w:lvlJc w:val="left"/><w:pPr><w:ind w:left="4320" w:hanging="360"/></w:pPr></w'
    ':lvl><w:lvl w:ilvl="6" w15:tentative="1"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:'
    'lvlText w:val="\u25cf"/><w:lvlJc w:val="left"/><w:pPr><w:ind w:left="5040" w:hanging="360"/></w'
    ':pPr></w:lvl><w:lvl w:ilvl="7" w15:tentative="1"><w:start w:val="1"/><w:numFmt w:val="bull'
    'et"/><w:lvlText w:val="\u25cf"/><w:lvlJc w:val="left"/><w:pPr><w:ind w:left="5760" w:hanging="3'
    '60"/></w:pPr></w:lvl><w:lvl w:ilvl="8" w15:tentative="1"><w:start w:val="1"/><w:numFmt w:v'
    'al="bullet"/><w:lvlText w:val="\u25cf"/><w:lvlJc w:val="left"/><w:pPr><w:ind w:left="6480" w:ha'
    'nging="360"/></w:pPr></w:lvl></w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="1"'
    '/><w:lvlOverride w:ilvl="0"><w:startOverride w:val="1"/></w:lvlOverride></w:num></w:number'
    'ing>'
)


def _xml_bytes(s):
    return s.encode('utf-16', 'surrogatepass').decode('utf-16', 'replace').encode('utf-8')


def buildDocx():
    footer = buildFooter()
    body = ''.join(buildTitleBar() + [
        spacer(20), buildMarketBar(), spacer(30), buildBusinessAndFinancials(), spacer(20),
        buildKPIs(), spacer(20), buildValuationBar(), spacer(30), buildThesis(), spacer(30),
        buildKeyDebates(), spacer(20), thinRule, spacer(20), buildCompsAndSupplyChain(),
        spacer(20), buildCatalystsAndNews(),
    ])
    sect = ('<w:sectPr><w:footerReference w:type="default" r:id="rId7"/>'
            '<w:pgSz w:w="%d" w:h="%d" w:orient="portrait"/>'
            '<w:pgMar w:top="%d" w:right="%d" w:bottom="%d" w:left="%d" w:header="708" w:footer="708" w:gutter="0"/>'
            '<w:pgNumType/><w:docGrid w:linePitch="360"/></w:sectPr>'
            % (PAGE_W, PAGE_H, MARGIN, MARGIN, MARGIN, MARGIN))
    document = DOC_HEAD + '<w:body>' + body + sect + '</w:body></w:document>'
    styles = STYLES_HEAD + _el('w:rFonts', [('w:ascii', FONTS.BODY), ('w:cs', FONTS.BODY),
                                            ('w:eastAsia', FONTS.BODY), ('w:hAnsi', FONTS.BODY)]) \
        + '<w:sz w:val="16"/><w:szCs w:val="16"/>' + STYLES_TAIL
    media = None
    rels = DOC_RELS_HEAD
    if LOGO_IMG is not None:
        media = 'media/%s.png' % hashlib.sha1(LOGO_IMG).hexdigest()
        rels += REL % ('rId8', 'image', media) + REL % ('rId9', 'fontTable', 'fontTable.xml')
    else:
        rels += REL % ('rId8', 'fontTable', 'fontTable.xml')
    rels += '</Relationships>'
    now = datetime.datetime.now(datetime.timezone.utc)
    stamp = now.strftime('%Y-%m-%dT%H:%M:%S.') + '%03dZ' % (now.microsecond // 1000)
    parts = [
        ('word/', None), ('word/_rels/', None),
        ('word/_rels/document.xml.rels', rels),
        ('word/document.xml', document),
        ('word/styles.xml', styles),
        ('docProps/', None),
        ('docProps/core.xml', CORE % (stamp, stamp)),
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
    ]
    if media:
        parts += [('word/media/', None), ('word/' + media, LOGO_IMG)]
    return parts


def _write_zip(path, parts):
    t = datetime.datetime.now().timetuple()[:6]
    with zipfile.ZipFile(path, 'w') as z:
        for name, content in parts:
            zi = zipfile.ZipInfo(name, date_time=max(t, (1980, 1, 1, 0, 0, 0)))
            if content is None:
                zi.compress_type = zipfile.ZIP_STORED
                zi.external_attr = 0o40755 << 16 | 0x10
                z.writestr(zi, b'')
            else:
                zi.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(zi, content if isinstance(content, bytes) else _xml_bytes(content))


_parts = buildDocx()
try:
    _write_zip(outputFile, _parts)
except OSError as e:
    if os.path.isdir(outputFile):
        _m = "EISDIR: illegal operation on a directory, open '%s'" % outputFile
    elif isinstance(e, FileNotFoundError):
        _m = "ENOENT: no such file or directory, open '%s'" % outputFile
    elif isinstance(e, PermissionError):
        _m = "EPERM: operation not permitted, open '%s'" % outputFile
    else:
        _m = str(e)
    die('Error generating document: %s' % _m)
_size = os.path.getsize(outputFile)
print('✅ Tear sheet generated: %s' % outputFile)
print('   File size: %s KB' % js_to_fixed(_size / 1024, 1))
