"""Earnings Review - validate_review.py: zero-error gate. Exit 1 on any ERROR.
2.0: re-derives what it can from a SECOND source (raw/ payloads, the search_tickerid payload, the polarity registry) instead of
re-asking the producer (class 21); checks the pre-flight inventory (class 22); scans EVERY prose string for hand-typed figures
(class 32) and banned wording (class 34); and fails, not warns, when the HTML or PDF it should check is missing or stale (class 4).
It prints NOTE lines for assumptions it shares with the producer and cannot test.
Usage: python validate_review.py <workdir> [--no-pdf]      (--no-pdf: PDF checks skipped on purpose, stated in the output)"""
import json, sys, os, re, glob, html as _h, datetime as _dt
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from erlib import parse_value, fix_suffix, payload_rows, load_json, crash_envelope
import periods as PER, polarity as POL
crash_envelope("validate_review")

W = next((a for a in sys.argv[1:] if not a.startswith("--")), "."); NOPDF = "--no-pdf" in sys.argv
E, Wn, NOTE = [], [], []
def err(m): E.append(m)
def warn(m): Wn.append(m)
def note(m): NOTE.append(m)
def J(f, default=None):
    p = os.path.join(W, f)
    if not os.path.exists(p):
        if default is None: err(f"{f} is missing"); return {}
        return default
    try: return json.load(open(p, encoding="utf-8"))
    except Exception as e: err(f"{f} is not valid JSON: {e}"); return {}
d = J("data.json"); cfg = J("review_config.json"); pr = J("prose.json"); cj = J("computed.json", {})
if E:
    for e in E: print("ERROR", e)
    print(f"validate_review: {len(E)} error(s), 0 warning(s)"); sys.exit(1)
M = d.get("metrics", {}); CO = d.get("company", {}); PD = d.get("period", {}); T = cj.get("tokens", {}); CM = cj.get("metrics", {})
tk = f"{CO.get('ticker')}_{PD.get('label')}_Earnings_Review"
html_path = os.path.join(W, tk + ".html"); pdf_path = os.path.join(W, tk + ".pdf")
SC_ROWS = [r for b in cfg.get("scorecard", {}).get("bands", []) for r in b.get("rows", [])]
EV_ROWS = [r for b in cfg.get("evolution", {}).get("bands", []) for r in b.get("rows", [])]

def check(name, fn):
    try: fn()
    except Exception as e: err(f"check '{name}' could not run: {type(e).__name__}: {e}")

def c_tokens():
    rep = os.path.join(W, "build_report.json")
    if os.path.exists(rep):
        u = json.load(open(rep)).get("unresolved_tokens", [])
        if u: err(f"unresolved prose tokens: {u}")
    else: err("build_report.json missing (build_html not run)")
check("tokens", c_tokens)

def c_config():
    ids = set(M)
    for r in SC_ROWS:
        if r not in ids: err(f"scorecard row '{r}' not in data.metrics")
    for r in EV_ROWS:
        if r["id"] not in ids: err(f"evolution row '{r['id']}' not in data.metrics")
    for r in cfg.get("guidance", {}).get("rows", []):
        if r not in ids: err(f"guidance row '{r}' not in data.metrics")
    for mid_, m in M.items():
        if len(m.get("values") or []) != len(d.get("periods", [])): err(f"metric '{mid_}' has {len(m.get('values') or [])} values for {len(d.get('periods', []))} periods")
        if m.get("base") and len(m["base"]) != len(d.get("base_periods", [])): err(f"metric '{mid_}' base length mismatch")
        c2 = m.get("consensus")
        if c2 and c2.get("source") is None: err(f"metric '{mid_}' consensus has no source")
        if c2 and c2.get("thin") and (c2.get("count") or 0) > 2: warn(f"metric '{mid_}' marked thin with count {c2['count']}")
check("config", c_config)

