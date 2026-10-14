"""Earnings Review - build_dataset.py
Assembles <workdir>/data.json from saved MCP payloads in <workdir>/raw/ and metric_map.json, doing NO arithmetic: it parses
value strings into base units and places them. It REFUSES rather than guesses:
  exit 1  error (bad metric_map, truncated or failed payload, mixed currencies, implausible magnitude, unreadable inputs)
  exit 2  the printed period has not reported                  exit 3  a later period has already reported (ask the user)
  exit 4  identity: a row belongs to another issuer, or the search_tickerid match is not exact
Every failure prints one JSON line {"status":"error",...} on stdout; human-readable ERROR/WARN/FILL BY HAND lines go to stderr.

Raw files (save each MCP response verbatim; one tool per file; names are read by pattern):
  raw/insync_search_tickerid.json   search_tickerid response (identity: exact match required)
  raw/insync_call_date.json         get_call_date, window quarter end -> today + 120 days (reported / stale guard)
  raw/insync_actuals*.json          get_actuals              raw/insync_estimates_pre*.json  get_estimates as-of the cut-off
  raw/insync_estimates_post*.json   get_estimates latest     raw/insync_guidance*.json       get_guidance
  raw/insync_probe_wide.json / insync_probe_latest.json      {"request": {...}, "response": {...}} for codes with no pre-print row
  raw/insync_search_lineitem_topkpi.json                     raw/insync_valuation*.json (NTM; _ltm, _ltm_1y, _ltm_3y)
  raw/insync_prices.json             get_market_data, adjusted closes for the ticker and IDX0001
Usage: python build_dataset.py <workdir> --ticker SMH --ticker-id 99900 --period 3Q26 --next 4Q26 --fy FY26 --fy-period 2026
       --periods 3Q25,4Q25,1Q26,2Q26,3Q26 --base 3Q24,4Q24,1Q25,2Q25 --frozen 2026-09-16 --post 2026-09-21 [--prior-source 2Q26]"""
import json, sys, os, glob, re, argparse, datetime as _dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from erlib import parse_value, parse_guide, fix_suffix, classify, currency_of, payload_rows, load_json, envelope, guarded
from insync_layer import build as insync_build, reported_check, codes_of, load_actuals, load as load_rows, payload_errors
import periods as PER, polarity as POL
from version import VERSION

UNITS = {"m", "b", "count", "k", "pct", "num"}; KINDS = {"level", "rate"}
FMTS = {"bn", "m", "int", "k", "knum", "pct1", "pct2", "usd2", "usd3", "num1", "num2", "days", "x"}

class _Args(argparse.ArgumentParser):
    def error(self, message): envelope("build_dataset", f"arguments: {message}")

