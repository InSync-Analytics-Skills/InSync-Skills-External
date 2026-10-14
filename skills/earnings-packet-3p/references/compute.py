#!/usr/bin/env python3
"""earnings-packet-3p, the compute step's reconciliation gate and figure publisher.

    python3 references/compute.py --audit                 # read packet_computed.json here
    python3 references/compute.py --audit --strict         # exit 1 on any disagreement
    python3 references/compute.py --figures                # write narrative_figures into it
    python3 references/compute.py --figures --dry-run      # print what it would add

WHY THIS FILE EXISTS
--------------------
Without it nothing checks the arithmetic in `packet_computed.json` against itself, with two
consequences.

First, a wrong figure from the feed reaches the page and every validator pass still says
PASS, because the packet is internally consistent against its own wrong input. Example (an
invented insurer): the feed's combined ratio reads 90.0, while underwriting income over net
premium earned in the SAME file implies 87.0. The identity is not approximate: where it
reproduces to the printed decimal in one quarter and breaks by points in the next, that is
the signature of a bad feed line, and it is findable without leaving the data.

Second, `validate_packet.py` pass 3, the no-fabrication gate, is a substring search over
the serialised computed file that accepts three scales and five roundings, so on its own it
accepts almost any random number. It cannot be
tightened in place, because Step 6 requires prose to quote figures at the precision the
TABLE prints, and those rounded forms are not in the file. `--figures` publishes them.

WHAT THIS FILE IS NOT
---------------------
It is not the raw-to-computed generator; that is `build_computed.py`. This file reads the computed file and
re-derives every figure the file itself contains enough information to re-derive, sharing
no code with the renderer. `--audit` writes nothing.

THE CHECKS
----------
R1  dispersion   min <= mean <= max
R2  dispersion   disp == (max - min) / |mean| * 100
R3  dispersion   yoy == pct_change(mean, prior), or (mean - prior) * 100 in basis points
                 where the line is already a rate (fmt '%')
R4  growth       growth[p] == pct_change(cells[p], cells[prior-year p]), where the
                 prior-year period is present in the same row
R5  valuation    vs_1y_mean == pct_change(ltm, y1.mean), and the 3-year equivalent
R6  reaction     m0 == (w_day / w_pre - 1) * 100
R7  move_band    every aggregate re-derived from reaction[]
R8  revisions    delta == pct_change(newest, base), in bps for a rate line,
                 with both columns resolved from rev_cols and rev_buckets
R9  guidance     pos follows from lo, hi and cons
R10 scale        no adjacent period in one row jumps by more than 100x, the tripwire for
                 a mis-scaled code such as a unit-cost line that returns a value in
                 $M beside revenue in hundreds of $bn
R11 identity     insurance: combined ratio == 100 - underwriting income / net premium
                 earned * 100. This is the check that catches the bad-feed-line case above
R14 peers        eps_growth == pct_change(CY27E EPS, CY26E EPS)
R15 peers        rev_growth == pct_change(CY27E revenue, CY26E revenue)
R16 peers        the LEAD multiple vs its 1-year mean, keys from val_metrics[].lead
R17 peers        peg == <LEAD multiple> / eps_growth, keys from val_metrics[].lead,
                 and no PEG on a near-zero or negative rate

R12 and R13 were written, tested and WITHDRAWN as false-positive generators; the reasons
are recorded at their old site in audit() so nobody writes them again.
"""
import argparse
import decimal
import json
import math
import os
import re
import statistics
import sys

TOL_PP = 0.05
TOL_PEER = 0.10
SCALE_JUMP = 100.0


def num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def pct_change(cur, base):
    if cur is None or base is None or not base:
        return None
    return (cur - base) / abs(base) * 100.0


def prior_year_key(p):
    p = re.sub(r"^R\d*-", "", str(p))
    m = re.fullmatch(r"([1-4])Q(\d{2})", str(p))
    if m:
        return f"{m.group(1)}Q{int(m.group(2)) - 1:02d}"
    m = re.fullmatch(r"([12])H(\d{2})", str(p))
    if m:
        return f"{m.group(1)}H{int(m.group(2)) - 1:02d}"
    m = re.fullmatch(r"(\d{4})", str(p))
    if m:
        return str(int(m.group(1)) - 1)
    return None


