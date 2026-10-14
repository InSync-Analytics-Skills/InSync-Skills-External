"""
prepare_revisions.py - merge raw InSync MCP estimate dumps into a revision-analysis contract.
Usage:  python prepare_revisions.py <raw_dir> <meta.json> <contract.json>

 <raw_dir>   folder with the verbatim JSON results of every get_estimates call
             (latest AND history pulls, all periods mixed - any nesting; the walker
             finds every row that has an LTCODE). Auto-saved tool-result .txt files work too.
 <meta.json> { company, ticker, tickerId, subindustry, q, q2, fy1, fy2, fy0, printPeriod,
               printDate, nextPrintDate, yoyQ, prepared, pastCycles[] }  (SKILL Step 5)
               - q like "3Q26", fy1/fy2 like "2026"/"2027", printDate ISO "2026-07-22"
               (last earnings call date), prepared = today ISO.

Rules encoded here (do NOT re-derive in the conversation):
 - Every row with BROKERCOUNT is an estimate snapshot keyed by (LTCODE, FISCALPERIOD,
   UPDATEDATE). The LAST snapshot per (lt, fp) is the Current consensus.
 - Anchors, sliced from history (value = last snapshot ON OR BEFORE the cut):
     pre  = printDate - 1 day   (consensus going into the last print)
     t10  = printDate + 10 days (post-print reset, once the bulk of revisions land)
     t40  = printDate + 40 days
   Eligibility is calendar-gated (a cut exists only once elapsed); visibility is
   information-gated (contract.anchorView: an anchor renders only where some row's
   value differs from its neighbours). The date row carries each column's consensus
   VINTAGE (contract.vintages), never the cut. Rows unrefreshed since the print are
   flagged stale (per.q.stale); the table-level state is intel.dataState.
 - Row set = base LTCODEs (no -Y/-Q/-M/-BY suffix) that carry a Current estimate for
   the next quarter (falling back to FY1 for annual-only lines).
 - Sections: SD_* revenue rows -> Segment Revenue; IS_* main-P&L whitelist -> Income
   Statement; KM_* -> Key Metrics; BS_* and CF_* key whitelists -> Balance Sheet / Cash Flow.
 - If Segment or KPI buckets exceed 15 rows they are trimmed to the 15 highest
   broker-count rows (logged) to keep the summary readable (~30-55 rows total).
 - History matrix (next-quarter period, contract.history, never rendered): one column
   per update date AFTER printDate, forward-filled per item; capped at 24 columns
   (the earliest post-print date plus the 23 most recent).
 - Curated movers (intel.takeaways) = Current vs Pre-Print, ranked by dollar
   materiality against revenue, one line per economic layer, both directions.

Ported from an earlier JavaScript version (internal edition only) with byte-identical behaviour:
the same console output, exit codes and contract bytes on the same inputs. JavaScript semantics that differ
from Python (number printing, toFixed, Math.round, parseFloat, sort, JSON, truthiness, object
key order, null/undefined, division by zero, regex flavour) go through jscompat.py and the
small helpers below; never "simplify" them back to plain Python.
"""
import json
import math
import os
import re
import sys
import time

from jscompat import (js_str, js_json, js_to_fixed, js_round, js_keys, js_sort_default,
                      js_cmp_sort, js_stdio, js_write, _js_read_json, _JSErr, _truthy,
                      _pint, _bad_const, UNDEFINED)
from revision_common import STRICT_CHECKS, cur_sym, money_fmt

VERSION = '2.5.0'

UNDEF = UNDEFINED
NaN = float('nan')
INF = float('inf')

_WS_CHARS = ('\t\n\x0b\x0c\r \xa0 ' + ''.join(chr(c) for c in range(0x2000, 0x200b)) +
             '    　﻿')
_WS_CLASS = '\t\n\x0b\x0c\r \xa0  -     　﻿'


def jre(src, flags=''):
    out, i, n, in_cls = [], 0, len(src), False
    while i < n:
        c = src[i]
        if c == '\\':
            nx = src[i + 1]
            if nx == 's':
                out.append(_WS_CLASS if in_cls else '[' + _WS_CLASS + ']')
            elif nx == 'S':
                out.append('[^' + _WS_CLASS + ']')
            elif nx == '/':
                out.append('/')
            else:
                out.append(c + nx)
            i += 2
            continue
        if in_cls:
            if c == ']':
                in_cls = False
            out.append(c)
        elif c == '[':
            in_cls = True
            out.append(c)
        elif c == '.':
            out.append('[^\n\r  ]')
        elif c == '$':
            out.append(r'\Z')
        else:
            out.append(c)
        i += 1
    f = re.ASCII
    if 'i' in flags:
        f |= re.IGNORECASE
    return re.compile(''.join(out), f)


def S(x):
    if x is None:
        return 'null'
    if x is UNDEF:
        return 'undefined'
    if x is True:
        return 'true'
    if x is False:
        return 'false'
    if isinstance(x, str):
        return x
    if isinstance(x, (int, float)):
        return js_str(x)
    if isinstance(x, list):
        return ','.join('' if (e is None or e is UNDEF) else S(e) for e in x)
    if isinstance(x, dict):
        return '[object Object]'
    return str(x)


def K(x):
    return S(x)


def nil(x):
    return x is None or x is UNDEF


def jtrim(s):
    return s.strip(_WS_CHARS)


_NUM_RE = re.compile(r'[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z', re.ASCII)


def jnum(v):
    if v is None:
        return 0
    if v is UNDEF:
        return NaN
    if v is True:
        return 1
    if v is False:
        return 0
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        t = jtrim(v)
        if t == '':
            return 0
        if t in ('Infinity', '+Infinity'):
            return INF
        if t == '-Infinity':
            return -INF
        for pre, base, digs in (('0x', 16, '0123456789abcdefABCDEF'), ('0o', 8, '01234567'), ('0b', 2, '01')):
            if len(t) > 2 and t[:2].lower() == pre and all(ch in digs for ch in t[2:]):
                return float(int(t[2:], base))
        if _NUM_RE.match(t):
            return float(t)
        return NaN
    if isinstance(v, list):
        return jnum(S(v))
    return NaN


_PF_RE = re.compile('[' + _WS_CLASS + r']*([+-]?(?:Infinity|(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?))', re.ASCII)


def jpf(s):
    if not isinstance(s, str):
        s = S(s)
    m = _PF_RE.match(s)
    if not m:
        return NaN
    t = m.group(1)
    if t.endswith('Infinity'):
        return -INF if t[0] == '-' else INF
    return float(t)


def isnan(x):
    return isinstance(x, float) and math.isnan(x)


def jfinite(x):
    if x is None:
        return True
    if x is UNDEF:
        return False
    x = jnum(x)
    return not (isinstance(x, float) and (math.isnan(x) or math.isinf(x)))


def N(x):
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return x
    return jnum(x)


def jdiv(a, b):
    a, b = N(a), N(b)
    try:
        return a / b
    except ZeroDivisionError:
        a = float(a)
        if math.isnan(a) or a == 0:
            return NaN
        neg = (a < 0) != (math.copysign(1.0, b) < 0)
        return -INF if neg else INF


def jabs(x):
    return abs(N(x))


def jsign(x):
    x = N(x)
    if isnan(x):
        return NaN
    if x > 0:
        return 1
    if x < 0:
        return -1
    return x


def _is_neg0(v):
    return v == 0 and math.copysign(1.0, v) < 0


def jmax(*a):
    r = -INF
    for v in a:
        v = N(v)
        if isnan(v):
            return NaN
        if v > r or (v == 0 and r == 0 and _is_neg0(r) and not _is_neg0(v)):
            r = v
    return r


def jmin(*a):
    r = INF
    for v in a:
        v = N(v)
        if isnan(v):
            return NaN
        if v < r or (v == 0 and r == 0 and _is_neg0(v) and not _is_neg0(r)):
            r = v
    return r


def jor(*xs):
    for x in xs[:-1]:
        if _truthy(x):
            return x
    return xs[-1]


def jsum(vals, start=0):
    t = start
    for v in vals:
        t = t + v
    return t


def jadd(a, b):
    if isinstance(a, (str, list, dict)) or isinstance(b, (str, list, dict)):
        return S(a) + S(b)
    return N(a) + N(b)


def scmp(a, b):
    if a == b:
        return 0
    if a.isascii() and b.isascii():
        return -1 if a < b else 1
    return -1 if a.encode('utf-16-be') < b.encode('utf-16-be') else 1


def slt(a, b):
    return scmp(a, b) < 0


def sle(a, b):
    return scmp(a, b) <= 0


def u16key(s):
    return s.encode('utf-16-be')


def jlen(s):
    return len(s) if s.isascii() else len(s.encode('utf-16-le')) // 2


def gp(o, k):
    if o is None or o is UNDEF:
        raise TypeError("Cannot read properties of %s (reading '%s')" % (S(o), k))
    if isinstance(o, dict):
        return o.get(k, UNDEF)
    if isinstance(o, list) and k == 'length':
        return len(o)
    return UNDEF


def jiter(x):
    if isinstance(x, list):
        return list(x)
    if isinstance(x, str):
        return list(x)
    raise TypeError('%s is not iterable' % S(x))


def rsub(R, s, repl, count=1):
    return R.sub(lambda m: repl, s, count=count)


def tf(x, d):
    return js_to_fixed(N(x), d)


def insp(v):
    if isinstance(v, float) and _is_neg0(v):
        return '-0'
    return js_str(v)


def log(*a):
    sys.stdout.write(' '.join(a) + '\n')


def err(*a):
    sys.stderr.write(' '.join(a) + '\n')


def exit_(code):
    sys.stdout.flush()
    sys.stderr.flush()
    sys.exit(code)


def ident_in(x, arr):
    return any(e is x for e in arr)


def _days_from_civil(y, m, d):
    y -= 1 if m <= 2 else 0
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
    return (y + (1 if m <= 2 else 0), m, d)


_ISO_DT = re.compile(r'([+-][0-9]{6}|[0-9]{4})(?:-([0-9]{2})(?:-([0-9]{2}))?)?'
                     r'(?:T([0-9]{2}):([0-9]{2})(?::([0-9]{2})(?:\.([0-9]+))?)?(Z|[+-][0-9]{2}:[0-9]{2})?)?\Z', re.ASCII)
_LEGACY_YMD = re.compile('[' + _WS_CLASS + r']*([0-9]{4})[-/]([0-9]{1,2})[-/]([0-9]{1,2})[' + _WS_CLASS + r']*\Z', re.ASCII)
_LEGACY_MDY = re.compile('[' + _WS_CLASS + r']*([0-9]{1,2})/([0-9]{1,2})/([0-9]{4})[' + _WS_CLASS + r']*\Z', re.ASCII)


def _local_ms(y, mo, d, h=0, mi=0, s=0, ms=0):
    try:
        return int(time.mktime((y, mo, d, h, mi, s, 0, 0, -1))) * 1000 + ms
    except (OverflowError, ValueError):
        return NaN


def date_parse(s):
    if not isinstance(s, str):
        s = S(s)
    m = _ISO_DT.match(s)
    if m:
        ys, mo, dd, hh, mi, ss, frac, tz = m.groups()
        if ys in ('-000000',):
            return NaN
        y = int(ys)
        mo = int(mo) if mo else 1
        dd = int(dd) if dd else 1
        if not (1 <= mo <= 12) or not (1 <= dd <= 31):
            return NaN
        h = int(hh) if hh else 0
        mi_ = int(mi) if mi else 0
        sec = int(ss) if ss else 0
        msec = int((frac + '00')[:3]) if frac else 0
        if h > 24 or mi_ > 59 or sec > 59 or (h == 24 and (mi_ or sec or msec)):
            return NaN
        if hh is not None and tz is None:
            if not (0 <= y <= 9999):
                return NaN
            return _local_ms(y, mo, dd, h, mi_, sec, msec)
        t = (_days_from_civil(y, mo, 1) + dd - 1) * 86400000 + ((h * 60 + mi_) * 60 + sec) * 1000 + msec
        if tz and tz != 'Z':
            off = (int(tz[1:3]) * 60 + int(tz[4:6])) * 60000
            t = t - off if tz[0] == '+' else t + off
        return t if abs(t) <= 8.64e15 else NaN
    m = _LEGACY_YMD.match(s)
    if m:
        y, mo, dd = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = _LEGACY_MDY.match(s)
        if not m:
            return NaN
        mo, dd, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= mo <= 12) or not (1 <= dd <= 31):
        return NaN
    return _local_ms(y, mo, dd)


def iso(t):
    t = N(t)
    if isnan(t) or abs(t) > 8.64e15:
        raise ValueError('RangeError: Invalid time value')
    y, mo, d = _civil_from_days(int(math.floor(t / 86400000)))
    ys = ('%04d' % y) if 0 <= y <= 9999 else (('+' if y > 0 else '-') + '%06d' % abs(y))
    return (ys + '-%02d-%02d' % (mo, d) + 'T00:00:00.000Z')[:10]


CUR_RE = jre(r'^(\$|€|£|¥|₹|₩|[A-Z]{2,3}\$?)(?=\s*-?[\d.])')


def curToken(s):
    if nil(s):
        return None
    m = CUR_RE.search(jtrim(S(s)))
    return m.group(1) if m else None


def curSym(t):
    return cur_sym(t)


