#!/usr/bin/env python3
"""Re-derive every published figure from raw.json, independently of the engine.

WHAT THIS IS FOR, AND WHY IT IS NOT format_lint.py.

Every other check in this skill asks whether the PAGE agrees with the ENGINE.
The number-trace gate asks whether each figure on the page exists in the
payload; the sentence gates ask whether the prose names the row its figures
belong to. All of them share one assumption: that the engine's arithmetic was
right. Nothing tests that assumption, so an error in `parse_value`, in the
growth calculation or in the acceleration would be reproduced faithfully by
every gate and reported as correct.

The risk is concrete: a value parser that read the currency and then discarded
it would let every gate pass a note that printed yen as though it were dollars,
because every gate checks the page against the engine and the engine would be
consistently wrong.

So this script is a SECOND IMPLEMENTATION. It reads raw.json - the MCP's own
rows, before the engine has touched them - parses the values with its own
parser, computes growth and acceleration with its own arithmetic, and compares
the result to what the engine published. Where the two disagree, one of them is
wrong, and the disagreement is the finding.

Deliberately NOT shared with compute.py: the value parser, the period ordering,
the growth formula. Importing any of them would make this a test of whether the
engine agrees with itself.

    python3 ground_truth.py --raw raw.json --computed out/T_inflection_computed.json
                            [--tolerance 0.005] [--json]

Exit 0 when every published figure re-derives. Exit 1 naming the ones that do
not.
"""

import argparse
import json
import re
import os
import sys


_SCALE = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}
_SYM = re.compile(r"^\s*(?P<cur>[A-Z]{1,3}\$|[$\u20ac\u00a3\u00a5\u20b9\u20a9\u20bd\u00a2]"
                  r"|[A-Za-z]{2,8}(?=\s*[-+(]?\s*[\d.]))\s*")
_SUBUNIT = {"gbpence": 0.01, "gbp pence": 0.01}


def gt_parse(value):
    if value is None:
        return None, False
    if isinstance(value, (int, float)):
        return float(value), False
    s = str(value).strip()
    if not s:
        return None, False
    sub = 1.0
    neg_outer = s.startswith("(") and s.endswith(")")
    if neg_outer:
        s = s[1:-1].strip()
    m = _SYM.match(s)
    if m:
        cur = m.group("cur")
        if cur == "GBp":
            sub = 0.01
        sub = _SUBUNIT.get(cur.lower(), sub)
        s = s[m.end():].strip()
    s = re.sub(r"\s+[A-Z]{3}$", "", s)
    neg = False
    if s.startswith("(") and ")" in s:
        neg, s = True, s[1:].replace(")", "", 1).strip()
    pct = s.endswith("%")
    bps = False
    if pct:
        s = s[:-1].strip()
    elif re.search(r"\s*(bps|bp)$", s, re.I):
        pct, bps, s = True, True, re.sub(r"\s*(bps|bp)$", "", s, flags=re.I)
    elif re.search(r"\s*pp$", s, re.I):
        pct, s = True, re.sub(r"\s*pp$", "", s, flags=re.I)
    if s[-1:].lower() == "x" and re.match(r"^-?[\d.,]+x$", s, re.I):
        s = s[:-1].strip()
    mult = 1.0
    if s and s[-1].upper() in _SCALE and not s[-1].isdigit():
        mult = _SCALE[s[-1].upper()]
        s = s[:-1]
    s = s.replace(",", "").strip()
    try:
        n = float(s)
    except ValueError:
        return None, False
    neg = neg or neg_outer
    return (-abs(n) if neg else n) * mult * sub / (100.0 if bps else 1.0), pct



_LABEL = re.compile(r"^(?:R\d*-)?([1-4])Q(\d{2})$", re.I)


def gt_label(fp):
    m = _LABEL.match(str(fp or "").strip())
    return int(m.group(2)) * 4 + int(m.group(1)) - 1 if m else None


def gt_series(raw):
    out = {}
    for r in (raw.get("actuals") or []):
        code = r.get("LTCODE") or r.get("ltcode")
        li = gt_label(r.get("FISCALPERIOD") or r.get("fiscal_period"))
        if not code or li is None:
            continue
        src = (r.get("DATA_SOURCE") or r.get("data_source") or "actual").lower()
        if src != "actual":
            continue
        v, pct = gt_parse(r.get("VALUE", r.get("value")))
        if v is None:
            continue
        out.setdefault(code, {})[li] = (v, pct)
    return out


def gt_growth(cur, prior, is_pct, all_negative=False):
    if cur is None or prior is None:
        return None
    if is_pct:
        return (cur - prior) * 100.0
    if prior == 0:
        return None
    if all_negative:
        return (abs(cur) - abs(prior)) / abs(prior) * 100.0
    if (cur < 0) != (prior < 0):
        return ("sign_change", abs((cur - prior) / abs(prior) * 100.0))
    return (cur - prior) / abs(prior) * 100.0


