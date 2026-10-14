#!/usr/bin/env python3
"""earnings-packet-3p, contract check.

    python3 references/check_contract.py          (run in the packet working directory)

Reads render_packet.py, works out every top-level field it will dereference on the three
input files, and verifies the working directory actually supplies them. Run it BEFORE the
build: a missing field otherwise surfaces as an undefined in a cell, a thrown width error, or
silence, depending on where it lands. It also checks the shape rules that the renderer assumes
but cannot assert cheaply: the tile count, the note keys, the row-level keys the dynamic
lookups need, and the lens agreement between sections 7 and 8 that rule 5 requires.

Python standard library only. It keeps the messages and exit codes of an earlier JavaScript
edition (internal edition only); where that edition died on an uncaught TypeError, this raises
the same error text.

THE FIELD SCAN. The renderer is render_packet.py, whose every read of an input goes through
G(obj, 'key') (see its PORTING RULES), so the contract is derived from its syntax tree:

    C.x  / N.x / PR.x / M.x     ->  G(C, 'x') / G(N, 'x') / G(PR, 'x') / G(M, 'x')
    N.notes.x                   ->  G(G(N, 'notes'), 'x'), or G(n, 'x') where n = G(N, 'notes')
    const b=C.move_band; b.x    ->  b = G(C, 'move_band'); G(b, 'x')

Three sub-objects (`guide_vs_cons`, `corp`, `val_excluded`) are bound to a local first; those
locals are named in INLINE_IN_JS so they are not mistaken for aliases.

The same porting rules as render_packet.py: every read of an input goes through G(), so a
missing key is JS `undefined` and a read off null throws V8's TypeError text; truthiness is
jt(), `a || b` is jor(), String(x) is jstr(), regexes spell out JS's \\s class and use [0-9]
for \\d and \\Z for `$`.
"""
import ast
import os
import re
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from fmt import (JSError, type_error, G, jt, jor, nn, jstr, is_num, js_eq, js_gt, js_le,
                 need, set_items, JSSet, js_sort_strings, js_json_wf, UNDEF, JSFunc, pkey,
                 _locale_number, _proto_get, _PROTO, _IDX)
from jscompat import js_keys, js_str, js_to_fixed, _js_read_json, _JSErr, _truthy

RENDERER = os.path.join(HERE, 'render_packet.py')

_WS = '\t\n\x0b\x0c\r \xa0                　﻿'
_S = '[\\t\\n\\x0b\\x0c\\r \\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000\\ufeff]'
_DOT = '[^\\n\\r\\u2028\\u2029]'


def jtrim(s):
    return s.strip(_WS)


def _utf8(s):
    return s.encode('utf-16', 'surrogatepass').decode('utf-16', 'replace').encode('utf-8')


VERBOSE = os.environ.get('PACKET_VERBOSE') == '1'


def out(s):
    sys.stdout.buffer.write(_utf8(s + '\n'))


def js_join(a, sep=','):
    return sep.join('' if nn(x) else jstr(x) for x in a)


def it(o):
    return set_items(o)


def js_in(k, o):
    key = pkey(k)
    if isinstance(o, dict):
        return key in o or key in _PROTO['object'][1] or key == '__proto__'
    if isinstance(o, list):
        return (bool(_IDX.match(key)) and int(key) < len(o)) or key == 'length' or key in _PROTO['array'][1] \
            or key == '__proto__'
    raise type_error("Cannot use 'in' operator to search for '%s' in %s" % (key, jstr(o)))


def js_entries(o):
    if isinstance(o, dict):
        return [(k, o[k]) for k in js_keys(o)]
    if isinstance(o, list):
        return [(str(i), v) for i, v in enumerate(o)]
    if isinstance(o, str):
        u = o.encode('utf-16-le', 'surrogatepass')
        return [(str(i), u[2 * i:2 * i + 2].decode('utf-16-le', 'surrogatepass')) for i in range(len(u) // 2)]
    if nn(o):
        raise type_error('Cannot convert undefined or null to object')
    return []


def same(a, b):
    if isinstance(a, JSFunc) or isinstance(b, JSFunc):
        return isinstance(a, JSFunc) and isinstance(b, JSFunc) and a.name == b.name
    return js_eq(a, b)


def lit(d, k):
    k = pkey(k)
    return d[k] if k in d else _proto_get('object', k)


def js_upper(s):
    return s.upper()


def js_lower(s):
    return s.lower()


def to_f(v):
    return float(v)


INLINE_IN_JS = ('g', 'corp', 'vx')


def _gcall(node):
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'G'
            and len(node.args) == 2 and not node.keywords and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)):
        return node.args[0], node.args[1].value
    return None


def derive(src):
    tree = ast.parse(src)
    reads = {}
    notes_alias = set()
    aliases = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            g = _gcall(node.value)
            if g and isinstance(g[0], ast.Name):
                if g[0].id == 'C' and node.targets[0].id not in INLINE_IN_JS:
                    aliases.append(((node.lineno, node.col_offset), node.targets[0].id, g[1]))
                if g[0].id == 'N' and g[1] == 'notes':
                    notes_alias.add(node.targets[0].id)
    notes = set()
    for node in ast.walk(tree):
        g = _gcall(node)
        if not g:
            continue
        base, key = g
        if isinstance(base, ast.Name):
            reads.setdefault(base.id, set()).add(key)
            if base.id in notes_alias:
                notes.add(key)
        else:
            inner = _gcall(base)
            if inner and isinstance(inner[0], ast.Name) and inner[0].id == 'N' and inner[1] == 'notes':
                notes.add(key)
    used = lambda name: js_sort_strings(list(reads.get(name, ())))
    return {'C': used('C'), 'N': used('N'), 'PR': used('PR'), 'M': used('M'),
            'notes': js_sort_strings(list(notes)),
            'aliases': [[a, f, used(a)] for _, a, f in sorted(aliases)]}


