"""Earnings Review - compute.py
Reads data.json (parsed numbers, no arithmetic) + review_config.json and writes computed.json: every surprise, growth rate,
midpoint, revision and reaction statistic, and a flat TOKEN namespace of pre-formatted strings that prose.json references.
2.0 rules: periods are matched by LABEL (prior year / prior period), never by list position; relative changes divide by |base|
and print 'n.m.' when the sign crosses zero; verdicts use the declared polarity only (no default); one display scale and
one precision per row; the currency printed is the one the data were reported in.
Usage: python compute.py <workdir>"""
import json, sys, os, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from erlib import parse_growth_words, ccy_symbol, envelope, guarded
import periods as PER
from version import VERSION
from decimal import Decimal, ROUND_HALF_UP
def q(x, dp): return Decimal(repr(float(x))).quantize(Decimal(1).scaleb(-dp), rounding=ROUND_HALF_UP)

def main():
    W = sys.argv[1] if len(sys.argv) > 1 else "."
    d = json.load(open(os.path.join(W, "data.json"), encoding="utf-8"))
    cfg = json.load(open(os.path.join(W, "review_config.json"), encoding="utf-8"))
    for k in ("metrics", "periods", "reaction", "consensus", "company", "period"):
        if k not in d: envelope("compute", f"data.json has no '{k}' (rebuild with build_dataset.py)")
    M = d["metrics"]; P = d["periods"]; n = len(P); i = n - 1
    if n == 0: envelope("compute", "data.json lists no periods")
    BASE = d.get("base_periods") or []; ALLP = BASE + P
    WARN = []
    S = ccy_symbol(d["company"].get("reporting_currency", "USD")); PS = ccy_symbol(d["reaction"].get("price_currency") or d["company"].get("reporting_currency", "USD"))
    cad = d["period"].get("cadence") or (sorted(PER.cadence_of(P)) or ["Q"])[0]

    def pct(a, b): return None if (a is None or b is None or b == 0) else (a - b) / abs(b) * 100
    def bp(a, b): return None if (a is None or b is None) else (a - b) * 100
    def mid(r): return None if not r else (r[0] + r[1]) / 2
    def crosses(a, b): return a is not None and b is not None and (a * b < 0 or b == 0)

    def f_bn(x):
        a_ = abs(x); body = f"{S}{q(a_ / 1e6, 2):,.2f}T" if a_ >= 1e6 else f"{S}{q(a_ / 1000, 2):,.2f}B"
        return body if x >= 0 else f"({body})"
    def f_m(x): return f"{q(x, 0):,.0f}" if x >= 0 else f"({q(abs(x), 0):,.0f})"
    def f_int(x): return f"{q(x, 0):,.0f}"
    def f_k(x): return f"{S}{q(x, 0):,.0f}k"
    def f_knum(x): return f"{q(x, 0):,.0f}"
    def f_pct1(x): return f"{q(x, 1):.1f}%"
    def f_pct2(x): return f"{q(x, 2):.2f}%"
    def f_usd2(x): return f"{S}{q(x, 2):,.2f}"
    def f_usd3(x): return f"{S}{q(x, 3):,.3f}"
    def f_num1(x): return f"{q(x, 1):,.1f}"
    def f_num2(x): return f"{q(x, 2):,.2f}"
    def f_days(x): return f"{q(x, 0):,.0f}"
    def f_x(x): return f"{q(x, 1):.1f}x"
    def f_cm(x): return f"{q(x / 1e6, 2):,.2f}M"
    def f_cb(x): return f"{q(x / 1e9, 2):,.2f}B"
    def f_mbn(x): return f_bn(x)
    FMT = {"bn": f_bn, "m": f_m, "int": f_int, "k": f_k, "knum": f_knum, "pct1": f_pct1, "pct2": f_pct2, "usd2": f_usd2, "usd3": f_usd3,
           "num1": f_num1, "num2": f_num2, "days": f_days, "x": f_x, "cm": f_cm, "cb": f_cb, "mbn": f_mbn}
    NEG_SELF = {"bn", "m", "mbn"}
    def fmt(x, kind):
        if x is None: return "—"
        if x < 0 and kind not in NEG_SELF: return f"({FMT[kind](abs(x))})"
        return FMT[kind](x)
    def pcn(x, dp=1): return "—" if x is None else (f"({q(abs(x), dp)})%" if x < 0 else f"+{q(x, dp)}%")
    def bps(x): return "—" if x is None else (f"({q(abs(x), 0)}) bps" if x < 0 else f"+{q(x, 0)} bps")
    def rng(r, kind):
        if not r: return "—"
        if r[0] == r[1]: return fmt(r[0], kind)
        a_, b_ = fmt(r[0], kind), fmt(r[1], kind)
        if "(" not in a_ + b_ and a_[-1:] == b_[-1:] and a_[-1:] in "BTMk%x" and not a_[-1:].isdigit(): a_ = a_[:-1]
        if "(" not in a_ + b_ and S.strip() and b_.startswith(S) and a_.startswith(S): b_ = b_[len(S):]
        return f"{a_}⁠–​{b_}"
    def rng_approx(r, kind): return "—" if not r else (f"~{fmt(r[0], kind)}" if r[0] == r[1] else rng(r, kind))

    def row_fmt(m, extra):
        k = m["fmt"]; xs = [abs(x) for x in (m["values"] or []) + (m.get("base") or []) + extra if isinstance(x, (int, float))]
        mx = max(xs) if xs else 0
        if k in ("int", "knum", "num1") and m.get("unit") == "count":
            if mx >= 1e9: return "cb"
            if mx >= 1e7: return "cm"
        if k == "m" and mx >= 1e5: return "mbn"
        return k

    out = {"version": VERSION, "metrics": {}, "tokens": {}, "warnings": WARN, "large_surprise": []}
    T = out["tokens"]
    T["period.seq_label"] = PER.seq_label(cad) or "sequential"

    for mid_, m in M.items():
        kind = m.get("kind", "level"); vals = list(m["values"]); base = list(m.get("base") or [None] * len(BASE))
        if len(vals) != n: envelope("compute", f"metric '{mid_}' has {len(vals)} values for {n} periods")
        if len(base) != len(BASE): base = (base + [None] * len(BASE))[:len(BASE)]
        full = dict(zip(ALLP, base + vals))
        cons = m.get("consensus"); gp = m.get("guide_prior"); gn = m.get("guide_new"); cn = m.get("consensus_next")
        if cons and cons.get("mean") is None: WARN.append(f"{mid_}: consensus present without a mean; treated as no consensus"); cons = None
        k = row_fmt(m, [x for x in ((cons or {}).get("mean"), (cn or {}).get("mean")) if x is not None] + list(gp or []) + list(gn or []))
        cur = P[i]; ya = PER.prior_year_of(cur)
        SC = m.get("stated_yoy"); sc_yago = None
        if SC and full.get(cur) is not None:
            cv, cu = SC["value"], SC["unit"]
            sc_yago = full[cur] - cv / 100 if cu == "bps" else (full[cur] / (1 + cv / 100) if cu == "pct" else full[cur] - cv)
            if SC.get("mode", "replace") == "replace" and ya in full: full[ya] = sc_yago
        def chg(a_, b_): return pct(a_, b_) if kind == "level" else bp(a_, b_)
        def yoy_of(p): return chg(full.get(p), full.get(PER.prior_year_of(p)))
        def seq_of(p):
            pp = PER.prior_period_of(p)
            return chg(full.get(p), full.get(pp)) if cad in ("Q", "H") and pp in full else None
        yoy = [yoy_of(p) for p in P]; qoq = [seq_of(p) for p in P]
        nat = m.get("native_yoy") or [None] * n
        replace_sc = bool(SC and SC.get("mode", "replace") == "replace")
        yoy_display = [(yoy[j] if (replace_sc and j == i) or nat[j] is None else nat[j]) for j in range(n)]
        for j, p in enumerate(P):
            if nat[j] is not None and yoy[j] is not None and kind == "level" and abs(nat[j] - yoy[j]) > 0.5:
                WARN.append(f"{mid_} {p}: InSync native Y/Y {nat[j]:.1f}% differs from the level-computed {yoy[j]:.1f}% (check basis)")
        act = full.get(cur)
        surprise = None if (act is None or not cons) else chg(act, cons["mean"])
        vs_guide = None if (act is None or not gp) else chg(act, mid(gp))
        nx = d["period"].get("next_label"); ya_next = full.get(PER.prior_year_of(nx)) if nx else None
        r = {"actual": act, "prior": full.get(PER.prior_period_of(cur)), "yago": full.get(ya), "yoy": yoy, "qoq": qoq, "yoy_display": yoy_display,
             "surprise": surprise, "vs_guide_mid": vs_guide, "guide_prior_mid": mid(gp), "guide_new_mid": mid(gn),
             "new_mid_vs_actual": None if (not gn or act is None) else chg(mid(gn), act),
             "new_mid_vs_yago_next": None if (not gn or ya_next is None) else chg(mid(gn), ya_next),
             "new_mid_vs_cons_next": None if (not gn or not cn) else chg(mid(gn), cn["mean"]),
             "vs_guide_low": None if (act is None or not gp) else act - gp[0], "row_fmt": k}
        better = m.get("better"); band = m.get("inline_band", 1.0 if kind == "level" else 10.0)
        if surprise is None or not cons: verdict = "Not comparable"
        elif better not in ("higher", "lower"): verdict = "UNDECLARED"; WARN.append(f"POLARITY UNDECLARED: {mid_} ({m.get('resolved_code')}) is graded but has no declared direction")
        elif abs(surprise) < band: verdict = "IN LINE"
        else: verdict = "BEAT" if ((surprise > 0) == (better == "higher")) else "MISS"
        if cons and surprise is not None and cons.get("thin") and verdict != "UNDECLARED": verdict += "†"
        r["verdict"] = verdict
        r["guide_pos"] = None if (act is None or not gp) else ("below range" if act < min(gp) else ("above range" if act > max(gp) else "within range"))
        if surprise is not None and ((kind == "level" and abs(surprise) > 15) or (kind == "rate" and abs(surprise) > 200)):
            out["large_surprise"].append({"id": mid_, "label": m["label"], "code": m.get("resolved_code"), "cons_code": cons.get("code"), "surprise": surprise})
        if SC:
            r["stated_yoy"] = {"yago": sc_yago, "mode": SC.get("mode", "replace"), "basis": SC.get("basis")}
            if SC.get("mode") == "alongside" and sc_yago is not None:
                ay = chg(act, sc_yago); r["stated_yoy"]["yoy"] = ay
                T[f"{mid_}.alt_yago"] = fmt(sc_yago, k); T[f"{mid_}.alt_yoy"] = pcn(ay) if kind == "level" else bps(ay)
                T[f"{mid_}.alt_change"] = fmt(act - sc_yago, k) if kind == "level" else bps(ay)
            T[f"{mid_}.alt_label"] = SC.get("basis", ""); T[f"{mid_}.basis_yago"] = fmt(sc_yago, k)
        out["metrics"][mid_] = r
        ka = k
        if k == "usd2" and cons and act is not None and q(act, 2) == q(cons["mean"], 2) and abs(act - cons["mean"]) > 1e-9: ka = "usd3"
        kc = ka
        def delta(x, a_=None, b_=None):
            if x is None: return "—"
            if a_ is not None and b_ is not None and kind == "level" and crosses(a_, b_): return "n.m."
            return pcn(x) if kind == "level" else bps(x)
        T[f"{mid_}.actual"] = m.get("actual_text") or fmt(act, ka)
        T[f"{mid_}.prior"] = fmt(r["prior"], k); T[f"{mid_}.yago"] = fmt(r["yago"], k)
        T[f"{mid_}.yoy"] = ("n.m." if (kind == "level" and nat[i] is None and crosses(act, full.get(ya))) else delta(yoy_display[i]))
        T[f"{mid_}.qoq"] = delta(qoq[i], act, r["prior"]); T[f"{mid_}.seq"] = T[f"{mid_}.qoq"]
        T[f"{mid_}.surprise"] = delta(surprise, act, (cons or {}).get("mean")); T[f"{mid_}.verdict"] = r["verdict"]; T[f"{mid_}.guide_pos"] = r["guide_pos"] or "—"
        T[f"{mid_}.consensus"] = "—" if not cons else fmt(cons["mean"], kc)
        T[f"{mid_}.consensus_n"] = "" if not cons else str(cons.get("count", ""))
        T[f"{mid_}.consensus_low"] = "—" if not cons or cons.get("low") is None else fmt(cons["low"], kc)
        T[f"{mid_}.consensus_high"] = "—" if not cons or cons.get("high") is None else fmt(cons["high"], kc)
        T[f"{mid_}.guide_prior"] = m.get("guide_prior_text") or rng_approx(gp, k)
        T[f"{mid_}.guide_new"] = m.get("guide_new_text") or rng_approx(gn, k)
        T[f"{mid_}.guide_prior_lo"] = "—" if not gp else fmt(gp[0], k); T[f"{mid_}.guide_prior_hi"] = "—" if not gp else fmt(gp[1], k)
        T[f"{mid_}.guide_new_lo"] = "—" if not gn else fmt(gn[0], k); T[f"{mid_}.guide_new_hi"] = "—" if not gn else fmt(gn[1], k)
        T[f"{mid_}.guide_new_mid"] = "—" if not gn else fmt(mid(gn), k)
        T[f"{mid_}.vs_guide"] = delta(vs_guide, act, mid(gp))
        T[f"{mid_}.vs_guide_low"] = "—" if r["vs_guide_low"] is None else fmt(abs(r["vs_guide_low"]), k)
        T[f"{mid_}.consensus_next"] = "—" if not cn else fmt(cn["mean"], k)
        T[f"{mid_}.consensus_next_n"] = "" if not cn else str(cn.get("count", ""))
        T[f"{mid_}.new_mid_vs_actual"] = delta(r["new_mid_vs_actual"]); T[f"{mid_}.new_mid_vs_yago_next"] = delta(r["new_mid_vs_yago_next"])
        T[f"{mid_}.new_mid_vs_cons_next"] = delta(r["new_mid_vs_cons_next"])
        for j, plabel in enumerate(P):
            T[f"{mid_}.v.{plabel}"] = fmt(full.get(plabel), k if plabel != cur else ka)
            T[f"{mid_}.yoy.{plabel}"] = ("n.m." if (kind == "level" and nat[j] is None and crosses(full.get(plabel), full.get(PER.prior_year_of(plabel)))) else delta(yoy_display[j]))

    def v(mid_, j): return M[mid_]["values"][j]
    def b(mid_, j): return M[mid_]["base"][j]
    def vp(mid_, label):
        full_ = dict(zip(ALLP, (M[mid_].get("base") or [None] * len(BASE)) + M[mid_]["values"])); return full_.get(PER.norm_period(label))
    def fy_actual(mid_, yy):
        parts = PER.fy_parts(cad, 2000 + int(yy))
        xs = [vp(mid_, p) for p in parts]
        return None if any(x is None for x in xs) else sum(xs)
    ns = {"v": v, "b": b, "vp": vp, "pct": pct, "bp": bp, "mid": mid, "abs": abs, "sum": sum, "min": min, "max": max, "M": M, "F": d.get("forward", {}), "fy": fy_actual,
          "P": P, "cur": P[i], "prior_year": PER.prior_year_of, "prior_period": PER.prior_period_of}
    out["derived"] = {}
    for dv in cfg.get("derived", []):
        try: val = eval(dv["expr"], {"__builtins__": {}}, ns)
        except Exception as e: val = None; WARN.append(f"derived {dv['id']}: {type(e).__name__}: {e}")
        out["derived"][dv["id"]] = val
        k = dv.get("fmt", "num1")
        T[f"d.{dv['id']}"] = (pcn(val) if k == "pcn" else bps(val) if k == "bps" else pcn(val, 0) if k == "pcn0" else fmt(val, k)) if val is not None else "—"
        T[f"d.{dv['id']}.abs"] = ("—" if val is None else (f"{q(abs(val), 0)}" if k in ("bps", "pcn0") else f"{q(abs(val), 1)}" if k == "pcn" else fmt(abs(val), k)))

    out["implied"] = {}
    for im in cfg.get("implied", []):
        a = out["metrics"][im["a"]]["guide_new_mid"]; bb = out["metrics"][im["b"]]["guide_new_mid"]
        val = None if (a is None or bb is None) else a * bb * im.get("scale", 1)
        out["implied"][im["id"]] = val; T[f"i.{im['id']}"] = fmt(val, im.get("fmt", "bn"))

    R = d["reaction"]; dates = R.get("dates") or []; ix = {dt: j for j, dt in enumerate(dates)}
    sym = R["symbol"]; idx = R.get("index", "SPY")
    if not dates or sym not in (R.get("closes") or {}): envelope("compute", f"no price history for {sym} in raw/insync_prices.json")
    for key, side in (("pre_date", -1), ("post_date", 1), ("day2_date", 1)):
        dtv = R.get(key)
        if dtv is None:
            if key == "day2_date": continue
            envelope("compute", f"reaction.{key} is not set: fill it from the release timing (data-collection §8) and rerun")
        if dtv not in ix:
            cand = [x for x in dates if (x < dtv if side < 0 else x > dtv)]
            if not cand: envelope("compute", f"reaction.{key} {dtv} is outside the price history ({dates[0]}..{dates[-1]})")
            new = cand[-1] if side < 0 else cand[0]; WARN.append(f"reaction.{key} {dtv} is not a trading day in the price file: using {new}"); R[key] = new
    def close(s, dt): return (R["closes"].get(s) or [None] * len(dates))[ix[dt]]
    def ret(s, a_, b_): return pct(close(s, b_), close(s, a_))
    rx = {"pre_price": close(sym, R["pre_date"]), "post_price": close(sym, R["post_date"]), "current_price": close(sym, R["asof_date"]) if R.get("asof_date") in ix else None,
          "chg": ret(sym, R["pre_date"], R["post_date"]), "index_chg": ret(idx, R["pre_date"], R["post_date"]) if idx in R["closes"] else None,
          "day2": ret(sym, R["post_date"], R["day2_date"]) if R.get("day2_date") else None,
          "index_day2": ret(idx, R["post_date"], R["day2_date"]) if R.get("day2_date") and idx in R["closes"] else None,
          "since": ret(sym, R["pre_date"], R["asof_date"]) if R.get("asof_date") in ix else None, "index_since": ret(idx, R["pre_date"], R["asof_date"]) if R.get("asof_date") in ix and idx in R["closes"] else None,
          "peers": {p: ret(p, R["pre_date"], R["post_date"]) for p in R.get("peers", [])},
          "peers_day2": {p: ret(p, R["post_date"], R["day2_date"]) for p in R.get("peers", [])} if R.get("day2_date") else {}}
    vols = R.get("volumes", {}).get(sym)
    if vols:
        pre_idx = ix[R["pre_date"]]; base_v = [x for x in vols[max(0, pre_idx - 5):pre_idx] if x]; avg = sum(base_v) / len(base_v) if base_v else None
        pv = vols[ix[R["post_date"]]]; dv2 = vols[ix[R["day2_date"]]] if R.get("day2_date") else None
        rx["vol_reaction_x"] = None if (not avg or pv is None) else pv / avg
        rx["vol_day2_x"] = None if (not avg or dv2 is None) else dv2 / avg
    if R.get("buyback_avg_price") and rx["current_price"]: rx["price_vs_buyback"] = pct(rx["current_price"], R["buyback_avg_price"])
    out["reaction"] = rx
    fp = lambda x: "—" if x is None else f"{PS}{q(x, 2):,.2f}"
    T.update({"reaction.pre_price": fp(rx["pre_price"]), "reaction.post_price": fp(rx["post_price"]), "reaction.current_price": fp(rx["current_price"]),
              "reaction.chg": pcn(rx["chg"]), "reaction.index_chg": pcn(rx["index_chg"]), "reaction.day2": pcn(rx["day2"]), "reaction.index_day2": pcn(rx["index_day2"]),
              "reaction.since": pcn(rx["since"]), "reaction.index_since": pcn(rx["index_since"]),
              "reaction.vol_reaction_x": "—" if rx.get("vol_reaction_x") is None else f"{q(rx['vol_reaction_x'], 1)}", "reaction.vol_day2_x": "—" if rx.get("vol_day2_x") is None else f"{q(rx['vol_day2_x'], 1)}",
              "reaction.vs_buyback": "—" if rx.get("price_vs_buyback") is None else f"{q(abs(rx['price_vs_buyback']), 1)}", "reaction.index": idx})
    for p, val in rx["peers"].items(): T[f"reaction.peer.{p}"] = pcn(val)
    for p, val in rx["peers_day2"].items(): T[f"reaction.peer_day2.{p}"] = pcn(val)

    VL = dict(d.get("valuation", {}))
    def eps_proxy():
        fw = d.get("forward", {}); e0, e1 = (fw.get("eps_fy0") or {}).get("post"), (fw.get("eps_fy1") or {}).get("post")
        if e0 and e1 and e0 > 0 and e1 > 0: return (min(e0, e1), max(e0, e1)), "forward EPS"
        em = next((k for k, m_ in M.items() if str(m_.get("resolved_code", "")).split("-")[0] in ("IS_P_128", "IS_P_127")), None)
        if not em: return None, None
        nper = {"Q": 4, "H": 2}.get(cad, 1); xs = [x for x in (M[em].get("values") or [])[-nper:] if x is not None]
        if len(xs) < nper or sum(xs) <= 0: return None, None
        return (sum(xs), sum(xs)), "trailing reported EPS"
    pe_v = VL.get("pe_adj_ntm"); px_ = rx.get("current_price"); band, eb = eps_proxy()
    out["valuation_check"] = None
    if pe_v is not None and px_ and band and (d["reaction"].get("price_currency") or d["company"].get("reporting_currency")) == d["company"].get("reporting_currency", "USD"):
        lo_, hi_ = px_ / band[1], px_ / band[0]
        ok_ = lo_ / 4 <= pe_v <= hi_ * 4
        out["valuation_check"] = {"pe_vendor": pe_v, "implied_low": lo_, "implied_high": hi_, "basis": eb, "ok": ok_}
        if not ok_:
            WARN.append(f"VALUATION IMPLAUSIBLE: InSync P/E (NTM) {pe_v:.2f}x vs {lo_:.1f}x-{hi_:.1f}x implied by the price {px_:,.2f} and {eb}: "
                        "market cap, EV, NTM multiples and the own-history table print as dashes (verify the vendor valuation before quoting any)")
            for k_ in ("market_cap_m", "ev_m", "pe_adj_ntm", "ev_ebitda_adj_ntm", "pb"): VL[k_] = None
            VL["history"] = None
    T.update({"val.mcap": f_bn(VL["market_cap_m"]) if VL.get("market_cap_m") is not None else "—", "val.ev": f_bn(VL["ev_m"]) if VL.get("ev_m") is not None else "—",
              "val.pe_ntm": f_x(VL["pe_adj_ntm"]) if VL.get("pe_adj_ntm") is not None else "—", "val.ev_ebitda_ntm": f_x(VL["ev_ebitda_adj_ntm"]) if VL.get("ev_ebitda_adj_ntm") is not None else "—",
              "val.pb": f"{q(VL['pb'], 2):.2f}x" if VL.get("pb") is not None else "—"})
    VK = {"P/E_Adj_Ratio": "pe", "P/E_Ratio": "pe_gaap", "EV/EBITDA_Adj": "ev_ebitda", "EV/EBITDA": "ev_ebitda_gaap", "P/B_Ratio": "pb", "EV/Sales": "ev_sales"}
    out["valuation_history"] = {}
    for mname, h in (VL.get("history") or {}).items():
        kk = VK.get(mname)
        if not kk or h.get("ltm") is None: continue
        y1, y3 = h.get("y1") or {}, h.get("y3") or {}
        o = {"ltm": h["ltm"], "avg1y": y1.get("avg"), "avg3y": y3.get("avg"), "min3y": y3.get("min"), "max3y": y3.get("max")}
        o["vs1y"] = pct(o["ltm"], o["avg1y"]); o["vs3y"] = pct(o["ltm"], o["avg3y"])
        out["valuation_history"][kk] = o
        fx = lambda x: "—" if x is None else f_x(x)
        T.update({f"val.hist.{kk}.ltm": fx(o["ltm"]), f"val.hist.{kk}.avg1y": fx(o["avg1y"]), f"val.hist.{kk}.avg3y": fx(o["avg3y"]),
                  f"val.hist.{kk}.min3y": fx(o["min3y"]), f"val.hist.{kk}.max3y": fx(o["max3y"]), f"val.hist.{kk}.vs1y": pcn(o["vs1y"]), f"val.hist.{kk}.vs3y": pcn(o["vs3y"])})

    out["forward"] = {}
    for key, f in d.get("forward", {}).items():
        c_ = pct(f["post"], f["pre"]); out["forward"][key] = {"chg": c_, "code": f.get("pre_code")}
        k = f.get("fmt", "usd3")
        T[f"fwd.{key}.pre"] = fmt(f["pre"], k); T[f"fwd.{key}.post"] = fmt(f["post"], k); T[f"fwd.{key}.chg"] = "n.m." if crosses(f["post"], f["pre"]) else pcn(c_)
        T[f"fwd.{key}.pre_n"] = str(f.get("pre_count", "")); T[f"fwd.{key}.post_n"] = str(f.get("post_count", "")); T[f"fwd.{key}.post_down"] = str(f.get("post_down", ""))
        T[f"fwd.{key}.label"] = f.get("label", key)

    GL = cfg.get("guide_vs_frozen"); GL = [GL] if isinstance(GL, dict) else (GL or [])
    out["guide_vs_frozen"] = []
    def _rtxt(r_):
        lo, hi = r_
        if lo is not None and hi is not None: return pcn(lo, 0) if lo == hi else f"{pcn(lo, 0)} to {pcn(hi, 0)}"
        return f"above {pcn(lo, 0)}" if lo is not None else f"below {pcn(hi, 0)}"
    def _pos(x, r_):
        if x is None or r_ is None: return "—"
        lo, hi = r_
        return "below the guided range" if (lo is not None and x < lo) else ("above the guided range" if (hi is not None and x > hi) else "within the guided range")
    FYA = {x.get("metric"): x for x in ((d.get("fy_guide") if isinstance(d.get("fy_guide"), list) else [d["fy_guide"]] if d.get("fy_guide") else []))}
    for gi, G in enumerate(GL):
        gp_ = "guide." if gi == 0 else f"guide{gi+1}."
        if G.get("forward") not in d.get("forward", {}): WARN.append(f"guide_vs_frozen: forward series {G.get('forward')} missing (not comparable or not fetched)"); continue
        mrow = M[G["metric"]]; fwd = d["forward"][G["forward"]]
        fpre, fpost = fwd["pre"], fwd["post"]
        if fwd.get("pre_code") and mrow.get("resolved_code") and fwd["pre_code"] != mrow["resolved_code"]:
            WARN.append(f"guide_vs_frozen {G['metric']}: the forward consensus is on {fwd['pre_code']}, the graded line item is {mrow['resolved_code']}; vs-guide left blank")
            fpre = fpost = None
        if G.get("mode") == "growth":
            row = FYA.get(G["metric"], {})
            yy = f"{int(d['period']['fy_label'][2:]) - 1:02d}"
            bv = fy_actual(G["metric"], yy) if G.get("base", "fy_prior") == "fy_prior" else out["derived"].get(G["base"])
            gtxt = G.get("guide_text") or row.get("new_text") or mrow.get("guide_fy_new_text"); ptxt = G.get("prior_text") or row.get("prior_text") or mrow.get("guide_fy_prior_text")
            gr, pr_ = parse_growth_words(gtxt), parse_growth_words(ptxt)
            o = {"base": bv, "implied": pct(fpre, bv), "implied_post": pct(fpost, bv), "range": gr, "prior_range": pr_}
            o["pos"] = _pos(o["implied"], gr); o["pos_post"] = _pos(o["implied_post"], gr); o["prior_pos"] = _pos(o["implied"], pr_)
            out["guide_vs_frozen"].append(o)
            if bv is None: WARN.append(f"guide_vs_frozen {G['metric']}: FY{yy} actual incomplete (needs {', '.join(PER.fy_parts(cad, 2000 + int(yy)))})")
            if gr is None: WARN.append(f"guide_vs_frozen {G['metric']}: no growth wording parsed from '{gtxt}'")
            T.update({gp_ + "implied_growth": pcn(o["implied"]), gp_ + "implied_growth_post": pcn(o["implied_post"]), gp_ + "guide_range": _rtxt(gr) if gr else "—",
                      gp_ + "prior_range": _rtxt(pr_) if pr_ else "—", gp_ + "pos": o["pos"], gp_ + "pos_post": o["pos_post"], gp_ + "prior_pos": o["prior_pos"],
                      gp_ + "base": fmt(bv, fwd.get("fmt", mrow["fmt"])), gp_ + "base_label": f"FY{yy}", gp_ + "guide_text": gtxt or "—",
                      gp_ + "frozen": fmt(fwd["pre"], fwd.get("fmt", "usd3")), gp_ + "frozen_n": str(fwd.get("pre_count", "")), gp_ + "metric": mrow["label"],
                      gp_ + "post": fmt(fwd["post"], fwd.get("fmt", "usd3"))})
            FYA.setdefault(G["metric"], {})["_growth"] = (gp_, o)
            continue
        gn_ = mrow.get("guide_fy_new") if G.get("basis") == "fy" else mrow.get("guide_new")
        if gn_:
            o = {"mid": pct(mid(gn_), fpre), "high": pct(gn_[1], fpre), "low": pct(gn_[0], fpre), "mid_vs_post": pct(mid(gn_), fpost)}
            out["guide_vs_frozen"].append(o)
            T[gp_ + "mid_vs_frozen"] = pcn(o["mid"]); T[gp_ + "high_vs_frozen"] = pcn(o["high"]); T[gp_ + "low_vs_frozen"] = pcn(o["low"]); T[gp_ + "mid_vs_post"] = pcn(o["mid_vs_post"])
            T[gp_ + "frozen"] = fmt(fwd["pre"], fwd.get("fmt", "usd3")); T[gp_ + "frozen_n"] = str(fwd.get("pre_count", "")); T[gp_ + "metric"] = mrow["label"]

    FYL = d.get("fy_guide"); FYL = [FYL] if isinstance(FYL, dict) else (FYL or [])
    out["fy_guide"] = []
    for fi, FY in enumerate(FYL):
        px = "fy." if fi == 0 else f"fy{fi+1}."
        k = FY.get("fmt", "int"); o = {}
        o["cut_vs_orig"] = pct(mid(FY["new"]), FY["orig_target"]) if (FY.get("orig_target") and FY.get("new")) else None
        o["cut_units"] = (mid(FY["prior"]) - mid(FY["new"])) if (FY.get("prior") and FY.get("new")) else None
        o["change"] = (mid(FY["new"]) - mid(FY["prior"])) if (FY.get("prior") and FY.get("new")) else None
        o["change_pct"] = pct(mid(FY["new"]), mid(FY["prior"])) if (FY.get("prior") and FY.get("new")) else None
        o["direction"] = None if o["change"] is None else ("raised" if o["change"] > 0 else "lowered" if o["change"] < 0 else "reaffirmed")
        o["implied_next"] = [x - FY["ytd_value"] for x in FY["new"]] if (FY.get("ytd_value") is not None and FY.get("new")) else None
        mrow = M.get(FY.get("metric") or "", {}); Fd = d.get("forward", {})
        fk = FY.get("forward") or next((c for c in ([{"EPS": "eps", "SALES": "rev"}.get(mrow.get("consensus_metric") or "", (mrow.get("consensus_metric") or "").lower()) + "_fy0"] if mrow.get("consensus_metric") else [])
                                        + ({"revenue": ["rev_fy0"], "eps": ["eps_fy0"]}.get(FY.get("metric"), [])) if c in Fd), None)
        cf, cfn, cs = None, None, None
        if fk: cf, cfn, cs = Fd[fk]["pre"], Fd[fk]["post"], "InSync"; o["cons_counts"] = (Fd[fk].get("pre_count"), Fd[fk].get("post_count"))
        elif mrow.get("consensus_fy"): cf, cs = mrow["consensus_fy"]["mean"], "InSync"; o["cons_counts"] = (mrow["consensus_fy"].get("count"), (mrow.get("consensus_fy_next") or {}).get("count"))
        if cfn is None and mrow.get("consensus_fy_next"): cfn = mrow["consensus_fy_next"]["mean"]
        o["consensus_frozen"], o["consensus_post"], o["consensus_src"] = cf, cfn, cs
        nm = mid(FY.get("new")) if FY.get("new") else None; pm = mid(FY.get("prior")) if FY.get("prior") else None
        kd = mrow.get("kind", "level")
        dlt = (lambda a_, b_: pct(a_, b_)) if kd == "level" else (lambda a_, b_: bp(a_, b_))
        o["new_mid_vs_cons"] = dlt(nm, cf) if (nm is not None and cf is not None) else None
        o["prior_mid_vs_cons"] = dlt(pm, cf) if (pm is not None and cf is not None) else None
        o["cons_pos"] = _pos(cf, FY["new"]) if (cf is not None and FY.get("new")) else "—"
        if cf is not None:
            n0 = (o.get("cons_counts") or (None, None))[0]
            T[px + "consensus"] = fmt(cf, k) + (f" ({n0})" if n0 else ""); T[px + "consensus_src"] = cs
            gz = FYA.get(FY.get("metric"), {}).get("_growth")
            if gz: T[px + "consensus_growth"] = T[gz[0] + "implied_growth"]
        if FY.get("consensus_next") is None and cfn is not None:
            FY["consensus_next"] = cfn; n1 = (o.get("cons_counts") or (None, None))[1]
            FY.setdefault("consensus_next_tag", "InSync post-print" + (f" ({n1})" if n1 else ""))
        T[px + "new_mid_vs_cons"] = (pcn(o["new_mid_vs_cons"]) if kd == "level" else bps(o["new_mid_vs_cons"])) if o["new_mid_vs_cons"] is not None else "—"
        T[px + "prior_mid_vs_cons"] = (pcn(o["prior_mid_vs_cons"]) if kd == "level" else bps(o["prior_mid_vs_cons"])) if o["prior_mid_vs_cons"] is not None else "—"
        T[px + "cons_pos"] = o["cons_pos"]
        out["fy_guide"].append(o)
        T.update({px + "prior": FY.get("prior_text") or rng(FY.get("prior"), k), px + "new": FY.get("new_text") or rng(FY.get("new"), k), px + "orig": fmt(FY.get("orig_target"), k), px + "cut_vs_orig": pcn(o["cut_vs_orig"]),
                  px + "cut_units": fmt(o["cut_units"], k) if o["cut_units"] is not None else "—", px + "implied_next": rng(o["implied_next"], k) if o["implied_next"] else "—",
                  px + "ytd": FY.get("ytd_text", "—"), px + "consensus_next": fmt(FY.get("consensus_next"), k) if FY.get("consensus_next") is not None else "—", px + "label": FY["label"],
                  px + "change": ("—" if o["change"] is None else ("+" if o["change"] > 0 else "") + fmt(o["change"], k)), px + "change_pct": pcn(o["change_pct"]), px + "direction": o["direction"] or "—"})

    for k2, v2 in d.get("facts", {}).items(): T[f"f.{k2}"] = str(v2)
    C = d["company"]; PD = d["period"]
    if cfg.get("primary_kpi") in M: T["kpi.primary"] = M[cfg["primary_kpi"]]["label"]
    T.update({"co.name": C["name"], "co.ticker": C["ticker"], "period": PD["label"], "period.next": PD["next_label"], "period.fy": PD["fy_label"],
              "consensus.frozen_date": d["consensus"]["frozen_date"], "consensus.post_date": d["consensus"]["post_print_date"], "consensus.source": d["consensus"]["frozen_source"]})
    out["fy_rows"] = FYL
    json.dump(out, open(os.path.join(W, "computed.json"), "w", encoding="utf-8"), indent=1, default=str, ensure_ascii=False)
    print(f"computed.json written: {len(out['metrics'])} metrics, {len(T)} tokens")
    for w in WARN: print("WARN:", w, file=sys.stderr)

if __name__ == "__main__": guarded("compute", main)
