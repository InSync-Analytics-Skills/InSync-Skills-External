"""
jscompat.py - JavaScript's exact semantics for the handful of operations where Python differs.

The preview's engine was written in JavaScript. The Python port has to print the same digits,
sort in the same order and write the same JSON, so every operation below reproduces the
ECMAScript rule rather than Python's nearest equivalent:

  js_str(x)            String(x) / Number::toString - exponent only past 1e21 or below 1e-6
  js_json(obj, indent) JSON.stringify(obj, null, indent)
  js_to_fixed(x, d)    Number.prototype.toFixed - on the EXACT binary value, ties to the larger
  js_round(x)          Math.round - floor(x + 0.5), ties toward +infinity
  js_parse_float(s)    parseFloat - the longest numeric prefix, else NaN
  js_number(v)         Number(v) - "" -> 0, "12a" -> NaN, null -> 0, bool -> 0/1
  js_sort_default(a)   Array.prototype.sort() with no comparator - by UTF-16 string order
  js_cmp_sort(a, f)    Array.prototype.sort(f) - stable, comparator sign only

Nothing here is guessed: the internal edition's tests compare every function against real
Node on many thousands of values.
"""
import json
import math
import re
from decimal import Decimal, ROUND_HALF_UP
from functools import cmp_to_key

NaN = float("nan")


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def js_str(x):
    if x is None:
        return "null"
    if x is True:
        return "true"
    if x is False:
        return "false"
    if isinstance(x, str):
        return x
    if not _is_num(x):
        return str(x)
    if isinstance(x, int):
        return str(x) if abs(x) < 10 ** 21 else js_str(float(x))
    if math.isnan(x):
        return "NaN"
    if math.isinf(x):
        return "Infinity" if x > 0 else "-Infinity"
    if x == 0:
        return "0"
    sign = "-" if x < 0 else ""
    r = repr(abs(x))
    if "e" in r or "E" in r:
        mant, exp = r.lower().split("e")
        exp = int(exp)
    else:
        mant, exp = r, 0
    if "." in mant:
        ip, fp = mant.split(".")
    else:
        ip, fp = mant, ""
    digits = (ip + fp).lstrip("0")
    lead_zeros = len(ip + fp) - len((ip + fp).lstrip("0"))
    n = len(ip) + exp - lead_zeros
    digits = digits.rstrip("0") or "0"
    k = len(digits)
    if k <= n <= 21:
        return sign + digits + "0" * (n - k)
    if 0 < n <= 21:
        return sign + digits[:n] + "." + digits[n:]
    if -6 < n <= 0:
        return sign + "0." + "0" * (-n) + digits
    e = n - 1
    es = ("+" if e >= 0 else "-") + str(abs(e))
    if k == 1:
        return sign + digits + "e" + es
    return sign + digits[0] + "." + digits[1:] + "e" + es


_INDEX = re.compile(r"^(?:0|[1-9][0-9]*)$")


def _is_index(k):
    return isinstance(k, str) and bool(_INDEX.match(k)) and int(k) <= 4294967294


def js_keys(d):
    ks = list(d.keys())
    idx = sorted((k for k in ks if _is_index(k)), key=int)
    return idx + [k for k in ks if not _is_index(k)]


def js_stdio():
    import sys
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", newline="\n")
        except (AttributeError, ValueError):
            pass


def js_write(path, text):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def _json_str(s):
    return json.dumps(s, ensure_ascii=False)


def js_json(obj, indent=None):
    pad = (" " * indent) if isinstance(indent, int) else (indent or "")

    def enc(v, cur):
        if v is None:
            return "null"
        if v is True:
            return "true"
        if v is False:
            return "false"
        if _is_num(v):
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                return "null"
            return js_str(v)
        if isinstance(v, str):
            return _json_str(v)
        if isinstance(v, (list, tuple)):
            if not v:
                return "[]"
            items = ["null" if x is _UNDEF else enc(x, cur + pad) for x in v]
            if not pad:
                return "[" + ",".join(items) + "]"
            inner = cur + pad
            return "[\n" + inner + (",\n" + inner).join(items) + "\n" + cur + "]"
        if isinstance(v, dict):
            parts = []
            for k in js_keys(v):
                x = v[k]
                if x is _UNDEF:
                    continue
                parts.append(_json_str(str(k)) + (": " if pad else ":") + enc(x, cur + pad))
            if not parts:
                return "{}"
            if not pad:
                return "{" + ",".join(parts) + "}"
            inner = cur + pad
            return "{\n" + inner + (",\n" + inner).join(parts) + "\n" + cur + "}"
        return _json_str(str(v))

    return enc(obj, "")