CCY_FALLBACK = ('R\\$|A\\$|C\\$|HK\\$|NT\\$|S\\$|NZ\\$|[\\u20ac\\u00a3\\u00a5\\u20b9\\u20a9\\u20bd\\u20ba\\u20aa\\u20a8'
                '\\u0e3f\\u20b1\\u20ab\\u20be\\u09f3]|EUR|GBP|CHF|DKK|SEK|NOK|ISK|CAD|JPY|BRL|MXN|ZAR|INR|KRW|TWD|HKD|'
                'CNY|RMB|SGD|AUD|NZD|PLN|TRY|ILS|AED|SAR|THB|PHP|MYR|IDR|VND|CZK|HUF|RON|BGN|HRK|RSD|CLP|COP|ARS|PEN|'
                'EGP|PKR|BDT|LKR|NGN|KES|GHS|MAD|QAR|KWD|BHD|OMR|JOD|RUB|UAH|GEL|KZT|UZS|VES')


def ccy_tok_src():
    try:
        d = _js_read_json(os.path.join(HERE, '..', 'common', 'currency-tokens.json'))
        parts = []
        for t in need(G(d, 'tokens'), 'map', 'map'):
            if not isinstance(t, str):
                raise type_error('t.replace is not a function')
            parts.append(re.sub('[.*+?^${}()|[\\]\\\\]', lambda m: '\\' + m.group(0), t))
        return '|'.join(parts)
    except Exception:
        return CCY_FALLBACK


