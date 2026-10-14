"""
build_revision_xlsx.py - renders the Consensus Revision Analysis workbook (ONE sheet:
Revision Summary + panel; contract.history is audit-only and never rendered).
Usage:  python build_revision_xlsx.py <contract.json> <output.xlsx> [narrative.json]
Input contract (from prepare_revisions):
  { meta:{company,ticker,subindustry,q,fy1,fy2,printPeriod,printDate,nextPrintDate,prepared,updated,isFlow},
    cuts:{pre,t10?,t40?}, anchors:[eligible cuts], anchorView:{q|fy1:{t10:{distinct,sameAs}}},
    vintages:{q|fy1:{pre,t10,t40,cur}}, periods:[["q","3Q26"],["fy1","2026"]],
    sections:[{key,title,items:[{lt,label,kind,isPct,fav,gaapTwin?,
               per:{q:{cur,pre,t10,t40,low,high,cnt,yoy,seq,stale,...D},fy1:{...}}}]}],
    intel:{...}, excluded, scaleNotes, flowNotes,
    history:{fp,dates:[iso...],items:[...]} (audit only, never rendered) }
Two period blocks (the next quarter, then the fiscal year it rolls into) on one sheet.
Zero external dependencies (Python standard library only). Delta columns are live Excel formulas.

Ported from an earlier JavaScript renderer (internal edition only), with identical output.
JavaScript semantics that differ from Python (number printing, toFixed, Math.round, truthiness,
UTF-16 string lengths, regex classes, Date parsing) are reproduced explicitly; the helpers are
in the "JS semantics" block below and in jscompat.py.
"""
import math
import os
import re
import sys
import zlib
from decimal import Decimal, ROUND_HALF_UP, localcontext
from functools import lru_cache

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jscompat import (js_str, js_to_fixed, js_round, js_parse_float, js_number, js_keys,
                      js_cmp_sort, js_stdio, _js_read_json, _truthy, UNDEFINED)
from revision_common import money_fmt, STRICT_CHECKS

VERSION = '2.5.0'
U = UNDEFINED


class JSTypeError(Exception):
    pass


_SURR = re.compile('[\ud800-\udfff]')
_ASTRAL = re.compile('[\U00010000-\U0010ffff]')


def _to16(s):
    if not _ASTRAL.search(s):
        return s
    out = []
    for ch in s:
        o = ord(ch)
        if o < 0x10000:
            out.append(ch)
        else:
            o -= 0x10000
            out.append(chr(0xD800 + (o >> 10)) + chr(0xDC00 + (o & 0x3FF)))
    return ''.join(out)


def _from16(s, lone='�'):
    if not _SURR.search(s):
        return s
    out = []
    i, n = 0, len(s)
    while i < n:
        o = ord(s[i])
        if 0xD800 <= o <= 0xDBFF and i + 1 < n and 0xDC00 <= ord(s[i + 1]) <= 0xDFFF:
            out.append(chr(0x10000 + ((o - 0xD800) << 10) + (ord(s[i + 1]) - 0xDC00)))
            i += 2
            continue
        out.append(s[i] if (lone is None or not (0xD800 <= o <= 0xDFFF)) else lone)
        i += 1
    return ''.join(out)


def _out16(s):
    return _from16(s)


def _load(v):
    if isinstance(v, str):
        return _to16(v)
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, int):
        return float(v)
    if isinstance(v, float):
        return v
    if isinstance(v, list):
        return [_load(x) for x in v]
    if isinstance(v, dict):
        return {_to16(k): _load(x) for k, x in v.items()}
    return v