JUDGE = ["clearly", "impressive", "disappointing", "stunning", "ugly", "great", "terrible", "worrying", "encouraging"]
def c_shape():
    for key, lo, hi, lab in (("summary", 3, 5, "Analytical Summary bullets"), ("what_changed", 3, 5, "What Changed rows"), ("monitor", 4, 7, "What to Monitor items"), ("the_print", 3, 5, "THE PRINT bullets")):
        if not (lo <= len(pr.get(key, [])) <= hi): err(f"{lab}: {len(pr.get(key, []))}; must be {lo}-{hi}")
    for k, x in enumerate(pr.get("summary", [])):
        sents = [s for s in re.split(r"(?<=[.!?])\s+", x["text"].strip()) if s]
        if len(sents) > 3: err(f"summary bullet {k+1} has {len(sents)} sentences; max 3")
        for w in JUDGE:
            if re.search(rf"\b{w}\b", x["lead"].lower()): err(f"summary lead-in {k+1} carries judgement word '{w}'; lead-ins must be factual")
    for r in SC_ROWS:
        if not pr.get("scorecard_comments", {}).get(r): err(f"scorecard row '{r}' has no comment")
    if any(re.search(r"\b(REPORTED|MANAGEMENT|CALCULATED|VIEW|MONITOR)\b\s", x["text"]) for x in pr.get("summary", [])): err("inline epistemic tags found in summary; separation must be by language, not labels")
    if any("Monitor:" in x.get("view", "") for x in pr.get("what_changed", [])): err("'Monitor:' clause inside What Changed; monitoring points belong in What to Monitor")
check("shape", c_shape)

QUOTES = [re.sub(r"\s+", " ", str(q.get("text", ""))).strip().lower() for q in (d.get("quotes") or {}).values()]
NUMPAT = re.compile(r"\$\s?\d|[€£¥]\s?\d|\d\s?%|\d\.\d|\d,\d{3}|\d+\s?(?:bps?|basis points?|percent(?:age)?|per cent|cents?|billion|million|thousand|bn|mn|mm|tn|pp|ppts?|pts?|points?)\b|\b\d+(?:\.\d+)?x\b|\b\d{2,}\b", re.I)
MONTH = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
_FY = int(re.sub(r"\D", "", str(PD.get("fy_label") or "")) or 0); _FY = (2000 + _FY if _FY < 100 else _FY) or _dt.date.today().year
_NOTFIG = r"(?!\s?(?:%|\.\d|,\d|x\b|bps?\b|bn\b|mn\b|pp\b|points?\b|percent|per cent|basis))"
_DAY = r"(?:3[01]|[12]\d|0?[1-9])"
_YEARS = "|".join(str(y) for y in range(_FY - 12, _FY + 7))
ALLOW = [rf"\b{_DAY}\s+{MONTH}\b(?:\s+\d{{4}})?{_NOTFIG}", rf"\b{MONTH}\s+{_DAY}\b(?:,?\s+\d{{4}})?{_NOTFIG}", r"\b\d{4}-\d{2}-\d{2}\b", rf"\b(?:{_YEARS})\b{_NOTFIG}",
         r"\b[1-4]Q\d{2}\b", r"\b[12]H\d{2}\b", r"\b9M\d{2}\b", r"\bFY\d{2,4}\b", r"\bQ[1-4]\b", r"\bH[12]\b", r"\b10-[QK]\b", r"\b8-K\b", r"\bS&P\s?500\b"]
_YEAR_NEXT = {"and", "or", "to", "through", "from", "in", "on", "at", "as", "the", "a", "of", "for", "versus", "vs", "with", "when", "while", "after",
              "before", "was", "were", "is", "are", "will", "guidance", "guide", "outlook", "target", "targets", "plan", "plans", "results", "consensus",
              "estimate", "estimates", "budget", "fiscal", "year", "quarter", "print", "reporting", "season", "framework", "dividend", "capex", "revenue",
              "revenues", "earnings", "eps", "ebitda", "sales", "margin", "margins", "levels", "level", "range", "ranges", "base", "comparison", "comparisons"}
_DATE_NEXT = {"and", "or", "to", "through", "from", "in", "on", "at", "the", "of", "for", "with", "when", "while", "after", "before", "was", "were", "is", "are", "will"}
ALLOW_CASE = {0, 1}
def _strings(o, path=""):
    if isinstance(o, str): yield path, o
    elif isinstance(o, dict):
        for k, v in o.items():
            if not str(k).startswith("_"): yield from _strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(o, list):
        for j, v in enumerate(o): yield from _strings(v, f"{path}[{j}]")
