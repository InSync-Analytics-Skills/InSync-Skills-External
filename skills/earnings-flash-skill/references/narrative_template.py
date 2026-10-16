#!/usr/bin/env python3
"""
narrative_template.py - v11. Two jobs, both deterministic.

1. fact_sheet()  - turns computed.json + input.json into the one sheet the narrative is written
                   from: every scorecard cell as printed, the guidance with its status tag
                   (RAISED / LOWERED / MAINTAINED / NEW, worked out by comparing this guide with
                   the last one) and the pinned consensus beside it. The model writes prose from
                   this sheet only, so it never has to do arithmetic or compare two guides.
2. build_narrative() - a complete, rule-based narrative.json that passes every lint. It is the
                   fallback when a model's own prose fails twice (flash.py template --failed
                   replaces only the failed fields), and a draft for weaker models to edit.

The rule-based prose is plain and factual by design: number first, the comparator, the change.
It ranks What Matters by size of surprise (absolute Delta %), which is the house rule.
"""
import re
from decimal import Decimal, ROUND_HALF_UP

DASH = "—"
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_PLAIN = {"IS_P_101": "revenue", "IS_P_128": "adjusted EPS", "IS_P_127": "GAAP EPS",
          "IS_P_124": "adjusted net income", "IS_P_123": "net income", "IS_P_114": "adjusted EBITDA", "IS_P_113": "EBITDA",
          "IS_P_110": "adjusted EBIT", "IS_P_109": "EBIT", "CF_P_209": "core FFO per share",
          "CF_P_202": "FFO per share", "CF_P_110": "free cash flow", "CF_P_109": "capex"}
_ORDER = ["IS_P_101", "IS_P_128", "IS_P_127", "CF_P_209", "CF_P_202", "IS_P_114", "IS_P_110"]
_CHANGE = {"RAISED": 0, "LOWERED": 0, "WITHDRAWN": 0, "NEW": 1, "MAINTAINED": 2}
_EPS = ("IS_P_128", "IS_P_127")
_NO_CONS = re.compile(r"not available|\bn/a\b|unavailable|no (?:available )?consensus", re.I)


def rank_guides(guides, board, cap=5):
    core = set(board) | set(_ORDER) | {"IS_P_102", "IS_P_113", "IS_P_109", "IS_P_124", "IS_P_123"}
    on = lambda g: re.sub(r"-[YM]$", "", str(g["ltcode"])) in core
    near = lambda p: (0 if re.match(r"^\d[QH]\d{2}$", str(p)) else 1 if re.match(r"^(FY)?\d{2,4}$", str(p)) else 2)
    key = lambda g: (0 if on(g) else 1, _CHANGE.get(g.get("tag"), 3), 0 if g.get("consensus") else 1,
                     near(g["period"]), _period_key(g["period"]),
                     _ORDER.index(g["ltcode"]) if g["ltcode"] in _ORDER else 99)
    shown, dropped, seen = [], [], set()
    for g in sorted(guides, key=key):
        base = re.sub(r"-[YM]$", "", str(g["ltcode"]))
        fam = ("EPS" if base in _EPS else base, str(g["period"]))
        if len(shown) >= cap or fam in seen:
            dropped.append(g)
            continue
        seen.add(fam)
        shown.append(g)
    return shown, dropped


def _round2(x):
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def short_value(v):
    s = str(v or "").strip()
    m = re.match(r"^(-?)([^\d\-]*?)(-?[\d,]*\.?\d+)([A-Za-z%]*)$", s)
    if not m:
        return s
    sign, cur, num, suf = m.groups()
    if suf == "%":
        q = Decimal(num.replace(",", "")).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        return "%s%s%s%s" % (sign, cur, "{:,.1f}".format(q) if abs(q) >= 1000 else "%.1f" % q, suf)
    q = _round2(num.replace(",", ""))
    return "%s%s%s%s" % (sign, cur, "{:,.2f}".format(q) if abs(q) >= 1000 else "%.2f" % q, suf)


def day_month(iso):
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(iso or ""))
    return "%d %s" % (int(m.group(3)), _MONTHS[int(m.group(2)) - 1]) if m else str(iso or "")


def stale_asof(asof, call, days=30):
    a, b = (re.match(r"^\d{4}-\d{2}-\d{2}", str(x or "")) for x in (asof, call))
    if not a or not b:
        return day_month(asof) if asof else None
    import datetime as _dt
    gap = (_dt.date.fromisoformat(b.group(0)) - _dt.date.fromisoformat(a.group(0))).days
    return day_month(asof) if gap > days else None


def pre_cons(value, as_of):
    return "pre-print consensus of %s%s" % (value, " (last updated %s)" % as_of if as_of else "")


def fy_label(fp):
    s = str(fp)
    return "FY" + s[2:] if re.match(r"^\d{4}$", s) else s


def _nums(text):
    out = []
    for m in re.finditer(r"(\()?-?\$?\s?([\d,]*\.?\d+)\)?", str(text)):
        try:
            v = float(m.group(2).replace(",", ""))
        except ValueError:
            continue
        out.append(-v if m.group(1) else v)
    return out


_SCALE_SUFFIX = {"10^3": "K", "1000": "K", "1e3": "K", "10^6": "M", "1000000": "M", "1e6": "M",
                 "10^9": "B", "1000000000": "B", "1e9": "B"}
_CCY_SYMBOL = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}
_SYMBOLS = "$€£¥"


_UNIT_IN_NAME = re.compile(r"\((k|m|mm)?(t|tonnes|oz|lb|boe|bbl)\)", re.I)
_PENCE_IN_NAME = re.compile(r"pence|\(p\)|\bGBp\b", re.I)


