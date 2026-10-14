#!/usr/bin/env python3
"""
resolve_metricset.py - Pure-MCP metric-set resolver for the Earnings Preview Portal.
A port of an earlier JavaScript resolver (internal edition only): same CLI, exit codes,
stderr text and a byte-identical metricset.json.

REPLACES the old company-master xlsx as the authority for WHICH metrics/KPIs a portal shows.
Fully deterministic: the same MCP responses always give the same metric set, in the same
order. The LLM does NOT choose metrics; this script does.

INPUT  (metricset_input.json):
{
  "tickerinfo": { ticker, ticker_id, company, subindustry, industry, latest_fiscal_quarter },
  "top_kpi_rows": [ {name, ltcode, tags} ],          // search_lineitem filter=top_kpi, queries=all
  "spine": {                                          // search_lineitem filter=standard (per query)
     "revenue":[rows], "gross_margin":[rows], "ebit":[rows], "ebitda":[rows],
     "eps":[rows], "fcf":[rows], "ocf":[rows], "capex":[rows], "debt":[rows], "cash":[rows]
  }
}
Each row is {name, ltcode, tags}. Envelopes tolerated: the loader digs for arrays of such rows
anywhere in the object.

OUTPUT (metricset.json) - paste verbatim under input.json meta.metricset:
{
  fin_metrics:[{name,ltcode,fmt}..<=5], top_kpis:[{name,ltcode,fmt}..<=5],
  eps_ltcode, fcf_ltcode, ocf_ltcode, capex_ltcode, debt_ltcode, cash_ltcode,
  multiples:[{label, ntm_metric, stm_metric}..<=3], classification, source:"insync-mcp",
  gate:{ ok, top_kpi_count, fin_count, notes:[...] }
}

Usage:  python3 resolve_metricset.py metricset_input.json metricset.json

PORT NOTES (JavaScript semantics reproduced here, beyond jscompat.py):
  _jsre            JS regex source -> Python: \\d \\w \\b ASCII, \\s = the JS (Unicode) set,
                   `.` excludes all four line terminators, `$` = end of input only.
  _js_sort         V8's TimSort, step for step: the comparators here are not always
                   transitive (bandCmp / sizeCmp abstain), so only the same algorithm gives
                   the same order.
  _js_locale_compare  String.prototype.localeCompare under Node 18's default ICU collation
                   (en-US, tertiary): punctuation < digits < letters, case second.
"""
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from jscompat import (js_str, js_json, js_parse_float, js_keys, js_stdio,
                      js_write, UNDEFINED)
from polarity import polarity_of, polarity_declared as _polarity_declared
from polarity import registry_status as _pol_registry_status

polarityOf = polarity_of


_S_INNER = "\t\n\x0b\x0c\r \xa0  -     　﻿"
_TRIM_CHARS = "\t\n\x0b\x0c\r \xa0         " \
              "       　﻿"


def _jsre(src, flags=""):
    out, i, in_cls = [], 0, False
    while i < len(src):
        c = src[i]
        if c == "\\":
            n = src[i + 1]
            if n == "s":
                out.append(_S_INNER if in_cls else "[" + _S_INNER + "]")
            elif n == "S":
                out.append("[^" + _S_INNER + "]")
            else:
                out.append(c + n)
            i += 2
            continue
        if in_cls:
            if c == "]":
                in_cls = False
            out.append(c)
        elif c == "[":
            in_cls = True
            out.append(c)
        elif c == ".":
            out.append("[^\n\r  ]")
        elif c == "$":
            out.append(r"\Z")
        else:
            out.append(c)
        i += 1
    return re.compile("".join(out), re.ASCII | (re.I if "i" in flags else 0))


def _t(src, flags=""):
    rx = _jsre(src, flags)
    return lambda s: rx.search(s) is not None


def _js_truthy(v):
    if v is None or v is UNDEFINED or v is False:
        return False
    if v is True:
        return True
    if isinstance(v, (int, float)):
        return not (v == 0 or v != v)
    if isinstance(v, str):
        return v != ""
    return True


def _js_string(v):
    if v is UNDEFINED:
        return "undefined"
    if isinstance(v, list):
        return ",".join("" if (x is None or x is UNDEFINED) else _js_string(x) for x in v)
    if isinstance(v, dict):
        return "[object Object]"
    return js_str(v)


def _js_join(items, sep):
    return sep.join("" if (x is None or x is UNDEFINED) else _js_string(x) for x in items)


def _js_trim(s):
    return s.strip(_TRIM_CHARS)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _js_get(obj, key):
    if obj is None or obj is UNDEFINED:
        raise TypeError("Cannot read properties of %s (reading '%s')" % (_js_string(obj), key))
    if isinstance(obj, dict):
        return obj.get(key, UNDEFINED)
    if isinstance(obj, (list, str)) and re.fullmatch(r"0|[1-9][0-9]*", key) and int(key) < len(obj):
        return obj[int(key)]
    return UNDEFINED


def _js_obj_keys(obj):
    if isinstance(obj, dict):
        return js_keys(obj)
    if isinstance(obj, (list, str)):
        return [str(i) for i in range(len(obj))]
    return []


def _js_has_own(obj, key):
    if isinstance(obj, dict):
        return key in obj
    if isinstance(obj, (list, str)):
        return key in _js_obj_keys(obj) or (key == "length")
    return False


def _js_concat(base, *parts):
    out = list(base)
    for p in parts:
        if isinstance(p, list):
            out.extend(p)
        else:
            out.append(p)
    return out


_COLL_ORDER = [9, 10, 11, 12, 13, 32, 95, 45, 44, 59, 58, 33, 63, 46, 39, 34, 40, 41, 91, 93,
               123, 125, 64, 42, 47, 92, 38, 35, 37, 96, 94, 43, 60, 61, 62, 124, 126, 36] + \
    list(range(48, 58))
_PRIMARY = {}
for _i, _c in enumerate(_COLL_ORDER):
    _PRIMARY[chr(_c)] = _i
for _j in range(26):
    _PRIMARY[chr(97 + _j)] = _PRIMARY[chr(65 + _j)] = len(_COLL_ORDER) + _j


def _coll_keys(s):
    prim, tert = [], []
    for ch in s:
        o = ord(ch)
        if o < 128:
            if ch not in _PRIMARY:
                continue
            prim.append(_PRIMARY[ch])
            tert.append(1 if "A" <= ch <= "Z" else 0)
        else:
            prim.append(1000 + o)
            tert.append(0)
    return prim, tert


def _js_locale_compare(a, b):
    if a == b:
        return 0
    if a == "" or b == "":
        return -1 if a == "" else 1
    pa, ta = _coll_keys(a)
    pb, tb = _coll_keys(b)
    if pa != pb:
        return -1 if pa < pb else 1
    if ta != tb:
        return -1 if ta < tb else 1
    return 0


