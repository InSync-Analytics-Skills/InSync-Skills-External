#!/usr/bin/env python3
"""One-page QC summary and client-readiness status for an Inflection Monitor note.

    python3 qc_summary.py --computed out/T_inflection_computed.json \
        --lint out/lint.json [--ground-truth out/gt.json] \
        [--changes changes.json] [--limitations limitations.json] \
        [--signed-off-by "Name, date"] --out-dir out/qc

Writes <T>_QC_Summary_<Q>.html, .pdf (when a renderer is available) and .json.

WHAT THE STATUS MEANS, AND WHO SETS IT.

    Hold for Data Validation     a gate failed, the independent recomputation
                                 disagreed, a headline or dive line has an
                                 unverified period, an unresolved unit or a
                                 modelled source, or a reviewer recorded a
                                 material limitation. The note does not go out.
    Client Ready with Disclosures  every automated check passed; the page carries
                                 disclosures a reader needs (calculated lines,
                                 inferred labels, guides not repeated, weak
                                 comparisons) and/or no reviewer has signed off.
    Client Ready                 as above, no reviewer-recorded limitation, and a
                                 named reviewer has signed off (--signed-off-by).

The engine never awards "Client Ready" on its own: the final analyst and
publication review is a human step, and a tool that certifies its own output
has certified nothing.

Inputs other than the computed payload and the lint report are optional:
    changes.json      ["Corrections made in this version", ...]
    limitations.json  [{"text": "...", "material": false}, ...]
"""

import argparse
import html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def esc(x):
    return html.escape(str(x if x is not None else ""))


SOURCE_LABEL = {
    "financial release": "Company-reported (earnings release)",
    "press release": "Company-reported (release)",
    "sec fillings": "Company-reported (SEC filing)",
    "sec filings": "Company-reported (SEC filing)",
    "other filings": "Company-reported (filing)",
    "presentations": "Company presentation",
    "transcripts": "Management commentary (call)",
    "transcript": "Management commentary (call)",
}


def source_class(r):
    if r.get("insync_calculated"):
        return "InSync-Calculated"
    mix = {str(k).strip().lower(): v for k, v in (r.get("source_mix") or {}).items()}
    if not mix:
        return "Source not stated"
    if any(re.search(r"model|consensus|estimate", k) for k in mix):
        return "Modelled (not a reported figure)"
    tot = sum(mix.values()) or 1
    if mix.get("calculated", 0) * 2 >= tot:
        return "Derived from company-reported lines"
    top = max((k for k in mix if k != "calculated"), key=lambda k: mix[k], default=None)
    return SOURCE_LABEL.get(top, "Company-reported")


def displayed_rows(p, held=()):
    seen, out = set(held), []
    for key, where in (("headline_moves", "Key changes"), ("deep_dive", "Drivers"),
                       ("rate_kpis", "Rate KPIs"), ("accelerating", "Accelerating"),
                       ("decelerating", "Decelerating")):
        for r in p.get(key) or []:
            c = r.get("ltcode")
            if not c or c in seen or (r.get("display") or r.get("name")) in seen:
                continue
            seen.add(c)
            out.append((where, r))
    return out