def guide_display(text, scale, currency, name=None):
    out = _guide_display(text, scale, currency, name)
    m = _UNIT_IN_NAME.search(str(name or ""))
    k = str(scale or "").strip()
    if m and ((m.group(1) or "").lower() == "k") == (k in ("1000", "10^3", "1e3")):
        unit = {"tonnes": "t"}.get(m.group(2).lower(), m.group(2).lower())
        out = re.sub(r"(\d)([KMB]?)(\)?)(?=$|-)", lambda x: x.group(1) + x.group(2) + unit + (x.group(3) or ""), out)
    return out


def _guide_display(text, scale, currency, name=None):
    t = str(text or "").strip()
    suf = _SCALE_SUFFIX.get(str(scale or "").strip(), "")
    ccy = str(currency or "").strip().upper()
    want = _CCY_SYMBOL.get(ccy, (ccy + " ") if ccy else None)
    if ccy == "GBP" and _PENCE_IN_NAME.search(str(name or "")) and not suf:
        t = re.sub("[" + re.escape(_SYMBOLS) + "]\\s?", "", t)
        return "-".join(re.sub(r"([\d.,]+)(\)?)$", r"\1p\2", q.strip()) for q in re.split(r"\s+-\s+", t))
    if want is not None and re.search("[" + re.escape(_SYMBOLS) + "]", t):
        t = re.sub("[" + re.escape(_SYMBOLS) + "]\\s?", lambda m: want, t)
    parts = [p.strip() for p in re.split(r"\s+-\s+", t)]
    if suf == "K":
        nums = [re.search(r"(-?[\d,]*\.?\d+)\)?$", p) for p in parts]
        if all(nums) and all(abs(float(n.group(1).replace(",", ""))) >= 1000 for n in nums):
            parts = [p[:n.start(1)] + "%.2f" % (float(n.group(1).replace(",", "")) / 1000) + "M" + p[n.end(1):]
                     for p, n in zip(parts, nums)]
            suf = ""
    if suf:
        parts = [re.sub(r"(?<![A-Za-z\d.,])([\d.,]+)(\)?)$", r"\1" + suf + r"\2", p) for p in parts]
    return "-".join(parts)


def _lower_better(g):
    try:
        import run_math
        return run_math._lower_is_better(g.get("lineitem") or "", g.get("ltcode") or "")
    except Exception:
        return False


_MAG_F = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


def in_consensus_format(text, scale, cons_value):
    c = re.match(r"^(-?)([^\d\-]*?)(-?[\d,]*\.?\d+)([KMBT])$", short_value(cons_value))
    f = _factor(text, scale)
    if not c or not c.group(2) or f is None or "%" in str(text or ""):
        return None
    if not str(scale or "").strip() and not _TEXT_UNIT.search(str(text or "")):
        return None
    parts = [q.strip() for q in re.split(r"\s+-\s+", str(text or "").strip())]
    out = []
    for q in parts:
        n = re.fullmatch(r"\(?-?[^\d\-(]*?(-?[\d,]*\.?\d+)\)?", q)
        if not n:
            return None
        v = float(n.group(1).replace(",", "")) * f / _MAG_F[c.group(4)]
        if q.startswith("(") or q.startswith("-"):
            v = -abs(v)
        r = _round2(v)
        out.append("%s%s%s%s" % ("-" if r < 0 else "", c.group(2), "{:,.2f}".format(abs(r)), c.group(4)))
    return "-".join(out)


def _norm_name(s):
    return re.sub(r"[^a-z0-9%]+", " ", str(s or "").lower()).strip()


_SCALE_FACTOR = {"": 1.0, "1": 1.0, "10^3": 1e3, "1000": 1e3, "1e3": 1e3, "10^6": 1e6,
                 "1000000": 1e6, "1e6": 1e6, "10^9": 1e9, "1000000000": 1e9, "1e9": 1e9}


_TEXT_UNIT = re.compile(r"\d\s*(tn|trillion|bn|billion|b|mn|million|m|k|thousand)\b", re.I)
_TEXT_FACTOR = {"tn": 1e12, "trillion": 1e12, "bn": 1e9, "billion": 1e9, "b": 1e9,
                "mn": 1e6, "million": 1e6, "m": 1e6, "k": 1e3, "thousand": 1e3}


def _factor(text, scale):
    m = _TEXT_UNIT.search(str(text or ""))
    if m:
        return _TEXT_FACTOR[m.group(1).lower()]
    return _SCALE_FACTOR.get(str(scale or "").strip())


def _fp_norm(fp):
    t = str(fp or "").strip().upper()
    m = re.match(r"^(?:FY)?(\d{4}|\d{2})$", t)
    return ("20" + m.group(1)[-2:]) if m else t


def _tag(cur, prior, cur_scale=None, prior_scale=None):
    if prior is None:
        return "NEW"
    a, b = _nums(cur), _nums(prior)
    fa, fb = _factor(cur, cur_scale), _factor(prior, prior_scale)
    if fa is not None and fb is not None:
        a, b = [x * fa for x in a], [x * fb for x in b]
    if not a or not b:
        return "MAINTAINED" if _norm_name(cur) == _norm_name(prior) else "NEW"
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    if abs(ma - mb) <= 1e-9 * max(1.0, abs(mb)):
        return "MAINTAINED"
    return "RAISED" if ma > mb else "LOWERED"


def _rows(payload):
    if not isinstance(payload, dict):
        return []
    d = payload.get("data")
    if isinstance(d, dict):
        return [r for r in (d.get("guidance") or []) if isinstance(r, dict)]
    return []


def _k(r, long, short):
    return r.get(long, r.get(short))


def _period_key(sp):
    m = re.match(r"^R?\d*-?(\d)([QH])(\d{2})$", str(sp or ""))
    if m:
        return (int(m.group(3)), int(m.group(1)) * (3 if m.group(2) == "Q" else 6))
    m = re.match(r"^(?:FY)?(\d{2}|\d{4})$", str(sp or ""))
    if m:
        return (int(m.group(1)[-2:]), 12)
    return (-1, -1)


