"""
sanity_check.py - invariant checks on a BUILT revision workbook and its contract.

  python references/sanity_check.py <dir>                 # dir holds contract.json + *.xlsx
  python references/sanity_check.py <contract.json> <file.xlsx>

Standard library only. Prints FAIL / WARN / INFO lines and exits 1 on any FAIL.
Covers: dates and vintages, anchors, labels, stale flags, segment roll-up, twin
identities, lead-flavour margins / bridge / curve / Consensus Embeds levels, eligibility,
divergence wording, the rendered table (headers, date row, Current ties, growth cells)
and panel artefacts (rendering glitches, double-signed amounts, path wording, mix order).
FAIL = a rule of the skill is broken on this workbook (must be investigated).
WARN = unusual but legitimate on some names (investigate, may be ticker noise).
The checks are general: they encode the rules in SKILL.md, never a ticker exception.

Ported from an earlier JavaScript version (internal edition only). The contract is read with
JavaScript's rules (a missing key is `undefined`, `null` arithmetic is 0, `&&` / `||` return
an operand, strings compare in UTF-16 order), so a damaged contract gives the same verdict
as the original.
"""
import math
import os
import re
import sys
import zlib
from functools import lru_cache, reduce

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

from jscompat import (UNDEFINED as U, js_stdio, js_str, js_number, js_to_fixed,
                      js_parse_float, js_cmp_sort, _js_read_json, _truthy, _utf16_key)
import revision_common
from revision_common import money_fmt

VERSION = '2.5.0'


