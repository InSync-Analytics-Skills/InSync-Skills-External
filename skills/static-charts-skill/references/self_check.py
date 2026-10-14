#!/usr/bin/env python3
"""Self-check: renders the fixture gallery and asserts the engine's guarantees.

Run this after any change to the theme or a primitive. It is the regression
suite for "the same chart type always looks the same".

    python self_check.py            # full check
    python self_check.py --quick    # skip the determinism re-render
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.join(os.path.dirname(_HERE), "scripts")
sys.path.insert(0, _SCRIPTS)

from insync_charts import (catalogue, load_specs, render_chart,
                           render_many, token_map, validate)
from insync_charts import registry, theme

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "v1_gallery.json")
V1_REQUIRED = [
    "revenue_trend", "eps_trend", "margin_trend", "valuation_history",
    "surprise_history", "revision_trend", "peer_comparison",
    "capital_allocation_waterfall",
]

results = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append({"check": name, "ok": bool(ok), "detail": detail})
    return bool(ok)


def digest(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def fmt_median(xs):
    s = sorted(xs); n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def _pf_raises(fn) -> bool:
    try:
        fn()
        return False
    except Exception:
        return True


def luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--keep", help="keep rendered output in this directory")
    args = ap.parse_args(argv)

    tmp = args.keep or tempfile.mkdtemp(prefix="insync_charts_")
    os.makedirs(tmp, exist_ok=True)

    reg = {r["chart_type"] for r in catalogue()}
    missing = [c for c in V1_REQUIRED if c not in reg]
    check("V1 chart types registered", not missing, f"missing: {missing}")

    from insync_charts.primitives import PRIMITIVES
    bad = [k for k, v in registry.REGISTRY.items() if v["primitive"] not in PRIMITIVES]
    check("registry primitives all exist", not bad, f"dangling: {bad}")

    specs = load_specs(FIXTURE)
    m1 = render_many(specs, os.path.join(tmp, "run1"))
    check("fixture gallery renders", m1["failed"] == 0,
          f"{m1['rendered']}/{m1['count']} rendered; "
          f"{[c.get('error') for c in m1['charts'] if not c.get('ok')]}")

    covered = {c["chart_type"] for c in m1["charts"] if c.get("ok")}
    gap = [c for c in V1_REQUIRED if c not in covered]
    check("V1 types exercised by fixtures", not gap, f"not exercised: {gap}")

    both = all(set(c["files"]) >= {"png", "svg"} for c in m1["charts"] if c.get("ok"))
    check("PNG and SVG emitted", both)

    if not args.quick:
        m2 = render_many(specs, os.path.join(tmp, "run2"))
        diffs = []
        for a, b in zip(m1["charts"], m2["charts"]):
            for ext, pa in a.get("files", {}).items():
                pb = b.get("files", {}).get(ext)
                if not pb or digest(pa) != digest(pb):
                    diffs.append(os.path.basename(pa))
        check("renders are byte-identical across runs", not diffs, f"differ: {diffs}")

    lums = [luminance(c) for c in theme.STACK_RAMP]
    steps = [round(b - a, 3) for a, b in zip(lums, lums[1:])]
    check("stack ramp luminance is monotonic and separated",
          all(s >= 0.09 for s in steps), f"steps: {steps}")

    holes = {"chart_type": "revenue_trend", "company": {"ticker": "TEST"},
             "periods": ["1Q", "2Q", "3Q", "4Q"],
             "series": [{"name": "Revenue", "kind": "bar", "unit": "usd_mn",
                         "values": [100, None, None, 130]}]}
    r = render_chart(holes, os.path.join(tmp, "edge"), strict=False)
    check("missing values render and are flagged",
          r.get("ok") and any("missing" in w for w in r.get("warnings", [])),
          str(r.get("error") or r.get("warnings")))

    empty = dict(holes)
    empty["series"] = [{"name": "Revenue", "kind": "bar", "values": [None] * 4}]
    r = render_chart(empty, os.path.join(tmp, "edge"), strict=False)
    check("all-missing series still renders without exception", r.get("ok"),
          str(r.get("error")))

    cases = [
        ({"chart_type": "not_a_chart", "series": [{"name": "x", "values": [1]}]},
         "unknown chart_type"),
        ({"chart_type": "revenue_trend"}, "series"),
        ({"chart_type": "revenue_trend", "periods": ["1Q", "2Q"],
          "series": [{"name": "x", "values": [1, 2, 3]}]}, "values"),
        ({"chart_type": "peer_comparison",
          "series": [{"name": "x", "values": [1, 2]}]}, "categories"),
    ]
    bad_msgs = []
    for spec, needle in cases:
        v = validate(spec)
        if v["ok"] or needle not in (v["error"] or ""):
            bad_msgs.append((spec.get("chart_type"), v))
    check("invalid specs rejected with a clear message", not bad_msgs, str(bad_msgs))

    pres = []
    for p in sorted(theme.PRESETS):
        s = dict(holes)
        s["output"] = {"preset": p, "formats": ["png"], "basename": f"preset_{p}"}
        rr = render_chart(s, os.path.join(tmp, "presets"), strict=False)
        if not rr.get("ok"):
            pres.append((p, rr.get("error")))
    check("every size preset renders", not pres, str(pres))

    t = token_map()
    check("design tokens exported",
          {"ink", "orange", "series", "stack_ramp", "serif", "sans"} <= set(t))

    import copy
    import build_pack
    shared = {"company": {"name": "Test Corp", "ticker": "TST"}, "source": "fixture",
              "period": "1Q26"}
    peer = {"chart_type": "peer_comparison", "categories": ["TST", "AAA"],
            "series": [{"name": "m", "unit": "pct", "values": [10, 20]}]}
    batch = [
        dict(shared, chart_type="revenue_trend", periods=["1Q", "2Q"],
             series=[{"name": "R", "kind": "bar", "unit": "usd_mn", "values": [1, 2]}]),
        dict(shared, **peer, title="Margin versus peers"),
        dict(shared, **peer, title="ROIC versus peers"),
    ]
    n1 = build_pack.assign_basenames(batch)
    swapped = copy.deepcopy(batch)
    swapped[1], swapped[2] = swapped[2], swapped[1]
    n2 = build_pack.assign_basenames(swapped)
    by_title_1 = {c.get("title", c["chart_type"]): n for c, n in zip(batch, n1)}
    by_title_2 = {c.get("title", c["chart_type"]): n for c, n in zip(swapped, n2)}
    check("pack basenames survive reordering", by_title_1 == by_title_2,
          f"{n1} vs {n2}")
    inserted = [dict(shared, chart_type="margin_trend", periods=["1Q"],
                     series=[{"name": "GM", "kind": "line", "unit": "pct",
                              "values": [70]}])] + copy.deepcopy(batch)
    check("inserting an exhibit renames none of the others",
          set(n1) <= set(build_pack.assign_basenames(inserted)))
    check("duplicate chart types get distinct names", len(set(n1)) == len(n1), str(n1))

    built = build_pack.build(batch, os.path.join(tmp, "packs"), gallery=True)
    check("pack builds with gallery and manifest",
          built["ok"] and built["index"] and os.path.exists(built["manifest"]),
          str(built.get("failures")))
    if built.get("manifest"):
        v = build_pack.verify(built["manifest"])
        check("pack re-renders identically from its manifest alone",
              v["ok"] and v["files_identical"] == v["files_checked"] > 0,
              f"{v['files_identical']}/{v['files_checked']} {v['problems']}")
        victim = os.path.join(os.path.dirname(built["manifest"]),
                              json.load(open(built["manifest"]))
                              ["exhibits"][0]["files"]["png"])
        with open(victim, "ab") as fh:
            fh.write(b"\x00tampered")
        check("tampered pack fails verification",
              not build_pack.verify(built["manifest"])["ok"])

    from insync_charts import dataquality as dq, observe

    partial = {"chart_type": "eps_trend", "company": {"ticker": "TST"},
               "source": "fx", "periods": ["1Q", "2Q", "3Q", "4Q", "5Q", "6Q"],
               "series": [{"name": "EPS", "kind": "bar", "unit": "usd_ps",
                           "values": [None, None, None, 1.0, 1.1, 1.2]}],
               "output": {"basename": "qc_partial", "formats": ["png"]}}
    r = render_chart(partial, os.path.join(tmp, "qc"), strict=False)
    codes = {f["code"] for f in r.get("findings", [])}
    check("incomplete series is flagged, not silently plotted",
          r.get("ok") and {"coverage", "late_start"} <= codes, str(sorted(codes)))
    check("incomplete series is disclosed on the chart",
          bool(dq.disclosure(r.get("findings", []))),
          str(dq.disclosure(r.get("findings", []))))

    vals = [25, 33, 48, 68, 147, 186, 80, 102, 84, 72, 60, 63, 62]
    check("robust outlier detection catches an extreme multiple",
          len(dq.outliers(vals)) >= 1, str(dq.outliers(vals)))

    band = {"chart_type": "valuation_history", "company": {"ticker": "TST"},
            "source": "fx", "periods": [f"P{i}" for i in range(len(vals))],
            "series": [{"name": "P/E", "kind": "line", "unit": "x",
                        "values": [float(v) for v in vals]}],
            "output": {"basename": "qc_band", "formats": ["png"]}}
    rb = render_chart(band, os.path.join(tmp, "qc"), strict=False)
    med, mean_ = fmt_median(vals), sum(vals) / len(vals)
    check("valuation band centres on the median, not the mean",
          rb.get("ok") and f"{med:,.1f}" in rb.get("observation", ""),
          f"median={med:.1f} mean={mean_:.1f} obs={rb.get('observation')}")
    check("outlier is reported for the valuation series",
          any(f["code"] == "outlier" for f in rb.get("findings", [])))

    missing_obs = []
    for sp in specs:
        rr = render_chart(sp, os.path.join(tmp, "qc"), strict=False)
        if rr.get("ok") and not rr.get("observation"):
            missing_obs.append(rr.get("chart_type"))
    check("every fixture chart yields a Key Observation", not missing_obs,
          str(missing_obs))

    off = dict(partial, options={"observation": False},
               output={"basename": "qc_obs_off", "formats": ["png"]})
    on = dict(partial, options={"observation": "Caller supplied text"},
              output={"basename": "qc_obs_on", "formats": ["png"]})
    r_off = render_chart(off, os.path.join(tmp, "qc"), strict=False)
    r_on = render_chart(on, os.path.join(tmp, "qc"), strict=False)
    check("observation can be suppressed and overridden",
          r_off.get("observation") == "" and
          r_on.get("observation") == "Caller supplied text",
          f"{r_off.get('observation')!r} / {r_on.get('observation')!r}")

    yoy = {"chart_type": "revenue_trend", "company": {"ticker": "TST"},
           "source": "fx", "periods": [f"{q}Q" for q in range(1, 9)],
           "series": [{"name": "Rev", "kind": "bar", "unit": "usd_mn",
                       "values": [10, 11, 12, 13, 14, 15, 16, 17]},
                      {"name": "YoY", "kind": "line", "axis": "right",
                       "unit": "pct", "derive": {"op": "yoy", "of": "Rev"}}],
           "output": {"basename": "qc_yoy", "formats": ["png"]}}
    ry = render_chart(yoy, os.path.join(tmp, "qc"), strict=False)
    late = [f for f in ry.get("findings", [])
            if f["code"] == "late_start" and f.get("series") == "Rev"]
    check("a complete primary series is not flagged as late-starting",
          ry.get("ok") and not late, str(late))

    fracs = [r.get("plot_fraction") for r in m1["charts"] if r.get("plot_fraction")]
    check("plot area is the dominant share of the canvas",
          fracs and min(fracs) >= 0.45, f"min={min(fracs) if fracs else None}")

    per = ["4Q24", "1Q25", "2Q25", "3Q25", "4Q25", "1Q26", "2Q26", "3Q26"]
    rev = [64.7, 65.6, 69.6, 70.1, 76.4, 77.7, 81.3, 82.9]
    no_prior = {"chart_type": "revenue_trend", "company": {"ticker": "TST"},
                "source": "fx", "periods": per,
                "series": [{"name": "Rev", "kind": "bar", "unit": "usd_mn",
                            "values": rev},
                           {"name": "YoY", "kind": "line", "axis": "right",
                            "unit": "pct", "derive": {"op": "yoy", "of": "Rev"}}],
                "output": {"basename": "qc_noprior", "formats": ["png"]}}
    with_prior = json.loads(json.dumps(no_prior))
    with_prior["series"][0]["prior"] = [56.2, 56.5, 62.0, 61.9]
    with_prior["output"]["basename"] = "qc_prior"

    a = render_chart(no_prior, os.path.join(tmp, "qc2"), strict=False)
    b = render_chart(with_prior, os.path.join(tmp, "qc2"), strict=False)
    check("a derived series without prior history is explained, not silent",
          any("Supply" in w and "prior" in w for w in a.get("warnings", [])),
          str([w for w in a.get("warnings", []) if "prior" in w])[:160])
    check("prior history completes a derived series",
          b.get("ok") and b["validation"]["worst_completeness_pct"] == 100.0,
          str(b.get("validation", {}).get("worst_completeness_pct")))

    from insync_charts import fmt as _yfmt
    yv = _yfmt.yoy([-3.88, 2.0, -2.16, 4.0], 2)
    check("year-on-year is blank from a negative base and unchanged from a positive one",
          yv[2] is None and yv[3] is not None and abs(yv[3] - 100.0) < 1e-9, str(yv))
    neg = json.loads(json.dumps(with_prior))
    neg["series"][0]["values"] = [-3.88, -1.0, 0.5, 1.2, -2.16, 0.4, 0.9, 1.5]
    neg["series"][0].pop("prior", None)
    neg["chart_type"] = "eps_trend"
    neg["series"][0]["unit"] = "usd_ps"
    neg["output"]["basename"] = "qc_negbase"
    rn = render_chart(neg, os.path.join(tmp, "qc2"), strict=False)
    check("a blank from a negative base is named in the warnings",
          any("negative base" in w for w in rn.get("warnings", [])),
          str([w for w in rn.get("warnings", []) if "derive" in w])[:200])

    check("growth is stated as YoY, never against an arbitrary anchor",
          "YoY" in b.get("observation", "")
          and " vs " not in b.get("observation", "")
          and "CAGR" not in b.get("observation", ""), b.get("observation", ""))

    check("completeness is reported per series",
          all("completeness_pct" in r for r in b["validation"]["series"]))
    check("a chart below 100% history is flagged",
          a["validation"]["complete"] is False,
          str(a["validation"]["worst_completeness_pct"]))

    pc = render_chart({"chart_type": "peer_comparison", "company": {"ticker": "TST"},
                       "source": "fx", "as_of": "2026-07-29",
                       "categories": ["TST", "A", "B", "C"],
                       "series": [{"name": "m", "unit": "pct",
                                   "values": [10, 20, 30, 40]}],
                       "output": {"basename": "qc_peer", "formats": ["png"]}},
                      os.path.join(tmp, "qc2"), strict=False)
    check("peer count and data freshness recorded",
          pc["validation"]["peer_count"] == 3
          and pc["validation"]["as_of"] == "2026-07-29",
          f"peers={pc['validation'].get('peer_count')} as_of={pc['validation'].get('as_of')}")

    def _pages(pk):
        if not (pk.get("pdf") and os.path.exists(pk["pdf"])):
            return 0
        raw = open(pk["pdf"], "rb").read()
        return raw.count(b"/Type /Page") - raw.count(b"/Type /Pages")

    one = [dict(shared, **json.loads(json.dumps(with_prior)))]
    vdef = build_pack.build(one, os.path.join(tmp, "qc2packs"), gallery=False)
    vopt = build_pack.build(one, os.path.join(tmp, "qc2packs_v"), gallery=False,
                            validation=True)
    p_def, p_opt = _pages(vdef), _pages(vopt)
    check("default pack is cover + exhibits, with NO validation page",
          p_def >= 2, f"pages={p_def}")
    check("--validation appends exactly one extra page",
          p_opt == p_def + 1, f"default={p_def} with_validation={p_opt}")

    from insync_charts import fmt as _fmt, observe as _obs
    from insync_charts.primitives import _surprise_series
    from insync_charts.spec import parse as _build

    check("compact labels honour the unit's magnitude",
          _fmt.fmt_value(20543, "usd_mn", compact=True) == "$20.5B"
          and _fmt.axis_label("usd_mn", peak=20543) == "US$",
          _fmt.fmt_value(20543, "usd_mn", compact=True))

    check("mathtext parsing disabled", theme.rc_params(200).get("text.parse_math") is False,
          str(theme.rc_params(200).get("text.parse_math")))

    est = _build({"chart_type": "eps_trend", "source": "t",
                  "periods": ["1Q26", "2Q26", "3Q26", "4Q26"],
                  "series": [{"name": "EPS", "unit": "usd_ps", "estimate_from": 2,
                              "values": [1.0, 1.1, 5.0, 6.0]}]})
    line = _obs.for_chart(est, "combo_trend")
    check("observation anchors on reported, not forecast",
          "in 2Q26" in line and "consensus" in line and "in 4Q26" not in line, line)

    gap = _build({"chart_type": "revenue_trend", "source": "t",
                  "periods": ["1Q25", "2Q25", "3Q25", "4Q25", "1Q26", "2Q26"],
                  "units": {"left": "usd_mn", "right": "pct"},
                  "series": [{"name": "Rev", "unit": "usd_mn",
                              "values": [10, None, 12, 13, 14, 15]},
                             {"name": "G", "axis": "right", "unit": "pct",
                              "derive": {"op": "yoy", "of": "Rev",
                                         "periods_per_year": 4}}]})
    check("no year-on-year claim without an aligned year-ago period",
          "YoY" not in _obs.for_chart(gap, "combo_trend"), _obs.for_chart(gap, "combo_trend"))

    two_ratios = _build({"chart_type": "margin_trend", "source": "t",
                         "periods": ["1Q26", "2Q26"],
                         "units": {"left": "pct", "right": "pct"},
                         "series": [{"name": "CET1", "unit": "pct", "values": [15.0, 14.1]},
                                    {"name": "Efficiency", "axis": "right",
                                     "unit": "pct", "values": [52.0, 48.0]}]})
    check("an unrelated right-axis ratio is not read as growth",
          "+48.0% YoY" not in _obs.for_chart(two_ratios, "combo_trend"), _obs.for_chart(two_ratios, "combo_trend"))

    sur = _build({"chart_type": "surprise_history", "source": "t",
                  "periods": ["1Q26", "2Q26"],
                  "series": [{"name": "Actual", "unit": "usd_ps", "values": [5.5, 8.2]},
                             {"name": "Estimate", "unit": "usd_ps", "values": [5.0, 6.3]},
                             {"name": "Surprise", "unit": "pct", "focus": True,
                              "derive": {"op": "surprise_pct", "actual": "Actual",
                                         "estimate": "Estimate"}}]})
    check("surprise chart plots the surprise, not series[0]",
          _surprise_series(sur).name == "Surprise", _surprise_series(sur).name)

    check("brand orange is reserved when a focus series is present",
          theme.series_color(1, reserve_brand=True) != theme.ORANGE
          and theme.series_color(1) == theme.ORANGE,
          theme.series_color(1, reserve_brand=True))

    long_note = ("Consensus captured as-of the day before each release "
                 "(8-9 brokers), not restated afterwards.")
    fnspec = _build({"chart_type": "eps_trend", "source": "t",
                     "periods": ["1Q26", "2Q26"],
                     "options": {"footnote": long_note},
                     "series": [{"name": "EPS", "unit": "usd_ps", "values": [1.0, 1.1]}]})
    fnraw = {"chart_type": "eps_trend", "source": "t",
             "periods": ["1Q26", "2Q26"],
             "options": {"footnote": long_note},
             "series": [{"name": "EPS", "unit": "usd_ps", "values": [1.0, 1.1]}],
             "output": {"basename": "footnote_kept", "formats": ["svg"]}}
    fnres = render_chart(fnraw, os.path.join(tmp, "footnote"), strict=False)
    svg = open(fnres["files"]["svg"]).read()
    check("a long footnote is wrapped onto its own line, not dropped",
          "not restated afterwards" in svg or "restated" in svg,
          f"chars={len(svg)}")

    from insync_charts import dataquality as _dq
    yoy = [-4.2, -5.0, None, None, 9.5, 18.0, None, None]
    qs = ["3Q24", "4Q24", "1Q25", "2Q25", "3Q25", "4Q25", "1Q26", "2Q26"]
    tspec = _build({"chart_type": "revenue_trend", "source": "t", "periods": qs,
                    "units": {"left": "usd_mn", "right": "pct"},
                    "series": [{"name": "Rev", "unit": "usd_mn",
                                "values": [16106, 16215, None, None, 17638, 19133,
                                           17415, 20543]},
                               {"name": "YoY growth", "axis": "right", "unit": "pct",
                                "values": yoy}]})
    tf = _dq.check(tspec, qs, "combo_trend")
    tg = next((f for f in tf if f["code"] == "trailing_gap"
               and f["series"] == "YoY growth"), None)
    check("a series stopping short of the axis is flagged",
          tg is not None and tg["detail"]["last_reported"] == "4Q25"
          and tg["detail"]["periods_missing"] == ["1Q26", "2Q26"],
          str(tg))

    notes = " | ".join(_dq.disclosure(tf))
    check("disclosure covers trailing gaps as well as interior ones",
          "1Q25" in notes and "4Q25" in notes, notes)

    from insync_charts import preflight as _pf

    check("R- revision prefixes are stripped, CY- kept",
          _pf.normalise_period("R-1Q25") == "1Q25"
          and _pf.normalise_period("CY-1Q25") == "CY-1Q25"
          and _pf.normalise_period("1Q25") == "1Q25",
          _pf.normalise_period("R-1Q25"))

    mcp = [{"TICKER_ID": 144, "LTCODE": "IS_P_101", "FISCALPERIOD": "R-1Q25",
            "VALUE": 14249.0},
           {"TICKER_ID": 144, "LTCODE": "IS_P_101", "FISCALPERIOD": "2Q25",
            "VALUE": 16569.0}]
    idx = _pf.normalise_rows(mcp)
    check("a restated row is reachable under its clean label",
          idx.get((144, "IS_P_101", "1Q25")) == 14249.0
          and _pf.restated_periods(mcp) == ["1Q25"],
          str(sorted(idx)))

    cases = [
        ("short sum", ([14249.0, 16569.0], 67589.0)),
        ("no anchor", ([14249.0, 16569.0, 17638.0, 19133.0], None)),
        ("a hole", ([14249.0, None, 17638.0, 19133.0], 67589.0)),
        ("10% off", ([14249.0, 16569.0, 17638.0, 19133.0], 74300.0)),
    ]
    leaks = []
    for name, (vals, annual) in cases:
        try:
            _pf.reconcile(vals, annual, label=name)
            leaks.append(name)
        except _pf.PreflightError:
            pass
    good = _pf.reconcile([14249.0, 16569.0, 17638.0, 19133.0], 67589.0)
    check("reconcile raises on every failure mode and passes the true case",
          not leaks and abs(good - 67589.0) < 1e-6, f"leaked: {leaks}")

    check("expect_rows catches a silently dropped period",
          _pf_raises(lambda: _pf.expect_rows([1] * 167, 169, "valuation")),
          "")

    v = validate({"chart_type": "revenue_trend", "periods": ["R-1Q25", "2Q25"],
                  "series": [{"name": "R", "unit": "usd_mn", "values": [1, 2]}]})
    check("engine rejects un-normalised R- period labels",
          not v["ok"] and "R-1Q25" in (v["error"] or ""), str(v.get("error"))[:120])

    loss = _build({"chart_type": "eps_trend", "source": "t",
                   "periods": ["3Q24", "4Q24", "1Q25", "2Q25",
                               "3Q25", "4Q25", "1Q26", "2Q26"],
                   "units": {"left": "usd_ps"},
                   "series": [{"name": "EPS", "unit": "usd_ps",
                               "values": [-3.88, -0.03, -0.19, -0.67,
                                          0.9, -0.12, -0.73, -2.16]}]})
    lo = _obs.for_chart(loss, "combo_trend")
    check("no percentage change is quoted across negative values",
          "%" not in lo and "2.16" in lo, lo)

    uneven = _build({"chart_type": "revenue_trend", "source": "t",
                     "periods": ["4Q24", "1Q25", "2Q25", "3Q25",
                                 "4Q25", "1Q26", "2Q26", "3Q26"],
                     "units": {"left": "usd_mn", "right": "pct"},
                     "series": [{"name": "Rev", "unit": "usd_mn",
                                 "values": [79700, 62200, 63700, 63200,
                                            86200, 67300, 69600, 70500]},
                                {"name": "YoY", "axis": "right", "unit": "pct",
                                 "derive": {"op": "yoy", "of": "Rev",
                                            "periods_per_year": 4}}]})
    un = _obs.for_chart(uneven, "combo_trend")
    whole = _build({"chart_type": "revenue_trend", "source": "t",
                    "periods": ["3Q24", "4Q24", "1Q25", "2Q25", "3Q25",
                                "4Q25", "1Q26", "2Q26", "3Q26"],
                    "units": {"left": "usd_mn"},
                    "series": [{"name": "Rev", "unit": "usd_mn",
                                "values": [100, 110, 120, 130, 140, 150, 160, 170, 180]}]})
    wh = _obs.for_chart(whole, "combo_trend")
    check("multi-period change only over a whole number of years",
          "over 7 quarters" not in un and "CAGR" not in un
          and "over 8 quarters" in wh and "CAGR" in wh,
          f"uneven={un!r} whole={wh!r}"[:200])

    check("the estimate hatch is never used to differentiate series",
          theme.ESTIMATE_HATCH not in theme.HATCHES
          and theme.series_hatch(0) is None and theme.series_hatch(1) is None
          and theme.series_hatch(2) is not None,
          f"HATCHES[:3]={theme.HATCHES[:3]} estimate={theme.ESTIMATE_HATCH!r}")

    f_big = _fmt.axis_formatter("num", peak=2_500_000)
    ticks = [f_big(v, 0) for v in (0, 500_000, 1_000_000, 2_000_000, 2_500_000)]
    f_small = _fmt.axis_formatter("num", peak=1000)
    small = [f_small(v, 0) for v in (0, 200, 800, 1000)]
    check("one abbreviation scale per axis, chosen from the peak",
          ticks == ["0", "0.5M", "1M", "2M", "2.5M"] and small == ["0", "200", "800", "1,000"],
          f"{ticks} / {small}")

    one = _build({"chart_type": "metric_trend", "source": "t", "periods": ["2Q26"],
                  "units": {"left": "usd_mn"},
                  "series": [{"name": "Revenue", "unit": "usd_mn", "values": [4820]}]})
    notes = _dq.disclosure(_dq.check(one, ["2Q26"], "combo_trend"))
    check("error-severity findings are disclosed on the chart",
          any("single period" in n for n in notes), str(notes))

    from insync_charts.render import _apply_registry_defaults, _auto_subtitle

    def _v(**kw):
        sp = {"chart_type": "revenue_trend", "source": "t", "periods": ["1Q26", "2Q26"],
              "series": [{"name": "Revenue", "unit": "usd_mn", "values": [100, 110]}]}
        sp.update(kw)
        return sp

    bad_unit = validate(_v(series=[{"name": "Revenue", "unit": "usd_mm", "values": [1, 2]}]))
    check("an unknown unit is refused, not drawn as a plain number",
          not bad_unit["ok"] and "not a known unit" in (bad_unit["error"] or ""), str(bad_unit))

    not_num = _build(_v(series=[{"name": "Revenue", "unit": "usd_mn", "values": ["abc", 2]}]))
    check("a value that is not a number is named in warnings",
          not_num.series[0].values[0] is None
          and any("'abc'" in w and "not a number" in w for w in not_num.warnings),
          str(not_num.warnings))

    inf = _build(_v(series=[{"name": "Revenue", "unit": "usd_mn", "values": [float("inf"), 2]}]))
    check("infinity becomes a flagged gap",
          inf.series[0].values[0] is None and any("not a number" in w for w in inf.warnings),
          str(inf.warnings))

    sfx = _build(_v(series=[{"name": "Revenue", "unit": "usd_mn", "values": ["$1.2B", "950"]}]))
    sfx_abs = _build(_v(series=[{"name": "Revenue", "unit": "usd", "values": ["$1.2B", 2]}]))
    check("'$1.2B' is converted into the series unit",
          sfx.series[0].values == [1200.0, 950.0] and sfx_abs.series[0].values[0] == 1.2e9
          and any("converted" in w for w in sfx.warnings),
          f"{sfx.series[0].values} {sfx_abs.series[0].values}")

    no_periods = validate(_v(periods=[]))
    check("empty periods are refused with a spec error",
          not no_periods["ok"] and "periods" in (no_periods["error"] or ""), str(no_periods))

    no_src = dict(_v()); no_src.pop("source")
    ns = _build(no_src)
    check("a missing source is never printed as InSync MCP",
          ns.source == "Source not stated" and any("source" in w for w in ns.warnings),
          ns.source)

    esc = validate(_v(output={"basename": "../escape"}))
    sub = validate(_v(output={"basename": "sub/inside"}))
    check("a basename may not leave the output folder (sub-folders still allowed)",
          not esc["ok"] and sub["ok"], f"{esc} {sub}")

    long_t = render_chart(_v(title="A deliberately long chart title " * 6,
                             output={"basename": "long_title", "formats": ["png"],
                                     "preset": "thumb"}),
                          os.path.join(tmp, "v190"), strict=False)
    check("a title too long for the size is shortened and warned about",
          long_t.get("ok") and any("shortened" in w for w in long_t.get("warnings", [])),
          str(long_t.get("warnings") or long_t.get("error")))

    one_series = _apply_registry_defaults(_v())
    sub_one = _auto_subtitle(_build(one_series), one_series["_meta"]) or ""
    two_series = _apply_registry_defaults(_v(series=[
        {"name": "Revenue", "unit": "usd_mn", "values": [100, 110]},
        {"name": "YoY growth", "kind": "line", "axis": "right", "unit": "pct",
         "values": [5, 6]}]))
    sub_two = _auto_subtitle(_build(two_series), two_series["_meta"]) or ""
    check("the subtitle mentions growth only when a growth line is drawn",
          "growth" not in sub_one and "growth" in sub_two, f"{sub_one!r} / {sub_two!r}")

    proot = os.path.join(tmp, "v190packs")
    two = [_v(chart_type=t, title=t) for t in ("revenue_trend", "eps_trend")]
    two[1]["series"] = [{"name": "EPS", "unit": "usd_ps", "values": [1.1, 1.2]}]
    build_pack.build(two, proot, name="P", gallery=False, pdf=False)
    rebuilt = build_pack.build(two[:1], proot, name="P", gallery=False, pdf=False)
    left = sorted(os.listdir(os.path.join(proot, "P", "png")))
    clean_ok = build_pack.verify(rebuilt["manifest"])["ok"]
    with open(os.path.join(proot, "P", "png", "stray.png"), "wb") as fh:
        fh.write(b"x")
    stray = build_pack.verify(rebuilt["manifest"])
    check("a pack rebuild drops old exhibits, and --verify fails on an unlisted file",
          len(left) == 1 and clean_ok and not stray["ok"]
          and any("stray.png" in p["name"] for p in stray["problems"]),
          f"{left} {stray['problems']}")

    from insync_charts import fmt as _f
    check("non-US currencies format with their own symbol or code",
          _f.fmt_value(1234, "gbp_mn") == "£1,234" and _f.fmt_value(4.5, "chf_ps") == "CHF 4.50",
          f"{_f.fmt_value(1234, 'gbp_mn')} {_f.fmt_value(4.5, 'chf_ps')}")

    ok = all(r["ok"] for r in results)
    print(json.dumps({"ok": ok,
                      "passed": sum(1 for r in results if r["ok"]),
                      "failed": sum(1 for r in results if not r["ok"]),
                      "output_dir": tmp if args.keep else None,
                      "checks": results}, indent=2))
    if not args.keep:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())


def _masthead_check(pdf_path=None):
    import os, subprocess, sys as _s
    checker = os.path.join(_HERE, "check_masthead.py")
    if not pdf_path:
        return {"check": "masthead geometry", "ok": True,
                "detail": "SKIPPED - pass a rendered pack PDF: python self_check.py <pack.pdf>"}
    if not os.path.exists(checker):
        return {"check": "masthead geometry", "ok": False, "detail": "check_masthead.py missing"}
    r = subprocess.run([_s.executable, checker, pdf_path], capture_output=True, text=True)
    tail = (r.stdout or "").strip().splitlines()[-1:] or [""]
    return {"check": "masthead geometry", "ok": r.returncode == 0, "detail": tail[0]}
