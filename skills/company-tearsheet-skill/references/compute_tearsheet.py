#!/usr/bin/env python3
"""
compute_tearsheet.py — deterministic math layer for company-tearsheet-skill.
SKILL VERSION: 8.1.0 (must match SKILL.md frontmatter and README.md).

The LLM's ONLY numeric job is to paste raw MCP rows into raw_data.json.
This script computes and formats EVERY derived number: YoY growth, gross
margin and bps deltas, vs-estimate deltas, market cap, PEG, 5Y valuation
averages, and 52W positioning. The LLM must not perform or override any
arithmetic — it merges this script's output with prose sections only.

Usage:
    python compute_tearsheet.py raw_data.json computed.json

raw_data.json schema (all MCP rows pasted verbatim as returned):
{
  "meta": {
    "ticker": "ACME", "company_name": "Acme Corp",
    "latest_fiscal_quarter": "1Q26", "prior_year_quarter": "1Q25",
    "prepared_date": "July 15, 2026",
    "next_earnings": {"date": "Aug 5, 2026", "period": "2Q FY2026"},
    "next_quarter": "2Q26", "fy1": "2026", "fy2": "2027"
  },
  "market": { "price": 100.00, "price_date": "2026-07-14",
              "week52_low": 80.00, "week52_high": 120.00,
              "fds_millions": 500.0, "dividend_annual": null },
  "actuals": [ ...get_actuals rows (both quarters)... ],
  "estimates_quarter": [ ...get_estimates rows, latest quarter... ],
  "estimates_next_quarter": [ ...get_estimates rows, next quarter (EPS)... ],
  "estimates_annual": [ ...get_estimates rows, FY1+FY2... ],
  "valuation": [ ...get_valuation rows (metric, value, DATE)... ],
  ---- or, for the same numbers with two thirds less typing (v7.2.28) ----
  "valuation_series": { "P/E_Adj_Ratio": {"2026-09-04": 25.1234, "2026-08-31": 24.9876},
                        "EV/Sales":      {"2026-09-04": 4.5678} }
  Both shapes are accepted and merge. ONLY metric, value and DATE are ever read from a
  valuation row, so TICKER, TICKER_ID and PERIOD do not need pasting in either shape.
  "kpis": [ {"ltcode": "SD_S_161", "name": "Product A Revenue"},
             {"ltcode": "KM_P_1205", "name": "Total Production (MBoe/d)",
              "unit_divisor": 1000} ]
  For non-currency volume/count KPIs (production, subscribers, units) add
  "unit_divisor": the divisor converting the MCP absolute value into the unit
  named in "name" (e.g. raw 2.35e6 boe/d with divisor 1000 -> 2,350 MBoe/d).
  Values are then formatted as plain numbers, never with $.
  For percent KPIs where a LOWER value beats the estimate (MLR, efficiency ratio,
  combined ratio, churn), add "lower_is_better": true — Beat/Miss inverts accordingly.

  "peers": OPTIONAL — raw peer rows; the script formats the entire comp table:
  [ {"name": "Example Energy (EXE)", "is_target": true},         <- auto-fills from valuation/actuals
    {"name": "Peer One (PONE)", "pe": 14.0, "ev_ebitda": 7.0, "ev_sales": 1.5,
     "rev_growth": "2.00%"},
    {"name": "Peer Two (PTWO)", "pe_approx": "~10x", "ev_ebitda_approx": "~6x", "rev_growth": "1.00%"} ]
  pe/ev_ebitda are raw MCP floats (null -> em dash); *_approx are model-knowledge
  strings that MUST start with "~" and are passed through verbatim.
}
"""
import json
import re
import sys
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from statistics import mean

FIN_CODES = ["IS_P_101", "IS_P_106", "IS_P_110", "IS_P_114", "IS_P_128", "IS_P_127"]

FIN_FALLBACK = {
    "IS_P_110": ["IS_P_109"],
    "IS_P_109": ["IS_P_110"],
    "IS_S_643": ["IS_P_128", "IS_P_127"],
    "IS_P_128": ["IS_P_127"],
    "IS_P_114": ["IS_P_113", "IS_P_123"],
    "IS_P_113": ["IS_P_114", "IS_P_123"],
    "IS_P_106": ["IS_P_105"],
    "IS_P_105": ["IS_P_106"],
    "IS_P_124": ["IS_P_123"],
    "IS_P_123": ["IS_P_124"],
    "IS_P_172": ["IS_P_114", "IS_P_113"],
}
FIN_FALLBACK_NAMES = {
    "IS_P_110": "EBIT (Adj)", "IS_P_113": "EBITDA", "IS_S_643": "EPS (Adj, ex-SBC)",
    "IS_P_123": "Net Income",
    "IS_P_106": "Gross Profit (Adj)", "IS_P_124": "Net Income (Adj)",
    "IS_P_114": "EBITDA (Adj)",
}
ADJ_TWIN = {"IS_P_105": "IS_P_106", "IS_P_109": "IS_P_110",
            "IS_P_113": "IS_P_114", "IS_P_123": "IS_P_124", "IS_P_127": "IS_P_128"}
MIN_FIN_ROWS = 5
DIV_YIELD_MAX_LAG_DAYS = 100
FIN_NAMES = {
    "IS_P_101": "Revenue", "IS_P_105": "Gross Profit", "IS_P_109": "EBIT",
    "IS_P_114": "EBITDA (Adj)", "IS_P_128": "EPS (Adj)", "IS_P_127": "EPS (GAAP)",
    "IS_P_123": "Net Income",
    "IS_P_106": "Gross Profit (Adj)", "IS_P_110": "EBIT (Adj)",
    "IS_P_124": "Net Income (Adj)",
}


_CUR_RE = re.compile(
    r'^\s*(R\$|A\$|C\$|HK\$|NT\$|[$\u20ac\u00a3\u00a5\u20b9\u20a9\u20bd\u20ba]|'
    r'GBPence|GBp|ZARc|USDc|EURc|'
    r'EUR|GBP|CHF|DKK|SEK|NOK|CAD|JPY|BRL|MXN|CLP|COP|ARS|PEN|ZAR|INR|IDR|'
    r'KRW|TWD|HKD|CNY|RMB|SGD|AUD|NZD|PLN|TRY|ILS|AED|SAR|THB|PHP|MYR)\s*', re.I)


def currency_of(v):
    if v is None:
        return ""
    m = _CUR_RE.match(str(v).strip().lstrip("-"))
    return m.group(1) if m else ""


def parse_value(v):
    if v is None:
        return None, None
    s = str(v).strip()
    s = _CUR_RE.sub("", s, count=1)
    if s.startswith("-"):
        s = "-" + _CUR_RE.sub("", s[1:], count=1)
    m = re.match(r"^\s*(-?[\d,]+\.?\d*)\s*([TBMKk%x]?)$", s)
    if not m:
        return None, None
    num = float(m.group(1).replace(",", ""))
    unit = m.group(2)
    if unit == "x":
        return num, "x"
    if unit == "T":
        return num * 1000000.0, "M"
    if unit == "B":
        return num * 1000.0, "M"
    if unit == "M":
        return num, "M"
    if unit in ("K", "k"):
        return num / 1000.0, "M"
    if unit == "%":
        return num, "%"
    return num, "raw"


def cur_sym(v):
    tok = currency_of(v)
    if not tok:
        return "$"
    if tok in ("GBPence", "GBp", "ZARc", "USDc", "EURc"):
        return tok + " "
    up = tok.upper()
    if up == "BRL":
        return "R$"
    for code, sym in (("EUR", "€"), ("GBP", "£"), ("JPY", "¥"), ("CNY", "¥"), ("INR", "₹")):
        if up == code:
            return sym
    if len(tok) == 3 and tok.isalpha():
        return tok.upper() + " "
    return tok



def _xr(v, d):
    return str(Decimal(repr(float(v))).quantize(Decimal(1).scaleb(-d), rounding=ROUND_HALF_UP))


def _sep(t):
    i, _, frac = t.partition(".")
    neg = i.startswith("-")
    i = f"{int(i.lstrip('-')):,}"
    return ("-" if neg else "") + i + ("." + frac if frac else "")


def _xrs(v, d, sep=False):
    t = _sep(_xr(v, d)) if sep else _xr(v, d)
    return t if t.startswith("-") else "+" + t


def _xrf(v, d):
    return float(_xr(v, d))


def fmt_musd(mn, cur="$"):
    if abs(mn) >= 1e6:
        return f"{cur}{_sep(_xr(mn / 1e6, 2))}T"
    if abs(mn) >= 1e5:
        return f"{cur}{_sep(_xr(mn / 1e3, 0))}B"
    return f"{cur}{_sep(_xr(mn, 0))}M"


def parse_abs(v):
    if v is None:
        return None
    _s = _CUR_RE.sub("", str(v).strip(), count=1)
    m = re.match(r"^\s*(-?[\d,]+\.?\d*)\s*([TBMKk%]?)$", _s)
    if not m or m.group(2) == "%":
        return None
    return float(m.group(1).replace(",", "")) * {"T": 1e12, "B": 1e9, "M": 1e6, "K": 1e3, "k": 1e3, "": 1.0}[m.group(2)]


KPI_DP = 2

def _skill_version():
    import os, re as _re
    try:
        with open(os.path.abspath(__file__), encoding="utf-8") as fh:
            head = fh.read(600)
        m = _re.search(r"SKILL VERSION:\s*([0-9][0-9.]*)", head)
        return m.group(1) if m else "unknown"
    except OSError:
        return "unknown"


SKILL_VERSION = _skill_version()



def fmt_count(x, currency=False):
    a, sign = abs(x), "-" if x < 0 else ""
    d = currency if isinstance(currency, str) else ("$" if currency else "")
    for lim, u in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if a >= lim:
            v = a / lim
            dp = 2 if v < 10 else 1 if v < 100 else 0
            return f"{sign}{d}{_sep(_xr(v, dp))}{u}"
    return f"{sign}{d}{_sep(_xr(a, 0))}"


def kpi_scale(x):
    a = abs(x or 0)
    for lim, u in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if a >= lim:
            return lim, u
    return 1.0, ""


def fmt_kpi(x, scale, currency=False, signed=False, dp=KPI_DP):
    div, u = scale
    sym = currency if isinstance(currency, str) else ("$" if currency else "")
    v = (x or 0) / div
    sign = "-" if v < 0 else ("+" if signed else "")
    return f"{sign}{sym}{_sep(_xr(abs(v), dp))}{u}"


def fmt_growth_str(g, signed=True):
    if g is None:
        return "—"
    try:
        return fmt_pct(float(str(g).rstrip("%")), signed=signed)
    except ValueError:
        return "—"


def fmt_x(v, dp=1):
    if v is None or not isinstance(v, (int, float)):
        return None
    return f"{_xr(v, dp)}x"


def fmt_pct(p, signed=True, dp=1):
    if p is None:
        return None
    return f"{'+' if (signed and p >= 0) else ''}{_xr(p, dp)}%"


NOT_MEANINGFUL = "n/m"
UNAVAILABLE = "unavail"
UNAVAILABLE_KEYS = ("pe", "ev_ebitda", "ev_sales", "pb", "rev_growth")
_ISO_SYM = {"USD": "$", "AUD": "A$", "CAD": "C$", "HKD": "HK$", "SGD": "S$", "NZD": "NZ$",
            "EUR": "€", "GBP": "£", "JPY": "¥", "INR": "₹", "KRW": "₩"}
KF_ONLY_CODES = ("RT_P_406",)


def yoy(cur, pri):
    if cur is None or pri is None or pri <= 0:
        return None
    return (cur - pri) / pri * 100.0


def growth_reason(cur, pri):
    if cur is None or pri is None or pri > 0:
        return None
    if pri == 0:
        return "the prior period is zero, so there is no base to grow from"
    if cur is None:
        return None
    if cur > 0:
        return ("the prior period was a LOSS and the current one a profit, so there is no "
                "percentage change between them")
    if cur > pri:
        return ("both periods are losses and the loss NARROWED (%s to %s), which is an "
                "improvement, not growth" % (fmt_signed(pri), fmt_signed(cur)))
    if cur < pri:
        return ("both periods are losses and the loss WIDENED (%s to %s)"
                % (fmt_signed(pri), fmt_signed(cur)))
    return "both periods are the same loss"


def fmt_signed(v):
    return _xrs(v, 2, sep=True) if abs(v) < 100 else _xrs(v, 1, sep=True)


_RESTATE_RE = re.compile(r"^R\d*-")


def norm_period(p):
    s = str(p or "").strip().upper()
    return _RESTATE_RE.sub("", s)


def rows_by_code(rows, period=None):
    out = {}
    want = norm_period(period) if period else None
    for r in rows or []:
        if want and norm_period(r.get("FISCALPERIOD")) != want:
            continue
        out[r["LTCODE"]] = r
    return out