def c_numbers():
    for path, t in _strings(pr):
        bare = re.sub(r"\{[^}]+\}", " ", t)
        for qm in re.finditer(r"[\"“”]([^\"“”]*)[\"“”]", bare):
            inner = re.sub(r"\s+", " ", qm.group(1)).strip().lower()
            if inner and any(inner in qq for qq in QUOTES if qq): bare = bare.replace(qm.group(0), " ")
            elif inner and any(qq in inner for qq in QUOTES if qq):
                rest = inner
                for qq in QUOTES:
                    if qq and qq in rest: rest = rest.replace(qq, " ")
                for a in ALLOW: rest = re.sub(a, " ", rest, flags=re.I)
                if NUMPAT.search(rest): err(f"hand-typed number in prose ({path}): text added to a stored quote carries a figure: ...{qm.group(0)[:60]}...")
                else: bare = bare.replace(qm.group(0), " ")
            elif inner and NUMPAT.search(inner): err(f"hand-typed number in prose ({path}): quoted text with a figure is not a stored quote (add it to data.json quotes): ...{qm.group(0)[:60]}...")
            elif inner and len(inner) > 12 and path.endswith(("mgmt", "prior", "this")): warn(f"quote in {path} is not in data.json quotes (store it with its source)")
        for ym in re.finditer(rf"\b(?:{_YEARS})\s+([a-z]+)\b", bare):
            if ym.group(1) not in _YEAR_NEXT: warn(f"'{ym.group(0)}' in {path} is read as a year and not checked; if it is a count, use a token or a facts entry")
        for dm in re.finditer(rf"\b{MONTH}\s+{_DAY}\s+([a-z]+)\b", bare):
            if dm.group(1) not in _DATE_NEXT: warn(f"'{dm.group(0)}' in {path} is read as a date and not checked; if the number is a count, use a token or a facts entry")
        clean = bare
        for j_, a in enumerate(ALLOW): clean = re.sub(a, " ", clean, flags=0 if j_ in ALLOW_CASE else re.I)
        clean = re.sub(r"[\"“”][^\"“”]*[\"“”]", " ", clean)
        m = NUMPAT.search(clean)
        if m: err(f"hand-typed number in prose ({path}) (use a token or a facts entry): ...{clean[max(0, m.start()-30):m.end()+20].strip()}...")
check("numbers", c_numbers)

BANNED = ["thin coverage", "thin mark", "one analyst", "single analyst", "broker count", "overweight", "underweight", "price target", "buy rating", "sell rating",
          "we recommend", "outperform", "underperform", "we initiate", "upgrade to", "downgrade to", "sparse coverage", "lone analyst", "lone broker"]
COUNT_TOK = re.compile(r"\{[a-z0-9_.]*(?:consensus_n|consensus_next_n|pre_n|post_n|frozen_n)(?:\|c)?\}")
_NUMW = r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|a dozen|dozen)"
COUNT_TXT = re.compile(rf"\b{_NUMW}(?!\s+(?:of|or\s+(?:fewer|more|less))\b)(?:\s+|-)(?:[a-z]+\s+){{0,2}}(?:brokers?|analysts?|contributors?)\b|\bonly\s+{_NUMW}\s+estimates?\b", re.I)
def c_banned():
    for path, t in _strings(pr):
        if COUNT_TOK.search(t) or COUNT_TXT.search(re.sub(r"\{[^}]+\}", "9", t)):
            err(f"broker count in prose ({path}): contributor counts appear only in the scorecard brackets, never in the narrative (class 34)")
        tt = re.sub(r"[\"“][^\"”]*[\"”]", "", t).lower()
        for w in BANNED:
            for mm_ in re.finditer(r"\b" + re.escape(w) + r"\b", tt):
                if re.search(r"\b(no|not|nor|without)\b(?:\W+\w+){0,2}\W*$", tt[:mm_.start()]): continue
                err(f"banned wording '{w}' in prose ({path})"); break
check("banned", c_banned)

