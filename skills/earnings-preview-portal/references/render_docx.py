#!/usr/bin/env python3
"""
render_docx.py - the platform-independent Word renderer for the Earnings Preview (Path B).
Usage: python3 render_docx.py <computed.json> <narrative.json> <out.docx> [chart.png]

Produces a .docx BYTE-IDENTICAL to the earlier JavaScript renderer: raw OOXML plus the same tiny STORED zip
writer (fixed DOS time/date, no compression, no extra fields), so the Word twin is identical
on every platform with no dependencies. Standard library only, Python 3.9+.

The layout is the LOCKED v11E format (black title band, orange section headings over orange
rules; tables follow InSync PDF house format v1: horizontal rules only, #EDEDED header bands,
#F5F0E8 zebra, (Estimate)/(Actual) sub-labels; cream callouts with flavor-coloured left
borders, rows never split, header rows repeat). The logo is read from
<skill>/assets/insync_logo.png; the chart PNG argument is accepted and ignored (the beat
chart is a native Word chart part).

PORTING RULES. Every value read from computed/narrative JSON goes through G() so a missing key
is JS `undefined` (UNDEFINED), distinct from JSON null (None) - the renderer branches on that
difference in several places (`!== null` lets undefined through, `o.pol !== undefined`, ...).
JSON integers load as floats, exactly as JSON.parse makes doubles. Every string concatenation
of a data value goes through jstr() (String()), truthiness through jt() (JS rules: [] and {}
are truthy, NaN is falsy), and regexes use explicit JS character classes (\\s, \\d, $).
Math.pow(10, e) is V8's value (pow10), which is not always correctly rounded.
Function names match the earlier JavaScript renderer.
"""
import json
import math
import os
import re
import struct
import subprocess
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jscompat import js_str, js_to_fixed, js_round, js_number, js_stdio, UNDEFINED

HERE = os.path.dirname(os.path.abspath(__file__))

_JS_WS = ('\t\n\x0b\x0c\r \xa0         '
          '       　﻿')
_S = '[\\t\\n\\x0b\\x0c\\r \\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000\\ufeff]'
_RE_WS_RUN = re.compile(_S + '+')
_EPS = 2.220446049250313e-16
_RE_LONE_SURR = re.compile('[' + chr(0xD800) + '-' + chr(0xDFFF) + ']')

_V8_POW10 = {
    -12: '3d719799812dea11', -11: '3da5fd7fe1796495', -10: '3ddb7cdfd9d7bdbb',
    -9: '3e112e0be826d695', -8: '3e45798ee2308c3a', -7: '3e7ad7f29abcaf48',
    -6: '3eb0c6f7a0b5ed8d', -5: '3ee4f8b588e368f0', -4: '3f1a36e2eb1c432c',
    -3: '3f50624dd2f1a9fc', -2: '3f847ae147ae147b', -1: '3fb999999999999a',
}


def pow10(e):
    if isinstance(e, (int, float)) and not isinstance(e, bool) and math.isfinite(e) and float(e).is_integer():
        ie = int(e)
        if ie in _V8_POW10:
            return struct.unpack('>d', bytes.fromhex(_V8_POW10[ie]))[0]
        if 0 <= ie <= 22:
            return float(10 ** ie)
    return math.pow(10, e)


def is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def nn(x):
    return x is None or x is UNDEFINED


def jt(x):
    if x is None or x is UNDEFINED or x is False:
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
    if x is UNDEFINED:
        return 'undefined'
    if isinstance(x, list):
        return ','.join('' if nn(e) else jstr(e) for e in x)
    if isinstance(x, dict):
        return '[object Object]'
    return js_str(x)


def jnum(x):
    if isinstance(x, dict):
        return float('nan')
    return float(js_number(x))


def jisfinite(x):
    return math.isfinite(jnum(x))


def jtrim(s):
    return s.strip(_JS_WS)


def utf16len(s):
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in s)


def seq(a, b):
    if nn(a) or nn(b):
        return a is b
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if is_num(a) and is_num(b):
        return a == b
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    return a is b


def G(o, k):
    if o is None or o is UNDEFINED:
        raise TypeError("Cannot read properties of %s (reading '%s')" % (jstr(o), jstr(k)))
    if isinstance(o, dict):
        return o.get(k if isinstance(k, str) else jstr(k), UNDEFINED)
    if isinstance(o, list):
        if k == 'length':
            return len(o)
        if isinstance(k, str) and (id(o), k) in _EXPANDO:
            return _EXPANDO[(id(o), k)][1]
        if is_num(k) and float(k).is_integer() and 0 <= k < len(o):
            return o[int(k)]
        return UNDEFINED
    if isinstance(o, str):
        if k == 'length':
            return utf16len(o)
        return UNDEFINED
    return UNDEFINED


_EXPANDO = {}


def setprop(o, k, v):
    if isinstance(o, dict):
        o[k] = v
    elif isinstance(o, list):
        _EXPANDO[(id(o), k)] = (o, v)
    else:
        raise TypeError("Cannot create property '%s' on %s" % (k, jstr(o)))


def jarr(x):
    if isinstance(x, list):
        return x
    raise TypeError('%s.map is not a function' % jstr(x))


def gi(lst, i):
    return lst[i] if isinstance(lst, list) and 0 <= i < len(lst) else UNDEFINED


def jlist(x):
    if not jt(x):
        return []
    if isinstance(x, list):
        return x
    raise TypeError('%s.forEach is not a function' % jstr(x))


def jjoin(lst, sep):
    return sep.join('' if nn(e) else jstr(e) for e in lst)


def js_parse_int(s):
    t = jstr(s).lstrip(_JS_WS)
    m = re.match(r'([+-]?)([0-9]+)', t)
    if not m:
        return float('nan')
    v = int(m.group(2))
    return -v if m.group(1) == '-' else v


def jslice(s, a, b=None):
    if not isinstance(s, str):
        raise TypeError('%s.slice is not a function' % jstr(s))
    return s[a:b] if b is not None else s[a:]


def utf8(s):
    return _RE_LONE_SURR.sub('�', s).encode('utf-8')


def err(msg):
    sys.stderr.write(msg + '\n')


def crc32(buf):
    return zlib.crc32(buf) & 0xFFFFFFFF


def zipStore(entries):
    locals_, centrals = [], []
    offset = 0
    for e in entries:
        name = utf8(e['name'])
        data = e['data']
        crc = crc32(data)
        lh = struct.pack('<IHHHHHIIIHH', 0x04034b50, 20, 0, 0, 0x548E, 0x5AF1,
                         crc, len(data), len(data), len(name), 0)
        locals_ += [lh, name, data]
        ch = struct.pack('<IHHHHHHIIIHHHHHII', 0x02014b50, 20, 20, 0, 0, 0x548E, 0x5AF1,
                         crc, len(data), len(data), len(name), 0, 0, 0, 0, 0, offset)
        centrals += [ch, name]
        offset += 30 + len(name) + len(data)
    cdStart = offset
    cdLen = sum(len(b) for b in centrals)
    end = struct.pack('<IHHHHIIH', 0x06054b50, 0, 0, len(entries), len(entries), cdLen, cdStart, 0)
    return b''.join(locals_ + centrals + [end])


MDASH, MINUS = '—', '−'
GE, LE = '≥', '≤'


def xlRound(n, dp=UNDEFINED):
    if nn(n) or not jisfinite(n):
        return n
    d = 0 if dp is UNDEFINED else dp
    x = jnum(n)
    sign = -1.0 if x < 0 else 1.0
    a = abs(x)
    f = pow10(d)
    scaled = a * f
    return sign * float(js_round(scaled + (abs(scaled) * _EPS * 4))) / f


def xlFixed(n, dp=UNDEFINED):
    d = 0 if dp is UNDEFINED else dp
    r = xlRound(n, d)
    if not is_num(r):
        raise TypeError("Cannot read properties of %s (reading 'toFixed')" % jstr(r))
    return js_to_fixed(0 if r == 0 else r, d)


MONEY_DP_MIN, MONEY_DP_MAX = 2, 4
MONEY_MAGS = [{'min': 1e6, 'div': 1e6, 'suf': 'T'}, {'min': 1e3, 'div': 1e3, 'suf': 'B'},
              {'min': 0, 'div': 1, 'suf': 'M'}]


def moneyShape(values):
    vals = values if isinstance(values, list) else []
    nums = [v for v in vals if is_num(v) and math.isfinite(v)]
    nz = [a for a in (abs(v) for v in nums) if a > 0]
    if not nz:
        return {'div': 1, 'suf': 'M', 'dp': MONEY_DP_MIN}
    mx = max(nz)
    m = next((x for x in MONEY_MAGS if mx >= x['min']), MONEY_MAGS[-1])
    uniq = sorted(dict.fromkeys(nums))
    gap = math.inf
    for i in range(1, len(uniq)):
        d = uniq[i] - uniq[i - 1]
        if d > 0 and d < gap:
            gap = d
    dp = MONEY_DP_MIN
    while dp < MONEY_DP_MAX and math.isfinite(gap) and (gap / m['div']) < pow10(-dp):
        dp += 1
    return {'div': m['div'], 'suf': m['suf'], 'dp': dp}


def moneyRow(row, values):
    if not jt(row):
        return moneyShape(values)
    if not jt(G(row, '__money')):
        setprop(row, '__money', moneyShape(values))
    return G(row, '__money')


def deltaShape(shape, d):
    sh = shape if jt(shape) else {'div': 1, 'suf': 'M', 'dp': MONEY_DP_MIN}
    dp = sh['dp']
    a = abs(d if (is_num(d) and math.isfinite(d)) else 0) / sh['div']
    while dp < MONEY_DP_MAX and a > 0 and a < pow10(-dp):
        dp += 1
    return {'div': sh['div'], 'suf': sh['suf'], 'dp': dp}


_RE_GROUP = re.compile(r'\B(?=(\d{3})+(?!\d))', re.ASCII)


def groupDigits(s):
    return _RE_GROUP.sub(',', s)


def fmtM(n, shape=UNDEFINED):
    if nn(n) or not jisfinite(n):
        return MDASH
    sh = shape if jt(shape) else moneyShape([n])
    x = jnum(n)
    parts = xlFixed(abs(x) / sh['div'], sh['dp']).split('.')
    s = CS + groupDigits(parts[0]) + '.' + parts[1] + sh['suf']
    return '(' + s + ')' if x < 0 else s


CS = '$'
CURM = 'USD'
ZERO_DEC = {'JPY', 'KRW', 'VND', 'CLP', 'ISK', 'PYG', 'RWF', 'UGX', 'VUV',
            'XAF', 'XOF', 'XPF', 'BIF', 'DJF', 'GNF', 'KMF'}


def fmtEps(n, minor=UNDEFINED):
    if nn(n) or not jisfinite(n):
        return MDASH
    x = jnum(n)
    a = abs(x)
    zeroDec = isinstance(CURM, str) and CURM in ZERO_DEC and not jt(minor) and not (a > 0 and a < 1)
    dec = 0 if zeroDec else (4 if (jt(minor) or (a > 0 and a < 0.1)) else 2)
    parts = xlFixed(a, dec).split('.')
    s = CS + groupDigits(parts[0]) + ('' if dec == 0 else '.' + parts[1])
    return '(' + s + ')' if x < 0 else s


def fmtMag(n):
    if nn(n) or not jisfinite(n):
        return MDASH
    return fmtM(n)


def fmtGrowth(n, nm=UNDEFINED):
    return ('NM' if jt(nm) else MDASH) if nn(n) else fmtPct(n, 1, True)


def fmtPct(n, dec=UNDEFINED, signed=UNDEFINED):
    if nn(n) or not jisfinite(n):
        return MDASH
    d = 1 if dec is UNDEFINED else dec
    shown = xlRound(n, d)
    body_ = xlFixed(abs(jnum(n)), d) + '%'
    if shown < 0:
        return MINUS + body_
    return ('+' if (jt(signed) and shown > 0) else '') + body_


def fmtX(v):
    if nn(v) or not jisfinite(v):
        return MDASH
    return xlFixed(v, 2 if abs(xlRound(v, 2)) < 1 else 1) + 'x'


def fmtSignedM(n, shape=UNDEFINED):
    if nn(n) or not jisfinite(n):
        return MDASH
    sh = deltaShape(shape, n)
    x = jnum(n)
    sp = xlFixed(abs(x) / sh['div'], sh['dp']).split('.')
    s = CS + groupDigits(sp[0]) + '.' + sp[1] + sh['suf']
    return (MINUS if x < 0 else '+') + s