def main():
    ap = _Args()
    ap.add_argument("workdir"); ap.add_argument("--ticker", required=True); ap.add_argument("--ticker-id", required=True); ap.add_argument("--company", default="")
    ap.add_argument("--period", required=True); ap.add_argument("--next", required=True); ap.add_argument("--fy", required=True)
    ap.add_argument("--periods", required=True); ap.add_argument("--base", default=""); ap.add_argument("--frozen", required=True); ap.add_argument("--post", required=True)
    ap.add_argument("--valuation-date", default="", help="date of the get_valuation snapshot (defaults to its DATE, else --post)")
    ap.add_argument("--prior-source", default="", help="InSync SOURCE_PERIOD of the call that gave the prior guide (the period before --period)")
    ap.add_argument("--fy-period", default="", help="InSync FISCALPERIOD label of the current fiscal year in get_guidance, e.g. 2026 (not FY26)")
    ap.add_argument("--price-currency", default="", help="currency of raw/insync_prices.json when the file does not say (e.g. USD, GBPence)")
    ap.add_argument("--confirm-stale", default="", help="reason the user gave for reviewing a period when a later one has already reported")
    ap.add_argument("--today", default="", help="date for the reported-quarter guard (default: today)")
    a = ap.parse_args()
    W = a.workdir; RAW = os.path.join(W, "raw"); ERR, WARN, FILL = [], [], []
    warn = WARN.append; today = a.today or _dt.date.today().isoformat()
    period, nxt = PER.norm_period(a.period), PER.norm_period(a.next)
    periods = [PER.norm_period(p) for p in a.periods.split(",") if p]; base = [PER.norm_period(p) for p in a.base.split(",") if p]

    mm = {k: v for k, v in load_json(os.path.join(W, "metric_map.json")).items() if not k.startswith("_")}
    for mid_, s in mm.items():
        for k in ("label", "fmt", "ltcode", "unit"):
            if k not in s: ERR.append(f"metric_map '{mid_}' is missing '{k}'")
        if s.get("unit") not in UNITS: ERR.append(f"metric_map '{mid_}': unit {s.get('unit')!r} not in {sorted(UNITS)}")
        if s.get("kind", "level") not in KINDS: ERR.append(f"metric_map '{mid_}': kind {s.get('kind')!r} not in {sorted(KINDS)}")
        if s.get("unit") == "pct" and "kind" not in s: ERR.append(f"metric_map '{mid_}' is a percentage with no 'kind': set \"kind\": \"rate\" (graded in bps), or \"level\" with a reason")
        if s.get("fmt") not in FMTS: ERR.append(f"metric_map '{mid_}': fmt {s.get('fmt')!r} not in {sorted(FMTS)}")
        if s.get("fmt") == "m" and s.get("unit") in ("m", "b") and not re.search(r"\$\s?m\b|\(\s?\$?m\s?\)|million", str(s.get("label", "")), re.I):
            warn(f"metric_map '{mid_}': fmt 'm' prints bare numbers ('263'); put the unit in the label (e.g. '{s.get('label')} ($M)') or use fmt 'bn'")
        if s.get("sign", 1) not in (1, -1): ERR.append(f"metric_map '{mid_}': sign must be 1 or -1")
        try: s["better"] = POL.validate_better(s.get("better"))
        except ValueError as e: ERR.append(f"metric_map '{mid_}': {e}")
    if ERR: envelope("build_dataset", "; ".join(ERR))

    cad = PER.cadence_of(base + periods + [period, nxt])
    if len(cad) != 1: envelope("build_dataset", f"periods mix cadences {sorted(cad)} ({','.join(base + periods)}; period {period}, next {nxt}); use one cadence")
    if periods[-1] != period: ERR.append(f"--period {period} must be the last of --periods ({','.join(periods)})")
    if PER.next_period_of(period) != nxt: warn(f"--next {nxt} is not the period after {period} ({PER.next_period_of(period)})")
    if not PER.contiguous(base + periods): warn(f"periods are not contiguous ({','.join(base + periods)}): sequential changes for gaps print as dashes")

    tid = str(a.ticker_id); ident = {"ticker": a.ticker, "ticker_id": tid}
    sp = os.path.join(RAW, "insync_search_tickerid.json")
    if not os.path.exists(sp): envelope("build_dataset", "identity: raw/insync_search_tickerid.json is missing (save the search_tickerid response; an exact match is required)", 4)
    hits = [r for r in payload_rows(load_json(sp)) if str(r.get("ticker_id", r.get("TICKER_ID", ""))) == tid]
    exact = [r for r in hits if str(r.get("match_type", "")).startswith("exact")]
    if not exact:
        envelope("build_dataset", f"identity: search_tickerid has no EXACT match for ticker_id {tid} (match types: {[r.get('match_type') for r in hits] or 'none'}); a similarity score is not identity", 4)
    ident.update(company_name=exact[0].get("company_name"), match_type=exact[0].get("match_type"), subindustry=exact[0].get("insync_subindustry"), latest_fiscal_quarter=exact[0].get("latest_fiscal_quarter"))
    perr, foreign = payload_errors(RAW, tid)
    if foreign: envelope("build_dataset", f"identity: rows for another issuer (TICKER_ID != {tid}) in {foreign}", 4)

    ERR += perr

    notes = []
    act, by = load_actuals(RAW, None, notes)
    for n in notes: warn(n)
    L = insync_build(RAW, mm, period, nxt, a.fy_period, a.frozen, a.fy if a.fy.upper().startswith("FY") else f"FY{a.fy[-2:]}",
                     f"FY{int((a.fy if a.fy.upper().startswith('FY') else 'FY' + a.fy[-2:])[2:]) + 1:02d}", FILL, act_by=by, warn=warn)
    FY_LAB = a.fy if a.fy.upper().startswith("FY") else f"FY{a.fy[-2:]}"; FY_NEXT = f"FY{int(FY_LAB[2:]) + 1:02d}"

    metrics, ccys = {}, {}
    for mid_, spec in mm.items():
        code = L["resolved"][mid_]; codes = codes_of(spec)
        def pick(p):
            raw = fix_suffix(by.get(code, {}).get(p), spec)
            cls = classify(raw)
            if cls == "blank":
                other = [c for c in codes if c != code and classify(by.get(c, {}).get(p)) == "ok"]
                if other and p in (base + periods): warn(f"basis gap: {mid_} {p} has no value on {code}; {other[0]} has one but is a different line item (not mixed in)")
                return None
            if cls == "nm": warn(f"could not be read: {mid_} {code} {p} is 'not meaningful' ({raw!r}): shown as a dash"); return None
            v = parse_value(raw, spec["unit"])
            if v is None: warn(f"could not be read: {mid_} {code} {p} VALUE {raw!r}"); return None
            c = currency_of(raw)
            if c: ccys.setdefault(mid_, set()).add(c)
            if spec.get("sign", 1) == -1:
                if v > 0: warn(f"{mid_} {p}: outflow line reported with a positive sign ({raw!r}); shown as absolute spend")
                v = abs(v)
            if spec["unit"] == "count" and abs(v) >= 1e9 and not spec.get("large_count_ok"):
                ERR.append(f"implausible count: {mid_} {code} {p} = {v:,.0f} from {raw!r} (a mislabelled suffix?): set suffix_override, or large_count_ok: true if real")
            return v
        EXC = set(spec.get("exclude_periods", []))
        vals = [None if p in EXC else pick(p) for p in periods]; bvals = [None if p in EXC else pick(p) for p in base] if base else None
        if all(v is None for v in vals): FILL.append(f"{mid_}: no actuals for {code} ({', '.join(codes)})")
        native = None
        if spec.get("native_yoy_ltcode"):
            native = [parse_value(by.get(spec["native_yoy_ltcode"], {}).get(p), "pct") for p in periods]
            for p, v in zip(periods, native):
                if v is None and classify(by.get(spec["native_yoy_ltcode"], {}).get(p)) not in ("blank",): warn(f"could not be read: native Y/Y {spec['native_yoy_ltcode']} {p} {by.get(spec['native_yoy_ltcode'], {}).get(p)!r}")
            if all(v is None for v in native): warn(f"{mid_}: no {spec['native_yoy_ltcode']} rows; its Y/Y row is computed from levels and must not be labelled InSync native")
        lineitem = next((r.get("LINEITEM") for r in act if r.get("LTCODE") == code and r.get("LINEITEM")), "")
        adj_code = bool(code) and str(code).startswith("IS_P_") and str(code).split("-")[0].split("_")[-1] in ("102", "106", "110", "112", "114", "122", "124", "126", "128", "134", "136", "252")
        basis = "adjusted" if (any(w in lineitem.lower() for w in ("ex-1x", "adj")) or adj_code) else ("reported" if len(codes) > 1 else "")
        bt, bsrc = POL.resolve([code] + [c for c in codes if c != code], spec.get("better"), tid)
        if bt is None: warn(f"POLARITY UNDECLARED: {mid_} ({code} {spec['label']}): {bsrc}; set 'better' in metric_map or add the code to assets/polarity_registry.json (a graded row will fail the validator)")
        metrics[mid_] = {"label": spec["label"], "fmt": spec["fmt"], "kind": spec.get("kind", "level"), "values": vals, "base": bvals, "native_yoy": native,
                         "consensus": L["consensus"].get(mid_), "guide_prior": None, "guide_new": None, "consensus_next": L["consensus_next"].get(mid_),
                         "ltcode": codes, "resolved_code": code, "lineitem": lineitem, "basis": basis, "better": bt, "polarity_source": bsrc, "unit": spec["unit"]}
        if mid_ in L["consensus_fy"]: metrics[mid_]["consensus_fy"] = L["consensus_fy"][mid_]
        if mid_ in L["consensus_fy_next"]: metrics[mid_]["consensus_fy_next"] = L["consensus_fy_next"][mid_]
        if mid_ in L["basis_conflict"]: metrics[mid_]["basis_conflict"] = L["basis_conflict"][mid_]
        for fl in ("inline_band", "fy_guide", "consensus_metric", "forward", "forward_key", "sign"):
            if fl in spec: metrics[mid_][fl] = spec[fl]

    for mid_, cs in ccys.items():
        if len(cs) > 1: ERR.append(f"{mid_}: values in more than one currency {sorted(cs)}; refetch on one basis")
        c0 = metrics[mid_].get("consensus") or {}
        if c0.get("currency") and cs and c0["currency"] not in cs: ERR.append(f"{mid_}: actual in {sorted(cs)} but consensus in {c0['currency']}")
    rep = sorted({c for cs in ccys.values() for c in cs})
    if len(rep) > 1: ERR.append(f"financials in more than one currency {rep}; the review needs one reporting currency")
    reporting_ccy = rep[0] if rep else "USD"

    head = [metrics[m]["resolved_code"] for m, sp_ in mm.items() if m in ("revenue", "eps") or sp_.get("headline")] or [metrics[m]["resolved_code"] for m in mm]
    if not glob.glob(os.path.join(RAW, "insync_call_date*.json")):
        warn("no raw/insync_call_date.json: the check that no LATER quarter has already reported could not run (save get_call_date with a forward window)")
    code_, why = reported_check(RAW, act, period, head, today, a.confirm_stale)
    if code_: envelope("build_dataset", f"REFUSED: {a.ticker} {period}: {why}" + (". Build an earnings preview instead; no review is written." if code_ == 2 else ""), code_)

    for p_ in L["probe"]["probed"]:
        if p_["found"] is None and p_.get("evidence") is None:
            FILL.append(f"consensus probe: {p_['id']} ({','.join(p_['codes'])}) has no pre-print row and no probe that covered it -> save the wide-window and latest probes WITH their request (data-collection.md §4)")
    if not L["kpi_universe"]["provenance"]: FILL.append("KPI universe empty: save search_lineitem filter top_kpi (then the unfiltered tiers) to raw/")

    unmatched = []
    for r in load_rows(RAW, "insync_guidance*.json"):
        code, per = r.get("LTCODE"), PER.norm_period(r.get("FISCALPERIOD", ""))
        if code is not None and not any(code == (spec.get("guide_ltcode") or metrics[m]["resolved_code"]) for m, spec in mm.items()): unmatched.append(r)
        for mid_, spec in mm.items():
            gcode = spec.get("guide_ltcode") or metrics[mid_]["resolved_code"]
            if code is None:
                _s = PER.norm_period(r.get("SOURCE_PERIOD") or "")
                if mid_ == next(iter(mm)) and (not _s or _s in (period, a.prior_source)): FILL.append(f"guidance row with null LTCODE (FISCALPERIOD {per}, SOURCE {r.get('SOURCE_PERIOD')}): {str(r.get('GUIDE_TEXT'))[:70]} -> place by hand in overrides.json guides")
                continue
            if code != gcode: continue
            if spec.get("guide_ltcode") and gcode != metrics[mid_]["resolved_code"] and gcode in metrics[mid_]["ltcode"] and not spec.get("guide_basis_ok"):
                ERR.append(f"{mid_}: guide is on {gcode} but the actual and consensus are on {metrics[mid_]['resolved_code']} (different line items); align guide_ltcode or set guide_basis_ok with a reason")
            txt = str(r.get("GUIDE_TEXT", ""))
            if spec.get("suffix_override"):
                txt = re.sub(r"(\d)([kKMBT])(?![A-Za-z])", lambda m_: m_.group(1) + spec["suffix_override"].get(m_.group(2), m_.group(2)), txt)
            try: rngv = parse_guide(txt, spec["unit"])
            except ValueError as e: rngv = None; warn(f"could not be read: guide {mid_} {per}: {e}")
            if rngv is None and txt.strip(): warn(f"guide {mid_} {per} kept as text (no figure parsed): {txt[:60]!r}")
            if rngv: rngv = sorted(abs(x) if spec.get("sign", 1) == -1 else x for x in rngv)
            src = PER.norm_period(r.get("SOURCE_PERIOD") or "")
            prior_ok = (not src) or (not a.prior_source) or src == PER.norm_period(a.prior_source)
            new_ok = (not src) or src == period
            M_ = metrics[mid_]
            if per == period and prior_ok and M_["guide_prior"] is None: M_.update(guide_prior=rngv, guide_prior_raw=txt, guide_prior_src=src, guide_code=code)
            if per == nxt and new_ok and M_["guide_new"] is None: M_.update(guide_new=rngv, guide_new_raw=txt, guide_new_src=src, guide_code=code)
            if a.fy_period and per == a.fy_period:
                if src and src == period and M_.get("guide_fy_new") is None: M_.update(guide_fy_new=rngv, guide_fy_new_raw=txt)
                elif src and a.prior_source and src == PER.norm_period(a.prior_source) and M_.get("guide_fy_prior") is None: M_.update(guide_fy_prior=rngv, guide_fy_prior_raw=txt)

    for r in unmatched:
        per, src = PER.norm_period(r.get("FISCALPERIOD", "")), PER.norm_period(r.get("SOURCE_PERIOD") or "")
        if per in (period, nxt, a.fy_period) and (not src or src in (period, PER.norm_period(a.prior_source) if a.prior_source else period)):
            FILL.append(f"guide not on any metric_map line item: {r.get('LTCODE')} ({r.get('LINEITEM') or 'no label'}) {per} from the {src or '?'} call: "
                        f"{str(r.get('GUIDE_TEXT'))[:60]!r} -> add the line item, or place the text in overrides.json guides on the metric it describes, or leave it out")

    val = {}
    for f_ in sorted(glob.glob(os.path.join(RAW, "insync_valuation*.json"))):
        if "_ltm" in os.path.basename(f_): continue
        for r in payload_rows(load_json(f_)):
            k = r.get("metric"); v = r.get("value")
            if k in ("Market Cap", "Market_Cap"): val["market_cap_m"] = v
            if k == "EV": val["ev_m"] = v
            if k == "P/E_Adj_Ratio": val["pe_adj_ntm"] = v
            if k == "EV/EBITDA_Adj": val["ev_ebitda_adj_ntm"] = v
            if k == "P/B_Ratio": val["pb"] = v
            if r.get("DATE"): val.setdefault("_date", r["DATE"])
    if not val: FILL.append("valuation: no get_valuation rows (mode NTM)")
    val["history"] = L["valuation_history"]; val["source"] = "InSync get_valuation"; val["asof"] = a.valuation_date or val.pop("_date", None) or a.post; val.pop("_date", None)
    val["currency"] = reporting_ccy

    ipf = sorted(glob.glob(os.path.join(RAW, "insync_prices*.json")))
    price_source = "InSync adjusted close" if ipf else None
    _piss = []
    for f in ipf: payload_rows(load_json(f), os.path.basename(f), _piss)
    for f_ in sorted({i[1] for i in _piss if i[0] == "has_more"}): warn(f"prices: {f_} says has_more (truncated); check the reaction dates are all present or page with offset")
    px, iccy = [], set()
    for f in ipf:
        for r in payload_rows(load_json(f)):
            t_ = str(r.get("TICKER_ID", "")); sym_ = a.ticker if t_ == tid else ("SPY" if t_ == "IDX0001" else str(r.get("TICKER") or t_))
            if r.get("ADJUSTED_CLOSE") is None or not r.get("DATE"): continue
            px.append({"requestId": sym_, "date": str(r["DATE"])[:10], "price": float(r["ADJUSTED_CLOSE"]), "volume": None})
            if t_ == tid and r.get("CURRENCY"): iccy.add(str(r["CURRENCY"]))
    if len(iccy) > 1: ERR.append(f"raw/insync_prices*.json: {a.ticker} prices in more than one currency {sorted(iccy)}")
    stated = a.price_currency or (sorted(iccy)[0] if iccy else "")
    pccy = stated or reporting_ccy
    if ipf and not any(r["requestId"] == a.ticker for r in px): ERR.append(f"raw/insync_prices*.json has no rows for ticker_id {tid} ({a.ticker})")
    if price_source and not stated:
        if reporting_ccy in ("GBP", "ZAR", "ILS"): ERR.append(f"prices: the price file states no currency and {reporting_ccy} shares usually trade in pence, cents or agorot; pass --price-currency (e.g. GBPence)")
        else: warn(f"prices: the price file states no currency; assumed {reporting_ccy} (the reporting currency). Pass --price-currency to state it")
    if price_source is None: FILL.append("prices: no raw/insync_prices.json (InSync get_market_data); the reaction table cannot be computed")
    else: warn("prices from InSync get_market_data (adjusted closes, no volume); disclosed in Source")
    sub = 0.01 if pccy in ("GBPence", "GBp", "GBX") else 1.0; pccy_iso = "GBP" if sub == 0.01 else pccy
    closes, vols, dates = {}, {}, []
    for r in sorted(px, key=lambda x: x.get("date", "")):
        if r.get("price") is None: continue
        sym = str(r["requestId"]).split("-")[0]; closes.setdefault(sym, {})[r["date"]] = round(r["price"] * sub, 6)
        if r["date"] not in dates: dates.append(r["date"])
    reaction = {"symbol": a.ticker, "index": "SPY", "peers": [s for s in closes if s not in (a.ticker, "SPY")], "dates": dates,
                "closes": {s: [closes[s].get(dt) for dt in dates] for s in closes}, "volumes": {a.ticker: [vols.get(a.ticker, {}).get(dt) for dt in dates]} if a.ticker in vols else {},
                "pre_date": None, "post_date": None, "day2_date": None, "asof_date": next((dt for dt in reversed(dates) if closes.get(a.ticker, {}).get(dt) is not None), None),
                "buyback_avg_price": None, "price_currency": pccy_iso, "price_source": price_source}
    if pccy_iso != reporting_ccy: warn(f"prices in {pccy_iso}, financials in {reporting_ccy}: market cap and EV are taken from InSync, not from price x shares")
    FILL.append("reaction.pre_date/post_date/day2_date: set from the release timestamp (after-close -> pre = release day, post = next session)")

    if ERR: envelope("build_dataset", "; ".join(ERR))
    company = a.company or ident.get("company_name") or a.ticker
    out = {"company": {"name": company, "ticker": a.ticker, "ticker_id": int(a.ticker_id), "fiscal_year_end": "", "insync_subindustry": ident.get("subindustry") or "",
                       "reporting_currency": reporting_ccy},
           "provenance": {"version": VERSION, "identity": ident, "confirm_stale": a.confirm_stale or None},
           "period": {"label": period, "quarter_end": "", "next_label": nxt, "fy_label": FY_LAB, "cadence": sorted(cad)[0]},
           "release": {"date": "", "display": "", "timing": ""}, "call": {"date": "", "display": ""}, "prepared": "",
           "consensus": {"frozen_source": "InSync", "frozen_date": a.frozen, "post_print_date": a.post, "cross_check": None},
           "consensus_probe": L["probe"], "kpi_universe": L["kpi_universe"],
           "periods": periods, "base_periods": base, "metrics": metrics, "reaction": reaction, "valuation": val, "forward": L["forward"],
           "guidance_extra_rows": [], "facts": {}, "quotes": {}, "guide_verified": {}, "build_warnings": WARN, "fill_by_hand": FILL,
           "fy_guide": [{"label": f"{m['label']} ({FY_LAB})", "fmt": m["fmt"], "metric": k, "prior": m.get("guide_fy_prior"), "new": m.get("guide_fy_new"),
                         "prior_tag": a.prior_source, "new_tag": period, "ytd_text": "—"}
                        for k, m in metrics.items() if (m.get("guide_fy_new") or m.get("guide_fy_prior")) and m.get("fy_guide", True) is not False] or None}
    kept, dp = [], os.path.join(W, "data.json")
    try: prev = load_json(dp) if os.path.exists(dp) else None
    except Exception: prev = None
    if isinstance(prev, dict) and str((prev.get("company") or {}).get("ticker_id")) == tid and (prev.get("period") or {}).get("label") == period:
        for k in ("release", "call", "prepared", "facts", "quotes", "guide_verified", "not_graded", "basis_override", "polarity_override", "guidance_extra_rows"):
            if prev.get(k) not in (None, "", {}, []) and prev.get(k) != {"date": "", "display": "", "timing": ""} and prev.get(k) != {"date": "", "display": ""}:
                out[k] = prev[k]; kept.append(k)
        for k, sub_ in (("fiscal_year_end", "company"), ("quarter_end", "period")):
            if (prev.get(sub_) or {}).get(k): out[sub_][k] = prev[sub_][k]; kept.append(f"{sub_}.{k}")
        for k in ("pre_date", "post_date", "day2_date", "buyback_avg_price"):
            if (prev.get("reaction") or {}).get(k): out["reaction"][k] = prev["reaction"][k]; kept.append(f"reaction.{k}")
        ovg = {(x.get("metric"), x.get("key")) for x in prev.get("overrides_log", []) if x.get("kind") == "guide"}
        for mid_, pm in (prev.get("metrics") or {}).items():
            if mid_ not in out["metrics"]: continue
            for k, v in pm.items():
                if k.endswith("_text") and v and not (k.startswith("guide_") and (mid_, k[:-5]) in ovg):
                    out["metrics"][mid_][k] = v; kept.append(f"{mid_}.{k}")
        if kept: FILL[:] = [x for x in FILL if not x.startswith("reaction.pre_date") or "reaction.pre_date" not in kept]
    json.dump(out, open(dp, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    if kept: print(f"kept hand-filled fields from the existing data.json (same ticker_id and period): {', '.join(kept)}", file=sys.stderr)
    print(f"data.json written with {len(metrics)} metrics, {len(L['forward'])} forward series (skill {VERSION})")
    for w in WARN: print("WARN:", w, file=sys.stderr)
    for m in FILL: print("FILL BY HAND:", m, file=sys.stderr)

if __name__ == "__main__": guarded("build_dataset", main)
