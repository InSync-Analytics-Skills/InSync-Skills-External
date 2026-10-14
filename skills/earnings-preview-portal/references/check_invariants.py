#!/usr/bin/env python3
"""SKILL VERSION: 9.0.5

check_invariants.py - DOCUMENT-LEVEL INVARIANTS.

Why this file exists, and why it is not another fixture. A regression bench (internal edition
only) asserts that a control still renders what it rendered last time, which makes a defect present at FREEZE time the
specification. These checks are properties of ANY document: they survive re-freezing, apply to
controls that do not exist yet, and fail on standing defects rather than on movement. Add an
invariant here whenever a defect turns out to be the same CLASS as an earlier one.

Usage:  python3 check_invariants.py <computed.json> [more...]
Exit:   0 clean . 9 one or more invariants violated, or a file could not be read
"""
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jscompat import js_str, js_number, js_to_fixed, js_round, js_stdio, UNDEFINED
from jscompat import _JSErr, _bad_const, _pint, _is_num, _truthy, _js_json_error, _js_read_json, _out

_A = re.ASCII
_I = re.I | re.ASCII
_WSC = "\t\n\x0b\x0c\r    -     　﻿"
_S = "[" + _WSC + "]"
EPSILON = 2.0 ** -52


def _s(v):
    if v is UNDEFINED:
        return "undefined"
    if isinstance(v, list):
        return ",".join("" if (x is None or x is UNDEFINED) else _s(x) for x in v)
    if isinstance(v, dict):
        return "[object Object]"
    return js_str(v)


def _g(o, k):
    if isinstance(o, dict):
        return o.get(k, UNDEFINED)
    if o is None or o is UNDEFINED:
        raise TypeError("Cannot read properties of %s (reading '%s')" % (_s(o), k))
    return UNDEFINED


def _or(a, b):
    return a if _truthy(a) else b


def _nullish(v):
    return v is None or v is UNDEFINED


def _seq(a, b):
    if isinstance(a, (dict, list)) or isinstance(b, (dict, list)):
        return a is b
    if _is_num(a) and _is_num(b):
        return a == b
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if a is UNDEFINED or b is UNDEFINED or a is None or b is None:
        return a is b
    return type(a) is type(b) and a == b


def _num(v):
    return v if _is_num(v) else js_number(v)


def _div(a, b):
    x, y = float(_num(a)), float(_num(b))
    if y == 0:
        if x == 0 or math.isnan(x):
            return float("nan")
        return math.copysign(float("inf"), x) * math.copysign(1.0, y)
    return x / y


def _test(rx, v):
    return rx.search(_s(v)) is not None


def xl_fix(n, dp):
    if n is None or n is UNDEFINED:
        return _s(n)
    x = _num(n)
    if not math.isfinite(x):
        return _s(n)
    sign = -1 if x < 0 else 1
    a = abs(x)
    f = 10.0 ** dp
    sc = a * f
    r = (sign * js_round(sc + abs(sc) * EPSILON * 4)) / f
    return js_to_fixed(0.0 if r == 0 else r, dp)


_PE1 = re.compile(r"(^|[^A-Z])P" + _S + r"*/" + _S + r"*(NTM" + _S + r"*)?(Adj" + _S + r"*)?E\b", _I)
_PE2 = re.compile(r"\bP/E\b", _I)
_EVE = re.compile(r"EV" + _S + r"*/" + _S + r"*(NTM" + _S + r"*)?(Adj" + _S + r"*)?EBITDA", _I)
CONCEPT = [
    {"key": "P/E", "test": lambda s: _test(_PE1, s) or _test(_PE2, s)},
    {"key": "EV/EBITDA", "test": lambda s: _test(_EVE, s)},
]
_ADJ = re.compile(r"_Adj|Adj_", _A)


def BASIS_OF(m):
    return "Adj" if _ADJ.search(_s(m)) else "GAAP"


def near(a, b, tol):
    if a is None or b is None:
        return True
    return abs(_num(a) - _num(b)) <= tol * abs(_num(b if _truthy(b) else 1))