def fmtCount(n, shape=UNDEFINED):
    if nn(n) or not jisfinite(n):
        return MDASH
    x = jnum(n)
    a = abs(x)
    sign = MINUS if x < 0 else ''
    if isinstance(shape, dict):
        parts = xlFixed(a / shape['div'], shape['dp']).split('.')
        return sign + groupDigits(parts[0]) + '.' + parts[1] + shape['suf']
    return sign + groupDigits(xlFixed(a, 0))


COUNT_MAGS = [{'min': 1e9, 'div': 1e9, 'suf': 'B'}, {'min': 1e6, 'div': 1e6, 'suf': 'M'}]


def countShape(values):
    nums = [v for v in (values if isinstance(values, list) else []) if is_num(v) and math.isfinite(v)]
    nz = [a for a in (abs(v) for v in nums) if a > 0]
    if not nz:
        return None
    mx = max(nz)
    m = next((x for x in COUNT_MAGS if mx >= x['min']), None)
    if m is None:
        return None
    uniq = sorted(dict.fromkeys(nums))
    gap = math.inf
    for i in range(1, len(uniq)):
        d = uniq[i] - uniq[i - 1]
        if d > 0 and d < gap:
            gap = d
    dp = MONEY_DP_MIN
    while dp < MONEY_DP_MAX and math.isfinite(gap) and (gap / m['div']) < pow10(-dp):
        dp += 1
    while dp < MONEY_DP_MAX and (min(nz) / m['div']) < pow10(1 - dp):
        dp += 1
    return {'div': m['div'], 'suf': m['suf'], 'dp': dp}


def countRow(row, values):
    if not jt(row):
        return countShape(values)
    if G(row, '__count') is UNDEFINED:
        setprop(row, '__count', countShape(values))
    return G(row, '__count')


def fmtCell(row, v):
    if nn(v) and jt(G(row, 'street_only')):
        return 'n/a'
    if nn(v):
        return MDASH
    fmt = G(row, 'fmt')
    if seq(fmt, '#'):
        return fmtCount(v, G(row, '__count'))
    if seq(fmt, '%'):
        return (MINUS + xlFixed(abs(jnum(v)), 1) if jnum(v) < 0 else xlFixed(v, 1)) + '%'
    if seq(fmt, '$'):
        return fmtEps(v, MINOR_has(G(row, 'ltcode')))
    return fmtM(v, G(row, '__money'))


def clsColor(n, pol=UNDEFINED, dp=UNDEFINED):
    if nn(n):
        return None
    if dp is not UNDEFINED and jnum(xlFixed(n, dp)) == 0:
        return 'Y'
    if seq(n, 0):
        return 'Y'
    if seq(jor(pol, 'higher'), 'neutral'):
        return None
    good = (jnum(n) < 0) if seq(pol, 'lower') else (jnum(n) > 0)
    return 'G' if good else 'R'


COLORS = {'G': '1E7A34', 'R': 'B02A1F', 'Y': '6E6E6E'}
_MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']


def monthName(d):
    mi = js_parse_int(jslice(d, 5, 7))
    mon = _MONTHS[mi - 1] if (isinstance(mi, int) and 1 <= mi <= 12) else 'undefined'
    return mon + ' ' + jstr(js_parse_int(jslice(d, 8, 10))) + ', ' + jslice(d, 0, 4)


def mmdd(d):
    return (jslice(d, 5, 7) + '/' + jslice(d, 8, 10)) if jt(d) else ''


def mmddyy(d):
    return (jslice(d, 5, 7) + '/' + jslice(d, 8, 10) + '/' + jslice(d, 2, 4)) if jt(d) else ''


def xesc(s):
    return jstr(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def run(text, o=None):
    o = o or {}
    font = jstr(jor(o.get('font'), 'Calibri'))
    pr = '<w:rFonts w:ascii="' + font + '" w:hAnsi="' + font + '"/>'
    if jt(o.get('b')):
        pr += '<w:b/>'
    if jt(o.get('i')):
        pr += '<w:i/>'
    pr += '<w:color w:val="' + jstr(jor(o.get('color'), '1A1A1A')) + '"/>'
    if jt(o.get('spacing')):
        pr += '<w:spacing w:val="' + jstr(o.get('spacing')) + '"/>'
    sz = jstr(jor(o.get('sz'), 21))
    pr += '<w:sz w:val="' + sz + '"/><w:szCs w:val="' + sz + '"/>'
    return '<w:r><w:rPr>' + pr + '</w:rPr><w:t xml:space="preserve">' + xesc(text) + '</w:t></w:r>'


_RE_INLINE_SPLIT = re.compile(r'(</?b>|</?i>)')
_RE_TAG = re.compile(r'<[^>]+>')


def inlineRuns(html, base=None):
    base = base or {}
    out = []
    b = i = 0
    for tok in _RE_INLINE_SPLIT.split(jstr(html)):
        if tok == '<b>':
            b += 1
        elif tok == '</b>':
            b = max(0, b - 1)
        elif tok == '<i>':
            i += 1
        elif tok == '</i>':
            i = max(0, i - 1)
        elif tok:
            text = tok.replace('&nbsp;', ' ').replace('&gt;', '>').replace('&lt;', '<').replace('&amp;', '&')
            text = _RE_TAG.sub('', text)
            if text:
                o = dict(base)
                o['b'] = True if b > 0 else base.get('b', UNDEFINED)
                o['i'] = True if i > 0 else base.get('i', UNDEFINED)
                out.append(run(text, o))
    return ''.join(out)


def para(children, o=None):
    o = o or {}
    pr = ''
    if o.get('keepNext'):
        pr += '<w:keepNext/>'
    if o.get('leftBorder') or o.get('bottomBorder') or o.get('topBorder'):
        pr += '<w:pBdr>'
        tb = o.get('topBorder')
        if tb:
            sp = tb.get('space', UNDEFINED)
            pr += ('<w:top w:val="single" w:sz="' + jstr(tb.get('sz') or 6) + '" w:space="' +
                   jstr(4 if sp is UNDEFINED else sp) + '" w:color="' + jstr(tb['color']) + '"/>')
        lb = o.get('leftBorder')
        if lb:
            pr += ('<w:left w:val="single" w:sz="' + jstr(lb.get('sz') or 24) +
                   '" w:space="4" w:color="' + jstr(lb['color']) + '"/>')
        bb = o.get('bottomBorder')
        if bb:
            pr += ('<w:bottom w:val="single" w:sz="' + jstr(bb.get('sz') or 12) +
                   '" w:space="1" w:color="' + jstr(bb['color']) + '"/>')
        pr += '</w:pBdr>'
    if o.get('shadeFill'):
        pr += '<w:shd w:val="clear" w:color="auto" w:fill="' + o['shadeFill'] + '"/>'
    if o.get('tabRight'):
        pr += '<w:tabs><w:tab w:val="right" w:pos="' + jstr(o['tabRight']) + '"/></w:tabs>'
    if o.get('line'):
        pr += ('<w:spacing w:before="' + jstr(o.get('spaceBefore') or 0) + '" w:after="' +
               jstr(o.get('spaceAfter') or 0) + '" w:line="' + jstr(o['line']) + '" w:lineRule="exact"/>')
    else:
        pr += ('<w:spacing w:before="' + jstr(o.get('spaceBefore') or 0) + '" w:after="' +
               jstr(o.get('spaceAfter') or 0) + '" w:line="240" w:lineRule="auto"/>')
    if o.get('indentLeft') or o.get('hanging'):
        pr += ('<w:ind w:left="' + jstr(o.get('indentLeft') or 0) + '"' +
               ((' w:hanging="' + jstr(o['hanging']) + '"') if o.get('hanging') else '') + '/>')
    if o.get('align'):
        pr += '<w:jc w:val="' + o['align'] + '"/>'
    return '<w:p><w:pPr>' + pr + '</w:pPr>' + children + '</w:p>'


def cell(paras, o=None):
    o = o or {}
    pr = '<w:tcW w:w="' + jstr(jor(o.get('w'), 0)) + '" w:type="dxa"/>'
    if jt(o.get('gridSpan')):
        pr += '<w:gridSpan w:val="' + jstr(o['gridSpan']) + '"/>'
    if jt(o.get('fill')):
        pr += '<w:shd w:val="clear" w:color="auto" w:fill="' + jstr(o['fill']) + '"/>'
    pr += ('<w:tcMar><w:top w:w="30" w:type="dxa"/><w:left w:w="80" w:type="dxa"/>'
           '<w:bottom w:w="30" w:type="dxa"/><w:right w:w="80" w:type="dxa"/></w:tcMar>')
    pr += '<w:vAlign w:val="' + jstr(jor(o.get('vAlign'), 'center')) + '"/>'
    return '<w:tc><w:tcPr>' + pr + '</w:tcPr>' + (''.join(paras) if isinstance(paras, list) else paras) + '</w:tc>'


def tRow(cells, o=None):
    return ('<w:tr><w:trPr><w:cantSplit/>' + ('<w:tblHeader/>' if (o and o.get('header')) else '') +
            '</w:trPr>' + ''.join(cells) + '</w:tr>')


def table(rows, grid, o=None):
    o = o or {}
    if o.get('outerBox'):
        borders = (''.join('<w:' + k + ' w:val="single" w:sz="6" w:space="0" w:color="' + o['outerBox'] + '"/>'
                           for k in ['top', 'left', 'bottom', 'right']) +
                   '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="FFFFFF"/>' +
                   '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="FFFFFF"/>')
    elif o.get('leftAccent'):
        borders = ('<w:top w:val="none" w:sz="0" w:space="0" w:color="auto"/>' +
                   '<w:left w:val="single" w:sz="24" w:space="0" w:color="' + o['leftAccent'] + '"/>' +
                   '<w:bottom w:val="none" w:sz="0" w:space="0" w:color="auto"/>' +
                   '<w:right w:val="none" w:sz="0" w:space="0" w:color="auto"/>' +
                   '<w:insideH w:val="none" w:sz="0" w:space="0" w:color="auto"/>' +
                   '<w:insideV w:val="none" w:sz="0" w:space="0" w:color="auto"/>')
    else:
        borders = ('<w:top w:val="single" w:sz="5" w:space="0" w:color="' + RULE_H + '"/>' +
                   '<w:left w:val="none" w:sz="0" w:space="0" w:color="auto"/>' +
                   '<w:bottom w:val="single" w:sz="5" w:space="0" w:color="' + RULE_H + '"/>' +
                   '<w:right w:val="none" w:sz="0" w:space="0" w:color="auto"/>' +
                   '<w:insideH w:val="single" w:sz="2" w:space="0" w:color="' + RULE_R + '"/>' +
                   '<w:insideV w:val="none" w:sz="0" w:space="0" w:color="auto"/>')
    tot = 0
    for w in grid:
        tot = tot + w
    pr = '<w:tblW w:w="' + jstr(tot) + '" w:type="dxa"/>' + '<w:tblBorders>' + borders + '</w:tblBorders>'
    if o.get('fill'):
        pr += '<w:shd w:val="clear" w:color="auto" w:fill="' + o['fill'] + '"/>'
    pr += '<w:tblLayout w:type="fixed"/>'
    body_ = rows
    if not o.get('noZebra') and not o.get('outerBox') and not o.get('leftAccent'):
        bi = -1
        body_ = []
        for r in rows:
            if '<w:tblHeader/>' in r:
                body_.append(r)
                continue
            cells = r.split('<w:tc>')
            unfilled = len([c for c in cells if c and '<w:shd' not in c])
            if unfilled == 0:
                body_.append(r)
                continue
            bi += 1
            if bi % 2 == 0:
                body_.append(r)
                continue
            body_.append('<w:tc>'.join(
                c if (i == 0 or '<w:shd' in c)
                else c.replace('<w:tcMar', '<w:shd w:val="clear" w:color="auto" w:fill="' + ZEBRA + '"/><w:tcMar', 1)
                for i, c in enumerate(cells)))
    return ('<w:tbl><w:tblPr>' + pr + '</w:tblPr><w:tblGrid>' +
            ''.join('<w:gridCol w:w="' + jstr(w) + '"/>' for w in grid) + '</w:tblGrid>' + ''.join(body_) + '</w:tbl>')


DRAWING_ID = 100


def imgXml(relId, cx, cy, name):
    global DRAWING_ID
    id_ = jstr(DRAWING_ID)
    DRAWING_ID += 1
    cx, cy = jstr(cx), jstr(cy)
    return ('<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">' +
            '<wp:extent cx="' + cx + '" cy="' + cy + '"/>' +
            '<wp:docPr id="' + id_ + '" name="' + name + '"/>' +
            '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">' +
            '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">' +
            '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">' +
            '<pic:nvPicPr><pic:cNvPr id="' + id_ + '" name="' + name + '"/><pic:cNvPicPr/></pic:nvPicPr>' +
            '<pic:blipFill><a:blip r:embed="' + relId + '"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>' +
            '<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="' + cx + '" cy="' + cy + '"/></a:xfrm>' +
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>' +
            '</a:graphicData></a:graphic></wp:inline></w:drawing></w:r>')


def chartDrawing(relId, cx, cy, name):
    global DRAWING_ID
    id_ = jstr(DRAWING_ID)
    DRAWING_ID += 1
    cx, cy = jstr(cx), jstr(cy)
    return ('<w:p><w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">' +
            '<wp:extent cx="' + cx + '" cy="' + cy + '"/>' +
            '<wp:docPr id="' + id_ + '" name="' + name + '"/>' +
            '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">' +
            '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart">' +
            '<c:chart xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" ' +
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:id="' + relId + '"/>' +
            '</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>')


AXTX = ('<c:txPr><a:bodyPr/><a:lstStyle/><a:p><a:pPr><a:defRPr sz="800">' +
        '<a:solidFill><a:srgbClr val="' + '6E6E6E' + '"/></a:solidFill></a:defRPr></a:pPr>' +
        '<a:endParaRPr lang="en-US"/></a:p></c:txPr>')
AXGRID = ('<c:majorGridlines><c:spPr><a:ln w="3175"><a:solidFill>' +
          '<a:srgbClr val="' + 'DDDDDD' + '"/></a:solidFill></a:ln></c:spPr></c:majorGridlines>')


def buildChartXml(BH):
    prints = jarr(G(BH, 'prints'))
    cats = [G(p, 'q') for p in prints]

    def strCache(arr):
        return ('<c:ptCount val="' + jstr(len(arr)) + '"/>' +
                ''.join('<c:pt idx="' + jstr(i) + '"><c:v>' + xesc(v) + '</c:v></c:pt>' for i, v in enumerate(arr)))

    def numCache(arr):
        return ('<c:formatCode>General</c:formatCode><c:ptCount val="' + jstr(len(arr)) + '"/>' +
                ''.join('' if (nn(v) or not jisfinite(v)) else
                        '<c:pt idx="' + jstr(i) + '"><c:v>' + jstr(v) + '</c:v></c:pt>' for i, v in enumerate(arr)))

    catRef = ('<c:cat><c:strRef><c:f>Sheet1!$A$2:$A$' + jstr(len(cats) + 1) + '</c:f><c:strCache>' +
              strCache(cats) + '</c:strCache></c:strRef></c:cat>')

    def dPts(out, vals):
        n = max(len(out) if isinstance(out, list) else 0, len(vals) if isinstance(vals, list) else 0)
        x = ''
        for i in range(n):
            excluded = gi(out, i) if jt(out) else out
            v = gi(vals, i) if jt(vals) else vals
            negative = is_num(v) and math.isfinite(v) and xlRound(v, 1) < 0
            if not jt(excluded) and not negative:
                continue
            x += ('<c:dPt><c:idx val="' + jstr(i) + '"/><c:invertIfNegative val="0"/><c:bubble3D val="0"/>' +
                  '<c:spPr><a:solidFill><a:srgbClr val="' + ('BFBFBF' if jt(excluded) else 'B02A1F') +
                  '"/></a:solidFill></c:spPr></c:dPt>')
        return x

    def barSer(idx, col, name, vals, out):
        L = chr(66 + idx)
        return ('<c:ser><c:idx val="' + jstr(idx) + '"/><c:order val="' + jstr(idx) + '"/>' +
                '<c:tx><c:strRef><c:f>Sheet1!$' + L + '$1</c:f><c:strCache><c:ptCount val="1"/><c:pt idx="0"><c:v>' +
                xesc(name) + '</c:v></c:pt></c:strCache></c:strRef></c:tx>' +
                '<c:spPr><a:solidFill><a:srgbClr val="' + col + '"/></a:solidFill></c:spPr>' +
                dPts(out, vals) +
                catRef +
                '<c:val><c:numRef><c:f>Sheet1!$' + L + '$2:$' + L + '$' + jstr(len(cats) + 1) +
                '</c:f><c:numCache>' + numCache(vals) + '</c:numCache></c:numRef></c:val></c:ser>')

    def lineSer(idx, col, name, vals):
        L = chr(66 + idx)
        return ('<c:ser><c:idx val="' + jstr(idx) + '"/><c:order val="' + jstr(idx) + '"/>' +
                '<c:tx><c:strRef><c:f>Sheet1!$' + L + '$1</c:f><c:strCache><c:ptCount val="1"/><c:pt idx="0"><c:v>' +
                xesc(name) + '</c:v></c:pt></c:strCache></c:strRef></c:tx>' +
                '<c:spPr><a:ln w="28575"><a:solidFill><a:srgbClr val="' + col + '"/></a:solidFill></a:ln></c:spPr>' +
                '<c:marker><c:symbol val="circle"/><c:size val="6"/><c:spPr><a:solidFill><a:srgbClr val="' + col +
                '"/></a:solidFill></c:spPr></c:marker>' +
                catRef +
                '<c:val><c:numRef><c:f>Sheet1!$' + L + '$2:$' + L + '$' + jstr(len(cats) + 1) +
                '</c:f><c:numCache>' + numCache(vals) + '</c:numCache></c:numRef></c:val><c:smooth val="0"/></c:ser>')

    kpi1 = G(BH, 'kpi1_name')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' +
            '<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" ' +
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" ' +
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">' +
            '<c:chart><c:autoTitleDeleted val="1"/><c:plotArea><c:layout/>' +
            '<c:barChart><c:barDir val="col"/><c:grouping val="clustered"/><c:varyColors val="0"/>' +
            barSer(0, '1E7A34', 'Revenue beat vs cons (%)', [G(p, 'rev_beat_pct') for p in prints],
                   [G(p, 'rev_excluded') for p in prints]) +
            (barSer(1, 'E8850C', jstr(kpi1) + ' beat vs cons (%)',
                    [G(p, 'kpi1_beat_pct') for p in prints], [G(p, 'kpi1_excluded') for p in prints]) if jt(kpi1) else '') +
            '<c:gapWidth val="90"/><c:overlap val="-20"/>' +
            '<c:axId val="111111111"/><c:axId val="222222222"/></c:barChart>' +
            '<c:lineChart><c:grouping val="standard"/><c:varyColors val="0"/>' +
            lineSer(2, 'B02A1F', 'Next-day stock move (%)', [G(p, 'reaction_pct') for p in prints]) +
            '<c:marker val="1"/><c:axId val="111111111"/><c:axId val="222222222"/></c:lineChart>' +
            '<c:catAx><c:axId val="111111111"/><c:scaling><c:orientation val="minMax"/></c:scaling>' +
            '<c:delete val="0"/><c:axPos val="b"/>' + AXTX +
            '<c:crossAx val="222222222"/></c:catAx>' +
            '<c:valAx><c:axId val="222222222"/><c:scaling><c:orientation val="minMax"/></c:scaling>' +
            '<c:delete val="0"/><c:axPos val="l"/>' + AXGRID +
            '<c:numFmt formatCode="0.0\\%" sourceLinked="0"/>' + AXTX +
            '<c:crossAx val="111111111"/></c:valAx>' +
            '</c:plotArea><c:legend><c:legendPos val="b"/><c:overlay val="0"/>' + AXTX + '</c:legend>' +
            '<c:plotVisOnly val="1"/><c:dispBlanksAs val="gap"/></c:chart></c:chartSpace>')