def build(p, lint, gt, changes, limits, signed, held=()):
    holds, disclosures = [], []
    if not lint.get("passed"):
        holds.append("%d gate failure(s): %s" % (len(lint.get("failures") or []),
                                                  "; ".join((lint.get("failures") or [])[:3])))
    if lint.get("pdf_checks_run") is not True:
        disclosures.append("The PDF text checks did not run (no PDF was produced, or pdftotext "
                           "is not installed): gate 104 was skipped, so the PDF has not been "
                           "compared with the page, and the page budget may be unmeasured.")
    if gt is None:
        holds.append("Independent recomputation not run: ground_truth.py was not passed "
                     "(--ground-truth), so no published figure has been re-derived from "
                     "the source data")
    elif gt.get("status") == "error" or gt.get("figures_re_derived") is None:
        holds.append("Independent recomputation failed to run: %s"
                     % (gt.get("message") or "no result"))
    elif (gt.get("disagreements") or 0) > 0:
        holds.append("%d figure(s) do not re-derive from the source data"
                     % gt["disagreements"])
    for key in ("headline_moves", "deep_dive"):
        for r in p.get(key) or []:
            nm = r.get("display") or r.get("name")
            if r.get("period_basis") in ("unverified", "year_to_date"):
                holds.append("%s: period %s on page one" % (nm, r["period_basis"].replace("_", "-")))
            if r.get("unit_status") not in (None, "validated"):
                holds.append("%s: unit %s" % (nm, r.get("unit_status")))
            if source_class(r).startswith("Modelled"):
                holds.append("%s: modelled source, not a reported figure" % nm)
    for L in limits:
        if L.get("material"):
            holds.append("Reviewer: " + str(L.get("text")))

    for r in (p.get("headline_moves") or []) + (p.get("deep_dive") or []):
        c = r.get("calculated_twin")
        if c and c.get("latest_display"):
            disclosures.append("%s is scored on the company-reported figure; %s (%s) is shown "
                               "beside it, up to %s bps apart over the window."
                               % (r.get("display"), c.get("display"), c.get("latest_display"),
                                  c.get("gap_bps_max")))
    calc = sorted({r.get("display") for _, r in displayed_rows(p, held) if r.get("insync_calculated")})
    if calc:
        disclosures.append("InSync-Calculated lines on the page, labelled as such: "
                           + ", ".join(calc) + ".")
    for e in p.get("label_evidence") or []:
        disclosures.append("Label: " + str(e.get("text")) + ".")
    seen = set()
    for key in ("deep_dive", "headline_moves"):
        for r in p.get(key) or []:
            yc = r.get("year_ago_comparison") or {}
            if yc.get("two_year_change_display") and r.get("ltcode") not in seen:
                seen.add(r.get("ltcode"))
                disclosures.append("%s: %s year-ago comparison (%s was %s y/y); against %s it is %s."
                                   % (r.get("display"), yc["which"], yc["year_ago_period"],
                                      yc["year_ago_growth_display"], yc["two_year_period"],
                                      yc["two_year_change_display"]))
    gc = p.get("guidance_check") or {}
    lq = str(p.get("latest_quarter") or "").upper()
    stale = sorted({o.get("display") for o in gc.get("other_horizon") or []
                    if o.get("source_period") and str(o["source_period"]).upper() != lq})
    if stale:
        disclosures.append("Guides not repeated with %s, shown with their issue quarter: %s."
                           % (lq, ", ".join(stale[:6]) + (" and others" if len(stale) > 6 else "")))
    worded = [r for r in gc.get("revisions") or [] if r.get("worded")]
    if worded:
        disclosures.append("Guide changes given in words, quoted as given: "
                           + ", ".join(sorted({r.get("display") for r in worded})) + ".")
    if not p.get("commentary_supplied"):
        disclosures.append("Management commentary was not retrieved for this run, so one-time, "
                           "policy and acquisition items inside the moves were not checked "
                           "(SKILL.md step 4b).")
    for e in (p.get("adjustment_quality") or []):
        disclosures.append("Adjustment quality: %s %s y/y includes %s from %s (%s, per %s %s %s)."
                           % (e.get("display"), e.get("move_display") or "", e.get("size_display"),
                              e.get("label"), e.get("kind"), e.get("speaker") or "management",
                              e.get("quarter") or "", e.get("document") or ""))
    for o in p.get("one_time_items") or []:
        disclosures.append("One-time item: %s %s%s." % (o.get("display"), o.get("latest_display"),
                           (", %s bps of revenue" % o["revenue_bps"]) if o.get("revenue_bps") is not None else ""))
    for it in p.get("non_operating_window") or []:
        disclosures.append("Comparison quarter %s (%s) included %s of %s (%.1f%% of that quarter's "
                           "operating profit); lines below operating profit are measured against it."
                           % (it.get("period"), it.get("role"), it.get("value_display"),
                              it.get("display"), float(it.get("share_of_operating_profit_pct") or 0)))
    spl = [e for e in p.get("excluded") or [] if "changed what it measures" in str(e.get("reason"))]
    for e in spl:
        disclosures.append("Held out: %s changed what it measures within the window." % e.get("name"))
    for L in limits:
        if not L.get("material"):
            disclosures.append("Reviewer: " + str(L.get("text")))
    _up = [u for u in (p.get("unresolved_polarity") or []) if u.get("displayed")]
    if _up:
        disclosures.append("No polarity rule declares which way these displayed lines are "
                           "better, so each reads Ambiguous: "
                           + ", ".join(sorted({str(u.get("name")) for u in _up})) + ".")
    for w in p.get("warnings") or []:
        disclosures.append("Engine warning: " + str(w.get("message")))

    groups = {}
    _names = {}
    for _k in ("excluded", "accelerating", "decelerating", "primary_context", "headline_moves",
               "rate_kpis"):
        for _r in p.get(_k) or []:
            if _r.get("ltcode"):
                _names.setdefault(_r["ltcode"], _r.get("display") or _r.get("name"))
    for e in p.get("excluded") or []:
        rs = str(e.get("reason") or "not scored")
        rs = re.split(r"[:;]", re.sub(r"\s*\([^)]*\)", "", rs))[0].strip()
        rs = re.split(r",\s*so\b", rs)[0].strip()
        rs = re.sub(r"\b[A-Z]{2}_[A-Z]_\d+(?:-[A-Z]+)?\b",
                    lambda m: str(_names.get(m.group(0)) or "a reported line"), rs)
        groups.setdefault(rs, []).append(e.get("name"))
    excl = sorted(groups.items(), key=lambda kv: -len(kv[1]))

    if holds:
        status = "Hold for Data Validation"
    elif signed and not any(not L.get("material") for L in limits) \
            and lint.get("pdf_checks_run") is True:
        status = "Client Ready"
    else:
        status = "Client Ready with Disclosures"
    return {"status": status, "holds": holds, "disclosures": disclosures,
            "exclusions": [{"reason": k, "count": len(v), "examples": v[:4]} for k, v in excl],
            "sources": [{"where": w, "metric": r.get("display") or r.get("name"),
                         "source": source_class(r)} for w, r in displayed_rows(p, held)],
            "changes": changes, "signed_off_by": signed,
            "gates_run": lint.get("checks_run"), "gates_passed": bool(lint.get("passed")),
            "figures_re_derived": (gt or {}).get("figures_re_derived"),
            "recompute_disagreements": (gt or {}).get("disagreements"),
            "recompute_ran": gt is not None and gt.get("figures_re_derived") is not None,
            "unresolved_polarity": p.get("unresolved_polarity"),
            "engine_version": p.get("engine_version")}


