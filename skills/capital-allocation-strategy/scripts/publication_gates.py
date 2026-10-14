"""
publication_gates.py - post-render enforcement.  (v3 architecture, steps 7 and 9)

WHY A POST-RENDER CHECK, NOT JUST A COMPUTE-TIME ONE
----------------------------------------------------
The v2 QC suite validated identities (does OCF - capex equal FCF?) and reported "5/5 pass" on
reports that printed 1.64x and 1.73x for the same metric on the same page.  It could not see the
contradiction because it never looked at the RENDERED OUTPUT - it only checked the inputs it had
just computed.

These gates read the finished document text.  That makes them independent of how the number got
there: a renderer that bypasses the metric registry, a sector module that hard-codes a string, or
a narrative sentence quoting a stale figure are all caught the same way.

GATES IMPLEMENTED
  G3  metric consistency  - no canonical metric printed with two different values
  G5  score coverage      - >=80% publish, 60-79% provisional, <60% suppress score
  G8  template leakage    - no other sector's vocabulary present
  G9  missing-not-zero    - no "n/a" input silently rendered as a 0 or a derived number

A failed gate BLOCKS the save.  Publishing a contradictory report is worse than publishing none.
"""

from __future__ import annotations

import re

import os as _os_hp, sys as _sys_hp
_sys_hp.path.insert(0, _os_hp.path.dirname(_os_hp.path.abspath(__file__)))
import metric_registry as _MR

_CUR_ALPHABET = (r"[$\u20ac\u00a3\u00a5\u20b9\u20a9\u20bd]|"
                 r"USD|EUR|GBP|JPY|CHF|CAD|AUD|SEK|NOK|DKK|HKD|SGD|CNY|RMB|INR|KRW|BRL|MXN|ZAR|"
                 r"NZD|TWD|PLN|ILS|HUF|TRY|CZK|THB|IDR|PHP|MYR|CLP|COP|ARS|AED|SAR")
_MONEY_RX = re.compile(r"[-+]?(?:%s)\s?[-+]?\d|[-+]?\d+\.\d+\s?%%|[-+]?\d+\.\d+x"
                       % _CUR_ALPHABET, re.I)


def _looks_like_money(text):
    return bool(_MONEY_RX.search(str(text or "")))



_CLASSES = [
    {"id": "leverage_x", "label": "Net debt / EBITDA", "unit": "x",
     "left": ["net debt / ebitda", "net debt/ebitda", "leverage"],
     "right": ["ebitda"],
     "exclude": ["interest cover", "interest coverage", "interest expense", "dividend cover",
                 "dividend coverage", "affo coverage", "affo / dividends", "fcf cover",
                 "free-cash-flow cover", "coverage ratio", "weight"]},
    {"id": "interest_cover", "label": "Interest coverage", "unit": "x",
     "left": ["interest cover", "interest coverage", "ebit / interest"],
     "right": ["interest cover", "interest coverage"],
     "exclude": ["dividend cover", "dividend coverage", "affo coverage", "affo / dividends",
                 "fcf cover", "free-cash-flow cover", "weight"]},
]

_APPROX_WORDS = ["near", "about", "around", "roughly", "approximately", "~", "circa",
                 "just over", "just under", "close to"]

_PEER_MARKERS = ["peer median", "peer average", "peer set", "vs peer", "peer range",
                 "median of", "peers"]

_WEIGHT_TOKENS = {"0.5", "1.0", "1.5", "2.0", "2.5", "3.0", "4.0"}

_WINDOW = 70


_ADJ = 20
_PEER_COL = 100


_MARKER_CACHE = {}


def _body_slice(text, marker):
    rx = _MARKER_CACHE.get(marker)
    if rx is None:
        rx = _MARKER_CACHE[marker] = re.compile(
            r"\s+".join(re.escape(w) for w in str(marker).split()), re.I)
    m = rx.search(str(text))
    return str(text)[:m.start()] if (m and m.start() > 0) else str(text)


def _left_dist(seg, pos, needles):
    best = None
    for n in needles:
        i = seg.rfind(n, max(0, pos - _WINDOW), pos)
        if i >= 0:
            d = pos - i
            best = d if best is None else min(best, d)
    return best


def _right_dist(seg, end, needles):
    tail = seg[end:end + _ADJ]
    best = None
    for n in needles:
        m = re.match(r"(\s*)" + re.escape(n), tail)
        if m:
            d = len(m.group(1))
            best = d if best is None else min(best, d)
    return best