BAND, HEAD, RULE, GRID, THBG, ROWHEAD, NOTEBG = '121110', '1A1A1A', 'E8850C', 'DDDDDD', 'EDEDED', 'EDEDED', 'F6F3EE'
ZEBRA, RULE_H, RULE_R, BOXC = 'F5F0E8', 'BBBBBB', 'EEEEEE', 'C9C9C9'
NOTE_BORDER = {'green': '1E7A34', 'red': 'B02A1F', 'yellow': 'B25E08', 'blue': 'E8850C'}
PAGE = 10800

GLYPH_PERMILLE = {}


def _build_glyphs():
    t = GLYPH_PERMILLE

    def put(chars, w):
        for c in chars:
            t[c] = w
    put(' \u00a0', 254)
    put('\'‘’', 269)
    put(',.', 328)
    put('·', 338)
    put('l', 344)
    put('j', 346)
    put('iìíîï', 354)
    put(':;', 367)
    put('!¡', 376)
    put('-­', 379)
    put('|¦', 388)
    put('f', 393)
    put('t', 397)
    put('°', 420)
    put('IÌÍÎÏ', 446)
    put('()[]', 447)
    put('/\\', 472)
    put('*', 482)
    put('1', 490)
    put('`{}¨´¸', 500)
    put('"', 510)
    put('s', 513)
    put('“”', 519)
    put('r', 520)
    put('z', 525)
    put('cç', 531)
    put('?¿', 548)
    put('¶', 549)
    put('ª²³¹º', 552)
    put('7', 554)
    put('yýÿ', 562)
    put('§', 563)
    put('v', 567)
    put('eèéêë', 572)
    put('g', 577)
    put('x', 588)
    put('J', 595)
    put('aàáâãäå', 596)
    put('5', 599)
    put('¢', 605)
    put('«»', 610)
    put('3', 625)
    put('2', 626)
    put('k', 632)
    put('oòóôõöø', 636)
    put('ð', 638)
    put('$', 641)
    put('þ', 645)
    put('b', 646)
    put('69q', 648)
    put('4S', 649)
    put('pß', 658)
    put('d', 663)
    put('µ', 668)
    put('F', 671)
    put('8', 676)
    put('uùúûü', 677)
    put('h', 680)
    put('T', 684)
    put('L', 686)
    put('Z', 689)
    put('n£ñ', 690)
    put('0P', 701)
    put('#+<=>^_~¤¬¯±×÷–−', 703)
    put('Þ', 708)
    put('CÇ€', 715)
    put('EÈÉÊË', 721)
    put('Y¥Ý', 732)
    put('B', 757)
    put('AÀÁÂÃÄÅ', 758)
    put('V', 762)
    put('R', 797)
    put('&', 799)
    put('G', 807)
    put('X', 809)
    put('K', 817)
    put('OQÒÓÔÕÖØ', 820)
    put('UÙÚÛÜ', 833)
    put('DÐ', 834)
    put('NÑ', 839)
    put('æ', 857)
    put('w', 863)
    put('%', 879)
    put('H', 913)
    put('—', 928)
    put('©®', 942)
    put('@', 967)
    put('m', 1016)
    put('M', 1023)
    put('Æ', 1070)
    put('¼½¾', 1071)
    put('W', 1126)


_build_glyphs()
GLYPH_DEFAULT = 669

SUBSTITUTE_WIDTH_FACTOR = 1.12


def _exec_sync(command):
    try:
        sys.stdout.flush()
        sys.stderr.flush()
        if os.name == 'nt':
            comspec = os.environ.get('ComSpec') or 'cmd.exe'
            p = subprocess.run(comspec + ' /d /s /c "' + command + '"', stdout=subprocess.PIPE,
                               stdin=subprocess.PIPE)
        else:
            p = subprocess.run(['/bin/sh', '-c', command], stdout=subprocess.PIPE, stdin=subprocess.PIPE)
    except OSError:
        return None
    if p.returncode != 0:
        return None
    return p.stdout.decode('utf-8', errors='replace')