def _js_sort(arr, cmp):
    work = list(arr)
    n = len(work)
    if n < 2:
        return work
    st = {"min_gallop": 7}
    runs = []

    def gallop_left(a, key, base, length, hint):
        last_ofs, offset = 0, 1
        order = cmp(a[base + hint], key)
        if order < 0:
            max_ofs = length - hint
            while offset < max_ofs:
                order = cmp(a[base + hint + offset], key)
                if order >= 0:
                    break
                last_ofs = offset
                offset = (offset << 1) + 1
            if offset > max_ofs:
                offset = max_ofs
            last_ofs += hint
            offset += hint
        else:
            max_ofs = hint + 1
            while offset < max_ofs:
                order = cmp(a[base + hint - offset], key)
                if order < 0:
                    break
                last_ofs = offset
                offset = (offset << 1) + 1
            if offset > max_ofs:
                offset = max_ofs
            last_ofs, offset = hint - offset, hint - last_ofs
        last_ofs += 1
        while last_ofs < offset:
            m = last_ofs + ((offset - last_ofs) >> 1)
            if cmp(a[base + m], key) < 0:
                last_ofs = m + 1
            else:
                offset = m
        return offset

    def gallop_right(a, key, base, length, hint):
        last_ofs, offset = 0, 1
        order = cmp(key, a[base + hint])
        if order < 0:
            max_ofs = hint + 1
            while offset < max_ofs:
                order = cmp(key, a[base + hint - offset])
                if order >= 0:
                    break
                last_ofs = offset
                offset = (offset << 1) + 1
            if offset > max_ofs:
                offset = max_ofs
            last_ofs, offset = hint - offset, hint - last_ofs
        else:
            max_ofs = length - hint
            while offset < max_ofs:
                order = cmp(key, a[base + hint + offset])
                if order < 0:
                    break
                last_ofs = offset
                offset = (offset << 1) + 1
            if offset > max_ofs:
                offset = max_ofs
            last_ofs += hint
            offset += hint
        last_ofs += 1
        while last_ofs < offset:
            m = last_ofs + ((offset - last_ofs) >> 1)
            if cmp(key, a[base + m]) < 0:
                offset = m
            else:
                last_ofs = m + 1
        return offset

    def merge_low(base_a, len_a, base_b, len_b):
        tmp = work[base_a:base_a + len_a]
        dest, ct, cb = base_a, 0, base_b
        work[dest] = work[cb]
        dest += 1
        cb += 1
        len_b -= 1

        def succeed():
            if len_a > 0:
                work[dest:dest + len_a] = tmp[ct:ct + len_a]

        def copy_b():
            work[dest:dest + len_b] = work[cb:cb + len_b]
            work[dest + len_b] = tmp[ct]

        if len_b == 0:
            return succeed()
        if len_a == 1:
            return copy_b()
        min_gallop = st["min_gallop"]
        while True:
            wins_a = wins_b = 0
            while True:
                if cmp(work[cb], tmp[ct]) < 0:
                    work[dest] = work[cb]
                    dest += 1
                    cb += 1
                    wins_b += 1
                    len_b -= 1
                    wins_a = 0
                    if len_b == 0:
                        return succeed()
                    if wins_b >= min_gallop:
                        break
                else:
                    work[dest] = tmp[ct]
                    dest += 1
                    ct += 1
                    wins_a += 1
                    len_a -= 1
                    wins_b = 0
                    if len_a == 1:
                        return copy_b()
                    if wins_a >= min_gallop:
                        break
            min_gallop += 1
            first = True
            while wins_a >= 7 or wins_b >= 7 or first:
                first = False
                min_gallop = max(1, min_gallop - 1)
                st["min_gallop"] = min_gallop
                wins_a = gallop_right(tmp, work[cb], ct, len_a, 0)
                if wins_a > 0:
                    work[dest:dest + wins_a] = tmp[ct:ct + wins_a]
                    dest += wins_a
                    ct += wins_a
                    len_a -= wins_a
                    if len_a == 1:
                        return copy_b()
                    if len_a == 0:
                        return succeed()
                work[dest] = work[cb]
                dest += 1
                cb += 1
                len_b -= 1
                if len_b == 0:
                    return succeed()
                wins_b = gallop_left(work, tmp[ct], cb, len_b, 0)
                if wins_b > 0:
                    work[dest:dest + wins_b] = work[cb:cb + wins_b]
                    dest += wins_b
                    cb += wins_b
                    len_b -= wins_b
                    if len_b == 0:
                        return succeed()
                work[dest] = tmp[ct]
                dest += 1
                ct += 1
                len_a -= 1
                if len_a == 1:
                    return copy_b()
            min_gallop += 1
            st["min_gallop"] = min_gallop

    def merge_high(base_a, len_a, base_b, len_b):
        tmp = work[base_b:base_b + len_b]
        dest, ct, ca = base_b + len_b - 1, len_b - 1, base_a + len_a - 1
        work[dest] = work[ca]
        dest -= 1
        ca -= 1
        len_a -= 1

        def succeed():
            if len_b > 0:
                work[dest - (len_b - 1):dest + 1] = tmp[0:len_b]

        def copy_a():
            d, c = dest - len_a, ca - len_a
            work[d + 1:d + 1 + len_a] = work[c + 1:c + 1 + len_a]
            work[d] = tmp[ct]

        if len_a == 0:
            return succeed()
        if len_b == 1:
            return copy_a()
        min_gallop = st["min_gallop"]
        while True:
            wins_a = wins_b = 0
            while True:
                if cmp(tmp[ct], work[ca]) < 0:
                    work[dest] = work[ca]
                    dest -= 1
                    ca -= 1
                    wins_a += 1
                    len_a -= 1
                    wins_b = 0
                    if len_a == 0:
                        return succeed()
                    if wins_a >= min_gallop:
                        break
                else:
                    work[dest] = tmp[ct]
                    dest -= 1
                    ct -= 1
                    wins_b += 1
                    len_b -= 1
                    wins_a = 0
                    if len_b == 1:
                        return copy_a()
                    if wins_b >= min_gallop:
                        break
            min_gallop += 1
            first = True
            while wins_a >= 7 or wins_b >= 7 or first:
                first = False
                min_gallop = max(1, min_gallop - 1)
                st["min_gallop"] = min_gallop
                k = gallop_right(work, tmp[ct], base_a, len_a, len_a - 1)
                wins_a = len_a - k
                if wins_a > 0:
                    dest -= wins_a
                    ca -= wins_a
                    work[dest + 1:dest + 1 + wins_a] = work[ca + 1:ca + 1 + wins_a]
                    len_a -= wins_a
                    if len_a == 0:
                        return succeed()
                work[dest] = tmp[ct]
                dest -= 1
                ct -= 1
                len_b -= 1
                if len_b == 1:
                    return copy_a()
                k = gallop_left(tmp, work[ca], 0, len_b, len_b - 1)
                wins_b = len_b - k
                if wins_b > 0:
                    dest -= wins_b
                    ct -= wins_b
                    work[dest + 1:dest + 1 + wins_b] = tmp[ct + 1:ct + 1 + wins_b]
                    len_b -= wins_b
                    if len_b == 1:
                        return copy_a()
                    if len_b == 0:
                        return succeed()
                work[dest] = work[ca]
                dest -= 1
                ca -= 1
                len_a -= 1
                if len_a == 0:
                    return succeed()
            min_gallop += 1
            st["min_gallop"] = min_gallop

    def merge_at(i):
        base_a, len_a = runs[i]
        base_b, len_b = runs[i + 1]
        runs[i] = [base_a, len_a + len_b]
        del runs[i + 1]
        k = gallop_right(work, work[base_b], base_a, len_a, 0)
        base_a += k
        len_a -= k
        if len_a == 0:
            return
        len_b = gallop_left(work, work[base_a + len_a - 1], base_b, len_b, len_b - 1)
        if len_b == 0:
            return
        if len_a <= len_b:
            merge_low(base_a, len_a, base_b, len_b)
        else:
            merge_high(base_a, len_a, base_b, len_b)

    def inv_ok(k):
        if k < 2:
            return True
        return runs[k - 2][1] > runs[k - 1][1] + runs[k][1]

    def merge_collapse():
        while len(runs) > 1:
            k = len(runs) - 2
            if not inv_ok(k + 1) or not inv_ok(k):
                if runs[k - 1][1] < runs[k + 1][1]:
                    k -= 1
                merge_at(k)
            elif runs[k][1] <= runs[k + 1][1]:
                merge_at(k)
            else:
                break

    def merge_force_collapse():
        while len(runs) > 1:
            k = len(runs) - 2
            if k > 0 and runs[k - 1][1] < runs[k + 1][1]:
                k -= 1
            merge_at(k)

    def count_and_make_run(low_arg, high):
        low = low_arg + 1
        if low == high:
            return 1
        run_len = 2
        prev = work[low]
        desc = cmp(prev, work[low - 1]) < 0
        for idx in range(low + 1, high):
            cur = work[idx]
            order = cmp(cur, prev)
            if desc:
                if order >= 0:
                    break
            elif order < 0:
                break
            prev = cur
            run_len += 1
        if desc:
            work[low_arg:low_arg + run_len] = work[low_arg:low_arg + run_len][::-1]
        return run_len

    def binary_insertion_sort(low, start, high):
        if low == start:
            start += 1
        while start < high:
            left, right = low, start
            pivot = work[start]
            while left < right:
                mid = left + ((right - left) >> 1)
                if cmp(pivot, work[mid]) < 0:
                    right = mid
                else:
                    left = mid + 1
            work[left + 1:start + 1] = work[left:start]
            work[left] = pivot
            start += 1

    rem, low = n, 0
    m, r = n, 0
    while m >= 64:
        r |= m & 1
        m >>= 1
    min_run = m + r
    while rem != 0:
        cur = count_and_make_run(low, low + rem)
        if cur < min_run:
            forced = min(min_run, rem)
            binary_insertion_sort(low, low + cur, low + forced)
            cur = forced
        runs.append([low, cur])
        merge_collapse()
        low += cur
        rem -= cur
    merge_force_collapse()
    return work


