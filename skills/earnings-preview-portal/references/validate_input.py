#!/usr/bin/env python3
"""=========================== PRE-FLIGHT INPUT VALIDATOR ===========================

   usage: python3 validate_input.py <input.json> [metricset_input.json]
          exit 0 clean | 1 usage | 8 errors

   The SECOND argument is optional and is the Step 0.2 file you fed the resolver. Pass it and
   the `sample` / `size` evidence checks run too - those fields live on the resolver's INPUT,
   not its output, so they are not checkable from input.json alone. 

   WHY THIS EXISTS. The COMPUTE half of this skill is tested exhaustively; the FETCH half - an
   operator reading data-collection.md and producing input.json - is where defects actually
   live, and compute gates only on the PRESENCE of nine meta blocks. Path B moved all the MATH into scripts; it also moved all the
   JUDGEMENT into the fetch step. This is the harness for that step.

   WHAT IT IS NOT. It does not re-check what compute already refuses on. It checks the SEMANTIC
   contract compute cannot see, BEFORE a document is built - so an error costs a re-fetch rather
   than a wrong number on a page.

   ERRORS block. WARNINGS are disclosures the operator must carry into the footer."""
import datetime
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jscompat import js_str, js_json, js_number, js_keys, js_stdio, UNDEFINED
from jscompat import _JSErr, _bad_const, _pint, _is_num, _truthy, _js_json_error, _js_read_json, _exit

_WSC = "\t\n\x0b\x0c\r    -     　﻿"
_S = "[" + _WSC + "]"
_TRIM = "\t\n\x0b\x0c\r   " + "".join(chr(c) for c in range(0x2000, 0x200b)) + \
        "    　﻿"
_A = re.ASCII
_PROTO = {"constructor", "hasOwnProperty", "isPrototypeOf", "propertyIsEnumerable",
          "toLocaleString", "toString", "valueOf", "__proto__", "__defineGetter__",
          "__defineSetter__", "__lookupGetter__", "__lookupSetter__"}


def _s(v):
    if v is UNDEFINED:
        return "undefined"
    if isinstance(v, list):
        return ",".join("" if (x is None or x is UNDEFINED) else _s(x) for x in v)
    if isinstance(v, dict):
        return "[object Object]"
    return js_str(v)


def _join(a, sep=","):
    return sep.join("" if (x is None or x is UNDEFINED) else _s(x) for x in a)


def _g(o, k):
    if isinstance(o, dict):
        return o.get(k, UNDEFINED)
    if o is None or o is UNDEFINED:
        raise TypeError("Cannot read properties of %s (reading '%s')" % (_s(o), k))
    return UNDEFINED


def _or(a, b):
    return a if _truthy(a) else b


def _u16(s):
    return s.encode("utf-16-be", "surrogatepass")


def _lt(a, b):
    return _u16(a) < _u16(b)


def _uniq(a):
    return list(dict.fromkeys(a))


def _stringify(v):
    return "undefined" if v is UNDEFINED else js_json(v)


def _err(s):
    sys.stderr.write(s + "\n")


js_stdio()
errors, warns, notes = [], [], []


def E(code, msg, fix):
    errors.append({"code": code, "msg": msg, "fix": fix})


def W(code, msg, fix):
    warns.append({"code": code, "msg": msg, "fix": fix})


argv = sys.argv[1:]
in_path = argv[0] if len(argv) > 0 else ""
if not in_path:
    _err("usage: node validate_input.js <input.json>")
    _exit(1)
try:
    J = _js_read_json(in_path)
except _JSErr as e:
    _err("input.json is not valid JSON: " + str(e))
    _exit(8)

if not _truthy(_g(J, "meta")) and (_truthy(_g(J, "top_kpi_rows")) or _truthy(_g(J, "spine")) or
                                   _truthy(_g(J, "tickerinfo"))):
    _err("WRONG FILE: this looks like a metricset_input.json (it has top_kpi_rows / " +
         "spine / tickerinfo and no `meta` block), but argument 1 must be the INPUT.JSON you are " +
         "about to give compute_portal.js.\n" +
         "usage: node validate_input.js <input.json> [metricset_input.json]\n" +
         "The metricset_input goes in the SECOND position, where it enables the sample/size checks.")
    _exit(1)
meta = _or(_g(J, "meta"), {})
mcp = _or(_g(J, "mcp"), {})
Q = _or(_g(meta, "quarters"), {})
FY = _or(_g(meta, "fiscal"), {})
MS = _or(_g(meta, "metricset"), {})

