"""fmt.py - the packet's number formatter and its printed set.

Every string this module returns is byte-identical to what an earlier JavaScript formatter
(internal edition only) returned for the same value, and function names match it.

    num(v, d)        v.toLocaleString('en-US', {min/maxFractionDigits: d}), en dash for minus
    cell(v, fmt)     the per-format cell string ('$M', '$', '$3', '%', '%2', 'x', 'k', '#B')
    xfmt(v)          the house multiple: two decimals below 5.00x, one above
    gcell(v, isPct)  a signed change: '+1.2%' / '–35 bps'
    setCcy / ccy / setScale / scale / scaleLabel / printed / record

load() reads packet_computed.json and packet_narrative.json from the working directory;
render_packet.py calls it, so another
Python module can import the formatter without a packet in its working directory.

The second half of this file is the small JavaScript runtime the renderer's port needs: JS
truthiness, String(), ToNumber, relational comparison, strict equality, property reads that
throw V8's own TypeError text, and Node's require() of a JSON file. Python standard library only.
"""
import json
import math
import os
import re
import sys
from decimal import Decimal, ROUND_HALF_UP, localcontext

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from jscompat import js_str, js_number, js_json, _utf16_key, _js_json_error, _JSErr, _pint, _bad_const
from jscompat import UNDEFINED as UNDEF


class JSError(Exception):

    def __init__(self, name, message):
        Exception.__init__(self, message)
        self.name = name
        self.message = message


def type_error(msg):
    return JSError('TypeError', msg)


def nn(x):
    return x is None or x is UNDEF


def is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def is_nan(x):
    return isinstance(x, float) and x != x


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


def jnc(a, b):
    return b if nn(a) else a


def jstr(x):
    if isinstance(x, str):
        return x
    if x is UNDEF:
        return 'undefined'
    if x is None:
        return 'null'
    if x is True:
        return 'true'
    if x is False:
        return 'false'
    if isinstance(x, list):
        return ','.join('' if nn(e) else jstr(e) for e in x)
    if isinstance(x, dict):
        return '[object Object]'
    if isinstance(x, JSFunc):
        return str(x)
    return js_str(x)


def to_prim(x):
    return jstr(x) if isinstance(x, (list, dict, JSFunc)) else x


def to_num(x):
    x = to_prim(x)
    if x is UNDEF:
        return float('nan')
    r = js_number(x)
    if isinstance(r, int) and not isinstance(r, bool):
        if abs(r) >= 2 ** 53:
            return float(r)
        if r == 0 and isinstance(x, str) and x.strip(_JS_WS)[:1] == '-':
            return -0.0
    return r


_JS_WS = ('\t\n\x0b\x0c\r \xa0            '
          '    　﻿')


def _rel(a, b):
    pa, pb = to_prim(a), to_prim(b)
    if isinstance(pa, str) and isinstance(pb, str):
        return _utf16_key(pa) < _utf16_key(pb)
    na, nb = to_num(pa), to_num(pb)
    if is_nan(na) or is_nan(nb):
        return None
    return na < nb


def js_lt(a, b):
    return _rel(a, b) is True


def js_ge(a, b):
    r = _rel(a, b)
    return r is False


def js_gt(a, b):
    return _rel(b, a) is True


def js_le(a, b):
    r = _rel(b, a)
    return r is False


def js_eq(a, b):
    if is_num(a) and is_num(b):
        return a == b
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    if a is None or a is UNDEF or b is None or b is UNDEF:
        return a is b
    if isinstance(a, (list, dict)) or isinstance(b, (list, dict)):
        return a is b
    return False


def js_abs(x):
    return abs(to_num(x))


def jfloor(x):
    x = to_num(x)
    if is_nan(x) or (isinstance(x, float) and math.isinf(x)):
        return x
    return math.floor(x)


def jceil(x):
    x = to_num(x)
    if is_nan(x) or (isinstance(x, float) and math.isinf(x)):
        return x
    return math.ceil(x)