class _Undefined:
    def __repr__(self):
        return "undefined"

    def __bool__(self):
        return False


_UNDEF = UNDEFINED = _Undefined()


def js_to_fixed(x, d=0):
    if not _is_num(x):
        x = js_number(x)
    x = float(x)
    if math.isnan(x):
        return "NaN"
    if abs(x) >= 1e21:
        return js_str(x)
    neg = x < 0
    q = Decimal(abs(x)).quantize(Decimal(1).scaleb(-d), rounding=ROUND_HALF_UP)
    s = format(q, "f")
    return "-" + s if neg else s


def js_round(x):
    x = float(x)
    if math.isnan(x) or math.isinf(x):
        return x
    f = math.floor(x)
    r = f + 1 if x - f >= 0.5 else f
    return int(r) if abs(r) < 2 ** 53 else float(r)


_FLOAT_PREFIX = re.compile(r"^[\t\n\v\f\r    -     　﻿]*"
                           r"([+-]?(?:Infinity|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?))")


def js_parse_float(s):
    if _is_num(s):
        return float(s)
    m = _FLOAT_PREFIX.match(js_str(s) if not isinstance(s, str) else s)
    if not m:
        return NaN
    t = m.group(1)
    if t.lstrip("+-") == "Infinity":
        return float("-inf") if t.startswith("-") else float("inf")
    return float(t)


_WS = "\t\n\v\f\r                  　﻿"