_RE_PER = re.compile(r"^(\d)([QqHh])(\d{2})\Z", _A)
_RE_YEAR = re.compile(r"^\d{4}\Z", _A)


def pnum(p):
    s = _s(p) if _truthy(p) else ""
    m = _RE_PER.match(s)
    if m:
        k = m.group(2).upper()
        return {"kind": k, "n": int(m.group(1)), "yy": int(m.group(3)),
                "closeQ": int(m.group(1)) * 2 if k == "H" else int(m.group(1))}
    return {"kind": "A", "n": 4, "yy": int(_s(p)[2:]), "closeQ": 4} if _RE_YEAR.match(s) else None


def rank(p):
    a = pnum(p)
    return a["yy"] * 4 + a["closeQ"] if a else None


def prior_year(p):
    a = pnum(p)
    if not a:
        return None
    return (str(2000 + a["yy"] - 1) if a["kind"] == "A"
            else str(a["n"]) + a["kind"] + str(a["yy"] - 1).rjust(2, "0"))


def step_back(p):
    a = pnum(p)
    if not a:
        return None
    if a["kind"] == "A":
        return str(2000 + a["yy"] - 1)
    per = 2 if a["kind"] == "H" else 4
    return (str(a["n"] - 1) + a["kind"] + str(a["yy"]).rjust(2, "0") if a["n"] > 1
            else str(per) + a["kind"] + str(a["yy"] - 1).rjust(2, "0"))


def rows(x):
    return x if isinstance(x, list) else []


all_metric_rows = rows(_g(MS, "fin_metrics")) + rows(_g(MS, "top_kpis"))

if True:
    tq = pnum(_g(Q, "target_q"))
    if not tq:
        E("PERIOD", 'meta.quarters.target_q is "' + _s(_g(Q, "target_q")) + '", which is not a period ' +
          "label. Expected nQyy (3Q26), nHyy (1H26) or a bare year (2026).",
          "Use the issuer's own label exactly as latest_fiscal_quarter reports it.")
    else:
        kinds = [x for x in ({"k": k, "p": _g(Q, k), "a": pnum(_g(Q, k))}
                             for k in ["py_q", "prior_q", "target_q", "next_q"]) if _truthy(x["p"])]
        mixed = _uniq([x["a"]["kind"] for x in kinds if x["a"] and x["a"]["kind"]])
        if len(mixed) > 1:
            E("PERIOD", "meta.quarters mixes cadences: " + ", ".join(x["k"] + "=" + _s(x["p"]) for x in kinds) +
              ". One document is built on ONE cadence.",
              "Pick the cadence the issuer reports on and label all four periods in it.")
        want_py, want_prior = prior_year(_g(Q, "target_q")), step_back(_g(Q, "target_q"))
        if _truthy(_g(Q, "py_q")) and want_py and _s(_g(Q, "py_q")) != want_py:
            E("PERIOD", 'meta.quarters.py_q is "' + _s(_g(Q, "py_q")) + '" but the prior-YEAR comparable of ' +
              _s(_g(Q, "target_q")) + ' is "' + want_py + '" - one year back AT THE SAME PERIOD.',
              'If you meant the prior PERIOD that is prior_q ("' + _s(want_prior) + '"), not py_q. ' +
              "Getting these the same way round makes the Y/Y and sequential rows print one number twice.")
        if _truthy(_g(Q, "prior_q")) and want_prior and _s(_g(Q, "prior_q")) != want_prior:
            W("PERIOD", 'meta.quarters.prior_q is "' + _s(_g(Q, "prior_q")) + '" where the period immediately ' +
              "before " + _s(_g(Q, "target_q")) + ' is "' + want_prior + '".',
              "Correct it unless the issuer genuinely skipped a period - and say so in the footer if it did.")
        if (_truthy(_g(Q, "next_q")) and rank(_g(Q, "next_q")) is not None and
                rank(_g(Q, "target_q")) is not None and rank(_g(Q, "next_q")) <= rank(_g(Q, "target_q"))):
            E("PERIOD", 'meta.quarters.next_q ("' + _s(_g(Q, "next_q")) + '") is not AFTER target_q ("' +
              _s(_g(Q, "target_q")) + '").', "next_q is the period after the one being previewed.")
        gb = _or(_g(meta, "growth_bases"), {})
        expect = {
            "py_q_prior_year": prior_year(_g(Q, "py_q")), "prior_q_prior_year": prior_year(_g(Q, "prior_q")),
            "next_q_prior_year": prior_year(_g(Q, "next_q")), "py_q_prev_q": step_back(_g(Q, "py_q")),
            "prior_q_prev_q": step_back(_g(Q, "prior_q")),
            "fy_prev_prior": js_str(js_number(_g(FY, "fy_prev")) - 1) if _truthy(_g(FY, "fy_prev")) else None,
        }
        for k in expect:
            if not expect[k] or _g(gb, k) is UNDEFINED:
                continue
            if _s(_g(gb, k)) != _s(expect[k]):
                E("PERIOD", "meta.growth_bases." + k + ' is "' + _s(_g(gb, k)) + '" where the label it must be ' +
                  'is "' + expect[k] + '".',
                  "Every growth sub-row divides by these. A wrong base is a wrong growth rate in " +
                  "every column, and nothing downstream can detect it.")
        if (_truthy(_g(FY, "fy0")) and _truthy(_g(FY, "fy1")) and
                _s(_g(FY, "fy1")) != js_str(js_number(_g(FY, "fy0")) + 1)):
            E("PERIOD", 'meta.fiscal.fy1 ("' + _s(_g(FY, "fy1")) + '") is not one year after fy0 ("' +
              _s(_g(FY, "fy0")) + '").', "The FY ladder steps one fiscal year at a time.")
        for k in ["fy_prev", "fy0", "fy1"]:
            if not _truthy(_g(FY, k)):
                E("PERIOD", "meta.fiscal." + k + " is missing.",
                  "meta.fiscal must carry {fy_prev, fy0, fy1, fy_end_month} - the fiscal-year labels the " +
                  'Summary Financials FY columns are built from (e.g. {"fy_prev":"2025","fy0":"2026",' +
                  '"fy1":"2027","fy_end_month":12}). Without them the renderer cannot title those columns.')
        if (_truthy(_g(FY, "fy0")) and _truthy(_g(FY, "fy_prev")) and
                _s(_g(FY, "fy_prev")) != js_str(js_number(_g(FY, "fy0")) - 1)):
            E("PERIOD", 'meta.fiscal.fy_prev ("' + _s(_g(FY, "fy_prev")) + '") is not one year before fy0 ("' +
              _s(_g(FY, "fy0")) + '").', "The FY ladder steps one fiscal year at a time.")