MAG_DIV = {"Tn": 1e12, "Bn": 1e9, "B": 1e9, "Mn": 1e6, "M": 1e6, "6": 1e6,
           "K": 1e3, "k": 1e3, "1": 1.0, "Units": 1.0, "x": 1.0}


MASTER_STATUS = {"missing": False, "ticker_absent": False}


def load_master(ticker):
    import os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "company_master.json")
    try:
        master = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        MASTER_STATUS["missing"] = True
        return {}
    if ticker and ticker not in master:
        MASTER_STATUS["ticker_absent"] = True
    return master.get(ticker, {}) if ticker else {}


PROFILE_STATUS = {"industry": None, "matched": False, "known": []}


def lower_is_better_set(profile):
    p = profile or {}
    return set(p.get("lower_is_better_rows") or []) | set(p.get("lower_is_better_codes") or [])


def load_profile(industry, subindustry=None):
    import os
    PROFILE_STATUS["industry"] = industry
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "sector_profiles.json")
    try:
        profiles = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    PROFILE_STATUS["known"] = [k for k in profiles if not k.startswith("_")]
    prof = profiles.get(industry, {}) if industry else {}
    _alt = (profiles.get("_by_subindustry") or {}).get(subindustry) if subindustry else None
    if _alt and _alt in profiles:
        prof = profiles[_alt]
    PROFILE_STATUS["matched"] = bool(prof)
    return prof


COMPACT_BLOCKS = ("actuals", "estimates_quarter", "estimates_next_quarter", "estimates_annual")


def _expand_compact(raw):
    for k in COMPACT_BLOCKS:
        b = raw.get(k)
        if not isinstance(b, dict):
            continue
        cols, rows = b.get("cols"), b.get("rows")
        if not isinstance(cols, list) or not cols or not isinstance(rows, list):
            sys.exit("ERROR: raw_data.json %s: the compact form needs a non-empty \"cols\" list "
                     "and a \"rows\" list (SKILL.md 12.1)." % k)
        bad = [i for i, r in enumerate(rows) if not isinstance(r, list) or len(r) != len(cols)]
        if bad:
            sys.exit("ERROR: raw_data.json %s: row(s) %s do not have %d values, one per column in "
                     "\"cols\". Re-paste those rows from the MCP response." % (k, bad[:5], len(cols)))
        if "row_count" in b and b["row_count"] != len(rows):
            sys.exit("ERROR: raw_data.json %s: row_count says %s but %d row(s) were pasted. Paste "
                     "every row the MCP returned." % (k, b["row_count"], len(rows)))
        raw[k] = [{c: v for c, v in zip(cols, r) if v is not None} for r in rows]
    return raw