def source_periods_newest_first(rows):
    try:
        import run_math
        pdate = run_math._parse_guide_date
    except Exception:
        pdate = lambda x: None
    seen, best = [], {}
    for r in rows:
        sp = _k(r, "SOURCE_PERIOD", "sp")
        if not sp:
            continue
        if sp not in seen:
            seen.append(sp)
        dt = pdate(_k(r, "GUIDEDATE", "gd"))
        if dt and (sp not in best or dt > best[sp]):
            best[sp] = dt
    return sorted(seen, key=lambda sp: (best.get(sp).toordinal() if best.get(sp) else 0, _period_key(sp)),
                  reverse=True)


_ADJ_EPS = ("IS_P_128", "CF_P_209", "IS_P_124")
_PRINTED = re.compile(r"^([^\d\-]*)(-?[\d,]*\.?\d+)([A-Za-z]*)$")


def _one_off_note(computed, inp, rows):
    try:
        import run_math
    except Exception:
        return None
    per = str(computed.get("currentPeriod") or "")
    acts = ((inp.get("mcp_responses") or {}).get("get_actuals") or {})
    data = acts.get("data") if isinstance(acts, dict) else None
    vals = {}
    for r in data or []:
        if isinstance(r, dict) and str(_k(r, "FISCALPERIOD", "fp")) == per and _k(r, "LTCODE", "lt") in ("IS_P_101", "IS_P_102"):
            vals[_k(r, "LTCODE", "lt")] = (run_math.parse_mcp_value(_k(r, "VALUE", "vl")), _k(r, "VALUE", "vl"))
    if "IS_P_101" not in vals or "IS_P_102" not in vals:
        return None
    (rep, rep_s), (adj, adj_s) = vals["IS_P_101"], vals["IS_P_102"]
    if rep is None or adj is None or not rep or abs(rep - adj) / abs(rep) < 0.02:
        return None
    fin = [r for r in rows if r.get("block") == "financialMetrics"]
    rev = next((r for r in fin if r.get("ltcode") == "IS_P_101"), None)
    eps = next((r for r in fin if r.get("ltcode") in _ADJ_EPS + ("IS_P_127", "CF_P_202", "CF_P_203", "IS_P_123")), None)
    profit = [r for r in fin if r is not rev and r is not eps and not str(r.get("name", "")).lower().endswith("margin")
              and "margin" not in str(r.get("name", "")).lower()]
    rep_p, adj_p = short_value(rep_s), short_value(adj_s)
    a, b = _PRINTED.match(rep_p), _PRINTED.match(adj_p)
    if not a or not b or a.group(3) != b.group(3):
        return None
    diff = _round2(a.group(2).replace(",", "")) - _round2(b.group(2).replace(",", ""))
    return {"reported": rep_p, "ex_one_off": adj_p,
            "difference": "%s%s%s" % (a.group(1), "{:,.2f}".format(diff), a.group(3)),
            "revenue_row": rev.get("name") if rev else "Total Revenue",
            "profit_rows": [r.get("name") for r in profit],
            "eps_row": eps.get("name") if eps else None,
            "eps_excludes": bool(eps and eps.get("ltcode") in _ADJ_EPS)}