if True:
    fin, kpi = rows(_g(MS, "fin_metrics")), rows(_g(MS, "top_kpis"))
    if len(fin) > 5:
        E("METRICSET", "fin_metrics has " + str(len(fin)) + " rows; STRICT RULE 1 caps it at 5.", "Drop the weakest rows in Step 0.3.")
    if len(kpi) > 5:
        E("METRICSET", "top_kpis has " + str(len(kpi)) + " rows; STRICT RULE 1 caps it at 5.", "Drop the weakest rows in Step 0.3.")
    if not fin:
        E("METRICSET", "fin_metrics is empty.", "Re-run resolve_metricset.js and paste metricset.json verbatim.")
    FMT_OK = ("$M", "$", "%", "#")
    bad_fmt = [r for r in all_metric_rows
               if _truthy(_g(r, "fmt")) and not (isinstance(_g(r, "fmt"), str) and _g(r, "fmt") in FMT_OK)]
    if bad_fmt:
        E("METRICSET", ", ".join(_s(_g(r, "name")) + " [" + _s(_g(r, "fmt")) + "]" for r in bad_fmt) + " carr" +
          ("y" if len(bad_fmt) > 1 else "ies") + " a format outside the four the renderer knows.",
          'Legal: "$M" magnitude, "$" per-unit/per-share, "%" ratio, "#" count. Anything else - "X" ' +
          "for a multiple especially - makes compute DROP the row, so the slot is spent on nothing.")
    if True:
        id_ = _g(meta, "identity")
        if not _truthy(id_):
            W("IDENTITY", "meta.identity is absent, so nothing records how this ticker was resolved.",
              "Capture match_type/score/input_query/resolved_ticker from search_tickerid (data-collection.md §1) " +
              "and carry them here. A fuzzy match returns a DIFFERENT company at status:success.")
        elif _g(id_, "match_type") != "exact_ticker" and not _truthy(_g(meta, "identity_override")):
            E("IDENTITY", "the ticker resolved by " + _stringify(_g(id_, "match_type")) +
              " (score " + _s(_g(id_, "score")) + ") on query " + _stringify(_g(id_, "input_query")) +
              ", not by an exact ticker match.",
              "Re-resolve with search_tickerid passing `ticker` ALONE and use the exact_ticker row. " +
              "A fuzzy match returns a different issuer at status:success, and the score does not " +
              "separate them. If the substitution is deliberate (an ADR, a local line), set " +
              "meta.identity_override to a short string saying which and why.")
        elif (_truthy(_g(id_, "resolved_ticker")) and _truthy(_g(meta, "ticker")) and
              _s(_g(id_, "resolved_ticker")) != _s(_g(meta, "ticker"))):
            E("IDENTITY", "meta.ticker is " + _stringify(_g(meta, "ticker")) + " but the resolver returned " +
              _stringify(_g(id_, "resolved_ticker")) + ".",
              "The document would be headed with a symbol the data does not belong to. Rebuild from the " +
              "resolved ticker, or correct meta.ticker.")

    bad_pol = [r for r in all_metric_rows if _truthy(_g(r, "polarity")) and
               not (isinstance(_g(r, "polarity"), str) and _g(r, "polarity") in ("higher", "lower", "neutral"))]
    if bad_pol:
        E("METRICSET", ", ".join(_s(_g(r, "name")) + " [" + _s(_g(r, "polarity")) + "]" for r in bad_pol) +
          " carries an unknown polarity.", "Legal: higher | lower | neutral.")
    if fin and _truthy(_g(fin[0], "fmt")) and _g(fin[0], "fmt") != "$M":
        W("METRICSET", 'fin_metrics[0] is "' + _s(_g(fin[0], "name")) + '" with fmt ' + _s(_g(fin[0], "fmt")) +
          ", not $M.", "The first row is the top line: the denominator of every share-of-revenue " +
          "sub-row and the base of the growth grid, the beat series and the whisper. compute " +
          "refuses a balance-sheet or deferred item there (exit 4).")