def utf16len(s):
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in s)


def u16slice(s, a, z=None):
    b = s.encode('utf-16-le', 'surrogatepass')
    n = len(b) // 2
    a = max(0, n + a) if a < 0 else min(a, n)
    z = n if z is None else (max(0, n + z) if z < 0 else min(z, n))
    if z <= a:
        return ''
    return b[2 * a:2 * z].decode('utf-16-le', 'surrogatepass')


_IDX = re.compile(r'^(?:0|[1-9][0-9]*)\Z')


class JSFunc(object):

    def __init__(self, name):
        self.name = name

    def __str__(self):
        return 'function %s() { [native code] }' % self.name


_OBJ_PROTO = ('constructor', '__defineGetter__', '__defineSetter__', 'hasOwnProperty', '__lookupGetter__',
              '__lookupSetter__', 'isPrototypeOf', 'propertyIsEnumerable', 'toString', 'valueOf', 'toLocaleString')
_PROTO = {
    'object': ('Object', _OBJ_PROTO),
    'string': ('String', _OBJ_PROTO + (
        'anchor', 'at', 'big', 'blink', 'bold', 'charAt', 'charCodeAt', 'codePointAt', 'concat', 'endsWith',
        'fontcolor', 'fontsize', 'fixed', 'includes', 'indexOf', 'italics', 'lastIndexOf', 'link', 'localeCompare',
        'match', 'matchAll', 'normalize', 'padEnd', 'padStart', 'repeat', 'replace', 'replaceAll', 'search', 'slice',
        'small', 'split', 'strike', 'sub', 'substr', 'substring', 'sup', 'startsWith', 'toLowerCase', 'toUpperCase',
        'toLocaleLowerCase', 'toLocaleUpperCase', 'trim', 'trimStart', 'trimLeft', 'trimEnd', 'trimRight')),
    'array': ('Array', _OBJ_PROTO + (
        'at', 'concat', 'copyWithin', 'fill', 'find', 'findIndex', 'findLast', 'findLastIndex', 'lastIndexOf', 'pop',
        'push', 'reverse', 'shift', 'unshift', 'slice', 'sort', 'splice', 'includes', 'indexOf', 'join', 'keys',
        'entries', 'values', 'forEach', 'filter', 'flat', 'flatMap', 'map', 'every', 'some', 'reduce', 'reduceRight')),
    'number': ('Number', _OBJ_PROTO + ('toExponential', 'toFixed', 'toPrecision')),
    'boolean': ('Boolean', _OBJ_PROTO),
}


def _proto_get(kind, k):
    ctor, names = _PROTO[kind]
    if k in names:
        return JSFunc(ctor if k == 'constructor' else k)
    if k == '__proto__':
        return {} if kind == 'object' else UNDEF
    return UNDEF


def pkey(k):
    return k if isinstance(k, str) else jstr(k)


def G(o, k):
    if nn(o):
        raise type_error("Cannot read properties of %s (reading '%s')" % (jstr(o), pkey(k)))
    k = pkey(k)
    if isinstance(o, dict):
        return o[k] if k in o else _proto_get('object', k)
    if isinstance(o, list):
        if k == 'length':
            return len(o)
        if _IDX.match(k) and int(k) < len(o):
            return o[int(k)]
        return _proto_get('array', k)
    if isinstance(o, str):
        if k == 'length':
            return utf16len(o)
        if _IDX.match(k):
            u = o.encode('utf-16-le', 'surrogatepass')
            i = int(k)
            if i < len(u) // 2:
                return u[2 * i:2 * i + 2].decode('utf-16-le', 'surrogatepass')
        return _proto_get('string', k)
    if isinstance(o, bool):
        return _proto_get('boolean', k)
    if is_num(o):
        return _proto_get('number', k)
    return UNDEF


_ARRAY_METHODS = {'map', 'filter', 'find', 'some', 'every', 'forEach', 'reduce', 'slice', 'join',
                  'includes', 'indexOf', 'concat', 'flatMap', 'push', 'sort', 'entries'}
