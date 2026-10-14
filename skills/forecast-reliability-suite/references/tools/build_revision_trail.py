#!/usr/bin/env python3
"""Deterministic revision-trail picker for the Forecast Reliability Suite (Step 6).

Picks the forward-quarter consensus snapshot for each Revision Momentum column so the
choice is made by code, the same way on every run, never by eye.

RULES:
  full panel    = BROKERCOUNT >= FULL_MIN (6, i.e. "more than 5 analysts")
  Pre Earnings  last FULL snapshot in [print-10d, print-1d]; else the last snapshot strictly
                before the print with NO broker minimum.
  Current       the LATEST snapshot on or before --today (per metric). Always shown.
A snapshot stamped ON a print date is never used (it may already reflect the actual).
Each metric is picked independently under the same rule; every fallback is reported.

UNITS (7.12.0; required from 7.12.1). Each value is put on the SAME scale as that metric's history
in input.json, so the trail and the metric's other cards agree. Every KPI takes its unit exactly as
`metrics[].unit` writes it ("$B", "$M", "$", "%", "c", "M members", "stores"...):
--kpi key=LTCODE:UNIT. Revenue takes --rev-unit (the input's `rev_unit`); EPS is per share. --eps,
--rev and --rev-unit are required: the script never guesses a code or a scale (a $M KPI forced
into billions would come out 1,000x too small against its own history).

Usage:
  python3 build_revision_trail.py snapshots.json --last-print 2026-09-10 \
      --last-q 1Q27 --next-q 2Q27 --today 2026-09-25 [--eps IS_P_128 --rev IS_P_101 --rev-unit B] \
      [--kpi cloud=SD_P_1007:$B --kpi nim=RT_S_12:% ...] > trail.json
  (--next-print is still accepted from older calls and ignored.)

snapshots.json: a list of raw get_estimates rows for the NEXT quarter (any wrapping is fine:
the script walks the JSON and keeps every row that has LTCODE, UPDATEDATE, VALUE, BROKERCOUNT).
Output: {"revision_trail":[...], "notes":[...]} ready to paste into input.json.
"""
import argparse, datetime as dt, json, re, sys

FULL_MIN = 6
WIN = 10
NOTES = []

_SUF = {"T": 1e12, "B": 1e9, "M": 1e6, "K": 1e3, "k": 1e3}


def _read(v):
    if isinstance(v, (int, float)):
        return float(v), None, False
    s = str(v).strip().replace(",", "")
    if s.startswith("USCents"):
        return float(s[7:]), "money", False
    if s.endswith("%"):
        return float(s[:-1]), "pct", False
    cur = ""
    while s and not (s[0].isdigit() or s[0] in "-+."):
        cur, s = cur + s[0], s[1:]
    suf = s[-1:] if s[-1:] in _SUF else ""
    num = float(s[:-1] if suf else s) * (_SUF[suf] if suf else 1.0)
    return num, ("money" if cur.strip() else ("count" if suf else None)), bool(suf)


def _target(unit):
    if unit is None:
        return None
    u = str(unit).strip()
    if u == "%":
        return 1.0, "pct"
    if u == "c":
        return 0.01, "money"
    m = re.match(r"^([$€£¥]|[A-Z]{3})?\s*([TBMKk])?(?![A-Za-z])", u)
    sym, sc = (m.group(1), m.group(2)) if m else (None, None)
    div = _SUF.get(sc, 1.0) if sc else 1.0
    return div, ("money" if sym else "count")


def _num(v, code="", unit=None):
    amt, kind, has_suf = _read(v)
    raw = str(v).strip()
    t = _target(unit)
    if t is None:
        if isinstance(v, (int, float)) or kind in (None, "pct"):
            return amt
        if kind == "money":
            if str(v).strip().startswith("USCents"):
                NOTES.append(f"{code}: {raw} converted to cents ({amt * 100:g}); pass :c as its unit.")
                return amt * 100
            return amt / 1e9 if has_suf else amt
        NOTES.append(f"{code}: {raw} read as {amt:,.0f} (a count); pass its unit (e.g. :M members) "
                     "so it matches the metric's history.")
        return amt
    div, tkind = t
    if isinstance(v, (int, float)) or kind is None:
        return amt
    if kind == "pct" or tkind == "pct":
        if kind != tkind:
            NOTES.append(f"{code}: {raw} does not look like its unit {unit!r}; check it.")
        return amt
    if kind != tkind:
        NOTES.append(f"{code}: {raw} looks like {kind} but its unit is {unit!r}; check the scale against the metric's history.")
    return amt / div