if len(argv) > 1 and argv[1]:
    MI = None
    try:
        MI = _js_read_json(argv[1])
    except _JSErr as e:
        E("METRICSET-IN", "second argument is not valid JSON: " + str(e), "Pass metricset_input.json, or omit it.")
    if _truthy(MI):
        def dig(n, out=None):
            out = out if out is not None else []
            if isinstance(n, list):
                for x in n:
                    dig(x, out)
            elif isinstance(n, dict):
                if _truthy(_g(n, "ltcode")) and _truthy(_g(n, "name")):
                    out.append(n)
                else:
                    for k in js_keys(n):
                        dig(n[k], out)
            return out

        cand = dig(_g(MI, "top_kpi_rows")) + dig(_g(MI, "spine")) + dig(_g(MI, "kpi_fallback_rows"))
        if not cand:
            W("METRICSET-IN", "no candidate rows found in the second file.",
              "Expected top_kpi_rows and/or spine.{...} arrays of {name, ltcode, tags, sample, size}.")
        else:
            BS_EXEMPT = re.compile(r"^(BS_P_|IS_P_125\Z)", _A)
            BS_NAME = re.compile(r"total debt|^cash\b|cash &|cash and|total equity|fully diluted|\bFDS\b", _A | re.I)

            def needs_evidence(r):
                return (not BS_EXEMPT.search(_s(_or(_g(r, "ltcode"), ""))) and
                        not BS_NAME.search(_s(_or(_g(r, "name"), ""))))

            gated = [r for r in cand if needs_evidence(r)]
            ns = [r for r in gated if _g(r, "sample") is UNDEFINED or _g(r, "sample") is None or _g(r, "sample") == ""]
            nz = [r for r in gated if _g(r, "size") is UNDEFINED or _g(r, "size") is None]
            if ns:
                W("METRICSET-IN", str(len(ns)) + " of " + str(len(gated)) + " estimate-gated candidate rows carry no `sample`: " +
                  _join([_g(r, "name") for r in ns[:4]], ", ") + (", …" if len(ns) > 4 else "") + ".",
                  "`fmt` then falls back to the NAME ladder (it has mis-typed rows on several sectors in a " +
                  "row). `sample` costs no extra MCP call - it is the probe value you already fetched.")
            if nz:
                W("METRICSET-IN", str(len(nz)) + " of " + str(len(gated)) + " estimate-gated candidate rows carry no `size`: " +
                  _join([_g(r, "name") for r in nz[:4]], ", ") + (", …" if len(nz) > 4 else "") + ".",
                  "KPI ranking then falls back to tag weight, which can put a smaller segment " +
                  "above the largest one - and the lead KPI drives THE SETUP's opening bullet and the " +
                  "beat-history series.")
            CURTOK = re.compile(_S + r"*(?:[A-Z]{3}|US\$|NT\$|HK\$|R\$|C\$|A\$|S\$|NZ\$|[$€£¥₩₹])", _A)
            PERSHARE = re.compile(r"\bEPS\b|/" + _S + r"*share\b|per share\b|\bARPU\b|\bARPA\b|\bDPS\b", _A | re.I)

            def group_of(r):
                if PERSHARE.search(_s(_or(_g(r, "name"), ""))):
                    return None
                return "money magnitude" if CURTOK.match(_s(_or(_g(r, "sample"), ""))) else "count / volume"

            SUF = {"T": 4, "B": 3, "M": 2, "k": 1}
            RE_MARK = re.compile(r"([0-9][0-9,.]*)" + _S + r"*([TBMk])\b", _A)

            def mark(r):
                m = RE_MARK.search(_s(_or(_g(r, "sample"), "")))
                size = _g(r, "size")
                if not m or not _is_num(size) or not math.isfinite(size) or size == 0:
                    return None
                g = group_of(r)
                if not g:
                    return None
                return {"r": r, "fmt": g, "suf": m.group(2), "rank": SUF.get(m.group(2), 0),
                        "band": math.floor(math.log10(abs(float(size))))}

            marked = [x for x in (mark(r) for r in cand) if x]
            groups = {}
            for x in marked:
                groups.setdefault(x["fmt"], []).append(x)

            def check_scale(marked, fmt_label):
                sufs = _uniq([x["suf"] for x in marked])
                if len(sufs) > 1:
                    by_band = {}
                    for x in marked:
                        by_band.setdefault(str(x["band"]), {})[x["suf"]] = True
                    overlap = [b for b in js_keys(by_band) if len(by_band[b]) > 1]
                    band0 = overlap[0] if overlap else UNDEFINED
                    in_band = [x for x in marked if str(x["band"]) == _s(band0)]
                    first = in_band[0] if in_band else None
                    other = next((x for x in in_band if x["suf"] != (first["suf"] if first else UNDEFINED)), None)
                    pair = [first, other] if (first and other) else []
                    if overlap and len(pair) == 2:
                        W("METRICSET-IN", 'within fmt "' + fmt_label + '", candidate rows mix scale suffixes (' +
                          ", ".join(sufs) + ') while their `size` values sit in the SAME magnitude band - e.g. "' +
                          _s(_g(pair[0]["r"], "name")) + '" sample ' + _s(_g(pair[0]["r"], "sample")) +
                          " size " + _s(_g(pair[0]["r"], "size")) + ' against "' +
                          _s(_g(pair[1]["r"], "name")) + '" sample ' + _s(_g(pair[1]["r"], "sample")) +
                          " size " + _s(_g(pair[1]["r"], "size")) + ".",
                          'This is the mixed-scale defect and it is invisible in the numbers alone: "KRW120.5000T" ' +
                          'and "KRW250.4000B" strip to 120.5 and 250.4, so the SMALLER line ranks HIGHER ' +
                          "and a residual whose latest actual is NEGATIVE revenue took the lead KPI slot - " +
                          "which drives THE SETUP's opening bullet and the beat series. Re-express every row " +
                          "on ONE scale (§2, unit convention) and re-run Step 0.3.")
                    else:
                        notes.append('fmt "' + fmt_label + '": samples use ' + ", ".join(sufs) + " scale marks but " +
                                     "their size bands do not overlap - safe.")

            for f in groups:
                check_scale(groups[f], f)