def check(raw, payload, tol_rel=0.005):
    series = gt_series(raw)
    rows, findings, skipped, conventions = 0, [], [], []
    sections = ("deep_dive", "accelerating", "decelerating", "primary_context",
                "monitor", "quiet_headline")
    seen = set()
    for sec in sections:
        for r in (payload.get(sec) or []):
            code = r.get("ltcode")
            if not code or (sec, code) in seen:
                continue
            seen.add((sec, code))
            hist = series.get(code)
            if not hist:
                findings.append({"ltcode": code, "section": sec,
                                 "field": "series",
                                 "detail": "published row has no rows in raw.json"})
                continue

            if r.get("flow_rate_mode"):
                skipped.append({"ltcode": code, "section": sec,
                                "why": "flow rate, not a year-over-year rate"})
                continue

            L = max(hist)

            def at(k, _h=hist, _L=L):
                return _h.get(_L - k)

            latest_v, latest_pct = at(0)
            n_win = len(r.get("value_series") or []) or 13
            pub_latest = r.get("latest_value")
            rows += 1
            scale = 1.0
            if pub_latest not in (None, 0) and latest_v not in (None, 0):
                ratio = pub_latest / latest_v
                for cand in (1.0, 10.0, 100.0, 1000.0, 0.1, 0.01, 0.001):
                    if abs(ratio - cand) <= 0.01 * cand:
                        scale = cand
                        break
            if scale != 1.0 and latest_pct:
                findings.append({
                    "ltcode": code, "section": sec, "field": "latest_value",
                    "published": pub_latest, "re_derived": latest_v,
                    "note": ("the source quotes this line with a percent sign, so "
                             "it is already in points; the published level is %g "
                             "times it" % scale)})
                scale = 1.0
            elif scale != 1.0:
                conventions.append({"ltcode": code, "factor": scale})
            elif pub_latest is not None and not _close(latest_v, pub_latest, tol_rel):
                findings.append({"ltcode": code, "section": sec, "field": "latest_value",
                                 "published": pub_latest, "re_derived": latest_v})
            hist = {q: (v * scale, pc) for q, (v, pc) in hist.items()}
            latest_v, latest_pct = at(0, hist)
            all_neg = all(v < 0 for v, _ in hist.values())

            def val(k, _h=hist, _L=L):
                x = _h.get(_L - k)
                return None if x is None else x[0]

            if r.get("kind") == "pre_growth":
                conventions.append({"ltcode": code, "convention": "value is a growth rate"})
                rows += 1
                _cmp(latest_v, r.get("latest_growth"), 0.02, 0.06, findings, conventions,
                     {"ltcode": code, "section": sec, "field": "latest_growth"})
                if val(1) is not None:
                    rows += 2
                    _cmp(val(1), r.get("prev_growth"), 0.02, 0.06, findings,
                         conventions, {"ltcode": code, "section": sec, "field": "prev_growth"})
                    _cmp(latest_v - val(1), r.get("latest_accel"), 0.02, 0.06,
                         findings, conventions,
                         {"ltcode": code, "section": sec, "field": "latest_accel"})
                continue
            if val(4) is not None:
                rows += 1
                _cmp(gt_growth(latest_v, val(4), latest_pct, all_neg),
                     r.get("latest_growth"), 0.02, 0.6, findings, conventions,
                     {"ltcode": code, "section": sec, "field": "latest_growth"})
            elif r.get("latest_growth") is not None:
                rows += 1
                findings.append({"ltcode": code, "section": sec, "field": "latest_growth",
                                 "published": r.get("latest_growth"), "re_derived": None,
                                 "detail": "the year-ago quarter is not in raw.json, so the "
                                           "published y/y was measured against a different "
                                           "quarter"})
            if val(1) is not None and val(5) is not None:
                p1, p2 = val(1), val(5)
                rows += 1
                pg = gt_growth(p1, p2, latest_pct, all_neg)
                _cmp(pg, r.get("prev_growth"), 0.02, 0.6, findings, conventions,
                     {"ltcode": code, "section": sec, "field": "prev_growth"})
                g = gt_growth(latest_v, val(4), latest_pct, all_neg) if val(4) is not None else None
                if (g is not None and pg is not None
                        and not isinstance(g, tuple) and not isinstance(pg, tuple)):
                    rows += 1
                    _cmp(g - pg, r.get("latest_accel"), 0.02, 0.6, findings,
                         conventions,
                         {"ltcode": code, "section": sec, "field": "latest_accel"})
            vals = [val(k) for k in range(n_win - 1, -1, -1)]
            gs = [None] * len(vals)
            for t in range(4, len(vals)):
                if vals[t] is None or vals[t - 4] is None:
                    continue
                gg = gt_growth(vals[t], vals[t - 4], latest_pct, all_neg)
                gs[t] = None if isinstance(gg, tuple) else gg
            acc = [None] * len(vals)
            for t in range(1, len(vals)):
                if gs[t] is not None and gs[t - 1] is not None:
                    acc[t] = gs[t] - gs[t - 1]
            past = [a for a in acc[:-1] if a is not None]
            clean = (len(vals) == len(r.get("value_series") or vals)
                     and not any(vals[t] is not None and vals[t - 4] is not None
                                 and isinstance(gt_growth(vals[t], vals[t - 4], latest_pct, all_neg), tuple)
                                 for t in range(4, len(vals))))
            if clean and acc[-1] is not None and len(past) >= 4 and r.get("z") is not None:
                mu = sum(past) / len(past)
                sd = (sum((a - mu) ** 2 for a in past) / len(past)) ** 0.5
                if sd > 0:
                    rows += 1
                    zz = (acc[-1] - mu) / sd
                    if abs(zz - r["z"]) > max(0.05, 0.02 * abs(r["z"])):
                        findings.append({"ltcode": code, "section": sec, "field": "z",
                                         "published": r["z"], "re_derived": zz})
                seq = [a for a in acc if a is not None]
                if seq and seq[-1] != 0 and r.get("streak") is not None:
                    sg = 1 if seq[-1] > 0 else -1
                    c = 0
                    for a in reversed(seq):
                        if (a > 0) == (sg > 0) and a != 0:
                            c += 1
                        else:
                            break
                    rows += 1
                    if c != r["streak"]:
                        findings.append({"ltcode": code, "section": sec, "field": "streak",
                                         "published": r["streak"], "re_derived": c})
            fb = r.get("forward_bar") or {}
            lth = fb.get("level_to_hold_growth")
            if (clean and lth is not None and len(vals) >= 4 and gs[-1] is not None
                    and not all_neg and vals[-4] is not None):
                base = vals[-4]
                want = (base + gs[-1] / 100.0) if latest_pct else base * (1.0 + gs[-1] / 100.0)
                rows += 1
                if not _close(want, lth, 0.005, 1e-6) and not _close(abs(want), abs(lth), 0.005, 1e-6):
                    findings.append({"ltcode": code, "section": sec, "field": "level_to_hold",
                                     "published": lth, "re_derived": want})
    return rows, findings, skipped, conventions