def _js_or(*vals):
    for v in vals:
        if _js_truthy(v):
            return v
    return vals[-1]


class _Rows(list):
    dropped_unnamed = UNDEFINED


def collectRows(node, out):
    if not isinstance(node, (dict, list)):
        return out
    if isinstance(node, list):
        for el in node:
            if isinstance(el, dict) and isinstance(el.get("ltcode", UNDEFINED), str) \
                    and isinstance(el.get("name", UNDEFINED), str):
                if not _js_trim(el["name"]):
                    prev = out.dropped_unnamed
                    out.dropped_unnamed = _js_concat(prev if _js_truthy(prev) else [], el["ltcode"])
                    continue
                tags = el.get("tags", UNDEFINED)
                out.append({"name": el["name"], "ltcode": el["ltcode"],
                            "tags": tags if _js_truthy(tags) else "",
                            "size": el.get("size", UNDEFINED), "sample": el.get("sample", UNDEFINED)})
            else:
                collectRows(el, out)
        return out
    for k in js_keys(node):
        collectRows(node[k], out)
    return out


def rowsFrom(x):
    return collectRows(x, _Rows())


_SF_PCT = _t(r"%\s*$")
_SF_BPS = _t(r"\d\s*(bps|bp)\s*\)?\s*$", "i")
_SF_DAYS = _t(r"\d\s*(days?|hrs?|hours?|weeks?|months?)\s*\)?\s*$", "i")
_SF_MULT = _t(r"\d\s*[x×]\s*\)?\s*$", "i")
_MINOR_RE = _t(r"(?:^\s*[-(]?\s*(USCents?|USDc|USc|GBPence|GBp|EURCents?|EURc|ZARc|JPYSen)\s*[\d(]|(USCents?|USDc|USc|GBPence|GBp|EURCents?|EURc|ZARc|JPYSen)\s*\)?\s*$)", "i")
_ISO_RE = _t(r"^[-(]?\s*(US\$|R\$|A\$|C\$|HK\$|NT\$|S\$|NZ\$|USD|EUR|GBP|CHF|DKK|SEK|NOK|CAD|JPY|BRL|MXN|CLP|COP|ARS|PEN|ZAR|INR|IDR|KRW|TWD|HKD|CNY|RMB|SGD|MYR|THB|PHP|VND|PLN|CZK|HUF|TRY|ILS|AED|SAR|QAR|EGP|NGN|KES|AUD|NZD)\s*[\d(]", "i")
_SF_SCALE = _t(r"[mMbBtT]\s*\)?\s*$")
_SF_CUR = _t(r"[$€£¥]")
_SF_BARE = _t(r"^[-(]?\s*[\d,.]+\s*[kKmMbBtT]?\s*\)?$")


def sampleFmt(sample):
    if sample is None or sample is UNDEFINED:
        return None
    s = _js_trim(_js_string(sample))
    if not s:
        return None
    if _SF_PCT(s):
        return "%"
    if _SF_BPS(s):
        return "%"
    if _SF_DAYS(s):
        return "#"
    if _SF_MULT(s):
        return "X"
    if _MINOR_RE(s):
        return "$"
    if _ISO_RE(s):
        return "$M" if _SF_SCALE(s) else "$"
    if _SF_CUR(s):
        return "$M" if _SF_SCALE(s) else "$"
    if _SF_BARE(s):
        return "#"
    return None



STOCK_NAME_RE = _t("\\b(" + "|".join([
    "backlog", "order book", "orders on hand", "remaining performance obligation", "rpo",
    "inventor(?:y|ies)", "dealer inventory", "deposits?", "loans?", "assets under management",
    "aum", "total assets", "book value", "headcount", "employees", "stores?", "store count",
    "units in operation", "installed base", "fleet size", "subscribers?", "members?",
    "shares outstanding", "net debt", "total debt",
    "rate base", "regulatory capital", "installed capacity", "capacity", "contracted backlog",
    "properties", "square feet", "sq ft", "real estate at cost", "gross investment",
    "policies in force", "policies", "customer accounts", "connections", "meters",
    "annual recurring revenue", "arr", "annuali[sz]ed", "run.?rate"
]) + ")\\b", "i")
_BS_PREFIX = _t(r"^BS_")


def flowOf(ltcode, name):
    if _BS_PREFIX(_js_string(ltcode)):
        return False
    if STOCK_NAME_RE(_js_string(name if _js_truthy(name) else "")):
        return False
    return True