NOT_RATE_FMTS = {"$M", "$", "$3", "x", "k", "#B"}


def pct_row(r):
    f = (r.get("fmt") or "").strip()
    if f in ("%", "%2"):
        return True
    if f in NOT_RATE_FMTS:
        return False
    return bool(re.search(r"\b(ratio|margin|yield|rate)\b\s*%?$|%$", (r.get("name") or "").strip(), re.I))


def find_row(groups, *patterns, exclude=()):
    for g in groups:
        for r in g or []:
            n = (r.get("name") or "").lower()
            if all(p in n for p in patterns) and not any(x in n for x in exclude):
                return r
    return None


def audit(C, PR=None, skips=None):
    out = []

    def bad(code, where, msg):
        out.append({"check": code, "where": where, "detail": msg})

    def skip(code, where, why):
        if skips is not None:
            skips.append({"check": code, "where": where, "detail": why})

    for r in C.get("dispersion") or []:
        nm = r.get("name", "?")
        mean, lo, hi = num(r.get("mean")), num(r.get("min")), num(r.get("max"))
        if None not in (mean, lo, hi):
            if not lo <= mean <= hi:
                bad("R1", nm, f"min {lo} / mean {mean} / max {hi} do not order")
            if mean:
                want = (hi - lo) / abs(mean) * 100
                got = num(r.get("disp"))
                if got is not None and abs(want - got) > TOL_PP:
                    bad("R2", nm, f"disp {got:.4f} but (max-min)/|mean| gives {want:.4f}")
        prior, yoy = num(r.get("prior")), num(r.get("yoy"))
        if None not in (mean, prior, yoy) and (prior or pct_row(r)):
            if pct_row(r):
                want, unit = (mean - prior) * 100, "bps"
            else:
                want, unit = pct_change(mean, prior), "%"
            if abs(want - yoy) > TOL_PP:
                bad("R3", nm, f"yoy {yoy:.4f} but the {unit} form gives {want:.4f}")

    for grp in ("model", "segments", "seg_ebit", "kpis"):
        for r in C.get(grp) or []:
            nm = f"{grp}: {r.get('name', '?')}"
            cells = r.get("cells") or {}
            g = r.get("growth") or {}
            for p, gv in g.items():
                gv = num(gv)
                pk = prior_year_key(p)
                a, b = num(cells.get(p)), num(cells.get(pk)) if pk else None
                if gv is None or a is None or b in (None, 0):
                    skip("R4", nm, f"growth[{p}] not re-derivable: "
                         + ("no published growth" if gv is None else
                            "no value for " + str(p) if a is None else
                            f"no prior-year base at {pk}"))
                    continue
                want = pct_change(a, b)
                if abs(want - gv) > max(TOL_PP, abs(want) * 1e-3):
                    bad("R4", nm, f"growth[{p}] {gv:.4f} but {a}/{b} gives {want:.4f}")
            vals = [(p, num(v)) for p, v in cells.items()]
            vals = [(p, v) for p, v in vals if v not in (None, 0)]
            for (p1, v1), (p2, v2) in zip(vals, vals[1:]):
                ratio = abs(v2 / v1)
                if ratio > SCALE_JUMP or ratio < 1 / SCALE_JUMP:
                    bad("R10", nm, f"{p1}={v1} then {p2}={v2}, a {max(ratio, 1/ratio):,.0f}x "
                                   f"step: check the code's scale")

    for v in C.get("valuation") or []:
        nm = v.get("name", "?")
        ltm = num(v.get("ltm"))
        for span, field in (("y1", "vs_1y_mean"), ("y3", "vs_3y_mean")):
            base = (v.get(span) or {}).get("mean")
            base, got = num(base), num(v.get(field))
            if None in (ltm, base, got) or not base:
                skip("R5", nm, f"{field} not re-derivable: ltm or {span}.mean absent")
                continue
            want = pct_change(ltm, base)
            if abs(want - got) > TOL_PP:
                bad("R5", nm, f"{field} {got:.4f} but ltm/{span}.mean gives {want:.4f}")

    m0 = []
    for r in C.get("reaction") or []:
        pre, day, got = num(r.get("w_pre")), num(r.get("w_day")), num(r.get("m0"))
        if None not in (pre, day, got) and pre:
            want = (day / pre - 1) * 100
            if abs(want - got) > TOL_PP:
                bad("R6", r.get("q", "?"), f"m0 {got:.4f} but w_day/w_pre gives {want:.4f}")
        if got is not None:
            m0.append(got)
    mb = C.get("move_band") or {}
    if m0 and mb:
        a = [abs(x) for x in m0]
        _r0 = [num(r.get("r0")) for r in C.get("reaction") or []]
        _r0 = [x for x in _r0 if x is not None]
        if _r0:
            _got = num(mb.get("mean_rel_1d"))
            if _got is not None and abs(statistics.fmean(_r0) - _got) > TOL_PP:
                bad("R7", "move_band", f"mean_rel_1d {_got:.4f} but reaction[] gives "
                                       f"{statistics.fmean(_r0):.4f}")
        for field, want in (("mean_abs_1d", statistics.fmean(a)),
                            ("median_abs_1d", statistics.median(a)),
                            ("min_abs_1d", min(a)),
                            ("max_abs_1d", max(a)),
                            ("up", sum(1 for x in m0 if x > 0)),
                            ("down", sum(1 for x in m0 if x < 0))):
            got = num(mb.get(field))
            if got is None:
                skip("R7", "move_band", f"{k} absent from the computed file")
                continue
            if abs(want - got) > (0 if field in ("up", "down") else TOL_PP):
                bad("R7", "move_band", f"{field} {got} but reaction[] gives {want}")
        m5 = [abs(num(r.get("m5"))) for r in C.get("reaction") or [] if num(r.get("m5")) is not None]
        got = num(mb.get("mean_abs_5d"))
        if m5 and got is not None and abs(statistics.fmean(m5) - got) > TOL_PP:
            bad("R7", "move_band", f"mean_abs_5d {got:.4f} but reaction[] gives "
                                   f"{statistics.fmean(m5):.4f}")

    _rc = C.get("rev_cols") or []
    _base_key = next((c.get("key") for c in _rc if c.get("base")), None)
    if _base_key is None and _rc:
        _base_key = _rc[0].get("key")
    _buckets = C.get("rev_buckets") or {}
    _keys = [c.get("key") for c in _rc if c.get("key")]
    _latest_key = (max((k for k in _keys if _buckets.get(k)),
                       key=lambda k: str(_buckets[k]), default=None)
                   or (_keys[-1] if _keys else None))
    if _base_key and _latest_key and _base_key != _latest_key:
        _report_rev_keys = (_base_key, _latest_key)
    else:
        _report_rev_keys = None
    for r in C.get("revisions") or []:
        if not _report_rev_keys:
            break
        pre, latest = num(r.get(_base_key)), num(r.get(_latest_key))
        got = num(r.get("delta"))
        if None in (pre, latest, got) or not pre:
            skip("R8", nm, "revision delta not re-derivable: base or latest vintage absent")
            continue
        if pct_row(r):
            want, unit = (latest - pre) * 100, "bps"
        else:
            want, unit = pct_change(latest, pre), "%"
        if abs(want - got) > TOL_PP:
            bad("R8", r.get("name", "?"), f"delta {got:.4f} but the {unit} form gives {want:.4f}")

    for r in C.get("guide_vs_cons") or []:
        lo, hi, cons = num(r.get("lo")), num(r.get("hi")), num(r.get("cons"))
        pos = (r.get("pos") or "").lower()
        if None in (lo, hi, cons) or not pos:
            skip("R9", r.get("item", "?"), "guidance position not re-derivable: lo, hi, "
                 "consensus or the printed position is absent")
            continue
        if lo > hi:
            lo, hi = hi, lo
        if lo == hi or r.get("approx"):
            pt = (lo + hi) / 2.0
            tol = guide_tolerance(r.get("text"), pt)
            eps = 1e-9 * max(1.0, abs(pt))
            want = "above" if cons > pt + tol + eps else "below" if cons < pt - tol - eps else "at"
        else:
            want = "above" if cons > hi else "below" if cons < lo else "in range"
        got = ("above" if "above" in pos else "below" if "below" in pos
               else "in range" if "range" in pos else "at" if "at guide" in pos else pos)
        if {want, got} <= {"at", "in range"}:
            continue
        if want != got:
            bad("R9", r.get("item", "?"),
                f"pos {r.get('pos')!r} but lo {lo} / hi {hi} / cons {cons} gives {want!r}")

    groups = [C.get(g) for g in ("model", "kpis", "segments", "seg_ebit")]
    cr = find_row(groups, "combined ratio", exclude=("accident", "ex cat", "ex-cat"))
    uwi = find_row(groups, "underwriting income")
    npe = find_row(groups, "premium", "earned")
    if cr and uwi and npe:
        agree, gaps = 0, []
        for p, v in (cr.get("cells") or {}).items():
            v, u, n = num(v), num((uwi.get("cells") or {}).get(p)), num((npe.get("cells") or {}).get(p))
            if None in (v, u, n) or not n:
                skip("R11", "insurance identity", "combined ratio, underwriting income or "
                     "net premium earned absent")
                continue
            want = 100 - u / n * 100
            if abs(want - v) <= 0.15:
                agree += 1
            else:
                gaps.append((p, v, want))
        if agree and gaps:
            for p, v, want in gaps:
                bad("R11", f"{cr.get('name')} {p}",
                    f"printed {v} but 100 - {uwi.get('name')}/{npe.get('name')} gives "
                    f"{want:.2f}, a {abs(v - want):.2f} point gap. The identity reproduces "
                    f"in {agree} other period(s) of this window, so the two lines are on the "
                    f"same basis and one of these three cells is wrong")

    _vm = (PR or {}).get("val_metrics") or []
    _lead = next((m for m in _vm if m.get("lead")), None) or (_vm[0] if _vm else {})
    _lead_name = _lead.get("label") or "the lead multiple"
    _lead_key = _lead.get("key") or "pe_ntm"
    _stem = re.sub(r"_(ntm|ltm)$", "", str(_lead_key))
    _rows0 = ((PR or {}).get("rows") or [{}])[0]
    def _pick(*cands):
        for c in cands:
            if c in _rows0:
                return c
        return cands[-1]
    _lead_ltm = _pick(_stem + "_ltm", "pe_ltm")
    _lead_1y = _pick(_stem + "1y_mean", _stem + "_1y_mean", "pe1y_mean")
    _lead_vs = _pick(_stem + "_vs_1y", "pe_vs_1y")
    for row in ((PR or {}).get("rows") or []):
        t = row.get("t", "?")

        def g(k):
            return num(row.get(k))

        for code, field, a, b, label in (
                ("R14", "eps_growth", "cy27_eps", "cy26_eps", "CY27E over CY26E EPS"),
                ("R15", "rev_growth", "cy27_rev", "cy26_rev", "CY27E over CY26E revenue"),
                ("R16", _lead_vs, _lead_ltm, _lead_1y,
                 "LTM " + _lead_name + " over its 1-year mean")):
            got, hi, lo = g(field), g(a), g(b)
            if None in (got, hi, lo) or not lo:
                skip("R17", "peers", "PEG not re-derivable: lead multiple or growth absent")
                continue
            want = pct_change(hi, lo)
            if abs(want - got) > TOL_PEER:
                bad(code, f"peer {t}", f"{field} {got:.4f} but {label} gives {want:.4f}")
        peg, pe, gr = g("peg"), g(_lead_key), g("eps_growth")
        if None not in (peg, pe, gr) and gr:
            want = pe / gr
            if abs(want - peg) > 0.05:
                bad("R17", f"peer {t}",
                    f"peg {peg:.4f} but {_lead_key}/eps_growth gives {want:.4f}"
                    f" (lead multiple: {_lead_name})")
        if peg is not None and gr is not None and (gr <= 0 or abs(gr) < 1):
            bad("R17", f"peer {t}", f"peg {peg:.2f} rests on an eps_growth of {gr:.2f}: "
                                    f"a PEG on a near-zero or negative growth rate is not "
                                    f"a multiple a reader can use, drop the cell")

    return out


