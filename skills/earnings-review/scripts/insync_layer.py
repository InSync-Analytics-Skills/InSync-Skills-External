"""Earnings Review - insync_layer.py
Everything the review takes from InSync estimates and tagging, from saved raw payloads, with no analytical arithmetic:
  - BASIS LOCK: one line item (LTCODE) per metric for actual, consensus, guide, forward pre and post (audit classes 5, 15)
  - pre-print consensus for the printed period (latest dated snapshot on or before the cut-off)
  - the consensus PROBE audit trail, where an absence counts only if a successful probe covered that code and period (class 27)
  - forward consensus (next period, FY0, FY1) before and after the print, pre and post on the SAME code
  - full-year consensus snapshots, the KPI universe and Primary KPI pick, valuation versus own history
  - the reported-quarter guard: not reported (exit 2) and a LATER period already reported (exit 3, class 18)
InSync is the ONLY consensus source. Used by build_dataset.py and refresh_insync.py."""
import json, os, glob, re
from erlib import parse_value, fix_suffix, payload_rows, classify, currency_of, load_json
import periods as PER

TAG_RANK = ["driver_kpi", "earnings_flash_kpi", "company_kpi", "industry_kpi"]
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

def rows_of(files, issues=None):
    out = []
    for f in files:
        for r in payload_rows(load_json(f), os.path.basename(f), issues):
            out.append(dict(r, _file=os.path.basename(f)))
    return out
def load(RAW, pattern, issues=None): return rows_of(sorted(glob.glob(os.path.join(RAW, pattern))), issues)
def pk(p): return PER.norm_period(p)
def codes_of(spec): return [c for c in (spec["ltcode"] if isinstance(spec["ltcode"], list) else [spec["ltcode"]]) if c and not str(c).startswith("_")]
def _d(x): return str(x or "")[:10]

def best_row(rows, codes, date_cap=None, undated=None):
    for cd in codes:
        c = []
        for r in rows:
            if r.get("LTCODE") != cd: continue
            ud = _d(r.get("UPDATEDATE"))
            if not DATE.match(ud):
                if undated is not None: undated.append(r)
                continue
            if date_cap and ud > _d(date_cap): continue
            c.append(r)
        if c: return max(c, key=lambda r: (_d(r.get("UPDATEDATE")), int(r.get("BROKERCOUNT") or 0)))
    return None

def cons_entry(spec, r, source, warn=None):
    cnt = int(r.get("BROKERCOUNT") or 0); u = spec["unit"]; raw = fix_suffix(r.get("VALUE"), spec)
    v = parse_value(raw, u)
    if v is None:
        if warn is not None and classify(raw) != "blank": warn(f"could not be read: estimate {r.get('LTCODE')} {pk(r.get('FISCALPERIOD'))} VALUE {r.get('VALUE')!r}")
        return None
    if spec.get("sign", 1) == -1: v = abs(v)
    e = {"mean": v, "count": cnt, "source": source, "thin": cnt <= 2, "asof": _d(r.get("UPDATEDATE")), "code": r.get("LTCODE"), "currency": currency_of(raw)}
    lo, hi = parse_value(fix_suffix(r.get("MIN"), spec), u), parse_value(fix_suffix(r.get("MAX"), spec), u)
    if spec.get("sign", 1) == -1 and lo is not None and hi is not None: lo, hi = abs(lo), abs(hi)
    if lo is not None and hi is not None: e["low"], e["high"] = min(lo, hi), max(lo, hi)
    return e

def load_actuals(RAW, issues=None, notes=None):
    rows = load(RAW, "insync_actuals*.json", issues); by, rev = {}, {}
    for r in rows:
        c, fp = r.get("LTCODE"), str(r.get("FISCALPERIOD", "")); p = pk(fp); n = PER.revision_of(fp)
        cur = by.setdefault(c, {}); old = rev.get((c, p))
        if old is None or n > old:
            if old is not None and notes is not None and cur[p] != r.get("VALUE"):
                notes.append(f"{c} {p}: revision {fp} {r.get('VALUE')} replaces {cur[p]} (check basis)")
            cur[p] = r.get("VALUE"); rev[(c, p)] = n
        elif n < old and notes is not None and cur[p] != r.get("VALUE"):
            notes.append(f"{c} {p}: earlier revision {fp} {r.get('VALUE')} ignored, {cur[p]} kept (check basis)")
    return rows, by