FMT_UNSURE = []
_FO_EXPLICIT = _t(r"^(\$M|\$|%|#)$")
_FO_M = _t(r"-M$")
_FO_YQ = _t(r"-[YQ]$")
_NAME_UNIT_RE = _t(r"\((GWh|MWh|TWh|kWh|GW|MW|MMBtu|Bcfe?|MMcf|Mcf|MBoe|Boe|boe\/d|kboe\/d|mboe\/d|bbls?\/d|kbd\/d|bbls?|MBbls?|kt|koz|Moz|oz|tonnes?|tons?|units?|MMlb|lbs?|sq ?ft|acres?|homes?|vehicles?|wafers?|subscribers?|customers?|accounts?|policies|members?|000s|mn|#)\)", "i")
_FO_PCTOPEN = _t(r"^\s*%")
_FO_PCTWORD = _t(r"^Margin\b|% ?Revenue|% of|Ratio\b|Take Rate|Yield\b", "i")
_FO_RATEKPI = _t(r"\b(occupancy|utili[sz]ation|attrition|churn|retention|penetration|delinquenc|renewal rate|conversion rate|win rate|attach rate|default rate|payor mix|census|load factor)\b", "i")
_FO_GROWTH = _t(r"\bgrowth\b", "i")
_FO_USD = _t(r"\(USD\)|\(\$|\bUSD\b", "i")
_FO_BANKRATIO = _t(r"^(NIM|ROTCE|ROA|ROE|ROIC|ETR)\b", "i")
_FO_COVER = _t(r"\/\s*(EBITDA|EBIT|Sales|Revenue|Assets|Equity|Capital)\b", "i")
_FO_PERSHARE_DEN = _t(r"\/\s*Share", "i")
_FO_EPS = _t(r"\bEPS\b|\/Share\b", "i")
_FO_EPSCODE = _t(r"^IS_P_(127|128|251|252)$")
_FO_PSCODE = _t(r"^(CF_P_(201|204|205)|BS_P_201)$")
_FO_PERUNIT = _t(r"\b(RevPAR|RevPOR|RevPOB|RevPAC|TRevPAR|ADR|ASP|ARPU|ARPPU|AOV|AISC|PRASM|RASM|CASM)\b|\bper\s+(unit|bed|room|day|night|member|subscriber|resident|patient|occupied|barrel|bbl|boe|Mcf|MMBtu|tonne|ton|ounce|oz|mile)\b|\brate per\b|\bprice per\b|\b(realized|blended)(\s+\w+)?\s+price\b", "i")
_FO_PHYS = _t(r"\((GWh|MWh|TWh|kWh|GW|MW|MMBtu|Bcfe?|MMcf|Mcf|MBoe|boe|bbls?|MBbls?|kt|koz|Moz|oz)\)", "i")
_FO_MONEY = _t(r"revenue|sales|income|profit|margin|ebit|cash|price|cost|expense|spend|backlog|bookings|GMV|TPV|AUM|ARR|RPO|orders|\bNOI\b|\bFFO\b|\bAFFO\b|premium", "i")
_FO_UNITPAREN = _t(r"\((GWh|MWh|TWh|kWh|GW|MW|MMBtu|Bcfe?|MMcf|Mcf|MBoe|boe|bbls?|MBbls?|kt|units|000s|mn|#)\)", "i")
_FO_COUNTWORD = _t(r"\b(volume|units?|apartments?|beds?|rooms?|keys|communities|facilities|properties|sites?|clinics?|hospitals?|deliveries|shipments|subscribers?|subscriptions?|members?|memberships?|customers?|accounts?|users?|DAUs?|MAUs?|passengers?|room nights|stores?|locations?|restaurants?|branches?|headcount|employees?|production|throughput|net adds|gross adds|GWh|MWh|TWh|Bcf|boe|barrels?|tonnes?|ounces|wafers?|vehicles?|aircraft|homes?)\b", "i")
_FO_AREA = _t(r"\b(square foot(age)?|square feet|sq\.? ?ft\.?|selling space|floor space|acres?|hectares?)\b", "i")
_FO_UNITTOKEN = _t(r"[a-z%$€£¥]", "i")


def fmtOf(ltcode, name, sample, explicit=UNDEFINED):
    if _js_truthy(explicit) and _FO_EXPLICIT(_js_string(explicit)):
        return _js_string(explicit)
    lc = ltcode if _js_truthy(ltcode) else ""
    nm = name if _js_truthy(name) else ""
    shape = sampleFmt(sample)
    if shape == "X":
        return "X"
    if shape == "%":
        return "%"
    if _FO_M(lc):
        return "%"
    if _FO_YQ(lc):
        return "%"
    if shape == "$":
        return "$"
    if shape == "$M" and _NAME_UNIT_RE(nm):
        return "#"
    if shape == "$M":
        return "$M"
    if _FO_PCTOPEN(nm):
        return "%"
    if _FO_PCTWORD(nm):
        return "%"
    if _FO_RATEKPI(nm):
        return "%"
    if _FO_GROWTH(nm) and not _FO_USD(nm):
        return "%"
    if _FO_BANKRATIO(nm):
        return "%"
    if _FO_COVER(nm) and not _FO_PERSHARE_DEN(nm):
        return "%"
    if _FO_EPS(nm):
        return "$"
    if _FO_EPSCODE(lc):
        return "$"
    if _FO_PSCODE(lc):
        return "$"
    if _FO_PERUNIT(nm):
        return "$"
    if _FO_PHYS(nm):
        return "#"
    if not _FO_MONEY(nm) and (_FO_UNITPAREN(nm) or _FO_COUNTWORD(nm) or _FO_AREA(nm)):
        return "#"
    if shape == "#":
        return "#"
    if sample is not UNDEFINED and sample is not None and \
            _FO_UNITTOKEN(_js_string(sample)) and shape is None:
        FMT_UNSURE.append({"ltcode": lc, "name": nm, "sample": _js_string(sample)})
    return "$M"


def tagList(r):
    tags = r["tags"]
    if isinstance(tags, list):
        return [_js_trim(_js_string(x)) for x in tags]
    if isinstance(tags, str):
        return [_js_trim(x) for x in tags.split("|")]
    return []


def hasTag(r, t):
    return t in tagList(r)


_DERIVED = _t(r"-(M|Y|Q|IMY|IMQ|IMM)$")
_NORM_RX = _jsre(r"[^a-z0-9]")


def isDerived(lc):
    return _DERIVED(lc)


def norm(s):
    return _NORM_RX.sub("", (s if _js_truthy(s) else "").lower())


def pick(rows, predicate, opts=None):
    opts = opts or {}
    cands = [r for r in rows if not isDerived(r["ltcode"]) and predicate(r)]
    if not cands:
        return None
    if opts.get("adjusted"):
        for r in cands:
            if hasTag(r, "adjusted"):
                return r
    return cands[0]


def pickMargin(rows, predicate):
    c = _js_sort([r for r in rows if _FO_M(r["ltcode"]) and predicate(r)],
                 lambda a, b: int(hasTag(b, "adjusted")) - int(hasTag(a, "adjusted")))
    return c[0] if c else None


_RANK = {"dropped_multiple": UNDEFINED, "topped_up": UNDEFINED}
_SDKM = _t(r"^(SD_|KM_)")
_TOTREV = _t(r"^Total Revenue$", "i")
_KIND_RE = _t(r"revenue|sales|gmv|bookings|volume|units|subscrib|members?|deliver", "i")
_GEO_CODE = _t(r"^SD_P_Geo")
_GEO_NAME = _t(r"^(US|International|Japan|Europe|China|Americas|EMEA|APAC|Asia|Rest of World|ROW)\b|International Revenue|- Revenue by Geography", "i")
_SIZE_RX = _jsre(r"([0-9][0-9,.]*)\s*([TBMk])\b")
_GUIDE_SUFFIX = _jsre(r"-(Y|Q|M|BY)$")
_SCALE = {"t": 1e12, "b": 1e9, "m": 1e6, "k": 1e3}