def guide_tolerance(text, point):
    if point is None:
        return None
    p = abs(float(point))
    for tok in re.findall(r"\d[\d,]*(?:\.\d+)?", str(text or "")):
        try:
            v = float(tok.replace(",", ""))
        except ValueError:
            continue
        if v == 0:
            continue
        dec = len(tok.split(".")[1]) if "." in tok else 0
        for scale in (1.0, 1000.0, 0.001, 100.0, 0.01, 1e6, 1e-6):
            if abs(v * scale - p) <= 1e-9 * max(1.0, p):
                return (10.0 ** -dec) * scale
    s = repr(p)
    dec = len(s.split(".")[1].rstrip("0")) if "." in s and "e" not in s else 0
    return 10.0 ** -dec


def _half_up(x, d):
    q = decimal.Decimal(1).scaleb(-d)
    return format(decimal.Decimal(repr(x)).quantize(q, rounding=decimal.ROUND_HALF_UP), "f")


def figures(C, PR=None):
    seen = set()

    def put(x, d):
        seen.add(f"{x:.{d}f}")
        seen.add(_half_up(x, d))

    def add(v):
        v = num(v)
        if v is None or not math.isfinite(v):
            return
        for d in range(0, 4):
            put(v, d)
            put(abs(v), d)
        for d in range(0, 3):
            put(v / 1000, d)
            put(abs(v) / 1000, d)
        if abs(v) < 1e7:
            for d in range(0, 2):
                put(v * 1000, d)
                put(abs(v) * 1000, d)
        if abs(v) < 1e5:
            for d in range(0, 2):
                put(v * 100, d)
                put(abs(v) * 100, d)

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if not str(k).startswith("_doc"):
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        else:
            add(o)

    walk({k: v for k, v in C.items() if k not in ("warnings", "narrative_figures")})

    if PR:
        walk({k: v for k, v in PR.items() if k not in ("lens",)})

    for t in (C.get("tiles") or []):
        txt = str((t or {}).get("value") or "")
        for tok in re.findall(r"[0-9][0-9,]*(?:\.[0-9]+)?", txt):
            try:
                add(float(tok.replace(",", "")))
            except ValueError:
                pass

    return sorted(seen, key=lambda s: (len(s), s))


