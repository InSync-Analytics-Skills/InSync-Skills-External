#!/usr/bin/env python3
"""
validate_metricset_input.py - the gate in FRONT of resolve_metricset.

  python3 references/validate_metricset_input.py metricset_input.json

WHY THIS FILE EXISTS. Every other gate sits DOWNSTREAM of metricset.json; nothing looked at the
candidate list that DECIDES the document. Filing ONE revenue candidate (e.g. IS_P_200, Total
Revenue (FTE)) leaves the resolver's IS_P_101 preference nothing to rank and can build the
document on a top line off headline revenue. A ranking rule cannot protect a slot
when the caller pre-selects the winner; this file makes "do NOT hand-pick metrics" enforceable.

WHAT IT CHECKS (each measured against all discovery fixtures for zero false positives):
  G1 REVENUE CANON (error). spine.revenue must offer IS_P_101, or a row whose normalised name is
     exactly "totalrevenue". A check on the CANDIDATE LIST, not on what the resolver picked.
  G2 CASH SLOT SANITY (error). Every row filed in spine.cash must carry "cash" in its name; an
     EMPTY bucket is the documented "supplied-empty" signal and stays legal.
  W1 MARGIN TWIN NOT PROBED (warning, exit 0). A financial sector with a ladder profit rung and
     no "-M" row anywhere in spine: batch 7's UNFILTERED margin-twin probe may not have been run.

ESCAPE HATCHES, because a gate with no honest exit gets bypassed.
  tickerinfo.revenue_basis_override = "<why>"                    G1 passes; reason echoed.
  tickerinfo.margin_twin_probed     = true | "<codes>" | "none"  W1 clears.

Exit 0 = the candidate list may go to the resolver.  1 = fix the fetch.  2 = usage.
"""
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jscompat import js_str, js_keys, js_stdio, UNDEFINED
from jscompat import _JSErr, _bad_const, _pint, _is_num, _truthy, _js_json_error, _js_read_json, _exit, _out

_A = re.ASCII
_WSC = "\t\n\x0b\x0c\r    -     　﻿"
_S = "[" + _WSC + "]"


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
    if isinstance(o, (list, str)) and re.fullmatch(r"0|[1-9][0-9]*", k, _A) and int(k) < len(o):
        return o[int(k)]
    if o is None or o is UNDEFINED:
        raise TypeError("Cannot read properties of %s (reading '%s')" % (_s(o), k))
    return UNDEFINED


def _keys(o):
    if isinstance(o, dict):
        return js_keys(o)
    if isinstance(o, (list, str)):
        return [str(i) for i in range(len(o))]
    return []


def _or(a, b):
    return a if _truthy(a) else b


def _err(s):
    sys.stderr.write(s + "\n")


js_stdio()
in_path = sys.argv[1] if len(sys.argv) > 1 else ""
if not in_path:
    _err("usage: node validate_metricset_input.js <metricset_input.json>")
    _exit(2)
try:
    D = _js_read_json(in_path)
except _JSErr as e:
    _err("METRICSET INPUT UNREADABLE: " + in_path + " - " + str(e))
    _exit(1)


def norm(s):
    return re.sub(r"[^a-z0-9]", "", _s("" if (s is None or s is UNDEFINED) else s).lower())


def _obj_rows(a):
    return [r for r in a if _truthy(r) and isinstance(r, (dict, list))]


spine = _or(_g(D, "spine") if _truthy(D) else D, {})
ti = _or(_g(D, "tickerinfo") if _truthy(D) else D, {})


def bucket(k):
    v = _g(spine, k)
    return _obj_rows(v if isinstance(v, list) else [])


all_rows = []
for _k in _keys(spine):
    _v = _g(spine, _k)
    all_rows += _obj_rows(_v) if isinstance(_v, list) else []

errors, warnings, notes = [], [], []