def js_number(v):
    if v is None:
        return 0
    if v is _UNDEF:
        return NaN
    if v is True:
        return 1
    if v is False:
        return 0
    if _is_num(v):
        return v
    if isinstance(v, str):
        t = v.strip(_WS)
        if t == "":
            return 0
        if re.fullmatch(r"[+-]?Infinity", t):
            return float("-inf") if t.startswith("-") else float("inf")
        if re.fullmatch(r"0[xX][0-9a-fA-F]+", t):
            return int(t, 16)
        if re.fullmatch(r"0[oO][0-7]+", t):
            return int(t, 8)
        if re.fullmatch(r"0[bB][01]+", t):
            return int(t, 2)
        if re.fullmatch(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?", t):
            f = float(t)
            return int(f) if f.is_integer() and abs(f) < 2 ** 53 and not re.search(r"[.eE]", t) else f
        return NaN
    if isinstance(v, list):
        return js_number(",".join("" if x is None else js_str(x) for x in v)) if len(v) <= 1 else NaN
    return NaN


def _utf16_key(s):
    return [c for ch in s for c in ((ord(ch),) if ord(ch) < 0x10000 else
                                    (0xD800 + ((ord(ch) - 0x10000) >> 10), 0xDC00 + ((ord(ch) - 0x10000) & 0x3FF)))]


def js_sort_default(a):
    und = [x for x in a if x is _UNDEF]
    rest = [x for x in a if x is not _UNDEF]
    rest.sort(key=lambda x: _utf16_key(js_str(x)))
    return rest + und


def js_cmp_sort(a, cmp):
    def c(x, y):
        r = cmp(x, y)
        try:
            r = float(r)
        except (TypeError, ValueError):
            return 0
        if math.isnan(r):
            return 0
        return -1 if r < 0 else (1 if r > 0 else 0)
    return sorted(a, key=cmp_to_key(c))


import os
import sys


class _JSErr(Exception):
    pass


def _bad_const(s):
    raise ValueError(s)


def _pint(s):
    v = int(s)
    if v == 0 and s.startswith("-"):
        return -0.0
    return v if abs(v) < 2 ** 53 else float(s)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _truthy(v):
    if v is None or v is UNDEFINED or v is False:
        return False
    if _is_num(v):
        return not (v == 0 or (isinstance(v, float) and math.isnan(v)))
    if isinstance(v, str):
        return v != ""
    return True


def _js_json_error(t):
    n = len(t)

    def fail(p):
        if p >= n:
            raise _JSErr("Unexpected end of JSON input")
        c = t[p]
        pos = p + sum(1 for ch in t[:p] if ord(ch) > 0xFFFF)
        if c == '"':
            raise _JSErr("Unexpected string in JSON at position %d" % pos)
        if c == "-" or "0" <= c <= "9":
            raise _JSErr("Unexpected number in JSON at position %d" % pos)
        raise _JSErr("Unexpected token %s in JSON at position %d" % ("�" if ord(c) > 0xFFFF else c, pos))

    def ws(i):
        while i < n and t[i] in " \t\n\r":
            i += 1
        return i

    def digits(i):
        while i < n and "0" <= t[i] <= "9":
            i += 1
        return i

    def string(i):
        i += 1
        while True:
            if i >= n:
                fail(i)
            c = t[i]
            if c == '"':
                return i + 1
            if c == "\\":
                i += 1
                if i >= n:
                    fail(i)
                if t[i] in '"\\/bfnrt':
                    i += 1
                elif t[i] == "u":
                    for j in range(i + 1, i + 5):
                        if j >= n or t[j] not in "0123456789abcdefABCDEF":
                            fail(j)
                    i += 5
                else:
                    fail(i)
            elif ord(c) < 0x20:
                fail(i)
            else:
                i += 1

    def value(i):
        i = ws(i)
        if i >= n:
            fail(i)
        c = t[i]
        if c == "{":
            i = ws(i + 1)
            if i < n and t[i] == "}":
                return i + 1
            while True:
                if i >= n or t[i] != '"':
                    fail(i)
                i = ws(string(i))
                if i >= n or t[i] != ":":
                    fail(i)
                i = ws(value(i + 1))
                if i < n and t[i] == ",":
                    i = ws(i + 1)
                    continue
                if i < n and t[i] == "}":
                    return i + 1
                fail(i)
        if c == "[":
            i = ws(i + 1)
            if i < n and t[i] == "]":
                return i + 1
            while True:
                i = ws(value(i))
                if i < n and t[i] == ",":
                    i += 1
                    continue
                if i < n and t[i] == "]":
                    return i + 1
                fail(i)
        if c == '"':
            return string(i)
        if c == "-" or "0" <= c <= "9":
            if c == "-":
                i += 1
            if i >= n:
                fail(i)
            if t[i] == "0":
                i += 1
                if i < n and "0" <= t[i] <= "9":
                    fail(i)
            elif "0" <= t[i] <= "9":
                i = digits(i)
            else:
                fail(i)
            if i < n and t[i] == ".":
                i += 1
                if i >= n or not ("0" <= t[i] <= "9"):
                    fail(i)
                i = digits(i)
            if i < n and t[i] in "eE":
                i += 1
                if i < n and t[i] in "+-":
                    i += 1
                if i >= n or not ("0" <= t[i] <= "9"):
                    fail(i)
                i = digits(i)
            return i
        for lit in ("true", "false", "null"):
            if c == lit[0]:
                for j, ch in enumerate(lit):
                    if i + j >= n or t[i + j] != ch:
                        fail(i + j)
                return i + len(lit)
        fail(i)

    i = ws(value(0))
    if i < n:
        fail(i)


def _js_read_json(path):
    if not os.path.exists(path):
        raise _JSErr("ENOENT: no such file or directory, open '%s'" % path)
    if os.path.isdir(path):
        raise _JSErr("EISDIR: illegal operation on a directory, read")
    try:
        with open(path, "rb") as f:
            text = f.read().decode("utf-8", "replace")
    except PermissionError:
        raise _JSErr("EACCES: permission denied, open '%s'" % path)
    try:
        return json.loads(text, parse_constant=_bad_const, parse_int=_pint)
    except (ValueError, RecursionError):
        _js_json_error(text)
        raise _JSErr("Unexpected end of JSON input")


def _exit(code):
    sys.stdout.flush()
    sys.stderr.flush()
    sys.exit(code)


def _out(s):
    sys.stdout.write(s + "\n")