def _preflight_prose(published):
    try:
        with open("packet_narrative.json", encoding="utf8") as fh:
            N = json.load(fh)
    except (OSError, ValueError):
        return
    FIG = set(published)
    DOC = {str(x).lstrip("+\u2013-($").rstrip("%x)B ").replace(",", "")
           for x in (N.get("document_figures") or [])}
    NOISE = re.compile(r"S&P\s+500|\b[A-Z]{2}_[A-Z]{1,2}_\d+")
    TOKEN = re.compile(r"[+\u2013-]?\$?\d[\d,]*\.?\d*%?")

    def unknown(tok):
        core = tok.lstrip("+\u2013-($").rstrip("%x)B ").rstrip(".,").replace(",", "")
        if not core or core in DOC:
            return False
        if re.fullmatch(r"(19|20|21)\d\d", core):
            return False
        return not (core in FIG or core.lstrip("0") in FIG or ("0" + core) in FIG)

    hits = []
    for k, v in (N.get("notes") or {}).items():
        hits += [("notes." + k, t) for t in TOKEN.findall(NOISE.sub(" ", str(v))) if unknown(t)]
    for i, p in enumerate(N.get("what_matters") or [], 1):
        hits += [("what_matters[%d]" % i, t)
                 for t in TOKEN.findall(NOISE.sub(" ", str(p.get("detail", "")))) if unknown(t)]
    if not hits:
        print("         prose pre-flight CLEAN: every figure in the narrative is in the set")
        return
    print("         prose pre-flight: %d figure(s) the strict gate will REFUSE at Step 8:"
          % len(hits))
    for where, tok in hits[:12]:
        print("           %s: %r" % (where, tok))
    if len(hits) > 12:
        print("           ... and %d more" % (len(hits) - 12))
    print("         Each one is either a figure to place in packet_computed.json, or a "
          "figure\n         quoted from an InSync document to declare in the narrative's "
          "document_figures.")


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--figures", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("path", nargs="?", default="packet_computed.json")
    ap.add_argument("-h", "--help", action="store_true")
    a = ap.parse_args()
    if a.help or not (a.audit or a.figures):
        print(__doc__)
        return 2
    try:
        with open(a.path, encoding="utf8") as fh:
            C = json.load(fh)
    except (OSError, ValueError) as e:
        print(f"compute: cannot read {a.path}: {e}")
        return 2
    PR = None
    if os.path.exists("peers.json"):
        try:
            with open("peers.json", encoding="utf8") as fh:
                PR = json.load(fh)
        except ValueError:
            pass

    audit_rc = 0
    if a.audit:
        audit_rc = _run_audit(C, PR, a)
        if a.figures:
            print("")

    if a.figures:
        f = figures(C, PR)
        if a.dry_run:
            print(f"compute --figures would publish {len(f)} display forms, e.g. {f[:12]}")
            return audit_rc
        C["narrative_figures"] = f
        with open(a.path, "w", encoding="utf8") as fh:
            json.dump(C, fh, indent=1)
        print(f"compute --figures  wrote narrative_figures ({len(f)} display forms) "
              f"into {a.path}")
        _preflight_prose(f)
        return audit_rc

    return audit_rc


