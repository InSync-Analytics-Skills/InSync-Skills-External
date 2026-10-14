#!/usr/bin/env python3
"""
copy_check.py - v11. Catch a number that was copied wrong BEFORE any maths runs.

WHY. In every sandbox this skill runs in, the model reads an MCP response and writes it into
input.json by hand. That copy is the one step code cannot watch. The v10 gates catch a copy
error only when it looks odd (a lost "B", a flipped sign). A wrong digit that still looks
sensible - $5.1234B typed as $5.1324B - sailed through.

HOW. The MCP payload carries its own internal cross-checks. A consensus mean always sits inside
its own [MIN, MAX]. The same (ltcode, period) never has two different values. EBITDA is never
below EBIT on the same basis. Revenue is never below the profit lines under it. So a copy slip
almost always breaks one of these, and the check names the exact row to re-copy.

These checks only READ the payload. They never change a value, so a clean payload renders the
same page as it did in v10.

BLOCK (exit 7 from run_pipeline, re-copy the named rows):
  [copy check] estimate outside its own range   - VALUE not inside [MIN, MAX]
  [copy check] range inverted                   - MIN > MAX
  [copy check] duplicate row                    - one (ltcode, period[, date]) with two values
  [copy check] broker count not a whole number  - BROKERCOUNT typed wrong
WARN (read it, re-copy if it is a slip; a real business can do this):
  [copy check] profit above revenue / EBIT above EBITDA
  [copy check] big jump vs prior year           - a level more than 4x or under 1/4 of last year

Usage:  python3 copy_check.py input.json      (exit 0 clean, 7 block, prints JSON)
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_math

_ORDER_PAIRS = [
    ("IS_P_113", "IS_P_109", "EBITDA", "EBIT"),
    ("IS_P_114", "IS_P_110", "EBITDA (adj)", "EBIT (adj)"),
]
_REVENUE = "IS_P_101"
_UNDER_REVENUE = ("IS_P_109", "IS_P_110", "IS_P_113", "IS_P_114", "IS_P_117", "IS_P_118", "IS_P_124")
_TOL = 1e-6


def _rows(resp):
    r = run_math._rows(run_math._unwrap_mcp_payload(resp)) if resp else []
    return [x for x in r if isinstance(x, dict)]


def _val(row, key):
    return run_math.parse_mcp_value(row.get(key))


def _label(row):
    return "%s %s (%s)" % (row.get("LTCODE"), row.get("FISCALPERIOD"), row.get("LINEITEM") or "")


def check(inp):
    blocks, warns = [], []
    mcp = run_math.expand_short_keys(inp.get("mcp_responses") or {})

    for key in ("get_estimates", "get_estimates_guidance"):
        for row in _rows(mcp.get(key)):
            v, lo, hi = _val(row, "VALUE"), _val(row, "MIN"), _val(row, "MAX")
            where = "%s %s" % (key, _label(row))
            if row.get("UPDATEDATE"):
                where += " as of %s" % row.get("UPDATEDATE")
            if lo is not None and hi is not None and lo > hi + _TOL * max(1.0, abs(hi)):
                blocks.append("[copy check] range inverted: %s has MIN %s above MAX %s. Re-copy this row."
                              % (where, row.get("MIN"), row.get("MAX")))
            elif v is not None and lo is not None and hi is not None:
                slack = _TOL * max(1.0, abs(v))
                if v < lo - slack or v > hi + slack:
                    blocks.append("[copy check] estimate outside its own range: %s has VALUE %s outside "
                                  "[%s, %s]. A consensus mean always sits inside its range, so one of the "
                                  "three was copied wrong. Re-copy this row from the MCP response."
                                  % (where, row.get("VALUE"), row.get("MIN"), row.get("MAX")))
            bc = row.get("BROKERCOUNT")
            if bc is not None and bc != "":
                try:
                    f = float(bc)
                    if f < 0 or f != int(f):
                        raise ValueError
                except (TypeError, ValueError):
                    blocks.append("[copy check] broker count not a whole number: %s has BROKERCOUNT %r. "
                                  "Re-copy it." % (where, bc))

    def _dups(key, keyfn):
        seen = {}
        for row in _rows(mcp.get(key)):
            k = keyfn(row)
            vl = str(row.get("VALUE")).strip()
            if k in seen and seen[k] != vl:
                blocks.append("[copy check] duplicate row: %s carries %s twice, as %s and %s. The MCP "
                              "returns one value per key. Re-copy it." % (key, " ".join(map(str, k)), seen[k], vl))
            seen.setdefault(k, vl)
    _dups("get_actuals", lambda r: (r.get("LTCODE"), r.get("FISCALPERIOD")))
    _dups("get_estimates", lambda r: (r.get("LTCODE"), r.get("FISCALPERIOD"), r.get("UPDATEDATE")))
    _dups("get_estimates_guidance", lambda r: (r.get("LTCODE"), r.get("FISCALPERIOD"), r.get("UPDATEDATE")))

    def _by_period(key):
        out = {}
        for row in _rows(mcp.get(key)):
            v = _val(row, "VALUE")
            if v is not None:
                out.setdefault(str(row.get("FISCALPERIOD")), {})[row.get("LTCODE")] = (v, row.get("VALUE"))
        return out
    for key in ("get_actuals", "get_estimates"):
        for fp, codes in _by_period(key).items():
            for hi_lt, lo_lt, hi_n, lo_n in _ORDER_PAIRS:
                if hi_lt in codes and lo_lt in codes and codes[hi_lt][0] >= 0 and codes[lo_lt][0] > codes[hi_lt][0] * (1 + 1e-9):
                    warns.append("[copy check] %s above %s: %s %s shows %s %s against %s %s. EBITDA adds "
                                 "D&A back to EBIT, so it is normally the larger. Re-check both rows."
                                 % (lo_n, hi_n, key, fp, lo_n, codes[lo_lt][1], hi_n, codes[hi_lt][1]))
            if _REVENUE in codes and codes[_REVENUE][0] > 0:
                for lt in _UNDER_REVENUE:
                    if lt in codes and codes[lt][0] > codes[_REVENUE][0] * (1 + 1e-9):
                        warns.append("[copy check] profit above revenue: %s %s shows %s %s against Total "
                                     "Revenue %s. Re-check both rows." % (key, fp, lt, codes[lt][1], codes[_REVENUE][1]))

    per, prior = str(inp.get("period") or ""), str(inp.get("prior_period") or "")
    act = _by_period("get_actuals")
    cur, pri = act.get(per) or {}, act.get(prior) or {}
    for lt, (cv, craw) in cur.items():
        if lt not in pri or "%" in str(craw):
            continue
        pv, praw = pri[lt]
        if cv <= 0 or pv <= 0:
            continue
        r = cv / pv
        if r > 4 or r < 0.25:
            warns.append("[copy check] big jump vs prior year: %s is %s in %s against %s in %s (%.1fx). "
                         "If that is a copy slip, re-copy both rows; if the business really moved that "
                         "much, carry on." % (lt, craw, per, praw, prior, r))
    return blocks, warns


def main(argv):
    if len(argv) != 1:
        print(__doc__)
        return 2
    with open(argv[0], encoding="utf-8") as f:
        inp = json.load(f)
    blocks, warns = check(inp)
    print(json.dumps({"status": "block" if blocks else "ok", "blocks": blocks, "warnings": warns}, indent=2))
    return 7 if blocks else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