def resolve_basis(spec, act_by, pre_q, period, frozen):
    codes = codes_of(spec)
    has_act = [c for c in codes if classify(act_by.get(c, {}).get(period)) == "ok"]
    has_cons = [c for c in codes if best_row(pre_q, [c], frozen)]
    both = [c for c in codes if c in has_act and c in has_cons]
    if both: return both[0], None
    if has_act:
        note = (f"consensus exists only on {has_cons[0]}, a different line item from the actual ({has_act[0]}): not graded") if has_cons else None
        return has_act[0], note
    any_act = [c for c in codes if any(classify(v) == "ok" for v in act_by.get(c, {}).values())]
    return (any_act or has_cons or codes or [None])[0], None

def probe_coverage(RAW, period):
    cov = {"wide": {}, "latest": {}, "files": []}
    for kind in ("wide", "latest"):
        for f in sorted(glob.glob(os.path.join(RAW, f"insync_probe_{kind}*.json"))):
            fn = os.path.basename(f); cov["files"].append(fn); j = load_json(f)
            req = j.get("request") if isinstance(j, dict) else None; resp = j.get("response", j) if isinstance(j, dict) else j
            iss = []; payload_rows(resp, fn, iss)
            if any(i[0] == "error" for i in iss): continue
            if req:
                a = req.get("tool_arguments", req)
                codes = [c.strip() for c in str(a.get("ltcode", "")).split(",") if c.strip()]
                pers = [pk(p.strip()) for p in str(a.get("fiscal_period", "")).split(",") if p.strip()]
                trap = isinstance(a.get("latest"), dict) and a["latest"].get("enabled") is True and bool(a.get("updatedate_start") or a.get("updatedate_end"))
                if trap or period not in pers: continue
                shape = f"{fn}: get_estimates ltcode={a.get('ltcode')} fiscal_period={a.get('fiscal_period')} latest={json.dumps(a.get('latest'))} window={a.get('updatedate_start', 'default')}..{a.get('updatedate_end', 'default')}"
                for c in codes: cov[kind][c] = shape
            for i in iss:
                if i[0] != "notes": continue
                for grp in re.findall(r"LTcode\(s\)\s+([A-Z0-9_\-, ]+?)\s+returned no results", i[2]):
                    for c in [x.strip() for x in grp.split(",") if x.strip()]: cov[kind].setdefault(c, f"{fn}: vendor note 'no results' for {c}")
    return cov

def is_forward(mid_, spec):
    if spec.get("forward") is False: return False
    return bool(spec.get("forward") or spec.get("consensus_metric") or mid_ in ("revenue", "eps"))
def fkey(mid_, spec):
    if spec.get("forward_key"): return spec["forward_key"]
    fm = (spec.get("consensus_metric") or "").upper()
    if fm == "EPS" or mid_ == "eps": return "eps"
    if fm == "SALES" or mid_ == "revenue": return "rev"
    return fm.lower() if fm else mid_