rev_b = bucket("revenue")
override = _g(ti, "revenue_basis_override")
_RE_101 = re.compile(r"^IS_P_101\Z", _A)
has_canon = any(_RE_101.search(_s(_or(_g(r, "ltcode"), ""))) or norm(_g(r, "name")) == "totalrevenue" for r in rev_b)
if not has_canon and not _truthy(override):
    errors.append("G1 REVENUE CANON: spine.revenue does not offer IS_P_101 (or a row named exactly " +
                  '"Total Revenue"). It offers ' + (str(len(rev_b)) + " row(s): " +
                  ", ".join(_s(_or(_g(r, "name"), "?")) + " (" + _s(_or(_g(r, "ltcode"), "?")) + ")" for r in rev_b)
                  if rev_b else "NOTHING") + ".\n" +
                  "      The resolver prefers IS_P_101 and cannot apply that preference to a list that does not\n" +
                  "      contain it, so the top line will be decided by what you happened to file. The top line is\n" +
                  '      the denominator of every "% of Total Revenue" cell and the base of the growth grid, the\n' +
                  "      beat series and the whisper.\n" +
                  '      FIX: re-run data-collection.md section 2 batch 2 ("revenue, gross margin, ebitda, ebit,\n' +
                  '      diluted eps, free cash flow", filter "standard") and file EVERY returned row, not the one\n' +
                  "      you judged best. If this issuer genuinely has no IS_P_101, set\n" +
                  "      tickerinfo.revenue_basis_override to a short reason and it will be echoed for the footer.")
elif not has_canon and _truthy(override):
    notes.append("revenue basis overridden: " + _s(override) + " (disclose this in the footer)")

bad_cash = [r for r in bucket("cash") if "cash" not in norm(_g(r, "name"))]
if bad_cash:
    errors.append("G2 CASH SLOT: " + str(len(bad_cash)) + " row(s) filed in spine.cash are not cash: " +
                  ", ".join(_s(_or(_g(r, "name"), "?")) + " (" + _s(_or(_g(r, "ltcode"), "?")) + ")" for r in bad_cash) + ".\n" +
                  "      spine.cash feeds the EV bridge and net debt. A deposits, receivables or investments\n" +
                  "      balance there understates or overstates net debt silently, and the valuation card still\n" +
                  "      reconciles because the bridge is internally consistent with the wrong leg.\n" +
                  "      FIX: file the issuer's cash and equivalents row (usually BS_P_101). An EMPTY spine.cash\n" +
                  '      is legal and is the documented "supplied-empty" signal; a wrong row is not.')

FIN_SECTOR = re.compile(r"bank|insur|reit|real estate|thrift|savings|asset manage|capital market|specialty" + _S +
                        r"*finance|consumer" + _S + r"*finance|diversified" + _S + r"*financ|institutional" + _S +
                        r"*financ|broker|exchange|financ", _A | re.I)
LADDER_RUNGS = ["IS_P_210", "IS_P_209", "IS_P_117", "IS_P_118", "IS_P_124", "CF_P_114", "CF_P_203"]
sector = _s(_or(_or(_or(_g(ti, "insync_subindustry"), _g(ti, "subindustry")), _g(ti, "classification")), ""))
has_rung = any(_s(_or(_g(r, "ltcode"), "")) in LADDER_RUNGS for r in all_rows)
has_twin = any(re.search(r"-M\Z", _s(_or(_g(r, "ltcode"), "")), _A) for r in all_rows)
if FIN_SECTOR.search(sector) and has_rung and not has_twin and not _truthy(_g(ti, "margin_twin_probed")):
    warnings.append('W1 MARGIN TWIN: sector "' + sector + '" resolves its profit slot through the bank / ' +
                    'insurer / REIT ladder, and no "-M" row appears anywhere in spine.\n' +
                    '      data-collection.md section 2 batch 7 ("margin PPNR, margin net income, margin PBT, margin ' +
                    'FFO", UNFILTERED) is the only query that returns them; with filter "standard" they are\n' +
                    "      UNREACHABLE and the margin slot silently falls to an Efficiency Ratio instead.\n" +
                    "      A ticker that carries no twin is normal (some banks have none). Run batch 7, then set\n" +
                    '      tickerinfo.margin_twin_probed to the codes found, or "none", to clear this.')