def fact_sheet(computed, inp, company_name=None):
    rows = []
    for block in ("financialMetrics", "operatingMetrics"):
        for m in computed.get(block) or []:
            rows.append({k: m.get(k) for k in ("name", "ltcode", "priorActual", "currentActual", "currentEstimate",
                                               "deltaAbsolute", "deltaPercent", "yoyChange", "beatMiss",
                                               "lowerIsBetter", "modelSourced", "sourceDerived")} | {"block": block})
    cons = {}
    for c in computed.get("guidanceConsensus") or []:
        cons[(c.get("ltcode"), str(c.get("fiscal_period")))] = c
    g = _rows((inp.get("mcp_responses") or {}).get("get_guidance"))
    sps = source_periods_newest_first(g)
    gsrc = inp.get("guidance_source") or "mcp"
    guides = []
    not_repeated = []
    if sps:
        newest = sps[0]
        prior_by = {}
        for sp in sps[1:]:
            for r in g:
                if _k(r, "SOURCE_PERIOD", "sp") == sp:
                    key = (_k(r, "LTCODE", "lt"), _fp_norm(_k(r, "FISCALPERIOD", "fp")))
                    prior_by.setdefault(key, r)
        cur_keys = {(_k(r, "LTCODE", "lt"), _fp_norm(_k(r, "FISCALPERIOD", "fp")))
                    for r in g if _k(r, "SOURCE_PERIOD", "sp") == newest}
        cur_lts = {k[0] for k in cur_keys}
        prior_lts = {k[0] for k in prior_by}
        moved_used = set()
        for r in g:
            if _k(r, "SOURCE_PERIOD", "sp") != newest:
                continue
            lt, fp = _k(r, "LTCODE", "lt"), str(_k(r, "FISCALPERIOD", "fp"))
            pr = prior_by.get((lt, _fp_norm(fp)))
            moved_from = None
            if pr is None and lt not in prior_lts:
                name = _norm_name(_k(r, "LINEITEM", "li"))
                hits = [(key, x) for key, x in prior_by.items()
                        if key[1] == _fp_norm(fp) and key[0] not in cur_lts and key not in moved_used
                        and name and _norm_name(_k(x, "LINEITEM", "li")) == name
                        and (x.get("SCALE") == r.get("SCALE")
                             or (_SCALE_FACTOR.get(str(x.get("SCALE") or "").strip()) is not None
                                 and _SCALE_FACTOR.get(str(r.get("SCALE") or "").strip()) is not None))
                        and x.get("CURRENCY") == r.get("CURRENCY")]
                if len(hits) == 1:
                    (moved_from, _), pr = hits[0][0], hits[0][1]
                    moved_used.add(hits[0][0])
            cur_t = _k(r, "GUIDE_TEXT", "gt")
            pri_t = _k(pr, "GUIDE_TEXT", "gt") if pr else None
            scale, ccy = r.get("SCALE"), r.get("CURRENCY")
            c = cons.get((lt, fp))
            q = re.match(r"^[1-4]Q(\d{2})$", fp)
            yr_twin = q and "%" not in str(cur_t) and next((x for x in g if _k(x, "SOURCE_PERIOD", "sp") != newest
                                                           and _k(x, "LTCODE", "lt") == lt and _k(x, "GUIDE_TEXT", "gt") == cur_t
                                                           and _fp_norm(_k(x, "FISCALPERIOD", "fp")) == "20" + q.group(1)), None)
            if yr_twin:
                c = None
            _cf = in_consensus_format(cur_t, scale, c["value"]) if c else None
            _pf = in_consensus_format(pri_t, pr.get("SCALE"), c["value"]) if (c and pr) else None
            guides.append({
                "ltcode": lt, "lineitem": _k(r, "LINEITEM", "li"), "period": fp, "period_label": fy_label(fp),
                "guide": _cf or guide_display(cur_t, scale, ccy, _k(r, "LINEITEM", "li")),
                "prior_guide": (_pf if _cf else None) or (guide_display(pri_t, pr.get("SCALE"), pr.get("CURRENCY"),
                                                                        _k(pr, "LINEITEM", "li")) if pr else None),
                "tag": _tag(cur_t, pri_t, scale, pr.get("SCALE") if pr else None),
                "guide_date": _k(r, "GUIDEDATE", "gd"),
                "consensus": short_value(c["value"]) if c else None,
                "consensus_as_of": stale_asof(c.get("asof"), computed.get("earningsDate")) if c else None})
            _gv, _f = _nums(cur_t), _factor(cur_t, scale)
            if c and _gv and _f:
                try:
                    import run_math
                    _cv = run_math.parse_mcp_value(c["value"])
                except Exception:
                    _cv = None
                _mid = sum(_gv) / len(_gv) * _f
                if _cv and _mid and max(abs(_mid / _cv), abs(_cv / _mid)) >= 50:
                    guides[-1]["unit_check"] = "%s is %.0fx away from its consensus %s" % (
                        guides[-1]["guide"], max(abs(_mid / _cv), abs(_cv / _mid)), guides[-1]["consensus"])
            if moved_from:
                guides[-1]["prior_ltcode"] = moved_from
            if yr_twin:
                guides[-1]["period_check"] = "the same figures were the FY%s guide last quarter" % fp[-2:]
        here = _period_key(computed.get("currentPeriod"))
        for key, r in prior_by.items():
            if (key not in cur_keys and key not in moved_used and _period_key(key[1]) > here
                    and not str(key[0] or "").endswith(("-Y", "-Q"))):
                not_repeated.append({"ltcode": key[0], "lineitem": _k(r, "LINEITEM", "li"), "period": key[1],
                                     "period_label": fy_label(key[1]),
                                     "guide": guide_display(_k(r, "GUIDE_TEXT", "gt"), r.get("SCALE"), r.get("CURRENCY"))})
    prior_guides = []
    if gsrc in ("release", "transcript"):
        for x in guides:
            prior_guides.append({k: x[k] for k in ("ltcode", "lineitem", "period", "period_label", "guide",
                                                   "guide_date")})
        guides = []
    guides, dropped = rank_guides(guides, {r.get("ltcode") for r in rows})
    names = {x["ltcode"]: x["lineitem"] for x in prior_guides}
    pinned = [{"ltcode": c.get("ltcode"), "name": names.get(c.get("ltcode")) or _PLAIN.get(c.get("ltcode")) or c.get("ltcode"),
               "period": str(c.get("fiscal_period")),
               "period_label": fy_label(c.get("fiscal_period")), "consensus": short_value(c.get("value")),
               "consensus_as_of": stale_asof(c.get("asof"), computed.get("earningsDate"))}
              for c in computed.get("guidanceConsensus") or []]
    one_off = _one_off_note(computed, inp, rows)
    fwd = []
    for r in ((inp.get("mcp_responses") or {}).get("get_estimates_forward") or {}).get("data") or []:
        if isinstance(r, dict):
            fwd.append({"ltcode": _k(r, "LTCODE", "lt"), "period": _k(r, "FISCALPERIOD", "fp"),
                        "value": short_value(_k(r, "VALUE", "vl"))})
    return {
        "company": re.sub(r"\s*\(.*?\)\s*", " ", company_name or computed.get("ticker") or "").strip(),
        "ticker": computed.get("ticker"), "period": computed.get("currentPeriod"),
        "prior_period": computed.get("priorPeriod"), "reported": computed.get("earningsDate"),
        "last_close": (computed.get("marketPrices") or {}).get("lastClosePrice"),
        "valuation": computed.get("valuationLine"),
        "guidance_source": gsrc,
        "rows": rows, "bench": computed.get("benchMetrics") or [], "guidance": guides, "forward": fwd,
        "guidance_dropped": dropped,
        "prior_guides": prior_guides, "pinned_consensus": pinned,
        "not_repeated": not_repeated if gsrc == "mcp" else [],
        "one_off": one_off,
        "warnings": [w for w in computed.get("warnings") or []
                     if w.startswith(("LARGE SURPRISE", "MODEL-SOURCED", "[guidance", "NO OPERATING", "THIN KPI",
                                      "INSYNC VS COMPANY", "ROUNDING"))],
    }