def _rows(obj, out):
    if isinstance(obj, dict):
        if all(k in obj for k in ("LTCODE", "UPDATEDATE", "VALUE")):
            out.append(obj)
        for v in obj.values():
            _rows(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _rows(v, out)
    return out


def _d(s):
    return dt.date.fromisoformat(str(s)[:10])


def pick_pre(snaps, prn):
    before = [s for s in snaps if s["d"] < prn]
    full = [s for s in before if s["d"] >= prn - dt.timedelta(WIN) and s["b"] >= FULL_MIN]
    if full: return max(full, key=lambda s: s["d"]), False
    return (max(before, key=lambda s: s["d"]), True) if before else (None, None)


def pick_current(snaps, today):
    upto = [s for s in snaps if s["d"] <= today]
    return (max(upto, key=lambda s: s["d"]), False) if upto else (None, None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("snapshots")
    ap.add_argument("--last-print", required=True)
    ap.add_argument("--next-print", help="accepted from older calls; not used")
    ap.add_argument("--last-q", required=True); ap.add_argument("--next-q", required=True)
    ap.add_argument("--today", required=True)
    ap.add_argument("--eps", required=True, help="the EPS LTCODE used in metrics[] (IS_P_128 or IS_P_127)")
    ap.add_argument("--rev", required=True, help="the revenue LTCODE used in metrics[] (IS_P_102 or IS_P_101)")
    ap.add_argument("--rev-unit", required=True, choices=("B", "M"), help="the input's rev_unit")
    ap.add_argument("--kpi", action="append", default=[],
                    help="key=LTCODE:UNIT, the unit as in metrics[].unit ($B, $M, $, %%, M members...); repeatable")
    a = ap.parse_args()
    for kv in a.kpi:
        if "=" not in kv or ":" not in kv.split("=", 1)[1] or not kv.split("=", 1)[1].partition(":")[2].strip():
            ap.error("--kpi %r has no unit. Write key=LTCODE:UNIT with the unit exactly as in metrics[].unit "
                     "(a %% KPI takes :%%), so the trail is on the same scale as the metric's history." % kv)

    tp, today = _d(a.last_print), _d(a.today)
    raw = _rows(json.load(open(a.snapshots)), [])
    NOTES.append(f"codes used: EPS {a.eps}, revenue {a.rev} (rev_unit {a.rev_unit}).")
    codes = {"eps": a.eps, "rev": a.rev}
    units = {"eps": "$", "rev": "$" + a.rev_unit}
    for kv in a.kpi:
        k, c = kv.split("=", 1)
        c, _, u = c.partition(":")
        codes[k] = c
        units[k] = u.strip()
    unit_of = {c: units.get(k) for k, c in codes.items()}
    by = {}
    for r in raw:
        if r.get("BROKERCOUNT") is None: continue
        code = r["LTCODE"]
        s = {"d": _d(r["UPDATEDATE"]), "v": _num(r["VALUE"], code, unit_of.get(code)), "b": int(r["BROKERCOUNT"])}
        if s["d"] > today: continue
        by.setdefault(code, {})[s["d"]] = s
    snaps = {k: sorted(by.get(c, {}).values(), key=lambda s: s["d"]) for k, c in codes.items()}

    cols = [("Pre Earnings", lambda S: pick_pre(S, tp)),
            ("Current", lambda S: pick_current(S, today))]
    trail, notes = [], []
    for label, fn in cols:
        picks = {k: fn(snaps[k]) for k in codes}
        e, efb = picks["eps"]
        if e is None:
            notes.append(f"{label}: no EPS snapshot available; column omitted."); continue
        col = {"label": label, "date": e["d"].strftime("%d-%b-%y"), "eps": e["v"],
               "rev": (picks["rev"][0] or {}).get("v"), "kpis": {}}
        for k in codes:
            if k in ("eps", "rev"): continue
            if picks[k][0] is not None: col["kpis"][k] = picks[k][0]["v"]
        for k, (s, fb) in picks.items():
            if s is None:
                notes.append(f"{label} {k}: no snapshot available.")
            elif fb:
                notes.append(f"{label} {k}: no full panel (>= {FULL_MIN}) in window; used {s['d']} with {s['b']} analyst(s).")
            elif s["d"] != e["d"]:
                notes.append(f"{label} {k}: full panel on {s['d']} ({s['b']} analysts), not the EPS date {e['d']}.")
        trail.append(col)
    json.dump({"revision_trail": trail, "notes": sorted(set(NOTES)) + notes}, sys.stdout, indent=1)


if __name__ == "__main__":
    main()
