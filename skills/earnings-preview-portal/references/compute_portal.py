#!/usr/bin/env python3
"""
compute_portal.py - deterministic math engine for the Earnings Preview Portal (Path B).
Usage: python3 compute_portal.py <input.json> <computed.json>

A port of an earlier JavaScript engine (internal edition only) with byte-identical output,
so every step mirrors it line for line (same function names in snake_case, same order of
floating-point operations, same warning order) and leans on jscompat.py for the places where
JavaScript and Python differ.

The LLM's ONLY jobs are (1) fetching raw InSync MCP responses into input.json and
(2) writing narrative.json prose. THIS script does every calculation. If a number
appears in the portal, it must exist in computed.json produced here.

input.json contract (see references/pipeline-contracts.md):
  meta: ticker/company/quarters/fiscal/prints/upcoming/asof/metricset/buckets
  mcp:  raw MCP responses grouped by purpose (pasted verbatim; envelopes tolerated)

JS-emulation helpers private to this file (the shared jscompat.py is not edited):
  _S  String(x)        _t  JS truthiness      _g  property read (missing key -> UNDEFINED)
  _n  ToNumber         _div/_mul/_sub/_pow  JS arithmetic (null -> 0, x/0 -> Infinity)
  _lt/_le/_gt/_ge  abstract relational comparison (string vs number vs null)
  _seq  strict equality  _join  Array.join (null/undefined -> '')
  _rx  a JS regex source compiled with JS semantics (\\s, '.', '$', ASCII \\b\\d\\w, /i)
  _date_ms/_iso  Date parsing (ISO date-only = UTC) and toISOString
  _json_parse  JSON.parse (numbers are doubles, NaN/Infinity rejected, V8 error text)
"""
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from jscompat import (js_str, js_json, js_to_fixed, js_round, js_parse_float,
                      js_number, js_cmp_sort, js_keys, js_stdio, js_write, UNDEFINED)
import polarity as POLARITY

U = UNDEFINED
NaN = float('nan')
INF = float('inf')
EPSILON = 2.220446049250313e-16