def _dig(n, out):
    if isinstance(n, list):
        for x in n:
            _dig(x, out)
    elif isinstance(n, dict):
        if _truthy(_g(n, "ltcode")) and _truthy(_g(n, "name")):
            out.append(n)
        else:
            for k in js_keys(n):
                _dig(n[k], out)
    return out


_D = D if isinstance(D, dict) else {}
_cand = _dig(_D.get("top_kpi_rows"), []) + _dig(_D.get("spine"), []) + _dig(_D.get("kpi_fallback_rows"), [])
_CURTOK = re.compile(_S + r"*(?:[A-Z]{3}|US\$|NT\$|HK\$|R\$|C\$|A\$|S\$|NZ\$|[$€£¥₩₹])", _A)
_PERSHARE = re.compile(r"\bEPS\b|/" + _S + r"*share\b|per share\b|\bARPU\b|\bARPA\b|\bDPS\b", _A | re.I)
_RE_MARK = re.compile(r"([0-9][0-9,.]*)" + _S + r"*([TBMk])\b", _A)
_groups = {}
for _r in _cand:
    _m = _RE_MARK.search(_s(_or(_g(_r, "sample"), "")))
    _size = _g(_r, "size")
    if not _m or not _is_num(_size) or not math.isfinite(_size) or _size == 0:
        continue
    if _PERSHARE.search(_s(_or(_g(_r, "name"), ""))):
        continue
    _f = "money magnitude" if _CURTOK.match(_s(_or(_g(_r, "sample"), ""))) else "count / volume"
    _groups.setdefault(_f, []).append({"r": _r, "suf": _m.group(2), "band": math.floor(math.log10(abs(float(_size))))})
for _f in _groups:
    _mk = _groups[_f]
    _sufs = list(dict.fromkeys(x["suf"] for x in _mk))
    if len(_sufs) < 2:
        continue
    _by_band = {}
    for x in _mk:
        _by_band.setdefault(str(x["band"]), {})[x["suf"]] = True
    _overlap = [b for b in js_keys(_by_band) if len(_by_band[b]) > 1]
    if not _overlap:
        continue
    _in = [x for x in _mk if str(x["band"]) == _overlap[0]]
    _o = next((x for x in _in if x["suf"] != _in[0]["suf"]), None)
    if _o is None:
        continue
    warnings.append('W2 MIXED SCALE: within fmt "' + _f + '", candidate rows mix scale suffixes (' + ", ".join(_sufs) +
                    ') while their `size` values sit in the SAME magnitude band - e.g. "' + _s(_g(_in[0]["r"], "name")) +
                    '" sample ' + _s(_g(_in[0]["r"], "sample")) + " size " + _s(_g(_in[0]["r"], "size")) + ' against "' +
                    _s(_g(_o["r"], "name")) + '" sample ' + _s(_g(_o["r"], "sample")) + " size " + _s(_g(_o["r"], "size")) + ".\n" +
                    "      The smaller line can rank higher and take a KPI slot. FIX NOW, before the resolver: put\n" +
                    "      every row's `size` on ONE scale (data-collection.md section 2, unit convention).\n" +
                    "      validate_input.py runs this same check after the fetch, where fixing it means redoing Step 0.\n" +
                    "      If every `size` is ALREADY on one scale and a `sample` only differs because it is another\n" +
                    "      period's value, change nothing: that is a known false alarm; say so in the footer.")

n_rows = len(all_rows)
n_buckets = len([k for k in _keys(spine) if isinstance(_g(spine, k), list) and len(_g(spine, k))])
for e in errors:
    _err("\n[ERROR] " + e)
for w in warnings:
    _out("\n[WARN]  " + w)
for n in notes:
    _out("\n[NOTE]  " + n)
if errors:
    _err("\nMETRICSET INPUT REJECTED - " + str(len(errors)) + " error(s). Fix the FETCH, not the resolver.")
    _exit(1)
_out("\nMETRICSET INPUT OK - " + str(n_rows) + " candidate row(s) across " + str(n_buckets) + " populated bucket(s)" +
     (", " + str(len(warnings)) + " warning(s) to read" if warnings else "") + ".")
_exit(0)