def _face_scale():
    forced = os.environ.get('INSYNC_FACE_WIDTHS')
    if forced == 'georgia':
        return 1
    if forced == 'substitute':
        return SUBSTITUTE_WIDTH_FACTOR
    want, accepted = 'Georgia', ['Georgia']
    try:
        for up in range(1, 5):
            cand = os.path.normpath(os.path.join(HERE, *(['..'] * up), 'common', 'brand.json'))
            if not os.path.exists(cand):
                continue
            m = jor(G(_json_load(cand), 'masthead'), {})
            product = os.path.basename(os.path.dirname(os.path.dirname(cand)))
            want = jor(jor(G(jor(G(m, 'face_overrides'), {}), product), G(m, 'face')), 'Georgia')
            subs = jor(G(jor(G(m, 'face_accepted_substitutes'), {}), want), [])
            accepted = [want] + (subs if isinstance(subs, list) else [subs])
            break
    except Exception:
        pass
    out = _exec_sync('fc-match -f "%{family}" "' + jstr(want) + ':bold" 2>/dev/null')
    if out is None:
        return 1
    resolved = jtrim(out)
    if not resolved:
        return 1
    flat = _RE_WS_RUN.sub('', resolved).lower()
    if any(_RE_WS_RUN.sub('', jstr(a)).lower() in flat for a in accepted):
        return 1
    err('[masthead] "' + jstr(want) + '" is NOT installed on this box - fontconfig resolves it to '
        + '"' + resolved + '", which is not a metric-faithful stand-in. Sizing the company line for '
        + 'the substitute so it cannot wrap. Install ' + jstr(want) + ' to render at the intended size.')
    return SUBSTITUTE_WIDTH_FACTOR


FACE_SCALE = 1


def textWidthPt(s, pt):
    mille = 0
    for c in jstr(s):
        mille += GLYPH_PERMILLE[c] if c in GLYPH_PERMILLE else GLYPH_DEFAULT
    return mille * pt * FACE_SCALE / 1000


def _mk(name, dflt):
    v = os.environ.get(name)
    return js_number(v) if v is not None else dflt


def _json_load(p):
    with open(p, 'rb') as f:
        text = f.read().decode('utf-8', errors='replace')

    def bad_const(c):
        raise ValueError('Unexpected token ' + c + ' in JSON')
    return json.loads(text, parse_int=float, parse_constant=bad_const)


def reEsc(x):
    return re.sub(r'[.*+?^${}()|\[\]\\]', lambda m: '\\' + m.group(0), jstr(x))


def symClass():
    glyphs = ['$', '₩', '£', '€', '¥']
    cs = jtrim(jstr(jor(CS, '')))
    alts = [a for a in [cs] if a] + glyphs
    alts = sorted(alts, key=lambda a: -utf16len(a))
    return '(?:' + '|'.join(reEsc(a) for a in alts) + ')?'


def isZeroish(t):
    return re.search('^[+−(]?' + _S + '*' + symClass() + _S + '*0+(\\.0+)?' + _S + '*%?\\)?\\Z',
                     jtrim(jstr(t))) is not None


def signColorPol(s, pol):
    t = jtrim(jstr(s))
    if isZeroish(t):
        return COLORS['Y']
    p = jor(pol, 'higher')
    if seq(p, 'neutral'):
        return UNDEFINED
    sym = symClass() + _S + '*'
    up = re.search('^\\+' + _S + '*' + sym + '[0-9(]', t) is not None
    down = (re.search('^−' + _S + '*' + sym + '[0-9(]', t) is not None or
            re.search('^\\(' + _S + '*' + sym + '[0-9,][0-9,.]*', t) is not None)
    if not up and not down:
        return UNDEFINED
    good = down if seq(p, 'lower') else up
    return COLORS['G'] if good else COLORS['R']


def td(text, w, o=UNDEFINED):
    if jt(o):
        color = o.get('color', UNDEFINED)
        if not jt(color):
            pol = o.get('pol', UNDEFINED)
            c2 = signColorPol(text, pol) if pol is not UNDEFINED else None
            color = c2 if jt(c2) else '1A1A1A'
        ro = {'sz': 19, 'b': o.get('b', UNDEFINED), 'i': o.get('i', UNDEFINED), 'color': color}
        align = 'left' if jt(o.get('left', UNDEFINED)) else 'right'
    else:
        ro = {'sz': 19, 'b': o, 'i': o, 'color': '1A1A1A'}
        align = 'right'
    return cell(para(run(text, ro), {'align': align}), {'w': w})


def tdRowhead(text, span, wsum):
    return cell(para(run(text, {'b': True, 'sz': 19}), {'align': 'left'}), {'w': wsum, 'gridSpan': span, 'fill': ROWHEAD})


def th(text, w, o=None):
    return cell(para(run(text, {'b': True, 'sz': 18}), {'align': 'left' if (o and o.get('left')) else 'right'}),
                {'w': w, 'fill': THBG})


def thML(l1, l2, w):
    return cell([para(run(l1, {'b': True, 'sz': 18}), {'align': 'right'}),
                 para(run(l2, {'b': True, 'sz': 16, 'color': '555555'}), {'align': 'right'})], {'w': w, 'fill': THBG})


body = []
C = N = V = Q = FY = T = None
MINOR_LIST = []
SEQ = 'Q/Q Growth'
BRAND = {}
hasLogo = False
MK = {}


def MINOR_has(x):
    return any(seq(x, m) for m in MINOR_LIST)


def fyLbl(y):
    return '—' if (nn(y) or seq(y, '')) else jstr(y)[2:]


def bandLabel(grp):
    return ('Key Financial Metrics' if seq(grp, 'fin') else 'Top Operating KPIs') + ' (units as stated per row)'


def heading(text):
    body.append(para(run(text, {'b': True, 'color': HEAD, 'sz': 25}),
                     {'bottomBorder': {'color': RULE, 'sz': 12}, 'spaceBefore': 200, 'spaceAfter': 80, 'keepNext': True}))


def noteBox(flavor, html, label=UNDEFINED):
    if not jt(html):
        return
    lead = run(jstr(label) + ' ', {'b': True, 'sz': 20}) if jt(label) else ''
    fl = NOTE_BORDER.get(jstr(flavor))
    body.append(para(lead + inlineRuns(html, {'sz': 20}),
                     {'shadeFill': NOTEBG, 'leftBorder': {'color': fl or HEAD}, 'spaceBefore': 60, 'spaceAfter': 60}))


def bullets(lst, sz=UNDEFINED):
    for b in jlist(lst):
        body.append(para(run('•  ', {}) + inlineRuns(b, {'sz': jor(sz, 21)}),
                         {'indentLeft': 260, 'hanging': 180, 'spaceAfter': 30}))


def titleHeader():
    meta = G(C, 'meta')
    prep = G(meta, 'prepared')
    asof = monthName(prep if jt(prep) else G(G(meta, 'asof'), 'market'))
    expectedDt = monthName(G(G(meta, 'upcoming'), 'call_date'))
    M = jor(G(BRAND, 'masthead') if jt(BRAND) else BRAND, {})
    LOGOW = 1150
    DATE_SZ = 18
    RIGHT_PAD = 60
    rightNeedPt = max(
        textWidthPt('Earnings Preview', 18),
        textWidthPt('Prepared: ' + asof, DATE_SZ / 2),
        textWidthPt('Expected Earnings Date: ' + expectedDt, DATE_SZ / 2))
    RIGHTW = min(4300, max(3400, math.ceil(rightNeedPt * 20) + 160 + RIGHT_PAD))
    MIDW = PAGE - LOGOW - RIGHTW

    BRAND_SZ = jor(G(M, 'brand_size_half_points'), 33)
    BRAND_SZ_2L = jor(G(M, 'brand_size_two_line_half_points'), 24)
    CO_SIZE = 28
    TWO_SIZE = 20
    BUDGET_PT = (MIDW - 160 - 60) / 20

    def fits(line, half):
        return textWidthPt(line, half / 2) <= BUDGET_PT * 0.97

    suffix = ' (' + jstr(T) + ') - ' + jstr(G(Q, 'target_q'))
    coName = jstr(jor(G(meta, 'company'), T))
    coSz = CO_SIZE
    for s2 in [28, 27, 26, 25, 24]:
        coSz = s2
        if fits(coName + suffix, s2):
            break
    twoLine = not fits(coName + suffix, coSz)
    coL1, coL2 = coName + suffix, None
    if twoLine:
        words = (coName + suffix).split(' ')

        def w(t):
            return textWidthPt(t, TWO_SIZE / 2)
        cap = BUDGET_PT * 0.95
        best = None
        for i in range(1, len(words)):
            a, b = ' '.join(words[:i]), ' '.join(words[i:])
            wa, wb = w(a), w(b)
            if wa > cap or wb > cap:
                continue
            score = abs(wa - wb)
            if best is None or score < best['score']:
                best = {'a': a, 'b': b, 'score': score}
        if best:
            coL1, coL2 = best['a'], best['b']
        else:
            acc = []
            while words and w(' '.join(acc + [words[0]])) <= cap:
                acc.append(words.pop(0))
            if not acc:
                acc.append(words.pop(0) if words else UNDEFINED)
            coL1 = jjoin(acc, ' ')
            coL2 = ' '.join(words) or ' '
        coSz = TWO_SIZE
    brandSz = BRAND_SZ
    if not twoLine:
        drop = (CO_SIZE - coSz) / 2
        brandTwips = 370
        CO_LINE_TWIPS = js_round(MK['CO_LINE_BASE'] + MK['CO_LINE_SLOPE'] * drop)
        epMarkUp = js_round(MK['MARK_UP_BASE'] + MK['MARK_UP_SLOPE'] * drop)
    else:
        brandSz = BRAND_SZ_2L
        brandTwips = 280
        CO_LINE_TWIPS = MK['CO_LINE_2L']
        epMarkUp = MK['MARK_UP_2L']
    mpx = G(M, 'mark_px')
    MARK_W_PX = jor(G(mpx, 'w') if jt(mpx) else mpx, 59)
    MARK_H_PX = jor(G(mpx, 'h') if jt(mpx) else mpx, 46)
    epMarkH = jnum(MARK_H_PX) * 9525
    epMarkW = jnum(MARK_W_PX) * 9525
    if hasLogo:
        logoCell = cell(para(imgXml('rIdLogo', epMarkW, epMarkH, 'InSync logo'),
                             {'spaceBefore': 0, 'spaceAfter': epMarkUp}),
                        {'w': LOGOW, 'fill': 'FFFFFF', 'vAlign': 'bottom'})
    else:
        logoCell = cell(para(run(' ', {})), {'w': LOGOW, 'fill': 'FFFFFF', 'vAlign': 'bottom'})
    tkTok = '(' + jstr(T) + ')'

    def coRuns(text, sz):
        def mk(t, c):
            return run(t, {'font': 'Georgia', 'b': True, 'sz': sz, 'color': c})
        i = text.find(tkTok)
        if i < 0:
            return mk(text, '1A1A1A')
        out = ''
        if i > 0:
            out += mk(text[:i], '1A1A1A')
        out += mk(tkTok, 'E8850C')
        if i + len(tkTok) < len(text):
            out += mk(text[i + len(tkTok):], '1A1A1A')
        return out

    def coPara(text):
        return para(coRuns(text, coSz), {'spaceAfter': 0, 'line': CO_LINE_TWIPS})
    midParas = [
        para(run('InSync', {'font': 'Georgia', 'b': True, 'sz': brandSz, 'color': 'E8850C'}) +
             run(' Analytics', {'font': 'Georgia', 'sz': brandSz, 'color': '1A1A1A'}),
             {'spaceBefore': 40, 'spaceAfter': 0, 'line': brandTwips})
    ]
    if twoLine:
        midParas.append(coPara(coL1))
        midParas.append(coPara(coL2))
    else:
        midParas.append(coPara(coL1))
    mid = cell(midParas, {'w': MIDW, 'fill': 'FFFFFF', 'vAlign': 'bottom'})
    dtSz = DATE_SZ
    right = cell([
        para(run('Earnings ', {'font': 'Georgia', 'b': True, 'sz': 36, 'color': '1A1A1A'}) +
             run('Preview', {'font': 'Georgia', 'b': True, 'sz': 36, 'color': '1A1A1A'}), {'align': 'right', 'spaceBefore': 40}),
        para(run('Prepared: ', {'font': 'Georgia', 'b': True, 'sz': dtSz, 'color': 'E8850C'}) +
             run(asof, {'font': 'Georgia', 'sz': dtSz, 'color': '555555'}), {'align': 'right'}),
        para(run('Expected Earnings Date: ', {'font': 'Georgia', 'b': True, 'sz': dtSz, 'color': 'E8850C'}) +
             run(expectedDt, {'font': 'Georgia', 'sz': dtSz, 'color': '555555'}), {'align': 'right', 'spaceAfter': 40})
    ], {'w': RIGHTW, 'fill': 'FFFFFF', 'vAlign': 'top'})
    body.append(table([tRow([logoCell, mid, right])], [LOGOW, MIDW, RIGHTW], {'outerBox': BOXC}))
    body.append(para('', {'bottomBorder': {'color': RULE, 'sz': 18}, 'spaceBefore': 40, 'spaceAfter': 100}))