if True:
    tid = _s("" if _g(meta, "ticker_id") is UNDEFINED else _g(meta, "ticker_id"))
    for k in ["estimates_latest", "revision_series", "preprint_snapshots", "actuals"]:
        bad = [r for r in rows(_g(mcp, k)) if _truthy(r) and _g(r, "TICKER_ID") is not UNDEFINED and
               _g(r, "TICKER_ID") is not None and _s(_g(r, "TICKER_ID")) != tid]
        if bad:
            ids = _uniq([_s(_g(r, "TICKER_ID")) for r in bad])
            E("FOREIGN", "mcp." + k + " contains " + str(len(bad)) + " row(s) for ticker_id " +
              ", ".join(ids) + ", but this document is ticker_id " + tid + ".",
              "A peer batch pasted into one of the four indexed arrays printed target-quarter revenue " +
              "as 1,000 instead of 94,791 (§11). Peer data belongs in mcp.peers, which nothing renders. " +
              "Re-fetch this array one ticker per call.")

if True:
    est = {}
    for r in rows(_g(mcp, "estimates_latest")) + rows(_g(mcp, "revision_series")):
        if _truthy(r) and _truthy(_g(r, "LTCODE")) and _truthy(_g(r, "FISCALPERIOD")):
            est[_s(_g(r, "LTCODE")) + "|" + _s(_g(r, "FISCALPERIOD"))] = True
    missing = [r for r in all_metric_rows if _truthy(_g(r, "ltcode")) and
               not est.get(_s(_g(r, "ltcode")) + "|" + _s(_g(Q, "target_q")))]
    if len(missing) == len(all_metric_rows) and all_metric_rows:
        E("COVERAGE", "NOT ONE metricset row has an estimate for the target period " + _s(_g(Q, "target_q")) + ".",
          "compute refuses this (exit 7). Either the Step 0 probe was never pasted into " +
          "mcp.estimates_latest, or this issuer is only covered at a coarser cadence - 600519 CH " +
          "has quarterly ACTUALS but only half-yearly and annual ESTIMATES, and returns data:[] " +
          "(an empty result, not an error) for a quarterly period. Rebuild on the cadence that has coverage.")
    elif missing:
        W("COVERAGE", str(len(missing)) + " of " + str(len(all_metric_rows)) + " rows have no " + _s(_g(Q, "target_q")) +
          " estimate: " + ", ".join(_s(_g(r, "ltcode")) + " (" + _s(_g(r, "name")) + ")" for r in missing[:5]) +
          (", …" if len(missing) > 5 else "") + ".",
          "Check this is real absence and not a dropped row: a period that does not exist comes " +
          "back as `success` with the row MISSING and notes:null. Key every row by its returned " +
          "FISCALPERIOD and diff requested against returned - never zip positionally.")