def build(RAW, mm, period, nxt, fy_period, frozen, fy_lab, fy_next_lab, missing, act_by=None, warn=None, issues=None):
    L = {"resolved": {}, "basis_conflict": {}}
    warn = warn or (lambda m: missing.append(m)); issues = issues if issues is not None else []
    act_by = act_by if act_by is not None else load_actuals(RAW, issues)[1]
    undated = []
    pre_all = load(RAW, "insync_estimates_pre*.json", issues) + load(RAW, "insync_probe_wide*.json")
    post_all = load(RAW, "insync_estimates_post*.json", issues)
    latest_probe = load(RAW, "insync_probe_latest*.json")
    cov = probe_coverage(RAW, period)
    pre_q = [r for r in pre_all if pk(r.get("FISCALPERIOD")) == period]
    cons, probe = {}, []
    for mid_, spec in mm.items():
        codes = codes_of(spec)
        code, conflict = resolve_basis(spec, act_by, pre_q, period, frozen)
        L["resolved"][mid_] = code
        r = best_row(pre_q, [code], frozen, undated) if code else None
        e = cons_entry(spec, r, "InSync", warn) if r else None
        if conflict:
            L["basis_conflict"][mid_] = conflict; warn(f"BASIS: {mid_}: {conflict}")
            probe.append({"id": mid_, "codes": codes, "found": None, "evidence": conflict, "basis_conflict": True}); continue
        if e:
            cons[mid_] = e
            probe.append({"id": mid_, "codes": codes, "found": e["mean"], "count": e["count"], "asof": e["asof"], "code": r.get("LTCODE"), "file": r.get("_file")})
            continue
        ev = None
        if not codes: ev = "no InSync line item (document-only metric)"
        elif all(c in cov["wide"] for c in codes):
            lat = [x for x in latest_probe if x.get("LTCODE") in codes and pk(x.get("FISCALPERIOD")) == period]
            lat_note = "; post-print rows only" if lat else ("; latest also empty" if all(c in cov["latest"] for c in codes) else "; latest probe did not cover this code")
            ev = f"no pre-print rows for {','.join(codes)} {period} on or before {frozen}; probe: {cov['wide'][codes[0]]}{lat_note}"
        probe.append({"id": mid_, "codes": codes, "found": None, "evidence": ev})
    for r in undated:
        if pk(r.get("FISCALPERIOD")) == period: warn(f"estimate row without UPDATEDATE ignored (cannot prove it is pre-print): {r.get('LTCODE')} {pk(r.get('FISCALPERIOD'))} {r.get('VALUE')} in {r.get('_file')}")
    L["consensus"] = cons; L["probe"] = {"asof": frozen, "period": period, "probed": probe}
    post_q = [r for r in post_all if pk(r.get("FISCALPERIOD")) == nxt]
    L["consensus_next"] = {}
    for m, s in mm.items():
        r = best_row(post_q, [L["resolved"][m]]) if L["resolved"][m] else None
        e = cons_entry(s, r, "InSync post-print", warn) if r else None
        if e: L["consensus_next"][m] = e
    L["consensus_fy"], L["consensus_fy_next"] = {}, {}
    if fy_period:
        fyp = [r for r in pre_all if pk(r.get("FISCALPERIOD")) == fy_period]; fyq = [r for r in post_all if pk(r.get("FISCALPERIOD")) == fy_period]
        for m, s in mm.items():
            c = L["resolved"][m]
            if not c: continue
            r = best_row(fyp, [c], frozen)
            e_ = cons_entry(s, r, "InSync", warn) if r else None
            if e_: L["consensus_fy"][m] = e_
            r = best_row(fyq, [c])
            e_ = cons_entry(s, r, "InSync post-print", warn) if r else None
            if e_: L["consensus_fy_next"][m] = e_
    fwd = {}
    fy1 = str(int(fy_period) + 1) if fy_period else None
    for m, s in mm.items():
        if not is_forward(m, s): continue
        pfx = fkey(m, s); nm = {"eps": "EPS", "rev": "revenue"}.get(pfx, s["label"])
        ffmt = "usd3" if pfx == "eps" else ("bn" if pfx == "rev" else s["fmt"])
        cand = [L["resolved"][m]] + [c for c in codes_of(s) if c != L["resolved"][m]]
        for per, sfx, lab in ((nxt, "next_q", nxt), (fy_period, "fy0", fy_lab), (fy1, "fy1", fy_next_lab)):
            if not per: continue
            rows_pre = [r for r in pre_all if pk(r.get("FISCALPERIOD")) == per]; rows_post = [r for r in post_all if pk(r.get("FISCALPERIOD")) == per]
            pick = None
            for c in cand:
                pr, po = best_row(rows_pre, [c], frozen), best_row(rows_post, [c])
                if pr and po: pick = (c, pr, po); break
            if pick:
                c, pr, po = pick; a_, b_ = cons_entry(s, pr, "InSync", warn), cons_entry(s, po, "InSync post-print", warn)
                if not (a_ and b_): continue
                if c != L["resolved"][m]: warn(f"forward {pfx}_{sfx}: shown on {c}, not the graded line item {L['resolved'][m]} (no common pre/post rows on the graded code)")
                fwd[f"{pfx}_{sfx}"] = {"label": f"{lab} {nm}", "pre": a_["mean"], "pre_count": a_["count"], "pre_asof": a_["asof"], "post": b_["mean"],
                                       "post_count": b_["count"], "post_asof": b_["asof"], "post_down": None, "fmt": ffmt, "metric": m, "source": "InSync", "pre_code": c, "post_code": c}
            else:
                pre_c = next((c for c in cand if best_row(rows_pre, [c], frozen)), None); post_c = next((c for c in cand if best_row(rows_post, [c])), None)
                if pre_c or post_c:
                    warn(f"forward {pfx}_{sfx} not comparable: pre-print on {pre_c or 'none'}, post-print on {post_c or 'none'} for {per} (different or missing line items): not shown")
    L["forward"] = fwd
    L["kpi_universe"] = kpi_universe(RAW)
    L["valuation_history"] = valuation_history(RAW, warn)
    L["probe_coverage"] = cov
    return L