def invariants(c, name):
    V, S, out = _or(_g(c, "valuation"), {}), _or(_g(c, "earnings_setup"), {}), []

    def fail(id_, msg):
        out.append({"id": id_, "msg": msg})

    lenses = _or(_g(V, "lenses"), [])

    pe_lens = [l for l in lenses if CONCEPT[0]["test"](_g(l, "label"))]
    for l in pe_lens:
        if (not _nullish(_g(V, "ntm_pe")) and
                not _seq(BASIS_OF(_g(l, "resolved_metric")), _g(V, "ntm_pe_basis"))):
            fail("INV-1", 'two P/E bases in one document: lens "' + _s(_g(l, "label")) + '" is ' +
                 BASIS_OF(_g(l, "resolved_metric")) + " " +
                 "(" + _s(_g(l, "resolved_metric")) + ") at " + _s(_g(l, "value")) + "x, while the Earnings Setup NTM P/E is " +
                 _s(_g(V, "ntm_pe_basis")) + " at " + _s(_g(V, "ntm_pe")) + "x. Pick one selector; print the basis on both.")
    if (not _nullish(_g(V, "ntm_ev_ebitda")) and not _nullish(_g(V, "ntm_pe")) and
            _truthy(_g(V, "ntm_ev_ebitda_basis")) and _truthy(_g(V, "ntm_pe_basis")) and
            not _seq(_g(V, "ntm_ev_ebitda_basis"), _g(V, "ntm_pe_basis")) and
            not _truthy(_g(V, "ntm_row_mixed_basis"))):
        fail("INV-1", "NTM P/E is " + _s(_g(V, "ntm_pe_basis")) + " while EV/EBITDA beside it is " +
             _s(_g(V, "ntm_ev_ebitda_basis")) + ", and the row does not say so.")

    PROMISE = [
        {"re": re.compile(r"TBV|tangible", _I), "ok": re.compile(r"TBV|Tangible", _I), "what": "tangible book"},
        {"re": re.compile(r"FFO", _I), "ok": re.compile(r"FFO", _I), "what": "FFO"},
        {"re": re.compile(r"\bAdj\b", _I), "ok": re.compile(r"_Adj|Adj_", _A), "what": "an Adj series"},
        {"re": re.compile(r"organic", _I), "ok": re.compile(r"Organic", _I), "what": "organic"},
    ]
    _ADJW = re.compile(r"\bAdj\b", _I)
    for l in lenses:
        for p in PROMISE:
            if _truthy(_g(l, "concept")) and _test(p["re"], _g(l, "concept")):
                continue
            adj_fallback = bool(_ADJW.search(p["what"])) and bool(_ADJ.search(_s(_or(_g(l, "requested"), ""))))
            if (not adj_fallback and _test(p["re"], _g(l, "label")) and
                    not p["ok"].search(_s(_g(l, "resolved_metric")))):
                fail("INV-2", 'lens label "' + _s(_g(l, "label")) + '" promises ' + p["what"] + " but resolves " +
                     _s(_g(l, "resolved_metric")) + ". Rename the label or change the metric.")

    cur = _or(_g(_or(_g(c, "meta"), {}), "currency"), {})
    mc = _g(cur, "minor_codes")
    minor_unit = (len(mc) > 0 if isinstance(mc, list)
                  else bool(re.search(r"pence|cents", _s(_or(_g(cur, "minor"), "")), _I)))
    _PB = re.compile(r"P" + _S + r"*/" + _S + r"*B\b", _I)
    pb = next((l for l in lenses if _test(_PB, _g(l, "label"))), UNDEFINED)
    bvps_disclosed = bool(re.search(r"incl\. preferred|TOTAL equity", _s(_or(_g(V, "bvps_basis"), "")), _I))
    if (_truthy(pb) and not minor_unit and not bvps_disclosed and _truthy(_g(V, "price")) and
            _truthy(_g(V, "bvps")) and _truthy(_g(pb, "value")) and
            not near(_div(_g(V, "price"), _g(V, "bvps")), _g(pb, "value"), 0.02)):
        ratio = _div(_g(V, "price"), _g(V, "bvps"))
        fail("INV-3", "the card contradicts itself: price " + _s(_g(V, "price")) + " / BVPS " + _s(_g(V, "bvps")) + " = " +
             xl_fix(ratio, 1) + 'x against a printed "' + _s(_g(pb, "label")) + '" of ' + _s(_g(pb, "value")) + "x " +
             "(" + xl_fix(abs(_div(ratio, _g(pb, "value")) - 1) * 100, 1) + "% apart). " +
             'bvps_basis="' + _s(_g(V, "bvps_basis")) + '" - check the equity base excludes preferred.')

    sector_wants_pe = any(CONCEPT[0]["test"](_g(l, "label")) for l in lenses)
    if not sector_wants_pe and (not _nullish(_g(V, "ntm_pe")) or not _nullish(_g(S, "pe_now"))):
        fail("INV-4", "the sector lens set deliberately omits P/E, yet the document still prints " +
             "NTM P/E " + _s(_g(S, "pe_now") if _nullish(_g(V, "ntm_pe")) else _g(V, "ntm_pe")) +
             "x in Earnings Setup. Suppress it or restore the lens.")

    _REP = re.compile(r"rep\.|reported|GAAP", _I)
    _HEAD = re.compile(r"^(P/E|EV/EBITDA)", _A)
    for l in lenses:
        if (BASIS_OF(_g(l, "resolved_metric")) == "GAAP" and not _test(_REP, _g(l, "label"))
                and not _HEAD.search(_s(_or(_g(l, "resolved_metric"), "")))
                and any(x["test"](_g(l, "label")) for x in CONCEPT)):
            fail("INV-5", 'lens "' + _s(_g(l, "label")) + '" is on a REPORTED basis by rule, not by fallback, and ' +
                 "nothing on the page says so. Mark it, or make the rule declare intent.")

    fm = _or(_g(_or(_g(_or(_g(c, "meta"), {}), "metricset"), {}), "fin_metrics"), [])
    _FTE = re.compile(r"\bFTE\b", _I)

    def is_fte(r):
        return bool(_FTE.search(_s(_or(_g(_or(r, {}), "name"), ""))))

    prof = fm[1] if len(fm) > 1 else UNDEFINED
    fm0 = fm[0] if len(fm) > 0 else UNDEFINED
    marg = next((r for r in fm if _truthy(r) and _seq(_g(r, "fmt"), "%") and not _seq(r, fm0)), UNDEFINED)
    if (_truthy(prof) and _truthy(marg) and is_fte(prof) != is_fte(marg)
            and not any(re.search(r"BASIS PAIRING", _s(w), _A) for w in _or(_g(c, "warnings"), []))):
        fail("INV-6", 'profit "' + _s(_g(prof, "name")) + '" (' + _s(_g(prof, "ltcode")) + ') and margin "' +
             _s(_g(marg, "name")) + '" ' +
             "(" + _s(_g(marg, "ltcode")) + ") are on different accounting bases (one FTE, one not) and nothing " +
             "says so. Re-fetch per data-collection.md §2 batch 4/7, or disclose.")

    return out