def _isnum(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _S(x):
    if x is U:
        return 'undefined'
    if isinstance(x, list):
        return ','.join('' if (e is None or e is U) else _S(e) for e in x)
    if isinstance(x, dict):
        return '[object Object]'
    return js_str(x)


def _join(arr, sep=','):
    return sep.join('' if (e is None or e is U) else _S(e) for e in arr)


def _t(x):
    if x is None or x is U or x is False:
        return False
    if x is True:
        return True
    if _isnum(x):
        return x == x and x != 0
    if isinstance(x, str):
        return len(x) > 0
    return True


def _g(o, k):
    if isinstance(o, dict):
        return o.get(k, U)
    if o is None or o is U:
        raise TypeError("Cannot read properties of %s (reading '%s')"
                        % ('null' if o is None else 'undefined', k))
    if isinstance(o, (list, str)) and k == 'length':
        return len(o)
    return U


def _n(x):
    return x if _isnum(x) else js_number(x)


def _fin(x):
    v = _n(x)
    return _isnum(v) and not (isinstance(v, float) and (math.isnan(v) or math.isinf(v)))


def _num_is_finite(v):
    return _isnum(v) and not (isinstance(v, float) and (math.isnan(v) or math.isinf(v)))


def _sub(a, b):
    return _n(a) - _n(b)


def _mul(a, b):
    return _n(a) * _n(b)


def _div(a, b):
    a, b = _n(a), _n(b)
    try:
        return a / b
    except ZeroDivisionError:
        if a != a or a == 0:
            return NaN
        neg = (a < 0) != (math.copysign(1.0, b) < 0)
        return -INF if neg else INF


def _pow(a, b):
    a, b = float(_n(a)), float(_n(b))
    if b != b or a != a:
        return NaN if b != 0 else 1.0
    if a < 0 and b != int(b):
        return NaN
    try:
        r = a ** b
    except OverflowError:
        return INF
    except ZeroDivisionError:
        return INF
    if isinstance(r, complex):
        return NaN
    return r


def _sign(x):
    if x != x:
        return NaN
    return 1 if x > 0 else (-1 if x < 0 else x)


def _u16(s):
    return [c for ch in s for c in ((ord(ch),) if ord(ch) < 0x10000 else
                                    (0xD800 + ((ord(ch) - 0x10000) >> 10),
                                     0xDC00 + ((ord(ch) - 0x10000) & 0x3FF)))]


def _slt(a, b):
    if a.isascii() and b.isascii():
        return a < b
    return _u16(a) < _u16(b)


def _prim(x):
    return _S(x) if isinstance(x, (list, dict)) else x


def _lt(a, b):
    a, b = _prim(a), _prim(b)
    if isinstance(a, str) and isinstance(b, str):
        return _slt(a, b)
    x, y = _n(a), _n(b)
    return x < y


def _le(a, b):
    a, b = _prim(a), _prim(b)
    if isinstance(a, str) and isinstance(b, str):
        return not _slt(b, a)
    x, y = _n(a), _n(b)
    if x != x or y != y:
        return False
    return x <= y


def _gt(a, b):
    return _lt(b, a)


def _ge(a, b):
    return _le(b, a)


def _seq(a, b):
    if a is b:
        return not (isinstance(a, float) and a != a)
    if _isnum(a) and _isnum(b):
        return a == b
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    return False


def _index_of(arr, x):
    for i, e in enumerate(arr):
        if _seq(e, x):
            return i
    return -1


def _uniq(arr):
    out, seen = [], []
    for x in arr:
        if not any(_same_value_zero(x, y) for y in seen):
            seen.append(x)
            out.append(x)
    return out


def _same_value_zero(a, b):
    if isinstance(a, float) and isinstance(b, float) and a != a and b != b:
        return True
    return _seq(a, b)


def _u16slice(s, a, b=None):
    if s.isascii() or all(ord(ch) < 0x10000 for ch in s):
        return s[a:b]
    units = _u16(s)
    part = units[a:b]
    return bytes(b2 for u in part for b2 in u.to_bytes(2, 'little')).decode('utf-16-le', 'surrogatepass')


_WSC = '\\t\\n\\x0b\\x0c\\r \\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000\\ufeff'
_WS_CHARS = ('\t\n\x0b\x0c\r \xa0         '
             '       　﻿')
_DOT = '[^\\n\\r\\u2028\\u2029]'


def _trim(s):
    return s.strip(_WS_CHARS)


_RX_CACHE = {}


def _tr(src):
    out, i, n, cls = [], 0, len(src), False
    while i < n:
        c = src[i]
        if c == '\\' and i + 1 < n:
            d = src[i + 1]
            if d == 's':
                out.append(_WSC if cls else '[' + _WSC + ']')
            elif d == 'S':
                out.append('[^' + _WSC + ']')
            else:
                out.append(c + d)
            i += 2
            continue
        if cls:
            if c == ']':
                cls = False
                out.append(c)
            elif c == '[':
                out.append('\\[')
            else:
                out.append(c)
        else:
            if c == '[':
                cls = True
                out.append(c)
                if i + 1 < n and src[i + 1] == '^':
                    out.append('^')
                    i += 1
            elif c == '.':
                out.append(_DOT)
            elif c == '$':
                out.append('\\Z')
            else:
                out.append(c)
        i += 1
    return ''.join(out)


def _rx(src, flags=''):
    key = (src, flags)
    r = _RX_CACHE.get(key)
    if r is None:
        f = re.ASCII
        if 'i' in flags:
            f |= re.IGNORECASE
        r = re.compile(_tr(src), f)
        _RX_CACHE[key] = r
    return r


def _test(src, s, flags=''):
    return _rx(src, flags).search(s if isinstance(s, str) else _S(s)) is not None


def _exec(src, s, flags=''):
    m = _rx(src, flags).search(s if isinstance(s, str) else _S(s))
    if m is None:
        return None
    return [m.group(0)] + [U if x is None else x for x in m.groups()]


def _replace(src, s, repl, flags='', glob=False):
    fn = repl if callable(repl) else (lambda m, r=repl: r)
    return _rx(src, flags).sub(fn, s, count=0 if glob else 1)


def _split(src, s, flags=''):
    return _rx(src, flags).split(s)


def _match_all(src, s, flags=''):
    r = _rx(src, flags).findall(s)
    return [x if isinstance(x, str) else x[0] for x in r] if r else None


class _JSONError(Exception):
    pass


def _reject_const(s):
    raise ValueError('No number: ' + s)


def _json_parse(text):
    return json.loads(text, parse_int=float, parse_constant=_reject_const)


def _v8_json_message(text, err):
    pos = getattr(err, 'pos', None)
    if pos is None:
        pos = 0
        m = re.search(r'(?<![\w"])(-?Infinity|NaN)', text)
        if m:
            pos = m.start() + (1 if text[m.start()] == '-' else 0)
    if pos >= len(text):
        return 'Unexpected end of JSON input'
    c = text[pos]
    if c == '"':
        return 'Unexpected string in JSON at position ' + str(len(_u16(text[:pos])))
    if c.isdigit() or c == '-':
        return 'Unexpected number in JSON at position ' + str(len(_u16(text[:pos])))
    return 'Unexpected token ' + c + ' in JSON at position ' + str(len(_u16(text[:pos])))


_ISO_RE = re.compile(r'^([+-]\d{6}|\d{4})(?:-(\d{2})(?:-(\d{2}))?)?'
                     r'(?:T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?(Z|[+-]\d{2}:\d{2})?)?\Z')


def _days_from_civil(y, m, d):
    y -= m <= 2
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def _date_ms(x):
    if x is None:
        return 0.0
    if x is U:
        return NaN
    if isinstance(x, bool):
        return float(x)
    if _isnum(x):
        return float(x)
    s = _S(x)
    m = _ISO_RE.match(s)
    if not m:
        return NaN
    y = int(m.group(1))
    mo = int(m.group(2) or 1)
    d = int(m.group(3) or 1)
    hh, mi, ss = int(m.group(4) or 0), int(m.group(5) or 0), int(m.group(6) or 0)
    ms = int((m.group(7) or '0')[:3].ljust(3, '0'))
    if not (1 <= mo <= 12 and 1 <= d <= 31 and hh <= 24 and mi <= 59 and ss <= 59):
        return NaN
    t = ((_days_from_civil(y, mo, d) * 24 + hh) * 60 + mi) * 60 * 1000 + ss * 1000 + ms
    tz = m.group(8)
    if m.group(4) is None or tz == 'Z':
        return float(t)
    if tz:
        sign = 1 if tz[0] == '+' else -1
        return float(t - sign * (int(tz[1:3]) * 60 + int(tz[4:6])) * 60000)
    import time
    try:
        lt = time.mktime((y, mo, d, hh, mi, ss, 0, 0, -1))
        return float(lt * 1000 + ms)
    except (OverflowError, ValueError):
        return NaN


def _iso(ms):
    if ms != ms or abs(ms) > 8.64e15:
        raise ValueError('Invalid time value')
    ms = int(ms)
    days, rem = divmod(ms, 86400000)
    z = days + 719468
    era = (z if z >= 0 else z - 146096) // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    mo = mp + (3 if mp < 10 else -9)
    y += mo <= 2
    hh, rem = divmod(rem, 3600000)
    mi, rem = divmod(rem, 60000)
    ss, msr = divmod(rem, 1000)
    ys = ('%04d' % y) if 0 <= y <= 9999 else (('+' if y > 0 else '-') + '%06d' % abs(y))
    return '%s-%02d-%02dT%02d:%02d:%02d.%03dZ' % (ys, mo, d, hh, mi, ss, msr)


def _add_days_iso(s, n):
    ms = _date_ms(_S(s) + 'T00:00:00Z')
    if ms != ms:
        raise ValueError('Invalid time value')
    return _iso(ms + n * 86400000)[:10]


CUR_TOKENS = [
    ['USCents', 'USD', True], ['USCent', 'USD', True], ['USDc', 'USD', True], ['USc', 'USD', True],
    ['GBPence', 'GBP', True], ['GBp', 'GBP', True],
    ['EURCents', 'EUR', True], ['EURCent', 'EUR', True], ['EURc', 'EUR', True],
    ['ZARc', 'ZAR', True], ['JPYSen', 'JPY', True],
    ['R$', 'BRL', False], ['A$', 'AUD', False], ['C$', 'CAD', False], ['HK$', 'HKD', False],
    ['NT$', 'TWD', False], ['S$', 'SGD', False], ['NZ$', 'NZD', False], ['US$', 'USD', False],
    ['$', 'USD', False], ['€', 'EUR', False], ['£', 'GBP', False], ['¥', 'JPY', False],
    ['₹', 'INR', False], ['₩', 'KRW', False], ['₽', 'RUB', False], ['₺', 'TRY', False],
    ['USD', 'USD', False], ['EUR', 'EUR', False], ['GBP', 'GBP', False], ['CHF', 'CHF', False],
    ['DKK', 'DKK', False], ['SEK', 'SEK', False], ['NOK', 'NOK', False], ['CAD', 'CAD', False],
    ['JPY', 'JPY', False], ['BRL', 'BRL', False], ['MXN', 'MXN', False], ['CLP', 'CLP', False],
    ['COP', 'COP', False], ['ARS', 'ARS', False], ['PEN', 'PEN', False], ['ZAR', 'ZAR', False],
    ['INR', 'INR', False], ['IDR', 'IDR', False], ['KRW', 'KRW', False], ['TWD', 'TWD', False],
    ['HKD', 'HKD', False], ['CNY', 'CNY', False], ['RMB', 'CNY', False], ['SGD', 'SGD', False],
    ['AUD', 'AUD', False], ['NZD', 'NZD', False], ['PLN', 'PLN', False], ['TRY', 'TRY', False],
    ['ILS', 'ILS', False], ['AED', 'AED', False], ['SAR', 'SAR', False], ['THB', 'THB', False],
    ['PHP', 'PHP', False], ['MYR', 'MYR', False]
]
CUR_INFO_EXACT, CUR_INFO = {}, {}
for _t0, _maj, _mnr in CUR_TOKENS:
    _rec = {'token': _t0, 'major': _maj, 'minor': _mnr}
    CUR_INFO_EXACT[_t0] = _rec
    if _t0.upper() not in CUR_INFO:
        CUR_INFO[_t0.upper()] = _rec
_CUR_ALT = '|'.join(re.escape(t) for t in sorted((t[0] for t in CUR_TOKENS), key=lambda t: -len(t)))
CUR_RE = re.compile('^(' + _CUR_ALT + ')[' + _WSC + ']*', re.I | re.ASCII)
CUR_RE_TRAIL = re.compile('(' + _CUR_ALT + ')[' + _WSC + ']*\\)?[' + _WSC + ']*\\Z', re.ASCII)

CUR_SYMBOL = {'USD': '$', 'EUR': '€', 'GBP': '£', 'JPY': '¥', 'CNY': '¥', 'INR': '₹',
              'KRW': '₩', 'RUB': '₽', 'TRY': '₺', 'CHF': 'CHF ', 'CAD': 'C$', 'AUD': 'A$',
              'NZD': 'NZ$', 'HKD': 'HK$', 'TWD': 'NT$', 'SGD': 'S$', 'BRL': 'R$'}


def currency_info(raw):
    if raw is None or raw is U or _isnum(raw):
        return None
    t = _replace('^\\((.*)\\)$', _trim(_S(raw)), lambda m: m.group(1))
    t = _replace('^-', t, '')
    m = CUR_RE.search(t)
    if not m:
        m = CUR_RE_TRAIL.search(t)
    if not m:
        return None
    return CUR_INFO_EXACT.get(m.group(1)) or CUR_INFO.get(m.group(1).upper()) or None


def currency_of(raw):
    c = currency_info(raw)
    return c['token'] if c else ''


BPS_SEEN = []


def parse_parts(raw):
    if raw is None or raw is U:
        return None
    if _isnum(raw):
        return {'v': raw, 'scale': 1, 'pct': False, 'cur': None}
    s = _trim(_S(raw))
    if not s or s == '-' or s == '—':
        return None
    cur = currency_info(s)
    neg = False
    if _test('^\\(.*\\)$', s):
        neg = True
        s = s[1:-1]
    s = _replace('[,\\s]', s, '', glob=True)
    s = CUR_RE.sub('', s, count=1)
    s = CUR_RE_TRAIL.sub('', s, count=1)
    if s.startswith('-'):
        neg = True
        s = s[1:]
    s = CUR_RE.sub('', s, count=1)
    scale, pct, bps = 1, False, False
    if _test('%$', s):
        pct = True
        s = s[:-1]
    elif _test('bps?$', s, 'i'):
        pct, bps = True, True
        s = _replace('bps?$', s, '', 'i')
    elif _test('T$', s, 'i'):
        scale = 1e12
        s = s[:-1]
    elif _test('B$', s, 'i'):
        scale = 1e9
        s = s[:-1]
    elif _test('M$', s, 'i'):
        scale = 1e6
        s = s[:-1]
    elif _test('k$', s, 'i'):
        scale = 1e3
        s = s[:-1]
    v = js_parse_float(s)
    if not _num_is_finite(v):
        return None
    val = v / 100 if bps else v
    if bps:
        BPS_SEEN.append(_trim(_S(raw)))
    return {'v': -val if neg else val, 'scale': scale, 'pct': pct, 'cur': cur, 'bps': bps}


def parse_value(raw):
    p = parse_parts(raw)
    if p is None:
        return None
    return p['v'] * (1 if p['scale'] == 1 else p['scale'] / 1e6)


def parse_absolute(raw):
    p = parse_parts(raw)
    return None if p is None else p['v'] * p['scale']


def parse_count(v):
    return parse_absolute(v)


def extract_rows(node, out=None):
    if out is None:
        out = []
    if isinstance(node, list):
        for n in node:
            extract_rows(n, out)
        return out
    if isinstance(node, dict):
        if ('LTCODE' in node or 'metric' in node or ('DATE' in node and 'ADJUSTED_CLOSE' in node) or
                'GUIDE_TEXT' in node or 'CALL_DATE' in node or ('MEAN' in node and 'MAX' in node)):
            out.append(node)
            return out
        for k in js_keys(node):
            extract_rows(node[k], out)
    elif isinstance(node, str) and _trim(node).startswith('{'):
        try:
            parsed = _json_parse(node)
        except (ValueError, RecursionError):
            parsed = U
        if parsed is not U:
            extract_rows(parsed, out)
    return out


def _round(x, d):
    if x is None or x is U:
        return None
    n = _n(x)
    if not _num_is_finite(n):
        return None
    sign = -1 if n < 0 else 1
    a = float(abs(n))
    f = 10 ** d
    scaled = a * f
    return sign * js_round(scaled + abs(scaled) * EPSILON * 4) / f


def pct1(x):
    if x is None or x is U or not _fin(x):
        return _S(x)
    return js_to_fixed(_round(x, 1), 1)


def _is_zero(b):
    return _isnum(b) and b == 0


def pct_change(a, b):
    if a is None or b is None or _is_zero(b) or a is U or b is U:
        return None
    return _round(_mul(_sub(_div(a, b), 1), 100), 10)


def growth_pct(cur, base):
    if cur is None or cur is U or base is None or base is U or _le(base, 0):
        return None
    return _round(_mul(_sub(_div(cur, base), 1), 100), 10)


def abs_change(cur, base):
    if cur is None or cur is U or base is None or base is U:
        return None
    return _round(_sub(cur, base), 4)


def median(arr):
    v = js_cmp_sort([x for x in arr if x is not None and _fin(x)], lambda a, b: _sub(a, b))
    if not v:
        return None
    m = len(v) // 2
    return v[m] if len(v) % 2 else _div(v[m - 1] + v[m], 2)


def mean(arr):
    v = [x for x in arr if x is not None and _fin(x)]
    if not v:
        return None
    s = 0
    for x in v:
        s = s + x
    return _div(s, len(v))


def note_currency(ledger, lt, raw, period, also_raw=None):
    if not _t(ledger):
        return
    key = _S(lt)
    e = ledger.get(key)
    if not _t(e):
        e = {'majors': {}, 'minor': False}
        ledger[key] = e

    def note(raw0, tag):
        c = currency_info(raw0)
        if not c:
            return
        lst = e['majors'].get(c['major'])
        if not _t(lst):
            lst = []
            e['majors'][c['major']] = lst
        lst.append(_S(period) + (tag if _t(tag) else ''))
        if c['minor']:
            e['minor'] = True
    note(raw, '')
    for x in (also_raw or []):
        note(x['v'], ':' + x['k'])


def _date_cmp(x, y):
    return -1 if _lt(x['date'], y['date']) else 1


def index_estimates(rows, abs_codes, ledger):
    idx = {}

    def pv(lt, v):
        return parse_absolute(v) if (abs_codes is not None and lt in abs_codes) else parse_value(v)
    for r in rows:
        if not _t(_g(r, 'LTCODE')) or not _t(_g(r, 'FISCALPERIOD')) or _g(r, 'UPDATEDATE') is U:
            continue
        note_currency(ledger, r['LTCODE'], _g(r, 'VALUE'), r['FISCALPERIOD'],
                      [{'k': 'MIN', 'v': _g(r, 'MIN')}, {'k': 'MAX', 'v': _g(r, 'MAX')}])
        k = _S(r['LTCODE']) + '|' + _S(r['FISCALPERIOD'])
        bc = _g(r, 'BROKERCOUNT')
        idx.setdefault(k, []).append({
            'date': r['UPDATEDATE'], 'value': pv(r['LTCODE'], _g(r, 'VALUE')),
            'brokers': None if (bc is U or bc is None or bc == '') else num(bc),
            'min': pv(r['LTCODE'], _g(r, 'MIN')), 'max': pv(r['LTCODE'], _g(r, 'MAX'))
        })
    for k in list(idx.keys()):
        idx[k] = js_cmp_sort(idx[k], _date_cmp)
    return idx


def last_on_or_before(series, date_str):
    if not _t(series):
        return None
    best = None
    for p in series:
        if _le(p['date'], date_str):
            best = p
    return best


def latest_of(series):
    return series[-1] if (_t(series) and len(series)) else None


def num(v):
    if v is None or v is U:
        return None
    if _isnum(v):
        return v if _num_is_finite(v) else None
    s = _replace('[,\\s]', _S(v), '', glob=True)
    s = _replace('[^0-9.eE+-]', s, '', glob=True)
    f = js_parse_float(s)
    return f if _num_is_finite(f) else None


def index_actuals(rows, abs_codes, ledger=None, warn_fn=None):
    idx, seen, restated = {}, {}, []
    for r in rows:
        if not _t(_g(r, 'LTCODE')) or not _t(_g(r, 'FISCALPERIOD')) or _g(r, 'UPDATEDATE') is not U:
            continue
        if _g(r, 'VALUE') is U:
            continue
        raw = _S(r['FISCALPERIOD'])
        is_r = raw.startswith('R-')
        fp = raw[2:] if is_r else raw
        k = _S(r['LTCODE']) + '|' + fp
        v = parse_absolute(r['VALUE']) if (abs_codes is not None and r['LTCODE'] in abs_codes) \
            else parse_value(r['VALUE'])
        note_currency(ledger, r['LTCODE'], r['VALUE'], fp)
        if k in seen and seen[k]['isR'] != is_r:
            restated.append({'k': k, 'orig': seen[k]['v'] if is_r else v, 'rest': v if is_r else seen[k]['v']})
            if not is_r:
                continue
        elif k in seen and seen[k]['isR'] == is_r and not _seq(seen[k]['v'], v):
            restated.append({'k': k, 'orig': seen[k]['v'], 'rest': v, 'same_basis': True})
        seen[k] = {'v': v, 'isR': is_r}
        idx[k] = v
    if restated and warn_fn:
        for x in restated:
            warn_fn('[actuals] ' + (
                'TWO DIFFERENT ACTUALS for ' + x['k'] + ' on the SAME basis (' + _S(x['orig']) + ' and ' +
                _S(x['rest']) + '); the later row was used. Array order must not decide a reported figure - de-duplicate ' +
                'the fetch and re-run.'
                if x.get('same_basis') else
                'RESTATED period ' + x['k'] + ': the issuer restated ' + _S(x['orig']) + ' to ' + _S(x['rest']) +
                '. The restated figure is used, because it is the basis the forward estimates are built ' +
                'on, but the history is NOT on one basis throughout. Say so wherever the growth off ' +
                'this period is discussed.'))
    return idx


def index_valuation(rows):
    idx = {}
    for r in rows:
        if not _t(_g(r, 'metric')) or _g(r, 'TICKER_ID') is U:
            continue
        k = _S(r['TICKER_ID']) + '|' + _S(r['metric'])
        d = _g(r, 'DATE')
        if k not in idx or _ge(_S(d if _t(d) else ''), _S(idx[k]['date'] if _t(idx[k]['date']) else '')):
            idx[k] = {'value': num(_g(r, 'value')), 'date': d}
    out = {}
    for k in js_keys(idx):
        out[k] = idx[k]['value']
    return out


def index_market(rows, default_tid=U, px_ledger=None):
    idx = {}
    for r in rows:
        if _g(r, 'ADJUSTED_CLOSE') is U or not _t(_g(r, 'DATE')):
            continue
        tv = _g(r, 'TICKER_ID')
        t = _S(U if default_tid is U else default_tid) \
            if (tv is U or tv is None or (isinstance(tv, str) and tv == '')) else _S(tv)
        cl = num(r['ADJUSTED_CLOSE'])
        if cl is None:
            continue
        if px_ledger is not None:
            note_currency(px_ledger, '__price', r['ADJUSTED_CLOSE'], r['DATE'])
        vol = num(_g(r, 'VOLUME'))
        idx.setdefault(t, []).append({'date': r['DATE'], 'close': cl, 'volume': vol if _t(vol) else 0})
    conflicts = []
    for t in js_keys(idx):
        by_date = {}
        for pt in idx[t]:
            dk = _hk(pt['date'])
            prev = by_date.get(dk)
            if prev and not _seq(prev['close'], pt['close']):
                conflicts.append(t + ' ' + _S(pt['date']) + ': ' + _S(prev['close']) + ' vs ' + _S(pt['close']))
            if not prev:
                by_date[dk] = pt
            elif _seq(prev['close'], pt['close']) and pt['volume'] > prev['volume']:
                by_date[dk] = pt
        idx[t] = list(by_date.values())
    for t in list(idx.keys()):
        idx[t] = js_cmp_sort(idx[t], _date_cmp)
    if conflicts:
        idx['__conflicts'] = conflicts
    return idx


def _hk(x):
    if isinstance(x, bool):
        return ('b', x)
    if _isnum(x):
        return ('n', float(x)) if x == x else ('nan',)
    if isinstance(x, str):
        return ('s', x)
    return ('o', id(x))


def index_market_summary(rows):
    idx = {}
    for r in rows:
        if _g(r, 'MEAN') is not U and _g(r, 'MAX') is not U:
            idx[_S(_g(r, 'TICKER_ID'))] = {'mean': num(r['MEAN']), 'min': num(_g(r, 'MIN')), 'max': num(r['MAX'])}
    return idx


class _Exit(Exception):
    def __init__(self, code):
        Exception.__init__(self, code)
        self.code = code


def _err(msg):
    sys.stdout.flush()
    sys.stderr.write(msg + '\n')
    sys.stderr.flush()


def _log(msg):
    sys.stdout.write(msg + '\n')


def _and(a, f):
    return f() if _t(a) else a


def _or(a, b):
    return a if _t(a) else b


def main():
    argv = sys.argv[1:]
    in_path = argv[0] if len(argv) > 0 else U
    out_path = argv[1] if len(argv) > 1 else U
    if not _t(in_path) or not _t(out_path):
        _err('usage: node compute_portal.js <input.json> <computed.json>')
        raise _Exit(1)
    try:
        try:
            with open(in_path, 'rb') as f:
                text = f.read().decode('utf-8', errors='replace')
        except FileNotFoundError:
            raise _JSONError("ENOENT: no such file or directory, open '" + in_path + "'")
        except IsADirectoryError:
            raise _JSONError('EISDIR: illegal operation on a directory, read')
        except PermissionError:
            if os.path.isdir(in_path):
                raise _JSONError('EISDIR: illegal operation on a directory, read')
            raise _JSONError("EPERM: operation not permitted, open '" + in_path + "'")
        try:
            input_ = _json_parse(text)
        except (ValueError, RecursionError) as e:
            raise _JSONError(_v8_json_message(text, e))
    except _JSONError as e:
        _err('INPUT ERROR - could not read ' + in_path + ' as JSON: ' + str(e) +
             '\nThis file is assembled by hand in Step 2. A truncated paste or a trailing comma is ' +
             'the usual cause; validate it (node -e "JSON.parse(require(\'fs\').readFileSync(process.argv[1]))" ' +
             in_path + ') before re-running.')
        raise _Exit(2)
    meta, mcp = _g(input_, 'meta'), _g(input_, 'mcp')
    warnings = []

    def warn(s):
        warnings.append(s)
    ac0 = _or(_and(meta, lambda: _g(meta, 'absent_confirmed')), [])
    confirmed_absent = set(_S(x) for x in _as_list(ac0))

    def cgap(keys, msg):
        ks = keys if isinstance(keys, list) else [keys]
        hit = next((k for k in (_S(x) for x in ks) if k in confirmed_absent), U)
        if _t(hit):
            warn('[absent] ' + _replace(' ->.*$', msg, '') + ' | recorded in meta.absent_confirmed as "' +
                 hit + '": the operator re-fetched and InSync returned no rows. The cell prints "—". ' +
                 'State it in the report as an absence in the data, not a number left out.')
            return
        warn('[completeness] ' + msg)
    ms_ = _and(meta, lambda: _g(meta, 'metricset'))
    need = [
        ['meta', meta, 'the whole calendar, metric set and bucket contract (Step 2)'],
        ['mcp', mcp, 'every fetched row (Step 1)'],
        ['meta.metricset', ms_, 'which 4 financial metrics and 5 KPIs the document covers - paste metricset.json from Step 0 verbatim'],
        ['meta.metricset.fin_metrics', _and(meta, lambda: _and(_g(meta, 'metricset'), lambda: _g(_g(meta, 'metricset'), 'fin_metrics'))), 'the Key Financial Metrics rows, top line FIRST (Step 0)'],
        ['meta.quarters', _and(meta, lambda: _g(meta, 'quarters')), 'py_q / prior_q / target_q / next_q, which name every column (Step 2)'],
        ['meta.fiscal', _and(meta, lambda: _g(meta, 'fiscal')), 'fy_prev / fy0 / fy1, which name the annual columns (Step 2)'],
        ['meta.growth_bases', _and(meta, lambda: _g(meta, 'growth_bases')), 'the prior-year and prior-quarter labels every growth sub-row divides by (Step 2)'],
        ['meta.buckets', _and(meta, lambda: _g(meta, 'buckets')), 'the pre / post / inter-Q dates the Earnings Revisions table is built from (Step 2)'],
        ['meta.prints', _and(meta, lambda: _g(meta, 'prints')), 'the reported call dates behind Beat History and the reaction series (get_call_date)'],
        ['meta.upcoming', _and(meta, lambda: _g(meta, 'upcoming')), 'the expected earnings date in the masthead (get_call_date)'],
        ['meta.ticker_id', _and(meta, lambda: _g(meta, 'ticker_id')), 'the subject-ticker gate that keeps another company\'s rows out of this document (search_tickerid)']
    ]
    missing = [x for x in need if x[1] is U or x[1] is None]
    if missing:
        _err('INPUT CONTRACT ERROR - ' + _S(len(missing)) + ' required block(s) absent from ' +
             in_path + ':\n' + '\n'.join('  - ' + k + ': ' + why for k, _v, why in missing) +
             '\nreferences/pipeline-contracts.md lists the full shape. Nothing was computed: a ' +
             'document built with any of these missing would be wrong in ways no completeness ' +
             'warning can describe.')
        raise _Exit(2)
    MS = meta['metricset']
    if not isinstance(_g(MS, 'fin_metrics'), list) or not len(MS['fin_metrics']):
        _err('INPUT CONTRACT ERROR - meta.metricset.fin_metrics is empty. The top line is ' +
             'fin_metrics[0] and is the base of every growth cell, every % of Total Revenue ' +
             'sub-row, the beat series and the whisper. Re-run Step 0.')
        raise _Exit(2)

    fin_m = MS['fin_metrics']
    seen_k, kpis, dropped = [], [], []
    for k in _as_list(_or(_g(MS, 'top_kpis'), [])):
        lk = _g(k, 'ltcode')
        if any(_same_value_zero(lk, s) for s in seen_k):
            dropped.append(lk)
            continue
        seen_k.append(lk)
        kpis.append(k)
    if dropped:
        warn('[metricset] DUPLICATE top KPI dropped: ' + _join(dropped, ', ') +
             ' appears more than once in meta.metricset.top_kpis. The later copy is discarded; ' +
             'without this the row printed twice in Summary Financials and twice in Earnings ' +
             'Revisions, once per copy, with whatever polarity each copy carried. Fix the metric set ' +
             'so the portal carries five DISTINCT KPIs.')
    all_codes = fin_m + kpis
    REV = _g(fin_m[0], 'ltcode')
    EPS = _or(_g(MS, 'eps_ltcode'),
              _g(_or(next((f for f in fin_m if _test('EPS', _g(f, 'name'), 'i')), U), {}), 'ltcode'))
    r0 = fin_m[0]
    n0 = _S(_or(_g(r0, 'name'), ''))
    if _test('^BS_', _S(REV)) or _test('deferred|unearned|contract liabilit', n0, 'i'):
        _err('TOP-LINE SLOT ERROR - meta.metricset.fin_metrics[0] is "' + n0 + '" (' + _S(REV) +
             '), which is a balance-sheet or deferred item, not a top line. Every "% of Total ' +
             'Revenue" cell, the entire revenue growth grid, the beat series and the whisper are ' +
             'computed against this row, so the document would be wrong throughout rather than ' +
             'incomplete. Re-run Step 0 and put the issuer\'s consolidated top line first.')
        raise _Exit(4)
    if not _test('revenue|sales|premium|income|turnover|billings', n0, 'i'):
        warn('[metricset] the TOP-LINE slot is "' + n0 + '" (' + _S(REV) + '), whose name carries no ' +
             'revenue vocabulary. finM[0] is used as revenue for every % of Total Revenue cell, the ' +
             'revenue growth grid, the beat series and the whisper. If this issuer genuinely reports ' +
             'its top line under this name, ignore this; if Step 0 mis-ordered fin_metrics, fix the ' +
             'metric set before building the document.')
    KPI1 = _g(kpis[0], 'ltcode') if (len(kpis) and _t(kpis[0])) else None
    Q, FY = meta['quarters'], meta['fiscal']

    def prior_year_guess(q):
        t = _S(q)
        m = _exec('^(\\d)([QqHh])(\\d{2})$', t)
        if m:
            return m[1] + m[2].upper() + _S(int(m[3], 10) - 1).rjust(2, '0')
        m = _exec('^(\\d{4})$', t)
        return _S(int(m[1], 10) - 1) if m else None
    tq_s = _S(_g(Q, 'target_q'))
    FREQ = 'H' if _test('^\\d[Hh]\\d{2}$', tq_s) else ('A' if _test('^\\d{4}$', tq_s) else 'Q')
    SEQ_LABEL = 'H/H Growth' if FREQ == 'H' else ('Seq. Growth' if FREQ == 'A' else 'Q/Q Growth')
    if FREQ != 'Q':
        warn('[frequency] ' + ('HALF-YEARLY' if FREQ == 'H' else 'ANNUAL') + ' reporter (' + _S(_g(Q, 'target_q')) +
             '): the sequential sub-row is labelled "' + SEQ_LABEL + '", not "Q/Q". What this changes, ' +
             'and what it does not:\n' +
             '  SAFE, no action - the §5 and §7 fetch windows are PRINT-RELATIVE ("prior print -10d", ' +
             '"print -21d"), so they follow the cadence on their own; the 30-day move, the P/E history ' +
             'and the reaction windows are CALENDAR-based and unaffected; period labels, ranking and ' +
             'the prior-year helper all handle ' + FREQ + ' natively.\n' +
             '  CHECK meta.quarters: for target ' + _S(_g(Q, 'target_q')) + ' the prior-YEAR comparable is ' +
             _S(_or(prior_year_guess(_g(Q, 'target_q')), '(one year back at the same period)')) + ' and the prior ' +
             'PERIOD is the one before it - those are DIFFERENT periods, and setting py_q to the prior ' +
             'period makes the Y/Y and sequential rows print the same number twice (the check below ' +
             'catches that exact case).\n' +
             '  CHECK the print count: 6 prints is 6 QUARTERS for a quarterly filer and ' +
             ('THREE YEARS' if FREQ == 'H' else 'SIX YEARS') + ' for this one. A beat history that old ' +
             'may not measure the same business - fetch 6 if InSync has them, and say in the read-out ' +
             'how far back the series reaches.\n' +
             '  CHECK guidance cadence: an issuer reporting half-yearly often guides ANNUALLY only, ' +
             'in which case Guidance Evolution is legitimately omitted under STRICT RULE 3 (a known ' +
             'limit) and the omission must be disclosed, not left implicit.')
    if _S(_g(Q, 'py_q')) == _S(_g(Q, 'prior_q')):
        warn('[frequency] prior-year period and prior period are the SAME (' + _S(_g(Q, 'py_q')) + '): the two ' +
             'columns would render identical values and the sequential sub-row would duplicate Y/Y. ' +
             'Confirm meta.quarters is correct for this reporting frequency before delivering.')

    count_codes = _HSet(_g(m, 'ltcode') for m in all_codes if _seq(_g(m, 'fmt'), '#'))
    abs_codes = _HSet(_g(m, 'ltcode') for m in all_codes if _seq(_g(m, 'fmt'), '#') or _seq(_g(m, 'fmt'), '$'))
    cur_ledger = {}
    tid_meta = _S(meta['ticker_id'])

    def own(rows, where):
        kept, foreign = [], {}
        for r in rows:
            tv = _g(r, 'TICKER_ID')
            rt = None if (tv is U or tv is None) else _S(tv)
            if rt is not None and rt != tid_meta:
                foreign.setdefault(rt, []).append(_S(_or(_g(r, 'LTCODE'), '?')) + '|' + _S(_or(_g(r, 'FISCALPERIOD'), '?')))
                continue
            kept.append(r)
        for t in js_keys(foreign):
            warn('[ingest] DROPPED ' + _S(len(foreign[t])) + ' row(s) from ' +
                 where + ' belonging to ticker_id ' + t + ', not the subject (' + _S(meta['ticker_id']) + '): ' +
                 ', '.join(foreign[t][0:6]) + (', ...' if len(foreign[t]) > 6 else '') +
                 '. A foreign row here shadows the subject\'s own row for the same code and period and ' +
                 'would have printed that company\'s number in this document. Peer data belongs in ' +
                 'mcp.peers; re-fetch one ticker per call.')
        return kept

    def mcp_market(key):
        mk = _g(mcp, 'market')
        v = _g(mk, key) if _t(mk) else mk
        return v if _t(v) else []
    est = index_estimates(own(extract_rows(_g(mcp, 'estimates_latest')) + extract_rows(_g(mcp, 'revision_series')), 'estimates'), abs_codes, cur_ledger)
    snaps = index_estimates(own(extract_rows(_g(mcp, 'preprint_snapshots')), 'preprint_snapshots'), abs_codes, cur_ledger)
    act = index_actuals(own(extract_rows(_g(mcp, 'actuals')), 'actuals'), abs_codes, cur_ledger, warn)
    px_ledger = {}
    mkt = index_market(extract_rows(mcp_market('current')) + extract_rows(mcp_market('daily_30d')) +
                       extract_rows(mcp_market('reactions')), tid_meta, px_ledger)
    mkt_yr = index_market_summary(extract_rows(mcp_market('yr_summary')))

    def mcp_val(key):
        vv = _g(mcp, 'valuation')
        v = _g(vv, key) if _t(vv) else vv
        return v if _t(v) else []
    val_now = index_valuation(extract_rows(mcp_val('current')))
    tid = tid_meta
    if BPS_SEEN:
        u = _uniq(BPS_SEEN)
        warn('[units] ' + _S(len(BPS_SEEN)) + ' value(s) arrived in BASIS POINTS (' +
             ', '.join(u[0:5]) + (', ...' if len(u) > 5 else '') + ') and were converted to ' +
             'percentage points, because a %-format row stores percentage points and would ' +
             'otherwise have printed a rate 100x too large. 1bp = 0.01% is definitional, but the ' +
             'printed figure is not the string InSync returned, so confirm the issuer quotes bps.')
    if '__conflicts' in mkt:
        cf = mkt['__conflicts']
        warn('[market] TWO DIFFERENT CLOSES for the same date: ' + '; '.join(cf[0:6]) +
             (' (+' + _S(len(cf) - 6) + ' more)' if len(cf) > 6 else '') +
             '. One was kept, but a print reaction is the ratio of two closes, so a wrong close is a ' +
             'wrong reaction and nothing else in the pipeline can detect it. Re-fetch get_market_data ' +
             'for these dates and remove the duplicate before shipping.')
        del mkt['__conflicts']

    mixed_rows, major_count = [], {}
    for lt in js_keys(cur_ledger):
        majors0 = js_keys(cur_ledger[lt]['majors'])
        for mj in majors0:
            major_count[mj] = (major_count.get(mj) or 0) + len(cur_ledger[lt]['majors'][mj])
        if len(majors0) > 1:
            mixed_rows.append(lt + ' [' + ' | '.join(
                mj + ': ' + ','.join(cur_ledger[lt]['majors'][mj][0:4]) for mj in majors0) + ']')
    majors = js_keys(major_count)
    if mixed_rows:
        _err('CURRENCY BASIS ERROR - one or more line items arrived on TWO currency bases,\n' +
             'so their periods cannot be printed in the same row:\n  ' + '\n  '.join(mixed_rows) +
             '\nCause, and there are TWO:\n' +
             '  (a) PER-LTCODE CONVERSION (the usual cause on a non-US issuer). InSync returns\n' +
             '      some line items already converted to USD and leaves others in the local\n' +
             '      currency, on single-ticker calls, and stamps the rate it used in the\n' +
             '      response\'s `meta.conversion`. Verified on a KRW reporter: one line came back\n' +
             '      in USD from get_estimates and in KRW from get_actuals.\n' +
             '  (b) A MULTI-TICKER BATCH. Batching several tickers in one get_estimates /\n' +
             '      get_actuals call converts the whole response to USD.\n' +
             'Fix, in this order:\n' +
             '  1. If any call carried more than one ticker, re-fetch that call with ONE ticker.\n' +
             '     That settles cause (b). If every call was already single-ticker, it is (a).\n' +
             '  2. For cause (a), pick ONE basis for the document - the LOCAL currency - and\n' +
             '     normalise the offending rows into it before pasting, using the rate in that\n' +
             '     response\'s own `meta.conversion` (multiply the USD figure by the rate). Note\n' +
             '     the conversion and the rate in the footer: a converted row is not a reported\n' +
             '     one.\n' +
             '  3. If a code cannot be normalised - no rate returned, or the two bases disagree\n' +
             '     after conversion - DROP that line item from the metric set and re-run Step 0.3.\n' +
             '     A dropped row costs one line; a mixed-basis row is a wrong number on the page.\n' +
             '     Say in the footer which code was dropped and why.\n' +
             'No document is built from mixed-currency data.')
        raise _Exit(3)
    if len(majors) > 1:
        _err('CURRENCY BASIS ERROR - the metric set spans ' + _S(len(majors)) +
             ' currencies (' + ', '.join(majors) + '). Every row of one document must share one\n' +
             'reporting currency. Re-fetch this ticker alone so the MCP returns its local currency\n' +
             'throughout, then re-run.')
        raise _Exit(3)
    major = majors[0] if majors else 'USD'
    minor_codes = [lt for lt in js_keys(cur_ledger) if cur_ledger[lt]['minor']]
    CUR = {'major': major, 'symbol': CUR_SYMBOL.get(major) or (major + ' '), 'minor_codes': minor_codes}
    if CUR['major'] != 'USD':
        warn('[currency] reporting currency is ' + CUR['major'] + '; every money cell and column band ' +
             'is labelled "' + CUR['symbol'] + '" / "' + CUR['symbol'] + 'M" accordingly. Growth rates are ' +
             'local-currency and carry no FX translation. State the currency in the read-out.')
    fx_applied = None
    r_fx = num(_g(meta, 'fx_price_to_report'))
    fx_rate = r_fx if (r_fx is not None and _fin(r_fx) and r_fx > 0) else None
    px_majors = js_keys(_or(_g(_or(px_ledger.get('__price', U), {}), 'majors'), {}))
    declared = _or(_or(_g(meta, 'currency_price'), _and(_g(meta, 'currency'), lambda: _g(meta['currency'], 'price_major'))), None)
    px_major = _or(declared, px_majors[0] if len(px_majors) == 1 else None)
    MKT_CCY = {'TT': 'TWD', 'HK': 'HKD', 'KS': 'KRW', 'JP': 'JPY', 'CH': 'CNY', 'C1': 'CNY',
               'LN': 'GBP', 'GR': 'EUR', 'GY': 'EUR', 'FP': 'EUR', 'NA': 'EUR', 'IM': 'EUR',
               'SM': 'EUR', 'BB': 'EUR', 'SW': 'CHF', 'VX': 'CHF', 'BZ': 'BRL', 'IN': 'INR',
               'AU': 'AUD', 'CN': 'CAD', 'SP': 'SGD', 'SS': 'SEK', 'DC': 'DKK', 'NO': 'NOK',
               'US': 'USD', 'UN': 'USD', 'UQ': 'USD', 'UW': 'USD'}
    suf_m = _exec('\\s([A-Z][A-Z0-9])$', _trim(_S(_or(_g(meta, 'ticker'), ''))).upper())
    want_ccy = MKT_CCY.get(suf_m[1], U) if suf_m else None
    if _t(want_ccy) and _t(px_major) and not _seq(px_major, want_ccy):
        _err('WRONG LISTING - meta.ticker is "' + _S(_g(meta, 'ticker')) + '", whose market prices in ' +
             want_ccy + ', but the price series fetched for this document is in ' + _S(px_major) + '.\n' +
             'That means the ticker_id resolved to a DIFFERENT LISTING than the one requested.\n' +
             'search_tickerid does this silently and still reports success: a local listing code\n' +
             'can return the US ADR line with a high fuzzy score, and that line can read\n' +
             'region "US". There is no exchange field in the response to catch it, and `region`\n' +
             'does not help - an ADR can read "Asia (NJA)" while being the US line.\n' +
             'Why this refuses instead of warning: the substitute line prices in a different\n' +
             'currency from the fundamentals it serves, so EVERY per-share and valuation figure is\n' +
             'wrong by the cross - about 30x on a typical cross. A document like that looks completely normal.\n' +
             'Fix, in order:\n' +
             '  1. Re-run search_tickerid with the LOCAL code AND its suffix ("NESN SW", "MC FP",\n' +
             '     "SAP GR" and "700 HK" all resolve exactly), read back the `ticker` it returns,\n' +
             '     and confirm it is the listing you asked for. Verify by the DATA\'s currency, not\n' +
             '     by the symbol - SAP legitimately returns the bare "SAP" for the European entity.\n' +
             '  2. If only the ADR is in the universe, decide deliberately and say so: either build\n' +
             '     the ADR document (set meta.ticker to the ADR symbol, with no local suffix, so\n' +
             '     this check passes honestly) or stop and tell the user the local line is not\n' +
             '     covered. Do not build a local-ticker document on ADR prices.\n' +
             '  3. If the price series is right and meta.ticker is simply mislabelled, correct\n' +
             '     meta.ticker.\n' +
             'No document is built on a listing that was not asked for.')
        raise _Exit(6)
    if len(px_majors) > 1:
        warn('[currency] the PRICE series itself arrived on ' + _S(len(px_majors)) + ' currency bases (' +
             ', '.join(px_majors) + '). A price history cannot change currency mid-series; one of the ' +
             'windows was fetched for a different listing. Re-fetch get_market_data for the ' +
             'ticker_id this document is built on and re-run.')
    elif _t(px_major) and not _seq(px_major, CUR['major']) and fx_rate is not None:
        fx_note = 'price series converted ' + _S(px_major) + ' -> ' + CUR['major'] + ' at ' + _S(fx_rate) + \
            (' (as of ' + _S(meta['fx_as_of']) + ')' if _t(_g(meta, 'fx_as_of')) else '')
        for k in js_keys(mkt):
            for pt in mkt[k]:
                pt['close'] = pt['close'] * fx_rate
        fx_applied = {'from': px_major, 'to': CUR['major'], 'rate': fx_rate, 'as_of': _or(_g(meta, 'fx_as_of'), None),
                      'source': _or(_g(meta, 'fx_source'), None), 'note': fx_note}
        warn('[currency] PRICE / REPORTING BASIS SPLIT, CONVERTED: this issuer reports in ' +
             CUR['major'] + ' and its listing prices in ' + _S(px_major) + ' (an issuer can report CNY and list ' +
             'HKD). ' + fx_note + ', so the price levels, the 52-week range and every ' +
             'price-over-fundamental ratio on the card are now on one basis. Percentage moves are ' +
             'ratios and are unaffected. Market cap and EV are NOT converted - the payload never says ' +
             'which basis the vendor returns them on - so they are cross-checked against price x FDS ' +
             'instead. STATE IN THE FOOTER that the price basis is CONVERTED, not reported, and give ' +
             'the rate and its date: a converted price is not a quoted one.')
    elif _t(px_major) and not _seq(px_major, CUR['major']):
        warn('[currency] PRICE / REPORTING BASIS MISMATCH - the price series is ' + _S(px_major) +
             ' while every fundamental is ' + CUR['major'] + '. This issuer REPORTS in one currency and ' +
             'LISTS in another (an issuer can report CNY and list HKD; an ADR line prices in USD over ' +
             'local-currency fundamentals). EVERY price-derived line is therefore wrong by the ' +
             _S(px_major) + '/' + CUR['major'] + ' cross: NTM P/E, Market Cap, Enterprise Value, Net Debt, ' +
             'Book Value/Share, Dividend Yield, FCF Yield and the whole valuation card. FIX: either ' +
             'fetch the price series for the LISTING whose currency matches the fundamentals, or - ' +
             'the usual answer, because a dual-listed issuer has no single-currency line - SUPPLY ' +
             'THE RATE and let compute convert it: set `meta.fx_price_to_report` to the multiplier ' +
             'that turns one ' + _S(px_major) + ' into ' + CUR['major'] + ', with `meta.fx_as_of` and ' +
             '`meta.fx_source`, and re-run. The price levels and the 52-week range are then ' +
             'converted, percentage moves are unaffected (they are ratios), and market cap and EV ' +
             'are cross-checked rather than converted. Do NOT ship the card as it stands.')
    elif not _t(px_major) and CUR['major'] != 'USD':
        warn('[currency] the price series carries NO currency token, so the price basis cannot be ' +
             'verified against the ' + CUR['major'] + ' reporting basis - and on a non-USD issuer that is ' +
             'exactly where a listing-vs-reporting split hides (an issuer can report CNY and list HKD; a ' +
             'silently-substituted ADR prices in USD over local fundamentals). Declare it: set ' +
             '`meta.currency_price` to the ISO code of the currency the price series is quoted in, ' +
             'and re-run. If it is not ' + CUR['major'] + ', see the mismatch remedy above.')
    by_code = {}
    for k in js_keys(act):
        i = k.rfind('|')
        if i < 0:
            continue
        code, fp = k[0:i], k[i + 1:]
        m = _exec('^(\\d)([QqHh])(\\d{2})$', fp)
        if not m:
            continue
        half = m[2].upper() == 'H'
        close_q = int(m[1], 10) * 2 if half else int(m[1], 10)
        key = code + '|' + m[3] + '|Q' + _S(close_q)
        by_code.setdefault(key, {})['H' if half else 'Q'] = fp
    clashes = [k.split('|')[0] + ' has both ' + by_code[k]['H'] + ' and ' + by_code[k]['Q']
               for k in js_keys(by_code) if _t(by_code[k].get('H')) and _t(by_code[k].get('Q'))]
    if clashes:
        warn('[cadence] this issuer carries BOTH half-yearly and quarterly actuals for the same ' +
             'period: ' + '; '.join(clashes[0:6]) + ('; +' + _S(len(clashes) - 6) + ' more' if len(clashes) > 6 else '') +
             '. The two rows share a QUARTERENDDATE, one holds ' +
             'a SIX-MONTH value and the other a THREE-MONTH value, and both are marked ' +
             '"Financial Release" - no field tells them apart. THE RULE: align on the FISCALPERIOD ' +
             'STRING, never on QUARTERENDDATE. The MCP\'s own formatting guidance says the opposite, ' +
             'and following it here lifts a half-year figure into a quarterly column - a ~2x ' +
             'overstatement that renders perfectly and trips nothing else. Verify that every period ' +
             'in meta.quarters and meta.growth_bases is the cadence you intend, and say in the ' +
             'footer which cadence the document is built on.')
    if CUR['minor_codes']:
        warn('[currency] quoted in a MINOR unit (cents/pence) by InSync: ' + ', '.join(CUR['minor_codes']) +
             '. The stored value is verified to be MAJOR units (one airline 2Q26: TRASM 0.2400 x ASM 80.000bn = ' +
             'revenue 19.20bn), so these render as ' + CUR['symbol'] + '0.2400 at full precision rather ' +
             'than rescaled. Say "cents" in the read-out if the row name does not.')

    def latest_est(code, fp):
        p = latest_of(est.get(_S(code) + '|' + _S(fp)))
        return p['value'] if p else None

    def latest_est_row(code, fp):
        return latest_of(est.get(_S(code) + '|' + _S(fp)))

    def actual(code, fp):
        v = act.get(_S(code) + '|' + _S(fp), U)
        return None if v is U else v

    def _span_half():
        m = _exec('^(\\d)Q(\\d{2})$', _S(_g(Q, 'target_q')))
        return (('1H' if int(m[1], 10) <= 2 else '2H') + m[2]) if m else None
    span_half = _span_half()
    span_year = _or(_u16slice(_S(_or(_g(FY, 'fy0'), '')), -4), None)

    def any_at(fp):
        return _t(fp) and any(latest_est(_g(m, 'ltcode'), fp) is not None for m in all_codes)
    if not any_at(_g(Q, 'target_q')):
        alt = []
        if any_at(span_half):
            alt.append('HALF-YEARLY (' + span_half + ')')
        if any_at(span_year):
            alt.append('ANNUAL (' + span_year + ')')
        _err('NO CONSENSUS AT THIS CADENCE - not one row of the metric set has a ' +
             'consensus estimate for the target period ' + _S(_g(Q, 'target_q')) + '.\n' +
             'Checked all ' + _S(len(all_codes)) + ' metricset codes; none returned an estimate for it.\n' +
             (('BUT consensus DOES exist at: ' + ' and '.join(alt) + '.\n' +
               'This issuer is covered at a coarser cadence than the one requested - verified on\n' +
               '600519 CH, which has quarterly ACTUALS but only half-yearly and annual ESTIMATES,\n' +
               'and returns `data:[]` (an empty result, not an error) for a quarterly period.\n' +
               'FIX: rebuild at the cadence that has coverage. Set meta.quarters and\n' +
               'meta.growth_bases to ' + (('the half-yearly labels (' +
                                           _S(span_half) + ' as target_q, the prior half as prior_q, the same half a year back as\n' +
                                           'py_q)') if alt[0].find('HALF') == 0 else 'the annual labels') + ', re-run Step 0.3, and say in the footer that the\n' +
               'document is built on that cadence because quarterly consensus does not exist for\n' +
               'this issuer.') if alt else
              ('No consensus exists at the half-yearly (' + _S(span_half) + ') or annual (' + _S(span_year) +
               ') span either, so this is not a cadence problem: either the Step 0 estimate probe\n' +
               'was never pasted into mcp.estimates_latest, or InSync does not cover this issuer\n' +
               'forward at all. Check the probe first; if it is genuinely uncovered, this skill\n' +
               'cannot build a preview for it - say so rather than shipping an empty grid.')) + '\n' +
             'Why this refuses rather than warning: it used to exit OK and ship a document with a\n' +
             'blank consensus column, no beat history, and completeness warnings telling the\n' +
             'operator to fetch estimates that do not exist - a fetch list that can never be\n' +
             'satisfied, presented as though it could.')
        raise _Exit(7)

    tq = _g(Q, 'target_q')
    profit = _g(_or(fin_m[1] if len(fin_m) > 1 else U, {}), 'ltcode')
    hits = [_g(m, 'name') for m in all_codes if actual(_g(m, 'ltcode'), tq) is not None]
    rev_done = actual(REV, tq) is not None
    prof_done = (actual(profit, tq) is not None) if _t(profit) else False
    if rev_done and prof_done:
        _err('SCOPE ERROR - the target period ' + _S(tq) + ' has ALREADY REPORTED: InSync ' +
             'carries actuals for both the top line (' + _S(REV) + ') and the profit line (' + _S(profit) +
             '). This skill builds a PREVIEW, so every column headed "(Estimate)" for ' + _S(tq) +
             ' would be describing a quarter the market has already seen. Use earnings-review for ' +
             'the full post-print grid, earnings-wrap for the narrative, or earnings-flash for a ' +
             'quick read. If ' + _S(tq) + ' has NOT reported and these actuals are a mislabelled ' +
             'restatement, fix meta.quarters.target_q or the actuals fetch and re-run.')
        raise _Exit(5)
    if hits:
        warn('[scope] the target period ' + _S(tq) + ' already has actuals for ' + _S(len(hits)) +
             ' metric set row(s) (' + _join(hits, ', ') + '), while the document presents ' + _S(tq) +
             ' as an estimate. One line reporting early, a pre-announcement or a mislabelled ' +
             'restatement all look like this. Confirm the quarter is genuinely unreported before ' +
             'shipping, and say in the setup which figures are already known.')
    prep = _g(meta, 'prepared')

    def _q_end_scan():
        for bucket in ([mcp[bk] for bk in js_keys(mcp)] if isinstance(mcp, dict) else (mcp if isinstance(mcp, list) else [])):
            if not isinstance(bucket, list):
                continue
            for r in bucket:
                if _t(r) and _seq(_g(r, 'FISCALPERIOD'), tq) and _t(_g(r, 'QUARTERENDDATE')):
                    return _S(r['QUARTERENDDATE'])
        return None
    upcoming = _g(meta, 'upcoming')
    q_end = _or(_and(upcoming, lambda: _g(upcoming, 'quarter_end')), U)
    if not _t(q_end):
        q_end = _q_end_scan()
    held = [x for x in (_and(p, lambda p=p: _g(p, 'call_date')) for p in _as_list(_or(_g(meta, 'prints'), []))) if _t(x)]
    upc = _and(upcoming, lambda: _g(upcoming, 'call_date'))
    after = [d for d in held if _gt(_S(d), _S(q_end))] if _t(q_end) else []
    if _t(q_end) and len(after) >= 1:
        warn('[scope] ' + _S(len(after)) + ' earnings call(s) in meta.prints fall AFTER ' + _S(tq) +
             ' ended (' + _S(q_end) + '): ' + _join(after, ', ') + '. For a preview exactly zero should - ' +
             'the only call after that date is the one being previewed, and it has not happened. ' +
             'Either a later period has already reported and InSync has not ingested it yet, or ' +
             'meta.prints carries the wrong quarter. Confirm with get_call_date before shipping; ' +
             'a preview of a quarter the market has already seen reads as a forecast of the past.')
    if _t(upc) and _t(prep) and _lt(_S(upc), _S(prep)):
        warn('[scope] the expected earnings date (' + _S(upc) + ') is BEFORE the prepared date (' +
             _S(prep) + '), so the masthead invites the reader to a call that has already happened. ' +
             'Re-fetch get_call_date for the next period, or correct meta.prepared.')

    est_rows = extract_rows(_or(_g(mcp, 'estimates_latest'), []))
    if not est_rows:
        warn('[NO CONSENSUS] InSync returned NO estimate rows for ANY metricset code. A preview ' +
             'grades the coming print against consensus, so the target-period column will be entirely ' +
             'blank. Re-fetch get_estimates; if it is genuinely empty, this ticker has no sell-side ' +
             'coverage in InSync and a preview portal should NOT be delivered for it.')
    else:
        tq2 = _S(_g(meta['quarters'], 'target_q'))
        if not any(_S(_g(r, 'FISCALPERIOD')) == tq2 for r in est_rows):
            warn('[NO CONSENSUS] no estimate row for the TARGET period ' + tq2 + ' on any metricset ' +
                 'code - the whole consensus column renders blank. Re-fetch before delivering.')

    px = latest_of(mkt.get(tid))
    yr = _or(mkt_yr.get(tid, U), {})
    if fx_applied:
        if _isnum(yr.get('max', U)):
            yr['max'] = yr['max'] * fx_applied['rate']
        if _isnum(yr.get('min', U)):
            yr['min'] = yr['min'] * fx_applied['rate']
        if _isnum(yr.get('mean', U)):
            yr['mean'] = yr['mean'] * fx_applied['rate']
    bs = index_actuals(extract_rows(_g(mcp, 'balance_sheet')), count_codes)

    def bs_at(code):
        pre = _S(code) + '|'
        k = next((k for k in js_keys(bs) if k.startswith(pre)), None)
        return bs[k] if k is not None else None
    mkt_cap = _or(val_now.get(tid + '|Market Cap', U), None)
    DEBT_INCL = 'BS_P_108'
    dl0 = [DEBT_INCL, _g(MS, 'debt_ltcode'), 'BS_P_114', 'BS_P_115', 'BS_P_107']
    DEBT_LADDER = [x for i, x in enumerate(dl0) if _t(x) and _index_of(dl0, x) == i]
    debt_incl = bs_at(DEBT_INCL)
    debt_code, debt = None, None
    for c in DEBT_LADDER:
        v = bs_at(c)
        if v is not None:
            debt_code, debt = c, v
            break
    if debt_code is None:
        debt_code = _or(_g(MS, 'debt_ltcode'), DEBT_INCL)
        debt = bs_at(debt_code)
    debt_label = 'Total Debt (incl-leases)' if _seq(debt_code, DEBT_INCL) else \
        ('Total Debt (ex-leases)' if _test('114|115|Ex-?Operating', _S(debt_code), 'i') else 'Total Debt')
    if debt_incl is None and debt is not None:
        warn('[valuation] no incl-operating-lease debt (' + DEBT_INCL + ') in balance_sheet for this ' +
             'ticker, so the EV bridge walked the debt ladder to ' + _S(debt_code) + ' and the card is ' +
             'labelled "' + debt_label + '" accordingly. Check the ev_gap line below before ' +
             're-fetching: if the bridge reconciles, the basis difference is immaterial for this ' +
             'issuer and no re-fetch is needed. If it does not, and InSync answers ' + DEBT_INCL +
             ' with "MISSING DATA ... returned no results" (seen on issuers that never report it), the code ' +
             'does not exist for this ticker and no re-fetch can ever clear this - disclose the ' +
             'basis and the gap in the footer instead.')
    if debt is None:
        warn('[valuation] NO DEBT CODE AT ALL in balance_sheet - the ladder tried ' +
             _join(DEBT_LADDER, ', ') + ' and found none. Net Debt and the EV bridge cannot be built, ' +
             'so the card falls back to the vendor EV and its own lines will not reconcile. Fetch a ' +
             'total-debt actual (§3) for the latest quarter and re-run; this is a genuine fetch gap, ' +
             'not a structural absence.')
    CASH_EV = 'BS_P_101'
    cash_ev = bs_at(CASH_EV)
    cash_code0 = _or(_g(MS, 'cash_ltcode'), CASH_EV)
    cash = cash_ev if cash_ev is not None else bs_at(cash_code0)
    cash_code = CASH_EV if cash_ev is not None else cash_code0
    if cash_ev is None and not _seq(cash_code0, CASH_EV):
        warn('[valuation] no ' + CASH_EV + ' (Cash and Cash Equivalents) in balance_sheet; the EV ' +
             'bridge falls back to ' + _S(cash_code0) + ', which may exclude marketable securities and so ' +
             'will not reconcile with the InSync EV -> fetch ' + CASH_EV + ' actuals for the latest ' +
             'quarter. This is the CASH-side twin of the BS_P_108 rule in data-collection.md §3.')
    equity, fds = bs_at('BS_P_113'), bs_at('IS_P_125')
    bvps_vendor = bs_at('BS_P_254')
    bvps_computed = _div(equity, fds) if (equity is not None and _t(fds)) else None
    if (bvps_computed is not None and bvps_vendor is not None and
            abs(_sub(bvps_computed, bvps_vendor)) > abs(bvps_vendor) * 0.005):
        warn('[valuation] Book Value/Share is computed as BS_P_113 / IS_P_125 = ' +
             _S(_round(bvps_computed, 2)) + ' on a FULLY DILUTED share count, while InSync\'s own BS_P_254 ' +
             '"BVPS" for the same quarter is ' + _S(_round(bvps_vendor, 2)) + ', which is on COMMON shares. ' +
             'The card prints the diluted figure because that is the basis every other per-share cell ' +
             'in this document uses (EPS is diluted, so a book value on a different count would not ' +
             'compare). Say "diluted" wherever BVPS or P/B is quoted; the ' +
             _S(_round(_div(100 * abs(_sub(bvps_computed, bvps_vendor)), abs(bvps_vendor)), 1)) +
             '% difference is the dilution, not an error.')
    net_debt = _sub(debt, cash) if (debt is not None and cash is not None) else None
    ev_bridge = mkt_cap + net_debt if (mkt_cap is not None and net_debt is not None) else None
    ev_insync = _or(val_now.get(tid + '|EV', U), None)
    ev = ev_bridge if ev_bridge is not None else ev_insync
    ev_from_vendor = (ev_bridge is None and ev_insync is not None)
    if ev_from_vendor:
        warn('[valuation] the EV BRIDGE COULD NOT BE BUILT (' +
             ('no market cap' if mkt_cap is None else 'no net debt: debt ' + ('absent' if debt is None else 'ok') +
              ', cash ' + ('absent' if cash is None else 'ok')) + '), so Enterprise Value on the card is ' +
             'the VENDOR figure and the card\'s own Market Cap / Debt / Cash / Net Debt lines do NOT ' +
             'add up to it. SKILL.md requires the printed EV to be the bridge so the card reconciles. ' +
             'Fix the missing leg (see the debt-ladder warning above) or state on the page that EV is ' +
             'vendor-supplied and unreconciled - never leave the reader to add the lines up.')
    ev_gap = None
    if ev_bridge is not None and ev_insync is not None:
        ev_gap = _round(ev_bridge - ev_insync, 0)
        tol = max(abs(ev_insync) * 0.001, 50)
        if abs(_n(ev_gap)) > tol:
            warn('[valuation] EV BRIDGE MISMATCH: market cap ' + _S(_round(mkt_cap, 0)) + ' + net debt ' +
                 _S(_round(net_debt, 0)) + ' = ' + _S(_round(ev_bridge, 0)) + ', but InSync EV is ' + _S(_round(ev_insync, 0)) +
                 ' (gap ' + _S(ev_gap) + ', ' + _S(_round(_div(_mul(100, ev_gap), ev_insync), 3)) + '%). The card prints the ' +
                 'BRIDGE so its six lines reconcile. Legs in use: debt=' + _S(debt_code) + ' (' + debt_label +
                 '), cash=' + _S(cash_code) + '. ' +
                 ('BOTH legs are already on the basis InSync EV uses, so this residual is NOT a ' +
                  'debt/cash basis difference and no re-fetch will close it: it is non-controlling ' +
                  'interests, preferred stock or an equity-method/tax-equity structure that the ' +
                  'vendor includes in EV and a two-leg bridge cannot. Disclose the basis and the gap ' +
                  'in the report rather than chasing it.'
                  if (debt_incl is not None and _seq(cash_code, CASH_EV)) else
                  'Fetch ' + (DEBT_INCL + ' (Total Debt incl-operating-lease)' if debt_incl is None else CASH_EV +
                              ' (Cash and Cash Equivalents)') + ' actuals for the latest quarter - that is the ' +
                  'basis InSync EV uses - then re-run. Disclose the basis if a gap remains.'))
    vn = val_now
    pe_adj, pe_gaap = vn.get(tid + '|P/E_Adj_Ratio', U), vn.get(tid + '|P/E_Ratio', U)
    eve_adj, eve_gaap = vn.get(tid + '|EV/EBITDA_Adj', U), vn.get(tid + '|EV/EBITDA', U)
    valuation = {
        'asof': _g(meta['asof'], 'market'), 'price': px['close'] if px else None, 'price_date': px['date'] if px else None,
        'market_cap': _round(mkt_cap, 0), 'total_debt': _round(debt, 0), 'debt_label': debt_label,
        'cash': _round(cash, 0), 'cash_code': cash_code,
        'net_debt': _round(net_debt, 0), 'ev': _round(ev, 0), 'ev_insync': _round(ev_insync, 0), 'ev_gap': ev_gap,
        'ev_basis': 'vendor (bridge unbuildable)' if ev_from_vendor else 'bridge', 'debt_ladder_used': debt_code,
        'wk52_high': _or(yr.get('max', U), None), 'wk52_low': _or(yr.get('min', U), None),
        'pct_off_high': _round(pct_change(px['close'], yr['max']), 1) if (px and _t(yr.get('max', U))) else None,
        'pct_above_low': _round(pct_change(px['close'], yr['min']), 1) if (px and _t(yr.get('min', U))) else None,
        'bvps': None if bvps_computed is None else _round(bvps_computed, 2),
        'bvps_basis': None if bvps_computed is None else 'diluted (BS_P_113 / IS_P_125)',
        'bvps_vendor': None if bvps_vendor is None else _round(bvps_vendor, 2),
        'dividend_yield': vn[tid + '|Dividend_Yield'] if (tid + '|Dividend_Yield') in vn else None,
        'fcf_yield_ntm': _round(_mul(vn[tid + '|FCF_Yield'], 100), 1) if (tid + '|FCF_Yield') in vn else None,
        'ntm_pe': _round(pe_adj if pe_adj is not U else pe_gaap, 4),
        'ntm_pe_basis': 'Adj' if pe_adj is not U else ('GAAP' if pe_gaap is not U else None),
        'ntm_ev_ebitda': _round(eve_adj if eve_adj is not U else eve_gaap, 4),
        'ntm_ev_ebitda_basis': 'Adj' if eve_adj is not U else ('GAAP' if eve_gaap is not U else None)
    }
    LENS_PCT = {'Dividend_Yield', 'FCF_Yield', 'Earnings_Yield'}
    lens_sib = {'P/E_Adj_Ratio': 'P/E_Ratio', 'P/E_Ratio': 'P/E_Adj_Ratio',
                'EV/EBITDA_Adj': 'EV/EBITDA', 'EV/EBITDA': 'EV/EBITDA_Adj'}

    def lens_val(m):
        own_v = vn.get(tid + '|' + _S(m), U)
        if own_v is not U:
            return {'v': own_v, 'basis': 'Adj' if _test('_Adj', m) else 'GAAP', 'metric': m}
        sib = lens_sib.get(m, U) if isinstance(m, str) else U
        if _t(sib) and (tid + '|' + sib) in vn:
            return {'v': vn[tid + '|' + sib], 'basis': 'Adj' if _test('_Adj', sib) else 'GAAP', 'metric': sib}
        return None
    lenses = []
    seen_lens = []

    def push_lens(label, metric, concept=U):
        if not _t(metric) or any(_same_value_zero(metric, s) for s in seen_lens):
            return
        seen_lens.append(metric)
        got = lens_val(metric)
        is_pct = isinstance(metric, str) and metric in LENS_PCT
        lenses.append({
            'label': label, 'metric': metric, 'requested': metric,
            'resolved_metric': got['metric'] if got else None, 'basis': got['basis'] if got else None,
            'kind': 'pct' if is_pct else 'x',
            'value': _round(_mul(got['v'], 100) if is_pct else got['v'], 1 if is_pct else 4) if got else None,
            'nm': False, 'nm_reason': None
        })
        if _t(concept):
            lenses[-1]['concept'] = concept
    for mp in _as_list(_or(_g(MS, 'multiples'), []))[0:3]:
        push_lens(_g(mp, 'label'), _g(mp, 'ntm_metric'), _g(mp, 'concept'))
    if 'Dividend_Yield' not in seen_lens:
        push_lens('Dividend Yield', 'Dividend_Yield')
    if ('FCF_Yield' not in seen_lens and (tid + '|FCF_Yield') in vn and _gt(vn[tid + '|FCF_Yield'], 0)):
        push_lens('FCF Yield (NTM)', 'FCF_Yield')
    lens_gone = [L for L in lenses if L['value'] is None]
    if fx_applied and _t(valuation['market_cap']) and px and _t(fds):
        implied_mc = px['close'] * fds
        mc_ratio = _div(implied_mc, valuation['market_cap'])
        if mc_ratio < 0.8 or mc_ratio > 1.25:
            warn('[currency] MARKET CAP BASIS: after converting the price series to ' + CUR['major'] +
                 ', price x FDS implies a market cap of ' + _S(_round(implied_mc, 0)) + ' against the vendor\'s ' +
                 _S(_round(valuation['market_cap'], 0)) + ' (a factor of ' + _S(_round(mc_ratio, 2)) + '). The two are not ' +
                 'on the same basis: the vendor figure is almost certainly still in ' + _S(fx_applied['from']) + ', ' +
                 'in which case Market Cap, Enterprise Value, Net Debt and every yield on the card sit on ' +
                 'the LISTING basis while the price beside them sits on the REPORTING basis. Multiply the ' +
                 'vendor market cap and EV by ' + _S(fx_applied['rate']) + ' before pasting, or state both bases ' +
                 'explicitly in the footer. Do not read the card as internally consistent.')
    if fx_applied:
        valuation['fx'] = fx_applied
    valuation['lenses'] = [L for L in lenses if L['value'] is not None]

    fm = _as_list(_or(_g(MS, 'fin_metrics'), []))

    def is_fte(r):
        return _test('\\bFTE\\b', _S(_or(_g(_or(r, {}), 'name'), '')), 'i')
    prof = fm[1] if len(fm) > 1 else U
    marg = next((r for r in fm if _t(r) and _seq(_g(r, 'fmt'), '%') and r is not fm[0]), U)
    if _t(prof) and _t(marg) and is_fte(prof) != is_fte(marg):
        warn('[metricset] BASIS PAIRING: the profit line "' + _S(_g(prof, 'name')) + '" (' + _S(_g(prof, 'ltcode')) +
             ') and the margin "' + _S(_g(marg, 'name')) + '" (' + _S(_g(marg, 'ltcode')) + ') do NOT share an ' +
             'accounting basis - one is FTE and the other is not. The printed margin cannot be ' +
             'reproduced from the printed profit over the printed revenue, and a reader checking ' +
             'the card cannot tell why. This is a FETCH gap, not a resolver choice: re-run Step 0 ' +
             'with data-collection.md §2 batch 4 (which names "efficiency ratio FTE" explicitly) ' +
             'and batch 7 (the profit line\'s -M twin, UNFILTERED), then re-resolve. If the ' +
             'issuer genuinely carries neither, say so in the footer.')

    def lens_concept(m):
        return 'pe' if _test('^P\\/E', _S(m)) else ('ev_ebitda' if _test('^EV\\/EBITDA', _S(m)) else None)
    lens_pe = next((L for L in valuation['lenses'] if lens_concept(L['resolved_metric']) == 'pe'), U)
    lens_ev = next((L for L in valuation['lenses'] if lens_concept(L['resolved_metric']) == 'ev_ebitda'), U)
    if (_t(lens_pe) and lens_pe['value'] is not None and
            (abs(js_number(valuation['ntm_pe']) - js_number(lens_pe['value'])) > 0.005 or
             not _seq(valuation['ntm_pe_basis'], lens_pe['basis']))):
        warn('[valuation] BASIS UNIFIED: the Earnings Setup NTM P/E was ' + _S(valuation['ntm_pe']) +
             'x on an ' + _S(valuation['ntm_pe_basis']) + ' basis while the sector lens "' + _S(lens_pe['label']) +
             '" is ' + _S(lens_pe['value']) + 'x on a ' + _S(lens_pe['basis']) + ' basis. The lens owns the basis, so ' +
             'the card, the Earnings Setup line and the P/E history now all read ' + _S(lens_pe['basis']) +
             '. Nothing was re-fetched; one of the two figures the page used to print is gone.')
        valuation['ntm_pe'] = lens_pe['value']
        valuation['ntm_pe_basis'] = lens_pe['basis']
    if _t(lens_ev) and lens_ev['value'] is not None:
        valuation['ntm_ev_ebitda'] = lens_ev['value']
        valuation['ntm_ev_ebitda_basis'] = lens_ev['basis']

    if not _t(lens_pe) and valuation['ntm_pe'] is not None and valuation['ntm_pe'] is not U:
        warn('[valuation] LENS SCOPE: the ' + _S(_or(_g(MS, 'classification'), 'sector')) +
             ' lens set does not list P/E, so the Earnings Setup NTM P/E (' + _S(valuation['ntm_pe']) +
             'x) and its one-year history are SUPPRESSED rather than printed beside a lens that ' +
             'measures something else. Say in the read-out which multiple the sector convention ' +
             'uses instead. To print a P/E for this issuer, add it to the rule in ' +
             'assets/valuation_rules.json - deliberately, and it will then own the basis.')
        valuation['ntm_pe'] = None
        valuation['ntm_pe_basis'] = None

    if _t(valuation['bvps']) and px and _t(px['close']) and len(_or(CUR['minor_codes'], [])) == 0:
        pb_lens = next((L for L in valuation['lenses'] if _test('^P\\/B', _S(_or(L['resolved_metric'], '')))), U)
        if _t(pb_lens) and _t(pb_lens['value']):
            implied = _div(px['close'], valuation['bvps'])
            valuation['bvps_implied_by_pb'] = _round(_div(px['close'], pb_lens['value']), 2)
            if abs(_div(implied, pb_lens['value']) - 1) > 0.02:
                valuation['bvps_basis'] = _S(valuation['bvps_basis']) + ' - TOTAL equity, incl. preferred'
                warn('[valuation] BOOK VALUE BASIS: the card prints Book Value/Share ' +
                     _S(_round(valuation['bvps'], 2)) + ' (' + _S(valuation['bvps_basis']) + ') beside a "' + _S(pb_lens['label']) +
                     '" of ' + _S(pb_lens['value']) + 'x, but price / BVPS is ' + _S(_round(implied, 2)) + 'x - ' +
                     _S(_round(abs(_div(implied, pb_lens['value']) - 1) * 100, 1)) + '% apart, so the card does not ' +
                     'reconcile to itself. The vendor ratio rests on COMMON equity; BS_P_113 is TOTAL ' +
                     'equity and includes preferred, which is why every bank shows this. The ' +
                     'book value per share implied by that ratio is ' + _S(valuation['bvps_implied_by_pb']) +
                     ((', against InSync\'s own BS_P_254 BVPS of ' + _S(valuation['bvps_vendor']))
                      if (valuation['bvps_vendor'] is not None and valuation['bvps_vendor'] is not U) else '') +
                     '. The row now names its ' +
                     'basis; to print a common-equity BVPS, fetch a preferred-equity code per ' +
                     'data-collection.md §3 and subtract it.')

    mixed = (valuation['ntm_pe'] is not None and valuation['ntm_ev_ebitda'] is not None and
             valuation['ntm_ev_ebitda'] is not U and _t(valuation['ntm_pe_basis']) and
             _t(valuation['ntm_ev_ebitda_basis']) and
             not _seq(valuation['ntm_pe_basis'], valuation['ntm_ev_ebitda_basis']))
    valuation['ntm_row_mixed_basis'] = True if mixed else U
    if lens_gone:
        warn('[valuation] the sector lens set asks for ' + ', '.join(_S(L['label']) +
             ' (' + _S(L['requested']) + ')' for L in lens_gone) + ' and InSync returns no such series for this ' +
             'ticker, so the slot is DROPPED rather than dashed. The card shows ' +
             _S(len(valuation['lenses'])) + ' lens(es); say in the read-out which lens the sector ' +
             'convention wanted and could not be shown.')
    if (valuation['ntm_ev_ebitda'] is not None and valuation['ntm_ev_ebitda'] is not U and
            valuation['ntm_ev_ebitda_basis'] == 'GAAP' and valuation['ntm_pe_basis'] == 'Adj'):
        warn('[valuation] the card mixes bases: EV/EBITDA is REPORTED (GAAP) because InSync carries ' +
             'no EV/EBITDA_Adj for this ticker, while the NTM P/E beside it is Ex-1x. Two multiples on ' +
             'one card measured differently - label both bases in the footer and do not compare either ' +
             'to a history on the other basis.')
    if valuation['ntm_pe'] is not None and valuation['ntm_pe'] is not U and valuation['ntm_pe_basis'] == 'GAAP':
        warn('[valuation] NTM P/E shown on a REPORTED (GAAP) basis; InSync carries no P/E_Adj_Ratio ' +
             'series for this ticker, while the EPS row and EV/EBITDA on the same card are Ex-1x - ' +
             'label the basis in the footer and do not compare it to the adjusted P/E history')
    if valuation['fcf_yield_ntm'] is not None and valuation['fcf_yield_ntm'] < 0:
        warn('[valuation] FCF Yield is negative (' + _S(valuation['fcf_yield_ntm']) + '%) - rendered NM, not as a ' +
             'headline multiple. Negative free cash flow is structural for this issuer, not a valuation signal')
        valuation['fcf_yield_ntm'] = None
        valuation['fcf_yield_nm'] = True
    EBITDA_LESS = 'bank|insur|reit|real estate|thrift|savings|asset manage|capital market|specialty\\s*finance|consumer\\s*finance|diversified\\s*financ|institutional\\s*financ|broker|exchange|financ'
    ms_or = _or(MS, {})
    cls = ' / '.join(_uniq([x for x in [_trim(_S(_or(_g(ms_or, 'classification'), ''))),
                                        _trim(_S(_or(_g(ms_or, 'industry'), '')))] if x]))
    confirmed = set(_S(x) for x in _as_list(_or(_g(meta, 'absent_confirmed'), [])))

    def gap(field, msg):
        if field in confirmed:
            warn('[absent] ' + field + ' is missing and the operator has recorded it as PROVED ABSENT ' +
                 'in meta.absent_confirmed (re-fetched, no rows returned). The cell prints "—"; state ' +
                 'this in the report as an absence in InSync, not as a value withheld.')
            return
        warn('[completeness] ' + msg)
    if valuation['price'] is not None:
        if valuation['ntm_pe'] is None or valuation['ntm_pe'] is U:
            gap('ntm_pe', 'valuation NTM P/E missing -> fetch get_valuation metrics=P/E_Adj_Ratio,P/E_Ratio mode=NTM into valuation.current (BOTH codes: many issuers carry only the Adj series)')
        if valuation['ntm_ev_ebitda'] is None or valuation['ntm_ev_ebitda'] is U:
            if _test(EBITDA_LESS, cls, 'i'):
                warn('[absent] valuation EV/EBITDA is not available for ' +
                     ('an ' if _test('^[aeiou]', _or(_trim(cls), 'financial'), 'i') else 'a ') + _or(_trim(cls), 'financial') +
                     ' issuer: there is no EBITDA to capitalise, which is the same reason the metric set ' +
                     'resolved its profit line through the bank ladder. This is a structural absence, not a ' +
                     'fetch gap, so it does NOT block the build - say so in the report.')
            else:
                gap('ntm_ev_ebitda', 'valuation EV/EBITDA missing -> fetch get_valuation metrics=EV/EBITDA,EV/EBITDA_Adj mode=NTM into valuation.current')
        if valuation['market_cap'] is None or valuation['market_cap'] is U:
            gap('market_cap', 'valuation Market Cap missing -> fetch get_valuation metrics=Market_Cap,EV into valuation.current')
        if (valuation['fcf_yield_ntm'] is None or valuation['fcf_yield_ntm'] is U) and \
                not _t(valuation.get('fcf_yield_nm', U)):
            if not _t(MS) or _g(MS, 'fcf_ltcode') is None:
                warn('[absent] FCF Yield is not available: the Step 0 probe proved this issuer carries no ' +
                     'FCF consensus (fcf_ltcode is null), which is why there is no Free Cash Flow row either. ' +
                     'Structural, not a fetch gap.')
            else:
                gap('fcf_yield_ntm', 'FCF Yield (NTM) missing -> fetch get_valuation metrics=FCF_Yield mode=NTM into valuation.current (only a no-rows re-fetch may leave this blank)')
        if (valuation['fcf_yield_ntm'] is not None and valuation['fcf_yield_ntm'] is not U and
                _t(MS) and _g(MS, 'fcf_ltcode') is None):
            warn('[valuation] FCF YIELD UNCORROBORATED: the card prints FCF Yield (NTM) ' +
                 pct1(valuation['fcf_yield_ntm']) + '% from the vendor, but this issuer has no FCF ' +
                 'consensus (the Step 0 probe set fcf_ltcode to null), so there is no Free Cash Flow ' +
                 'row on the page and the lens cannot be cross-checked against the document\'s own ' +
                 'cells the way every other lens is. Say in the read-out and the footer that the yield ' +
                 'is vendor-supplied and uncorroborated, and on what FCF definition if the vendor states one.')
        if valuation['bvps'] is None:
            gap('bvps', 'Book Value/Share missing -> fetch actuals BS_P_113 (Total Equity) + IS_P_125 (FDS), latest quarter, into balance_sheet')

    mkt30 = index_market(extract_rows(mcp_market('daily_30d')), tid)
    d30 = _or(mkt30.get(tid, U), [])
    setup = None
    if len(d30) >= 2:
        first, last = d30[0], d30[-1]
        lo, hi_vol = d30[0], d30[0]
        for p in d30:
            if p['close'] < lo['close']:
                lo = p
            if p['volume'] > hi_vol['volume']:
                hi_vol = p
        pe_rows = extract_rows(mcp_val('pe_summary'))

        def mean_of(r):
            v = _g(r, 'mean')
            return v if v is not U else _g(r, 'MEAN')

        def is_pe(r):
            return _test('^P\\/E(_|$)|price.?earnings', _S(_or(_or(_g(r, 'metric'), _g(r, 'METRIC')), '')), 'i')

        def is_adj(r):
            return _test('Adj', _S(_or(_or(_g(r, 'metric'), _g(r, 'METRIC')), '')), 'i')
        pe_cands = [r for r in pe_rows if mean_of(r) is not U]
        want_adj_hist = valuation['ntm_pe_basis'] != 'GAAP'
        pe_pe = [r for r in pe_cands if is_pe(r)]
        first_a = next((r for r in pe_pe if is_adj(r)), U)
        first_g = next((r for r in pe_pe if not is_adj(r)), U)
        pe_sum = _or(_or(_or(_or(first_a if want_adj_hist else first_g, first_a),
                             next((r for r in pe_cands if is_pe(r)), U)),
                         pe_cands[0] if pe_cands else U), {})
        if not pe_rows or not [r for r in pe_rows if mean_of(r) is not U]:
            warn('[setup] pe_summary is EMPTY - no P/E history series came back - so "NTM P/E vs its ' +
                 'own 1-yr average" and the year-ago anchor are both absent from the Earnings Setup line. ' +
                 'Two causes: (1) the §3 call was not made or was made with the wrong shape - re-run ' +
                 'get_valuation metrics=P/E_Adj_Ratio,P/E_Ratio mode=NTM start_date=1y summarize=true; ' +
                 '(2) InSync genuinely carries no P/E series for this ticker, which is what happened on ' +
                 'both a REIT and a Korean listing. If it is (2), try the lens the SECTOR actually uses ' +
                 '(P/FCF_Ratio for a REIT, P/B_Ratio for a bank or insurer) and say in the read-out that ' +
                 'the valuation-history anchor is on that lens, not P/E. Do not leave the line silent.')
        if [r for r in pe_rows if mean_of(r) is not U] and not any(is_pe(r) for r in pe_rows):
            warn('[setup] pe_summary carries no P/E series - "1-yr avg P/E" falls back to ' +
                 _S(_or(_or(_g(pe_sum, 'metric'), _g(pe_sum, 'METRIC')), 'the first row')) + '; fetch get_valuation metrics=P/E_Adj_Ratio mode=NTM start_date=1y summarize=true')
        pe_monthly_all = [r for r in extract_rows(mcp_val('pe_monthly'))
                          if _test('^P\\/E(_Adj)?_Ratio$', _S(_or(_g(r, 'metric'), '')), 'i')]
        mon_adj = [r for r in pe_monthly_all if _test('Adj', _S(_g(r, 'metric')), 'i')]
        mon_gaap = [r for r in pe_monthly_all if not _test('Adj', _S(_g(r, 'metric')), 'i')]
        pe_monthly = (mon_adj if mon_adj else mon_gaap) if want_adj_hist else (mon_gaap if mon_gaap else mon_adj)
        pe_monthly = js_cmp_sort(pe_monthly, lambda a, b: -1 if _lt(_g(a, 'DATE'), _g(b, 'DATE')) else 1)

        def _pe_year_ago_row():
            if not pe_monthly:
                return None
            asof = _or(_and(px, lambda: px['date']), _g(pe_monthly[-1], 'DATE'))
            target = _add_days_iso(asof, -365)

            def days(a, b):
                return abs(_div(_date_ms(a) - _date_ms(b), 86400000))
            best = pe_monthly[0]
            for r in pe_monthly[1:]:
                best = r if days(_g(r, 'DATE'), target) < days(_g(best, 'DATE'), target) else best
            if days(_g(best, 'DATE'), asof) < 300:
                warn('[setup] the P/E history reaches back only to ' + _S(_g(best, 'DATE')) + ', ' +
                     _S(js_round(days(_g(best, 'DATE'), asof))) + ' days before the ' + _S(asof) + ' close, so there is no ' +
                     'year-ago anchor to compare against. pe_year_ago is left BLANK rather than labelling a ' +
                     'recent observation "a year ago". PAGINATE the §3 pe_monthly call (it is a DAILY series, ' +
                     '250-500 rows, has_more=true on page 1) and re-run.')
                return None
            return best
        pe_year_ago_row = _pe_year_ago_row()

        def _move_30d():
            cutoff = _add_days_iso(last['date'], -30)
            inwin = [x for x in d30 if _le(x['date'], cutoff)]
            anchor = inwin[-1] if inwin else d30[0]
            if anchor is d30[0] and _gt(d30[0]['date'], cutoff):
                warn('[setup] the market series starts ' + _S(d30[0]['date']) + ', later than 30 days before ' +
                     _S(last['date']) + ', so "30-day move" is measured over the ' +
                     _S(js_round(_div(_date_ms(last['date']) - _date_ms(d30[0]['date']), 86400000))) + ' days available. ' +
                     'Fetch get_market_data with start_date="45d" per data-collection.md §3 and say so if it ' +
                     'still cannot reach back.')
            return _round(pct_change(last['close'], anchor['close']), 1)
        mv = _move_30d()
        has_vol = any(x['volume'] > 0 for x in d30)
        pm1 = _g(pe_sum, 'mean')
        setup = {
            'move_30d_pct': mv,
            'drawdown_low': lo['close'], 'drawdown_date': lo['date'],
            'heaviest_volume_shares_m': _round(hi_vol['volume'] / 1e6, 1) if has_vol else None,
            'heaviest_volume_date': hi_vol['date'] if has_vol else None,
            'pe_now': valuation['ntm_pe'], 'pe_1yr_avg': _round(pm1 if pm1 is not U else _g(pe_sum, 'MEAN'), 4),
            'pe_year_ago': _round(_g(pe_year_ago_row, 'value'), 4) if pe_year_ago_row else None
        }
    else:
        warn('[setup] fewer than 2 daily closes in 30d window')

    def margin_row_exists_for(code):
        c = _S(code) + '-M'
        return any(_seq(_g(m, 'ltcode'), c) for m in all_codes)

    cols = {'py_q': _g(Q, 'py_q'), 'prior_q': _g(Q, 'prior_q'), 'target_q': _g(Q, 'target_q'), 'next_q': _g(Q, 'next_q'),
            'fy_prev': _g(FY, 'fy_prev'), 'fy0': _g(FY, 'fy0'), 'fy1': _g(FY, 'fy1')}
    sf = {'columns': cols, 'rows': [], 'rev_ltcode': REV, 'eps_ltcode': _or(EPS, None)}

    def cell(code, fp, is_actual):
        return actual(code, fp) if is_actual else latest_est(code, fp)
    for i, m in enumerate(all_codes):
        lt = _g(m, 'ltcode')
        r = {
            'name': _g(m, 'name'), 'ltcode': lt, 'group': 'fin' if i < len(fin_m) else 'kpi', 'fmt': _or(_g(m, 'fmt'), '$M'),
            'polarity': _or(_g(m, 'polarity'), 'higher'),
            'flow': _g(m, 'flow'),
            'pctrev': _g(m, 'pctrev'),
            'py_q': cell(lt, _g(Q, 'py_q'), True), 'prior_q': cell(lt, _g(Q, 'prior_q'), True),
            'target_q': cell(lt, _g(Q, 'target_q'), False), 'next_q': cell(lt, _g(Q, 'next_q'), False),
            'fy_prev': cell(lt, _g(FY, 'fy_prev'), True), 'fy0': cell(lt, _g(FY, 'fy0'), False), 'fy1': cell(lt, _g(FY, 'fy1'), False)
        }
        sf['rows'].append(r)
    FCF = _g(MS, 'fcf_ltcode')
    if _t(FCF) and not any(_seq(_g(m, 'ltcode'), FCF) for m in all_codes):
        sf['rows'].insert(len(fin_m), {
            'name': 'Free Cash Flow', 'ltcode': FCF, 'group': 'fin', 'fmt': '$M', 'polarity': 'higher',
            'py_q': actual(FCF, _g(Q, 'py_q')), 'prior_q': actual(FCF, _g(Q, 'prior_q')),
            'target_q': latest_est(FCF, _g(Q, 'target_q')), 'next_q': latest_est(FCF, _g(Q, 'next_q')),
            'fy_prev': actual(FCF, _g(FY, 'fy_prev')), 'fy0': latest_est(FCF, _g(FY, 'fy0')), 'fy1': latest_est(FCF, _g(FY, 'fy1'))
        })

    def growth_for(code, bases):
        pq_base = _g(bases, 'py_q_base')
        pairs = {
            'yy': {
                'py_q': [actual(code, _g(Q, 'py_q')), pq_base if pq_base is not U else actual(code, _g(bases, 'py_q_prior_year'))],
                'prior_q': [actual(code, _g(Q, 'prior_q')), actual(code, _g(bases, 'prior_q_prior_year'))],
                'target_q': [latest_est(code, _g(Q, 'target_q')), actual(code, _g(Q, 'py_q'))],
                'next_q': [latest_est(code, _g(Q, 'next_q')), actual(code, _g(bases, 'next_q_prior_year'))],
                'fy_prev': [actual(code, _g(FY, 'fy_prev')), actual(code, _g(bases, 'fy_prev_prior'))],
                'fy0': [latest_est(code, _g(FY, 'fy0')), actual(code, _g(FY, 'fy_prev'))],
                'fy1': [latest_est(code, _g(FY, 'fy1')), latest_est(code, _g(FY, 'fy0'))]
            },
            'qq': {
                'py_q': [actual(code, _g(Q, 'py_q')), actual(code, _g(bases, 'py_q_prev_q'))],
                'prior_q': [actual(code, _g(Q, 'prior_q')), actual(code, _g(bases, 'prior_q_prev_q'))],
                'target_q': [latest_est(code, _g(Q, 'target_q')), actual(code, _g(Q, 'prior_q'))],
                'next_q': [latest_est(code, _g(Q, 'next_q')), latest_est(code, _g(Q, 'target_q'))]
            }
        }
        out = {'yy': {}, 'qq': {}, 'yy_nm': {}, 'qq_nm': {}, 'yy_abs': {}, 'qq_abs': {}}
        for g in ['yy', 'qq']:
            for k in js_keys(pairs[g]):
                cur, base = pairs[g][k]
                out[g][k] = growth_pct(cur, base)
                if (out[g][k] is None and cur is not None and cur is not U and base is not None and
                        base is not U and _le(base, 0)):
                    out[g + '_nm'][k] = True
                    out[g + '_abs'][k] = abs_change(cur, base)
        return out
    gb0 = _or(_g(meta, 'growth_bases'), {})
    sf['growth'] = {'revenue': growth_for(REV, gb0), 'eps': growth_for(EPS, gb0) if _t(EPS) else None}

    d_rows = extract_rows(_or(_g(mcp, 'derived_subrows'), []))
    d_val, d_name = {}, {}
    for r in d_rows:
        if not _t(_g(r, 'LTCODE')) or not _t(_g(r, 'FISCALPERIOD')) or _g(r, 'VALUE') is U:
            continue
        if _test('-(IMY|IMQ|BPS)$', _S(r['LTCODE']), 'i'):
            continue
        fp = _replace('^R-', _S(r['FISCALPERIOD']), '')
        k = _S(r['LTCODE']) + '|' + fp
        is_act = _g(r, 'UPDATEDATE') is U
        if k not in d_val or is_act:
            d_val[k] = parse_value(r['VALUE'])
        li = _g(r, 'LINEITEM')
        if _t(li) and not _t(d_name.get(_S(r['LTCODE']), U)):
            d_name[_S(r['LTCODE'])] = li

    def derived(code, fp):
        v = d_val.get(_S(code) + '|' + _S(fp), U)
        return None if v is U else v

    def has_derived(code):
        c = _S(code)
        return c in d_name or any(k.startswith(c + '|') for k in js_keys(d_val))
    period_of = {'py_q': _g(Q, 'py_q'), 'prior_q': _g(Q, 'prior_q'), 'target_q': _g(Q, 'target_q'), 'next_q': _g(Q, 'next_q'),
                 'fy_prev': _g(FY, 'fy_prev'), 'fy0': _g(FY, 'fy0'), 'fy1': _g(FY, 'fy1')}
    ALLKEYS = ['py_q', 'prior_q', 'target_q', 'next_q', 'fy_prev', 'fy0', 'fy1']
    QKEYS = ['py_q', 'prior_q', 'target_q', 'next_q']

    def db_cells(dcode, fallback, keys=None):
        o, any_ = {}, False
        for k in (keys or ALLKEYS):
            dv = derived(dcode, period_of[k])
            if dv is not None:
                o[k] = dv
            else:
                fb = fallback.get(k, U) if (_t(fallback) and isinstance(fallback, dict)) else U
                o[k] = fb if (_t(fallback) and fb is not U) else None
            if o[k] is not None and o[k] is not U:
                any_ = True
        return o if any_ else None

    def gate_derived(dcode, cells, keys=None):
        if not _t(cells) or cells.get('target_q', U) is None or cells.get('target_q', U) is U:
            return
        miss = [k for k in (keys or ['py_q', 'prior_q', 'fy_prev']) if cells.get(k, U) is None or cells.get(k, U) is U]
        if miss:
            cgap([dcode], 'derived "' + _S(_or(d_name.get(_S(dcode), U), dcode)) + '": cells missing for ' +
                 _join([period_of[k] for k in miss], ', ') + ' -> fetch ' + _S(dcode) + ' actuals/estimates for those periods')
    for pair in [x for x in [[REV, sf['growth']['revenue']], [EPS, sf['growth']['eps']] if _t(EPS) else None] if _t(x)]:
        code, g = pair
        if not _t(g):
            continue
        if has_derived(_S(code) + '-Y'):
            g['yy'] = _or(db_cells(_S(code) + '-Y', g['yy']), g['yy'])
            gate_derived(_S(code) + '-Y', g['yy'])
        if has_derived(_S(code) + '-Q'):
            g['qq'] = _or(db_cells(_S(code) + '-Q', g['qq'], QKEYS), g['qq'])
            gate_derived(_S(code) + '-Q', g['qq'], ['py_q', 'prior_q'])

    COLKEYS = ['py_q', 'prior_q', 'target_q', 'next_q', 'fy_prev', 'fy0', 'fy1']
    rev_row_ref = next((r for r in sf['rows'] if _seq(r['ltcode'], REV)), U)

    def ratio_cells(num_row, den_row):
        o, any_ = {}, False
        for k in COLKEYS:
            n = num_row.get(k, U)
            d = den_row.get(k, U) if _t(den_row) else None
            o[k] = _round(_mul(_div(n, d), 100), 10) if (n is not None and n is not U and _t(d)) else None
            if o[k] is not None:
                any_ = True
        return o if any_ else None
    for r in sf['rows']:
        if latest_est(r['ltcode'], _g(Q, 'target_q')) is None:
            continue
        miss_cells = [p for v, p in [[r['py_q'], _g(Q, 'py_q')], [r['prior_q'], _g(Q, 'prior_q')],
                                     [r['fy_prev'], _g(FY, 'fy_prev')]] if v is None]
        no_actual_anywhere = r['py_q'] is None and r['prior_q'] is None and r['fy_prev'] is None
        if (no_actual_anywhere and actual(r['ltcode'], _g(Q, 'py_q')) is None and
                actual(r['ltcode'], _g(Q, 'prior_q')) is None and actual(r['ltcode'], _g(FY, 'fy_prev')) is None):
            r['street_only'] = True
            warn('[absent] Summary Financials "' + _S(r['name']) + '" (' + _S(r['ltcode']) + ') is STREET-ONLY: the ' +
                 'street models it and InSync carries no actual for it in any period, so its historical ' +
                 'cells render "n/a" rather than "—". Kept per Step 0.1 because an un-reported line the ' +
                 'street forecasts is still part of the setup; say so in the report.')
            continue
        if miss_cells:
            cgap([r['ltcode']] + [_S(r['ltcode']) + '|' + _S(q) for q in miss_cells],
                 'Summary Financials "' + _S(r['name']) + '": actuals missing for ' +
                 _join(miss_cells, ', ') + ' -> fetch actuals for ' + _S(r['ltcode']) + ' in those periods')
    gb_all = _or(_g(meta, 'growth_bases'), {})

    def _base_checks(code, gbx):
        return [
            [actual(code, _g(Q, 'py_q')), _g(gbx, 'py_q_prior_year')], [actual(code, _g(Q, 'prior_q')), _g(gbx, 'prior_q_prior_year')],
            [latest_est(code, _g(Q, 'next_q')), _g(gbx, 'next_q_prior_year')], [actual(code, _g(FY, 'fy_prev')), _g(gbx, 'fy_prev_prior')],
            [actual(code, _g(Q, 'py_q')), _g(gbx, 'py_q_prev_q')], [actual(code, _g(Q, 'prior_q')), _g(gbx, 'prior_q_prev_q')]
        ]
    for spec in [x for x in [{'code': REV, 'label': 'Revenue'}, {'code': EPS, 'label': 'EPS'} if _t(EPS) else None] if _t(x)]:
        code, label = spec['code'], spec['label']
        miss = _uniq([bq for cur, bq in _base_checks(code, gb_all) if cur is not None and _t(bq) and actual(code, bq) is None])
        if miss:
            cgap([code] + [_S(code) + '|' + _S(q) for q in miss],
                 label + ' growth rows: growth-base actuals missing for ' + _join(miss, ', ') + ' -> fetch actuals for ' + _S(code))
    kpi_rows = [r for r in sf['rows'] if r['group'] == 'kpi']
    for r in kpi_rows:
        if _seq(r['fmt'], '%'):
            continue
        subs = []
        gb = _or(_g(meta, 'growth_bases'), {})
        gr = growth_for(r['ltcode'], gb)
        missing_bases = _uniq([bq for cur, bq in _base_checks(r['ltcode'], gb)
                               if cur is not None and _t(bq) and actual(r['ltcode'], bq) is None])
        if missing_bases and not _t(r.get('street_only', U)):
            cgap([r['ltcode']] + [_S(r['ltcode']) + '|' + _S(q) for q in missing_bases],
                 'KPI sub-rows "' + _S(r['name']) + '": growth-base actuals missing for ' +
                 _join(missing_bases, ', ') + ' -> fetch actuals for ' + _S(r['ltcode']) + ' in those periods')
        lt = _S(r['ltcode'])
        if has_derived(lt + '-Y'):
            gr['yy'] = _or(db_cells(lt + '-Y', gr['yy']), gr['yy'])
            gate_derived(lt + '-Y', gr['yy'])
        if has_derived(lt + '-Q'):
            gr['qq'] = _or(db_cells(lt + '-Q', gr['qq'], QKEYS), gr['qq'])
            gate_derived(lt + '-Q', gr['qq'], ['py_q', 'prior_q'])

        def nm_flags(cells, keys):
            out = {}
            for k in keys:
                if cells.get(k, U) is None or cells.get(k, U) is U:
                    out[k] = True
            return out
        neg_base = ((r['py_q'] is not None and _le(r['py_q'], 0)) or (r['prior_q'] is not None and _le(r['prior_q'], 0)) or
                    (r['fy_prev'] is not None and _le(r['fy_prev'], 0)))
        if any(gr['yy'].get(k, U) is not None and gr['yy'].get(k, U) is not U for k in COLKEYS):
            subs.append({'label': 'Y/Y Growth', 'kind': 'growth', 'cells': gr['yy'], 'cells_nm': _or(gr['yy_nm'], {})})
        elif neg_base:
            subs.append({'label': 'Y/Y Growth', 'kind': 'growth', 'cells': gr['yy'], 'cells_nm': nm_flags(gr['yy'], COLKEYS)})
        if any(gr['qq'].get(k, U) is not None and gr['qq'].get(k, U) is not U for k in ['py_q', 'prior_q', 'target_q', 'next_q']):
            subs.append({'label': SEQ_LABEL, 'kind': 'growth', 'cells': gr['qq'], 'cells_nm': _or(gr['qq_nm'], {})})
        elif neg_base:
            subs.append({'label': SEQ_LABEL, 'kind': 'growth', 'cells': gr['qq'], 'cells_nm': nm_flags(gr['qq'], ['py_q', 'prior_q', 'target_q', 'next_q'])})
        if neg_base:
            warn('[growth] "' + _S(r['name']) + '" (' + _S(r['ltcode']) + ') has a NON-POSITIVE growth base in ' +
                 'at least one period, so those Y/Y and ' + SEQ_LABEL + ' cells are NOT MEANINGFUL and render NM. ' +
                 'The rows are kept: a percentage change off a negative base is undefined, which is a fact about ' +
                 'the line item, not a missing number. Do not describe those cells as growth.')
        m_name = _or(d_name.get(lt + '-M', U), '')
        m_is_margin = has_derived(lt + '-M') and _test('^(Margin|GM)\\b', m_name, 'i')
        m_is_pct_rev = has_derived(lt + '-M') and _test('^%\\s*Rev', m_name, 'i')
        mg_computed = None
        if _test('EBIT|profit|income', r['name'], 'i') and not _seq(r['fmt'], '#'):
            prefix = _trim(_replace('\\s*[-–]?\\s*(EBIT|Operating Income|Gross Profit|Profit|Income).*$', r['name'], '', 'i')).lower()
            pair_row = next((x for x in kpi_rows if x is not r and _test('revenue|sales', x['name'], 'i') and
                             _trim(x['name']).lower().startswith(prefix)), U) if prefix else None
            if _t(pair_row):
                mg_computed = ratio_cells(r, pair_row)
        mg = _or(db_cells(lt + '-M', mg_computed), mg_computed) if m_is_margin else mg_computed
        if _t(mg):
            subs.append({'label': 'Margin', 'kind': 'pct', 'cells': mg})
            if m_is_margin:
                gate_derived(lt + '-M', mg)

        def row_is_flow(row):
            fl = row.get('flow', U)
            return False if fl is False else (True if fl is True else not _test('^BS_', _S(row['ltcode'])))

        def row_pct_rev(row):
            return row.get('pctrev', U) is not False
        if (_t(rev_row_ref) and not _seq(r['ltcode'], REV) and row_pct_rev(r) and
                ((_seq(r['fmt'], '$M') and row_is_flow(r)) or m_is_pct_rev)):
            pc_computed = ratio_cells(r, rev_row_ref)
            pc = _or(db_cells(lt + '-M', pc_computed), pc_computed) if m_is_pct_rev else (pc_computed if _seq(r['fmt'], '$M') else None)
            has_margin = any(_test('^Margin', _S(_or(s.get('label', U), '')), 'i') for s in subs) or _t(mg)
            RATIO_CAP, QCOLS = 250, ['py_q', 'prior_q', 'target_q', 'next_q']
            pc_vals = [v for v in (pc.get(k, U) for k in COLKEYS) if _num_is_finite(v)] if _t(pc) else []
            q_vals = [v for v in (pc.get(k, U) for k in QCOLS) if _num_is_finite(v)] if _t(pc) else []
            fy_vals = [v for v in (pc.get(k, U) for k in ['fy_prev', 'fy0', 'fy1']) if _num_is_finite(v)] if _t(pc) else []

            def avg(a):
                s = 0
                for y in a:
                    s = s + abs(y)
                return _div(s, len(a))
            over_cap = len(q_vals) >= 2 and all(abs(v) > RATIO_CAP for v in q_vals)
            SKEW_MULT = 1.4 if FREQ == 'H' else (INF if FREQ == 'A' else 2.5)
            period_skew = (len(q_vals) >= 2 and len(fy_vals) >= 2 and
                           avg(q_vals) > avg(fy_vals) * SKEW_MULT and avg(q_vals) > RATIO_CAP)
            implausible = over_cap or period_skew
            if implausible:
                warn('[subrow] "' + _S(r['name']) + '" (' + _S(r['ltcode']) + ') computes to ' +
                     pct1(avg(q_vals)) + '% of QUARTERLY revenue on average' +
                     ((' against ' + pct1(avg(fy_vals)) + '% of ANNUAL revenue') if fy_vals else '') +
                     ', so it is not a share of revenue and the sub-row is SUPPRESSED. Either the row ' +
                     'is not a $M currency magnitude (a volume or a count InSync returned with a currency ' +
                     'mark - pass an explicit `fmt` on the candidate row in Step 0 to fix it), or it is a ' +
                     'different PERIOD from quarterly revenue (an annualised run-rate such as ARR, which no ' +
                     'stock-vs-flow test can detect). Say which in the report; do not describe the row as a ' +
                     'share of revenue.')
            all_neg = len(pc_vals) >= 2 and all(v < 0 for v in pc_vals)
            if all_neg:
                warn('[subrow] "' + _S(r['name']) + '" (' + _S(r['ltcode']) + ') is NEGATIVE in every column, so ' +
                     '"% of Total Revenue" has no reading and is SUPPRESSED. An outflow or a loss as a share of ' +
                     'revenue is a ratio, not a share. Its Y/Y and ' + SEQ_LABEL + ' rows are kept (marked NM ' +
                     'where the base is non-positive).')
            if _t(pc) and not has_margin and not implausible and not all_neg:
                pc_nm = {}
                for k in COLKEYS:
                    v = pc.get(k, U)
                    if _num_is_finite(v) and v < 0:
                        pc_nm[k] = True
                        pc[k] = None
                subs.append({'label': '% of Total Revenue', 'kind': 'pct', 'cells': pc, 'cells_nm': pc_nm})
        if subs:
            for x in subs:
                x['polarity'] = 'higher' if x['label'] == 'Margin' else _or(r['polarity'], 'higher')
            r['subs'] = subs

    def bare_row_check():
        bare = [r for r in sf['rows'] if r['group'] == 'fin' and _seq(r['fmt'], '$M') and
                not _seq(r['ltcode'], REV) and not _seq(r['ltcode'], EPS) and not len(_or(r.get('subs', U), [])) and
                r['py_q'] is not None and r['prior_q'] is not None]
        if bare:
            warn('[subrow] ' + ', '.join('"' + _S(r['name']) + '" (' + _S(r['ltcode']) + ')' for r in bare) +
                 ' render with NO sub-rows because InSync carries no -Y/-Q/-M derived item for them, while ' +
                 'other rows in the same table carry Y/Y. The growth IS derivable from actuals already ' +
                 'fetched, so this is STRICT RULE 5 (database-first, keep the sheet lean) overriding ' +
                 'STRICT RULE 6 (no blank where data exists) by design - not a fetch gap. Say so if the ' +
                 'asymmetry is likely to be read as one.')

    parts = []
    for r in sf['rows']:
        if r['group'] != 'kpi':
            continue
        s = next((x for x in _or(r.get('subs', U), []) if _test('% of Total Revenue', _S(_or(x.get('label', U), '')), 'i')), U)
        v = js_number(_g(s['cells'], 'target_q')) if (_t(s) and _t(s.get('cells', U))) else None
        if v is None or not _fin(v):
            continue
        parts.append({'name': r['name'], 'pct': v})
    total = 0
    for c in parts:
        total = total + c['pct']
    sf['kpi_revenue_coverage_pct'] = _round(total, 1) if parts else None
    if len(parts) > 1 and total > 105:
        warn('[subrow] the revenue-like KPI rows claim ' + _S(_round(total, 1)) + '% of total revenue ' +
             'between them (' + ', '.join(_S(c['name']) + ' ' + _S(_round(c['pct'], 1)) + '%' for c in parts) + '), so ' +
             'at least one is NESTED inside another and the shares do not add up. The rows are correct ' +
             'individually; the block does not partition revenue. Say so wherever the KPI shares are ' +
             'read, or pick a disjoint level of the segment tree in Step 0.')
    for r in [x for x in sf['rows'] if x['group'] == 'fin' and not _seq(x['fmt'], '%') and
              not _seq(x['ltcode'], REV) and not _seq(x['ltcode'], EPS)]:
        subs = []
        grf = growth_for(r['ltcode'], _or(_g(meta, 'growth_bases'), {}))
        lt = _S(r['ltcode'])
        is_profit = _test('\\b(EBITDA|EBIT|Operating Income)\\b', r['name'], 'i')
        if has_derived(lt + '-Y'):
            c = db_cells(lt + '-Y', grf['yy'])
            if _t(c):
                subs.append({'label': 'Y/Y Growth', 'kind': 'growth', 'cells': c, 'cells_nm': grf['yy_nm']})
                gate_derived(lt + '-Y', c)
        elif is_profit and (any(grf['yy'].get(k, U) is not None and grf['yy'].get(k, U) is not U for k in COLKEYS) or
                            len(js_keys(_or(grf['yy_nm'], {})))):
            subs.append({'label': 'Y/Y Growth', 'kind': 'growth', 'cells': grf['yy'], 'cells_nm': grf['yy_nm']})
        if has_derived(lt + '-Q'):
            c = db_cells(lt + '-Q', grf['qq'], QKEYS)
            if _t(c):
                subs.append({'label': SEQ_LABEL, 'kind': 'growth', 'cells': c})
                gate_derived(lt + '-Q', c, ['py_q', 'prior_q'])
        if has_derived(lt + '-M'):
            mn = _or(d_name.get(lt + '-M', U), '')
            m_label = '% of Total Revenue' if _test('^%\\s*Rev', mn, 'i') else 'Margin'
            dup_of_margin_row = m_label == 'Margin' and margin_row_exists_for(r['ltcode'])
            c = None if dup_of_margin_row else db_cells(lt + '-M', ratio_cells(r, rev_row_ref))
            if _t(c):
                subs.append({'label': m_label, 'kind': 'pct', 'cells': c})
                gate_derived(lt + '-M', c)
        elif is_profit and _t(rev_row_ref) and _seq(r['fmt'], '$M') and not margin_row_exists_for(r['ltcode']):
            c = ratio_cells(r, rev_row_ref)
            if _t(c):
                subs.append({'label': 'Margin', 'kind': 'pct', 'cells': c})
        if subs:
            for x in subs:
                x['polarity'] = 'higher' if x['label'] == 'Margin' else _or(r['polarity'], 'higher')
            r['subs'] = subs

    nm_notes = []

    def collect(label, gr):
        if not _t(gr):
            return
        for g in ['yy', 'qq']:
            for k in js_keys(_or(gr.get(g + '_nm', U), {})):
                d = gr[g + '_abs'].get(k, U)
                nm_notes.append(label + ' ' + ('Y/Y' if g == 'yy' else SEQ_LABEL) + ' @' + k + ': base <= 0, swing ' +
                                ('+' if _gt(d, 0) else '') + _S(d))
    collect('Revenue', sf['growth']['revenue'])
    collect('EPS', sf['growth']['eps'])
    if nm_notes:
        warn('[growth] percentage growth is NOT MEANINGFUL off a non-positive base and renders NM: ' +
             '; '.join(nm_notes) + '. State the ABSOLUTE swing in the read-out - a loss that narrows is ' +
             'an improvement even though the percentage cannot say so.')
    bare_row_check()

    ECOLS = [['target_q', _g(Q, 'target_q')], ['next_q', _g(Q, 'next_q')], ['fy0', _g(FY, 'fy0')], ['fy1', _g(FY, 'fy1')]]
    thin_rows = []
    for r in sf['rows']:
        thin = []
        for key, fp in ECOLS:
            if r.get(key, U) is None or r.get(key, U) is U:
                continue
            row = latest_est_row(r['ltcode'], fp)
            b = row['brokers'] if row else None
            if b is not None and b is not U and _le(b, 2):
                thin.append(_S(fp) + ' (' + _S(b) + ')')
        if thin:
            r['thin_cells'] = thin
            thin_rows.append(_S(r['name']) + ' [' + _S(r['ltcode']) + ']: ' + ', '.join(thin))

    V = valuation

    def row_at(pred, col):
        r = next((x for x in _or(sf['rows'], []) if pred(x)), U)
        v = r.get(col, U) if _t(r) else None
        return v if _num_is_finite(v) else None
    EBITDA_RE, REV_RE = '\\bEBITDA(re)?\\b', '^(total )?(revenue|net sales|sales)\\b'
    fy0 = 'fy0'
    ebitda = row_at(lambda r: r['group'] == 'fin' and _seq(r['fmt'], '$M') and _test(EBITDA_RE, _S(_or(r['name'], '')), 'i'), fy0)
    rev_fy0 = _or(row_at(lambda r: _seq(r['ltcode'], sf['rev_ltcode']), fy0),
                  row_at(lambda r: _test(REV_RE, _S(_or(r['name'], '')), 'i'), fy0))
    if _t(sf['eps_ltcode']):
        row_at(lambda r: _seq(r['ltcode'], sf['eps_ltcode']), fy0)
    fcf_fy0 = row_at(lambda r: _test('^(free cash flow|FCF)\\b', _S(_or(r['name'], '')), 'i'), fy0)
    mc, ev2 = V['market_cap'], V['ev']
    recompute = {
        'EV/EBITDA': lambda: _div(ev2, ebitda) if (_t(ev2) and _t(ebitda)) else None,
        'EV/EBITDA_Adj': lambda: _div(ev2, ebitda) if (_t(ev2) and _t(ebitda)) else None,
        'EV/Sales': lambda: _div(ev2, rev_fy0) if (_t(ev2) and _t(rev_fy0)) else None,
        'EV/FCF': lambda: _div(ev2, fcf_fy0) if (_t(ev2) and _t(fcf_fy0) and fcf_fy0 > 0) else None,
        'P/FCF_Ratio': lambda: _div(mc, fcf_fy0) if (_t(mc) and _t(fcf_fy0) and fcf_fy0 > 0) else None,
        'FCF_Yield': lambda: _div(100 * fcf_fy0, mc) if (_t(mc) and _t(fcf_fy0) and fcf_fy0 > 0) else None
    }
    minor_present = len(_or(CUR['minor_codes'], [])) > 0

    def credible(v, kind):
        return v is not None and _fin(v) and ((abs(v) < 100) if kind == 'pct' else (v > 0 and v < 500))

    def _lev_den():
        def pick(rx_src):
            r = next((x for x in _or(sf['rows'], []) if x['group'] == 'fin' and _seq(x['fmt'], '$M') and
                      _test(rx_src, _S(_or(x['name'], '')), 'i') and _isnum(x.get(fy0, U)) and x[fy0] > 0), U)
            return {'v': r[fy0], 'label': _trim(_replace('\\s*\\(.*$', _S(r['name']), ''))} if _t(r) else None
        return _or(_or(pick('\\bEBITDA(re)?\\b'), pick('\\b(AFFO|FFO)\\b')), None)
    lev_den = _lev_den()
    valuation['net_debt_ebitda'] = _round(_div(V['net_debt'], lev_den['v']), 1) \
        if (V['net_debt'] is not None and V['net_debt'] is not U and V['net_debt'] > 0 and _t(lev_den)) else None
    incl_lease = _test('incl', _S(_or(valuation['debt_label'], '')), 'i')
    valuation['net_debt_ebitda_label'] = None if valuation['net_debt_ebitda'] is None \
        else 'Net Debt / ' + _S(lev_den['label']) + (' (incl-lease debt)' if incl_lease else '')
    if valuation['net_debt_ebitda'] is not None and incl_lease:
        warn('[valuation] Net Debt / ' + _S(lev_den['label']) + ' is ' + _S(valuation['net_debt_ebitda']) +
             'x on the INCL-OPERATING-LEASE debt basis, because §3 requires that basis for the EV ' +
             'bridge and this row divides the same net debt. The row is labelled with the basis, but ' +
             'an EX-LEASE reading is materially lower and is what most issuers and the Street quote - ' +
             'on a telecom this row read well above both the issuer-native RT_P_406 consensus and the ' +
             'management target. If you quote leverage in prose, name the basis; InSync carries RT_P_406 ' +
             'as a consensus line if you want the issuer-native figure instead.')
    for L in _or(V.get('lenses', U), []):
        f = None if minor_present else recompute.get(_S(_or(L['resolved_metric'], L['metric'])))
        mine = f() if f else None
        L['crosscheck'] = None if mine is None else _round(mine, 1 if L['kind'] == 'pct' else 4)
        if mine is None or _is_zero(mine):
            continue
        got = L['value']
        if got is None or got is U:
            continue
        if not credible(mine, L['kind']):
            continue
        ratio = 0 if _is_zero(got) else abs(_div(got, mine))
        vendor_ok = credible(got, L['kind'])
        if vendor_ok and ratio >= 0.1 and ratio <= 10:
            if ratio < 0.55 or ratio > 1.30:
                warn('[valuation] ' + _S(L['label']) + ': InSync returns ' + _S(got) + ' but the same ratio computed ' +
                     'from figures THIS document prints is ' + _S(_round(mine, 2)) + ' - ' +
                     _S(_round(abs(ratio - 1) * 100, 0)) + '% apart, inside the order-of-magnitude NM test but ' +
                     'outside every basis difference this corpus has measured (0.60x-1.14x). The vendor ' +
                     'numerator or denominator is probably a DIFFERENT definition from the row the document ' +
                     'prints (a consolidated FCF against a company-defined one, an NTM against an FY0 base). ' +
                     'The card keeps the vendor figure; say in the read-out and the footer which definition ' +
                     'each is on, or put the matching row on the page.')
            continue
        if _is_zero(got) and not credible(mine, L['kind']):
            continue
        L['nm'] = True
        L['nm_reason'] = 'vendor ' + _S(_or(L['resolved_metric'], L['metric'])) + ' = ' + _S(got) + \
            ' against ' + _S(_round(mine, 1)) + ' recomputed from this document\'s own cells'
        warn('[valuation] ' + _S(L['label']) + ': InSync returns ' + _S(got) + ' for ' +
             _S(_or(L['resolved_metric'], L['metric'])) + ', but the same ratio computed from figures THIS ' +
             'document prints is ' + _S(_round(mine, 1)) + ' - a factor of ' +
             _S(_round(ratio if ratio >= 1 else _div(1, ratio), 0)) + '. The vendor denominator is on a different ' +
             'scale from its numerator for this issuer (verified on a KRW reporter, where EV/EBITDA came ' +
             'back 2950.0x against 4.0x from the card\'s own EV and EBITDA). The slot renders NM ' +
             'rather than printing either number as fact; state the recomputed figure in the ' +
             'read-out and say it is computed, not vendor-supplied.')
    if _is_zero(V['dividend_yield']):
        L = next((x for x in _or(V.get('lenses', U), []) if _seq(x['metric'], 'Dividend_Yield')), U)
        if _t(L):
            L['nm'] = True
            L['nm_reason'] = 'vendor Dividend_Yield returned exactly 0'
        warn('[valuation] Dividend Yield came back EXACTLY 0 from get_valuation. Zero and ' +
             'not-fetched are indistinguishable here, so the slot renders n/a rather than ' +
             'asserting a 0.00% yield. If the issuer genuinely pays no dividend, say so in the ' +
             'read-out; if it does pay one, the series is missing for this ticker.')

    COLS = ['py_q', 'prior_q', 'target_q', 'next_q', 'fy_prev', 'fy0', 'fy1']
    empty = [r for r in sf['rows'] if all(r.get(k, U) is None or r.get(k, U) is U for k in COLS)]
    if empty:
        warn('[completeness] EMPTY ROW - no value in ANY column for: ' +
             ', '.join('"' + _S(r['name']) + '" (' + _S(r['ltcode']) + ')' for r in empty) +
             '. Either the code was never fetched, or InSync carries no estimates AND no actuals ' +
             'for it, in which case it should have been excluded by the Step 0 estimate probe and ' +
             'must not occupy a Summary Financials slot. Do NOT ship an all-dash row: re-run Step 0 ' +
             'so the resolver promotes the next estimate-backed line item.')

    lower = [r['name'] for r in sf['rows'] if _seq(r['polarity'], 'lower')]
    neutral = [r['name'] for r in sf['rows'] if _seq(r['polarity'], 'neutral')]
    if lower:
        warn('[polarity] LOWER IS BETTER on: ' + _join(lower, ', ') + '. Growth and revision cells ' +
             'for these rows are coloured by improvement, not by arithmetic sign, so a falling ' +
             'combined ratio or cost line is GREEN. Verify the classification is right for this issuer.')
    if neutral:
        warn('[polarity] DIRECTIONLESS (rendered without sign colour): ' + _join(neutral, ', ') +
             '. A balance-sheet stock, a capex decision or a capital-structure level has no better ' +
             'direction in a preview; colouring one would assert a view the data does not carry.')
    rs = POLARITY.registry_status()
    if not rs or not rs.get('ok'):
        warn('[polarity] THE POLARITY REGISTRY DID NOT LOAD (' +
             _S((rs and rs.get('error')) or 'unknown error') + ') from ' + _S((rs and rs.get('path')) or 'assets/') +
             '. Every code it declares has silently reverted to the NAME LEXICON, so a cost ' +
             'line the lexicon does not recognise is graded higher-is-better again and a rising ' +
             'cost prints green - the defect the registry exists to prevent. Any ' +
             '"DIRECTION NOT DECLARED" warning below is a consequence of this, not a missing ' +
             'entry: restore assets/polarity_registry.json before reading them.')
    elif rs['count'] < 10:
        warn('[polarity] the polarity registry loaded but declares only ' + _S(rs['count']) +
             ' code(s), far fewer than the shipped set. It may have been truncated or partially ' +
             'written; confirm before trusting the direction on any graded row.')
    undeclared = [r for r in sf['rows'] if not POLARITY.polarity_declared(r['ltcode'], r['name']) and
                  _test('^(\\$M|\\$|#)$', _S(_or(r['fmt'], '')))]
    sf['undeclared_direction'] = [{'name': r['name'], 'ltcode': r['ltcode']} for r in undeclared]
    fm_all = _as_list(_or(_g(MS, 'fin_metrics'), []))
    kp_all = _as_list(_or(_g(MS, 'top_kpis'), []))

    def is_fte_row(r):
        return _test('\\bFTE\\b', _S(_or(_g(_or(r, {}), 'name'), '')), 'i')
    rev_row = _or(fm_all[0] if fm_all else U, None)
    rev_fte = is_fte_row(rev_row) if _t(rev_row) else None
    body = [x for x in fm_all[1:] + kp_all if _t(x)]
    fte_rows = [r for r in body if is_fte_row(r)]
    sf['basis'] = {
        'revenue': ('FTE' if rev_fte else 'reported') if _t(rev_row) else None,
        'fte_rows': [{'name': _g(r, 'name'), 'ltcode': _g(r, 'ltcode')} for r in fte_rows],
        'mixed': bool(_t(rev_row) and not rev_fte and len(fte_rows))
    }
    if sf['basis']['mixed']:
        warn('[metricset] TOP-LINE BASIS: the top line "' + _S(_g(rev_row, 'name')) + '" (' + _S(_g(rev_row, 'ltcode')) +
             ') is NOT on an FTE basis, while ' + _S(len(fte_rows)) + ' row(s) beneath it are: ' +
             ', '.join(_S(_g(r, 'name')) + ' (' + _S(_g(r, 'ltcode')) + ')' for r in fte_rows[0:4]) +
             ((' and ' + _S(len(fte_rows) - 4) + ' more') if len(fte_rows) > 4 else '') + '. Every ' +
             '"% of Total Revenue" sub-row on those rows divides an FTE numerator by a non-FTE ' +
             'denominator, so it cannot be reproduced from the two figures printed above it. ' +
             'This is not necessarily wrong: the resolver reached each row by its own correct rule, ' +
             'and a bank reports both bases. But the page must SAY so, and the footer now carries ' +
             'the basis line. Keep that sentence, or re-resolve onto one basis throughout.')
    if undeclared:
        warn('[polarity] DIRECTION NOT DECLARED for ' + _S(len(undeclared)) + ' graded row(s): ' +
             ', '.join(_S(r['name']) + ' (' + _S(r['ltcode']) + ')' for r in undeclared) +
             '. These resolved to higher-is-better by DEFAULT, not by decision: no entry in ' +
             'assets/polarity_registry.json and no match in the name lexicon. Their growth and ' +
             'revision cells are coloured green when the number RISES. That is right for a revenue ' +
             'or a profit line and inverted for a cost line, and nothing downstream can tell the ' +
             'difference - both gates re-derive the verdict from this same resolver, so neither can ' +
             'disagree with it. Check each row, and add the code to the registry with its label so ' +
             'the answer is recorded rather than re-guessed next quarter.')

    B = meta['buckets']
    b_pre, b_post, b_inter = _g(B, 'pre'), _g(B, 'post'), _g(B, 'interq')
    seq = [x for x in [['pre', b_pre], ['post', b_post], ['interq', b_inter]] if _t(x[1])]
    for i in range(1, len(seq)):
        if _lt(_S(seq[i][1]), _S(seq[i - 1][1])):
            warn('[revisions] BUCKET ORDER: meta.buckets.' + seq[i][0] + ' (' + _S(seq[i][1]) + ') is ' +
                 'EARLIER than meta.buckets.' + seq[i - 1][0] + ' (' + _S(seq[i - 1][1]) + '). The columns ' +
                 'must run pre-print -> post-print -> mid-quarter -> latest, so as written the grid ' +
                 'reads backwards and the earlier columns resolve empty. Any "bucket(s) empty" ' +
                 'warning below is caused by this, not by a fetch gap: fix the dates first.')
    revisions = {'buckets': B, 'rows': []}
    rev_dropped = []
    bucket_collapse = []
    for m in sf['rows']:
        s = est.get(_S(m['ltcode']) + '|' + _S(_g(Q, 'target_q')))
        pre, post, inter, lat = (last_on_or_before(s, b_pre), last_on_or_before(s, b_post),
                                 last_on_or_before(s, b_inter), latest_of(s))
        is_pct = _test('%|Margin', m['name'], 'i') or _seq(m['fmt'], '%')
        delta, delta_unit, raw_delta = None, None, None
        if post and lat:
            if is_pct:
                raw_delta = _mul(_sub(lat['value'], post['value']), 100)
                delta = _round(raw_delta, 0)
                delta_unit = 'bps'
            elif _gt(post['value'], 0):
                raw_delta = growth_pct(lat['value'], post['value'])
                delta = _round(raw_delta, 2)
                delta_unit = '%'
            else:
                raw_delta = abs_change(lat['value'], post['value'])
                delta = _round(raw_delta, 2)
                delta_unit = 'abs'
        for an, a, bn, b in [['Pre', pre, 'Post', post], ['Post', post, 'Inter-Q', inter],
                             ['Inter-Q', inter, 'latest', lat]]:
            if a and b and _seq(a['date'], b['date']):
                key = an + '|' + bn + '|' + _S(m['ltcode'])
                if key not in bucket_collapse:
                    bucket_collapse.append(key)
        if lat and (not pre or not post or not inter):
            gaps = '/'.join(x for x in [(not pre) and 'pre', (not post) and 'post', (not inter) and 'inter-Q'] if x)
            cgap([m['ltcode'], _S(m['ltcode']) + '|revisions'], 'revisions "' + _S(m['name']) + '" (' + _S(m['ltcode']) + '): latest present but ' + gaps +
                 ' bucket(s) empty -> fetch revision_series for ' + _S(m['ltcode']) + ' | ' + _S(_g(Q, 'target_q')) +
                 ' across updatedate ' + '(prior print -10d .. today), paginate fully')
        cov = [x for x in [['Pre', pre], ['Post', post], ['Inter-Q', inter], ['latest', lat]]
               if x[1] and x[1]['brokers'] is not None and x[1]['brokers'] is not U]
        if len(cov) >= 2:
            thick = max(v['brokers'] for _nm, v in cov)
            thin = [x for x in cov if x[1]['brokers'] <= 2 and thick >= max(3, x[1]['brokers'] * 3)]
        rev_vals = [x for x in [pre, post, inter, lat] if x and x['value'] is not None and x['value'] is not U]
        if len(rev_vals) < 2:
            rev_dropped.append(_S(m['name']) + ' (' + _S(m['ltcode']) + ', ' + _S(len(rev_vals)) + ' observation' +
                               ('' if len(rev_vals) == 1 else 's') + ')')
        revisions['rows'].append({
            'name': m['name'], 'ltcode': m['ltcode'], 'group': m['group'], 'fmt': m['fmt'], 'polarity': _or(m['polarity'], 'higher'), 'is_pct': is_pct,
            'pre': pre['value'] if pre else None, 'post': post['value'] if post else None,
            'interq': inter['value'] if inter else None, 'latest': lat['value'] if lat else None,
            'latest_date': lat['date'] if lat else None, 'delta_vs_post': delta, 'delta_unit': delta_unit,
            'delta_raw': None if raw_delta is None else _round(raw_delta, 8),
            'dir': None if delta is None else ('up' if delta > 0 else ('down' if delta < 0 else 'flat'))
        })

    defs = [['pre', 'Pre-' + _S(_g(Q, 'prior_q')), b_pre], ['post', 'Post-' + _S(_g(Q, 'prior_q')), b_post],
            ['interq', 'Inter-Q', b_inter],
            ['latest', 'Pre-' + _S(_g(Q, 'target_q')), _or(_g(_or(revisions['rows'][0] if revisions['rows'] else U, {}), 'latest_date'), None)]]
    rcols = []
    for key, label, date in defs:
        twin = next((c for c in rcols if _t(c['date']) and _t(date) and _seq(c['date'], date)), U)
        if _t(twin):
            twin['labels'].append(label)
            twin['keys'].append(key)
        else:
            rcols.append({'keys': [key], 'labels': [label], 'date': date})

    def _col_label(c):
        if len(c['labels']) == 1:
            return c['labels'][0]

        def tail(l):
            i = l.find('-')
            return '' if i < 0 else l[i + 1:]
        t = tail(c['labels'][0])
        if t and all(tail(l) == t for l in c['labels']):
            n = len(c['labels'])
            return '/'.join(l if i == n - 1 else l[0:l.find('-') + 1] for i, l in enumerate(c['labels']))
        return '/'.join(c['labels'])
    revisions['columns'] = [{'keys': c['keys'], 'date': c['date'], 'label': _col_label(c)} for c in rcols]
    merged = [c for c in revisions['columns'] if len(c['keys']) > 1]
    if merged:
        warn('[revisions] ' + _S(len(merged)) + ' bucket column(s) resolve to ONE observation and are ' +
             'MERGED in the table rather than printed twice: ' +
             '; '.join(' + '.join(c['keys']) + ' -> "' + c['label'] + '" (' + _S(_or(c['date'], 'no date')) + ')' for c in merged) +
             '. The quarter is too young - or the vintage too sparse - for that many distinct marks. ' +
             'The table now shows ' + _S(len(revisions['columns'])) + ' column(s) instead of 4, so the reader ' +
             'cannot mistake a duplicated pair for two independent revisions; say in the read-out how ' +
             'many vintages the row actually rests on.')
    if rev_dropped:
        warn('[coverage] ' + _S(len(rev_dropped)) + ' Earnings Revisions row(s) rest on FEWER THAN ' +
             'TWO bucket observations, so the row is mostly dashes and its delta cannot be computed: ' +
             '; '.join(rev_dropped) + '. The row is KEPT because STRICT CONTENT RULE 2 requires the ' +
             'Revisions table to carry exactly the Summary Financials rows, so say in the read-out ' +
             'that these lines have no revision history rather than letting the dashes imply a ' +
             'fetch gap. Two real causes to check: (1) the §5 revision_series fetch did not cover ' +
             'this code - widen updatedate_start and re-probe; (2) InSync genuinely carries only the ' +
             'latest vintage for it, which is normal for a line only one or two brokers model.')
    if bucket_collapse:
        by_pair = {}
        for k in bucket_collapse:
            parts3 = k.split('|')
            a, b, lt = parts3[0], parts3[1], parts3[2] if len(parts3) > 2 else U
            by_pair.setdefault(a + ' / ' + b, []).append(lt)
        for pair in js_keys(by_pair):
            codes = by_pair[pair]

            def span_of(pr):
                return [b_pre, b_post] if pr == 'Pre / Post' else \
                    ([b_post, b_inter] if pr == 'Post / Inter-Q' else [b_inter, None])
            lo_, hi_ = span_of(pair)
            single = [lt for lt in codes if not any(
                _gt(x['date'], lo_) and (hi_ is None or _le(x['date'], hi_))
                for x in _or(est.get(_S(lt) + '|' + _S(_g(Q, 'target_q')), U), []))]
            movable = [lt for lt in codes if _index_of(single, lt) == -1]
            if single:
                degenerate = hi_ is not None and _ge(lo_, hi_)
                warn('[revisions] BUCKET COLLAPSE (' + ('DEGENERATE SPAN' if degenerate else 'NO OBSERVATION IN SPAN') +
                     '): the ' + pair + ' columns resolve to the same observation for ' + _S(len(single)) +
                     ' row(s) (' + _join(single, ', ') + '). ' + (
                         ('The two bucket dates are the same or inverted (' + _S(lo_) + ' vs ' + _S(hi_) + '), so NO ' +
                          'observation can fall between them whatever the data holds - this happens by ' +
                          'construction whenever the prior print is less than ~2 weeks back, because post ' +
                          '(prior-print+7d) and inter-Q (midpoint of prior print and today) coincide. Move the ' +
                          'inter-Q bucket to the midpoint of the two observation dates that bracket it, or ' +
                          'state in the report that the quarter is too young for a mid-quarter column.')
                         if degenerate else
                         ('The dates are distinct but InSync holds no estimate observation between them, so no ' +
                          'bucket date can separate the columns: do NOT move a date or re-fetch. State that ' +
                          'these rows have one observation and that the delta across them spans zero time.')))
            if movable:
                warn('[revisions] BUCKET COLLAPSE: the ' + pair + ' columns resolve to the SAME estimate ' +
                     'observation for ' + _S(len(movable)) + ' row(s) (' + _join(movable, ', ') + '), so the ' +
                     'table prints one number twice under two headings and any delta measured across them ' +
                     'spans zero time. Bucket dates in use: pre=' + _S(b_pre) + ', post=' + _S(b_post) + ', interq=' +
                     _S(b_inter) + '. These codes DO have more than one vintage, so move the offending bucket ' +
                     'to the MIDPOINT of the two observation dates that bracket it - a deterministic rule, ' +
                     'so two operators pick the same date - and re-fetch.')

    g_rows = [r for r in extract_rows(_g(mcp, 'guidance')) if _g(r, 'GUIDE_TEXT') is not U and _t(_g(r, 'LTCODE'))]
    CENTS_RE = '(?:^|\\d\\s*)(?:c\\b|¢|cents?\\b)'
    BOUND_MIN = '\\s*(?:or\\s+(?:great|more|high|above|better|bett)\\w*|and\\s+(?:above|higher)|at\\s+least|no\\s+less\\s+than|north\\s+of|minimum(?:\\s+of)?)\\s*$'
    BOUND_MAX = '\\s*(?:or\\s+(?:less|lower|below|fewer)|and\\s+(?:below|lower)|at\\s+most|no\\s+more\\s+than|maximum(?:\\s+of)?)\\s*$'
    UP_TO = '^\\s*(?:up\\s+to|no\\s+higher\\s+than|under)\\s+'

    def parse_guide(txt, fmt):
        t = _trim(_S(txt))
        bare = {'lo': None, 'hi': None, 'point': None, 'text': t}
        if not t:
            return dict(bare, qualitative=True)
        tp = _replace('^(approx\\.?|approximately|about|around|~)\\s*', t, '', 'i')
        cents_row = _seq(fmt, '$') and _test(CENTS_RE, tp, 'i')

        def scale(v):
            return None if v is None else (_div(v, 100) if cents_row else v)
        tol = _exec('^(.*?)\\s*(?:\\+\\/-|\\+-|±)\\s*(.+)$', tp)
        if tol:
            base = parse_value(tol[1])
            span_txt = _trim(tol[2])
            if base is not None:
                pct = _exec('^([\\d.]+)\\s*%$', span_txt)
                span = _div(abs(base) * js_parse_float(pct[1]), 100) if pct else parse_value(span_txt)
                if span is not None:
                    return {'lo': scale(base - abs(span)), 'hi': scale(base + abs(span)),
                            'point': None, 'text': t, 'basis': 'cents' if cents_row else None}
                return dict(bare, point=scale(base), tolerance_unparsed=span_txt)
        two = None
        word = _split('\\s+(?:to|through|-)\\s+(?=[-−+$₩£€¥\\d(])', tp, 'i')
        if len(word) == 2:
            two = word
        if not two:
            dash = _split('\\s*[–—]\\s*', tp)
            if len(dash) == 2:
                two = dash
        if not two:
            mm = _exec('^\\s*([-−+]?[^-–—]+?)\\s*-\\s*([-−+]?.+)$', tp)
            if mm:
                two = [mm[1], mm[2]]
        if two:
            lo, hi = parse_value(_trim(two[0])), parse_value(_trim(two[1]))
            if lo is not None and hi is not None:
                a, b = scale(lo), scale(hi)
                return {'lo': _js_min(a, b), 'hi': _js_max(a, b), 'point': None, 'text': t,
                        'basis': 'cents' if cents_row else None}
        bound, tb = None, tp
        if _test(BOUND_MIN, tb, 'i'):
            bound = 'min'
            tb = _replace(BOUND_MIN, tb, '', 'i')
        elif _test(BOUND_MAX, tb, 'i'):
            bound = 'max'
            tb = _replace(BOUND_MAX, tb, '', 'i')
        elif _test(UP_TO, tb, 'i'):
            bound = 'max'
            tb = _replace(UP_TO, tb, '', 'i')
        if bound:
            bv = parse_value(_trim(tb))
            if bv is not None:
                return {'lo': scale(bv) if bound == 'min' else None,
                        'hi': scale(bv) if bound == 'max' else None,
                        'point': None, 'bound': bound, 'text': t,
                        'basis': 'cents' if cents_row else None}
        pv = parse_value(tp)
        if pv is not None:
            return {'lo': None, 'hi': None, 'point': scale(pv), 'text': t,
                    'basis': 'cents' if cents_row else None}
        return dict(bare, qualitative=True)
    fmt_by_code = {}
    for m in all_codes:
        fmt_by_code[_hk(_g(m, 'ltcode'))] = _or(_g(m, 'fmt'), '$M')
    guides = [{
        'ltcode': r['LTCODE'], 'lineitem': _g(r, 'LINEITEM'), 'source': _g(r, 'SOURCE_PERIOD'), 'target': _g(r, 'FISCALPERIOD'),
        'date': _g(r, 'GUIDEDATE'),
        'parsed': parse_guide(r['GUIDE_TEXT'], fmt_by_code.get(_hk(_replace('-Y$', _S(r['LTCODE']), '')), U))
    } for r in g_rows]
    for g in guides:
        P = g['parsed']
        has_digits = _test('\\d', _S(P['text']))
        if _t(g['date']) and _t(_g(meta, 'prepared')) and _gt(_S(g['date']), _S(meta['prepared'])):
            warn('[guidance] "' + _S(g['lineitem']) + '" (' + _S(g['target']) + ') carries a GUIDEDATE of ' + _S(g['date']) +
                 ', which is after the prepared date (' + _S(meta['prepared']) + '). A guide cannot be issued ' +
                 'on a call that has not happened, so this row is mislabelled and is competing for the ' +
                 'guidance cell against the real one. Re-fetch get_guidance and drop it.')
        if P.get('basis', U) == 'cents':
            warn('[guidance] "' + _S(g['lineitem']) + '" (' + _S(g['target']) + ') is guided in CENTS ("' + _S(P['text']) +
                 '") on a per-share row, so it was converted to the row basis (' +
                 (_S(_round(P['point'], 4)) if P['point'] is not None else _S(_round(P['lo'], 4)) + ' to ' + _S(_round(P['hi'], 4))) +
                 '). Confirm the issuer quotes cents; if InSync already stores major units this ' +
                 'guide is being divided by 100.')
        if _t(P.get('tolerance_unparsed', U)):
            warn('[guidance] "' + _S(g['lineitem']) + '" (' + _S(g['target']) + ') carries a tolerance this parser ' +
                 'could not read ("' + _S(P['tolerance_unparsed']) + '"). The base is shown as a POINT and the ' +
                 'range is lost. Restate the guide as an explicit low and high.')
        if has_digits and P['lo'] is None and P['hi'] is None and P['point'] is None:
            warn('[guidance] "' + _S(g['lineitem']) + '" (' + _S(g['target']) + ') contains a number the parser ' +
                 'could not resolve to a point or a range ("' + _S(P['text']) + '"), so the cell prints the ' +
                 'company wording verbatim and NO figure enters the grid. That is the safe outcome; if ' +
                 'this wording is common for the issuer, extend parseGuide rather than hand-editing.')

    def period_rank(p):
        s = _S(p)
        m = _exec('^(\\d)Q(\\d{2})$', s)
        if m:
            return int(m[2], 10) * 4 + int(m[1], 10)
        m = _exec('^(\\d)H(\\d{2})$', s)
        if m:
            return int(m[2], 10) * 4 + int(m[1], 10) * 2
        m = _exec('^\\d{4}$', s)
        if m:
            return int(s[2:], 10) * 4 + 4
        return -1
    latest_source = _g(Q, 'prior_q')
    live_guides = [g for g in guides if _seq(g['source'], latest_source)]
    stale_guides = [g for g in guides if not _seq(g['source'], latest_source) and period_rank(g['target']) >= period_rank(_g(Q, 'target_q'))]
    for g in stale_guides:
        warn('[guidance-freshness] ' + _S(g['lineitem']) + ' for ' + _S(g['target']) + ' guided at ' + _S(g['source']) + ' (not latest call) - commentary only, NOT a guidance cell')
    live_target_q = [g for g in live_guides if _seq(g['target'], _g(Q, 'target_q'))]
    live_fy = [g for g in live_guides if _test('^\\d{4}$', _S(g['target']))]
    live_other = [g for g in live_guides if not _seq(g['target'], _g(Q, 'target_q')) and not _test('^\\d{4}$', _S(g['target']))]
    if live_other:
        warn('[guidance] ' + _S(len(live_other)) + ' LIVE guide(s) target a period that is neither the ' +
             'target quarter nor a fiscal year, so they have no column in this table and appear in no ' +
             'card: ' + '; '.join(_S(g['ltcode']) + ' [' + _S(g['target']) + '] "' +
                                  _u16slice(_replace('\\s+', _S(_or(g['parsed']['text'], '')), ' ', glob=True), 0, 60) + '"' for g in live_other) +
             '. These are multi-year or open-ended targets (a long-run CAGR, a "by FY30" framework) and ' +
             'are often the most-quoted guide the company gives. HOW TO HANDLE THEM, and this is ' +
             'narrower than it was (v8.0.9): this used to say "cover them in Tone Shift or Call ' +
             'Commentary", which collides head-on with SKILL.md\'s INTERNAL CONSISTENCY rule - the ' +
             'narrative may only cite metrics that appear SOMEWHERE in the document, and a guide with ' +
             'no column and no card appears nowhere. Two instructions, opposite directions, and an ' +
             'operator obeying this one breaks the locked rule. So: (1) if the metric HAS a Summary ' +
             'Financials row, its multi-year guide may be quoted as management\'s own words in Call ' +
             'Commentary - the metric is in the document, only this horizon is not. (2) If the metric ' +
             'has NO row anywhere, do NOT cite the figure: name the THEME without the number ' +
             '("management reiterated a multi-year margin framework") and say the target sits outside ' +
             'the grid. Either way say it is not in the grid. The internal-consistency rule wins.')

    def map_g(g, with_target):
        d = {'ltcode': g['ltcode'], 'lineitem': g['lineitem'], 'lo': g['parsed']['lo'], 'hi': g['parsed']['hi'],
             'point': g['parsed']['point'], 'bound': _or(g['parsed'].get('bound', U), None), 'text': g['parsed']['text']}
        if with_target:
            d['target'] = g['target']
        return d
    sf['guidance_target_q'] = [map_g(g, False) for g in live_target_q]
    sf['guidance_fy'] = [map_g(g, True) for g in live_fy if _S(g['target']) == _S(_g(FY, 'fy0'))]
    sf['guidance_fy1'] = [map_g(g, True) for g in live_fy if _S(g['target']) == _S(_g(FY, 'fy1'))]
    if _S(_g(Q, 'target_q')) == _S(_g(FY, 'fy0')):
        dupes = [g for g in sf['guidance_fy'] if any(_seq(t['ltcode'], g['ltcode']) for t in sf['guidance_target_q'])]
        if dupes:
            warn('[guidance] annual reporter: target period ' + _S(_g(Q, 'target_q')) + ' IS fiscal year ' + _S(_g(FY, 'fy0')) +
                 ' - the FY guide column duplicated the target-period column and was suppressed (' +
                 _join([d['lineitem'] for d in dupes], ', ') + ')')
            sf['guidance_fy'] = [g for g in sf['guidance_fy'] if not any(_seq(t['ltcode'], g['ltcode']) for t in sf['guidance_target_q'])]
    all_base_codes = set(_replace('-Y$', _S(_g(m, 'ltcode')), '') for m in all_codes)

    def guide_opens_column(g):
        lt = _S(g['ltcode'])
        if lt in all_base_codes:
            return True
        if not _test('-Y$', lt):
            return False
        b = _replace('-Y$', lt, '')
        if _seq(b, REV):
            return _t(sf['growth']['revenue'])
        if _seq(b, EPS):
            return _t(sf['growth']['eps'])
        row = next((r for r in sf['rows'] if _seq(r['ltcode'], b)), U)
        return bool(_t(row) and any(_test('^Y\\/Y', _S(_or(s.get('label', U), ''))) for s in _or(row.get('subs', U), [])))
    sf['show_fy_guide_column'] = any(guide_opens_column(g) for g in sf['guidance_fy'])
    sf['show_fy1_guide_column'] = any(guide_opens_column(g) for g in sf['guidance_fy1'])
    sf['show_guide_column'] = any(guide_opens_column(g) for g in sf['guidance_target_q'])

    prints = meta['prints'][-5:]
    FAM = {'IS': 0, 'SD': 1, 'KM': 2, 'BS': 3, 'CF': 4}

    def order_rank(lt):
        fam = FAM[lt[0:2]] if lt[0:2] in FAM else 9
        prim = 0 if '_P_' in lt else 1
        mm = _exec('_(\\d+)', lt)
        num_ = int(mm[1], 10) if mm else 999
        suffix = 1 if _test('-[A-Z]+$', lt) else 0
        return prim * 1e8 + fam * 1e7 + num_ * 10 + suffix
    sf_codes = [r['ltcode'] for r in sf['rows']]

    def evo_core(lt):
        b = _replace('-Y$', _S(lt), '')
        return any(_seq(c, b) for c in sf_codes)
    evo_dropped = _uniq([g['ltcode'] for g in live_target_q + live_fy if not evo_core(g['ltcode'])])
    if evo_dropped:
        warn('[evolution] guided items without a Summary Financials row kept out of the grid (still in guidance_live): ' + _join(evo_dropped, ', '))
    STOP = {'the', 'of', 'and', 'a', 'an', 'total', 'per', 'by', 'for', 'in',
            'consolidated', 'net', 'adj', 'adjusted', 'rate', 'ratio', 'pct'}

    def toks(n):
        s = _replace('\\([^)]*\\)', _S(_or(n, '')).lower(), ' ', glob=True)
        s = _replace('[^a-z0-9 ]+', s, ' ', glob=True)
        return [t for t in _split('\\s+', s) if t and t not in STOP]

    def subset(a, b):
        return len(a) and all(t in b for t in a)
    hits2 = []

    def is_expr_name(n):
        return _test('[\\/+]|\\)\\s*\\/|\\bless\\b|\\bminus\\b', _S(_or(n, '')), 'i')
    BASIS_RE = '\\b(ex-?1x|excl|adj|adjusted|gaap|reported|non-?gaap|sbc|amorti[sz]ation|pro ?forma|fte|organic|constant currency|cc)\\b'

    def basis_tag(n):
        m = _exec(BASIS_RE, _S(_or(n, '')), 'i')
        return _join(m if m else [''], '').lower()
    for g in [x for x in live_fy + live_target_q if not evo_core(x['ltcode'])]:
        if is_expr_name(g['lineitem']):
            continue
        gt = toks(g['lineitem'])
        if not gt:
            continue

        def same(a, b):
            return len(a) == len(b) and _t(subset(a, b)) and _t(subset(b, a))
        g_fmt = '%' if (_t(_and(g['parsed'], lambda: g['parsed'].get('unit', U) == '%')) or _test('%', _S(g['lineitem']))) else None

        def _cand(r):
            rt = toks(r['name'])
            if not rt or not same(gt, rt):
                return False
            if _test(BASIS_RE, _S(g['lineitem']), 'i') != _test(BASIS_RE, _S(r['name']), 'i'):
                return False
            if basis_tag(g['lineitem']) != basis_tag(r['name']):
                return False
            return g_fmt is None or _seq(r['fmt'], g_fmt)
        cands = [r for r in sf['rows'] if _cand(r)]
        if len(cands) != 1:
            continue
        hits2.append({'code': g['ltcode'], 'item': g['lineitem'], 'row': cands[0]['name'], 'rowCode': cands[0]['ltcode'],
                      'text': _or(_or(_and(g['parsed'], lambda: g['parsed']['text']), g.get('text', U)), '')})
    if hits2:
        warn('[guidance] SAME CONCEPT, DIFFERENT LTCODE - ' + _S(len(hits2)) + ' live guide(s) name a ' +
             'concept a Summary Financials row already carries, under a different code, so the guide ' +
             'cell renders BLANK beside a row that has the value in hand: ' +
             '; '.join('"' + _S(h['item']) + '" (' + _S(h['code']) + ', guide ' + _S(h['text']) + ') looks like the row "' +
                       _S(h['row']) + '" (' + _S(h['rowCode']) + ')' for h in hits2) + '. Verified on O, where a live FY26 ' +
             'occupancy guide of 98.5% printed as an em-dash. Nothing is bound automatically - a ' +
             'wrong bind is worse than a blank, and only you can confirm the two codes measure the ' +
             'same thing. To bind it, put the GUIDE\'s ltcode on that candidate row in Step 0 (or ' +
             'add the guide\'s code to the metric set so it has a row of its own) and re-run. If they ' +
             'are NOT the same concept, say so in the read-out and treat the guide as commentary.')
    dropped_margins = [lt for lt in evo_dropped if _test('-M$', _S(lt))]
    if dropped_margins:
        held_m = [r['ltcode'] for r in sf['rows'] if _test('-M$', _S(r['ltcode']))]
        warn('[evolution] the company GUIDES a margin the document does not carry (' +
             _join(dropped_margins, ', ') + ') while the margin slot holds ' + _or(_join(held_m, ', '), 'nothing') +
             '. There is one margin slot, so that guide cannot reach Guidance Evolution and, under the ' +
             'internal-consistency rule, cannot be cited in prose either - the metric the print turns on ' +
             'exists nowhere in the deliverable. FIX, in this order (v8.0.3 - this used to prescribe ' +
             'only the second step, and on the O live run the operator HAD already done it and the ' +
             'slot did not move): (1) check the guided code is in the CANDIDATE POOL at all. ' +
             '`guided_ltcodes` is a TIEBREAK - it re-ranks rows the Step 0.2 search_lineitem batches ' +
             'already returned, and can do nothing for a code none of them found. If the code is ' +
             'absent, add a query that reaches it to the Step 0.2 batches and re-probe. (2) then pass ' +
             '`guided_ltcodes` to resolve_metricset.js and re-run, and the guided margin wins the slot. ' +
             '(3) if the guided margin is an EXPENSE RATIO or a narrow component (a "% Revenue - G&A", ' +
             'a "Margin - Property Expense") do NEITHER: promoting it would displace the headline ' +
             'margin with something less informative. Disclose the guide in prose as a guidance-only ' +
             'figure and say why it has no row.')
    evo_map = {}
    for g in live_target_q:
        if evo_core(g['ltcode']):
            evo_map[_hk(g['ltcode'])] = g
    evo_items = js_cmp_sort(list(evo_map.values()), lambda a, b: order_rank(a['ltcode']) - order_rank(b['ltcode']))
    prior_year_of = prior_year_guess
    evolution = {'columns': [_g(p, 'q') for p in prints] + [_S(_g(Q, 'target_q')) + 'E'],
                 'print_dates': [_g(p, 'call_date') for p in prints] + [_g(meta['upcoming'], 'call_date')], 'blocks': []}
    for item in evo_items:
        sf_row_for_item = next((r for r in sf['rows'] if _seq(r['ltcode'], item['ltcode'])), U)
        is_pct = bool((_t(sf_row_for_item) and _seq(sf_row_for_item['fmt'], '%'))
                      or _test('-(Y|Q|M|IMY|IMQ)$', item['ltcode'], 'i')
                      or _test('%|Y\\/Y|Margin|ETR|Rate', item['lineitem'], 'i'))
        y_base = _replace('-Y$', item['ltcode'], '') if _test('-Y$', item['ltcode']) else None
        block = {'lineitem': item['lineitem'], 'ltcode': item['ltcode'], 'is_pct': is_pct,
                 'fmt': '%' if is_pct else _or(_and(sf_row_for_item, lambda: sf_row_for_item['fmt']), '$M'), 'cols': []}
        py_gaps = []
        for p in prints:
            pq, pcd = _g(p, 'q'), _g(p, 'call_date')
            g = next((x for x in guides if _seq(x['ltcode'], item['ltcode']) and _seq(x['target'], pq)), U)
            if y_base:
                py_snap = snaps.get(y_base + '|' + _S(pq))
                base_cons = _or(last_on_or_before(py_snap, pcd), latest_of(py_snap)) if _t(py_snap) else None
                prev_yr = actual(y_base, prior_year_of(pq))
                cons = {'value': _round(pct_change(base_cons['value'], prev_yr), 1)} if (base_cons and prev_yr is not None) else None
                a = _round(pct_change(actual(y_base, pq), prev_yr), 1) if (actual(y_base, pq) is not None and prev_yr is not None) else None
                if prev_yr is None and (base_cons or actual(y_base, pq) is not None):
                    py_gaps.append(prior_year_of(pq))
            else:
                snap_s = snaps.get(_S(item['ltcode']) + '|' + _S(pq))
                cons = _or(last_on_or_before(snap_s, pcd), latest_of(snap_s)) if _t(snap_s) else None
                a = actual(item['ltcode'], pq)
            hi = (g['parsed']['hi'] if g['parsed']['hi'] is not None else g['parsed']['point']) if _t(g) else None
            lo = (g['parsed']['lo'] if g['parsed']['lo'] is not None else g['parsed']['point']) if _t(g) else None
            cv = cons['value'] if cons else U
            block['cols'].append({
                'q': pq, 'guide_hi': hi, 'guide_lo': lo,
                'guide_point': g['parsed']['point'] if (_t(g) and g['parsed']['point'] is not None) else None,
                'guide_text': g['parsed']['text'] if (_t(g) and _t(g['parsed'].get('qualitative', U))) else None,
                'consensus': cv if cons else None, 'actual': a,
                'vs_guide_hi': ((_round(_sub(a, hi), 1) if is_pct else (_round(pct_change(a, hi), 1) if (_gt(a, 0) and _gt(hi, 0)) else None))
                                if (a is not None and hi is not None) else None),
                'vs_cons': ((_round(_sub(a, cv), 1) if is_pct else (_round(pct_change(a, cv), 1) if (_gt(a, 0) and _gt(cv, 0)) else None))
                            if (a is not None and cons and cv is not None) else None)
            })
        lg = next((x for x in live_target_q if _seq(x['ltcode'], item['ltcode'])), U)
        setup_cons = _round(pct_change(latest_est(y_base, _g(Q, 'target_q')), actual(y_base, _g(Q, 'py_q'))), 1) \
            if y_base else latest_est(item['ltcode'], _g(Q, 'target_q'))
        block['cols'].append({
            'q': _S(_g(Q, 'target_q')) + 'E',
            'guide_hi': (lg['parsed']['hi'] if lg['parsed']['hi'] is not None else lg['parsed']['point']) if _t(lg) else None,
            'guide_lo': (lg['parsed']['lo'] if lg['parsed']['lo'] is not None else lg['parsed']['point']) if _t(lg) else None,
            'guide_point': lg['parsed']['point'] if (_t(lg) and lg['parsed']['point'] is not None) else None,
            'guide_text': lg['parsed']['text'] if (_t(lg) and _t(lg['parsed'].get('qualitative', U))) else None,
            'consensus': setup_cons, 'actual': None, 'vs_guide_hi': None, 'vs_cons': None
        })
        past_cols = block['cols'][:-1]
        cons_gaps = [c['q'] for c in past_cols if c['actual'] is not None and c['consensus'] is None]
        if cons_gaps:
            code = _or(y_base, item['ltcode'])
            cgap([code, item['ltcode']], 'evolution "' + _S(item['lineitem']) + '": actual present but consensus missing for ' +
                 _join(cons_gaps, ', ') + ' -> fetch preprint_snapshots for ' + _S(code) +
                 ' in each of those quarters (updatedate = print-21d .. print-1d)')
        if y_base and py_gaps:
            qs = _uniq(py_gaps)
            warn('[completeness] evolution "' + _S(item['lineitem']) + '": derived Y/Y cells blank because the prior-year actual for ' +
                 y_base + ' is missing for ' + _join(qs, ', ') + ' -> fetch get_actuals for ' + y_base +
                 ' in those quarters (fills both the Y/Y consensus and actual cells of this block)')
        evolution['blocks'].append(block)
    timing_raw = _S(_or(_g(meta, 'call_timing'), 'after_close')).lower()
    before_open = timing_raw == 'before_open'
    timing_unknown = timing_raw == 'unknown'

    def reaction_at(p, bo):
        series = _or(mkt.get(tid, U), [])
        pre_close, post_close = None, None
        pcd = _g(p, 'call_date')
        if bo:
            for pt in series:
                if _lt(pt['date'], pcd):
                    pre_close = pt
            post_close = next((pt for pt in series if _ge(pt['date'], pcd)), None)
        else:
            for pt in series:
                if _le(pt['date'], pcd):
                    pre_close = pt
            post_close = next((pt for pt in series if _gt(pt['date'], pcd)), None)
        return {
            'q': _g(p, 'q'), 'pre': pre_close['close'] if pre_close else None, 'post': post_close['close'] if post_close else None,
            'move_pct': _round(pct_change(post_close['close'], pre_close['close']), 1) if (pre_close and post_close) else None
        }
    evolution['reactions'] = [reaction_at(p, False if timing_unknown else before_open) for p in prints]
    evolution['timing_basis'] = 'unknown (after-close shown)' if timing_unknown else ('before_open' if before_open else 'after_close')
    if timing_unknown:
        evolution['reactions_alt'] = [reaction_at(p, True) for p in prints]
        flips = []
        for i, r in enumerate(evolution['reactions']):
            a = evolution['reactions_alt'][i] if i < len(evolution['reactions_alt']) else U
            if r['move_pct'] is None or not _t(a) or a['move_pct'] is None:
                continue
            flips.append({'q': r['q'], 'after': r['move_pct'], 'before': a['move_pct'],
                          'flip': not _seq(_sign(r['move_pct']), _sign(a['move_pct']))})
        flipped = [f for f in flips if f['flip']]
        warn('[reaction] CALL TIMING IS UNDETERMINED for this listing, so the reaction row is ' +
             'CONVENTION-DEPENDENT and the table shows the after-close reading. Both readings: ' +
             '; '.join(_S(f['q']) + ' ' + ('+' if f['after'] > 0 else '') + _S(f['after']) + '% after-close vs ' +
                       ('+' if f['before'] > 0 else '') + _S(f['before']) + '% before-open' for f in flips) + '. ' +
             ((_S(len(flipped)) + ' print(s) CHANGE SIGN between the two (' +
               _join([f['q'] for f in flipped], ', ') + '). ') if flipped else '') +
             'State in the report that the convention could not be established from InSync evidence ' +
             'and which reading the table uses. §9\'s ET-timestamp evidence path exists only for US ' +
             'releases; for a non-US listing, set "unknown" rather than accepting the default.')
    rx_gaps = [r['q'] for r in evolution['reactions'] if r['move_pct'] is None]
    if rx_gaps:
        warn('[completeness] Next-day stock move missing for ' + _join(rx_gaps, ', ') +
             ' -> fetch get_market_data daily closes for a window around each of those call dates (call_date-3d .. call_date+3d) into market.reactions')

    def beat_eligible(k):
        return _or(_g(k, 'fmt'), '$M') != '%' and _or(_g(k, 'polarity'), 'higher') != 'neutral'
    beat_kpi = next((k for k in kpis if beat_eligible(k)), None)
    BK = _g(beat_kpi, 'ltcode') if beat_kpi is not None else None
    if beat_kpi is None:
        warn('[beat] no beat-eligible top KPI (none is both absolute and directional) - ' +
             'beat chart/table shows revenue only')
    elif kpis and beat_kpi is not kpis[0]:
        warn('[beat] top KPI "' + _S(_g(kpis[0], 'name')) +
             '" is not beat-eligible (' + ('%-format' if _seq(_g(kpis[0], 'fmt'), '%') else 'directionless: ' +
                                           _S(_g(kpis[0], 'polarity'))) + '); using "' + _S(_g(beat_kpi, 'name')) + '" instead')
    if not kpis:
        warn('[metricset] this ticker carries NO top_kpi rows - degrading to a FINANCIALS-ONLY portal as specified; the Top Operating KPIs block and the KPI beat series are omitted, not blank')
    beat = {'prints': [], 'avg_rev_beat': None, 'avg_kpi1_beat': None, 'avg_reaction': None,
            'whisper': None, 'avg_basis': None}
    credit = {}

    def densest_on_or_before(series, date_str):
        if not _t(series) or not len(series):
            return None
        in_win = [s for s in series if _le(s['date'], date_str)]
        if not in_win:
            return None
        known = [s for s in in_win if s['brokers'] is not None and s['brokers'] is not U]
        if len(known) < 2:
            return in_win[-1]
        acc = known[0]
        for b in known[1:]:
            acc = b if (b['brokers'] > acc['brokers'] or (_seq(b['brokers'], acc['brokers']) and _ge(b['date'], acc['date']))) else acc
        return acc

    def discredit(cs, ac):
        if not cs or ac is None:
            return 'no pre-print consensus'
        if cs['brokers'] is not None and _lt(cs['brokers'], 1):
            return 'no pre-print consensus'
        if cs['min'] is None or cs['max'] is None:
            return None
        if cs['brokers'] is not None and _lt(cs['brokers'], 3):
            return None
        if not _t(cs['value']):
            return None
        span = abs(_sub(cs['max'], cs['min']))
        rel = _div(span, abs(cs['value']))
        ratio = _div(cs['max'], cs['min']) if (cs['min'] > 0 and cs['max'] > 0) else None
        if rel > 0.40 or (ratio is not None and ratio > 1.5):
            return 'incoherent vintage: its range spans ' + pct1(rel * 100) + '% of its own mean'
        br = _sub(ac, cs['max']) if _gt(ac, cs['max']) else (_sub(cs['min'], ac) if _lt(ac, cs['min']) else 0)
        if _is_zero(br):
            return None
        if abs(_div(br, cs['value'])) <= 0.01:
            return None
        return ('actual outside the whole consensus range by ' +
                pct1(abs(_div(br, cs['value'])) * 100) + '% of consensus')
    for p in meta['prints'][-6:]:
        pq, pcd = _g(p, 'q'), _g(p, 'call_date')
        rk = _S(REV) + '|' + _S(pq)
        cons_r = densest_on_or_before(snaps[rk], pcd) if _t(snaps.get(rk, U)) else None
        bk_key = _S(BK) + '|' + _S(pq)
        cons_k = densest_on_or_before(snaps[bk_key], pcd) if (_t(BK) and _t(snaps.get(bk_key, U))) else None
        a_r = actual(REV, pq)
        a_k = actual(BK, pq) if _t(BK) else None

        def sane(c, a):
            if c is not None and a is not None and not _is_zero(a) and _div(c, a) > 0.4 and _div(c, a) < 2.5:
                return c
            if c is not None:
                warn('[beat] insane pre-print consensus for ' + _S(pq) + ' dropped (' + _S(c) + ' vs actual ' + _S(a) + ')')
                return None
            return None
        c_r = sane(cons_r['value'] if cons_r else None, a_r)
        c_k = sane(cons_k['value'] if cons_k else None, a_k)
        if a_r is not None and cons_r is None:
            cgap([REV, _S(REV) + '|' + _S(pq), 'beat|' + _S(pq)],
                 'beat history: no pre-print revenue consensus for ' + _S(pq) + ' -> fetch preprint_snapshots for ' + _S(REV) + ' (updatedate = print-21d .. print-1d)')
        if a_k is not None and cons_k is None:
            cgap([BK, _S(BK) + '|' + _S(pq), 'beat|' + _S(pq)],
                 'beat history: no pre-print ' + _S(_g(beat_kpi, 'name')) + ' consensus for ' + _S(pq) + ' -> fetch preprint_snapshots for ' + _S(BK) + ' (updatedate = print-21d .. print-1d)')
        credit[_S(pq)] = {'rev': discredit(cons_r, a_r), 'kpi': discredit(cons_k, a_k),
                          'csR': cons_r, 'acR': a_r, 'csK': cons_k, 'acK': a_k}
        if cons_k and cons_r:
            b_k, b_r = cons_k['brokers'], cons_r['brokers']
            pass
        rx = next((r for r in evolution['reactions'] if _seq(r['q'], pq)), U)
        if not _t(rx):
            series = _or(mkt.get(tid, U), [])
            pre_, post_ = None, None
            if before_open:
                for pt in series:
                    if _lt(pt['date'], pcd):
                        pre_ = pt
                post_ = next((pt for pt in series if _ge(pt['date'], pcd)), None)
            else:
                for pt in series:
                    if _le(pt['date'], pcd):
                        pre_ = pt
                post_ = next((pt for pt in series if _gt(pt['date'], pcd)), None)
            rx = {'pre': pre_['close'] if pre_ else None, 'post': post_['close'] if post_ else None,
                  'move_pct': _round(pct_change(post_['close'], pre_['close']), 1) if (pre_ and post_) else None}
        beat['prints'].append({
            'q': pq, 'rev_beat_pct': _round(growth_pct(a_r, c_r), 2), 'kpi1_beat_pct': _round(growth_pct(a_k, c_k), 2),
            'reaction_pct': rx['move_pct'],
            'rev_excluded': None, 'kpi1_excluded': None
        })
        last_p = beat['prints'][-1]
        cr = _or(credit.get(_S(pq), U), {})
        if last_p['rev_beat_pct'] is not None:
            last_p['rev_excluded'] = _or(cr.get('rev', U), None)
        if last_p['kpi1_beat_pct'] is not None:
            last_p['kpi1_excluded'] = _or(cr.get('kpi', U), None)

    def cr_of(q):
        return _or(credit.get(_S(q), U), {})

    def live_brokers(lt):
        s = latest_of(est.get(_S(lt) + '|' + _S(_g(Q, 'target_q'))))
        return s['brokers'] if (s and s['brokers'] is not None and s['brokers'] is not U) else None
    RANGE_RE = '^actual outside the whole'
    for ck, ex_key, lbl, lt in [['rev', 'rev_excluded', 'revenue', REV],
                                ['kpi', 'kpi1_excluded', _g(beat_kpi, 'name') if beat_kpi is not None else 'KPI',
                                 _g(beat_kpi, 'ltcode') if beat_kpi is not None else None]]:
        live = live_brokers(lt) if _t(lt) else None
        unrep = 0
        for p2 in beat['prints']:
            cr = cr_of(p2['q'])
            why = cr.get(ck, U)
            if not _test(RANGE_RE, _S(_or(why, ''))):
                continue
            cs = cr.get('csR', U) if ck == 'rev' else cr.get('csK', U)
            if live is None or not _t(cs) or cs['brokers'] is None:
                continue
            if _lt(cs['brokers'], live * 0.60):
                cr[ck] = None
                p2[ex_key] = None
                unrep += 1
        if unrep:
            warn('[beat] ' + _S(lbl) + ': the range test was NOT APPLIED to ' + _S(unrep) + ' print(s) - ' +
                 'their pre-print snapshots carry fewer than 60% of the ' + _S(live) + ' brokers on the live ' +
                 'consensus, so their MIN/MAX is a minority of the Street and narrower than the real range ' +
                 'by construction. Re-fetch those windows per data-collection.md §7 (without the `latest` ' +
                 'filter) if you need the breach test to mean anything.')
        breached = [p2 for p2 in beat['prints'] if _test(RANGE_RE, _S(_or(cr_of(p2['q']).get(ck, U), '')))]
        scored = [p2 for p2 in beat['prints'] if p2['rev_beat_pct' if ck == 'rev' else 'kpi1_beat_pct'] is not None]
        if len(scored) >= 3 and len(breached) > len(scored) / 3:
            for p2 in breached:
                cr = credit.get(_S(p2['q']), U)
                if _t(cr):
                    cr[ck] = None
                p2[ex_key] = None
            warn('[beat] ' + _S(lbl) + ': ' + _S(len(breached)) + ' of ' + _S(len(scored)) + ' prints (' +
                 _join([p2['q'] for p2 in breached], ', ') + ') printed OUTSIDE the whole pre-print range. That ' +
                 'is a PATTERN for this issuer, not a run of definitional breaks, so they are kept in the ' +
                 'average - excluding them would report a track record the prints do not show. Read the ' +
                 'average as "consistently ahead of the pre-print range", and say so wherever it is quoted.')
    for ck, ex_key, val_key, lbl in [['rev', 'rev_excluded', 'rev_beat_pct', 'revenue'],
                                     ['kpi', 'kpi1_excluded', 'kpi1_beat_pct', _g(beat_kpi, 'name') if beat_kpi is not None else 'KPI']]:
        scored = [p2 for p2 in beat['prints'] if p2[val_key] is not None and p2[val_key] is not U]
        if len(scored) < 4:
            continue
        for p2 in scored:
            if _t(cr_of(p2['q']).get(ck, U)):
                continue
            others = js_cmp_sort([abs(x[val_key]) for x in scored if x is not p2], lambda a, b: a - b)
            med = others[(len(others) - 1) // 2] if len(others) % 2 else \
                _div(others[len(others) // 2 - 1] + others[len(others) // 2], 2)
            lim = max(3 * med, 5)
            if abs(p2[val_key]) <= lim:
                continue
            why = 'beat of ' + pct1(p2[val_key]) + '% is ' + ((js_to_fixed(_round(_div(abs(p2[val_key]), med), 1), 1) + 'x') if med > 0 else 'far above') + \
                ' the issuer\'s own median beat of ' + pct1(med) + '%'
            cr = credit.get(_S(p2['q']), U)
            if _t(cr):
                cr[ck] = why
            p2[ex_key] = why
            warn('[beat] ' + _S(p2['q']) + ' ' + _S(lbl) + ': ' + why + '. One print that far outside the ' +
                 'issuer\'s own distribution is a definitional change, an acquisition or a one-off, not ' +
                 'the same quantity the other prints measure, and it moves the average and the whisper ' +
                 'more than every other print combined. The cell is kept and marked; say what broke.')
    for p2 in beat['prints']:
        cr = cr_of(p2['q'])
        for ck, lbl, cs, ac in [['rev', 'revenue', cr.get('csR', U), cr.get('acR', U)],
                                ['kpi', _g(beat_kpi, 'name') if beat_kpi is not None else 'KPI', cr.get('csK', U), cr.get('acK', U)]]:
            why = cr.get(ck, U)
            if not _t(why) or not _t(cs) or ac is None or ac is U:
                continue
            if _test('^no pre-print consensus$', why) or _test('^thin mark, not a consensus$', why):
                continue
            if _test('^incoherent', why):
                warn('[beat] ' + _S(p2['q']) + ' ' + _S(lbl) + ': the pre-print vintage is NOT INTERNALLY COHERENT (' +
                     why + '). A range that wide is a stale or partial snapshot, not the Street\'s view, so ' +
                     'neither the beat nor its sign can be read from it. The per-print cell is kept and marked; ' +
                     'the average and the whisper exclude it. Re-fetch the window per data-collection.md §7 ' +
                     'before quoting this print.')
            else:
                row_fmt = '$M' if ck == 'rev' else _or(_and(beat_kpi, lambda: _g(beat_kpi, 'fmt')), '$M')
                dp = 4 if (_seq(row_fmt, '%') or _seq(row_fmt, '$')) else 0
                wid = _round(_div(100 * _sub(cs['max'], cs['min']), abs(cs['value'])), 2) \
                    if (cs['max'] is not None and cs['min'] is not None and _t(cs['value'])) else None
                warn('[beat] ' + _S(p2['q']) + ' ' + _S(lbl) + ': the ACTUAL (' + _S(_round(ac, dp)) + ') falls OUTSIDE the ' +
                     'entire pre-print range (' + _S(_round(cs['min'], dp)) + ' to ' + _S(_round(cs['max'], dp)) + ', ' +
                     _S(cs['brokers']) + ' brokers) by more than the vintage\'s own dispersion allows (' + why +
                     '), and this issuer does NOT do so routinely. That makes it a definitional change or a ' +
                     'one-off rather than a consensus miss, and it contaminates the average beat and the ' +
                     'whisper. Exclude it from the read-out or explain what broke.' +
                     ((' CONTEXT: this vintage\'s whole range spans ' + _S(wid) + '% of ' +
                       'consensus' + ((', which is TIGHT - on a range this narrow any beat ' +
                                       'above roughly ' + _S(_round(wid * 0.9, 1)) + '% breaches it by construction, so ' +
                                       'check whether this is a definitional break or simply a good print against a ' +
                                       'clustered Street. The caption prints the average both ways; quote the ' +
                                       'all-prints figure if you judge the exclusion an artefact.') if wid < 3 else '.'))
                      if wid is not None else ''))

    def basis(key, ex_key):
        used = [p for p in beat['prints'] if p[key] is not None and not _t(p[ex_key])]
        return {'used': [p['q'] for p in used],
                'excluded': [{'q': p['q'], 'why': p[ex_key]} for p in beat['prints'] if _t(p[ex_key])],
                'n': len(used),
                'avg': _round(mean([p[key] for p in used]), 2),
                'avg_all_prints': _round(mean([p[key] for p in beat['prints']]), 2)}
    b_r2, b_k2 = basis('rev_beat_pct', 'rev_excluded'), basis('kpi1_beat_pct', 'kpi1_excluded')

    def thin_avg(b, lbl):
        if b['n'] >= 2:
            return b['avg']
        warn('[beat] ' + _S(lbl) + ': only ' + _S(b['n']) + ' print' + ('' if b['n'] == 1 else 's') +
             ' carries a usable pre-print consensus' + ((' (' + _join(b['used'], ', ') + ')') if b['used'] else '') +
             ', so the average beat is left BLANK rather than repeating a single observation under an ' +
             '"Avg" heading' + (', and the whisper is not computed' if lbl == 'revenue' else '') +
             '. State the single print in Beat History if it is worth showing at all.')
        return None
    beat['avg_rev_beat'] = thin_avg(b_r2, 'revenue')
    beat['avg_kpi1_beat'] = thin_avg(b_k2, _g(beat_kpi, 'name') if beat_kpi is not None else 'KPI')
    THIN_N = 4
    for lbl, b in [['revenue', b_r2], [_g(beat_kpi, 'name') if beat_kpi is not None else 'KPI', b_k2]]:
        if b['n'] >= 2 and b['n'] < THIN_N:
            warn('[beat] ' + _S(lbl) + ': the average beat rests on only ' + _S(b['n']) + ' of ' + _S(len(beat['prints'])) +
                 ' prints (' + _join(b['used'], ', ') + '), so it is a thin track record, not a pattern. It ' +
                 'reads ' + _S(b['avg']) + '% here and ' + _S(b['avg_all_prints']) + '% across all prints; the whisper ' +
                 'inherits the thin figure. Say which prints it rests on wherever it is quoted.')
    beat['avg_withheld'] = {'revenue': beat['avg_rev_beat'] is None, 'kpi1': beat['avg_kpi1_beat'] is None}
    beat['avg_thin'] = {'revenue': b_r2['n'] < THIN_N, 'kpi1': b_k2['n'] < THIN_N}
    beat['avg_basis'] = {'revenue': b_r2, 'kpi1': b_k2}
    for p in beat['prints']:
        if p['reaction_pct'] is not None and abs(_n(p['reaction_pct'])) > 50:
            warn('[market] the ' + _S(p['q']) + ' print reaction computes to ' + _S(p['reaction_pct']) + '%, which ' +
                 'is not a plausible one-day move. Check the two closes either side of the call date: a ' +
                 'duplicate row, an unadjusted split or a stub price will produce exactly this. The ' +
                 'figure is shown as computed, so correct the series rather than the number.')
    beat['avg_reaction'] = _round(mean([p['reaction_pct'] for p in beat['prints']]), 2) \
        if len([p for p in beat['prints'] if p['reaction_pct'] is not None]) >= 2 else None
    for lbl, b in [['revenue', b_r2], [_g(beat_kpi, 'name') if beat_kpi is not None else 'KPI', b_k2]]:
        if not b['excluded']:
            continue
        if not b['n']:
            warn('[beat] ' + _S(lbl) + ': EVERY print is discredited (' +
                 '; '.join(_S(e['q']) + ': ' + _S(e['why']) for e in b['excluded']) + '), so the average beat and the ' +
                 'whisper are blank rather than misleading. State in Beat History that no usable ' +
                 'pre-print consensus exists for this line.')
        else:
            warn('[beat] ' + _S(lbl) + ': average beat computed on ' + _S(b['n']) + ' of ' + _S(len(beat['prints'])) +
                 ' prints (' + _join(b['used'], ', ') + ') = ' + _S(b['avg']) + '%. Excluded ' +
                 '; '.join(_S(e['q']) + ': ' + _S(e['why']) for e in b['excluded']) + '. On all ' + _S(len(beat['prints'])) +
                 ' prints it would read ' + _S(b['avg_all_prints']) + '%. Say in Beat History which prints the ' +
                 'average rests on.')
    cons_tq = latest_est(REV, _g(Q, 'target_q'))
    beat['whisper'] = _round(_mul(cons_tq, 1 + beat['avg_rev_beat'] / 100), 0) \
        if (cons_tq is not None and beat['avg_rev_beat'] is not None) else None
    beat['show'] = len(beat['prints']) > 0
    if not beat['show']:
        warn('[beat] no reported print is available, so the Beat History card is OMITTED rather ' +
             'than rendered as an empty table above a blank chart (STRICT RULE 6). If this issuer HAS ' +
             'reported, meta.prints was not populated: re-run get_call_date and the pre-print snapshots.')
    beat['kpi1_name'] = _g(beat_kpi, 'name') if beat_kpi is not None else None
    beat['kpi1_polarity'] = _or(_g(beat_kpi, 'polarity'), 'higher') if beat_kpi is not None else None
    beat['kpi1_ltcode'] = BK

    P = _or(_g(MS, 'peer_ids'), {})
    peer_t = js_keys(P) if isinstance(P, dict) else ([_S(i) for i in range(len(P))] if isinstance(P, list) else [])
    pm = _or(_g(mcp, 'peers'), {})
    p_mkt = index_market(extract_rows(_or(_g(pm, 'market_current'), [])))
    p_yr = index_market_summary(extract_rows(_or(_g(pm, 'market_yr'), [])))
    v_ntm = index_valuation(extract_rows(_or(_g(pm, 'val_ntm'), [])))
    v_stm = index_valuation(extract_rows(_or(_g(pm, 'val_stm'), [])))
    cal_rows = extract_rows(_or(_g(pm, 'cal_rev'), [])) + extract_rows(_or(_g(pm, 'cal_rev_actual'), [])) + \
        extract_rows(_or(_g(pm, 'cal_fcf'), []))
    cal = {}
    for r in cal_rows:
        if not _t(_g(r, 'LTCODE')) or not _t(_g(r, 'CALENDARIZED_FISCALPERIOD')):
            continue
        cfp = r['CALENDARIZED_FISCALPERIOD']
        if not isinstance(cfp, str):
            raise TypeError('r.CALENDARIZED_FISCALPERIOD.replace is not a function')
        cal[_S(_g(r, 'TICKER_ID')) + '|' + _S(r['LTCODE']) + '|' + cfp.replace('CY-', '', 1)] = parse_value(_g(r, 'VALUE'))
    gm_fall = index_valuation(extract_rows(_or(_g(pm, 'gm_fallback'), [])))
    multi_pref = _g(MS, 'multiples')
    tickers = [{'t': _g(meta, 'ticker'), 'id': tid, 'subject': True}] + \
        [{'t': t, 'id': _S(P[t] if isinstance(P, dict) else P[int(t)]), 'subject': False} for t in peer_t]

    def peer_row(o):
        last_pt = _or(latest_of(p_mkt.get(o['id'])), px if o['subject'] else None)
        y = _or(p_yr.get(o['id'], U), yr if o['subject'] else {})
        cy0, cy1, cyp = _g(FY, 'fy0'), _g(FY, 'fy1'), _g(FY, 'fy_prev')
        ck = o['id'] + '|' + _S(REV) + '|'
        g26 = pct_change(cal.get(ck + _S(cy0), U), cal.get(ck + _S(cyp), U))
        g27 = pct_change(cal.get(ck + _S(cy1), U), cal.get(ck + _S(cy0), U))
        gm_adj, gm_gaap = v_ntm.get(o['id'] + '|Gross_Margin_Adj', U), gm_fall.get(o['id'] + '|Gross_Margin', U)

        def nm(v):
            return None if (v is U or v is None or _le(v, 0)) else _round(v, 1)

        def mult(mp):
            ntm_m = _g(mp, 'ntm_metric')
            stm_m = _g(mp, 'stm_metric')
            if not _t(stm_m):
                if not isinstance(ntm_m, str):
                    raise TypeError("Cannot read properties of undefined (reading 'replace')")
                stm_m = ntm_m.replace('_Adj_Ratio', '_Ratio', 1)
            return {'label': _g(mp, 'label'),
                    'ntm': nm(v_ntm.get(o['id'] + '|' + _S(ntm_m), U)),
                    'cy1': nm(v_stm.get(o['id'] + '|' + _S(stm_m), U))}
        gms_adj, gms = v_stm.get(o['id'] + '|Gross_Margin_Adj', U), v_stm.get(o['id'] + '|Gross_Margin', U)
        em, ems = v_ntm.get(o['id'] + '|EBITDA_Margin', U), v_stm.get(o['id'] + '|EBITDA_Margin', U)
        return {
            'ticker': o['t'], 'subject': o['subject'],
            'price': last_pt['close'] if last_pt else None,
            'pct_high': _round(pct_change(last_pt['close'], y['max']), 1) if (last_pt and _t(y.get('max', U))) else None,
            'pct_low': _round(pct_change(last_pt['close'], y['min']), 1) if (last_pt and _t(y.get('min', U))) else None,
            'mkt_cap': _round(v_ntm.get(o['id'] + '|Market Cap', U), 0), 'ev': _round(v_ntm.get(o['id'] + '|EV', U), 0),
            'multiples': [mult(mp) for mp in _as_array(multi_pref)],
            'growth': {'cy0': _round(g26, 0), 'cy1': _round(g27, 0)},
            'gross_margin': {'v': _round(_mul(gm_adj, 100), 0), 'gaap': False} if gm_adj is not U else
                            ({'v': _round(_mul(gm_gaap, 100), 0), 'gaap': True} if gm_gaap is not U else None),
            'ebitda_margin': _round(_mul(em, 100), 0) if em is not U else None,
            'gross_margin_stm': _round(_mul(gms_adj, 100), 0) if gms_adj is not U else (_round(_mul(gms, 100), 0) if gms is not U else None),
            'ebitda_margin_stm': _round(_mul(ems, 100), 0) if ems is not U else None
        }
    peers = {'order': [_g(m, 'label') for m in _as_array(multi_pref)], 'rows': [peer_row(o) for o in tickers]}
    peers['stats'] = {
        'multiples': [{
            'label': _g(mp, 'label'),
            'avg_ntm': _round(mean([r['multiples'][i]['ntm'] for r in peers['rows']]), 1),
            'med_ntm': _round(median([r['multiples'][i]['ntm'] for r in peers['rows']]), 1),
            'avg_cy1': _round(mean([r['multiples'][i]['cy1'] for r in peers['rows']]), 1),
            'med_cy1': _round(median([r['multiples'][i]['cy1'] for r in peers['rows']]), 1)
        } for i, mp in enumerate(_as_array(multi_pref))],
        'growth': {
            'avg_cy0': _round(mean([r['growth']['cy0'] for r in peers['rows']]), 0), 'med_cy0': _round(median([r['growth']['cy0'] for r in peers['rows']]), 0),
            'avg_cy1': _round(mean([r['growth']['cy1'] for r in peers['rows']]), 0), 'med_cy1': _round(median([r['growth']['cy1'] for r in peers['rows']]), 0)
        }
    }

    two_yr = []
    for k in ['revenue', 'ebitda', 'eps', 'kpi1', 'fcf']:
        code = {'revenue': REV, 'ebitda': _g(_or(fin_m[1] if len(fin_m) > 1 else U, {}), 'ltcode'), 'eps': EPS, 'kpi1': KPI1,
                'fcf': _or(_g(MS, 'fcf_ltcode'), 'CF_P_110')}[k]
        if not _t(code):
            continue
        a, e0, e1 = actual(code, _g(FY, 'fy_prev')), latest_est(code, _g(FY, 'fy0')), latest_est(code, _g(FY, 'fy1'))
        two_yr.append({'key': k, 'ltcode': code, 'fy_prev': a, 'fy0': e0, 'fy1': e1,
                       'g0': _round(pct_change(e0, a), 1), 'g1': _round(pct_change(e1, e0), 1)})
    irr = None
    eps_prev, eps_y2 = actual(EPS, _g(FY, 'fy_prev')), latest_est(EPS, _g(FY, 'fy1'))
    anchor = setup['pe_1yr_avg'] if setup else None
    price = valuation['price']
    if eps_y2 is not None and _gt(eps_y2, 0) and _t(anchor) and _t(price):
        def irr_of(mult_, e):
            return _round(_mul(_pow(_div(_mul(mult_, e), price), 1 / 2) - 1, 100), 1)
        req_eps = _round(_div(_mul(price, _pow(1.15, 2)), anchor), 2)
        mult_axis = [_round(_mul(anchor, s), 1) for s in [0.745, 0.882, 1.0, 1.124, 1.246]]
        eps_axis = [_round(_mul(eps_y2, s), 2) for s in [1.2, 1.1, 1.0, 0.9, 0.8]]
        irr = {
            'anchor_multiple': anchor, 'price': price, 'eps_y2_consensus': eps_y2,
            'consensus_irr': irr_of(anchor, eps_y2),
            'required_eps_15': req_eps,
            'required_vs_cons_pct': _round(pct_change(req_eps, eps_y2), 1),
            'consensus_cagr': _round((_pow(_div(eps_y2, eps_prev), 1 / 2) - 1) * 100, 1) if _t(eps_prev) else None,
            'required_cagr': _round((_pow(_div(req_eps, eps_prev), 1 / 2) - 1) * 100, 1) if _t(eps_prev) else None,
            'multiples_axis': mult_axis, 'eps_axis': eps_axis,
            'grid': [[irr_of(m, e) for m in mult_axis] for e in eps_axis]
        }
    else:
        warn('[irr] skipped - ' + ('no FY+1 EPS' if eps_y2 is None else ('negative FY+1 EPS (loss-maker)' if _le(eps_y2, 0) else 'no anchor P/E or price')))

    def v_(code):
        return latest_est(code, _g(Q, 'target_q'))
    ocf_code = _or(_g(MS, 'ocf_ltcode'), 'CF_P_108')
    capex_code = _or(_g(MS, 'capex_ltcode'), 'CF_P_109')
    fcf_code = _or(_g(MS, 'fcf_ltcode'), 'CF_P_110')
    ocf, capex_v, fcf_v = v_(ocf_code), v_(capex_code), v_(fcf_code)
    if (ocf is not None and capex_v is not None and fcf_v is not None and
            abs(_sub(_n(ocf) + _n(capex_v), fcf_v)) > abs(_n(_or(fcf_v, 1))) * 0.02 + 50):
        warn('[check] the FCF row does not equal OCF + capex for ' + _S(_g(Q, 'target_q')) + ': ' +
             _S(ocf_code) + ' ' + _S(_round(ocf, 0)) + ' + ' +
             _S(capex_code) + ' ' + _S(_round(capex_v, 0)) + ' = ' +
             _S(_round(_n(ocf) + _n(capex_v), 0)) + ', against ' + _S(fcf_code) +
             ' = ' + _S(_round(fcf_v, 0)) + ' (' + CUR['symbol'] + 'M). The document prints all three, so as ' +
             'it stands one of them contradicts the other two on the same page. Two real causes: ' +
             '(1) the three lines rest on DIFFERENT broker sets - check the [coverage] warning, ' +
             'because a 1-broker FCF against a multi-broker OCF is not an arithmetic error but it is ' +
             'not reconcilable either, and the read-out must say which; or (2) the issuer\'s FCF is a ' +
             'COMPANY-SPECIFIC definition (ME&T FCF, FCF after distributions) that does not equal ' +
             'OCF+capex by construction - name the definition in the footer. Do not ship the three ' +
             'rows without addressing it.')

    mg_ = _g(mcp, 'guidance')
    outlook_src = _or(_or(_or(_and(mg_, lambda: _g(mg_, 'outlook')), _g(mcp, 'guidance_outlook')), _g(mcp, 'outlook')), [])
    outlook_rows = []
    for r in extract_rows(outlook_src):
        o = {
            'lineitem': _or(_or(_or(_g(r, 'LINEITEM'), _g(r, 'lineitem')), _g(r, 'METRIC')), None),
            'target': _or(_or(_g(r, 'FISCALPERIOD'), _g(r, 'target')), None),
            'source': _or(_or(_g(r, 'SOURCE_PERIOD'), _g(r, 'source')), None),
            'text': _trim(_replace('\\s+', _S(_or(_or(_or(_g(r, 'GUIDE_TEXT'), _g(r, 'text')), _g(r, 'VALUE')), '')), ' ', glob=True))
        }
        if o['text']:
            outlook_rows.append(o)

    def _citable():
        vals, pcts, counts = _NumSet(), _NumSet(), _NumSet()

        def add(x, kind=U):
            if not _num_is_finite(x):
                return
            vals.add(x)
            vals.add(abs(x))
            if kind == 'pct':
                pcts.add(x)
                pcts.add(abs(x))
            if kind == '#':
                counts.add(x)
                counts.add(abs(x))
        Vc = _or(valuation, {})
        for k in ['price', 'market_cap', 'total_debt', 'cash', 'net_debt', 'ev', 'ev_insync', 'wk52_high',
                  'wk52_low', 'bvps', 'ntm_pe', 'ntm_ev_ebitda']:
            add(Vc.get(k, U))
        for k in ['pct_off_high', 'pct_above_low', 'fcf_yield_ntm']:
            add(Vc.get(k, U), 'pct')
        if _isnum(Vc.get('dividend_yield', U)):
            add(_round(Vc['dividend_yield'] * 100, 2), 'pct')
        add(Vc.get('net_debt_ebitda', U))
        for L in _or(Vc.get('lenses', U), []):
            if not _isnum(L['value']):
                continue
            kind = 'pct' if L['kind'] == 'pct' else 'values'
            add(L['value'], 'pct' if kind == 'pct' else U)
            dp = 2 if _seq(L['metric'], 'Dividend_Yield') else 1
            add(_round(L['value'], dp), 'pct' if kind == 'pct' else U)
        if setup:
            add(setup['move_30d_pct'], 'pct')
            for k in ['drawdown_low', 'heaviest_volume_shares_m', 'pe_now', 'pe_1yr_avg', 'pe_year_ago']:
                add(setup[k])
        CC = ['py_q', 'prior_q', 'target_q', 'next_q', 'fy_prev', 'fy0', 'fy1']
        for r in _or(sf['rows'], []):
            kind = 'pct' if _seq(r['fmt'], '%') else ('#' if _seq(r['fmt'], '#') else None)
            for k in CC:
                add(r.get(k, U), kind)
            for s in _or(r.get('subs', U), []):
                for k in CC:
                    add(_or(s.get('cells', U), {}).get(k, U), 'pct')
        for k in ['revenue', 'eps']:
            g = _or(sf.get('growth', U), {}).get(k, U)
            if not _t(g):
                continue
            for w in ['yy', 'qq']:
                for c in CC:
                    add(_or(g.get(w, U), {}).get(c, U), 'pct')
        for k in ['guidance_target_q', 'guidance_fy', 'guidance_fy1']:
            for g in _or(sf.get(k, U), []):
                add(g['lo'])
                add(g['hi'])
                add(g['point'])
        for r in _or(revisions['rows'], []):
            for k in ['pre', 'post', 'interq', 'latest']:
                add(r[k], 'pct' if r['is_pct'] else None)
            add(r['delta_vs_post'], 'pct' if (r['delta_unit'] == '%' or r['delta_unit'] == 'bps') else None)
        for b in _or(evolution['blocks'], []):
            for c in _or(b['cols'], []):
                for k in ['guide_hi', 'guide_lo', 'guide_point', 'consensus', 'actual']:
                    add(c[k], 'pct' if b['is_pct'] else None)
                for k in ['vs_guide_hi', 'vs_cons']:
                    add(c[k], 'pct')
        for r in _or(evolution.get('reactions', U), []):
            add(r['pre'])
            add(r['post'])
            add(r['move_pct'], 'pct')
        for pr in _or(beat['prints'], []):
            for k in ['rev_beat_pct', 'kpi1_beat_pct', 'reaction_pct']:
                add(pr.get(k, U), 'pct')
            for k in ['pre', 'post']:
                add(pr.get(k, U))
        for k in ['avg_rev_beat', 'avg_kpi1_beat', 'avg_reaction']:
            add(beat[k], 'pct')
        add(beat['whisper'])
        ab = _or(beat.get('avg_basis', U), {})
        for kk in js_keys(ab):
            b = ab[kk]
            add(b['avg'], 'pct')
            add(b['avg_all_prints'], 'pct')

        def add_text(t):
            s0 = _S(_or(t, ''))
            kind = 'pct' if _test('%|\\bbps?\\b', s0, 'i') else None
            found = _match_all('-?\\d+(?:\\.\\d+)?', s0.replace(',', ''))
            if found is not None:
                for s in found:
                    add(js_parse_float(s), kind)
        for k in ['target_q', 'fy', 'fy1']:
            for g in _or(sf.get('guidance_' + k, U), []):
                if guide_opens_column(g):
                    add_text(g['text'])
        for g in _or(live_fy, []):
            if guide_opens_column(g):
                add_text(_and(g['parsed'], lambda: g['parsed']['text']))
        outlook_held = len(outlook_rows)
        if outlook_held > 0 or len(_or(stale_guides, [])):
            warn('[guidance-freshness] ' + _S(len(_or(stale_guides, []))) + ' superseded guide(s) and ' +
                 _S(outlook_held) + ' outlook row(s) are NOT in the citable index, so their figures ' +
                 'will be REFUSED by the narrative gate - that is correct, not a gate bug. They are ' +
                 'commentary about what management said THEN, and the document prints the latest call ' +
                 'only. On O this stopped six different investment-volume figures (10, 9.5, 8, 5.5, 5, 4) ' +
                 'from all validating as current. To reference one, attribute it as a quote with the call ' +
                 'it came from ("on the 4Q25 call management guided $8.0bn") - quoted spans are exempt ' +
                 'from the number scan and reported as an advisory for you to verify.')
        gp = _NumSet()
        for g in _or(stale_guides, []):
            p = _and(g, lambda: g['parsed'])
            if not _t(p):
                continue
            for v in [p['lo'], p['hi'], p['point']]:
                if _num_is_finite(v) and v != 0:
                    gp.add(v)
                    gp.add(abs(v))
        arr = sorted(x for x in vals.items() if x != 0)
        return {'values': arr, 'b_ok': [x / 1000 for x in arr if abs(x) >= 1000],
                'pct': sorted(x for x in pcts.items() if x != 0),
                'counts': sorted(x for x in counts.items() if x != 0),
                'guide_prior': sorted(gp.items()),
                'count': len(arr)}
    citable = _citable()

    def norm(s):
        s = _replace('\\(.*?\\)', _S(_or(s, '')).lower(), ' ', glob=True)
        s = _replace('\\b(total|consolidated|adjusted|adj|ex-?1x|non-?gaap|company|co\\.)\\b', s, ' ', glob=True)
        return _trim(_replace('[^a-z0-9]+', s, ' ', glob=True))

    def first_num(t):
        m = _exec('-?\\d+(?:\\.\\d+)?', _S(_or(t, '')).replace(',', ''))
        return js_parse_float(m[0]) if m else None
    FAMILY = '\\b(ebitda|ebit|operating income|operating profit|net income|free cash flow|fcf|capex|capital expenditure)\\b'

    def is_expr(s):
        return _test('[\\/+]|\\)\\s*\\/|\\bless\\b|\\bminus\\b', _S(_or(s, '')), 'i')
    clashes2 = []
    for o in outlook_rows + [{'lineitem': g['lineitem'], 'text': g['parsed']['text'], 'target': g['target']} for g in _or(live_fy, [])]:
        if is_expr(o['lineitem']) or not _test(FAMILY, o['lineitem'], 'i'):
            continue
        n = norm(o['lineitem'])
        if not n or len(n.split(' ')) < 1:
            continue
        row = next((r for r in sf['rows'] if _seq(r['fmt'], '$M') and norm(r['name']) == n and _test(FAMILY, r['name'], 'i')), U)
        if not _t(row) or row['fy0'] is None or row['fy0'] is U:
            continue
        v = first_num(o['text'])
        if v is None or v == 0:
            continue
        ratio = _div(abs(row['fy0']), abs(v))
        if ratio > 1.25 or ratio < 0.8:
            clashes2.append('"' + _S(o['lineitem']) + '" guides ' + _u16slice(_replace('\\s+', o['text'], ' ', glob=True), 0, 40) +
                            ' for ' + _S(o['target']) + ' while the table\'s "' + _S(row['name']) + '" (' + _S(row['ltcode']) +
                            ') prints ' + _S(_round(row['fy0'], 0)))
    if clashes2:
        warn('[guidance] SAME NAME, DIFFERENT DEFINITION: ' + '; '.join(clashes2) + '. The company ' +
             'and the table are using one word for two quantities, and both figures are citable, so a ' +
             'reader can put them side by side and neither is labelled. Usually the guide is on an ' +
             'ADJUSTED basis the table does not carry (often because that code has no consensus and so ' +
             'cannot win the spine slot). Say which basis the table prints wherever the guide is quoted, ' +
             'or supply the adjusted code in Step 0 if InSync estimates it.')

    computed = {
        'generated_from': in_path, 'citable': citable,
        'meta': {'ticker': _g(meta, 'ticker'), 'company': _g(meta, 'company'), 'quarters': Q, 'fiscal': FY, 'asof': _g(meta, 'asof'),
                 'prepared': _or(_g(meta, 'prepared'), None), 'upcoming': _g(meta, 'upcoming'), 'currency': CUR, 'seq_label': SEQ_LABEL},
        'valuation': valuation, 'earnings_setup': setup, 'summary_financials': sf, 'revisions': revisions, 'beat_history': beat,
        'guidance_evolution': evolution, 'guidance_live': {
            'target_q': sf['guidance_target_q'],
            'fy': [map_g(g, True) for g in live_fy],
            'stale': [{'lineitem': g['lineitem'], 'target': g['target'], 'source': g['source'],
                       'lo': g['parsed']['lo'], 'hi': g['parsed']['hi'], 'point': g['parsed']['point'],
                       'bound': _or(g['parsed'].get('bound', U), None),
                       'text': g['parsed']['text']} for g in stale_guides],
            'outlook': outlook_rows
        },
        'peer_comps': peers, 'market_expectations': {'two_year': two_yr, 'irr': irr},
        'warnings': warnings
    }
    js_write(out_path, js_json(computed, 1))
    _log('COMPUTE OK: ' + out_path + ' | rows sf=' + _S(len(sf['rows'])) + ' rev=' + _S(len(revisions['rows'])) +
         ' evoBlocks=' + _S(len(evolution['blocks'])) + ' peers=' + _S(len(peers['rows'])) + ' warnings=' + _S(len(warnings)))
    for w in warnings:
        _log('WARN ' + w)


class _HSet:
    def __init__(self, it=()):
        self._s = set(_hk(x) for x in it)

    def __contains__(self, x):
        return _hk(x) in self._s


class _NumSet:
    def __init__(self):
        self._s = set()

    def add(self, x):
        self._s.add(x)

    def items(self):
        return list(self._s)


def _as_list(x):
    if isinstance(x, list):
        return x
    raise TypeError(_S(x) + '.map is not a function')


def _as_array(x):
    if isinstance(x, list):
        return x
    raise TypeError("Cannot read properties of " + ('undefined' if x is U else _S(x)) + " (reading 'map')")


def _js_min(a, b):
    if a != a or b != b:
        return NaN
    return b if b < a else a


def _js_max(a, b):
    if a != a or b != b:
        return NaN
    return b if b > a else a


if __name__ == '__main__':
    js_stdio()
    try:
        main()
    except _Exit as e:
        sys.stdout.flush()
        sys.exit(e.code)
    except Exception as e:
        sys.stdout.flush()
        sys.stderr.write('Uncaught ' + type(e).__name__ + ': ' + str(e) + '\n')
        sys.exit(1)
    sys.stdout.flush()