def _kpi_rows(files):
    out = []
    for f in files:
        st = [load_json(f)]
        while st:
            x = st.pop()
            if isinstance(x, dict):
                if "ltcode" in x and "name" in x: out.append({"name": x["name"], "ltcode": x["ltcode"], "tags": x.get("tags", "")})
                else: st += list(x.values())[::-1]
            elif isinstance(x, list): st += x[::-1]
    return out
def kpi_universe(RAW):
    t1 = _kpi_rows(sorted(glob.glob(os.path.join(RAW, "insync_search_lineitem_topkpi*.json"))))
    if t1: rows, prov = t1, "mcp_top_kpi"
    else:
        t2 = [r for r in _kpi_rows(sorted(glob.glob(os.path.join(RAW, "insync_search_lineitem_kpi_tiers*.json")))) if any(t in r["tags"] for t in TAG_RANK + ["top_kpi"])]
        if t2: rows, prov = t2, "mcp_kpi_tier"
        else:
            t3 = _kpi_rows(sorted(glob.glob(os.path.join(RAW, "insync_kpi_discovery*.json"))))
            rows, prov = t3, ("mcp_discovery" if t3 else None)
    seen, uniq = set(), []
    for r in rows:
        if r["ltcode"] not in seen: seen.add(r["ltcode"]); uniq.append(r)
    return {"provenance": prov, "kpis": uniq, "primary_pick": pick_primary(uniq)}
def pick_primary(kpis):
    for t in TAG_RANK:
        c = [(j, r) for j, r in enumerate(kpis) if t in str(r.get("tags", "")).split("|")]
        if c:
            j, r = max(c, key=lambda x: (len(str(x[1].get("tags", "")).split("|")), -x[0]))
            return {"name": r["name"], "ltcode": r["ltcode"], "tag": t}
    return None

def valuation_history(RAW, warn=None):
    if warn:
        for r in load(RAW, "insync_valuation_ltm*.json"):
            for k in ("value", "mean"):
                if r.get(k) not in (None, "") and not isinstance(r.get(k), (int, float)):
                    warn(f"could not be read: valuation {r.get('metric') or r.get('METRIC')} {k} {r.get(k)!r}")
    def by_metric(rows):
        o = {}
        for r in rows:
            m = r.get("metric") or r.get("METRIC")
            if m: o.setdefault(m, []).append(r)
        return o
    cur = by_metric(load(RAW, "insync_valuation_ltm.json")); h1 = by_metric(load(RAW, "insync_valuation_ltm_1y.json")); h3 = by_metric(load(RAW, "insync_valuation_ltm_3y.json"))
    def last(rs):
        rs = [r for r in rs if isinstance(r.get("value"), (int, float))]
        return max(rs, key=lambda r: r.get("DATE") or "")["value"] if rs else None
    def summ(rs):
        if not rs: return None
        r = rs[0]; o = {}
        for k, v in r.items():
            kl = k.lower()
            if not isinstance(v, (int, float)): continue
            if kl in ("avg", "average", "mean", "value_avg", "avg_value"): o["avg"] = v
            elif kl in ("min", "minimum", "value_min", "min_value", "low"): o["min"] = v
            elif kl in ("max", "maximum", "value_max", "max_value", "high"): o["max"] = v
            elif kl == "median": o["median"] = v
            elif kl in ("count", "n", "observations", "row_count", "data_points"): o["n"] = v
        return o or None
    out = {}
    for m in sorted(set(cur) | set(h1) | set(h3)):
        out[m] = {"ltm": last(cur.get(m, [])), "y1": summ(h1.get(m)), "y3": summ(h3.get(m))}
    return out or None