def theSetup():
    ss = G(N, 'setup_summary')
    if isinstance(ss, list) and len(ss):
        bl = ss
    else:
        thesis = G(N, 'thesis')
        bl = [thesis] if jt(thesis) else []
    if not len(bl):
        return
    inner = [para(run('THE SETUP', {'b': True, 'color': HEAD, 'sz': 22}), {'spaceAfter': 30})]
    for i, b in enumerate(bl):
        inner.append(para(run('•  ', {'color': HEAD}) + inlineRuns(b, {'sz': 21}),
                          {'indentLeft': 200, 'hanging': 160, 'spaceAfter': 0 if i == len(bl) - 1 else 40}))
    body.append(table([tRow([cell(inner, {'w': PAGE, 'fill': NOTEBG})])], [PAGE], {'leftAccent': RULE, 'fill': NOTEBG}))
    body.append(para('', {'spaceAfter': 40}))


def valuation():
    heading('Valuation')
    g = [3300, 2100, 3300, 2100]

    def kv(l, v):
        return [l, v]
    price = G(V, 'price')
    rowsL = [
        kv('Price', MDASH if nn(price) else fmtEps(price)), kv('Market Cap', fmtMag(G(V, 'market_cap'))),
        kv(jor(G(V, 'debt_label'), 'Total Debt (incl-leases)'), fmtMag(G(V, 'total_debt'))), kv('Cash', fmtMag(G(V, 'cash'))),
        kv('Net Debt', fmtMag(G(V, 'net_debt'))), kv('Enterprise Value', fmtMag(G(V, 'ev')))
    ]

    def money2(x):
        return MDASH if nn(x) else fmtEps(x)

    def withPct(x, p, pos):
        if nn(x):
            return money2(x)
        return money2(x) + ('' if nn(p) else ' (' + fmtPct(p, 1, pos) + ')')

    def lensRow(L):
        repBasis = seq(G(L, 'basis'), 'GAAP') and re.search(
            r'^(P/E|EV/EBITDA)', jstr(jor(G(L, 'resolved_metric'), ''))) is not None
        lab = G(L, 'label')
        lbl = (jstr(lab) + ' (rep.)') if (repBasis and not re.search(r'rep\.|reported', jstr(lab), re.I | re.ASCII)) else lab
        if jt(G(L, 'nm')):
            return kv(lbl, 'NM')
        val = G(L, 'value')
        if nn(val):
            return kv(lbl, MDASH)
        return kv(lbl, fmtPct(val, 1) if seq(G(L, 'kind'), 'pct') else fmtX(val))
    bvps = G(V, 'bvps')
    rowsR = [
        kv('52-wk High', withPct(G(V, 'wk52_high'), G(V, 'pct_off_high'), False)),
        kv('52-wk Low', withPct(G(V, 'wk52_low'), G(V, 'pct_above_low'), True)),
        kv('Book Value/Share', MDASH if bvps is None else fmtEps(bvps))
    ]
    lenses = G(V, 'lenses')
    if isinstance(lenses, list) and len(lenses):
        for L in lenses:
            rowsR.append(lensRow(L))
    else:
        dy = G(V, 'dividend_yield')
        rowsR.append(kv('Dividend Yield', MDASH if dy is None else fmtPct(jnum(dy) * 100, 1)))
        rowsR.append(kv('FCF Yield (NTM)', 'NM' if jt(G(V, 'fcf_yield_nm')) else fmtPct(G(V, 'fcf_yield_ntm'), 1)))
        if jt(G(V, 'ntm_row_mixed_basis')):
            mixLbl = ('NTM P/E (' + ('rep.' if seq(G(V, 'ntm_pe_basis'), 'GAAP') else 'Adj') + ') | EV/EBITDA (' +
                      ('rep.' if seq(G(V, 'ntm_ev_ebitda_basis'), 'GAAP') else 'Adj') + ')')
        else:
            mixLbl = 'NTM P/E | EV/EBITDA'
        pe, eve = G(V, 'ntm_pe'), G(V, 'ntm_ev_ebitda')
        rowsR.append(kv(mixLbl, (MDASH if pe is None else fmtX(pe)) + ' | ' + (MDASH if eve is None else fmtX(eve))))
    nde = G(V, 'net_debt_ebitda')
    if not nn(nde):
        rowsL.insert(len(rowsL) - 1, kv(jor(G(V, 'net_debt_ebitda_label'), 'Net Debt / EBITDA'), fmtX(nde)))
    while len(rowsL) < len(rowsR):
        rowsL.append(kv('', ''))
    while len(rowsR) < len(rowsL):
        rowsR.append(kv('', ''))
    rows = [tRow([
        td(l[0], g[0], {'left': True, 'b': True}), td(l[1], g[1], {'pol': 'neutral'}),
        td(rowsR[i][0], g[2], {'left': True, 'b': True}), td(rowsR[i][1], g[3], {'pol': 'neutral'})
    ]) for i, l in enumerate(rowsL)]
    body.append(table(rows, g))
    fx = G(V, 'fx')
    if jt(fx) and jt(G(fx, 'rate')):
        asOf = (', ' + jstr(G(fx, 'as_of'))) if jt(G(fx, 'as_of')) else ''
        body.append(para(inlineRuns('<i>Price, 52-week range and the daily close series are CONVERTED ' +
                                    jstr(G(fx, 'from')) + ' → ' + jstr(G(fx, 'to')) + ' at ' + jstr(G(fx, 'rate')) + asOf +
                                    ((' (' + jstr(G(fx, 'source')) + ')') if jt(G(fx, 'source')) else '') +
                                    ' — this issuer reports in ' + jstr(G(fx, 'to')) + ' and its listing prices in ' +
                                    jstr(G(fx, 'from')) +
                                    '. Percentage moves are ratios and are unaffected. Market cap and EV are as the vendor ' +
                                    'returned them and are NOT converted.</i>', {'sz': 15, 'color': '6E6E6E'}), {'align': 'left'}))