def _isnum(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def nul(v):
    return v is None or v is U


def truthy(v):
    return _truthy(v)


def A(a, f):
    return f() if _truthy(a) else a


def O(a, f):
    if _truthy(a):
        return a
    return f() if callable(f) else f


def S(v):
    if v is U:
        return "undefined"
    if isinstance(v, list):
        return ",".join("" if nul(x) else S(x) for x in v)
    if isinstance(v, dict):
        return "[object Object]"
    return js_str(v)


def prim(v):
    if isinstance(v, (list, dict)):
        return S(v)
    return v


def N(v):
    v = prim(v)
    r = js_number(v)
    return float(r)


def jeq(a, b):
    if _isnum(a) and _isnum(b):
        return float(a) == float(b)
    if _isnum(a) or _isnum(b):
        return False
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    return a is b


def svz(a, b):
    if _isnum(a) and _isnum(b) and math.isnan(float(a)) and math.isnan(float(b)):
        return True
    return jeq(a, b)


def includes(arr, v):
    return any(svz(x, v) for x in arr)


def jlt(a, b):
    a, b = prim(a), prim(b)
    if isinstance(a, str) and isinstance(b, str):
        return _utf16_key(a) < _utf16_key(b)
    return N(a) < N(b)


def jle(a, b):
    a, b = prim(a), prim(b)
    if isinstance(a, str) and isinstance(b, str):
        return _utf16_key(a) <= _utf16_key(b)
    return N(a) <= N(b)


def jgt(a, b):
    return jlt(b, a)


def jge(a, b):
    return jle(b, a)


def jadd(a, b):
    pa, pb = prim(a), prim(b)
    if isinstance(pa, str) or isinstance(pb, str):
        return S(pa) + S(pb)
    return N(pa) + N(pb)


def jsub(a, b):
    return N(a) - N(b)


def jmul(a, b):
    return N(a) * N(b)


def jdiv(a, b):
    x, y = N(a), N(b)
    if y == 0:
        if x == 0 or math.isnan(x):
            return float("nan")
        return math.copysign(1.0, x) * math.copysign(1.0, y) * float("inf")
    return x / y


def jabs(v):
    return abs(N(v))


def jsign(v):
    x = N(v)
    if math.isnan(x) or x == 0:
        return x
    return 1.0 if x > 0 else -1.0


def jmax(vals):
    r = float("-inf")
    for v in vals:
        x = N(v)
        if math.isnan(x):
            return x
        if x > r:
            r = x
    return r


def jmin(*vals):
    r = float("inf")
    for v in vals:
        x = N(v)
        if math.isnan(x):
            return x
        if x < r:
            r = x
    return r


def jceil(v):
    x = N(v)
    if math.isnan(x) or math.isinf(x):
        return x
    return float(math.ceil(x))


def tofixed(v, d):
    if not _isnum(v):
        raise TypeError("%s.toFixed is not a function" % S(v))
    return js_to_fixed(v, d)


def _u16(s):
    return s.encode("utf-16-le", "surrogatepass")


def u16len(s):
    return len(_u16(s)) // 2


def u16slice(s, a, b):
    return _u16(s)[2 * a:2 * b].decode("utf-16-le", "surrogatepass")


def _idx(k):
    if _isnum(k):
        if isinstance(k, float) and not k.is_integer():
            return None
        k = int(k)
        return k if k >= 0 else None
    if isinstance(k, str) and re.fullmatch(r"(?:0|[1-9][0-9]*)", k):
        return int(k)
    return None


def get(o, k):
    if o is None or o is U:
        raise TypeError("Cannot read properties of %s (reading '%s')" % ("null" if o is None else "undefined", S(k)))
    if isinstance(o, dict):
        return o.get(k if isinstance(k, str) else S(k), U)
    if isinstance(o, list):
        if k == "length":
            return len(o)
        i = _idx(k)
        return o[i] if i is not None and i < len(o) else U
    if isinstance(o, str):
        if k == "length":
            return u16len(o)
        i = _idx(k)
        if i is not None and i < u16len(o):
            return u16slice(o, i, i + 1)
        return U
    return U


def it_(x):
    if isinstance(x, list):
        return list(x)
    if isinstance(x, str):
        return list(x)
    raise TypeError("%s is not iterable" % S(x))


def first(x):
    if isinstance(x, list):
        return x[0] if x else U
    if isinstance(x, str):
        return x[0] if x else U
    raise TypeError("%s is not iterable" % S(x))


def arr(x):
    if isinstance(x, list):
        return x
    raise TypeError("array method on %s" % S(x))


def jstr(x):
    if isinstance(x, str):
        return x
    raise TypeError("string method on %s" % S(x))


def jin(k, o):
    if isinstance(o, dict):
        return k in o
    if isinstance(o, list):
        i = _idx(k)
        return k == "length" or (i is not None and i < len(o))
    raise TypeError("Cannot use 'in' operator to search for '%s' in %s" % (k, S(o)))


def concat(*xs):
    out = []
    for x in xs:
        if isinstance(x, list):
            out.extend(x)
        else:
            out.append(x)
    return out


_JS_WS = "\t\n\x0b\x0c\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
_WB = r"(?:(?<=[A-Za-z0-9_])(?![A-Za-z0-9_])|(?<![A-Za-z0-9_])(?=[A-Za-z0-9_]))"
_LT = "\n\r\u2028\u2029"


@lru_cache(maxsize=None)
def R(src, flags=""):
    out = []
    i, n, cls = 0, len(src), False
    while i < n:
        c = src[i]
        if c == "\\" and i + 1 < n:
            e = src[i + 1]
            if e == "d":
                out.append("0-9" if cls else "[0-9]")
            elif e == "w":
                out.append("A-Za-z0-9_" if cls else "[A-Za-z0-9_]")
            elif e == "s":
                out.append(_JS_WS if cls else "[" + _JS_WS + "]")
            elif e == "S":
                out.append("\\S\x1c-\x1f\x85" if cls else "[^" + _JS_WS + "]")
            elif e == "D":
                out.append("[^0-9]")
            elif e == "W":
                out.append("[^A-Za-z0-9_]")
            elif e == "b" and not cls:
                out.append(_WB)
            else:
                out.append(c + e)
            i += 2
            continue
        if cls:
            if c == "]":
                cls = False
                out.append(c)
            elif c in "[&~|":
                out.append("\\" + c)
            else:
                out.append(c)
        elif c == "[":
            cls = True
            out.append(c)
            if i + 1 < n and src[i + 1] == "^":
                out.append("^")
                i += 1
        elif c == ".":
            out.append("(?s:.)" if "s" in flags else "[^" + _LT + "]")
        elif c == "$":
            out.append("(?=[" + _LT + "]|\\Z)" if "m" in flags else "\\Z")
        elif c == "^":
            out.append("(?:(?<=[" + _LT + "])|\\A)" if "m" in flags else "\\A")
        else:
            out.append(c)
        i += 1
    return re.compile("".join(out), re.I if "i" in flags else 0)


def test(src, s, flags=""):
    return R(src, flags).search(S(s)) is not None


def grp(m, i):
    if m is None:
        return U
    g = m.group(i)
    return U if g is None else g


_TRIM = "\t\n\x0b\x0c\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"


def jtrim(s):
    return s.strip(_TRIM)


def rx_esc(s):
    return re.sub(r"[.*+?^${}()|\[\]\\]", lambda m: "\\" + m.group(0), S(s))


def pj(*parts):
    return os.path.normpath(os.path.join(*parts))


_SUR = re.compile("[\ud800-\udfff]")


def emit(s):
    sys.stdout.write(_SUR.sub("\ufffd", s) + "\n")


class RangeErr(Exception):
    pass


def _u16le(buf, off):
    if off < 0 or off + 2 > len(buf):
        raise RangeErr('The value of "offset" is out of range.')
    return buf[off] | (buf[off + 1] << 8)


def _u32le(buf, off):
    if off < 0 or off + 4 > len(buf):
        raise RangeErr('The value of "offset" is out of range.')
    return int.from_bytes(buf[off:off + 4], "little")


def read_zip_entry(buf, name):
    eocd = buf.rfind(b"\x50\x4b\x05\x06")
    cd_off, n = _u32le(buf, eocd + 16), _u16le(buf, eocd + 10)
    p = cd_off
    for _ in range(n):
        nl, el, cl = _u16le(buf, p + 28), _u16le(buf, p + 30), _u16le(buf, p + 32)
        nm = buf[p + 46:p + 46 + nl].decode("utf-8", "replace")
        lho, method, csz = _u32le(buf, p + 42), _u16le(buf, p + 10), _u32le(buf, p + 20)
        if nm == name:
            lnl, lel = _u16le(buf, lho + 26), _u16le(buf, lho + 28)
            start = lho + 30 + lnl + lel
            data = buf[start:start + csz]
            if method == 8:
                d = zlib.decompressobj(-15)
                raw = d.decompress(data)
                if not d.eof:
                    raise zlib.error("unexpected end of file")
                return raw.decode("utf-8", "replace")
            return data.decode("utf-8", "replace")
        p += 46 + nl + el + cl
    return None


def _read(p):
    with open(p, "rb") as f:
        return f.read()


def sheet_cells(xlsx):
    xml = read_zip_entry(_read(xlsx), "xl/worksheets/sheet1.xml")
    if xml is None:
        raise TypeError("Cannot read properties of null (reading 'matchAll')")
    cells = {}
    for m in R(r'<c r="([A-Z]+)(\d+)"([^>]*?)(?:\/>|>(.*?)<\/c>)', "gs").finditer(xml):
        inner = m.group(4) or ""
        t = R(r"<[^>]+>", "g").sub("", inner)
        f = grp(R(r"<f>(.*?)<\/f>").search(inner), 1)
        v = grp(R(r"<v>(.*?)<\/v>").search(inner), 1)
        is_str = R(r't="(inlineStr|str|s)"').search(m.group(3)) is not None
        cells[m.group(1) + m.group(2)] = {
            "t": t.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"), "f": f,
            "v": None if is_str else (float(js_number(v)) if not nul(v) else None),
            "col": m.group(1), "row": float(js_number(m.group(2)))}
    return cells, xml


def col_idx(s):
    n = 0.0
    for ch in s:
        n = n * 26 + (ord(ch) - 64)
    return n


def main():
    js_stdio()
    args = sys.argv[1:]
    if not args:
        sys.stderr.write("usage: python sanity_check.py <dir> | <contract.json> <file.xlsx>\n")
        sys.stderr.flush()
        sys.exit(2)
    if len(args) == 1:
        d = args[0]
        contract_path = pj(d, "contract.json")
        x = [f for f in os.listdir(d) if re.search(r"\.xlsx\Z", f, re.I)]
        x = sorted(x, key=lambda f: -os.stat(pj(d, f)).st_mtime_ns)
        xlsx_path = pj(d, x[0]) if x else None
    else:
        contract_path, xlsx_path = args[0], args[1]
    C = _js_read_json(contract_path)
    out = []
    cnt = {"fails": 0, "warns": 0}

    def FAIL(m):
        cnt["fails"] += 1
        out.append("FAIL  " + m)

    def WARN(m):
        cnt["warns"] += 1
        out.append("WARN  " + m)

    def INFO(m):
        out.append("INFO  " + m)

    cur = O(A(get(C, "meta"), lambda: get(get(C, "meta"), "currency")), "$")
    MF = money_fmt(cur if isinstance(cur, str) else S(cur))
    SYMRX = rx_esc(MF.sym)

    def fBn(v):
        x = N(v)
        if math.isnan(x):
            return MF.sym + "NaNm"
        return MF.level(x)

    root = os.path.join(HERE, "..")
    stamps = {"references/sanity_check.py": VERSION,
              "contract.meta.version": A(get(C, "meta"), lambda: get(get(C, "meta"), "version"))}
    sk = os.path.join(root, "SKILL.md")
    if os.path.exists(sk):
        txt = _read(sk).decode("utf-8", "replace").replace("\r\n", "\n")
        fm = O(grp(R(r"^---\n([\s\S]*?)\n---").search(txt), 1), "")
        stamps["SKILL.md metadata.version"] = grp(R(r"""^\s+version:\s*["']?([^\s"']+)""", "m").search(fm), 1)
    for f in ["prepare_revisions.py", "build_revision_xlsx.py", "ingest_raw.py", "revision_common.py"]:
        p = os.path.join(root, "scripts", f)
        if os.path.exists(p):
            src = _read(p).decode("utf-8", "replace")
            m = re.search(r"""^VERSION\s*=\s*['"]([^'"]+)['"]""", src, re.M)
            if m:
                stamps["scripts/" + f] = m.group(1)
            elif re.search(r"^from revision_common import[^\n]*\bVERSION\b", src, re.M):
                stamps["scripts/" + f] = revision_common.VERSION
            else:
                stamps["scripts/" + f] = U
    vals = []
    for v in stamps.values():
        if not includes(vals, v):
            vals.append(v)
    if len(vals) != 1 or includes(vals, U):
        FAIL("version stamps disagree: " + ", ".join("%s=%s" % (k, S(O(v, "MISSING"))) for k, v in stamps.items()))
    else:
        INFO("version " + VERSION + " on every stamp")

    M = get(C, "meta")
    I = O(get(C, "intel"), lambda: {})
    items = []
    for s in it_(get(C, "sections")):
        for it in it_(get(s, "items")):
            o = {"sec": get(s, "key")}
            if isinstance(it, dict):
                o.update(it)
            elif isinstance(it, list):
                for i, v in enumerate(it):
                    o[str(i)] = v
            elif isinstance(it, str):
                for i in range(u16len(it)):
                    o[str(i)] = u16slice(it, i, i + 1)
            items.append(o)
    byLt = {}
    for it in items:
        byLt[S(get(it, "lt"))] = it

    def lead(*lts):
        any_ = None
        for lt in lts:
            it = byLt.get(lt, U)
            if not truthy(it):
                continue
            if not truthy(get(it, "gaapTwin")):
                return it
            if not truthy(any_):
                any_ = it
        return any_

    def near(a, b, tol):
        return (not nul(a)) and (not nul(b)) and jabs(jsub(a, b)) <= tol * jmax([1, jabs(b)])

    def per(it, pk):
        return get(get(it, "per"), pk)

    periods = get(C, "periods")
    INFO("%s %s/FY%s print %s prepared %s flow %s rows %d state %s" % (
        S(get(M, "ticker")), S(get(M, "q")), S(get(M, "fy1")), S(get(M, "printDate")), S(get(M, "prepared")),
        S(get(M, "isFlow")), len(items), S(A(get(I, "dataState"), lambda: get(get(I, "dataState"), "key")))))

    if not jle(get(M, "printDate"), get(M, "prepared")):
        FAIL("printDate %s after prepared %s" % (S(get(M, "printDate")), S(get(M, "prepared"))))
    for pr in it_(periods):
        pk = first(pr)
        v = get(O(get(C, "vintages"), lambda: {}), pk)
        if not truthy(v):
            continue
        if truthy(get(v, "pre")) and jge(get(v, "pre"), get(M, "printDate")):
            FAIL("%s: pre-print vintage %s on/after the print" % (S(pk), S(get(v, "pre"))))
        if truthy(get(v, "cur")) and jgt(get(v, "cur"), get(M, "prepared")):
            FAIL("%s: current vintage %s after the run date" % (S(pk), S(get(v, "cur"))))
        seq = [x for x in (get(v, k) for k in ["pre", "t10", "t40", "cur"]) if truthy(x)]
        for i in range(1, len(seq)):
            if jlt(seq[i], seq[i - 1]):
                FAIL("%s: vintages out of order %s" % (S(pk), " → ".join(S(x) for x in seq)))
    for k in it_(O(get(C, "anchors"), lambda: [])):
        for pr in it_(periods):
            pk = first(pr)
            av = A(get(C, "anchorView"), lambda: A(get(get(C, "anchorView"), pk), lambda: get(get(get(C, "anchorView"), pk), k)))
            if not truthy(av):
                continue
            if not jeq(k, "pre") and jeq(get(av, "distinct"), False) and not truthy(get(av, "sameAs")):
                FAIL("%s: collapsed anchor %s without a sameAs marker" % (S(pk), S(k)))
    labels = {}
    EXPECT_FAV = {"IS_P_101": "up", "IS_P_102": "up", "IS_P_103": "down", "IS_P_104": "down", "IS_P_105": "up", "IS_P_106": "up", "IS_P_107": "down", "IS_P_108": "down",
                  "IS_P_109": "up", "IS_P_110": "up", "IS_P_113": "up", "IS_P_114": "up", "IS_P_117": "up", "IS_P_118": "up", "IS_P_119": "down", "IS_P_120": "down", "IS_P_121": "down", "IS_P_122": "down",
                  "IS_P_123": "up", "IS_P_124": "up", "IS_P_125": "down", "IS_P_126": "down", "IS_P_127": "up", "IS_P_128": "up", "IS_S_656": "up", "IS_S_657": "up",
                  "CF_P_108": "up", "CF_P_110": "up", "BS_P_114": "down", "BS_P_115": "down", "BS_P_108": "down", "BS_P_109": "down"}
    for it in items:
        want = EXPECT_FAV.get(S(get(it, "lt")), U)
        if jeq(get(it, "lt"), "CF_P_109"):
            p = O(get(get(it, "per"), "q"), lambda: get(get(it, "per"), "fy1"))
            if truthy(p) and not nul(get(p, "cur")):
                want = "up" if jlt(get(p, "cur"), 0) else "down"
        if jeq(get(it, "sec"), "seg"):
            want = "up"
        if truthy(want) and not jeq(get(it, "fav"), want):
            FAIL("%s (%s): direction %s but a rise in this line is %s news" % (
                S(get(it, "label")), S(get(it, "lt")), S(O(get(it, "fav"), "unknown")), "good" if want == "up" else "bad"))
    for it in items:
        lab = get(it, "label")
        if not jin("fav", it):
            FAIL("%s: no polarity (fav) on the row" % S(lab))
        if test("…", lab):
            FAIL("%s: truncated label in the contract" % S(lab))
        if test(r"\*\s*$", lab):
            FAIL("%s: star marker in the label" % S(lab))
        if jgt(get(lab, "length"), 44):
            FAIL("%s: label longer than the 40-width cap (%s chars)" % (S(lab), S(get(lab, "length"))))
        if test(r"\s{2,}", lab):
            WARN("%s: double space in label" % S(lab))
        if jeq(get(it, "sec"), "seg") and test(r"(\s-\s|\s)(Adjusted\s+)?Revenues?$", lab, "i"):
            WARN("%s: segment label keeps a metric suffix" % S(lab))
        key = jadd(jadd(get(it, "sec"), "|"), lab)
        if truthy(labels.get(key, U)):
            FAIL("duplicate label in %s: %s" % (S(get(it, "sec")), S(lab)))
        labels[key] = 1
        for pr in it_(periods):
            pk = first(pr)
            p = per(it, pk)
            if not truthy(p):
                continue
            g = lambda k: get(p, k)
            if nul(g("cur")):
                FAIL("%s %s: no current value" % (S(lab), S(pk)))
            if nul(g("cnt")):
                WARN("%s %s: no broker count" % (S(lab), S(pk)))
            if (not nul(g("low")) and not nul(g("high")) and not nul(g("cur"))
                    and not (jle(jsub(g("low"), 1e-6), g("cur")) and jle(g("cur"), jadd(g("high"), 1e-6)))):
                WARN("%s %s: current %s outside Low/High %s/%s" % (S(lab), S(pk), S(g("cur")), S(g("low")), S(g("high"))))
            if not nul(g("yoy")) and jabs(g("yoy")) > 10:
                WARN("%s %s: YoY %s%% looks like a unit or base problem" % (S(lab), S(pk), js_to_fixed(jmul(g("yoy"), 100), 0)))
            if not nul(g("seq")) and jabs(g("seq")) > 10:
                WARN("%s %s: Seq %s%% looks like a unit or base problem" % (S(lab), S(pk), js_to_fixed(jmul(g("seq"), 100), 0)))
            if truthy(g("stale")) and jge(g("curD"), get(M, "printDate")):
                FAIL("%s %s: flagged stale but refreshed %s" % (S(lab), S(pk), S(g("curD"))))
            if not truthy(g("stale")) and truthy(g("curD")) and jlt(g("curD"), get(M, "printDate")):
                FAIL("%s %s: refreshed %s before the print but not flagged stale" % (S(lab), S(pk), S(g("curD"))))
            if truthy(g("preD")) and jge(g("preD"), get(M, "printDate")):
                FAIL("%s %s: pre-print vintage %s on/after the print" % (S(lab), S(pk), S(g("preD"))))
    seg = [it for it in items if jeq(get(it, "sec"), "seg")]
    rev = lead("IS_P_101", "IS_P_102", "IS_P_200", "IS_P_403", "IS_P_402")
    if len(seg) and truthy(rev):
        for pr in it_(periods):
            pk = first(pr)
            r = per(rev, pk)
            if not truthy(r):
                continue
            for k in ["cur", "pre"]:
                vals = [A(per(it, pk), lambda: get(per(it, pk), k)) for it in seg]
                if any(nul(v) for v in vals):
                    continue
                s = reduce(jadd, vals, 0)
                t = get(r, k)
                if not truthy(t):
                    continue
                ratio = jdiv(s, t)
                if jgt(ratio, 1.02):
                    FAIL("segments %s/%s sum to %s%% of revenue (overshoot)" % (S(pk), k, js_to_fixed(ratio * 100, 1)))
                elif jlt(ratio, 0.90):
                    WARN("segments %s/%s cover only %s%% of revenue" % (S(pk), k, js_to_fixed(ratio * 100, 1)))
        if len(seg) == 1:
            WARN("a single segment row (a split needs two or more rows to say anything)")
    ni, eps, fds = lead("IS_P_124", "IS_S_656", "IS_P_123"), lead("IS_S_657", "IS_P_128", "IS_P_127"), lead("IS_P_126", "IS_P_125")
    if truthy(ni) and truthy(eps) and truthy(fds):
        for pr in it_(periods):
            pk = first(pr)
            a, b, c = per(ni, pk), per(eps, pk), per(fds, pk)
            if not truthy(a) or not truthy(b) or not truthy(c):
                continue
            if (not nul(get(a, "cur")) and not nul(get(b, "cur")) and not nul(get(c, "cur"))
                    and not near(jmul(get(b, "cur"), get(c, "cur")), get(a, "cur"), 0.04)):
                x1 = js_to_fixed(jmul(get(b, "cur"), get(c, "cur")), 0)
                WARN("%s: EPS × shares %s vs net income %s (lead flavour) differ by more than 4%%" % (S(pk), x1, tofixed(get(a, "cur"), 0)))
    gni, geps = byLt.get("IS_P_123", U), byLt.get("IS_P_127", U)
    if truthy(gni) and truthy(geps) and truthy(fds) and truthy(get(gni, "gaapTwin")) and truthy(get(geps, "gaapTwin")):
        a, b, c = per(gni, "q"), per(geps, "q"), per(fds, "q")
        if (truthy(a) and truthy(b) and truthy(c) and not nul(get(a, "cur")) and not nul(get(b, "cur"))
                and not near(jmul(get(b, "cur"), get(c, "cur")), get(a, "cur"), 0.04)):
            WARN("q: GAAP EPS × shares vs GAAP net income differ by more than 4%")

    def pq(x):
        return A(per(x, "q"), lambda: get(per(x, "q"), "cur"))
    rv, cogs, gp = lead("IS_P_101", "IS_P_102"), lead("IS_P_104", "IS_P_103"), lead("IS_P_106", "IS_P_105")
    if truthy(rv) and truthy(cogs) and truthy(gp):
        if (not nul(pq(rv)) and not nul(pq(cogs)) and not nul(pq(gp))
                and not near(jsub(pq(rv), pq(cogs)), pq(gp), 0.02)):
            x1 = js_to_fixed(jsub(pq(rv), pq(cogs)), 0)
            WARN("q: revenue − COGS = %s vs gross profit %s" % (x1, tofixed(pq(gp), 0)))
    ebit, pbt, nio = lead("IS_P_110", "IS_P_109"), lead("IS_P_118", "IS_P_117"), lead("IS_P_116", "IS_P_115")
    if truthy(ebit) and truthy(pbt) and truthy(nio):
        e, b, n = pq(ebit), pq(pbt), pq(nio)
        if not nul(e) and not nul(b) and not nul(n):
            as_inc, as_exp = jabs(jsub(jadd(e, n), b)), jabs(jsub(jsub(e, n), b))
            if jmin(as_inc, as_exp) > 0.03 * jabs(b):
                x1 = js_to_fixed(jmin(as_inc, as_exp), 0)
                WARN("q: EBIT ± net interest does not reach PBT (best gap %s on PBT %s)" % (x1, tofixed(b, 0)))
            expense = as_exp < as_inc
            if expense and jeq(get(nio, "fav"), "up"):
                FAIL("net interest line behaves as an EXPENSE in the identity but carries UP polarity")
            if not expense and jeq(get(nio, "fav"), "down") and as_inc < 0.5 * as_exp:
                FAIL("net interest line behaves as INCOME in the identity but carries DOWN polarity")
    for g_, a_ in [["IS_P_109", "IS_P_110"], ["IS_P_113", "IS_P_114"], ["IS_P_123", "IS_P_124"], ["IS_P_127", "IS_P_128"]]:
        G, Ax = byLt.get(g_, U), byLt.get(a_, U)
        if truthy(G) and truthy(Ax):
            if truthy(get(G, "gaapTwin")) == truthy(get(Ax, "gaapTwin")):
                FAIL("pair %s/%s: both flavours present but not one lead + one twin" % (g_, a_))
            tw = G if truthy(get(G, "gaapTwin")) else Ax
            if not test(r"\(GAAP\)$", get(tw, "label")) and jeq(get(M, "isFlow"), "nongaap"):
                WARN("twin %s not labelled (GAAP)" % S(get(tw, "label")))
    den = lead("IS_P_101", "IS_P_102")

    def chk(name, num):
        d = A(get(I, "derived"), lambda: get(get(I, "derived"), name))
        if not truthy(d) or not truthy(num) or not truthy(den):
            return
        for pr in it_(periods):
            pk = first(pr)
            n, r, dd = per(num, pk), per(den, pk), get(d, pk)
            if not truthy(n) or not truthy(r) or not truthy(dd):
                continue
            bps = (jdiv(get(n, "cur"), get(r, "cur")) - jdiv(get(n, "pre"), get(r, "pre"))) * 1e4
            if jabs(jsub(bps, get(dd, "bps"))) > 0.5:
                x1 = tofixed(get(dd, "bps"), 1)
                FAIL("derived %s %s: contract %sbp vs lead-flavour recompute %sbp" % (name, S(pk), x1, js_to_fixed(bps, 1)))
    chk("Gross margin", lead("IS_P_106", "IS_P_105"))
    chk("EBITDA margin", lead("IS_P_114", "IS_P_113"))
    chk("EBIT margin", lead("IS_P_110", "IS_P_109"))
    chk("Net margin", lead("IS_P_124", "IS_S_656", "IS_P_123"))
    MIN = O(A(get(I, "breadth"), lambda: get(get(I, "breadth"), "minCoverage")), 3)

    def elig(it):
        p = per(it, "q")
        return (truthy(p) and not truthy(get(p, "stale")) and not nul(get(p, "pre")) and not nul(get(p, "cur"))
                and (nul(get(p, "cnt")) or jge(get(p, "cnt"), MIN)))
    cnt_rows = [it for it in items if truthy(per(it, "q")) and not truthy(get(it, "gaapTwin"))]
    low = len([it for it in cnt_rows if not elig(it) and not truthy(A(per(it, "q"), lambda: get(per(it, "q"), "stale")))])
    br = get(I, "breadth")
    if truthy(br) and not nul(get(br, "lowCoverage")) and not jeq(get(br, "lowCoverage"), low):
        WARN("breadth.lowCoverage %s vs recount %d (rows failing ELIG, non-stale, non-twin)" % (S(get(br, "lowCoverage")), low))
    tk = lambda k: O(A(get(I, "takeaways"), lambda: get(get(I, "takeaways"), k)), lambda: [])
    curated = concat(tk("positive"), tk("negative"))
    for t in curated:
        lab = jtrim(R(r"\s[+−-].*$").sub("", jstr(t), count=1))
        it = next((x for x in items if jeq(get(x, "label"), lab)), U)
        if truthy(it) and not elig(it):
            FAIL("curated list carries an ineligible row: %s" % S(t))
    for d in it_(O(get(I, "divergence"), lambda: [])):
        if not jin("fav", d):
            FAIL("divergence %s: no fav" % S(get(d, "label")))
        good = (jlt(get(d, "q"), 0) if jeq(get(d, "fav"), "down") else
                (jgt(get(d, "q"), 0) if jeq(get(d, "fav"), "up") else None))
        want = ("The quarter's move does not carry through the full year." if good is None else
                "The %s does not carry through the full year." % ("improvement" if good else "deterioration"))
        if not jeq(get(d, "tail"), want):
            FAIL('divergence %s: tail "%s" vs polarity "%s"' % (S(get(d, "label")), S(get(d, "tail")), want))
    for x in it_(O(get(C, "excluded"), lambda: [])):
        if not truthy(get(x, "reason")):
            FAIL("excluded row %s without a reason" % S(get(x, "label")))
    mg, cv = get(I, "magnitude"), get(I, "curve")
    hr = A(mg, lambda: A(get(mg, "rev"), lambda: get(get(mg, "rev"), "pct")))
    c0 = A(cv, lambda: A(get(cv, "rev"), lambda: A(get(get(cv, "rev"), 0), lambda: get(get(get(cv, "rev"), 0), "pct"))))
    if not nul(hr) and not nul(c0) and jabs(jsub(hr, c0)) > 1e-9:
        FAIL("curve first point %s%% differs from the headline revenue revision %s%%" % (js_to_fixed(jmul(c0, 100), 2), js_to_fixed(jmul(hr, 100), 2)))
    he = A(mg, lambda: A(get(mg, "eps"), lambda: get(get(mg, "eps"), "pct")))
    e0 = A(cv, lambda: A(get(cv, "eps"), lambda: A(get(get(cv, "eps"), 0), lambda: get(get(get(cv, "eps"), 0), "pct"))))
    if not nul(he) and not nul(e0) and jabs(jsub(he, e0)) > 1e-9:
        FAIL("EPS curve first point %s%% differs from the headline EPS revision %s%%" % (js_to_fixed(jmul(e0, 100), 2), js_to_fixed(jmul(he, 100), 2)))
    At = get(I, "attribution")
    if truthy(At) and truthy(get(At, "driver")) and truthy(get(At, "dir")) and not jeq(jsign(get(get(At, "driver"), "dollar")), get(At, "dir")):
        FAIL("segment driver %s moves against the segment revision" % S(get(get(At, "driver"), "label")))
    le = lead("IS_S_657", "IS_P_128", "IS_P_127")
    bg = get(I, "bridge")
    if (truthy(bg) and truthy(le) and truthy(per(le, "q"))
            and (not near(get(bg, "eps1"), get(per(le, "q"), "cur"), 1e-6) or not near(get(bg, "eps0"), get(per(le, "q"), "pre"), 1e-6))):
        FAIL("EPS bridge %s -> %s is not on the lead EPS (%s: %s -> %s)" % (
            S(get(bg, "eps0")), S(get(bg, "eps1")), S(get(le, "label")), S(get(per(le, "q"), "pre")), S(get(per(le, "q"), "cur"))))
    ds = get(I, "dataState")
    if truthy(ds) and jeq(get(ds, "key"), "ok") and jge(get(ds, "staleCount"), jceil(jdiv(get(ds, "rows"), 3))):
        WARN("%s of %s rows stale" % (S(get(ds, "staleCount")), S(get(ds, "rows"))))

    if truthy(xlsx_path) and os.path.exists(xlsx_path):
        cells, xml = sheet_cells(xlsx_path)
        allc = list(cells.values())
        Acol = sorted([c for c in allc if c["col"] == "A"], key=lambda c: c["row"])
        if not test("Revision Analysis", get(O(cells.get("A1", U), lambda: {}), "t")):
            FAIL("masthead lacks the product name")
        if not any(test("Prepared:", c["t"]) for c in allc):
            FAIL("masthead lacks the Prepared date")
        if test("shrinkToFit", read_zip_entry(_read(xlsx_path), "xl/styles.xml")):
            FAIL("shrink-to-fit present in styles")
        wA = N(grp(R(r'<col min="1" max="1" width="([\d.]+)"').search(xml), 1))
        if not (wA >= 25.7 and wA <= 40.8):
            FAIL("column A width %s outside [25,40]" % js_str(wA))
        sheet_labels = [c["t"] for c in Acol]
        for it in items:
            if not includes(sheet_labels, get(it, "label")):
                FAIL("row missing on the sheet: %s" % S(get(it, "label")))
        for c in Acol:
            if test("…", c["t"]):
                FAIL("truncated label on the sheet: %s" % c["t"])
            if test(r"\*\s*$", c["t"]) and c["row"] > 2:
                FAIL("star marker on the sheet: %s" % c["t"])
            if test(r"^Note:", c["t"]):
                FAIL("footnote printed on the sheet")
        hdr_rows = [c["row"] for c in Acol if test(r"^Line Item", c["t"])]
        if not jeq(len(hdr_rows), get(periods, "length")):
            FAIL("%d header rows for %s blocks" % (len(hdr_rows), S(get(periods, "length"))))
        hdr_re = r"^(Pre-Print|T\+10|T\+40|Current|" + rx_esc(MF.deltaHeader) + r"|%Δ since print|YoY %|Seq %|Low|High|Broker cnt)$"
        for hr in hdr_rows:
            hdr_cells = sorted([c for c in allc if c["row"] == hr and c["col"] != "A" and c["t"]], key=lambda c: col_idx(c["col"]))
            hdr = [c["t"] for c in hdr_cells]
            last_col = jmax([col_idx(c["col"]) for c in hdr_cells if test(hdr_re, c["t"])])
            if not any(c["t"] == MF.deltaHeader for c in hdr_cells):
                FAIL('header row %s: no "%s" column for the reporting currency %s' % (js_str(hr), MF.deltaHeader, MF.sym))
            if not any(c["row"] == hr and c["t"] == "Line Item  (%s unless stated)" % MF.unitHeader for c in Acol):
                FAIL('header row %s: the unit label is not "%s"' % (js_str(hr), MF.unitHeader))
            dates = [c["t"] for c in sorted([c for c in allc if c["row"] == hr + 1 and col_idx(c["col"]) <= last_col], key=lambda c: col_idx(c["col"]))]
            anch = [h for h in hdr if test(r"^(Pre-Print|T\+10|T\+40|Current)$", h)]
            if (anch[0] if anch else U) != "Pre-Print" or (anch[-1] if anch else U) != "Current":
                FAIL("anchor columns not Pre-Print … Current: %s" % " | ".join(anch))
            dd = [t for t in dates if test(r"^\d\d\/\d\d$", t)]
            for i in range(1, len(dd)):
                if jlt(dd[i], dd[i - 1]) and not (jgt(dd[i - 1], "11") and jlt(dd[i], "02")):
                    FAIL("date row out of order at header row %s: %s" % (js_str(hr), " → ".join(dd)))
            for t in dates:
                if t and not test(r"^\d\d\/\d\d$", t) and not test(r"^Same as (Current|Pre-Print)$", t):
                    FAIL("unexpected date-row cell: %s" % t)
            same = len([t for t in dates if test(r"^Same as", t)])
            if same:
                INFO("header row %s: %d collapsed anchor cell(s)" % (js_str(hr), same))
        hr0 = hdr_rows[0] if hdr_rows else U
        hdr0 = sorted([c for c in allc if jeq(c["row"], hr0)], key=lambda c: col_idx(c["col"]))
        cur_col = next((c for c in hdr0 if c["t"] == "Current"), {}).get("col", U)
        if truthy(cur_col):
            block_rows = {}
            blk = 0
            for c in Acol:
                if c["row"] in hdr_rows:
                    blk += 1
                block_rows[c["row"]] = blk
            pks = [get(p, "0") for p in arr(periods)]
            checked = bad = 0
            for c in Acol:
                it = next((x for x in items if jeq(get(x, "label"), c["t"])), U)
                if not truthy(it):
                    continue
                pk = get(pks, block_rows[c["row"]] - 1)
                p = per(it, pk)
                if not truthy(p) or nul(get(p, "cur")):
                    continue
                cell = cells.get(cur_col + js_str(c["row"]))
                if not cell or cell["v"] is None:
                    if not truthy(get(p, "stale")):
                        FAIL("%s %s: Current cell empty on the sheet" % (S(get(it, "label")), S(pk)))
                    continue
                checked += 1
                if jabs(jsub(cell["v"], get(p, "cur"))) > 1e-6 * jmax([1, jabs(get(p, "cur"))]):
                    bad += 1
                    FAIL("%s %s: sheet Current %s vs contract %s" % (S(get(it, "label")), S(pk), js_str(cell["v"]), S(get(p, "cur"))))
            INFO("Current column: %d cells tie to the contract%s" % (checked, (", %d differ" % bad) if bad else ""))
        lim = col_idx(O(cur_col, "D")) + 7
        panel = [c["t"] for c in allc if col_idx(c["col"]) > lim and c["t"]]
        bad_re = r"undefined|NaN|\bnull\b|Infinity|\[object|" + SYMRX + r"0m\b|\+0\.0%|−0\.0%|\bnull\b"
        if MF.sym != "$":
            for t in panel:
                if test(r"(^|[^A-Z])\$\d", t):
                    FAIL('"$" amount on a %s sheet: "%s"' % (MF.sym, u16slice(t, 0, 110)))
        for t in panel:
            if test(bad_re, t):
                FAIL('panel text carries a rendering artefact: "%s"' % u16slice(t, 0, 120))
        for t in panel:
            if test("—", t):
                WARN('em dash in panel text: "%s"' % u16slice(t, 0, 100))
            if test(";", t):
                WARN('semicolon in panel text: "%s"' % u16slice(t, 0, 100))
        q = S(get(M, "q"))
        if truthy(ds) and jeq(get(ds, "key"), "ok"):
            if not any(t == "Upgrades (%s)" % q or test(r"^No material upgrades identified \(" + q + r"\)", t) for t in panel):
                WARN("no Upgrades header with the period label")
            if not any(t == "Downgrades (%s)" % q or test(r"^No material downgrades identified \(" + q + r"\)", t) for t in panel):
                WARN("no Downgrades header with the period label")
            RPx = get(I, "revisionPath")
            if truthy(RPx) and truthy(get(RPx, "dominant")) and not any(test(r"^Revision path \(" + q + r"\):", t) for t in panel):
                WARN("revision path computed but not printed with its period label")
        if any(test(r"Exception\. [A-Za-z]+, estimates", t) for t in panel):
            FAIL("old exception wording")
        for t in panel:
            if test(r"\b(cut|took|raised|added|lifted|reduced)\s+[+−-]" + SYMRX, t):
                FAIL('double-signed amount after a directional verb: "%s"' % u16slice(t, 0, 110))
        rvl = lead("IS_P_101", "IS_P_102", "IS_P_200", "IS_P_403", "IS_P_402")
        ep = lead("IS_S_657", "IS_P_128", "IS_P_127")
        line = next((t for t in panel if test(r"^•\s+" + q + " Revenue: ", t)), U)
        if truthy(line) and truthy(rvl) and truthy(per(rvl, "q")) and not nul(get(per(rvl, "q"), "cur")):
            m = R(r"Revenue: (" + SYMRX + r"[\d.,]+(?:bn|m))(?:\s+\|\s+EPS " + SYMRX + r"([\d.]+))?").search(line)
            if m and m.group(1) != fBn(get(per(rvl, "q"), "cur")):
                FAIL("Consensus Embeds revenue %s is not the lead flavour's %s (%s)" % (m.group(1), fBn(get(per(rvl, "q"), "cur")), S(get(rvl, "label"))))
            if (m and m.group(2) and truthy(ep) and truthy(per(ep, "q")) and not nul(get(per(ep, "q"), "cur"))
                    and m.group(2) != js_to_fixed(N(get(per(ep, "q"), "cur")), 2)):
                FAIL("Consensus Embeds EPS $%s is not the lead flavour's $%s (%s)" % (m.group(2), js_to_fixed(N(get(per(ep, "q"), "cur")), 2), S(get(ep, "label"))))
        H = get(I, "history")
        line = next((t for t in panel if test(r"^•\s+Cycle rank:", t)), U)
        if truthy(H) and truthy(line) and not jeq(get(H, "uniformBasis"), False):
            def chk_rank(cur, key, name):
                vals = [v for v in (get(r, key) for r in arr(O(get(H, "rows"), lambda: []))) if not nul(v)]
                if nul(cur) or len(vals) < 4:
                    return None
                a = jabs(cur)
                big = all(a > jabs(v) for v in vals)
                small = all(a < jabs(v) for v in vals)
                flip = jabs(cur) > 0.0005 and all(jsign(v) == -jsign(cur) for v in vals)
                return {"name": name, "big": big, "small": small, "flip": flip}
            m = R(r"(largest|smallest) (revenue|EPS) revision|only post-print (cut|raise) to (revenue|EPS)").search(line)
            if m:
                name = O(grp(m, 2), lambda: grp(m, 4))
                r = chk_rank(get(H, "curRev") if name == "revenue" else get(H, "curEps"), "rev" if name == "revenue" else "eps", name)
                if r:
                    if grp(m, 1) == "largest" and not r["big"]:
                        FAIL('cycle rank says the largest %s revision but it is not the largest in magnitude: "%s"' % (S(name), u16slice(line, 0, 100)))
                    if grp(m, 1) == "smallest" and not r["small"]:
                        FAIL('cycle rank says the smallest %s revision but it is not the smallest in magnitude: "%s"' % (S(name), u16slice(line, 0, 100)))
                    if truthy(grp(m, 3)) and not r["flip"]:
                        FAIL('cycle rank claims a sign flip that the prior cycles do not show: "%s"' % u16slice(line, 0, 100))
                    if not truthy(grp(m, 3)) and r["flip"]:
                        FAIL('cycle rank misses a sign flip against every prior cycle: "%s"' % u16slice(line, 0, 100))
        RP = get(I, "revisionPath")
        if (truthy(RP) and truthy(get(RP, "dominant")) and not jeq(get(RP, "dominant"), "Reset & Hold")
                and any(test(r"has held, on a|has broadly held since|has held on the headline line since", t) for t in panel)):
            FAIL("panel says the reset has held on a %s path" % S(get(RP, "dominant")))
        A2 = get(I, "attribution")
        i0 = next((i for i, t in enumerate(panel) if test(r"^Revenue mix \(" + q + r"\)$", t)), -1)
        if truthy(A2) and truthy(get(A2, "dir")) and i0 >= 0:
            lines = []
            i = i0 + 1
            while i < len(panel) and test(r"^•\s", panel[i]):
                lines.append(panel[i])
                i += 1

            def amt(t):
                m = R(r":\s*([+−-])" + SYMRX + r"([\d.,]+)(bn|m)$").search(t)
                if not m:
                    return None
                v = js_parse_float(m.group(2).replace(",", "")) * (1000 if m.group(3) == "bn" else 1)
                return v if m.group(1) == "+" else -v
            segv = [v for v in (amt(t) for t in lines if not test(r"^•\s+Other contributors:", t)) if v is not None]
            if len(segv) and not jeq(jsign(segv[0]), get(A2, "dir")):
                FAIL('Revenue mix opens against the revision\'s direction: "%s"' % lines[0])
            for i in range(1, len(segv)):
                if jgt(jmul(segv[i], get(A2, "dir")), jadd(jmul(segv[i - 1], get(A2, "dir")), 0.5)):
                    FAIL("Revenue mix is not largest-first in the revision's direction: %s" % " / ".join(lines))
                    break
            rows = [r for r in arr(O(get(A2, "rows"), lambda: [])) if jabs(get(r, "dollar")) > 0]
            gross = reduce(lambda t, r: t + jabs(get(r, "dollar")), rows, 0.0)
            offs = js_cmp_sort([r for r in rows if jlt(jmul(get(r, "dollar"), get(A2, "dir")), 0)],
                               lambda a, b: jabs(get(b, "dollar")) - jabs(get(a, "dollar")))
            off = offs[0] if offs else U
            if (truthy(off) and truthy(gross) and jdiv(jabs(get(off, "dollar")), gross) >= 0.05
                    and not any(jsign(v) == -N(get(A2, "dir")) for v in segv)):
                FAIL("Revenue mix omits its largest offset (%s, %s)" % (S(get(off, "label")), tofixed(get(off, "dollar"), 0)))
        for d in it_(O(get(I, "divergence"), lambda: [])):
            line = U
            for t in panel:
                esc = re.sub(r"[.*+?^${}()|\[\]\\]", lambda m: "\\" + m.group(0), jstr(get(d, "label")))
                if test("^" + esc + " Divergence:", t):
                    line = t
                    break
            if truthy(line) and truthy(get(d, "tail")) and S(get(d, "tail")) not in line:
                FAIL('divergence line wording differs from the contract tail: "%s"' % line)
        yoy_col = next((c for c in hdr0 if c["t"] == "YoY %"), {}).get("col", U)
        seq_col = next((c for c in hdr0 if c["t"] == "Seq %"), {}).get("col", U)
        if truthy(yoy_col):
            for c in Acol:
                it = next((x for x in items if jeq(get(x, "label"), c["t"])), U)
                if not truthy(it) or not truthy(per(it, "q")):
                    continue
                if len(hdr_rows) and jgt(c["row"], get(hdr_rows, 1)):
                    break
                rk = js_str(c["row"])
                if nul(get(per(it, "q"), "yoy")) and cells.get(yoy_col + rk) and cells[yoy_col + rk]["v"] is not None:
                    FAIL("%s: YoY printed but null in the contract" % S(get(it, "label")))
                if (truthy(seq_col) and nul(get(per(it, "q"), "seq")) and cells.get(seq_col + rk)
                        and cells[seq_col + rk]["v"] is not None):
                    FAIL("%s: Seq printed but null in the contract" % S(get(it, "label")))
    else:
        WARN("no workbook found: sheet checks skipped")

    emit("\n".join(out))
    emit("\n%d FAIL, %d WARN" % (cnt["fails"], cnt["warns"]))
    sys.stdout.flush()
    sys.exit(1 if cnt["fails"] else 0)


if __name__ == "__main__":
    main()