def main(raw_path, out_path):
    raw = _expand_compact(json.load(open(raw_path, encoding="utf-8")))
    meta, mkt = raw["meta"], raw["market"]
    _num_fields = ("price", "week52_low", "week52_high", "fds_millions", "dividend_annual",
                   "shares_outstanding", "shares_diluted", "dividend_yield")
    _bad_types = [(k, mkt[k]) for k in _num_fields
                  if k in mkt and mkt[k] is not None
                  and not isinstance(mkt[k], (int, float))]
    if _bad_types:
        sys.exit("ERROR: raw_data.json market.%s must be a NUMBER, not %s (%r). Paste the "
                 "MCP value without quotes; a quoted number is a bad paste, and every figure "
                 "derived from it would be wrong."
                 % (_bad_types[0][0], type(_bad_types[0][1]).__name__, _bad_types[0][1]))
    _missing_mkt = [k for k in ("price", "week52_low", "week52_high")
                    if mkt.get(k) is None]
    if _missing_mkt:
        sys.exit("ERROR: raw_data.json market is missing %s. The 52W range and the price are "
                 "required — SKILL.md 6.7 fetches them in one get_market_data call "
                 "(start_date '1y', summarize true) and the masthead prints the range. Add "
                 "the missing key(s) and re-run Step 1."
                 % ", ".join("market." + k for k in _missing_mkt))
    profile = load_profile(meta.get("industry"), meta.get("subindustry"))
    master = load_master(meta.get("ticker"))
    if master.get("fins"):
        fins6 = master["fins"][:6]
        fin_codes = [f["ltcode"] for f in fins6]
        fin_names = {f["ltcode"]: f["name"] for f in fins6}
    elif profile.get("financial_rows"):
        fin_codes = [c for c, _ in profile["financial_rows"]]
        fin_names = dict(profile["financial_rows"])
    else:
        fin_codes, fin_names = FIN_CODES, FIN_NAMES
    lq, pq = meta["latest_fiscal_quarter"], meta["prior_year_quarter"]
    cur = rows_by_code(raw.get("actuals"), lq)
    pri = rows_by_code(raw.get("actuals"), pq)
    est_q = rows_by_code(raw.get("estimates_quarter"), lq)
    warnings = []

    def growth_cell(cur_v, pri_v, who):
        _why = growth_reason(cur_v, pri_v)
        if _why is not None:
            warnings.append(
                "%s: Y/Y prints %s because %s. Through 7.2.20 this divided by the ABSOLUTE "
                "prior value and printed a percentage, so a narrowing loss read as growth "
                "(and the renderer painted it green). Quote the direction in prose, never a "
                "rate." % (who, NOT_MEANINGFUL, _why))
            return NOT_MEANINGFUL
        return fmt_pct(yoy(cur_v, pri_v)) or "—"


    _detected = ""
    for _r in (raw.get("actuals") or []):
        _t = currency_of(_r.get("VALUE"))
        if _t:
            _detected = _t
            break
    _declared = meta.get("currency_symbol")
    if _declared and _detected and _declared.upper() != _detected.upper():
        warnings.append(
            f"meta.currency_symbol is '{_declared}' but the actuals carry '{_detected}'; "
            f"the declared symbol was used. Confirm which is the reporting currency.")
    if not _declared and _detected:
        meta["currency_symbol"] = _detected
        if _detected != "$":
            warnings.append(
                f"reporting currency detected as '{_detected}' from the MCP value strings; "
                f"every money figure on this sheet is in {_detected}, NOT dollars. Prices and "
                f"multiples come from the LISTING currency and may differ, so check the "
                f"valuation block before quoting a multiple.")

    _freq = 'H' if re.match(r"^\d[Hh]\d{2}$", str(lq)) else ('A' if re.match(r"^\d{4}$", str(lq)) else 'Q')
    if _freq != 'Q':
        warnings.append(
            f"NON-QUARTERLY filer ({lq}): this is a "
            + ("HALF-YEARLY" if _freq == 'H' else "ANNUAL-ONLY")
            + " reporter. Row labels and forward slots follow the period tokens you supplied, "
            + "which is correct. Verify prior_year_quarter is the SAME period one year back ("
            + ("1H26 -> 1H25, not 2H25" if _freq == 'H' else "2025 -> 2024")
            + "), and note in the delivery message that KPI and peer coverage is usually thinner "
            + "for non-quarterly reporters.")
    if str(lq) == str(pq):
        warnings.append(f"latest_fiscal_quarter and prior_year_quarter are the SAME period ({lq}) "
                        "— every Y/Y cell will be 0% or blank. Fix meta before delivering.")

    _ind = meta.get("industry")
    if not _ind:
        warnings.append("meta.industry is MISSING — no sector profile applied; the default "
                        "income-statement template is in use. Capture the `industry` field "
                        "from search_tickerid (NOT `insync_subindustry`) and re-run.")
    elif not PROFILE_STATUS["matched"] and _ind in ("Banking", "Insurance", "Health Care",
                                                    "Financial Services", "Real Estate"):
        warnings.append(f"meta.industry '{_ind}' did not match any sector profile — check "
                        "it is the MCP `industry` value, not `insync_subindustry`.")
    elif not PROFILE_STATUS["matched"]:
        warnings.append(f"no sector profile for industry '{_ind}' — default income-statement "
                        "template in use (expected for most operating sectors).")


    _resolved, _seen = [], set()
    for _code in fin_codes:
        _pick = _code
        if parse_value(cur.get(_code, {}).get("VALUE"))[0] is None:
            for _fb in FIN_FALLBACK.get(_code, []):
                if _fb in _seen or _fb in fin_codes:
                    continue
                if parse_value(cur.get(_fb, {}).get("VALUE"))[0] is not None:
                    warnings.append(f"financial row {_code} has no {lq} actual — substituted {_fb}")
                    _pick = _fb
                    break
        if _pick in _seen:
            continue
        _seen.add(_pick)
        _resolved.append(_pick)
        if _pick not in fin_names:
            fin_names[_pick] = FIN_FALLBACK_NAMES.get(_pick) or FIN_NAMES.get(_pick, _pick)
    fin_codes = _resolved

    for _code in fin_codes:
        _twin = ADJ_TWIN.get(_code)
        if not _twin or _twin in fin_codes:
            continue
        if parse_value(cur.get(_twin, {}).get("VALUE"))[0] is not None:
            warnings.append(
                f"BASIS: {fin_names.get(_code, _code)} ({_code}) is the REPORTED line, but the "
                f"adjusted twin {_twin} is populated for this issuer. Under the adjusted-first "
                f"policy the row should be {_twin} — correct the sector profile in "
                f"assets/sector_profiles.json.")
        else:
            warnings.append(
                f"BASIS: {fin_names.get(_code, _code)} ({_code}) is on a REPORTED basis because "
                f"the adjusted twin {_twin} is empty for this issuer. Expected under "
                f"adjusted-first; say so if the row is quoted in prose.")

    _undeclared_polarity = []
    key_financials, vals, money_rows = [], {}, []
    for code in fin_codes:
        c, cu_ = parse_value(cur.get(code, {}).get("VALUE"))
        p, _ = parse_value(pri.get(code, {}).get("VALUE"))
        vals[code] = (c, p)
        if c is None:
            _rv = cur.get(code, {}).get("VALUE")
            if _rv not in (None, ""):
                warnings.append(f"financial row {code}: the {lq} value {_rv!r} could not be read — row omitted")
            else:
                warnings.append(f"missing actual for {code} {lq} — row omitted (renderer shows dash)")
            continue
        label = fin_names[code] + (f" ({lq})" if code == "IS_P_101" else "")
        _cost_row = code in lower_is_better_set(profile)
        if not _cost_row:
            _undeclared_polarity.append((code, fin_names[code]))
        if cu_ == "x":
            _r = {"metric": label, "value": f"{_xr(c, 2)}x",
                  "yoy": (f"{_xrs(c - p, 2)}x" if p is not None else "—")}
            _r["lower_is_better" if _cost_row else "ungraded"] = True
            key_financials.append(_r)
            continue
        if cu_ == "%":
            bps_ = int(_xr((c - p) * 100, 0)) if p is not None else None
            _r = {"metric": label, "value": f"{_xr(c, 1)}%",
                  "yoy": (f"{'+' if bps_ >= 0 else ''}{bps_}bps" if bps_ is not None else "—")}
            if _cost_row:
                _r["lower_is_better"] = True
            key_financials.append(_r)
            continue
        is_eps = "EPS" in fin_names[code] or "/Share" in fin_names[code] or "BVPS" in fin_names[code]
        _sym = cur_sym(cur.get(code, {}).get("VALUE"))
        key_financials.append({
            "metric": label + (" ($/sh)" if is_eps else ""),
            "value": f"{_sym}{_xr(c, 2)}" if is_eps else None,
            "yoy": growth_cell(c, p, fin_names[code]),
            **({"lower_is_better": True} if _cost_row else {}),
        })
        if not is_eps:
            money_rows.append((len(key_financials) - 1, c, _sym))
    if money_rows:
        _mx = max(abs(mn) for _, mn, _ in money_rows)
        if _mx >= 1e6:
            _div, _unit = 1e6, "T"
        elif _mx >= 1e5:
            _div, _unit = 1e3, "B"
        else:
            _div, _unit = 1.0, "M"
        _small_m = min((abs(mn) for _, mn, _ in money_rows if mn), default=0.0)
        while _div > 1.0 and _small_m and _small_m / _div < 0.001:
            _div, _unit = (1e3, "B") if _div == 1e6 else (1.0, "M")
        if _unit == "M":
            _dp = 0
        else:
            _small = min((abs(mn) / _div for _, mn, _ in money_rows if mn), default=1.0)
            _int_digits = len(str(int(_small))) if _small >= 1 else 0
            _dp = max(1, min(3, 3 - _int_digits))
        for _i, _mn, _sy in money_rows:
            key_financials[_i]["value"] = f"{_sy}{_sep(_xr(_mn / _div, _dp))}{_unit}"
    _fin_floor = profile.get("min_fin_rows", MIN_FIN_ROWS)
    if len(key_financials) < _fin_floor:
        warnings.append(f"THIN FINANCIALS: only {len(key_financials)} of the prescribed rows "
                        f"populated (floor {_fin_floor}). The MCP has no data for the "
                        "remaining codes on this issuer — add or correct a sector profile in "
                        "assets/sector_profiles.json rather than shipping a half-empty table.")
    (rev_c, rev_p) = vals.get("IS_P_101", (None, None))
    _gm_code = "IS_P_106" if vals.get("IS_P_106", (None, None))[0] is not None else "IS_P_105"
    (gp_c, gp_p) = vals.get(_gm_code, (None, None))
    _gm_label = "Gross Margin (Adj)" if _gm_code == "IS_P_106" else "Gross Margin"
    if rev_c and gp_c and not profile.get("suppress_gross_margin"):
        gm_c = gp_c / rev_c * 100.0
        gm_p = (gp_p / rev_p * 100.0) if (rev_p and gp_p) else None
        bps = int(_xr((gm_c - gm_p) * 100, 0)) if gm_p is not None else None
        key_financials.append({
            "metric": _gm_label, "value": f"{_xr(gm_c, 1)}%",
            "yoy": (f"{'+' if bps >= 0 else ''}{bps}bps" if bps is not None else "—"),
        })

    master_kpis = {k_["ltcode"]: k_ for k_ in master.get("kpis", [])}
    universe = {u["ltcode"]: u for u in raw.get("kpi_universe", [])}
    fetched_codes = {k_["ltcode"] for k_ in raw.get("kpis", [])}
    if universe:
        _on_sheet = fetched_codes | set(fin_codes)
        missing_uni = [u["name"] for c, u in universe.items() if c not in _on_sheet]
        if missing_uni:
            warnings.append("MCP top_kpi universe entries on neither the KPI block nor the "
                            f"financial rows: {', '.join(missing_uni[:4])}")
        backstop = [k_ for k_ in master.get("kpis", [])
                    if k_["tier"] == "T1" and k_["ltcode"] not in universe]
        if backstop:
            warnings.append("master completeness backstop supplied top KPIs missing from MCP top_kpi: "
                            + ", ".join(k_["name"] for k_ in backstop[:4]))
    else:
        missing_top = [k_["name"] for k_ in master.get("kpis", [])
                       if k_["tier"] == "T1" and k_["ltcode"] not in fetched_codes]
        if missing_top:
            warnings.append(f"company master top KPIs not fetched this run: {', '.join(missing_top[:4])}")
    if universe and profile:
        _prof_codes = set(profile.get("kpi_code_whitelist") or [])
        for _r in (profile.get("financial_rows") or []):
            _prof_codes.add(_r[0] if isinstance(_r, (list, tuple)) else str(_r))
        _ignored = [(c, u.get("name", c)) for c, u in universe.items()
                    if "top_kpi" in str(u.get("tags", "")) and c not in _prof_codes]
        if _ignored:
            warnings.append(
                "SECTOR PROFILE MAY BE THIN: the MCP tags %s as headline metric(s) for this "
                "company, and the '%s' profile names %s in neither `financial_rows` nor "
                "`kpi_code_whitelist`. The KPI block may still show them, but the financial "
                "rows and the valuation basis are chosen from the profile, so check "
                "assets/sector_profiles.json before delivering. This is the check that was "
                "missing when a REIT's sheet led with P/E."
                % (", ".join(f"{n} ({c})" for c, n in _ignored[:4]),
                   meta.get("industry"), "them" if len(_ignored) > 1 else "it"))

    operational_kpis = []
    for k in raw.get("kpis", []):
        code = k["ltcode"]
        if code in KF_ONLY_CODES and code in fin_codes:
            warnings.append(f"KPI {code} ({k.get('name', code)}) is already a Key Financials "
                            f"row — not repeated in the KPI table")
            continue
        mk = master_kpis.get(code)
        uni = universe.get(code)
        if universe:
            k = dict(k)
            if uni:
                k["name"] = uni.get("name") or k.get("name")
                tags = uni.get("tags", "")
                k["tier"] = "T1" if ("top_kpi" in tags or "driver_kpi" in tags) else "S"
            else:
                if mk:
                    k["name"] = mk["name"]
                k["tier"] = "S"
            if mk:
                unit_ = (mk.get("unit") or "").strip()
                mag_ = str(mk.get("mag")).strip() if mk.get("mag") is not None else None
                if unit_ in ("N", "C", "R", "MWh"):
                    k["unit_divisor"] = MAG_DIV.get(mag_, 1.0)
                    k["_master_units"] = True
                if unit_ in ("P", "Pct", "%"):
                    k.pop("unit_divisor", None)
        elif mk:
            k = dict(k)
            k["name"] = mk["name"]
            k["tier"] = mk["tier"]
            unit_ = (mk.get("unit") or "").strip()
            mag_ = str(mk.get("mag")).strip() if mk.get("mag") is not None else None
            if unit_ in ("N", "C", "R", "MWh"):
                k["unit_divisor"] = MAG_DIV.get(mag_, 1.0)
                k["_master_units"] = True
            if unit_ in ("P", "Pct", "%"):
                k.pop("unit_divisor", None)
        if not (code in lower_is_better_set(profile) or k.get("lower_is_better")):
            _undeclared_polarity.append((code, k.get("name", "")))
        if code in lower_is_better_set(profile) and not k.get("lower_is_better"):
            k = dict(k)
            k["lower_is_better"] = True
            warnings.append(f"KPI {code} ({k.get('name', code)}): lower_is_better applied from "
                            f"the {meta.get('industry')} sector profile — the payload omitted it")
        c, cu = parse_value(cur.get(code, {}).get("VALUE"))
        p, _ = parse_value(pri.get(code, {}).get("VALUE"))
        e, _ = parse_value(est_q.get(code, {}).get("VALUE"))
        if c is None:
            _rv = cur.get(code, {}).get("VALUE")
            if _rv not in (None, ""):
                warnings.append(f"KPI {code}: the {lq} value {_rv!r} could not be read — excluded")
            else:
                warnings.append(f"KPI {code} has no {lq} actual — excluded")
            continue
        div = k.get("unit_divisor")
        if cu == "x":
            value = f"{_xr(c, 2)}x"
            if e is None:
                vs = "—"
            elif k.get("lower_is_better"):
                vs = f"{'Beat' if c <= e else 'Miss'} ({_xr(e, 2)}xE)"
            else:
                vs = f"{_xr(e, 2)}xE"
            growth = f"{_xrs(c - p, 2)}x" if p is not None else "—"
        elif div:
            ca, pa, ea = (parse_abs(cur.get(code, {}).get("VALUE")),
                          parse_abs(pri.get(code, {}).get("VALUE")),
                          parse_abs(est_q.get(code, {}).get("VALUE")))
            _inv = bool(k.get("lower_is_better"))
            if k.get("_master_units"):
                rawv = str(cur.get(code, {}).get("VALUE", ""))
                curflag = cur_sym(rawv) if currency_of(rawv) else False
                _sc = kpi_scale(ca)
                value = fmt_kpi(ca, _sc, curflag)
                if ea is None:
                    vs = "—"
                else:
                    _d = fmt_kpi(ca - ea, _sc, curflag, signed=True)
                    vs = f"{'Beat' if ca <= ea else 'Miss'} ({_d})" if _inv else _d
            else:
                d = ca / div
                value = _sep(_xr(d, KPI_DP))
                if ea is None:
                    vs = "—"
                else:
                    _d = fmt_kpi(ca - ea, (div, ""), False, signed=True)
                    vs = f"{'Beat' if ca <= ea else 'Miss'} ({_d})" if _inv else _d
            growth = growth_cell(ca, pa, k["name"])
        elif cu == "%":
            value = f"{_xr(c, 1)}%"
            if e is None:
                vs = "—"
            else:
                better = (c <= e) if k.get("lower_is_better") else (c >= e)
                vs = f"{'Beat' if better else 'Miss'} ({_xr(e, 1)}%E)"
            growth = f"{'+' if c - p >= 0 else ''}{int(_xr((c - p) * 100, 0))}bps" if p is not None else "—"
        elif universe and code in universe:
            ca, pa, ea = (parse_abs(cur.get(code, {}).get("VALUE")),
                          parse_abs(pri.get(code, {}).get("VALUE")),
                          parse_abs(est_q.get(code, {}).get("VALUE")))
            rawv = str(cur.get(code, {}).get("VALUE", ""))
            curflag = cur_sym(rawv) if currency_of(rawv) else False
            _sc = kpi_scale(ca)
            value = fmt_kpi(ca, _sc, curflag)
            if ea is None:
                vs = "—"
            else:
                _d = fmt_kpi(ca - ea, _sc, curflag, signed=True)
                if k.get("lower_is_better"):
                    vs = f"{'Beat' if ca <= ea else 'Miss'} ({_d})"
                else:
                    vs = _d
            growth = growth_cell(ca, pa, k["name"])
        else:
            _ca, _pa, _ea = (parse_abs(cur.get(code, {}).get("VALUE")),
                             parse_abs(pri.get(code, {}).get("VALUE")),
                             parse_abs(est_q.get(code, {}).get("VALUE")))
            _rawv = str(cur.get(code, {}).get("VALUE", ""))
            _curflag = cur_sym(_rawv) if currency_of(_rawv) else False
            _sc = kpi_scale(_ca)
            value = fmt_kpi(_ca, _sc, _curflag)
            if _ea is None:
                vs = "—"
            else:
                _d = fmt_kpi(_ca - _ea, _sc, _curflag, signed=True)
                if k.get("lower_is_better"):
                    vs = f"{'Beat' if _ca <= _ea else 'Miss'} ({_d})"
                else:
                    vs = _d
            growth = growth_cell(_ca, _pa, k["name"])
        if str(vs).strip() in ("—", "-", "", "n/a"):
            warnings.append(
                "KPI %s (%s) has a %s actual but NO estimate, so its vs-Est cell is a dash. "
                "SKILL.md 9.2 asks for both — when you pick KPIs, prefer a code that carries "
                "a consensus figure, and replace this one from the priority order if a "
                "qualifying candidate is left. The row is kept because its value and Y/Y are "
                "real." % (code, k.get("name", code), lq))
        row_ = {"name": k["name"], "value": value, "vs_est": vs, "yoy": growth}
        if cu == "x" and not k.get("lower_is_better"):
            row_["ungraded"] = True
        if k.get("lower_is_better"):
            row_["lower_is_better"] = True
        if k.get("tier"):
            row_["tier"] = k["tier"]
        _val_s = str(row_.get("value", ""))
        _name_s = str(row_.get("name", ""))
        if (_val_s and _val_s[:1] not in "$\u20ac\u00a3\u00a5"
                and not _val_s.endswith(("K", "M", "B", "T", "bps", "%", "x"))
                and _val_s not in ("\u2014", "-", "n/a", NOT_MEANINGFUL)
                and not re.search(r"[(/]|\b(?:bps|pts?|days?|units?|000s|mn|bn)\b",
                                  _name_s, re.I)):
            warnings.append(
                "KPI %s (%s) renders as the bare figure %r: no currency symbol, no magnitude "
                "suffix, and no unit in the label, so the reader cannot tell what scale it "
                "is on (SKILL.md 14.4). This is the one thing the script cannot infer - an "
                "unsuffixed MCP value is read as UNITS here and as MILLIONS in a Key "
                "Financials row. Fix it at the source: name the unit in the KPI label, or "
                "add a `unit_divisor` (SKILL.md 9.3)."
                % (code, _name_s, _val_s))
        operational_kpis.append(row_)

    fy1, fy2 = meta.get("fy1"), meta.get("fy2")
    ann = raw.get("estimates_annual") or []
    def ann_row(code, period):
        for r in ann:
            if r["LTCODE"] == code and r["FISCALPERIOD"] == period:
                return r
        return None
    def eps_row(period):
        return ann_row("IS_P_128", period) or ann_row("IS_S_643", period) or ann_row("IS_P_127", period)
    _ps_cv = next((cv for cv in (profile.get("computed_valuation") or []) if cv.get("per_share_code")), None)
    _ps_code = _ps_cv["per_share_code"] if _ps_cv else None
    _ps_lbl = (str(_ps_cv.get("label", "")).split("/")[-1] + "/sh") if _ps_cv else None
    _fy0p = str(int(fy1) - 1) if str(fy1).isdigit() else None
    fwd = {}
    _fwd_base = {}
    for code, period, key in [("IS_P_101", fy1, "fy1_rev"), ("EPS", fy1, "fy1_eps"),
                              ("IS_P_101", fy2, "fy2_rev"), ("EPS", fy2, "fy2_eps")]:
        if code == "EPS":
            r = ann_row(_ps_code, period) if _ps_code else eps_row(period)
        else:
            r = ann_row(code, period)
        if r:
            v, _ = parse_value(r.get("VALUE"))
            g = r.get("YOY_GROWTH")
            g = float(str(g).rstrip("%")) if g is not None else None
            fwd[key] = {"value": v, "growth": g}
            if g is not None:
                _fwd_base[key] = "A" if key == "fy1_eps" else "E"
    for _k, _base_period in (("fy1_eps", "fy0"), ("fy1_rev", "fy0")):
        _e = fwd.get(_k)
        if not _e or _e.get("growth") is not None or _e.get("value") is None:
            continue
        _code = "IS_P_128" if _k.endswith("eps") else "IS_P_101"
        _fy0 = str(int(fy1) - 1) if str(fy1).isdigit() else None
        _base_kind = "E"
        if _k.endswith("eps") and _ps_code:
            _act = next((x for x in (raw.get("actuals") or []) if x.get("LTCODE") == _ps_code
                         and str(x.get("FISCALPERIOD", "")).replace("R-", "") == _fy0), None) if _fy0 else None
            _prior = _act or (ann_row(_ps_code, _fy0) if _fy0 else None)
            _base_kind = "A" if _act else "E"
        else:
            _prior = (eps_row(_fy0) if _k.endswith("eps") else ann_row(_code, _fy0)) if _fy0 else None
        _pv, _ = parse_value(_prior.get("VALUE")) if _prior else (None, None)
        if isinstance(_pv, (int, float)) and _pv > 0:
            _e["growth"] = round((_e["value"] / _pv - 1) * 100.0, 2)
            _fwd_base[_k] = _base_kind
            warnings.append(f"{_k} YOY_GROWTH absent from MCP — derived from FY{_fy0[-2:]} "
                            f"{'actual' if _base_kind == 'A' else 'estimate'}")
        elif isinstance(_pv, (int, float)) and _pv <= 0:
            _dir = ("a LOSS turning into a PROFIT" if _e["value"] > 0 else
                    "a loss on both sides" if _e["value"] < 0 else "a swing to breakeven")
            warnings.append(
                "%s: FY%s consensus is %s and FY%s is %s — %s. The MCP gave no YOY_GROWTH and "
                "one CANNOT be derived: a growth rate needs a positive base, so dividing by "
                "%s would invert the sign and report a rise as a fall. The box prints a dash. "
                "Describe the direction in words and quote the two figures, never a rate."
                % (_k, _fy0[-2:], fmt_signed(_pv), str(fy1)[-2:], fmt_signed(_e["value"]),
                   _dir, fmt_signed(_pv)))

    nq = raw.get("estimates_next_quarter") or []
    if _ps_code:
        nq_eps = next((r for r in nq if r["LTCODE"] == _ps_code), None)
        if nq_eps is None:
            warnings.append(f"next-quarter {_ps_lbl}: no {_ps_code} consensus for "
                            f"{meta.get('next_quarter', 'the next quarter')} was supplied, so the slot "
                            f"is omitted (EPS is never substituted on this sector). Fetch it (SKILL.md 6.5).")
    else:
        nq_eps = next((r for r in nq if r["LTCODE"] in ("IS_P_128", "IS_S_643", "IS_P_127")), None)

    _VAL_BOUNDS = {"P/E_Ratio": (1.0, 300.0), "P/E_Adj_Ratio": (1.0, 300.0),
                   "P/FFO": (1.0, 200.0), "EV/EBITDA": (0.5, 200.0),
                   "EV/EBITDA_Adj": (0.5, 200.0), "EV/Sales": (0.05, 100.0),
                   "P/B_Ratio": (0.05, 100.0)}
    groups = defaultdict(list)
    _val_rows = list(raw.get("valuation") or [])
    _series = raw.get("valuation_series") or {}
    if isinstance(_series, dict):
        for _met, _pts in _series.items():
            if isinstance(_pts, dict):
                _val_rows += [{"metric": _met, "DATE": _d, "value": _v}
                              for _d, _v in _pts.items()]
            elif isinstance(_pts, list):
                _val_rows += [{"metric": _met, "DATE": _p[0], "value": _p[1]}
                              for _p in _pts if isinstance(_p, (list, tuple)) and len(_p) == 2]
    for r in _val_rows:
        met = r.get("metric") or r.get("METRIC")
        v = r.get("value") if r.get("value") is not None else r.get("VALUE")
        dt = r.get("DATE") or r.get("date")
        if v is not None:
            try:
                _fv = float(str(v).strip().replace(",", "").rstrip("xX"))
            except (TypeError, ValueError):
                warnings.append(
                    "valuation row for %s carries %r, which is not a number — that point is "
                    "SKIPPED. The MCP returns this for a metric it cannot compute on a given "
                    "date; if many points read this way the average will be thin and the "
                    "history guard below will say so."
                    % (met or "an unnamed metric", v))
                continue
            if dt is None or str(dt).strip() == "":
                warnings.append(
                    "valuation row for %s (value %r) has no DATE — that point is SKIPPED. The "
                    "date orders the series and selects the current multiple, so an undated "
                    "point cannot be placed. Re-run get_valuation and paste the rows as "
                    "returned." % (met or "an unnamed metric", v))
                continue
            _lo, _hi = _VAL_BOUNDS.get(met, (None, None))
            if _lo is not None and not (_lo <= abs(_fv) <= _hi):
                warnings.append(
                    f"VALUATION UNIT CHECK: {met} = {_fv:,.4g}x is outside the plausible range "
                    f"{_lo}x-{_hi}x and has been SUPPRESSED. This is a currency mismatch, not a "
                    f"market fact: get_valuation divides a listing-currency price by "
                    f"reporting-currency earnings. Verify the basis before quoting a multiple.")
                continue
            groups[met].append((dt, _fv))
    val_stats = {}
    for met, pts in groups.items():
        pts.sort()
        val_stats[met] = {"current": pts[-1][1], "avg5y": mean(v for _, v in pts), "n": len(pts),
                          "current_date": str(pts[-1][0])[:10]}
        _span_days = 0
        if len(pts) > 1:
            try:
                import datetime as _dt
                _d0 = _dt.date.fromisoformat(str(pts[0][0])[:10])
                _d1 = _dt.date.fromisoformat(str(pts[-1][0])[:10])
                _span_days = (_d1 - _d0).days
            except (ValueError, TypeError):
                _span_days = 0
        val_stats[met]["span_days"] = _span_days
        _expected = max(4, round(_span_days / 30.44)) if _span_days else len(pts)
        _multiyear = _span_days >= 900
        _partial_floor = 48 if _multiyear else 16
        _suppress_floor = 8
        val_stats[met]["thin"] = len(pts) < _suppress_floor
        _cur = pts[-1][1]
        _signs = {(1 if v > 0 else -1 if v < 0 else 0) for _, v in pts}
        val_stats[met]["not_meaningful"] = isinstance(_cur, (int, float)) and _cur <= 0
        val_stats[met]["sign_mixed"] = len(_signs - {0}) > 1
        if val_stats[met]["not_meaningful"]:
            warnings.append(
                f"{met} is {_cur:,.4g}x, which is not a multiple: the denominator is "
                f"negative. The sheet prints n/a with the reason rather than a number, and "
                f"quotes no premium or discount against it.")
        if val_stats[met]["sign_mixed"]:
            warnings.append(
                f"valuation history for {met} CROSSES ZERO ({len(pts)} points) — the average "
                f"is an artifact of where the denominator changed sign, not an average "
                f"multiple, and has been SUPPRESSED.")
        if _multiyear and _expected and len(pts) < 0.6 * _expected:
            warnings.append(f"SPARSE valuation history for {met}: {len(pts)} points across "
                            f"~{_expected} months — the average is taken over whichever "
                            "months reported, not a continuous series. Verify before quoting "
                            "the premium/(discount).")
        if len(pts) < _partial_floor:
            warnings.append(f"valuation history for {met} has only {len(pts)} points — the sheet labels the average with the window it actually covers, not 5Y")
        if len(pts) < _suppress_floor:
            warnings.append(f"valuation history for {met} has only {len(pts)} points — "
                            f"history average SUPPRESSED on the sheet (below {_suppress_floor}-point floor)")

    def avg_window(met):
        d = (val_stats.get(met) or {}).get("span_days") or 0
        if d <= 0:
            return "Avg"
        yrs = d / 365.25
        if yrs >= 1.5:
            return "%dY Avg" % round(yrs)
        months = max(1, round(d / 30.44))
        return "%dM Avg" % months

    def pick(adj_name, gaap_name):
        if adj_name in val_stats:
            return adj_name, "Adj"
        if gaap_name in val_stats:
            return gaap_name, "GAAP"
        return None, None

    pe_key, pe_basis = pick("P/E_Adj_Ratio", "P/E_Ratio")
    ev_key, ev_basis = pick("EV/EBITDA_Adj", "EV/EBITDA")

    def vs(met, key, dp=1):
        if met not in val_stats:
            return None
        if key == "avg5y" and (val_stats[met].get("thin")
                               or val_stats[met].get("sign_mixed")
                               or val_stats[met].get("not_meaningful")):
            return None
        if key == "current" and val_stats[met].get("not_meaningful"):
            return None
        return round(val_stats[met][key], dp)

    def vs_raw(met, key):
        if vs(met, key) is None:
            return None
        return val_stats[met][key]

    def not_meaningful(met):
        return bool(met and (val_stats.get(met) or {}).get("not_meaningful"))

    def mult_slot(label, cur, avg, met, window_key=None):
        if not_meaningful(met):
            _raw = (val_stats.get(met) or {}).get("current")
            return {"label": label, "current": "n/a",
                    "context": f"negative {met.replace('_Ratio', '').replace('_Adj', ' (Adj)')}"
                               f" ({_raw:,.4g}x) — not a multiple"}
        _p = prem(vs_raw(met, "current") if met else cur,
                  vs_raw(met, "avg5y") if met else avg)
        return {"label": label, "current": fmt_x(cur) if cur else "—",
                "context": f"{avg_window(window_key or met)} {fmt_x(avg)} ({_p or prem(cur, avg)})"
                           if avg else "—"}

    def prem(cur_v, base_v):
        if cur_v is None or base_v in (None, 0):
            return None
        return fmt_pct((cur_v / base_v - 1) * 100.0)

    pe, pe_avg = (vs(pe_key, "current"), vs(pe_key, "avg5y")) if pe_key else (None, None)
    ev_eb, ev_eb_avg = (vs(ev_key, "current"), vs(ev_key, "avg5y")) if ev_key else (None, None)
    ev_s, ev_s_avg = vs("EV/Sales", "current"), vs("EV/Sales", "avg5y")
    pb, pb_avg = vs("P/B_Ratio", "current"), vs("P/B_Ratio", "avg5y")
    dy_raw = vs("Dividend_Yield", "current", 4)
    _dy_stale_days = None
    if dy_raw is not None:
        _dy_date = (val_stats.get("Dividend_Yield") or {}).get("current_date")
        _px_date = str(mkt.get("price_date") or "")[:10]
        try:
            import datetime as _dtm
            _dy_stale_days = (_dtm.date.fromisoformat(_px_date)
                              - _dtm.date.fromisoformat(_dy_date)).days
        except (ValueError, TypeError):
            _dy_stale_days = None
        if _dy_stale_days is not None and _dy_stale_days > DIV_YIELD_MAX_LAG_DAYS:
            warnings.append(
                "STALE DIVIDEND YIELD: the newest Dividend_Yield point is %s, %d days before "
                "the %s price this sheet is priced at, so it is not a current yield and the "
                "slot is DROPPED (SKILL.md 13: omitted, not estimated). This is the signature "
                "of an issuer that does not pay a dividend, or stopped: the series ends and "
                "its last value keeps looking current. If the company does pay, supply "
                "market.dividend_annual and the slot is computed from that instead."
                % (_dy_date, _dy_stale_days, _px_date))
            dy_raw = None
    div_yield_pct = (dy_raw * 100 if dy_raw is not None and dy_raw < 1 else dy_raw)

    ADJ_CODES = {"IS_P_128", "IS_S_643", "IS_P_124", "IS_P_110", "IS_P_114", "IS_P_106"}
    mv = (master.get("val_multiples") or {})
    master_pe_adj = None
    if master.get("fins") is not None and mv.get("primary"):
        master_pe_adj = "Adj" if "Adj" in str(mv.get("primary", "")) else "GAAP"
    prim = str(mv.get("primary", ""))
    is_pe_primary = ("P/E" in prim or "P / NTM E" in prim) and not any(x in prim for x in ("BV", "Book", "EBITDA", "EBIT", "Revenue", "Sales"))
    if master_pe_adj and pe_basis and is_pe_primary and master_pe_adj != pe_basis:
        warnings.append(f"BASIS MISMATCH: master prescribes {master_pe_adj} P/E but the fetched series is {pe_basis} — refetch the other variant")

    def _cur_sym(v):
        s = str(v or "").strip()
        return _ISO_SYM.get(s, _ISO_SYM.get(s.upper(), s)) if s not in ("GBp", "GBX", "GBx") else "GBp"
    _px_sym = _cur_sym(meta.get("price_currency")) or meta.get("currency_symbol", "$")
    if meta.get("price_currency") and _px_sym != _cur_sym(meta.get("currency_symbol", "$")):
        warnings.append(
            f"DUAL CURRENCY: financials are in {meta.get('currency_symbol','$')} and the listing "
            f"is in {_px_sym}. Every multiple on this sheet divides a "
            f"{_px_sym} price by {meta.get('currency_symbol','$')} earnings and is "
            f"NOT FX-corrected. Do not quote a multiple from this sheet without converting first.")
    def ps_ctx(key):
        _e = fwd.get(key) or {}
        if not isinstance(_e.get("value"), (int, float)):
            warnings.append(f"{_ps_lbl} {key[:3].upper()} consensus ({_ps_code}) not supplied — the "
                            f"box shows a dash; EPS is never substituted on this sector (SKILL.md 6.5)")
            return "—"
        _v = eps_ctx(key, "")
        if _e.get("growth") is None:
            return _v
        if key == "fy1_eps":
            _b = f"FY{str(_fy0p)[-2:]}A" if _fwd_base.get(key) == "A" else f"FY{str(_fy0p)[-2:]} est."
        else:
            _b = f"FY{str(fy1)[-2:]}E"
        return f"{_v} vs {_b}"

    def eps_ctx(key, suffix=" Est"):
        _e = fwd.get(key) or {}
        _v = _e.get("value")
        if not isinstance(_v, (int, float)):
            if _e:
                warnings.append(
                    "%s: the consensus row exists but its VALUE is not a number (%r), so the "
                    "figure renders as a dash. Re-run get_estimates for that period; do not "
                    "hand-fill it." % (key, _v))
            return "—"
        return f"{meta.get('currency_symbol', '$')}{_xr(_v, 2)}{suffix}"

    price, fds = mkt.get("price"), mkt.get("fds_millions")
    mcap_b = _xrf(price * fds / 1000.0, 0) if (price and fds) else None
    eps_g1 = fwd.get("fy1_eps", {}).get("growth")

    PEG_MIN, PEG_MAX = 0.1, 10.0
    peg, peg_note = None, None
    if pe and eps_g1 is not None and not _ps_code:
        if eps_g1 <= 0:
            peg_note = ("negative FY%s EPS growth (%s) — PEG is not a ratio"
                        % (fy1[-2:], fmt_pct(eps_g1))) if eps_g1 < 0 else \
                       ("FY%s EPS growth is zero — no denominator" % fy1[-2:])
            warnings.append(
                "PEG SUPPRESSED: FY%s consensus EPS growth is %s, so P/E divided by it is "
                "not a PEG — it is a negative number (or a division by zero) with no "
                "interpretation. The slot prints n/a with the reason. Do NOT quote a PEG for "
                "this issuer; quote the P/E and describe the earnings direction in words."
                % (fy1[-2:], fmt_pct(eps_g1)))
        else:
            _peg_raw = pe / eps_g1
            _peg_shown = _xrf(_peg_raw, 1)
            if _peg_shown < PEG_MIN or _peg_raw > PEG_MAX:
                _why = ("above" if _peg_raw > PEG_MAX else "below")
                peg_note = ("PEG %s from FY%s EPS growth of %s — not a usable denominator"
                            % ((_xr(_peg_raw, 1) + "x" if _peg_raw >= 0.05 else "under 0.1x"),
                               fy1[-2:], fmt_pct(eps_g1, signed=False)))
                warnings.append(
                    "PEG SUPPRESSED: P/E %.1fx over FY%s EPS growth of %s gives %.4gx, %s the "
                    "%.1f-%.0fx bound. At either end the ratio is set by the size of the "
                    "denominator rather than by the valuation: near zero growth it explodes, "
                    "and at very high growth it collapses to a figure that prints as 0.0x. "
                    "Both are usually a base effect in the growth rate. The slot prints n/a "
                    "with the reason — this is a data condition and NOT a QC failure, so the "
                    "run continues. Quote the P/E and the growth rate separately instead."
                    % (pe, fy1[-2:], fmt_pct(eps_g1, signed=False), _peg_raw, _why,
                       PEG_MIN, PEG_MAX))
            else:
                peg = round(_peg_raw, 2)
    pos_high = (price / mkt["week52_high"] - 1) * 100 if (price and mkt.get("week52_high")) else None
    eps_g2 = fwd.get("fy2_eps", {}).get("growth")
    market_snapshot = [
        {"label": "PRICE", "value": f"{_px_sym}{_sep(_xr(price, 2))}",
         "context": f"as of {mkt.get('price_date', 'n/a')}"},
        {"label": "MARKET CAP", "value": f"{_px_sym}{_sep(_xr(mcap_b, 0))}B" if mcap_b else "—",
         "context": f"{_sep(_xr(mkt.get('fds_millions', 0), 0))}M shares"},
        {"label": "52W POSITION", "value": fmt_pct(pos_high) if pos_high is not None else "—",
         "context": f"H {_px_sym}{_sep(_xr(mkt['week52_high'], 2))} / L {_px_sym}{_sep(_xr(mkt['week52_low'], 2))}"},
        {"label": "NEXT EARNINGS", "value": meta["next_earnings"]["date"],
         "context": meta["next_earnings"]["period"]},
        {"label": f"FY{fy1[-2:]}E {_ps_lbl or 'EPS'} Gr", "value": fmt_pct(eps_g1) if eps_g1 is not None else "—",
         "context": ps_ctx("fy1_eps") if _ps_code else eps_ctx("fy1_eps")},
        {"label": f"FY{fy2[-2:]}E {_ps_lbl or 'EPS'} Gr", "value": fmt_pct(eps_g2) if eps_g2 is not None else "—",
         "context": ps_ctx("fy2_eps") if _ps_code else eps_ctx("fy2_eps")},
    ]

    valuation_metrics = [
        mult_slot(f"NTM P/E ({pe_basis})" if pe_basis else "NTM P/E", pe, pe_avg, pe_key),
        mult_slot(f"NTM EV/EBITDA ({ev_basis})" if ev_basis else "NTM EV/EBITDA",
                  ev_eb, ev_eb_avg, ev_key),
        mult_slot("NTM EV/Sales", ev_s, ev_s_avg, "EV/Sales"),
        {"label": "PEG (NTM)",
         "current": "n/a" if peg_note else (fmt_x(peg) if peg else "—"),
         "context": peg_note or (f"FY{fy1[-2:]}E EPS Gr: {fmt_pct(eps_g1, signed=False)}"
                                 if eps_g1 else "—")},
    ]
    for _cv in (profile.get("computed_valuation") or []):
        _code, _lbl = _cv.get("per_share_code"), _cv.get("label", "computed")
        _r = ann_row(_code, fy1) if _code else None
        _ps = parse_value(_r.get("VALUE"))[0] if _r else None
        if _ps and price and _ps > 0:
            valuation_metrics.insert(0, {
                "label": f"{_lbl} (FY{str(fy1)[-2:]}E)",
                "current": fmt_x(price / _ps),
                "context": f"FY{str(fy1)[-2:]}E {_lbl.split('/')[-1]}/sh "
                           f"{meta.get('currency_symbol', '$')}{_sep(_xr(_ps, 2))} consensus"})
        else:
            warnings.append(
                f"{_lbl} SUPPRESSED: the '{meta.get('industry')}' profile asks for it, but "
                f"{'the FY' + str(fy1) + ' consensus for ' + str(_code) + ' was not supplied' if not _ps else 'the per-share figure is not positive'}. "
                f"It is the headline multiple for this sector — fetch {_code} in the "
                f"estimates_annual call and re-run, or the sheet leads on a lesser metric.")

    _nq_dupes_fy1 = (meta.get("next_quarter") is not None
                     and str(meta.get("next_quarter")) == str(meta.get("fy1")))
    if nq_eps and _nq_dupes_fy1:
        warnings.append(
            f"annual filer: forward EPS slot suppressed — its label ({meta.get('fy1')}) would "
            f"duplicate the FY{str(fy1)[-2:]}E EPS box in the market snapshot while showing a "
            f"different consensus series (estimates_next_quarter vs estimates_annual)")
    elif nq_eps:
        v, _ = parse_value(nq_eps.get("VALUE"))
        g = nq_eps.get("YOY_GROWTH")
        if isinstance(v, (int, float)):
            valuation_metrics.append({"label": f"{meta.get('next_quarter','NQ')}E {_ps_lbl or 'EPS'}",
                                      "current": f"{meta.get('currency_symbol', '$')}{_xr(v, 2)}",
                                      "context": f"{fmt_growth_str(g, signed=False)} YoY" if g else "—"})
        else:
            warnings.append(
                "next-quarter EPS: the consensus row exists but its VALUE is not a number "
                "(%r), so the slot is omitted rather than printed empty. Re-run get_estimates "
                "for %s." % (nq_eps.get("VALUE"), meta.get("next_quarter", "the next period")))
    _optional = []
    if pb is not None:
        _optional.append({"label": "P/B (NTM)", "current": fmt_x(pb),
                          "context": f"{avg_window('P/B_Ratio')} {fmt_x(pb_avg)} "
                                     f"({prem(vs_raw('P/B_Ratio', 'current'), vs_raw('P/B_Ratio', 'avg5y'))})"
                          if pb_avg else "—"})
    if div_yield_pct is not None:
        _optional.append({"label": "Div Yield", "current": f"{_xr(div_yield_pct, 1)}%",
                          "context": "from MCP Dividend_Yield series"})
    elif (div := mkt.get("dividend_annual")) and price:
        _optional.append({"label": "Div Yield", "current": f"{_xr(div / price * 100, 1)}%",
                                  "context": "computed from annualized dividend"})
    elif div_yield_pct is None and not mkt.get("dividend_annual") and _dy_stale_days is None:
        warnings.append("no dividend yield source supplied — slot omitted (do NOT estimate it)")

    rev_est, _ = parse_value(est_q.get("IS_P_101", {}).get("VALUE"))
    _eps_printed = [c_ for c_ in fin_codes if c_ in ("IS_S_643", "IS_P_128", "IS_P_127")
                    and (vals.get(c_) or (None, None))[0] is not None]
    _eps_code = next((c_ for c_ in _eps_printed if c_ != "IS_P_127"), None) \
        or (_eps_printed[0] if _eps_printed else None)
    eps_act = vals[_eps_code][0] if _eps_code else None
    eps_est = parse_value(est_q.get(_eps_code, {}).get("VALUE"))[0] if _eps_code else None
    signals = {
        "pct_below_52w_high": round((mkt["week52_high"] - price) / mkt["week52_high"] * 100, 1),
        "pct_above_52w_low": round((price - mkt["week52_low"]) / mkt["week52_low"] * 100, 1),
        "revenue_beat_musd": round(rev_c - rev_est) if (rev_c and rev_est) else None,
        "revenue_beat_pct": round(yoy(rev_c, rev_est) if rev_est else 0, 1) if (rev_c and rev_est) else None,
        "eps_actual_vs_est": [eps_act, eps_est] if eps_est is not None else [eps_act, None],
    }

    comparable_companies = []
    PEER_MULT_MIN, PEER_MULT_MAX = 0.0, 200.0

    PEER_MULT_FLOOR = {"pe": 1.0, "ev": 0.5, "es": 0.05, "pb": 0.05}
    _FLOOR_BY_LABEL = {"P/E": "pe", "EV/EBITDA": "ev", "EV/Sales": "es", "P/B": "pb"}

    def in_bound(x, met=None):
        lo = PEER_MULT_FLOOR.get(met, PEER_MULT_MIN)
        if isinstance(x, (int, float)) and lo < x < PEER_MULT_MAX:
            return x
        return None

    def sane_mult(x, who, what):
        _met = _FLOOR_BY_LABEL.get(what)
        if isinstance(x, (int, float)) and in_bound(x, _met) is None:
            _lo = PEER_MULT_FLOOR.get(_met, PEER_MULT_MIN)
            if x <= 0:
                _why = ("A negative multiple means the denominator is negative (a loss), not "
                        "that the stock is cheap.")
            elif x < _lo:
                _why = ("A small POSITIVE multiple below the floor is usually a CURRENCY "
                        "SCALE error — GBpence quoted against GBP earnings is a factor of "
                        "100. Check the peer's listing currency before re-supplying it.")
            else:
                _why = ("A multiple this large is usually a near-zero denominator rather "
                        "than a valuation.")
            warnings.append(f"peer {who}: {what} {_xr(x, 1)}x outside the {_lo:g}-"
                            f"{PEER_MULT_MAX:.0f}x sanity bound for {what} — discarded from "
                            f"the table, the rank and the peer median. {_why}")
            return None
        return x

    def fmt_mult(x):
        return f"{_xr(x, 1)}x" if isinstance(x, (int, float)) and x is not None else "—"

    GROWTH_MAX = 1000.0

    def sane_growth(v, who):
        if isinstance(v, (int, float)) and abs(v) > GROWTH_MAX:
            warnings.append(
                f"{who}: revenue growth of {_xrs(v, 1, sep=True)}% is beyond the {GROWTH_MAX:,.0f}% "
                f"plausibility bound — this is a base effect, not a growth rate (a near-zero "
                f"prior-year figure). The cell prints n/m and the row takes no growth rank, "
                f"so it cannot win a comparison it does not support.")
            return None
        return v
    _SUPPRESS_BASIS = {"P/E": "pe", "P/E_Ratio": "pe", "P/E_Adj_Ratio": "pe",
                       "EV/EBITDA": "ev", "EV/EBITDA_Adj": "ev", "EV/Sales": "es",
                       "P/B": "pb", "P/B_Ratio": "pb"}
    _suppressed = profile.get("suppress_valuation", [])
    _sup_bases = {_SUPPRESS_BASIS[m] for m in _suppressed if m in _SUPPRESS_BASIS}
    _unmapped_sup = [m for m in _suppressed if m not in _SUPPRESS_BASIS and m != "PEG"]
    if _unmapped_sup:
        warnings.append(
            f"sector profile suppresses {', '.join(_unmapped_sup)}, which is dropped from the "
            "valuation slots but is NOT a peer-table basis this build knows how to remove. "
            "Add it to _SUPPRESS_BASIS in compute_tearsheet.py or the comp table and the "
            "valuation strip will disagree.")

    def keep(basis, v):
        return None if basis in _sup_bases else v

    _PEER_MULT_KEYS = (("pe", "P/E"), ("ev_ebitda", "EV/EBITDA"),
                       ("ev_sales", "EV/Sales"), ("pb", "P/B"))

    def _coerce_peer_mult(v):
        if v is None or (isinstance(v, str) and not v.strip()):
            return None, None
        if isinstance(v, bool):
            return None, "a boolean is not a multiple"
        if isinstance(v, (int, float)):
            return v, None
        if isinstance(v, str):
            s = v.strip().replace(",", "")
            if s[-1:] in ("x", "X"):
                s = s[:-1].strip()
            try:
                return float(s), None
            except ValueError:
                if v.strip().startswith("~"):
                    return None, ("a '~' approximation belongs in the *_approx key (§7.8), "
                                  "not in this one")
                return None, "not a number"
        return None, "%s is not a multiple" % type(v).__name__

    _peers_in = []
    for _p in raw.get("peers") or []:
        if _p.get("is_target"):
            _peers_in.append(_p)
            continue
        _p = dict(_p)
        for _k, _what in _PEER_MULT_KEYS:
            if _k not in _p:
                continue
            _num, _why = _coerce_peer_mult(_p[_k])
            if _why is not None:
                warnings.append(
                    "peer %s: %s is %r — %s. Dropped from the cell, the rank and the peer "
                    "median, so the median is taken over one fewer company. Supply it as a "
                    "raw number (§7.8) or omit the key."
                    % (_p.get("name", "?"), _what, _p[_k], _why))
                _p[_k] = None
            elif _num is not None and not isinstance(_p[_k], (int, float)):
                warnings.append(
                    "peer %s: %s arrived as the string %r and was read as %g. §7.8 asks for "
                    "raw numbers — the script does all rounding. Through 7.2.19 a quoted "
                    "multiple was discarded from the cell, the rank AND the peer median with "
                    "no warning, which moved the headline figure."
                    % (_p.get("name", "?"), _what, _p[_k], _num))
                _p[_k] = _num
            else:
                _p[_k] = _num
        _unav = _p.get("unavailable")
        _p["_unavail"] = set()
        if isinstance(_unav, dict):
            for _k, _why in _unav.items():
                if _k not in UNAVAILABLE_KEYS:
                    warnings.append("peer %s: unavailable key %r is not one of %s - ignored"
                                    % (_p.get("name", "?"), _k, ", ".join(UNAVAILABLE_KEYS)))
                    continue
                _p[_k] = None
                _p["_unavail"].add(_k)
            if _p["_unavail"]:
                warnings.append(
                    "UNAVAILABLE: peer %s - %s refused by InSync (%s). Printed as '%s', left out "
                    "of the rank and the peer median. Say so in the delivery message."
                    % (_p.get("name", "?"), ", ".join(sorted(_p["_unavail"])),
                       ", ".join(sorted({str(_unav[k]) for k in _p["_unavail"]})), UNAVAILABLE))
        _es_, _eb_ = _p.get("ev_sales"), _p.get("ev_ebitda")
        if (isinstance(_es_, (int, float)) and isinstance(_eb_, (int, float))
                and _es_ > 0 and _eb_ > 0 and _es_ / _eb_ > 1):
            warnings.append(
                "IMPOSSIBLE MULTIPLE: peer %s EV/EBITDA %sx is below its EV/Sales %sx, which "
                "would make EBITDA larger than revenue. EV/EBITDA dropped from the cell, the "
                "rank and the peer median."
                % (_p.get("name", "?"), _xr(_eb_, 2), _xr(_es_, 2)))
            _p["ev_ebitda"] = None
        _peers_in.append(_p)

    _tgt_growth_raw = yoy(rev_c, rev_p)
    _tgt_growth_ok = sane_growth(_tgt_growth_raw, "target revenue growth")
    _tgt_growth_cell = (
        NOT_MEANINGFUL if (growth_reason(rev_c, rev_p) is not None
                           or (_tgt_growth_raw is not None and _tgt_growth_ok is None))
        else (fmt_pct(_tgt_growth_raw) or "—"))
    for p_ in _peers_in:
        if p_.get("is_target"):
            row = {"name": p_["name"], "pe": fmt_mult(keep("pe", in_bound(pe, "pe"))),
                   "ev_ebitda": fmt_mult(keep("ev", in_bound(ev_eb, "ev"))),
                   "ev_sales": fmt_mult(keep("es", in_bound(ev_s, "es"))),
                   "pb": fmt_mult(keep("pb", in_bound(pb, "pb"))),
                   "rev_growth": _tgt_growth_cell, "is_target": True}
        else:
            _pe_raw = sane_mult(p_.get("pe"), p_["name"], "P/E") if "pe" not in _sup_bases else None
            _ev_raw = sane_mult(p_.get("ev_ebitda"), p_["name"], "EV/EBITDA") if "ev" not in _sup_bases else None
            _es_raw = sane_mult(p_.get("ev_sales"), p_["name"], "EV/Sales") if "es" not in _sup_bases else None
            _pb_raw = sane_mult(p_.get("pb"), p_["name"], "P/B") if "pb" not in _sup_bases else None
            pe_v = keep("pe", p_.get("pe_approx")) if p_.get("pe_approx") else fmt_mult(_pe_raw)
            ev_v = keep("ev", p_.get("ev_ebitda_approx")) if p_.get("ev_ebitda_approx") else fmt_mult(_ev_raw)
            es_v = keep("es", p_.get("ev_sales_approx")) if p_.get("ev_sales_approx") else fmt_mult(_es_raw)
            pb_v = keep("pb", p_.get("pb_approx")) if p_.get("pb_approx") else fmt_mult(_pb_raw)
            pe_v, ev_v, es_v, pb_v = (v if v else "—" for v in (pe_v, ev_v, es_v, pb_v))
            for ap in ("pe_approx", "ev_ebitda_approx", "ev_sales_approx", "pb_approx"):
                if p_.get(ap) and not str(p_[ap]).startswith("~"):
                    warnings.append(f"peer {p_['name']}: {ap} must start with '~' - flagged")
            try:
                _g_raw = (float(str(p_.get("rev_growth")).rstrip("%"))
                          if p_.get("rev_growth") is not None else None)
            except ValueError:
                _g_raw = None
            _g_cell = ("n/m" if (_g_raw is not None and sane_growth(_g_raw, "peer " + p_["name"]) is None)
                       else fmt_growth_str(p_.get("rev_growth")))
            row = {"name": p_["name"], "pe": pe_v, "ev_ebitda": ev_v, "ev_sales": es_v,
                   "pb": pb_v, "rev_growth": _g_cell, "is_target": False}
            for _k in p_.get("_unavail") or ():
                row[_k] = UNAVAILABLE
        comparable_companies.append(row)
    if comparable_companies and sum(1 for r in comparable_companies if r["is_target"]) != 1:
        warnings.append("peers: exactly one row must have is_target true")

    peer_median_note = None
    if comparable_companies:
        raws = []
        for p_ in _peers_in:
            if p_.get("is_target"):
                g = _tgt_growth_ok
                raws.append({"name": p_["name"], "is_target": True,
                             "pe": keep("pe", in_bound(pe, "pe")),
                             "ev": keep("ev", in_bound(ev_eb, "ev")),
                             "es": keep("es", in_bound(ev_s, "es")),
                             "pb": keep("pb", in_bound(pb, "pb")), "g": g})
            else:
                try:
                    g = float(str(p_.get("rev_growth")).rstrip("%")) if p_.get("rev_growth") is not None else None
                except ValueError:
                    g = None
                if isinstance(g, (int, float)) and abs(g) > GROWTH_MAX:
                    g = None
                raws.append({"name": p_["name"], "is_target": False,
                             "pe": keep("pe", in_bound(p_.get("pe"), "pe")),
                             "ev": keep("ev", in_bound(p_.get("ev_ebitda"), "ev")),
                             "es": keep("es", in_bound(p_.get("ev_sales"), "es")),
                             "pb": keep("pb", in_bound(p_.get("pb"), "pb")),
                             "g": g})
        if [r["name"] for r in raws] != [r["name"] for r in comparable_companies]:
            warnings.append(
                "PEER PASS MISALIGNED: the ranking pass and the table pass produced different "
                "company orders, so rank letters would be attached to the wrong rows. This is "
                "a compute_tearsheet.py bug, not a data problem — do not deliver this sheet.")
        def ranks(key, reverse):
            vals_ = sorted([r[key] for r in raws if r[key] is not None], reverse=reverse)
            return {v: i + 1 for i, v in enumerate(vals_)}, len(vals_)
        g_rank, g_n = ranks("g", True)
        pe_rank, pe_n = ranks("pe", False)
        ev_rank, ev_n = ranks("ev", False)
        es_rank, es_n = ranks("es", False)
        pb_rank, pb_n = ranks("pb", False)
        _bases = [("G", g_rank, g_n, "g"), ("P", pe_rank, pe_n, "pe"),
                  ("E", ev_rank, ev_n, "ev"), ("S", es_rank, es_n, "es"),
                  ("B", pb_rank, pb_n, "pb")]
        for row, r_ in zip(comparable_companies, raws):
            parts = []
            for _ltr, _map, _n, _key in _bases:
                if _n < 2:
                    continue
                parts.append(f"{_ltr}{_map[r_[_key]]}" if r_[_key] is not None else f"{_ltr}–")
            row["rank"] = "·".join(parts)

        def median_of(vals):
            s = sorted(vals)
            return s[len(s) // 2] if len(s) % 2 else sum(s[len(s)//2-1:len(s)//2+1]) / 2
        for _key, _own, _name in (("pe", pe, "P/E"), ("ev", ev_eb, "EV/EBITDA"),
                                  ("es", ev_s, "EV/Sales"), ("pb", pb, "P/B")):
            if _key in _sup_bases:
                continue
            _peers = [r[_key] for r in raws
                      if not r.get("is_target") and r[_key] is not None]
            _own_ok = in_bound(_own, _key)
            if len(_peers) >= 2 and _own_ok:
                _med = median_of(_peers)
                _own_exact = {"pe": vs_raw(pe_key, "current") if pe_key else None,
                              "ev": vs_raw(ev_key, "current") if ev_key else None,
                              "es": vs_raw("EV/Sales", "current"),
                              "pb": vs_raw("P/B_Ratio", "current")}.get(_key)
                if _own_exact is None or in_bound(_own_exact, _key) is None:
                    _own_exact = _own_ok
                peer_median_note = {
                    "label": f"vs Peer Median ({_name})",
                    "current": prem(_own_exact, _med) or "—",
                    "context": f"{_name} prem/(disc) to median {fmt_x(_med)} "
                               f"of {len(_peers)} peers"}
                break
        else:
            warnings.append(
                "PEER MEDIAN SUPPRESSED: on none of P/E, EV/EBITDA, EV/Sales or P/B do at "
                "least two peers AND the target all carry a value inside the "
                f"{PEER_MULT_MIN:.0f}-{PEER_MULT_MAX:.0f}x bound. The comp table still shows "
                "revenue growth, which is the comparison that remains meaningful. Do NOT "
                "quote a relative valuation for this issuer from this sheet.")
    for _pref in profile.get("prefer_valuation", []):
        if _pref not in val_stats:
            warnings.append(f"sector profile expects {_pref} for {meta.get('industry')} but the "
                            f"series was not fetched — re-issue get_valuation with {_pref} in "
                            "`metrics`; it is the primary multiple for this sector.")
    if _suppressed:
        def _is_suppressed(label):
            for m in _suppressed:
                if (label.startswith(f"NTM {m}")
                        or label == m
                        or label.startswith(f"{m} (")):
                    return True
            return False
        _before = len(valuation_metrics)
        valuation_metrics = [v for v in valuation_metrics if not _is_suppressed(v["label"])]
        warnings.append(
            f"sector profile suppressed valuation metrics: {', '.join(_suppressed)} "
            f"({_before - len(valuation_metrics)} slot(s) removed; not analytically "
            f"appropriate for {meta.get('industry')})")
    _blank = ("", "-", "–", "—", None, "n/a")
    _empty_slots = [v for v in valuation_metrics
                    if str(v.get("current") or "").strip() in _blank
                    and str(v.get("context") or "").strip() in _blank]
    if _empty_slots:
        valuation_metrics = [v for v in valuation_metrics if v not in _empty_slots]
        warnings.append(
            "%d valuation slot(s) carried no value AND no context and were dropped from the "
            "strip (%s). They are genuinely unavailable for this issuer — nothing was "
            "estimated — but an empty row must not take space a real comparison needs."
            % (len(_empty_slots), ", ".join(v["label"] for v in _empty_slots)))
    if peer_median_note:
        _optional.append(peer_median_note)
    _dropped_for_room = []
    for _slot in _optional:
        if len(valuation_metrics) < 6:
            valuation_metrics.append(_slot)
        else:
            _dropped_for_room.append(_slot["label"])
    if _dropped_for_room:
        warnings.append(
            "valuation strip full at 6 slots — %s did not fit and %s dropped. The fill order "
            "is declared in compute_tearsheet.py (P/B, Div Yield, then the peer median); if "
            "one of these matters more for this issuer than a slot that did fit, say so in "
            "the delivery message."
            % (", ".join(_dropped_for_room),
               "was" if len(_dropped_for_room) == 1 else "were"))

    qc = []
    def check(name, ok):
        qc.append({"check": name, "pass": bool(ok)})
        if not ok:
            warnings.append(f"QC FAIL: {name}")

    _dupes, _seen = [], {}
    for _r in raw.get("actuals") or []:
        _k = (_r.get("LTCODE"), norm_period(_r.get("FISCALPERIOD")))
        _v = str(_r.get("VALUE"))
        if _k in _seen and _seen[_k] != _v:
            _dupes.append((_k[0], _k[1], _seen[_k], _v))
        _seen[_k] = _v
    check("no conflicting duplicate actuals rows", not _dupes)
    for _c, _p, _a, _b in _dupes[:6]:
        warnings.append(
            f"CONFLICTING DUPLICATE: actuals carries {_c} for {_p} twice with different "
            f"values ({_a!r} then {_b!r}). The LAST one is what the sheet uses, so the figure "
            f"may be wrong. Do NOT guess: re-run get_actuals for that code and period and "
            f"paste one row. A restated period (R- prefix) normalises to the same key as the "
            f"original, so keep only the row you mean to report.")
    _NEVER_NEGATIVE = ("IS_P_101",)
    _negs = []
    for _code in _NEVER_NEGATIVE:
        _v = parse_value((cur.get(_code) or {}).get("VALUE"))[0]
        if isinstance(_v, (int, float)) and _v < 0:
            _negs.append((_code, (cur.get(_code) or {}).get("VALUE")))
    check("no negative value in a line that cannot be negative", not _negs)
    for _code, _rawv in _negs:
        warnings.append(
            f"NEGATIVE {_code}: the MCP returned {_rawv!r} for a line that cannot be "
            f"negative. The sheet prints it as given and every derived figure (Y/Y, margins, "
            f"the column unit) is computed from it, so they are all wrong. Re-run get_actuals "
            f"for this code; do not hand-correct the sign.")
    check("price within 52W range",
          mkt["week52_low"] * 0.99 <= price <= mkt["week52_high"] * 1.01 if price else False)
    check("market cap computed and positive (price x FDS)", bool(mcap_b and mcap_b > 0))
    check("no out-of-bound PEG reached the payload",
          peg is None or (round(peg, 1) >= PEG_MIN and peg <= PEG_MAX))
    check("gross margin (GP/Rev) inside 0-100%",
          (not (rev_c and gp_c)) or (0 < gp_c / rev_c < 1))
    check("no discarded peer multiple reached a rank or the peer median",
          all(all(r[_b] is None
                  or (isinstance(r[_b], (int, float)) and not isinstance(r[_b], bool)
                      and PEER_MULT_MIN < r[_b] < PEER_MULT_MAX)
                  for _b in ("pe", "ev", "es", "pb"))
              for r in (raws if comparable_companies else [])))
    check("52W low < high", mkt["week52_low"] < mkt["week52_high"])
    check("FY2 vs FY1 estimate periods distinct", fy1 != fy2)
    mcp_yoy = None
    for r_ in raw.get("actuals", []):
        if r_.get("LTCODE") == "IS_P_101" and norm_period(r_.get("FISCALPERIOD")) == norm_period(lq) and r_.get("YOY_GROWTH"):
            try:
                mcp_yoy = float(str(r_["YOY_GROWTH"]).rstrip("%"))
            except ValueError:
                pass
    _own_yoy = yoy(rev_c, rev_p)
    check("revenue YoY matches MCP YOY_GROWTH (±0.5pp)",
          mcp_yoy is None or _own_yoy is None or abs(_own_yoy - mcp_yoy) < 0.5)
    if mcp_yoy is not None and _own_yoy is None:
        warnings.append(
            "QC NOT COMPARABLE: the MCP supplied a revenue YOY_GROWTH of %+.1f%% but this run "
            "could not compute its own rate to check it against — the prior-year revenue row "
            "is missing, zero or negative (prior=%r, latest=%r). Check meta.prior_year_quarter "
            "and the actuals rows; the MCP tags restated periods 'R-2Q25' (SKILL.md 6.4). The "
            "sheet still builds, and the Y/Y cell says n/m or a dash rather than a number."
            % (mcp_yoy, rev_p, rev_c))
    if mcp_yoy is None:
        warnings.append(
            "QC NOT RUN: the revenue Y/Y cross-check needs YOY_GROWTH on the latest-quarter "
            "IS_P_101 actuals row and none was supplied, so the computed growth rate was "
            "compared against nothing. get_actuals returns the field - paste it with the row "
            "(SKILL.md 6.4) and this becomes a real check on the sheet's headline growth "
            "figure. It is counted as a pass above only because there was nothing to compare.")
    _ebitda_margin_testable = (ev_s is not None and ev_eb is not None
                               and ev_s > 0 and ev_eb > 0)
    check("implied EBITDA margin (EV/Sales ÷ EV/EBITDA) within 0-100%",
          (not _ebitda_margin_testable) or (0 < ev_s / ev_eb < 1))

    _leaks = []
    if _sup_bases:
        _label_of = {"pe": "P/E", "ev": "EV/EBITDA", "es": "EV/Sales", "pb": "P/B"}
        _field_of = {"pe": "pe", "ev": "ev_ebitda", "es": "ev_sales", "pb": "pb"}
        for _b in sorted(_sup_bases):
            if peer_median_note and f"({_label_of[_b]})" in peer_median_note["label"]:
                _leaks.append(f"{_label_of[_b]} is the peer-median basis")
            if any(r.get(_field_of[_b]) not in (None, "", "—")
                   for r in comparable_companies):
                _leaks.append(f"{_label_of[_b]} has a value in the peer table")
    check("no sector-suppressed metric reaches the peer table, a rank or the peer median"
          + (f" (leaked: {'; '.join(_leaks)})" if _leaks else ""),
          not _leaks)

    def _loose_date(s):
        import datetime as _d
        t = str(s or "").strip().replace(",", " ")
        t = " ".join(t.split())
        for fmt in ("%Y-%m-%d", "%b %d %Y", "%B %d %Y", "%d %b %Y", "%d %B %Y"):
            try:
                return _d.datetime.strptime(t, fmt).date()
            except ValueError:
                pass
        return None

    _ne = (meta.get("next_earnings") or {}).get("date")
    _ne_d, _prep_d = _loose_date(_ne), _loose_date(meta.get("prepared_date"))
    if _ne_d and _prep_d and _ne_d < _prep_d:
        _late = (_prep_d - _ne_d).days
        warnings.append(
            f"INGESTION LAG: the NEXT earnings date on this sheet ({_ne}) is {_late} day(s) "
            f"BEFORE the prepared date ({meta.get('prepared_date')}), so that call has already "
            f"happened. The company has most likely reported a period newer than "
            f"{meta.get('latest_fiscal_quarter')}, which is what this whole sheet is built on. "
            f"Every figure here can be internally consistent and still be the WRONG QUARTER. "
            f"Re-run search_tickerid and get_call_date before delivering; if the resolved "
            f"period really is the newest one, the calendar is stale and worth reporting.")
    elif _ne and not _ne_d:
        warnings.append(
            f"next_earnings.date {_ne!r} could not be parsed, so the ingestion-lag check did "
            f"not run. Write it as 'Oct 14, 2026' or '2026-10-14'.")

    all_est_rows = (raw.get("estimates_annual") or []) + (raw.get("estimates_quarter") or []) + (raw.get("estimates_next_quarter") or [])
    est_dates = [r.get("UPDATEDATE") for r in all_est_rows if r.get("UPDATEDATE")]
    broker_counts = [r.get("BROKERCOUNT") for r in all_est_rows if isinstance(r.get("BROKERCOUNT"), int)]
    min_brokers = min(broker_counts) if broker_counts else None
    if min_brokers is not None and min_brokers < 3:
        warnings.append(f"THIN CONSENSUS: minimum broker count across estimates is {min_brokers} — disclose in prose")
    val_dates = sorted(pts[-1][0] for pts in groups.values()) if groups else []
    qend = next((r.get("QUARTERENDDATE") for r in raw.get("actuals", []) if r.get("QUARTERENDDATE")), None)
    provenance = (f"Data as of: price {mkt.get('price_date', 'n/a')} | actuals {lq}"
                  + (f" (qtr end {qend})" if qend else "")
                  + " (company reports via InSync MCP)"
                  + (f" | consensus updated {max(est_dates)}" if est_dates
                     else " | consensus date not reported" if broker_counts
                     else " | quarter consensus unavailable")
                  + (f" ({min_brokers}-{max(broker_counts)} brokers)" if broker_counts else "")
                  + (f" | valuation as of {val_dates[-1]}" if val_dates else "")
                  + (f" | multiples basis: P/E {pe_basis or 'n/a'}, EV/EBITDA {ev_basis or 'n/a'}")
                  + " | Source: InSync MCP")

    _mt = meta.get("match_type")
    if _mt:
        _score, _amb = meta.get("score"), meta.get("ambiguous")
        provenance += (f" | identity: {_mt}"
                       + (f" (score {_score})" if _score is not None else "")
                       + (" AMBIGUOUS" if _amb else ""))
        _idmiss = [k for k, v in (("score", _score), ("ambiguous", _amb)) if v is None]
        if _idmiss:
            warnings.append(
                f"IDENTITY INCOMPLETE: meta.match_type was captured but {' and '.join(_idmiss)} "
                f"{'was' if len(_idmiss) == 1 else 'were'} not (SKILL.md 6.1). Capture all three "
                f"from search_tickerid so the footer and this check can say how sure the match is.")
        if _mt not in ("exact_ticker", "exact_company"):
            warnings.append(
                f"IDENTITY NOT EXACT: search_tickerid returned match_type '{_mt}'"
                + (f" at score {_score}" if _score is not None else "")
                + ". A similarity match can return a DIFFERENT issuer at status: success — "
                  "scores above 99 have done so. Confirm the company, ticker and period in "
                  "the masthead are the ones you asked for before delivering, and say in the "
                  "response how the company was resolved.")
        if _amb:
            warnings.append(
                "IDENTITY AMBIGUOUS: search_tickerid reported more than one candidate for "
                "this query. Re-resolve with an exact ticker rather than a name.")

    out = {
        "computed_by": f"company-tearsheet-skill v{SKILL_VERSION} / compute_tearsheet.py — all figures deterministic; LLM merges prose only",
        "key_financials": key_financials,
        "operational_kpis": operational_kpis,
        "kpi_universe": raw.get("kpi_universe", []),
        "market_snapshot": market_snapshot,
        "valuation_metrics": valuation_metrics,
        "comparable_companies": comparable_companies,
        "provenance": provenance,
        "master_context": ({"val_preference": master.get("val_multiples"),
                            "master_peers": master.get("peers"),
                            "classification": master.get("classification")} if master else None),
        "forward_estimates": fwd,
        "signals": signals,
        "min_fin_rows": profile.get("min_fin_rows", MIN_FIN_ROWS),
        "qc": {"passed": sum(1 for c in qc if c["pass"]),
               "failed": sum(1 for c in qc if not c["pass"]),
               "failed_checks": [c["check"] for c in qc if not c["pass"]]},
        "warnings": warnings,
    }
    json.dump(out, open(out_path, "w", encoding="utf-8"), indent=2)

    import os, datetime
    manifest = {
        "skill": "company-tearsheet-skill",
        "engine": f"company-tearsheet-skill v{SKILL_VERSION} / compute_tearsheet.py",
        "run_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "input": os.path.abspath(raw_path),
        "output": os.path.abspath(out_path),
        "ticker": meta.get("ticker"),
        "periods": {"latest": lq, "prior_year": pq, "fy1": fy1, "fy2": fy2},
        "codes_in_actuals": sorted(set(cur) | set(pri)),
        "kpis_requested": [k["ltcode"] for k in raw.get("kpis", [])],
        "rows": {"key_financials": len(key_financials), "operational_kpis": len(operational_kpis),
                 "valuation_metrics": len(valuation_metrics)},
        "omissions_and_warnings": warnings,
        "qc": {"checks": qc, "passed": sum(1 for c in qc if c["pass"]), "failed": sum(1 for c in qc if not c["pass"])},
    }
    mpath = os.path.join(os.path.dirname(os.path.abspath(out_path)) or ".", "run_manifest.json")
    json.dump(manifest, open(mpath, "w", encoding="utf-8"), indent=2)

    _qc_failed = [c["check"] for c in qc if not c["pass"]]
    _head = ("OK" if not _qc_failed
             else "QC FAILED — DO NOT DELIVER")
    print(f"{_head} — computed {len(key_financials)} financial rows, "
          f"{len(operational_kpis)} KPIs, {len(valuation_metrics)} valuation slots; "
          f"{len(warnings)} warning(s)")
    print(f"Manifest: {mpath}")
    for w in warnings:
        print(f"  WARN: {w}")

    if _undeclared_polarity:
        _seen, _uniq = set(), []
        for _c, _n in _undeclared_polarity:
            if _c not in _seen:
                _seen.add(_c)
                _uniq.append(f"{_c} ({_n})" if _n else _c)
        _graded = len(operational_kpis) + len(key_financials)
        print(f"  POLARITY NOT DECLARED — {len(_uniq)} of {_graded} graded row(s) were graded "
              f"higher-is-better by DEFAULT, not by declaration: {', '.join(_uniq)}")
        print("    Any cost or efficiency metric in that list is being graded BACKWARDS. "
              "Declare it under the sector's lower_is_better_codes in "
              "assets/sector_profiles.json — never by widening a name regex.")

    if _qc_failed:
        print()
        print("QC FAILURE — %d check(s) failed, so Step 1 did NOT succeed:" % len(_qc_failed))
        for c in _qc_failed:
            print("   FAILED: %s" % c)
        print("A failed QC check means a figure the sheet would print is wrong or cannot be "
              "trusted, and this script cannot know which input is the right one. Read the "
              "QC FAIL warnings above, fix raw_data.json, and re-run Step 1. Do NOT run "
              "Step 2 on this computed.json — merge_payload.py will refuse it — and do not "
              "deliver anything built from it.")
        return 1
    return 0


SELFTEST_SECTIONS = ["key_financials", "operational_kpis", "market_snapshot",
                     "valuation_metrics", "forward_estimates", "signals",
                     "comparable_companies", "warnings", "min_fin_rows", "provenance"]


def selftest(regen=False):
    import os, tempfile
    here = os.path.dirname(os.path.abspath(__file__))
    fixture = os.path.join(here, "tests", "fixture_raw.json")
    expected_path = os.path.join(here, "tests", "expected_computed.json")
    if not (os.path.exists(fixture) and os.path.exists(expected_path)):
        print("SELF-TEST SKIPPED: references/tests/fixture_raw.json or references/tests/expected_computed.json missing")
        sys.exit(2)

    def run_one(raw_path):
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "computed.json")
            main(raw_path, out)
            return json.load(open(out, encoding="utf-8")), json.load(open(os.path.join(td, "run_manifest.json"), encoding="utf-8"))

    PARSE_CASES = [
        ("1.5T", (1500000.0, "M")),
        ("2T", (2000000.0, "M")),
        ("3.4B", (3400.0, "M")),
        ("125.5M", (125.5, "M")),
        ("-4.2B", (-4200.0, "M")),
        ("$1.5T", (1500000.0, "M")),
        ("-$4.2B", (-4200.0, "M")),
        ("1,250.5M", (1250.5, "M")),
        ("12.4%", (12.4, "%")),
        ("-3.1%", (-3.1, "%")),
        ("500K", (0.5, "M")),
        ("500k", (0.5, "M")),
        ("$1,500,000.0K", (1500.0, "M")),
        ("42", (42.0, "raw")),
        ("4.72", (4.72, "raw")),
        ("not a number", (None, None)),
        ("", (None, None)),
    ]
    pfails = ["parse_value(%r) = %r, expected %r" % (s, parse_value(s), want)
              for s, want in PARSE_CASES if parse_value(s) != want]
    if pfails:
        print("SELF-TEST FAILED: the value parser is wrong:")
        for m in pfails:
            print("   " + m)
        sys.exit(1)

    PERIOD_CASES = [
        ("2Q26",    "2Q26"),
        ("1H26",    "1H26"),
        ("2H26",    "2H26"),
        ("2026",    "2026"),
        ("R-2Q25",  "2Q25"),
        ("R1-2025", "2025"),
        ("R2-3Q25", "3Q25"),
        ("R-1H26",  "1H26"),
        (" r-2q25 ", "2Q25"),
    ]
    nfails = ["norm_period(%r) = %r, expected %r" % (s, norm_period(s), want)
              for s, want in PERIOD_CASES if norm_period(s) != want]
    if nfails:
        print("SELF-TEST FAILED: the period normaliser is wrong "
              "(every Y/Y cell for a restated period depends on it):")
        for m in nfails:
            print("   " + m)
        sys.exit(1)

    _idt = []
    _base = json.load(open(fixture, encoding="utf-8"))
    for _inject, _want_in, _want_warn in [
        (None,                                              None,                  False),
        ({"match_type": "exact_ticker", "score": 100},       "identity: exact_ticker (score 100)", False),
        ({"match_type": "exact_company", "score": 100},      "identity: exact_company (score 100)", False),
        ({"match_type": "fuzzy", "score": 99.14},            "identity: fuzzy (score 99.14)",       True),
        ({"match_type": "fuzzy", "score": 98.5, "ambiguous": True},
                                                             "identity: fuzzy (score 98.5) AMBIGUOUS", True),
    ]:
        _raw = json.loads(json.dumps(_base))
        if _inject:
            _raw.setdefault("meta", {}).update(_inject)
        with tempfile.TemporaryDirectory() as _td:
            _rp = os.path.join(_td, "raw.json")
            json.dump(_raw, open(_rp, "w", encoding="utf-8", newline="\n"))
            _got = run_one(_rp)[0]
        _prov = _got.get("provenance", "")
        _warned = any(w.startswith("IDENTITY NOT EXACT") for w in _got.get("warnings", []))
        if _want_in is None:
            if "identity:" in _prov:
                _idt.append("no match_type supplied, yet provenance carries an identity clause "
                            "— the stamp is not conditional and every frozen file will churn")
            if _got.get("warnings") != run_one(fixture)[0].get("warnings"):
                _idt.append("the absent-match_type path changed `warnings`; it must not, "
                            "because warnings is a frozen section")
        else:
            if _want_in not in _prov:
                _idt.append("expected %r in the provenance line, got: %s" % (_want_in, _prov))
            if _warned != _want_warn:
                _idt.append("match_type %r: IDENTITY NOT EXACT warning was %s, expected %s"
                            % (_inject.get("match_type"), _warned, _want_warn))
    if _idt:
        print("SELF-TEST FAILED: the identity stamp is wrong (no fixture covers this path, "
              "so these assertions are its only coverage):")
        for m in _idt:
            print("   " + m)
        sys.exit(1)

    problems = []

    got, _mf = run_one(fixture)
    exp = json.load(open(expected_path, encoding="utf-8"))
    fails = [s for s in SELFTEST_SECTIONS if got.get(s) != exp.get(s)]
    if regen and fails:
        exp.update({s: got.get(s) for s in SELFTEST_SECTIONS})
        json.dump(exp, open(expected_path, "w", encoding="utf-8", newline="\n"), indent=2)
        print(f"REGEN: tests/expected_computed.json ({', '.join(fails)})")
    elif fails:
        problems.append("tests/fixture_raw.json: sections differ from frozen "
                        "expectations: %s" % ", ".join(fails))

    reg_dir = os.path.join(here, "tests", "regression")
    exp_dir = os.path.join(reg_dir, "expected")
    ran, regenerated = 0, []
    if os.path.isdir(reg_dir):
        if regen:
            os.makedirs(exp_dir, exist_ok=True)
        for fn in sorted(os.listdir(reg_dir)):
            if not fn.endswith(".json"):
                continue
            got2, mf = run_one(os.path.join(reg_dir, fn))
            snapshot = {s: got2.get(s) for s in SELFTEST_SECTIONS}
            ep = os.path.join(exp_dir, fn)
            if regen:
                before = json.load(open(ep, encoding="utf-8")) if os.path.exists(ep) else None
                if before != snapshot:
                    regenerated.append(fn)
                json.dump(snapshot, open(ep, "w", encoding="utf-8", newline="\n"), indent=2)
            elif not os.path.exists(ep):
                problems.append(f"{fn}: no frozen output at tests/regression/expected/{fn}. "
                                f"Run --regen, read the diff, commit it.")
            else:
                frozen = json.load(open(ep, encoding="utf-8"))
                missing = sorted(set(frozen) - set(SELFTEST_SECTIONS))
                extra = sorted(set(SELFTEST_SECTIONS) - set(frozen))
                if missing or extra:
                    problems.append(
                        "%s: freezes sections this run does not compare (%s) or omits sections "
                        "it should (%s). SELFTEST_SECTIONS was changed; run --regen and read "
                        "the diff." % (fn, ", ".join(missing) or "-", ", ".join(extra) or "-"))
                diff = [s for s in SELFTEST_SECTIONS
                        if snapshot.get(s) != frozen.get(s)]
                if diff:
                    problems.append(f"{fn}: differs from its frozen output in "
                                    f"{', '.join(diff)}. If the change is intended, run "
                                    f"--regen and read the diff.")
            if mf["qc"]["failed"] > 0:
                bad = [c["check"] for c in mf["qc"]["checks"] if not c["pass"]]
                problems.append(f"{fn}: QC failures: {', '.join(bad)}")
            ran += 1
        if not regen:
            _orphans = sorted(fn for fn in os.listdir(exp_dir)
                              if fn.endswith(".json")
                              and not os.path.exists(os.path.join(reg_dir, fn))) \
                       if os.path.isdir(exp_dir) else []
            if _orphans:
                problems.append(
                    "%d frozen file(s) under tests/regression/expected/ have no fixture: %s. "
                    "A fixture was deleted or renamed, so that path is no longer tested. "
                    "Restore the fixture, or delete the frozen file deliberately if the "
                    "coverage is genuinely being dropped."
                    % (len(_orphans), ", ".join(_orphans)))
    if regen:
        print(f"REGEN COMPLETE: {ran} fixtures frozen; "
              f"{len(regenerated)} changed{': ' + ', '.join(regenerated) if regenerated else ''}")
        print("Read the diff before committing. A frozen file regenerated without being read "
              "is not a test.")
        return

    if problems:
        print("SELF-TEST FAILED — %d problem(s) across %d regression fixture(s) + the LLY "
              "fixture:" % (len(problems), ran))
        for p_ in problems:
            print("   " + p_)
        sys.exit(1)
    print(f"SELF-TEST PASSED: frozen LLY fixture exact; {ran} regression fixtures match their "
          f"frozen output section-by-section, zero QC failures")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--selftest":
        selftest()
    elif len(sys.argv) == 2 and sys.argv[1] == "--regen":
        selftest(regen=True)
    elif len(sys.argv) == 3:
        sys.exit(main(sys.argv[1], sys.argv[2]) or 0)
    else:
        print(__doc__)
        sys.exit(1)