TODAY = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
if True:
    B = _or(_g(meta, "buckets"), {})
    seq = [x for x in [["pre", _g(B, "pre")], ["post", _g(B, "post")], ["interq", _g(B, "interq")]] if _truthy(x[1])]
    for i in range(1, len(seq)):
        if _lt(_s(seq[i][1]), _s(seq[i - 1][1])):
            E("BUCKETS", "meta.buckets." + seq[i][0] + " (" + _s(seq[i][1]) + ") is EARLIER than " +
              seq[i - 1][0] + " (" + _s(seq[i - 1][1]) + ").",
              "The columns would read right-to-left in time. pre <= post <= interq, and each is the " +
              "DATE OF THE RECORD §5 selects, not an offset.")
    for k, v in seq:
        if _lt(TODAY, _s(v)):
            E("BUCKETS", "meta.buckets." + k + " (" + _s(v) + ") is in the FUTURE.",
              "No estimate observation can exist on or before a future date.")

if True:
    by_key = {}
    for r in rows(_g(mcp, "actuals")):
        if not _truthy(r) or not _truthy(_g(r, "LTCODE")) or not _truthy(_g(r, "FISCALPERIOD")):
            continue
        a = pnum(_g(r, "FISCALPERIOD"))
        if not a or a["kind"] == "A":
            continue
        k = _s(_g(r, "LTCODE")) + "|" + str(a["yy"]) + "|Q" + str(a["closeQ"])
        by_key.setdefault(k, {})[a["kind"]] = _g(r, "FISCALPERIOD")
    clash = [k for k in js_keys(by_key) if _truthy(by_key[k].get("H")) and _truthy(by_key[k].get("Q"))]
    if clash:
        W("CADENCE", str(len(clash)) + " line item(s) carry BOTH a half-yearly and a quarterly actual " +
          "for one period, e.g. " + "; ".join(k.split("|")[0] + ": " + _s(by_key[k]["H"]) + " and " +
                                              _s(by_key[k]["Q"]) for k in clash[:3]) + ".",
          "They share a QUARTERENDDATE, one holds a SIX-MONTH value and the other a THREE-MONTH " +
          'value, and both are marked "Financial Release" - no field tells them apart. Align on the ' +
          "FISCALPERIOD STRING, never QUARTERENDDATE; the MCP's own formatting guidance says the " +
          "opposite and following it lifts a half-year figure into a quarterly column (~2x).")