def c_html():
    if not os.path.exists(html_path): err("HTML not rendered (run build_html.py / run_pipeline.py)"); return
    h = open(html_path, encoding="utf-8").read(); body = re.sub(r"<div class=\"src\">.*?</div>", "", h, flags=re.S).lower()
    lint = re.sub(r"[\"“][^\"”]*[\"”]", "", _h.unescape(re.sub(r"<[^>]+>", " ", body)))
    for w in BANNED:
        if re.search(r"\b" + re.escape(w) + r"\b", lint): err(f"banned wording in the document: '{w}'")
    for m in re.finditer(r'<span class="up">([^<]*)</span>', h):
        if m.group(1).strip().startswith(("(", "-")): err(f"green negative: {m.group(1)}")
    for m in re.finditer(r'<span class="dn">([^<]*)</span>', h):
        if m.group(1).strip().startswith("+"): err(f"red positive: {m.group(1)}")
    if re.search(r"<td class=\"[lr]\"></td>", h): err("empty table cell found (use an em dash)")
    heads = [_h.unescape(x) for x in re.findall(r'<(?:h2 class="sec"|div class="sub dark"|h3)>([^<]+)<', h)]
    order = ["Share Price Reaction and Valuation", "Valuation versus Own History", "THE PRINT", "Analytical Summary", "KPI Scorecard", "What Changed",
             "Segment and KPI Review", "Guidance Evolution", "Management Tone Shift", "What to Monitor"]
    def pos(s): return next((j for j, x in enumerate(heads) if x.startswith(s)), -1)
    P_ = [(s, pos(s)) for s in order]
    miss = [s for s, p in P_ if p < 0 and s != "Valuation versus Own History"]
    if miss: err(f"missing sections: {miss}")
    else:
        seq = [p for s, p in P_ if p >= 0]
        if seq != sorted(seq): err("sections out of order")
    if "class=\"lab\"" in h or "class=\"legend\"" in h: err("epistemic label markup present")
    lit = re.findall(r"\{[a-z][A-Za-z0-9_.\-]*(?:\|c)?\}", re.sub(r"<style>.*?</style>", "", h, flags=re.S))
    if lit: err(f"literal token(s) printed in the document: {sorted(set(lit))[:6]}")
    for rid in SC_ROWS:
        v = (CM.get(rid) or {}).get("verdict"); com = (pr.get("scorecard_comments", {}).get(rid) or "").upper()
        if v in ("BEAT", "MISS", "IN LINE") and not com.startswith("{") and any(com.startswith(w) for w in ("BEAT", "MISS", "IN LINE")) and not com.startswith(v) and "UNDERLYING" not in com:
            err(f"scorecard '{rid}': comment verdict disagrees with computed {v}")
check("html", c_html)