def _cmp(re_derived, published, rel, floor, findings, conventions, meta):
    if isinstance(re_derived, tuple):
        conventions.append(dict(meta, convention="sign change, magnitude only"))
        re_derived = re_derived[1]
        published = abs(published) if published is not None else None
    if published is None or re_derived is None:
        return
    if not _close(re_derived, published, rel, floor):
        findings.append(dict(meta, published=published,
                             re_derived=round(re_derived, 4)))


def _close(a, b, rel, floor=0.0):
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return False
    d = abs(a - b)
    return d <= floor or d <= rel * max(abs(a), abs(b), 1e-9)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--computed", required=True)
    ap.add_argument("--tolerance", type=float, default=0.005)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    with open(a.raw, encoding="utf-8") as fh:
        raw = json.load(fh)
    with open(a.computed, encoding="utf-8") as fh:
        payload = json.load(fh)

    rows, findings, skipped, conventions = check(raw, payload, a.tolerance)
    _ser = gt_series(raw)
    for _sec in ("headline_moves", "accelerating", "decelerating", "deep_dive", "primary_context"):
        for _r in payload.get(_sec) or []:
            _t = _r.get("adjusted_twin") or {}
            if _t.get("basis") != "adjusted_revenue":
                continue
            rows += 1
            try:
                _n, _d = _ser[_t["numerator_ltcode"]], _ser[_t["denominator_ltcode"]]
                _q, _y = gt_label(_t["period"]), gt_label(_t["year_ago_period"])
                _g = (_n[_q][0] / _d[_q][0] - _n[_y][0] / _d[_y][0]) * 1e4
                if abs(_g - _t["latest_growth"]) > 1.0:
                    findings.append({"ltcode": _t["ltcode"], "section": _sec, "field": "adjusted_twin",
                                     "published": _t["latest_growth"], "re_derived": round(_g, 1)})
            except Exception as _e:
                findings.append({"ltcode": _t.get("ltcode"), "section": _sec, "field": "adjusted_twin",
                                 "detail": "could not re-derive: %s" % _e})
    out = {"ticker": payload.get("ticker"),
           "figures_re_derived": rows,
           "disagreements": len(findings),
           "rows_not_comparable": skipped,
           "scale_conventions": conventions,
           "faithfulness": round(1.0 - (len(findings) / rows), 6) if rows else None,
           "findings": findings[:20],
           "passed": not findings}
    if a.json:
        print(json.dumps(out, indent=2))
    else:
        if findings:
            print("FAIL  %d of %d published figures do not re-derive from raw.json"
                  % (len(findings), rows))
            for f in findings[:12]:
                print("  %-14s %-16s published %s, re-derived %s"
                      % (f.get("ltcode"), f.get("field"),
                         f.get("published"), f.get("re_derived", f.get("detail"))))
        else:
            print("PASS  %d published figures re-derive from raw.json "
                  "by an independent implementation" % rows)
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _contract import run as _run_contract
    _run_contract(main, 'ground_truth')