def main():
    CCY_TOK_SRC = ccy_tok_src()
    CCY_TOK = re.compile('(' + CCY_TOK_SRC + ')(?![A-Za-z])')

    cwd = os.getcwd()
    st = {'fail': 0, 'warn': 0}

    def ok(c, m, *_extra):
        if c and not VERBOSE:
            st['ok'] = st.get('ok', 0) + 1
            return
        out(('  ok  ' if c else '  FAIL') + '  ' + m)
        if not c:
            st['fail'] += 1

    def note(m):
        out('  warn  ' + m)
        st['warn'] += 1

    def load(f):
        p = os.path.join(cwd, f)
        if not os.path.exists(p):
            out('  FAIL  missing input: ' + f)
            st['fail'] += 1
            return None
        try:
            return _js_read_json(p)
        except _JSErr as e:
            out('  FAIL  unparseable: ' + f + ' (' + str(e) + ')')
            st['fail'] += 1
            return None

    C = load('packet_computed.json')
    N = load('packet_narrative.json')
    PR = load('peers.json')
    if not _truthy(C) or not _truthy(N) or not _truthy(PR):
        out('\nCONTRACT CHECK FAILED, inputs missing')
        return 1

    try:
        with open(RENDERER, 'rb') as fh:
            src = fh.read().decode('utf-8', 'replace')
    except OSError:
        if os.path.isdir(RENDERER):
            raise JSError('Error', 'EISDIR: illegal operation on a directory, read')
        raise JSError('Error', "ENOENT: no such file or directory, open '%s'" % RENDERER)
    D = derive(src)
    used = {'C': D['C'], 'N': D['N'], 'PR': D['PR'], 'M': D['M']}
    for a, _f, fields in D['aliases']:
        used.setdefault(a, fields)

    def M_():
        return G(C, 'meta')

    def meta(k):
        m = G(C, 'meta')
        return G(m, k) if jt(m) else m

    OPTIONAL_TOP = {'C': {'estimate_word', 'section_labels', 'corp'}, 'N': set(), 'PR': set()}

    for label, obj, prefix in (('packet_computed.json', C, 'C'), ('packet_narrative.json', N, 'N'),
                               ('peers.json', PR, 'PR')):
        nd = [k for k in used[prefix] if (k != 'meta' or prefix != 'C') and k not in OPTIONAL_TOP[prefix]]
        miss = [k for k in nd if G(obj, k) is UNDEF]
        ok(len(miss) == 0, label + ': all ' + str(len(nd)) + ' referenced fields present'
           + (', missing: ' + ', '.join(miss) if miss else ''))
    OPTIONAL_META = {'ccy', 'ccy_label', 'prior_label', 'periodicity', 'warnings', 'money_scale', 'reaction_basis'}
    nd = [k for k in used['M'] if k not in OPTIONAL_META]
    miss = [k for k in nd if not jt(M_()) or G(M_(), k) is UNDEF]
    ok(len(miss) == 0, 'meta: all ' + str(len(nd)) + ' required fields present'
       + (', missing: ' + ', '.join(miss) if miss else ''))

    EQ = {'€': 'EUR', '₹': 'INR', '₩': 'KRW', '₪': 'ILS', '₭': 'LAK',
          '₱': 'PHP', '₫': 'VND', '₽': 'RUB', '₺': 'TRY', '₴': 'UAH',
          '฿': 'THB', 'R$': 'BRL', 'A$': 'AUD', 'C$': 'CAD', 'HK$': 'HKD', 'NT$': 'TWD',
          'S$': 'SGD', 'NZ$': 'NZD', 'RM': 'MYR', 'Rp': 'IDR', 'Rs': 'INR', '₨': 'INR',
          'Fr': 'CHF', 'CHF': 'CHF', 'Kč': 'CZK', 'zł': 'PLN', 'Ft': 'HUF',
          'lei': 'RON', 'R': 'ZAR', '₾': 'GEL', '৳': 'BDT'}
    AMBIG = {'¥': ['JPY', 'CNY', 'RMB'],
             '$': ['USD', 'CAD', 'AUD', 'NZD', 'SGD', 'HKD', 'TWD', 'MXN', 'BRL', 'ARS', 'CLP', 'COP'],
             '£': ['GBP', 'EGP', 'SYP', 'LBP'],
             'kr': ['SEK', 'NOK', 'DKK', 'ISK'],
             '₱': ['PHP'],
             '₡': ['CRC']}
    SUBSET = {'GBPence', 'GBp', 'ZARc', 'USDc', 'EURc'}

    def norm(t):
        x = jtrim(jstr(t))
        if x in SUBSET:
            return x
        e = lit(EQ, x)
        if jt(e):
            return e
        u = js_upper(x)
        return 'CNY' if u == 'RMB' else u

    def agrees(hint, sym):
        cand = lit(AMBIG, jtrim(jstr(sym)))
        if not jt(cand):
            return same(norm(hint), norm(sym))
        if not isinstance(cand, list):
            raise type_error('cand.map is not a function')
        nh = norm(hint)
        return any(same(norm(c), nh) for c in cand)

    SUB = re.compile('^(GBPence|GBp|ZARc|USDc|EURc)(?![A-Za-z])')
    TOK = re.compile('^(' + CCY_TOK_SRC + ')(?![A-Za-z])', re.I)

    def tok(t):
        a = SUB.search(t)
        if a:
            return a.group(1)
        b = TOK.search(t)
        return b.group(1) if b else None

    hints = []
    for k in ('reporting_currency', 'primary_currency', 'currency'):
        v = meta(k)
        if isinstance(v, str) and jtrim(v):
            if jtrim(v) not in hints:
                hints.append(jtrim(v))
    lbl = meta('ccy_label')
    if isinstance(lbl, str):
        m = tok(jtrim(lbl))
        if m and m not in hints:
            hints.append(m)
    declared = meta('ccy')
    arr = list(hints)
    if not arr:
        ok(True, 'currency: no non-USD signal in the input')
    elif declared is UNDEF:
        ok(False, 'currency: input carries ' + ', '.join(arr) + ' but meta.ccy is unset, so every '
           + 'money cell would print "$". Set meta.ccy and meta.ccy_label.')
    else:
        odd = [h for h in arr if not agrees(h, declared)]
        amb = lit(AMBIG, jtrim(jstr(declared)))
        ok(len(odd) == 0, ('currency: meta.ccy "' + jstr(declared) + '" CONFLICTS with ' + ', '.join(odd)
                           + ' elsewhere in the input. Confirm the reporting currency before building.')
           if odd else ('currency: meta.ccy "' + jstr(declared) + '" agrees with the input'
                        + (' (an ambiguous symbol, resolved by the label to ' + jstr(norm(arr[0])) + ')'
                           if jt(amb) else '')))

    declared = meta('ccy')
    lbl = jstr(jor(meta('ccy_label'), ''))
    known = bool(CCY_TOK.search(lbl)) or bool(re.search('\\b(?:GBPence|GBp|ZARc|USDc|EURc)\\b', lbl, re.A))
    if declared is not UNDEF and jtrim(jstr(declared)) != '$' and not known:
        note('currency: meta.ccy is "' + jstr(declared) + '" but ccy_label ' + js_json_wf(lbl)
             + ' names no currency this gate recognises, so the reporting currency could NOT be '
             + 'cross-checked. Put the ISO code in the label (e.g. "IDR in billions") or add the '
             + 'code to TOK in check_contract.js.')

    nd = D['notes']
    miss = [k for k in nd if not jt(G(N, 'notes')) or G(G(N, 'notes'), k) is UNDEF]
    ok(len(miss) == 0, 'notes: all ' + str(len(nd)) + ' keys present'
       + (', missing: ' + ', '.join(miss) if miss else ''))

    INTRINSIC = {'length', 'name', 'map', 'filter', 'slice', 'join', 'push', 'find', 'some', 'every', 'concat',
                 'indexOf', 'forEach', 'toString', 'replace', 'split', 'trim', 'includes'}
    for alias, field, fields in D['aliases']:
        if alias == 'M':
            continue
        target = G(C, field)
        if not jt(target) or not isinstance(target, dict):
            continue
        nd = [k for k in fields if k not in INTRINSIC]
        miss = [k for k in nd if G(target, k) is UNDEF]
        ok(len(miss) == 0, 'C.' + field + ' (read as `' + alias + '`): all ' + str(len(nd))
           + ' referenced fields present' + (', missing: ' + ', '.join(miss) if miss else ''))

    tiles = G(C, 'tiles')
    ok(isinstance(tiles, list) and len(tiles) == 12,
       'tiles: 12 entries, two rows of six (got ' + jstr(G(jor(G(C, 'tiles'), []), 'length')) + ')')
    ok(all(jt(t) and isinstance(G(t, 'label'), str) and isinstance(G(t, 'value'), str)
           for t in need(jor(G(C, 'tiles'), []), 'every', '(C.tiles || []).every')),
       'tiles: every entry has a label and a pre-formatted string value')
    h = meta('hist')
    ok(isinstance(h, list) and len(h) == 4, 'meta.hist: four reported quarters')
    for k in ('fy_prev', 'fy0', 'fy1'):
        ok(bool(re.match('^[0-9]{4}\\Z', jstr(G(G(C, 'meta'), k)))), 'meta.' + k + ' is a bare 4-digit year, not FY-prefixed')
    wm = G(N, 'what_matters')
    ok(isinstance(wm, list) and len(wm) == 5, 'what_matters: five pointers')
    for k in ('risks', 'catalysts'):
        a = G(N, k)
        ok(isinstance(a, list) and len(a) == 5, k + ': five items')
        ok(all(jt(x) and jt(G(x, 'text')) and jt(G(x, 'source'))
               for x in need(jor(G(N, k), []), 'every', '(N[k] || []).every')),
           k + ': every item carries a source')
        pairs = [(i + 1, jstr(jor(G(x, 'source') if jt(x) else x, '')))
                 for i, x in enumerate(need(jor(G(N, k), []), 'map', '(N[k] || []).map'))]
        joined = [(i, s) for i, s in pairs if re.search(';|' + _S + '/' + _S, s)]
        ok(len(joined) == 0, k + ': every source names exactly one document'
           + (', joined on: ' + ', '.join('#' + str(i) + ' ' + js_json_wf(s) for i, s in joined) if joined else ''))

    keys = [G(c, 'key') for c in need(jor(G(C, 'rev_cols'), []), 'map', '(C.rev_cols || []).map')]
    missing = []
    for r in need(jor(G(C, 'revisions'), []), 'flatMap', '(C.revisions || []).flatMap'):
        missing += [k for k in keys if not js_in(k, r)]
    ok(len(keys) > 0 and len(missing) == 0,
       'revisions: every row carries a value for each rev_cols key (' + js_join(keys, ', ') + ')')
    ok(len([c for c in need(jor(G(C, 'rev_cols'), []), 'filter', '(C.rev_cols || []).filter')
            if jt(G(c, 'base'))]) == 1,
       'rev_cols: exactly one column marked base for the delta comparison')
    spineCodes = [G(s, 'code') for s in need(jor(G(C, 'spine'), []), 'map', '(C.spine || []).map')]
    modelCodes = JSSet([G(m, 'code') for m in need(jor(G(C, 'model'), []), 'map', '(C.model || []).map')])
    miss = [c for c in spineCodes if not modelCodes.has(c)]
    ok(len(miss) == 0, 'model: a row for every spine code' + (', missing: ' + js_join(miss, ', ') if miss else ''))
    subs = [s for s in (G(s, 'sub') for s in need(jor(G(C, 'spine'), []), 'map', '(C.spine || []).map'))
            if jt(s) and not js_eq(s, 'yoy')]
    missSub = [c for c in subs if not modelCodes.has(c)]
    ok(len(missSub) == 0, 'model: a row for every margin sub-row code'
       + (', missing: ' + js_join(missSub, ', ') if missSub else ''))
    bands = ['val_metrics', 'growth_metrics', 'op_metrics']
    bad = []
    for b in bands:
        for m in it(jor(G(PR, b), [])):
            if not jt(G(m, 'label')) or not jt(G(m, 'key')) or not jt(G(m, 'fmt')):
                bad.append(b + ':' + jstr(jor(G(m, 'label'), '?')))
                continue
            if not any(js_in(G(m, 'key'), r) for r in need(jor(G(PR, 'rows'), []), 'some', '(PR.rows || []).some')):
                bad.append(b + ':' + jstr(G(m, 'key')) + ' on no row')
    ok(len(bad) == 0, 'peer bands: every metric has label/key/fmt and a matching row field'
       + (', bad: ' + ', '.join(bad) if bad else ''))
    fmts = {'money', 'mult', 'pct1', 'pct100', 'pctchg', 'ratio', 'raw'}
    bad = []
    for b in bands:
        bad += [G(m, 'fmt') for m in need(jor(G(PR, b), []), 'filter', '(PR[b] || []).filter')
                if not (isinstance(G(m, 'fmt'), str) and G(m, 'fmt') in fmts)]
    ok(len(bad) == 0, 'peer bands: every fmt is known' + (', unknown: ' + js_join(bad, ', ') if bad else ''))
    n = G(jor(G(PR, 'rows'), []), 'length')
    ok(js_gt(n, 0) and js_le(n, 6), 'peers.rows: ' + jstr(n) + ' tickers, ceiling is 6 (lead plus at most 5 comparables)')

    def normMult(s):
        t = js_lower(jstr(jor(s, '')))
        t = re.sub('\\((?:adj\\.?|adjusted)\\)', ' ', t)
        t = re.sub('\\b(?:ntm|ltm|fwd|forward|trailing|adj\\.?|adjusted|unadjusted)\\b', ' ', t, flags=re.A)
        return re.sub('[^a-z0-9/]+', '', t)

    lens = [G(v, 'name') for v in need(jor(G(C, 'valuation'), []), 'map', '(C.valuation || []).map')]
    labels = [normMult(G(m, 'label')) for m in need(jor(G(PR, 'val_metrics'), []), 'map', '(PR.val_metrics || []).map')]
    strayLens = [x for x in need(jor(G(C, 'val_excluded'), []), 'filter', '(C.val_excluded || []).filter')
                 if normMult(G(x, 'name')) in labels]
    ok(len(strayLens) == 0, 'peer band shows no multiple excluded from the lens'
       + (', stray: ' + js_join([G(x, 'name') for x in strayLens], ', ') if strayLens else ''))
    missing = [nm for nm in lens if normMult(nm) not in labels]
    ok(len(missing) == 0, 'peer band covers every lens multiple (' + js_join(lens, ', ') + ')'
       + (', missing: ' + js_join(missing, ', ') if missing else ''))

    key = G(C, 'val_sector_key')
    try:
        CFG = _js_read_json(os.path.join(HERE, 'sector_config.json'))
    except Exception:
        CFG = None
    if jt(key) and jt(CFG) and jt(G(CFG, key)):
        want = jor(G(G(CFG, key), 'lens'), [])
        have = [normMult(G(v, 'name')) for v in need(jor(G(C, 'valuation'), []), 'map', '(C.valuation || []).map')]
        excl = [normMult(G(x, 'name')) for x in need(jor(G(C, 'val_excluded'), []), 'map', '(C.val_excluded || []).map')]
        unaccounted = [m for m in need(want, 'filter', 'want.filter')
                       if normMult(m) not in have and normMult(m) not in excl]
        ok(len(unaccounted) == 0,
           "lens: every multiple sector_config.json gives '" + jstr(key) + "' (" + js_join(want, ', ')
           + ') is either shown or named in val_excluded with a reason'
           + (', unaccounted: ' + js_join(unaccounted, ', ') + ' (substitution is allowed, silence is not)'
              if unaccounted else ''))
        survivor = next((m for m in need(want, 'find', 'want.find') if normMult(m) in have), UNDEF)
        leadMetric = next((m for m in need(jor(G(PR, 'val_metrics'), []), 'find', '(PR.val_metrics || []).find')
                           if jt(G(m, 'lead'))), UNDEF)
        if jt(survivor) and jt(leadMetric):
            okLead = normMult(G(leadMetric, 'label')) == normMult(survivor)
            ok(okLead, "lens: the peer band's lead multiple is the sector's own leading multiple ('"
               + jstr(survivor) + "')"
               + ('' if okLead else ", got '" + jstr(G(leadMetric, 'label')) + "'. R17 divides PEG by the lead, "
                  + 'and the section 8 reader reads it first'))
    elif jt(key):
        note("val_sector_key '" + jstr(key) + "' is not in sector_config.json, so the lens cannot be "
             + 'cross-checked against the sector. Confirm the key with references/sector.py')

    multKeys = [G(m, 'key') for m in need(jor(G(PR, 'val_metrics'), []), 'filter', '(PR.val_metrics || []).filter')
                if js_eq(G(m, 'fmt'), 'mult')]
    bad = []
    for r in it(jor(G(PR, 'rows'), [])):
        for k in multKeys:
            v = G(r, k)
            if is_num(v) and v < 0:
                bad.append(jstr(G(r, 't')) + '.' + jstr(k))
    ok(len(bad) == 0, 'no negative multiple in the peer rows' + (': ' + ', '.join(bad) if bad else ''))
    nulls = []
    for r in it(jor(G(PR, 'rows'), [])):
        for m in it(jor(G(PR, 'val_metrics'), [])):
            if js_eq(G(m, 'fmt'), 'mult') and nn(G(r, G(m, 'key'))) and not jt(G(r, jstr(G(m, 'key')) + '_note')):
                nulls.append(jstr(G(r, 't')) + '.' + jstr(G(m, 'key')))
    if nulls:
        note('peer multiples null with no reason, they will print an en dash: ' + ', '.join(nulls))

    ok(chr(0x2014) not in js_json_wf(N), 'narrative: no em dash')
    FRAGMENT = ['valuation_excluded_reason', 'peers_read', 'peers_comparability']
    STOP = re.compile('[.!?]\\Z')
    bad = [(k, v) for k, v in js_entries(jor(G(N, 'notes'), {}))
           if k not in FRAGMENT and not STOP.search(jtrim(jstr(v)))]
    ok(len(bad) == 0, 'notes: every standalone note ends with terminal punctuation'
       + (', missing on: ' + ', '.join(k for k, _ in bad) if bad else ''))
    bad = [k for k in FRAGMENT if jt(G(N, 'notes')) and STOP.search(jtrim(jstr(jor(G(G(N, 'notes'), k), ''))))]
    ok(len(bad) == 0, 'notes: fragment notes (' + ', '.join(FRAGMENT)
       + ') carry no terminal stop' + (', stray stop on: ' + ', '.join(bad) if bad else ''))

    PERIODICITY = ['quarterly', 'semi_annual', 'annual']
    p = meta('periodicity')
    if p is not UNDEF:
        has = isinstance(p, str) and p in PERIODICITY
        ok(has, 'meta.periodicity is one of ' + ' / '.join(PERIODICITY)
           + (" ('" + p + "')" if has else ', got: ' + js_json_wf(p)))
        if has and p != 'quarterly':
            pl = jtrim(jstr(jor(meta('prior_label'), '')))
            ok(len(pl) > 0, 'meta.prior_label supplied for ' + ('an' if p == 'annual' else 'a')
               + " '" + p + "' axis (section 4 "
               + 'otherwise names hist[0], the wrong comparison base on a non-quarterly grid)')
            h = jor(meta('hist'), [])
            if isinstance(h, list) and len(h) and pl and not any(js_eq(x, pl) for x in h):
                note("meta.prior_label '" + pl + "' is not one of hist " + js_json_wf(h)
                     + '; confirm it is the true prior-year comparable of the target')
    else:
        h = jor(meta('hist'), [])
        if isinstance(h, list) and len(h) and len(h) != 4:
            note('meta.hist has ' + str(len(h)) + ' periods but meta.periodicity is absent, so the '
                 + 'grid is treated as four quarters. Declare the axis if it is not quarterly.')

    MONEY_SCALE = ['B', 'M']
    ms = meta('money_scale')
    if ms is not UNDEF:
        has = isinstance(ms, str) and ms in MONEY_SCALE
        ok(has, "meta.money_scale is 'B' or 'M'" + (" ('" + ms + "')" if has else ', got: ' + js_json_wf(ms)))
    else:
        mx = 0
        for r in it(jor(G(C, 'model'), [])):
            if js_eq(G(jor(r, {}), 'fmt'), '$M'):
                for _k, v in js_entries(jor(G(jor(r, {}), 'cells'), {})):
                    if is_num(v) and abs(v) > mx:
                        mx = abs(v)
        if jt(mx) and mx < 1000:
            note('meta.money_scale is absent, so every $M cell prints at $B scale, but the '
                 + 'largest money level in the spine is ' + js_to_fixed(mx, 1) + 'M and would print as $'
                 + js_to_fixed(to_f(mx) / 1000, 2) + 'B. Set money_scale to "M" for this issuer.')

    ms = jor(meta('money_scale'), 'B')
    isM = js_eq(ms, 'M')

    def fmtM(v):
        b = v if isM else to_f(v) / 1000
        d = 1 if isM else 2
        t = _locale_number(abs(b), d)
        return ('(' if v < 0 else '') + '$' + t + ('M' if isM else 'B') + (')' if v < 0 else '')

    GRPS = ('model', 'segments', 'seg_ebit', 'kpis')
    hits = []
    for grp in GRPS:
        for row in it(jor(G(C, grp), [])):
            f = G(jor(row, {}), 'fmt')
            if jt(f) and not js_eq(G(row, 'fmt'), '$M'):
                continue
            cells = [(p_, v) for p_, v in js_entries(jor(G(jor(row, {}), 'cells'), {})) if is_num(v)]
            byText = {}
            for p_, v in cells:
                byText.setdefault(fmtM(v), []).append((p_, v))
            for t, group in byText.items():
                for i in range(len(group)):
                    for j in range(i + 1, len(group)):
                        (pa, va), (pb, vb) = group[i], group[j]
                        den = max(abs(to_f(va)), abs(to_f(vb)))
                        if den and abs(to_f(va) - to_f(vb)) / den > 0.01:
                            hits.append(jstr(G(row, 'name')) + ': ' + pa + '=' + js_str(va) + ' and ' + pb + '='
                                        + js_str(vb) + ' both print ' + t)
    ok(len(hits) == 0,
       "money cells: no two values in one row print the same string at money_scale '" + jstr(ms)
       + "'" + ((' (' + str(len(hits)) + ' collision(s)): ' + ' | '.join(hits[:4])
                 + (' | ...' if len(hits) > 4 else '')) if hits else ''))

    pcthits = []
    for grp in GRPS:
        for row in it(jor(G(C, grp), [])):
            if not js_eq(G(jor(row, {}), 'fmt'), '%'):
                continue
            cells = [v for _p, v in js_entries(jor(G(jor(row, {}), 'cells'), {})) if is_num(v)]
            byText = {}
            for v in cells:
                byText.setdefault(_locale_number(v, 1), []).append(v)
            worst = 0
            for group in byText.values():
                for i in range(len(group)):
                    for j in range(i + 1, len(group)):
                        den = max(abs(to_f(group[i])), abs(to_f(group[j])))
                        if den and abs(to_f(group[i]) - to_f(group[j])) / den > 0.004:
                            worst += 1
            if worst:
                pcthits.append(jstr(G(row, 'name')) + ' (' + str(worst) + ')')
    if pcthits:
        note('percentage rows where two materially different values print the same string at '
             + 'one decimal: ' + ', '.join(pcthits) + '. If the line moves in basis points, set '
             + "its fmt to '%2' so the series is legible; a genuinely flat rate needs no change.")

    NOTE_MIDSENTENCE = ['peers_caveat', 'valuation_excluded_reason']
    bad = []
    for k, raw in js_entries(jor(G(N, 'notes'), {})):
        v = jtrim(jstr('' if nn(raw) else raw))
        if not v:
            continue
        if k in NOTE_MIDSENTENCE:
            if re.match('[A-Z][a-z]', v):
                bad.append(k + ' should start lowercase')
        elif re.match('[a-z]', v):
            bad.append(k + ' should start with a capital')
    ok(len(bad) == 0,
       'notes: leading case matches the splice position (' + ', '.join(NOTE_MIDSENTENCE)
       + ' start lowercase, every other note starts with a capital)'
       + (', wrong on: ' + ' | '.join(bad) if bad else ''))

    NO_EBIT_KEYS = ['banking', 'financials_nonbank', 'insurance', 'reit']
    key = jor(meta('sector_key'), '')
    if isinstance(key, str) and key in NO_EBIT_KEYS:
        bad = [t for t in need(jor(G(C, 'tiles'), []), 'filter', '(C.tiles || []).filter')
               if re.search('\\bEBIT\\b', jstr(jor(G(t, 'label'), '')), re.I | re.A)]
        ok(len(bad) == 0,
           "tiles: sector_key '" + key + "' has no EBIT, so no tile label may say EBIT "
           + '(substitute from sector-config.md B12 and label the tile with the measure)'
           + (', found: ' + js_join([js_json_any(G(t, 'label')) for t in bad], ', ') if bad else ''))

        def words(l):
            s = js_lower(jstr(jor(l, '')))
            s = re.sub('^consensus' + _S + '+', '', s, count=1)
            s = re.sub(_S + '*y/y' + _S + '*%?\\Z', '', s, count=1)
            return jtrim(s)
        lvl, yoy = G(jor(G(C, 'tiles'), []), 3), G(jor(G(C, 'tiles'), []), 5)
        if jt(lvl) and jt(yoy):
            a, b = words(G(lvl, 'label')), words(G(yoy, 'label'))
            ok(a == b, 'tiles: the row 1 profit level and its Y/Y name the SAME measure'
               + (" ('" + a + "')" if a == b else ', got ' + js_json_wf(a) + ' and ' + js_json_wf(b)))
        w = jor(jor(meta('warnings'), G(C, 'warnings')), [])
        wn = js_lower(js_join(need(w, 'join', '((C.meta && C.meta.warnings) || C.warnings || []).join'), ' '))
        ok(bool(re.search('tile strip|profit tile|substitut', wn)),
           'warnings: the row 1 profit substitution is recorded, naming the rung taken '
           + '(B12 requires it: the comparison is legitimate only while it is disclosed)')

    REACTION_BASIS = ['intraday', 'after_close']
    rb = meta('reaction_basis')
    if rb is not UNDEF:
        has = isinstance(rb, str) and rb in REACTION_BASIS
        ok(has, 'meta.reaction_basis is one of ' + ' / '.join(REACTION_BASIS)
           + (" ('" + rb + "')" if has else ', got: ' + js_json_wf(rb)))

    NEGTILE = re.compile('^\\(' + _DOT + '*\\)\\Z|^[–-]|^[^0-9(]{0,3}[–-]' + _S + '*[0-9]')

    def negtile(t):
        return (jt(t) and isinstance(G(t, 'value'), str) and bool(NEGTILE.search(jtrim(G(t, 'value'))))
                and bool(re.search('[0-9]', G(t, 'value'))))
    tl = need(jor(G(C, 'tiles'), []), 'filter', '(C.tiles || []).filter')
    bad = [t for t in tl if negtile(t) and nn(G(t, 'sign'))]
    ok(len(bad) == 0,
       'tiles: every tile printing a negative number carries `sign` (pass 1 fails a negative '
       + 'rendered black)' + (', missing on: ' + ' | '.join(jstr(G(t, 'label')) + ' = ' + jstr(G(t, 'value'))
                                                            for t in bad) if bad else ''))
    tl = need(jor(G(C, 'tiles'), []), 'filter', '(C.tiles || []).filter')
    wrong = [t for t in tl if negtile(t) and js_gt(G(t, 'sign'), 0)]
    ok(len(wrong) == 0,
       'tiles: no tile printing a negative number carries a positive `sign`'
       + (', wrong on: ' + js_join([G(t, 'label') for t in wrong], ' | ') if wrong else ''))

    m = jor(G(C, 'meta'), {})
    fwd = [x for x in (G(m, 'target'), G(m, 'next_q'), G(m, 'fy0'), G(m, 'fy1')) if jt(x)]
    rows = jor(G(C, 'model'), [])

    def cellAt(r, p_):
        return jt(G(r, 'cells')) and not nn(G(G(r, 'cells'), p_))

    def hasFwd(r):
        return any(cellAt(r, p_) for p_ in fwd)

    def hasHist(r):
        return any(cellAt(r, p_) for p_ in need(jor(G(m, 'hist'), []), 'some', '(m.hist || []).some'))
    declaredSet = JSSet([G(x, 'code') for x in need(jor(G(C, 'dispersion'), []), 'map', '(C.dispersion || []).map')])
    contradict = [jstr(G(r, 'name')) + ' (' + jstr(G(r, 'code')) + ')'
                  for r in need(rows, 'filter', 'rows.filter')
                  if declaredSet.has(G(r, 'code')) and hasHist(r) and not hasFwd(r)]
    ok(len(contradict) == 0,
       'every row listed in `dispersion` carries a consensus value in at least one forward '
       + 'period (a graded row with actuals and no consensus is the wrong-code fingerprint, '
       + 'not a coverage gap)')
    noFwd = [G(r, 'name') for r in need(rows, 'filter', 'rows.filter') if hasHist(r) and not hasFwd(r)]
    if noFwd:
        out('  note  ' + str(len(noFwd)) + ' of ' + jstr(G(rows, 'length')) + ' model row(s) carry actuals '
            + 'and no forward estimate, which is the designed state for a line the Street does not '
            + 'forecast: ' + js_join(noFwd, ', ') + '. Report the count; it is not a defect.')

    m = jor(G(C, 'meta'), {})
    if G(m, 'feed_complete') is UNDEF:
        note('meta.feed_complete is not declared, so fetch completeness is UNVERIFIED for this '
             + 'run. get_actuals silently returns 25 rows by default and reports has_more in the '
             + 'payload; record it as true once every band was fetched to exhaustion.')
    else:
        ok(G(m, 'feed_complete') is True, 'meta.feed_complete declares the fetch reached the end of every band')

    MON = {'jan': 0, 'feb': 1, 'mar': 2, 'apr': 3, 'may': 4, 'jun': 5, 'jul': 6, 'aug': 7, 'sep': 8,
           'oct': 9, 'nov': 10, 'dec': 11}

    def parseDate(s):
        if not jt(s):
            return None
        t = jtrim(jstr(s))
        m2 = re.match('^([0-9]{4})-([0-9]{2})-([0-9]{2})\\Z', t)
        if m2:
            return date_utc(int(m2.group(1)), int(m2.group(2)) - 1, int(m2.group(3)))
        m2 = re.match('^([0-9]{1,2})' + _S + '+([A-Za-z]{3})[a-z]*' + _S + '+([0-9]{4})\\Z', t)
        if m2 and m2.group(2).lower() in MON:
            return date_utc(int(m2.group(3)), MON[m2.group(2).lower()], int(m2.group(1)))
        return None
    lastPrint = parseDate(G(m, 'last_print_date'))
    if jt(lastPrint):
        stale = []
        for r in need(jor(G(C, 'dispersion'), []), 'filter', '(C.dispersion || []).filter'):
            u = parseDate(G(r, 'upd'))
            if jt(u) and u <= lastPrint:
                stale.append(r)
        ok(len(stale) == 0,
           'every graded consensus was struck AFTER the last print (an update date at or before '
           + 'it is the pre-print row the feed returns for a reported quarter, not a current '
           + 'estimate)')
    elif jt(G(m, 'last_print_date')):
        note('meta.last_print_date "' + jstr(G(m, 'last_print_date')) + '" did not parse as a date, so the '
             + 'consensus-vintage check did not run.')

    rows = jor(G(C, 'model'), [])
    noteModel = jor(G(G(N, 'notes'), 'model') if jt(G(N, 'notes')) else G(N, 'notes'), '')
    star = lambda r: bool(re.search('\\*', jstr(jor(G(r, 'name'), ''))))
    starred = [r for r in need(rows, 'filter', 'rows.filter') if star(r)]
    nm = jstr(noteModel)
    ok(len(starred) == 0 or bool(re.search('\\*', nm)) or bool(re.search('reported|GAAP|non-GAAP', nm, re.I | re.A)),
       'a row marked with the rule-3 asterisk is disclosed in notes.model')
    mismatched = [r for r in need(rows, 'filter', 'rows.filter') if js_eq(G(r, 'rung'), 'gaap') and not star(r)]
    ok(len(mismatched) == 0, 'every row whose `rung` is "gaap" carries the rule-3 asterisk in its name')
    starNoRung = [r for r in need(rows, 'filter', 'rows.filter')
                  if star(r) and jt(G(r, 'rung')) and not js_eq(G(r, 'rung'), 'gaap')]
    ok(len(starNoRung) == 0, 'no row carries the asterisk while declaring an adjusted rung')

    m = jor(G(C, 'meta'), {})
    for band in ('model', 'kpis', 'segments', 'seg_ebit', 'dispersion'):
        for r in it(jor(G(C, band), [])):
            if jt(r):
                G(r, 'code')
    empty = []
    for band in ('model', 'kpis', 'segments'):
        for r in it(jor(G(C, band), [])):
            cells = jor(G(r, 'cells'), {})
            anyv = any(not nn(G(cells, k)) for k, _v in js_entries(cells))
            if not anyv:
                empty.append(band + ':' + jstr(jor(G(r, 'name'), G(r, 'code'))))
    ok(len(empty) == 0,
       'every declared row carries at least one value somewhere in the grid (a row declared in '
       + 'the plan and empty in every period is a code that resolved to nothing, not a line the '
       + 'issuer does not report)')
    fn = G(m, 'feed_notes')
    if fn is UNDEF:
        note('meta.feed_notes is not declared, so any note the feed returned with the data - a '
             + 'silent GAAP-for-adjusted substitution is the measured case - is UNRECORDED for this '
             + 'run. Carry the response `notes` array even when it is empty.')
    elif isinstance(fn, list) and len(fn):
        subs = [x for x in fn if re.search('substitut|used as|unavailable|fallback', jstr(x), re.I)]
        nts = G(N, 'notes')
        printed = ' '.join(jstr(v) for _k, v in js_entries(nts) if isinstance(v, str)) if isinstance(nts, dict) else ''
        disclosed = bool(re.search(r'\b(GAAP|reported)\b', printed, re.I)) and bool(
            re.search(r'substitut|used as|in place of|instead of|unavailable|not available', printed, re.I))
        if len(subs) and disclosed:
            note('the feed reported %d substitution(s) and a section note discloses it: %s'
                 % (len(subs), '; '.join(jstr(x) for x in subs)))
        else:
            ok(len(subs) == 0, 'the feed reported no silent substitution for this packet'
               + ('' if not len(subs) else ' (disclose it in a section note in packet_narrative.json '
                  '`notes`, naming the line and the reported/GAAP basis used in place of the adjusted one)'))

    out('\nCONTRACT CHECK ' + ('FAILED, ' + str(st['fail']) + ' problem(s)' if st['fail'] else 'PASSED')
        + (', ' + str(st['warn']) + ' warning(s)' if st['warn'] else '')
        + ('' if VERBOSE else ' (%d checks passed, not listed)' % st.get('ok', 0)))
    return 1 if st['fail'] else 0