if True:
    tokens = re.compile(r"(^|[^A-Z])(USD|EUR|GBP|JPY|CNY|RMB|KRW|TWD|HKD|CHF|BRL|INR|SGD|AUD|CAD|SEK|DKK|NOK)|[$€£¥₩₹]|NT\$|HK\$|R\$|C\$|A\$|S\$", _A)

    def any_cur(arr, key):
        return any(_truthy(r) and _g(r, key) is not UNDEFINED and tokens.search(_s(_g(r, key))) for r in rows(arr))

    market = _or(_g(mcp, "market"), {})
    px_cur = any(any_cur(_g(market, g), "ADJUSTED_CLOSE") for g in ["current", "daily_30d", "reactions"])
    _NONUSD = re.compile(r"KRW|TWD|CNY|RMB|JPY|CHF|BRL|INR|HKD|SGD|€|£|¥|₩|₹", _A)
    non_usd = any(_truthy(r) and _NONUSD.search(_s(_or(_g(r, "VALUE"), "")))
                  for r in rows(_g(mcp, "actuals")) + rows(_g(mcp, "estimates_latest")))
    cur_ = _g(meta, "currency")
    declared = _or(_g(meta, "currency_price"), _g(cur_, "price_major") if _truthy(cur_) else cur_)
    if non_usd and not px_cur and not _truthy(declared):
        W("PRICE-BASIS", "the fundamentals are NON-USD and the price series carries no currency " +
          "token, so the price basis cannot be verified.",
          "Set meta.currency_price to the ISO code the price series is quoted in. This is exactly " +
          "where a listing-versus-reporting split hides: an issuer can report CNY and list HKD, and " +
          "every price-derived line on the card is then wrong by the cross. If they differ, supply " +
          "meta.fx_price_to_report and compute will convert the price legs (§0b).")
    suf_m = re.search(_S + r"([A-Z][A-Z0-9])\Z", _s(_or(_g(meta, "ticker"), "")).strip(_TRIM).upper(), _A)
    MKT = {"TT": "TWD", "HK": "HKD", "KS": "KRW", "JP": "JPY", "CH": "CNY", "LN": "GBP", "GR": "EUR",
           "FP": "EUR", "SW": "CHF", "BZ": "BRL", "IN": "INR", "SP": "SGD", "AU": "AUD", "CN": "CAD"}

    def _px_tok():
        MAP = {"NT$": "TWD", "HK$": "HKD", "R$": "BRL", "C$": "CAD", "A$": "AUD",
               "S$": "SGD", "NZ$": "NZD", "US$": "USD", "$": "USD", "€": "EUR",
               "£": "GBP", "¥": "JPY", "₩": "KRW", "₹": "INR"}
        by_len = sorted(MAP.keys(), key=lambda k: -len(k))
        seen = {}
        for g in ["current", "daily_30d", "reactions"]:
            for r in rows(_g(market, g)):
                ac = _g(_or(r, {}), "ADJUSTED_CLOSE")
                v = _s("" if ac is UNDEFINED else _g(r, "ADJUSTED_CLOSE")).strip(_TRIM)
                if not v:
                    continue
                alpha = re.match(r"([A-Z]{3})[\d.]", v, _A)
                if alpha:
                    seen[alpha.group(1)] = True
                    continue
                sym = next((k for k in by_len if v.startswith(k)), None)
                if sym:
                    seen[MAP[sym]] = True
        return list(seen)[0] if len(seen) == 1 else None

    px_tok = _px_tok()
    px_major = _or(declared, px_tok)
    if (suf_m and suf_m.group(1) in MKT and _truthy(px_major) and
            not (isinstance(px_major, str) and px_major == MKT[suf_m.group(1)])):
        E("PRICE-BASIS", 'meta.ticker is "' + _s(_g(meta, "ticker")) + '" (a market that prices in ' +
          MKT[suf_m.group(1)] + ") but the price series is " + _s(px_major) +
          (" (declared)" if _truthy(declared) else " (from its own ADJUSTED_CLOSE tokens)") + ".",
          'search_tickerid silently resolves a DIFFERENT listing and still reports success - a local ' +
          'code can return the US ADR. compute refuses this (exit 6). Re-resolve with the local code AND ' +
          "its suffix and verify by the DATA's currency, not the symbol.")
    if non_usd and not _truthy(_g(meta, "call_timing")):
        W("TIMING", "the issuer is non-USD and meta.call_timing is not set.",
          "§9's ET-timestamp evidence exists only for US releases, so the field silently defaults " +
          "to after_close - on a non-US listing the two conventions can differ by many points and flip two signs. " +
          'Set "unknown" for a non-US listing and compute reports BOTH readings.')