def summary():
    heading('Summary Financials')
    sf = G(C, 'summary_financials')
    showFyG = G(sf, 'show_fy_guide_column')
    showFy1G = seq(G(sf, 'show_fy1_guide_column'), True)
    sgc = G(sf, 'show_guide_column')
    showG = (not seq(sgc, False)) and (seq(sgc, True) or jnum(G(jor(G(sf, 'guidance_target_q'), []), 'length')) > 0)
    nGuide = (1 if showG else 0) + (1 if jt(showFyG) else 0) + (1 if showFy1G else 0)
    GUIDE_W = 1160 if nGuide >= 2 else 1310
    base = PAGE - nGuide * GUIDE_W
    METRIC_W = js_round(base * (0.235 if nGuide >= 2 else 0.25))
    DATA_W = math.floor((base - METRIC_W) / 7)
    g = [METRIC_W, DATA_W, DATA_W, DATA_W]
    if showG:
        g.append(GUIDE_W)
    g += [DATA_W, DATA_W, DATA_W]
    if jt(showFyG):
        g.append(GUIDE_W)
    g.append(DATA_W)
    if showFy1G:
        g.append(GUIDE_W)
    wsum = sum(g)
    rows = []

    def cellsFor(metricCell, vals, pol):
        c = [metricCell]
        cur = [1]

        def nxt():
            v = g[cur[0]]
            cur[0] += 1
            return v
        P = {'pol': pol}
        c.append(td(vals['py'], nxt(), P))
        c.append(td(vals['prior'], nxt(), P))
        c.append(td(vals['target'], nxt(), dict(P, **(vals.get('targetOpts') or {}))))
        if showG:
            c.append(td(vals['guide'], nxt(), P))
        c.append(td(vals['next'], nxt(), P))
        c.append(td(vals['fyprev'], nxt(), P))
        c.append(td(vals['fy0'], nxt(), P))
        if jt(showFyG):
            c.append(td(vals['fyg'], nxt(), P))
        c.append(td(vals['fy1'], nxt(), P))
        if showFy1G:
            c.append(td(MDASH if vals.get('fyg1', UNDEFINED) is UNDEFINED else vals['fyg1'], nxt(), P))
        return c
    hdr = [th('Metric', g[0], {'left': True}),
           thML(G(Q, 'py_q'), '(Actual)', g[1]), thML(G(Q, 'prior_q'), '(Actual)', g[2]), thML(G(Q, 'target_q'), '(Estimate)', g[3])]
    hi = 4
    if showG:
        hdr.append(th('Guidance', g[hi]))
        hi += 1
    hdr.append(thML(G(Q, 'next_q'), '(Estimate)', g[hi]))
    hi += 1
    hdr.append(thML('FY' + fyLbl(G(FY, 'fy_prev')), '(Actual)', g[hi]))
    hi += 1
    hdr.append(thML('FY' + fyLbl(G(FY, 'fy0')), '(Estimate)', g[hi]))
    hi += 1
    if jt(showFyG):
        hdr.append(thML('FY' + fyLbl(G(FY, 'fy0')), 'Guide', g[hi]))
        hi += 1
    hdr.append(thML('FY' + fyLbl(G(FY, 'fy1')), '(Estimate)', g[hi]))
    hi += 1
    if showFy1G:
        hdr.append(thML('FY' + fyLbl(G(FY, 'fy1')), 'Guide', g[hi]))
        hi += 1
    rows.append(tRow(hdr, {'header': True}))
    rows.append(tRow([tdRowhead(bandLabel('fin'), len(g), wsum)]))

    def guideCellFor(ltcode, lst, fmt=UNDEFINED, row=UNDEFINED):
        gg = next((x for x in jlist(lst) if seq(G(x, 'ltcode'), ltcode)), UNDEFINED)
        if not jt(gg):
            return MDASH
        f = fmt if jt(fmt) else ('%' if re.search(r'-Y\Z', jstr(ltcode)) else '$M')

        def one(v):
            if seq(f, '%'):
                return xlFixed(v, 1) + '%'
            if seq(f, '$'):
                return fmtEps(v, MINOR_has(ltcode))
            if seq(f, '#'):
                return fmtCount(v)
            return fmtM(v, G(row, '__money') if jt(row) else row)
        lo, hi_, pt = G(gg, 'lo'), G(gg, 'hi'), G(gg, 'point')
        if seq(G(gg, 'bound'), 'min') and not nn(lo):
            return GE + one(lo)
        if seq(G(gg, 'bound'), 'max') and not nn(hi_):
            return LE + one(hi_)
        if not nn(pt):
            return '~' + one(pt)
        if lo is not None and hi_ is not None:
            return (xlFixed(lo, 1) + '–' + xlFixed(hi_, 1) + '%') if seq(f, '%') else (one(lo) + '–' + one(hi_))
        return jtrim(_RE_WS_RUN.sub(' ', jstr(G(gg, 'text'))))

    rows0 = G(sf, 'rows')

    def growthRows(gr, showGuideYY, pol):
        yyGuide = guideCellFor(jstr(G(gi(rows0, 0), 'ltcode')) + '-Y', G(sf, 'guidance_target_q')) if showGuideYY else MDASH
        NMy, NMq = jor(G(gr, 'yy_nm'), {}), jor(G(gr, 'qq_nm'), {})
        yyv, qqv = G(gr, 'yy'), G(gr, 'qq')
        yy = cellsFor(td('   Y/Y Growth', g[0], {'left': True}), {
            'py': fmtGrowth(G(yyv, 'py_q'), G(NMy, 'py_q')), 'prior': fmtGrowth(G(yyv, 'prior_q'), G(NMy, 'prior_q')),
            'target': fmtGrowth(G(yyv, 'target_q'), G(NMy, 'target_q')),
            'guide': yyGuide, 'next': fmtGrowth(G(yyv, 'next_q'), G(NMy, 'next_q')),
            'fyprev': fmtGrowth(G(yyv, 'fy_prev'), G(NMy, 'fy_prev')), 'fy0': fmtGrowth(G(yyv, 'fy0'), G(NMy, 'fy0')),
            'fy1': fmtGrowth(G(yyv, 'fy1'), G(NMy, 'fy1')),
            'fyg': guideCellFor(jstr(G(gi(rows0, 0), 'ltcode')) + '-Y', G(sf, 'guidance_fy'), '%') if showGuideYY else MDASH,
            'fyg1': guideCellFor(jstr(G(gi(rows0, 0), 'ltcode')) + '-Y', G(sf, 'guidance_fy1'), '%') if showGuideYY else MDASH
        }, pol)
        qq = cellsFor(td('   ' + jstr(SEQ), g[0], {'left': True}), {
            'py': fmtGrowth(G(qqv, 'py_q'), G(NMq, 'py_q')), 'prior': fmtGrowth(G(qqv, 'prior_q'), G(NMq, 'prior_q')),
            'target': fmtGrowth(G(qqv, 'target_q'), G(NMq, 'target_q')),
            'guide': MDASH, 'next': fmtGrowth(G(qqv, 'next_q'), G(NMq, 'next_q')),
            'fyprev': MDASH, 'fy0': MDASH, 'fy1': MDASH, 'fyg': MDASH, 'fyg1': MDASH
        }, pol)
        return [tRow(yy), tRow(qq)]

    for i, r in enumerate(jarr(rows0)):
        prev = gi(rows0, i - 1) if i >= 1 else UNDEFINED
        if seq(G(r, 'group'), 'kpi') and jt(prev) and seq(G(prev, 'group'), 'fin'):
            rows.append(tRow([tdRowhead(bandLabel('kpi'), len(g), wsum)]))
        moneyRow(r, [G(r, 'py_q'), G(r, 'prior_q'), G(r, 'target_q'), G(r, 'next_q'), G(r, 'fy_prev'), G(r, 'fy0'), G(r, 'fy1')])
        if seq(G(r, 'fmt'), '#'):
            countRow(r, [G(r, 'py_q'), G(r, 'prior_q'), G(r, 'target_q'), G(r, 'next_q'), G(r, 'fy_prev'), G(r, 'fy0'), G(r, 'fy1')])
        rows.append(tRow(cellsFor(td(G(r, 'name'), g[0], {'left': True}), {
            'py': fmtCell(r, G(r, 'py_q')), 'prior': fmtCell(r, G(r, 'prior_q')), 'target': fmtCell(r, G(r, 'target_q')),
            'targetOpts': {'b': True},
            'guide': guideCellFor(G(r, 'ltcode'), G(sf, 'guidance_target_q'), G(r, 'fmt'), r), 'next': fmtCell(r, G(r, 'next_q')),
            'fyprev': fmtCell(r, G(r, 'fy_prev')), 'fy0': fmtCell(r, G(r, 'fy0')), 'fy1': fmtCell(r, G(r, 'fy1')),
            'fyg': guideCellFor(G(r, 'ltcode'), G(sf, 'guidance_fy'), G(r, 'fmt'), r),
            'fyg1': guideCellFor(G(r, 'ltcode'), G(sf, 'guidance_fy1'), G(r, 'fmt'), r)
        }, 'neutral')))
        growth = G(sf, 'growth')
        if i == 0 and jt(G(growth, 'revenue')):
            rows.extend(growthRows(G(growth, 'revenue'), True, G(r, 'polarity')))
        epsCode = G(sf, 'eps_ltcode')
        if not jt(epsCode):
            epsCode = G(jor(G(jor(G(C, 'meta'), {}), 'metricset'), {}), 'eps_ltcode')
        isEpsRow = seq(G(r, 'ltcode'), epsCode) if jt(epsCode) else (
            re.search(r'^EPS', jstr(G(r, 'name')), re.I | re.ASCII) is not None)
        if isEpsRow and jt(G(growth, 'eps')):
            rows.extend(growthRows(G(growth, 'eps'), False, G(r, 'polarity')))
        for s in jlist(G(r, 'subs')):
            NMs = jor(G(s, 'cells_nm'), {})
            cells_ = G(s, 'cells')

            def fmtSub(v, k, s=s, NMs=NMs):
                if nn(v):
                    return 'NM' if jt(G(NMs, k)) else MDASH
                return fmtPct(v, 1, True) if seq(G(s, 'kind'), 'growth') else fmtPct(v, 1, False)
            subPol = G(s, 'polarity') if seq(G(s, 'kind'), 'growth') else 'neutral'
            isYY = seq(G(s, 'kind'), 'growth') and re.search(r'^Y/Y', jstr(jor(G(s, 'label'), ''))) is not None

            def subGuide(lst, r=r, isYY=isYY):
                return guideCellFor(jstr(G(r, 'ltcode')) + '-Y', lst, '%') if isYY else MDASH
            rows.append(tRow(cellsFor(td('   ' + jstr(G(s, 'label')), g[0], {'left': True}), {
                'py': fmtSub(G(cells_, 'py_q'), 'py_q'), 'prior': fmtSub(G(cells_, 'prior_q'), 'prior_q'),
                'target': fmtSub(G(cells_, 'target_q'), 'target_q'),
                'guide': subGuide(G(sf, 'guidance_target_q')), 'next': fmtSub(G(cells_, 'next_q'), 'next_q'),
                'fyprev': fmtSub(G(cells_, 'fy_prev'), 'fy_prev'), 'fy0': fmtSub(G(cells_, 'fy0'), 'fy0'),
                'fy1': fmtSub(G(cells_, 'fy1'), 'fy1'),
                'fyg': subGuide(G(sf, 'guidance_fy')), 'fyg1': subGuide(G(sf, 'guidance_fy1'))
            }, subPol)))
    body.append(table(rows, g))

    def topLineBasisCaption():
        b = G(sf, 'basis')
        if not jt(b) or not jt(G(b, 'mixed')):
            return
        rws = [x for x in jlist(G(b, 'fte_rows')) if jt(x) and jt(G(x, 'name'))]
        if not len(rws):
            return
        names = jjoin([G(x, 'name') for x in rws][:3], '; ') + (('; and ' + jstr(len(rws) - 3) + ' more') if len(rws) > 3 else '')
        body.append(para(run(
            'Basis: the top line is on a reported basis while ' + jstr(len(rws)) + ' row' +
            (' is' if len(rws) == 1 else 's are') + ' fully taxable equivalent (' + names + '). ' +
            'Percent-of-revenue cells on ' + ('that row' if len(rws) == 1 else 'those rows') +
            ' therefore divide an FTE figure by a reported one and will not tie to the two numbers ' +
            'printed above them.',
            {'sz': 14, 'i': True, 'color': '6E6E6E'}), {'spaceAfter': 60}))
    topLineBasisCaption()

    def undeclaredDirectionCaption():
        u = [x for x in jlist(G(sf, 'undeclared_direction')) if jt(x) and jt(G(x, 'name'))]
        if not len(u):
            return
        names = [jstr(G(x, 'name')) + ' (' + jstr(G(x, 'ltcode')) + ')' for x in u]
        shown = '; '.join(names[:4]) + (('; and ' + jstr(len(names) - 4) + ' more') if len(names) > 4 else '')
        body.append(para(run(
            'Direction not declared for ' + jstr(len(u)) + ' row' + ('' if len(u) == 1 else 's') + ': ' + shown +
            '. These are coloured as higher-is-better by default rather than by a recorded decision, ' +
            'so read the sign on ' + ('that row' if len(u) == 1 else 'those rows') + ' rather than the colour.',
            {'sz': 14, 'i': True, 'color': '6E6E6E'}), {'spaceAfter': 60}))
    undeclaredDirectionCaption()
    body.append(para(run('Summary', {'b': True, 'color': HEAD, 'sz': 21}), {'spaceBefore': 100, 'spaceAfter': 40, 'keepNext': True}))
    bullets(G(N, 'summary_bullets'), 20)


def setupFacts():
    ES = G(C, 'earnings_setup')
    if not jt(ES):
        return
    bits = []

    def pct(v):
        return (MINUS if jnum(v) < 0 else '+') + xlFixed(abs(jnum(v)), 1) + '%'
    if not nn(G(ES, 'move_30d_pct')):
        bits.append('30-day move ' + pct(G(ES, 'move_30d_pct')))
    if not nn(G(ES, 'drawdown_low')):
        bits.append('30-day low ' + fmtEps(G(ES, 'drawdown_low'), False) +
                    ((' (' + jstr(G(ES, 'drawdown_date')) + ')') if jt(G(ES, 'drawdown_date')) else ''))
    if not nn(G(ES, 'pe_now')):
        s = 'NTM P/E ' + fmtX(G(ES, 'pe_now'))
        if not nn(G(ES, 'pe_1yr_avg')):
            s += ' vs 1-yr avg ' + fmtX(G(ES, 'pe_1yr_avg'))
        if not nn(G(ES, 'pe_year_ago')):
            s += ' and ' + xlFixed(G(ES, 'pe_year_ago'), 1) + 'x a year ago'
        elif not nn(G(ES, 'pe_1yr_avg')):
            s += ' (no year-ago anchor in the fetched history)'
        if nn(G(ES, 'pe_1yr_avg')) and nn(G(ES, 'pe_year_ago')):
            s += ' (no P/E history returned, so it is not anchored to its own range)'
        bits.append(s)
    if not len(bits):
        return
    body.append(para(run('  ·  '.join(bits), {'sz': 18, 'i': True, 'color': '4A4A4A'}), {'spaceAfter': 60}))


def revisions():
    heading('Earnings Revisions: ' + jstr(G(Q, 'target_q')) + 'E')
    R = G(C, 'revisions')
    B = G(R, 'buckets')
    Rrows = G(R, 'rows')
    cols = G(R, 'columns')
    if isinstance(cols, list) and len(cols):
        RCOLS = cols
    else:
        r0 = gi(Rrows, 0) if isinstance(Rrows, list) else G(Rrows, 0)
        RCOLS = [
            {'keys': ['pre'], 'label': 'Pre-' + jstr(G(Q, 'prior_q')), 'date': G(B, 'pre')},
            {'keys': ['post'], 'label': 'Post-' + jstr(G(Q, 'prior_q')), 'date': G(B, 'post')},
            {'keys': ['interq'], 'label': 'Inter-Q', 'date': G(B, 'interq')},
            {'keys': ['latest'], 'label': 'Pre-' + jstr(G(Q, 'target_q')),
             'date': jor(G(r0, 'latest_date') if jt(r0) else r0, '')}
        ]
    BUCKET_W = math.floor((10800 - 2500 - 1500 - 1000) / len(RCOLS))
    g = [2500] + [BUCKET_W for _ in RCOLS] + [1500, 1000]
    wsum = sum(g)
    DCOL, DIRCOL = len(g) - 2, len(g) - 1
    rows = [tRow([th('Metric', g[0], {'left': True})] +
                 [thML(G(c, 'label'), mmdd(G(c, 'date')), g[i + 1]) for i, c in enumerate(RCOLS)] +
                 [th('Δ vs Post', g[DCOL]), th('Dir.', g[DIRCOL])], {'header': True})]
    lastGroup = None
    for r in jarr(Rrows):
        if not seq(G(r, 'group'), lastGroup):
            rows.append(tRow([tdRowhead(bandLabel(G(r, 'group')), len(g), wsum)]))
            lastGroup = G(r, 'group')
        acc = []
        for c in RCOLS:
            acc += [G(r, k) for k in jarr(G(c, 'keys'))]
        moneyRow(r, acc)
        if seq(G(r, 'fmt'), '#'):
            countRow(r, acc)

        def f(v, r=r):
            if jt(G(r, 'is_pct')):
                if v is None:
                    return MDASH
                return (MINUS + xlFixed(abs(jnum(v)), 1) if jnum(v) < 0 else xlFixed(v, 1)) + '%'
            return fmtCell(r, v)
        dtxt = MDASH
        dvp = G(r, 'delta_vs_post')
        du = G(r, 'delta_unit')
        if dvp is not None:
            if seq(du, 'bps'):
                dtxt = (MINUS if jnum(dvp) < 0 else '+') + xlFixed(abs(jnum(dvp)), 0) + 'bps'
            elif seq(du, '%'):
                draw = G(r, 'delta_raw')
                src = draw if is_num(draw) else dvp
                mag = xlFixed(abs(jnum(dvp)), 1)
                flat = jnum(mag) == 0
                dtxt = ('' if flat else (MINUS if jnum(src) < 0 else ('+' if jnum(src) > 0 else ''))) + mag + '%'
            elif seq(du, 'abs'):
                dtxt = (MINUS if jnum(dvp) < 0 else '+') + (
                    fmtEps(abs(jnum(dvp)), MINOR_has(G(r, 'ltcode'))) if seq(G(r, 'fmt'), '$')
                    else fmtCount(abs(jnum(dvp)), G(r, '__count')) if seq(G(r, 'fmt'), '#')
                    else fmtM(abs(jnum(dvp)), deltaShape(G(r, '__money'), dvp)))
            else:
                dtxt = fmtSignedM(dvp, G(r, '__money'))
        dr = G(r, 'dir')
        dirTxt = '↑' if seq(dr, 'up') else '↓' if seq(dr, 'down') else '→' if seq(dr, 'flat') else MDASH
        cc = clsColor(dvp, G(r, 'polarity'), 0 if seq(du, 'bps') else 1)
        cells_ = [td(G(r, 'name'), g[0], {'left': True})]
        for i, c in enumerate(RCOLS):
            k = next((kk for kk in jarr(G(c, 'keys')) if not nn(G(r, kk))), UNDEFINED)
            cells_.append(td(f(None if k is UNDEFINED else G(r, k)), g[i + 1], {'pol': 'neutral'}))
        ccol = COLORS[cc] if cc else UNDEFINED
        cells_.append(td(dtxt, g[DCOL], {'color': ccol, 'b': bool(cc), 'pol': G(r, 'polarity')}))
        cells_.append(td(dirTxt, g[DIRCOL], {'color': ccol, 'b': bool(cc), 'pol': G(r, 'polarity')}))
        rows.append(tRow(cells_))
    body.append(table(rows, g))
    noteBox('blue', G(N, 'revisions_note'))