def render_sheet(s):
    out = ["FACT SHEET - %s (%s) %s, reported %s. Quote these exactly as printed." %
           (s["company"], s["ticker"], s["period"], s["reported"]),
           "", "%-44s %9s %9s %9s %10s %8s %9s  %s" % ("row", s["prior_period"] + "(A)", s["period"] + "(A)",
                                                    s["period"] + "(E)", "delta", "delta%", "YoY", "verdict")]
    for r in s["rows"]:
        out.append("%-44s %9s %9s %9s %10s %8s %9s  %s%s" % (
            str(r["name"])[:44], r["priorActual"], r["currentActual"], r["currentEstimate"], r["deltaAbsolute"],
            r["deltaPercent"], r["yoyChange"], r["beatMiss"], "  (lower is better)" if r.get("lowerIsBetter") else ""))
    calc = [r for r in s["rows"] if str(r.get("sourceDerived") or "").lower() == "calculated"
            and r.get("block") == "financialMetrics"]
    if calc:
        out += ["", "INSYNC-CALCULATED (worked out by InSync, not copied from the release): "
                + ", ".join("%s %s" % (r["name"], r["currentActual"]) for r in calc) + ".",
                "  If a release you read prints a different figure:  python3 flash.py set reported <LTCODE> <value>"]
    for g in s.get("guidance") or []:
        if g.get("unit_check"):
            out += ["", "UNIT CHECK - %s guide: %s. The feed's scale is probably wrong; check the release"
                    % (g.get("lineitem"), g["unit_check"]), "  and do not quote this guide until it is confirmed."]
        if g.get("period_check"):
            out += ["", "PERIOD CHECK - %s %s guide: %s, so the period label is probably wrong in the feed."
                    % (g["period_label"], g.get("lineitem"), g["period_check"]), "  No consensus is quoted beside it."]
    unk = [r for r in s["rows"] if r.get("beatMiss") == DASH]
    if unk:
        out += ["", "DIRECTION UNKNOWN - not graded. For each, decide whether a HIGHER or a LOWER number is",
                "  good news, run  python3 flash.py set direction <LTCODE> higher|lower  and re-run facts:"]
        out += ["  %s (%s)" % (r["name"], r.get("ltcode")) for r in unk]
    oo = s.get("one_off")
    if oo:
        incl = ", ".join(oo["profit_rows"]) if oo["profit_rows"] else ""
        out += ["", "ONE-OFF ITEMS: %s of %s includes %s that InSync marks as one-off (Total Revenue ex-1x"
                % (oo["revenue_row"], oo["reported"], oo["difference"]),
                "  items: %s; there is no consensus for that line)." % oo["ex_one_off"]
                + ("  %s include(s) them too." % incl if incl else ""),
                "  " + ("%s EXCLUDES them, so the scorecard rows are on different bases." % oo["eps_row"]
                        if oo["eps_excludes"] else "%s includes them as well." % (oo["eps_row"] or "The EPS row")),
                "  Say so in whatMatters (quality of earnings) and do not call the revenue beat clean."]
    if s["bench"]:
        out += ["", "BENCH (no consensus - quote the level and Y/Y only, never a beat or miss):"]
        for b in s["bench"]:
            out.append("  %s: %s (prior %s, Y/Y %s)" % (b.get("name"), b.get("currentActual"), b.get("priorActual"),
                                                       b.get("yoyChange")))
    dr = s.get("document_read")
    if dr:
        out += ["", "DOCUMENT READ: %s (text: %s)." % (dr.get("name"), dr.get("text_file") or "as returned"),
                "  Use it in whatMatters for the top quality-of-earnings signal and one clause of management",
                "  tone. A figure from it goes in only if it is on this sheet; otherwise say it in words."]
    elif s.get("document_unread"):
        out += ["", "NO DOCUMENT COULD BE READ. End flashVerdict.net with: Release text not retrievable;",
                "  narrative built from variance and guidance data only."]
    if s["guidance_source"] in ("release", "transcript"):
        out += ["", "GUIDANCE (source: %s). The structured feed had NOT updated for this print." % s["guidance_source"],
                "  Take each CURRENT guide from the document you parsed%s. Write them to a JSON file,"
                % ((" (%s)" % s["guidance_doc"]) if s.get("guidance_doc") else ""),
                '  [{"ltcode": "IS_P_101", "period": "2Q27", "guide": "$6.80B-$6.85B"}, ...], using the ltcode',
                "  and period of the PRIOR line it updates (leave ltcode out for a line not listed), and run:",
                "  python3 flash.py guides <file>. It tags each guide that matches a PRIOR guide exactly (same",
                "  line, same period, clean figures). Tag the rest yourself against the PRIOR list: higher",
                "  midpoint RAISED, lower LOWERED, same MAINTAINED, no prior NEW. A PRIOR guide with no new one",
                "  is NOT withdrawn unless the document says the company withdrew or suspended it: otherwise",
                "  leave it out. Quote consensus ONLY on a guide TAGGED BY CODE (printed beside it); every",
                "  other guide is its tag and figures alone, never with a consensus. Show at most 5 guides,",
                "  in the order flash.py guides prints them.",
                "  PRIOR guides (feed, last quarter):"]
        for g in s.get("prior_guides") or []:
            out.append("    %s %s: %s (dated %s)  ->  \"ltcode\": \"%s\", \"period\": \"%s\"" % (
                g["period_label"], g["lineitem"], g["guide"], g["guide_date"], g["ltcode"], g["period"]))
        if not s.get("prior_guides"):
            out.append("    (none - every guide in the document is NEW)")
        for r in s.get("release_guides") or []:
            if r.get("by") == "code":
                out.append("  TAGGED BY CODE - use it: [%s] %s %s: %s (was %s)%s%s" % (
                    r["tag"], r["period_label"], r["lineitem"], r["guide"], r["prior_guide"],
                    " | " + pre_cons(r["consensus"], r["consensus_as_of"]) if r.get("consensus") else "",
                    " | NOT ON THE PAGE (ranked out)" if r.get("dropped") else ""))
        out.append("  Pinned pre-guide consensus (quoted only through a guide tagged by code):")
        for c in s.get("pinned_consensus") or []:
            out.append("    %s %s: %s" % (c["period_label"], c.get("name") or c["ltcode"], pre_cons(c["consensus"], c["consensus_as_of"])))
        if not s.get("pinned_consensus"):
            out.append("    (none pinned)")
    else:
        out += ["", "GUIDANCE (source: %s). Tag and order are worked out for you - use both, these 5 at most:"
                % s["guidance_source"]]
    def _was(g):
        if not g["prior_guide"] or g["tag"] == "MAINTAINED":
            return ""
        if g["tag"] == "NEW":
            if _nums(g["guide"]) and not _nums(g["prior_guide"]):
                return ' (first figure; last quarter it was guided in words: "%s")' % g["prior_guide"]
            if not _nums(g["guide"]) and _nums(g["prior_guide"]):
                return " (now guided in words only; last quarter: %s)" % g["prior_guide"]
            return ' (the wording changed; last quarter: "%s")' % g["prior_guide"]
        return " (was %s)" % g["prior_guide"]

    for g in s["guidance"]:
        out.append("  [%s] %s %s: %s%s | %s%s" % (
            g["tag"], g["period_label"], g["lineitem"], g["guide"], _was(g),
            pre_cons(g["consensus"], g["consensus_as_of"]) if g["consensus"] else "consensus none - write the guide alone",
            (" | the feed moved this line from %s; compared with it" % g["prior_ltcode"]) if g.get("prior_ltcode") else ""))
    if not s["guidance"] and s["guidance_source"] not in ("release", "transcript"):
        out.append("  (no structured guidance rows)")
    if s.get("guidance_dropped"):
        out.append("  NOT ON THE PAGE (ranked out): " + "; ".join(
            "%s %s" % (g["period_label"], g["lineitem"]) for g in s["guidance_dropped"]))
    if s.get("not_repeated"):
        out.append("  NOT REPEATED this quarter - a hint, not a finding (the feed may not carry it):")
        for g in s["not_repeated"]:
            out.append("    %s %s: %s" % (g["period_label"], g["lineitem"], g["guide"]))
        out.append("  Write [WITHDRAWN] only if the release or call says the company withdrew or suspended it;"
                   " otherwise leave it out.")
    if s["forward"]:
        out += ["", "FORWARD CONSENSUS (for Forward View, mark each line [E]):"]
        out += ["  %s %s: %s" % (f["period"], f["ltcode"], f["value"]) for f in s["forward"]]
    out += ["", "Last close %s | Valuation: %s" % (s["last_close"], s["valuation"] or "none")]
    for w in s["warnings"]:
        out.append("WARNING: " + w[:220])
    return "\n".join(out)