RAW = os.path.join(W, "raw")
def c_consensus():
    cs = d.get("consensus", {})
    if not cs.get("frozen_date"): err("consensus.frozen_date missing")
    if cs.get("frozen_source") != "InSync": err(f"consensus source must be InSync, found {cs.get('frozen_source')}")
    if cs.get("cross_check"): err("consensus.cross_check present: InSync is the only consensus source")
    for mid_, m in M.items():
        for fld in ("consensus", "consensus_next", "consensus_fy", "consensus_fy_next"):
            c2 = m.get(fld)
            if c2 and not str(c2.get("source", "")).startswith("InSync"): err(f"metric '{mid_}' {fld} source '{c2.get('source')}' is not InSync")
        c2 = m.get("consensus")
        if c2:
            if not c2.get("asof"): err(f"metric '{mid_}' consensus has no as-of date: it cannot be shown to be pre-print")
            elif str(c2["asof"])[:10] > str(cs.get("frozen_date", "9999"))[:10]: err(f"metric '{mid_}' consensus dated {c2['asof']} is after the pre-print cut-off {cs['frozen_date']}")
    for k, f_ in d.get("forward", {}).items():
        if f_.get("source") != "InSync": err(f"forward '{k}' source '{f_.get('source')}' is not InSync")
        if f_.get("pre_code") and f_.get("post_code") and f_["pre_code"] != f_["post_code"]: err(f"forward '{k}': pre-print on {f_['pre_code']}, post-print on {f_['post_code']} (different line items)")
    PB = d.get("consensus_probe")
    if not PB: err("consensus probe missing (build_dataset.py / refresh_insync.py writes data.json -> consensus_probe)"); return
    PBI = {x["id"]: x for x in PB["probed"]}; NG = d.get("not_graded") or {}
    for rid in SC_ROWS:
        e = PBI.get(rid); c2 = (M.get(rid) or {}).get("consensus")
        if e is None: err(f"consensus probe: scorecard row '{rid}' was never probed"); continue
        if e["found"] is not None:
            if c2 is None and rid not in NG: err(f"consensus probe: '{rid}' has InSync consensus {e['found']:.4g} but is ungraded; grade it or give data.json not_graded['{rid}'] a reason")
            if c2 is not None and c2.get("mean") is not None and abs(c2["mean"] - e["found"]) > 1e-6 * max(1.0, abs(e["found"])): err(f"consensus probe: '{rid}' graded against {c2['mean']:.6g}, probe found {e['found']:.6g}")
        else:
            if c2 is not None: err(f"consensus probe: '{rid}' is graded but the probe found no pre-print consensus")
            if not e.get("evidence"): err(f"consensus probe: '{rid}' has no consensus and no probe evidence (save the wide-window and latest probes with their request)")
    for k, v in NG.items():
        if not str(v).strip(): err(f"not_graded['{k}'] needs a reason")
    if not os.path.isdir(RAW): note("raw/ not present: consensus could not be re-derived from the saved payloads"); return
    rows = []
    for f in sorted(glob.glob(os.path.join(RAW, "insync_estimates_pre*.json")) + glob.glob(os.path.join(RAW, "insync_probe_wide*.json"))):
        try: rows += payload_rows(load_json(f))
        except Exception as e: err(f"raw/{os.path.basename(f)} unreadable: {e}")
    frozen = str(cs.get("frozen_date", ""))[:10]
    mm = {}
    if os.path.exists(os.path.join(W, "metric_map.json")): mm = load_json(os.path.join(W, "metric_map.json"))
    for rid in SC_ROWS:
        c2 = (M.get(rid) or {}).get("consensus")
        if not c2 or c2.get("mean") is None: continue
        cand = [r for r in rows if r.get("LTCODE") == c2.get("code") and PER.norm_period(r.get("FISCALPERIOD")) == PD.get("label")
                and re.match(r"\d{4}-\d{2}-\d{2}", str(r.get("UPDATEDATE") or "")) and str(r["UPDATEDATE"])[:10] <= frozen]
        if not cand: err(f"consensus for '{rid}' ({c2.get('code')}) has no dated pre-print row in raw/ (independent re-read)"); continue
        best = sorted(cand, key=lambda r: (str(r["UPDATEDATE"])[:10], int(r.get("BROKERCOUNT") or 0)))[-1]
        v = parse_value(fix_suffix(best.get("VALUE"), mm.get(rid, {})), M[rid].get("unit") or mm.get(rid, {}).get("unit", "num"))
        if v is not None and M[rid].get("sign", 1) == -1: v = abs(v)
        if v is None or abs(v - c2["mean"]) > 1e-6 * max(1.0, abs(v)): err(f"consensus for '{rid}' ({c2['mean']:.6g}) does not match raw/ ({v}) on an independent re-read of {best.get('_file', 'the estimates file')}")
check("consensus", c_consensus)

def c_basis_polarity():
    BO = d.get("basis_override") or {}; PO = d.get("polarity_override") or {}
    single = []
    for rid in SC_ROWS:
        m = M.get(rid) or {}; c2 = m.get("consensus")
        if len(m.get("ltcode") or []) <= 1: single.append(rid)
        if c2 and m.get("resolved_code") and c2.get("code") != m["resolved_code"] and not str(BO.get(rid, "")).strip():
            err(f"basis: '{rid}' actual is on {m['resolved_code']} but consensus on {c2.get('code')} (different line items); give data.json basis_override['{rid}'] a reason or fix metric_map")
        if m.get("guide_code") and m.get("resolved_code") and m["guide_code"] != m["resolved_code"] and m["guide_code"] in (m.get("ltcode") or []) and not str(BO.get(rid, "")).strip():
            err(f"basis: '{rid}' guide is on {m['guide_code']} but the actual on {m['resolved_code']}")
        if c2:
            codes = [m.get("resolved_code")] + [x for x in (m.get("ltcode") or []) if x != m.get("resolved_code")]
            reg = POL.resolve([x for x in codes if x], None, CO.get("ticker_id"))
            if m.get("better") not in ("higher", "lower"): err(f"POLARITY UNDECLARED: '{rid}' ({m.get('resolved_code')}) is graded with no declared direction ({reg[1]}); set metric_map 'better' or add the code to the registry")
            elif reg[0] and reg[0] != m["better"] and not str(PO.get(rid, "")).strip():
                err(f"polarity: '{rid}' metric_map says '{m['better']}' but the registry says '{reg[0]}' for {m.get('resolved_code')}; give data.json polarity_override['{rid}'] a reason")
            elif m.get("polarity_source") == "metric_map" and reg[0] is None: note(f"polarity of '{rid}' rests on metric_map alone (no registry entry): not independently checked")
    if single: note(f"basis rests on a single available line item for: {', '.join(single)} (no alternative to compare)")