def payload_errors(RAW, tid):
    errs, foreign, issues = [], {}, []
    for f in sorted(glob.glob(os.path.join(RAW, "insync_*.json"))):
        fn = os.path.basename(f)
        if fn.startswith(("insync_search_tickerid", "insync_search_lineitem", "insync_prices")): continue
        for r in payload_rows(load_json(f)):
            t = r.get("TICKER_ID")
            if t is not None and str(t) != str(tid): foreign[fn] = foreign.get(fn, 0) + 1
    for pat in ("insync_actuals*.json", "insync_estimates_pre*.json", "insync_estimates_post*.json", "insync_guidance*.json", "insync_valuation*.json", "insync_call_date*.json"):
        for f in sorted(glob.glob(os.path.join(RAW, pat))): payload_rows(load_json(f), os.path.basename(f), issues)
    for f in sorted(glob.glob(os.path.join(RAW, "insync_probe_*.json"))):
        pi = []; payload_rows(load_json(f), os.path.basename(f), pi)
        issues += [i for i in pi if i[0] == "has_more"]
    for i in issues:
        if i[0] == "has_more": errs.append(f"{i[1]}: has_more / remaining_items says the response is truncated: page with offset until has_more is false")
        if i[0] == "error": errs.append(f"{i[1]}: the saved response is an error ({i[2]}); refetch")
        if i[0] == "notes" and any(w in i[2].lower() for w in ("convert", "conversion", "redenominat", "exchange rate")):
            errs.append(f"{i[1]}: the vendor converted currencies ({i[2][:120]}); refetch without the foreign-currency line so actuals and estimates share one basis")
    return errs, foreign

def call_dates(RAW):
    out = []
    for r in load(RAW, "insync_call_date*.json"):
        fp = pk(r.get("FISCALPERIOD") or r.get("fiscal_period"))
        ds = [str(v)[:10] for k, v in sorted(r.items(), key=lambda kv: kv[0] != "CALL_DATE") if "date" in k.lower() and isinstance(v, str) and DATE.match(str(v)[:10])]
        if ds: out.append((fp if fp and PER.sort_key(fp) not in (None, (0, 0)) else None, ds[0]))
    return out
def _days(a, b):
    import datetime as _dt
    return (_dt.date.fromisoformat(b) - _dt.date.fromisoformat(a)).days

def reported_check(RAW, act_rows, period, headline_codes, today, confirm_stale=""):
    has = any(pk(r.get("FISCALPERIOD")) == period and r.get("LTCODE") in headline_codes and classify(r.get("VALUE")) == "ok" for r in act_rows)
    cd = call_dates(RAW)
    mine = [d for p, d in cd if p == period]
    if mine and min(mine) > today: return 2, f"{period} call date {min(mine)} is after {today}"
    if not has: return 2, f"no InSync actuals for {period} on the headline lines ({', '.join(headline_codes)})"
    later = sorted({p for p, d in cd if p and PER.is_later(p, period) and d <= today}, key=PER.sort_key)
    seen = [f"{p} on {d}" for p, d in cd if p in later]
    if mine and not later:
        unl = [d for p, d in cd if p is None and d <= today and _days(max(mine), d) > 30]
        if unl: later, seen = ["unlabelled"], [f"a call on {max(unl)} with no readable fiscal period"]
    if later and not confirm_stale:
        return 3, f"a later period has already reported ({', '.join(seen)}); the database may lag. Ask the user which quarter to review; rerun with --confirm-stale \"<reason>\" only if they chose {period}"
    return 0, ""