_STRING_METHODS = {'slice', 'includes', 'indexOf', 'split', 'replace', 'trim', 'match', 'toLowerCase',
                   'toUpperCase', 'charCodeAt', 'concat', 'substring'}


def need(o, method, expr, spread=False):
    if nn(o):
        raise type_error("Cannot read properties of %s (reading '%s')" % (jstr(o), method))
    if isinstance(o, list) and method in _ARRAY_METHODS:
        return o
    if isinstance(o, str) and method in _STRING_METHODS:
        return o
    raise type_error('%s is not a function%s' % (expr, ' or its return value is not iterable' if spread else ''))


def jiter(o, expr):
    if isinstance(o, list):
        return o
    if isinstance(o, str):
        return list(o)
    raise type_error('%s is not iterable' % expr)


def set_items(o):
    if nn(o):
        return []
    if isinstance(o, list):
        return o
    if isinstance(o, str):
        return list(o)
    if isinstance(o, dict):
        raise type_error('object is not iterable (cannot read property Symbol(Symbol.iterator))')
    raise type_error('%s %s is not iterable (cannot read property Symbol(Symbol.iterator))'
                     % ('boolean' if isinstance(o, bool) else 'number', jstr(o)))


def _skey(x):
    if is_num(x):
        return ('n', 'nan') if is_nan(x) else ('n', float(x) if abs(x) < 2 ** 63 else x)
    if isinstance(x, bool):
        return ('b', x)
    if isinstance(x, str):
        return ('s', x)
    if x is None:
        return ('null',)
    if x is UNDEF:
        return ('u',)
    return ('o', id(x))


class JSSet(object):

    def __init__(self, items=()):
        self._d = {}
        for x in items:
            self.add(x)

    def add(self, x):
        k = _skey(x)
        if k not in self._d:
            self._d[k] = x

    def has(self, x):
        return _skey(x) in self._d

    def values(self):
        return list(self._d.values())

    def __len__(self):
        return len(self._d)


def js_sort_strings(a):
    return sorted(a, key=_utf16_key)


def read_json_module(path, stack):
    if not os.path.isfile(path):
        raise JSError('Error', "Cannot find module '%s'\nRequire stack:\n%s"
                      % (path, '\n'.join('- ' + s for s in stack)))
    with open(path, 'rb') as f:
        text = f.read().decode('utf-8', 'replace')
    if text[:1] == '﻿':
        text = text[1:]
    try:
        return json.loads(text, parse_constant=_bad_const, parse_int=_pint)
    except (ValueError, RecursionError):
        try:
            _js_json_error(text)
            msg = 'Unexpected end of JSON input'
        except _JSErr as e:
            msg = str(e)
        raise JSError('SyntaxError', '%s: %s' % (path, msg))


C = UNDEF
N = UNDEF


def load(cwd=None, stack=None):
    global C, N
    cwd = os.getcwd() if cwd is None else cwd
    stack = stack or [os.path.join(HERE, 'fmt.py')]
    C = read_json_module(os.path.join(cwd, 'packet_computed.json'), stack)
    N = read_json_module(os.path.join(cwd, 'packet_narrative.json'), stack)
    return C, N


PRINTED = JSSet()
_DIGIT = re.compile('[0-9]')


def printed():
    return js_sort_strings(PRINTED.values())


def record(s):
    if isinstance(s, str) and _DIGIT.search(s):
        PRINTED.add(s)
    return s


MINUS = '–'


def _group(intpart):
    out = []
    while len(intpart) > 3:
        out.insert(0, intpart[-3:])
        intpart = intpart[:-3]
    out.insert(0, intpart)
    return ','.join(out)