check("basis", c_basis_polarity)

def c_inventory():
    for r in EV_ROWS:
        if not r.get("yoy"): continue
        mid_ = r["id"]; vals = (M.get(mid_) or {}).get("values") or []
        ys = [T.get(f"{mid_}.yoy.{p}", "—") for p in d.get("periods", [])]
        if any(v is not None for v in vals) and all(y == "—" for y in ys):
            need = [PER.prior_year_of(p) for p, v in zip(d.get("periods", []), vals) if v is not None]
            err(f"Y/Y requested for '{mid_}' but no year-ago value exists (needs {', '.join(x for x in need if x)}); fetch the base periods or set yoy: false")
    GV = d.get("guide_verified") or {}; OVG = {(x["metric"], x["key"]) for x in d.get("overrides_log", []) if x.get("kind") == "guide"}
    shown = set(cfg.get("guidance", {}).get("rows", [])) | set(SC_ROWS) | ({cfg["guidance"]["eps_row"]} if cfg.get("guidance", {}).get("eps_row") else set())
    for mid_ in shown:
        m = M.get(mid_) or {}
        for fld in ("guide_prior", "guide_new", "guide_fy_prior", "guide_fy_new"):
            if (m.get(fld) or m.get(fld + "_text")) and f"{mid_}.{fld}" not in GV and (mid_, fld) not in OVG:
                err(f"guide {mid_}.{fld} is shown but not verified against the company document: add data.json guide_verified['{mid_}.{fld}'] = {{source, checked}} or an overrides.json guide")
    for w in (cj.get("warnings") or []):
        if "native Y/Y" in w: warn(w)
check("inventory", c_inventory)

def c_kpi():
    KU = d.get("kpi_universe") or {}
    if not KU.get("kpis"): err("KPI gate: InSync KPI universe is empty (save search_lineitem filter top_kpi to raw/ and rebuild)")
    pk_ = cfg.get("primary_kpi")
    if not pk_: err("KPI gate: review_config.primary_kpi is not set"); return
    if pk_ not in M: err(f"KPI gate: primary_kpi '{pk_}' is not a metric"); return
    if KU.get("kpis"):
        mcodes = set(M[pk_].get("ltcode") or [])
        ucodes = {x["ltcode"] for x in KU["kpis"]}
        if not (mcodes & ucodes): err(f"KPI gate: primary_kpi '{pk_}' ({', '.join(sorted(mcodes)) or 'no ltcode'}) is not in InSync's KPI universe ({KU.get('provenance')}: {', '.join(x['name'] for x in KU['kpis'][:6])} ...)")
        pick = (KU.get("primary_pick") or {}).get("ltcode")
        if pick and pick not in mcodes and not str(cfg.get("primary_kpi_reason", "")).strip():
            err(f"KPI gate: InSync's mechanical Primary KPI is '{KU['primary_pick']['name']}' ({pick}); primary_kpi '{pk_}' differs -> set review_config.primary_kpi_reason")
        if pk_ not in SC_ROWS or pk_ not in [r["id"] for r in EV_ROWS]: err(f"KPI gate: primary_kpi '{pk_}' must appear in both the scorecard and the KPI evolution")
        if KU.get("provenance") == "mcp_discovery": warn("KPI gate: universe from line-item discovery (no InSync KPI tagging); disclosed in Source")
check("kpi", c_kpi)

def c_reported():
    rel_ = (d.get("release") or {}).get("date") or ""
    if not rel_: err("release.date is not set")
    elif rel_ > _dt.date.today().isoformat(): err(f"quarter not reported: release date {rel_} is in the future")
    pk_ = cfg.get("primary_kpi")
    if not any(M.get(h) and (M[h].get("values") or [None])[-1] is not None for h in [pk_, "revenue", "eps", "eps_adj"] if h):
        err("quarter not reported: no printed-quarter actual on the headline lines")
check("reported", c_reported)