def _classify_tokens(text, unit):
    pat = r"([-+]?\d{1,6}\.\d{1,2})" + ("x" if unit == "x" else "%")
    for raw in str(text).splitlines():
        if not raw.strip():
            continue
        low = raw.lower()
        toks = list(re.finditer(pat, low))
        if len(toks) >= 3:
            continue
        for m in toks:
            s, e = m.span()
            val = m.group(1)
            if unit == "x" and val.lstrip("+-") in _WEIGHT_TOKENS and \
                    re.search(r"/\s*10", low[max(0, s - 60):s]):
                continue

            scored = []
            for c in _CLASSES:
                if c["unit"] != unit:
                    continue
                dl = _left_dist(low, s, c["left"])
                dr = _right_dist(low, e, c["right"])
                cand = [d for d in (dl, dr) if d is not None]
                if not cand:
                    continue
                d = min(cand)
                dx = _left_dist(low, s, c["exclude"])
                if dx is not None and dx < d:
                    continue
                scored.append((d - 10 if (dr is not None and dr <= 2) else d, c))
            if not scored:
                continue
            scored.sort(key=lambda t: t[0])
            c = scored[0][1]
            demote = (s > _PEER_COL
                      or any(p in low for p in _PEER_MARKERS)
                      or any(w in low[max(0, s - 30):s] for w in _APPROX_WORDS))
            yield c["id"], c["label"], val, demote


_APPENDIX_MARKER = "scores high when"


def check_metric_consistency(text, registry=None):
    low = " ".join(str(text).lower().split())
    _cut = low.find(_APPENDIX_MARKER)
    body = low[:_cut] if _cut > 0 else low
    text = text[:len(text)] if _cut < 0 else _body_slice(text, _APPENDIX_MARKER)
    low = body
    findings = []

    for unit in ("x", "%"):
        buckets = {}
        for cid, label, val, is_peer in _classify_tokens(text, unit):
            if is_peer:
                continue
            b = buckets.setdefault(cid, {"label": label, "vals": {}})
            b["vals"][val] = b["vals"].get(val, 0) + 1
        for _cid, b in buckets.items():
            if len(b["vals"]) > 1:
                findings.append({
                    "label": b["label"],
                    "values": sorted(b["vals"].items(), key=lambda kv: -kv[1]),
                    "detail": ", ".join("%s (x%d)" % (v, n) for v, n in sorted(b["vals"].items())),
                })

    drift = []
    if registry:
        for full_key, m in (registry.get("metrics") or {}).items():
            if not m.get("available") or m.get("value") is None:
                continue
            unit = m.get("unit")
            if unit not in ("x", "%"):
                continue
            mid = full_key.split("@", 1)[0]
            if mid not in ("NET_DEBT_TO_EBITDA", "DEBT_TO_TOTAL_CAPITAL"):
                continue
            expect = _MR._xr(m["value"], 2 if unit == "x" else 1)
            stem = m.get("label", "").lower().split("(")[0].strip()
            if not stem:
                continue
            pat = (re.escape(stem).replace(r"\ ", r"\s+")
                   + r"[^0-9\n]{0,40}?(-?\d{1,6}\.\d{1,2})"
                   + ("x" if unit == "x" else "%"))
            found = [mm.group(1) for mm in re.finditer(pat, low)]
            if found and expect not in found:
                drift.append({"metric": m.get("label"), "registered": expect,
                              "printed": ", ".join(sorted(set(found))),
                              "detail": "the label appears %d time(s) and the registered value "
                                        "is published against none of them" % len(found)})
    return {"contradictions": findings, "registry_drift": drift,
            "passed": not findings and not drift}


_DERIVATION_WORDS = ["retained", " less ", "net of", "minus", "coverage", "cover ",
                     "per share", "margin", "ratio", "intensity", "conversion",
                     "deployed)", "after "]


def check_missing_not_zero(text):
    problems = []
    for raw in str(text).splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        has_na = ("not applicable" in low) or bool(re.search(r"(?<![a-z])n/a(?![a-z])", low))
        if not has_na:
            continue
        if not any(w in low for w in _DERIVATION_WORDS):
            continue
        if _looks_like_money(line):
            problems.append(line[:140])
    return {"rows": problems, "passed": not problems}


def check_score_narratives(text):
    bad = []
    for raw in str(text).splitlines():
        line = " ".join(raw.split())
        if not re.search(r"scored\s+\d{1,2}\s*/\s*10", line, re.I):
            continue
        if re.search(r"(?<![a-z])n/a(?![a-z])|not applicable", line, re.I):
            bad.append(line[:140])
    return {"rows": bad, "passed": not bad}



_DEFN_CONFLICTS = [
    ("net debt definition",
     [r"net debt[^.]{0,60}less cash(?![^.]{0,40}short-term)",
      r"total debt less cash"],
     [r"debt\s*-\s*cash\s*-\s*st\s*investments",
      r"nets short-term investments as well as cash",
      r"(?<!not )netted in net debt"]),
]