_SCALE = {"K": 1e3, "M": 1e6, "B": 1e9, "BN": 1e9, "MN": 1e6, "T": 1e12}
_SIDE = re.compile(r"^([$€£¥])?\s*(-?[\d,]*\.?\d+)\s*(K|M|B|T|BN|MN|%)?$", re.I)


def parse_guide(text):
    t = str(text or "").strip()
    t = re.sub(r"[–—−]", "-", t)
    parts = [p.strip() for p in re.split(r"\s+to\s+|(?<=[\d%KMBTkmbt])\s*-\s*(?=[-$€£¥\d])", t) if p.strip()]
    if not 1 <= len(parts) <= 2:
        return None
    sides = []
    for p in parts:
        m = _SIDE.match(p.replace(" ", ""))
        if not m:
            return None
        cur, num, unit = m.group(1), float(m.group(2).replace(",", "")), (m.group(3) or "").upper()
        sides.append([cur, num, unit])
    if len(sides) == 2 and not sides[0][2] and sides[1][2]:
        sides[0][2] = sides[1][2]
    if len(sides) == 2 and sides[0][0] and not sides[1][0]:
        sides[1][0] = sides[0][0]
    units = {s[2] for s in sides}
    curs = {s[0] for s in sides}
    if len(units) != 1 or len(curs) != 1:
        return None
    cur = curs.pop() or ""
    unit = units.pop()
    if unit == "%":
        kind, mult = "pct", 1.0
    elif unit:
        kind, mult = "money", _SCALE[unit]
    else:
        kind, mult = "plain", 1.0
    vals = [s[1] * mult for s in sides]
    return (min(vals), max(vals), kind + cur)


def tag_release_guides(entries, prior_guides, pinned=None):
    prior = {(p["ltcode"], str(p["period"])): p for p in prior_guides or []}
    pin = {(c["ltcode"], str(c["period"])): c for c in pinned or []}
    by_label = {(p["ltcode"], p["period_label"]): p for p in prior_guides or []}
    out = []
    for e in entries or []:
        lt, per, guide = e.get("ltcode"), str(e.get("period") or ""), str(e.get("guide") or "")
        p = prior.get((lt, per)) or by_label.get((lt, fy_label(per))) or by_label.get((lt, per))
        rec = {"ltcode": lt, "period": p["period"] if p else per, "period_label": p["period_label"] if p else fy_label(per),
               "lineitem": (p or {}).get("lineitem") or e.get("lineitem") or lt, "guide": guide,
               "prior_guide": p["guide"] if p else None, "tag": None, "by": "you"}
        a, b = parse_guide(guide), parse_guide(p["guide"]) if p else None
        if not p:
            rec["why"] = "no PRIOR guide for this line and period: tag it yourself (NEW if it was never guided)"
        elif not a or not b:
            rec["why"] = "a figure did not parse cleanly: tag it yourself against the PRIOR guide"
        elif a[2] != b[2]:
            rec["why"] = "the two guides are in different units: tag it yourself"
        else:
            ma, mb = (a[0] + a[1]) / 2, (b[0] + b[1]) / 2
            if abs(ma - mb) <= 1e-9 * max(1.0, abs(mb)):
                rec["tag"] = "MAINTAINED"
                if (a[0], a[1]) != (b[0], b[1]):
                    rec["note"] = "same midpoint, range %s: say so in words" % (
                        "narrowed" if (a[1] - a[0]) < (b[1] - b[0]) else "widened")
            else:
                rec["tag"] = "RAISED" if ma > mb else "LOWERED"
            rec["by"] = "code"
        c = pin.get((lt, rec["period"])) if rec["by"] == "code" else None
        rec["consensus"], rec["consensus_as_of"] = (c["consensus"], c["consensus_as_of"]) if c else (None, None)
        out.append(rec)
    return out