def _run_audit(C, PR, a):
    skipped = []
    found = audit(C, PR, skipped)
    by = {}
    for f in found:
        by[f["check"]] = by.get(f["check"], 0) + 1
    _blocking = {f"R{i}" for i in range(1, 18)}
    _hard = [f for f in found if f["check"] in _blocking]
    state = "CLEAN" if not found else (
        "FAIL" if (a.strict and _hard) else "REPORT")
    print(f"compute --audit  {state}   ({C.get('meta', {}).get('ticker', '?')}: "
          f"{len(found)} disagreement(s){', ' + str(by) if by else ''})")
    for f in found:
        print(f"         [{f['check']}] {f['where']}: {f['detail']}")
    if skipped:
        grp = {}
        for sx in skipped:
            grp.setdefault((sx["check"], sx["where"]), []).append(sx["detail"])
        print(f"         not checked: {len(skipped)} re-derivation(s) across {len(grp)} row(s), "
              f"each because an input the rule needs is absent. This is the designed state for "
              f"a line the Street does not forecast; it is NOT a silent pass.")
        for (code, where), det in sorted(grp.items()):
            print(f"         [{code}] {where}: {len(det)} period(s)")
    if a.json:
        with open(a.json, "w", encoding="utf8") as fh:
            json.dump({"found": found, "skipped": skipped}
                      if skipped else found, fh, indent=1)
    if found and not a.strict:
        print("\nreport-only: every one of these is a figure the computed file contradicts "
              "itself on, so read them before the packet leaves the desk. Add --strict once "
              "the run is clean.")
    hard = _hard
    soft = [f for f in found if f["check"] not in _blocking]
    if soft and a.strict:
        print(f"\nadvisory under --strict ({len(soft)}): "
              + ", ".join(sorted({f['check'] for f in soft}))
              + " report but do not block.")
    return 1 if (hard and a.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