def check_definition_conflicts(text):
    low = " ".join(str(text).lower().split())
    hits = []
    for label, affirm, deny in _DEFN_CONFLICTS:
        a = [p for p in affirm if re.search(p, low)]
        d = [p for p in deny if re.search(p, low)]
        if a and d:
            hits.append({"metric": label, "stated": a[0], "contradicted_by": d[0]})
    return {"conflicts": hits, "passed": not hits}


def check_coverage(registry, required):
    metrics = (registry or {}).get("metrics") or {}
    avail, missing = 0, []
    for r in required:
        hit = [m for k, m in metrics.items() if k.split("@", 1)[0] == r]
        if hit and any(h.get("available") for h in hit):
            avail += 1
        else:
            missing.append(r)
    pct = (avail / float(len(required))) if required else 1.0
    if pct >= 0.80:
        status = "publish"
    elif pct >= 0.60:
        status = "provisional"
    else:
        status = "suppress_score"
    return {"coverage": pct, "missing": missing, "status": status,
            "passed": status != "suppress_score"}


def run_gates(text, *, registry=None, routing=None, required=None, strict=True):
    import sector_router as SR

    manifest = {}
    manifest["G3_metric_consistency"] = check_metric_consistency(text, registry)
    manifest["G9_missing_not_zero"] = check_missing_not_zero(text)
    manifest["G18_score_narratives"] = check_score_narratives(text)
    manifest["G19_definition_conflicts"] = check_definition_conflicts(text)

    if routing is not None:
        hits = SR.guard_output(text, routing, raise_on_hit=False)
        manifest["G8_template_leakage"] = {
            "template": routing.get("template"), "hits": hits, "passed": not hits}
    if required:
        manifest["G5_score_coverage"] = check_coverage(registry, required)
    else:
        manifest["G5_score_coverage"] = {
            "ran": False, "passed": True,
            "note": ("no required-metric set was supplied for the active template, so score "
                     "coverage was not measured - this gate did not run")}

    failed = [k for k, v in manifest.items() if not v.get("passed", True)]
    manifest["passed"] = not failed
    manifest["failed_gates"] = failed

    if failed and strict:
        raise GateFailure(_describe(manifest, failed), manifest)
    return manifest


class GateFailure(RuntimeError):
    def __init__(self, message, manifest=None):
        super().__init__(message)
        self.manifest = manifest or {}


def _describe(manifest, failed):
    out = ["Publication gates failed - report not saved. A contradictory report is worse than "
           "no report."]
    for g in failed:
        d = manifest[g]
        if g == "G3_metric_consistency":
            for c in d["contradictions"]:
                out.append("  G3: %r published with multiple values: %s"
                           % (c["label"], c["detail"]))
            for c in d["registry_drift"]:
                out.append("  G3: %r printed as %s but the registry holds %s"
                           % (c["metric"], c["printed"], c["registered"]))
        elif g == "G9_missing_not_zero":
            for r in d["rows"][:5]:
                out.append("  G9: derived figure on a row with an unavailable input: %s" % r)
        elif g == "G19_definition_conflicts":
            for c in d["conflicts"]:
                out.append("  G19: %s is defined two ways in one report (%r contradicted by %r)"
                           % (c["metric"], c["stated"], c["contradicted_by"]))
        elif g == "G18_score_narratives":
            for r in d["rows"][:5]:
                out.append("  G18: score asserted while quoting an unavailable input: %s" % r)
        elif g == "G8_template_leakage":
            for h in d["hits"]:
                out.append("  G8: %r is forbidden for template %s"
                           % (h["term"], d["template"]))
        elif g == "G5_score_coverage":
            out.append("  G5: only %.0f%% of required metrics available (missing: %s)"
                       % (d["coverage"] * 100, ", ".join(d["missing"])))
    return "\n".join(out)


def format_manifest(manifest):
    order = ["G3_metric_consistency", "G5_score_coverage", "G8_template_leakage",
             "G9_missing_not_zero"]
    names = {"G3_metric_consistency": "Metric consistency (one value per label)",
             "G5_score_coverage": "Score coverage",
             "G8_template_leakage": "Sector template integrity",
             "G9_missing_not_zero": "No derived value from an unavailable input"}
    rows = []
    for g in order:
        if g not in manifest:
            continue
        d = manifest[g]
        note = ""
        if g == "G5_score_coverage":
            note = "%.0f%% of required metrics available - %s" % (d["coverage"] * 100, d["status"])
        elif g == "G3_metric_consistency" and not d["passed"]:
            note = "; ".join(c["label"] + ": " + c["detail"] for c in d["contradictions"])
        elif g == "G8_template_leakage" and not d["passed"]:
            note = ", ".join(h["term"] for h in d["hits"])
        rows.append({"gate": g.split("_")[0], "check": names[g],
                     "result": "Pass" if d.get("passed", True) else "Fail", "note": note})
    return rows