if True:
    ac = [_s(x) for x in rows(_g(meta, "absent_confirmed"))]
    if ac:
        ARR = {"actuals": _g(mcp, "actuals"), "estimates_latest": _g(mcp, "estimates_latest"),
               "revision_series": _g(mcp, "revision_series"), "preprint_snapshots": _g(mcp, "preprint_snapshots")}
        has_in = {}
        for a in ARR:
            h = {}
            for r in rows(ARR[a]):
                if _truthy(r) and _truthy(_g(r, "LTCODE")):
                    h[_s(_g(r, "LTCODE"))] = True
                    if _truthy(_g(r, "FISCALPERIOD")):
                        h[_s(_g(r, "LTCODE")) + "|" + _s(_g(r, "FISCALPERIOD"))] = True
            has_in[a] = h
        names = list(ARR)

        def _has(h, k):
            return (k in h and k != "__proto__") or k in _PROTO

        for k in ac:
            in_arr = [a for a in names if _has(has_in[a], k)]
            out_arr = [a for a in names if not _has(has_in[a], k)]
            if not in_arr:
                continue
            if not out_arr:
                E("ABSENT", 'meta.absent_confirmed declares "' + k + '" absent, but EVERY pasted array ' +
                  "carries it (" + ", ".join(in_arr) + ").",
                  "No gate site could be empty for this key, so the declaration silences a warning that " +
                  "had a cause - which turns a disclosed gap into an undisclosed one. Remove it (§0c).")
            else:
                W("ABSENT", '"' + k + '" is declared absent and IS present in ' + ", ".join(in_arr) +
                  ", though absent from " + ", ".join(out_arr) + ".",
                  "That is legitimate if the empty one is the array you re-probed - a missing PRE-PRINT " +
                  "SNAPSHOT is the usual case, and its key is a code+period that must also have an " +
                  "actual. The key form names no array, so only you can confirm it. State in the footer " +
                  "WHICH series you re-probed and found empty, not just the code.")
        notes.append(str(len(ac)) + " key(s) declared absent_confirmed - name them in the footer, with " +
                     "the fact that you re-probed each one.")

if True:
    P = rows(_g(meta, "prints"))
    if not P:
        E("PRINTS", "meta.prints is empty.", "Beat History and the reaction series are built from it (get_call_date).")
    else:
        dates = [d for d in ((_g(p, "call_date") if _truthy(p) else p) for p in P) if _truthy(d)]
        for i in range(1, len(dates)):
            if _lt(_s(dates[i]), _s(dates[i - 1])):
                E("PRINTS", "meta.prints is not oldest-first (" + _s(dates[i - 1]) + " then " + _s(dates[i]) + ").",
                  'The contract is "oldest first"; the beat chart and the reaction row read it in order.')
                break
        future = [p for p in P if _truthy(p) and _truthy(_g(p, "call_date")) and _lt(TODAY, _s(_g(p, "call_date")))]
        if future:
            E("PRINTS", str(len(future)) + " entry in meta.prints has a FUTURE call date (" +
              ", ".join(_s(_g(p, "q")) + " " + _s(_g(p, "call_date")) for p in future) + ").",
              "meta.prints is REPORTED prints only - get_call_date returns future scheduled dates " +
              "mixed in with historical ones and no flag separating them. The upcoming call belongs " +
              "in meta.upcoming.")
        if len(P) < 6:
            W("PRINTS", "only " + str(len(P)) + " print(s) supplied; the beat history is built on 6.",
              "Fetch 6 if InSync has them, and say in the read-out how far back the series reaches - " +
              "note 6 prints is THREE YEARS for a half-yearly filer.")


def line(x):
    return "  " + x["code"].ljust(14) + x["msg"] + "\n" + " " * 14 + "-> " + x["fix"]


if errors:
    _err("INPUT ERRORS (" + str(len(errors)) + ") - fix before building:\n")
    for x in errors:
        _err(line(x) + "\n")
if warns:
    _err("INPUT WARNINGS (" + str(len(warns)) + ") - resolve or disclose:\n")
    for x in warns:
        _err(line(x) + "\n")
for n in notes:
    _err("  " + "NOTE".ljust(14) + n)
if not errors and not warns:
    _err("INPUT OK - " + str(len(all_metric_rows)) + " metricset rows, target " + _s(_g(Q, "target_q")) +
         ", cadence " + ((pnum(_g(Q, "target_q")) or {}).get("kind") or "?") + ".")
else:
    _err("\n" + ("REFUSING: " + str(len(errors)) + " error(s). " if errors else "No blocking errors. ") +
         str(len(warns)) + " warning(s). A warning is a DISCLOSURE, not a pass - carry it into the footer.")
_exit(8 if errors else 0)
