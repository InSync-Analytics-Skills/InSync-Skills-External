"""Chart-level data validation.

Named `dataquality` rather than `validate` on purpose: the package already
exports a `validate()` function for spec parsing, and a module of the same
name is shadowed by it for any caller doing `from insync_charts import ...`.

Runs on every spec before drawing. The engine will still render a flawed series —
refusing would just push the caller into drawing it somewhere worse — but it will
never render one *silently*. Every finding lands in the manifest, and the ones a
reader must know about are disclosed on the chart itself.

Findings are structured, not prose:

    {"code": "coverage", "severity": "warn", "series": "Adjusted EPS",
     "message": "5 of 8 periods reported", "detail": {...}}

Severities: `info` (worth recording), `warn` (a reader should be told),
`error` (the chart would mislead; the caller should reconsider the request).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

Number = Optional[float]

MIN_TREND_POINTS = 4
MIN_BAND_POINTS = 8
OUTLIER_Z = 3.5
SPARSE_FRACTION = 0.5


def _finding(code: str, severity: str, message: str,
             series: Optional[str] = None, **detail) -> Dict[str, Any]:
    return {"code": code, "severity": severity, "series": series,
            "message": message, "detail": detail}


def median(xs: Sequence[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def outliers(values: Sequence[Number]) -> List[int]:
    xs = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(xs) < MIN_BAND_POINTS:
        return []
    vals = [v for _, v in xs]
    med = median(vals)
    mad = median([abs(v - med) for v in vals])
    if mad == 0:
        return []
    out = []
    for i, v in xs:
        if abs(0.6745 * (v - med) / mad) > OUTLIER_Z:
            out.append(i)
    return out


def leading_gap(values: Sequence[Number]) -> int:
    n = 0
    for v in values:
        if v is None:
            n += 1
        else:
            break
    return n


def interior_gaps(values: Sequence[Number]) -> List[int]:
    idx = [i for i, v in enumerate(values) if v is not None]
    if len(idx) < 2:
        return []
    return [i for i in range(idx[0], idx[-1]) if values[i] is None]


def trailing_gap(values: Sequence[Number]) -> List[int]:
    idx = [i for i, v in enumerate(values) if v is not None]
    if not idx:
        return []
    return [i for i in range(idx[-1] + 1, len(values)) if values[i] is None]


def check(spec, periods: Sequence[str], primitive: str) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    n_x = len(periods) or (len(spec.series[0].values) if spec.series else 0)

    for s in spec.series:
        vals = list(s.values)
        n = len(vals)
        present = [v for v in vals if v is not None]
        n_missing = n - len(present)

        if n and not present:
            findings.append(_finding(
                "empty", "error", "no reported values in this series", s.name,
                periods=n))
            continue

        if n_missing:
            findings.append(_finding(
                "coverage", "warn",
                f"{len(present)} of {n} periods reported", s.name,
                reported=len(present), periods=n,
                first_reported=(periods[vals.index(present[0])]
                                if periods and present else None)))

        lead = leading_gap(vals)
        if lead:
            findings.append(_finding(
                "late_start", "warn",
                (f"series begins at {periods[lead]} — no data for the first "
                 f"{lead} period(s) shown" if periods and lead < len(periods)
                 else f"series begins after {lead} empty period(s)"),
                s.name, leading_gap=lead))

        holes = interior_gaps(vals)
        if holes:
            findings.append(_finding(
                "interior_gap", "warn",
                f"{len(holes)} gap(s) inside the reported range", s.name,
                indices=holes,
                periods_missing=[periods[i] for i in holes
                                 if periods and i < len(periods)]))

        tail = trailing_gap(vals)
        if tail:
            last_i = tail[0] - 1
            last_p = periods[last_i] if periods and last_i < len(periods) else None
            findings.append(_finding(
                "trailing_gap", "warn",
                (f"stops at {last_p}; no value for the last {len(tail)} period(s) shown"
                 if last_p else
                 f"no value for the last {len(tail)} period(s) shown"),
                s.name, indices=tail, last_reported=last_p,
                periods_missing=[periods[i] for i in tail
                                 if periods and i < len(periods)]))

        claims_trend = getattr(s, "kind", "bar") not in ("marker", "scatter")

        if (claims_trend and n and len(present) / n < SPARSE_FRACTION
                and primitive in ("combo_trend", "band_history", "surprise")):
            findings.append(_finding(
                "sparse", "error",
                (f"only {len(present)} of {n} periods reported — too sparse to "
                 f"read as a trend; plot it as markers, or narrow the period range"),
                s.name, reported=len(present), periods=n))

        if claims_trend and primitive in ("combo_trend", "band_history") and \
                len(present) < MIN_TREND_POINTS:
            findings.append(_finding(
                "short_history", "warn",
                f"only {len(present)} reported point(s) — a trend needs at least "
                f"{MIN_TREND_POINTS}", s.name, reported=len(present)))

        if primitive == "band_history" and len(present) < MIN_BAND_POINTS:
            findings.append(_finding(
                "band_history_short", "warn",
                (f"{len(present)} points — statistical bands need at least "
                 f"{MIN_BAND_POINTS} to mean anything"), s.name,
                reported=len(present)))

        outs = outliers(vals)
        if outs:
            findings.append(_finding(
                "outlier", "warn",
                (f"{len(outs)} robust outlier(s) present — they will stretch the "
                 f"axis and flatten the rest of the series"), s.name,
                indices=outs,
                periods_affected=[periods[i] for i in outs
                                  if periods and i < len(periods)],
                values=[vals[i] for i in outs]))

    if spec.matrix:
        cells = [v for row in spec.matrix for v in row]
        miss = sum(1 for v in cells if v is None)
        if miss:
            findings.append(_finding(
                "coverage", "warn",
                f"{len(cells) - miss} of {len(cells)} cells populated",
                None, populated=len(cells) - miss, cells=len(cells)))

    if n_x and n_x < 2 and primitive in ("combo_trend", "band_history"):
        findings.append(_finding(
            "single_period", "error",
            "a single period cannot show a trend", None, periods=n_x))

    return findings


def summary(spec, periods: Sequence[str], primitive: str,
            findings: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    n_x = len(periods) or (len(spec.series[0].values) if spec.series else 0)
    expected = int(spec.opt("expected_periods", n_x) or n_x)

    rows = []
    for s in spec.series:
        got = sum(1 for v in s.values if v is not None)
        derived = bool(s.derive)
        rows.append({
            "series": s.name,
            "reported": got,
            "expected": expected,
            "completeness_pct": round(got / expected * 100.0, 1) if expected else None,
            "missing": max(0, expected - got),
            "first_reported": next((periods[i] for i, v in enumerate(s.values)
                                    if v is not None and i < len(periods)), None),
            "last_reported": next((periods[i] for i in range(len(s.values) - 1, -1, -1)
                                   if s.values[i] is not None and i < len(periods)), None),
            "truncated": got < expected,
            "derived": derived,
            "prior_supplied": len(s.prior) if getattr(s, "prior", None) else 0,
        })

    worst_pct = min((r["completeness_pct"] for r in rows
                     if r["completeness_pct"] is not None), default=None)
    peers = None
    if primitive in ("peer_bar", "scatter_xy"):
        cats = spec.categories or spec.periods
        target = str(spec.opt("highlight", spec.ticker) or "").upper()
        peers = sum(1 for c in cats if str(c).upper() != target)

    return {
        "expected_periods": expected,
        "x_positions": n_x,
        "series": rows,
        "worst_completeness_pct": worst_pct,
        "complete": bool(worst_pct is not None and worst_pct >= 100.0),
        "peer_count": peers,
        "as_of": spec.as_of,
        "report_date": spec.report_date,
        "severity": worst(findings),
        "finding_codes": sorted({f["code"] for f in findings}),
    }


def worst(findings: Sequence[Dict[str, Any]]) -> str:
    order = {"info": 0, "warn": 1, "error": 2}
    return max((f["severity"] for f in findings), key=lambda s: order.get(s, 0),
               default="info")


def disclosure(findings: Sequence[Dict[str, Any]]) -> List[str]:
    notes: List[str] = []
    for f in findings:
        if f["code"] == "coverage" and f.get("series"):
            notes.append(f"{f['series']}: {f['message']}")
        elif f["code"] == "late_start":
            first = f["detail"].get("leading_gap")
            notes.append(f"{f['series']}: no data before the "
                         f"{_ordinal(first + 1)} period shown")
        elif f["code"] == "interior_gap":
            ps = f["detail"].get("periods_missing") or []
            notes.append(f"{f['series']}: not reported in "
                         + ", ".join(str(p) for p in ps[:3])
                         + ("…" if len(ps) > 3 else ""))
        elif f["code"] == "trailing_gap":
            last = f["detail"].get("last_reported")
            ps = f["detail"].get("periods_missing") or []
            notes.append(f"{f['series']}: nothing after {last}" if last
                         else f"{f['series']}: not reported in "
                              + ", ".join(str(p) for p in ps[:3]))
        elif f["code"] in ("single_period", "short_history", "sparse", "empty"):
            notes.append(f"{f['series']}: {f['message']}" if f.get("series")
                         else f["message"])
        elif f["code"] == "outlier":
            ps = f["detail"].get("periods_affected") or []
            if ps:
                notes.append("outlier: " + ", ".join(str(p) for p in ps[:2])
                             + ("…" if len(ps) > 2 else ""))
    seen, out = set(), []
    for n in notes:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"