def rankTopKpis(rows, fallbackPool, guided_ltcodes):
    seen, uniq, dropped_multiple = set(), [], []
    for r in rows:
        if not hasTag(r, "top_kpi"):
            continue
        if r["ltcode"] in seen:
            continue
        if fmtOf(r["ltcode"], r["name"], r["sample"], r.get("fmt", UNDEFINED)) == "X":
            dropped_multiple.append(r["name"] + " (" + _js_string(r["sample"]) + ")")
            continue
        seen.add(r["ltcode"])
        uniq.append(r)
    _RANK["dropped_multiple"] = dropped_multiple
    usedFallback = False
    toppedUp = []
    if len(uniq) < 5 and fallbackPool and len(fallbackPool):
        usedFallback = len(uniq) == 0
        before = len(uniq)
        for r in fallbackPool:
            if not _SDKM(r["ltcode"]):
                continue
            if isDerived(r["ltcode"]):
                continue
            if fmtOf(r["ltcode"], r["name"], r["sample"], r.get("fmt", UNDEFINED)) == "%":
                continue
            if _TOTREV(r["name"]):
                continue
            if r["ltcode"] in seen:
                continue
            seen.add(r["ltcode"])
            uniq.append(r)
            toppedUp.append(r["ltcode"] + " " + r["name"])
        if before > 0 and len(uniq) > before:
            _RANK["topped_up"] = toppedUp

    def fk(r):
        return fmtOf(r["ltcode"], r["name"], r["sample"], r.get("fmt", UNDEFINED))

    def tagW(r):
        return (4 if hasTag(r, "driver_kpi") else 0) + (2 if hasTag(r, "earnings_flash_kpi") else 0) + \
               (1 if hasTag(r, "industry_kpi") else 0)

    def kindW(r):
        if fk(r) == "%":
            return 0
        if _KIND_RE(r["name"]):
            return 3
        return 2

    def isGeo(r):
        return _GEO_CODE(r["ltcode"]) or _GEO_NAME(r["name"])

    def geoW(r):
        return 0 if (usedFallback and isGeo(r)) else 1

    def sizeOf(r):
        sz = r["size"]
        n = sz if (_is_num(sz) and math.isfinite(sz)) else None
        if n is None:
            return None
        m = _SIZE_RX.search(_js_string(r["sample"] if _js_truthy(r["sample"]) else ""))
        if not m:
            return n
        digits = js_parse_float(m.group(1).replace(",", ""))
        if not math.isfinite(digits) or digits == 0:
            return n
        if abs(abs(n) - abs(digits)) > abs(digits) * 0.01:
            return n
        mult = _SCALE[m.group(2).lower()]
        return n * mult if mult else n

    def sizeCmp(a, b):
        sa, sb = sizeOf(a), sizeOf(b)
        if sa is None or sb is None:
            return 0
        return sb - sa

    def bandW(r):
        s = sizeOf(r)
        if s is None or abs(s) < 1:
            return 0
        if math.isinf(s):
            return float("inf")
        return math.floor(math.log10(abs(s)))

    def bandCmp(a, b):
        return (bandW(b) - bandW(a)) if fk(a) == fk(b) else 0

    guidedKpi = set(_GUIDE_SUFFIX.sub("", _js_string(x), count=1)
                    for x in _js_concat([], guided_ltcodes if _js_truthy(guided_ltcodes) else []))

    def guidedW(r):
        return 1 if _GUIDE_SUFFIX.sub("", _js_string(r["ltcode"]), count=1) in guidedKpi else 0

    def pctW(r):
        return 0 if fk(r) == "%" else 1

    def cmp(a, b):
        for f in (lambda: pctW(b) - pctW(a), lambda: guidedW(b) - guidedW(a),
                  lambda: bandCmp(a, b), lambda: kindW(b) - kindW(a), lambda: tagW(b) - tagW(a),
                  lambda: geoW(b) - geoW(a), lambda: sizeCmp(a, b)):
            v = f()
            if _js_truthy(v):
                return v
        return _js_locale_compare(a["ltcode"], b["ltcode"])

    ranked = [{"name": r["name"], "ltcode": r["ltcode"], "fmt": fk(r),
               "polarity": polarityOf(r["ltcode"], r["name"]), "flow": flowOf(r["ltcode"], r["name"]),
               "pctrev": pctrevOf(r["ltcode"], r["name"])}
              for r in _js_sort(uniq, cmp)[:5]]
    return {"kpis": ranked, "usedFallback": usedFallback}


PCTREV_DENY = [
    _t(r"\b(acquisitions?|dispositions?|divestitures?|asset sales?)\b", "i"),
    _t(r"\b(capital deploy|investment volume|deployment volume|development spend|land (purchase|acquisition))\b", "i"),
    _t(r"\bpremiums? written\b|\bgross written\b|\bnet written\b|\bnew business (written|premium)\b", "i"),
]
PCTREV_ALLOW = [_t(r"\brevenue\b|premiums? earned\b|\bNPE\b", "i")]


def pctrevOf(ltcode, name):
    nm = _js_string(name if _js_truthy(name) else "")
    if any(t(nm) for t in PCTREV_ALLOW):
        return UNDEFINED
    if any(t(nm) for t in PCTREV_DENY):
        return False
    return UNDEFINED


def sectorOf(ti):
    ti = ti if _js_truthy(ti) else {}
    return _js_or(_js_get(ti, "insync_subindustry"), _js_get(ti, "subindustry"),
                  _js_get(ti, "classification"), None)


_REV_NEVER = _t(r"deferred|unearned|contract liabilit|revenue received in advance|billings in excess", "i")
_REV_NARROW = _t(r"revenue\s*[-–:]\s*\S", "i")
_REVENUE = _t(r"revenue", "i")
_IS101 = _t(r"^IS_P_101$")
_AFFO_RE = _t(r"\bAFFO\b|adjusted funds from operations", "i")
_FFO_RE = _t(r"\bFFO\b|funds from operations", "i")
_PERSHARE = _t(r"\/\s*share\b|per share\b|\bPS\b", "i")
_NORMALIZED = _t(r"normali[sz]ed", "i")
_ADJNAME = _t(r"ex[-\s]?1x|adj|non[-\s]?gaap", "i")
_EFF = _t(r"^Efficiency Ratio\b", "i")
_FTE = _t(r"FTE", "i")