def c_repeat():
    def _items(p):
        it = [("THE PRINT", x) for x in p.get("the_print", [])] + [("Summary", x["lead"] + " " + x["text"]) for x in p.get("summary", [])]
        it += [("Guidance read", p.get("guidance_read", "")), ("Revisions", p.get("revisions_tail", ""))] + [("Monitor", x["lead"] + " " + x["text"]) for x in p.get("monitor", [])]
        return [(s, t) for s, t in it if t and t.strip()]
    IT = _items(pr); TOKRE = re.compile(r"\{([^}|]+)(?:\|c)?\}")
    NONFIG = re.compile(r"^(co\.|period|consensus\.|reaction\.index$|kpi\.|q\.|.*\.label$|.*\.metric$|.*\.verdict$|.*\.guide_pos$|.*\.pos(_post)?$|.*\.direction$|.*\.alt_label$|guide\d*\.guide_text$|guide\d*\.base_label$)")
    cnt = {}
    for s, t in IT:
        for k in set(TOKRE.findall(t)):
            if NONFIG.match(k): continue
            cnt.setdefault(k, []).append(s)
    rc = sum(t.count("{reaction.chg") for _, t in IT)
    if rc > 1: err(f"repeated fact: the reaction move {{reaction.chg}} is stated {rc} times in the prose (once at most; the reaction table carries it)")
    for k, where in cnt.items():
        if len(where) > 3: err(f"repeated fact: {{{k}}} headlines {len(where)} prose items ({', '.join(where)}); give it one home")
    def _words(t): return re.findall(r"\{[^}]+\}|[a-z0-9']+", t.lower())
    def _grams(t):
        w = _words(t); return {tuple(w[j:j + 3]) for j in range(len(w) - 2)}
    for i_ in range(len(IT)):
        for j_ in range(i_ + 1, len(IT)):
            (s1, t1), (s2, t2) = IT[i_], IT[j_]
            w1, w2 = _words(t1)[:4], _words(t2)[:4]
            if len(w1) == 4 and w1 == w2: err(f"repeated fact: {s1} and {s2} items open with the same words ('{' '.join(w1)}')")
            g1, g2 = _grams(t1), _grams(t2)
            if g1 and g2 and len(g1 & g2) / len(g1 | g2) > 0.5: err(f"repeated fact: {s1} and {s2} items are near-duplicates")
check("repeat", c_repeat)

def c_identity():
    sp = os.path.join(RAW, "insync_search_tickerid.json")
    if os.path.exists(sp):
        hits = [r for r in payload_rows(load_json(sp)) if str(r.get("ticker_id", r.get("TICKER_ID", ""))) == str(CO.get("ticker_id"))]
        if not hits: err(f"identity: ticker_id {CO.get('ticker_id')} is not in raw/insync_search_tickerid.json")
        elif not any(str(h.get("match_type", "")).startswith("exact") for h in hits): err("identity: the search_tickerid match is not exact")
        elif CO.get("name") and hits[0].get("company_name") and _norm(hits[0]["company_name"]) != _norm(CO["name"]):
            err(f"identity: data.json names '{CO['name']}' but InSync resolved ticker_id {CO.get('ticker_id')} to '{hits[0]['company_name']}'")
    else: note("identity: raw/insync_search_tickerid.json not present; identity not re-checked against the search payload")
def _norm(s): return " ".join(re.sub(r"\b(inc|corp|corporation|co|company|plc|ag|sa|nv|ltd|limited|holdings?|group|the)\b|[^a-z0-9 ]", " ", str(s).lower()).split())
check("identity", c_identity)