_GENERIC = {"total", "net", "the", "and", "of", "for", "from", "in", "adjusted", "reported", "per", "other"}
_PERIOD_TOK = re.compile(r"\b(?:[1-4]Q\d{2}|[12]H\d{2}|FY\d{2,4}|(?:19|20)\d{2})\b", re.I)
_NUM_TOK = re.compile(r"(?<![\w.])[-+−]?[$€£¥]?(\d[\d,]*(?:\.\d+)?)")
_GRADE = re.compile(r"\b(beat|beats|missed|miss|misses|in-line|in line|consensus|estimates?|expectations?)\b", re.I)


def _nums_in(text):
    return {round(float(m.group(1).replace(",", "")), 4) for m in _NUM_TOK.finditer(_PERIOD_TOK.sub(" ", str(text)))}


def bench_prose_check(narrative, sheet):
    bench = (sheet or {}).get("bench") or []
    if not bench:
        return []
    import json as _json
    allowed = _nums_in(_json.dumps(sheet))
    texts = []

    def walk(x):
        if isinstance(x, str):
            texts.append(x)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk({k: v for k, v in (narrative or {}).items() if k != "companyName"})
    clauses = [c for t in texts for c in re.split(r";|\.\s", t) if c.strip()]
    warns = []
    for b in bench:
        words = [w for w in re.findall(r"[a-z][a-z&]{2,}", str(b.get("name") or "").lower()) if w not in _GENERIC]
        if not words:
            continue
        for c in clauses:
            low = c.lower()
            if not all(re.search(r"\b" + re.escape(w) + r"s?\b", low) for w in words):
                continue
            if _GRADE.search(c):
                warns.append("[bench graded] %s has no consensus, but the prose grades it: %r" % (b.get("name"), c.strip()[:160]))
            stray = sorted(n for n in _nums_in(c) if n not in allowed)
            if stray:
                warns.append("[bench figure] %s: %s is not on the fact sheet (bench: %s, prior %s, Y/Y %s): %r" % (
                    b.get("name"), ", ".join("%g" % n for n in stray), b.get("currentActual"), b.get("priorActual"),
                    b.get("yoyChange"), c.strip()[:160]))
    return warns


def _pct(v):
    try:
        return abs(float(str(v).replace("%", "").replace("+", "").replace("−", "-")))
    except ValueError:
        return 0.0


def _unsign(v):
    return re.sub(r"^[+\-−]", "", str(v or ""))


def _side(delta):
    return "below" if str(delta).strip().startswith(("-", "−")) else "above"


def _yoy_phrase(y):
    y = str(y or "").strip()
    if not y or y in ("NM", "N/A", "—", "-"):
        return ""
    if y.endswith("bps"):
        return "%s %s Y/Y" % (_unsign(y), "lower" if y.startswith(("-", "−")) else "higher")
    return "%s %s Y/Y" % ("declined" if y.startswith(("-", "−")) else "grew", _unsign(y))


def _short(name, words=5):
    w = re.sub(r"\s*\([^)]*\)?", "", str(name)).split()
    return " ".join(w[:words])


def _bullet(r, lead=""):
    name, a, e, dlt, dp = r["name"], r["currentActual"], r["currentEstimate"], r["deltaAbsolute"], r["deltaPercent"]
    if r["beatMiss"] in ("Beat", "Miss"):
        core = "%s of %s came in %s %s consensus" % (name, a, _unsign(dlt), _side(dlt))
        if dp and dp not in ("—", "NM", "N/A"):
            core += " (%s)" % dp
    else:
        core = "%s of %s was in line with the %s consensus" % (name, a, e)
    yp = _yoy_phrase(r["yoyChange"])
    return (lead + core + (", and " + yp if yp else "") + ".").strip()


def _verdict_side(rows, kind):
    parts, words = [], 0
    for r in rows:
        p = "%s %s, %s" % (_short(r["name"], 3), r["currentActual"], r["deltaAbsolute"])
        n = len(p.split())
        if words + n > 12:
            break
        parts.append(p)
        words += n + 0
    return "; ".join(parts)