REQUIRED_KEYS = ["beat_history", "citable", "earnings_setup", "generated_from",
                 "guidance_evolution", "guidance_live", "market_expectations", "meta", "peer_comps",
                 "revisions", "summary_financials", "valuation", "warnings"]


def _has_key(c, k):
    if isinstance(c, dict):
        return k in c
    if isinstance(c, list):
        return k == "length" or (re.fullmatch(r"0|[1-9][0-9]*", k, _A) is not None and int(k) < len(c))
    raise TypeError("Cannot use 'in' operator to search for '%s' in %s" % (k, _s(c)))


def _err(s):
    sys.stderr.write(s + "\n")


js_stdio()
bad = checked = unreadable = 0
files = sys.argv[1:]
if not files:
    _err("usage: node check_invariants.js <computed.json> [...]")
    sys.exit(1)
_DOT = "[^\n\r  ]"
for f in files:
    name = re.sub(r"_expected\.json|\.json", "", re.sub(_DOT + "*/", "", f, count=1), count=1)
    try:
        c = _js_read_json(f)
    except _JSErr as e:
        unreadable += 1
        bad += 1
        msg = str(e)
        looks_binary = (re.search(r"\.(docx|pdf|zip|xlsx|pptx)\Z", name, _I) is not None or
                        re.match(r"Unexpected token 'P'", msg) is not None)
        _err("  UNREADABLE  " + name + ": " + msg + (
            "\n              this checker reads computed.json (the compute output), not the rendered document." +
            "\n              try: node references/check_invariants.js computed.json" if looks_binary else ""))
        continue
    checked += 1
    missing = [k for k in REQUIRED_KEYS if not _has_key(c, k)]
    if missing:
        bad += 1
        _out("  FAIL  " + name)
        _out("          [INV-0] not a computed document: missing top-level key(s) " + ", ".join(missing) + ". " +
             "A card that is absent entirely cannot be checked by INV-1..6, which all guard on the " +
             "fields being present; an empty value is fine and is the degradation matrix's business.")
        continue
    v = invariants(c, name)
    if not v:
        _out("  ok    " + name)
        continue
    bad += 1
    _out("  FAIL  " + name)
    for x in v:
        _out("          [" + x["id"] + "] " + x["msg"])
if unreadable:
    _err("  " + str(unreadable) + " file(s) could not be read - counted as failures, not skipped.")
_out("\nINVARIANTS VIOLATED in " + str(bad) + " of " + str(checked + unreadable) + " document(s)." if bad
     else "\nINVARIANTS OK - " + str(checked) + " document(s).")
sys.stdout.flush()
sys.stderr.flush()
sys.exit(9 if bad else 0)
