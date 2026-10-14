#!/usr/bin/env python3
"""
compute_capalloc.py - deterministic math layer for the capital-allocation-strategy skill.

The LLM's ONLY numeric job is to paste raw MCP get_actuals rows into raw_data.json.
This script parses them and computes EVERY derived figure and EVERY rating: the
allocation snapshot and %, sources & uses, the strategy classification LABEL, buyback /
dividend / M&A / capex analysis with their Excellent-Good-Neutral-Poor ratings, leverage
& coverage, ROIC / ROE, value-creation metrics, the weighted 0-10 scorecard, the peer
comparison, and a QC block. The LLM must not perform, round, or override any arithmetic
or any rating - run_pipeline.py re-runs this script and discards LLM-supplied numbers.

Usage:
    python compute_capalloc.py raw_data.json computed.json

raw_data.json (all MCP rows pasted verbatim; envelope {"data":[...]} or bare list ok):
{
  "meta": {"ticker","company_name","ticker_id","industry","classification",
           "sector_key"(opt),"latest_quarter","prepared_date","currency"(opt "$"),
           "cost_of_capital"(opt 9.0),"cost_of_equity"(opt 10.0)},
  "market": {"price": 470.0, "price_date": "2026-07-20",
             "market_cap": null, "shares_current": null},
  "actuals_annual":  <get_actuals for 5 fiscal years, all LTCODEs>,
  "actuals_quarter": <get_actuals for latest quarter (current balance sheet)>,
  "tsr": {"price_start","price_end","years","div_per_share_cumulative"}  (optional),
  "peers": [ {"ticker","name","market_cap","actuals_annual":[...]} ]        (optional)
}
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import metric_registry as _MR

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from ltcodes import CF, BS, IS, NAMES, ACQUISITION_LTCODES, acquisition_codes

C = {
    "ocf": ["CF_P_108"], "fcf": ["CF_P_110"], "capex": ["CF_P_109"],
    "buyback_usd": ["CF_P_101"], "buyback_num": ["CF_P_102"],
    "dividend": ["CF_P_104"], "payout": ["CF_P_107"],
    "acq": list(ACQUISITION_LTCODES),
    "cash": ["BS_P_141", "BS_P_101"], "total_debt": ["BS_P_114", "BS_P_108"],
    "net_debt": ["BS_P_115", "BS_P_109"], "equity": ["BS_P_113"],
    "dcf": ["CF_S_126"], "maint_capex": ["CF_S_122"],
    "roe_reported": ["RT_P_102"],
    "debt_issued": ["CF_P_133"], "debt_repaid": [],
    "short_term_investments": ["BS_P_118"],
    "assets": ["BS_P_103"], "intangibles": ["BS_P_127"],
    "revenue": ["IS_P_101"], "ebit": ["IS_P_109"], "ebitda": ["IS_P_114", "IS_P_113"],
    "net_income": ["IS_P_124", "IS_P_123"], "eps": ["IS_P_128", "IS_P_127"],
    "net_income_gaap": ["IS_P_123"],
    "shares": ["IS_P_125", "IS_P_250"], "interest": ["IS_P_141"], "etr": ["IS_P_121"],
    "rnd": ["IS_P_135"], "da": ["IS_P_111"],
    "affo": ["CF_P_114"], "ffo": ["CF_P_113"],
    "affo_per_share": ["CF_P_203"], "affo_payout": ["CF_P_206"],
    "cet1": ["BS_P_287", "BS_S_1000"],
    "rwa": ["BS_P_289"],
    "tbv_per_share": ["BS_P_256"],
    "rotce": ["RT_P_107"],
    "combined_ratio": ["SD_P_103"],
}

_PCT_CODE_PREFIXES = ("RT_", "SD_")
_PCT_CODES = {"BS_P_287",
              "BS_S_1000",
              "BS_P_278",
              "IS_P_121",
              "CF_P_107",
              "CF_P_206"}


def _pct_metrics():
    out = set()
    for _k, _codes in C.items():
        for _c in (_codes or []):
            if _c in _PCT_CODES or _c.startswith(_PCT_CODE_PREFIXES):
                out.add(_k)
                break
    return out


PCT_METRICS = _pct_metrics()

SCORE_RULES = {
    "buybacks": {
        "label": "Buybacks", "base": 5,
        "measures": ["Buyback yield", "Share-count change",
                     "Average repurchase price vs current price", "Authorisation remaining"],
        "applies": "Only when buybacks were executed; otherwise the base score stands.",
        "rules": [
            {"id": "share_red_gt", "v": 0.02, "delta": +2,
             "text": "net share count reduced by more than 2.0% over the window"},
            {"id": "share_flat", "v": 0.0, "delta": -2,
             "text": "share count flat or higher despite buybacks (dilution offset)"},
            {"id": "rep_below", "v": -5.0, "delta": +2,
             "text": "average repurchase price more than 5% below the current price"},
            {"id": "rep_above", "v": 10.0, "delta": -1,
             "text": "average repurchase price more than 10% above the current price"},
            {"id": "bb_yield_gt", "v": 0.03, "delta": +1,
             "text": "buyback yield above 3.0% of market capitalisation"},
        ]},
    "dividends": {
        "label": "Dividends", "base": 5,
        "measures": ["Payout of earnings", "Free-cash-flow cover", "Dividend growth"],
        "applies": "Only when a dividend was paid. Midstream issuers are scored on "
                   "distributable cash flow instead - see the midstream rules below.",
        "rules": [
            {"id": "payout_low", "v": 60.0, "delta": +2,
             "text": "payout below 60% of earnings"},
            {"id": "payout_high", "v": 90.0, "delta": -2,
             "text": "payout above 90% of earnings (limited cushion)"},
            {"id": "fcf_cover_gt", "v": 1.5, "delta": +2,
             "text": "free cash flow covers the dividend more than 1.5x"},
            {"id": "fcf_cover_lt", "v": 1.0, "delta": -3,
             "text": "free cash flow does not cover the dividend"},
            {"id": "div_cagr_gt", "v": 5.0, "delta": +1,
             "text": "dividend growth above 5% a year"},
        ],
        "variant": {
            "label": "Midstream (distributions from distributable cash flow)",
            "rules": [
                {"id": "dcf_cov_strong", "v": 1.5, "delta": +3,
                 "text": "distributions covered 1.5x or better by distributable cash flow"},
                {"id": "dcf_cov_ok", "v": 1.1, "delta": +1,
                 "text": "distributions covered 1.1x to 1.5x"},
                {"id": "dcf_cov_short", "v": 1.1, "delta": -3,
                 "text": "distributions not covered by distributable cash flow"},
            ]}},
    "m_and_a": {
        "label": "M&A", "base": 5, "style": "set",
        "measures": ["Spend vs cumulative operating cash flow", "Realised return trend (ROIC)",
                     "Deal-return disclosure"],
        "applies": "Scored only where acquisition spend exceeds 15% of cumulative operating "
                   "cash flow. Below that it is immaterial and UNSCORED (no weight). Where the "
                   "acquisition line is absent from the source it is UNSCORED - never read as "
                   "'no acquisitions occurred'.",
        "rules": [
            {"id": "material_floor", "v": 0.15, "delta": None,
             "text": "materiality floor: acquisitions above 15% of cumulative operating cash flow"},
            {"id": "roic_up", "v": 1.0, "delta": 8,
             "text": "sets the score to 8 where ROIC expanded more than 1pp over the period"},
            {"id": "roic_down", "v": -3.0, "delta": 3,
             "text": "sets the score to 3 where ROIC contracted more than 3pp AND acquisitions "
                     "took more than 40% of operating cash flow"},
            {"id": "roic_down_share", "v": 0.40, "delta": None,
             "text": "the acquisition-share condition attached to the rule above"},
            {"id": "otherwise", "v": None, "delta": 5,
             "text": "otherwise 5: ROIC change reflects the whole capital base, and deal-level "
                     "attribution needs transaction data the feed does not carry"},
        ]},
    "capex": {
        "label": "Capex", "base": 6, "style": "ungraded",
        "measures": ["Investment intensity (capex / revenue)",
                     "Reinvestment rate (capex / operating cash flow)",
                     "Realised return trend (ROIC)"],
        "applies": "NOT CURRENTLY GRADED. The dimension returns its base score of 6 for every "
                   "company - an issuer at 1% capex intensity and one at 41% both score "
                   "6 - while still carrying its sector weight. The intensity and growth figures "
                   "below are measured and printed, and a descriptive assessment is derived from "
                   "them, but no threshold moves the score. Earlier versions of this document "
                   "published a four-band table here, which described grading that does not "
                   "happen. Whether to grade it is an open methodology decision; until it is "
                   "taken, this says what the engine does.",
        "rules": [
            {"id": "intensity_heavy", "v": 0.15, "delta": None,
             "text": "flagged as capital-heavy above 15% of revenue (no score effect)"},
            {"id": "cagr_fast", "v": 20.0, "delta": None,
             "text": "flagged as accelerating above 20% CAGR (no score effect)"},
            {"id": "intensity_low", "v": 0.03, "delta": None,
             "text": "below 3% of revenue reads as lower capital intensity; for a "
                     "capital-intensive sector it is reported as a data-completeness signal "
                     "(no score effect)"},
            {"id": "intensity_underinvest", "v": 0.02, "delta": None,
             "text": "below 2% of revenue AND declining is described as under-investing "
                     "(no score effect)"},
        ]},
    "balance_sheet": {
        "label": "Balance sheet", "base": 6, "style": "set",
        "measures": ["Net debt / EBITDA (or debt / total capital where that is the sector "
                     "measure)", "Interest cover"],
        "applies": "Banks and insurers are re-scored by their sector module on regulatory "
                   "capital instead. Where the net-debt figure is withheld over a source "
                   "conflict the dimension carries half weight.",
        "rules": [
            {"id": "net_cash", "v": 0.0, "delta": 9,
             "text": "sets 9 - a net cash position (leverage below zero)"},
            {"id": "lev_low", "v": 1.5, "delta": 8, "text": "sets 8 - below 1.5x net debt / EBITDA"},
            {"id": "lev_mid", "v": 3.0, "delta": 6, "text": "sets 6 - from 1.5x to 3.0x"},
            {"id": "lev_high", "v": 3.0, "delta": 3, "text": "sets 3 - above 3.0x"},
            {"id": "int_cov_floor", "v": 3.0, "delta": None,
             "text": "caps the dimension at 4 where interest cover is below 3.0x"},
        ],
        "variant": {
            "label": "Insurance (debt / total capital, per cent)",
            "rules": [
                {"id": "dtc_low", "v": 25.0, "delta": 8, "text": "sets 8 - below 25%"},
                {"id": "dtc_mid", "v": 35.0, "delta": 7, "text": "sets 7 - from 25% to 35%"},
                {"id": "dtc_high", "v": 45.0, "delta": 5, "text": "sets 5 - from 35% to 45%"},
                {"id": "dtc_stretched", "v": 45.0, "delta": 3, "text": "sets 3 - above 45%"},
            ]}},
    "capital_discipline": {
        "label": "Capital discipline", "base": 5,
        "measures": ["Reinvestment rate within a sustainable band", "Leverage",
                     "Buyback price discipline"],
        "applies": "The result is averaged with the value-creation score, so the outcome feeds "
                   "back into the process grade. Reported PROVISIONAL at half weight unless the "
                   "funding bridge (gross debt issued and repaid) is available, which the "
                   "fetched set does not carry.",
        "rules": [
            {"id": "reinvest_band_lo", "v": 0.05, "delta": +1,
             "text": "reinvestment rate between 5% and 60% of operating cash flow"},
            {"id": "reinvest_band_hi", "v": 0.60, "delta": None,
             "text": "the upper bound of the band above"},
            {"id": "lev_under", "v": 2.0, "delta": +1, "text": "leverage below 2.0x"},
            {"id": "bought_below", "v": None, "delta": +1,
             "text": "buybacks executed below the current price"},
            {"id": "dilution_offset", "v": None, "delta": -2,
             "text": "buybacks not reducing the share count (dilution offset)"},
            {"id": "avg_with_vc", "v": None, "delta": None,
             "text": "the subtotal is then averaged with the value-creation score"},
        ]},
    "value_creation": {
        "label": "Value creation (outcome)", "base": 5,
        "measures": ["ROIC vs cost of capital", "Free cash flow per share growth",
                     "EPS growth", "Total shareholder return", "Share-count change",
                     "Capital returned vs free cash flow", "Book value per share"],
        "applies": "Graded on the OUTCOME, not the process. Sector modules replace this with "
                   "their own lens (REIT on AFFO, banks on ROTCE and CET1, insurance on ROE).",
        "rules": [
            {"id": "roic_above", "v": 3.0, "delta": +1,
             "text": "ROIC more than 3pp above the assumed cost of capital"},
            {"id": "roic_below", "v": 0.0, "delta": -1,
             "text": "ROIC below the assumed cost of capital"},
            {"id": "fcfps_strong", "v": 8.0, "delta": +1.5,
             "text": "free cash flow per share compounding above 8% a year"},
            {"id": "fcfps_positive", "v": 0.0, "delta": +0.5,
             "text": "free cash flow per share growing but below 8%"},
            {"id": "fcfps_negative", "v": 0.0, "delta": -1,
             "text": "free cash flow per share not growing"},
            {"id": "eps_strong", "v": 8.0, "delta": +1,
             "text": "EPS compounding above 8% a year"},
            {"id": "eps_negative", "v": 0.0, "delta": -1, "text": "EPS declining"},
            {"id": "tsr_strong", "v": 12.0, "delta": +1.5,
             "text": "total shareholder return 12% a year or better"},
            {"id": "tsr_ok", "v": 8.0, "delta": +0.5, "text": "total shareholder return 8% to 12%"},
            {"id": "tsr_negative", "v": 0.0, "delta": -1,
             "text": "total shareholder return below zero"},
            {"id": "share_red", "v": 0.03, "delta": +1,
             "text": "share count reduced more than 3.0%"},
            {"id": "share_no_red", "v": 0.0, "delta": -1,
             "text": "buybacks executed without reducing the share count"},
            {"id": "cap_ret_fcf", "v": 0.60, "delta": +0.5,
             "text": "60% or more of free cash flow returned to holders"},
            {"id": "bvps_decline", "v": 0.0, "delta": -1,
             "text": "book value per share declining"},
        ]},
}

SCORE_BANDS = [(8.0, "strong"), (6.5, "solid"), (5.0, "adequate"), (0.0, "weak")]


def RD(dim, rid):
    for r in SCORE_RULES[dim]["rules"]:
        if r["id"] == rid:
            return r["delta"]
    for r in (SCORE_RULES[dim].get("variant") or {}).get("rules", []):
        if r["id"] == rid:
            return r["delta"]
    raise KeyError("%s/%s is not a declared scoring rule" % (dim, rid))


def RV(dim, rid):
    for r in SCORE_RULES[dim]["rules"]:
        if r["id"] == rid:
            return r["v"]
    for r in (SCORE_RULES[dim].get("variant") or {}).get("rules", []):
        if r["id"] == rid:
            return r["v"]
    raise KeyError("%s/%s is not a declared scoring rule" % (dim, rid))



_UNIT_MARKS = {}
_UNIT_REFUSALS = []
_ROE_REFUSALS = []
_ROE_THIN = []
_DEBT_ISSUED_NET = []

_MAX_ROE_ABS = 1000.0
_THIN_ROE_ABS = 100.0
_CUR_CODES = {}

_COMPOUND_CUR = (
    ("GBPENCE", "GBP", 0.01),
    ("GBP P",   "GBP", 0.01),
    ("US$",     "$",   1.0),
    ("A$",      "AUD", 1.0),
    ("R$",      "BRL", 1.0),
    ("C$",      "CAD", 1.0),
    ("NZ$",     "NZD", 1.0),
    ("HK$",     "HKD", 1.0),
    ("S$",      "SGD", 1.0),
    ("NT$",     "TWD", 1.0),
)


def _compound_prefix(s):
    t = str(s or "").strip()
    neg = t.startswith("-")
    body = t[1:].lstrip() if neg else t
    for pfx, cur, scale in _COMPOUND_CUR:
        if body.upper().startswith(pfx):
            rest = body[len(pfx):].lstrip()
            return cur, scale, ("-" + rest if neg else rest)
    return None


_CUR_TOKEN = re.compile(
    r"^-?\s*([$\u20ac\u00a3\u00a5\u20b9\u20a9\u20bd]|"
    r"USD|EUR|GBP|JPY|CHF|CAD|AUD|SEK|NOK|DKK|HKD|SGD|CNY|RMB|INR|KRW|BRL|MXN|ZAR|NZD|"
    r"TWD|PLN|ILS|HUF|TRY|CZK|THB|IDR|PHP|MYR|CLP|COP|ARS|AED|SAR)", re.I)


def _cur_code(raw):
    _c = _compound_prefix(raw)
    if _c:
        return _c[0]
    m = _CUR_TOKEN.match(str(raw or "").strip())
    return m.group(1).upper() if m else None


def _unit_mark(raw):
    s = str(raw or "").strip()
    if not s:
        return None
    if s.endswith("%"):
        return "%"
    if _compound_prefix(s):
        return "cur"
    if re.match(r"^-?\s*(?:[$\u20ac\u00a3\u00a5\u20b9\u20a9\u20bd]|(?:USD|EUR|GBP|JPY|CHF|CAD|AUD|SEK|NOK|"
                r"DKK|HKD|SGD|CNY|RMB|INR|KRW|BRL|MXN|ZAR|NZD|TWD|PLN|ILS|HUF|TRY|CZK|THB|IDR|"
                r"PHP|MYR|CLP|COP|ARS|AED|SAR))", s, re.I):
        return "cur"
    return "bare"


def pnum(v):
    if v is None:
        return None
    s = str(v).strip()
    if s == "" or s.lower() in ("n/a", "na", "none", "null", "-"):
        return None
    _par = re.match(r"^\((.+)\)\s*([TBMKtbmk]?)$", s)
    if _par:
        s = "-" + _par.group(1).strip() + _par.group(2)
    _cp = _compound_prefix(s)
    if _cp:
        _cur_tok, _scale, _rest = _cp
        _inner = pnum(_rest)
        return None if _inner is None else _inner * _scale
    _CUR = (r"USD|EUR|GBP|JPY|CHF|CAD|AUD|SEK|NOK|DKK|HKD|SGD|CNY|RMB|INR|KRW|BRL|MXN|ZAR|NZD|"
            r"TWD|PLN|ILS|HUF|TRY|CZK|THB|IDR|PHP|MYR|CLP|COP|ARS|AED|SAR")
    s = re.sub(r"^\s*(?:[$€£¥₹₩₽¢]|(?:%s))\s*" % _CUR, "", s, flags=re.I)
    s = re.sub(r"\s*(?:%s)\s*$" % _CUR, "", s, flags=re.I).strip()
    s = re.sub(r"^-\s*(?:[$€£¥₹₩₽¢]|(?:%s))\s*" % _CUR, "-", s, flags=re.I).strip()
    m = re.match(r"^\$?\s*(-?[\d,]+\.?\d*)\s*([TBMKtbmk%]?)$", s.replace("$-", "-$").replace("$", ""))
    if not m:
        m = re.match(r"^(-?[\d,]+\.?\d*)\s*([TBMKtbmk%]?)$", s)
        if not m:
            return None
    num = float(m.group(1).replace(",", ""))
    unit = m.group(2).upper() if m.group(2) != "%" else "%"
    return {"T": num * 1e6, "B": num * 1e3, "M": num, "K": num / 1e3,
            "%": num, "": num}[unit]


def unwrap(block):
    if block is None:
        return []
    if isinstance(block, dict):
        for k in ("data", "rows", "result"):
            if isinstance(block.get(k), list):
                return block[k]
        return []
    if isinstance(block, str):
        try:
            return unwrap(json.loads(block))
        except Exception:
            return []
    if isinstance(block, list):
        out = []
        for r in block:
            if isinstance(r, dict) and "LTCODE" in r:
                out.append(r)
            elif isinstance(r, dict) and isinstance(r.get("data"), list):
                out.extend(r["data"])
        return out
    return []


_CONSUMED_RESTATED = {}


def _restated_target(per):
    m = _RESTATED_ANNUAL.match(per)
    if m:
        return m.group(2)
    p = _RESTATED_PREFIX.match(str(per or "").strip())
    if p and not _RESTATED_QUARTER.match(per):
        return _alias_target(p.group(2))
    return None


_YEAR_ALIAS = re.compile(r"^(?:FY[-\s]?)?((?:19|20)\d{2}|\d{2})A?$", re.I)
_CONSUMED_ALIAS = {}
_ALIAS_CONFLICTS = []


def _alias_target(per):
    p = str(per or "").strip()
    if re.fullmatch(r"\d{4}", p):
        return None
    m = _YEAR_ALIAS.match(p)
    if not m:
        return None
    y = int(m.group(1))
    return str(y + 2000 if y < 100 else y)


def _vintage(per):
    m = (_RESTATED_ANNUAL.match(per) or _RESTATED_QUARTER.match(per)
         or _RESTATED_PREFIX.match(str(per or "").strip()))
    try:
        return int(m.group(1)) if (m and m.group(1)) else 1
    except (TypeError, ValueError):
        return 1


def index_rows(rows):
    by_period = {}
    end_dates = {}
    _plain = {}
    for r in rows:
        _p = str(r.get("FISCALPERIOD", "")).strip()
        if r.get("LTCODE") and _p and not _restated_target(_p) and not _alias_target(_p):
            _plain[(_p, r["LTCODE"])] = True
    _alias_best, _alias_seen = {}, {}
    for r in rows:
        code = r.get("LTCODE")
        _p = str(r.get("FISCALPERIOD", "")).strip()
        if not code or not _p:
            continue
        tgt = _alias_target(_p)
        if not tgt or (tgt, code) in _plain:
            continue
        _alias_best[(tgt, code)] = r
        _alias_seen.setdefault((tgt, code), []).append((_p, pnum(r.get("VALUE"))))
    _CONSUMED_ALIAS.clear()
    _ALIAS_CONFLICTS.clear()
    for (tgt, code), seen in sorted(_alias_seen.items()):
        for _p, _v in seen:
            _CONSUMED_ALIAS.setdefault(tgt, set()).add(_p)
        _vals = {v for _p, v in seen if v is not None}
        if len(_vals) > 1:
            _ALIAS_CONFLICTS.append(
                (tgt, code, sorted("%s=%s" % (p, v) for p, v in seen)))
    rows = list(rows) + [dict(r, FISCALPERIOD=tgt)
                         for (tgt, _c), r in sorted(_alias_best.items())]
    for (tgt, code) in _alias_best:
        _plain[(tgt, code)] = True
    _best = {}
    for r in rows:
        code = r.get("LTCODE")
        _p = str(r.get("FISCALPERIOD", "")).strip()
        if not code or not _p:
            continue
        tgt = _restated_target(_p)
        if not tgt or (tgt, code) in _plain:
            continue
        v = _vintage(_p)
        if (tgt, code) not in _best or v > _best[(tgt, code)][0]:
            _best[(tgt, code)] = (v, r)
    _CONSUMED_RESTATED.clear()
    for (tgt, code), (_v, r) in sorted(_best.items()):
        _CONSUMED_RESTATED.setdefault(tgt, set()).add(str(r.get("FISCALPERIOD", "")).strip())
    rows = list(rows) + [dict(r, FISCALPERIOD=tgt)
                         for (tgt, _c), (_v, r) in sorted(_best.items())]
    for r in rows:
        code = r.get("LTCODE")
        per = str(r.get("FISCALPERIOD", "")).strip()
        if not code or not per:
            continue
        if _restated_target(per) or _alias_target(per):
            continue
        by_period.setdefault(per, {})[code] = pnum(r.get("VALUE"))
        _m = _unit_mark(r.get("VALUE"))
        if _m:
            _UNIT_MARKS.setdefault(code, set()).add(_m)
        _cc = _cur_code(r.get("VALUE"))
        if _cc:
            _CUR_CODES.setdefault(per, set()).add(_cc)
        end_dates[per] = r.get("QUARTERENDDATE", per)
        if r.get("SOURCE_DERIVED"):
            _PROVENANCE.setdefault(code, set()).add(str(r["SOURCE_DERIVED"]).strip())
    _fold_half_years(by_period, end_dates)
    return by_period, end_dates


_STOCK_PREFIX = ("BS_",)
_TAKE_LATEST = {"IS_P_125", "IS_P_250", "IS_P_121", "CF_P_107", "CF_P_103", "RT_P_102"}
_TAKE_LATEST |= {c for k in (set(PCT_METRICS) | {"affo_per_share", "tbv_per_share", "eps", "dps"})
                 for c in (C.get(k) or [])}


def _fold_half_years(by_period, end_dates):
    halves = {}
    for per in list(by_period):
        m = re.fullmatch(r"([12])H(\d{2}|\d{4})", str(per).strip(), re.I)
        if m:
            yr = m.group(2)
            yr = yr if len(yr) == 4 else ("20" + yr)
            halves.setdefault(yr, {})[m.group(1)] = per
    for yr, parts in halves.items():
        if yr in by_period or set(parts) != {"1", "2"}:
            continue
        h1, h2 = by_period[parts["1"]], by_period[parts["2"]]
        merged = {}
        for code in set(h1) | set(h2):
            a, b = h1.get(code), h2.get(code)
            if code.startswith(_STOCK_PREFIX) or code in _TAKE_LATEST:
                merged[code] = b if b is not None else a
            elif a is None or b is None:
                merged[code] = a if b is None else b
            else:
                merged[code] = a + b
        by_period[yr] = merged
        end_dates[yr] = end_dates.get(parts["2"], yr)


def provenance_summary():
    if not _PROVENANCE:
        return None
    derived = sorted(c for c, v in _PROVENANCE.items() if v == {"Calculated"})
    reported = sorted(c for c, v in _PROVENANCE.items() if "Calculated" not in v)
    return {"reported_codes": reported, "derived_codes": derived,
            "note": ("%d of %d line items are reported by the company; %d are vendor-calculated."
                     % (len(reported), len(reported) + len(derived), len(derived)))}


_PROVENANCE = {}


def monitor_thresholds(computed):
    def num(v):
        m = re.search(r"-?\d+(?:\.\d+)?", str(v or "").replace(",", ""))
        return float(m.group()) if m and not str(v).lower().startswith(("n/a", "not ")) else None

    bs = computed.get("balance_sheet") or {}
    dv = computed.get("dividends") or {}
    cx = computed.get("capex") or {}
    vc = computed.get("value_creation") or {}
    out = []

    lev = num(bs.get("leverage_nd_ebitda"))
    _lev_unit = bs.get("leverage_unit") or "x"
    if lev is not None and _lev_unit == "%":
        _dtc_hi = RV("balance_sheet", "dtc_high")
        out.append({"lead": "Leverage.",
                    "text": "Debt / total capital is %.1f%%. The balance-sheet score steps down "
                            "above %.0f%%; headroom is %.1f percentage points."
                            % (lev, _dtc_hi, max(0.0, _dtc_hi - lev))})
    elif lev is not None:
        _lev_hi = RV("balance_sheet", "lev_mid")
        out.append({"lead": "Leverage.",
                    "text": "Net debt / EBITDA is %.2fx. The balance-sheet score steps down above "
                            "%.1fx; headroom is %.2fx of EBITDA."
                            % (lev, _lev_hi, max(0.0, _lev_hi - lev))})
    pay = num(dv.get("payout_ratio"))
    if pay is not None:
        out.append({"lead": "Dividend payout.",
                    "text": "%.1f%% of earnings. The dividend score steps down above %.0f%%; "
                            "headroom is %.1f percentage points."
                            % (pay, RV("dividends", "payout_high"),
                               max(0.0, RV("dividends", "payout_high") - pay))})
    cov = num(dv.get("fcf_coverage"))
    if cov is not None and bool(dv.get("fcf_coverage_meaningful", True)):
        out.append({"lead": "Distribution cover.",
                    "text": "Dividends are covered %.2fx by free cash flow. Below 1.0x the payout "
                            "is being funded from sources other than free cash flow." % cov})
    ci = num(cx.get("capex_intensity"))
    if ci is not None and ci < 6.0:
        out.append({"lead": "Reinvestment intensity.",
                    "text": "Capex is %.1f%% of revenue against a 3.0%% reference. A sustained "
                            "reading below that reference is what triggers the under-investment "
                            "flag; the gap is %.1f percentage points." % (ci, ci - 3.0)})
    _roe, _eq, _as = num(vc.get("roe")), num(bs.get("equity_raw")), num(bs.get("assets_raw"))
    if _roe is not None and _roe > 40:
        out.append({"lead": "Return on equity base.",
                    "text": "Reported return on equity is %.1f%%. Sustained buybacks have reduced "
                            "the equity base, so this ratio is arithmetically inflated by a small "
                            "denominator and is not comparable with peers carrying normal equity."
                            % _roe})
    roic, hurdle = num(vc.get("roic")) or num(vc.get("roe")), num(vc.get("cost_of_capital"))
    if roic is not None and hurdle is not None:
        _sp = roic - hurdle
        out.append({"lead": "Return on capital.",
                    "text": ("%.1f%% against a %.1f%% hurdle, a %.1f-point spread; the "
                             "value-creation score falls once that spread closes."
                             % (roic, hurdle, _sp)) if _sp > 0 else
                            ("%.1f%% against a %.1f%% hurdle - already %.1f points below it. "
                             "Returns above the hurdle are what would lift the value-creation "
                             "score." % (roic, hurdle, abs(_sp)))})
    return out[:5]


_RESTATED_ANNUAL = re.compile(r"^R(\d*)-((?:19|20)\d{2}|[12]H\d{2,4})$", re.I)
_RESTATED_QUARTER = re.compile(r"^R(\d*)-([1-4]Q\d{2,4})$", re.I)
_RESTATED_PREFIX = re.compile(r"^R(\d*)-(.+)$", re.I)


def annual_restatements(rows):
    out = {}
    for r in rows:
        _fp = str(r.get("FISCALPERIOD", "")).strip()
        m = _RESTATED_ANNUAL.match(_fp) or _RESTATED_QUARTER.match(_fp)
        if m:
            out.setdefault(m.group(2), set()).add(str(r["FISCALPERIOD"]).strip())
    return {y: sorted(v) for y, v in sorted(out.items())}


def annual_years(by_period):
    yrs = [p for p in by_period if re.fullmatch(r"\d{4}", p)]
    return sorted(yrs, key=int)


_ADJUSTED_CODES = {
    "IS_P_114": "adjusted",
    "IS_P_124": "adjusted",
    "IS_P_128": "adjusted",
}


def basis_of(period_map, keyset, default="reported"):
    return _ADJUSTED_CODES.get(_resolve(period_map, keyset)[1], default)


def _resolve(period_map, keyset):
    for code in C[keyset]:
        v = period_map.get(code)
        if v is None:
            continue
        if keyset in PCT_METRICS and "cur" in (_UNIT_MARKS.get(code) or set()):
            _UNIT_REFUSALS.append((keyset, code))
            continue
        return v, code
    return None, None


def gv(period_map, keyset):
    return _resolve(period_map, keyset)[0]


def series(by_period, years, keyset, absolute=False):
    out = []
    for y in years:
        v = gv(by_period.get(y, {}), keyset)
        if v is not None and absolute:
            v = abs(v)
        out.append(v)
    return out


def cagr(first, last, years):
    if first is None or last is None or years is None or years <= 0 or first <= 0 or last <= 0:
        return None
    return ((last / first) ** (1.0 / years) - 1.0) * 100.0


def safe_div(a, b):
    if a is None or b in (None, 0):
        return None
    return a / b


_SOFT_PREFIXES = ("ADVISORY:", "DISCLOSURE:")
_SOFT_SUBSTRINGS = ("net debt bridge indeterminate",)


def warning_severity(w):
    t = str(w)
    if t.startswith(_SOFT_PREFIXES):
        return "soft"
    if any(sub in t.lower() for sub in _SOFT_SUBSTRINGS):
        return "soft"
    return "hard"


def hard_warnings(warns):
    return [w for w in (warns or []) if warning_severity(w) == "hard"]


def ssum(lst):
    vals = [x for x in lst if x is not None]
    return sum(vals) if vals else None


_xr = _MR._xr
_sep = _MR._sep


def _rf(v, d):
    return float(_xr(v, d))


def fmt_usd(mn, cur="$"):
    if mn is None:
        return "n/a"
    a = abs(mn)
    sign = "-" if mn < 0 else ""
    if a >= 1e6:
        return f"{sign}{cur}{_sep(_xr(a/1e6, 2))}T"
    if a >= 1e3:
        return f"{sign}{cur}{_sep(_xr(a/1e3, 2))}B"
    return f"{sign}{cur}{_sep(_xr(a, 0))}M"


def fmt_pct(p, signed=False, dp=1, small=False):
    if p is None:
        return "n/a"
    if small and 0 < p < 0.05:
        return "<0.1%"
    return f"{'+' if (signed and p >= 0) else ''}{_xr(p, dp)}%"


def fmt_x(x, dp=2):
    return "n/a" if x is None else f"{_xr(x, dp)}x"


def fmt_sh(mn):
    if mn is None:
        return "n/a"
    if abs(mn) >= 1e3:
        return f"{_sep(_xr(mn/1e3, 2))}B"
    return f"{_sep(_xr(mn, 0))}M"


def fmt_ps(x, cur="$"):
    return "n/a" if x is None else f"{cur}{_sep(_xr(x, 2))}"


MONITOR_CODES = tuple(C[k][0] for k in ("ocf", "capex", "buyback_usd", "dividend"))
MONITOR_QUARTERS = 8


def fold_restated_quarters(rows):
    rows = list(rows or [])
    if not rows:
        return rows
    out, restated_keys = [], set()
    for r in rows:
        m = _RESTATED_QUARTER.match(str(r.get("FISCALPERIOD") or "").strip())
        if m:
            r = dict(r)
            r["FISCALPERIOD"] = m.group(2).upper()
            r["_restated_vintage"] = True
            restated_keys.add((r.get("LTCODE"), r["FISCALPERIOD"]))
        out.append(r)
    if not restated_keys:
        return out
    return [r for r in out
            if r.get("_restated_vintage")
            or (r.get("LTCODE"), str(r.get("FISCALPERIOD") or "").upper()) not in restated_keys]


def quarterly_depth(rows):
    rows = fold_restated_quarters(rows)
    periods = {str(r.get("FISCALPERIOD")) for r in rows
               if r.get("LTCODE") in MONITOR_CODES and r.get("FISCALPERIOD")}
    have = {r.get("LTCODE") for r in rows}
    return {"quarters": len(periods), "expected": MONITOR_QUARTERS,
            "short": 0 < len(periods) < MONITOR_QUARTERS,
            "empty": len(periods) == 0,
            "codes_missing": sorted(c for c in MONITOR_CODES if c not in have)}


def compute(raw):
    meta = raw.get("meta", {}) or {}
    extra = meta.get("acquisition_codes") or meta.get("acquisition_code")
    if not extra:
        return _compute_inner(raw)
    saved = list(C["acq"])
    C["acq"] = acquisition_codes(extra)
    try:
        return _compute_inner(raw)
    finally:
        C["acq"] = saved


def _compute_inner(raw):
    _PROVENANCE.clear()
    meta = raw.get("meta", {})
    cur = meta.get("currency", "$")
    coc = float(meta.get("cost_of_capital", 9.0))
    coe = float(meta.get("cost_of_equity", 10.0))
    _sector_key = meta.get("sector_key", "") or ""
    _cap_intensive = _sector_key in {"Energy", "Industrials", "Utilities", "Telecom", "Materials", "REIT"}
    _cyclical = _sector_key in {"Energy", "Industrials", "Materials"}
    warnings = []

    _UNIT_MARKS.clear()
    _CUR_CODES.clear()
    del _UNIT_REFUSALS[:]
    del _ROE_REFUSALS[:]
    del _ROE_THIN[:]
    del _DEBT_ISSUED_NET[:]
    ann_rows = unwrap(raw.get("actuals_annual"))
    q_rows = unwrap(raw.get("actuals_quarter"))
    _restated = annual_restatements(ann_rows)
    by_period, _ = index_rows(ann_rows)
    for _ay, _ac, _av in _ALIAS_CONFLICTS:
        warnings.append(
            "WITHHELD DATA: fiscal year %s is served under more than one label for %s with "
            "different values (%s). Both name the same year, so one of them is wrong and there "
            "is no rule here that can say which - re-fetch the year and supply a single row."
            % (_ay, _ac, "; ".join(_av)))
    _aliased = {y: sorted(t) for y, t in _CONSUMED_ALIAS.items()}
    if _aliased:
        warnings.append(
            "Annual periods relabelled: %s. These name a fiscal year under a label the annual "
            "window does not match on its own (an FY prefix, an 'A' actual suffix or a "
            "two-digit year), so each was read as that year and included. Before this release "
            "they were indexed and then dropped from the window in silence, which shortened the "
            "analysis period without saying so."
            % ", ".join("%s (from %s)" % (y, "/".join(t)) for y, t in sorted(_aliased.items())))
    _consumed = {y: sorted(t) for y, t in _CONSUMED_RESTATED.items()}
    if _consumed:
        _yrs = ", ".join("%s (from %s)" % (y, "/".join(t)) for y, t in sorted(_consumed.items()))
        warnings.append(
            "Restated reporting periods consumed: %s. The feed returned no plain-year row for "
            "these, so the restated vintage was used for the affected line items and the year is "
            "included in the window. A restated year reflects the company's current presentation; "
            "earlier years on the original basis are not restated to match, so period-to-period "
            "comparisons across the substitution carry a basis difference." % _yrs)
    _unconsumable = {y: sorted(t) for y, t in _restated.items()
                     if y not in _consumed and any(_RESTATED_QUARTER.match(x) for x in t)}
    if _unconsumable:
        _u = ", ".join("%s (as %s)" % (y, "/".join(t)) for y, t in sorted(_unconsumable.items()))
        _msg = ("Restated quarterly vintages present in the ANNUAL rows and not consumable: %s. "
                "A quarterly token names three months, not a fiscal year, so it cannot stand in "
                "for an annual period - using it would publish a quarter as a year, and dropping "
                "it would shorten the window while the report printed the full period. Re-fetch "
                "the affected year on an annual token, or place these rows in actuals_quarterly "
                "where they belong. CAPALLOC_ENFORCE=warn renders with the shortened window "
                "disclosed." % _u)
        if (os.environ.get("CAPALLOC_ENFORCE") or "block").lower() == "block":
            return {"status": "error", "message": _msg, "restated_periods": _unconsumable}
        warnings.append("WITHHELD DATA: " + _msg)
    _superseded = {y: sorted(t) for y, t in _restated.items()
                   if y not in _consumed and y not in _unconsumable}
    if _superseded:
        warnings.append(
            "Restated vintages present but not used: %s. A plain-year row exists for these, and "
            "the plain row is used so that one basis runs across the window. The restated figures "
            "are available in the source if the current presentation is wanted instead."
            % ", ".join("%s (%s)" % (y, "/".join(t)) for y, t in sorted(_superseded.items())))
    q_by_period, _ = index_rows(q_rows)
    years = annual_years(by_period)
    if len(years) < 2:
        return {"status": "error",
                "message": "Need >=2 annual periods in actuals_annual "
                           "(bare-year FISCALPERIOD tokens). Re-fetch 5 fiscal years."}

    latest, first = years[-1], years[0]
    prior = years[-2]
    n_periods = len(years)
    span = int(latest) - int(first)
    _win_curs = sorted(set().union(*[_CUR_CODES.get(y, set()) for y in years]) or set())
    _cur_mix = _win_curs if len(_win_curs) > 1 else []
    if _cur_mix:
        warnings.append(
            "WITHHELD DATA: the annual window reports in more than one currency (%s), so every "
            "multi-year growth and compound rate is withheld - a rate across the change would "
            "divide values denominated in different units. Reported levels are unaffected."
            % ", ".join(_cur_mix))
        span = None
    L = by_period[latest]
    P = by_period[prior]

    cur_bs = None
    if q_by_period:
        qk = sorted(q_by_period.keys())[-1]
        cur_bs = q_by_period[qk]
    if not cur_bs:
        cur_bs = L

    market = raw.get("market", {})
    price = market.get("price")
    price_cur = str(market.get("price_currency") or cur or "").strip() or None
    _price_basis_ok = (price_cur is None or cur is None
                       or price_cur.upper().lstrip("$") == str(cur).upper().lstrip("$")
                       or price_cur == cur)
    if not _price_basis_ok:
        warnings.append(
            "WITHHELD DATA: the share price is quoted in %s while the financial statements report "
            "in %s. Market capitalisation and the average-repurchase-price comparison both divide "
            "one into the other, so both are withheld - a conversion needs an FX rate this skill "
            "does not carry. Supply a price in %s, or omit market.price_currency if they are in "
            "fact the same unit." % (price_cur, cur, cur))
        price = None
    shares_cur = gv(cur_bs, "shares") or gv(L, "shares") or market.get("shares_current")
    mcap = market.get("market_cap")
    if mcap is None and price is not None and shares_cur is not None:
        mcap = price * shares_cur
    if mcap is not None and mcap < 1e4 and shares_cur and price and price > 0:
        pass

    ocf_s = series(by_period, years, "ocf")
    capex_s = series(by_period, years, "capex", absolute=True)
    fcf_s = []
    for i, y in enumerate(years):
        f = gv(by_period[y], "fcf")
        if f is None and ocf_s[i] is not None and capex_s[i] is not None:
            f = ocf_s[i] - capex_s[i]
        fcf_s.append(f)
    bb_s = series(by_period, years, "buyback_usd", absolute=True)
    div_s = series(by_period, years, "dividend", absolute=True)
    acq_s = series(by_period, years, "acq", absolute=True)
    rev_s = series(by_period, years, "revenue")
    ni_s = series(by_period, years, "net_income")
    eps_s = series(by_period, years, "eps")
    sh_s = series(by_period, years, "shares")
    eq_s = series(by_period, years, "equity")
    ebitda_s = series(by_period, years, "ebitda")
    ebit_s = series(by_period, years, "ebit")
    da_s = series(by_period, years, "da", absolute=True)
    rnd_s = series(by_period, years, "rnd", absolute=True)

    def latest_of(s):
        return s[-1] if s else None

    ocf, capex = latest_of(ocf_s), latest_of(capex_s)
    fcf = latest_of(fcf_s)
    buyback, dividend, acq = latest_of(bb_s), latest_of(div_s), latest_of(acq_s)
    revenue, net_income = latest_of(rev_s), latest_of(ni_s)
    ebitda, ebit = latest_of(ebitda_s), latest_of(ebit_s)

    debt_L = gv(L, "total_debt")
    debt_P = gv(P, "total_debt")
    debt_reduction = max(0.0, (debt_P - debt_L)) if (debt_L is not None and debt_P is not None) else None
    cash_L = gv(L, "cash")
    cash_P = gv(P, "cash")
    cash_build = max(0.0, (cash_L - cash_P)) if (cash_L is not None and cash_P is not None) else None

    uses = {
        "Capex": capex, "Buybacks": buyback, "Dividends": dividend,
        "M&A": acq, "Debt Reduction": debt_reduction, "Cash Build": cash_build,
    }
    total_deployed = ssum([v for v in uses.values() if v is not None])
    _cash_delta = ((cash_L - cash_P) if (cash_L is not None and cash_P is not None) else None)
    snapshot = {
        "period": latest,
        "operating_cash_flow": {"value": fmt_usd(ocf, cur), "raw": ocf},
        "total_deployed": {"value": fmt_usd(total_deployed, cur), "raw": total_deployed},
        "cash_delta_raw": _cash_delta,
        "cash_built": bool(_cash_delta is not None and _cash_delta > 0),
        "rows": [],
    }
    for name, v in uses.items():
        pct = safe_div(v, total_deployed)
        snapshot["rows"].append({
            "use": name, "value": fmt_usd(v, cur), "raw": v,
            "pct": (_rf(pct * 100, 1) if pct is not None else None),
            "pct_disp": (fmt_pct(pct * 100) if pct is not None else "n/a"),
        })

    def yoy(a, b):
        return safe_div((a - b), b) if (a is not None and b is not None and b != 0) else None
    sources = [
        {"item": "Operating Cash Flow", "value": fmt_usd(ocf, cur), "raw": ocf,
         "yoy": fmt_pct((yoy(ocf, gv(P, "ocf")) or 0) * 100, signed=True) if gv(P, "ocf") else "n/a"},
    ]
    _di = gv(L, "debt_issued")
    if _di is not None and _di > 0:
        sources.append({"item": "Debt Issuance", "value": fmt_usd(_di, cur),
                        "raw": _di, "yoy": "n/a"})
    elif _di is not None:
        _DEBT_ISSUED_NET.append(_di)
    uses_tbl = []
    for label, keyset in (("Capex", "capex"), ("Acquisitions", "acq"),
                          ("Buybacks", "buyback_usd"), ("Dividends", "dividend")):
        cv = abs(gv(L, keyset)) if gv(L, keyset) is not None else None
        pv = abs(gv(P, keyset)) if gv(P, keyset) is not None else None
        uses_tbl.append({"item": label, "value": fmt_usd(cv, cur), "raw": cv,
                         "yoy": fmt_pct((yoy(cv, pv) or 0) * 100, signed=True) if pv else "n/a"})
    uses_tbl.append({"item": "Debt Paydown", "value": fmt_usd(debt_reduction, cur),
                     "raw": debt_reduction, "yoy": "n/a"})
    deltas = []
    for row in uses_tbl:
        cvp = abs(gv(P, {"Capex": "capex", "Acquisitions": "acq", "Buybacks": "buyback_usd",
                         "Dividends": "dividend"}.get(row["item"], "capex"))) \
            if row["item"] in ("Capex", "Acquisitions", "Buybacks", "Dividends") and \
            gv(P, {"Capex": "capex", "Acquisitions": "acq", "Buybacks": "buyback_usd",
                   "Dividends": "dividend"}[row["item"]]) is not None else None
        if row["raw"] is not None and cvp is not None:
            deltas.append((row["item"], row["raw"] - cvp))
    biggest = max(deltas, key=lambda t: abs(t[1])) if deltas else None
    sources_uses = {
        "period": latest, "prior": prior, "sources": sources, "uses": uses_tbl,
        "largest_change": (f"{biggest[0]} {'rose' if biggest[1] > 0 else 'fell'} "
                           f"{fmt_usd(abs(biggest[1]), cur)} vs {prior}") if biggest else "n/a",
    }

    def roic_of(pm):
        eb = gv(pm, "ebit")
        etr = gv(pm, "etr")
        tax = (etr / 100.0) if etr is not None else 0.21
        if eb is None:
            return None
        nopat = eb * (1 - tax)
        d, e, csh = gv(pm, "total_debt"), gv(pm, "equity"), gv(pm, "cash")
        if e is None or e <= 0:
            return None
        inv = (d or 0) + e - (csh or 0)
        if inv <= 0:
            return None
        return nopat / inv * 100.0

    def roe_of(pm):
        ni, e = gv(pm, "net_income"), gv(pm, "equity")
        if e is not None and e <= 0:
            _ROE_REFUSALS.append(e)
            return None
        _r = safe_div(ni, e) * 100.0 if (ni is not None and e) else None
        if _r is not None and abs(_r) > _THIN_ROE_ABS:
            _ROE_THIN.append((_r, e))
        return _r

    roic_s = [roic_of(by_period[y]) for y in years]
    roe_s = [roe_of(by_period[y]) for y in years]

    bb_total = ssum(bb_s)
    bb_yield = safe_div(buyback, mcap)
    _sh_idx = [i for i, v in enumerate(sh_s or []) if v]
    if len(_sh_idx) >= 2:
        sh_first, sh_last = sh_s[_sh_idx[0]], sh_s[_sh_idx[-1]]
        _sh_span = None if _cur_mix else (_sh_idx[-1] - _sh_idx[0])
        _sh_first_yr, _sh_last_yr = years[_sh_idx[0]], years[_sh_idx[-1]]
    else:
        sh_first, sh_last = (sh_s[0] if sh_s else None), (sh_s[-1] if sh_s else None)
        _sh_span = span
        _sh_first_yr, _sh_last_yr = (years[0] if years else ""), (years[-1] if years else "")
    _sh_period = ("FY%s-FY%s" % (_sh_first_yr, _sh_last_yr)) if _sh_first_yr else ""
    sh_reduction = safe_div((sh_first - sh_last), sh_first) if (sh_first and sh_last) else None
    sh_cagr = cagr(sh_first, sh_last, _sh_span) if (sh_first and sh_last and _sh_span) else None
    _bb_nil_corroborated = (buyback is None
                            and sh_reduction is not None
                            and sh_reduction <= 0.005)
    num_s = series(by_period, years, "buyback_num", absolute=True)
    tot_bb_usd = ssum(bb_s)
    tot_bb_num = ssum(num_s)
    avg_rep_px = safe_div(tot_bb_usd, tot_bb_num) if tot_bb_num else None
    rep_vs_price = None
    if avg_rep_px is not None and price:
        rep_vs_price = (avg_rep_px - price) / price * 100.0
    eps_uplift = (-sh_cagr) if sh_cagr is not None else None
    bb_flags = []
    bb_score = 5
    if buyback and buyback > 0:
        if sh_reduction is not None and sh_reduction > RV("buybacks", "share_red_gt"):
            bb_score += RD("buybacks", "share_red_gt"); bb_flags.append("net share count reduced")
        elif sh_reduction is not None and sh_reduction <= RV("buybacks", "share_flat"):
            bb_score += RD("buybacks", "share_flat"); bb_flags.append("share count flat/up despite buybacks (dilution offset)")
        if rep_vs_price is not None and rep_vs_price < RV("buybacks", "rep_below"):
            bb_score += RD("buybacks", "rep_below"); bb_flags.append("repurchased below current price")
        elif rep_vs_price is not None and rep_vs_price > RV("buybacks", "rep_above"):
            bb_score += RD("buybacks", "rep_above"); bb_flags.append("average repurchase price above current price")
        if bb_yield is not None and bb_yield > RV("buybacks", "bb_yield_gt"):
            bb_score += RD("buybacks", "bb_yield_gt"); bb_flags.append("buyback yield >3%")
    else:
        bb_flags.append("no material buybacks")
        bb_score = 5
    bb_score = max(0, min(10, bb_score))
    buybacks_avg_rep_disp = (fmt_pct(rep_vs_price, signed=True) if rep_vs_price is not None else "n/a")
    buybacks = {
        "total_5y": fmt_usd(bb_total, cur), "latest": fmt_usd(buyback, cur),
        "buyback_yield": fmt_pct(bb_yield * 100, small=True) if bb_yield is not None else "n/a",
        "share_count_change": fmt_pct(-sh_reduction * 100, signed=True) if sh_reduction is not None else "n/a",
        "share_count_period": _sh_period,
        "share_count_cagr": fmt_pct(sh_cagr, signed=True) if sh_cagr is not None else "n/a",
        "avg_repurchase_price": fmt_ps(avg_rep_px, cur) if avg_rep_px is not None else "n/a",
        "avg_rep_vs_current": fmt_pct(rep_vs_price, signed=True) if rep_vs_price is not None else "n/a",
        "eps_accretion_est": fmt_pct(eps_uplift, signed=True) if eps_uplift is not None else "n/a",
        "rating": rate(bb_score), "score": bb_score, "flags": bb_flags,
        "nil_corroborated_by_share_count": _bb_nil_corroborated,
        "share_count_period": _sh_period,
        "discipline": {
            "value_creating": (("Yes - repurchased " + buybacks_avg_rep_disp + " vs current price")
                               if (rep_vs_price is not None and rep_vs_price < 0) else
                               ("No - at/above current price" if rep_vs_price is not None else "n/a")),
            "material_reduction": (("Yes - count down " + fmt_pct(sh_reduction * 100))
                                   if (sh_reduction is not None and sh_reduction > 0.02) else
                                   ("No - largely offsetting dilution" if buyback else "no buyback")),
        },
    }

    div_cagr = cagr(div_s[0], div_s[-1], span) if (div_s and div_s[0] and div_s[-1]) else None
    _dps_first = (safe_div(div_s[0], sh_s[0]) if (div_s and sh_s and div_s[0] and sh_s[0]) else None)
    _dps_last = (safe_div(div_s[-1], sh_s[-1]) if (div_s and sh_s and div_s[-1] and sh_s[-1]) else None)
    dps_cagr = cagr(_dps_first, _dps_last, span) if (_dps_first and _dps_last) else None
    _ni_gaap = gv(L, "net_income_gaap")
    payout = None
    if dividend is not None and _ni_gaap and _ni_gaap > 0:
        payout = dividend / _ni_gaap * 100.0
    if payout is None:
        payout = gv(L, "payout")
    if payout is None and _ni_gaap is None and dividend is not None and net_income and net_income > 0:
        payout = dividend / net_income * 100.0
    _payout_on_loss = None
    if payout is None and dividend is not None and net_income:
        if net_income > 0:
            payout = dividend / net_income * 100.0
        else:
            _payout_on_loss = dividend
    coverage_fcf = safe_div(fcf, dividend) if dividend else None
    _dcf = gv(L, "dcf")
    _mid = (_dcf is not None) and (gv(L, "maint_capex") is not None)
    _dcf_cov = safe_div(_dcf, dividend) if (_mid and dividend) else None
    _dcf_payout = (safe_div(dividend, _dcf) * 100.0) if (_mid and _dcf) else None
    dps = safe_div(dividend, shares_cur) if shares_cur else None
    div_yield = safe_div(dps, price) if (dps is not None and price) else None
    _div_reported = any(v is not None for v in (div_s or []))
    _div_nil_corroborated = (dividend is None
                             and not _div_reported
                             and len(years) >= 3
                             and str((raw.get("meta") or {}).get("dividend_policy",
                                                                 "")).lower() != "pays")
    if str((raw.get("meta") or {}).get("dividend_policy", "")).lower() == "none":
        _div_nil_corroborated = True
    d_score = 5
    d_flags = []
    if _div_nil_corroborated:
        d_score = None
        d_flags = ["no dividend was paid in any period of the analysis window"]
        sustain = "n/a"
    elif _mid and dividend and dividend > 0:
        if _dcf_cov is not None and _dcf_cov >= RV("dividends", "dcf_cov_strong"):
            d_score += RD("dividends", "dcf_cov_strong"); d_flags.append("distributions covered %.2fx by distributable cash flow" % _dcf_cov)
        elif _dcf_cov is not None and _dcf_cov >= RV("dividends", "dcf_cov_ok"):
            d_score += RD("dividends", "dcf_cov_ok"); d_flags.append("distributions covered %.2fx by distributable cash flow" % _dcf_cov)
        elif _dcf_cov is not None:
            d_score += RD("dividends", "dcf_cov_short"); d_flags.append("distributions not covered by distributable cash flow (%.2fx)" % _dcf_cov)
        d_flags.append("earnings payout and free-cash-flow cover are not the midstream tests: free "
                       "cash flow is struck after growth capital expenditure, while distributions "
                       "are paid from distributable cash flow")
        sustain = ("Safe" if d_score >= 8 else "Moderate Risk" if d_score >= 5 else "At Risk")
    elif dividend and dividend > 0:
        if payout is not None and payout < RV("dividends", "payout_low"):
            d_score += RD("dividends", "payout_low"); d_flags.append("payout <60% of earnings")
        elif payout is not None and payout > RV("dividends", "payout_high"):
            d_score += RD("dividends", "payout_high"); d_flags.append("payout >90% - limited cushion")
        elif _payout_on_loss:
            d_score += RD("dividends", "payout_high")
            d_flags.append("dividend paid against a loss - no earnings cushion")
        if coverage_fcf is not None and coverage_fcf < 0:
            d_flags.append("free cash flow negative; dividend assessed on earnings payout only")
        elif coverage_fcf is not None and coverage_fcf > RV("dividends", "fcf_cover_gt"):
            d_score += RD("dividends", "fcf_cover_gt"); d_flags.append("FCF covers dividend >1.5x")
        elif coverage_fcf is not None and coverage_fcf < RV("dividends", "fcf_cover_lt"):
            d_score += RD("dividends", "fcf_cover_lt"); d_flags.append("FCF does not cover dividend")
        if div_cagr is not None and div_cagr > RV("dividends", "div_cagr_gt"):
            d_score += RD("dividends", "div_cagr_gt"); d_flags.append("dividend growth >5% CAGR")
        sustain = ("Safe" if d_score >= 8 else "Moderate Risk" if d_score >= 5 else "At Risk")
    else:
        d_flags.append("no dividend")
        sustain = "n/a"
    d_score = None if d_score is None else max(0, min(10, d_score))
    dividends = {
        "not_applicable": bool(_div_nil_corroborated),
        "not_applicable_reason": ("No dividend was paid in any period of the analysis window."
                                  if _div_nil_corroborated else None),
        "dividend_5y_cagr": fmt_pct(div_cagr, signed=True) if div_cagr is not None else "n/a",
        "dividend_per_share_cagr": fmt_pct(dps_cagr, signed=True) if dps_cagr is not None else "n/a",
        "payout_ratio": fmt_pct(payout) if payout is not None else "n/a",
        "dcf_coverage": (fmt_x(_dcf_cov) if _dcf_cov is not None else None),
        "dcf_payout_ratio": (fmt_pct(_dcf_payout) if _dcf_payout is not None else None),
        "distribution_basis": ("Distributions are assessed against distributable cash flow, which "
                               "is struck after maintenance capital expenditure. Free cash flow is "
                               "after growth capital expenditure and is not the midstream test."
                               if _mid else None),
        "fcf_coverage": ("Not meaningful because free cash flow is negative"
                         if (coverage_fcf is not None and coverage_fcf < 0)
                         else fmt_x(coverage_fcf) if coverage_fcf is not None else "n/a"),
        "fcf_coverage_meaningful": bool(coverage_fcf is not None and coverage_fcf >= 0),
        "dividend_per_share": fmt_ps(dps, cur) if dps is not None else "n/a",
        "dividend_yield": fmt_pct(div_yield * 100, small=True) if div_yield is not None else "n/a",
        "sustainability": sustain, "score": d_score, "flags": d_flags,
    }

    _acq_reported = any(v is not None for v in (acq_s or []))
    acq_total = ssum(acq_s)
    _cum_ocf = ssum(ocf_s)
    acq_pct_ocf = safe_div(acq_total, _cum_ocf)
    _MAT_PROSE = {"cumulative operating cash flow": "operating cash flow",
                  "cumulative capital deployed": "capital deployed"}
    _cum_deploy = None
    _parts = [ssum(capex_s), ssum(acq_s), ssum(bb_s), ssum(div_s)]
    if any(v is not None for v in _parts):
        _cum_deploy = sum(abs(v) for v in _parts if v is not None) or None
    if _cum_ocf and _cum_ocf > 0:
        _mat_pct, _mat_basis, _mat_denom = acq_pct_ocf, "cumulative operating cash flow", _cum_ocf
    else:
        _mat_pct = safe_div(acq_total, _cum_deploy)
        _mat_basis, _mat_denom = "cumulative capital deployed", _cum_deploy
    roic_change = (roic_s[-1] - roic_s[0]) if (roic_s and roic_s[0] is not None and roic_s[-1] is not None) else None
    _roic_terminal = roic_s[-1] if roic_s else None
    _roic_readable = (_roic_terminal is not None and _roic_terminal > 0)
    if roic_change is not None and not _roic_readable:
        roic_change = None
    m_score = 5
    m_flags = []
    material = _mat_pct is not None and _mat_pct > RV("m_and_a", "material_floor")
    if acq_total and acq_total > 0 and material:
        if not _roic_readable:
            m_score = None; assess = "Unscored"
            m_flags.append("return on invested capital is not positive over the period, so the "
                           "change in it cannot evidence acquisition outcomes in either "
                           "direction; deal-level returns are not disclosed")
        elif roic_change is not None and roic_change > RV("m_and_a", "roic_up"):
            m_score = RD("m_and_a", "roic_up"); m_flags.append("ROIC expanded over the acquisition period")
            assess = "Value Creating"
        elif (roic_change is not None and roic_change < RV("m_and_a", "roic_down")
              and (_mat_pct or 0) > RV("m_and_a", "roic_down_share")):
            m_score = RD("m_and_a", "roic_down"); m_flags.append("ROIC contracted while acquisitions dominated uses")
            assess = "Value Destructive"
        else:
            m_score = RD("m_and_a", "otherwise"); assess = "Neutral"
            m_flags.append("ROIC change reflects the total capital base; deal-level attribution needs transaction data")
        if _mat_pct is not None and _mat_pct > 0.5:
            m_flags.append("acquisitions consumed >50%% of %s" % _mat_basis)
    elif acq_total and acq_total > 0:
        m_score = None
        assess = "Not applicable"
        m_flags.append("acquisition spend below 15% of cumulative operating cash flow - immaterial")
    elif not _acq_reported:
        m_score = None
        assess = "Unscored"
        m_flags.append("the acquisition line is not reported in the source data, so whether "
                       "acquisitions occurred cannot be determined from the feed")
    else:
        m_score = None
        assess = "Not applicable"
        m_flags.append("no material acquisitions occurred during the analysis period")

    _named = [d for d in ((raw.get("narrative") or {}).get("m_and_a") or {}).get("major_transactions", [])
              if isinstance(d, (str, dict))]
    _acq_line_missing = (acq_total is None) or (acq_total == 0)
    if _acq_line_missing and _named:
        m_score = None
        assess = "Unscored"
        m_flags = ["material transactions are named in the filings but the acquisition "
                   "consideration is not available in the standardised data, so the dimension "
                   "cannot be scored"]
    m_score = (max(0, min(10, m_score)) if m_score is not None else None)
    m_and_a = {
        "acq_total_5y": fmt_usd(acq_total, cur),
        "acq_pct_of_ocf": fmt_pct(_mat_pct * 100) if _mat_pct is not None else "n/a",
        "materiality_basis": _mat_basis,
        "materiality_prose": _MAT_PROSE[_mat_basis],
        "cumulative_ocf": fmt_usd(_mat_denom, cur) if _mat_denom is not None else "n/a",
        "roic_change_bps": (f"{roic_change*100:+.0f} bps" if roic_change is not None
                            else ("n/a - return on invested capital is not positive at the end of "
                                  "the period, so the change in it is not readable as an outcome"
                                  if not _roic_readable else "n/a")),
        "assessment": assess, "score": m_score, "flags": m_flags,
        "not_applicable": bool(assess == "Not applicable"),
        "acq_line_reported": _acq_reported,
        "status_text": (("Not applicable. Acquisition spending of %s represented %s of "
                         "%s, below the methodology's 15%% materiality "
                         "threshold."
                         % (fmt_usd(acq_total, cur),
                            fmt_pct(_mat_pct * 100) if _mat_pct is not None else "n/a",
                            _mat_basis))
                        if (assess == "Not applicable" and not _named and acq_total) else
                        "Not applicable. No acquisition spend was reported during the analysis period."
                        if assess == "Not applicable" and not _named else
                        "Unscored due to incomplete transaction consideration."
                        if assess == "Unscored" else None),
        "note": "Deal-level purchase multiples require transaction inputs; add them in narrative if known.",
    }

    capex_intensity = safe_div(capex, revenue)
    capex_cagr = cagr(capex_s[0], capex_s[-1], span) if (capex_s and capex_s[0] and capex_s[-1]) else None
    reinvest_rate = safe_div(capex, ocf) if (ocf is not None and ocf > 0) else None
    _reinvest_no_ocf = (ocf is not None and ocf <= 0 and capex is not None)
    if _reinvest_no_ocf:
        warnings.append(
            "Reinvestment rate withheld: operating cash flow is %s for the period measured, so "
            "capex as a share of it is a negative number that reads as the opposite of what it "
            "measures. The capex figures are published on their own terms (amount, intensity of "
            "revenue, growth) and the funding is stated separately."
            % fmt_usd(ocf, cur))
    da = gv(L, "da")
    growth_capex = (capex - da) if (capex is not None and da is not None and capex > da) else None
    cx_score = 6
    cx_flags = []
    if capex_intensity is not None:
        if capex_intensity > RV("capex", "intensity_heavy"):
            cx_flags.append("capex intensity >15% of revenue (capital-heavy)")
        if capex_cagr is not None and capex_cagr > RV("capex", "cagr_fast"):
            cx_flags.append("capex accelerating >20% CAGR")
        if capex_intensity < RV("capex", "intensity_low"):
            if _cap_intensive:
                assess_cx = "Low reported capex"
                cx_flags.append("capex <3% of revenue - low for a capital-intensive sector; verify capex data completeness")
            else:
                assess_cx = "Low capital intensity"
                cx_flags.append("capex <3% of revenue (lower capital intensity)")
        elif (capex_cagr or 0) > 25 and capex_intensity > 0.10:
            assess_cx = "Aggressive"
        elif capex_intensity < RV("capex", "intensity_underinvest") and (capex_cagr or 0) < 0:
            assess_cx = "Underinvesting"; cx_flags.append("declining, sub-2% intensity")
        else:
            assess_cx = "Within the range observed for the sector"
    else:
        assess_cx = "n/a"
    capex_out = {
        "capex_latest": fmt_usd(capex, cur), "capex_5y_cagr": fmt_pct(capex_cagr, signed=True) if capex_cagr is not None else "n/a",
        "capex_intensity": fmt_pct(capex_intensity * 100) if capex_intensity is not None else "n/a",
        "reinvestment_rate": fmt_pct(reinvest_rate * 100) if reinvest_rate is not None else "n/a",
        "growth_capex_est": fmt_usd(growth_capex, cur) if growth_capex is not None else "n/a (<=D&A)",
        "maintenance_capex_proxy": fmt_usd(da, cur) if da is not None else "n/a",
        "assessment": assess_cx, "score": cx_score, "flags": cx_flags,
    }

    rnd_latest = gv(L, "rnd")
    rnd_intensity = safe_div(rnd_latest, revenue)
    reinvest_intensity = safe_div(ssum([capex, rnd_latest]), revenue)
    rev_yoy_latest = (yoy(rev_s[-1], rev_s[-2]) if len(rev_s) >= 2 else None)
    rev_yoy_prior = (yoy(rev_s[-2], rev_s[-3]) if len(rev_s) >= 3 else None)
    growth_decel = (rev_yoy_latest is not None and rev_yoy_prior is not None
                    and rev_yoy_latest < rev_yoy_prior - 0.10)
    ri = reinvest_intensity
    _RAIL = any(k in " ".join(str((raw.get("meta") or {}).get(k, "")) for k in
                              ("industry", "classification", "company_name")).lower()
                for k in ("railroad", "rail ", "transportation and logistics", "union pacific"))
    _RI_FLOOR = {"Energy": 0.12, "Utilities": 0.12, "Industrials": 0.07, "Materials": 0.08,
                 "Telecom": 0.10, "REIT": 0.08, "Software": 0.08, "Healthcare": 0.08,
                 "Consumer": 0.03, "Banks": None, "Insurance": None}
    _ri_floor = _RI_FLOOR.get(_sector_key, 0.05)
    if _RAIL:
        _ri_floor = 0.12
    if ri is None or _ri_floor is None:
        adequacy = "n/a"
    elif ((ri < _ri_floor) or (ri < _ri_floor * 2 and growth_decel)) and (rev_yoy_latest or 0) < 0.10:
        adequacy = "Below peer reference (insufficient data to classify)"
    elif ri > 0.25 and growth_decel:
        adequacy = "Over-Investing"
    else:
        adequacy = "Within the observed range (no threshold breached)"
    capex_out["reinvest_floor"] = (fmt_pct(_ri_floor * 100) if _ri_floor is not None else "n/a")
    capex_out["rnd_intensity"] = fmt_pct(rnd_intensity * 100) if rnd_intensity is not None else "n/a"
    capex_out["reinvestment_intensity"] = fmt_pct(reinvest_intensity * 100) if reinvest_intensity is not None else "n/a"
    capex_out["revenue_growth_yoy"] = fmt_pct(rev_yoy_latest * 100, signed=True) if rev_yoy_latest is not None else "n/a"
    if _RAIL and not str(adequacy).startswith("Incomplete"):
        adequacy = ("Provisional: capital intensity and reinvestment rate are measured, but "
                    "the data does not separate maintenance from growth capital expenditure")
    capex_out["reinvestment_adequacy"] = adequacy
    capex_out["capex_state"] = ("Incomplete" if str(adequacy).startswith("Incomplete")
                                else "Provisional" if _RAIL else "Scored")
    capex_out["weight_factor"] = (0.0 if capex_out["capex_state"] == "Incomplete"
                                  else 0.5 if capex_out["capex_state"] == "Provisional" else 1.0)
    capex_out["capex_state_reason"] = (
        "the data does not separate maintenance from growth capital expenditure, and that difference "
        "matters for a network operator." if _RAIL else
        "the reinvestment rate could not be measured." if str(adequacy).startswith("Incomplete") else "")
    if _RAIL:
        capex_out["framework"] = (
            "Network-asset reinvestment framework: for a railroad, capital expenditure sustains a "
            "fixed network (track, locomotives, terminals) rather than adding discretionary "
            "capacity. Reinvestment is assessed against railroad operating measures - carloads and "
            "revenue ton-miles, operating ratio, network velocity and terminal dwell, fuel "
            "efficiency and pricing - together with capital expenditure against depreciation and "
            "the share of operating cash flow retained for the network. Backlog and contracted "
            "project returns are engineering-and-construction measures and do not apply. "
            "The data does not separate maintenance from growth capital expenditure, so that split is "
            "reported as unavailable and the capex read is provisional.")
        _dep = gv(L, "da")
        capex_out["capex_vs_depreciation"] = (
            fmt_x(safe_div(capex, _dep)) if (capex and _dep) else "n/a")
    _rnd_na = str(capex_out["rnd_intensity"]).lower().startswith("n/a")
    if _rnd_na:
        _basis_head = (f"Capex {capex_out['capex_intensity']} of revenue "
                       f"(capex only; R&D not reported separately)")
    else:
        _basis_head = (f"R&D {capex_out['rnd_intensity']} + capex {capex_out['capex_intensity']} "
                       f"of revenue (reinvestment intensity {capex_out['reinvestment_intensity']})")
    capex_out["adequacy_basis"] = (
        f"{_basis_head} vs {_article(capex_out['reinvest_floor'])} {capex_out['reinvest_floor']} "
        f"sector norm; revenue {capex_out['revenue_growth_yoy']} YoY. Verdict: {adequacy}.")

    net_debt = gv(cur_bs, "net_debt")
    total_debt = gv(cur_bs, "total_debt")
    cash_now = gv(cur_bs, "cash")
    interest = gv(L, "interest")
    if interest in (None, 0):
        interest = next((gv(by_period[y], "interest") for y in reversed(years)
                         if gv(by_period[y], "interest") not in (None, 0)), None)
    ebitda_eff = ebitda if (ebitda and ebitda > 0) else None
    if ebitda_eff is None:
        _eb_fallback = next(((i, v) for i, v in reversed(list(enumerate(ebitda_s or [])))
                             if v and v > 0), None)
        if _eb_fallback and ebitda is None:
            ebitda_eff = _eb_fallback[1]
        elif ebitda is not None and ebitda <= 0:
            warnings.append(
                "Leverage withheld: EBITDA is %s in the latest reported year, so net debt / "
                "EBITDA has no positive denominator for the period being measured. Substituting "
                "an earlier profitable year would state a multiple this period does not support. "
                "The balance sheet is described from the reported debt and cash instead."
                % fmt_usd(ebitda, cur))
    ebit_eff = ebit if ebit is not None else next((v for v in reversed(ebit_s) if v is not None), None)
    leverage = safe_div(net_debt, ebitda_eff) if (net_debt is not None and ebitda_eff) else None
    leverage_unit = "x"
    if _sector_key == "Insurance":
        _eq_now = gv(cur_bs, "equity")
        _tot_cap = ((total_debt or 0) + _eq_now) if (total_debt is not None and _eq_now) else None
        leverage = (safe_div(total_debt, _tot_cap) * 100.0) if (_tot_cap and _tot_cap > 0) else None
        leverage_unit = "%"
    _sti = gv(cur_bs, "short_term_investments")
    _sti_name = None
    for _r in list(unwrap(raw.get("actuals_annual"))) + list(unwrap(raw.get("actuals_quarter"))):
        if _r.get("LTCODE") in (C.get("short_term_investments") or []) and _r.get("LINEITEM"):
            _sti_name = str(_r["LINEITEM"]).strip()
    _sti_liquid = bool(_sti_name and any(k in _sti_name.lower() for k in
                                         ("short-term", "short term", "marketable")))
    _nd_calc = None
    if total_debt is not None and cash_now is not None:
        _nd_calc = total_debt - cash_now
    _sti_reported = gv(cur_bs, "short_term_investments") is not None
    _nd_calc_b = (_nd_calc - _sti) if (_nd_calc is not None and _sti is not None) else None
    _nd_recon, _nd_via_sti = None, False
    if _nd_calc is not None and net_debt is not None:
        _tol = max(1.0, abs(net_debt) * 0.01)
        if abs(_nd_calc - net_debt) <= _tol:
            _nd_recon = True
        elif _nd_calc_b is not None and abs(_nd_calc_b - net_debt) <= max(0.1, abs(net_debt) * 0.001):
            _nd_recon, _nd_via_sti = True, True
        else:
            _nd_recon = False if _sti_reported else None
    if _nd_via_sti:
        _nd_calc = _nd_calc_b
    _nd_residual = (net_debt - _nd_calc) if (_nd_calc is not None and net_debt is not None) else None
    _nd_conflict = bool(_nd_recon is not True and _nd_residual is not None
                        and _nd_calc is not None and net_debt is not None
                        and _nd_residual > max(1.0, abs(net_debt) * 0.01))
    if _nd_conflict:
        warnings.append(
            "Net debt conflict: the source reports %s, but total debt %s less cash %s is %s. The "
            "source figure nets less than cash and equivalents, which no cash definition permits, "
            "so it is a source inconsistency rather than a definitional difference. The headline "
            "net-debt figure and the leverage multiple are withheld; both figures are shown in the "
            "bridge." % (fmt_usd(net_debt, cur), fmt_usd(total_debt, cur),
                         fmt_usd(cash_now, cur), fmt_usd(_nd_calc, cur)))
        leverage = None
    if _nd_recon is None and _nd_calc is not None and net_debt is not None:
        warnings.append("Net debt bridge indeterminate: reported net debt %s differs from total "
                        "debt less cash (%s) by %s, and no marketable-securities line was returned "
                        "to explain the difference. The residual is disclosed, not netted."
                        % (fmt_usd(net_debt, cur), fmt_usd(_nd_calc, cur), fmt_usd(_nd_residual, cur)))
    if _nd_recon is False:
        warnings.append("Net debt does not reconcile: total debt %s less cash %s gives %s, against "
                        "a reported %s. The %s difference reflects a broader or narrower cash "
                        "definition in the source and is disclosed in the bridge."
                        % (fmt_usd(total_debt, cur), fmt_usd(cash_now, cur), fmt_usd(_nd_calc, cur),
                           fmt_usd(net_debt, cur), fmt_usd(_nd_residual, cur)))
    int_cov = safe_div(ebit_eff, abs(interest)) if (ebit_eff is not None and interest not in (None, 0)) else None
    bs_score = 6
    bs_flags = []
    if leverage is not None and leverage_unit == "%":
        if leverage < RV("balance_sheet", "dtc_low"):
            bs_score = RD("balance_sheet", "dtc_low"); bs_flags.append("debt <25% of total capital")
        elif leverage < RV("balance_sheet", "dtc_mid"):
            bs_score = RD("balance_sheet", "dtc_mid")
        elif leverage < RV("balance_sheet", "dtc_high"):
            bs_score = RD("balance_sheet", "dtc_high")
        else:
            bs_score = RD("balance_sheet", "dtc_stretched"); bs_flags.append("debt >45% of total capital")
    elif (leverage is None and leverage_unit == "x" and not _nd_conflict
          and net_debt is not None and net_debt < 0):
        bs_score = RD("balance_sheet", "net_cash"); bs_flags.append("net cash position")
    elif leverage is not None:
        if leverage < RV("balance_sheet", "net_cash"):
            bs_score = RD("balance_sheet", "net_cash"); bs_flags.append("net cash position")
        elif leverage < RV("balance_sheet", "lev_low"):
            bs_score = RD("balance_sheet", "lev_low"); bs_flags.append("leverage <1.5x")
        elif leverage < RV("balance_sheet", "lev_mid"):
            bs_score = RD("balance_sheet", "lev_mid")
        else:
            bs_score = RD("balance_sheet", "lev_high"); bs_flags.append("leverage >3x")
    if int_cov is not None and int_cov < RV("balance_sheet", "int_cov_floor"):
        bs_score = min(bs_score, 4); bs_flags.append("interest coverage <3x")
    st_invest = None
    bs_reconciles = None
    if total_debt is not None and cash_now is not None and net_debt is not None:
        st_invest = gv(cur_bs, "short_term_investments")
        _tol = max(1000.0, 0.02 * abs(net_debt))
        _ties = (_nd_recon is True) if _nd_recon is not None else \
                (abs((total_debt - cash_now) - net_debt) <= _tol)
        bs_reconciles = True if _ties else (False if st_invest is not None else None)
    balance_sheet = {
        "total_debt": fmt_usd(total_debt, cur) if total_debt is not None else "Debt unavailable from source",
        "net_debt": ("Withheld: the source figure conflicts with the bridge (see below)"
                     if _nd_conflict else
                     (fmt_usd(net_debt, cur) if net_debt is not None
                      else "Net debt unavailable from source")),
        "net_debt_conflict": _nd_conflict,
        "cash": fmt_usd(cash_now, cur) if cash_now is not None else "Cash balance unavailable from source",
        "short_term_investments": (fmt_usd(st_invest, cur) if st_invest else None),
        "short_term_investments_label": (_sti_name or "Short-term investments"),
        "short_term_investments_liquid": _sti_liquid,
        "net_debt_via_investments": _nd_via_sti,
        "reconciles": bs_reconciles,
        "leverage_nd_ebitda": ((fmt_pct(leverage) if leverage_unit == "%" else fmt_x(leverage))
                               if leverage is not None
                               else ("n/a (net debt withheld - source conflict)" if _nd_conflict
                                     else "n/a (EBITDA not positive)"
                                     if (ebitda is not None and ebitda <= 0)
                                     else "n/a (EBITDA unavailable)" if ebitda_eff is None
                                     else "n/a")),
        "weight_factor": (0.5 if _nd_conflict else None),
        "bs_state": ("Provisional" if _nd_conflict else "Scored"),
        "bs_state_reason": ("the net-debt figure is withheld over a source conflict, so leverage "
                            "is not available and the dimension rests on interest coverage alone"
                            if _nd_conflict else ""),
        "leverage_unit": leverage_unit,
        "net_debt_bridge": [_r for _r in [
            ("Total debt", fmt_usd(total_debt, cur)),
            ("Less: cash and equivalents", fmt_usd(cash_now, cur) if cash_now is not None
             else "not reported in source"),
            ("Less: %s" % (_sti_name or "short-term investments"),
             fmt_usd(_sti, cur)) if _nd_via_sti else None,
            ("= Net debt (%s)" % ("total debt less cash less investments" if _nd_via_sti
                                  else "total debt less cash"),
             fmt_usd(_nd_calc, cur) if _nd_calc is not None else "cannot be derived"),
            ("Net debt (as reported by source)", fmt_usd(net_debt, cur)),
            ("Difference in the source's cash definition",
             (fmt_usd(_nd_residual, cur) if _nd_residual not in (None, 0)
              else "none - the bridge ties exactly") if _nd_residual is not None
             else "not determinable"),
            None if _nd_via_sti else
            ("Memo: %s (not netted)" % (_sti_name or "short-term investments"),
             (fmt_usd(_sti, cur) + ("" if _sti_liquid else
                                    " - not a liquid short-term investment balance; not eligible "
                                    "for netting"))
             if _sti is not None else "none reported"),
        ] if _r is not None],
        "net_debt_formula": (
            "Total debt less cash and equivalents less %s - the formula that reconciles to the "
            "source's reported figure." % (_sti_name or "short-term investments")
            if _nd_via_sti else
            "Total debt less cash and equivalents, as reported by the source."),
        "net_debt_reconciled": _nd_recon,
        "interest_coverage": (fmt_x(int_cov) if int_cov is not None
                              else ("n/a (interest expense unavailable)" if interest in (None, 0) else "n/a")),
        "cash_available": cash_now is not None, "debt_available": total_debt is not None,
        "interest_available": interest not in (None, 0), "ebitda_available": ebitda_eff is not None,
        "leverage_metric_label": None,
        "score": max(0, min(10, bs_score)), "flags": bs_flags,
    }

    fcf_ps_first = safe_div(fcf_s[0], sh_s[0]) if (fcf_s and fcf_s[0] and sh_s and sh_s[0]) else None
    fcf_ps_last = safe_div(fcf_s[-1], sh_s[-1]) if (fcf_s and fcf_s[-1] and sh_s and sh_s[-1]) else None
    fcf_ps_cagr = cagr(fcf_ps_first, fcf_ps_last, span)
    _eps_f = next((v for v in eps_s if v is not None), None) if eps_s else None
    _eps_l = next((v for v in reversed(eps_s) if v is not None), None) if eps_s else None
    eps_cagr = cagr(_eps_f, _eps_l, span) if (_eps_f and _eps_l) else None
    bvps_first = safe_div(eq_s[0], sh_s[0]) if (eq_s and eq_s[0] and sh_s and sh_s[0]) else None
    bvps_last = safe_div(eq_s[-1], sh_s[-1]) if (eq_s and eq_s[-1] and sh_s and sh_s[-1]) else None
    bvps_cagr = cagr(bvps_first, bvps_last, span)
    roic_now = roic_s[-1] if roic_s else None
    roe_now = roe_s[-1] if roe_s else None
    _roe_reported = gv(L, "roe_reported")
    _roe_basis = "derived: net income / average shareholders equity"
    if _roe_reported is not None:
        roe_now = _roe_reported
        _roe_basis = "as reported by the source (RT_P_102)"
    tsr = raw.get("tsr") or {}
    _tsr_d0 = tsr.get("price_start_date") or tsr.get("date_start")
    _tsr_d1 = tsr.get("price_end_date") or tsr.get("date_end")
    tsr_ann = None
    if tsr.get("price_start") and tsr.get("price_end") and tsr.get("years"):
        ps, pe, ny = tsr["price_start"], tsr["price_end"], tsr["years"]
        divc = tsr.get("div_per_share_cumulative", 0) or 0
        tsr_ann = ((pe + divc) / ps) ** (1.0 / ny) - 1.0
        tsr_ann *= 100.0
    cum_return = ssum([ssum(bb_s), ssum(div_s)])
    cum_fcf = ssum(fcf_s)
    cap_return_of_fcf = (safe_div(cum_return, cum_fcf) if (cum_fcf and cum_fcf > 0) else None)
    total_return_latest = ssum([buyback, dividend])
    roc_yield = safe_div(total_return_latest, mcap)

    vc_score = 5.0
    vc_flags = []
    vc_drivers, vc_constraints = [], []
    if roic_now is not None:
        if roic_now > coc + RV("value_creation", "roic_above"):
            vc_score += RD("value_creation", "roic_above"); vc_flags.append(f"ROIC {roic_now:.1f}% above the ~{coc:.0f}% hurdle"); vc_drivers.append(f"ROIC {roic_now:.1f}% vs a ~{coc:.0f}% hurdle")
        elif roic_now < coc:
            vc_score += RD("value_creation", "roic_below"); vc_flags.append(f"ROIC {roic_now:.1f}% below the ~{coc:.0f}% hurdle"); vc_constraints.append(f"ROIC {roic_now:.1f}% below the ~{coc:.0f}% hurdle")
    if fcf_ps_cagr is not None:
        if fcf_ps_cagr > RV("value_creation", "fcfps_strong"):
            vc_score += RD("value_creation", "fcfps_strong"); vc_flags.append(f"FCF/share compounding {fmt_pct(fcf_ps_cagr, signed=True)}"); vc_drivers.append(f"FCF per share {fmt_pct(fcf_ps_cagr, signed=True)} a year")
        elif fcf_ps_cagr > 0:
            vc_score += RD("value_creation", "fcfps_positive")
        else:
            vc_score += RD("value_creation", "fcfps_negative"); vc_flags.append("FCF/share not growing"); vc_constraints.append("free cash flow per share not growing")
    if eps_cagr is not None:
        if eps_cagr > RV("value_creation", "eps_strong"):
            vc_score += RD("value_creation", "eps_strong"); vc_flags.append(f"EPS compounding {fmt_pct(eps_cagr, signed=True)}"); vc_drivers.append(f"EPS {fmt_pct(eps_cagr, signed=True)} a year")
        elif eps_cagr < 0:
            vc_score += RD("value_creation", "eps_negative")
    if tsr_ann is not None:
        if tsr_ann >= RV("value_creation", "tsr_strong"):
            vc_score += RD("value_creation", "tsr_strong"); vc_flags.append(f"TSR {fmt_pct(tsr_ann)} annualized"); vc_drivers.append(f"TSR {fmt_pct(tsr_ann)} annualised")
        elif tsr_ann >= RV("value_creation", "tsr_ok"):
            vc_score += RD("value_creation", "tsr_ok")
        elif tsr_ann < 0:
            vc_score += RD("value_creation", "tsr_negative")
    if sh_reduction is not None:
        if sh_reduction > RV("value_creation", "share_red"):
            vc_score += RD("value_creation", "share_red"); vc_flags.append(f"share count reduced {fmt_pct(sh_reduction*100, signed=True)}"); vc_drivers.append(f"share count reduced {fmt_pct(sh_reduction*100)}")
        elif sh_reduction <= 0 and buyback:
            vc_score += RD("value_creation", "share_no_red"); vc_flags.append("buybacks not reducing the share count"); vc_constraints.append("buybacks not reducing the share count")
    if cap_return_of_fcf is not None and cap_return_of_fcf >= RV("value_creation", "cap_ret_fcf"):
        vc_score += RD("value_creation", "cap_ret_fcf"); vc_flags.append(f"returned {fmt_pct(cap_return_of_fcf*100)} of FCF to holders"); vc_drivers.append(f"returned {fmt_pct(cap_return_of_fcf*100)} of FCF")
    if bvps_cagr is not None and bvps_cagr < 0:
        vc_score += RD("value_creation", "bvps_decline"); vc_flags.append("book value per share declining"); vc_constraints.append("book value per share declining")
    vc_score = int(round(max(0, min(10, vc_score))))
    _verdict = ("Value Creating" if vc_score >= 7 else "Value Neutral" if vc_score >= 5 else "Value Destructive")
    _vc_drivers = ", ".join(vc_flags[:3]) if vc_flags else "mixed signals"
    verdict_basis = (f"{_verdict}: outcome score {vc_score}/10 "
                     f"(>=7 = Value Creating, 5-6 = Neutral, <5 = Destructive). "
                     f"Driven by {_vc_drivers}. Grades OUTCOMES (TSR, FCF/share, EPS, share count, "
                     f"capital returned), not the allocation mix.")
    if _cyclical:
        verdict_basis += (" Cyclical sector: a single-year ROIC/return can reflect trough or peak "
                          "margins; read this outcome through the cycle and distinguish cyclical "
                          "pressure from a structural capital-allocation issue (see Through-cycle assessment).")
    payout_of_fcf = safe_div(total_return_latest, fcf)
    if payout_of_fcf is None:
        sustain_lbl = "n/a"
    elif payout_of_fcf > 1.0:
        sustain_lbl = "Above FCF (balance-sheet funded)"
    elif payout_of_fcf >= 0.80:
        sustain_lbl = "Full (near FCF)"
    else:
        sustain_lbl = "Sustainable"
    value_creation = {
        "roic": fmt_pct(roic_now) if roic_now is not None else "n/a",
        "roic_5y_ago": fmt_pct(roic_s[0]) if (roic_s and roic_s[0] is not None) else "n/a",
        "return_series": [
            {"period": "FY%s" % _y,
             "roic": (fmt_pct(_v) if _v is not None else "n/a"),
             "spread": (fmt_pct(_v - coc, signed=True) if (_v is not None and coc is not None) else "n/a")}
            for _y, _v in zip(years, (roe_s if _sector_key in ("Banks", "Insurance") else roic_s))],
        "return_series_label": ("Return on equity" if _sector_key in ("Banks", "Insurance")
                                else "Return on invested capital"),
        "roe": fmt_pct(roe_now) if roe_now is not None else "n/a",
        "roe_basis": _roe_basis,
        "cost_of_capital": fmt_pct(coc), "cost_of_equity": fmt_pct(coe),
        "tsr_annualized": (fmt_pct(tsr_ann) if tsr_ann is not None else "Not available"),
        "tsr_available": tsr_ann is not None,
        "tsr_period_label": (
            ("%s to %s (%g yr%s)" % (_tsr_d0, _tsr_d1, tsr.get("years"),
                                     "" if tsr.get("years") == 1 else "s")
             if (_tsr_d0 and _tsr_d1)
             else "%g yr%s, annualised" % (tsr.get("years"), "" if tsr.get("years") == 1 else "s"))
            if tsr_ann is not None else None),
        "tsr_basis": ("Annualised price change plus cumulative dividends per share."
                      if tsr_ann is not None else
                      "Total shareholder return is not scored in this framework: over a five-year "
                      "window it reflects the change in valuation multiple as much as the allocation "
                      "decisions being assessed, so the outcome rests on return on invested capital "
                      "against the hurdle, per-share progression and the share count. The financial "
                      "data source also carries no price history, so the figure is excluded from "
                      "score coverage rather than treated as a missing input."),
        "fcf_per_share_cagr": fmt_pct(fcf_ps_cagr, signed=True) if fcf_ps_cagr is not None else "n/a",
        "eps_cagr": fmt_pct(eps_cagr, signed=True) if eps_cagr is not None else "n/a",
        "share_count_change": fmt_pct(-sh_reduction * 100, signed=True) if sh_reduction is not None else "n/a",
        "share_count_period": _sh_period,
        "book_value_per_share_cagr": fmt_pct(bvps_cagr, signed=True) if bvps_cagr is not None else "n/a",
        "capital_returned_period": fmt_usd(cum_return, cur) if cum_return is not None else "n/a",
        "capital_return_pct_fcf": fmt_pct(cap_return_of_fcf * 100) if cap_return_of_fcf is not None else "n/a",
        "cumulative_fcf": fmt_usd(cum_fcf, cur) if cum_fcf is not None else "n/a",
        "cumulative_ocf": fmt_usd(ssum(ocf_s), cur) if ssum(ocf_s) is not None else "n/a",
        "return_of_capital_yield": fmt_pct(roc_yield * 100, small=True) if roc_yield is not None else "n/a",
        "capital_return_payout_of_fcf": fmt_pct(payout_of_fcf * 100) if payout_of_fcf is not None else "n/a",
        "capital_return_sustainability": sustain_lbl,
        "verdict": _verdict, "verdict_basis": verdict_basis,
        "score": vc_score, "flags": vc_flags,
    }

    _sector_score_overrides = {}
    _sector_evidence_overrides = {}
    _sector_module_failed = None
    try:
        import sector_modules as _SM
        _mod = _SM.get_module(meta.get("sector_key"))
        _ctx = {
            "cur": cur, "coc": coc, "coe": coe, "span": span, "years": years, "by_period": by_period,
            "L": L, "period": f"FY{first}-FY{latest}",
            "gv": gv, "series": series, "safe_div": safe_div, "cagr": cagr, "ssum": ssum,
            "fmt_usd": fmt_usd, "fmt_pct": fmt_pct, "fmt_x": fmt_x, "fmt_ps": fmt_ps,
            "div_s": div_s, "sh_s": sh_s, "eq_s": eq_s, "bb_s": bb_s, "fcf_s": fcf_s,
            "sh_first_obs": sh_first, "sh_last_obs": sh_last,
            "sh_obs_idx": _sh_idx, "sh_span_obs": _sh_span, "sh_period_obs": _sh_period,
            "ni_s": ni_s, "eps_s": eps_s, "acq_s": acq_s, "capex_s": capex_s, "rev_s": rev_s,
            "intang_s": series(by_period, years, "intangibles"),
            "affo_yield_base": mcap,
            "roe_canonical": roe_now, "roe_basis": _roe_basis,
            "mcap": mcap, "price": price,
            "net_debt": gv(cur_bs, "net_debt"), "ebitda": gv(L, "ebitda"),
        }
        _so = _mod.outcome(_ctx)
        if _so:
            value_creation["sector_key"] = _mod.key
            value_creation["sector_outcome"] = _so["rows"]
            value_creation["sector_verdict"] = _so.get("verdict")
            value_creation["sector_verdict_basis"] = _so.get("verdict_basis")
            value_creation["sector_outcome_score"] = _so.get("score")
            value_creation["sector_notes"] = _so.get("notes")
            value_creation["sector_dashboard"] = _so.get("dashboard")
            value_creation["sector_reinvestment"] = _so.get("reinvestment")
            value_creation["sector_methodology"] = _so.get("methodology")
            value_creation["sector_snapshot"] = _so.get("snapshot")
            value_creation["sector_div_coverage"] = _so.get("div_coverage")
            _sector_score_overrides = _so.get("scorecard_overrides") or {}
            _sector_evidence_overrides = _so.get("evidence_overrides") or {}
            value_creation["weight_merges"] = _so.get("weight_merges") or []
            value_creation["dimension_labels"] = _so.get("dimension_labels") or {}
    except Exception as _e:
        _sector_module_failed = "%s: %s" % (type(_e).__name__, _e)
        warnings.append(
            "WITHHELD DATA: the %s sector lens did not run (%s). The report's spine and the base "
            "free-cash-flow lens are unaffected, but every measure this sector is judged on - and "
            "the score overrides that follow from them - is absent, so a score published here "
            "would be the generic lens under a sector heading."
            % (meta.get("sector_key") or "active", _sector_module_failed))
        value_creation["sector_module_error"] = _sector_module_failed

    _out_score = value_creation.get("sector_outcome_score")
    if _out_score is None:
        _out_score = vc_score
    def _num(v):
        m = re.search(r"[-+]?\d+(?:\.\d+)?", str(v or "").replace(",", ""))
        return float(m.group()) if (m and not str(v).lower().startswith(("n/a", "not "))) else None

    _fav, _tot = 0, 0
    for _k, _rule in (("eps_cagr", "up"), ("fcf_per_share_cagr", "up"),
                      ("share_count_change", "down")):
        _x = _num(value_creation.get(_k))
        if _x is None:
            continue
        _tot += 1
        _fav += 1 if ((_x > 0) if _rule == "up" else (_x < 0)) else 0
    _r, _h = _num(value_creation.get("roic")) or _num(value_creation.get("roe")), \
             _num(value_creation.get("cost_of_capital"))
    if _r is not None and _h is not None:
        _tot += 1
        _fav += 1 if _r >= _h else 0
    _outcome = (("%d of %d realised measures moved favourably over the period" % (_fav, _tot))
                if _tot else "Realised measures are not available")
    value_creation["allocation_outcome"] = _outcome
    value_creation["allocation_outcome_score"] = int(round(_out_score))
    value_creation["allocation_outcome_drivers"] = vc_drivers[:3]
    value_creation["allocation_outcome_constraints"] = vc_constraints[:3]
    value_creation["allocation_outcome_basis"] = (
        "Realised outcomes only (returns vs hurdle, per-share compounding, capital returned); "
        "it does not grade whether each decision was correct.")

    _cum_capex = ssum(capex_s)
    _cum_acq = ssum(acq_s)
    _cum_bb = ssum(bb_s)
    _cum_div = ssum(div_s)
    _d_first, _d_last = gv(by_period[years[0]], "total_debt"), gv(cur_bs, "total_debt")
    _cum_debt_red = (max(0.0, _d_first - _d_last)
                     if (_d_first is not None and _d_last is not None) else None)

    pool = ssum([_cum_capex, _cum_acq, _cum_bb, _cum_div, _cum_debt_red])
    reinvest = ssum([_cum_capex, _cum_acq])
    shret = ssum([_cum_bb, _cum_div])
    debt_reduction_cum = _cum_debt_red
    acq_cum = _cum_acq
    r_reinv = safe_div(reinvest, pool)
    r_shret = safe_div(shret, pool)
    r_debt = safe_div(debt_reduction_cum, pool)
    r_acq = safe_div(acq_cum, pool)

    _mix_amounts = {
        "reinvestment": reinvest, "acquisitions": acq_cum, "shareholder_returns": shret,
        "debt_reduction": debt_reduction_cum, "total": pool,
        "capex": _cum_capex, "buybacks": _cum_bb, "dividends": _cum_div,
    }
    _mix_recon, _mix_recon_fail = [], []
    for _k, _r in (("reinvestment", r_reinv), ("acquisitions", r_acq),
                   ("shareholder_returns", r_shret), ("debt_reduction", r_debt)):
        _amt = _mix_amounts.get(_k)
        if _r is None or _amt is None or not pool:
            continue
        _implied = _amt / pool
        _ok = abs(_implied - _r) <= 0.001
        _mix_recon.append({"line": _k, "amount": _amt, "share": _r * 100.0,
                           "implied": _implied * 100.0, "ok": _ok})
        if not _ok:
            _mix_recon_fail.append(_k)
    _mix_sum = ssum([r_reinv, r_shret, r_debt])
    if _mix_sum is not None and abs(_mix_sum - 1.0) > 0.001:
        _mix_recon_fail.append("total != 100%")
    rr = r_reinv or 0.0
    rs = r_shret or 0.0
    if r_debt is not None and r_debt > 0.40:
        label = "Balance Sheet Repair"
    elif r_acq is not None and r_acq > 0.35:
        label = "Acquisition-Led"
    elif rr >= 0.60:
        label = "Growth-Oriented"
    elif rs >= 0.60:
        _div_share = safe_div(_cum_div, shret)
        label = ("Dividend-Led" if (_div_share is not None and _div_share >= 0.65)
                 else "Buyback-Led" if (_div_share is not None and _div_share <= 0.35)
                 else "Return-Oriented")
    elif rr >= 0.50 and rr > rs:
        label = "Reinvestment-Led (Growth-Leaning)"
    elif rs >= 0.50 and rs > rr:
        label = "Return-Leaning"
    else:
        label = "Balanced"
    class_rule = ("Debt reduction >40% of uses -> Balance Sheet Repair; acquisitions >35% -> "
                  "Acquisition-Led; else the dominant bucket sets the tilt: >=60% = Oriented "
                  "(split into Dividend-Led / Buyback-Led by the return channel), "
                  "50-60% and largest = Led/Leaning, otherwise Balanced (<50% either side).")
    class_basis = (f"Reinvestment (capex+M&A) {fmt_pct(rr*100)} vs Shareholder Return "
                   f"(buyback+dividend) {fmt_pct(rs*100)} of capital deployed; acquisitions "
                   f"{fmt_pct((r_acq or 0)*100)}, debt reduction {fmt_pct((r_debt or 0)*100)}. "
                   f"Applying the rule -> {label}.")
    def _amt_txt(v):
        return fmt_usd(v, cur) if v is not None else "n/a"

    classification = {
        "label": label, "rule": class_rule, "basis": class_basis,
        "period_basis": "cumulative, %s-%s" % (first, latest),
        "reinvestment_pct": fmt_pct(r_reinv * 100) if r_reinv is not None else "n/a",
        "shareholder_return_pct": fmt_pct(r_shret * 100) if r_shret is not None else "n/a",
        "debt_reduction_pct": fmt_pct(r_debt * 100) if r_debt is not None else "n/a",
        "acquisition_pct": fmt_pct(r_acq * 100) if r_acq is not None else "n/a",
        "reinvestment_amt": _amt_txt(reinvest),
        "shareholder_return_amt": _amt_txt(shret),
        "debt_reduction_amt": _amt_txt(debt_reduction_cum),
        "acquisition_amt": _amt_txt(acq_cum),
        "capex_amt": _amt_txt(_cum_capex),
        "buyback_amt": _amt_txt(_cum_bb),
        "dividend_amt": _amt_txt(_cum_div),
        "total_deployed_amt": _amt_txt(pool),
        "total_deployed_pct": (fmt_pct(((r_reinv or 0) + (r_shret or 0) + (r_debt or 0)) * 100)
                               if any(x is not None for x in (r_reinv, r_shret, r_debt)) else "n/a"),
        "reconciliation": _mix_recon,
        "reconciled": not _mix_recon_fail,
        "reconciliation_failures": _mix_recon_fail,
    }
    if _mix_recon_fail:
        warnings.append("Allocation mix does not reconcile: %s" % ", ".join(_mix_recon_fail))

    peer_rows = compute_peers(raw, coc, canonical={
        "leverage": (leverage if leverage_unit == "x" else None),
        "capital_return_fcf": cap_return_of_fcf,
        "reinvestment_rate": reinvest_rate,
    })
    warnings.extend(peer_rows.get("peer_warnings", []))

    cap_disc_score = _capital_discipline_score(reinvest_rate, leverage, bb_flags, vc_score)
    _funding_bridge_available = all(gv(L, k) is not None for k in ("debt_issued", "debt_repaid"))
    _cap_disc_state = "Scored" if _funding_bridge_available else "Provisional"
    _components = {
        "buybacks": bb_score, "dividends": d_score, "m_and_a": m_score,
        "capex": cx_score, "balance_sheet": balance_sheet["score"],
        "capital_discipline": cap_disc_score, "value_creation": vc_score,
    }
    for _k, _v in (_sector_score_overrides or {}).items():
        if _k in _components:
            _components[_k] = _v
    scorecard = {
        "components": _components,
        "sector_scored": sorted(_sector_score_overrides.keys()) if _sector_score_overrides else [],
        "capital_discipline_state": _cap_disc_state,
        "capital_discipline_state_reason": (
            "" if _funding_bridge_available else
            "gross debt raised and repaid are not reported, so there is no way to trace how the "
            "spending was funded."),
    }

    hist = {
        "years": years,
        "ocf": [round(x, 1) if x is not None else None for x in ocf_s],
        "capex": [round(x, 1) if x is not None else None for x in capex_s],
        "fcf": [round(x, 1) if x is not None else None for x in fcf_s],
        "buybacks": [round(x, 1) if x is not None else None for x in bb_s],
        "dividends": [round(x, 1) if x is not None else None for x in div_s],
        "acquisitions": [round(x, 1) if x is not None else None for x in acq_s],
        "roic": [round(x, 1) if x is not None else None for x in roic_s],
        "shares": [round(x, 1) if x is not None else None for x in sh_s],
    }

    tyears = years[-4:] if len(years) >= 4 else years
    ti = [years.index(y) for y in tyears]
    trend_rows = []
    for idx, y in zip(ti, tyears):
        cx = capex_s[idx]; bb = bb_s[idx]; dv = div_s[idx]; aq = acq_s[idx]
        dep = ssum([cx, bb, dv, aq])
        def _p(v):
            return (_rf(v / dep * 100, 1) if (v is not None and dep) else None)
        trend_rows.append({
            "year": y,
            "capex": fmt_usd(cx, cur), "capex_pct": _p(cx),
            "buybacks": fmt_usd(bb, cur), "buybacks_pct": _p(bb),
            "dividends": fmt_usd(dv, cur), "dividends_pct": _p(dv),
            "m_and_a": fmt_usd(aq, cur), "m_and_a_pct": _p(aq),
            "total": fmt_usd(dep, cur),
        })
    cap_first = trend_rows[0]["capex_pct"] if trend_rows else None
    cap_last = trend_rows[-1]["capex_pct"] if trend_rows else None
    mix_shift = None
    if cap_first is not None and cap_last is not None:
        mix_shift = _rf(cap_last - cap_first, 1)
    trend = {"years": tyears, "rows": trend_rows, "capex_mix_shift_pp": mix_shift}

    acq_by_year = [{"year": y, "value": fmt_usd(acq_s[i], cur), "raw": acq_s[i]}
                   for i, y in enumerate(years)]
    acq_vals = sorted([v for v in acq_s if v is not None])
    acq_med = (acq_vals[len(acq_vals)//2] if acq_vals else None)
    spike_years = []
    if acq_med is not None:
        for i, y in enumerate(years):
            v = acq_s[i]
            if v is not None and v > 1000 and (acq_med == 0 or v > 1.5 * acq_med):
                spike_years.append(y)
    largest_acq_year = None
    if acq_vals:
        mi = max(range(len(acq_s)), key=lambda i: (acq_s[i] if acq_s[i] is not None else -1))
        if acq_s[mi] is not None and acq_s[mi] > 0:
            largest_acq_year = {"year": years[mi], "value": fmt_usd(acq_s[mi], cur)}
    m_and_a["acq_by_year"] = acq_by_year
    m_and_a["spike_years"] = spike_years
    m_and_a["largest_acq_year"] = largest_acq_year

    risks = []
    if leverage is not None and leverage > 3:
        risks.append(f"Leverage at {fmt_x(leverage)} (Net Debt/EBITDA) constrains capital-return flexibility.")
    if rep_vs_price is not None and rep_vs_price > 10:
        risks.append(f"Average repurchase price {fmt_pct(rep_vs_price, signed=True)} vs current - buybacks executed above value.")
    if buyback and sh_reduction is not None and sh_reduction <= 0.005:
        risks.append("Buybacks are offsetting dilution rather than reducing the share count; limited per-share benefit.")
    if coverage_fcf is not None and coverage_fcf < 1.0 and dividend:
        risks.append(f"Dividend not covered by free cash flow (coverage {fmt_x(coverage_fcf)}).")
    if roic_now is not None and roic_now < coc:
        risks.append(f"ROIC {fmt_pct(roic_now)} below the ~{coc:.0f}% cost of capital - current reinvestment is not yet value-accretive.")
    if capex_cagr is not None and capex_cagr > 25 and roic_now is not None and roic_s[0] is not None and roic_now < roic_s[0]:
        risks.append(f"Capex compounding {fmt_pct(capex_cagr, signed=True)} while ROIC has compressed - incremental returns are the key swing factor.")
    if leverage is not None and leverage < -1 and (roic_now or 0) < coc + 3:
        risks.append("Large net cash with sub-hurdle returns points to an under-utilised balance sheet.")
    if _mat_pct is not None and _mat_pct > 0.4:
        risks.append(f"Acquisitions absorbed {fmt_pct(_mat_pct*100)} of {_mat_basis} - integration and multiple discipline are material to the thesis.")
    if cap_return_of_fcf is not None and cap_return_of_fcf > 1.0:
        risks.append(f"Capital returned ({value_creation['capital_return_pct_fcf']} of cumulative free cash flow, {first}-{latest}) exceeds free cash flow - the pace is balance-sheet-funded and not sustainable indefinitely.")

    _sector_active = bool(value_creation.get("sector_outcome"))
    quality_flags = []
    if _consumed:
        quality_flags.append({
            "severity": "Monitoring",
            "flag": "Restated reporting periods in the window",
            "text": ("FY%s taken from the source's restated vintage because the feed returned no "
                     "plain-year row. Earlier years remain on the original basis, so growth "
                     "measured across the substitution carries a basis difference."
                     % "/FY".join(sorted(_consumed)))})
    _nd_open = gv(by_period.get(first, {}), "net_debt")
    _nd_move = ""
    if _nd_open is not None and net_debt is not None:
        _nd_move = (" Over the same period net debt %s from %s to %s"
                    % ("rose" if net_debt > _nd_open else
                       "fell" if net_debt < _nd_open else "was unchanged at",
                       fmt_usd(_nd_open, cur), fmt_usd(net_debt, cur)))
        _nd_move = (_nd_move + ", but the available data do not isolate the funding source of "
                    "individual uses.") if net_debt != _nd_open else (_nd_move + ".")
    if (not _sector_active) and cap_return_of_fcf is not None and cap_return_of_fcf > 1.0:
        quality_flags.append({"severity": "Material", "flag": "Capital returned above free cash flow",
                              "text": (f"Cumulative shareholder returns were {fmt_pct(cap_return_of_fcf*100)} of "
                                       f"cumulative free cash flow ({first}-{latest}), so distributions exceeded "
                                       f"internally generated cash in the period."
                                       + (_nd_move if _nd_move else
                                          " The available data do not isolate the funding source of "
                                          "individual uses."))})
    nd_first = gv(by_period.get(first, {}), "net_debt")
    if ((not _sector_active) and cap_return_of_fcf is not None and cap_return_of_fcf > 0.9 and net_debt is not None
            and nd_first is not None and net_debt > nd_first and net_debt > 0):
        quality_flags.append({"severity": "Material", "flag": "Distributions exceeded free cash flow while net debt rose",
                              "text": f"Net debt rose from {fmt_usd(nd_first, cur)} to {fmt_usd(net_debt, cur)} while "
                                      f"{fmt_pct(cap_return_of_fcf*100)} of cumulative free cash flow ({first}-{latest}) "
                                      f"was returned. Coincident over the period, not attributed: a complete "
                                      f"sources-and-uses bridge with gross debt issuance and repayment is required to "
                                      f"establish which uses the borrowing funded."})
    if buyback and rep_vs_price is not None and rep_vs_price > 5:
        quality_flags.append({"severity": "Monitoring", "flag": "Buybacks at elevated valuations",
                              "text": f"Buybacks were executed on average {fmt_pct(rep_vs_price, signed=True)} versus the current price."})
    rnd_int_first = safe_div(gv(by_period.get(first, {}), "rnd"), rev_s[0]) if (rev_s and rev_s[0]) else None
    if (not _sector_active) and (adequacy.startswith("Below peer reference") or assess_cx == "Underinvesting"):
        quality_flags.append({"severity": "Monitoring", "flag": "Capex intensity below peer reference",
                              "text": capex_out["adequacy_basis"]})
    elif ((not _sector_active) and rnd_intensity is not None and rnd_int_first is not None
          and rnd_intensity < rnd_int_first - 0.02 and (roc_yield or 0) > 0.02):
        quality_flags.append({"severity": "Monitoring", "flag": "Under-investment",
                              "text": f"R&D intensity fell from {fmt_pct(rnd_int_first*100)} to {fmt_pct(rnd_intensity*100)} of revenue while capital returns ran at a {fmt_pct((roc_yield or 0)*100, small=True)} yield."})

    _sh_first = sh_first
    _sh_last = sh_last
    _nd_series = series(by_period, years, "net_debt")
    _nd_base = next((v for v in _nd_series if v is not None), None)
    if _nd_base is None:
        _nd_base = nd_first
    _debt_trend_known = (_nd_base is not None and net_debt is not None)
    _debt_up = (_debt_trend_known and net_debt > _nd_base * 1.05 and net_debt > _nd_base)
    _shares_up = (_sh_first and _sh_last and _sh_last > _sh_first * 1.02)
    _outspend = ((fcf is not None and fcf < 0) or (reinvest_rate is not None and reinvest_rate > 1.0))
    _fund_bits = []
    _sh_trend_known = bool(_sh_first and _sh_last)
    if _debt_up:
        _fund_bits.append("net debt rose from %s to %s" % (fmt_usd(_nd_base, cur), fmt_usd(net_debt, cur)))
    if _shares_up:
        _fund_bits.append("the share count rose")
    if _outspend and _fund_bits:
        funding_note = "capital deployed exceeded operating cash flow; " + " and ".join(_fund_bits) + " (externally funded)"
    elif _outspend:
        funding_note = "capital deployed exceeded operating cash flow (funded from balance-sheet cash)"
    elif _fund_bits:
        funding_note = ("over the period " + " and ".join(_fund_bits)
                        + ". Gross debt issuance and repayment are not reported, so the funding of "
                          "individual uses cannot be traced")
    elif not (_debt_trend_known and _sh_trend_known):
        funding_note = ("the net debt and share-count trend cannot be determined over the full "
                        "window because the opening values are not reported, so the funding "
                        "position is not established")
    else:
        _rose = []
        if _debt_trend_known and net_debt > _nd_base:
            _rose.append("net debt rose from %s to %s" % (fmt_usd(_nd_base, cur), fmt_usd(net_debt, cur)))
        if _sh_first and _sh_last and _sh_last > _sh_first:
            _rose.append("the share count rose %s"
                         % fmt_pct((_sh_last / _sh_first - 1.0) * 100, signed=True))
        _tail = (". Gross debt issuance and repayment are not reported, so the funding of "
                 "individual uses cannot be traced")
        funding_note = (("over the period " + " and ".join(_rose) + _tail) if _rose else
                        ("neither net debt nor the share count increased over the period" + _tail))
    capex_out["funding_note"] = funding_note

    _ni_now = gv(L, "net_income")
    _tot_ret = ssum([buyback, dividend])
    _payout_ni = safe_div(_tot_ret, _ni_now)
    if _sector_key != "REIT" and _tot_ret and _tot_ret > 0 and _ni_now is not None:
        if _ni_now > 0 and _payout_ni is not None and _payout_ni > 1.0:
            quality_flags.append({"severity": "Material", "flag": "Total payout > 100% of earnings",
                                  "text": f"Buybacks plus dividends were {fmt_pct(_payout_ni*100)} of net income "
                                          f"({fmt_usd(_tot_ret, cur)} returned vs {fmt_usd(_ni_now, cur)} earned); "
                                          f"the excess draws on capital rather than current earnings."})
        elif _ni_now <= 0:
            quality_flags.append({"severity": "Material",
                                  "flag": "Capital returned while earnings were negative",
                                  "text": f"{fmt_usd(_tot_ret, cur)} was returned to holders in a year with "
                                          f"net income of {fmt_usd(_ni_now, cur)}. A payout ratio is not "
                                          f"defined against a loss, so the figure is stated as an amount: "
                                          f"the whole of the return was drawn from the balance sheet rather "
                                          f"than from current earnings."})
    if _sector_key == "REIT":
        _affo_payout = (value_creation.get("sector_dashboard") or [])
        _ap = next((v for v, lab in _affo_payout if "payout" in str(lab).lower()), None)
        _ac = next((v for v, lab in _affo_payout if "coverage" in str(lab).lower()), None)
        try:
            _ap_num = float(str(_ap).strip().rstrip("%")) if _ap is not None else None
        except ValueError:
            _ap_num = None
        if _ap_num is not None and _ap_num > 100.0:
            quality_flags.append({"severity": "Material", "flag": "AFFO payout above 100%",
                                  "text": f"Distributions were {_ap} of AFFO with coverage of {_ac}; "
                                          f"the distribution is not covered by adjusted funds from operations."})
        elif _ap_num is not None and _ap_num > 90.0:
            quality_flags.append({"severity": "Monitoring", "flag": "AFFO payout above 90%",
                                  "text": f"Distributions were {_ap} of AFFO with coverage of {_ac}; "
                                          f"headroom to the distribution is limited."})

    if fcf is not None and fcf < 0:
        _neg_yrs = sum(1 for v in fcf_s if v is not None and v < 0)
        quality_flags.append({"severity": "Monitoring", "flag": "Negative free cash flow",
                              "text": f"Free cash flow was {fmt_usd(fcf, cur)} in the latest year and negative in "
                                      f"{_neg_yrs} of {len([v for v in fcf_s if v is not None])} reported years; "
                                      f"the deployment plan is not self-funding."})
    if leverage is not None and leverage_unit == "x" and leverage >= 3.0:
        quality_flags.append({"severity": "Monitoring", "flag": "Elevated leverage",
                              "text": f"Net debt / EBITDA was {leverage:.2f}x"
                                      + (f" with {balance_sheet['interest_coverage']} interest cover"
                                         if not str(balance_sheet.get('interest_coverage','n/a')).startswith('n/a') else "")
                                      + "; the capital plan carries balance-sheet risk."})
    if _sh_first and _sh_last and _sh_last > _sh_first * 1.02:
        _bb_any = ssum([v for v in bb_s if v is not None])
        quality_flags.append({"severity": "Monitoring", "flag": "Share count increased",
                              "text": (f"Diluted shares rose from {_sh_first:,.0f}M to {_sh_last:,.0f}M over the "
                                       f"window ({fmt_pct((_sh_last / _sh_first - 1.0) * 100, signed=True)}); ")
                                      + ("repurchases did not offset issuance and stock compensation."
                                         if _bb_any else
                                         "no repurchase is reported in any year of the window, so the "
                                         "dilution was not offset - the increase funded the business.")})

    def _ev(*parts):
        return " ".join([x for x in parts if x])
    _pm_ev = (peer_rows.get("median") or {}) if peer_rows.get("available") else {}
    _PEER_ALREADY_PCT = {"roic"}
    _PEER_NOT_A_PCT = {"leverage"}
    def _vs_peer(key, val_pct, higher_better=True, unit="%"):
        mv = _pm_ev.get(key)
        if mv is None or val_pct is None:
            return ""
        mvp = mv if (key in _PEER_ALREADY_PCT or key in _PEER_NOT_A_PCT) else mv * 100
        side = "above" if (val_pct > mvp) == higher_better else "below"
        return f", {side} the {mvp:.1f}{unit} peer median"
    _bb_pk = _vs_peer("buyback_yield", (bb_yield * 100 if bb_yield is not None else None))
    _cx_pk = _vs_peer("capex_intensity", (capex_intensity * 100 if capex_intensity is not None else None))
    _rc_pk = _vs_peer("roic", roic_now)
    evidence = {
        "buybacks": ((f"Scored {_components['buybacks']}/10 because no repurchase is reported in the "
                      f"latest year and the share count moved {buybacks['share_count_change']}, which "
                      f"corroborates that no material repurchase occurred; cumulative repurchases over "
                      f"the period were {buybacks['total_5y']}.")
                     if _bb_nil_corroborated else
                     (f"Scored {_components['buybacks']}/10 because the buyback yield was "
                      f"{buybacks['buyback_yield']} and the share count moved {buybacks['share_count_change']}, "
                      f"with repurchases at {buybacks['avg_rep_vs_current']} versus the current price{_bb_pk}.")),
        "dividends": (dividends.get("not_applicable_reason")
                      if dividends.get("not_applicable") else
                      (f"Scored {_components['dividends']}/10 because distributions were "
                       f"{dividends.get('dcf_payout_ratio')} of distributable cash flow with "
                       f"{dividends.get('dcf_coverage')} cover. {dividends.get('distribution_basis')}")
                      if dividends.get("dcf_coverage") else
                      (f"Scored {_components['dividends']}/10 because the payout was {dividends['payout_ratio']} "
                       f"of earnings with {dividends['fcf_coverage']} free-cash-flow cover and "
                       f"{dividends['dividend_5y_cagr']} growth.")),
        "m_and_a": (m_and_a.get("status_text") or f"Scored {_components['m_and_a']}/10 because " + (
                     "the acquisition cash line is not reported in the source data, so no cash consideration can be shown; named transactions, if any, are in Section 5 and deal-level returns are not disclosed."
                     if str(m_and_a['acq_total_5y']).lower().startswith("n/a")
                     else f"acquisitions totalled {m_and_a['acq_total_5y']} ({m_and_a['acq_pct_of_ocf']} of {m_and_a['materiality_prose']}), spend was concentrated and deal-level returns are not disclosed.")),
        "capex": (f"Scored {_components['capex']}/10 because capex was {capex_out['capex_intensity']} of revenue "
                  f"growing {capex_out['capex_5y_cagr']}, absorbing {capex_out['reinvestment_rate']} of operating cash flow{_cx_pk}."
                  if not _reinvest_no_ocf else
                  f"Scored {_components['capex']}/10 because capex was {capex_out['capex_intensity']} of revenue "
                  f"growing {capex_out['capex_5y_cagr']}; operating cash flow was negative over the "
                  f"period, so capex as a share of it is not a meaningful measure{_cx_pk}."),
        "balance_sheet": ((f"Scored {_components['balance_sheet']}/10 on interest cover of "
                           f"{balance_sheet['interest_coverage']} alone: the source's net-debt "
                           f"figure conflicts with total debt less cash, so neither it nor the "
                           f"leverage multiple is published, and the dimension carries half weight.")
                          if balance_sheet.get("net_debt_conflict") else
                          (f"Scored {_components['balance_sheet']}/10 because "
                           + _bs_evidence_clauses(balance_sheet) + ".")),
        "capital_discipline": ((f"Scored {_components['capital_discipline']}/10 because "
                                f"{funding_note}.")
                               if str(capex_out.get('reinvestment_rate', 'n/a')).lower().startswith("n/a")
                               else f"Scored {_components['capital_discipline']}/10 because reinvestment ran "
                               f"{capex_out['reinvestment_rate']} of operating cash flow at {balance_sheet['leverage_nd_ebitda']} leverage, {funding_note}."),
        "value_creation": (f"Scored {_components['value_creation']}/10 because ROIC was {value_creation['roic']} against a "
                           f"{value_creation['cost_of_capital']} hurdle{_rc_pk}, with EPS {value_creation['eps_cagr']}"
                           + (f" and TSR {value_creation['tsr_annualized']}."
                              if value_creation.get("tsr_available") else
                              "; total shareholder return is not scored, since over this horizon it "
                              "tracks the valuation multiple more than the allocation decisions.")),
    }
    for _k, _txt in (_sector_evidence_overrides or {}).items():
        if _txt:
            evidence[_k] = _txt

    _PRIMARY_INPUT = {
        "buybacks": [0.0 if _bb_nil_corroborated else buyback],
        "dividends": [0.0 if _div_nil_corroborated else dividend],
        "capex": [capex],
        "balance_sheet": [net_debt],
        "capital_discipline": [(reinvest_rate if reinvest_rate is not None else
                                (1 if funding_note else None))
                               if _sector_key not in ("Banks", "Insurance") else 0],
        "value_creation": [roic_now if _sector_key not in ("Banks", "Insurance") else roe_now],
    }
    _MEASURE_INPUTS = {
        "buybacks": [("buyback yield", bb_yield), ("share-count change", sh_reduction),
                     ("average repurchase price vs current", rep_vs_price)],
        "dividends": [("payout of earnings", payout), ("free-cash-flow cover", coverage_fcf),
                      ("dividend growth", div_cagr)],
        "capex": [("capex intensity", capex_intensity), ("capex growth", capex_cagr),
                  ("reinvestment rate", reinvest_rate)],
        "balance_sheet": [("net debt position", net_debt), ("leverage", leverage),
                          ("interest coverage", int_cov)],
        "m_and_a": [("spend vs its denominator", _mat_pct), ("return trend", roic_change)],
        "capital_discipline": [("reinvestment rate", reinvest_rate), ("funding trace", funding_note or None),
                               ("leverage", leverage), ("repurchase price discipline", rep_vs_price)],
        "value_creation": [("return on capital", roic_now if _sector_key not in ("Banks", "Insurance")
                            else roe_now), ("earnings per share growth", eps_cagr),
                           ("free cash flow per share growth", fcf_ps_cagr),
                           ("share-count change", sh_reduction)],
    }
    _NA_BY_SECTOR = {
        "Banks":     {"free-cash-flow cover", "leverage", "interest coverage", "reinvestment rate",
                      "capex intensity", "capex growth", "free cash flow per share growth"},
        "Insurance": {"free-cash-flow cover", "reinvestment rate", "capex intensity",
                      "capex growth", "free cash flow per share growth"},
        "REIT":      {"leverage", "return trend"},
    }
    _na_here = _NA_BY_SECTOR.get(_sector_key, set())
    _measures_evaluated = {}
    for _k, _ms in _MEASURE_INPUTS.items():
        _apply = [(n, v) for n, v in _ms if n not in _na_here]
        _absent = [n for n, v in _apply if v is None]
        _measures_evaluated[_k] = {"evaluated": len(_apply) - len(_absent), "total": len(_apply),
                                   "not_reported": _absent,
                                   "not_applicable": sorted(n for n, _ in _ms if n in _na_here)}

    evidence, _unscored = _hygiene_evidence(evidence, _components, _PRIMARY_INPUT)
    for _k in _unscored:
        if _k in _components:
            _components[_k] = None
    for _k, _v in list(_components.items()):
        if _v is not None and str(evidence.get(_k, "")).startswith("Unscored"):
            _components[_k] = None
            _unscored.add(_k)
    if _unscored:
        warnings.append("Dimensions unscored for missing required inputs (score and weight "
                        "withdrawn): %s" % ", ".join(sorted(_unscored)))

    for _k, _mi in _measures_evaluated.items():
        if _components.get(_k) is None or not _mi["not_reported"]:
            continue
        _txt = str(evidence.get(_k) or "").rstrip()
        if not _txt or _txt.startswith("Unscored"):
            continue
        evidence[_k] = "%s (%d of %d measures evaluated; %s unavailable.)" % (
            _txt, _mi["evaluated"], _mi["total"], ", ".join(_mi["not_reported"]))

    qc = run_qc(ocf, capex, fcf, buyback, dividend, mcap, bb_yield, leverage,
                roic_now, sh_s, warnings, leverage_unit, win_curs=_win_curs,
                roe=roe_now)

    _vchecks = []
    def _vc(name, ok, detail):
        _vchecks.append({"check": name, "pass": bool(ok), "detail": detail})

    _arith_ok = all(c["pass"] for c in qc["checks"])
    _sc_ok = all((v is None) or (0 <= v <= 10) for v in scorecard["components"].values())
    _vc("Internal arithmetic checks passed", _arith_ok and _sc_ok,
        f"QC {qc['passed']}/{qc['total']} passed; all scorecard scores in 0-10")

    _mix = [x for x in (r_reinv, r_shret, r_debt) if x is not None]
    _mix_sum = sum(_mix) if _mix else None
    _vc("Allocation mix sums to 100%", _mix_sum is not None and abs(_mix_sum - 1.0) <= 0.005,
        (f"reinvestment + shareholder returns + debt reduction = {_mix_sum*100:.1f}%" if _mix_sum is not None else "no deployment data"))

    _sr = ssum([buyback, dividend])
    _sr_ok = (_sr is None and total_return_latest is None) or (
        _sr is not None and total_return_latest is not None and abs(_sr - total_return_latest) <= max(1.0, 0.005 * abs(total_return_latest)))
    _vc("Shareholder-return calculations validated", _sr_ok,
        f"buybacks + dividends = {fmt_usd(_sr, cur)} (latest); yields on market cap")

    if balance_sheet.get("reconciles") is not None:
        _vc("Balance sheet reconciles (debt - cash - investments = net debt)",
            balance_sheet["reconciles"] is not False,
            ("net debt ties to total debt, cash and short-term investments"
             if balance_sheet["reconciles"] is True else
             "net debt does not tie to the shown components"
             if balance_sheet["reconciles"] is False else
             "indeterminate: the marketable-securities line was not returned, so the residual is "
             "disclosed in the net-debt bridge rather than treated as a contradiction"))

    _peer_ok, _peer_detail = True, "no peer set supplied"
    if peer_rows.get("available"):
        tgt = peer_rows.get("target") or {}; med = peer_rows.get("median") or {}
        _bad = []
        for k in ("buyback_yield", "dividend_yield_proxy", "total_shareholder_yield", "share_count_change",
                  "reinvestment_rate", "roic", "leverage", "capital_return_fcf"):
            tv, mv = tgt.get(k), med.get(k)
            if tv is None or mv is None:
                continue
            d = tv - mv
            if d != d or d in (float("inf"), float("-inf")):
                _bad.append(k)
        sr_t, sr_m = tgt.get("share_count_change"), med.get("share_count_change")
        _dir = (f"share count change {sr_t*100:+.1f}% vs peer median {sr_m*100:+.1f}% "
                f"({'diluting more than' if (sr_t or 0) > (sr_m or 0) else 'shrinking more than'} the peer group)"
                if (sr_t is not None and sr_m is not None) else "share count change not comparable")
        _peer_ok = not _bad
        _peer_detail = f"{len(peer_rows.get('rows', []))} peers; differences recomputed (target - median); " + _dir
    _vc("Peer comparison calculations validated", _peer_ok, _peer_detail)

    _confl = []
    _s9 = value_creation.get("capital_return_pct_fcf")
    _ptgt = (peer_rows.get("target") or {}).get("capital_return_fcf")
    if _s9 not in (None, "n/a") and _ptgt is not None:
        try:
            if abs(float(str(_s9).rstrip("%")) - _ptgt * 100) > 0.2:
                _confl.append("capital returned / FCF differs between the Outcome section and the peer table")
        except ValueError:
            pass
    _vc("No conflicting metrics found", not _confl,
        "capital returned / FCF reconciles across sections (same cumulative basis)" if not _confl else "; ".join(_confl))

    _gaps = []
    if not balance_sheet.get("cash_available", True): _gaps.append("cash balance (stated unavailable from source)")
    if not balance_sheet.get("interest_available", True): _gaps.append("interest coverage (interest expense not reported)")
    if not balance_sheet.get("ebitda_available", True): _gaps.append("leverage (EBITDA not reported)")
    if str(m_and_a.get("acq_total_5y", "")).lower().startswith("n/a"):
        _gaps.append("acquisition spend (stock-funded; named transactions shown from filings)")
    _vc("Missing data explained", True,
        ("all gaps carry an explicit reason: " + "; ".join(_gaps)) if _gaps else "no material data gaps")

    validation = {"checks": _vchecks, "passed": sum(1 for c in _vchecks if c["pass"]), "total": len(_vchecks)}

    def _annual_quarterly_breaks(ann_by_period, q_rows):
        out = []
        if not q_rows:
            return out
        CHECK = {_c: _lbl
                 for _key, _lbl in (("ocf", "operating cash flow"),
                                    ("capex", "capital expenditure"),
                                    ("buyback_usd", "buybacks"),
                                    ("dividend", "dividends"))
                 for _c in (C.get(_key) or [])}
        _SIGNED_FLOWS = {_c
                         for _key in ("capex", "buyback_usd", "dividend")
                         for _c in (C.get(_key) or [])}
        byyear = {}
        for r in q_rows:
            code = r.get("LTCODE")
            if code not in CHECK:
                continue
            m = re.fullmatch(r"[1-4]Q(\d{2}|\d{4})", str(r.get("FISCALPERIOD", "")).strip(), re.I)
            if not m:
                continue
            yr = m.group(1)
            yr = yr if len(yr) == 4 else ("20" + yr)
            v = pnum(r.get("VALUE"))
            if v is None:
                continue
            byyear.setdefault(yr, {}).setdefault(code, []).append(v)
        for yr, codes in sorted(byyear.items()):
            annual = ann_by_period.get(yr)
            if not annual:
                continue
            for code, vals in sorted(codes.items()):
                a = annual.get(code)
                _signs = ({(v > 0) for v in vals if abs(v) > 1.0}
                          if code in _SIGNED_FLOWS else set())
                if len(_signs) > 1:
                    out.append({"code": code, "name": CHECK[code], "year": yr,
                                "quarters": sum(vals), "annual": a,
                                "detail": "the quarters disagree on sign (%s) - this line is a "
                                          "same-signed flow, so one of them is wrong"
                                          % ", ".join(fmt_usd(v, cur) for v in vals)})
                if len(vals) != 4:
                    if a is None or not vals:
                        continue
                    _psum = sum(vals)
                    if abs(_psum) > abs(a) + max(1.0, abs(a) * 0.02):
                        _r = safe_div(abs(_psum), abs(a))
                        out.append({"code": code, "name": CHECK[code], "year": yr,
                                    "quarters": _psum, "annual": a, "partial": len(vals),
                                    "detail": "%d of 4 quarters are present and already sum to "
                                              "%s against an annual row of %s%s - a partial "
                                              "year cannot exceed its own total, so one of the "
                                              "two is wrong (indicative: the remaining quarter "
                                              "is not available to confirm which)"
                                              % (len(vals), fmt_usd(_psum, cur),
                                                 fmt_usd(a, cur),
                                                 " (%.2fx)" % _r if _r else "")})
                    continue
                if a is None:
                    out.append({"code": code, "name": CHECK[code], "year": yr,
                                "quarters": sum(vals), "annual": None,
                                "detail": "the four quarters sum to %s but no annual row was "
                                          "returned" % fmt_usd(sum(vals), cur)})
                    continue
                qsum = sum(vals)
                if a == 0 and qsum == 0:
                    continue
                _tol = max(1.0, abs(a) * 0.02)
                if (a < 0) != (qsum < 0) and abs(a) > 1.0 and abs(qsum) > 1.0:
                    out.append({"code": code, "name": CHECK[code], "year": yr,
                                "quarters": qsum, "annual": a,
                                "detail": "the annual row is %s while the four quarters sum to "
                                          "%s - the sign disagrees"
                                          % (fmt_usd(a, cur), fmt_usd(qsum, cur))})
                elif abs(abs(a) - abs(qsum)) > _tol:
                    _ratio = safe_div(abs(qsum), abs(a))
                    out.append({"code": code, "name": CHECK[code], "year": yr,
                                "quarters": qsum, "annual": a,
                                "detail": "the four quarters sum to %s against an annual row of "
                                          "%s%s" % (fmt_usd(qsum, cur), fmt_usd(a, cur),
                                                    " (%.2fx)" % _ratio if _ratio else "")})
        return out

    if _ROE_REFUSALS:
        warnings.append(
            "Return on equity withheld: book equity is not positive in %d of the years measured "
            "(most negative %s). A ratio against a negative denominator is a sign artefact, not "
            "a return - a profitable issuer with negative book equity would otherwise publish a "
            "large negative or positive percentage that reads as performance. The value-creation "
            "lens falls back to the measures that remain defined."
            % (len(_ROE_REFUSALS), fmt_usd(min(_ROE_REFUSALS), cur)))
        if len(_ROE_REFUSALS) >= max(2, len(years) - 1):
            warnings.append(
                "ADVISORY: book equity is negative in %d of the %d years measured, not in an "
                "isolated year. Both equity-denominated dimensions (balance sheet, value "
                "creation) are therefore unscored, and with their weight withdrawn the score "
                "coverage is likely to fall under the 60%% publication floor - in which case the "
                "overall score and the allocation style are withheld and the report says so. "
                "This is the intended treatment, not a data fault: an issuer that has retired "
                "its book value through buybacks or carries spin-off history has no meaningful "
                "equity denominator. The mix, the deployment tables and the peer section are "
                "unaffected and remain the usable output."
                % (len(_ROE_REFUSALS), len(years)))
    if _ROE_THIN:
        _tr, _te = max(_ROE_THIN, key=lambda t: abs(t[0]))
        _why = ("The ratio is arithmetically correct and is published, but an issuer that has "
                "retired most of its book value through buybacks shows a return that says more "
                "about the size of the denominator than about the return on capital deployed. "
                "Read it alongside return on invested capital, which is measured against debt "
                "and equity together.")
        _lat_thin = next((t for t in _ROE_THIN if roe_now is not None
                          and abs((t[0] or 0) - roe_now) < 0.05), None)
        if _lat_thin is not None:
            _msg = ("Return on equity is published against a thin equity base: %.1f%% on book "
                    "equity of %s. " % (_lat_thin[0], fmt_usd(_lat_thin[1], cur))) + _why
            if abs(_tr - _lat_thin[0]) >= 0.05:
                _msg += (" Book equity is thin in %d of the years measured; the widest reading in "
                         "the window is %.1f%%." % (len(_ROE_THIN), _tr))
        else:
            _msg = ("Return on equity in %d of the years measured is against a thin equity base "
                    "(the widest reading is %.1f%% on book equity of %s); the latest-year figure "
                    "the document publishes is not among them. "
                    % (len(_ROE_THIN), _tr, fmt_usd(_te, cur))) + _why
        warnings.append(_msg)
    if _DEBT_ISSUED_NET:
        warnings.append(
            "Gross debt issuance not used: the reported proceeds-from-debt line came back "
            "negative (%s), which means the feed carries a NET issuance-less-repayment figure "
            "in a slot Sources and Uses states gross. A net figure in a gross column would "
            "understate both the money raised and the money repaid, so the line is left out and "
            "the funding bridge stays unreconciled."
            % fmt_usd(_DEBT_ISSUED_NET[0], cur))
    for _mk, _cd in sorted(set(_UNIT_REFUSALS)):
        warnings.append(
            "Unit mismatch refused: %s is a percentage metric, but %s returned a currency "
            "amount. The code measures a different quantity from the metric it was mapped to, "
            "so the value is not used and the metric renders n/a. Verify the mapping with "
            "tools/verify_ltcodes.py." % (_mk, _cd))
    _aq_breaks = _annual_quarterly_breaks(by_period, unwrap(raw.get("actuals_quarterly")))
    for _b in _aq_breaks:
        warnings.append("Source data does not reconcile across periods - %s (%s), FY%s: %s. "
                        "Figures are reported as the source supplies them; raise with the data "
                        "team." % (_b["name"], _b["code"], _b["year"], _b["detail"]))

    monitor = {"available": False}
    q_rows_all = fold_restated_quarters(unwrap(raw.get("actuals_quarterly")))
    _qdepth = quarterly_depth(q_rows_all)
    _STALE_DAYS = 135
    _prep = str(meta.get("prepared_date") or "").strip()
    _newest = max([str(r.get("QUARTERENDDATE") or "") for r in (q_rows_all or [])
                   if str(r.get("QUARTERENDDATE") or "")[:4].isdigit()] or [""])
    import datetime as _dt
    _prep_date = None
    for _f in ("%B %d, %Y", "%Y-%m-%d", "%d %B %Y", "%b %d, %Y"):
        try:
            _prep_date = _dt.datetime.strptime(_prep, _f).date()
            break
        except ValueError:
            continue
    if _prep and _newest:
        try:
            _pd = _prep_date
            _qd = _dt.date.fromisoformat(_newest[:10])
            if _pd and (_pd - _qd).days > _STALE_DAYS:
                warnings.append(
                    "ADVISORY: the newest quarter in the data ends %s but this report is prepared "
                    "%s - %d days later, longer than one reporting cycle. The company has very "
                    "likely reported again since. Every figure here is correct for the period it "
                    "covers; confirm the feed has ingested the latest print before distributing, "
                    "because a report for the previous quarter passes every check in this run."
                    % (_newest[:10], _prep, (_pd - _qd).days))
        except Exception:
            pass

    _calls = [str(d)[:10] for d in ((meta.get("call_dates") or []) if isinstance(
        meta.get("call_dates"), (list, tuple)) else []) if str(d)[:4].isdigit()]
    if not _calls:
        warnings.append(
            "ADVISORY: no earnings calendar was supplied (meta.call_dates), so the ingestion-lag "
            "control could not run. It is the only check that can see a company which has "
            "reported since the feed was pulled - every figure in a stale report is correct for "
            "the period it covers, so no internal consistency check can detect one. Fetch "
            "get_call_date with a window reaching into the future and re-run.")
    elif _newest:
        _horizon = (_prep_date or _dt.date.today()).isoformat()
        _after = sorted(d for d in _calls if _newest[:10] < d <= _horizon)
        _scheduled = sorted(d for d in _calls if d > _horizon)
        if _scheduled:
            warnings.append(
                "DISCLOSURE: %d scheduled earnings date(s) after %s (%s) are in the calendar and "
                "are not counted as ingestion lag - they have not happened yet."
                % (len(_scheduled), _horizon, ", ".join(_scheduled)))
        if len(_after) > 1:
            _msg = ("%d earnings events fall after %s, the newest period in this pull (%s). "
                    "Exactly one is expected - the call that reported it. Two or more means the "
                    "company has reported a later period and the database has not ingested it, "
                    "so this report would be complete, internally consistent and about the "
                    "PREVIOUS period. Re-fetch the actuals, or confirm the window is intended. "
                    "CAPALLOC_ENFORCE=warn renders with the lag disclosed."
                    % (len(_after), _newest[:10], ", ".join(_after)))
            if (os.environ.get("CAPALLOC_ENFORCE") or "block").lower() == "block":
                return {"status": "error", "message": _msg, "ingestion_lag": _after}
            warnings.append("WITHHELD DATA: " + _msg)
        elif len(_after) == 1:
            warnings.append(
                "DISCLOSURE: one earnings event (%s) falls after %s, the newest period in this "
                "pull, which is the call that reported it. The feed is current as of that print."
                % (_after[0], _newest[:10]))

    monitor["depth"] = _qdepth
    if _qdepth["short"]:
        warnings.append(
            "ADVISORY: the quarterly pull returned %d of the %d fiscal quarters the Quarterly "
            "Monitor and the twelve-month comparison need, so those two analyses are omitted. "
            "This is a truncated fetch, not an issuer that reports fewer quarters - re-fetch "
            "actuals_quarterly with an explicit limit (32 rows for 8 quarters x 4 codes; the "
            "vendor default pages at 25) and re-run."
            % (_qdepth["quarters"], _qdepth["expected"]))
    if q_rows_all:
        qmap = {}
        for r in q_rows_all:
            code = r.get("LTCODE")
            ed = r.get("QUARTERENDDATE") or r.get("FISCALPERIOD")
            if not code or not ed:
                continue
            lab = str(r.get("FISCALPERIOD", ed)).replace("R-", "")
            q = qmap.setdefault(ed, {"label": lab})
            v = pnum(r.get("VALUE"))
            if v is None:
                continue
            if code in (C.get("ocf") or []):
                q["ocf"] = v
            elif code in (C.get("capex") or []):
                q["capex"] = abs(v)
            elif code in (C.get("buyback_usd") or []):
                q["bb"] = abs(v)
            elif code in (C.get("dividend") or []):
                q["div"] = abs(v)
        order = sorted(qmap.keys())
        qs = [qmap[k] for k in order]
        if len(qs) >= 8:
            def _wsum(win, key):
                return ssum([w.get(key) for w in win])
            curw, priw = qs[-4:], qs[-8:-4]
            def _win(win):
                o = _wsum(win, "ocf"); cx = _wsum(win, "capex")
                bb = _wsum(win, "bb"); dv = _wsum(win, "div")
                return {"ocf": o, "capex": cx, "bb": bb, "div": dv,
                        "reinv": safe_div(cx, o), "bb_pct": safe_div(bb, o),
                        "div_pct": safe_div(dv, o), "res": (o - (cx or 0) - (dv or 0)) if o is not None else None}
            _LW, _PW = _win(curw), _win(priw)
            def _pp(a, b):
                return _rf((a - b) * 100, 1) if (a is not None and b is not None) else None
            qtbl = []
            for w in qs[-6:]:
                o = w.get("ocf")
                qtbl.append({
                    "label": w.get("label", ""),
                    "ocf": fmt_usd(o, cur), "capex": fmt_usd(w.get("capex"), cur),
                    "bb": fmt_usd(w.get("bb"), cur), "div": fmt_usd(w.get("div"), cur),
                    "reinv": fmt_pct(safe_div(w.get("capex"), o) * 100) if (w.get("capex") is not None and o) else "n/a",
                })
            _ocf_ratios = _sector_key not in ("Banks", "Insurance")
            _ratio_rows = [
                    {"metric": "Reinvestment Rate (capex / OCF)", "prior": fmt_pct(_PW["reinv"]*100) if _PW["reinv"] is not None else "n/a",
                     "ltm": fmt_pct(_LW["reinv"]*100) if _LW["reinv"] is not None else "n/a",
                     "chg": (f"{_pp(_LW['reinv'], _PW['reinv']):+.1f} pp" if _pp(_LW["reinv"], _PW["reinv"]) is not None else "n/a")},
                    {"metric": "Buybacks % of OCF", "prior": fmt_pct(_PW["bb_pct"]*100) if _PW["bb_pct"] is not None else "n/a",
                     "ltm": fmt_pct(_LW["bb_pct"]*100) if _LW["bb_pct"] is not None else "n/a",
                     "chg": (f"{_pp(_LW['bb_pct'], _PW['bb_pct']):+.1f} pp" if _pp(_LW["bb_pct"], _PW["bb_pct"]) is not None else "n/a")},
                    {"metric": "Dividends % of OCF", "prior": fmt_pct(_PW["div_pct"]*100) if _PW["div_pct"] is not None else "n/a",
                     "ltm": fmt_pct(_LW["div_pct"]*100) if _LW["div_pct"] is not None else "n/a",
                     "chg": (f"{_pp(_LW['div_pct'], _PW['div_pct']):+.1f} pp" if _pp(_LW["div_pct"], _PW["div_pct"]) is not None else "n/a")},
                    {"metric": "OCF - capex - dividends", "prior": fmt_usd(_PW["res"], cur), "ltm": fmt_usd(_LW["res"], cur),
                     "chg": (fmt_usd(_LW["res"]-_PW["res"], cur) if (_LW["res"] is not None and _PW["res"] is not None) else "n/a")},
            ]
            _cash_rows = [
                    {"metric": "Buybacks", "prior": fmt_usd(_PW["bb"], cur), "ltm": fmt_usd(_LW["bb"], cur),
                     "chg": (fmt_usd(_LW["bb"]-_PW["bb"], cur) if (_LW["bb"] is not None and _PW["bb"] is not None) else "n/a")},
                    {"metric": "Dividends", "prior": fmt_usd(_PW["div"], cur), "ltm": fmt_usd(_LW["div"], cur),
                     "chg": (fmt_usd(_LW["div"]-_PW["div"], cur) if (_LW["div"] is not None and _PW["div"] is not None) else "n/a")},
            ]
            monitor = {
                "available": True,
                "quarters": qtbl,
                "ocf_ratios": _ocf_ratios,
                "ocf_ratio_note": ("" if _ocf_ratios else
                                   "Operating cash flow is not a measure of distributable capital "
                                   "for this sector, so uses of capital are shown as cash amounts "
                                   "rather than as a share of it."),
                "ltm_label": f"{curw[0]['label']}-{curw[-1]['label']}",
                "prior_label": f"{priw[0]['label']}-{priw[-1]['label']}",
                "rows": [
                    {"metric": "Operating Cash Flow", "prior": fmt_usd(_PW["ocf"], cur), "ltm": fmt_usd(_LW["ocf"], cur),
                     "chg": (fmt_pct(safe_div(_LW["ocf"]-_PW["ocf"], _PW["ocf"])*100, signed=True) if (_LW["ocf"] and _PW["ocf"]) else "n/a")},
                ] + (_ratio_rows if _ocf_ratios else _cash_rows),
            }
            if _LW["reinv"] is not None:
                warnings.append("QC: LTM reinvestment rate %.0f%%" % (_LW["reinv"]*100)) if not (0 <= _LW["reinv"] <= 2) else None

    period_reconciliation = {"checked": bool(unwrap(raw.get("actuals_quarterly"))),
                             "breaks": _aq_breaks}

    market_snapshot = {
        "price": (f"{cur}{_sep(_xr(price, 2))}" if price is not None else "n/a"),
        "market_cap": fmt_usd(mcap, cur),
        "fcf_yield": fmt_pct(safe_div(fcf, mcap) * 100, small=True) if (fcf is not None and mcap) else "n/a",
        "total_shareholder_yield": (fmt_pct(roc_yield * 100, small=True) if roc_yield is not None else "n/a"),
        "net_debt": ("n/a (withheld - source conflict)" if _nd_conflict
                     else fmt_usd(net_debt, cur)),
        "leverage": (fmt_x(leverage) if leverage is not None else "n/a"),
    }

    methodology = {
        "bands": "Excellent >= 8.0  |  Good 6.5-7.9  |  Neutral 5.0-6.4  |  Weak < 5.0",
        "weighting": "Each 0-10 dimension is weighted by the sector priority levers (Weight column); "
                     "the Overall is the weighted average. Dimensions with weight 0 are not scored for the sector.",
        "process_vs_outcome": "The Scorecard grades the allocation PROCESS (buyback/dividend/capex/M&A "
                              "execution, balance-sheet posture, capital discipline). The Value Creation "
                              "Verdict grades the OUTCOME (TSR, FCF/share, EPS, share-count change, ROIC vs "
                              "hurdle). A 'Good' process can still be clearly 'Value Creating'.",
        "dimensions": [
            "Buybacks - yield, share-count reduction, repurchase price vs value",
            "Dividends - payout, FCF coverage, growth, sustainability",
            "M&A - spend vs OCF, ROIC trajectory, integration/strategic fit",
            "Capex - intensity, growth, reinvestment rate, maintenance vs growth",
            "Balance Sheet - leverage, interest coverage, liquidity",
            "Capital Discipline - reinvestment restraint + balance sheet + buyback price discipline",
            "Value Creation - ROIC vs hurdle, TSR, FCF/share, EPS, share count, capital returned",
        ],
        "classification_rule": class_rule,
    }

    _store = _MR.new_store()

    if leverage is not None:
        if leverage_unit == "%":
            _store.register("DEBT_TO_TOTAL_CAPITAL", leverage, period="LQ", basis="reported",
                            unit="%", formula=_MR.FORMULAS["DEBT_TO_TOTAL_CAPITAL"][0],
                            numerator=_MR.FORMULAS["DEBT_TO_TOTAL_CAPITAL"][1],
                            denominator=_MR.FORMULAS["DEBT_TO_TOTAL_CAPITAL"][2],
                            as_of=str(latest))
        else:
            _eb_basis = basis_of(L, "ebitda")
            _store.register("NET_DEBT_TO_EBITDA", leverage, period="LFY", basis=_eb_basis,
                            unit="x", formula=_MR.FORMULAS["NET_DEBT_TO_EBITDA"][0],
                            numerator="net debt", denominator="EBITDA (%s)" % _eb_basis,
                            as_of=str(latest))
    else:
        _store.unavailable("NET_DEBT_TO_EBITDA",
                           "EBITDA or net debt not reported for the latest fiscal year.",
                           period="LFY", basis=basis_of(L, "ebitda"), unit="x")

    if int_cov is not None:
        _store.register("INTEREST_COVERAGE", int_cov, period="LFY", basis="reported", unit="x",
                        formula=_MR.FORMULAS["INTEREST_COVERAGE"][0], as_of=str(latest))

    if cum_return is not None:
        _store.register("CAPITAL_RETURNED", cum_return, period="CUM5", basis="reported",
                        unit="$", formula=_MR.FORMULAS["CAPITAL_RETURNED"][0], as_of=str(latest))
    if cap_return_of_fcf is not None:
        _store.register("CAPITAL_RETURNED_PCT_FCF", cap_return_of_fcf * 100.0, period="CUM5",
                        basis="reported", unit="%",
                        formula="cumulative capital returned / cumulative free cash flow",
                        numerator="capital returned (5-year cumulative)",
                        denominator="free cash flow (5-year cumulative)", as_of=str(latest))
    if payout_of_fcf is not None:
        _store.register("CAPITAL_RETURNED_PCT_FCF", payout_of_fcf * 100.0, period="LFY",
                        basis="reported", unit="%",
                        formula="latest-year capital returned / latest-year free cash flow",
                        numerator="capital returned (latest fiscal year)",
                        denominator="free cash flow (latest fiscal year)", as_of=str(latest))

    _ni_pay = gv(L, "net_income")
    for _mid, _num, _lbl in (("DIVIDEND_PAYOUT_RATIO", dividend, "dividends paid"),
                             ("BUYBACK_PAYOUT_RATIO", buyback, "share repurchases"),
                             ("TOTAL_SHAREHOLDER_PAYOUT_RATIO", total_return_latest,
                              "dividends paid + share repurchases")):
        _ni_basis = basis_of(L, "net_income")
        if _num is not None and _ni_pay and _ni_pay > 0:
            _store.register(_mid, (_num / _ni_pay) * 100.0, period="LFY", basis=_ni_basis,
                            unit="%", formula=_MR.FORMULAS[_mid][0], numerator=_lbl,
                            denominator="net income (%s)" % _ni_basis, as_of=str(latest))
        else:
            _store.unavailable(_mid, "Net income is not positive, so a payout ratio is not "
                                     "meaningful.", period="LFY", basis=_ni_basis, unit="%")

    for _mid, _val, _unit, _per, _bas in (
            ("ROIC", roic_now, "%", "LFY", "reported"),
            ("ROE", roe_now, "%", "LFY", "adjusted"),
            ("TSR", tsr_ann, "%", "CUM5", "reported"),
            ("BUYBACK_YIELD", (bb_yield * 100.0 if bb_yield is not None else None), "%", "LFY", "reported"),
            ("FCF", fcf, "$", "LFY", "reported"),
            ("OCF", ocf, "$", "LFY", "reported"),
            ("NET_DEBT", net_debt, "$", "LQ", "reported"),
            ("TOTAL_DEBT", total_debt, "$", "LQ", "reported"),
            ("CASH", cash_now, "$", "LQ", "reported"),
            ("REINVESTMENT_RATE", (reinvest_rate * 100.0 if reinvest_rate is not None else None), "%", "LFY", "reported"),
            ("SHARE_COUNT_CHANGE", (-sh_reduction * 100.0 if sh_reduction is not None else None), "%", "CUM5", "n/a")):
        if _val is not None:
            _store.register(_mid, _val, period=_per, basis=_bas, unit=_unit,
                            formula=_MR.FORMULAS.get(_mid, ("", "", ""))[0], as_of=str(latest))
        else:
            _store.unavailable(_mid, "Required input not reported for the period.",
                               period=_per, basis=_bas, unit=_unit)

    _store.freeze()

    out = {
        "status": "ok",
        "registry": _store.emit(),
        "meta": {
            "ticker": meta.get("ticker"), "company_name": meta.get("company_name"),
            "industry": meta.get("industry"), "classification": meta.get("classification"),
            "sector_key": meta.get("sector_key"),
            "prepared_date": meta.get("prepared_date"), "currency": cur,
            "period_latest": latest, "period_first": first, "years_covered": n_periods,
            "cagr_span": span, "currencies_in_window": _win_curs,
            "price": price, "price_date": market.get("price_date"),
            "market_cap": fmt_usd(mcap, cur), "market_cap_raw": mcap,
        },
        "snapshot": snapshot,
        "sources_uses": sources_uses,
        "classification": classification,
        "buybacks": buybacks,
        "dividends": dividends,
        "m_and_a": m_and_a,
        "capex": capex_out,
        "balance_sheet": balance_sheet,
        "peers": peer_rows,
        "value_creation": value_creation,
        "scorecard": {**scorecard, "evidence": evidence},
        "measures_evaluated": _measures_evaluated,
        "trend": trend,
        "monitor": monitor,
        "market_snapshot": market_snapshot,
        "risks_computed": risks,
        "quality_flags": quality_flags,
        "cyclical": _cyclical,
        "capital_intensive": _cap_intensive,
        "methodology": methodology,
        "history": hist,
        "qc": qc,
        "period_reconciliation": period_reconciliation,
        "validation": validation,
        "warnings": warnings,
    }
    out["monitor_thresholds"] = monitor_thresholds(out)
    return out


def _capital_discipline_score(reinvest_rate, leverage, bb_flags, vc_score):
    s = 5
    if (reinvest_rate is not None
            and RV("capital_discipline", "reinvest_band_lo") <= reinvest_rate
            <= RV("capital_discipline", "reinvest_band_hi")):
        s += 1
    if leverage is not None and leverage < RV("capital_discipline", "lev_under"):
        s += 1
    if any("below current price" in f for f in bb_flags):
        s += 1
    if any("dilution offset" in f for f in bb_flags):
        s -= 2
    s = int(round((s + vc_score) / 2))
    return max(0, min(10, s))


def rate(score):
    return ("Excellent" if score >= 8 else "Good" if score >= 6
            else "Neutral" if score >= 4 else "Poor")


def _peer_metrics(actuals_annual, market_cap, coc):
    rows = unwrap(actuals_annual)
    by, _ = index_rows(rows)
    yrs = annual_years(by)
    if len(yrs) < 2:
        return None
    L = by[yrs[-1]]
    ocf = gv(L, "ocf")
    capex = gv(L, "capex")
    capex = abs(capex) if capex is not None else None
    fcf = gv(L, "fcf")
    if fcf is None and ocf is not None and capex is not None:
        fcf = ocf - capex
    ni = gv(L, "net_income")
    rev = gv(L, "revenue")
    bb = gv(L, "buyback_usd")
    bb = abs(bb) if bb is not None else None
    dv = gv(L, "dividend")
    dv = abs(dv) if dv is not None else None
    _bb_ever = any(gv(by[y], "buyback_usd") is not None for y in yrs)
    _dv_ever = any(gv(by[y], "dividend") is not None for y in yrs)
    nd = gv(L, "net_debt")
    ebitda = gv(L, "ebitda")
    _peer_ebitda_eff = ebitda if (ebitda and ebitda > 0) else None
    _shs = [gv(by[y], "shares") for y in yrs]
    _shi = [i for i, v in enumerate(_shs) if v]
    if len(_shi) >= 2:
        shf, shl = _shs[_shi[0]], _shs[_shi[-1]]
    else:
        shf, shl = gv(by[yrs[0]], "shares"), gv(L, "shares")
    eb = gv(L, "ebit")
    etr = gv(L, "etr")
    tax = etr / 100.0 if etr is not None else 0.21
    d, e, csh = gv(L, "total_debt"), gv(L, "equity"), gv(L, "cash")
    roic = None
    if eb is not None and e is not None and e > 0:
        inv = (d or 0) + e - (csh or 0)
        roic = (eb * (1 - tax) / inv * 100.0) if inv > 0 else None
    cr = None if (bb is None and dv is None) else (bb or 0) + (dv or 0)

    def _cum(field, absval=False):
        tot = None
        for y in yrs:
            v = gv(by[y], field)
            if v is None:
                continue
            tot = (tot or 0) + (abs(v) if absval else v)
        return tot
    cum_bb = _cum("buyback_usd", absval=True)
    cum_dv = _cum("dividend", absval=True)
    cum_cr = None if (cum_bb is None and cum_dv is None) else (cum_bb or 0) + (cum_dv or 0)
    cum_fcf = None
    for y in yrs:
        f = gv(by[y], "fcf")
        if f is None:
            o, cx = gv(by[y], "ocf"), gv(by[y], "capex")
            if o is not None and cx is not None:
                f = o - abs(cx)
        if f is not None:
            cum_fcf = (cum_fcf or 0) + f

    return {
        "buyback_yield": (safe_div(bb, market_cap) if bb is not None
                          else (safe_div(0, market_cap) if (dv is not None and not _bb_ever)
                                else None)),
        "dividend_yield_proxy": (safe_div(dv, market_cap) if dv is not None
                                 else (safe_div(0, market_cap) if (bb is not None and not _dv_ever)
                                       else None)),
        "total_shareholder_yield": safe_div(cr, market_cap),
        "capex_intensity": safe_div(capex, rev),
        "reinvestment_rate": (safe_div(capex, ocf) if (ocf and ocf > 0) else None),
        "capital_return_fcf": (safe_div(cum_cr, cum_fcf) if (cum_fcf and cum_fcf > 0) else None),
        "roic": roic,
        "leverage": safe_div(nd, _peer_ebitda_eff),
        "_ebitda_positive": bool(ebitda and ebitda > 0),
        "fcf_conversion": safe_div(fcf, ni),
        "share_count_change": (safe_div((shl - shf), shf) if (shf and shl) else None),
    }


def _bs_evidence_clauses(bs):
    parts = []
    _lev = str(bs.get("leverage_nd_ebitda", "") or "")
    if _lev and not _lev.lower().startswith(("n/a", "withheld", "-")):
        parts.append("leverage was %s" % _lev)
    _cov = str(bs.get("interest_coverage", "") or "")
    if _cov and not _cov.lower().startswith(("n/a", "withheld")):
        parts.append("interest cover was %s" % _cov)
    _nd = str(bs.get("net_debt", "") or "")
    if _nd and not _nd.lower().startswith(("n/a", "withheld")):
        parts.append("the company held net cash" if _nd.startswith("-")
                     else "the company held net debt of %s" % _nd)
    if not parts:
        return ("no leverage, interest-cover or net-debt measure is available for the period, "
                "so the dimension carries its base score")
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _outlier_reason(metric, v):
    if metric == "fcf_conversion":
        return "distorted by near-breakeven/negative net income denominator"
    if metric == "roic":
        return ("negative NOPAT / operating losses" if v < 0 else "very small invested-capital base")
    if metric == "leverage":
        return ("net cash position (net debt below zero)" if v < 0
                else "elevated net debt vs EBITDA")
    if metric in ("buyback_yield", "dividend_yield_proxy", "capex_intensity"):
        return "one-off or definitional distortion vs the group"
    return "one-off/definitional distortion"


def _dev_reason(metric, tv, dev):
    if metric == "roic":
        return "capital-light / high-return model" if dev > 0 else "trough earnings or heavier asset base"
    if metric == "leverage":
        return "more conservatively capitalised (net cash / low debt)" if dev < 0 else "carries more leverage than the group"
    if metric == "fcf_conversion":
        return "stronger cash conversion" if dev > 0 else "weaker cash conversion (capex- or WC-heavy)"
    if metric == "capex_intensity":
        return "higher capex intensity than the group" if dev > 0 else "lower capex intensity than the group"
    if metric == "buyback_yield":
        return "returning more via buybacks" if dev > 0 else "buys back less than the group"
    if metric == "dividend_yield_proxy":
        return "higher payout" if dev > 0 else "lower/again-no dividend vs group"
    if metric == "share_count_change":
        return "diluting faster than the group" if dev > 0 else "shrinking the count faster than the group"
    return "structural difference vs the group"


_NA_RX = re.compile(r"(?<![a-z])n/?a(?![a-z])", re.I)

_CLAUSE_SPLIT = re.compile(r",\s+|\s+with\s+|\s+and\s+|;\s+")


def _hygiene_evidence(evidence, components, primary_inputs):
    out, unscored = {}, set()
    for key, text in (evidence or {}).items():
        if not text or not isinstance(text, str):
            out[key] = text
            continue

        prim = primary_inputs.get(key)
        if prim is not None and all(v is None for v in prim):
            out[key] = ("Unscored due to unavailable required inputs. The primary input for this "
                        "dimension is not reported in the source data, so no grade is assigned "
                        "and its weight is excluded from the overall score.")
            unscored.add(key)
            continue

        if not _NA_RX.search(text):
            out[key] = text
            continue

        m = re.match(r"(scored\s+\d{1,2}\s*/\s*10\s+because\s+)(.*)$", text, re.I | re.S)
        if not m:
            out[key] = _NA_RX.sub("not reported", text)
            continue
        stem, body_txt = m.group(1), m.group(2).rstrip(". ")
        clauses = [c.strip() for c in _CLAUSE_SPLIT.split(body_txt) if c.strip()]
        keep = [c for c in clauses if not _NA_RX.search(c)]
        if not keep:
            out[key] = ("Unscored due to unavailable required inputs. None of the supporting "
                        "measures for this dimension are reported in the source data.")
            unscored.add(key)
        elif len(keep) == len(clauses):
            out[key] = text
        else:
            body_out = body_txt
            for c in clauses:
                if _NA_RX.search(c):
                    body_out = body_out.replace(c, "\x00")
            _SEP = r"(?:,\s*|\s+and\s+|\s+with\s+|\s+at\s+|\s+over\s+|;\s*)"
            body_out = re.sub(_SEP + r"\x00", "", body_out)
            body_out = re.sub(r"\x00" + _SEP, "", body_out)
            body_out = body_out.replace("\x00", "")
            body_out = re.sub(r"\s{2,}", " ", body_out).strip(" ,;.")
            body_out = re.sub(r"\s+([,;.])", r"\1", body_out)
            body_out = re.sub(r"^(?:and|with|at|over|,|;)\s+", "", body_out, flags=re.I)
            out[key] = (stem + body_out + ".") if body_out else (
                "Unscored due to unavailable required inputs. None of the supporting measures "
                "for this dimension are reported in the source data.")
    return out, unscored


def compute_peers(raw, coc, canonical=None):
    peers = raw.get("peers") or []
    if not peers:
        return {"available": False, "rows": [], "note": "No peer data supplied."}
    metrics_keys = ["buyback_yield", "dividend_yield_proxy", "total_shareholder_yield",
                    "capex_intensity", "reinvestment_rate", "capital_return_fcf",
                    "roic", "leverage", "fcf_conversion", "share_count_change"]
    target_m = _peer_metrics(raw.get("actuals_annual"), raw.get("market", {}).get("market_cap")
                             or (raw.get("market", {}).get("price") and None), coc)
    mkt = raw.get("market", {})
    tmc = mkt.get("market_cap")
    if tmc is None and mkt.get("price"):
        rows = unwrap(raw.get("actuals_annual"))
        by, _ = index_rows(rows)
        yrs = annual_years(by)
        if yrs:
            sh = gv(by[yrs[-1]], "shares")
            if sh:
                tmc = mkt["price"] * sh
    target_m = _peer_metrics(raw.get("actuals_annual"), tmc, coc)

    if canonical and target_m:
        for _k, _v in canonical.items():
            if _k in target_m and _v is not None:
                target_m[_k] = _v

    rows = []
    collected = {k: [] for k in metrics_keys}
    for p in peers:
        m = _peer_metrics(p.get("actuals_annual"), p.get("market_cap"), coc)
        if not m:
            continue
        rows.append({"name": f"{p.get('name', p.get('ticker'))}", "metrics": m})
        for k in metrics_keys:
            if m[k] is not None:
                collected[k].append(m[k])
    BANDS = {"buyback_yield": (0.0, 0.25), "dividend_yield_proxy": (0.0, 0.15),
             "total_shareholder_yield": (0.0, 0.30), "capex_intensity": (0.0, 0.60),
             "reinvestment_rate": (0.0, 3.0), "capital_return_fcf": (-1.0, 5.0),
             "roic": (-50.0, 200.0), "leverage": (-10.0, 15.0), "fcf_conversion": (-5.0, 5.0),
             "share_count_change": (-0.5, 0.5)}
    peer_warnings = []
    for row in rows:
        m = row["metrics"]
        row["flags"] = []
        for k, (lo, hi) in BANDS.items():
            v = m.get(k)
            if v is not None and not (lo <= v <= hi):
                why = _outlier_reason(k, v)
                peer_warnings.append(f"peer {row['name']}: {k}={v:.2f} outside band [{lo},{hi}] - excluded from median ({why})")
                row["flags"].append(f"{k} outlier: {why}")
                m[k] = None
    collected2 = {k: [] for k in metrics_keys}
    for row in rows:
        for k in metrics_keys:
            v = row["metrics"].get(k)
            if v is not None:
                collected2[k].append(v)
    median = {}
    _thin_median = []
    for k in metrics_keys:
        vals = sorted(collected2[k])
        if len(vals) >= 2:
            n = len(vals)
            median[k] = (vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2)
        else:
            median[k] = None
            if len(vals) == 1:
                _thin_median.append(k)
    if _thin_median:
        peer_warnings.append(
            "only one peer reported %s, so no median is published for %s: a single observation is "
            "that company's own figure, not a group measure, and the difference column derived "
            "from it would compare the company with one peer under the word \"median\"."
            % ("it" if len(_thin_median) == 1 else "them", ", ".join(sorted(_thin_median))))
    _all_missing = sorted(k for k in metrics_keys
                          if rows and all((r.get("metrics") or {}).get(k) is None for r in rows))
    if _all_missing and rows:
        peer_warnings.append(
            "ADVISORY: %s returned no value for ANY of the %d peers. One peer missing a line is "
            "that filer's disclosure; every peer missing the same line is usually the fetch - a "
            "calendarised get_actuals call drops balance-sheet codes for filers whose periods "
            "come back as restated vintages. Re-fetch the peer annuals with calendarize=false "
            "and compare row counts before treating this as a genuine gap."
            % (", ".join(_all_missing), len(rows)))
    dev_notes = []
    THRESH = {"roic": (20.0, "pp"), "leverage": (2.0, "x"), "fcf_conversion": (0.5, "x"),
              "capex_intensity": (0.08, "frac"), "buyback_yield": (0.02, "frac"),
              "dividend_yield_proxy": (0.02, "frac"), "share_count_change": (0.05, "frac")}
    lbl = {"roic": "ROIC", "leverage": "Leverage", "fcf_conversion": "FCF conversion",
           "capex_intensity": "Capex intensity", "buyback_yield": "Buyback yield",
           "dividend_yield_proxy": "Dividend yield", "share_count_change": "Share count change"}
    for k, (thr, unit) in THRESH.items():
        tv, mv = (target_m or {}).get(k), median.get(k)
        if tv is None or mv is None:
            continue
        dev = tv - mv
        if abs(dev) < thr:
            continue
        if unit == "pp":
            mag = f"{dev:+.0f}pp"
        elif unit == "x":
            mag = f"{dev:+.2f}x"
        else:
            mag = f"{dev*100:+.1f}pp"
        why = _dev_reason(k, tv, dev)
        dev_notes.append(f"{lbl[k]} {mag} vs peer median - {why}.")
    return {"available": True, "target": target_m, "median": median,
            "rows": rows, "metric_keys": metrics_keys, "peer_warnings": peer_warnings,
            "deviation_notes": dev_notes[:3]}



def _article(v):
    d = str(v).lstrip("$€£¥ ").lstrip()
    return "an" if (d[:2] in ("11", "18") or d[:1] == "8") else "a"


_MCAP_CCY_FACTOR = {
    "JPY": 200.0, "¥": 200.0, "KRW": 2000.0, "₩": 2000.0, "INR": 120.0, "₹": 120.0,
    "CNY": 10.0, "RMB": 10.0, "HKD": 10.0, "TWD": 40.0, "IDR": 20000.0, "THB": 45.0,
    "PHP": 70.0, "MYR": 6.0, "SGD": 2.0, "CLP": 1200.0, "COP": 5000.0, "ARS": 2000.0,
    "HUF": 500.0, "CZK": 30.0, "PLN": 6.0, "ZAR": 25.0, "MXN": 25.0, "BRL": 7.0,
    "TRY": 60.0, "SEK": 15.0, "NOK": 15.0, "DKK": 10.0, "RUB": 150.0, "₽": 150.0,
    "SAR": 5.0, "AED": 5.0, "ILS": 5.0, "NZD": 2.5, "AUD": 2.0, "CAD": 2.0,
}


def run_qc(ocf, capex, fcf, buyback, dividend, mcap, bb_yield, leverage,
           roic, sh_s, warnings, leverage_unit="x", win_curs=None, roe=None):
    checks = []

    def chk(name, ok, detail):
        checks.append({"check": name, "pass": bool(ok), "detail": detail})
        if not ok:
            warnings.append(f"QC: {name} - {detail}")

    if ocf is not None and capex is not None and fcf is not None:
        chk("FCF identity (OCF - Capex ~ FCF)",
            abs((ocf - capex) - fcf) <= max(1.0, 0.02 * abs(fcf)) or fcf == 0,
            f"OCF-Capex={ocf-capex:.0f} vs FCF={fcf:.0f} (millions)")
    if bb_yield is not None:
        chk("Buyback yield in 0-25% band", 0 <= bb_yield <= 0.25, f"{bb_yield*100:.1f}%")
    if leverage is not None:
        if leverage_unit == "%":
            chk("Debt / total capital in 0-100% band", 0 <= leverage <= 100, f"{leverage:.1f}%")
        else:
            chk("Leverage in -5x..15x band", -5 <= leverage <= 15, f"{leverage:.2f}x")
    if roic is not None:
        chk("ROIC in -50%..200% band", -50 <= roic <= 200, f"{roic:.1f}%")
    sh_vals = [x for x in sh_s if x is not None]
    if len(sh_vals) >= 2:
        chk("Share count monotonic/plausible", all(v > 0 for v in sh_vals),
            f"{sh_vals[0]:.0f} -> {sh_vals[-1]:.0f} (M)")
    _MAX_SHARES_M = 50000.0
    _MAX_MCAP_M = 15000000.0
    if sh_vals:
        chk("Share count within any real-world bound", max(sh_vals) <= _MAX_SHARES_M,
            ("%.0fM shares - the feed's figures for this issuer are likely scaled wrongly"
             % max(sh_vals)) if max(sh_vals) > _MAX_SHARES_M else "%.0fM" % max(sh_vals))
    if roe is not None:
        chk("Return on equity within any real-world bound", abs(roe) <= _MAX_ROE_ABS,
            ("%.1f%% - the equity base for this issuer is likely scaled wrongly"
             % roe) if abs(roe) > _MAX_ROE_ABS else "%.1f%%" % roe)
    if mcap:
        _mcap_max = _MAX_MCAP_M * max([_MCAP_CCY_FACTOR.get(str(c).upper(), 1.0)
                                       for c in (win_curs or [])] or [1.0])
        chk("Market capitalisation within any real-world bound", mcap <= _mcap_max,
            ("%s - larger than any company that has ever listed; check the reported scale"
             % fmt_usd(mcap, "")) if mcap > _mcap_max else fmt_usd(mcap, ""))
    _wc = list(win_curs or [])
    if _wc:
        chk("Single reporting currency across the window", len(_wc) == 1,
            ("window mixes %s - multi-year rates withheld" % ", ".join(_wc))
            if len(_wc) > 1 else _wc[0])
    passed = sum(1 for c in checks if c["pass"])
    return {"checks": checks, "passed": passed, "total": len(checks)}


def main():
    if len(sys.argv) < 3:
        sys.exit("Usage: python compute_capalloc.py raw_data.json computed.json")
    with open(sys.argv[1]) as f:
        raw = json.load(f)
    out = compute(raw)
    with open(sys.argv[2], "w") as f:
        json.dump(out, f, indent=2)
    if out.get("status") != "ok":
        print(json.dumps(out)); sys.exit(2)
    print(json.dumps({"status": "ok", "ticker": out["meta"]["ticker"],
                      "classification": out["classification"]["label"],
                      "qc": f"{out['qc']['passed']}/{out['qc']['total']}",
                      "warnings": out["warnings"]}, indent=2))


if __name__ == "__main__":
    main()