def _locale_number(v, d):
    v = float(v)
    if v != v:
        return 'NaN'
    if math.isinf(v):
        return '∞' if v > 0 else '-∞'
    with localcontext() as ctx:
        ctx.prec = 1200
        q = Decimal(repr(v)).quantize(Decimal(1).scaleb(-d), rounding=ROUND_HALF_UP)
        s = format(q, 'f')
    neg = s.startswith('-')
    if neg:
        s = s[1:]
    ip, _, fp = s.partition('.')
    return ('-' if neg else '') + _group(ip) + ('.' + fp if fp else '')


def to_locale(v, d):
    if is_num(v):
        return _locale_number(v, d)
    if isinstance(v, str):
        return v
    if v is True:
        return 'true'
    if v is False:
        return 'false'
    if isinstance(v, list):
        return ','.join('' if nn(e) else to_locale(e, d) for e in v)
    if isinstance(v, dict):
        return '[object Object]'
    if nn(v):
        raise type_error("Cannot read properties of %s (reading 'toLocaleString')" % jstr(v))
    return jstr(v)


def num(v, d=0):
    if v is None or v is UNDEF or is_nan(v):
        return '–'
    s = to_locale(v, d)
    return record(MINUS + s[1:] if s[:1] == '-' else s)


CCY = '$'


def setCcy(s):
    global CCY
    if jt(s):
        CCY = jstr(s)


def ccy():
    return CCY


SCALE = 'B'


def setScale(s):
    global SCALE
    if js_eq(s, 'B') or js_eq(s, 'M'):
        SCALE = s
    elif jt(s):
        raise JSError('Error', 'meta.money_scale must be "B" or "M", got ' + jstr(js_json_any(s)))


def js_json_any(v):
    if v is UNDEF:
        return UNDEF
    return js_json_wf(v)


_LONE = re.compile('[\ud800-\udfff]')


def js_json_wf(v, indent=None):
    return _LONE.sub(lambda m: '\\u%04x' % ord(m.group(0)), js_json(v, indent))


def scale():
    return SCALE


def scaleLabel():
    return CCY + ' in millions' if SCALE == 'M' else CCY + ' in billions'


def cell(v, fmt):
    return record(_cell(v, fmt))


def _cell(v, fmt):
    if v is None or v is UNDEF:
        return '–'
    f = fmt if isinstance(fmt, str) else None
    if f == '$M':
        b = v if SCALE == 'M' else to_num(v) / 1000
        d = 1 if SCALE == 'M' else 2
        u = 'M' if SCALE == 'M' else 'B'
        neg = js_lt(v, 0)
        return ('(' if neg else '') + CCY + num(js_abs(b), d) + u + (')' if neg else '')
    if f == '$':
        return CCY + num(v, 2)
    if f == '$3':
        return CCY + num(v, 3)
    if f == '%':
        return num(v, 1) + '%'
    if f == '%2':
        return num(v, 2) + '%'
    if f == 'x':
        return xfmt(v)
    if f == 'k':
        return num(to_num(v) * 1000, 0)
    if f == '#B':
        return num(v, 3) + 'B'
    return num(v, 1)


def xfmt(v):
    if v is None or v is UNDEF:
        return '–'
    return record(num(v, 2 if js_lt(js_abs(v), 5) else 1) + 'x')


def is_rate(fmt, name):
    f = (fmt if isinstance(fmt, str) else '').strip()
    if f in ('%', '%2'):
        return True
    if f in ('$M', '$', '$3', 'x', 'k', '#B'):
        return False
    n = (name if isinstance(name, str) else '').strip()
    return bool(re.search(r'\b(ratio|margin|yield|rate)\b\s*%?$|%$', n, re.I))


def gzero(v, isPct):
    if v is None or v is UNDEF:
        return False
    return to_locale(js_abs(v), 0 if jt(isPct) else 1) in ('0', '0.0')


def gcell(v, isPct):
    if v is None or v is UNDEF:
        return '–'
    if gzero(v, isPct):
        return record('0 bps' if jt(isPct) else '0.0%')
    s = '+' if js_ge(v, 0) else '–'
    a = js_abs(v)
    return record(s + num(a, 0) + ' bps' if jt(isPct) else s + num(a, 1) + '%')