def evolution():
    E = G(C, 'guidance_evolution')
    blocks = G(E, 'blocks')
    if not jt(G(blocks, 'length')):
        note = G(N, 'evolution_note')
        if isinstance(note, str):
            t = re.sub(r'^\s*(?:the\s+)?guidance evolution(?:\s+(?:section|card))?\s+(?:is\s+|was\s+)?omitted'
                       r'\s*[:.;,\-–—]?\s*', '', note, count=1, flags=re.I)
            if t and t != note:
                note = t[:1].upper() + t[1:]
        noteBox('green', note, 'Guidance Evolution omitted:')
        return
    heading('Guidance Evolution')
    columns = jarr(G(E, 'columns'))
    n = len(columns)
    label = 2400
    colW = math.floor((PAGE - label) / n) if n else math.inf
    g = [label] + [colW] * n
    wsum = sum(g)
    pdates = G(E, 'print_dates')
    rows = [tRow([th('Guidance Evolution', g[0], {'left': True})] +
                 [thML(c, '(' + mmddyy(G(pdates, i)) + ')', g[i + 1]) for i, c in enumerate(columns)], {'header': True})]
    for b in jarr(blocks):
        bcols = jarr(G(b, 'cols'))
        is_pct = jt(G(b, 'is_pct'))
        isFx = re.search(r'FX', jstr(G(b, 'lineitem')), re.I | re.ASCII) is not None
        dec = 2 if isFx else 1
        bf = jor(G(b, 'fmt'), '%' if is_pct else '$M')
        if is_pct or seq(bf, '$') or seq(bf, '#'):
            bShape = None
        else:
            vals = []
            for c in bcols:
                vals += [G(c, 'guide_point'), G(c, 'guide_hi'), G(c, 'guide_lo'), G(c, 'consensus'), G(c, 'actual')]
            bShape = moneyShape(vals)
        sfx = '' if is_pct else ((' (' + CS + ')') if seq(bf, '$') else '')

        def fnum(v, bf=bf, bShape=bShape):
            if nn(v):
                return MDASH
            return fmtEps(v) if seq(bf, '$') else fmtM(v, bShape)
        isPoint = all((G(c, 'guide_point') is not None) or (G(c, 'guide_hi') is None and G(c, 'guide_lo') is None)
                      for c in bcols)
        rows.append(tRow([tdRowhead(jstr(G(b, 'lineitem')) + sfx, len(g), wsum)]))

        def fv(v, d=UNDEFINED, is_pct=is_pct, fnum=fnum):
            if v is None:
                return MDASH
            if is_pct:
                dd = 1 if d is UNDEFINED else d
                return ('(' + xlFixed(abs(jnum(v)), dd) + '%)') if jnum(v) < 0 else (xlFixed(v, dd) + '%')
            return fnum(v)

        def dataRow(lbl, get, o=UNDEFINED, bcols=bcols):
            lo = {'left': True}
            if jt(o) and jt(o.get('lblO')):
                lo.update(o['lblO'])
            out = [td(lbl, g[0], lo)]
            for i, c in enumerate(bcols):
                r = get(c, i)
                out.append(td(r['text'], gi(g, i + 1), r.get('o', UNDEFINED)))
            return tRow(out)

        def gTxt(c):
            t = G(c, 'guide_text')
            return jstr(t) if jt(t) else None
        hasGuide = any(G(c, 'guide_point') is not None or G(c, 'guide_hi') is not None or
                       G(c, 'guide_lo') is not None or jt(gTxt(c)) for c in bcols)
        if hasGuide:
            if isPoint:
                rows.append(dataRow('Guide', lambda c, i: {
                    'text': gTxt(c) if (G(c, 'guide_point') is None and jt(gTxt(c))) else fv(G(c, 'guide_point'))}))
            else:
                rows.append(dataRow('Guide - High', lambda c, i: {
                    'text': gTxt(c) if (G(c, 'guide_hi') is None and jt(gTxt(c))) else fv(G(c, 'guide_hi'))}))
                rows.append(dataRow('Guide - Low', lambda c, i: {
                    'text': MDASH if (G(c, 'guide_lo') is None and jt(gTxt(c))) else fv(G(c, 'guide_lo'))}))
        hasCons = any(G(c, 'consensus') is not None for c in bcols)
        if hasCons:
            rows.append(dataRow('Consensus', lambda c, i: {
                'text': (MDASH if G(c, 'consensus') is None else fmtPct(G(c, 'consensus'), 1, False)) if is_pct
                else fnum(G(c, 'consensus')),
                'o': {'b': i == len(bcols) - 1 and G(c, 'consensus') is not None}}))
        rows.append(dataRow('Actual', lambda c, i: {
            'text': MDASH if G(c, 'actual') is None else (fmtPct(G(c, 'actual'), dec, False) if is_pct else fnum(G(c, 'actual'))),
            'o': {'b': G(c, 'actual') is not None}}))
        unit = 'pp' if is_pct else '%'
        if any(G(c, 'vs_guide_hi') is not None for c in bcols):
            def vg(c, i, unit=unit):
                v = G(c, 'vs_guide_hi')
                if v is None:
                    return {'text': MDASH}
                cc = clsColor(v)
                return {'text': (MINUS if jnum(v) < 0 else '+') + xlFixed(abs(jnum(v)), 1) + unit,
                        'o': {'i': True, 'color': COLORS[cc] if cc else UNDEFINED}}
            rows.append(dataRow('vs Guide - High', vg, {'lblO': {'i': True}}))
        if hasCons and any(G(c, 'vs_cons') is not None for c in bcols):
            def vc(c, i, unit=unit):
                v = G(c, 'vs_cons')
                if v is None:
                    return {'text': MDASH}
                cc = clsColor(v)
                return {'text': (MINUS if jnum(v) < 0 else '+') + xlFixed(abs(jnum(v)), 1) + unit,
                        'o': {'i': True, 'color': COLORS[cc] if cc else UNDEFINED}}
            rows.append(dataRow('vs Cons', vc, {'lblO': {'i': True}}))
    reactions = jarr(G(E, 'reactions'))
    rows.append(tRow([tdRowhead('Stock Reaction', len(g), wsum)]))
    rows.append(tRow([td('Pre → Post close', g[0], {'left': True})] +
                     [td((xlFixed(G(r, 'pre'), 2) + '→' + xlFixed(G(r, 'post'), 2))
                         if (G(r, 'pre') is not None and G(r, 'post') is not None) else MDASH, gi(g, i + 1))
                      for i, r in enumerate(reactions)] +
                     [td(MDASH, g[len(g) - 1])]))
    nd = [td('Next-day move', g[0], {'left': True})]
    for i, r in enumerate(reactions):
        cc = clsColor(G(r, 'move_pct'), UNDEFINED, 1)
        nd.append(td(MDASH if G(r, 'move_pct') is None else fmtPct(G(r, 'move_pct'), 1, True), gi(g, i + 1),
                     {'color': COLORS[cc] if cc else UNDEFINED, 'b': bool(cc)}))
    nd.append(td(MDASH, g[len(g) - 1]))
    rows.append(tRow(nd))
    body.append(table(rows, g))
    noteBox('green', G(N, 'evolution_note'))


def tone():
    TS = jor(G(N, 'tone_shift'), {'rows': []})
    if not jt(G(jor(G(TS, 'rows'), []), 'length')):
        heading('Management Tone Shift')
        note = jor(G(TS, 'note'), 'The prior-call transcript pass ' +
                   '(§Transcripts pass B) returned nothing for this issuer, and this table is a side-by-side ' +
                   'of two calls. Per the degradation rule it is omitted rather than filled from the latest ' +
                   'call alone. Call Commentary below covers the latest call.')
        if isinstance(note, str):
            t = re.sub(r'^\s*(?:management\s+)?tone shift(?:\s+(?:section|table))?\s+(?:is\s+|was\s+)?omitted'
                       r'\s*[:.;,\-–—]?\s*', '', note, count=1, flags=re.I)
            if t and t != note:
                note = t[:1].upper() + t[1:]
        noteBox('green', note, 'Tone Shift omitted:')
        return
    heading('Management Tone Shift')
    g = [1950, 4425, 4425]
    rows = [tRow([th('Dimension', g[0], {'left': True}), th(jstr(jor(G(TS, 'header_prev'), 'Prior call')), g[1], {'left': True}),
                  th(jstr(jor(G(TS, 'header_latest'), 'Latest call')), g[2], {'left': True})], {'header': True})]
    for r in jlist(G(TS, 'rows')):
        rows.append(tRow([
            cell(para(inlineRuns('<b>' + jstr(G(r, 'dim')) + '</b>', {'sz': 19}), {'align': 'left'}), {'w': g[0], 'vAlign': 'top'}),
            cell(para(inlineRuns(G(r, 'prev'), {'sz': 19}), {'align': 'left'}), {'w': g[1], 'vAlign': 'top'}),
            cell(para(inlineRuns(G(r, 'latest'), {'sz': 19}), {'align': 'left'}), {'w': g[2], 'vAlign': 'top'})
        ]))
    body.append(table(rows, g))
    nc = G(TS, 'note_color')
    noteBox('yellow' if seq(nc, 'amber') else jor(nc, 'green'), G(TS, 'note'))


def sellside():
    S = jor(G(N, 'sell_side'), {'rows': [], 'watch': []})
    heading('Sell-Side Focus')
    body.append(para(run('Persistent Focus Areas', {'b': True, 'color': HEAD, 'sz': 21}), {'spaceAfter': 40, 'keepNext': True}))
    g = [1950, 4425, 4425]
    rows = [tRow([th('Theme', g[0], {'left': True}), th('Analyst concern', g[1], {'left': True}),
                  th('Management pattern', g[2], {'left': True})], {'header': True})]
    for r in jlist(G(S, 'rows')):
        rows.append(tRow([
            cell(para(inlineRuns('<b>' + jstr(G(r, 'theme')) + '</b>', {'sz': 19}), {'align': 'left'}), {'w': g[0], 'vAlign': 'top'}),
            cell(para(inlineRuns(G(r, 'concern'), {'sz': 19}), {'align': 'left'}), {'w': g[1], 'vAlign': 'top'}),
            cell(para(inlineRuns(G(r, 'pattern'), {'sz': 19}), {'align': 'left'}), {'w': g[2], 'vAlign': 'top'})
        ]))
    body.append(table(rows, g))
    body.append(para(run('Watch on this call', {'b': True, 'color': HEAD, 'sz': 21}), {'spaceBefore': 100, 'spaceAfter': 40, 'keepNext': True}))
    bullets(G(S, 'watch'), 20)