def js_json_any(v):
    return UNDEF if v is UNDEF else js_json_wf(v)


def date_utc(y, mo, d):
    if 0 <= y <= 99:
        y += 1900
    y += mo // 12
    mo %= 12
    yy = y - (1 if mo < 2 else 0)
    era = (yy if yy >= 0 else yy - 399) // 400
    yoe = yy - era * 400
    mp = (mo + 10) % 12
    doy = (153 * mp + 2) // 5
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    days = era * 146097 + doe - 719468 + (d - 1)
    return days * 86400000


def _uncaught(e):
    if isinstance(e, JSError):
        head = '%s: %s' % (e.name, e.message)
    elif isinstance(e, RecursionError):
        head = 'RangeError: Maximum call stack size exceeded'
    else:
        head = '%s: %s' % (type(e).__name__, e)
    tb = ''.join('    at ' + ln.strip() + '\n' for ln in traceback.format_tb(e.__traceback__) if ln.strip())
    sys.stdout.flush()
    sys.stderr.buffer.write(_utf8(RENDERER.replace('render_packet.py', 'check_contract.py') + '\n\n' + head + '\n' + tb))
    sys.stderr.flush()
    sys.exit(1)


if __name__ == '__main__':
    try:
        rc = main()
    except SystemExit:
        raise
    except BaseException as _e:
        _uncaught(_e)
    sys.stdout.flush()
    sys.exit(rc)