def build_narrative(s):
    t, per, co = s["ticker"], s["period"], s["company"] or s["ticker"]
    first = re.sub(r"\s+(Inc|Corp|Corporation|Co|Ltd|plc|PLC|SA|AG|NV|SE|Limited|Holdings)\.?$", "", co)
    rows = s["rows"]
    graded = [r for r in rows if r["beatMiss"] in ("Beat", "Miss") and r.get("ltcode") != "COMPUTED"]
    graded.sort(key=lambda r: -_pct(r["deltaPercent"]))
    beats = [r for r in graded if r["beatMiss"] == "Beat"]
    misses = [r for r in graded if r["beatMiss"] == "Miss"]
    inline = [r for r in rows if r["beatMiss"] not in ("Beat", "Miss", DASH) and r.get("ltcode") != "COMPUTED"]
    inline.sort(key=lambda r: -_pct(r["yoyChange"]))
    by_lt = {r.get("ltcode"): r for r in rows}
    rev = by_lt.get("IS_P_101") or (rows[0] if rows else None)
    eps = next((r for r in rows if "EPS" in str(r["name"]) or "FFO" in str(r["name"])
                or r.get("ltcode") in ("IS_P_124", "IS_P_123")), None)

    wm_rows = (graded + inline)[:5]
    if len(wm_rows) < 3:
        wm_rows = (graded + inline + [r for r in rows if r not in graded + inline])[:3]
    what = [_bullet(r, "%s (%s) " % (first, t) if i == 0 else "") for i, r in enumerate(wm_rows)]
    left_out = [r for r in graded if r not in wm_rows]

    gl = []
    gsrc = s["guidance_source"]
    picked = s["guidance"] if gsrc not in ("release", "transcript") else [
        g for g in s.get("release_guides") or [] if g.get("tag") and not g.get("dropped")]
    for g in picked:
        name = ((by_lt.get(g["ltcode"]) or {}).get("name") if g["ltcode"] in _EPS else None) or \
            _PLAIN.get(g["ltcode"]) or (g["lineitem"] + (" growth" if str(g["ltcode"]).endswith("-Y") else ""))
        cons = ("vs. " + pre_cons(g["consensus"], g.get("consensus_as_of"))) if g.get("consensus") else ""
        if g["tag"] in ("RAISED", "LOWERED") and g["prior_guide"]:
            body = "%s %s to %s, from %s" % (g["period_label"], name, g["guide"], g["prior_guide"])
            if _lower_better(g):
                body += " (%s, as lower is better for this line)" % (
                    "favourable" if g["tag"] == "LOWERED" else "unfavourable")
        elif g["tag"] == "MAINTAINED":
            body = "%s %s held at %s" % (g["period_label"], name, g["guide"])
        else:
            body = "%s %s of %s" % (g["period_label"], name, g["guide"])
        if not cons:
            gl.append("[%s] %s." % (g["tag"], body))
        elif g["tag"] in ("RAISED", "LOWERED"):
            gl.append("[%s] %s, %s." % (g["tag"], body, cons))
        else:
            gl.append("[%s] %s %s." % (g["tag"], body, cons))
    fwd = []
    if gsrc == "none":
        for f in s["forward"][:4]:
            fwd.append("[E] %s consensus %s of %s." % (fy_label(f["period"]), _PLAIN.get(f["ltcode"], f["ltcode"]),
                                                       f["value"]))
    if gsrc in ("none", "unavailable"):
        gl = []
    elif gsrc in ("release", "transcript"):
        gl.append("Source: earnings %s." % ("release" if gsrc == "release" else "call transcript"))

    pos = _verdict_side(beats, "beat")
    neg = _verdict_side(misses, "miss")
    moves = [g for g in picked if g["tag"] in ("RAISED", "LOWERED")]
    net = "%d beat%s, %d miss%s, %d in line vs consensus" % (
        len(beats), "" if len(beats) == 1 else "s", len(misses), "" if len(misses) == 1 else "es", len(inline))
    if moves:
        net += "; %s %s guide %s" % (moves[0]["period_label"], _PLAIN.get(moves[0]["ltcode"], moves[0]["lineitem"]),
                                     moves[0]["tag"].lower())
    elif picked:
        net += "; guidance %s" % ("held" if all(g["tag"] == "MAINTAINED" for g in picked) else "issued")

    nq = next((g for g in picked if g["ltcode"] == "IS_P_101" and not str(g["period"]).isdigit()), None)
    if nq and nq.get("consensus"):
        ir = "Can %s revenue land in the %s guide against the %s consensus?" % (nq["period_label"], nq["guide"],
                                                                              nq["consensus"])
    else:
        lead = (misses or beats or inline or rows)[0]
        ir = "Does %s hold %s after this print?" % (_short(lead["name"], 4), lead["currentActual"])
    if len(ir) > 110:
        ir = "Does %s hold at %s?" % (_short((misses or beats or rows)[0]["name"], 3), (misses or beats or rows)[0]["currentActual"])

    bits = ["%s %s" % (r["currentEstimate"], "revenue" if r is rev else r["name"]) for r in (rev, eps)
            if r and r.get("currentEstimate") and not _NO_CONS.search(str(r["currentEstimate"]))]
    street = ("Consensus stood at %s for %s." % (" and ".join(bits), per) if bits
              else "The scorecard carries no consensus for revenue or EPS for %s." % per)
    gap_bits = []
    for r in [x for x in (rev, eps) if x]:
        if r["beatMiss"] in ("Beat", "Miss"):
            gap_bits.append("%s came in %s %s consensus" % (_short(r["name"], 3), _unsign(r["deltaAbsolute"]), _side(r["deltaAbsolute"])))
        else:
            gap_bits.append("%s was in line at %s" % (_short(r["name"], 3), r["currentActual"]))
    gap = "; ".join(gap_bits) + "." if gap_bits else "The scorecard is in line with consensus."
    ev_rows = left_out or [r for r in rows if r.get("block") == "operatingMetrics"][:1] or rows[:1]
    evidence = " ".join(_bullet(r) for r in ev_rows[:2])
    if nq and nq.get("consensus"):
        why = "The %s revenue guide of %s compares with the %s." % (
            nq["period_label"], nq["guide"], pre_cons(nq["consensus"], nq.get("consensus_as_of")))
    elif nq:
        why = "The %s revenue guide of %s sets the bar for the next print." % (nq["period_label"], nq["guide"])
    else:
        k = (inline or graded or rows)[0]
        why = "%s at %s sets the base for the next print." % (_short(k["name"], 4), k["currentActual"])

    watch = []
    if nq:
        watch.append("Whether %s revenue lands inside the %s guide%s." % (
            nq["period_label"], nq["guide"], (" against the %s consensus" % nq["consensus"]) if nq.get("consensus") else ""))
    for r in (misses[:1] + [x for x in rows if x.get("block") == "operatingMetrics" and x not in misses][:2]):
        yp = _yoy_phrase(r["yoyChange"])
        watch.append("Whether %s moves from %s%s at the next print." % (r["name"], r["currentActual"],
                                                                      (" after it " + yp) if yp else ""))
    if not watch:
        watch.append("Whether %s moves from %s at the next print." % (rows[0]["name"], rows[0]["currentActual"]))
    return {
        "companyName": co,
        "flashVerdict": {"positives": pos, "negatives": neg, "net": net},
        "investmentRead": ir,
        "whatMatters": what,
        "variantView": {"street": street, "expectationGap": gap, "evidence": evidence,
                        "whyItMatters": why},
        "guidance": gl,
        **({"forwardView": fwd} if fwd else {}),
        "nextWatchItems": watch[:3],
    }