def beats():
    BH = G(C, 'beat_history')
    if seq(G(BH, 'show'), False):
        return
    heading('Beat History & Reaction')
    body.append(chartDrawing('rIdChart', 6309360, 2286000, 'Beat history chart'))
    body.append(para('', {'spaceAfter': 40}))
    prints = jarr(G(BH, 'prints'))
    n = len(prints)
    label = 2400
    colW = math.floor((PAGE - label) / (n + 1))
    g = [label] + [colW] * (n + 1)
    rows = [tRow([th('vs Consensus (%)', g[0], {'left': True})] + [th(G(p, 'q'), g[i + 1]) for i, p in enumerate(prints)] +
                 [th('Avg', g[n + 1])], {'header': True})]

    def mkRow(lbl, arr, avg, pol, ex):
        cs = [td(lbl, g[0], {'left': True})]
        for i, v in enumerate(arr):
            out = gi(ex, i) if jt(ex) else ex
            cc = 'Y' if jt(out) else clsColor(v, pol, 1)
            cs.append(td(MDASH if v is None else fmtPct(v, 1, True) + ('*' if jt(out) else ''), g[i + 1],
                         {'color': COLORS[cc] if cc else UNDEFINED, 'b': (not jt(out)) and bool(cc),
                          'pol': 'neutral' if jt(out) else pol}))
        cc = clsColor(avg, pol, 1)
        cs.append(td(MDASH if avg is None else fmtPct(avg, 1, True), g[n + 1],
                     {'color': COLORS[cc] if cc else UNDEFINED, 'b': True, 'pol': pol}))
        return tRow(cs)
    AB = jor(G(BH, 'avg_basis'), {})

    def anyVal(a):
        return any(not nn(v) for v in a)
    revVals = [G(p, 'rev_beat_pct') for p in prints]
    if anyVal(revVals):
        rows.append(mkRow('Revenue beat', revVals, G(BH, 'avg_rev_beat'), 'higher', [G(p, 'rev_excluded') for p in prints]))
    kpiVals = [G(p, 'kpi1_beat_pct') for p in prints]
    kpi1 = G(BH, 'kpi1_name')
    if jt(kpi1) and anyVal(kpiVals):
        rows.append(mkRow(jstr(kpi1) + ' beat', kpiVals, G(BH, 'avg_kpi1_beat'), jor(G(BH, 'kpi1_polarity'), 'higher'),
                          [G(p, 'kpi1_excluded') for p in prints]))
    rows.append(mkRow('Next-day stock move', [G(p, 'reaction_pct') for p in prints], G(BH, 'avg_reaction'), 'higher', None))
    body.append(table(rows, g))
    capBits = []
    THIN, HELD = jor(G(BH, 'avg_thin'), {}), jor(G(BH, 'avg_withheld'), {})
    for lbl, b, isWithheld, isThin in [['Revenue', G(AB, 'revenue'), G(HELD, 'revenue'), G(THIN, 'revenue')],
                                       [kpi1, G(AB, 'kpi1'), G(HELD, 'kpi1'), G(THIN, 'kpi1')]]:
        if not jt(lbl) or not jt(b) or not jt(G(b, 'excluded')) or not jt(G(G(b, 'excluded'), 'length')):
            continue
        usedL = G(b, 'used')
        if not isinstance(usedL, list):
            raise TypeError('b.used.join is not a function')
        used = jjoin(usedL, ', ') or 'none'
        excl = '; '.join(jstr(G(e, 'q')) + ', ' + jstr(G(e, 'why')) for e in jarr(G(b, 'excluded')))
        if jt(isWithheld):
            capBits.append(jstr(lbl) + ' Avg is WITHHELD: only ' + jstr(G(b, 'n')) + ' of ' + jstr(len(prints)) + ' prints (' +
                           used + ') carries a usable pre-print consensus, and one ' +
                           'observation is not an average; * excludes ' + excl + '.')
        else:
            aap = G(b, 'avg_all_prints')
            capBits.append(jstr(lbl) + ' Avg rests on ' + jstr(G(b, 'n')) + ' of ' + jstr(len(prints)) + ' prints (' +
                           used + ')' +
                           (('; on all ' + jstr(len(prints)) + ' it reads ' + xlFixed(aap, 1) + '%')
                            if (not nn(aap) and not seq(aap, G(b, 'avg'))) else '') +
                           (', a thin track record rather than a pattern' if jt(isThin) else '') +
                           '; * excludes ' + excl + '.')
    if len(capBits):
        body.append(para(run(' '.join(capBits), {'sz': 14, 'i': True, 'color': '6E6E6E'}), {'spaceAfter': 60}))
    noteBox('yellow', G(N, 'beat_note'))


def documentFooter():
    asof = G(G(C, 'meta'), 'asof')
    provenance = ('Data: InSync MCP Financial Database. Estimates as of ' + jstr(G(asof, 'estimates')) +
                  '; market data ' + jstr(G(asof, 'market')) + '. Draft for analyst review - not investment advice.')
    ft = G(N, 'footer')
    txt = jtrim(jstr(ft)) if (jt(ft) and jtrim(jstr(ft))) else provenance
    body.append(para(inlineRuns(txt, {'sz': 15, 'color': '555555'}),
                     {'topBorder': {'color': GRID, 'sz': 6, 'space': 6}, 'spaceBefore': 220, 'spaceAfter': 0}))


def main():
    global C, N, V, Q, FY, T, CS, CURM, MINOR_LIST, SEQ, hasLogo, FACE_SCALE, BRAND, MK
    js_stdio()
    argv = sys.argv[1:]
    computedPath, narrativePath, outPath = (argv + [None, None, None])[:3]
    chartPng = argv[3] if len(argv) > 3 else None
    if not computedPath or not narrativePath or not outPath:
        err('usage: node render_docx.js <computed.json> <narrative.json> <out.docx> [chart.png]')
        sys.exit(1)
    C = _json_load(computedPath)
    N = _json_load(narrativePath)
    meta = G(C, 'meta')
    V, Q, FY, T = G(C, 'valuation'), G(meta, 'quarters'), G(meta, 'fiscal'), G(meta, 'ticker')
    cur = G(meta, 'currency')
    CURM = jor(G(cur, 'major') if jt(cur) else cur, 'USD')
    CS = jstr(jor(G(cur, 'symbol') if jt(cur) else cur, '$'))
    mc = jor(G(cur, 'minor_codes') if jt(cur) else cur, [])
    if not isinstance(mc, (list, str)):
        raise TypeError('object is not iterable (cannot read property Symbol(Symbol.iterator))')
    MINOR_LIST = list(mc)
    SEQ = jor(G(meta, 'seq_label'), 'Q/Q Growth')

    logoPath = os.path.normpath(os.path.join(HERE, '..', 'assets', 'insync_logo.png'))
    hasLogo = os.path.exists(logoPath)
    hasChart = bool(chartPng) and os.path.exists(chartPng)

    FACE_SCALE = _face_scale()
    MK = {
        'CO_LINE_BASE': _mk('EPP_CO_LINE_BASE', 372),
        'CO_LINE_SLOPE': _mk('EPP_CO_LINE_SLOPE', 5.5),
        'MARK_UP_BASE': _mk('EPP_MARK_UP_BASE', 13),
        'MARK_UP_SLOPE': _mk('EPP_MARK_UP_SLOPE', 5.5),
        'CO_LINE_2L': _mk('EPP_CO_LINE_2L', 224),
        'MARK_UP_2L': _mk('EPP_MARK_UP_2L', 1),
    }
    try:
        BRAND = _json_load(os.path.join(HERE, '..', 'common', 'brand.json'))
    except Exception:
        BRAND = {}

    titleHeader()
    theSetup()
    valuation()
    summary()
    heading('Earnings Setup')
    setupFacts()
    bullets(G(N, 'setup_bullets'), 20)
    revisions()
    heading('Earnings Expectations')
    bullets(G(N, 'expectations_bullets'), 20)
    evolution()
    heading('Call Commentary: Key Takeaways')
    bullets(G(jor(G(N, 'call_commentary'), {}), 'bullets'), 20)
    noteBox('red', G(jor(G(N, 'call_commentary'), {}), 'red_note'))
    tone()
    sellside()
    beats()
    documentFooter()

    asofO = G(meta, 'asof')
    asofM = jor(G(asofO, 'market') if jt(asofO) else asofO, None)
    asofE = jor(G(asofO, 'estimates') if jt(asofO) else asofO, None)
    if jt(asofE) and jt(asofM) and not seq(asofE, asofM):
        asofTxt = 'Estimates as of ' + jstr(asofE) + ', market as of ' + jstr(asofM)
    else:
        asofTxt = 'Data as of ' + jstr(jor(jor(asofM, asofE), ''))
    firm = jor(G(BRAND, 'firmName') if jt(BRAND) else BRAND, 'InSync Analytics')
    footerLine = (jstr(firm) + '  |  ' + jstr(T) + ' ' + jstr(G(Q, 'target_q')) +
                  ' Earnings Preview  |  Source: InSync MCP  |  ' + asofTxt)
    footerXml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' +
                 '<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" ' +
                 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">' +
                 para(run(footerLine, {'sz': 14, 'color': '6E6E6E'}) +
                      '<w:r><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:color w:val="6E6E6E"/>' +
                      '<w:sz w:val="14"/><w:szCs w:val="14"/></w:rPr><w:tab/></w:r>' +
                      '<w:fldSimple w:instr=" PAGE ">' + run('1', {'sz': 14, 'color': '6E6E6E'}) + '</w:fldSimple>',
                      {'topBorder': {'color': GRID, 'sz': 6, 'space': 4}, 'tabRight': PAGE, 'spaceBefore': 60, 'spaceAfter': 0}) +
                 '</w:ftr>')

    documentXml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' +
                   '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" ' +
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" ' +
                   'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">' +
                   '<w:body>' + ''.join(body) +
                   '<w:sectPr><w:footerReference w:type="default" r:id="rIdFooter"/>' +
                   '<w:pgSz w:w="12240" w:h="15840"/>' +
                   '<w:pgMar w:top="720" w:right="720" w:bottom="720" w:left="720" w:header="360" w:footer="360" w:gutter="0"/>' +
                   '</w:sectPr></w:body></w:document>')
    stylesXml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' +
                 '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">' +
                 '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="21"/><w:szCs w:val="21"/></w:rPr></w:rPrDefault>' +
                 '<w:pPrDefault><w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>' +
                 '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style></w:styles>')
    rels = ['<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>']
    media, parts = [], []
    if hasLogo:
        rels.append('<Relationship Id="rIdLogo" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/logo.png"/>')
        with open(logoPath, 'rb') as fh:
            media.append({'name': 'word/media/logo.png', 'data': fh.read()})
    rels.append('<Relationship Id="rIdChart" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart" Target="charts/chart1.xml"/>')
    parts.append({'name': 'word/charts/chart1.xml', 'data': utf8(buildChartXml(G(C, 'beat_history')))})
    rels.append('<Relationship Id="rIdFooter" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>')
    parts.append({'name': 'word/footer1.xml', 'data': utf8(footerXml)})
    docRels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' +
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(rels) + '</Relationships>')
    rootRels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' +
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>' +
                '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>' +
                '</Relationships>')
    contentTypes = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' +
                    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' +
                    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>' +
                    '<Default Extension="xml" ContentType="application/xml"/>' +
                    '<Default Extension="jpeg" ContentType="image/jpeg"/>' +
                    '<Default Extension="png" ContentType="image/png"/>' +
                    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>' +
                    '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>' +
                    '<Override PartName="/word/charts/chart1.xml" ContentType="application/vnd.openxmlformats-officedocument.drawingml.chart+xml"/>' +
                    '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>' +
                    '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>' +
                    '</Types>')
    coreXml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' +
               '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" ' +
               'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>' + xesc(jstr(T) + ' ' + jstr(G(Q, 'target_q')) + ' Earnings Preview') +
               '</dc:title>' + '<dc:creator>InSync Analytics</dc:creator></cp:coreProperties>')

    zipb = zipStore([
        {'name': '[Content_Types].xml', 'data': utf8(contentTypes)},
        {'name': '_rels/.rels', 'data': utf8(rootRels)},
        {'name': 'docProps/core.xml', 'data': utf8(coreXml)},
        {'name': 'word/document.xml', 'data': utf8(documentXml)},
        {'name': 'word/styles.xml', 'data': utf8(stylesXml)},
        {'name': 'word/_rels/document.xml.rels', 'data': utf8(docRels)},
    ] + parts + media)
    with open(outPath, 'wb') as fh:
        fh.write(zipb)
    print('DOCX OK: ' + outPath + ' (' + str(len(zipb)) + ' bytes, native beat chart embedded' +
          (', logo embedded' if hasLogo else '') + ')')


if __name__ == '__main__':
    main()