def buildSpine(spine, guided_ltcodes, ti):
    ti = ti if _js_truthy(ti) else {}

    def R(q):
        v = _js_get(spine, q)
        return rowsFrom(v if _js_truthy(v) else [])

    notes = []
    _rs = _pol_registry_status()
    if not _rs or not _rs.get("ok"):
        err = _rs.get("error") if _rs else None
        notes.append("WARN: POLARITY REGISTRY DID NOT LOAD (" + _js_string(err if _js_truthy(err) else "?") +
                     ") - every declared direction reverted to the name lexicon. A cost line the lexicon " +
                     "does not recognise is now graded higher-is-better. Restore " +
                     "assets/polarity_registry.json before trusting any direction on this sheet.")
    seen, allRows = set(), []
    for k in _js_obj_keys(spine if _js_truthy(spine) else {}):
        for r in R(k):
            if r["ltcode"] not in seen:
                seen.add(r["ltcode"])
                allRows.append(r)
    revBucket = R("revenue")

    def revBad(r):
        return _REV_NEVER(r["name"]) or _BS_PREFIX(r["ltcode"])

    revRejected = [r for r in revBucket if revBad(r) and _REVENUE(r["name"])]
    revOk = [r for r in revBucket if not revBad(r)]
    rev = pick(revOk, lambda r: _IS101(r["ltcode"]) or norm(r["name"]) == "totalrevenue") \
        or pick(revOk, lambda r: _REVENUE(r["name"]) and not _REV_NARROW(r["name"])) \
        or pick(revOk, lambda r: _REVENUE(r["name"]))
    if revRejected:
        notes.append("WARN: rejected " + js_str(len(revRejected)) + " row(s) from the REVENUE slot because " +
                     "they are balance-sheet or deferred/unearned items, not revenue: " +
                     ", ".join(r["name"] + " (" + r["ltcode"] + ")" for r in revRejected) +
                     ". Such a balance in the top-line slot becomes the denominator of every " +
                     "% of Total Revenue cell and the base of the whole growth grid.")
    if rev and not _IS101(rev["ltcode"]) and norm(rev["name"]) != "totalrevenue":
        notes.append("WARN: the REVENUE slot did NOT resolve to IS_P_101 or a row named \"Total " +
                     "Revenue\"; it fell back to \"" + rev["name"] + "\" (" + rev["ltcode"] + ")" +
                     (", which reads as a SINGLE segment or geography - almost " +
                      "certainly a fetch gap, not the issuer's top line" if _REV_NARROW(rev["name"]) else "") +
                     ". Verify this is the issuer's consolidated top line before the document is built: " +
                     "every % of Total Revenue cell, the revenue growth grid, the beat series and the " +
                     "whisper are all computed against it.")
    _ebitda, _ebit = _t(r"^EBITDA", "i"), _t(r"^EBIT\b", "i")
    prof = pick(R("ebitda"), lambda r: _ebitda(r["name"]), {"adjusted": True}) \
        or pick(R("ebit"), lambda r: _ebit(r["name"]), {"adjusted": True})
    isREIT = _t(r"reit|real estate", "i")(norm(_js_string(_js_or(sectorOf(ti), "")) + " " +
                                              _js_string(_js_or(_js_get(ti, "industry"), ""))))
    bankProf = False
    if not prof:
        pool = allRows
        _ppnr, _fte = _t(r"^PPNR\b", "i"), _t(r"\bFTE\b", "i")
        _pbt, _ex1x = _t(r"^PBT\b", "i"), _t(r"ex[-\s]?1x|excl", "i")
        _pretax, _ni = _t(r"^Pre-?Tax (Profit|Income)\b", "i"), _t(r"^Net Income\b", "i")
        prof = pick(pool, lambda r: _t(r"^IS_P_210$")(r["ltcode"]) or (_ppnr(r["name"]) and _fte(r["name"]))) \
            or pick(pool, lambda r: _t(r"^IS_P_209$")(r["ltcode"]) or _ppnr(r["name"])) \
            or pick(pool, lambda r: _t(r"^IS_P_118$")(r["ltcode"]) or (_pbt(r["name"]) and _ex1x(r["name"]))) \
            or pick(pool, lambda r: _t(r"^IS_P_117$")(r["ltcode"]) or _pbt(r["name"]) or _pretax(r["name"])) \
            or pick(pool, lambda r: _t(r"^IS_P_124$")(r["ltcode"]) or (_ni(r["name"]) and _ex1x(r["name"])))
        if prof:
            bankProf = True
            notes.append("NOTE: no EBITDA/EBIT line exists for this ticker (correct for a financial - " +
                         "interest expense is a lender's cost of goods, not a financing line); profitability " +
                         "resolved via the FINANCIALS ladder (PPNR -> PBT -> Net Income Ex-1x, a bank sequence an " +
                         "insurer or a REIT reaches by default) to " + prof["ltcode"] + " (" + prof["name"] + ").")
    if isREIT:
        reitProf = pick(allRows, lambda r: _t(r"^CF_P_114$")(r["ltcode"]) or (_AFFO_RE(r["name"]) and not _PERSHARE(r["name"]))) \
            or pick(allRows, lambda r: _t(r"^CF_P_113$")(r["ltcode"]) or (_FFO_RE(r["name"]) and not _PERSHARE(r["name"]) and not _NORMALIZED(r["name"]))) \
            or pick(allRows, lambda r: _FFO_RE(r["name"]) and _NORMALIZED(r["name"]) and not _PERSHARE(r["name"]))
        if reitProf and (not prof or not _AFFO_RE(prof["name"])):
            notes.append("NOTE: real-estate issuer; profitability resolved via the REIT ladder to " +
                         reitProf["ltcode"] + " (" + reitProf["name"] + ")" +
                         (", displacing " + prof["ltcode"] if prof else "") +
                         " - FFO/AFFO is a REIT's earnings line, GAAP net income is depressed by property depreciation.")
            prof = reitProf
            bankProf = True
    reitEps = (pick(allRows, lambda r: _t(r"^CF_P_203$")(r["ltcode"]) or (_AFFO_RE(r["name"]) and _PERSHARE(r["name"])))
               or pick(allRows, lambda r: _t(r"^CF_P_202$")(r["ltcode"]) or (_FFO_RE(r["name"]) and _PERSHARE(r["name"])))) \
        if isREIT else None
    _epsn, _basic, _epsc = _t(r"\bEPS\b", "i"), _t(r"basic", "i"), _t(r"^IS_P_(128|127)$")
    eps = reitEps \
        or pick(R("eps"), lambda r: _epsn(r["name"]) and not _basic(r["name"]), {"adjusted": True}) \
        or pick(R("eps"), lambda r: _epsc(r["ltcode"]))
    if reitEps:
        notes.append("NOTE: the per-share slot holds " + reitEps["ltcode"] + " (" + reitEps["name"] +
                     ") rather than GAAP EPS - for a REIT that is the line management guides and the Street models.")
    if not isinstance(guided_ltcodes if _js_truthy(guided_ltcodes) else [], list):
        raise TypeError("(guided_ltcodes || []).map is not a function")
    guidedSet = set(_js_string(x) for x in (guided_ltcodes if _js_truthy(guided_ltcodes) else []))
    guidedMarginCands = _js_sort(
        [r for r in allRows if _FO_M(r["ltcode"]) and r["ltcode"] in guidedSet],
        lambda a, b: _js_or(int(hasTag(b, "adjusted")) - int(hasTag(a, "adjusted")),
                            int(_ADJNAME(b["name"])) - int(_ADJNAME(a["name"])),
                            _js_locale_compare(a["ltcode"], b["ltcode"]))) if guidedSet else []
    guidedMargin = guidedMarginCands[0] if guidedMarginCands else None
    if len(guidedMarginCands) > 1:
        notes.append("NOTE: the company guides " + js_str(len(guidedMarginCands)) + " margin lines (" +
                     ", ".join(r["ltcode"] for r in guidedMarginCands) + "); the slot took " + guidedMargin["ltcode"] +
                     " on the adjusted-first ladder. Name the basis in the report - a GAAP and a non-GAAP " +
                     "operating margin can be 14 points apart and both are \"guided\".")

    def _find(pred):
        for r in allRows:
            if pred(r):
                return r
        return None

    marg = guidedMargin \
        or (_find(lambda r: r["ltcode"] == prof["ltcode"] + "-M") if prof else None) \
        or pickMargin(allRows, lambda r: _t(r"Margin - EBIT", "i")(r["name"])) \
        or pickMargin(allRows, lambda r: _t(r"Margin - Gross Profit", "i")(r["name"]))
    if guidedMargin:
        notes.append("NOTE: the margin slot is " + guidedMargin["ltcode"] +
                     " because the company GUIDES it; the profit line is " + (prof["ltcode"] if prof else "n/a") +
                     ". This puts the guided margin on the page and into Guidance Evolution, at the cost of " +
                     "the printed margin not being the printed profit over the printed revenue.")
    if prof and marg and marg["ltcode"] != prof["ltcode"] + "-M":
        notes.append("NOTE: the margin row (" + marg["ltcode"] + ") is not the " + prof["ltcode"] +
                     " margin; the printed margin will not equal the printed profit over the printed revenue. " +
                     "Fetch " + prof["ltcode"] + "-M if InSync carries it.")
    _is206 = _t(r"^IS_P_206$")
    if not marg and bankProf:
        marg = _find(lambda r: r["ltcode"] == prof["ltcode"] + "-M") \
            or _find(lambda r: _is206(r["ltcode"])) \
            or _find(lambda r: _EFF(r["name"]) and _FTE(r["name"])) \
            or _find(lambda r: _EFF(r["name"])) \
            or None
        if marg and _EFF(marg["name"]) and not _FTE(marg["name"]) and \
                not any(_is206(r["ltcode"]) for r in allRows):
            notes.append("NOTE: the margin slot is the NON-FTE Efficiency Ratio (" + marg["ltcode"] +
                         ") because IS_P_206 was not in the fetched pool, while the profit line is " +
                         prof["ltcode"] + ". Fetch IS_P_206 to put both on the same basis.")
    if not marg:
        _mni, _cr, _mpbt = _t(r"^Margin - Net Income\b", "i"), _t(r"^Combined Ratio", "i"), _t(r"^Margin - PBT\b", "i")
        marg = _find(lambda r: _mni(r["name"]) and hasTag(r, "adjusted")) \
            or _find(lambda r: _mni(r["name"])) \
            or _find(lambda r: _cr(r["name"])) \
            or _find(lambda r: _mpbt(r["name"])) \
            or None
        if marg:
            notes.append("NOTE: no EBIT/gross margin and no bank ladder margin; the headline " +
                         "margin resolved to " + marg["ltcode"] + " (" + marg["name"] + ").")
    _fcfn, _fcfc = _t(r"^FCF$", "i"), _t(r"^CF_P_110$")
    fcfPool = R("fcf")
    fcf = pick(fcfPool, lambda r: _fcfn(r["name"]) or _fcfc(r["ltcode"])) \
        or (fcfPool[0] if fcfPool else None) or None
    if fcf and not _fcfn(fcf["name"]) and not _fcfc(fcf["ltcode"]):
        notes.append("NOTE: the FCF slot is the COMPANY-SPECIFIC definition " + fcf["ltcode"] + " (\"" +
                     fcf["name"] + "\"), not the consolidated CF_P_110, because that is what was supplied in " +
                     "spine.fcf. The row carries its own name; say in the report which definition the " +
                     "document prints, since it will not equal a consolidated FCF screen.")
    ocf = pick(R("ocf"), lambda r: _t(r"^OCF$", "i")(r["name"]) or _t(r"^CF_P_108$")(r["ltcode"]))
    capex = pick(R("capex"), lambda r: _t(r"capex|capital expenditure", "i")(r["name"]) or _t(r"^CF_P_109$")(r["ltcode"]))
    _nettot, _tot, _incl = _t(r"net debt|total debt", "i"), _t(r"total debt", "i"), _t(r"incl", "i")
    debtCands = [r for r in R("debt") if _nettot(r["name"]) and not _t(r"^RT_")(r["ltcode"]) and
                 not _t(r"\/")(r["name"])]
    debt = pick(debtCands, lambda r: _t(r"^BS_P_108$")(r["ltcode"])) \
        or pick(debtCands, lambda r: _tot(r["name"]) and _incl(r["name"])) \
        or pick(debtCands, lambda r: _t(r"^BS_P_114$")(r["ltcode"])) \
        or pick(debtCands, lambda r: _tot(r["name"])) \
        or pick(debtCands, lambda r: _t(r"^BS_P_109$")(r["ltcode"])) \
        or pick(debtCands, lambda r: _t(r"net debt", "i")(r["name"]))
    if debt and not _t(r"^BS_P_108$")(debt["ltcode"]):
        notes.append("NOTE: debt row resolved to " + debt["ltcode"] + " (" + debt["name"] +
                     "); InSync EV assumes incl-operating-lease total debt — check the [valuation] reconciliation.")
    cash = pick(R("cash"), lambda r: _t(r"^cash( and| &|$)", "i")(r["name"]) or _t(r"^BS_P_(101|141)$")(r["ltcode"]))

    fin = []

    def add(r, forceFmt):
        if r and not any(f["ltcode"] == r["ltcode"] for f in fin):
            fin.append({"name": r["name"], "ltcode": r["ltcode"],
                        "fmt": forceFmt or fmtOf(r["ltcode"], r["name"], r["sample"], r.get("fmt", UNDEFINED)),
                        "polarity": polarityOf(r["ltcode"], r["name"]), "flow": flowOf(r["ltcode"], r["name"]),
                        "pctrev": pctrevOf(r["ltcode"], r["name"])})
    add(rev, "$M")
    add(prof, "$M")
    add(eps, "$")
    add(marg, "%")
    add(fcf, "$M")
    if not rev:
        notes.append("WARN: no Total Revenue row found — check the revenue query.")
    if not prof:
        notes.append("WARN: no profitability row found — no EBITDA/EBIT, and the bank ladder (PPNR/PBT/Net Income Ex-1x) matched nothing.")
    if not eps:
        notes.append("WARN: no EPS row found.")

    def suppliedEmpty(k):
        if not _js_has_own(spine, k):
            return False
        v = _js_get(spine, k)
        return len(rowsFrom(v if _js_truthy(v) else [])) == 0

    fcfSuppliedEmpty = suppliedEmpty("fcf")
    return {
        "fin_metrics": fin[:5],
        "eps_ltcode": eps["ltcode"] if eps else None,
        "fcf_ltcode": fcf["ltcode"] if fcf else (None if fcfSuppliedEmpty else "CF_P_110"),
        "ocf_ltcode": ocf["ltcode"] if ocf else (None if suppliedEmpty("ocf") else "CF_P_108"),
        "capex_ltcode": capex["ltcode"] if capex else (None if suppliedEmpty("capex") else "CF_P_109"),
        "debt_ltcode": debt["ltcode"] if debt else "BS_P_108",
        "cash_ltcode": cash["ltcode"] if cash else "BS_P_101",
        "notes": notes,
    }