CSS = """
body{font-family:Georgia,'Times New Roman',serif;color:#222;margin:0;font-size:10px;}
.page{width:7.6in;margin:0.35in auto;}
h1{font-size:15px;margin:0 0 2px;} .sub{color:#666;font-size:9.5px;margin-bottom:6px;}
.st{display:inline-block;padding:3px 8px;font-weight:bold;font-size:11px;border-radius:3px;
    background:#1A1A1A;color:#fff;margin:4px 0 6px;}
.st.hold{background:#B3261E;} .st.disc{background:#E8850C;} .st.ready{background:#1E7B34;}
h2{font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;color:#E8850C;
   border-bottom:1px solid #ddd;margin:8px 0 3px;padding-bottom:1px;}
ul{margin:2px 0 2px 16px;padding:0;} li{margin:1px 0;line-height:1.3;}
table{border-collapse:collapse;width:100%;font-size:9px;} td,th{border-bottom:1px solid #eee;
   padding:1px 4px;text-align:left;vertical-align:top;} th{color:#666;font-weight:normal;}
.two{display:grid;grid-template-columns:1fr 1fr;column-gap:14px;}
.small{font-size:8.5px;color:#666;}
"""


def to_html(p, q):
    cls = {"Hold for Data Validation": "hold", "Client Ready with Disclosures": "disc",
           "Client Ready": "ready"}[q["status"]]
    li = lambda xs: "<ul>" + "".join("<li>" + esc(x) + "</li>" for x in xs) + "</ul>" if xs else \
        '<p class="small">None.</p>'
    src_rows = "".join("<tr><td>" + esc(s["metric"]) + "</td><td>" + esc(s["source"])
                       + "</td><td>" + esc(s["where"]) + "</td></tr>" for s in q["sources"])
    ex_rows = "".join("<tr><td>" + esc(e["reason"]) + "</td><td>" + esc(e["count"]) + "</td><td>"
                      + esc(", ".join(str(x) for x in e["examples"])) + "</td></tr>"
                      for e in q["exclusions"][:8])
    checks = ("%s of %s gates %s" % (q["gates_run"], q["gates_run"],
                                     "passed" if q["gates_passed"] else "run; failures listed above"))
    if q.get("figures_re_derived") is not None:
        checks += "; %s figures re-derived independently, %s disagreements" % (
            q["figures_re_derived"], q["recompute_disagreements"])
    return ("<!doctype html><html><head><meta charset='utf-8'><title>QC Summary</title><style>"
            + CSS + "</style></head><body><div class='page'>"
            "<h1>QC Summary: " + esc(p.get("company")) + " (" + esc(p.get("ticker")) + ") Inflection Monitor "
            + esc(p.get("latest_quarter")) + "</h1>"
            "<div class='sub'>Prepared " + esc(str(p.get("generated_utc", ""))[:10])
            + ". Automated checks: " + esc(checks) + ". The gates test that every figure traces "
            "to the source and is labelled, placed and worded consistently; they do not judge "
            "economic meaning, which is what the reviewer limitations below record.</div>"
            "<div class='st " + cls + "'>Status: " + esc(q["status"]) + "</div>"
            + ("<div class='small'>Signed off by " + esc(q["signed_off_by"]) + ".</div>" if q["signed_off_by"] else
               "<div class='small'>Not yet signed off by a reviewer; \"Client Ready\" requires a named "
               "sign-off after the final analyst and publication review.</div>")
            + ("<h2>Hold reasons</h2>" + li(q["holds"]) if q["holds"] else "")
            + "<h2>Corrections made in this version</h2>" + li(q["changes"])
            + "<h2>Remaining limitations and disclosures</h2>" + li(q["disclosures"])
            + "<h2>Company-reported versus InSync-calculated, as displayed</h2>"
            "<table><tr><th>Metric</th><th>Basis</th><th>Where</th></tr>" + src_rows + "</table>"
            "<h2>Metrics excluded from scoring</h2>"
            "<table><tr><th>Reason</th><th>Lines</th><th>Examples</th></tr>" + ex_rows + "</table>"
            "</div></body></html>")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--computed", required=True)
    ap.add_argument("--lint", required=True)
    ap.add_argument("--ground-truth")
    ap.add_argument("--changes")
    ap.add_argument("--limitations")
    ap.add_argument("--signed-off-by")
    ap.add_argument("--render-json", help="render.py's run summary; rows it held out for the "
                                          "page budget are not listed as displayed")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--no-pdf", action="store_true")
    a = ap.parse_args()
    p = json.load(open(a.computed, encoding="utf-8"))
    lint = json.load(open(a.lint, encoding="utf-8"))
    gt = json.load(open(a.ground_truth, encoding="utf-8")) if a.ground_truth else None
    changes = json.load(open(a.changes, encoding="utf-8")) if a.changes else []
    limits = json.load(open(a.limitations, encoding="utf-8")) if a.limitations else []
    if not isinstance(limits, list):
        raise SystemExit("--limitations must be a JSON list of {\"text\": ..., \"material\": "
                         "true|false} objects")
    _lim = []
    for L in limits:
        if isinstance(L, str):
            _lim.append({"text": L, "material": False})
        elif isinstance(L, dict) and "text" in L:
            _lim.append(L)
        else:
            raise SystemExit("--limitations entry %r is not {\"text\": ..., \"material\": "
                             "true|false}" % (L,))
    limits = _lim
    held = ()
    if a.render_json and os.path.exists(a.render_json):
        try:
            _rj = json.load(open(a.render_json, encoding="utf-8"))
            held = tuple(_rj.get("rows_held_out") or ())
            if _rj.get("page_budget_breach"):
                limits = list(limits) + [{"text": "Page budget: " + str(_rj["page_budget_breach"]),
                                          "material": False}]
        except Exception:
            held = ()
    q = build(p, lint, gt, changes, limits, a.signed_off_by, held)
    _seen, _d = set(), []
    for x in q["disclosures"]:
        if x not in _seen:
            _seen.add(x)
            _d.append(x)
    q["disclosures"] = _d
    os.makedirs(a.out_dir, exist_ok=True)
    base = os.path.join(a.out_dir, "%s_QC_Summary_%s" % (re.sub(r"\W+", "_", str(p.get("ticker"))),
                                                         p.get("latest_quarter")))
    open(base + ".html", "w", encoding="utf-8").write(to_html(p, q))
    json.dump(q, open(base + ".json", "w", encoding="utf-8"), indent=1)
    if not a.no_pdf:
        sys.path.insert(0, HERE)
        import render as _rd
        _rd.to_pdf(base + ".html", base + ".pdf")
        _cnt = getattr(_rd, "page_count", None)
        for _fs in ("9px", "8.4px"):
            try:
                _n = _cnt(base + ".pdf") if _cnt else 1
            except Exception:
                _n = 1
            if not _n or _n <= 1:
                break
            _h = open(base + ".html", encoding="utf-8").read().replace(
                "font-size:10px;}", "font-size:%s;} li{line-height:1.2 !important;}" % _fs, 1)
            open(base + ".html", "w", encoding="utf-8").write(_h)
            _rd.to_pdf(base + ".html", base + ".pdf")
    print(json.dumps({"status": q["status"], "holds": len(q["holds"]),
                      "disclosures": len(q["disclosures"]), "out": base}))


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _contract import run as _run_contract
    _run_contract(main, 'qc_summary')