def c_pdf():
    if NOPDF: note("PDF checks skipped on request (--no-pdf): page count, layout and identity-in-PDF were NOT checked"); return
    if not os.path.exists(pdf_path): err("PDF not rendered (run render_pdf.py / run_pipeline.py, or pass --no-pdf deliberately)"); return
    if os.path.exists(html_path) and os.path.getmtime(pdf_path) + 1 < os.path.getmtime(html_path): err("PDF is older than the HTML: stale render, rebuild the PDF")
    try:
        try: import pymupdf as fitz
        except ImportError: import fitz
    except ImportError: err("PyMuPDF not installed; page count and layout cannot be checked (pip install -r requirements.txt)"); return
    doc = fitz.open(pdf_path); n = len(doc)
    txt = re.sub(r"\s+", " ", "".join(pg.get_text() for pg in doc))
    if re.sub(r"\s+", " ", CO.get("name", "")) not in txt or f"({CO.get('ticker')})" not in txt or PD.get("label", "") not in txt:
        err(f"identity: PDF does not name {CO.get('name')} ({CO.get('ticker')}) {PD.get('label')}: wrong or stale render")
    if not (4 <= n <= 5): err(f"PDF has {n} pages; budget is 4-5")
    heads = ["Analytical Summary", "KPI Scorecard", "What Changed", "Segment and KPI Review", "Guidance Evolution", "Management Tone Shift", "What to Monitor"]
    thead = ["Metric", "Guided item", "Change", "Dimension", "Earnings release date"]
    for pi, pg in enumerate(doc):
        H = pg.rect.height; blocks = [b for b in pg.get_text("blocks") if b[4].strip() and b[1] < H - 45]
        if not blocks: err(f"page {pi+1} is empty"); continue
        last = max(blocks, key=lambda b: b[3]); lt = last[4].strip()
        if pi < n - 1:
            if any(lt.startswith(h) for h in heads): err(f"page {pi+1}: section heading \"{lt[:40]}\" stranded at the page foot")
            if any(lt.split("\n")[0].strip() == h for h in thead): err(f"page {pi+1}: table header stranded at the page foot")
            fill = last[3] / (H - 45)
            if fill < 0.6: warn(f"page {pi+1} is only {fill:.0%} full")
        elif n > 1 and last[3] / (H - 45) < 0.2: err(f"last page (page {pi+1}) is nearly empty ({last[3] / (H - 45):.0%}); tighten to fewer pages")
    p0 = doc[0]; W0 = p0.rect.width
    for b in p0.get_text("blocks"):
        if b[1] < 110 and CO.get("ticker", "@@") in b[4] and b[2] > W0 * 0.66: err("masthead: the company line runs into the Prepared/Reported cell")
    print(f"pages: {n}")
check("pdf", c_pdf)

def c_valuation():
    pe_tok = T.get("val.pe_ntm", "—")
    if pe_tok in ("—", "", None): return
    R_ = d.get("reaction") or {}; cl = [x for x in (R_.get("closes") or {}).get(CO.get("ticker"), []) if x]
    pe = (d.get("valuation") or {}).get("pe_adj_ntm")
    if not cl or not pe or (R_.get("price_currency") or CO.get("reporting_currency")) != CO.get("reporting_currency", "USD"):
        note("valuation: P/E (NTM) not cross-checked against price and EPS (no price, no P/E or a different price currency)"); return
    fw = d.get("forward") or {}; es = [x for x in ((fw.get("eps_fy0") or {}).get("post"), (fw.get("eps_fy1") or {}).get("post")) if x and x > 0]
    if len(es) < 2:
        em = [m for m in M.values() if str(m.get("resolved_code", "")).split("-")[0] in ("IS_P_128", "IS_P_127")]
        n_ = {"Q": 4, "H": 2}.get(PD.get("cadence", "Q"), 1); xs = [x for x in ((em[0].get("values") if em else None) or [])[-n_:] if x is not None]
        es = [sum(xs)] * 2 if len(xs) == n_ and sum(xs) > 0 else []
    if not es: note("valuation: P/E (NTM) not cross-checked (no positive EPS to compare)"); return
    lo, hi = cl[-1] / max(es), cl[-1] / min(es)
    if not (lo / 4 <= pe <= hi * 4): err(f"valuation: printed P/E (NTM) {pe_tok} is inconsistent with the price {cl[-1]:,.2f} and EPS (implied {lo:.1f}x-{hi:.1f}x); the vendor valuation is not usable for this issuer")
check("valuation", c_valuation)

for w in (d.get("build_warnings") or []) + [x for x in (cj.get("warnings") or []) if "native Y/Y" not in x]:
    if w.startswith(("POLARITY", "BASIS", "could not be read", "forward", "basis gap", "VALUATION", "prices:", "no raw/", "metric_map", "guide_vs_frozen")) or "labelled InSync native" in w:
        print("UNRESOLVED", w)
for f_ in (d.get("fill_by_hand") or []): print("NOTE  listed FILL BY HAND at build (confirm it was filled or is deliberately omitted):", f_)
for w in Wn: print("WARN ", w)
for nn in NOTE: print("NOTE ", nn)
for e in E: print("ERROR", e)
print(f"validate_review: {len(E)} error(s), {len(Wn)} warning(s)")
sys.exit(1 if E else 0)