def _js_json_parse(text):
    def _int(s):
        v = int(s)
        return v if abs(v) < 2 ** 53 else float(s)

    def _const(c):
        raise ValueError("Unexpected token " + c[0] + " in JSON")
    return json.loads(text, parse_int=_int, parse_constant=_const)


def _read_utf8(p):
    with open(p, "rb") as f:
        return f.read().decode("utf-8", errors="replace")


def loadValuationRules():
    p = os.path.join(HERE, "..", "assets", "valuation_rules.json")
    return _js_json_parse(_read_utf8(p))


def multiplesFor(classification, industry, rules):
    key, ind = norm(classification), norm(industry if _js_truthy(industry) else "")
    match = None
    for rule in rules["rules"]:
        def hit(m):
            n = norm(m)
            return (bool(key) and (n in key or key in n)) or (bool(ind) and n in ind)
        if any(hit(m) for m in rule["match"]):
            match = rule
            break
    return (match or rules["default"])["multiples"]


def _err(line):
    sys.stderr.write(line + "\n")


def main():
    js_stdio()
    argv = sys.argv
    inPath = argv[1] if len(argv) > 1 else ""
    outPath = argv[2] if len(argv) > 2 else ""
    if not inPath or not outPath:
        _err("usage: node resolve_metricset.js metricset_input.json metricset.json")
        sys.exit(1)
    inp = _js_json_parse(_read_utf8(inPath))
    ti = _js_or(_js_get(inp, "tickerinfo"), {})
    sp = _js_get(inp, "spine")
    sp = sp if _js_truthy(sp) else {}
    spineRows = []
    for k in _js_obj_keys(sp):
        spineRows = spineRows + list(rowsFrom(_js_get(sp, k)))
    kfr = _js_get(inp, "kpi_fallback_rows")
    fallbackPool = list(rowsFrom(kfr if _js_truthy(kfr) else [])) + spineRows
    tkr = _js_get(inp, "top_kpi_rows")
    gl = _js_get(inp, "guided_ltcodes")
    gl = gl if _js_truthy(gl) else []
    res = rankTopKpis(rowsFrom(tkr if _js_truthy(tkr) else []), fallbackPool, gl)
    top_kpis, usedFallback = res["kpis"], res["usedFallback"]
    spine = buildSpine(sp, gl, ti)
    rules = loadValuationRules()
    multiples = multiplesFor(sectorOf(ti), _js_get(ti, "industry"), rules)

    notes = list(spine["notes"])
    du = lambda v: (lambda d: d if _js_truthy(d) else [])(rowsFrom(v).dropped_unnamed)
    spine_unnamed = []
    for k in _js_obj_keys(sp):
        spine_unnamed = _js_concat(spine_unnamed, du(_js_get(sp, k)))
    unnamed = _js_concat([], du(tkr if _js_truthy(tkr) else []), du(kfr if _js_truthy(kfr) else []),
                         spine_unnamed)
    if unnamed:
        uniq = list(dict.fromkeys(unnamed))
        notes.append("WARN: dropped " + js_str(len(unnamed)) + " candidate row(s) with a " +
                     "BLANK line-item name (" + _js_join(uniq, ", ") + "). A row with no name " +
                     "renders an unreadable label and cannot be cited or checked; the slot goes to the next " +
                     "estimate-backed candidate instead. A blank LINEITEM is an InSync metadata defect - " +
                     "worth reporting through the data-validator.")
    tu = _RANK["topped_up"]
    if tu is not UNDEFINED and len(tu):
        notes.append("NOTE: the top_kpi universe held fewer than 5 estimate-backed rows, so the KPI " +
                     "block was TOPPED UP from the segment/operating pool (v8.0.0): " +
                     "; ".join(tu) + ". Tagged rows keep priority; say in the report that " +
                     "part of the block is untagged, because the tag is the company's own signal of what matters.")
    dm = _RANK["dropped_multiple"]
    if dm is not UNDEFINED and len(dm):
        notes.append("WARN: dropped " + js_str(len(dm)) + " KPI candidate(s) " +
                     "quoted as a MULTIPLE: " + ", ".join(dm) + ". The document " +
                     "renders four formats ($M, $, %, #) and a multiple is none of them - as a count it " +
                     "would print \"2\", as a percent \"2.4%\". The slot went to the next estimate-backed " +
                     "candidate. A leverage or coverage multiple is a balance-sheet ratio, not an operating " +
                     "KPI a preview turns on; if this issuer genuinely needs one on the page, it belongs in " +
                     "the narrative, not the grid.")
    if FMT_UNSURE:
        seenU, u = set(), []
        for x in FMT_UNSURE:
            if x["ltcode"] not in seenU:
                seenU.add(x["ltcode"])
                u.append(x)
        notes.append("WARN: " + js_str(len(u)) + " row(s) fell through the whole format ladder and were " +
                     "typed $M BY DEFAULT while carrying a unit the ladder does not recognise: " +
                     ", ".join("\"" + x["name"] + "\" (" + x["ltcode"] + ", sample " + x["sample"] + ")" for x in u) +
                     ". $M is the right guess for an untagged money line, but a unit token nobody matched is " +
                     "evidence it is wrong - a fee rate in basis points and a day count both printed as " +
                     "dollars this way. Check each one against the issuer's own reporting and, if $M is " +
                     "wrong, extend fmtOf rather than editing the metric set by hand.")
    if usedFallback and top_kpis:
        notes.append("NOTE: no top_kpi tag for this ticker; KPIs derived from segment/operating line items (verify relevance).")
    if len(top_kpis) == 0:
        notes.append("NOTE: no operating KPIs found in InSync for this ticker; portal degrades to financials-only.")

    classification = sectorOf(ti)
    ind = _js_get(ti, "industry")
    out = {
        "classification": classification,
        "industry": ind if _js_truthy(ind) else None,
        "source": "insync-mcp",
        "kpi_source": "none" if len(top_kpis) == 0 else ("segment-fallback" if usedFallback else "top_kpi-tag"),
        "fin_metrics": spine["fin_metrics"],
        "top_kpis": top_kpis,
        "eps_ltcode": spine["eps_ltcode"],
        "fcf_ltcode": spine["fcf_ltcode"],
        "ocf_ltcode": spine["ocf_ltcode"],
        "capex_ltcode": spine["capex_ltcode"],
        "debt_ltcode": spine["debt_ltcode"],
        "cash_ltcode": spine["cash_ltcode"],
        "multiples": multiples,
        "gate": {
            "ok": len(spine["fin_metrics"]) >= 3,
            "top_kpi_count": len(top_kpis),
            "fin_count": len(spine["fin_metrics"]),
            "notes": notes,
        },
    }
    js_write(outPath, js_json(out, 2))
    _err("[metricset] " + _js_string(_js_or(_js_get(ti, "ticker"), "?")) +
         " (" + _js_string(_js_or(out["classification"], "n/a")) + ")")
    _err("  fin_metrics: " + _js_or(" | ".join(f["name"] + " [" + f["fmt"] + "]" for f in out["fin_metrics"]), "(none)"))
    _err("  top_kpis   : " + _js_or(" | ".join(k["name"] + " [" + k["fmt"] + "]" for k in out["top_kpis"]), "(none)"))
    _err("  multiples  : " + _js_join([_js_get(m, "label") for m in out["multiples"]], " · "))
    _err("  gate.ok=" + js_str(out["gate"]["ok"]) + "  top_kpi_count=" + js_str(out["gate"]["top_kpi_count"]) +
         "  fin_count=" + js_str(out["gate"]["fin_count"]))
    for n in out["gate"]["notes"]:
        _err("  " + n)
    if not out["gate"]["ok"]:
        sys.exit(2)


if __name__ == "__main__":
    main()