def main():
    js_stdio()
    argv = sys.argv[1:]
    rawDir = argv[0] if len(argv) > 0 else ''
    metaPath = argv[1] if len(argv) > 1 else ''
    outPath = argv[2] if len(argv) > 2 else ''
    if not rawDir or not metaPath or not outPath:
        err('usage: python prepare_revisions.py <raw_dir> <meta.json> <contract.json>')
        exit_(1)

    def stop(msg):
        err('Input error: ' + msg)
        exit_(2)
    if not os.path.exists(metaPath):
        stop('meta.json not found: %s' % metaPath)
    if not os.path.exists(rawDir) or not os.path.isdir(rawDir):
        stop('raw folder not found: %s' % rawDir)
    meta = None
    try:
        meta = _js_read_json(metaPath)
    except _JSErr as e:
        stop('meta.json is not valid JSON (%s)' % e)
    if meta is None:
        raise TypeError("Cannot read properties of null (reading 'tickerId')")

    def mg(k):
        return meta.get(k, UNDEF) if isinstance(meta, dict) else UNDEF
    if True:
        ISO = jre(r'^\d{4}-\d{2}-\d{2}$')
        QTR = jre(r'^[1-4]Q\d{2}$')
        HALF = jre(r'^[12]H\d{2}$', 'i')
        YEAR = jre(r'^\d{4}$')

        def isoOk(v):
            return bool(ISO.search(S(v))) and not isnan(date_parse(S(v)))
        for k in ['tickerId', 'printDate', 'printPeriod', 'q', 'fy1', 'prepared']:
            if nil(mg(k)) or jtrim(S(mg(k))) == '':
                stop('meta.json is missing "%s" (SKILL Step 5)' % k)
        for k in ['q', 'printPeriod', 'q2', 'yoyQ']:
            if not nil(mg(k)) and HALF.search(S(mg(k))):
                stop('meta.json "%s" is a half-year period (%s): this skill covers quarterly reporters only (SKILL Step 1)' % (k, S(mg(k))))
        for k in ['q', 'printPeriod', 'q2', 'yoyQ']:
            if not nil(mg(k)) and not QTR.search(S(mg(k))):
                stop('meta.json "%s" must be a quarter like 2Q27, got "%s"' % (k, S(mg(k))))
        for k in ['fy1', 'fy2', 'fy0']:
            if not nil(mg(k)) and not YEAR.search(S(mg(k))):
                stop('meta.json "%s" must be a four-digit year like 2027, got "%s"' % (k, S(mg(k))))
        for k in ['printDate', 'prepared', 'nextPrintDate']:
            if not nil(mg(k)) and not isoOk(mg(k)):
                stop('meta.json "%s" must be an ISO date like 2026-09-25, got "%s"' % (k, S(mg(k))))
        for c in jiter(jor(mg('pastCycles'), [])):
            if not _truthy(c) or not isoOk(gp(c, 'printDate')) or not QTR.search(S(jor(gp(c, 'targetFp'), ''))):
                stop('meta.json pastCycles entry %s needs an ISO printDate and a quarter targetFp' % js_json(c))
        meta['tickerId'] = S(meta['tickerId'])
        if not _truthy(mg('yoyQ')):
            q = meta['q']
            q0 = q[0] if isinstance(q, (str, list)) and len(q) else UNDEF
            qs = q[2:] if isinstance(q, (str, list)) else UNDEF
            meta['yoyQ'] = jadd(jadd(q0, 'Q'), S(jnum(qs) - 1).rjust(2, '0'))
            log('Input note: meta.json has no "yoyQ"; derived %s from q %s.' % (S(meta['yoyQ']), S(meta['q'])))
    PERIODS = [['q', meta['q']]]
    if _truthy(mg('fy1')):
        PERIODS.append(['fy1', meta['fy1']])
    FY0 = mg('fy0') if _truthy(mg('fy0')) else (S(jnum(mg('fy1')) - 1) if jre(r'^\d{4}$').search(S(jor(mg('fy1'), ''))) else None)
    if not _truthy(mg('fy0')) and _truthy(FY0):
        meta['fy0'] = FY0
    kQ, kFY1, kFY0 = K(meta['q']), K(mg('fy1')), K(mg('fy0'))
    kPP, kYOY = K(meta['printPeriod']), K(meta['yoyQ'])
    PD = S(meta['printDate'])

    raw, docs = [], []

    def walk(o):
        if isinstance(o, list):
            if len(o) and _truthy(o[0]) and isinstance(o[0], (dict, list)) and _truthy(gp(o[0], 'LTCODE')):
                raw.extend(o)
            else:
                for x in o:
                    walk(x)
        elif _truthy(o) and isinstance(o, dict):
            for k in js_keys(o):
                walk(o[k])
    foreign, noId = {}, []
    FILE_RE = jre(r'\.(json|txt)$', 'i')
    for f in js_sort_default(os.listdir(rawDir)):
        if not FILE_RE.search(f):
            continue
        with open(os.path.join(rawDir, f), 'rb') as fh:
            txt = jtrim(fh.read().decode('utf-8', 'replace'))
        try:
            j = json.loads(txt, parse_constant=_bad_const, parse_int=_pint)
        except (ValueError, RecursionError):
            continue
        docs.append(j)
        n0 = len(raw)
        walk(j)
        for row in raw[n0:]:
            tid = gp(row, 'TICKER_ID')
            if nil(tid) or jtrim(S(tid)) == '':
                noId.append(f)
                continue
            if S(tid) != meta['tickerId']:
                foreign.setdefault(f, {})[S(tid)] = True
    if len(foreign):
        stop(('raw folder holds rows from another company (meta.json tickerId %s): ' % meta['tickerId']) +
             '; '.join('%s has TICKER_ID %s' % (f, ', '.join(s)) for f, s in ((k, foreign[k]) for k in js_keys(foreign))) +
             '. Use a new, empty raw/ folder for each ticker and run (SKILL Step 4a).')
    if len(noId):
        fl = list(dict.fromkeys(noId))
        msg = '%d estimate/actual row(s) carry no TICKER_ID (%s), so the company they belong to cannot be checked.' % (len(noId), ', '.join(fl))
        if STRICT_CHECKS:
            stop(msg)
        log('WARNING: ' + msg + ' Save MCP results verbatim with ingest_raw.py.')
    if not len(raw):
        err('No LTCODE rows found in', rawDir)
        exit_(1)
    if True:
        res = []

        def find(o):
            if isinstance(o, list):
                for x in o:
                    find(x)
                return
            if not _truthy(o) or not isinstance(o, (dict, list)):
                return
            data = o.get('data', UNDEF)
            om = o.get('meta', UNDEF)
            if _truthy(om) and isinstance(data, list) and len(data):
                d0 = data[0] if _truthy(data[0]) else {}
                kind = 'rows' if _truthy(gp(d0, 'LTCODE')) else ('px' if gp(d0, 'ADJUSTED_CLOSE') is not UNDEF else None)
                if kind:
                    keys = {}
                    for r in data:
                        keys[S(jor(gp(r, 'FISCALPERIOD'), '')) if kind == 'rows' else S(jor(gp(r, 'TICKER_ID'), ''))] = True
                    res.append({'kind': kind, 'more': gp(om, 'has_more') is True, 'off': jnum(jor(gp(om, 'offset'), 0)),
                                'lim': jnum(jor(gp(om, 'limit'), len(data))), 'keys': keys})
            for k in js_keys(o):
                if k != 'data':
                    find(o[k])
        find(docs)
        gaps = [r for r in res if r['more'] and not any(
            n['kind'] == r['kind'] and n['off'] == r['off'] + r['lim'] and any(k in r['keys'] for k in n['keys']) for n in res)]
        if len(gaps):
            msg = ('%d saved result(s) say more pages exist but the next page is not in raw/ (%s). Fetch it with offset += limit and ingest it (SKILL Step 4).'
                   % (len(gaps), '; '.join('%s %s at offset %s' % ('prices' if g['kind'] == 'px' else 'estimates/actuals', ','.join(list(g['keys'])[:3]),
                                                                 js_str(g['off'] + g['lim'])) for g in gaps[:4])))
            if STRICT_CHECKS:
                stop(msg)
            log('WARNING: ' + msg)

    def parseVal(s):
        if nil(s) or s == '':
            return None
        s = jtrim(S(s))
        if s.endswith('%'):
            return {'n': jpf(s) / 100, 'kind': 'pct'}
        if s.endswith('x'):
            return {'n': jpf(s), 'kind': 'ratio'}
        cm = CUR_RE.search(s)
        if cm:
            r = jtrim(s[len(cm.group(1)):])
            if r.endswith('T'):
                return {'n': jpf(r) * 1e6, 'kind': 'money'}
            if r.endswith('B'):
                return {'n': jpf(r) * 1000, 'kind': 'money'}
            if r.endswith('M'):
                return {'n': jpf(r), 'kind': 'money'}
            if r.endswith('k'):
                return {'n': jpf(r) / 1000, 'kind': 'money'}
            return {'n': jpf(r), 'kind': 'eps'}
        if s.endswith('T'):
            return {'n': jpf(s) * 1e6, 'kind': 'shares'}
        if s.endswith('B'):
            return {'n': jpf(s) * 1000, 'kind': 'shares'}
        if s.endswith('M'):
            return {'n': jpf(s), 'kind': 'shares'}
        if s.endswith('k'):
            return {'n': jpf(s) / 1000, 'kind': 'shares'}
        n = jpf(s)
        return None if isnan(n) else {'n': n, 'kind': 'num'}
    SUF = {'k': 1e3, 'M': 1e6, 'B': 1e9, 'T': 1e12}
    KM_RE = jre(r'^(\$|€|£|¥|₹|₩|[A-Z]{2,3}\$?)?\s*(-?[\d.,]+)\s*([kMBT])?$')

    def kmParts(s):
        if nil(s) or s == '':
            return None
        s = jtrim(S(s))
        if s.endswith('%'):
            return {'kind': 'pct', 'n': jpf(s) / 100}
        if s.endswith('x'):
            return {'kind': 'ratio', 'n': jpf(s)}
        m = KM_RE.search(s)
        if not m:
            return None
        mant = jpf(m.group(2).replace(',', ''))
        if isnan(mant):
            return None
        return {'mant': mant, 'suf': m.group(3) or None, 'dollar': bool(m.group(1))}
    kmUnit = {}

    def parseValKM(s, lt):
        p = kmParts(s)
        if not p:
            return None
        if p.get('kind'):
            return {'n': p['n'], 'kind': p['kind']}
        u = ({'factor': (SUF[p['suf']] if p['suf'] else 1), 'dollar': kmUnit[lt]['dollar']} if (_truthy(lt) and lt in kmUnit)
             else {'factor': (SUF[p['suf']] if p['suf'] else 1), 'dollar': p['dollar']})
        nd = p['mant'] * u['factor']
        if u['dollar']:
            if abs(nd) < 1e6:
                return {'n': nd, 'kind': 'eps'}
            return {'n': nd / 1e6, 'kind': 'money'}
        return {'n': nd, 'kind': 'num'}
    KM_LT = jre(r'^KM_')

    def isKM(lt):
        return bool(KM_LT.search(S(lt)))
    _pcache = {}

    def _parsed(lt, s):
        try:
            key = (lt, s if not isinstance(s, float) else ('f', s))
            hash(key)
        except TypeError:
            key = None
        if key is not None and key in _pcache:
            return _pcache[key]
        p = parseValKM(s, lt) if isKM(lt) else parseVal(s)
        if key is not None:
            _pcache[key] = p
        return p

    def pnum(lt, s):
        p = _parsed(lt, s)
        return p['n'] if p else None

    def pkind(lt, s):
        p = _parsed(lt, s)
        return p['kind'] if p else None

    def ppct(s):
        if nil(s) or s == '':
            return None
        n = jpf(S(s))
        return None if isnan(n) else n / 100

    hist, act, name = {}, {}, {}
    actR = {}
    _seenD = {}
    RPFX = jre(r'^R-')
    for row in raw:
        lt = gp(row, 'LTCODE')
        if not _truthy(lt):
            continue
        lt = K(lt)
        rawFp = S(jor(gp(row, 'FISCALPERIOD'), ''))
        fp = rsub(RPFX, rawFp, '')
        if not fp:
            continue
        li = gp(row, 'LINEITEM')
        if _truthy(li) and not _truthy(name.get(lt, UNDEF)):
            name[lt] = li
        bc = gp(row, 'BROKERCOUNT')
        if nil(bc):
            act.setdefault(lt, {})[fp] = gp(row, 'VALUE')
            if rawFp != fp:
                actR.setdefault(lt, {})[fp] = gp(row, 'VALUE')
            continue
        d = S(jor(gp(row, 'UPDATEDATE'), ''))[:10]
        if not d:
            continue
        bucket = hist.setdefault(lt, {})
        arr = bucket.setdefault(fp, [])
        sd = _seenD.setdefault((lt, fp), set())
        if d not in sd:
            sd.add(d)
            arr.append({'d': d, 'v': gp(row, 'VALUE'), 'min': gp(row, 'MIN'), 'max': gp(row, 'MAX'), 'cnt': bc})
    for lt in hist:
        for fp in hist[lt]:
            hist[lt][fp].sort(key=lambda x: u16key(x['d']))

    def H(lt, fp):
        b = hist.get(lt)
        return b.get(K(fp)) if b is not None else None

    curCount = {}
    for row in raw:
        if isKM(jor(gp(row, 'LTCODE'), '')):
            continue
        t = curToken(gp(row, 'VALUE'))
        if t:
            s = curSym(t)
            curCount[s] = (curCount.get(s) or 0) + 1
    curRank = js_cmp_sort([[k, curCount[k]] for k in js_keys(curCount)], lambda a, b: b[1] - a[1])
    CUR = curRank[0][0] if len(curRank) else '$'
    if not len(curRank):
        log('WARNING: no currency marker on any estimate or actual value; amounts are shown in $.')
    elif len(curRank) > 1:
        log('WARNING: estimate/actual values carry more than one currency marker (%s); amounts are shown in %s. Check for a redenominated pull before trusting the sheet.'
            % (', '.join(s + ' x' + js_str(n) for s, n in curRank), CUR))
    meta['currency'] = CUR
    MF = money_fmt(CUR)

    for lt in js_keys(act):
        if not isKM(lt):
            continue
        for fp in js_keys(act[lt]):
            p = kmParts(act[lt][fp])
            if p and not p.get('kind'):
                kmUnit[lt] = {'factor': SUF[p['suf']] if p['suf'] else 1, 'dollar': p['dollar']}
                break

    DAY = 86400000
    printT, prepT = date_parse(S(meta['printDate'])), date_parse(S(meta['prepared']))
    if printT > prepT:
        err('printDate %s is after prepared %s: the %s has not been reported yet. Use the previous quarter as printPeriod (SKILL Step 1, print-day rule).'
            % (PD, S(meta['prepared']), S(jor(mg('printPeriod'), 'print'))))
        exit_(2)
    if printT == prepT:
        anyPost = any(any(not slt(x['d'], PD) for x in hist[lt][fp]) for lt in hist for fp in hist[lt])
        if not anyPost:
            err('printDate %s equals the run date and no consensus snapshot is dated on or after it: the call has not been reflected yet. Use the previous quarter as printPeriod (SKILL Step 1, print-day rule).' % PD)
            exit_(2)
    cuts = {'pre': iso(printT - 1 * DAY)}
    if prepT >= printT + 10 * DAY:
        cuts['t10'] = iso(printT + 10 * DAY)
    if prepT >= printT + 40 * DAY:
        cuts['t40'] = iso(printT + 40 * DAY)
    ANCHORS = list(cuts.keys())

    def atCut(arr, cut):
        best = None
        for r in arr:
            if sle(r['d'], cut):
                best = r
            else:
                break
        return best

    SPLIT_RE = jre(r'^(.*)-(Y|Q|M|BY)$')

    def split(lt):
        m = SPLIT_RE.search(lt)
        return {'root': m.group(1), 'suf': m.group(2)} if m else {'root': lt, 'suf': None}
    IS_P_RE = jre(r'^IS_P_\d+$')

    def IS_OK(lt):
        return bool(IS_P_RE.search(lt)) or lt == 'IS_S_656' or lt == 'IS_S_657'
    BS_KEY = {'BS_P_101', 'BS_P_141', 'BS_P_118', 'BS_P_103', 'BS_P_111', 'BS_P_114', 'BS_P_115', 'BS_P_108', 'BS_P_109', 'BS_P_123',
              'BS_P_250', 'BS_P_251', 'BS_P_252', 'BS_P_254', 'BS_P_276', 'BS_P_278', 'BS_P_262', 'BS_P_113'}
    CF_KEY = {'CF_P_108', 'CF_P_109', 'CF_P_110', 'CF_P_115', 'CF_P_101', 'CF_P_104', 'CF_P_105', 'CF_P_106'}
    REIT_CF = ['CF_P_113', 'CF_P_202', 'CF_P_114', 'CF_P_203']
    if jre(r'\breit', 'i').search(S(jor(mg('subindustry'), ''))):
        CF_KEY = CF_KEY | set(REIT_CF)
    R_KM, R_IS, R_BS, R_CF, R_RT, R_SD = (jre(r'^KM_'), jre(r'^IS_'), jre(r'^BS_'), jre(r'^CF_'), jre(r'^RT_'), jre(r'^SD_'))
    R_SD_RATIO = jre(r'(combined|loss|expense|catastrophe|development|payout)\s+ratio|catastrophe loss|\bAYCR\b|\bAYLR\b', 'i')
    R_SD_POL = jre(r'polic(y|ies)|in force|\(in thousand', 'i')
    R_PCT = jre(r'%')
    R_SSS = jre(r'\bsss\b|same[- ]?store|comparable', 'i')
    R_SD_METRIC = jre(r'\bEBIT(DA)?\b|Margin|%|Per Share', 'i')
    R_SD_REVEND = jre(r'\b(Revenues?|Sales)\s*$', 'i')
    R_SD_BIZ = jre(r'\b(Units?|Volumes?|Growth|Income|Expenses?|Opex|ASPs?|Headcount|Capex)\b', 'i')
    _sec_cache = {}

    def sectionOf(lt):
        if lt in _sec_cache:
            return _sec_cache[lt]
        r = _sectionOf(lt)
        _sec_cache[lt] = r
        return r

    def _sectionOf(lt):
        nm = S(jor(name.get(lt, UNDEF), ''))
        if R_KM.search(lt):
            return 'kpi'
        if R_IS.search(lt):
            return 'is' if IS_OK(lt) else None
        if R_BS.search(lt):
            return 'bs' if lt in BS_KEY else None
        if R_CF.search(lt):
            return 'cf' if lt in CF_KEY else None
        if R_RT.search(lt):
            return None
        if R_SD.search(lt):
            if R_SD_RATIO.search(nm):
                return 'kpi'
            if R_SD_POL.search(nm):
                return 'kpi'
            if R_PCT.search(nm) and R_SSS.search(nm):
                return 'kpi'
            if R_SD_METRIC.search(nm):
                return None
            if not R_SD_REVEND.search(nm) and R_SD_BIZ.search(nm):
                return None
            return 'seg'
        return None
    CL = [jre(r'\s*-\s*Consolidated\b', 'i'), jre(r'\s*\(Ex\. ?1x items\)', 'i'), jre(r'\s*\(Ex-1x Items\)', 'i'), jre(r'\s*\(Adjusted\)', 'i'),
          jre(r'\s*\(Ex\s*-\s*1x Items?\)', 'i'), jre(r'\bTotal Remaining Performance Obligations\s*\(RPO\)', 'i'),
          jre(r'\bRemaining Performance Obligations\s*\(RPO\)', 'i'), jre(r'\(Ex-Operating Lease\)', 'i'), jre(r'\(Incl-Operating Lease\)', 'i'),
          jre(r'^Net Income \(Loss\) from Continuing Operations$', 'i'), jre(r'\band\b'), jre(r'\s*-\s*(Adjusted\s+)?Revenues?$', 'i'),
          jre(r'^(revenues?|sales|segments?|geographic|geography|regions?|regional|products?|divisions?|channels?|business(es)?|total|by|data|other)$', 'i'),
          jre(r'^(.+?)\s*-\s*(.+?)\s+(?:Adjusted\s+)?Revenues?$', 'i'), jre(r'\S\s+(?:Adjusted\s+)?Revenues?$', 'i'),
          jre(r'\s+(?:Adjusted\s+)?Revenues?$', 'i'), jre(r'\s*-\s*Opex$', 'i')]
    _cl_cache = {}
    SP2 = jre(r'\s{2,}')

    def cleanLabel(lt):
        if lt in _cl_cache:
            return _cl_cache[lt]
        r = _cleanLabel(lt)
        _cl_cache[lt] = r
        return r

    def _cleanLabel(lt):
        s = jor(name.get(lt, UNDEF), lt)
        if not isinstance(s, str):
            raise TypeError('s.replace is not a function')
        s = rsub(SP2, s, ' ', 0)
        s = rsub(CL[0], s, '')
        s = rsub(CL[3], rsub(CL[2], rsub(CL[1], s, ' (Ex-1x)'), ' (Ex-1x)'), '')
        s = rsub(CL[4], s, ' (Ex-1x)')
        s = rsub(CL[6], rsub(CL[5], s, 'Total RPO'), 'RPO')
        s = rsub(CL[8], rsub(CL[7], s, '(Ex-Op Lease)'), '(Incl-Op Lease)')
        s = rsub(CL[9], s, 'Net Income')
        s = rsub(CL[10], s, '&', 0)
        if sectionOf(lt) == 'seg':
            s = rsub(CL[11], s, '')
            GENERIC_HEAD = CL[12]
            m = CL[13].search(s)
            if m:
                head, qual = jtrim(m.group(1)), jtrim(m.group(2))
                s = qual if GENERIC_HEAD.search(head) else '%s (%s)' % (head, qual)
            elif CL[14].search(s):
                s = rsub(CL[15], s, '')
        s = rsub(CL[16], s, '')
        return jtrim(s)

    qFp = meta['q']

    def primaryFp(lt):
        a = H(lt, qFp)
        if a:
            return qFp
        b = H(lt, mg('fy1'))
        return meta['fy1'] if b else None
    buckets = {'seg': [], 'is': [], 'kpi': [], 'bs': [], 'cf': []}
    segNotes = []
    segFlow = []
    excluded = []
    for lt in js_keys(hist):
        if split(lt)['suf']:
            continue
        if not _truthy(primaryFp(lt)):
            continue
        sec = sectionOf(lt)
        if sec:
            buckets[sec].append(lt)

    def _segment_block():
        seg = buckets['seg']

        def estQ(lt):
            a = H(lt, qFp)
            return pnum(lt, a[-1]['v']) if a else None

        def actLocal(lt, fp):
            o = act.get(lt)
            if o is None:
                return None
            v = o.get(K(fp), UNDEF)
            return pnum(lt, v) if not nil(v) else None
        _pv = [{}, {}, {}]

        def _mk(i, f):
            def g(lt):
                c = _pv[i]
                if lt not in c:
                    c[lt] = f(lt)
                return c[lt]
            return g
        perVals = [
            _mk(0, lambda lt: estQ(lt)),
            _mk(1, lambda lt: actLocal(lt, meta['printPeriod'])),
            _mk(2, lambda lt: actLocal(lt, meta['yoyQ'])),
        ]
        topVals = [f('IS_P_101') for f in perVals]

        def isWhole(lt):
            n = 0
            for i in range(len(perVals)):
                t, v = topVals[i], perVals[i](lt)
                if nil(t) or not _truthy(t) or nil(v):
                    continue
                if jdiv(abs(v - t), abs(t)) > 0.01:
                    return False
                n += 1
            return n > 0
        TOTREV = jre(r'^total revenue', 'i')
        totalDup = [lt for lt in seg if TOTREV.search(cleanLabel(lt)) or isWhole(lt)]
        for lt in totalDup:
            if not TOTREV.search(cleanLabel(lt)):
                log('Segment roll-up filter: "%s" (%s) equals Total Revenue in every period; dropped as a total duplicate.' % (cleanLabel(lt), lt))
        pool = [lt for lt in seg if lt not in totalDup]
        TOP_LADDER = ['IS_P_101', 'IS_P_403', 'IS_P_402', 'IS_P_200']
        totals = [f('IS_P_101') for f in perVals]
        TOL = 0.02

        def needPeriods():
            return min(2, len([t for t in totals if not nil(t) and _truthy(t)]))

        def rollErr(subset):
            checked, errSum = 0, 0
            for i in range(len(perVals)):
                t = totals[i]
                if nil(t) or not _truthy(t):
                    continue
                vals = [perVals[i](lt) for lt in subset]
                if any(nil(v) for v in vals):
                    continue
                checked += 1
                s = jsum(vals)
                e = jdiv(abs(s - t), abs(t))
                if e > TOL:
                    return None
                errSum += e
            return jdiv(errSum, checked) if (checked >= needPeriods() and checked) else None
        TIGHT = 0.012

        totRef = UNDEF
        for v in totals:
            if not nil(v) and _truthy(v):
                totRef = v
                break

        def matCnt(sub):
            tr = totRef
            if not _truthy(tr):
                return len(sub)
            n = 0
            for lt in sub:
                v = UNDEF
                for f in perVals:
                    x = f(lt)
                    if not nil(x):
                        v = x
                        break
                if not nil(v) and jdiv(abs(v), abs(tr)) >= 0.03:
                    n += 1
            return n
        DASH = jre(r' - ')

        def famKeys(lt):
            L = cleanLabel(lt)
            if not DASH.search(L):
                return []
            p = [jtrim(x).lower() for x in L.split(' - ')]
            return ['h:' + p[0], 't:' + p[-1]]

        def famMasks(group):
            fams = {}
            for i, lt in enumerate(group):
                for k in famKeys(lt):
                    fams.setdefault(k, []).append(i)
            out = []
            for k in fams:
                F = fams[k]
                if len(F) < 2:
                    continue
                fm = 0
                for i in F:
                    fm |= (1 << i)
                out.append((fm, len(F), F))
            return out

        def siblingOK(fmasks, mask):
            for fm, ln, F in fmasks:
                n = sum(1 for i in F if mask & (1 << i))
                if n and n != ln:
                    return False
            return True

        def depthOfLt(lt):
            a = H(lt, qFp)
            r = a[-1] if a else None
            return jnum(r['cnt']) if (r and not nil(r['cnt'])) else 0

        def bestSubset(group):
            if not len(group):
                return None
            whole = rollErr(group)
            if whole is not None:
                return group
            if len(group) > 16:
                return None
            cands = []
            fmasks = famMasks(group)
            for mask in range(1, 1 << len(group)):
                sub = [lt for i, lt in enumerate(group) if mask & (1 << i)]
                if len(sub) < 2 or not siblingOK(fmasks, mask):
                    continue
                e = rollErr(sub)
                if e is None:
                    continue
                cands.append({'sub': sub, 'e': e, 'm': matCnt(sub)})
            if not len(cands):
                return None
            tight = [c for c in cands if c['e'] <= TIGHT]
            pool2 = tight if len(tight) else cands

            def minDepth(sub):
                return jmin(*[depthOfLt(lt) for lt in sub])
            SDP = jre(r'^SD_P_')

            def nPri(sub):
                return len([lt for lt in sub if SDP.search(lt)])
            if len(tight):
                cmp = lambda a, b: jor(b['m'] - a['m'], a['e'] - b['e'], nPri(b['sub']) - nPri(a['sub']), minDepth(b['sub']) - minDepth(a['sub']), len(a['sub']) - len(b['sub']))
            else:
                cmp = lambda a, b: jor(a['e'] - b['e'], b['m'] - a['m'], nPri(b['sub']) - nPri(a['sub']), minDepth(b['sub']) - minDepth(a['sub']))
            pool2 = js_cmp_sort(pool2, cmp)
            return pool2[0]['sub']
        if True:
            depth = depthOfLt

            def sig(lt):
                return '|'.join('x' if nil(f(lt)) else tf(f(lt), 3) for f in perVals)
            SDP = jre(r'^SD_P_')
            ranked = js_cmp_sort(pool, lambda a, b: jor((0 if SDP.search(a) else 1) - (0 if SDP.search(b) else 1), depth(b) - depth(a)))
            seen, keep = {}, []
            XB = jre(r'[x|]')
            for lt in ranked:
                k = sig(lt)
                if rsub(XB, k, '', 0) == '':
                    keep.append(lt)
                    continue
                if _truthy(seen.get(k)):
                    log('Segment dedupe: "%s" (%s) carries the same values as "%s" (%s) in every period; dropped as a value-duplicate.'
                        % (cleanLabel(lt), lt, cleanLabel(seen[k]), seen[k]))
                    continue
                seen[k] = lt
                keep.append(lt)
            pool = [lt for lt in pool if lt in keep]
        fullPool = list(pool)
        GEO_WORDS = {'us', 'usa', 'u.s.', 'u.s.a.', 'u.s', 'united', 'states', 'domestic', 'international', 'intl', 'america', 'americas', 'north', 'south', 'central', 'latin', 'latam', 'emea', 'europe', 'european', 'middle', 'east', 'west', 'africa', 'african', 'asia', 'asian', 'apac', 'pacific', 'japan', 'china', 'greater', 'mainland', 'hong', 'kong', 'taiwan', 'india', 'canada', 'canadian', 'uk', 'u.k.', 'britain', 'world', 'worldwide', 'foreign', 'overseas', 'germany', 'france', 'italy', 'spain', 'korea', 'australia', 'brazil', 'mexico', 'row', 'oceania', 'nordics', 'benelux', 'dach', 'cee', 'mea', 'singapore'}
        GEO_FILL = {'rest', 'of', 'the', 'other', 'others', 'markets', 'market', 'region', 'regions', 'geography', 'geographic', 'segment', 'segments', 'area', 'areas', 'zone', 'zones', 'country', 'countries', 'total', 'all', 'and', 'ex', 'non', 'excluding', 'incl', 'including', 'revenue', 'revenues', 'sales', 'net'}
        GEO_SPLIT = jre(r'[\s,\/&()\-–]+')

        def geoLabel(s):
            toks = [t for t in GEO_SPLIT.split(S(jor(s, '')).lower()) if t]
            if not len(toks):
                return False
            geo = 0
            for t in toks:
                if t in GEO_WORDS:
                    geo += 1
                elif t not in GEO_FILL:
                    return False
            return geo > 0
        GEO_LT = jre(r'Geo', 'i')

        def isGeo(lt):
            return bool(GEO_LT.search(lt)) or geoLabel(cleanLabel(lt))
        HIST_TOL, HIST_MIN, HIST_N, HIST_MAXK = 0.001, 4, 8, 15
        HIST_BUDGET = int(os.environ.get('REV_SEG_BUDGET') or 2000000)
        HIST_QRX = jre(r'^([1-4])Q(\d{2})$')

        def histV(lt, fp):
            for src in (actR, act):
                o = src.get(lt)
                v = o.get(fp, UNDEF) if o is not None else UNDEF
                if not nil(v):
                    return pnum(lt, v)
            return None

        def exactSplit():
            m0 = HIST_QRX.search(S(meta['printPeriod']))
            if not m0:
                return None
            q, y, qs = int(m0.group(1)), int(m0.group(2)), []
            for _ in range(HIST_N):
                qs.append('%dQ%02d' % (q, y))
                q -= 1
                if q == 0:
                    q, y = 4, y - 1
            tot = {fp: histV('IS_P_101', fp) for fp in qs}
            qs = [fp for fp in qs if tot[fp] is not None and tot[fp] > 0]
            tq = estQ('IS_P_101')
            if len(qs) < HIST_MIN or tq is None or tq <= 0:
                return None
            rows = []
            for lt in fullPool:
                c = estQ(lt)
                if c is None:
                    continue
                hv = [histV(lt, fp) for fp in qs]
                if sum(1 for v in hv if v is not None) >= HIST_MIN:
                    rows.append((lt, c, hv))
            if len(rows) < 2:
                return None
            rows.sort(key=lambda r: -abs(r[1]))
            n, P = len(rows), len(qs)
            lo, hi = tq * (1 - TOL), tq * (1 + TOL)
            sufPos, sufNeg = [0.0] * (n + 1), [0.0] * (n + 1)
            for i in range(n - 1, -1, -1):
                sufPos[i] = sufPos[i + 1] + max(rows[i][1], 0)
                sufNeg[i] = sufNeg[i + 1] + min(rows[i][1], 0)
            fm = famMasks([r[0] for r in rows])
            cands, chosen = [], []
            sums, miss = [0.0] * P, [0] * P
            left = [HIST_BUDGET]

            def visit(i, cs, mask):
                left[0] -= 1
                if left[0] < 0:
                    return
                if len(chosen) >= 2 and lo <= cs <= hi:
                    judged = [k for k in range(P) if miss[k] == 0]
                    if len(judged) >= HIST_MIN:
                        worst = max(abs(sums[k] - tot[qs[k]]) / tot[qs[k]] for k in judged)
                        if worst <= HIST_TOL and siblingOK(fm, mask):
                            cands.append({'sub': [rows[j][0] for j in chosen], 'worst': worst, 'qe': abs(cs - tq) / tq, 'nq': len(judged)})
                if i == n or len(chosen) >= HIST_MAXK:
                    return
                if cs + sufNeg[i] > hi or cs + sufPos[i] < lo:
                    return
                lt_, c_, hv_ = rows[i]
                chosen.append(i)
                for k in range(P):
                    if hv_[k] is None:
                        miss[k] += 1
                    else:
                        sums[k] += hv_[k]
                visit(i + 1, cs + c_, mask | (1 << i))
                for k in range(P):
                    if hv_[k] is None:
                        miss[k] -= 1
                    else:
                        sums[k] -= hv_[k]
                chosen.pop()
                visit(i + 1, cs, mask)
            visit(0, 0.0, 0)
            if left[0] < 0:
                log('Segment roll-up filter: the reported-history check stopped at its search limit (%d candidate rows); using the consensus roll-up instead.' % n)
                return None
            if not cands:
                return None
            SDPH = jre(r'^SD_P_')

            def gtype(sub):
                g = [isGeo(lt) for lt in sub]
                return 1 if all(g) else (0 if not any(g) else 2)

            def key(c):
                sub = c['sub']
                return {'m': matCnt(sub), 'g': gtype(sub), 'p': sum(1 for lt in sub if SDPH.search(lt)),
                        'd': min(depthOfLt(lt) for lt in sub), 'n': len(sub)}
            for c in cands:
                c['k'] = key(c)

            def cmp(a, b):
                ka, kb = a['k'], b['k']
                if ka['m'] != kb['m']:
                    return kb['m'] - ka['m']
                if abs(a['worst'] - b['worst']) > 0.00005:
                    return -1 if a['worst'] < b['worst'] else 1
                if ka['g'] != kb['g']:
                    return ka['g'] - kb['g']
                for f, sign in (('d', -1), ('p', -1)):
                    if ka[f] != kb[f]:
                        return sign * (ka[f] - kb[f])
                if abs(a['qe'] - b['qe']) > 1e-12:
                    return -1 if a['qe'] < b['qe'] else 1
                return ka['n'] - kb['n']
            import functools
            cands.sort(key=functools.cmp_to_key(cmp))
            best = cands[0]
            best['qs'] = qs
            return best
        t0 = exactSplit()
        if t0:
            pick = t0['sub']
            log('Segment roll-up filter: the reported split adds up to Total Revenue in every one of %d reported quarters (worst gap %s%%) and within %s%% on consensus -> %s'
                % (t0['nq'], tf(t0['worst'] * 100, 2), tf(t0['qe'] * 100, 2), ', '.join(cleanLabel(x) for x in pick)))
            segFlow.append('Segment Revenue rows: the split that adds up to Total Revenue in %d reported quarters (%s to %s; worst gap %s%%), chosen before the consensus roll-up.'
                           % (t0['nq'], t0['qs'][-1], t0['qs'][0], tf(t0['worst'] * 100, 2)))
            dropped = [lt for lt in seg if lt not in pick]
            if len(dropped):
                log('Segment roll-up filter: dropped ' + js_str(len(dropped)) + ' row(s) not rolling to Total Revenue -> ' + ', '.join(cleanLabel(x) for x in dropped))
            buckets['seg'] = pick
            return
        CAP = 16
        depthQ = depthOfLt
        SDP2 = jre(r'^SD_P_')

        def capGroup(group, what):
            if len(group) <= CAP:
                return group

            def pri(lt):
                return 0 if SDP2.search(lt) else 1
            ranked = js_cmp_sort(group, lambda a, b: jor(pri(a) - pri(b), depthQ(b) - depthQ(a), group.index(a) - group.index(b)))
            keep = ranked[:CAP]
            cut = [lt for lt in group if lt not in keep]
            log('Segment roll-up filter: %d %s candidates exceed the %d-row search limit; keeping the primary series and the deepest-covered rows, setting aside -> %s'
                % (len(group), what, CAP, ', '.join(cleanLabel(x) for x in cut)))
            return [lt for lt in group if lt in keep]
        bizGroup = capGroup([lt for lt in pool if not isGeo(lt)], 'business')
        geoGroup = capGroup([lt for lt in pool if isGeo(lt)], 'geography')
        pool = [lt for lt in pool if lt in bizGroup or lt in geoGroup]
        MEASURE_FAM = jre(r'premium|revenue|income|fees|sales|losses', 'i')

        def famOf(lt):
            return jtrim(S(cleanLabel(lt)).split(' - ')[0]).lower()
        fams = {}
        for lt in bizGroup:
            f = famOf(lt)
            if MEASURE_FAM.search(f):
                fams.setdefault(f, []).append(lt)
        famGroups = [fams[k] for k in js_keys(fams) if len(fams[k]) >= 2]
        COVER_MIN = 0.90

        def coverSubset(group):
            if not len(group) or len(group) > 16:
                return None
            best = None
            fmasks = famMasks(group)
            for mask in range(1, 1 << len(group)):
                sub = [lt for i, lt in enumerate(group) if mask & (1 << i)]
                if len(sub) < 2 or not siblingOK(fmasks, mask):
                    continue
                checked, covSum, ok = 0, 0, True
                for i in range(len(perVals)):
                    t = totals[i]
                    if nil(t) or not _truthy(t):
                        continue
                    vals = [perVals[i](lt) for lt in sub]
                    if any(nil(v) for v in vals):
                        continue
                    s = jsum(vals)
                    if s > t * (1 + TOL):
                        ok = False
                        break
                    checked += 1
                    covSum += jdiv(s, t)
                if not ok or not checked or checked < needPeriods():
                    continue
                cov = jdiv(covSum, checked)
                if cov < COVER_MIN:
                    continue
                primaryOnly = 1 if all(SDP2.search(lt) for lt in sub) else 0
                minDepth = jmin(*[depthOfLt(lt) for lt in sub])
                m = matCnt(sub)
                better = (best is None or primaryOnly > best['primaryOnly']
                          or (primaryOnly == best['primaryOnly'] and (minDepth > best['minDepth']
                              or (minDepth == best['minDepth'] and (cov > best['cov'] + 1e-9 or (abs(cov - best['cov']) <= 1e-9 and m > best['m']))))))
                if better:
                    best = {'sub': sub, 'cov': cov, 'm': m, 'primaryOnly': primaryOnly, 'minDepth': minDepth}
            return best
        famPick = bizPick = coverPick = geoPick = mixPick = mixCover = None
        mixed = len(bizGroup) and len(geoGroup)
        for cand in TOP_LADDER:
            totals = [f(cand) for f in perVals]
            if not any(not nil(t) and _truthy(t) for t in totals):
                continue
            for g in famGroups:
                famPick = bestSubset(g)
                if famPick:
                    break
            if famPick:
                break
            if not bizPick and not len(famGroups):
                bizPick = bestSubset(bizGroup)
            if not bizPick and not coverPick and not len(famGroups):
                coverPick = coverSubset(bizGroup)
            if not geoPick:
                geoPick = bestSubset(geoGroup)
            if mixed and not len(famGroups) and not bizPick and not coverPick and not geoPick and not mixPick:
                mixPick = bestSubset(pool)
            if mixed and not len(famGroups) and not bizPick and not coverPick and not geoPick and not mixPick and not mixCover:
                mixCover = coverSubset(pool)
        pick = famPick
        if not pick and len(famGroups):
            def depthOf(g):
                t = 0
                for lt in g:
                    a = H(lt, qFp)
                    r = a[-1] if a else None
                    t = jadd(t, r['cnt'] if (r and _truthy(r['cnt'])) else 1)
                return t
            bestFam = js_cmp_sort(famGroups, lambda x, y: N(depthOf(y)) - N(depthOf(x)))[0]
            log('Segment roll-up filter: no basis-homogeneous subset reconciles; keeping the deepest family unfiltered (' + ', '.join(cleanLabel(x) for x in bestFam) + ')')
            pick = bestFam
        if not pick:
            pick = bizPick
        if not pick and coverPick:
            pick = coverPick['sub']
            msg = ('Segment roll-up filter: no business subset reconciles to Total Revenue (the published split is missing a component); keeping the deepest non-overlapping business split, covering %s%% of revenue -> %s'
                   % (tf(coverPick['cov'] * 100, 1), ', '.join(cleanLabel(x) for x in pick)))
            log(msg)
            segNotes.append('Segment Revenue rows cover %s%% of Total Revenue; the remainder is a component the estimate feed does not publish.' % tf(coverPick['cov'] * 100, 1))
        if not pick:
            pick = geoPick
        if not pick and mixPick:
            pick = mixPick
            log('Segment roll-up filter: neither the business rows nor the geography rows reconcile on their own; the reported split mixes both and reconciles as a whole -> ' + ', '.join(cleanLabel(x) for x in pick))
        if not pick and mixCover:
            pick = mixCover['sub']
            log('Segment roll-up filter: no subset reconciles to Total Revenue; keeping the deepest non-overlapping mixed business + geography split, covering %s%% of revenue -> %s'
                % (tf(mixCover['cov'] * 100, 1), ', '.join(cleanLabel(x) for x in pick)))
            segNotes.append('Segment Revenue rows cover %s%% of Total Revenue; the remainder is a component the estimate feed does not publish.' % tf(mixCover['cov'] * 100, 1))
        if pick:
            dropped = [lt for lt in seg if lt not in pick]
            if len(dropped):
                log('Segment roll-up filter: dropped ' + js_str(len(dropped)) + ' row(s) not rolling to Total Revenue -> ' + ', '.join(cleanLabel(x) for x in dropped))
            buckets['seg'] = pick
        elif len(pool):
            log('Segment roll-up filter: WARNING - no group reconciles to a top-line ladder target; keeping business-segment rows unfiltered' + (' (total-duplicate rows still dropped)' if len(totalDup) else ''))
            buckets['seg'] = bizGroup if len(bizGroup) else pool
    _segment_block()

    for sec in ['seg', 'kpi']:
        if len(buckets[sec]) > 15:
            def cntOf(lt):
                fp = primaryFp(lt)
                arr = H(lt, fp)
                return jor(arr[-1]['cnt'], 0)
            srt = js_cmp_sort(buckets[sec], lambda a, b: jnum(cntOf(b)) - jnum(cntOf(a)))
            keep = srt[:15]
            dropped = [lt for lt in buckets[sec] if lt not in keep]
            buckets[sec] = [lt for lt in buckets[sec] if lt in keep]
            log('Trimmed %s bucket to 15 rows by broker count; dropped: %s' % (sec.upper(), ', '.join(cleanLabel(x) for x in dropped)))
    PRI = {
        'is': ['IS_P_101', 'IS_P_102', 'IS_P_200',
               'IS_P_402', 'IS_P_403',
               'IS_P_201', 'IS_P_202', 'IS_P_211', 'IS_P_212',
               'IS_P_410', 'IS_P_443',
               'IS_P_103', 'IS_P_104', 'IS_P_105', 'IS_P_106', 'IS_P_107', 'IS_P_108', 'IS_P_113', 'IS_P_114', 'IS_P_109', 'IS_P_110',
               'IS_P_207', 'IS_P_209', 'IS_P_210', 'IS_P_406',
               'IS_P_141', 'IS_P_142', 'IS_P_157', 'IS_P_158', 'IS_P_1003', 'IS_P_1004', 'IS_P_115', 'IS_P_116', 'IS_P_117', 'IS_P_118', 'IS_P_119', 'IS_P_120', 'IS_P_121', 'IS_P_122', 'IS_P_269', 'IS_P_123', 'IS_P_124', 'IS_S_656', 'IS_P_250', 'IS_P_125', 'IS_P_126', 'IS_P_251', 'IS_P_252', 'IS_P_127', 'IS_P_128', 'IS_S_657'],
        'seg': [], 'kpi': [], 'bs': ['BS_P_101', 'BS_P_141', 'BS_P_118', 'BS_P_250', 'BS_P_251', 'BS_P_252', 'BS_P_103', 'BS_P_111', 'BS_P_113', 'BS_P_114', 'BS_P_115', 'BS_P_108', 'BS_P_109', 'BS_P_254', 'BS_P_276', 'BS_P_278', 'BS_P_262', 'BS_P_123'],
        'cf': REIT_CF + ['CF_P_108', 'CF_P_106', 'CF_P_109', 'CF_P_110', 'CF_P_115', 'CF_P_101', 'CF_P_104', 'CF_P_105'],
    }
    NUM_IN = jre(r'(\d+(?:\.\d+)?)')
    SDS = jre(r'^SD_S_')

    def rank(sec, lt):
        i = PRI[sec].index(lt) if (sec in PRI and lt in PRI[sec]) else -1
        if i >= 0:
            return i
        m = NUM_IN.search(lt)
        sSeries = 1e6 if (sec == 'seg' and SDS.search(lt)) else 0
        return 1000 + sSeries + (jpf(m.group(1)) if m else 0)

    if True:
        TWIN_ONLY = {'IS_P_102'}
        before = list(buckets['is'])
        buckets['is'] = [lt for lt in buckets['is'] if lt in PRI['is'] or lt in TWIN_ONLY]
        droppedIS = [lt for lt in before if lt not in buckets['is']]
        if len(droppedIS):
            log('Statement spine guard: dropped %d unregistered IS row(s) (no position in the statement) -> %s' % (len(droppedIS), ', '.join(cleanLabel(x) for x in droppedIS)))

    K_TRPO = jre(r'total remaining performance obligations\s*\(rpo\)', 'i')
    K_RPO = jre(r'remaining performance obligations\s*\(rpo\)', 'i')
    K_BOOK = jre(r'^book\s*[\/-]?\s*(to\s*)?[\/-]?\s*bill', 'i')
    MODS = [[jre(r'^total\s+', 'i'), 0], [jre(r'^current\s+', 'i'), 1], [jre(r'^non[\s-]*current\s+', 'i'), 2], [jre(r'^change in\s+', 'i'), 3],
            [jre(r'^net adds\s*-?\s*', 'i'), 3], [jre(r'^net new\s+', 'i'), 3], [jre(r'^average\s+', 'i'), 3]]

    def kpiOrder(lts):
        def cntOf(lt):
            fp = primaryFp(lt)
            arr = H(lt, fp)
            c = arr[-1]['cnt']
            return jnum(c) if not nil(c) else 0
        info = []
        for lt in lts:
            nm = cleanLabel(lt)
            s = rsub(K_RPO, rsub(K_TRPO, nm, 'Total RPO'), 'RPO')
            if K_BOOK.search(s):
                info.append({'lt': lt, 'stem': 'BOOKINGS', 'mod': 4, 'cnt': cntOf(lt), 'nm': nm})
                continue
            mod = 0.5
            for rx, r in MODS:
                if rx.search(s):
                    mod = r
                    s = rsub(rx, s, '')
                    break
            info.append({'lt': lt, 'stem': jtrim(s).upper(), 'mod': mod, 'cnt': cntOf(lt), 'nm': nm})
        stems = list(dict.fromkeys(i['stem'] for i in info))
        for i in info:
            for st in stems:
                if st != i['stem'] and i['stem'].startswith(st + ' '):
                    i['stem'] = st
                    i['mod'] = jmax(i['mod'], 5)
                    break
        famMax = {}
        for i in info:
            famMax[i['stem']] = jmax(jor(famMax.get(i['stem']), 0), i['cnt'])
        info = js_cmp_sort(info, lambda a, b: jor(famMax[b['stem']] - famMax[a['stem']],
                                                  scmp(a['stem'], b['stem']),
                                                  a['mod'] - b['mod'], b['cnt'] - a['cnt'], -1 if slt(a['nm'], b['nm']) else 1))
        return [i['lt'] for i in info]

    def actOf(lt, fp):
        o = act.get(lt)
        if o is None:
            return None
        v = o.get(K(fp), UNDEF)
        return pnum(lt, v) if not nil(v) else None

    def grow(cur, base):
        return (jdiv(cur, base) - 1) if (not nil(cur) and not nil(base) and base > 1e-9 and cur >= 0) else None
    NOGROW_RE = jre(r'^IS_P_(115|116|1003|1004)$')

    def periodData(lt, fp, key, ctx):
        arr = H(lt, fp)
        if not arr:
            return None
        last = arr[-1]
        a = {}
        a['cur'] = pnum(lt, last['v'])
        a['curD'] = last['d']
        for k in ANCHORS:
            r = atCut(arr, cuts[k])
            a[k] = pnum(lt, r['v']) if r else None
            a[k + 'D'] = r['d'] if r else None
            if k == 'pre':
                a['preCnt'] = jnum(r['cnt']) if (r and not nil(r['cnt'])) else None
        a['low'] = pnum(lt, last['min'])
        a['high'] = pnum(lt, last['max'])
        a['cnt'] = jnum(last['cnt']) if not nil(last['cnt']) else None
        a['stale'] = not (not slt(last['d'], PD))
        if key == 'q':
            a['actPrint'] = actOf(lt, meta['printPeriod'])
            a['actYoy'] = actOf(lt, meta['yoyQ'])
        elif key == 'fy1':
            a['actFy0'] = actOf(lt, mg('fy0'))
        isPctRow = pkind(lt, last['v']) == 'pct' or pkind(lt, last['v']) == 'ratio'
        noGrowth = bool(NOGROW_RE.search(lt))
        if isPctRow or noGrowth:
            a['yoy'] = None
            a['seq'] = None
            if noGrowth and lt not in noGrowthNoted:
                noGrowthNoted.add(lt)
                flowNotes.append('%s: no YoY/Seq growth shown (net interest line; its sign convention varies across periods, so a growth rate is not meaningful).' % cleanLabel(lt))
        elif key == 'q':
            a['seq'] = grow(a['cur'], actOf(lt, meta['printPeriod']))
            a['yoy'] = grow(a['cur'], actOf(lt, meta['yoyQ']))
        elif key == 'fy1':
            a['yoy'] = grow(a['cur'], actOf(lt, mg('fy0')))
            a['seq'] = None
        elif key == 'fy2':
            a['yoy'] = grow(a['cur'], ctx['fy1']['cur'] if (ctx and ctx.get('fy1')) else None)
            a['seq'] = None
        if pkind(lt, last['v']) == 'money' and key != 'fy2':
            def revBase(p):
                v = actOf('IS_P_101', p)
                return v if not nil(v) else actOf('IS_P_102', p)
            for g, bp in [[a.get('yoy', UNDEF), meta['yoyQ'] if key == 'q' else mg('fy0')], [a.get('seq', UNDEF), meta['printPeriod'] if key == 'q' else None]]:
                if nil(g) or nil(bp) or abs(g) <= 3:
                    continue
                base, rb = actOf(lt, bp), revBase(bp)
                if not nil(base) and not nil(rb) and rb > 0 and abs(base) < 0.01 * rb:
                    msg = '%s (%s): growth of %s%s%% is measured on a base of %s (under 1%% of revenue); read it with caution.' % (
                        cleanLabel(lt), S(meta['q']) if key == 'q' else 'FY' + S(mg('fy1')), '+' if g > 0 else '', js_str(js_round(g * 100)), tf(base, 1))
                    log('WARNING: ' + msg)
                    scaleNotes.append(msg)
        return a
    noGrowthNoted = set()
    SEC_TITLES = {'seg': 'Segment Revenue', 'is': 'Income Statement', 'kpi': 'Key Metrics (KPIs)', 'bs': 'Balance Sheet', 'cf': 'Cash Flow'}
    sections = []
    scaleNotes, flowNotes = list(segNotes), list(segFlow)
    total = 0
    ADJREV = jre(r'adjusted revenue', 'i')
    for sec in ['is', 'seg', 'kpi', 'bs', 'cf']:
        if sec == 'kpi':
            lts = kpiOrder(buckets[sec])
        else:
            buckets[sec][:] = js_cmp_sort(buckets[sec], lambda a, b, sec=sec: rank(sec, a) - rank(sec, b))
            lts = buckets[sec]
        items = []
        for lt in lts:
            fpQ = qFp if H(lt, qFp) else None
            kSrc = H(lt, fpQ if _truthy(fpQ) else primaryFp(lt))
            kind = jor(pkind(lt, kSrc[-1]['v']), 'money')
            it = {'lt': lt, 'label': cleanLabel(lt), 'kind': kind, 'isPct': kind == 'pct', 'per': {}}
            for key, fp in PERIODS:
                pd = periodData(lt, fp, key, it['per'])
                if pd:
                    it['per'][key] = pd
            if isKM(lt):
                est = it['per']['q']['cur'] if it['per'].get('q') else None
                ref = actOf(lt, meta['printPeriod']) if not nil(actOf(lt, meta['printPeriod'])) else actOf(lt, meta['yoyQ'])
                if not nil(est) and not nil(ref) and abs(ref) > 1e-9 and abs(jdiv(est, ref)) >= 100:
                    scaleNotes.append('%s: estimate feed unit could not be reconciled with the reported actual (shown verbatim; treat the level with caution)' % it['label'])
            if len(it['per']):
                items.append(it)
                total += 1
        if len(items):
            title = SEC_TITLES[sec]
            if sec == 'seg':
                adj = len([it for it in items if ADJREV.search(S(jor(name.get(it['lt'], UNDEF), '')))])
                if adj and adj >= len(items) / 2:
                    title += ' (Adjusted)'
            sections.append({'key': sec, 'title': title, 'items': items})

    if True:
        DROP = {'IS_P_250', 'IS_P_251', 'IS_P_252', 'CF_P_115', 'BS_P_201'}
        PAIRS = [
            {'g': 'IS_P_101', 'a': ['IS_P_102', 'IS_P_200'], 'dual': False},
            {'g': 'IS_P_201', 'a': ['IS_P_202'], 'dual': False},
            {'g': 'IS_P_211', 'a': ['IS_P_212'], 'dual': False},
            {'g': 'IS_P_209', 'a': ['IS_P_210'], 'dual': False},
            {'g': 'IS_P_103', 'a': ['IS_P_104'], 'dual': False},
            {'g': 'IS_P_105', 'a': ['IS_P_106'], 'dual': False},
            {'g': 'IS_P_107', 'a': ['IS_P_108'], 'dual': False},
            {'g': 'IS_P_109', 'a': ['IS_P_110'], 'dual': True},
            {'g': 'IS_P_113', 'a': ['IS_P_114'], 'dual': True},
            {'g': 'IS_P_115', 'a': ['IS_P_116'], 'dual': False},
            {'g': 'IS_P_1003', 'a': ['IS_P_1004'], 'dual': False},
            {'g': 'IS_P_141', 'a': ['IS_P_142'], 'dual': False},
            {'g': 'IS_P_157', 'a': ['IS_P_158'], 'dual': False},
            {'g': 'IS_P_117', 'a': ['IS_P_118'], 'dual': False},
            {'g': 'IS_P_119', 'a': ['IS_P_120'], 'dual': False},
            {'g': 'IS_P_121', 'a': ['IS_P_122'], 'dual': False},
            {'g': 'IS_P_123', 'a': ['IS_P_124', 'IS_S_656'], 'dual': True},
            {'g': 'IS_P_125', 'a': ['IS_P_126'], 'dual': False},
            {'g': 'IS_P_127', 'a': ['IS_P_128', 'IS_S_657'], 'dual': True},
        ]
        GAAP_RE = jre(r'\(GAAP\)', 'i')
        GAAP_END = jre(r' \(GAAP\)$')
        for sec in sections:
            sec['items'] = [it for it in sec['items'] if it['lt'] not in DROP]
            if sec['key'] != 'is':
                continue
            byLt = {}
            for it in sec['items']:
                byLt[it['lt']] = it
            remove = set()

            def cnt(it):
                return it['per']['q']['cnt'] if (it and it['per'].get('q') and not nil(it['per']['q']['cnt'])) else 0
            pairInfo = []
            for p in PAIRS:
                cands = js_cmp_sort([byLt[lt] for lt in p['a'] if byLt.get(lt)], lambda x, y: cnt(y) - cnt(x))
                for extra in cands[1:]:
                    remove.add(extra['lt'])
                pairInfo.append({'p': p, 'adj': cands[0] if len(cands) else None, 'g': byLt.get(p['g'])})
            ratios = js_cmp_sort([jdiv(cnt(x['adj']), cnt(x['g'])) for x in pairInfo if x['adj'] and x['g'] and cnt(x['g']) > 0], lambda a, b: a - b)
            med = ratios[(len(ratios) - 1) >> 1] if len(ratios) else 0
            FLOW = 'nongaap' if (len(ratios) and med >= 0.75) else 'gaap'
            detail = ', '.join('%s %s/%s' % (rsub(GAAP_END, S(jor(x['g']['label'], '')), ''), js_str(cnt(x['adj'])), js_str(cnt(x['g']))) for x in pairInfo if x['adj'] and x['g'])
            flowNotes.append('Income Statement flow: %s. One flow for the whole statement, decided by adjusted-chain broker depth (adjusted/GAAP counts: %s; median %s%% %s 75%%). Main items (EBITDA, EBIT, Net Income, EPS) also show the other flow as a muted twin.'
                             % ('NON-GAAP' if FLOW == 'nongaap' else 'GAAP', detail or 'n/a', tf(med * 100, 0), '≥' if FLOW == 'nongaap' else '<'))
            meta['isFlow'] = FLOW
            for x in pairInfo:
                p = x['p']
                lead = (x['adj'] or x['g']) if FLOW == 'nongaap' else (x['g'] or x['adj'])
                if not lead:
                    continue
                other = x['g'] if (lead is x['adj']) else x['adj']
                if other:
                    if p['dual']:
                        other['gaapTwin'] = True
                        if other is x['g'] and not GAAP_RE.search(S(other['label'])):
                            other['label'] = jadd(other['label'], ' (GAAP)')
                    else:
                        remove.add(other['lt'])
                if (FLOW == 'nongaap' and lead is x['g'] and len(p['a'])) or (FLOW == 'gaap' and lead is x['adj']):
                    lead['gaapFallback'] = True
            sec['items'] = [it for it in sec['items'] if it['lt'] not in remove]
            twinOf = {}
            for x in pairInfo:
                if not x['adj'] or not x['g'] or not x['p']['dual']:
                    continue
                lead = x['adj'] if FLOW == 'nongaap' else x['g']
                tw = x['g'] if (lead is x['adj']) else x['adj']
                if ident_in(lead, sec['items']) and ident_in(tw, sec['items']):
                    twinOf[lead['lt']] = tw
            ordered, used = [], set()
            for it in sec['items']:
                if it['lt'] in used or it.get('gaapTwin'):
                    continue
                ordered.append(it)
                used.add(it['lt'])
                tw = twinOf.get(it['lt'])
                if tw and tw['lt'] not in used:
                    ordered.append(tw)
                    used.add(tw['lt'])
            for it in sec['items']:
                if it['lt'] not in used:
                    ordered.append(it)
                    used.add(it['lt'])
            sec['items'] = ordered
        total = 0
        for s in sections:
            total += len(s['items'])

    intel = {}

    def findItem(*lts):
        for lt in lts:
            for s in sections:
                for it in s['items']:
                    if it['lt'] == lt and it['per'].get('q'):
                        return it
        return None

    def leadItem(*lts):
        anyIt = None
        for lt in lts:
            for s in sections:
                for it in s['items']:
                    if it['lt'] != lt or not it['per'].get('q'):
                        continue
                    if not it.get('gaapTwin'):
                        return it
                    if not anyIt:
                        anyIt = it
        return anyIt
    if True:
        isec = next((s for s in sections if s['key'] == 'is'), None)
        if isec:
            hasBroad = any(jre(r'^IS_P_(115|116)$').search(it['lt']) for it in isec['items'])
            hasNarrow = any(jre(r'^IS_P_(1003|1004)$').search(it['lt']) for it in isec['items'])
            if hasBroad or hasNarrow:
                before = len(isec['items'])
                kill = jre(r'^IS_P_(141|142|157|158|1003|1004)$') if hasBroad else jre(r'^IS_P_(141|142|157|158)$')
                isec['items'] = [it for it in isec['items'] if not kill.search(it['lt'])]
                if len(isec['items']) < before:
                    flowNotes.append('Interest components%s suppressed: the net interest line is published, so they would restate it.' % (' and the narrower Net Interest line' if hasBroad and hasNarrow else ''))
            fb = [cleanLabel(it['lt']) for it in isec['items'] if it.get('gaapFallback')]
            NG = mg('isFlow') == 'nongaap'
            if len(fb):
                flowNotes.append('Slots filled by the %s flavour because the %s series is not published: %s.' % ('GAAP' if NG else 'adjusted', 'adjusted' if NG else 'GAAP', ', '.join(fb)))

    if True:
        bs = next((s for s in sections if s['key'] == 'bs'), None)
        if bs:
            def cntOfB(lt):
                it = next((x for x in bs['items'] if x['lt'] == lt), None)
                return jnum(it['per']['q']['cnt']) if (it and it['per'].get('q') and not nil(it['per']['q']['cnt'])) else None

            def has(lt):
                return any(x['lt'] == lt for x in bs['items'])
            keep = set()
            cashPrefs = [lt for lt in ['BS_P_101', 'BS_P_141', 'BS_P_118'] if has(lt)]
            if len(cashPrefs):
                best, bestC = cashPrefs[0], cntOfB(cashPrefs[0])
                for lt in cashPrefs[1:]:
                    c = cntOfB(lt)
                    if c is not None and (bestC is None or c > bestC):
                        best, bestC = lt, c
                keep.add(best)
                if len(cashPrefs) > 1:
                    bi = next((x for x in bs['items'] if x['lt'] == best), None)
                    flowNotes.append('Cash line: %s kept (%s) from %d overlapping cash lines.' % (S(bi['label'] if bi else UNDEF), (js_str(bestC) + ' brokers') if bestC is not None else 'deepest coverage', len(cashPrefs)))
            keep.add('BS_P_103')
            keep.add('BS_P_111')
            finSheet = not len(cashPrefs) and not any(has(x) for x in ['BS_P_114', 'BS_P_115', 'BS_P_108', 'BS_P_109'])
            if finSheet:
                FIN = js_cmp_sort([lt for lt in ['BS_P_250', 'BS_P_252', 'BS_P_254', 'BS_P_276', 'BS_P_278', 'BS_P_262', 'BS_P_113', 'BS_P_251'] if has(lt)],
                                  lambda x, y: jor(cntOfB(y), 0) - jor(cntOfB(x), 0))
                for lt in FIN[:4]:
                    keep.add(lt)
            EX, INCL = ['BS_P_114', 'BS_P_115'], ['BS_P_108', 'BS_P_109']

            def depthB(a):
                v = [x for x in (cntOfB(y) for y in a) if x is not None]
                return jsum(v) if len(v) else -1
            dEx, dIn = depthB(EX), depthB(INCL)
            if dEx >= 0 or dIn >= 0:
                useEx = dEx > dIn
                for lt in (EX if useEx else INCL):
                    if has(lt):
                        keep.add(lt)

                def nB(a):
                    return '/'.join(js_str(x) for x in (cntOfB(y) for y in a) if x is not None) or 'n/a'
                flowNotes.append('Debt basis: %s for both Total and Net Debt, by broker depth (Ex %s vs Incl %s%s).'
                                 % ('EX-operating-lease' if useEx else 'INCL-operating-lease', nB(EX), nB(INCL), '; tie resolved to Incl-lease' if dEx == dIn else ''))
            bs['items'] = [it for it in bs['items'] if it['lt'] in keep]
            if not len(bs['items']):
                sections.pop(next(i for i, s in enumerate(sections) if s is bs))

    SUF_RE = jre(r'\s\(([^()]+)\)\s*$')
    SUF_CUT = jre(r'\s\([^()]+\)\s*$')
    for sec in sections:
        def suf(it):
            m = SUF_RE.search(S(it['label']))
            return m.group(1) if m else None
        tally = {}
        for it in sec['items']:
            s = suf(it)
            if s:
                tally[s] = (tally.get(s) or 0) + 1
        ks = js_cmp_sort(js_keys(tally), lambda a, b: tally[b] - tally[a])
        best = ks[0] if len(ks) else UNDEF
        if not _truthy(best):
            continue
        if jlen(best) < 12 or tally[best] < 3 or tally[best] <= len(sec['items']) / 2:
            continue
        for it in sec['items']:
            if suf(it) != best:
                continue
            it['label'] = jtrim(rsub(SUF_CUT, it['label'], ''))
            it['basis'] = best
        sec['title'] = '%s: %s unless stated' % (sec['title'], best)

    CORE = {
        'rev': leadItem('IS_P_101', 'IS_P_102', 'IS_P_200', 'IS_P_403', 'IS_P_402'),
        'eps': leadItem('IS_S_657', 'IS_P_128', 'IS_P_127'),
        'ebitda': leadItem('IS_P_114', 'IS_P_113', 'IS_P_210', 'IS_P_209', 'IS_P_406'),
        'fcf': leadItem('CF_P_110', 'CF_P_115'),
    }
    segSec = next((s for s in sections if s['key'] == 'seg'), None)
    segItems = [it for it in (segSec['items'] if segSec else []) if it['per'].get('q')]

    def pctMove(p, frm):
        if not p or nil(p.get(frm)) or nil(p.get('cur')):
            return None
        if p[frm] <= 0 or p['cur'] <= 0:
            return None
        return jdiv(p['cur'] - p[frm], abs(p[frm]))

    def bpsMove(p, frm):
        return None if (not p or nil(p.get(frm)) or nil(p.get('cur'))) else (p['cur'] - p[frm]) * 10000

    def fmtPct(v):
        return 'n/a' if nil(v) else ('0.0%' if abs(v) < 0.0005 else (('+' if v >= 0 else '−') + tf(abs(v * 100), 1) + '%'))

    def fmtPct2(v):
        return 'n/a' if nil(v) else ('0.00%' if abs(v) < 0.00005 else (('+' if v >= 0 else '−') + tf(abs(v * 100), 2) + '%'))

    def fmtM(v):
        if not nil(v) and isnan(v):
            return '−' + MF.sym + 'NaNm'
        return MF.signed(None if nil(v) else v)

    W_TOP = jre(r'^IS_P_10[12]$')
    W_2 = jre(r'^IS_P_(105|106|109|110|117|118|123|124)$')

    def W(it):
        lt = it['lt']
        if W_TOP.search(lt) or lt == 'IS_P_200' or lt == 'IS_P_402' or lt == 'IS_P_403':
            return 3
        if lt in ['IS_P_201', 'IS_P_202', 'IS_P_211', 'IS_P_212']:
            return 2.5
        if lt in ['IS_P_207', 'IS_P_209', 'IS_P_210', 'IS_P_406', 'IS_P_410']:
            return 2
        if lt in ['IS_S_657', 'IS_P_127', 'IS_P_128', 'IS_P_251']:
            return 3
        if lt in ['IS_P_113', 'IS_P_114', 'CF_P_110', 'CF_P_115', 'CF_P_108', 'CF_P_109']:
            return 2
        if W_2.search(lt) or lt == 'IS_S_656':
            return 2
        if R_SD.search(lt):
            return 1.5
        if R_KM.search(lt):
            return 1
        if R_BS.search(lt):
            return 0.5
        return 1

    def dirState(it):
        p = it['per'].get('q')
        if not p:
            return None
        if it['isPct']:
            b = bpsMove(p, 'pre')
            return None if b is None else (0 if abs(b) < 2 else jsign(b))
        m = pctMove(p, 'pre')
        return None if m is None else (0 if abs(m) < 0.0005 else jsign(m))
    QUAL_RE = jre(r'\s*\([^()]*\)')

    def stripQual(l):
        return rsub(QUAL_RE, S(l), ' ', 0)
    UP_RE = jre(r'(revenue|gross profit|operating income|\bebit(da)?\b|net income|\beps\b|earnings per|\bpbt\b|pre-?tax (income|profit|earnings)|profit before tax|free cash|operating cash|\bocf\b|\bfcf\b|dividend|\bdps\b|backlog|\brpo\b|remaining performance|billings?\b|bookings?\b|book to bill|deferred revenue|\bcash\b|marketable securities|net interest (&|and) other|interest income|non.?interest income|net interest income|\bppnr\b|pre.?provision|premium|underwriting (income|profit)|investment income|book value|\bbvps\b|\bcet ?1\b|tier 1 capital|tangible common|total equity|total assets|gross loans|net loans|\bdeposits\b|policies in force|working capital|headcount|deliver|shipment|\bunits?\b|subscriber|customers?\b|\bmembers?\b|\bstores?\b|traffic|comparable|comp sales|\bsss\b|same[- ]?store|\basps?\b|volume|bits? sold|bit growth|retention|\bnrr\b|renewal|\bfleet\b|capacity)', 'i')
    DOWN_RE = jre(r'(cost of|\bcogs\b|operating expenses|\bopex\b|sg&a|advertis|research|r&d|traffic acquisition|\btac\b|depreciation|amortization|stock-based|\bsbc\b|severance|interest expense|income tax|effective tax|\betr\b|minority|shares outstanding|basic shares|diluted shares|\bfds\b|share count|\bcapex\b|purchases of property|\bdebt\b|total liabilities|provision|charge.?off|combined ratio|loss ratio|expense ratio|catastrophe|nonaccrual|non.?performing|\bnpa\b|\bnco\b|delinquen|cost per|per unit cost|unit cost|churn|attrition|\btaxes\b|revenue deductions?|cash burn|cash used)', 'i')
    LABEL_POLARITY = {}
    CODE_DIR = {
        'IS_P_101': 1, 'IS_P_102': 1, 'IS_P_200': 1, 'IS_P_103': -1, 'IS_P_104': -1, 'IS_P_105': 1, 'IS_P_106': 1, 'IS_P_107': -1, 'IS_P_108': -1,
        'IS_P_109': 1, 'IS_P_110': 1, 'IS_P_113': 1, 'IS_P_114': 1, 'IS_P_117': 1, 'IS_P_118': 1, 'IS_P_119': -1, 'IS_P_120': -1, 'IS_P_121': -1, 'IS_P_122': -1,
        'IS_P_123': 1, 'IS_P_124': 1, 'IS_S_656': 1, 'IS_P_125': -1, 'IS_P_126': -1, 'IS_P_127': 1, 'IS_P_128': 1, 'IS_S_657': 1,
        'IS_P_141': -1, 'IS_P_142': -1, 'IS_P_157': 1, 'IS_P_158': 1,
        'IS_P_201': 1, 'IS_P_202': 1, 'IS_P_207': -1, 'IS_P_209': 1, 'IS_P_210': 1, 'IS_P_211': 1, 'IS_P_212': 1,
        'IS_P_402': 1, 'IS_P_403': 1, 'IS_P_406': 1, 'IS_P_410': 1, 'IS_P_443': 1,
        'CF_P_108': 1, 'CF_P_109': -1, 'CF_P_110': 1,
        'CF_P_113': 1, 'CF_P_114': 1, 'CF_P_202': 1, 'CF_P_203': 1,
        'BS_P_101': 1, 'BS_P_141': 1, 'BS_P_118': 1, 'BS_P_103': 1, 'BS_P_111': -1, 'BS_P_114': -1, 'BS_P_115': -1, 'BS_P_108': -1, 'BS_P_109': -1,
    }

    def ltOfLabel(label):
        for s in sections:
            for it in s['items']:
                if it['label'] == label:
                    return it['lt']
        return None
    LEVER_RE = jre(r'(\bdebt\b.*\bebitda\b|leverage)', 'i')

    def favSign(label, s, curVal, isStock=UNDEF):
        if nil(s) or s == 0:
            return s
        L = stripQual(label)
        code = ltOfLabel(label)
        if label in LABEL_POLARITY:
            d = LABEL_POLARITY[label]
        elif code and CODE_DIR.get(code):
            d = CODE_DIR[code]
        elif LEVER_RE.search(L):
            d = -1
        elif DOWN_RE.search(L):
            d = -1
        elif UP_RE.search(L):
            d = 1
        else:
            return 0
        if d == -1 and not nil(curVal) and curVal < 0 and label not in LABEL_POLARITY and not _truthy(isStock):
            d = 1
        return s * d

    if True:
        isec = next((s for s in sections if s['key'] == 'is'), None)
        if isec:
            byLt = {}
            for it in isec['items']:
                byLt[it['lt']] = it

            def pickOn(*lts):
                for lt in lts:
                    if byLt.get(lt) and not byLt[lt].get('gaapTwin'):
                        return byLt[lt]
                for lt in lts:
                    if byLt.get(lt):
                        return byLt[lt]
                return None
            nio = pickOn('IS_P_116', 'IS_P_115', 'IS_P_1004', 'IS_P_1003')
            ebit, pbt = pickOn('IS_P_110', 'IS_P_109'), pickOn('IS_P_118', 'IS_P_117')
            if nio and ebit and pbt:
                for pk in ['q', 'fy1']:
                    n, e, p = nio['per'].get(pk), ebit['per'].get(pk), pbt['per'].get(pk)
                    if not n or not e or not p or nil(n['cur']) or nil(e['cur']) or nil(p['cur']) or abs(p['cur']) < 1e-9:
                        continue
                    asExpense, asIncome = abs(e['cur'] - n['cur'] - p['cur']), abs(e['cur'] + n['cur'] - p['cur'])
                    tie = jdiv(jmin(asExpense, asIncome), abs(p['cur']))
                    if tie > 0.05:
                        continue
                    d = -1 if asExpense < asIncome else 1
                    for it in isec['items']:
                        if NOGROW_RE.search(it['lt']):
                            LABEL_POLARITY[it['label']] = d
                    nio['polaritySource'] = 'identity'
                    flowNotes.append('Non-operating line polarity resolved from the statement identity on %s: %s is stored as %s (identity residual %s%% of PBT).'
                                     % (S(meta['q']) if pk == 'q' else 'FY' + S(mg('fy1')), nio['label'],
                                        'an EXPENSE (EBIT − line = PBT; a cut is favourable)' if d == -1 else 'INCOME (EBIT + line = PBT; a rise is favourable)', tf(tie * 100, 1)))
                    break

    for s in sections:
        for it in s['items']:
            p = it['per'].get('q') or it['per'].get('fy1') or None
            curVal = p['cur'] if (p and not nil(p['cur'])) else None
            if s['key'] == 'seg':
                it['fav'] = 'up'
                continue
            f = favSign(it['label'], 1, curVal, s['key'] == 'bs')
            it['fav'] = 'up' if f > 0 else ('down' if f < 0 else None)
    if True:
        unk = [it for s in sections for it in s['items'] if it['fav'] is None]
        if len(unk):
            log('Direction unknown for %d row(s) (no colour, not in breadth or the curated lists): %s.' % (len(unk), ', '.join('%s (%s)' % (it['label'], it['lt']) for it in unk)))

    if True:
        kq = next((s for s in sections if s['key'] == 'kpi'), None)
        if kq:
            STEM_CUT = jre(r'^(total|current|non[\s-]*current)\s+', 'i')
            M_NC, M_C, M_T = jre(r'^non[\s-]*current\s+', 'i'), jre(r'^current\s+', 'i'), jre(r'^total\s+', 'i')

            def norm(l):
                return jtrim(rsub(K_RPO, rsub(K_TRPO, stripQual(l), 'Total RPO'), 'RPO'))

            def stemOf(l):
                return jtrim(rsub(STEM_CUT, norm(l), '')).upper()

            def modOf(l):
                s = norm(l)
                if M_NC.search(s):
                    return 'nc'
                if M_C.search(s):
                    return 'c'
                if M_T.search(s):
                    return 'total'
                return 'parent'
            for it in kq['items']:
                mod = modOf(it['label'])
                if mod != 'c' and mod != 'nc':
                    continue
                stem = stemOf(it['label'])
                parent = next((o for o in kq['items'] if o is not it and stemOf(o['label']) == stem and (modOf(o['label']) == 'total' or modOf(o['label']) == 'parent')), None)
                if not parent:
                    continue
                for pk in ['q', 'fy1']:
                    c, pp = it['per'].get(pk), parent['per'].get(pk)
                    if not c or not pp or nil(c['cur']) or nil(pp['cur']) or pp['cur'] <= 0:
                        continue
                    if c['cur'] > pp['cur'] * 1.01:
                        it['identityFail'] = True
                        it['identityNote'] = '%s (%s) exceeds its parent %s (%s vs %s), which breaks the Current + Non-Current = Total identity' % (
                            it['label'], S(meta['q']) if pk == 'q' else 'FY' + S(mg('fy1')), parent['label'], tf(c['cur'], 1), tf(pp['cur'], 1))
                        break
            bad = [it for it in kq['items'] if it.get('identityFail')]
            for it in bad:
                excluded.append({'lt': it['lt'], 'label': it['label'], 'section': kq['key'], 'reason': 'family identity: ' + it['identityNote'], 'per': it['per']})
                log('Excluded from the table (kept in contract.excluded): %s.' % it['identityNote'])
            kq['items'] = [it for it in kq['items'] if not it.get('identityFail')]
            if not len(kq['items']):
                sections.pop(next(i for i, s in enumerate(sections) if s is kq))

    MIN_COV = 3

    def ELIG(p):
        return bool(p and not p['stale'] and not nil(p['pre']) and not nil(p['cur']) and (nil(p['cnt']) or p['cnt'] >= MIN_COV))

    if True:
        up = dn = fl = stale = lowCov = 0
        wUp = wDn = 0
        for s in sections:
            for it in s['items']:
                q = it['per'].get('q')
                if q and q['stale']:
                    stale += 1
                    continue
                if q and not ELIG(q):
                    lowCov += 1
                    continue
                ds = dirState(it)
                if ds is None:
                    continue
                fav = ds if s['key'] == 'seg' else favSign(it['label'], ds, q['cur'] if q else None, s['key'] == 'bs')
                if fav > 0:
                    up += 1
                    wUp += W(it)
                elif fav < 0:
                    dn += 1
                    wDn += W(it)
                else:
                    fl += 1
        revised = up + dn
        intel['breadth'] = {'up': up, 'down': dn, 'flat': fl, 'stale': stale, 'lowCoverage': lowCov, 'minCoverage': MIN_COV, 'tracked': up + dn + fl,
                            'raw': jdiv(up - dn, revised) if revised else None,
                            'weighted': jdiv(wUp - wDn, wUp + wDn) if (wUp + wDn) else None,
                            'pctUp': jdiv(up, revised) if revised else None}

    intel['magnitude'] = {}
    for k in CORE:
        it = CORE[k]
        if not it:
            continue
        p = it['per']['q']
        intel['magnitude'][k] = {'label': it['label'], 'pct': pctMove(p, 'pre'), 'dollar': (p['cur'] - p['pre']) if (not nil(p['cur']) and not nil(p['pre'])) else None, 'cur': p['cur']}

    keyList = [x for x in [['Revenue', CORE['rev']], ['EPS', CORE['eps']], ['EBITDA', CORE['ebitda']], ['FCF', CORE['fcf']]] +
               [[it['label'], it] for it in segItems] if x[1]]

    def postSnaps(lt):
        arr = H(lt, qFp) or []
        out = []
        for x in arr:
            if slt(PD, x['d']):
                v = pnum(lt, x['v'])
                if not nil(v):
                    out.append({'d': x['d'], 'v': v})
        return out
    intel['metrics'] = []
    for nm, it in keyList:
        snaps = postSnaps(it['lt'])
        if len(snaps) < 2:
            continue
        t0, t1 = date_parse(snaps[0]['d']), date_parse(snaps[-1]['d'])
        cutA, cutB = t0 + jdiv(t1 - t0, 3), t0 + jdiv(2 * (t1 - t0), 3)
        sT = [date_parse(s['d']) for s in snaps]

        def at(t):
            b = snaps[0]
            for i, s in enumerate(snaps):
                if sT[i] <= t:
                    b = s
            return b['v']
        posBase = all(s['v'] > 0 for s in snaps)

        def seg3(a, b):
            va, vb = at(a), at(b)
            return None if abs(va) < 1e-9 else jdiv(vb - va, abs(va))

        def seg3d(a, b):
            return at(b) - at(a)
        v1 = seg3(t0, cutA) if posBase else None
        v2 = seg3(cutA, cutB) if posBase else None
        v3 = seg3(cutB, t1) if posBase else None
        d1, d3 = seg3d(t0, cutA), seg3d(cutB, t1)
        scale = jmax(abs(at(t0)), 1e-9)
        e1, e3 = jdiv(d1, scale), jdiv(d3, scale)
        regime = 'stable'
        if (e1 > 0.001 and e3 < -0.001) or (e1 < -0.001 and e3 > 0.001):
            regime = 'reversing'
        elif abs(e3) > abs(e1) * 1.5 and abs(e3) > 0.001:
            regime = 'accelerating'
        elif abs(e3) < abs(e1) * 0.5 and abs(e1) > 0.001:
            regime = 'decelerating'
        diffs = []
        for i in range(1, len(snaps)):
            d = snaps[i]['v'] - snaps[i - 1]['v']
            if abs(d) > abs(snaps[i - 1]['v']) * 1e-6:
                diffs.append(jsign(d))
        streak = 0
        for i in range(len(diffs) - 1, -1, -1):
            if streak == 0:
                streak = diffs[i]
            elif jsign(streak) == diffs[i]:
                streak += diffs[i]
            else:
                break
        reversals = 0
        for i in range(1, len(diffs)):
            if diffs[i] != diffs[i - 1]:
                reversals += 1
        dom = jdiv(jmax(len([x for x in diffs if x > 0]), len([x for x in diffs if x < 0])), len(diffs)) if len(diffs) else None
        persistence = 'n/a' if dom is None else ('High' if (dom >= 0.75 and reversals <= 1) else ('Medium' if dom >= 0.6 else 'Low'))
        revRisk = 'Elevated' if reversals >= 3 else ('Moderate' if reversals == 2 else 'Low')
        arr = H(it['lt'], qFp) or []
        posts = [x for x in arr if slt(PD, x['d'])]

        def rangePct(x):
            lo, hi, v = pnum(it['lt'], x['min']), pnum(it['lt'], x['max']), pnum(it['lt'], x['v'])
            return jdiv(hi - lo, v) if (not nil(lo) and not nil(hi) and not nil(v) and v > 1e-9) else None
        r0 = rangePct(posts[0]) if len(posts) else None
        rN = rangePct(posts[-1]) if len(posts) else None
        last = arr[-1] if len(arr) else None
        lo = pnum(it['lt'], last['min']) if last else None
        hi = pnum(it['lt'], last['max']) if last else None
        cv = pnum(it['lt'], last['v']) if last else None
        pctile = jdiv(cv - lo, hi - lo) if (not nil(lo) and not nil(hi) and not nil(cv) and hi > lo) else None
        conv = ('converging' if rN < r0 * 0.85 else ('diverging' if rN > r0 * 1.15 else 'stable')) if (r0 is not None and rN is not None) else None
        intel['metrics'].append({'name': nm, 'label': it['label'], 'kind': it['kind'], 'isSeg': bool(R_SD.search(it['lt'])),
                                 'chg': pctMove(it['per']['q'], 'pre'),
                                 'v1': v1, 'v2': v2, 'v3': v3, 'regime': regime, 'streak': streak, 'reversals': reversals, 'pctSame': dom, 'persistence': persistence, 'revRisk': revRisk,
                                 'rangePct': rN, 'pctile': pctile, 'convergence': conv, 'cnt': it['per']['q']['cnt']})

    if True:
        rows = [{'label': it['label'], 'd': it['per']['q']['cur'] - it['per']['q']['pre']} for it in segItems if ELIG(it['per']['q'])]
        tot = jsum(x['d'] for x in rows)
        rv = CORE['rev']
        intel['attribution'] = {'rows': js_cmp_sort([{'label': x['label'], 'dollar': x['d'], 'share': jdiv(x['d'], tot) if abs(tot) > 1e-9 else None} for x in rows], lambda a, b: b['dollar'] - a['dollar']),
                                'total': tot,
                                'revTotal': (N(rv['per']['q']['cur']) - rv['per']['q']['pre']) if (rv and not nil(rv['per']['q']['pre'])) else None}
        if True:
            A = intel['attribution']
            t = A['total'] if abs(A['total']) > 1e-9 else jor(A['revTotal'], 0)
            sg = jsign(t)
            cand = js_cmp_sort([r for r in A['rows'] if r['dollar'] * sg > 0], lambda a, b: b['dollar'] * sg - a['dollar'] * sg) if _truthy(sg) else []
            A['dir'] = sg
            A['driver'] = cand[0] if len(cand) else None
            if _truthy(sg):
                offs = js_cmp_sort([r for r in A['rows'] if r['dollar'] * sg < 0], lambda a, b: a['dollar'] * sg - b['dollar'] * sg)
                A['offset'] = offs[0] if len(offs) else None
            else:
                A['offset'] = None

    if True:
        def g(*lts):
            lead = leadItem(*lts)
            for lt in ([lead['lt']] if lead else lts):
                a = H(lt, qFp)
                if a:
                    cur = pnum(lt, a[-1]['v'])
                    r = atCut(a, cuts['pre'])
                    pre = pnum(lt, r['v']) if r else None
                    if not nil(cur) and not nil(pre):
                        return {'cur': cur, 'pre': pre, 'lt': lt}
            return None
        rev, gp_, opex = g('IS_P_101', 'IS_P_102'), g('IS_P_106', 'IS_P_105'), g('IS_P_108', 'IS_P_107')
        nio = g('IS_P_116', 'IS_P_115') if mg('isFlow') == 'nongaap' else g('IS_P_115', 'IS_P_116')
        etr, ni = g('IS_P_122', 'IS_P_121'), g('IS_S_656', 'IS_P_124', 'IS_P_123')
        eps, sh = g('IS_S_657', 'IS_P_128', 'IS_P_127'), g('IS_P_125', 'IS_P_126')
        if eps and sh and ni:
            t0 = etr['pre'] if etr else 0
            S0, S1 = sh['pre'], sh['cur']
            after = 1 - jor(t0, 0)
            steps = []

            def push(label, v):
                if not nil(v) and jfinite(v):
                    steps.append({'label': label, 'v': v})
            if rev and gp_:
                gm0 = jdiv(gp_['pre'], rev['pre']) if abs(rev['pre']) > 1e-9 else None
                if gm0 is not None:
                    revEff = jdiv((rev['cur'] - rev['pre']) * gm0 * after, S0)
                    push('Revenue growth (at prior gross margin)', revEff)
                    push('Gross-margin mix', jdiv(((gp_['cur'] - gp_['pre']) - (rev['cur'] - rev['pre']) * gm0) * after, S0))
                else:
                    push('Gross profit', jdiv((gp_['cur'] - gp_['pre']) * after, S0))
            if opex:
                push('Operating costs (Opex)', jdiv(-(opex['cur'] - opex['pre']) * after, S0))
            if nio:
                nioIt = findItem(nio['lt'])
                nd = LABEL_POLARITY[nioIt['label']] if (nioIt and nioIt['label'] in LABEL_POLARITY) else 1
                push('Non-operating / other', jdiv(nd * (nio['cur'] - nio['pre']) * after, S0))
            if etr:
                pbt1 = jdiv(ni['cur'], 1 - jor(etr['cur'], 0))
                push('Tax rate', jdiv(-((etr['cur'] - etr['pre']) * pbt1), S0))
            push('Share count', jdiv(ni['cur'], S1) - jdiv(ni['cur'], S0))
            explained = jsum(s['v'] for s in steps)
            totalD = eps['cur'] - eps['pre']
            steps.append({'label': 'Consensus mix residual', 'v': totalD - explained, 'residual': True})
            OPS_RE, NONOPS_RE = jre(r'Revenue growth|Gross-margin|Operating costs|Gross profit'), jre(r'Non-operating|Tax rate|Share count')
            ops = jsum(s['v'] for s in steps if OPS_RE.search(s['label']))
            nonOps = jsum(s['v'] for s in steps if NONOPS_RE.search(s['label']))
            quality, qNote = 'Mixed', 'mixed operating and non-operating drivers'
            if abs(totalD) > 1e-6:
                opsShare = jdiv(ops, totalD)
                if opsShare >= 0.7:
                    quality, qNote = 'High', 'driven primarily by operating earnings'
                elif opsShare >= 0.4:
                    quality, qNote = 'Medium', 'driven by operating earnings with meaningful non-operating support'
                else:
                    quality, qNote = 'Low', 'driven mainly by non-operating items (tax, share count, other income)'
            intel['bridge'] = {'eps0': eps['pre'], 'eps1': eps['cur'], 'steps': steps, 'quality': quality, 'qNote': qNote, 'adjusted': bool(jre(r'IS_S_657').search(eps['lt']))}

    intel['matrix'] = []
    VERDICT = {'improving / rising': 'Strongest signal', 'weakening / rising': 'Questionable: estimates rising into slowing growth', 'improving / falling': 'Potential opportunity — growth intact, estimates cut', 'weakening / falling': 'Negative confirmation'}
    for nm, it in [['Revenue', CORE['rev']], ['EPS', CORE['eps']], ['EBITDA', CORE['ebitda']], ['FCF', CORE['fcf']]]:
        if not it:
            continue
        p = it['per']['q']
        rv_ = pctMove(p, 'pre')
        if rv_ is None or nil(p.get('yoy')):
            continue
        q = ('improving' if p['yoy'] >= 0.0 else 'weakening') + ' / ' + ('rising' if rv_ >= 0 else 'falling')
        intel['matrix'].append({'name': nm, 'growth': p['yoy'], 'revision': rv_, 'quadrant': q, 'verdict': VERDICT.get(q, UNDEF)})


    if True:
        periods = [p for p in [['Next Q', meta['q']], ['Q+2', mg('q2')], ['FY1', mg('fy1')], ['FY2', mg('fy2')]] if _truthy(p[1])]

        def curveFor(lts):
            out = []
            for lab, fp in periods:
                pt = None
                for lt in lts:
                    a = H(lt, fp)
                    if a:
                        cur = pnum(lt, a[-1]['v'])
                        pr = atCut(a, cuts['pre'])
                        pre = pnum(lt, pr['v']) if pr else None
                        if not nil(cur) and not nil(pre) and abs(pre) > 1e-9:
                            pt = {'lab': lab, 'fp': fp, 'pct': jdiv(cur - pre, abs(pre))}
                            break
                out.append(pt if pt else {'lab': lab, 'fp': fp, 'pct': None})
            return out

        def oneFlavour(lead, alts):
            if not lead:
                return curveFor(alts)
            L = lead['lt']
            lq = H(L, meta['q'])

            def lv(a):
                if not a:
                    return None
                pr = atCut(a, cuts['pre'])
                return {'cur': pnum(L, a[-1]['v']), 'pre': pnum(L, pr['v']) if pr else None}
            lvl = lv(lq)

            def same(lt):
                if lt == L:
                    return True
                a = H(lt, meta['q'])
                if not a or not lvl:
                    return False
                pr = atCut(a, cuts['pre'])
                cur = pnum(lt, a[-1]['v'])
                pre = pnum(lt, pr['v']) if pr else None

                def close(x, y):
                    return not nil(x) and not nil(y) and abs(y) > 1e-9 and abs(jdiv(x, y) - 1) <= 0.005
                return close(cur, lvl['cur']) and close(pre, lvl['pre'])
            return curveFor([L] + [lt for lt in alts if lt != L and same(lt)])
        revC = oneFlavour(CORE['rev'], ['IS_P_101', 'IS_P_102', 'IS_P_200', 'IS_P_403', 'IS_P_402'])
        epsC = oneFlavour(CORE['eps'], ['IS_S_657', 'IS_P_128', 'IS_P_127'])

        def shape(c):
            v = [x['pct'] for x in c]
            if any(x is None for x in v):
                return None
            near, far = v[0], v[-1]
            if near > 0.003 and far < -0.003:
                return 'Reversal: near-term positive, outer-year negative'
            if all(x > 0.003 for x in v):
                return 'Front-loaded: upgrades concentrated near-term' if abs(near) > abs(far) * 2 else 'Broad-based: estimates rising across all periods'
            if all(x < -0.003 for x in v):
                return 'Broad-based downgrade'
            if near < 0.003 and far > 0.003:
                return 'Back-loaded: outer-year upgrades without near-term confirmation'
            if abs(near) > 0.003 and abs(far) <= 0.003:
                return 'Front-loaded: outer years not yet confirming'
            return 'Flat / mixed'
        intel['curve'] = {'periods': [p[0] for p in periods], 'fps': [p[1] for p in periods], 'rev': revC, 'eps': epsC, 'revShape': shape(revC), 'epsShape': shape(epsC)}

    if True:
        rows = []
        elapsed = prepT - printT
        for cyc in jiter(jor(mg('pastCycles'), [])):
            cpd = S(cyc['printDate'])
            pT = date_parse(cpd)
            cutEnd = iso(pT + elapsed)

            def one(lts, cyc=cyc, cpd=cpd, cutEnd=cutEnd):
                for lt in lts:
                    a = H(lt, cyc.get('targetFp', UNDEF))
                    if a:
                        pre = [x for x in a if slt(x['d'], cpd)]
                        win = [x for x in a if sle(x['d'], cutEnd)]
                        if len(pre) and len(win):
                            v0, v1 = pnum(lt, pre[-1]['v']), pnum(lt, win[-1]['v'])
                            if not nil(v0) and not nil(v1) and abs(v0) > 1e-9:
                                return {'v': jdiv(v1 - v0, abs(v0)), 'basis': 'pre'}
                        post = [x for x in a if not slt(x['d'], cpd) and sle(x['d'], cutEnd)]
                        if len(post) >= 2:
                            v0, v1 = pnum(lt, post[0]['v']), pnum(lt, post[-1]['v'])
                            if not nil(v0) and not nil(v1) and abs(v0) > 1e-9:
                                return {'v': jdiv(v1 - v0, abs(v0)), 'basis': 'post-only'}
                return None
            rv = one([CORE['rev']['lt']]) if CORE['rev'] else None
            ep = one([CORE['eps']['lt']]) if CORE['eps'] else None
            rows.append({'cycle': cyc.get('label', UNDEF), 'targetFp': cyc.get('targetFp', UNDEF), 'rev': rv['v'] if rv else None, 'eps': ep['v'] if ep else None,
                         'basis': (rv and rv['basis']) or (ep and ep['basis']) or None})
        curRev = pctMove(CORE['rev']['per']['q'], 'pre') if CORE['rev'] else None
        curEps = pctMove(CORE['eps']['per']['q'], 'pre') if CORE['eps'] else None

        def med(a):
            v = js_cmp_sort([x for x in a if x is not None], lambda x, y: x - y)
            return v[(len(v) - 1) >> 1] if len(v) else None
        withData = [r for r in rows if r['rev'] is not None or r['eps'] is not None]
        uniformBasis = len(withData) > 0 and all(r['basis'] == 'pre' for r in withData)
        if len(withData) and not uniformBasis:
            log('Prior cycles: %d of %d measured on a post-only basis (no pre-print vintage in the fetched window) - the cycle rank will not print. Fetch prior cycles from printDate - 10 days (SKILL Step 4a).'
                % (len([r for r in withData if r['basis'] != 'pre']), len(withData)))
        intel['history'] = {'rows': rows, 'curRev': curRev, 'curEps': curEps, 'medRev': med([r['rev'] for r in rows]), 'medEps': med([r['eps'] for r in rows]), 'uniformBasis': uniformBasis}

    if True:
        px, ix, otherPx, pxCur = [], [], {}, {}
        IDX = S(jor(mg('indexId'), 'IDX0001'))

        def walkP(o):
            if isinstance(o, list):
                for x in o:
                    walkP(x)
            elif _truthy(o) and isinstance(o, dict):
                if o.get('ADJUSTED_CLOSE', UNDEF) is not UNDEF and o.get('DATE', UNDEF) is not UNDEF:
                    rec = {'d': S(o['DATE'])[:10], 'c': jnum(o['ADJUSTED_CLOSE'])}
                    tid = S(o.get('TICKER_ID', UNDEF))
                    if tid == meta['tickerId']:
                        px.append(rec)
                        if _truthy(o.get('CURRENCY', UNDEF)):
                            pxCur[S(o['CURRENCY'])] = True
                    elif tid == IDX:
                        ix.append(rec)
                    else:
                        otherPx[tid] = True
                else:
                    for k in js_keys(o):
                        walkP(o[k])
        walkP(docs)
        SYM2ISO = {'$': 'USD', '€': 'EUR', '£': 'GBP', '₹': 'INR', '₩': 'KRW'}
        rep = SYM2ISO.get(CUR) or CUR
        if len(pxCur) and not all(c == rep for c in pxCur):
            meta['priceCurrency'] = ','.join(pxCur)
            log('Note: the share price trades in %s while the estimates are in %s; normal for a listing outside the home market. Money on the sheet stays in %s.' % (meta['priceCurrency'], CUR, CUR))
        if len(otherPx):
            log('Price rows ignored for %s: only the stock and the index %s are used%s.' % (', '.join(otherPx), IDX, '' if _truthy(mg('indexId')) else ' (set meta.indexId to use another index)'))

        def ret(arr):
            if not len(arr):
                return None
            arr[:] = js_cmp_sort(arr, lambda a, b: -1 if slt(a['d'], b['d']) else 1)
            base = None
            for p in arr:
                if slt(p['d'], PD):
                    base = p
            lastP = arr[-1]
            return ({'base': base['c'], 'last': lastP['c'], 'ret': jdiv(lastP['c'], base['c']) - 1, 'baseD': base['d'], 'lastD': lastP['d']}
                    if (base and lastP and _truthy(base['c'])) else None)
        s_, m_ = ret(px), ret(ix)
        intel['price'] = ({'stock': s_['ret'], 'stockFrom': s_['baseD'], 'stockTo': s_['lastD'], 'index': m_['ret'] if m_ else None,
                           'relative': (s_['ret'] - m_['ret']) if (s_ and m_) else None} if s_ else None)

    if True:
        comp = []

        def add(name_, val, note):
            comp.append({'name': name_, 'val': val, 'note': note})
        b = intel['breadth']['weighted']
        add('Breadth', 0 if b is None else (2 if b > 0.4 else 1 if b > 0.1 else -2 if b < -0.4 else -1 if b < -0.1 else 0), 'n/a' if b is None else 'weighted breadth %s' % fmtPct(b))
        mags = [x for x in (intel['magnitude'][k]['pct'] if intel['magnitude'].get(k) else None for k in ['rev', 'eps', 'ebitda']) if x is not None]
        mAvg = jdiv(jsum(mags), len(mags)) if len(mags) else None
        add('Magnitude', 0 if mAvg is None else (2 if mAvg > 0.02 else 1 if mAvg > 0.005 else -2 if mAvg < -0.02 else -1 if mAvg < -0.005 else 0), 'n/a' if mAvg is None else 'avg Rev/EPS/EBITDA revision %s' % fmtPct(mAvg))
        kv = [m for m in intel['metrics'] if m['name'] in ['Revenue', 'EPS']]
        accel = len([m for m in kv if m['regime'] == 'accelerating'])
        decel = len([m for m in kv if m['regime'] == 'decelerating' or m['regime'] == 'reversing'])
        add('Velocity', 1 if accel > decel else (-1 if decel > accel else 0), 'Rev/EPS momentum: %s' % (', '.join(m['regime'] for m in kv) or 'n/a'))
        hp = len([m for m in kv if m['persistence'] == 'High'])
        lp = len([m for m in kv if m['persistence'] == 'Low'])
        kDir = 0 if mAvg is None else jsign(mAvg)
        add('Persistence', 0 if kDir == 0 else (2 * kDir if (hp >= 1 and lp == 0) else (1 * kDir if hp >= 1 else 0)), '%d high / %d low persistence among Rev/EPS' % (hp, lp))
        convs = [m['convergence'] for m in kv if _truthy(m['convergence'])]
        add('Crowding', -1 if 'diverging' in convs else (1 if 'converging' in convs else 0), 'dispersion %s' % ', '.join(convs) if len(convs) else 'n/a')
        farE = intel['curve']['eps'][-1]['pct'] if (intel.get('curve') and len(intel['curve']['eps'])) else None
        add('Fwd-year confirmation', 0 if (farE is None or kDir == 0) else (1 if (jsign(farE) == kDir and abs(farE) > 0.003) else (-1 if (jsign(farE) == -kDir and abs(farE) > 0.003) else 0)),
            'n/a' if farE is None else 'FY2 EPS revision %s' % fmtPct(farE))
        score = jsum(c['val'] for c in comp)
        label = 'STRONG POSITIVE' if score >= 6 else ('POSITIVE' if score >= 2 else ('STRONG NEGATIVE' if score <= -6 else ('NEGATIVE' if score <= -2 else 'NEUTRAL / MIXED')))
        intel['score'] = {'components': comp, 'total': score, 'max': 10, 'label': label}

    MAT_DPS = jre(r'(^|\b)(dps|dividend|share repurchase|buyback)', 'i')
    MAT_SH = jre(r'(basic shares|diluted shares|\bfds\b|adjusted fds|share count|shares outstanding)', 'i')
    MAT_BTL = jre(r'(interest (income|expense)|net interest|income tax|effective tax|\betr\b)', 'i')

    def MAT(label, lt):
        if MAT_DPS.search(label):
            return {'w': 0.05, 'cls': 'capret'}
        if MAT_SH.search(label):
            return {'w': 0.05, 'cls': 'capret'}
        if MAT_BTL.search(label):
            return {'w': 0.4, 'cls': 'btl'}
        if R_BS.search(S(jor(lt, ''))):
            return {'w': 0.35, 'cls': 'bs'}
        if R_CF.search(S(jor(lt, ''))):
            return {'w': 0.9, 'cls': 'cash'}
        if R_KM.search(S(jor(lt, ''))):
            return {'w': 0.7, 'cls': 'kpi'}
        return {'w': 1, 'cls': 'core'}
    L_BOT = jre(r'(\beps\b|earnings per|net income)', 'i')
    L_PROF = jre(r'(gross profit|\bebit(da)?\b|\bpbt\b|pre-?tax|operating income)', 'i')
    L_COST = jre(r'(cost of goods|\bcogs\b|cost of (revenue|sales)|operating expenses|\bopex\b|sg&a|research|r&d)', 'i')
    L_BTL = jre(r'(interest|tax|\betr\b)', 'i')
    L_TOP = jre(r'revenue|sales', 'i')

    def LAYER(label, lt, secKey):
        if secKey == 'seg':
            return 'seg'
        if R_KM.search(S(jor(lt, ''))):
            return 'kpi'
        if R_BS.search(S(jor(lt, ''))):
            return 'bs'
        if L_BOT.search(label):
            return 'bottom'
        if L_PROF.search(label):
            return 'profit'
        if L_COST.search(label):
            return 'cost'
        if L_BTL.search(label):
            return 'btl'
        if R_CF.search(S(jor(lt, ''))):
            return 'cash'
        if L_TOP.search(label):
            return 'top'
        return 'other'
    P_EPS, P_EBIT, P_FCF, P_GP = jre(r'(\beps\b|earnings per)', 'i'), jre(r'\bebit(da)?\b', 'i'), jre(r'\bfcf\b|free cash', 'i'), jre(r'gross profit', 'i')
    P_OCF, P_PBT, P_WC = jre(r'\bocf\b|operating cash', 'i'), jre(r'\bpbt\b|pre-?tax', 'i'), jre(r'working capital', 'i')

    def PREF(label):
        if P_EPS.search(label):
            return 1.25
        if P_EBIT.search(label):
            return 1.15
        if P_FCF.search(label):
            return 1.15
        if P_GP.search(label):
            return 0.9
        if P_OCF.search(label):
            return 0.85
        if P_PBT.search(label):
            return 0.7
        if P_WC.search(label):
            return 0.7
        return 1
    COST_RE = jre(r'(cost of goods|\bcogs\b|cost of (revenue|sales)|operating expenses|\bopex\b|sg&a|research|r&d|traffic acquisition|\btac\b)', 'i')

    if True:
        D = 864e5

        def day(s):
            return date_parse(S(s) + 'T00:00:00Z')
        prep, pr = day(meta['prepared']), day(meta['printDate'])
        nx = day(mg('nextPrintDate')) if _truthy(mg('nextPrintDate')) else None
        if nx is None:
            log('WARNING: meta.json has no nextPrintDate; the earnings-cycle phase falls back to days since the print alone, so a pre-print report can be framed as late-cycle. Say so in the run summary.')
        elif nx < prep:
            log('WARNING: meta.json nextPrintDate %s is before the run date %s; ignored. That print has happened: check printPeriod/printDate (SKILL Step 1).' % (S(mg('nextPrintDate')), S(meta['prepared'])))
            nx = None
        since = js_round(jdiv(prep - pr, D))
        to = js_round(jdiv(nx - prep, D)) if nx is not None else None
        if to is not None and to <= 21 and to >= 0:
            key = 'pre'
        elif since <= 14:
            key = 'fresh'
        elif since <= 45:
            key = 'digest'
        else:
            key = 'late'
        P = {
            'pre': {'label': 'PRE-PRINT',
                    'window': 'reports today' if to == 0 else 'reports in %s day%s' % (js_str(to), '' if to == 1 else 's'),
                    'question': 'Whether the print can validate the expectations already built into consensus.',
                    's2': 'ESTIMATE REVISIONS', 's4': 'EARNINGS EXPECTATIONS',
                    'job': 'Base, Extend and Challenge define what the upcoming print must deliver.',
                    'framing': 'The next print is imminent. Frame every section around the bar consensus has set: what the numbers now embed, and what management must evidence to validate them. State the level first, the implication second.'},
            'fresh': {'label': 'FRESH POST-PRINT',
                      'window': '%s day%s since the print' % (js_str(since), '' if since == 1 else 's'),
                      'question': 'Whether the post-print reset is complete, given broker coverage is still rebuilding.',
                      's2': 'ESTIMATE REVISIONS', 's4': 'EARNINGS EXPECTATIONS',
                      'job': 'Base, Extend and Challenge define whether the initial reset holds or evolves.',
                      'framing': 'The print is days old and estimates are still moving. Frame every section around the reset: which lines re-rated and whether broker coverage has rebuilt enough for the new consensus to be reliable. Where coverage is still too thin for a line to be reliable, let that govern which lines you cite, not what you write about coverage.'},
            'digest': {'label': 'REVISION DIGESTION',
                       'window': '%s days since the print' % js_str(since),
                       'question': 'Whether the post-print revision is durable or already fading.',
                       's2': 'ESTIMATE REVISIONS', 's4': 'EARNINGS EXPECTATIONS',
                       'job': 'Base, Extend and Challenge define what must happen before the next catalyst.',
                       'framing': 'Estimates have had time to settle. Frame every section around durability: whether the initial post-print move is holding, extending or decaying, and whether the outer year confirms it.'},
            'late': {'label': 'LATE CYCLE',
                     'window': '%s days since the print%s' % (js_str(since), (', %s to the next' % js_str(to)) if to is not None else ''),
                     'question': 'Whether the post-print revision has been confirmed or reversed heading into the next print.',
                     's2': 'ESTIMATE REVISIONS', 's4': 'EARNINGS EXPECTATIONS',
                     'job': 'Base, Extend and Challenge define what consensus still needs to see.',
                     'framing': 'The quarter is stale and the next print is the next catalyst. Frame every section around carry-forward: what survived of the post-print move and what the next event must settle.'}
        }
        intel['phase'] = dict({'key': key, 'daysSince': since, 'daysTo': to}, **P[key])

    if len(ANCHORS) >= 2:
        def classify(p):
            v = [x for x in [p['pre'], p.get('t10', UNDEF), p.get('t40', UNDEF), p['cur']] if not nil(x)]
            if len(v) < 3 or N(p['pre']) <= 0 or N(p['cur']) <= 0:
                return None
            tot = N(p['cur']) - N(p['pre'])
            if not _truthy(tot) or abs(tot) < 1e-9:
                return None
            legs = [jdiv(x - v[i], abs(tot)) for i, x in enumerate(v[1:])]
            while len(legs) < 3:
                legs.append(0)
            s = jsign(tot)
            first, rest = legs[0] * s, (legs[1] + legs[2]) * s
            if first > 0.7 and abs(legs[1]) + abs(legs[2]) < 0.4:
                key, note = 'Reset & Hold', 'the reset was front-loaded and has held'
            elif first > 0.5 and rest > 0.15:
                key, note = 'Reset & Build', 'estimates have continued to rise since the print'
            elif first > 1.0 and rest < -0.15:
                key, note = 'Spike & Fade', 'part of the initial move has been given back'
            elif first < 0 and rest > 0.9:
                key, note = 'Dip & Recover', 'estimates fell on the print and have since recovered'
            elif rest > 0.6:
                key, note = 'Late Revision', 'the revision came after the print, not on it'
            else:
                key, note = 'Unsettled', 'estimates have moved in both directions'
            return {'key': key, 'note': note, 'firstLegShare': legs[0], 'restShare': rest * s}
        segS = next((s for s in sections if s['key'] == 'seg'), None)
        leadL = js_cmp_sort([it for it in (segS['items'] if segS else []) if ELIG(it['per'].get('q'))],
                            lambda a, b: abs(N(jor(b['per']['q']['cur'], 0)) - N(jor(b['per']['q']['pre'], 0))) - abs(N(jor(a['per']['q']['cur'], 0)) - N(jor(a['per']['q']['pre'], 0))))
        lead = leadL[0] if len(leadL) else None
        probes = [['Revenue', CORE['rev']], ['EPS', CORE['eps']], ['EBITDA', CORE['ebitda']], ['FCF', CORE['fcf']]]
        if lead:
            probes.append([lead['label'], lead])
        paths = []
        for nm, it in probes:
            if not it or not it['per'].get('q'):
                continue
            c = classify(it['per']['q'])
            if c:
                paths.append(dict({'name': nm}, **c))
        tally = {}
        for p in paths:
            tally[p['key']] = (tally.get(p['key']) or 0) + 1
        ks = js_cmp_sort(js_keys(tally), lambda a, b: tally[b] - tally[a])
        dom = ks[0] if len(ks) else None
        revP = next((p for p in paths if p['name'] == 'Revenue'), None) or (paths[0] if len(paths) else None)
        intel['revisionPath'] = ({'rows': paths, 'dominant': dom, 'agree': tally[dom] if dom else 0, 'of': len(paths),
                                  'headline': revP['firstLegShare'] if revP else None, 'note': (next((p for p in paths if p['key'] == dom), None) or {}).get('note') or None}
                                 if len(paths) else None)

    if True:
        rows = []
        maxCnt = 0
        for s in sections:
            for it in s['items']:
                p = it['per'].get('q')
                if not p or not _truthy(p['cnt']):
                    continue
                if p['cnt'] > maxCnt:
                    maxCnt = p['cnt']
        revC = abs(CORE['rev']['per']['q']['cur']) if (CORE['rev'] and not nil(CORE['rev']['per']['q']['cur'])) else None
        for s in sections:
            for it in s['items']:
                if it.get('gaapTwin'):
                    continue
                p = it['per'].get('q')
                if not p or not _truthy(p['cnt']) or nil(p['low']) or nil(p['high']) or nil(p['cur']) or p['cur'] <= 0:
                    continue
                if it['isPct'] or it['kind'] == 'ratio':
                    continue
                spread = jdiv(p['high'] - p['low'], abs(p['cur']))
                if not jfinite(spread) or spread <= 0:
                    continue
                cov = jdiv(p['cnt'], maxCnt) if _truthy(maxCnt) else 1
                mv = pctMove(p, 'pre')
                if MAT(it['label'], it['lt'])['cls'] == 'capret':
                    continue
                size = jmin(1, jdiv(abs(p['cur']), revC) * 8) if (it['kind'] == 'money' and _truthy(revC)) else 0.8
                annCnt = jnum(it['per']['fy1']['cnt']) if (it['per'].get('fy1') and not nil(it['per']['fy1']['cnt'])) else None
                rows.append({'label': it['label'], 'sec': s['key'], 'cnt': p['cnt'], 'annCnt': annCnt, 'cov': cov, 'spread': spread, 'move': mv, 'size': size,
                             'layer': LAYER(it['label'], it['lt'], s['key']),
                             'thinScore': spread * (1 - cov) * size * (1 + abs(N(jor(mv, 0))) * 5),
                             'low': p['low'], 'high': p['high'], 'cur': p['cur']})
        spreads = js_cmp_sort([r['spread'] for r in rows], lambda a, b: a - b)
        medSpread = spreads[(len(spreads) - 1) >> 1] if len(spreads) else 0

        def byLayer(arr, n):
            seen, out = set(), []
            for r in arr:
                if r['layer'] in seen:
                    continue
                seen.add(r['layer'])
                out.append(r)
                if len(out) >= n:
                    break
            return out
        thin = byLayer(js_cmp_sort([r for r in rows if r['cov'] < 0.6 and r['spread'] > medSpread], lambda a, b: b['thinScore'] - a['thinScore']), 2)
        deep = byLayer(js_cmp_sort([r for r in rows if r['cov'] >= 0.7 and r['spread'] > medSpread * 1.5 and r['size'] > 0.5], lambda a, b: b['spread'] - a['spread']), 1)
        intel['disagreement'] = {'maxCnt': maxCnt,
                                 'thin': [{'label': r['label'], 'cnt': r['cnt'], 'annCnt': r['annCnt'], 'spreadPct': r['spread'], 'move': r['move'], 'low': r['low'], 'high': r['high'], 'kind': 'kpi' if r['size'] == 0.8 else 'money'} for r in thin],
                                 'deep': [{'label': r['label'], 'cnt': r['cnt'], 'spreadPct': r['spread'], 'move': r['move']} for r in deep]}

    DIVERGENT = set()
    if True:
        revCur = abs(CORE['rev']['per']['q']['cur']) if (CORE['rev'] and not nil(CORE['rev']['per']['q']['cur'])) else None
        rows = []
        for s in sections:
            for it in s['items']:
                if it.get('gaapTwin') or it['isPct'] or it['kind'] == 'ratio':
                    continue
                if s['key'] == 'bs':
                    continue
                if MAT(it['label'], it['lt'])['cls'] == 'capret':
                    continue
                q, f = it['per'].get('q'), it['per'].get('fy1')
                if not ELIG(q) or not ELIG(f):
                    continue
                mq, mf = pctMove(q, 'pre'), pctMove(f, 'pre')
                if mq is None or mf is None:
                    continue
                if jsign(mq) == jsign(mf):
                    continue
                if abs(mq) < 0.01 or abs(mf) < 0.01:
                    continue
                if it['kind'] == 'money' and _truthy(revCur) and jdiv(abs(f['cur'] - f['pre']), revCur) < 0.005:
                    continue
                good = (mq < 0) if it['fav'] == 'down' else ((mq > 0) if it['fav'] == 'up' else None)
                rows.append({'label': it['label'], 'layer': LAYER(it['label'], it['lt'], s['key']), 'q': mq, 'fy1': mf, 'fav': it['fav'] or None,
                             'gap': abs(mq - mf),
                             'text': '%s diverges by horizon: %s for %s but %s for FY%s' % (it['label'], fmtPct(mq), S(meta['q']), fmtPct(mf), S(mg('fy1'))),
                             'tail': "The quarter's move does not carry through the full year." if good is None else 'The %s does not carry through the full year.' % ('improvement' if good else 'deterioration')})
        rows = js_cmp_sort(rows, lambda a, b: b['gap'] - a['gap'])
        for r in rows:
            DIVERGENT.add(r['label'])
        seen, out = set(), []
        for r in rows:
            if r['layer'] in seen:
                continue
            seen.add(r['layer'])
            out.append(r)
        intel['divergence'] = out[:3]

    PCT_TAIL = jre(r'\s*\(%\)\s*$')
    if True:
        AGG = {'IS_P_105', 'IS_P_106', 'IS_P_113', 'IS_P_114', 'IS_P_109', 'IS_P_110', 'IS_P_209', 'IS_P_210', 'IS_P_406', 'IS_P_117', 'IS_P_118', 'IS_P_119', 'IS_P_120', 'IS_P_121', 'IS_P_122', 'IS_P_123', 'IS_P_124', 'IS_S_656', 'IS_P_125', 'IS_P_126', 'IS_P_127', 'IS_P_128', 'IS_S_657', 'IS_P_250', 'IS_P_251', 'IS_P_252', 'IS_P_402'}
        target = leadItem('IS_P_118', 'IS_P_117', 'IS_P_124', 'IS_P_123')
        rows = []

        def rowsMatch(a, b):
            return jtrim(S(a)) == jtrim(S(b))
        if target and target['per'].get('q') and not nil(target['per']['q']['cur']) and not nil(target['per']['q']['pre']):
            tDelta = target['per']['q']['cur'] - target['per']['q']['pre']
            revAnchor = CORE['rev']['per']['q']['cur'] if (CORE['rev'] and CORE['rev']['per'].get('q')) else None
            isSec = next((x for x in sections if x['key'] == 'is'), None) or {'items': []}
            COMP_RE = jre(r'net interest income|non.?interest income|premium earned|investment income|fee income', 'i')
            revComponents = [it for it in isSec['items'] if not it.get('gaapTwin') and it['kind'] == 'money' and it['lt'] != (CORE['rev'] and CORE['rev']['lt'])
                             and it['lt'] not in AGG and COMP_RE.search(stripQual(it['label']))]
            excludeRev = len(revComponents) >= 2
            lossRatioIn = False
            RATIO_TOP = jre(r'^ *(loss|expense) ratio', 'i')
            LOSS_TOP = jre(r'^ *loss ratio', 'i')
            PL_FLOW = jre(r'\b(loss|losses|provision|provisions|claim|claims|expense|expenses|charge|charges)\b', 'i')
            CAT = jre(r'catastrophe', 'i')
            for sec in sections:
                if sec['key'] == 'bs' or sec['key'] == 'cf' or sec['key'] == 'seg':
                    continue
                for it in sec['items']:
                    if it.get('gaapTwin') or it['lt'] in AGG:
                        continue
                    p = it['per'].get('q')
                    if not p or nil(p['cur']) or nil(p['pre']):
                        continue
                    if MAT(it['label'], it['lt'])['cls'] == 'capret':
                        continue
                    L = stripQual(it['label'])
                    if it['isPct'] or it['kind'] == 'ratio':
                        if not RATIO_TOP.search(L):
                            continue
                        npe = findItem('IS_P_403')
                        base = npe['per']['q']['cur'] if (npe and npe['per'].get('q') and not nil(npe['per']['q']['cur'])) else revAnchor
                        if nil(base):
                            continue
                        e = -(p['cur'] - p['pre']) * base
                        if LOSS_TOP.search(L):
                            lossRatioIn = True
                        rows.append({'label': rsub(PCT_TAIL, it['label'], ''), 'e': e, 'kind': 'ratio'})
                    elif it['kind'] == 'money':
                        if sec['key'] != 'is' and not PL_FLOW.search(L):
                            continue
                        if excludeRev and CORE['rev'] and it['lt'] == CORE['rev']['lt']:
                            continue
                        identityResolved = it['label'] in LABEL_POLARITY
                        if identityResolved:
                            d = LABEL_POLARITY[it['label']]
                        elif DOWN_RE.search(L):
                            d = -1
                        elif UP_RE.search(L):
                            d = 1
                        elif sec['key'] == 'seg':
                            d = 1
                        else:
                            continue
                        if d == -1 and not nil(p['cur']) and p['cur'] < 0 and not identityResolved:
                            d = 1
                        rows.append({'label': it['label'], 'e': (p['cur'] - p['pre']) if d == 1 else -(p['cur'] - p['pre']), 'kind': 'money', 'cat': bool(CAT.search(L))})
            cleaned = [r for r in rows if not (r.get('cat') and lossRatioIn)]
            cleaned = js_cmp_sort(cleaned, lambda a, b: abs(b['e']) - abs(a['e']))
            gross = jsum(abs(r['e']) for r in cleaned)
            shown, cum = [], 0
            for r in cleaned:
                if len(shown) >= 4:
                    break
                if abs(r['e']) < jmax(1, 0.02 * gross):
                    break
                shown.append(r)
                cum += abs(r['e'])
                if jdiv(cum, gross) >= 0.9:
                    break
            sm = jsum(r['e'] for r in shown)
            residual = tDelta - sm
            dom = shown[0] if len(shown) else None
            REVPE = jre(r'revenue|premium earned', 'i')
            domIsRevenue = bool(dom and ((CORE['rev'] and rowsMatch(dom['label'], CORE['rev']['label'])) or REVPE.search(stripQual(dom['label']))))
            intel['engine'] = {'target': {'label': target['label'], 'delta': tDelta},
                               'rows': [{'label': r['label'], 'e': r['e'], 'kind': r['kind']} for r in shown],
                               'residual': residual, 'dominant': {'label': dom['label'], 'e': dom['e']} if dom else None, 'domIsRevenue': domIsRevenue}
        else:
            intel['engine'] = None

    if True:
        FAMS = [
            ['fwd-demand', jre(r'\brpo\b|billing|booking|backlog|deferred revenue|remaining performance|book to bill', 'i')],
            ['retention', jre(r'retention|\bnrr\b|churn|renewal', 'i')],
            ['volume-pricing', jre(r'\basp\b|price|\bunits?\b|deliver|shipment|volume|bits? sold|bit growth|traffic|comparable sales|comp sales|\bsss\b|same[- ]?store|comparable|members|subscriber|customers', 'i')],
            ['credit', jre(r'provision|charge.?off|\bnco\b|delinquen|non.?performing', 'i')],
            ['underwriting', jre(r'combined ratio|loss ratio|expense ratio|catastrophe', 'i')],
            ['capacity', jre(r'headcount|stores|capacity|fleet', 'i')]]
        FAMW = {'fwd-demand': 1.0, 'volume-pricing': 0.9, 'retention': 0.9, 'credit': 0.9, 'underwriting': 0.9, 'capacity': 0.5, 'other': 0.6}

        def famOfE(l):
            for f, rx in FAMS:
                if rx.search(l):
                    return f
            return 'other'
        revPct = intel['magnitude']['rev']['pct'] if (intel.get('magnitude') and intel['magnitude'].get('rev')) else None
        dir_ = jsign(revPct) if (revPct is not None and revPct != 0) else 0
        rows = []
        kq2 = next((s for s in sections if s['key'] == 'kpi'), None)
        if kq2 and dir_ != 0:
            for it in kq2['items']:
                p = it['per'].get('q')
                if not ELIG(p):
                    continue
                m = pctMove(p, 'pre')
                if m is None or abs(m) < 0.005:
                    continue
                fam = famOfE(stripQual(it['label']))
                fav = favSign(it['label'], jsign(m), p['cur'])
                if not _truthy(fav):
                    continue
                rows.append({'label': it['label'], 'family': fam, 'pct': m, 'cnt': jnum(p['cnt']) if not nil(p['cnt']) else 0,
                             'align': 'confirms' if fav * dir_ > 0 else 'contradicts',
                             'w': jmin(1, jdiv(abs(m), jmax(0.01, abs(revPct)))) * (FAMW.get(fam) or 0.6) * jmin(1, jdiv(jor(p['cnt'], 0), 10))})
        rows = js_cmp_sort(rows, lambda a, b: jor(b['w'] - a['w'], b['cnt'] - a['cnt'], abs(b['pct']) - abs(a['pct'])))
        contr = [r for r in rows if r['align'] == 'contradicts']
        intel['evidence'] = {'dir': dir_, 'rows': rows,
                             'confirms': [r for r in rows if r['align'] == 'confirms'],
                             'contradicts': contr,
                             'top': contr[0] if len(contr) else None}

    derived = {}
    derivedOf = {}
    if True:
        den = leadItem('IS_P_101', 'IS_P_102')

        def mk(name_, numItem, costRe):
            if not den or not numItem:
                return
            out = {'name': name_}
            for pk, _fp in PERIODS:
                n, d = numItem['per'].get(pk), den['per'].get(pk)
                if not n or not d or nil(n['cur']) or nil(n['pre']) or nil(d['cur']) or nil(d['pre']) or d['cur'] <= 0 or d['pre'] <= 0:
                    continue
                cur, pre = jdiv(n['cur'], d['cur']), jdiv(n['pre'], d['pre'])
                out[pk] = {'cur': cur, 'pre': pre, 'bps': (cur - pre) * 10000}
            if out.get('q') or out.get('fy1'):
                derived[name_] = out
                derivedOf[name_] = costRe
        mk('Gross margin', leadItem('IS_P_106', 'IS_P_105'), jre(r'(cost of goods|\bcogs\b|cost of (revenue|sales))', 'i'))
        mk('EBITDA margin', leadItem('IS_P_114', 'IS_P_113'), None)
        mk('EBIT margin', leadItem('IS_P_110', 'IS_P_109'), jre(r'(operating expenses|\bopex\b|sg&a|research|r&d)', 'i'))
        mk('Net margin', leadItem('IS_P_124', 'IS_S_656', 'IS_P_123'), None)
        mk('FCF margin', leadItem('CF_P_110', 'CF_P_115'), None)
        intel['derived'] = derived

    def fmtBps(v):
        return 'n/a' if nil(v) else (('+' if v >= 0 else '−') + tf(abs(v), 0) + 'bp')

    def marginFor(label):
        for k in derived:
            d = derived[k]
            of = derivedOf.get(k)
            if of and of.search(label) and d.get('q') and abs(d['q']['bps']) >= 25:
                return d
        return None

    if True:
        revCur = abs(CORE['rev']['per']['q']['cur']) if (CORE['rev'] and not nil(CORE['rev']['per']['q']['cur'])) else None
        revMove = pctMove(CORE['rev']['per']['q'], 'pre') if CORE['rev'] else None
        _sh = leadItem('IS_P_126', 'IS_P_125')
        shares = _sh['per']['q'].get('cur') if _sh else UNDEF
        shares = shares if _truthy(shares) else None
        if not _truthy(shares):
            ni, ep = leadItem('IS_P_124', 'IS_P_123'), leadItem('IS_P_128', 'IS_P_127')
            if ni and ep and ni['per'].get('q') and ep['per'].get('q') and _truthy(ni['per']['q']['cur']) and _truthy(ep['per']['q']['cur']):
                shares = jdiv(ni['per']['q']['cur'], ep['per']['q']['cur'])
        moves = []
        OPRATIO = jre(r'(combined|loss|expense|catastrophe|development)\s+ratio|\bAYCR\b|\bAYLR\b|net interest margin|\bnim\b|charge.?off|\bnco\b', 'i')
        PERSHARE = jre(r'(\beps\b|earnings per)', 'i')
        INCTAX = jre(r'income tax', 'i')
        for s in sections:
            for it in s['items']:
                if s['key'] == 'bs':
                    continue
                if it.get('gaapTwin'):
                    continue
                p = it['per'].get('q')
                if not ELIG(p):
                    continue
                if it['isPct'] or it['kind'] == 'ratio':
                    if not OPRATIO.search(it['label']):
                        continue
                    bps = (p['cur'] - p['pre']) * 10000
                    if not jfinite(bps) or abs(bps) < 25:
                        continue
                    fav = favSign(it['label'], jsign(bps), p['cur'])
                    if not _truthy(fav):
                        continue
                    moves.append({'label': it['label'], 'cls': MAT(it['label'], it['lt'])['cls'], 'layer': LAYER(it['label'], it['lt'], s['key']),
                                  'txt': '%s %s' % (rsub(PCT_TAIL, it['label'], ''), fmtBps(bps)),
                                  'score': (abs(bps) / 100) * MAT(it['label'], it['lt'])['w'] * PREF(it['label']), 'fav': fav})
                    continue
                d, m = p['cur'] - p['pre'], pctMove(p, 'pre')
                isPerShare = bool(R_IS.search(S(jor(it['lt'], '')))) and bool(PERSHARE.search(it['label']))
                dollar = None
                if isPerShare and _truthy(shares):
                    dollar = d * shares
                elif it['kind'] == 'money':
                    dollar = d
                mat = MAT(it['label'], it['lt'])
                rel = jdiv(abs(dollar), revCur) if (dollar is not None and _truthy(revCur)) else None
                if rel is None or rel < 0.005:
                    continue
                if COST_RE.search(it['label']) and m is not None and revMove is not None:
                    excess = m - revMove
                    fav = 0 if abs(excess) < 0.005 else -jsign(excess)
                else:
                    fav = jsign(d) if s['key'] == 'seg' else favSign(it['label'], jsign(d), p['cur'])
                if not _truthy(fav):
                    continue
                txt = '%s %s%s' % (it['label'], fmtPct(m) if m is not None else fmtM(d), (' (%s of earnings)' % fmtM(dollar)) if (isPerShare and m is not None) else '')
                if INCTAX.search(stripQual(it['label'])):
                    etr = leadItem('IS_P_122', 'IS_P_121')
                    if etr and etr['per'].get('q') and not nil(etr['per']['q']['cur']) and not nil(etr['per']['q']['pre']):
                        bps = (etr['per']['q']['cur'] - etr['per']['q']['pre']) * 10000
                        if abs(bps) >= 10:
                            txt = 'ETR %s' % fmtBps(bps)
                        else:
                            continue
                mg_ = marginFor(it['label']) if COST_RE.search(it['label']) else None
                if mg_ and m is not None and revMove is not None and jsign(mg_['q']['bps']) == fav:
                    txt = '%s %s on %s %s vs revenue %s' % (mg_['name'], fmtBps(mg_['q']['bps']), it['label'], fmtPct(m), fmtPct(revMove))
                moves.append({'label': it['label'], 'cls': mat['cls'], 'layer': LAYER(it['label'], it['lt'], s['key']), 'txt': txt, 'm': m,
                              'score': rel * mat['w'] * PREF(it['label']), 'fav': fav})
        BOTTOM = jre(r'(net income|\beps\b|earnings per)', 'i')
        for i in range(len(moves) - 1, -1, -1):
            lb = moves[i]['label']
            L = stripQual(lb)
            if (CORE['rev'] and lb == CORE['rev']['label']) or (CORE['eps'] and lb == CORE['eps']['label']):
                moves.pop(i)
                continue
            if BOTTOM.search(L):
                moves.pop(i)
                continue
        if True:
            PREMS, NONAZ, WSP = jre(r'\bpremiums\b'), jre(r'[^a-z ]'), jre(r'\s+')

            def norm(l):
                return jtrim(rsub(WSP, rsub(NONAZ, rsub(PREMS, S(l).lower(), 'premium', 0), ' ', 0), ' ', 0))
            drop = set()
            for A in moves:
                for B in moves:
                    if A is B or A['fav'] != B['fav']:
                        continue
                    na, nb = norm(A['label']), norm(B['label'])
                    if jlen(nb) > jlen(na) and nb.startswith(na + ' '):
                        drop.add(id(A))
            for i in range(len(moves) - 1, -1, -1):
                if id(moves[i]) in drop:
                    moves.pop(i)
        for i in range(len(moves) - 1, -1, -1):
            if moves[i]['label'] in DIVERGENT:
                moves.pop(i)
        if True:
            EVC = set(r['label'] for r in ((intel.get('evidence') and intel['evidence']['contradicts']) or []))
            for x in moves:
                if x['label'] in EVC:
                    x['score'] *= 1.75
        MECH_RE = jre(r'working capital|deferred (income )?tax|effect of exchange|exchange rate|restricted cash|shares outstanding|share count', 'i')
        for x in moves:
            if x['cls'] != 'capret' and MECH_RE.search(stripQual(x['label'])):
                x['cls'] = 'mech'
        if True:
            EV = intel.get('evidence')
            top = EV and EV['top']
            LEADF = {'fwd-demand', 'volume-pricing', 'retention', 'credit', 'underwriting'}
            if top and top['family'] in LEADF and top['w'] >= 0.35:
                for i in range(len(moves) - 1, -1, -1):
                    if moves[i]['label'] == top['label']:
                        moves.pop(i)
            if top:
                byLab = {}
                for r in (EV['rows'] or []):
                    byLab[r['label']] = r
                STOP = {'TOTAL', 'CURRENT', 'NON', 'CHANGE', 'NET', 'ADDS', 'AVERAGE', 'THE', 'AND'}
                TOKSPLIT = jre(r'[^A-Z0-9]+')

                def toks(l):
                    return set(w for w in TOKSPLIT.split(stripQual(l).upper()) if jlen(w) >= 3 and w not in STOP)
                tTok = toks(top['label'])
                for i in range(len(moves) - 1, -1, -1):
                    r = byLab.get(moves[i]['label'])
                    if not r or r['align'] != 'contradicts' or moves[i]['label'] == top['label'] or r['family'] != top['family']:
                        continue
                    if any(w in tTok for w in toks(moves[i]['label'])) and abs(r['pct']) < 0.5 * abs(top['pct']):
                        moves.pop(i)
        moves = js_cmp_sort(moves, lambda a, b: b['score'] - a['score'])
        core = [x for x in moves if x['cls'] != 'capret' and x['cls'] != 'mech']
        ranked = core if len(core) >= 3 else moves

        def pick(arr):
            cap, seen, out = {'seg': 2}, {}, []
            for x in arr:
                n = seen.get(x['layer']) or 0
                if n >= (cap.get(x['layer']) or 1):
                    continue
                seen[x['layer']] = n + 1
                out.append(x['txt'])
                if len(out) >= 6:
                    break
            return out
        intel['takeaways'] = {
            'positive': pick([x for x in ranked if x['fav'] > 0]),
            'negative': pick([x for x in ranked if x['fav'] < 0]),
            'suppressed': len([x for x in moves if x['cls'] == 'capret' or x['cls'] == 'mech'])}

    BIND_AT = 0.45
    if True:
        M, c = intel['magnitude'], []

        def sg(k):
            return M[k]['pct'] if M.get(k) else None

        def dol(k):
            return M[k]['dollar'] if M.get(k) else None
        revScale = abs(CORE['rev']['per']['q']['cur']) if (CORE['rev'] and _truthy(CORE['rev']['per']['q']['cur'])) else None
        revPct, revDol = sg('rev'), dol('rev')

        def clamp(v):
            return jmax(0, jmin(1, v))

        def add(key, subject, sev, weight, text, probe=None):
            if nil(sev) or not jfinite(sev):
                return
            c.append({'key': key, 'subject': subject, 'score': clamp(sev) * weight, 'text': text, 'probe': probe or [subject]})

        if True:
            A = intel.get('attribution')
            if A and A['rows'] and len(A['rows']) >= 2 and _truthy(revDol):
                drv, off = A['driver'], A['offset']

                def amt(v):
                    return fmtM(abs(v))[1:]
                WS_RUN = jre(r'\s+')
                if drv and off:
                    add('mix', off['label'], jdiv(abs(off['dollar']), abs(revDol)), 1.0,
                        ('The revision is carried by %s (%s) while %s was cut %s. A mix shift, not a broad acceleration.' % (drv['label'], fmtM(drv['dollar']), off['label'], amt(off['dollar'])))
                        if A['dir'] > 0 else
                        ('The cut is carried by %s (%s) while %s was raised %s. A mix shift, not a broad deterioration.' % (drv['label'], fmtM(drv['dollar']), off['label'], amt(off['dollar']))),
                        [off['label'], drv['label'], off['label'].split(' - ')[0], drv['label'].split(' - ')[0], jtrim(rsub(WS_RUN, stripQual(off['label']), ' ', 0)), jtrim(rsub(WS_RUN, stripQual(drv['label']), ' ', 0))])
        if True:
            gm, gp2 = derived.get('Gross margin'), leadItem('IS_P_106', 'IS_P_105')
            if gm and gm.get('q') and gm['q']['bps'] < -20 and revPct is not None and revPct > 0.002 and gp2:
                gpM = pctMove(gp2['per']['q'], 'pre')
                if gpM is not None:
                    add('margin', 'gross margin', 1 - clamp(jdiv(gpM, revPct)), 0.9,
                        'Revenue ↑ %s but gross margin %s. Only %s reached gross profit.' % (fmtPct(revPct), fmtBps(gm['q']['bps']), fmtPct(gpM)),
                        ['margin', 'gross profit'])

        def ok3(it):
            return bool(it and it['per'].get('q') and not nil(it['per']['q']['cnt']) and it['per']['q']['cnt'] >= 3)
        fcfIt = CORE['fcf'] if ok3(CORE['fcf']) else None
        fcfQ = sg('fcf') if fcfIt else None
        fcfY = pctMove(fcfIt['per']['fy1'], 'pre') if (fcfIt and fcfIt['per'].get('fy1')) else None
        worse = ({'v': fcfY, 'h': 'FY%s' % S(mg('fy1'))} if (fcfY is not None and (fcfQ is None or fcfY < fcfQ))
                 else ({'v': fcfQ, 'h': meta['q']} if fcfQ is not None else None))
        fcfDown = bool((worse and worse['v'] < -0.02) or (fcfIt and fcfQ is None and dol('fcf') is not None and _truthy(revScale) and dol('fcf') < -0.01 * revScale))
        fcfTxt = ('%s for %s' % (fmtPct(worse['v']), S(worse['h']))) if worse else fmtM(dol('fcf'))
        revSame = pctMove(CORE['rev']['per']['fy1'], 'pre') if (worse and worse['h'] != meta['q'] and CORE['rev'] and CORE['rev']['per'].get('fy1')) else revPct

        def sevCash():
            if worse:
                ratio = jmin(1, abs(jdiv(worse['v'], revSame))) if _truthy(revSame) else 1
                size = jmin(1, jdiv(abs(worse['v']), 0.10))
                return ratio * size
            fd = dol('fcf')
            rq = CORE['rev']['per']['q'] if CORE['rev'] else None
            rd = (rq['cur'] - rq['pre']) if (rq is not None and not nil(rq['cur']) and not nil(rq['pre'])) else None
            if fd is None or not _truthy(revScale):
                return 0
            ratio = jmin(1, abs(jdiv(fd, rd))) if (_truthy(rd) and rd != 0) else 1
            size = jmin(1, jdiv(abs(fd), 0.10 * revScale))
            return ratio * size
        if revSame is not None and revSame > 0.002 and fcfDown:
            add('rev_fcf', 'free cash flow', sevCash(), 1.0,
                'Revenue ↑ %s while FCF ↓ (%s). Top-line expectations improving while cash generation deteriorates.' % (fmtPct(revSame), fcfTxt), ['FCF', 'cash'])
        if revPct is not None and sg('eps') is not None and revPct > 0.002 and sg('eps') < -0.002:
            add('rev_eps', 'EPS', abs(jdiv(sg('eps'), revPct)), 1.0,
                'Revenue ↑ %s but EPS ↓ %s. Margin or below-the-line pressure offsets the top line.' % (fmtPct(revPct), fmtPct(sg('eps'))), ['EPS', 'margin'])
        if sg('eps') is not None and sg('ebitda') is not None and sg('eps') > 0.002 and sg('ebitda') < -0.002:
            add('eps_ebitda', 'earnings quality', abs(jdiv(sg('ebitda'), sg('eps'))), 1.0,
                'EPS ↑ %s but EBITDA ↓ %s. The EPS upgrade is not operating-earnings driven.' % (fmtPct(sg('eps')), fmtPct(sg('ebitda'))), ['EBITDA', 'operating'])
        if sg('ebitda') is not None and sg('ebitda') > 0.002 and fcfDown:
            add('ebitda_fcf', 'cash conversion', sevCash(), 0.8,
                'EBITDA ↑ %s but FCF ↓ (%s). The earnings upgrade is absorbed below the operating line.' % (fmtPct(sg('ebitda')), fcfTxt), ['FCF', 'cash'])

        if True:
            cv = intel.get('curve')
            if cv and cv['rev'] and len(cv['rev']) >= 2:
                n, f = cv['rev'][0], cv['rev'][-1]
                if n['pct'] is not None and f['pct'] is not None and jsign(n['pct']) != jsign(f['pct']) and abs(n['pct']) > 0.003 and abs(f['pct']) > 0.003:
                    add('horizon', 'the %s outer year' % S(f['fp']), jdiv(abs(f['pct']), abs(n['pct']) + abs(f['pct'])) * 2, 0.85,
                        'The near quarter and the outer year disagree: %s %s against %s %s.' % (S(n['fp']), fmtPct(n['pct']), S(f['fp']), fmtPct(f['pct'])), [f['fp'], 'outer year'])
        if True:
            EV = intel.get('evidence')
            LEAD = {'fwd-demand', 'volume-pricing', 'retention', 'credit', 'underwriting'}
            if EV and EV['top'] and EV['top']['family'] in LEAD and EV['top']['w'] >= 0.35 and revPct is not None and abs(revPct) > 0.002:
                t = EV['top']
                add('finvsops', t['label'], t['w'], 0.95,
                    'The P&L revision (revenue %s) and the %s evidence disagree: %s %s for %s. The operating evidence does not confirm the financial revision.'
                    % (fmtPct(revPct), 'forward-demand' if t['family'] == 'fwd-demand' else 'operating', t['label'], fmtPct(t['pct']), S(meta['q'])),
                    [t['label'], t['label'].split(' ')[0]])
        for d in (intel.get('divergence') or [])[:1]:
            add('crosshorizon', d['label'], jdiv(d['gap'], 0.05), 0.6, d['text'] + '. ' + d['tail'], [d['label'], d['label'].split(' ')[0]])

        if True:
            P_ = intel.get('price')
            if P_ and P_['relative'] is not None and P_['relative'] < -0.05 and ((revPct is not None and revPct > 0.005) or (sg('eps') is not None and sg('eps') > 0.005)):
                add('price', 'the price/revision gap', jdiv(abs(P_['relative']), 0.15), 0.7,
                    'Estimates rose while the shares fell %s (%s vs the index). The market is not paying for the revision.' % (fmtPct(P_['stock']), fmtPct(P_['relative'])), ['shares', 'price', 'index'])

        IRRELEVANT = {'price'}
        for x in c:
            x['relevant'] = x['key'] not in IRRELEVANT
        c = js_cmp_sort(c, lambda a, b: b['score'] - a['score'])
        intel['contradictions'] = c
        bindable = next((x for x in c if x['relevant'] and x['score'] >= BIND_AT), None)
        intel['contradiction'] = dict(c[0], binding=bool(bindable and bindable['key'] == c[0]['key'])) if len(c) else None
        intel['boundContradiction'] = dict(bindable, binding=True) if bindable else None

    if True:
        req = []
        M = intel['magnitude']

        def near(v):
            return None if v is None else tf(abs(v * 100), 1)
        if M.get('rev') and M['rev']['pct'] is not None:
            req.append({'id': 'headline', 'need': 'the %s revenue revision' % S(meta['q']), 'probe': [jadd(near(M['rev']['pct']), '%'), near(M['rev']['pct'])]})
        A = intel['attribution']['driver'] if intel.get('attribution') else None
        WS_RUN = jre(r'\s+')

        def probesOf(l):
            return list(dict.fromkeys(x for x in [l, S(l).split(' - ')[0], jtrim(rsub(WS_RUN, stripQual(l), ' ', 0))] if _truthy(x)))
        if A:
            req.append({'id': 'driver', 'need': 'the dominant driver (%s)' % A['label'], 'probe': probesOf(A['label'])})
        BC = intel['boundContradiction']
        if BC:
            req.append({'id': 'contradiction', 'need': 'the dominant contradiction (%s)' % BC['subject'], 'probe': BC['probe']})
        qualityFlags = []
        if True:
            A2 = intel.get('attribution') and intel['attribution']['driver']
            if A2 and A2['share'] is not None and abs(A2['share']) > 0.6:
                qualityFlags.append('driver concentration: %s is %s%% of the segment move' % (A2['label'], js_str(js_round(A2['share'] * 100))))
            gm = derived.get('Gross margin')
            if gm and gm.get('q') and abs(gm['q']['bps']) >= 25:
                qualityFlags.append('gross margin %s' % fmtBps(gm['q']['bps']))
            eng = intel.get('engine')
            if eng and eng['dominant'] and not eng['domIsRevenue']:
                qualityFlags.append('dominant lever is %s, not the top line' % eng['dominant']['label'])
            f2 = pctMove(CORE['fcf']['per']['fy1'], 'pre') if (CORE['fcf'] and CORE['fcf']['per'].get('fy1')) else None
            if f2 is not None and f2 < -0.02:
                qualityFlags.append('FY FCF %s' % fmtPct(f2))
            EV = intel.get('evidence')
            if EV and EV['top']:
                qualityFlags.append('operating evidence against: %s %s' % (EV['top']['label'], fmtPct(EV['top']['pct'])))
            elif EV and EV['confirms'] and len(EV['confirms']) and EV['confirms'][0]:
                qualityFlags.append('operating evidence confirms: %s %s' % (EV['confirms'][0]['label'], fmtPct(EV['confirms'][0]['pct'])))
        ct = intel['contradiction']
        intel['thesisSpine'] = {
            'phase': intel['phase']['key'] if intel.get('phase') else None,
            'qualityFlags': qualityFlags,
            'boundContradiction': {'key': BC['key'], 'subject': BC['subject'], 'score': BC['score']} if BC else None,
            'headline': {'label': M['rev']['label'], 'pct': M['rev']['pct']} if M.get('rev') else None,
            'eps': {'label': M['eps']['label'], 'pct': M['eps']['pct']} if M.get('eps') else None,
            'driver': {'label': A['label'], 'dollar': A['dollar'], 'share': A['share']} if A else None,
            'contradiction': {'key': ct['key'], 'subject': ct['subject'], 'score': ct['score'], 'binding': ct['binding']} if ct else None,
            'required': req}

    if True:
        w = []
        if True:
            f = intel['magnitude'].get('fcf')
            revScale = abs(CORE['rev']['per']['q']['cur']) if (CORE['rev'] and _truthy(CORE['rev']['per']['q']['cur'])) else None
            if f and ((f['pct'] is not None and f['pct'] < -0.05) or (f['pct'] is None and f['dollar'] is not None and _truthy(revScale) and f['dollar'] < -0.01 * revScale)):
                w.append({'name': 'Free cash flow', 'why': 'FCF consensus cut %s while earnings estimates held. Monitor capex/working-capital assumptions' % (fmtPct(f['pct']) if f['pct'] is not None else fmtM(f['dollar']))})
        farE = intel['curve']['eps'][-1]['pct'] if (intel.get('curve') and len(intel['curve']['eps'])) else None
        if farE is not None and intel['magnitude'].get('eps') and intel['magnitude']['eps']['pct'] is not None and intel['magnitude']['eps']['pct'] > 0.005 and farE < 0.003:
            w.append({'name': 'FY2 estimates', 'why': 'near-term EPS upgrades not yet confirmed in the outer year'})
        for m in intel['metrics']:
            if m['regime'] == 'reversing':
                w.append({'name': m['name'], 'why': 'revision direction has reversed within the cycle'})
        for m in intel['metrics']:
            if m['regime'] == 'accelerating' and m['convergence'] == 'diverging':
                w.append({'name': m['name'], 'why': 'strong revision momentum but dispersion is widening, the street has not converged'})
        for m in intel['metrics']:
            if m['persistence'] == 'High' and abs(N(jor(m['chg'], 0))) > 0.01:
                w.append({'name': m['name'], 'why': 'persistent one-way revisions (%s%% of updates same direction, %s)' % (js_str(js_round(N(jor(m['pctSame'], 0)) * 100)), fmtPct(m['chg']))})
        rr = 0
        for m in intel['metrics']:
            if m['revRisk'] == 'Elevated' and rr < 2:
                w.append({'name': m['name'], 'why': '%d direction changes since the print, elevated reversal risk, treat the current level as unsettled' % m['reversals']})
                rr += 1
        seen = set()
        wl = []
        for x in w:
            if x['name'] in seen:
                continue
            seen.add(x['name'])
            wl.append(x)
        intel['watchlist'] = wl[:5]

    if True:
        s = []
        b, M = intel['breadth'], intel['magnitude']
        dirWord = 'positive' if intel['score']['total'] >= 2 else ('negative' if intel['score']['total'] <= -2 else 'mixed')
        if intel.get('bridge') and abs(intel['bridge']['eps1'] - intel['bridge']['eps0']) >= 0.005:
            s.append('The EPS revision is %s (revision quality: %s).' % (intel['bridge']['qNote'], intel['bridge']['quality']))
        att = intel.get('attribution')
        if att and att['driver'] and att['driver']['share'] is not None and att['driver']['share'] > 0.4 and att['driver']['share'] <= 1:
            s.append('%s accounts for %s%% of the segment-level revenue revision.' % (att['driver']['label'], js_str(js_round(att['driver']['share'] * 100))))
        kv = next((m for m in intel['metrics'] if m['name'] == 'Revenue'), None)
        if kv and kv['regime'] != 'stable':
            s.append('Revenue revision momentum is %s.' % kv['regime'])
        if intel.get('curve') and _truthy(intel['curve']['epsShape']):
            s.append('Forward curve: %s.' % intel['curve']['epsShape'].lower())
        if intel['contradiction']:
            s.append(intel['contradiction']['text'])
        hi_ = intel.get('history')
        if hi_ and hi_['uniformBasis'] and hi_['curEps'] is not None and hi_['medEps'] is not None:
            s.append("This cycle's post-print EPS revision (%s) is %s than the prior-4-cycle median (%s) in magnitude."
                     % (fmtPct(hi_['curEps']), 'larger' if abs(hi_['curEps']) > abs(hi_['medEps']) else 'smaller', fmtPct(hi_['medEps'])))
        if intel.get('price') and intel['price']['relative'] is not None:
            s.append('Since the print the stock has %s %s%% (%s vs index) against %s estimate revisions%s.'
                     % ('gained' if intel['price']['stock'] >= 0 else 'lost', tf(abs(intel['price']['stock'] * 100), 1), fmtPct(intel['price']['relative']), dirWord,
                        ', a potential expectation gap' if (intel['price']['relative'] < -0.01 and intel['score']['total'] >= 2) else ''))
        intel['readthrough'] = s

    vintages, anchorView = {}, {}
    if True:
        def maxD(arr):
            v = js_sort_default([x for x in arr if _truthy(x)])
            return v[-1] if len(v) and _truthy(v[-1]) else None

        def same(a, b):
            return (a is None and b is None) or (a is not None and b is not None and abs(a - b) <= 1e-9 * jmax(1, abs(b)))
        for pk, _fp in PERIODS:
            items = []
            for s in sections:
                for it in s['items']:
                    if it['per'].get(pk):
                        items.append(it['per'][pk])
            if not len(items):
                continue
            v = {}
            for k in ANCHORS:
                v[k] = maxD([p[k + 'D'] if p[k] is not None else None for p in items])
            v['cur'] = maxD([p['curD'] for p in items])
            vintages[pk] = v
            view = {}
            prevKept = 'pre'
            for k in ANCHORS:
                if k == 'pre':
                    view['pre'] = {'distinct': True}
                    continue
                rows = [p for p in items if p[k] is not None or p[prevKept] is not None or p['cur'] is not None]
                allSamePrev = all(same(p[k], p[prevKept]) for p in rows)
                allSameCur = all(same(p[k], p['cur']) for p in rows)
                distinct = not (allSamePrev or allSameCur)
                view[k] = {'distinct': distinct, 'sameAs': None if distinct else ('cur' if allSameCur else prevKept), 'vintage': v[k]}
                if distinct:
                    prevKept = k
            anchorView[pk] = view
        meta['updated'] = jor((vintages.get('q') and vintages['q']['cur']), (vintages.get('fy1') and vintages['fy1']['cur']), meta['prepared'])
        for pk, _fp in PERIODS:
            if anchorView.get(pk):
                for k in ANCHORS:
                    if k != 'pre' and not anchorView[pk][k]['distinct']:
                        sa = anchorView[pk][k]['sameAs']
                        log('Anchor %s on %s: no row differs from %s - column collapses (Same as %s).'
                            % (k.upper(), S(meta['q']) if pk == 'q' else 'FY' + S(mg('fy1')), 'Current' if sa == 'cur' else sa.upper(),
                               'Current' if sa == 'cur' else ('Pre-Print' if sa == 'pre' else sa.upper())))

    if True:
        qItems = [it['per']['q'] for s in sections for it in s['items'] if it['per'].get('q')]
        anyPost, anyPre = any(not p['stale'] for p in qItems), any(p['pre'] is not None for p in qItems)
        staleLabels = [it['label'] for s in sections for it in s['items'] if it['per'].get('q') and it['per']['q']['stale']]
        intel['dataState'] = {
            'key': 'empty' if not len(qItems) else ('no-post-print-update' if not anyPost else ('no-pre-print-anchor' if not anyPre else 'ok')),
            'lastVintage': vintages['q']['cur'] if vintages.get('q') else None,
            'staleCount': len(staleLabels), 'staleLabels': staleLabels, 'rows': len(qItems)}
        if intel['dataState']['key'] != 'ok':
            log('Data state: %s (last consensus vintage %s; %d of %d rows unrefreshed since the print).' % (intel['dataState']['key'], intel['dataState']['lastVintage'] or 'n/a', len(staleLabels), len(qItems)))
        elif len(staleLabels):
            log('Stale rows (unrefreshed since the print, shown as "–" and excluded from breadth): %s' % ', '.join(staleLabels))
        if intel['dataState']['key'] == 'no-post-print-update':
            intel['revisionPath'] = None

    if True:
        core_ = CORE['rev'] if (CORE['rev'] and CORE['rev']['per'].get('q')) else (CORE['eps'] if (CORE['eps'] and CORE['eps']['per'].get('q')) else None)
        p = core_['per']['q'] if core_ else None
        intel['coverage'] = ({'label': core_['label'], 'preCnt': p['preCnt'], 'curCnt': p['cnt'], 'ratio': jdiv(p['cnt'], p['preCnt']) if _truthy(p['preCnt']) else None,
                              'thin': bool(p['preCnt'] > 0 and jdiv(p['cnt'], p['preCnt']) < 0.5)}
                             if (p and p['preCnt'] is not None and p['cnt'] is not None) else None)

    for s in sections:
        for it in s['items']:
            p = it['per'].get('q')
            if not p:
                continue
            if p['cur'] is not None and p['pre'] is not None:
                d = p['cur'] - p['pre']
                if not jfinite(d):
                    raise Exception('Validation failed (non-finite Δ) at ' + S(it['label']))
    allDates = {}
    histItems = []
    for sec in sections:
        for it in sec['items']:
            arr = H(it['lt'], qFp)
            if arr is None:
                continue
            post = [r for r in arr if slt(PD, r['d'])]
            for r in post:
                allDates[r['d']] = True
    dates = js_sort_default(list(allDates))
    if len(dates) > 24:
        first = dates[0]
        dates = dates[-23:]
        if dates[0] != first:
            dates.insert(0, first)
        log('History matrix capped at 24 date columns (earliest post-print date kept).')
    for sec in sections:
        for it in sec['items']:
            arr = H(it['lt'], qFp)
            if not arr:
                continue
            postA = [x for x in arr if slt(PD, x['d'])]
            vals = []
            for d in dates:
                r = atCut(postA, d)
                vals.append(pnum(it['lt'], r['v']) if r else None)
            if all(v is None for v in vals):
                continue
            histItems.append({'label': it['label'], 'section': sec['title'], 'kind': it['kind'], 'isPct': it['isPct'], 'vals': vals})

    if True:
        allR = [it for s in sections for it in s['items'] if it['per'].get('q') and it['per']['q']['cur'] is not None and not it['isPct'] and not NOGROW_RE.search(it['lt'])]
        noYoy = [it for it in allR if it['per']['q']['actYoy'] is None]
        noSeq = [it for it in allR if it['per']['q']['actPrint'] is None]
        if len(noYoy):
            log('WARNING: %d of %d rows have no YoY: no reported actual for %s%s.' % (len(noYoy), len(allR), S(meta['yoyQ']),
                ' on any row (fetch get_actuals for it, SKILL Step 4b)' if len(noYoy) == len(allR) else ' (%s%s)' % (', '.join(i['label'] for i in noYoy[:5]), ' …' if len(noYoy) > 5 else '')))
        if len(noSeq):
            log('WARNING: %d of %d rows have no Seq: no reported actual for %s%s.' % (len(noSeq), len(allR), S(meta['printPeriod']),
                ' on any row (fetch get_actuals for it, SKILL Step 4b)' if len(noSeq) == len(allR) else ' (%s%s)' % (', '.join(i['label'] for i in noSeq[:5]), ' …' if len(noSeq) > 5 else '')))
    meta['version'] = VERSION
    js_write(outPath, js_json({'meta': meta, 'cuts': cuts, 'vintages': vintages, 'anchorView': anchorView, 'anchors': ANCHORS, 'periods': PERIODS, 'sections': sections,
                               'excluded': excluded, 'intel': intel, 'scaleNotes': scaleNotes, 'flowNotes': flowNotes,
                               'history': {'fp': qFp, 'dates': dates, 'items': histItems}}, 0))
    if True:
        vq = vintages.get('q') or {}
        log('Anchors (cut -> vintage):', ' | '.join('%s %s -> %s' % (k, cuts[k], vq.get(k) or 'n/a') for k in ANCHORS), '| current ->', vq.get('cur') or 'n/a',
            '| visible:', ','.join(k for k in ANCHORS if k == 'pre' or any(anchorView.get(pk) and anchorView[pk].get(k) and anchorView[pk][k]['distinct'] for pk, _fp in PERIODS)) + ',cur')
    if len(scaleNotes):
        log('Audit notes (contract.scaleNotes, never printed on the sheet):', ' · '.join(scaleNotes))
    finalTotal = jsum(len(x['items']) for x in sections)
    log('WROTE', outPath, '| sections:', insp(len(sections)), '| line items:', insp(finalTotal), '| history dates:', insp(len(dates)), '| history rows:', insp(len(histItems)),
        '| intel: breadth', js_str(intel['breadth']['up']) + '↑/' + js_str(intel['breadth']['down']) + '↓', 'score', insp(intel['score']['total']), intel['score']['label'],
        '| bridge:', 'yes' if intel.get('bridge') else 'no', '| curve pts:', insp(len([x for x in intel['curve']['rev'] if x['pct'] is not None]) if intel.get('curve') else 0),
        '| cycles:', insp(len([r for r in intel['history']['rows'] if r['eps'] is not None or r['rev'] is not None]) if intel.get('history') else 0), '| price:', 'yes' if intel.get('price') else 'no')


if __name__ == '__main__':
    main()