def _isnum(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def nn(v):
    return v is not None and v is not U


T = _truthy


def S(x):
    if isinstance(x, str):
        return x
    if isinstance(x, list):
        return ','.join('' if (e is None or e is U) else S(e) for e in x)
    if isinstance(x, dict):
        return '[object Object]'
    if x is U:
        return 'undefined'
    return js_str(x)


_IDX = re.compile(r'^(?:0|[1-9][0-9]*)$')


def G(o, k):
    if isinstance(o, dict):
        return o.get(k if isinstance(k, str) else S(k), U)
    if o is None or o is U:
        raise JSTypeError("Cannot read properties of %s (reading '%s')"
                          % ('null' if o is None else 'undefined', S(k)))
    if isinstance(o, (list, str)):
        if k == 'length':
            return len(o)
        if _isnum(k) and k == k and k >= 0 and k == int(k):
            i = int(k)
        elif isinstance(k, str) and _IDX.match(k):
            i = int(k)
        else:
            return U
        return o[i] if i < len(o) else U
    return U


def LEN(x):
    return G(x, 'length')


def N(v):
    if type(v) is float:
        return v
    if _isnum(v):
        return v
    if isinstance(v, (list, dict)):
        return js_number(S(v)) if isinstance(v, list) else math.nan
    r = js_number(v)
    return float(r) if _isnum(r) else math.nan


def PLUS(a, b):
    if isinstance(a, (list, dict)):
        a = S(a)
    if isinstance(b, (list, dict)):
        b = S(b)
    if isinstance(a, str) or isinstance(b, str):
        return S(a) + S(b)
    return N(a) + N(b)


def SUB(a, b):
    return N(a) - N(b)


def MUL(a, b):
    return N(a) * N(b)


def DIV(a, b):
    a, b = N(a), N(b)
    if b == 0:
        if a != a or a == 0:
            return math.nan
        neg = (a < 0) != (math.copysign(1.0, b) < 0)
        return -math.inf if neg else math.inf
    return a / b


def ABS(v):
    return abs(N(v))


def SIGN(v):
    v = N(v)
    if v != v:
        return math.nan
    if v > 0:
        return 1
    if v < 0:
        return -1
    return v


def _prim(v):
    return S(v) if isinstance(v, (list, dict)) else v


def _rel(a, b):
    a, b = _prim(a), _prim(b)
    if isinstance(a, str) and isinstance(b, str):
        return a, b
    return N(a), N(b)


def LT(a, b):
    a, b = _rel(a, b)
    return a < b


def GT(a, b):
    a, b = _rel(a, b)
    return a > b


def LE(a, b):
    a, b = _rel(a, b)
    if isinstance(a, float) and (a != a or b != b):
        return False
    return a <= b


def GE(a, b):
    a, b = _rel(a, b)
    if isinstance(a, float) and (a != a or b != b):
        return False
    return a >= b


def SEQ(a, b):
    if _isnum(a) and _isnum(b):
        return a == b
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    if isinstance(a, (bool, type(None))) or a is U:
        return a is b
    return a is b


def OR(*vals):
    for v in vals[:-1]:
        if T(v):
            return v
    return vals[-1]


def JMAX(*a):
    if not a:
        return -math.inf
    for x in a:
        if x != x:
            return math.nan
    return max(a)


def JMIN(*a):
    if not a:
        return math.inf
    for x in a:
        if x != x:
            return math.nan
    return min(a)


def CEIL(x):
    return math.ceil(x) if math.isfinite(x) else x


def FLOOR(x):
    return math.floor(x) if math.isfinite(x) else x


def ARR(v):
    if isinstance(v, list):
        return v
    raise JSTypeError('%s is not an array' % S(v))


def ITER(v):
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        return [_to16(ch) for ch in _from16(v, None)]
    raise JSTypeError('%s is not iterable' % S(v))


def SLICE(v, a, b=None):
    if isinstance(v, (list, str)):
        return v[a:b]
    raise JSTypeError('slice is not a function')


def JOIN(v, sep):
    return sep.join('' if (e is None or e is U) else S(e) for e in ARR(v))


def STR_ONLY(v):
    if not isinstance(v, str):
        raise JSTypeError('not a string method target')
    return v


def UP(s):
    return _to16(_from16(STR_ONLY(s), None).upper())


def LOW(s):
    return _to16(_from16(STR_ONLY(s), None).lower())


_WS_CHARS = '\t\n\x0b\x0c\r                  　﻿'


def TRIM(s):
    return STR_ONLY(s).strip(_WS_CHARS)


def FIXED(v, d):
    if not _isnum(v):
        raise JSTypeError('toFixed is not a function')
    return js_to_fixed(v, d)


_WSC = r'\t\n\x0b\x0c\r    -     　﻿'


def _tr(src):
    out = []
    i, n = 0, len(src)
    incls = False
    while i < n:
        c = src[i]
        if c == '\\':
            d = src[i + 1]
            if d == 's':
                out.append(_WSC if incls else '[' + _WSC + ']')
            elif d == 'S':
                out.append('[^' + _WSC + ']')
            elif d == 'd':
                out.append('0-9' if incls else '[0-9]')
            elif d == '/':
                out.append('/')
            else:
                out.append('\\' + d)
            i += 2
            continue
        if incls:
            if c == ']':
                incls = False
                out.append(c)
            elif c in '[&~|':
                out.append('\\' + c)
            else:
                out.append(c)
        else:
            if c == '[':
                incls = True
                out.append(c)
                if i + 1 < n and src[i + 1] == '^':
                    out.append('^')
                    i += 1
            elif c == '.':
                out.append(r'[^\n\r  ]')
            elif c == '$':
                out.append(r'\Z')
            else:
                out.append(c)
        i += 1
    return ''.join(out)


@lru_cache(maxsize=None)
def RX(src, flags=''):
    f = re.ASCII
    if 'i' in flags:
        f |= re.IGNORECASE
    return re.compile(_tr(src), f)


def TEST(src, s, flags=''):
    return RX(src, flags).search(S(s)) is not None


def MATCHALL(src, s, flags=''):
    return [m.group(0) for m in RX(src, flags).finditer(s)]


def REPL(src, s, rep, flags='', count=0):
    return RX(src, flags).sub(lambda m: rep, s, count=count)


def SPLIT(src, s):
    return RX(src).split(s)


def JS_RE_ESCAPE(s):
    return re.sub(r'[.*+?^${}()|\[\]\\]', lambda m: '\\' + m.group(0), s)


_POW10N = [1, 0.1, 0.01, 0.001, 0.00009999999999999999, 0.000009999999999999999, 0.000001, 1e-7, 1e-8,
           1e-9, 1e-10, 1e-11, 1e-12, 1e-13, 1e-14, 1e-15, 1e-16, 9.999999999999999e-18, 1e-18, 1e-19,
           1.0000000000000001e-20, 1.0000000000000001e-21, 1e-22, 1.0000000000000001e-23,
           1.0000000000000001e-24, 1e-25, 1e-26, 1e-27, 1e-28, 1.0000000000000001e-29, 1e-30]


def POW10N(k):
    return _POW10N[k] if k < len(_POW10N) else 10.0 ** -k


_ISO_D = re.compile(r'^([0-9]{4}|[+-][0-9]{6})(?:-([0-9]{2})(?:-([0-9]{2}))?)?$')


def _days_from_civil(y, m, d):
    y -= m <= 2
    era = y // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def _civil_from_days(z):
    z += 719468
    era = z // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + (3 if mp < 10 else -9)
    return y + (m <= 2), m, d


def _utc_date(d):
    m = _ISO_D.match(S(d))
    if not m:
        return None
    ys = m.group(1)
    if ys == '-000000':
        return None
    y = int(ys)
    mo = int(m.group(2)) if m.group(2) else 1
    dd = int(m.group(3)) if m.group(3) else 1
    if not (1 <= mo <= 12) or not (1 <= dd <= 31):
        return None
    days = _days_from_civil(y, mo, 1) + dd - 1
    if abs(days) > 100000000:
        return None
    yy, mm, dy = _civil_from_days(days)
    return yy, mm - 1, dy


_MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']


def _fmt_arg(a):
    if isinstance(a, str):
        return a
    if isinstance(a, float) and a == 0 and math.copysign(1.0, a) < 0:
        return '-0'
    return S(a)


def log(*args):
    sys.stdout.write(_out16(' '.join(_fmt_arg(a) for a in args)) + '\n')


def elog(*args):
    sys.stderr.write(_out16(' '.join(_fmt_arg(a) for a in args)) + '\n')


def _exit(code):
    sys.stdout.flush()
    sys.stderr.flush()
    sys.exit(code)


def _js_json_key(o):
    return '{' + ','.join('"%s":%s' % (k, ('"%s"' % v) if isinstance(v, str) else S(v))
                          for k, v in o.items() if v is not U) + '}'


def deflate_raw(data):
    c = zlib.compressobj(6, zlib.DEFLATED, -15, 8)
    return c.compress(bytes(data)) + c.flush()


def main(argv):
    js_stdio()
    contractPath = argv[1] if len(argv) > 1 else U
    outPath = argv[2] if len(argv) > 2 else U
    narrativePath = argv[3] if len(argv) > 3 else U
    if not T(contractPath) or not T(outPath):
        elog('usage: python build_revision_xlsx.py <contract.json> <output.xlsx> [narrative.json]')
        _exit(1)
    C = _load(_js_read_json(contractPath))
    M = G(C, 'meta')
    cur_in = OR(G(M, 'currency'), '$')
    MF = money_fmt(cur_in if isinstance(cur_in, str) else '$')
    if not isinstance(cur_in, str):
        raise JSTypeError('MF.sym.replace is not a function')
    SYMRX = JS_RE_ESCAPE(MF.sym)
    if not SEQ(G(M, 'version'), VERSION):
        log(f"   ⚠ contract.json was prepared by version {S(OR(G(M, 'version'), '(none)'))}; this builder is {VERSION}. Re-run prepare_revisions.py from the same package.")

    def stripQual(l):
        return REPL(r'\s*\([^()]*\)', S(l), ' ')
    UP_RE = r'(revenue|gross profit|operating income|operating profit|\bebit(da)?\b|net income|\beps\b|earnings per|free cash|operating cash|\bocf\b|\bfcf\b|dividend|\bdps\b|book value|\bbvps\b|backlog|\brpo\b|net interest (&|and) other|interest income|non.?interest income|net interest income|\bppnr\b|pre.?provision|premium|underwriting (income|profit)|investment income|\bcet ?1\b|tier 1 capital|tangible common|total equity|total assets|gross loans|net loans|\bdeposits\b|policies in force|working capital|headcount)'
    DOWN_RE = r'(cost of|\bcogs\b|operating expenses|\bopex\b|sg&a|selling, general|general & admin|advertis|research|r&d|traffic acquisition|\btac\b|depreciation|amortization|^d&a\b|stock-based|share-based|\bsbc\b|severance|interest expense|income tax|effective tax|tax rate|\betr\b|minority|shares outstanding|basic shares|diluted shares|\bfds\b|share count|\bcapex\b|purchases of property|capital expenditure|\bdebt\b|total liabilities|provision|charge.?off|combined ratio|loss ratio|expense ratio|catastrophe|nonaccrual|non.?performing|\bnpa\b|\bnco\b|delinquen)'

    def dirOf(l):
        L = stripQual(l)
        if TEST(r'(\bdebt\b.*\bebitda\b|leverage)', L, 'i'):
            return 'down'
        if TEST(DOWN_RE, L, 'i'):
            return 'down'
        if TEST(UP_RE, L, 'i'):
            return 'up'
        return None

    def zip_(es):
        ch, cn = [], []
        off = 0
        for e in es:
            nm = _out16(e['name']).encode('utf-8')
            raw = e['data'] if isinstance(e['data'], (bytes, bytearray)) else _out16(e['data']).encode('utf-8')
            c = deflate_raw(raw)
            cr = zlib.crc32(raw) & 0xFFFFFFFF
            lh = bytearray(30)
            lh[0:4] = (0x04034b50).to_bytes(4, 'little')
            lh[4:6] = (20).to_bytes(2, 'little')
            lh[8:10] = (8).to_bytes(2, 'little')
            lh[14:18] = cr.to_bytes(4, 'little')
            lh[18:22] = len(c).to_bytes(4, 'little')
            lh[22:26] = len(raw).to_bytes(4, 'little')
            lh[26:28] = len(nm).to_bytes(2, 'little')
            ch += [bytes(lh), nm, c]
            cd = bytearray(46)
            cd[0:4] = (0x02014b50).to_bytes(4, 'little')
            cd[4:6] = (20).to_bytes(2, 'little')
            cd[6:8] = (20).to_bytes(2, 'little')
            cd[10:12] = (8).to_bytes(2, 'little')
            cd[16:20] = cr.to_bytes(4, 'little')
            cd[20:24] = len(c).to_bytes(4, 'little')
            cd[24:28] = len(raw).to_bytes(4, 'little')
            cd[28:30] = len(nm).to_bytes(2, 'little')
            cd[42:46] = off.to_bytes(4, 'little')
            cn += [bytes(cd), nm]
            off += len(lh) + len(nm) + len(c)
        cb = b''.join(cn)
        co = off
        end = bytearray(22)
        end[0:4] = (0x06054b50).to_bytes(4, 'little')
        end[8:10] = len(es).to_bytes(2, 'little')
        end[10:12] = len(es).to_bytes(2, 'little')
        end[12:16] = len(cb).to_bytes(4, 'little')
        end[16:20] = co.to_bytes(4, 'little')
        return b''.join(ch) + cb + bytes(end)

    def esc(s):
        return S(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')

    def colL(n):
        s = ''
        while n > 0:
            n -= 1
            s = chr(65 + n % 26) + s
            n = n // 26
        return s

    numFmts = [
        {'id': 164, 'code': '#,##0.0;(#,##0.0)'}, {'id': 165, 'code': '#,##0.00;(#,##0.00)'}, {'id': 166, 'code': '0.0%;(0.0%)'},
        {'id': 167, 'code': '#,##0.0'}, {'id': 168, 'code': '0.00"x"'}, {'id': 169, 'code': '#,##0'},
        {'id': 170, 'code': '#,##0.0" ▲";#,##0.0" ▼";"–"'},
        {'id': 171, 'code': '#,##0.00" ▲";#,##0.00" ▼";"–"'},
        {'id': 172, 'code': '0.0%" ▲";0.0%" ▼";"–"'},
        {'id': 173, 'code': '#,##0" bps ▲";#,##0" bps ▼";"–"'},
        {'id': 174, 'code': '0.00%" ▲";0.00%" ▼";"–"'},
        {'id': 177, 'code': '+0.000;-0.000;"–"'},
        {'id': 178, 'code': '0%;(0%)'},
    ]
    NF = {'money': 164, 'eps': 165, 'pct': 166, 'shares': 167, 'ratio': 168, 'num': 167, 'days': 167}
    fonts = [
        {'color': 'FF243141'}, {'b': 1, 'color': 'FF12314C'}, {'b': 1, 'sz': 9, 'color': 'FFFFFFFF'}, {'b': 1, 'sz': 14, 'color': 'FFFFFFFF'},
        {'i': 1, 'color': 'FF5B6B7B'}, {'b': 1, 'sz': 10, 'color': 'FFFFFFFF'}, {'i': 1, 'sz': 8, 'color': 'FF6B7A8A'}, {'color': 'FF5B6B7B'},
        {'b': 1, 'color': 'FF0F2A43'}, {'i': 1, 'color': 'FF7A8794'}, {'b': 1, 'sz': 9, 'color': 'FF12314C'},
        {'b': 1, 'sz': 9, 'color': 'FF0B7A38'}, {'b': 1, 'sz': 9, 'color': 'FFC0392B'},
        {'sz': 9, 'color': 'FF3A4653'}, {'b': 1, 'sz': 14, 'color': 'FF1B1B19'}, {'b': 1, 'sz': 16, 'color': 'FFE98A38'},
        {'b': 1, 'sz': 11, 'color': 'FFE98A38'},
        {'sz': 11, 'color': 'FF5B6B7B'},
    ]
    fills = [{'none': 1}, {'gray': 1}, {'fg': 'FF1B1B19'}, {'fg': 'FF1B1B19'}, {'none': 1}, {'fg': 'FF1B1B19'}, {'fg': 'FFF6F7F9'}, {'none': 1}, {'fg': 'FFFDF3EA'}, {'fg': 'FF1B1B19'}, {'fg': 'FFF2F5F8'}, {'fg': 'FF1E7A44'}, {'fg': 'FFB03A2E'}, {'fg': 'FFB9862B'}, {'fg': 'FF3A4653'}]
    borders = [{'none': 1}, {'b': 'FFEDEFF2'}, {'none': 1}, {'b': 'FFE98A38'}]
    xfs = []
    xk = {}

    def xf(o):
        k = _js_json_key(o)
        if k in xk:
            return xk[k]
        i = len(xfs)
        xfs.append(o)
        xk[k] = i
        return i

    def oget(o, k):
        v = o.get(k, U)
        return v

    def fontXML(f):
        s = '<font>'
        if T(f.get('b')):
            s += '<b/>'
        if T(f.get('i')):
            s += '<i/>'
        s += f'<sz val="{S(OR(oget(f, "sz"), 10))}"/><color rgb="{S(OR(oget(f, "color"), "FF000000"))}"/><name val="Calibri"/></font>'
        return s

    def fillXML(f):
        if f.get('none'):
            return '<fill><patternFill patternType="none"/></fill>'
        if f.get('gray'):
            return '<fill><patternFill patternType="gray125"/></fill>'
        return f'<fill><patternFill patternType="solid"><fgColor rgb="{f["fg"]}"/></patternFill></fill>'

    def borderXML(b):
        if b.get('none'):
            return '<border><left/><right/><top/><bottom/><diagonal/></border>'
        s = '<border><left/><right/>'
        s += f'<top style="thin"><color rgb="{b["t"]}"/></top>' if b.get('t') else '<top/>'
        s += f'<bottom style="thin"><color rgb="{b["b"]}"/></bottom>' if b.get('b') else '<bottom/>'
        return s + '<diagonal/></border>'

    def stylesXML():
        nf = ''.join(f'<numFmt numFmtId="{n["id"]}" formatCode="{esc(n["code"])}"/>' for n in numFmts)
        ft = ''.join(fontXML(f) for f in fonts)
        fl = ''.join(fillXML(f) for f in fills)
        bd = ''.join(borderXML(b) for b in borders)

        def one(o):
            g = lambda k: oget(o, k)
            if T(g('h')) or T(g('ind')) or T(g('w')) or T(g('sh')):
                al = (f'<alignment{(" horizontal=" + chr(34) + S(g("h")) + chr(34)) if T(g("h")) else ""} vertical="center"'
                      f'{(" indent=" + chr(34) + S(g("ind")) + chr(34)) if T(g("ind")) else ""}'
                      f'{" wrapText=" + chr(34) + "1" + chr(34) if T(g("w")) else ""}'
                      f'{" shrinkToFit=" + chr(34) + "1" + chr(34) if T(g("sh")) else ""}/>')
            else:
                al = '<alignment vertical="center"/>'
            return (f'<xf numFmtId="{S(OR(g("nf"), 0))}" fontId="{S(OR(g("f"), 0))}" fillId="{S(OR(g("fill"), 0))}" borderId="{S(OR(g("bd"), 0))}" xfId="0"'
                    f'{" applyNumberFormat=" + chr(34) + "1" + chr(34) if T(g("nf")) else ""} applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1">{al}</xf>')
        cx = ''.join(one(o) for o in xfs)
        dxfs = '<dxfs count="2"><dxf><font><color rgb="FF0B7A38"/></font></dxf><dxf><font><color rgb="FFC0392B"/></font></dxf></dxfs>'
        return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts count="{len(numFmts)}">{nf}</numFmts><fonts count="{len(fonts)}">{ft}</fonts><fills count="{len(fills)}">{fl}</fills><borders count="{len(borders)}">{bd}</borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="{len(xfs)}">{cx}</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>{dxfs}</styleSheet>')

    class Sheet:
        def __init__(self):
            self.rows = {}
            self.merges = []
            self.rowHt = {}
            self.cfUp = {}
            self.cfDown = {}

        def put(self, r, c, o):
            self.rows.setdefault(r, {})[c] = o

        def T(self, r, c, v, s):
            self.put(r, c, {'t': 's', 'v': v, 's': s})

        def N(self, r, c, v, s):
            self.put(r, c, {'v': v, 's': s})

        def Fo(self, r, c, f, s, cached=U):
            self.put(r, c, {'f': f, 's': s, 'cv': cached})

        def merge(self, r, c1, c2):
            self.merges.append(f'{colL(c1)}{r}:{colL(c2)}{r}')

        def setHt(self, r, h):
            if not T(self.rowHt.get(r)) or self.rowHt[r] < h:
                self.rowHt[r] = h

        def cf(self, dir_, col, row):
            b = self.cfDown if SEQ(dir_, 'down') else self.cfUp
            b.setdefault(col, []).append(row)

        def xml(self, opts):
            sd = []
            for rn in sorted(self.rows):
                cs = self.rows[rn]
                cx = []
                for c in sorted(cs):
                    cell = cs[c]
                    ref = colL(c) + str(rn)
                    st = f' s="{S(OR(cell.get("s", U), 0))}"'
                    if nn(cell.get('rich')):
                        cx.append(f'<c r="{ref}"{st} t="inlineStr"><is>{cell["rich"]}</is></c>')
                    elif nn(cell.get('f')):
                        cv = cell.get('cv', U)
                        vv = f'<v>{S(cv)}</v>' if (nn(cv) and math.isfinite(N(cv))) else ''
                        cx.append(f'<c r="{ref}"{st}><f>{esc(cell["f"])}</f>{vv}</c>')
                    elif cell.get('t') == 's':
                        v = cell['v']
                        cx.append(f'<c r="{ref}"{st}{" t=" + chr(34) + "inlineStr" + chr(34) if T(v) else ""}>'
                                  f'{("<is><t xml:space=" + chr(34) + "preserve" + chr(34) + ">" + esc(v) + "</t></is>") if T(v) else ""}</c>')
                    else:
                        cx.append(f'<c r="{ref}"{st}><v>{S(cell["v"])}</v></c>')
                ht = f' ht="{S(self.rowHt[rn])}" customHeight="1"' if T(self.rowHt.get(rn)) else ''
                sd.append(f'<row r="{rn}"{ht}>{"".join(cx)}</row>')
            sd = ''.join(sd)
            mg = (f'<mergeCells count="{len(self.merges)}">{"".join(f"<mergeCell ref={chr(34)}{m}{chr(34)}/>" for m in self.merges)}</mergeCells>'
                  if self.merges else '')

            def rr(rs):
                rs = sorted(rs)
                out = []
                s = p = None
                for x in rs:
                    if s is None:
                        s = p = x
                        continue
                    if x == p + 1:
                        p = x
                        continue
                    out.append((s, p))
                    s = p = x
                if s is not None:
                    out.append((s, p))
                return out
            cf = ''
            pr = 1
            for bag, g, r2 in ((self.cfUp, 0, 1), (self.cfDown, 1, 0)):
                for col in bag:
                    ranges = ' '.join(f'{col}{a}:{col}{b}' for a, b in rr(bag[col]))
                    cf += (f'<conditionalFormatting sqref="{ranges}"><cfRule type="cellIs" dxfId="{g}" priority="{pr}" operator="greaterThan"><formula>0</formula></cfRule>'
                           f'<cfRule type="cellIs" dxfId="{r2}" priority="{pr + 1}" operator="lessThan"><formula>0</formula></cfRule></conditionalFormatting>')
                    pr += 2
            drawing = f'<drawing r:id="{opts["drawing"]}"/>' if opts.get('drawing') else ''
            return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheetPr><pageSetupPr fitToPage="1"/></sheetPr><sheetViews><sheetView showGridLines="0" workbookViewId="0">{opts.get("pane") or ""}</sheetView></sheetViews><sheetFormatPr defaultRowHeight="14.5"/>{opts.get("cols") or ""}<sheetData>{sd}</sheetData>{mg}{cf}<pageMargins left="0.4" right="0.4" top="0.4" bottom="0.4" header="0.3" footer="0.3"/><pageSetup orientation="landscape" fitToWidth="1" fitToHeight="0"/><headerFooter><oddFooter>&amp;C&amp;8InSync Analytics  ·  For informational purposes only. Not investment advice.</oddFooter></headerFooter>{drawing}</worksheet>')

    ST = {}
    ST['hL'] = xf({'f': 2, 'fill': 3, 'h': 'left', 'bd': 2})
    ST['hR'] = xf({'f': 2, 'fill': 3, 'h': 'right', 'bd': 2})
    ST['hC'] = xf({'f': 2, 'fill': 3, 'h': 'center', 'bd': 2})
    ST['sec'] = xf({'f': 5, 'fill': 5, 'h': 'left'})
    ST['perBand'] = xf({'f': 16, 'fill': 14, 'h': 'left'})
    ST['dt'] = xf({'f': 6, 'h': 'right', 'bd': 1})
    ST['dtL'] = xf({'f': 6, 'h': 'left', 'bd': 1})
    RULE = xf({'bd': 3})

    def idRich(name, prod):
        return (f'<r><rPr><sz val="10"/><color rgb="FF5B6B7B"/><rFont val="Calibri"/></rPr><t xml:space="preserve">{" " * 46}</t></r><r><rPr><b/><sz val="14"/><color rgb="FF1B1B19"/><rFont val="Calibri"/></rPr><t xml:space="preserve">{esc(name)}</t></r>'
                + (f'<r><rPr><sz val="12"/><color rgb="FF5B6B7B"/><rFont val="Calibri"/></rPr><t xml:space="preserve">   {esc(prod)}</t></r>' if T(prod) else ''))
    dtSep = '<r><rPr><sz val="10"/><color rgb="FF9AA4AE"/><rFont val="Calibri"/></rPr><t xml:space="preserve">  ·  </t></r>'

    def mastRich(label, val):
        return f'<r><rPr><b/><sz val="10"/><color rgb="FFE98A38"/><rFont val="Calibri"/></rPr><t xml:space="preserve">{esc(label)} </t></r><r><rPr><sz val="10"/><color rgb="FF1B1B19"/><rFont val="Calibri"/></rPr><t xml:space="preserve">{esc(val)}</t></r>'

    def fmtDateUS(d):
        if not T(d):
            return ''
        dt = _utc_date(PLUS(d, ''))
        if dt is None:
            return 'undefined NaN, NaN'
        return f'{_MON[dt[1]]} {dt[2]}, {dt[0]}'

    def nmB(b):
        return xf({'f': 1, 'fill': 6 if b else 0, 'h': 'left', 'bd': 1})

    def nfOf(k):
        return NF.get(k if isinstance(k, str) else S(k), U)

    def vSty(k, b, em=U):
        return xf({'f': 8 if T(em) else 0, 'fill': (10 if T(em) else 6) if b else (10 if T(em) else 0), 'h': 'right', 'bd': 1, 'nf': nfOf(k)})

    def vStyStale(k, b):
        return xf({'f': 4, 'fill': 6 if b else 0, 'h': 'right', 'bd': 1, 'nf': nfOf(k)})

    def dashSty(b):
        return xf({'f': 9, 'fill': 6 if b else 0, 'h': 'right', 'bd': 1})

    def gSty(b):
        return xf({'f': 0, 'fill': 6 if b else 0, 'h': 'right', 'bd': 1, 'nf': 166})

    def gStyBig(b):
        return xf({'f': 0, 'fill': 6 if b else 0, 'h': 'right', 'bd': 1, 'nf': 178})

    def gCell(r, c, v, b):
        if nn(v):
            s1.N(r, c, v, gStyBig(b) if ABS(v) >= 1 else gSty(b))
        else:
            s1.T(r, c, '', blank(b))

    def dSty(k, b):
        return xf({'f': 13, 'fill': 6 if b else 0, 'h': 'right', 'bd': 1, 'nf': 171 if (SEQ(k, 'eps') or SEQ(k, 'ratio')) else 170})

    def pSty(p, b, fine):
        return xf({'f': 13, 'fill': 6 if b else 0, 'h': 'right', 'bd': 1, 'nf': 173 if T(p) else (174 if fine else 172)})

    def cSty(b):
        return xf({'f': 7, 'fill': 6 if b else 0, 'h': 'right', 'bd': 1, 'nf': 169})

    def blank(b):
        return xf({'f': 0, 'fill': 6 if b else 0, 'bd': 1})

    def mmdd(d):
        if not T(d):
            return ''
        t = S(d)
        return t[5:7] + '/' + t[8:10]

    def fmtDate(d):
        if not T(d):
            return ''
        dt = _utc_date(PLUS(d, ''))
        if dt is None:
            return 'NaN undefined NaN'
        return f'{dt[2]} {_MON[dt[1]]} {dt[0]}'

    def per_of(e):
        if isinstance(e, list):
            return G(e, 0), G(e, 1)
        if isinstance(e, str):
            e = ITER(e)
            return G(e, 0), G(e, 1)
        raise JSTypeError('periods entry is not iterable')

    def sections():
        return ITER(G(C, 'sections'))

    def items_of(sec):
        return ITER(G(sec, 'items'))

    anchors = G(C, 'anchors')
    ANCH_ALL = anchors if (T(anchors) and T(LEN(anchors))) else ['pre']
    AHDR = {'pre': 'Pre-Print', 't10': 'T+10', 't40': 'T+40', 'cur': 'Current'}

    def AH(k):
        return G(AHDR, k)

    def _AV():
        if T(G(C, 'anchorView')):
            return G(C, 'anchorView')
        out = {}

        def same(a, b):
            return ((not nn(a) and not nn(b))
                    or (nn(a) and nn(b) and ABS(SUB(a, b)) <= 1e-9 * JMAX(1, ABS(b))))
        for e in ITER(G(C, 'periods')):
            pk = per_of(e)[0]
            items = []
            for s in sections():
                for it in items_of(s):
                    if T(G(G(it, 'per'), pk)):
                        items.append(G(G(it, 'per'), pk))
            view = {'pre': {'distinct': True}}
            prev = 'pre'
            for k in ITER(ANCH_ALL):
                if SEQ(k, 'pre'):
                    continue
                sp = all(same(G(p, k), G(p, prev)) for p in items)
                sc = all(same(G(p, k), G(p, 'cur')) for p in items)
                distinct = not (sp or sc)
                view[S(k)] = {'distinct': distinct, 'sameAs': None if distinct else ('cur' if sc else prev)}
                if distinct:
                    prev = k
            out[S(pk)] = view
        return out
    AV = _AV()
    VINT = OR(G(C, 'vintages'), {})

    def _anch_vis(k):
        if SEQ(k, 'pre'):
            return True
        for e in ARR(G(C, 'periods')):
            pk = per_of(e)[0]
            a = G(AV, pk)
            if T(a) and T(G(a, k)) and T(G(G(a, k), 'distinct')):
                return True
        return False
    ANCH = [k for k in ARR(ANCH_ALL) if _anch_vis(k)]

    def _label_w():
        m = 0
        for sec in sections():
            for it in items_of(sec):
                n = PLUS(LEN(G(it, 'label')), 3 if T(G(it, 'gaapTwin')) else 0)
                if GT(n, m):
                    m = n
        return JMAX(25, JMIN(40, CEIL(m * 0.87) + 1))
    LABEL_W = _label_w()
    LABEL_CAP = math.floor((40 - 1) / 0.87)

    def fitLabel(t, extra=U):
        ex = extra if T(extra) else 0
        n = PLUS(LEN(t), ex)
        if LE(n, LABEL_CAP):
            return t
        return REPL(r'[\s,;:&-]+$', SLICE(STR_ONLY(t), 0, LABEL_CAP - ex - 1), '', count=1) + '…'
    CURC = 2 + len(ANCH)
    DC, PCT, YC, QC, LOWC, HIC, CNTC = CURC + 1, CURC + 2, CURC + 3, CURC + 4, CURC + 5, CURC + 6, CURC + 7
    NCOL = CNTC
    s1 = Sheet()
    r = 1
    RULE_END = NCOL + 7

    def fmtLen(v):
        if not nn(v):
            return 0
        x = N(v)
        if not math.isfinite(x):
            return 0
        with localcontext() as ctx:
            ctx.prec = 1000
            q = Decimal(repr(abs(x))).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)
        ip = format(q, 'f').split('.')[0]
        t = format(int(ip), ',') + '.0'
        return len(t) + (2 if x < 0 else 0)
    maxA = 0
    maxLH = 0
    for sec in sections():
        for it in items_of(sec):
            per = OR(G(it, 'per'), {})
            for pk in (js_keys(per) if isinstance(per, dict) else []):
                p = G(per, pk)
                if not T(p):
                    continue
                for k in ('pre', 't10', 't40', 'cur'):
                    maxA = JMAX(maxA, fmtLen(G(p, k)))
                maxLH = JMAX(maxLH, fmtLen(G(p, 'low')), fmtLen(G(p, 'high')))
    ANCH_W = JMIN(15, JMAX(10, CEIL(maxA * 0.9 + 1.5)))
    LH_W = JMIN(15, JMAX(9, CEIL(maxLH * 0.9 + 0.5)))

    def colW(c):
        if c == 1:
            return LABEL_W + 0.71
        if c <= CURC:
            return ANCH_W
        if c <= PCT:
            return 12
        if c <= QC:
            return 7
        if c <= HIC:
            return LH_W
        return 8 if c == CNTC else 2.5
    if True:
        PXCH = 6.45
        DATEC = NCOL + 2
        compTxt = f"{UP(OR(G(M, 'company'), G(M, 'ticker')))}  ({S(G(M, 'ticker'))})"
        dates = 'Prepared: ' + fmtDateUS(G(M, 'prepared')) + ('  ·  Expected Earnings Date: ' + fmtDateUS(G(M, 'nextPrintDate')) if T(G(M, 'nextPrintDate')) else '')
        avail = 0
        for c in range(1, NCOL + 2):
            avail += colW(c)
        avail = avail * PXCH - 135
        prod = 'Revision Analysis' if (len(compTxt) * 8.7 + 125 + 12 <= avail) else ''
        s1.setHt(r, 26)
        s1.put(r, 1, {'rich': idRich(compTxt, prod), 's': xf({'h': 'left', 'bd': 3})})
        s1.put(r, DATEC, {'rich': mastRich('Prepared:', fmtDateUS(G(M, 'prepared')))
                          + (dtSep + mastRich('Expected Earnings Date:', fmtDateUS(G(M, 'nextPrintDate'))) if T(G(M, 'nextPrintDate')) else ''),
                          's': xf({'f': 0, 'h': 'left', 'bd': 3})})
        for c in range(1, RULE_END + 1):
            if not (s1.rows.get(r) and s1.rows[r].get(c)):
                s1.T(r, c, '', RULE)
        r += 1

    for e in ITER(G(C, 'periods')):
        key, fp = per_of(e)
        hasSeq = SEQ(key, 'q')
        band = f'NEXT QUARTER  -  {S(fp)}' if SEQ(key, 'q') else f'FY{S(fp)}'
        s1.merge(r, 1, NCOL)
        s1.T(r, 1, band, ST['perBand'])
        s1.setHt(r, 20)
        r += 1
        s1.T(r, 1, f'Line Item  ({MF.unitHeader} unless stated)', ST['hL'])
        for i, h in enumerate([AH(k) for k in ANCH] + ['Current']):
            s1.T(r, 2 + i, h, ST['hR'])
        for i, h in enumerate([MF.deltaHeader, '%Δ since print', 'YoY %', 'Seq %' if hasSeq else '', 'Low', 'High', 'Broker cnt']):
            s1.T(r, DC + i, h, ST['hC'])
        s1.setHt(r, 17)
        r += 1
        bView = OR(G(AV, key), {})
        bVint = OR(G(VINT, key), {})

        def collapsed(k, bView=bView):
            return (not SEQ(k, 'pre')) and not (T(G(bView, k)) and T(G(G(bView, k), 'distinct')))

        def sameTxt(k, bView=bView):
            s = G(G(bView, k), 'sameAs') if T(G(bView, k)) else G(bView, k)
            return 'Same as ' + S(OR(G(AHDR, s), 'Current'))
        s1.T(r, 1, '', ST['dtL'])
        for i, k in enumerate(ANCH):
            s1.T(r, 2 + i, sameTxt(k) if collapsed(k) else mmdd(G(bVint, k) if T(G(bVint, k)) else G(G(C, 'cuts'), k)), ST['dt'])
        s1.T(r, CURC, mmdd(OR(G(bVint, 'cur'), G(M, 'updated'))), ST['dt'])
        for c in range(DC, NCOL + 1):
            s1.T(r, c, '', ST['dt'])
        s1.setHt(r, 12)
        r += 1
        for sec in sections():
            items = [it for it in ARR(G(sec, 'items')) if T(G(G(it, 'per'), key))]
            if not items:
                continue
            s1.merge(r, 1, NCOL)
            s1.T(r, 1, UP(G(sec, 'title')), ST['sec'])
            s1.setHt(r, 15)
            r += 1
            band = False
            for it in items:
                p = G(G(it, 'per'), key)
                k = G(it, 'kind')
                isP = G(it, 'isPct')
                d = G(it, 'fav') if G(it, 'fav') is not U else ('up' if SEQ(G(sec, 'key'), 'seg') else dirOf(G(it, 'label')))
                twin = T(G(it, 'gaapTwin'))
                stale = T(G(p, 'stale'))
                lab = fitLabel(G(it, 'label'), 3 if twin else 0)
                s1.T(r, 1, lab, xf({'f': 4, 'fill': 6 if band else 0, 'h': 'left', 'bd': 1, 'ind': 1}) if twin else nmB(band))

                def putV(c, v, em=U):
                    if nn(v):
                        s1.N(r, c, v, vSty(k, band, em))
                    else:
                        s1.T(r, c, '', vSty(k, band, em))
                for i, ak in enumerate(ANCH):
                    if collapsed(ak):
                        s1.T(r, 2 + i, '', blank(band))
                    else:
                        putV(2 + i, G(p, ak))
                if stale and nn(G(p, 'cur')):
                    s1.N(r, CURC, G(p, 'cur'), vStyStale(k, band))
                else:
                    putV(CURC, G(p, 'cur'), True)

                def have(a, b):
                    return nn(G(p, a)) and nn(G(p, b))
                CL = colL(CURC)
                dec = 2 if (SEQ(k, 'eps') or SEQ(k, 'ratio')) else 1
                cur_, pre_ = G(p, 'cur'), G(p, 'pre')
                nil = have('cur', 'pre') and ((ABS(SUB(cur_, pre_) * 10000) < 0.5) if T(isP) else (ABS(SUB(cur_, pre_)) < 0.5 * POW10N(dec)))
                if stale or nil:
                    s1.T(r, DC, '–', dashSty(band))
                    s1.T(r, PCT, '–', dashSty(band))
                else:
                    if not T(isP) and have('cur', 'pre'):
                        s1.Fo(r, DC, f'{CL}{r}-B{r}', dSty(k, band), SUB(cur_, pre_))
                    else:
                        s1.T(r, DC, '', blank(band))
                    pctNil = (not T(isP)) and have('cur', 'pre') and not SEQ(pre_, 0) and ABS(DIV(SUB(cur_, pre_), ABS(pre_))) < 0.0005
                    if pctNil:
                        s1.T(r, PCT, '–', dashSty(band))
                    elif (not T(isP)) and have('cur', 'pre') and SEQ(pre_, 0):
                        s1.T(r, PCT, '', blank(band))
                    elif have('cur', 'pre'):
                        s1.Fo(r, PCT, f'({CL}{r}-B{r})*10000' if T(isP) else f'({CL}{r}-B{r})/ABS(B{r})', pSty(isP, band, False),
                              SUB(cur_, pre_) * 10000 if T(isP) else DIV(SUB(cur_, pre_), ABS(pre_)))
                        if T(d):
                            s1.cf(d, colL(PCT), r)
                    else:
                        s1.T(r, PCT, '', blank(band))
                gCell(r, YC, G(p, 'yoy'), band)
                if hasSeq:
                    gCell(r, QC, G(p, 'seq'), band)
                else:
                    s1.T(r, QC, '', blank(band))
                if nn(G(p, 'low')):
                    s1.N(r, LOWC, G(p, 'low'), vSty(k, band))
                else:
                    s1.T(r, LOWC, '', blank(band))
                if nn(G(p, 'high')):
                    s1.N(r, HIC, G(p, 'high'), vSty(k, band))
                else:
                    s1.T(r, HIC, '', blank(band))
                if nn(G(p, 'cnt')):
                    s1.N(r, CNTC, G(p, 'cnt'), cSty(band))
                else:
                    s1.T(r, CNTC, '', blank(band))
                band = not band
                r += 1
        r += 1
    if T(LEN(OR(G(C, 'scaleNotes'), []))):
        log('   audit notes (not printed):', JOIN(G(C, 'scaleNotes'), ' · '))
    if T(LEN(OR(G(C, 'excluded'), []))):
        log('   excluded rows (not printed):', ' · '.join(f"{S(G(x, 'label'))} [{S(G(x, 'reason'))}]" for x in ARR(G(C, 'excluded'))))


    I = OR(G(C, 'intel'), {})
    NAR = None
    try:
        if T(narrativePath):
            NAR = _load(_js_read_json(narrativePath))
    except Exception:
        elog('narrative.json unreadable — using computed fallback text')

    def NG(k):
        return G(NAR, k) if T(NAR) else NAR

    def _leaves(o):
        if isinstance(o, str):
            yield o
        elif isinstance(o, dict):
            for v in o.values():
                yield from _leaves(v)
        elif isinstance(o, list):
            for v in o:
                yield from _leaves(v)
    NARR_TXT = [x for x in (LOW(REPL(r'\s+', TRIM(t), ' ')) for t in _leaves(NAR)) if len(x) >= 12] if T(NAR) else []

    def _nar_figure_check():
        if not T(NAR):
            return []
        pool = []

        def add(v):
            if _isnum(v) and math.isfinite(v):
                pool.append(abs(v))
        FIGTXT = r'\d[\d,]*(?:\.\d+)?'
        items = []
        for s in ARR(OR(G(C, 'sections'), [])):
            its = OR(G(s, 'items'), [])
            if isinstance(its, list):
                items.extend(its)
            else:
                items.append(its)
        for it in items:
            per = OR(G(it, 'per'), {})
            for p in ([per[k] for k in js_keys(per)] if isinstance(per, dict) else []):
                if not T(p):
                    continue
                for k in ('cur', 'pre', 't10', 't40', 'low', 'high', 'cnt', 'preCnt', 'yoy', 'seq', 'actPrint', 'actYoy', 'actFy0'):
                    add(G(p, k))
                if nn(G(p, 'cur')) and nn(G(p, 'pre')):
                    add(SUB(G(p, 'cur'), G(p, 'pre')))
                    add(SUB(G(p, 'cur'), G(p, 'pre')) * 1e4)
                    if not SEQ(G(p, 'pre'), 0):
                        add(DIV(SUB(G(p, 'cur'), G(p, 'pre')), ABS(G(p, 'pre'))))
                if nn(G(p, 'cur')) and nn(G(p, 'actPrint')):
                    add(SUB(G(p, 'cur'), G(p, 'actPrint')))
                if nn(G(p, 'cur')) and nn(G(p, 'actYoy')):
                    add(SUB(G(p, 'cur'), G(p, 'actYoy')))

        def walk(o):
            if not nn(o):
                return
            if _isnum(o):
                return add(o)
            if isinstance(o, str):
                for m in MATCHALL(FIGTXT, o):
                    add(js_parse_float(m.replace(',', '')))
                return
            if isinstance(o, list):
                for x in o:
                    walk(x)
                return
            if isinstance(o, dict):
                for k in js_keys(o):
                    walk(o[k])
        walk(I)
        labels = [it_l for it_l in (G(it, 'label') for it in items) if T(it_l)]
        labels = js_cmp_sort(labels, lambda a, b: N(LEN(b)) - N(LEN(a)))
        MON = 'January|February|March|April|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec'

        def strip(t):
            s = S(t)
            for l in labels:
                s = s.replace(S(l), ' ')
            s = REPL(rf'\b\d{{1,2}}\s+(?:{MON})\b\.?(?:\s+\d{{2,4}})?', s, ' ')
            s = REPL(rf'\b(?:{MON})\b\.?\s+\d{{1,2}}(?:,?\s+\d{{4}})?\b', s, ' ')
            s = REPL(r'\b\d{4}-\d{2}-\d{2}\b', s, ' ')
            s = REPL(r'\b\d{1,2}\/\d{1,2}(?:\/\d{2,4})?\b', s, ' ')
            s = REPL(r'\bFY\s?\d{2,4}\b', s, ' ', 'i')
            s = REPL(r'\b[1-4]Q\d{2}\b', s, ' ')
            s = REPL(r'\b[12]H\d{2}\b', s, ' ')
            s = REPL(r'\b(?:19|20)\d{2}\b', s, ' ')
            s = REPL(r'\bT\+\d+\b', s, ' ')
            s = REPL(r'S&P\s?500', s, ' ')
            s = REPL(r'\blast \d+ cycles\b', s, ' ', 'i')
            s = REPL(r'\bwithin \d+ days\b', s, ' ', 'i')
            return s
        FIG = rf'({SYMRX})?([+−-])?({SYMRX})?(\d[\d,]*(?:\.\d+)?)\s?(bn|bps|bp|pp|%|x|[mMBkT])?(?![\w])'
        bad = []

        def visit(v, kp):
            if not nn(v):
                return
            if isinstance(v, str):
                for m in RX(FIG).finditer(strip(v)):
                    cur = T(m.group(1) or m.group(3))
                    unit = m.group(5) or ''
                    if not cur and not unit:
                        continue
                    raw = m.group(4).replace(',', '')
                    x = js_parse_float(raw)
                    dec = len((raw.split('.') + [''])[1]) if '.' in raw else 0
                    half = 0.5 * POW10N(dec)
                    cand = []

                    def c(val, tol):
                        cand.append((val, tol))
                    if unit == 'bn' or unit == 'B':
                        c(x * 1000, half * 1000)
                    elif unit == 'm' or unit == 'M':
                        c(x, half)
                        if not cur:
                            c(x * 1e6, half * 1e6)
                    elif unit == 'k':
                        c(x * 1e3, half * 1e3)
                    elif unit == 'T':
                        c(x * 1e6, half * 1e6)
                    elif unit == '%':
                        c(DIV(x, 100), DIV(half, 100))
                        c(x, half)
                    elif unit == 'bp' or unit == 'bps':
                        c(x, half)
                        c(DIV(x, 1e4), DIV(half, 1e4))
                    elif unit == 'pp':
                        c(DIV(x, 100), DIV(half, 100))
                        c(x * 100, half * 100)
                    else:
                        c(x, half)
                    if not any(any(abs(p - val) <= tol + 1e-9 for p in pool) for val, tol in cand):
                        bad.append(f'{kp}: "{TRIM(m.group(0))}"')
                return
            if isinstance(v, list):
                for i, x in enumerate(v):
                    visit(x, f'{kp}[{i}]')
                return
            if isinstance(v, dict):
                for k in js_keys(v):
                    visit(v[k], f'{kp}.{k}' if kp else k)
        visit(NAR, '')
        return bad
    narFigureCheck = _nar_figure_check()
    if narFigureCheck:
        msg = (f'{len(narFigureCheck)} narrative figure(s) are not in the contract (the narrative may cite only computed figures): '
               f'{"; ".join(narFigureCheck[:12])}{" …" if len(narFigureCheck) > 12 else ""}')
        if STRICT_CHECKS:
            elog('Narrative check failed: ' + msg)
            _exit(2)
        log('   ⚠ WARNING: ' + msg)
    P0 = NCOL + 2
    P5 = P0 + 5
    fp0 = G(G(C, 'periods'), 0)
    fp0 = G(fp0, 1)
    rp = 2

    def fPct(v):
        if not nn(v):
            return 'n/a'
        a = ABS(v)
        if a < 0.0005:
            return '0.0%'
        return ('+' if GE(v, 0) else '−') + js_to_fixed(abs(MUL(v, 100)), 1) + '%'

    def _nanfix(f, v, sign):
        if not nn(v):
            return 'n/a'
        x = N(v)
        if x != x:
            return (('+' if False else '−') if sign else '') + MF.sym + 'NaN' + 'm'
        return f(x)

    def fBn(v):
        return _nanfix(MF.level, v, False)

    def fEps(v):
        if not nn(v):
            return 'n/a'
        return MF.sym + js_to_fixed(N(v), 2)

    def fM(v):
        return _nanfix(MF.signed, v, True)
    PW = 136

    def wrapLines(t, w):
        out = []
        cur = ''
        for wd in SPLIT(r'\s+', S(t)):
            if cur and len(cur + ' ' + wd) > w:
                out.append(cur)
                cur = wd
            else:
                cur = cur + ' ' + wd if cur else wd
        if cur:
            out.append(cur)
        return out if out else ['']

    def pLine(txt, st):
        nonlocal rp
        s1.merge(rp, P0, P5)
        s1.T(rp, P0, txt, st)
        rp += 1

    def pBand(t):
        nonlocal rp
        s1.merge(rp, P0, P5)
        s1.T(rp, P0, t, ST['sec'])
        s1.setHt(rp, 17)
        rp += 1

    def noteWrap(full, nrows):
        if nrows > 1 and NARR_TXT:
            f = LOW(REPL(r'\s+', TRIM(S(full)), ' '))
            if any(x in f for x in NARR_TXT):
                LONGLINES.append(TRIM(S(full)))

    def pTxt(t, f=U):
        st = xf({'f': f if f is not U else 0, 'h': 'left'})
        ls = wrapLines(clip(t, CAP['txt']), PW)
        noteWrap(t, len(ls))
        for ln in ls:
            pLine(ln, st)

    def pBul(t, f=U):
        st = xf({'f': f if f is not U else 0, 'h': 'left', 'ind': 1})
        ls = wrapLines(clip(t, CAP['bullet']), PW - 4)
        noteWrap(t, len(ls))
        for i, ln in enumerate(ls):
            pLine(('    ' if i else '•  ') + ln, st)

    def pGap():
        nonlocal rp
        rp += 1

    def item(*lts):
        any_ = None
        for lt in lts:
            for s in sections():
                for it in items_of(s):
                    if not SEQ(G(it, 'lt'), lt) or not T(G(G(it, 'per'), 'q')):
                        continue
                    if not T(G(it, 'gaapTwin')):
                        return it
                    if not T(any_):
                        any_ = it
        return any_

    def qof(it):
        return G(G(it, 'per'), 'q')

    def preof(it):
        if T(it) and nn(G(qof(it), 'cur')) and nn(G(qof(it), 'pre')):
            return SUB(G(qof(it), 'cur'), G(qof(it), 'pre'))
        return None

    PH = OR(G(I, 'phase'), {'key': 'digest', 'label': '', 'window': '', 'question': ''})
    DS0 = G(I, 'dataState')
    NOPOST = T(DS0) and SEQ(G(DS0, 'key'), 'no-post-print-update')
    FLAT = (not NOPOST) and (T(DS0) and SEQ(G(DS0, 'key'), 'ok') and T(G(I, 'breadth'))
                             and SEQ(PLUS(G(G(I, 'breadth'), 'up'), G(G(I, 'breadth'), 'down')), 0))

    def magof(k):
        mg = G(I, 'magnitude')
        return G(mg, k) if T(mg) else mg

    def _head0():
        r_, e_ = magof('rev'), magof('eps')
        return bool(T(r_) and nn(G(r_, 'pct')) and ABS(G(r_, 'pct')) < 0.0005
                    and (not T(e_) or not nn(G(e_, 'pct')) or ABS(G(e_, 'pct')) < 0.0005))
    HEAD0 = _head0()
    CAP = {'thesis': 230, 'bullet': 122, 'txt': 230}
    CLIPPED = []
    LONGLINES = []

    def clip(t, n):
        t = TRIM(S(t))
        if len(t) <= n:
            return t
        cut = t[:n]
        sp = cut.rfind(' ')
        out = REPL(r'[,;:—-]$', cut[:sp] if sp > n * 0.6 else cut, '', count=1) + '…'
        CLIPPED.append({'head': t[:40], 'lost': TRIM(t[len(out) - 1:])})
        return out

    def fy1pct(label):
        for sec in sections():
            for it in items_of(sec):
                if not SEQ(G(it, 'label'), label):
                    continue
                f = G(G(it, 'per'), 'fy1') if T(G(it, 'per')) else G(it, 'per')
                if T(f) and GT(G(f, 'pre'), 0) and GT(G(f, 'cur'), 0):
                    return DIV(G(f, 'cur'), G(f, 'pre')) - 1
        return None

    KQ = None
    MARKS = []
    EXEC_START = 5

    def mark(name, role):
        MARKS.append({'row': rp, 'name': name, 'role': role})
    FCOL = {0: 'FF243141', 1: 'FF12314C', 7: 'FF5B6B7B', 8: 'FF0F2A43', 10: 'FF12314C', 11: 'FF0B7A38', 12: 'FFC0392B', 13: 'FF3A4653'}
    FSZ = {10: 9, 11: 9, 12: 9, 13: 9}

    def RUN(t, b, f):
        return (f'<r><rPr>{"<b/>" if b else ""}<sz val="{FSZ.get(f, 10)}"/><color rgb="{FCOL.get(f, "FF243141")}"/>'
                f'<rFont val="Calibri"/></rPr><t xml:space="preserve">{esc(t)}</t></r>')

    def pLbl(label, body, f=U, bullet=U):
        nonlocal rp
        if TEST(r'\.$', S(label)) and isinstance(body, str) and TEST(r'^[a-z]', body):
            body = body[0].upper() + body[1:]
        lvl = 1 if bullet is True else (bullet if T(bullet) else 0)
        st = xf({'f': f if f is not U else 0, 'h': 'left', 'ind': lvl})
        pre = '–  ' if lvl >= 2 else ('•  ' if lvl else '')
        cont = '   ' if lvl else ''
        lines = wrapLines(label + ' ' + clip(body, CAP['txt']), PW - 4 if lvl else PW)
        noteWrap(S(label) + ' ' + S(body), len(lines))
        for i, ln in enumerate(lines):
            s1.merge(rp, P0, P5)
            if i == 0 and ln.find(label) == 0:
                s1.put(rp, P0, {'rich': RUN(pre + label, True, f) + RUN(ln[len(label):], False, f), 's': st})
            else:
                s1.put(rp, P0, {'rich': RUN((cont if i else pre) + ln, False, f), 's': st})
            rp += 1

    def g2(o, a, b):
        x = G(o, a) if T(o) else o
        return G(x, b) if T(x) else x

    if True:
        mark('EXECUTIVE SUMMARY', 'summary')
        vq0 = G(VINT, 'q')
        asOf = OR(G(vq0, 'cur') if T(vq0) else vq0, G(M, 'updated'))
        s1.merge(rp, P0, P5)
        s1.T(rp, P0, f"{S(G(PH, 'label'))}  ·  {S(G(PH, 'window'))}{('  ·  consensus as of ' + fmtDate(asOf)) if T(asOf) else ''}",
             xf({'f': 16, 'fill': 14, 'h': 'left'}))
        s1.setHt(rp, 19)
        rp += 1
        if True:
            DS, CV = G(I, 'dataState'), G(I, 'coverage')
            noteSt = xf({'f': 12, 'h': 'left'})
            if T(DS) and SEQ(G(DS, 'key'), 'no-post-print-update'):
                pLine(f"No consensus update has been published since the {S(G(M, 'printPeriod'))} print (last vintage {fmtDate(G(DS, 'lastVintage'))}): every revision below is unmeasured, not zero.", noteSt)
            elif T(DS) and SEQ(G(DS, 'key'), 'no-pre-print-anchor'):
                pLine('No pre-print consensus vintage exists inside the 45-day lookback: the since-print revision cannot be measured on this run.', noteSt)
            elif T(DS) and GT(G(DS, 'staleCount'), 0) and T(G(DS, 'rows')) and GE(DIV(G(DS, 'staleCount'), G(DS, 'rows')), 1 / 3):
                pLine(f"{S(G(DS, 'staleCount'))} of {S(G(DS, 'rows'))} lines have not been refreshed since the print (shown as \"–\" and excluded from breadth).", xf({'f': 7, 'h': 'left'}))
            if SEQ(G(PH, 'key'), 'fresh') and T(CV) and T(G(CV, 'thin')):
                pLine(f"Current consensus carries {S(G(CV, 'curCnt'))} of {S(G(CV, 'preCnt'))} pre-print brokers on {S(G(CV, 'label'))}: the moves below are partly a change of sample, not of view.", noteSt)
        pBand('EXECUTIVE SUMMARY')
        th = NG('thesis')
        if not T(th):
            att = OR(g2(I, 'attribution', 'driver'), None)
            r_, e_ = magof('rev'), magof('eps')
            if NOPOST:
                th = f"No consensus update has been published since the {S(G(M, 'printPeriod'))} print, so the since-print revision is unmeasured on this run."
            elif FLAT:
                th = f"{S(fp0)} consensus is unchanged since the {S(G(M, 'printPeriod'))} print: no tracked line has been revised."
            elif HEAD0:
                th = f"{S(fp0)} revenue and EPS consensus are unchanged since the {S(G(M, 'printPeriod'))} print."
            elif T(r_) and nn(G(r_, 'pct')):
                if T(att):
                    sh = G(att, 'share')
                    if nn(sh) and GT(sh, 0) and LE(sh, 1):
                        tail = f", with {S(G(att, 'label'))} {S(js_round(MUL(sh, 100)))}% of the segment-level revision"
                    else:
                        tail = f", led by {S(G(att, 'label'))} at {fM(G(att, 'dollar'))}"
                else:
                    tail = ''
                th = (f"{S(fp0)} revenue consensus moved {fPct(G(r_, 'pct'))} and EPS "
                      f"{fPct(G(e_, 'pct')) if (T(e_) and nn(G(e_, 'pct'))) else 'n/a'} since the {S(G(M, 'printPeriod'))} print{tail}.")
            else:
                th = 'Estimate changes since the print are immaterial.'
        for ln in wrapLines('Result: ' + clip(th, CAP['thesis']), PW):
            pLine(ln, xf({'f': 1, 'h': 'left'}))
        Sm = OR(NG('summary'), {})
        legacy = NG('takeaway')
        if not T(legacy):
            if NOPOST or FLAT:
                legacy = []
            else:
                legacy = [t for t in ARR(OR(G(I, 'readthrough'), []))
                          if not TEST(r'^Since the print the stock has|revision momentum is|^Forward curve:', t)]
        MR = OR(G(Sm, 'marketReaction'), None)
        price = G(I, 'price')
        relv = G(price, 'relative') if (T(price) and nn(G(price, 'relative'))) else None
        mrev = magof('rev')
        revPctH = G(mrev, 'pct') if T(mrev) else None
        contradicts = ((not NOPOST) and nn(relv) and nn(revPctH) and ABS(revPctH) > 0.002
                       and not SEQ(SIGN(relv), SIGN(revPctH)) and ABS(relv) >= 0.03)
        reprices = (not NOPOST) and nn(relv) and ABS(relv) >= 0.15
        mrEarns = (isinstance(MR, str) and T(MR)) or contradicts or reprices
        slots = []
        wc = OR(G(Sm, 'whatChanged'), G(legacy, 0), None)
        wd = OR(G(Sm, 'whatDroveIt'), G(legacy, 1), None)
        qy = OR(G(Sm, 'quality'), G(Sm, 'revisionQuality'), None)
        if T(wc):
            slots.append(('What Changed.', wc, 0))
        if T(wd):
            slots.append(('What Drove It.', wd, 0))
        if T(qy):
            slots.append(('Revision Quality.', qy, 0))
        if mrEarns:
            slots.append(('Market Reaction.', MR if (T(MR) and isinstance(MR, str)) else None, 0))
        for lab, body, f in slots[:3]:
            if lab == 'Market Reaction.':
                if T(MR) and isinstance(MR, (dict, list)) and (T(G(MR, 'positive')) or T(G(MR, 'negative')) or T(G(MR, 'net'))):
                    pLbl(lab, '', f, True)
                    if T(G(MR, 'positive')):
                        pLbl('Positive:', G(MR, 'positive'), 11, 2)
                    if T(G(MR, 'negative')):
                        pLbl('Negative:', G(MR, 'negative'), 12, 2)
                    if T(G(MR, 'net')):
                        pLbl('Net:', G(MR, 'net'), 0, 2)
                elif T(body):
                    pLbl(lab, body, f, True)
                else:
                    r_ = magof('rev')
                    pLbl(lab, f"Shares {fPct(G(G(I, 'price'), 'stock'))} since the print, {fPct(G(G(I, 'price'), 'relative'))} against the index, while "
                              f"{'headline estimates rose' if (T(r_) and nn(G(r_, 'pct')) and GT(G(r_, 'pct'), 0)) else 'headline estimates fell'}.", f, True)
            else:
                pLbl(lab, body, f, True)
        KQ = OR(NG('keyQuestion'), G(PH, 'question'), None)
        if T(KQ):
            pLbl('Key Debate.', KQ, 8, True)
        pGap()
    EXEC_END = rp

    if True:
        mark('ESTIMATE REVISIONS', 'finding')
        pBand('ESTIMATE REVISIONS')
        tka = {'positive': [], 'negative': []} if NOPOST else OR(G(I, 'takeaways'), {'positive': [], 'negative': []})
        ups = SLICE(G(tka, 'positive'), 0, 3)
        dns = SLICE(G(tka, 'negative'), 0, 2)
        if NOPOST:
            pTxt('No revision to curate: no line has been refreshed since the print.', 7)
        else:
            upEmpty = f'No material upgrades identified ({S(fp0)}).'
            ARR(ups)
            upPx = JMAX(0, *[N(PLUS(LEN(t), 3)) * 5.3 for t in ups],
                        N(PLUS(PLUS(10, LEN(fp0)), 3)) * 6 if ups else len(upEmpty) * 5.3) + 25
            R = P0 + 1
            cumPx = 30 * 6.45
            while R < P0 + 3 and cumPx < upPx:
                cumPx += 19 * 6.45
                R += 1
            L, LE_, RE = P0, R - 1, P5
            hdr = xf({'f': 8, 'h': 'left'})
            s1.merge(rp, L, LE_)
            if ups:
                s1.T(rp, L, f'Upgrades ({S(fp0)})', hdr)
            else:
                s1.T(rp, L, upEmpty, xf({'f': 7, 'h': 'left'}))
            s1.merge(rp, R, RE)
            if dns:
                s1.T(rp, R, f'Downgrades ({S(fp0)})', hdr)
            else:
                s1.T(rp, R, f'No material downgrades identified ({S(fp0)}).', xf({'f': 7, 'h': 'left'}))
            rp += 1
            for i in range(max(len(ups), len(dns))):
                s1.merge(rp, L, LE_)
                if T(G(ups, i)):
                    s1.T(rp, L, PLUS('▲  ', G(ups, i)), xf({'f': 11, 'h': 'left'}))
                s1.merge(rp, R, RE)
                if T(G(dns, i)):
                    s1.T(rp, R, PLUS('▼  ', G(dns, i)), xf({'f': 12, 'h': 'left'}))
                rp += 1
        pGap()

        A = G(I, 'attribution')
        GENERIC = r'^(revenue|revenues|data|segment|segments|by|total|sales|business|products?)$'
        MEASURE = r'premium|revenue|income|sales|fees|losses'

        def short(l):
            parts = S(l).split(' - ')
            h, t = parts[0], parts[1:]
            if t and TEST(MEASURE, h, 'i') and not TEST(MEASURE, ' '.join(t), 'i') and TRIM(t[0]):
                return TRIM(t[0])
            if len(h) > 8 or not t:
                return h
            w = ' - '.join(t).split(' ')[0]
            return h if TEST(GENERIC, w, 'i') else TRIM(h + ' ' + w)

        def pick(rowsIn, dir_):
            rs = [x for x in ARR(rowsIn) if ABS(G(x, 'dollar')) >= 0.5]
            rs = js_cmp_sort(rs, lambda a, b: ABS(G(b, 'dollar')) - ABS(G(a, 'dollar')))
            if not rs:
                return [], [], U
            gross = 0
            for x in rs:
                gross = PLUS(gross, ABS(G(x, 'dollar')))
            if T(dir_):
                sg = dir_
            else:
                tot = 0
                for x in rs:
                    tot = PLUS(tot, G(x, 'dollar'))
                sg = -1 if LT(tot, 0) else 1
            shown = []
            cum = 0
            for x in rs:
                if len(shown) >= 3:
                    break
                shown.append(x)
                cum += ABS(G(x, 'dollar'))
                if DIV(cum, gross) >= 0.85:
                    break
            offset = next((x for x in rs if MUL(G(x, 'dollar'), sg) < 0), U)
            if T(offset) and not any(x is offset for x in shown) and DIV(ABS(G(offset, 'dollar')), gross) >= 0.05:
                shown.append(offset)
            rest = [x for x in rs if not any(y is x for y in shown)]
            shown = js_cmp_sort(shown, lambda a, b: MUL(G(b, 'dollar'), sg) - MUL(G(a, 'dollar'), sg))
            return shown, rest, gross
        E = G(I, 'engine')
        Arows = G(A, 'rows') if T(A) else A
        if (not NOPOST and not FLAT
                and ((T(E) and T(LEN(G(E, 'rows'))))
                     or (T(A) and T(Arows) and any(ABS(G(x, 'dollar')) >= 0.5 for x in ARR(Arows))))):
            mark('WHAT DROVE THE REVISION', 'decomposition')
            pTxt('What drove the revision', 8)
            if T(E) and T(LEN(G(E, 'rows'))):
                pTxt(f"Financial drivers: contribution to the {S(fp0)} {short(G(G(E, 'target'), 'label'))} revision", 13)
                for x in ITER(G(E, 'rows')):
                    pLbl(f"{short(G(x, 'label'))}{' impact' if SEQ(G(x, 'kind'), 'ratio') else ''}:", fM(G(x, 'e')), 13, True)
            if T(E) and T(G(E, 'domIsRevenue')) and T(A) and T(G(A, 'rows')) and T(LEN(G(A, 'rows'))):
                shown, rest, gross = pick(G(A, 'rows'), G(A, 'dir'))
                pTxt(f'Revenue mix ({S(fp0)})', 13)
                for x in shown:
                    pLbl(f"{short(G(x, 'label'))}:", fM(G(x, 'dollar')), 13, True)
                restSum = 0
                for x in rest:
                    restSum = PLUS(restSum, G(x, 'dollar'))
                if rest and T(gross) and DIV(ABS(restSum), gross) >= 0.05:
                    pLbl('Other contributors:', f"{fM(restSum)}{' combined' if len(rest) > 1 else ''}", 13, True)
                pos = js_cmp_sort([x for x in shown if GT(G(x, 'dollar'), 0)], lambda a, b: SUB(G(b, 'dollar'), G(a, 'dollar')))
                neg = js_cmp_sort([x for x in shown if LT(G(x, 'dollar'), 0)], lambda a, b: SUB(G(a, 'dollar'), G(b, 'dollar')))
                if pos and neg:
                    if GT(G(pos[0], 'dollar'), ABS(G(neg[0], 'dollar'))):
                        pTxt(f"   {short(G(pos[0], 'label'))} more than offset the reduction in {short(G(neg[0], 'label'))}.", 7)
                    elif GT(ABS(G(neg[0], 'dollar')), G(pos[0], 'dollar')):
                        pTxt(f"   The reduction in {short(G(neg[0], 'label'))} outweighed the gain in {short(G(pos[0], 'label'))}.", 7)
                fy = [{'l': short(G(x, 'label')), 'p': fy1pct(G(x, 'label'))} for x in shown]
                fy = [x for x in fy if nn(x['p']) and abs(x['p']) >= 0.0005]
                if len(fy) >= 2:
                    pTxt(f"FY{S(G(M, 'fy1'))} mix", 13)
                    for x in fy:
                        pLbl(f"{x['l']}:", fPct(x['p']), 13, True)
            elif T(A) and T(G(A, 'rows')) and T(LEN(G(A, 'rows'))):
                srt = js_cmp_sort(list(ARR(G(A, 'rows'))), lambda a, b: ABS(G(b, 'dollar')) - ABS(G(a, 'dollar')))
                top = srt[0] if srt else U
                if T(top) and ABS(G(top, 'dollar')) >= 0.5:
                    pLbl('Revenue mix.', f"{short(G(top, 'label'))} {fM(G(top, 'dollar'))} was the largest {S(fp0)} segment move.", 13, True)
            EV = G(I, 'evidence')
            if T(EV) and T(G(EV, 'rows')) and T(LEN(G(EV, 'rows'))):
                parts = []
                if T(G(EV, 'confirms')) and T(G(G(EV, 'confirms'), 0)):
                    c0 = G(G(EV, 'confirms'), 0)
                    parts.append(f"{S(G(c0, 'label'))} {fPct(G(c0, 'pct'))}")
                if T(G(EV, 'top')):
                    parts.append(f"{S(G(G(EV, 'top'), 'label'))} {fPct(G(G(EV, 'top'), 'pct'))}")
                if parts:
                    pLbl(f'Forward evidence ({S(fp0)}):', '  |  '.join(parts), 13, True)
            mfcf = magof('fcf')
            fd = G(mfcf, 'dollar') if T(mfcf) else None
            cx = item('CF_P_109')
            cd = preof(cx) if T(cx) else None
            ox = item('CF_P_108')
            od = preof(ox) if T(ox) else None
            if nn(fd) and LT(fd, 0):
                spend = None if not nn(cd) else ((-cd) if (LT(G(qof(cx), 'cur'), 0) or LT(G(qof(cx), 'pre'), 0)) else cd)
                parts = []
                if nn(od):
                    parts.append(f'OCF {fM(od)}')
                if nn(spend):
                    parts.append(f'capex spend {fM(spend)}')
                parts.append(f'FCF {fM(fd)}')
                leg = ''
                if nn(spend) and nn(od):
                    if spend > 0 and od >= 0:
                        leg = 'The cut sits in capital spending, not operating cash flow.'
                    elif od < 0 and spend <= 0:
                        leg = 'The cut sits in operating cash flow, not capital spending.'
                    elif od < 0 and spend > 0:
                        leg = 'Both operating cash flow and capital spending moved against FCF.'
                    else:
                        leg = 'The cut is not explained by OCF or capex on the sheet.'
                elif nn(spend) and spend > 0:
                    leg = 'Higher capital spending carries the cut.'
                elif nn(od) and od < 0:
                    leg = 'Lower operating cash flow carries the cut.'
                pLbl(f'Cash flow ({S(fp0)}).', TRIM(f"{', '.join(parts)}. {leg}"), 13, True)
            pGap()

        RP = None if NOPOST else G(I, 'revisionPath')
        if T(RP) and T(G(RP, 'dominant')) and nn(G(RP, 'headline')):
            mark('REVISION PATH', 'behaviour')
            pTxt(f"Revision path ({S(fp0)}): {S(G(RP, 'dominant'))}", 8)
            fl = ABS(G(RP, 'headline'))
            broad = GE(G(RP, 'agree'), 3) and GE(DIV(G(RP, 'agree'), G(RP, 'of')), 0.6)
            if SEQ(G(RP, 'key'), 'Reset & Hold') or SEQ(G(RP, 'dominant'), 'Reset & Hold'):
                flTxt = ('The full reset landed within 10 days of the print' if fl >= 0.98
                         else f'{S(js_round(JMIN(fl, 1) * 100))}% of the reset landed within 10 days of the print')
                pBul(f"{flTxt} and has {'broadly held' if broad else 'held on the headline line'} since.", 13)
            elif T(G(RP, 'note')):
                note = G(RP, 'note')
                pBul(f"{PLUS(UP(G(note, 0)), SLICE(note, 1))}{' across the core metrics' if broad else ''}.", 13)
            CORE_PATH = ('Revenue', 'EPS', 'EBITDA')
            odd = [x for x in ARR(OR(G(RP, 'rows'), []))
                   if not SEQ(G(x, 'key'), G(RP, 'dominant')) and isinstance(G(x, 'name'), str) and G(x, 'name') in CORE_PATH]
            if len(odd) == 1:
                pLbl('Exception.', f"On {S(G(odd[0], 'name'))}, {S(G(odd[0], 'note'))}.", 13, True)
            pGap()

        mark('WHAT CONSENSUS EMBEDS', 'threshold')
        pTxt('What consensus embeds', 8)
        rv = item('IS_P_101', 'IS_P_102', 'IS_P_200', 'IS_P_403', 'IS_P_402')
        ep = item('IS_S_657', 'IS_P_128', 'IS_P_127')
        if T(rv) and T(qof(rv)):
            y, q = G(qof(rv), 'yoy'), G(qof(rv), 'seq')
            if nn(y) or nn(q):
                pLbl(f'{S(fp0)} Revenue Growth:', f"{(fPct(y) + ' Y/Y') if nn(y) else ''}{'  |  ' if (nn(y) and nn(q)) else ''}{(fPct(q) + ' Q/Q') if nn(q) else ''}", 13, True)
            if nn(G(qof(rv), 'cur')):
                pLbl(f'{S(fp0)} Revenue:', f"{fBn(G(qof(rv), 'cur'))}{('  |  EPS ' + fEps(G(qof(ep), 'cur'))) if (T(ep) and nn(G(qof(ep), 'cur'))) else ''}", 13, True)
        if (not NOPOST and T(G(I, 'engine')) and T(LEN(G(G(I, 'engine'), 'rows')))
                and not T(G(G(I, 'engine'), 'domIsRevenue'))):
            lvl = []

            def findByLabel(lb):
                for s2 in sections():
                    for it in items_of(s2):
                        if SEQ(G(it, 'label'), lb) or SEQ(REPL(r'\s*\(%\)\s*$', STR_ONLY(G(it, 'label')), '', count=1), lb):
                            return it
                return None
            for x in SLICE(ARR(G(G(I, 'engine'), 'rows')), 0, 3):
                if len(lvl) >= 2:
                    break
                if T(rv) and SEQ(G(x, 'label'), G(rv, 'label')):
                    continue

                def _oc(k):
                    v = NG(k)
                    if not T(v):
                        return ''
                    return S(OR(G(v, 'threshold'), '')) if isinstance(v, (dict, list)) else S(v)
                outcomes = ' '.join(_oc(k) for k in ('base', 'extend', 'challenge'))

                def already(f, outcomes=outcomes):
                    return f in outcomes
                gm = G(G(I, 'derived'), 'Gross margin') if T(G(I, 'derived')) else G(I, 'derived')
                if TEST(r'\bcogs\b|cost of', G(x, 'label'), 'i') and T(gm) and T(G(gm, 'q')):
                    f = js_to_fixed(MUL(G(G(gm, 'q'), 'cur'), 100), 1) + '%'
                    if not already(f):
                        lvl.append(f'Gross margin {f}')
                    continue
                it = findByLabel(G(x, 'label'))
                if not T(it) or not T(qof(it)) or not nn(G(qof(it), 'cur')):
                    continue
                if T(G(it, 'isPct')):
                    f = js_to_fixed(MUL(G(qof(it), 'cur'), 100), 1) + '%'
                    if not already(f):
                        lvl.append(f"{S(G(x, 'label'))} {f}")
                else:
                    f = fBn(G(qof(it), 'cur'))
                    if not already(f):
                        lvl.append(f"{short(G(x, 'label'))} {f}")
            if lvl:
                pLbl('Key levers:', '  |  '.join(lvl), 13, True)
        cv = None if NOPOST else G(I, 'curve')
        if T(cv) and T(G(cv, 'rev')) and T(LEN(G(cv, 'rev'))):
            def fpLab(f):
                return ('FY' + S(f)) if TEST(r'^\d{4}$', S(f)) else S(f)
            withData = [x for x in ARR(G(cv, 'rev')) if nn(G(x, 'pct'))]
            parts = [f"{fpLab(G(x, 'fp'))} {fPct(G(x, 'pct'))}" for x in withData]
            far = withData[-1] if withData else U
            near = withData[0] if withData else U
            if len(parts) >= 2:
                pLbl('Revision by horizon:', '  |  '.join(parts), 13, True)
                sg = SIGN(G(near, 'pct')) if (T(near) and T(G(near, 'pct'))) else 0
                if (T(far) and T(near) and nn(G(far, 'pct')) and nn(G(near, 'pct')) and T(sg) and SEQ(SIGN(G(far, 'pct')), sg)
                        and ABS(G(far, 'pct')) > ABS(G(near, 'pct')) * 1.5 and ABS(G(far, 'pct')) - ABS(G(near, 'pct')) >= 0.005):
                    pLbl('Read-through:', 'The revision strengthens further out.' if sg > 0 else 'The cut deepens further out.', 7, True)
        H2 = None if NOPOST else G(I, 'history')
        if T(H2) and G(H2, 'uniformBasis') is not False and (nn(G(H2, 'curEps')) or nn(G(H2, 'curRev'))):
            def hvals(key):
                return [v for v in (G(x, key) for x in ARR(OR(G(H2, 'rows'), []))) if nn(v)]

            def rk(cur, key):
                vals = hvals(key)
                if not nn(cur) or len(vals) < 4:
                    return None
                a = ABS(cur)
                beats = len([v for v in vals if a > ABS(v)])
                return {'rank': len(vals) - beats + 1, 'tot': len(vals) + 1}
            rr = rk(G(H2, 'curRev'), 'rev')
            re_ = rk(G(H2, 'curEps'), 'eps')

            def ext(x):
                return bool(x and (x['rank'] == 1 or x['rank'] == x['tot']))
            if (ext(rr) and ext(re_) and nn(G(H2, 'medRev')) and nn(G(H2, 'medEps'))
                    and T(G(H2, 'medRev')) and T(G(H2, 'medEps'))):
                def gap(cur, med):
                    v = DIV(JMAX(1e-6, ABS(cur)), ABS(med))
                    return abs(math.log(v) if (v == v and v > 0) else (math.nan if v != v or v < 0 else -math.inf))
                if gap(G(H2, 'curEps'), G(H2, 'medEps')) > gap(G(H2, 'curRev'), G(H2, 'medRev')):
                    rr = None

            def flip(cur, key):
                vals = hvals(key)
                return nn(cur) and ABS(cur) > 0.0005 and len(vals) >= 4 and all(SEQ(SIGN(v), -SIGN(cur)) for v in vals)

            def line(x, cur, med, name):
                if flip(cur, 'rev' if name == 'revenue' else 'eps'):
                    return f"the only post-print {'cut' if LT(cur, 0) else 'raise'} to {name} in the last {x['tot']} cycles (prior-cycle median {fPct(med)})"
                return (f"an unusually strong repricing for this name, with the largest {name} revision of the last {x['tot']} post-print cycles (prior-cycle median {fPct(med)})"
                        if x['rank'] == 1 else
                        f"the Street's weakest post-print response in the sample, with the smallest {name} revision of the last {x['tot']} cycles (prior-cycle median {fPct(med)})")
            if rr and (rr['rank'] == 1 or rr['rank'] == rr['tot']):
                pLbl('Cycle rank:', line(rr, G(H2, 'curRev'), G(H2, 'medRev'), 'revenue'), 13, True)
            elif re_ and (re_['rank'] == 1 or re_['rank'] == re_['tot']):
                pLbl('Cycle rank:', line(re_, G(H2, 'curEps'), G(H2, 'medEps'), 'EPS'), 13, True)
        BC = G(I, 'boundContradiction')
        DV = None if NOPOST else G(OR(G(I, 'divergence'), []), 0)
        if T(DV) and not (T(BC) and (SEQ(G(BC, 'key'), 'crosshorizon') or SEQ(G(BC, 'subject'), G(DV, 'label')))):
            tail = G(DV, 'tail')
            if not T(tail):
                fav = G(DV, 'fav')
                if SEQ(fav, 'down'):
                    w = 'The improvement' if LT(G(DV, 'q'), 0) else 'The deterioration'
                elif SEQ(fav, 'up'):
                    w = 'The improvement' if GT(G(DV, 'q'), 0) else 'The deterioration'
                else:
                    w = "The quarter's move"
                tail = w + ' does not carry through the full year.'
            pLbl(f"{S(G(DV, 'label'))} Divergence:", f"{fPct(G(DV, 'q'))} for {S(fp0)} against {fPct(G(DV, 'fy1'))} for FY{S(G(M, 'fy1'))}. {S(tail)}", 13, True)
        pGap()

    if True:
        mark('KEY DEBATE: VARIANT VIEW', 'insight')
        pBand('KEY DEBATE: VARIANT VIEW')
        street = OR(OR(NG('street'), G(OR(NG('bull'), []), 0)) if T(NAR) else NAR, None)
        vchange = OR(NG('variantChange'), None)
        vart = OR(OR(NG('variant'), G(OR(NG('bear'), []), 0)) if T(NAR) else NAR, None)
        assess = OR(OR(NG('assessment'), NG('variantAssessment')) if T(NAR) else NAR, None)
        CD = G(I, 'contradiction')

        if T(street):
            pLbl('Street View.', street, 10)
        elif NOPOST:
            pLbl('Street View.', 'the pre-print consensus still stands: no broker has re-based the estimates since the release.', 10)
        elif FLAT:
            pLbl('Street View.', 'the pre-print consensus still stands: brokers have refreshed their estimates since the release without revising them.', 10)
        else:
            att = OR(g2(I, 'attribution', 'driver'), None)
            if T(att):
                sh = G(att, 'share')
                share_txt = (S(js_round(MUL(sh, 100))) + '% of the segment move') if (nn(sh) and GT(sh, 0) and LE(sh, 1)) else fM(G(att, 'dollar'))
                pLbl('Street View.', f"The {'revision' if GT(G(att, 'dollar'), 0) else 'cut'} is carried by {S(G(att, 'label'))} ({share_txt}).", 10)

        if T(vchange):
            pLbl('What Changed.', vchange, 0)
        elif NOPOST:
            pLbl('What Changed.', 'nothing in the estimates yet: the release has not been reflected in any consensus line.', 0)
        elif FLAT:
            pLbl('What Changed.', f"nothing in the estimates: no tracked line has been revised since the {S(G(M, 'printPeriod'))} print.", 0)
        elif T(CD) and T(G(CD, 'binding')):
            pLbl('What Changed.', G(CD, 'text'), 0)
        else:
            r_, e_ = magof('rev'), magof('eps')
            if HEAD0:
                pLbl('What Changed.', f"{S(fp0)} revenue and EPS consensus are unchanged since the {S(G(M, 'printPeriod'))} print.", 0)
            elif T(r_) and nn(G(r_, 'pct')):
                pLbl('What Changed.', f"{S(fp0)} revenue consensus {fPct(G(r_, 'pct'))} and EPS {fPct(G(e_, 'pct')) if (T(e_) and nn(G(e_, 'pct'))) else 'n/a'} since the {S(G(M, 'printPeriod'))} print.", 0)

        if T(vart):
            pLbl('Potential Variant.', vart, 12)
        elif NOPOST or FLAT:
            pass
        else:
            D = G(I, 'disagreement')
            t = g2(D, 'thin', 0) if T(D) else D
            if T(t):
                pLbl('Potential Variant.', f"the extension of the revision rests on {S(G(t, 'label'))}, which the Street covers far less precisely than the headline lines.", 12)

        VCANON = {'supported': 'Supported', 'partially supported': 'Partially Supported',
                  'partly supported': 'Partially Supported', 'not supported': 'Not Supported',
                  'insufficient evidence': 'Insufficient Evidence'}
        if T(assess):
            V = OR(NG('verdict'), None)
            m = RX(r'^(Supported|Partially Supported|Partly supported|Not Supported|Insufficient Evidence)[.:]?\s*', 'i').search(S(assess))
            verdict = None
            if T(V):
                verdict = OR(VCANON.get(REPL(r'[.:]\s*$', LOW(S(V)), '', count=1), U), V)
            if not T(verdict):
                verdict = (OR(VCANON.get(LOW(m.group(1)), U), m.group(1)) if m else None)
            body = S(assess)[len(m.group(0)):] if m else S(assess)
            pLbl('Variant Assessment.', ((S(verdict) + '. ') if T(verdict) else '') + body, 1)
        else:
            pLbl('Variant Assessment.', 'Insufficient Evidence. The current cycle does not resolve the variant either way.', 1)
        pGap()

    if True:
        mark('EARNINGS EXPECTATIONS', 'outcome')
        pBand('EARNINGS EXPECTATIONS')

        def outcome(lab, val, f):
            if not T(val):
                return
            if isinstance(val, str):
                pLbl(lab + '.', val, f)
                return
            pTxt(lab, 8 if f == 1 else (11 if f == 11 else 12))
            if T(G(val, 'threshold')):
                pTxt('   ' + REPL(r'[.\s]*$', S(G(val, 'threshold')), '', count=1) + '.', 13)
            if T(G(val, 'implication')):
                pTxt(PLUS('   ', G(val, 'implication')), 7)
        base = OR(NG('base'), G(OR(NG('bullNeeds'), []), 0), None)
        ext_ = OR(NG('extend'), None)
        chal = OR(NG('challenge'), G(OR(NG('bearIf'), []), 0), None)
        outcome('Base', base, 1)
        outcome('Extend', ext_, 11)
        outcome('Challenge', chal, 12)
        if not T(base) and not T(ext_) and not T(chal) and not NOPOST:
            rvI = item('IS_P_101', 'IS_P_102', 'IS_P_200', 'IS_P_403', 'IS_P_402')
            epI = item('IS_S_657', 'IS_P_128', 'IS_P_127')
            rvL = f"revenue of {fBn(G(qof(rvI), 'cur'))}" if (T(rvI) and nn(G(qof(rvI), 'cur'))) else None
            epL = f"EPS of {fEps(G(qof(epI), 'cur'))}" if (T(epI) and nn(G(qof(epI), 'cur'))) else None
            lv = [x for x in (rvL, epL) if T(x)]
            if lv:
                pLbl('Base.', f"results at or above the {'current' if FLAT else 'revised'} {S(fp0)} consensus, {' and '.join(lv)}.", 1)
                pLbl('Challenge.', f"{' or '.join(x.replace(' of ', ' below ', 1) for x in lv)} for {S(fp0)}, the base the consensus now embeds.", 12)
            else:
                pLbl('Base.', f"results and guidance at or above the {'current' if FLAT else 'revised'} consensus levels.", 1)
                pLbl('Challenge.', 'guidance below the base embedded in the next quarter, or the primary driver softening.', 12)
        elif not T(base) and not T(ext_) and not T(chal):
            pLbl('Base.', 'the first broker refresh re-bases estimates in line with the release.', 1)
            pLbl('Challenge.', 'the first refresh moves the headline lines against the pre-print consensus.', 12)
        pGap()

    if True:
        mark('KEY MONITORING VARIABLES', 'action')
        pBand('KEY MONITORING VARIABLES')
        ag = OR(NG('agenda'), [])

        def row(i, a):
            fact = OR(G(a, 'fact'), G(a, 'why'), None)
            mon = OR(G(a, 'monitor'), G(a, 'watch'), G(a, 'question'), None)
            pTxt(f"{i}.  {S(G(a, 'title'))}", 8)
            if T(fact):
                pLbl('Fact:', fact, 13, True)
            if T(mon):
                pLbl('Monitor:', mon, 7, True)
        if T(LEN(ag)):
            for i, a in enumerate(ARR(SLICE(ag, 0, 3))):
                row(i + 1, a)
        elif FLAT:
            vq1 = G(VINT, 'q')
            row(1, {'title': 'First post-print revision', 'fact': f"the {S(fp0)} consensus is unchanged since the {S(G(M, 'printPeriod'))} print on every tracked line (latest vintage {fmtDate(OR(G(vq1, 'cur') if T(vq1) else vq1, G(M, 'updated')))}).", 'monitor': 'the first broker revision, and whether it moves the headline lines or confirms the pre-print consensus.'})
        elif NOPOST:
            row(1, {'title': 'First post-print consensus vintage', 'fact': f"the latest consensus vintage is {fmtDate(G(G(I, 'dataState'), 'lastVintage'))}, before the {S(G(M, 'printPeriod'))} print.", 'monitor': 'the first broker refresh, and whether it re-bases the headline lines or leaves the pre-print consensus standing.'})
        else:
            n = 1
            att = OR(g2(I, 'attribution', 'driver'), None)
            if T(att) and ABS(G(att, 'dollar')) >= 0.5:
                row(n, {'title': G(att, 'label'), 'fact': f"it {'added' if GT(G(att, 'dollar'), 0) else 'took'} {fM(ABS(G(att, 'dollar')))[1:]} {'to' if GT(G(att, 'dollar'), 0) else 'from'} the quarterly revision, the largest single contributor.", 'monitor': 'whether results and guidance support the revised level.'})
                n += 1
            D = G(I, 'disagreement')
            t = g2(D, 'thin', 0) if T(D) else D
            if T(t):
                row(n, {'title': G(t, 'label'), 'fact': f"the revision leans on {S(G(t, 'label'))}, revised {fPct(G(t, 'move')) if nn(G(t, 'move')) else 'materially'} and the least settled assumption in the model.", 'monitor': 'management commentary that firms up this assumption.'})
                n += 1
            RP2 = G(I, 'revisionPath')
            if T(RP2) and T(G(RP2, 'dominant')) and nn(G(RP2, 'headline')):
                row(n, {'title': 'Revision durability',
                        'fact': (f"the reset landed inside 10 days of the print and has held, on a {S(G(RP2, 'dominant'))} path."
                                 if SEQ(G(RP2, 'dominant'), 'Reset & Hold') else
                                 f"the {S(fp0)} revision is on a {S(G(RP2, 'dominant'))} path{(': ' + S(G(RP2, 'note'))) if T(G(RP2, 'note')) else ''}."),
                        'monitor': 'whether the print produces another reset or validates the current base.'})
                n += 1

    if True:
        warn = []

        def roleAt(r_):
            cur = 'summary'
            for m in MARKS:
                if r_ >= m['row']:
                    cur = m['role']
                else:
                    break
            return cur
        lines = []
        for rr_ in sorted(s1.rows):
            row_ = s1.rows[rr_]
            for cc in sorted(row_):
                if cc < P0:
                    continue
                v = row_[cc]
                if nn(v.get('rich')):
                    txt = (re.sub(r'<[^>]+>', '', S(v['rich'])).replace('&amp;', '&').replace('&lt;', '<')
                           .replace('&gt;', '>').replace('&quot;', '"'))
                else:
                    txt = v['v'] if (v.get('t') == 's' and isinstance(v.get('v'), str)) else None
                if T(txt) and T(TRIM(txt)):
                    lines.append({'r': rr_, 'role': roleAt(rr_), 'txt': txt})
        sumN = len([l for l in lines if l['r'] < EXEC_END])
        log('   panel lines:', len(lines), f'(summary {sumN} / detail {len(lines) - sumN})')

        subs = []
        for s in sections():
            for it in items_of(s):
                subs.append(G(it, 'label'))
                h = S(G(it, 'label')).split(' - ')[0]
                if not SEQ(h, G(it, 'label')):
                    subs.append(h)
        der = OR(G(I, 'derived'), {})
        for k in (js_keys(der) if isinstance(der, dict) else ([str(i) for i in range(len(der))] if isinstance(der, (list, str)) else [])):
            subs.append(k)
        subs += ['revenue', 'EPS', 'EBITDA', 'EBIT', 'FCF', 'margin', 'shares', 'the index', 'consensus']
        subs = js_cmp_sort(subs, lambda a, b: N(LEN(b)) - N(LEN(a)))
        claims = {}
        CLAIM_RX = rf'[+−-]?(?:{SYMRX})?[\d,]+(?:\.\d+)?\s?(?:bn|bps|bp|pp|m|M|%)(?![a-z])'
        subs_low = [LOW(S(s)) for s in subs]
        for L in lines:
            if L['r'] < EXEC_END:
                continue
            low = LOW(L['txt'])
            subj = U
            for s, sl in zip(subs, subs_low):
                if sl in low:
                    subj = s
                    break
            if not T(subj):
                continue
            for f in MATCHALL(CLAIM_RX, L['txt']):
                k = f"{S(subj)}|{REPL(r'\s+', f, '')}"
                claims.setdefault(k, {})[L['role']] = True
        overs = [(k, v) for k, v in claims.items() if len(v) >= 3]
        if overs:
            warn.append('claim restated across 3+ roles without transformation: ' + '; '.join(f"{k} ({' -> '.join(v)})" for k, v in overs))

        execText = LOW(' '.join(l['txt'] for l in lines if l['r'] < EXEC_END))
        DSx = G(I, 'dataState')
        if T(DSx) and T(G(DSx, 'key')) and not SEQ(G(DSx, 'key'), 'ok'):
            spineReq = []
        else:
            tsp = G(I, 'thesisSpine')
            spineReq = OR(G(tsp, 'required') if T(tsp) else tsp, [])
        for req in ITER(spineReq):
            hit = any(T(p) and LOW(S(p)) in execText for p in ARR(OR(G(req, 'probe'), [])))
            if not hit:
                warn.append(f"executive summary does not reference {S(G(req, 'need'))} — the spine says it is central")
        if True:
            CDb = G(I, 'boundContradiction')
            v = LOW(' '.join(S(x) for x in (NG('variantChange'), NG('variant'), NG('assessment')) if T(x)))
            if T(CDb) and v and not any(T(p) and LOW(S(p)) in v for p in ARR(OR(G(CDb, 'probe'), []))):
                warn.append(f"variant argues something other than the dominant contradiction ({S(G(CDb, 'subject'))}, score {FIXED(G(CDb, 'score'), 2)})")
        MEAS = rf'({SYMRX}[\d,]|[\d,.]+\s?(?:bn|bp|%|M\b))'
        for f in ('base', 'extend', 'challenge'):
            raw = NG(f)
            t = (f"{S(OR(G(raw, 'threshold'), ''))} {S(OR(G(raw, 'implication'), ''))}"
                 if (T(raw) and isinstance(raw, (dict, list))) else raw)
            if T(t) and not TEST(MEAS, t):
                warn.append(f'{f} names no measurable threshold — state the consensus number the outcome turns on')
        if True:
            CDb = G(I, 'boundContradiction')
            if T(CDb):
                def _oc2(k):
                    v_ = NG(k)
                    if not T(v_):
                        return ''
                    if isinstance(v_, (dict, list)):
                        return f"{S(OR(G(v_, 'threshold'), ''))} {S(OR(G(v_, 'implication'), ''))}"
                    return S(v_)
                oc = LOW(' '.join(_oc2(k) for k in ('base', 'extend', 'challenge')))
                if TRIM(oc) and not any(T(p) and LOW(S(p)) in oc for p in ARR(OR(G(CDb, 'probe'), []))):
                    warn.append(f"no Base/Extend/Challenge outcome resolves the Variant ({S(G(CDb, 'subject'))}) — at least one threshold must test the contradiction the Variant argues")
        if True:
            asmt = S(OR(NG('assessment'), ''))
            if TEST(r'\b(shares?|the stock|index|de-?rated?|outperform\w*|underperform\w*)\b', asmt, 'i'):
                warn.append('variant assessment cites market performance: the verdict must rest on evidence for the variant, not on how the shares traded')
            if TEST(r'\b(broker|coverage|dispersion|underwritten by)\b', asmt, 'i'):
                warn.append('variant assessment cites coverage: state the evidence for or against the variant, not how well the line is covered')
        if True:
            ag = OR(NG('agenda'), [])
            vague = [a for a in ARR(ag) if not TEST(MEAS, f"{S(OR(G(a, 'fact'), G(a, 'why'), ''))} {S(OR(G(a, 'monitor'), G(a, 'watch'), ''))}")]
            if len(vague) >= 2:
                warn.append(f'{len(vague)} watch items name no measurable or observable test — a watch item is a variable and a test, not a topic')
            if True:
                STOPT = {'the', 'and', 'for', 'with', 'quarter', 'quarterly', 'annual', 'annually', 'next', 'post', 'print', 'key', 'watch', 'core', 'total'}
                for it in ITER(OR(NG('agenda'), [])):
                    if not T(it) or not T(G(it, 'title')):
                        continue
                    words = [w for w in SPLIT(r'[^a-z0-9-]+', LOW(S(G(it, 'title')))) if len(w) >= 3 and w not in STOPT]
                    if not words:
                        continue
                    body = LOW(S(OR(G(it, 'fact'), G(it, 'why'), '')) + ' ' + S(OR(G(it, 'monitor'), G(it, 'watch'), '')))
                    if not any(w in body for w in words):
                        warn.append(f"watch item \"{S(G(it, 'title'))}\" names a metric its Fact/Monitor do not test — retitle it to the monitored metric")
        if True:
            blocks = []
            bandEnd = MARKS[0]['row'] if MARKS else EXEC_START
            firstBand = next((l for l in lines if TEST(r'^EXECUTIVE SUMMARY$', TRIM(l['txt']))), None)
            proseStart = firstBand['r'] if firstBand else max(EXEC_START, bandEnd)

            def isHead(t):
                x = TRIM(t)
                if not x:
                    return False
                if not TEST(r'[a-z]', x):
                    return True
                return len(SPLIT(r'\s+', x)) <= 2 and not TEST(r'\d', x)
            for L in lines:
                if L['r'] < EXEC_START or L['r'] < proseStart:
                    continue
                prev = blocks[-1] if blocks else None
                isNew = (not prev or L['role'] != prev['role'] or isHead(prev['txt']) or isHead(L['txt'])
                         or TEST(r'^\s*(•|▲|▼|\d+\.\s)', L['txt']))
                if isNew:
                    blocks.append({'r': L['r'], 'role': L['role'], 'txt': L['txt']})
                else:
                    blocks[-1]['txt'] += ' ' + REPL(r'^\s+', L['txt'], '', count=1)
            prose = blocks
            em = [l for l in prose if '—' in l['txt']]
            if em:
                warn.append(f'{len(em)} panel line(s) contain an em dash (house rule: commas, colons or periods): "{em[0]["txt"][:58]}"')
            semi = [l for l in prose if ';' in l['txt']]
            if semi:
                warn.append(f'{len(semi)} panel line(s) contain a semicolon (house rule: split the sentence): "{semi[0]["txt"][:58]}"')
            BANNED = [r'\battractive\b', r'\bcompelling\b', r'\bundervalued\b', r'\bovervalued\b', r'investors should', r'\boverweight\b', r'\bunderweight\b', r'price target', r'upside potential', r'\bbuy\b', r'\bsell\b',
                      r'future optionality', r'investors believe', r'the market thinks', r'premium valuation', r'attractive multiple', r'cheap valuation', r'rich valuation', r'priced for perfection', r'premium to peers', r'discount to peers']
            for L in prose:
                for rx in BANNED:
                    if TEST(rx, L['txt'], 'i'):
                        warn.append(f'banned lexicon /{rx}/i in: "{L["txt"][:58]}"')
                        break
            MOVE = r'\b(beat|beats|missed|exceeded|expand\w*|contract\w*|improv\w*|deteriorat\w*|accelerat\w*|decelerat\w*|slow\w*|surg\w*|plung\w*|jump\w*|rose|fell|grew|declin\w*|rising|falling|higher|lower|stronger|weaker|doubled|record)\b'
            for L in prose:
                if (len(SPLIT(r'\s+', L['txt'])) >= 4 and TEST(MOVE, L['txt'], 'i') and not TEST(r'\d', L['txt'])
                        and not TEST(r'^Variant Assessment\.', L['txt'])):
                    warn.append(f'unquantified movement claim (add the number): "{L["txt"][:58]}"')
            for L in prose:
                for sen in SPLIT(r'(?<=[.!?])\s+', L['txt']):
                    if len(SPLIT(r'\s+', sen)) > 30:
                        warn.append(f'sentence over 30 words: "{sen[:58]}"')
                        break
            MACHINERY = [
                (r'\b\d+\s+of\s+\d+\s+brokers?\b', 'broker count'),
                (r'\b\d+(-|\s+)(brokers?|analysts?)\b', 'broker count'),
                (r'\bthin coverage\b|\bcoverage from\b|\b(one|single) analyst\b', 'coverage statistic'),
                (r'\b\d+\s+of\s+\d+\s+(core\s+)?metrics?\b', 'agreement count'),
                (r'\b\d+\s+of\s+\d+\s+tracked\b', 'breadth count'),
                (r'broker (range|coverage|count)', 'coverage statistic'),
                (r'\b(unchanged|thin|deep) broker\b', 'coverage statistic'),
                (r'\(\s*\d{3,}%[^)]*offset', 'contribution share over 100%'),
                (r'\bweighted breadth\b', 'breadth mechanic'),
                (r'\b\d+\s+(line|item|row)s?\s+(excluded|filtered|dropped|suppressed)', 'exclusion count'),
                (r'\b(excluded|filtered out|dropped) (from|because)\b', 'filtering decision'),
                (r'\b(coverage|data|dataset|the sample)\b[^.]{0,24}\b(thin|limited|sparse|incomplete|too few)\b', 'dataset limitation'),
                (r'\bthe (model|engine|script) (excluded|selected|ranked|filtered)\b', 'engine narration'),
            ]
            variantTxt = PLUS(PLUS(OR(NG('variant'), ''), ' '), OR(NG('variantChange'), ''))
            for L in prose:
                if L['role'] == 'insight' and T(variantTxt) and TEST(r'(broker|coverage|dispersion|range)', variantTxt, 'i'):
                    continue
                for rx, what in MACHINERY:
                    if TEST(rx, L['txt'], 'i'):
                        warn.append(f'visible {what} (keep it in the engine): "{L["txt"][:58]}"')
                        break
            FIGRX = rf'(?<![\w.])[+−-]?(?:{SYMRX})?\d[\d,]*(?:\.\d+)?\s?(?:bps|bp|pp|%|bn|[BMTkm])(?![\w])'

            def isDataRow(t):
                return TEST(r'^[•–\s]*[^.]{0,44}:\s', t) or '  |  ' in t
            for L in prose:
                if isDataRow(L['txt']):
                    continue
                for sen in SPLIT(r'(?<=[.!?])\s+', L['txt']):
                    n = len(MATCHALL(FIGRX, sen))
                    if n > 3:
                        warn.append(f'{n} figures stacked in one sentence (split the evidence): "{sen[:58]}"')
                        break
            CAUSAL = r'\b(because of|caused by|due to|driven by|reflects|a timing (shift|issue)|the result of|on the back of)\b'
            HEDGE = r'\b(suggest\w*|point\w* to|imply|implies|implying|appears?|indicat\w*|consistent with|raises the possibility)\b'
            for L in prose:
                if TEST(CAUSAL, L['txt'], 'i') and not TEST(HEDGE, L['txt'], 'i'):
                    warn.append(f'unhedged causal claim (use suggests, points to or implies unless the data establishes the mechanism): "{L["txt"][:58]}"')
            if True:
                n = 0
                for L in lines:
                    if L['r'] < EXEC_END and not TEST(r'^[A-Z\s·]+$', L['txt']):
                        n += len(MATCHALL(FIGRX, L['txt']))
                if n > 8:
                    warn.append(f'executive summary carries {n} figures (target 4-6): keep only the evidence the story needs')
            if True:
                kq = OR(NG('keyQuestion'), '')
                if T(kq) and not TEST(r',\s*or\s', kq, 'i'):
                    warn.append('key debate poses no alternative: phrase it "Whether X, or Y"')
            for L in prose:
                if isDataRow(L['txt']):
                    continue
                for sen in SPLIT(r'(?<=[.!?])\s+', L['txt']):
                    labelled = TEST(r'(for|in) \dQ\d{2}\b', sen, 'i') and TEST(r'(for|in) FY ?20\d{2}\b', sen, 'i')
                    if (TEST(r'\b\dQ\d{2}\b', sen) and TEST(r'\bFY ?20\d{2}\b', sen) and not labelled
                            and TEST(r'\d', REPL(r'\b\dQ\d{2}\b|\bFY ?20\d{2}\b|\b20\d{2}\b|T\+\d+', sen, ''))
                            and not TEST(r'(versus|vs\.?|against|while|but|compared|than|widen|then)', sen, 'i')):
                        warn.append(f'quarterly and annual figures mixed in one sentence (label the horizons or split): "{sen[:58]}"')
            if True:
                def figsOf(t):
                    out = {}
                    for x in MATCHALL(rf'[+−-]?(?:{SYMRX})?\d[\d,]*(?:\.\d+)?\s?(?:bps|bp|pp|%|bn|[BMTkm])', S(t)):
                        out[REPL(r'\s+', x, '')] = True
                    return out
                th_ = figsOf(OR(NG('thesis'), ''))
                st_ = figsOf(OR(NG('street'), ''))
                overlap = 0
                for f in st_:
                    if f in th_:
                        overlap += 1
                if overlap >= 2:
                    warn.append(f'street view repeats {overlap} figures from the executive summary: state what consensus believes, not the headline again')
            open_ = {}
            for L in prose:
                if isDataRow(L['txt']):
                    continue
                k = ' '.join(SPLIT(r'\s+', REPL(r'^[•▲▼\s]+', L['txt'], '', count=1))[:6])
                if len(k) >= 22:
                    open_[k] = open_.get(k, 0) + 1
            for k in open_:
                if open_[k] > 1:
                    warn.append(f'repeated bullet opening {open_[k]}x (state once): "{k}"')
        for w in warn:
            log('   ⚠ ' + w)

    LOGO = None
    try:
        lp = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'assets', 'insync_logo.png')
        if os.path.exists(lp):
            with open(lp, 'rb') as fh:
                LOGO = fh.read()
    except Exception:
        pass
    cols1 = (f'<cols><col min="1" max="1" width="{js_to_fixed(LABEL_W + 0.7109375, 7)}" customWidth="1"/><col min="2" max="{CURC}" width="{S(ANCH_W)}" customWidth="1"/>'
             f'<col min="{DC}" max="{PCT}" width="12" customWidth="1"/><col min="{YC}" max="{QC}" width="7" customWidth="1"/><col min="{LOWC}" max="{HIC}" width="{S(LH_W)}" customWidth="1"/>'
             f'<col min="{CNTC}" max="{CNTC}" width="8" customWidth="1"/><col min="{NCOL + 1}" max="{NCOL + 1}" width="2.5" customWidth="1"/><col min="{P0}" max="{P0}" width="30" customWidth="1"/>'
             f'<col min="{P0 + 1}" max="{P5}" width="19" customWidth="1"/></cols>')
    xml1 = s1.xml({'cols': cols1, 'pane': '<pane xSplit="1" ySplit="4" topLeftCell="B5" state="frozen"/>', 'drawing': 'rIdD' if LOGO else None})
    n1 = 'Revision Summary'
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
          + ('<Default Extension="png" ContentType="image/png"/>' if LOGO else '')
          + '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
          + ('<Override PartName="/xl/drawings/drawing1.xml" ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/>' if LOGO else '')
          + '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
    rels = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'
    wbr = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'
    wb = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="{esc(n1)}" sheetId="1" r:id="rId1"/></sheets><calcPr calcId="0" fullCalcOnLoad="1"/></workbook>'
    entries = [{'name': '[Content_Types].xml', 'data': ct}, {'name': '_rels/.rels', 'data': rels}, {'name': 'xl/workbook.xml', 'data': wb}, {'name': 'xl/_rels/workbook.xml.rels', 'data': wbr}, {'name': 'xl/styles.xml', 'data': stylesXML()}, {'name': 'xl/worksheets/sheet1.xml', 'data': xml1}]
    if LOGO:
        def drel(n):
            return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rIdD" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing" Target="../drawings/drawing{n}.xml"/></Relationships>'
        dxml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><xdr:oneCellAnchor><xdr:from><xdr:col>0</xdr:col><xdr:colOff>28575</xdr:colOff><xdr:row>0</xdr:row><xdr:rowOff>21600</xdr:rowOff></xdr:from><xdr:ext cx="1155906" cy="285750"/><xdr:pic><xdr:nvPicPr><xdr:cNvPr id="1" name="InSync Analytics Logo"/><xdr:cNvPicPr/></xdr:nvPicPr><xdr:blipFill><a:blip r:embed="rId1"/><a:stretch><a:fillRect/></a:stretch></xdr:blipFill><xdr:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="1155906" cy="285750"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></xdr:spPr></xdr:pic><xdr:clientData/></xdr:oneCellAnchor></xdr:wsDr>'
        irel = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/image1.png"/></Relationships>'
        entries.append({'name': 'xl/worksheets/_rels/sheet1.xml.rels', 'data': drel(1)})
        entries.append({'name': 'xl/drawings/drawing1.xml', 'data': dxml})
        entries.append({'name': 'xl/drawings/_rels/drawing1.xml.rels', 'data': irel})
        entries.append({'name': 'xl/media/image1.png', 'data': LOGO})
    blob = zip_(entries)
    with open(outPath, 'wb') as fh:
        fh.write(blob)
    n = 0
    for s in ARR(G(C, 'sections')):
        for _ in ARR(G(s, 'items')):
            n += 1
    if True:
        vq = OR(G(VINT, 'q'), {})
        cutTxt = ', '.join(f"{S(AH(k))} {mmdd(G(G(C, 'cuts'), k))}->{mmdd(G(vq, k) if T(G(vq, k)) else G(G(C, 'cuts'), k))}" for k in ARR(ANCH_ALL))
        hidden = [k for k in ARR(ANCH_ALL) if k not in ANCH]

        def blk(e):
            pk = per_of(e)[0]
            v = OR(G(AV, pk), {})
            c = [k for k in ANCH if not SEQ(k, 'pre') and not (T(G(v, k)) and T(G(G(v, k), 'distinct')))]
            if not c:
                return ''
            lab = S(G(G(G(C, 'periods'), 0), 1)) if SEQ(pk, 'q') else PLUS('FY', G(G(G(C, 'periods'), 1), 1))
            return f"| {lab}: {', '.join(S(AH(k)) + ' same as ' + S(G(AHDR, G(G(v, k), 'sameAs'))) for k in c)}"
        log('   anchors:', cutTxt, '| Current', mmdd(OR(G(vq, 'cur'), G(M, 'updated'))), '| columns:', JOIN([AH(k) for k in ANCH] + ['Current'], ' | '),
            f"| collapsed: {JOIN([AH(k) for k in hidden], ', ')}" if hidden else '',
            ' '.join(blk(e) for e in ARR(G(C, 'periods'))))
        DSz = G(I, 'dataState')
        if T(DSz) and not SEQ(G(DSz, 'key'), 'ok'):
            log('   data state:', G(DSz, 'key'), '(last vintage', PLUS(G(DSz, 'lastVintage'), ')'))
    for c in CLIPPED:
        log(f'   ⚠ WARNING: text cut at the length cap ("{c["head"]}…"); lost: "{c["lost"][:90]}". Shorten it.')
    if LONGLINES:
        log(f'   ⚠ {len(LONGLINES)} narrative line(s) wrap past one panel row (label + text over {PW} characters; shorten to one row): "{LONGLINES[0][:60]}…"{" …" if len(LONGLINES) > 1 else ""}')
    log('WROTE', outPath, '| sheets: 1 (Revision Summary + Panel) | line items:', n, '| period:', JOIN([G(p, 1) for p in ARR(G(C, 'periods'))], '/'),
        '| narrative:', 'provided' if T(NAR) else 'fallback templates')


if __name__ == '__main__':
    try:
        main(sys.argv)
    except SystemExit:
        raise
    except BaseException as e:
        sys.stdout.flush()
        name = 'TypeError' if isinstance(e, (JSTypeError, TypeError, AttributeError, KeyError, IndexError)) else (
            'SyntaxError' if type(e).__name__ == '_JSErr' and 'JSON' in str(e) else 'Error')
        sys.stderr.write(_out16(f'{name}: {e}') + '\n')
        sys.stderr.flush()
        sys.exit(1)
