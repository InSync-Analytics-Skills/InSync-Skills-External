#!/usr/bin/env python
"""
earnings-flash math engine.

Usage: python references/run_math.py <input.json> <output_dir>

Input JSON schema (LLM writes this from raw MCP responses):
  {
    "ticker": "EXM",
    "period": "4Q25",           # current fiscal period
    "prior_period": "4Q24",     # YoY comparison period
    "kpi_mapping": { ... raw get_kpi_mapping row ... },   # may be null/empty
    "kpi_override": ["SD_P_1007", ...],                   # optional: LLM-selected fallback
    "mcp_responses": {
      "get_actuals":   { ... raw MCP response ... },
      "get_estimates": { ... raw MCP response ... },
      "get_valuation": { ... },
      "get_market_data": { ... },
      "get_call_date": { ... },
      "get_guidance":  { ... }
    }
  }

Output files in <output_dir>:
  computed.json   — financialMetrics[], operatingMetrics[], marketPrices, valuation,
                    valuationLine, basis, kpi_source, kpi_count, warnings[]
                    (guidance is not copied into computed.json, provenance is
                    `kpi_source`, and warnings ride in computed.json rather than a
                    side file.)
"""
import datetime as _dt
import json
import os
import re
from decimal import Decimal, ROUND_HALF_UP
from collections import Counter
import sys
from typing import Any, Dict, List, Optional, Tuple



_FFO_CODES = ("CF_P_209", "CF_P_202", "CF_P_203")

FINANCIAL_METRICS = [
    {"name": "Total Revenue",        "ltcodes": ["IS_P_101"]},
    {"name": "EBITDA (Ex-1x Items)", "ltcodes": ["IS_P_114", "IS_P_113", "IS_P_276",
                                                 "IS_P_210", "IS_P_209", "IS_P_406",
                                                 "IS_P_110", "IS_P_109",
                                                 "IS_P_118", "IS_P_117", "IS_P_124"]},
    {"name": "EPS (Ex-1x Items)",    "ltcodes": ["CF_P_209", "CF_P_202", "CF_P_203",
                                                 "IS_P_128", "IS_P_127", "IS_P_124", "IS_P_123"]},
]

BEAT_MISS_THRESHOLD = 0.005
PCT_BEAT_MISS_THRESHOLD_PPT = 0.25


KPI_TIER_PREFIXES = ("SD_P_", "KM_P_", "SD_S_", "KM_S_")

MAX_KPIS = 5
MIN_KPIS = 3



_CUR_RE = re.compile(
    r'^\s*(R\$|A\$|C\$|HK\$|NT\$|[$\u20ac\u00a3\u00a5\u20b9\u20a9\u20bd\u20ba]|'
    r'GBPence|GBp|ZARc|USCents|USDc|EURc|'
    r'EUR|GBP|CHF|DKK|SEK|NOK|CAD|JPY|BRL|MXN|CLP|COP|ARS|PEN|ZAR|INR|IDR|'
    r'KRW|TWD|HKD|CNY|RMB|SGD|AUD|NZD|PLN|TRY|ILS|AED|SAR|THB|PHP|MYR|'
    r'USD|HUF|CZK|RON|BGN|HRK|ISK|RSD|MKD|BAM|ALL|MDL|BYN|UAH|KZT|AZN|GEL|AMD|'
    r'UZS|MNT|VND|KHR|LAK|MMK|BND|PKR|BDT|LKR|NPR|MOP|'
    r'EGP|NGN|KES|GHS|TZS|UGX|ZMW|BWP|MUR|MAD|TND|DZD|'
    r'QAR|KWD|BHD|OMR|JOD|'
    r'JMD|TTD|BSD|BBD|DOP|GTQ|CRC|HNL|NIO|PAB|UYU|PYG|BOB|VES|'
    r'FJD|PGK)\s*', re.I)
_MINOR_UNITS = ("GBPence", "GBp", "ZARc", "USCents", "USDc", "EURc")
_PCT_RE = re.compile(r'%\s*$')
_MAG_RE = re.compile(r'([TBMK])\s*$', re.I)


def parse_mcp_value(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    if not s or s in ("-", "N/A", "null", "None"):
        return None
    s = _CUR_RE.sub("", s, count=1).strip()
    _sign = ""
    if s[:1] in ("-", "+"):
        _rest = _CUR_RE.sub("", s[1:], count=1).strip()
        if _rest != s[1:]:
            _sign, s = ("-" if s[0] == "-" else ""), _rest
    s = s.replace("$", "").replace(",", "").strip()
    s = _sign + s if s else s
    if s.endswith("%"):
        s = s[:-1].strip()
    mult = 1.0
    if s.upper().endswith("T"):
        mult = 1_000_000_000_000; s = s[:-1].strip()
    elif s.upper().endswith("B"):
        mult = 1_000_000_000; s = s[:-1].strip()
    elif s.upper().endswith("M"):
        mult = 1_000_000; s = s[:-1].strip()
    elif s.upper().endswith("K"):
        mult = 1_000; s = s[:-1].strip()
    try:
        return float(s) * mult
    except (ValueError, TypeError):
        return None



def _unwrap_mcp_payload(payload: Any) -> Dict[str, Any]:
    if payload is None:
        return {}
    if isinstance(payload, str):
        s = payload.strip()
        if s.startswith("{") or s.startswith("["):
            try:
                return _unwrap_mcp_payload(json.loads(s))
            except json.JSONDecodeError:
                return {}
        return {}
    if isinstance(payload, list):
        return {"data": payload}
    if not isinstance(payload, dict):
        return {}
    if "result" in payload and isinstance(payload["result"], dict) and "content" in payload["result"]:
        return _unwrap_mcp_payload(payload["result"])
    if "content" in payload and isinstance(payload["content"], list):
        for item in payload["content"]:
            if isinstance(item, dict) and item.get("type") == "text" and "text" in item:
                return _unwrap_mcp_payload(item["text"])
    return payload


def _mcp_meta(mcp_resp):
    r = _unwrap_mcp_payload(mcp_resp) or {}
    m = r.get("meta")
    return m if isinstance(m, dict) else {}


def _guidance_rows(mcp_resp):
    r = _unwrap_mcp_payload(mcp_resp) or {}
    d = r.get("data")
    if not isinstance(d, dict):
        return len(_rows(mcp_resp)), 0
    g = d.get("guidance") if isinstance(d.get("guidance"), list) else []
    o = d.get("outlook") if isinstance(d.get("outlook"), list) else []
    return len(g), len(o)


_GUIDE_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%d-%b-%Y", "%Y/%m/%d", "%d/%m/%Y")


def _parse_guide_date(raw):
    if not raw:
        return None
    s = str(raw).strip()
    for cand in (s, s.split("T")[0].split(" ")[0], s[:10]):
        for fmt in _GUIDE_DATE_FORMATS:
            try:
                return _dt.datetime.strptime(cand, fmt).date()
            except ValueError:
                continue
    return None


def _guidance_dates(mcp_resp):
    r = _unwrap_mcp_payload(mcp_resp) or {}
    d = r.get("data")
    if not isinstance(d, dict):
        d = {}
    out = []
    for key in ("guidance", "outlook"):
        rows = d.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict):
                dt = _parse_guide_date(row.get("GUIDEDATE"))
                if dt:
                    out.append(dt)
    return out


GUIDANCE_RUNGS = ("feed", "release", "transcript")
GUIDANCE_RUNG_FAILURES = ("empty", "stale", "timeout", "refusal", "error", "absent",
                          "unparsed", "skipped", "failed", "not_found", "no_guide")


def guidance_attempt_receipt(guidance_attempts):
    missing, routes = [], []
    if not isinstance(guidance_attempts, dict):
        return list(GUIDANCE_RUNGS), routes
    for rung in GUIDANCE_RUNGS:
        entry = guidance_attempts.get(rung)
        if not isinstance(entry, dict):
            missing.append(rung)
            continue
        result = str(entry.get("result", "")).strip().lower()
        detail = []
        if rung == "feed":
            rows = entry.get("rows")
            if not isinstance(rows, int):
                missing.append(rung)
                continue
            detail.append(f"{rows} row(s)")
        else:
            n = entry.get("attempts")
            if isinstance(n, int):
                detail.append(f"{n} attempt(s)")
            if entry.get("path"):
                detail.append(str(entry["path"])[-48:])
        if result not in GUIDANCE_RUNG_FAILURES:
            missing.append(rung)
            continue
        routes.append(f"{rung}: {result}" + (f" ({', '.join(detail)})" if detail else ""))
    return missing, routes


def guidance_freshness_gate(mcp_resp, call_date_iso, guidance_source=None,
                            guidance_attempts=None):
    struct, outlook = _guidance_rows(mcp_resp)
    dates = _guidance_dates(mcp_resp)
    newest = max(dates) if dates else None
    call_d = _parse_guide_date(call_date_iso)
    warnings = []

    status = {
        "structured_rows": struct,
        "outlook_rows": outlook,
        "newest_guide_date": newest.isoformat() if newest else None,
        "call_date": call_d.isoformat() if call_d else None,
        "state": "ok",
        "declared_source": guidance_source or "mcp",
    }

    if (guidance_source or "").strip().lower() == "unavailable":
        _missing, _routes = guidance_attempt_receipt(guidance_attempts)
        status["guidance_attempts"] = guidance_attempts or {}
        status["guidance_routes_tried"] = _routes
        status["guidance_rungs_unrecorded"] = _missing
        warnings.append(
            "[guidance unavailable] guidance_source='unavailable' — NOTHING could be "
            "retrieved; the Guidance Snapshot carries one line, 'Guidance could not be "
            "retrieved at press time.' Routes tried: "
            + ("; ".join(_routes) if _routes else "NONE RECORDED")
            + (". Rungs with no recorded failed attempt: " + ", ".join(_missing)
               if _missing else ". Every rung recorded a failed attempt.")
            + " Confirm with the reader that an absent guidance section is understood as "
              "'could not be retrieved', never as 'the company does not guide' — that claim "
              "is 'none', and it renders a Forward View block instead.")

    if struct == 0 and outlook == 0:
        status["state"] = "empty"
        warnings.append(
            "[no guidance rows] get_guidance returned zero structured guidance AND zero "
            "outlook rows. This is a real state for non-guiding companies (some issue none), "
            "but it is indistinguishable from a failed fetch OR from guidance that has not "
            "ingested yet. DO NOT resolve it by assertion: parse the Earnings Release (a "
            "fixed Tier B slot, and the one document guaranteed to carry the guidance range), "
            "take the guides from there, and declare input.json.guidance_source as 'release'. "
            "Only if the release carries no guide may you declare 'none' and say the company "
            "does not guide.")
        return warnings, status

    if newest and call_d and newest < call_d:
        status["state"] = "stale"
        warnings.append(
            f"[guidance predates print] the newest GUIDEDATE in get_guidance is "
            f"{newest.isoformat()}, BEFORE the {call_d.isoformat()} earnings call. These are "
            f"last period's guides, returned by latest=true because this period's have not "
            f"ingested yet. Quoting them would print a superseded guide as current with no "
            f"other tell on the page. Parse the Earnings Release for the guides actually "
            f"given on the call and declare input.json.guidance_source as 'release'; the "
            f"rows in hand remain valid as the PRIOR guide for 'from [prior] to [current]'.")
    elif not call_d and (struct or outlook):
        status["state"] = "undated"
        warnings.append(
            "[guidance freshness unknown] guidance rows are present but no call date "
            "resolved, so nothing can tell this period's guides from last period's. Supply "
            "call_date_iso (or a get_call_date payload) and re-run before quoting any guide "
            "as current.")
    elif not dates and (struct or outlook):
        status["state"] = "undated"
        warnings.append(
            "[guidance freshness unknown] guidance rows are present but NONE carries a "
            "parseable GUIDEDATE, so the staleness check could not run. Confirm against the "
            "Earnings Release that the guides quoted are the ones given on this call.")

    if outlook and not struct:
        warnings.append(
            f"[guidance in outlook only] {outlook} free-text outlook row(s) and no "
            f"structured guidance. data.outlook[] carries the Guide Notes and is NOT reached "
            f"by the standard row extractor — read it directly when writing the section.")

    return warnings, status


_AFTER_CLOSE_ET_HOUR = 16
_PRE_MARKET_ET_HOUR = 9.5
_REPANEL_RANGE_SHIFT = 0.05
_MARKET_HOURS = {"ET": (_PRE_MARKET_ET_HOUR, _AFTER_CLOSE_ET_HOUR), "EST": (9.5, 16), "EDT": (9.5, 16),
                 "GMT": (8, 16.5), "BST": (8, 16.5), "UTC": (8, 16.5), "CET": (9, 17.5), "CEST": (9, 17.5),
                 "JST": (9, 15), "HKT": (9.5, 16), "IST": (9.25, 15.5), "KST": (9, 15.5), "SGT": (9, 17),
                 "AEST": (10, 16), "AEDT": (10, 16), "CST": (9.5, 15)}


def release_is_after_close(release_filename: Optional[str]) -> Optional[bool]:
    if not release_filename:
        return None
    m = re.search(r"_(\d{2})\.(\d{2})\.(\d{2})_([A-Z]{2,4})\b", str(release_filename))
    if not m:
        return None
    opens, closes = _MARKET_HOURS.get(m.group(4), (None, None))
    if opens is None:
        return None
    hour = int(m.group(1)) + int(m.group(2)) / 60.0
    if hour >= closes:
        return True
    if hour < opens:
        return False
    return None


def _est_rows_by_key(mcp_resp) -> Dict[Tuple[str, str], List[Dict]]:
    out: Dict[Tuple[str, str], List[Dict]] = {}
    for row in _rows(mcp_resp):
        if not isinstance(row, dict):
            continue
        key = (str(row.get("LTCODE") or ""), str(row.get("FISCALPERIOD") or ""))
        d = _parse_guide_date(row.get("UPDATEDATE"))
        if not d:
            continue
        out.setdefault(key, []).append({**row, "_d": d})
    for k in out:
        out[k].sort(key=lambda r: r["_d"])
    return out


def _repanelled(chosen: Dict, prior: Optional[Dict]) -> Optional[str]:
    if not prior:
        return None
    try:
        nb, pb = int(chosen.get("BROKERCOUNT") or 0), int(prior.get("BROKERCOUNT") or 0)
    except (TypeError, ValueError):
        nb = pb = 0
    if pb and nb and nb < pb:
        return f"broker count fell {pb} -> {nb}"
    for field in ("MIN", "MAX"):
        a, b = parse_mcp_value(chosen.get(field)), parse_mcp_value(prior.get(field))
        if a is not None and b not in (None, 0):
            if abs(a - b) / abs(b) > _REPANEL_RANGE_SHIFT:
                return f"{field} moved {b:,.4g} -> {a:,.4g}"
    return None


def pick_pre_print_consensus(mcp_resp, call_date_iso: str,
                             release_filename: Optional[str] = None):
    warnings: List[str] = []
    call_d = _parse_guide_date(call_date_iso)
    if not call_d:
        return {}, ["[consensus not pinned] no call date resolved, so guidance consensus could "
                    "not be pinned to the print. The value shown is whatever the fetch "
                    "returned and may already reflect the guide it is being compared to."]

    after_close = release_is_after_close(release_filename)
    if after_close is True:
        cutoff, basis = call_d, "release after the close, so same-day estimates are pre-guide"
    elif after_close is False:
        cutoff = call_d - _dt.timedelta(days=1)
        basis = "release pre-market, so same-day estimates may already be reactions"
    else:
        cutoff = call_d - _dt.timedelta(days=1)
        basis = "release time unknown, so the day before the call is used"
        warnings.append(
            "[consensus cutoff assumed] the earnings-release filename carried no readable ET "
            "timestamp, so the pre-guide cutoff fell back to the day BEFORE the call. That is "
            "the safe direction, but it can miss a same-day pre-market snapshot. Pass the "
            "release file name from get_document_tree to pin it exactly.")

    picked: Dict[Tuple[str, str], Dict] = {}
    for key, rows in _est_rows_by_key(mcp_resp).items():
        eligible = [r for r in rows if r["_d"] <= cutoff]
        if not eligible:
            warnings.append(
                f"[no pre-print consensus] {key[0]} / {key[1]}: every consensus snapshot "
                f"post-dates {cutoff.isoformat()}, so no pre-guide comparator exists. Write "
                f"the guide alone, with no consensus, rather than quoting a post-guide number.")
            continue
        chosen = eligible[-1]
        prior = eligible[-2] if len(eligible) > 1 else None
        if prior is None:
            warnings.append(
                f"[single pre-print snapshot] {key[0]} / {key[1]}: only one consensus "
                f"snapshot stands at or before {cutoff.isoformat()}, so the repanel check "
                f"had no prior to step back to and did not run. The pinned value is "
                f"unverified against the post-print signature. Widen updatedate_start and "
                f"re-fetch if the Guidance Snapshot leans on it. (Warn only: guidance "
                f"bullets are not graded rows, so this does not stop the build.)")
        why = _repanelled(chosen, prior)
        if why and prior:
            warnings.append(
                f"[consensus repanelled] {key[0]} / {key[1]}: the {chosen['_d'].isoformat()} "
                f"snapshot shows the post-print signature ({why}), so it likely already "
                f"reflects the guide. Stepped back to {prior['_d'].isoformat()}.")
            chosen = prior
        picked[key] = {
            "ltcode": key[0], "fiscal_period": key[1],
            "value": chosen.get("VALUE"), "asof": chosen["_d"].isoformat(),
            "brokers": chosen.get("BROKERCOUNT"),
            "min": chosen.get("MIN"), "max": chosen.get("MAX"),
        }
    if picked:
        warnings.append(f"[consensus pinned] guidance consensus taken as of "
                        f"{cutoff.isoformat()} ({basis}); {len(picked)} metric(s) pinned.")
    return picked, warnings


def _meta(mcp_resp: Any) -> Dict:
    m = _unwrap_mcp_payload(mcp_resp)
    if not isinstance(m, dict):
        return {}
    meta = m.get("meta")
    return meta if isinstance(meta, dict) else {}


_CCY_IN_LABEL_RE = re.compile(
    r"\((?:in\s+)?(?:US\$|USD|EUR|GBP|JPY|KRW|CNY|CHF|SEK|NOK|DKK|HKD|SGD|AUD|CAD|"
    r"INR|BRL|MXN|ZAR|TRY|PLN|HUF|TWD|THB|IDR|\$|\u20ac|\u00a3|\u00a5)\s*[a-zA-Z]{0,2}\)",
    re.I)


def _rows(mcp_resp: Any) -> List[Dict]:
    mcp_resp = _unwrap_mcp_payload(mcp_resp)
    if not mcp_resp or mcp_resp.get("status") == "error":
        return []
    data = mcp_resp.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("guidance", "rows", "success"):
            inner = data.get(key)
            if isinstance(inner, list):
                return inner
    results = mcp_resp.get("results", [])
    if results and isinstance(results, list) and isinstance(results[0], dict):
        inner = results[0].get("data", [])
        if isinstance(inner, list):
            return inner
    return []


def build_estimate_meta(mcp_resp: Dict[str, Any], period: str) -> Dict[str, Dict[str, Any]]:
    meta: Dict[str, Dict[str, Any]] = {}
    _rows_for_period, _ = period_rows(mcp_resp, period)
    for row in _rows_for_period:
        lt = row.get("LTCODE") or row.get("ltcode") or ""
        if not lt or lt in meta:
            continue
        lo = parse_mcp_value(row.get("MIN", row.get("min")))
        hi = parse_mcp_value(row.get("MAX", row.get("max")))
        try:
            brokers = int(row.get("BROKERCOUNT", row.get("brokercount")) or 0)
        except (TypeError, ValueError):
            brokers = 0
        meta[lt] = {"min": lo, "max": hi, "brokers": brokers}
    return meta


_Q_RE = re.compile(r"^[1-4]Q\d{2}$", re.I)
_H_RE = re.compile(r"^[1-2]H\d{2}$", re.I)
_I_RE = re.compile(r"^([1-9]|1[0-2])M\d{2}$", re.I)
_A_RE = re.compile(r"^(FY)?(\d{4}|\d{2})$", re.I)
PERIOD_PREFERENCE = ("quarterly", "half-yearly", "interim", "annual", "other")

_R_PREFIX_RE = re.compile(r"^R-", re.I)
_RESTATED_PERIODS: set = set()

_OWNS_STDOUT = False


def _emit(payload) -> None:
    print(json.dumps(payload), file=(sys.stdout if _OWNS_STDOUT else sys.stderr))


def _norm_period(label) -> str:
    return _R_PREFIX_RE.sub("", str(label or "").strip())


def period_rows(mcp_resp: Dict[str, Any], period: str) -> Tuple[List[Dict[str, Any]], bool]:
    want = str(period or "").strip()
    exact = [r for r in _rows(mcp_resp)
             if str(r.get("FISCALPERIOD") or r.get("fiscal_period") or "").strip() == want]
    if exact:
        return exact, False
    want_n = _norm_period(want)
    if not want_n:
        return [], False
    restated = [r for r in _rows(mcp_resp)
                if _norm_period(r.get("FISCALPERIOD") or r.get("fiscal_period")) == want_n]
    return restated, bool(restated)



def _period_order(label):
    t = str(label or "").strip().upper()
    if t.startswith("R-"):
        t = t[2:]
    m = re.match(r"^(\d)([QH])(\d{2})$", t)
    if m:
        return (int(m.group(3)), int(m.group(1)) * (3 if m.group(2) == "Q" else 6))
    m = re.match(r"^(?:FY)?(\d{2}|\d{4})$", t)
    if m:
        return (int(m.group(1)[-2:]), 12)
    return (-1, -1)


def classify_period(label):
    t = str(label or "").strip()
    if t.upper().startswith("R-"):
        t = t[2:]
    if _Q_RE.match(t):
        return "quarterly"
    if _H_RE.match(t):
        return "half-yearly"
    if _I_RE.match(t):
        return "interim"
    if _A_RE.match(t):
        return "annual"
    return "other"


def available_periods(mcp_resp):
    out = {k: [] for k in PERIOD_PREFERENCE}
    seen = set()
    for row in _rows(mcp_resp):
        fp = str(row.get("FISCALPERIOD") or row.get("fiscal_period") or "").strip()
        if not fp or fp in seen:
            continue
        seen.add(fp)
        out[classify_period(fp)].append(fp)
    for k in out:
        out[k].sort(key=lambda fp: (_period_order(fp), fp), reverse=True)
    return out


def preferred_period(mcp_resp):
    avail = available_periods(mcp_resp)
    for shape in PERIOD_PREFERENCE:
        if avail[shape]:
            return avail[shape][0], shape, avail
    return None, None, avail


def build_period_dict(mcp_resp: Dict[str, Any], period: str) -> Tuple[Dict[str, float], Dict[str, str], Dict[str, Dict[str, Any]], Dict[str, str]]:
    values: Dict[str, float] = {}
    names: Dict[str, str] = {}
    shapes: Dict[str, Dict[str, Any]] = {}
    sources: Dict[str, str] = {}
    _rows_for_period, _restated = period_rows(mcp_resp, period)
    if _restated:
        _RESTATED_PERIODS.add(str(period))
    for row in _rows_for_period:
        lt = row.get("LTCODE") or row.get("ltcode") or ""
        raw = row.get("VALUE", row.get("value"))
        v = parse_mcp_value(raw)
        if v is not None and lt:
            values[lt] = v
            li = row.get("LINEITEM") or row.get("lineitem")
            if li and li != lt:
                names[lt] = li
            if lt not in shapes:
                sh = learn_shape(raw)
                if sh:
                    shapes[lt] = sh
            _src = row.get("SOURCE_DERIVED") or row.get("source_derived")
            if _src and lt not in sources:
                sources[lt] = str(_src)
    return values, names, shapes, sources

def parse_rate_probe(mcp_resp, period):
    matched = parsed = 0
    samples = []
    rows, _ = period_rows(mcp_resp, period)
    for row in rows:
        if not (row.get("LTCODE") or row.get("ltcode")):
            continue
        matched += 1
        raw = row.get("VALUE", row.get("value"))
        if parse_mcp_value(raw) is not None:
            parsed += 1
        elif len(samples) < 3 and raw not in (None, ""):
            samples.append(str(raw))
    return matched, parsed, samples




def compute_variance(actual: Optional[float], estimate: Optional[float],
                     prior: Optional[float], is_pct: bool = False) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "delta_abs": None, "delta_pct": None, "beat_miss": None,
        "yoy_pct": None, "yoy_bps": None,
    }
    if actual is not None and estimate is not None:
        out["delta_abs"] = actual - estimate
        if estimate != 0 and not is_pct:
            out["delta_pct"] = (actual - estimate) / abs(estimate) * 100.0
        if is_pct:
            diff = (actual - estimate)
            out["beat_miss"] = ("Beat" if diff >= PCT_BEAT_MISS_THRESHOLD_PPT
                                else ("Miss" if diff <= -PCT_BEAT_MISS_THRESHOLD_PPT else "In-Line"))
        else:
            if estimate != 0 and out["delta_pct"] is not None:
                dp = out["delta_pct"]
                out["beat_miss"] = "Beat" if dp >= BEAT_MISS_THRESHOLD * 100 else ("Miss" if dp <= -BEAT_MISS_THRESHOLD * 100 else "In-Line")
    if actual is not None and prior is not None:
        if is_pct:
            out["yoy_bps"] = (actual - prior) * 100
        elif prior != 0 and (actual >= 0) == (prior >= 0):
            out["yoy_pct"] = (actual - prior) / abs(prior) * 100.0
        elif prior != 0:
            out["yoy_nm"] = True
    return out





def learn_shape(raw: Any) -> Optional[Dict[str, Any]]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s in ("-", "N/A", "null", "None"):
        return None
    cur = ""
    m = _CUR_RE.match(s)
    if m:
        _tok = m.group(1)
        cur = _tok if _tok in _MINOR_UNITS else _tok.upper()
        s = s[m.end():].strip()
    if cur == "$":
        cur_out = "$"
    elif cur in ("\u20ac", "\u00a3", "\u00a5", "\u20b9", "\u20a9", "\u20bd", "\u20ba"):
        cur_out = cur
    elif cur in ("R$", "A$", "C$", "HK$", "NT$"):
        cur_out = cur
    elif cur == "USD":
        cur_out = "$"
    elif cur == "BRL":
        cur_out = "R$"
    elif cur in _MINOR_UNITS:
        cur_out = cur + " "
    elif cur.isalpha() and len(cur) == 3:
        cur_out = cur.upper() + " "
    else:
        cur_out = ""
    pct = bool(_PCT_RE.search(s))
    if pct:
        s = _PCT_RE.sub("", s).strip()
    mag = ""
    mm = _MAG_RE.search(s)
    if mm:
        mag = mm.group(1).upper()
        s = _MAG_RE.sub("", s).strip()
    dec_part = str(raw).rstrip("%").split(".")[-1] if "." in str(raw) else ""
    dec = len(re.sub(r"\D.*$", "", dec_part))
    _has_frac = "." in str(raw)
    _floor = 2 if (cur_out or _has_frac) else 0
    return {"cur": cur_out, "mag": mag, "pct": pct,
            "dec": (dec if pct else max(_floor, min(4, dec)))}


NOT_APPLICABLE = "\u2014"
NOT_MEANINGFUL = "NM"


def _r(v: float, d: int) -> str:
    return str(Decimal(repr(float(v))).quantize(Decimal(1).scaleb(-d), rounding=ROUND_HALF_UP))


def _sep(t: str) -> str:
    i, _, frac = t.partition(".")
    neg = i.startswith("-")
    i = f"{int(i.lstrip('-')):,}"
    return ("-" if neg else "") + i + ("." + frac if frac else "")


def fmt(val: Optional[float], shape: Optional[Dict[str, Any]], is_delta: bool = False) -> str:
    if val is None or shape is None:
        return "N/A"
    if is_delta:
        sign = "+" if val >= 0 else "-"
        v = abs(val)
    else:
        sign = ""
        v = val
    if shape["pct"]:
        _d = 1 if not shape.get("_widened") else int(shape.get("dec") or 1)
        return f"{sign}{_r(v, _d)}ppt" if is_delta else f"{sign}{_r(v, _d)}%"
    cur = shape["cur"]
    mag = shape["mag"]
    _dm = int(shape.get("dec_mag") or 2)
    if shape.get("_mag_switched"):
        _c = lambda x: "{:,}".format(int(x.split(".")[0])) + ("." + x.split(".")[1] if "." in x else "")
        return f"{sign}{cur}{_c(_r(v / (1e9 if mag == 'B' else 1e6), _dm))}{mag}"
    if mag == "T":
        return f"{sign}{cur}{_r(v/1e12, _dm)}T"
    if mag == "B":
        return f"{sign}{cur}{_r(v/1e9, _dm)}B"
    if mag == "M":
        return f"{sign}{cur}{_r(v/1e6, _dm)}M"
    if mag == "K":
        return f"{sign}{cur}{_r(v/1e3, _dm)}K"
    dec = shape.get("dec", 2)
    dec = 2 if dec is None else int(dec)
    return f"{sign}{cur}{_sep(_r(v, dec))}" if cur else f"{sign}{_sep(_r(v, dec))}"


def quantize(val: Optional[float], shape: Optional[Dict[str, Any]]) -> Optional[float]:
    if val is None or shape is None:
        return val
    s = fmt(abs(val), shape)
    m = re.search(r"([\d,]*\.?\d+)\s*(T|B|M|K)?", s.replace("%", ""))
    if not m:
        return val
    x = float(m.group(1).replace(",", ""))
    x *= {"T": 1e12, "B": 1e9, "M": 1e6, "K": 1e3}.get(m.group(2) or "", 1)
    return -x if val < 0 else x


def _merge_row_shape(cur: Optional[Dict[str, Any]],
                     est: Optional[Dict[str, Any]],
                     pri: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    base = cur or est or pri
    if not base:
        return base
    out = dict(base)
    decs = [int(s["dec"]) for s in (cur, est, pri)
            if s and s.get("dec") is not None]
    if decs:
        out["dec"] = max(decs)
    return out


_MAX_DISPLAY_DEC = 2
_MAX_PCT_DEC = 2


def _verdict_at(a: Optional[float], e: Optional[float], is_pct: bool) -> Optional[str]:
    if a is None or e is None:
        return None
    if is_pct:
        d = a - e
        t = PCT_BEAT_MISS_THRESHOLD_PPT
        return "Beat" if d >= t else ("Miss" if d <= -t else "In-Line")
    if not e:
        return None
    dp = (a - e) / abs(e) * 100.0
    t = BEAT_MISS_THRESHOLD * 100
    return "Beat" if dp >= t else ("Miss" if dp <= -t else "In-Line")


def _row_shape(shape: Optional[Dict[str, Any]],
               actual: Optional[float], estimate: Optional[float],
               prior: Optional[float], is_pct: bool = False) -> Optional[Dict[str, Any]]:
    if not shape:
        return shape
    gaps = [(a, b) for a, b in ((actual, estimate),)
            if a is not None and b is not None and a != b]
    sh = dict(shape)
    is_pct = bool(sh.get("pct"))
    key = "dec" if (is_pct or not sh.get("mag")) else "dec_mag"
    cap = _MAX_PCT_DEC if is_pct else _MAX_DISPLAY_DEC
    _per_share = bool(sh.get("cur")) and not sh.get("mag") and not is_pct
    if is_pct:
        dec = 1
    elif _per_share:
        dec = 2
    else:
        _cur_dec = sh.get(key)
        dec = int(_cur_dec) if _cur_dec is not None else 2
    sh[key] = dec
    _house = dec
    if not gaps:
        return sh
    while dec < cap and any(quantize(a, sh) == quantize(b, sh) for a, b in gaps):
        dec += 1
        sh[key] = dec
    want = _verdict_at(actual, estimate, is_pct)
    if want is not None:
        while dec < cap and _verdict_at(quantize(actual, sh), quantize(estimate, sh), is_pct) != want:
            dec += 1
            sh[key] = dec
    if dec != _house:
        sh["_widened"] = dec
    def _hidden():
        return (any(quantize(a, sh) == quantize(b, sh) for a, b in gaps)
                or (want is not None and _verdict_at(quantize(actual, sh), quantize(estimate, sh), is_pct) != want))
    while key == "dec_mag" and sh.get("mag") in ("T", "B") and _hidden():
        sh["mag"] = {"T": "B", "B": "M"}[sh["mag"]]
        sh["dec_mag"] = 2
        sh["_mag_switched"] = True
    if _hidden():
        sh["_verdict_hidden"] = True
    return sh


def fmt_pct(p: Optional[float]) -> str:
    if p is None:
        return "N/A"
    return f"{'+' if p >= 0 else ''}{_r(p, 1)}%"


def fmt_yoy(var: Dict[str, Any]) -> str:
    if var.get("yoy_bps") is not None:
        bps = var["yoy_bps"]
        return f"{'+' if bps >= 0 else ''}{bps:.0f}bps"
    if var.get("yoy_pct") is not None:
        return fmt_pct(var["yoy_pct"])
    if var.get("yoy_nm"):
        return NOT_MEANINGFUL
    return "N/A"


_PER_UNIT_NAME_RE = re.compile(
    r"/|\bEPS\b|\bDPS\b|\bBVPS\b|\bTBVPS\b|\bPMPM\b|\bPMPY\b|"
    r"\bper\b|per[-\s]|price|ARPU|ARPPU|\bAOV\b|\bASP\b|"
    r"fare|tariff|\brate\b|yield|book value|\bNAV\b|\bNIM\b|premium\b|"
    r"\bRevPAR\b|\bRevPOR\b|\bADR\b", re.I)
_UNIT_SANITY_FLOOR = 100_000.0


def unit_sanity_check(name: str, ltcode: str,
                      values: List[Optional[float]],
                      shape: Optional[Dict[str, Any]]) -> Optional[str]:
    if not shape or not shape.get("cur") or shape.get("pct"):
        return None
    if shape.get("mag"):
        return None
    if str(shape.get("cur") or "").strip() in _MINOR_UNITS:
        return None
    if _PER_UNIT_NAME_RE.search(name or "") or _PER_UNIT_NAME_RE.search(ltcode or ""):
        return None
    present = [abs(v) for v in values if v is not None]
    if not present or max(present) >= _UNIT_SANITY_FLOOR:
        return None
    return (f"[UNIT SANITY] '{name}' ({ltcode}) parsed to a bare-dollar value of "
            f"{max(present):,.4f} with no T/B/M/K suffix — an aggregate currency metric "
            f"below ${_UNIT_SANITY_FLOOR:,.0f} is almost always a magnitude suffix dropped "
            f"while transcribing the MCP VALUE into input.json (e.g. '$1.5425T' retyped as "
            f"'$1.5425'). Re-copy the VALUE strings VERBATIM from the MCP response — "
            f"including the trailing T/B/M/K — into input.json and re-run.")



def resolve_fin_ltcode(metric: Dict, actuals: Dict[str, float],
                       estimates: Dict[str, float], used=()) -> Optional[str]:
    for lt in metric["ltcodes"]:
        if lt in actuals and lt in estimates and lt not in used:
            return lt
    for lt in metric["ltcodes"]:
        if lt in actuals and lt not in used:
            return lt
    return None


def resolve_spine(actuals: Dict[str, float], estimates: Dict[str, float]) -> List[Optional[str]]:
    out: List[Optional[str]] = []
    for m in FINANCIAL_METRICS:
        out.append(resolve_fin_ltcode(m, actuals, estimates, [x for x in out if x]))
    return out


_SHORT_KEYS = {
    "fp": "FISCALPERIOD", "vl": "VALUE",   "lt": "LTCODE",     "li": "LINEITEM",
    "qe": "QUARTERENDDATE", "sd": "SOURCE_DERIVED", "cu": "CURRENCY",
    "mn": "MIN", "mx": "MAX", "bc": "BROKERCOUNT", "cd": "CALL_DATE",
    "mt": "METRIC", "dt": "DATE", "ac": "ADJUSTED_CLOSE", "sr": "SOURCE",
    "gt": "GUIDE_TEXT", "gd": "GUIDEDATE", "sp": "SOURCE_PERIOD",
}


def expand_short_keys(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            full = _SHORT_KEYS.get(k)
            if full is not None:
                if full in obj:
                    raise ValueError(
                        "[ambiguous row] a row carries both '%s' and its alias '%s'. Write one "
                        "spelling or the other, never both - which value is authoritative "
                        "cannot be inferred." % (full, k))
                out[full] = expand_short_keys(v)
            else:
                out[k] = expand_short_keys(v)
        return out
    if isinstance(obj, list):
        return [expand_short_keys(x) for x in obj]
    return obj


def spine_fetch_gate(provenance):
    spine = list(dict.fromkeys(lt for m in FINANCIAL_METRICS for lt in m["ltcodes"]))
    prov = provenance or {}
    requested = list(prov.get("ltcodes_requested") or [])
    if not requested:
        return ["[provenance absent] input.json carries no provenance.ltcodes_requested, so "
                "nothing can tell a ltcode that was never requested from one the issuer does "
                "not report. Add the provenance block (ltcodes_requested, period_probe, "
                "kpi_query, tiers_checked, call_window) - see SKILL.md Step 2."]
    missing = [lt for lt in spine if lt not in set(requested)]
    if not missing:
        return []
    return [f"SPINE CODE NOT FETCHED - {len(missing)} of {len(spine)} financial-spine ltcodes "
            f"were never requested: {', '.join(missing)}. resolve_fin_ltcode selects from the "
            f"payload and cannot reach a code you did not ask for, so the profit row - and "
            f"with it the computed margin row - may be absent or resolved to a lower rung "
            f"than this issuer actually reports. Re-fetch with the full {len(spine)}-code "
            f"ladder and re-run. This is NOT the same as the issuer having no such line; "
            f"that case is reported as 'No ltcode resolved for ...'."]


def period_probe_gate(provenance, resolved_period):
    prov = provenance or {}
    probe = [str(x) for x in (prov.get("period_probe") or [])]
    if not probe:
        return ["[period probe missing] provenance.period_probe is absent, so nothing can "
                "confirm get_actuals was probed WITHOUT fiscal_period. The Step 2 fetch is "
                "filtered to the label already chosen, so a wrong label cannot be detected "
                "downstream. Re-probe and record the FISCALPERIOD labels actually present."]
    if resolved_period and str(resolved_period) not in probe:
        return [f"[period not in probe] the resolved period '{resolved_period}' does not "
                f"appear in provenance.period_probe {probe}. The label was not observed in "
                f"get_actuals; re-probe without fiscal_period and resolve from what is there."]
    shapes = {}
    for lab in probe:
        shapes.setdefault(classify_period(lab), []).append(lab)
    for shape in PERIOD_PREFERENCE:
        if shapes.get(shape):
            if classify_period(str(resolved_period)) != shape:
                return [f"[period shape] '{resolved_period}' is "
                        f"{classify_period(str(resolved_period))}, but {shape} labels "
                        f"{shapes[shape]} were present in the probe. House order is "
                        f"{' > '.join(PERIOD_PREFERENCE)}: a flash grades ONE reporting "
                        f"period against its own consensus, so the shortest available "
                        f"window wins. Re-run with {shapes[shape][0]}."]
            break
    return []


def calendar_shape_gate(mcp_call_date, resolved_period):
    if not resolved_period:
        return []
    if classify_period(str(resolved_period)) != "annual":
        return []
    order = list(PERIOD_PREFERENCE)
    try:
        ann = order.index("annual")
    except ValueError:
        return []
    shorter = set(order[:ann])
    seen = {}
    for row in _rows(mcp_call_date):
        fp = str(row.get("FISCALPERIOD") or row.get("fiscal_period") or "").strip()
        if not fp:
            continue
        sh = classify_period(fp)
        if sh in shorter:
            seen.setdefault(sh, []).append(fp)
    if not seen:
        return []
    labels = sorted({l for v in seen.values() for l in v})
    return [f"[calendar shape] the resolved period '{resolved_period}' is ANNUAL, but "
            f"get_call_date carries shorter-window label(s) {labels} for this ticker. The "
            f"unfiltered get_actuals probe returns only ONE label however it is called, so "
            f"period_probe_gate cannot see a shorter window and stays silent -- the earnings "
            f"calendar is the independent witness. A flash grades ONE reporting period against "
            f"its own consensus, so an annual roll-up shipped in place of {labels[0]} is a "
            f"complete, gate-clean note for the WRONG window. Re-probe get_actuals explicitly "
            f"for {labels[0]} and re-run with it if rows come back."]


def ticker_resolution_gate(provenance, requested_ticker, as_of_date=None):
    prov = provenance or {}
    res = prov.get("ticker_resolution")
    if not isinstance(res, dict) or not res:
        return ["[ticker resolution unrecorded] provenance.ticker_resolution is absent, so "
                "nothing can confirm search_tickerid returned an EXACT ticker match. Record "
                "match_type, returned_ticker, company_name, latest_fiscal_quarter and "
                "latest_quarter_end_date VERBATIM from the search_tickerid row that produced "
                "ticker_id, then re-run."]
    out = []
    mt = str(res.get("match_type") or "").strip().lower()
    got = str(res.get("returned_ticker") or "").strip() or "?"
    nm = str(res.get("company_name") or "").strip() or "?"
    if mt != "exact_ticker":
        out.append(
            "[ticker unresolved] search_tickerid matched " + repr(str(requested_ticker)) +
            " as match_type=" + repr(mt or "unknown") + ", not 'exact_ticker'. It returned "
            + got + " / " + nm + ". A non-exact ticker match means the requested symbol is "
            "NOT in the InSync ticker table and the resolver substituted its nearest "
            "neighbour - measured, that is a DIFFERENT COMPANY, at a score high enough to "
            "look convincing. Confirm the symbol, or resolve ticker_id by hand and pass it "
            "directly. A flash is NEVER rendered on a fuzzy ticker match.")

    lq = str(res.get("latest_fiscal_quarter") or "").strip()
    lqe = str(res.get("latest_quarter_end_date") or "").strip()
    if as_of_date is not None:
        yr = getattr(as_of_date, "year", None)
        if yr and len(lq) >= 4 and lq[-2:].isdigit():
            lq_yr = 2000 + int(lq[-2:])
            if lq_yr < yr - 1:
                out.append(
                    "[ticker row stale] search_tickerid reports latest_fiscal_quarter=" + lq +
                    " for " + got + ", which is more than a year behind as_of " +
                    str(as_of_date) + ". The ticker matched exactly but the row behind it is "
                    "stale or corrupt, so any period resolved from it is untrustworthy. "
                    "Verify coverage for this issuer before rendering.")
        if lqe[:4].isdigit() and int(lqe[:4]) > yr + 1:
            out.append(
                "[ticker row implausible] search_tickerid reports latest_quarter_end_date=" +
                lqe + " for " + got + ", a date in the far future. The row is corrupt; do "
                "not render from it.")
    return out


def call_window_gate(provenance, as_of_date):
    prov = provenance or {}
    win = prov.get("call_window") or {}
    end = win.get("end")
    if not end:
        return ["[call window missing] provenance.call_window.end is absent, so nothing can "
                "confirm get_call_date spanned the FUTURE. Without a forward window the "
                "scheduled-call gate cannot see a print that has already happened for a "
                "period the actuals do not yet carry. Fetch today-120d .. today+120d."]
    try:
        end_d = _dt.date.fromisoformat(str(end)[:10])
    except Exception:
        return [f"[call window unparsed] provenance.call_window.end={end!r} is not an ISO "
                f"date, so the forward-span check could not run."]
    if as_of_date and end_d <= as_of_date:
        return [f"[call window not forward] get_call_date was fetched to {end_d}, which is "
                f"not after the reference date {as_of_date}. The calendar is pre-populated "
                f"with SCHEDULED future calls, and that is the only thing that reveals a "
                f"print the database has not ingested yet. Re-fetch with end_date=today+120d."]
    return []


def kpi_ladder_gate(provenance, kpi_mapping):
    prov, km = provenance or {}, kpi_mapping or {}
    out = []
    q = prov.get("kpi_query")
    if q is None:
        out.append("[kpi query unrecorded] provenance.kpi_query is absent, so nothing can "
                   "confirm search_lineitem was called with queries=\"all\". A descriptive "
                   "query under-samples the tier and renders a thin table that looks complete.")
    elif str(q).strip().lower() != "all":
        out.append(f"[kpi query not 'all'] search_lineitem was called with queries={q!r}. "
                   f"`queries` is a semantic search field, not a label: it must be the "
                   f"literal string \"all\". Re-run the KPI resolution and re-fetch.")
    if km.get("no_kpis_available"):
        tiers = [str(t).lower() for t in (prov.get("tiers_checked") or [])]
        if "keyword" not in tiers:
            out.append(f"[rung 4 skipped] kpi_mapping declares no_kpis_available, but "
                       f"provenance.tiers_checked={tiers} does not include the keyword sweep. "
                       f"Rung 5 is only reachable when rung 4 came back EMPTY. Run the "
                       f"keyword sweep before dropping the operating block.")
    return out


_KPI_ADMISSIBLE_PREFIXES = ("SD_P_", "KM_P_", "SD_S_", "KM_S_")
_KPI_DERIVED_SUFFIXES = ("-Y", "-Q", "-M", "-BY")


def kpi_admission_gate(kpi_mapping):
    km = kpi_mapping or {}
    out = []
    for i in range(1, 9):
        lt = km.get(f"top_kpi_{i}_ltcode")
        if not lt:
            continue
        name = km.get(f"top_kpi_{i}") or lt
        tier = str(km.get(f"top_kpi_{i}_tier") or "").lower()
        if str(lt).endswith(_KPI_DERIVED_SUFFIXES):
            out.append(f"[kpi derived series] '{name}' ({lt}) carries a derived suffix. "
                       f"-Y/-Q/-M/-BY are Y/Y, Q/Q, margin and bps-change series the flash "
                       f"computes itself; admitting one duplicates a generated column. "
                       f"Drop it and backfill from the next qualifying candidate.")
        if tier == "keyword" and not str(lt).startswith(_KPI_ADMISSIBLE_PREFIXES):
            out.append(f"[kpi prefix] '{name}' ({lt}) came from the rung-4 keyword sweep, "
                       f"which searches the UNFILTERED line-item universe. Only "
                       f"{'/'.join(_KPI_ADMISSIBLE_PREFIXES)} codes are admissible there. "
                       f"A tagged tier is exempt - it has already been curated.")
    return out



KPI_TIERS = ("top_kpi", "company_kpi", "industry_kpi", "keyword", "ungraded")
_HEADLINE_TIER = "top_kpi"


_DERIVED_SUFFIX = ("-Y", "-Q", "-M", "-BY")


def is_derived_ltcode(lt):
    return any(str(lt or "").endswith(sfx) for sfx in _DERIVED_SUFFIX)


def kpis_from_mapping(kpi_mapping: Dict[str, Any]) -> List[Dict[str, str]]:
    out = []
    for i in range(1, 9):
        name = kpi_mapping.get(f"top_kpi_{i}")
        lt = kpi_mapping.get(f"top_kpi_{i}_ltcode")
        tier = kpi_mapping.get(f"top_kpi_{i}_tier") or "top_kpi"
        if name and lt:
            if tier not in KPI_TIERS:
                tier = "unknown"
            out.append({"ltcode": lt, "name": name, "tier": tier})
    return out


def registry_label_guard(ltcode, mcp_name):
    entry = METRIC_REGISTRY.get(ltcode or "")
    if not entry:
        return None
    lbl = (entry.get("label") or entry.get("name") or "").strip().lower()
    got = (mcp_name or "").strip().lower()
    if not lbl or not got:
        return None
    a = set(re.sub(r"[^a-z0-9 ]", " ", lbl).split())
    b = set(re.sub(r"[^a-z0-9 ]", " ", got).split())
    if a & b:
        return None
    return (f"[registry label mismatch] {ltcode} is declared as '{entry.get('label') or entry.get('name')}' "
            f"but this ticker returned '{mcp_name}'. Segment/company ltcodes are NOT globally "
            f"unique, so the stored entry has been DISCARDED for this row and its own shape used "
            f"instead. STOP: confirm the row's type and direction before shipping it.")


def qualification_gate(kpis: List[Dict[str, str]], actuals: Dict[str, float],
                       estimates: Dict[str, float],
                       require_estimate: bool = False) -> Tuple[List[Dict], List[str]]:
    kept, dropped = [], []
    for k in kpis:
        if is_derived_ltcode(k.get("ltcode")):
            dropped.append(f"{k.get('name')} ({k.get('ltcode')}): DERIVED series (Y/Y, Q/Q, "
                           f"margin or bps-change) — the flash computes these itself as COLUMNS, "
                           f"so rendering one as a KPI row duplicates them")
            continue
        lt = k["ltcode"]
        has_a = lt in actuals
        has_e = lt in estimates
        if has_a and (has_e or not require_estimate):
            kept.append(k)
        else:
            reason = []
            if not has_a: reason.append("no actual")
            if not has_e: reason.append("no estimate")
            dropped.append(f"{k['name']} ({lt}): {', '.join(reason)}")
    return kept, dropped




def build_market_prices(mcp_md: Dict[str, Any], call_date_iso: str = "") -> Dict[str, str]:
    out = {"lastClosePrice": "N/A"}
    rows = _rows(mcp_md)
    if not rows:
        return out
    pmap: Dict[str, float] = {}
    sym = "$"
    _unknown_ccy: set = set()
    for r in rows:
        d = r.get("DATE") or r.get("date")
        c = r.get("ADJUSTED_CLOSE") or r.get("adjusted_close") or r.get("close")
        ccy = (r.get("CURRENCY") or r.get("currency") or "").strip()
        if ccy:
            sh = learn_shape(f"{ccy}1.00")
            if sh and sh.get("cur"):
                sym = sh["cur"]
            else:
                sym = ccy.upper() + " "
                _unknown_ccy.add(ccy.upper())
        v = parse_mcp_value(c)
        if d and v is not None:
            pmap[d] = v
    if not pmap:
        return out
    out["lastClosePrice"] = f"{sym}{pmap[max(pmap)]:,.2f}"
    if _unknown_ccy:
        out["unknown_currency"] = sorted(_unknown_ccy)
    return out




_LOWER_IS_BETTER_TERMS = (
    r"charge-?off", r"\bNCO\b", r"provision", r"expense", r"efficiency ratio",
    r"churn", r"attrition", r"\bDSO\b", r"\bDPO\b", r"days (inventory|sales|payable)",
    r"net debt", r"leverage ratio", r"\bNPL\b", r"\bNPA\b", r"non.?performing",
    r"delinquen", r"cost of (funds|deposit|revenue|goods|risk)", r"\bcapex\b",
    r"\bETR\b", r"effective tax", r"reserve build",
    r"combined ratio", r"loss ratio", r"operating ratio", r"expense ratio",
    r"\bMLR\b", r"\bMCR\b", r"medical (?:loss|care) ratio", r"cost/income",
    r"\bopex\b", r"\bcosts?\b", r"\bCASM\b", r"\bAISC\b",
    r"unit cash cost", r"net cruise cost",
    r"catastrophe", r"cat loss(?:es)?", r"prior period development",
    r"salar", r"\bbenefits?\b", r"\bwages?\b", r"compensation",
    r"\bsupplies\b", r"\bSG&A\b", r"\bMG&A\b", r"\bR&D\b", r"\bG&A\b",
    r"interest expense", r"\bclaims?\b", r"\bimpairment",
)
_LOWER_IS_BETTER_PAT = re.compile("|".join(_LOWER_IS_BETTER_TERMS), re.I)

_WRONG_SLOT_TERMS = (r"depreciat", r"amorti[sz]", r"\bD&A\b", r"\bDD&A\b")
def _load_metric_registry():
    assets = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets")
    csv_path = os.path.join(assets, "metric_registry.csv")
    if os.path.isfile(csv_path):
        import csv
        out = {}
        try:
            with open(csv_path, "r", encoding="utf-8", newline="") as f:
                for row in csv.DictReader(f):
                    e = {"label": row["label"], "lower_is_better": row["lower_is_better"] == "yes"}
                    if row["pct"] != "":
                        e["pct"] = row["pct"] == "yes"
                    out[row["ltcode"]] = e
        except (OSError, ValueError, KeyError):
            return {}
        return out
    path = os.path.join(assets, "metric_registry.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return (json.load(f) or {}).get("metrics", {}) or {}
    except (OSError, ValueError):
        return {}


METRIC_REGISTRY = _load_metric_registry()


_DIRECTIONS: Dict[str, str] = {}
_HIGHER_IS_BETTER_PAT = re.compile(
    r"revenue|sales|\bARR\b|bookings|backlog|orders|billings|\bRPO\b|performance obligation|"
    r"subscri|members|\busers\b|\bMAU\b|\bDAU\b|customers|deliveries|shipments|volume|units|"
    r"\bincome\b|profit|\bEBIT|margin|\bFFO\b|\bEPS\b|net adds|occupancy|utili[sz]ation|"
    r"comparable|same.store|traffic|\bGMV\b|\bAUM\b|deposits|loans|premiums", re.I)
_TURNS_ROUND_PAT = re.compile(
    r"inactive|churn|\blost\b|cancel|declin|decreas|refund|write.?off|default|delinquen|"
    r"attrition|disconnect|downgrade|deactivat|uncollect", re.I)


def _lower_is_better(name, ltcode):
    if ltcode in _DIRECTIONS:
        return _DIRECTIONS[ltcode] == "lower"
    entry = _registry_entry(ltcode, name)
    if entry is not None and "lower_is_better" in entry:
        return bool(entry["lower_is_better"])
    return bool(_LOWER_IS_BETTER_PAT.search(name or "") or _LOWER_IS_BETTER_PAT.search(ltcode or ""))


def _polarity_declared(name, ltcode):
    if ltcode in _DIRECTIONS:
        return True
    entry = _registry_entry(ltcode, name)
    if entry is not None and "lower_is_better" in entry:
        return True
    if _LOWER_IS_BETTER_PAT.search(name or "") or _LOWER_IS_BETTER_PAT.search(ltcode or ""):
        return True
    return bool(_HIGHER_IS_BETTER_PAT.search(name or "") and not _TURNS_ROUND_PAT.search(name or ""))


_SEGMENT_PREFIXES = ("SD_S_", "KM_S_")


def _registry_entry(ltcode, mcp_name=None):
    entry = METRIC_REGISTRY.get(ltcode or "")
    if entry is None:
        return None
    if str(ltcode or "").startswith(_SEGMENT_PREFIXES) and mcp_name:
        lbl = (entry.get("label") or entry.get("name") or "").strip().lower()
        got = str(mcp_name).strip().lower()
        if lbl and got:
            a = set(re.sub(r"[^a-z0-9 ]", " ", lbl).split())
            b = set(re.sub(r"[^a-z0-9 ]", " ", got).split())
            if not (a & b):
                return None
    return entry


def _registry_pct(ltcode, mcp_name=None):
    entry = _registry_entry(ltcode, mcp_name)
    if entry is not None and "pct" in entry:
        return bool(entry["pct"])
    return None


MODEL_SOURCED_MARK = " \u2020"
_MODEL_SOURCE_PAT = re.compile(r"sell.?side|model source|analyst model|consensus model", re.I)


def is_model_sourced(source: Optional[str]) -> bool:
    return bool(source and _MODEL_SOURCE_PAT.search(str(source)))


def _cap_target(sel) -> int:
    return MAX_KPIS if (len(sel) >= MAX_KPIS
                        and all(k.get("tier") == _HEADLINE_TIER
                                for k in sel[:MAX_KPIS])) else MAX_KPIS - 1


_ADJUSTED_LTCODES = {"IS_P_114", "IS_P_276", "IS_P_210", "IS_P_406", "IS_P_110",
                     "IS_P_118", "IS_P_124", "IS_P_128", "CF_P_209", "CF_P_202", "CF_P_203"}
_REPORTED_LTCODES = {"IS_P_113", "IS_P_117", "IS_P_127", "IS_P_209", "IS_P_109"}
_ADJ_LABEL_RE = re.compile(r"ex\s*-?\s*1x|adjusted|\badj\b|\bFTE\b|\bcore\b", re.I)


def is_adjusted_basis(ltcode: str, name: str = "") -> bool:
    if ltcode in _REPORTED_LTCODES:
        return False
    if ltcode in _ADJUSTED_LTCODES:
        return True
    return bool(_ADJ_LABEL_RE.search(name or ""))


def page_basis(fin_ltcodes, names) -> str:
    graded = [lt for lt in (fin_ltcodes or []) if lt and lt != "IS_P_101"]
    if not graded:
        return "Reported"
    flags = [is_adjusted_basis(lt, (names or {}).get(lt, "")) for lt in graded]
    if all(flags):
        return "Adjusted (Non-GAAP)"
    if not any(flags):
        return "Reported (GAAP)"
    return "Mixed basis (Adjusted and GAAP)"


ROUNDING_NOTES: List[str] = []


def build_metric_row(name: str, ltcode: str, actual: Optional[float],
                     estimate: Optional[float], prior: Optional[float],
                     shape: Optional[Dict[str, Any]] = None,
                     source: Optional[str] = None) -> Optional[Dict[str, str]]:
    if actual is None and estimate is None and prior is None:
        return None
    _reg_pct = _registry_pct(ltcode, name)
    is_pct = _reg_pct if _reg_pct is not None else bool(shape and shape.get("pct"))
    shape = _row_shape(shape, actual, estimate, prior, is_pct)
    if shape and shape.get("_verdict_hidden"):
        ROUNDING_NOTES.append(f"ROUNDING - '{name}' ({ltcode}): at two decimals the printed figures do "
                              f"not carry this row's real surprise (actual {actual!r} vs consensus "
                              f"{estimate!r}); the verdict follows the printed numbers. Check it by hand.")
    actual   = quantize(actual,   shape)
    estimate = quantize(estimate, shape)
    prior    = quantize(prior,    shape)
    var = compute_variance(actual, estimate, prior, is_pct=is_pct)
    if (ltcode.startswith(KPI_TIER_PREFIXES) and var.get("beat_miss") in ("Beat", "Miss", "In-Line")
            and not _polarity_declared(name, ltcode)):
        var["beat_miss"] = NOT_APPLICABLE
    if _lower_is_better(name, ltcode) and var.get("beat_miss") in ("Beat", "Miss"):
        var["beat_miss"] = "Miss" if var["beat_miss"] == "Beat" else "Beat"
    graded = var.get("beat_miss") in ("Beat", "Miss", "In-Line")
    model_sourced = bool(graded and is_model_sourced(source))
    _lb = bool(_lower_is_better(name, ltcode))
    return {
        "lowerIsBetter": _lb,
        "name": name + MODEL_SOURCED_MARK if model_sourced else name,
        "ltcode": ltcode,
        "sourceDerived": source or "",
        "modelSourced": model_sourced,
        "currentActual":   fmt(actual,   shape),
        "currentEstimate": fmt(estimate, shape),
        "priorActual":     fmt(prior,    shape),
        "deltaAbsolute":   fmt(var["delta_abs"], shape, is_delta=True),
        "deltaPercent":    fmt_pct(var["delta_pct"]) if not is_pct else NOT_APPLICABLE,
        "yoyChange":       fmt_yoy(var),
        "beatMiss":        var["beat_miss"] or "N/A",
    }


def build_financials(actuals: Dict[str, float], estimates: Dict[str, float],
                     priors: Dict[str, float], names: Dict[str, str],
                     shapes: Dict[str, Dict[str, Any]],
                     sources: Optional[Dict[str, str]] = None) -> Tuple[List[Dict], List[str]]:
    rows, warnings = [], []
    for metric, lt in zip(FINANCIAL_METRICS, resolve_spine(actuals, estimates)):
        if lt is None:
            warnings.append(f"No ltcode resolved for {metric['name']} (tried {metric['ltcodes']})")
            continue
        display_name = names.get(lt, metric["name"])
        row = build_metric_row(display_name, lt,
                               actuals.get(lt), estimates.get(lt), priors.get(lt),
                               shapes.get(lt), (sources or {}).get(lt))
        if row is None:
            warnings.append(f"Omitted {metric['name']} ({lt}) — all data columns N/A")
            continue
        rows.append(row)
    return rows, warnings


def build_kpis(selected: List[Dict[str, str]], actuals: Dict[str, float],
               estimates: Dict[str, float], priors: Dict[str, float],
               names: Dict[str, str],
               shapes: Dict[str, Dict[str, Any]],
               sources: Optional[Dict[str, str]] = None) -> List[Dict]:
    rows = []
    for k in selected:
        lt = k["ltcode"]
        display = names.get(lt, k["name"])
        row = build_metric_row(display, lt,
                               actuals.get(lt), estimates.get(lt), priors.get(lt),
                               shapes.get(lt), (sources or {}).get(lt))
        if row is not None:
            rows.append(row)
    return rows



def main(in_path: str, out_dir: str, require_estimate: bool = False,
         allow_unit_sanity: bool = False) -> int:
    os.makedirs(out_dir, exist_ok=True)
    with open(in_path, "r", encoding="utf-8") as f:
        inp = json.load(f)
    _DIRECTIONS.clear()
    _DIRECTIONS.update({k: v for k, v in (inp.get("kpi_direction") or {}).items() if v in ("lower", "higher")})

    ticker = inp.get("ticker", "").upper()
    period = inp.get("period", "")
    prior_period = inp.get("prior_period", "")
    if not ticker or not period:
        print("ERROR: input.json missing 'ticker' or 'period'", file=sys.stderr)
        return 2

    mcp_raw = expand_short_keys(inp.get("mcp_responses", {}) or {})
    mcp: Dict[str, Any] = {k: _unwrap_mcp_payload(v) for k, v in mcp_raw.items()}
    kpi_map = _unwrap_mcp_payload(inp.get("kpi_mapping"))
    kpi_override = inp.get("kpi_override")

    if not kpi_map and ticker:
        kpi_map = _self_fetch_kpi_mapping(ticker) or {}

    cur_actuals,   cur_names,   cur_shapes,   cur_sources = build_period_dict(mcp.get("get_actuals", {}),   period)
    estimates,     est_names,   est_shapes,   _est_sources = build_period_dict(mcp.get("get_estimates", {}), period)
    prior_actuals, prior_names, prior_shapes, _pri_sources = (
        build_period_dict(mcp.get("get_actuals", {}), prior_period) if prior_period else ({}, {}, {}, {}))
    _pm, _pp, _psamp = parse_rate_probe(mcp.get("get_actuals", {}), period)
    if _pm > 0 and _pp == 0:
        print(f"ERROR: [values unparseable] {_pm} get_actuals row(s) matched FISCALPERIOD "
              f"'{period}', but NOT ONE of their VALUE strings parsed. The period label is "
              f"CORRECT - do not re-probe it. Every downstream stage would build zero rows "
              f"and the renderer would fail with 'financialMetrics must be a non-empty "
              f"array'. Unparsed samples: {', '.join(repr(x) for x in _psamp) or 'n/a'}. "
              f"The usual cause is a currency token absent from _CUR_RE in run_math.py "
              f"(add it to the 3-letter alternation); the other is a VALUE column that is "
              f"not a number at all.", file=sys.stderr)
        return 5

    names: Dict[str, str] = {**prior_names, **est_names, **cur_names}
    shapes: Dict[str, Dict[str, Any]] = {
        _lt: _merge_row_shape(cur_shapes.get(_lt), est_shapes.get(_lt), prior_shapes.get(_lt))
        for _lt in set(cur_shapes) | set(est_shapes) | set(prior_shapes)}

    warnings: List[str] = []

    _bench_candidates: List[Dict[str, str]] = []
    kpi_source = "unknown"
    has_mapping = bool(kpi_map) and bool(kpi_map.get("top_kpi_1_ltcode"))
    mapping_ticker = (kpi_map.get("ticker") or "").upper() if isinstance(kpi_map, dict) else ""
    if mapping_ticker and mapping_ticker != ticker:
        warnings.append(f"[KPI mapping ticker mismatch] input ticker={ticker} mapping ticker={mapping_ticker} — using mapping anyway")

    if has_mapping:
        candidates = kpis_from_mapping(kpi_map)
        if kpi_override:
            master_lts = [c["ltcode"] for c in candidates]
            override_extras = [lt for lt in kpi_override if lt not in master_lts]
            if override_extras:
                warnings.append(f"[KPI override IGNORED] Master is authoritative. LLM tried to add {override_extras} — discarded. Using master top_kpi_1..5: {master_lts}")
        selected, dropped = qualification_gate(candidates, cur_actuals, estimates, require_estimate)
        _sel_lts = {k["ltcode"] for k in selected}
        _bench_candidates = [k for k in candidates
                             if k.get("ltcode") not in _sel_lts
                             and k.get("ltcode") in cur_actuals
                             and not is_derived_ltcode(k.get("ltcode"))]
        for d in dropped:
            warnings.append(f"[KPI dropped from master] {d} — LLM should have fetched actuals/estimates for this ltcode")
        if selected:
            kpi_source = "get_kpi_mapping" if len(selected) >= MIN_KPIS else "get_kpi_mapping_partial"
        else:
            master_lts = [c["ltcode"] for c in candidates]
            selected = []
            kpi_source = "no_operating_kpis"
            _n_act = len([c for c in candidates if c["ltcode"] in cur_actuals])
            _why = (f"{_n_act} had a reported actual but none had a consensus" if _n_act
                    else "had a reported actual")
            warnings.append(
                f"NO OPERATING KPIs — none of the {len(candidates)} mapped KPI(s) ({master_lts}) "
                f"{_why}, so the operating block is OMITTED entirely (no blank "
                f"rows). Escalate before shipping: search_lineitem with filter='company_kpi', "
                f"then filter='industry_kpi', then a keyword sweep with no filter. If nothing "
                f"resolves, re-run with kpi_mapping.no_kpis_available=true. State in "
                f"flashVerdict.net that no operating KPIs are available for this ticker.")
    elif kpi_override:
        selected = [{"ltcode": lt, "name": names.get(lt, lt)} for lt in kpi_override]
        kpi_source = "llm_override_no_master"
    elif isinstance(kpi_map, dict) and kpi_map.get("no_kpis_available"):
        selected = []
        kpi_source = "no_kpis_tagged"
        _tiers = kpi_map.get("tiers_checked") or "top_kpi, company_kpi, industry_kpi"
        warnings.append(
            f"NO TAGGED KPIs — the operating block is OMITTED by design. Tiers checked and "
            f"empty: {_tiers}. The Key Financial Metrics block still carries revenue, profit, "
            f"margin and EPS vs consensus. State in flashVerdict.net that no operating KPIs "
            f"are tagged for this ticker.")
    else:
        qualified = [
            {"ltcode": lt, "name": names.get(lt, lt)}
            for lt in sorted(estimates.keys())
            if lt in cur_actuals and any(lt.startswith(p) for p in KPI_TIER_PREFIXES)
        ][:10]
        result = {
            "kpi_selection_needed": True,
            "qualified_kpis": qualified,
            "reason": "No kpi_mapping provided; use search_lineitem + re-run with kpi_override",
            "warnings": warnings,
        }
        with open(os.path.join(out_dir, "computed.json"), "w") as f:
            json.dump(result, f, indent=2)
        _emit({"kpi_selection_needed": True, "message": result["reason"]})
        return 0

    _fin_lts = [lt for lt in resolve_spine(cur_actuals, estimates) if lt]
    _profit_lt = _fin_lts[1] if len(_fin_lts) > 1 else None
    _collide = [k for k in selected if k["ltcode"] in set(_fin_lts)]
    if _collide:
        warnings.append("KPI(s) already shown in Key Financial Metrics, removed from the "
                        "operating table to avoid a duplicate row: "
                        + ", ".join(f"{k['name']} ({k['ltcode']})" for k in _collide))
        selected = [k for k in selected if k["ltcode"] not in set(_fin_lts)]

    _fin_sig = {}
    for _lt in _fin_lts:
        _sh = shapes.get(_lt) or {}
        if cur_actuals.get(_lt) is not None and prior_actuals.get(_lt) is not None:
            _fin_sig[(cur_actuals[_lt], prior_actuals[_lt], _sh.get("cur"), bool(_sh.get("pct")))] = \
                f"{names.get(_lt, _lt)} ({_lt})"
    _rep = []
    for _k in list(selected):
        _sh = shapes.get(_k["ltcode"]) or {}
        _hit = _fin_sig.get((cur_actuals.get(_k["ltcode"]), prior_actuals.get(_k["ltcode"]),
                             _sh.get("cur"), bool(_sh.get("pct"))))
        if _hit:
            selected = [k for k in selected if k is not _k]
            _rep.append(f"{_k['name']} ({_k['ltcode']}) repeats {_hit}")
    if _rep:
        warnings.append("KPI repeating a Key Financial Metrics row removed: "
                        + "; ".join(_rep) + ".")

    _seen_sig: Dict[Any, str] = {}
    _dupes: List[str] = []
    _kept: List[Dict[str, Any]] = []
    for _k in selected:
        _lt = _k["ltcode"]
        _a, _e, _p = cur_actuals.get(_lt), estimates.get(_lt), prior_actuals.get(_lt)
        _sh = shapes.get(_lt) or {}
        if _a is None or _e is None or _p is None:
            _kept.append(_k)
            continue
        _sig = (_a, _e, _p, _sh.get("cur"), _sh.get("mag"), bool(_sh.get("pct")))
        _first = _seen_sig.get(_sig)
        if _first is None:
            _seen_sig[_sig] = f"{_k['name']} ({_lt})"
            _kept.append(_k)
        else:
            _dupes.append(f"{_k['name']} ({_lt}) duplicates {_first}")
    if _dupes:
        _spare = len(_kept) > _cap_target(_kept)
        selected = _kept
        warnings.append(
            "DUPLICATE KPI removed — the following ltcode(s) returned an identical actual, "
            "estimate AND prior to a row already in the table: " + "; ".join(_dupes) + ". "
            + ("The freed slot was backfilled from the next qualifying candidate."
               if _spare else
               "There was NO spare candidate to backfill with, so the operating table is one "
               "row SHORTER rather than showing the same number twice. To refill it, widen "
               "the Step 1 fetch: top_kpi often returns more than five entries and the whole "
               "list is the bench, or descend to company_kpi / industry_kpi."))

    financial_metrics, fin_warn = build_financials(cur_actuals, estimates, prior_actuals, names, shapes, cur_sources)
    warnings.extend(fin_warn)
    _page_basis = page_basis(_fin_lts, names)
    if _page_basis != "Adjusted (Non-GAAP)":
        warnings.append(
            f"[basis] This page is {_page_basis}, not the Adjusted (Non-GAAP) basis the flash "
            f"normally carries — one or more spine rows resolved to a reported/GAAP rung "
            f"({', '.join(f'{lt}={names.get(lt, lt)}' for lt in _fin_lts if lt) or 'no spine ltcode resolved'}). The footer "
            f"stamp and the margin row label follow the data, so the page is honest; check the "
            f"prose says the same thing before it goes out.")
    margin_row = build_margin_row(cur_actuals, estimates, prior_actuals, names)
    if margin_row is not None:
        idx = next((i for i, r in enumerate(financial_metrics)
                    if r.get("ltcode") == _profit_lt), len(financial_metrics) - 1)
        financial_metrics.insert(idx + 1, margin_row)
    operating_metrics = build_kpis(selected, cur_actuals, estimates, prior_actuals, names, shapes, cur_sources)
    operating_metrics.sort(key=lambda r: 0 if (r.get("currentEstimate") not in
                                               (None, "", "N/A", NOT_APPLICABLE)) else 1)
    _tier_by_lt = {k["ltcode"]: (k.get("tier") or "unknown") for k in selected}
    for _m in operating_metrics:
        _m["tier"] = _tier_by_lt.get(_m.get("ltcode"), "unknown")
    _clean_sweep = (len(operating_metrics) >= MAX_KPIS
                    and all(r.get("tier") == _HEADLINE_TIER
                            for r in operating_metrics[:MAX_KPIS])
                    and all(r.get("currentEstimate") != "N/A"
                            for r in operating_metrics[:MAX_KPIS]))
    _cap = MAX_KPIS if _clean_sweep else MAX_KPIS - 1
    _capped_rows = []
    if len(operating_metrics) > _cap:
        _capped_rows = operating_metrics[_cap:]
        _cut = [r["name"] for r in _capped_rows]
        operating_metrics = operating_metrics[:_cap]
        warnings.append(
            f"ROW CAP — {_cap} operating rows shown. Five are reserved for a clean top_kpi "
            f"sweep (all five tagged top_kpi and all five with consensus); this run did not "
            f"qualify, so it settles at four. Trimmed: {', '.join(_cut)}.")
    for _lt, _co in (inp.get("company_reported") or {}).items():
        _ins = cur_actuals.get(_lt)
        if _ins is not None and _co and abs(_ins - _co) / abs(_co) > 0.05:
            warnings.append(f"INSYNC VS COMPANY - {names.get(_lt, _lt)} ({_lt}): InSync's figure is "
                            f"{_ins:,.4g}, the company reported {_co:,.4g} ({(_ins - _co) / abs(_co) * 100:+.1f}%). "
                            f"The page prints InSync's. Check which is right before sending.")
    _on_page = [r["name"] for r in financial_metrics + operating_metrics]
    warnings.extend(n for n in ROUNDING_NOTES if any(p.startswith(n.split("'")[1]) for p in _on_page))
    ROUNDING_NOTES.clear()

    bench_metrics = []
    _table_lts = {r.get("ltcode") for r in operating_metrics}
    for _k in _bench_candidates:
        if _k.get("ltcode") in _table_lts:
            continue
        _row = build_metric_row(names.get(_k["ltcode"], _k.get("name") or _k["ltcode"]),
                                _k["ltcode"], cur_actuals.get(_k["ltcode"]), None,
                                prior_actuals.get(_k["ltcode"]),
                                shapes.get(_k["ltcode"]), cur_sources.get(_k["ltcode"]))
        if _row is not None:
            _row["benchReason"] = "no consensus"
            _row["tier"] = _k.get("tier") or "unknown"
            bench_metrics.append(_row)
    for _row in _capped_rows:
        if _row.get("ltcode") in _table_lts:
            continue
        _r = dict(_row)
        _r["benchReason"] = "row cap"
        bench_metrics.append(_r)
    if bench_metrics:
        warnings.append(
            "[bench] " + str(len(bench_metrics)) + " KPI(s) are off the scorecard but kept in "
            "computed.json.benchMetrics with actual, prior and Y/Y: "
            + "; ".join(f"{b.get('name')} ({b.get('benchReason')})" for b in bench_metrics)
            + ". They carry NO consensus, so never write a beat, miss or delta for one — "
            "quote the reported value and its Y/Y only.")

    _bench = [(m["name"], m.get("tier") or "unknown")
              for m in operating_metrics if (m.get("tier") or "unknown") != _HEADLINE_TIER]
    if _bench:
        _by_tier: Dict[str, List[str]] = {}
        for _n, _t in _bench:
            _by_tier.setdefault(_t, []).append(_n)
        warnings.append(
            "KPI bench used - top_kpi could not supply 5 rows with consensus, so "
            + str(len(_bench)) + " row(s) came from lower rungs: "
            + "; ".join(f"{_t} -> {', '.join(_ns)}" for _t, _ns in sorted(_by_tier.items()))
            + ". The five-row table is reserved for a clean top_kpi sweep, so this run "
              "correctly settles at four.")

    _built_lts = {m.get("ltcode") for m in operating_metrics}
    _capped_lts = {r.get("ltcode") for r in _capped_rows}
    _dropped = [k["name"] for k in selected
                if k["ltcode"] not in _built_lts and k["ltcode"] not in _capped_lts]
    if _dropped:
        warnings.append("Operating KPI(s) dropped — no data returned for: " + ", ".join(_dropped))

    _emeta = build_estimate_meta(mcp.get("get_estimates", {}), period)
    _MIN_BROKERS_FOR_RANGE = 2
    _BASIS_DELTA_PCT = 15.0
    _BASIS_DELTA_PPT = 2.0
    _SOFT_DELTA_PCT = 5.0
    _SOFT_DELTA_PPT = 1.0
    for row in financial_metrics + operating_metrics:
        lt = row.get("ltcode", "")
        m = _emeta.get(lt)
        if not m:
            continue
        a, lo, hi, nb = cur_actuals.get(lt), m["min"], m["max"], m["brokers"]
        e = estimates.get(lt)
        if (a is not None and e is not None and lo is not None and hi is not None
                and nb >= _MIN_BROKERS_FOR_RANGE and (a > hi or a < lo)):
            _pct = _registry_pct(lt, cur_names.get(lt))
            if _pct is None:
                _pct = bool((shapes.get(lt) or {}).get("pct"))
            if _pct:
                _big, _mag, _unit = abs(a - e) >= _BASIS_DELTA_PPT, abs(a - e), "ppt"
            else:
                _mag = abs(a - e) / abs(e) * 100.0 if e else 0.0
                _big, _unit = _mag >= _BASIS_DELTA_PCT, "%"
            _soft = (abs(a - e) >= _SOFT_DELTA_PPT) if _pct else (_mag >= _SOFT_DELTA_PCT)
            if not _big and _soft:
                warnings.append(
                    f"OUTSIDE BROKER RANGE — '{row.get('name')}' ({lt}) actual {a:,.4g} sits "
                    f"outside the entire published range ({lo:,.4g} to {hi:,.4g}, {nb} brokers) "
                    f"at {_mag:,.1f}{_unit} from consensus. Below the "
                    f"{_BASIS_DELTA_PCT:,.0f}% / {_BASIS_DELTA_PPT:,.1f}ppt basis-mismatch bar, "
                    f"so this is a heads-up, not a stop: confirm the row reads as intended.")
            if _big:
                warnings.append(
                    f"LARGE SURPRISE, VERIFY BASIS — '{row.get('name')}' ({lt}) actual is "
                    f"{_mag:,.1f}{_unit} from consensus AND outside the entire broker range "
                    f"(actual {a:,.4g} vs range {lo:,.4g} to {hi:,.4g} across {nb} brokers). "
                    f"A surprise this size beyond every published estimate is more often a "
                    f"basis mismatch (reported vs managed, FTE vs non-FTE, adjusted vs GAAP) "
                    f"than a real result. Confirm the basis before shipping this row.")

    _model = [f"{row.get('name')} ({row.get('ltcode')}) — {row.get('sourceDerived')}"
              for row in financial_metrics + operating_metrics if row.get("modelSourced")]
    if _model:
        warnings.append(
            "MODEL-SOURCED ACTUAL — the ACTUAL on the following graded row(s) came from a "
            "sell-side model, not from the company, and is being graded against broker "
            "consensus as though it were reported. The row is marked "
            f"'{MODEL_SOURCED_MARK.strip()}' on the page and footnoted. Confirm the figure "
            "against the filing before this goes out, and do not describe it in the prose as "
            "something the company reported: " + "; ".join(_model))

    unit_errors: List[str] = []
    for row in financial_metrics + operating_metrics:
        _lg = registry_label_guard(row.get("ltcode", ""), names.get(row.get("ltcode", "")))
        if _lg:
            warnings.append(_lg)
    for row in financial_metrics + operating_metrics:
        lt = row.get("ltcode", "")
        err = unit_sanity_check(
            row.get("name", ""), lt,
            [cur_actuals.get(lt), estimates.get(lt), prior_actuals.get(lt)],
            shapes.get(lt))
        if err:
            unit_errors.append(err)
    if unit_errors and allow_unit_sanity:
        warnings.append(
            "UNIT SANITY DOWNGRADED (--allow-unit-sanity) — " + str(len(unit_errors)) +
            " value(s) look like a dropped magnitude suffix and the block was overridden ON "
            "PURPOSE. Each one is listed below; confirm every figure against the MCP response "
            "before this goes out, and prefer adding the metric to _PER_UNIT_NAME_RE so the "
            "next run is right by default: " + " | ".join(unit_errors))
        unit_errors = []
    if unit_errors:
        result = {
            "unit_check_failed": True,
            "unit_errors": unit_errors,
            "reason": ("Magnitude suffix (T/B/M/K) appears to have been dropped while "
                       "transcribing MCP VALUE strings into input.json. Fix the flagged "
                       "values (copy them VERBATIM from the MCP response) and re-run. "
                       "Rendering was BLOCKED to avoid misstating a figure."),
            "warnings": warnings,
        }
        with open(os.path.join(out_dir, "computed.json"), "w") as f:
            json.dump(result, f, indent=2)
        _emit({"unit_check_failed": True, "errors": unit_errors})
        print(f"UNIT SANITY FAILED ({len(unit_errors)}):", file=sys.stderr)
        for e in unit_errors:
            print(f"  - {e}", file=sys.stderr)
        return 4

    _SYM2ISO = {"$": "USD", "\u00a3": "GBP", "\u20ac": "EUR", "\u00a5": "JPY",
                "\u20b9": "INR", "\u20a9": "KRW", "\u20bd": "RUB", "\u20ba": "TRY"}

    _MINOR2MAJOR = {"USCENTS": "USD", "USDC": "USD", "GBPENCE": "GBP", "GBP": "GBP",
                    "ZARC": "ZAR", "EURC": "EUR"}

    def _iso_ccy(raw):
        c = str(raw or "").strip()
        if not c:
            return None
        if c == "GBp":
            return "GBP"
        u = c.upper()
        if u in _MINOR2MAJOR:
            return _MINOR2MAJOR[u]
        return _SYM2ISO.get(c, u if c.isalpha() and len(c) == 3 else c)

    def _row_ccy(lt):
        sh = shapes.get(lt) or {}
        if sh.get("pct"):
            return None
        return _iso_ccy(sh.get("cur"))

    _spine_ccys = [c for c in (_row_ccy(r.get("ltcode", "")) for r in financial_metrics) if c]
    _inferred_ccy = Counter(_spine_ccys).most_common(1)[0][0] if _spine_ccys else None
    _declared_ccy = _iso_ccy((inp.get("provenance") or {}).get("reported_currency"))
    reported_ccy = _declared_ccy or _inferred_ccy

    _ccy_errors: List[str] = []

    _converted_tools = [t for t in ("get_actuals", "get_estimates")
                        if _meta(mcp.get(t, {})).get("conversion")]
    if _converted_tools:
        _suspects = sorted({
            f"{r.get('LTCODE')} ({r.get('LINEITEM')})"
            for t in ("get_actuals", "get_estimates") for r in _rows(mcp.get(t, {}))
            if _CCY_IN_LABEL_RE.search(str(r.get("LINEITEM") or ""))})
        _rates = "; ".join(
            f"{c.get('currency')}@{c.get('rate')} on {c.get('date')}"
            for t in _converted_tools for c in _meta(mcp.get(t, {})).get("conversion", []))
        _ccy_errors.append(
            "the MCP CONVERTED this payload - " + ", ".join(_converted_tools) + " returned a "
            f"meta.conversion block ({_rates}). A converted scorecard cannot be graded: actuals "
            "and estimates convert at DIFFERENT dates, so the FX drift lands inside every delta "
            "while the arithmetic still closes. Re-fetch with the offending ltcode removed from "
            "the LTCODE_LIST. " + (f"Likely offender(s), by label: {', '.join(_suspects)}. "
                                   if _suspects else
                                   "No ltcode names a currency in its label, so probe the KPI "
                                   "candidates against an IS_P_101 anchor to find it. ") +
            "A foreign-currency KPI is not junk - fetch it ALONE and render it as its own "
            "labelled row, never mixed into the spine.")

    _payload_converted = bool(_ccy_errors) or bool(
        _declared_ccy and _inferred_ccy and _inferred_ccy != _declared_ccy)
    if _declared_ccy and _inferred_ccy and _inferred_ccy != _declared_ccy:
        _ccy_errors.append(
            f"the payload resolved in {_inferred_ccy} but this issuer reports in "
            f"{_declared_ccy}. The MCP converted the fetch - almost always because a "
            f"foreign-currency ltcode (e.g. a line labelled '(US$M)') shared the "
            f"LTCODE_LIST with the spine. Remove it and re-fetch; do not grade a converted "
            f"scorecard, because actuals and estimates convert at different dates and the FX "
            f"drift lands inside every delta.")
    _foreign_ok = set(inp.get("foreign_ltcodes") or [])
    if reported_ccy and not _payload_converted:
        for row in financial_metrics + [r for r in operating_metrics
                                        if r.get("ltcode", "") not in _foreign_ok]:
            _lt = row.get("ltcode", "")
            _c = _row_ccy(_lt)
            if _c and _c != reported_ccy:
                _ccy_errors.append(
                    f"'{row.get('name')}' ({_lt}) resolved in {_c} but this issuer reports in "
                    f"{reported_ccy}. A mixed-currency scorecard makes every delta meaningless, "
                    f"and one foreign-currency ltcode in the fetch silently redenominates the "
                    f"WHOLE payload. Drop {_lt} from the LTCODE_LIST and re-run, or fetch it "
                    f"alone and render it as its own labelled row.")
    if _ccy_errors:
        _emit({"currency_split": True, "errors": _ccy_errors,
               "warnings": warnings + [f"[currency split] {e}" for e in _ccy_errors]})
        print(f"CURRENCY COHERENCE FAILED ({len(_ccy_errors)}):", file=sys.stderr)
        for e in _ccy_errors:
            print(f"  - {e}", file=sys.stderr)
        return 6

    call_date = inp.get("call_date_iso") or _extract_call_date(mcp.get("get_call_date", {}), period,
                                                               inp.get("as_of"))
    market_prices = build_market_prices(mcp.get("get_market_data", {}), call_date)

    _val_suppressed: List[str] = []
    valuation = _format_valuation(mcp.get("get_valuation", {}), _val_suppressed)
    warnings.extend(_val_suppressed)

    _mkt_ccy = None
    for _mr in _rows(mcp.get("get_market_data", {})):
        _mc = _iso_ccy(_mr.get("CURRENCY"))
        if _mc:
            _mkt_ccy = _mc
            break
    suppress_price_multiples = bool(
        reported_ccy and _mkt_ccy and _mkt_ccy != reported_ccy)
    if suppress_price_multiples:
        valuation = {}
        warnings.append(
            f"[valuation currency] the price is quoted in {_mkt_ccy} but this issuer reports "
            f"in {reported_ccy}. Every price-based multiple (P/E, P/B, P/S, EV/*, dividend "
            f"and FCF yields) divides one currency by the other, so the ENTIRE valuation "
            f"anchor has been suppressed. Margin, leverage, coverage and liquidity ratios are "
            f"currency-immune and remain quotable. Last Close still prints, in its own currency.")

    _home_ccy = _iso_ccy(inp.get("home_currency")) if inp.get("home_currency") else None
    if _home_ccy and _mkt_ccy:
        _second_listing = {"CNY": ("HKD",)}
        _ok_ccy = {c for c in (reported_ccy, _home_ccy, "USD") if c} | set(_second_listing.get(_home_ccy, ()))
        if _mkt_ccy not in _ok_ccy and market_prices.get("lastClosePrice") not in (None, "N/A"):
            warnings.append(
                f"[price listing] get_market_data quotes this ticker in {_mkt_ccy} "
                f"({market_prices.get('lastClosePrice')}), which is neither the reporting currency "
                f"({reported_ccy}), the home-market currency ({_home_ccy}) nor USD. It is most likely "
                f"a depositary receipt or cross-listing, not the company's share, so Last Close has "
                f"been left off the page.")
            market_prices["lastClosePrice"] = "N/A"
            market_prices["priceSuppressed"] = _mkt_ccy

    _sub = inp.get("insync_subindustry") or ""
    if not _sub:
        for _r in _rows(mcp.get("search_tickerid", {})):
            if _r.get("insync_subindustry"):
                _sub = _r["insync_subindustry"]
                break
    _sector = sector_group(_sub)
    _val_order = valuation_order(_sector)
    valuation_line = build_valuation_line(valuation, _val_order)
    if not valuation_line and not suppress_price_multiples:
        valuation_line = build_valuation_line(valuation, valuation_order(_sector, fallback=True))
    if not valuation_line and not suppress_price_multiples:
        warnings.append(
            f"[no valuation] get_valuation returned nothing usable for the {_sector} chain "
            f"({valuation_request_metrics(_sector)}). Every candidate was absent, "
            f"uncomputable for this issuer, or suppressed by a sanity check, so the anchor "
            f"line is omitted rather than half-filled. Measured: 8951 JP returns zero rows "
            f"for all twelve metrics. Nothing is computed to fill the gap.")

    _all_rows = list(financial_metrics) + list(operating_metrics)

    _today_d = _today(inp)
    _allow_stale = bool(inp.get("allow_stale_period"))
    _stale_errors = []
    _g1 = scheduled_call_gate(mcp.get("get_call_date", {}), mcp.get("get_actuals", {}),
                              period, _today_d)
    if _g1:
        if _g1.startswith("[staleness gate INACTIVE]"):
            warnings.append(_g1)
        else:
            (warnings if _allow_stale else _stale_errors).append(_g1)
    _g2 = quarter_end_age_gate(mcp.get("get_actuals", {}), period, _today_d)
    if _g2:
        (warnings if _allow_stale else _stale_errors).append(_g2)
    _g3 = document_tree_corroboration(mcp.get("get_document_tree", {}), period)
    if _g3:
        warnings.append(_g3)

    _avail = available_periods(mcp.get("get_actuals", {}))
    _shape = classify_period(period)
    _pretty = "; ".join(f"{k}: {', '.join(v[:4])}" for k, v in _avail.items() if v) or "none"
    if _avail["quarterly"] and _shape != "quarterly":
        warnings.append(
            f"[period shape] period='{period}' is {_shape}, but QUARTERLY data exists for this "
            f"ticker ({', '.join(_avail['quarterly'][:4])}). House order reads quarterly first, "
            f"then half-yearly, then annual — confirm this is deliberate.")

    if _avail["half-yearly"] and _shape == "quarterly" and not _avail["quarterly"]:
        _sug = _avail["half-yearly"][0]
        warnings.append(
            f"[quarter label on a half-yearly reporter] period='{period}' is a QUARTER, but this "
            f"ticker reports in halves ({', '.join(_avail['half-yearly'][:4])}) and has no "
            f"quarterly actuals at all. get_document_tree and search_tickerid label documents by "
            f"quarter even when the financials are half-yearly — BHP's tree says 4Q26 while its "
            f"actuals are 1H26/2H26. Re-run with period='{_sug}'.")
    if not _all_rows:
        _filtered = not any(_avail[k] for k in _avail)
        _pm2, _pp2, _ps2 = parse_rate_probe(mcp.get("get_actuals", {}), period)
        if _pm2 > 0 and _pp2 == 0:
            warnings.append(
                f"[values unparseable] ZERO metric rows were built, and the period label is "
                f"NOT the cause — {_pm2} get_actuals row(s) DID match FISCALPERIOD "
                f"'{period}', but not one of their VALUE strings parsed. Do not re-probe the "
                f"period. Unparsed samples: "
                f"{', '.join(repr(x) for x in _ps2) or 'n/a'}. The usual cause is a currency "
                f"token missing from _CUR_RE in run_math.py; the other is a VALUE column that "
                f"is not a number.")
        else:
            warnings.append(
                f"[period unmatched] ZERO metric rows were built — period='{period}' ({_shape}) "
                f"matched no FISCALPERIOD in get_actuals. "
                + ("The payload came back EMPTY. Note this says nothing about what the ticker "
                   "reports: Step 2 fetches get_actuals filtered to fiscal_period='<period>,"
                   "<prior_period>', so a label that does not exist returns zero rows and leaves "
                   "nothing here to list. Re-probe with get_actuals for the same ltcode list and "
                   "NO fiscal_period argument to see the labels that do exist. "
                   if _filtered else f"Periods actually present -> {_pretty}. ")
                + "Half-yearly reporters are labelled 1H/2H and annual reporters a bare year; take "
                "the label from get_actuals, never from search_tickerid.latest_fiscal_quarter.")
    if _RESTATED_PERIODS:
        warnings.append(
            "[restated period used] No exact FISCALPERIOD match existed for "
            + ", ".join(sorted(_RESTATED_PERIODS))
            + ", so the RESTATED ('R-') label was used instead (v7.4.1). Exact labels always "
              "win; this fired only because there were none. The figures are the company's own "
              "restatement, which is usually what you want for a prior-year comparator — but "
              "say so if the prose leans on the YoY."
        )
    if all(r.get("priorActual") in (None, "", "N/A") for r in _all_rows):
        warnings.append(
            f"[prior period unmatched] All {len(_all_rows)} rows have priorActual=N/A, so the "
            f"prior column and every YoY cell on this page are dead. prior_period="
            f"'{prior_period}' matched no FISCALPERIOD in get_actuals — including after the "
            f"restated-label ('R-') fallback, which v7.4.1 applies automatically. "
            f"⛔ Do NOT re-fetch with an 'R-' prefixed fiscal_period: the MCP rejects it "
            f"(validation_error, 'Invalid fiscal period'). Re-fetch WITHOUT fiscal_period and "
            f"take the prior label from the FISCALPERIOD values actually returned. "
            + ("The payload lists no periods at all, which reflects the "
               "fiscal_period filter Step 2 sent, NOT what the ticker reports."
               if not any(_avail[k] for k in _avail)
               else f"Periods actually present -> {_pretty}.")
        )

    for _tool in ("get_actuals", "get_estimates", "get_guidance", "get_market_data",
                  "get_estimates_guidance", "get_estimates_guidance_history"):
        _m = _mcp_meta(mcp_raw.get(_tool))
        if _m.get("has_more"):
            warnings.append(
                f"[truncated fetch] {_tool} returned has_more=true with "
                f"{_m.get('remaining_items', 'unknown')} row(s) unread at limit="
                f"{_m.get('limit', '?')}. Page with offset until has_more=false and re-run — "
                f"otherwise the note is built on a partial payload.")

    warnings.extend(ticker_resolution_gate(inp.get("provenance"), ticker, _today_d))
    warnings.extend(spine_fetch_gate(inp.get("provenance")))
    warnings.extend(period_probe_gate(inp.get("provenance"), period))
    warnings.extend(call_window_gate(inp.get("provenance"), _today_d))
    warnings.extend(calendar_shape_gate(mcp.get("get_call_date", {}), period))
    warnings.extend(kpi_ladder_gate(inp.get("provenance"), inp.get("kpi_mapping")))
    warnings.extend(kpi_admission_gate(inp.get("kpi_mapping")))

    _guid_warns, _guidance_status = guidance_freshness_gate(
        mcp.get("get_guidance", {}), call_date, inp.get("guidance_source"),
        (inp.get("provenance") or {}).get("guidance_attempts"))
    warnings.extend(_guid_warns)

    _release_file = (inp.get("provenance") or {}).get("release_file")
    _gc_resp = mcp.get("get_estimates_guidance") or mcp.get("get_estimates_guidance_history")
    if _gc_resp:
        _guidance_consensus, _gc_warns = pick_pre_print_consensus(
            _gc_resp, call_date, _release_file)
        warnings.extend(_gc_warns)
    else:
        _guidance_consensus = {}
        if _guidance_status.get("state") != "empty":
            warnings.append(
                "[consensus not pinned] no get_estimates_guidance payload in mcp_responses, so "
                "the `vs. consensus of` figures in the Guidance Snapshot are whatever the live "
                "fetch returned. Brokers revise toward a guide within days of hearing it, so a "
                "live comparator can already reflect the guide it is measuring. Measured on "
                "a hardware maker's FY27 revenue: $165.00B live against $150.00B on the day, which "
                "flipped the note's headline from a 7.3% beat to a 2.4% miss.")

    if market_prices.get("unknown_currency"):
        warnings.append(
            "[currency token unknown] get_market_data reported CURRENCY="
            + ", ".join(market_prices["unknown_currency"])
            + ", which is not in _CUR_RE. The price box now prints the code rather than "
              "defaulting to '$', so the page is honest — but any get_actuals VALUE carrying "
              "the same token will FAIL TO PARSE and its row will be dropped. Add the code to "
              "the 3-letter alternation in run_math.py and re-run before shipping.")

    if (_rows(mcp.get("get_market_data", {})) and market_prices.get("lastClosePrice") == "N/A"
            and not market_prices.get("priceSuppressed")):
        warnings.append(
            "[market series unparsed] get_market_data returned rows but no price resolved. "
            "The close field is ADJUSTED_CLOSE (not CLOSE) and the date field is DATE; a "
            "renamed key yields a silent 'Last Close: N/A' box.")

    if _stale_errors:
        try:
            os.makedirs(out_dir, exist_ok=True)
            with open(os.path.join(out_dir, "computed.json"), "w", encoding="utf-8") as _f:
                json.dump({"stale_period": True, "errors": _stale_errors,
                           "stale_facts": getattr(scheduled_call_gate, "facts", {})}, _f, indent=2)
        except Exception:
            pass
        _emit({"stale_period": True, "errors": _stale_errors,
               "stale_facts": getattr(scheduled_call_gate, "facts", {})})
        print("\n".join("ERROR: " + e for e in _stale_errors), file=sys.stderr)
        return 1

    if not financial_metrics:
        _em, _ep, _es = parse_rate_probe(mcp.get("get_actuals", {}), period)
        _why = (f"{_em} row(s) matched FISCALPERIOD '{period}' but only {_ep} of their VALUE "
                f"strings parsed" + (f" (unparsed samples: "
                f"{', '.join(repr(x) for x in _es)})" if _es else "")) if _em else \
               f"no get_actuals row matched FISCALPERIOD '{period}'"
        print(f"ERROR: [no financial rows] the financial spine resolved ZERO rows, which "
              f"_render_internal.js rejects outright ('financialMetrics must be a non-empty "
              f"array'). Diagnosis: {_why}. If VALUE strings failed to parse, the usual cause "
              f"is a currency token missing from _CUR_RE in run_math.py — the period label is "
              f"not at fault. If the rows matched and parsed, the financial-spine ltcodes were "
              f"not in LTCODE_LIST for this fetch.", file=sys.stderr)
        try:
            os.makedirs(out_dir, exist_ok=True)
            with open(os.path.join(out_dir, "computed.json"), "w", encoding="utf-8") as _f:
                json.dump({"no_financial_rows": True, "warnings": warnings,
                           "parse_matched": _em, "parse_ok": _ep}, _f, indent=2)
        except Exception:
            pass
        return 5

    result = {
        "_computed_by": "run_math",
        "_computed_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "ticker": ticker,
        "currentPeriod": period,
        "priorPeriod": prior_period,
        "earningsDate": call_date,
        "financialMetrics": financial_metrics,
        "operatingMetrics": operating_metrics,
        "marketPrices": market_prices,
        "valuation": valuation,
        "valuationLine": valuation_line,
        "basis": _page_basis,
        "kpi_source": kpi_source,
        "kpi_count": len(operating_metrics),
        "guidanceStatus": _guidance_status,
        "guidanceConsensus": list(_guidance_consensus.values()),
        "provenance": inp.get("provenance") or {},
        "benchMetrics": bench_metrics,
        "warnings": warnings,
    }
    with open(os.path.join(out_dir, "computed.json"), "w") as f:
        json.dump(result, f, indent=2)

    print(f"OK: {len(financial_metrics)} financials, {len(operating_metrics)} KPIs, source={kpi_source}", file=sys.stderr)
    if warnings:
        print(f"Warnings ({len(warnings)}):", file=sys.stderr)
        for w in warnings:
            print(f"  - {w}", file=sys.stderr)
    return 0


def _self_fetch_kpi_mapping(ticker: str) -> Optional[Dict[str, Any]]:
    return None


_PERIOD_MAX_AGE_DAYS = {"quarterly": 135, "half-yearly": 260, "annual": 455, "other": 455}


def _today(inp=None):
    if isinstance(inp, dict) and inp.get("as_of"):
        try:
            return _dt.date.fromisoformat(str(inp["as_of"])[:10])
        except ValueError:
            pass
    return _dt.date.today()


_MAX_END_TO_CALL_DAYS = {"quarterly": 75, "half-yearly": 120, "annual": 200, "other": 200}


def scheduled_call_gate(mcp_cd, mcp_actuals, period, today):
    rows = _rows(mcp_cd)
    past = []
    for r in rows:
        fp = str(r.get("FISCALPERIOD") or r.get("fiscal_period") or "").strip()
        cd = str(r.get("CALL_DATE") or r.get("call_date") or "")[:10]
        if not cd:
            continue
        try:
            d = _dt.date.fromisoformat(cd)
        except ValueError:
            continue
        if d <= today:
            past.append((d, fp))
    if not past:
        return None
    call_d, call_fp = max(past)

    qed = _period_end_date(mcp_actuals, period)
    if qed is None:
        return ("[staleness gate INACTIVE] No QUARTERENDDATE on the '%s' actuals rows, so the "
                "reported-but-not-loaded check could not run. Confirm manually that '%s' is the "
                "latest REPORTED period before shipping." % (period, period))
    since = sorted({d for d, _ in past if d > qed})
    if len(since) > 1:
        have = sorted({str(r.get("FISCALPERIOD") or r.get("fiscal_period") or "").strip()
                       for r in _rows(mcp_actuals)} - {""})
        days = (today - since[-1]).days
        when = "TODAY" if days == 0 else f"{days} day(s) ago"
        _fp = next((f for d, f in past if d == since[-1] and f), "") or ""
        scheduled_call_gate.facts = {
            "reported_period": _fp,
            "reported_on": since[-1].isoformat(),
            "days_ago": days,
            "resolved_period": str(period),
            "resolved_period_end": qed.isoformat(),
            "periods_available": have,
            "calls_since_period_end": [d.isoformat() for d in since],
        }
        return (f"REPORTED BUT NOT LOADED — {len(since)} earnings calls have taken place since "
                f"'{period}' ended on {qed.isoformat()} "
                f"({', '.join(d.isoformat() for d in since)}). Only ONE should follow a period: "
                f"its own. The most recent was {when}, so a LATER period has already reported. "
                f"get_actuals still carries only: {', '.join(have) or 'nothing'}. Building on "
                f"'{period}' would ship a complete, clean-looking note for the WRONG QUARTER. "
                f"Wait for the new period to load and re-run, or pass --allow-stale-period to "
                f"publish the prior quarter deliberately and say so in flashVerdict.net.")

    shape = classify_period(period)
    gap = (call_d - qed).days
    limit = _MAX_END_TO_CALL_DAYS.get(shape, 200)
    if gap <= limit:
        return None

    have = sorted({str(r.get("FISCALPERIOD") or r.get("fiscal_period") or "").strip()
                   for r in _rows(mcp_actuals)} - {""})
    days = (today - call_d).days
    when = "TODAY" if days == 0 else f"{days} day(s) ago"
    label = f" (labelled {call_fp})" if call_fp else ""
    return (f"REPORTED BUT NOT LOADED — the most recent earnings call was {call_d.isoformat()} "
            f"({when}){label}, which is {gap} days after '{period}' ended on {qed.isoformat()} — "
            f"far beyond the ~{limit}-day reporting lag for a {shape} reporter. That call "
            f"belongs to a LATER print than the one resolved here. get_actuals currently "
            f"carries: {', '.join(have) or 'nothing'}. Building on '{period}' would ship a "
            f"complete, clean-looking note for the WRONG QUARTER. Wait for the new period to "
            f"load and re-run, or pass --allow-stale-period to publish the prior quarter "
            f"deliberately and say so in flashVerdict.net.")


def _period_end_date(mcp_actuals, period):
    for r in _rows(mcp_actuals):
        if str(r.get("FISCALPERIOD") or r.get("fiscal_period") or "").strip() != str(period).strip():
            continue
        v = str(r.get("QUARTERENDDATE") or r.get("quarterenddate") or "")[:10]
        if v:
            try:
                return _dt.date.fromisoformat(v)
            except ValueError:
                pass
    return None


def quarter_end_age_gate(mcp_actuals, period, today):
    qed = _period_end_date(mcp_actuals, period)
    if qed is None:
        return None
    shape = classify_period(period)
    limit = _PERIOD_MAX_AGE_DAYS.get(shape, 455)
    age = (today - qed).days
    if age <= limit:
        return None
    return (f"STALE PERIOD — '{period}' ({shape}) ended {qed.isoformat()}, {age} days ago, past "
            f"the {limit}-day limit for that shape. A newer print has almost certainly occurred; "
            f"get_actuals is likely mid-ingest or the period was resolved from a stale source. "
            f"Confirm against get_call_date before shipping. Override with --allow-stale-period.")


def document_tree_corroboration(mcp_tree, period):
    txt = json.dumps(_unwrap_mcp_payload(mcp_tree) or {})
    found = sorted(set(re.findall(r"\b([1-4]Q\d{2}|[1-2]H\d{2})\b", txt)))
    if not found or str(period).strip() in found:
        return None
    return (f"[document tree disagrees] The filed-document tree shows {', '.join(found[:4])} but "
            f"the period resolved to '{period}'. Documents and actuals load on separate paths, so "
            f"this is advisory — but if a NEWER quarter appears above, treat it as a sign the "
            f"actuals are still loading.")


def _extract_call_date(mcp_cd: Dict[str, Any], period: str, today: Optional[str] = None) -> Optional[str]:
    rows = _rows(mcp_cd)
    for row in rows:
        if str(row.get("FISCALPERIOD") or row.get("fiscal_period") or "").strip() == str(period).strip():
            return row.get("CALL_DATE") or row.get("call_date")
    dates = sorted(d for d in (str(r.get("CALL_DATE") or r.get("call_date") or "") for r in rows) if d)
    if today:
        dates = [d for d in dates if d[:10] <= str(today)[:10]]
    return dates[-1] if dates else None


def _valuation_row_date(row: Dict[str, Any]) -> Optional[str]:
    for k, v in row.items():
        if isinstance(k, str) and k.strip().lower() == "date" and v is not None:
            s = str(v).strip()
            return s.split("T")[0] if s else None
    return None


def _newest_valuation_rows(rows: List[Dict], _suppressed=None) -> List[Dict]:
    best: Dict[str, Any] = {}
    order: List[str] = []
    collapsed = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        m = row.get("metric") or row.get("METRIC") or ""
        if not m:
            continue
        d = _valuation_row_date(row)
        if m not in best:
            best[m] = (d, row)
            order.append(m)
            continue
        collapsed.add(m)
        prev_d = best[m][0]
        if d is not None and (prev_d is None or d > prev_d):
            best[m] = (d, row)
    if collapsed and _suppressed is not None:
        _dates = sorted({d for d, _r in best.values() if d})
        _suppressed.append(
            "[valuation vintage] get_valuation returned multiple vintages for "
            + ", ".join(sorted(collapsed))
            + ". The NEWEST row per metric was used"
            + (" (as-of %s)" % _dates[-1] if _dates else "")
            + "; older vintages were ignored. The documented Step 2 call passes no date "
              "range and returns one vintage per metric — pass none unless you mean to.")
    return [best[m][1] for m in order]


def _format_valuation(mcp_val: Dict[str, Any], _suppressed=None) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if _suppressed is None:
        _suppressed = []
    for row in _newest_valuation_rows(_rows(mcp_val), _suppressed):
        m = row.get("metric") or row.get("METRIC") or ""
        v = row.get("value") if row.get("value") is not None else row.get("VALUE")
        fv = parse_mcp_value(v)
        if not m or fv is None:
            continue
        if "yield" in m.lower():
            pct = fv * 100.0
            if pct > 30.0:
                _suppressed.append(
                    f"[valuation unit check] {m} = {fv:,.4g} implies a {pct:,.1f}% yield, "
                    f"which is outside any plausible range. get_valuation returns a FRACTION "
                    f"(0.0192 = 1.92%, verified live), so a value this large is either already "
                    f"a percentage or a bad row. SUPPRESSED from the anchor line — confirm the "
                    f"unit before quoting a yield.")
                continue
            _yd = 1
            while _yd < 3 and pct != 0 and round(pct, _yd) == 0:
                _yd += 1
            out[m] = f"{pct:.{_yd}f}%"
            continue
        _w = valuation_sanity(m, fv)
        if _w:
            _suppressed.append(_w)
            continue
        out[m] = f"{fv:.1f}x"
    return out


_VALUATION_LABELS = {
    "EV/EBITDAX_Adj": "EV/EBITDAX", "EV/EBITDAX": "EV/EBITDAX", "EV/DACF": "EV/DACF",
    "EV/EBITDA_Adj": "EV/EBITDA",   "EV/EBITDA": "EV/EBITDA",
    "P/OCF_Ratio": "P/OCF",         "P/FCF_Ratio": "P/FCF",
    "P/TBV_Ratio": "P/TBV",         "P/B_Ratio": "P/B",
    "P/AFFO_Ratio": "P/AFFO",       "P/FFO_Ratio": "P/FFO", "P/NAV_Ratio": "P/NAV",
    "P/S_Ratio": "P/S",
    "P/E_Adj_Ratio": "NTM P/E",     "P/E_Ratio": "NTM P/E",
    "Net_Debt_EBITDA_Ratio": "net debt/EBITDA",
    "FCF_Yield": "FCF yield",       "Dividend_Yield": "dividend yield",
}

_SECTOR_CHAINS = {
    "energy":     ["EV/EBITDAX_Adj", "EV/EBITDAX", "EV/DACF", "EV/EBITDA_Adj", "EV/EBITDA",
                   "P/OCF_Ratio", "P/FCF_Ratio", "FCF_Yield", "Net_Debt_EBITDA_Ratio",
                   "Dividend_Yield"],
    "financials": ["P/TBV_Ratio", "P/B_Ratio", "P/E_Adj_Ratio", "P/E_Ratio",
                   "Dividend_Yield"],
    "reit":       ["P/AFFO_Ratio", "P/FFO_Ratio", "P/NAV_Ratio", "P/OCF_Ratio",
                   "P/FCF_Ratio", "EV/EBITDA_Adj", "EV/EBITDA", "Dividend_Yield",
                   "P/B_Ratio"],
    "default":    ["EV/EBITDA_Adj", "EV/EBITDA", "P/E_Adj_Ratio", "P/E_Ratio", "FCF_Yield",
                   "Dividend_Yield"],
}

_SECTOR_FALLBACK = {
    "energy":     [],
    "financials": [],
    "reit":       [],
    "default":    ["P/B_Ratio", "P/S_Ratio"],
}


_SECTOR_PATTERNS = [
    ("energy",     re.compile(r"oil|gas|refin|energy|coal|oilfield", re.I)),
    ("reit",       re.compile(r"\breit\b|real estate invest", re.I)),
    ("financials", re.compile(r"\bbank|insur", re.I)),
]


def sector_group(subindustry: Optional[str]) -> str:
    s = str(subindustry or "")
    for name, pat in _SECTOR_PATTERNS:
        if pat.search(s):
            return name
    return "default"


def _load_valuation_catalogue() -> List[str]:
    try:
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "assets", "valuation_catalogue.json")
        return list(json.load(open(p)).get("metrics") or [])
    except Exception:
        return []


def valuation_request_metrics(group: str) -> str:
    cat = set(_load_valuation_catalogue())
    chain = _SECTOR_CHAINS.get(group, _SECTOR_CHAINS["default"])
    live = [m for m in chain if not cat or m in cat]
    return ",".join(live)


def valuation_order(group: str, fallback: bool = False) -> List[Tuple[str, str]]:
    cat = set(_load_valuation_catalogue())
    src = (_SECTOR_FALLBACK if fallback else _SECTOR_CHAINS).get(
        group, _SECTOR_FALLBACK["default"] if fallback else _SECTOR_CHAINS["default"])
    return [(m, _VALUATION_LABELS.get(m, m)) for m in src if not cat or m in cat]


def compute_p_ffo(last_close: Optional[float], ffo_ps_ntm: Optional[float]) -> Optional[float]:
    if not last_close or not ffo_ps_ntm or ffo_ps_ntm <= 0:
        return None
    r = last_close / ffo_ps_ntm
    return r if 0 < r < 200 else None


_VALUATION_BOUNDS = {
    "P/E_Ratio": (1.0, 300.0),          "P/E_Adj_Ratio": (1.0, 300.0),
    "PEG_Ratio": (0.01, 20.0),          "PEG_Ratio_Adj": (0.01, 20.0),
    "EV/EBITDA": (0.5, 200.0),          "EV/EBITDA_Adj": (0.5, 200.0),
    "EV/EBITDAX": (0.5, 200.0),         "EV/EBITDAX_Adj": (0.5, 200.0),
    "EV/DACF": (0.5, 200.0),            "EV/Sales": (0.05, 100.0),
    "EV/EBIT": (0.5, 300.0),            "EV/EBIT_Adj": (0.5, 300.0),
    "P/B_Ratio": (0.05, 100.0),         "P/TBV_Ratio": (0.05, 100.0),
    "P/S_Ratio": (0.05, 100.0),
    "P/OCF_Ratio": (0.5, 200.0),        "P/FCF_Ratio": (0.5, 300.0),
    "P/FFO": (1.0, 200.0),              "P/FFO_Ratio": (1.0, 200.0),
    "P/AFFO_Ratio": (1.0, 200.0),       "P/NAV_Ratio": (0.05, 20.0),
    "Net_Debt_EBITDA_Ratio": (0.0, 30.0),
}


_NEVER_NEGATIVE_MULTIPLES = {"P/B_Ratio", "P/S_Ratio", "P/FCF_Ratio", "P/OCF_Ratio",
                             "EV/Sales", "EV/EBITDA", "EV/EBIT", "EV/Gross_Profit"}


def valuation_sanity(metric, value):
    lo, hi = _VALUATION_BOUNDS.get(metric, (None, None))
    if lo is None or value is None:
        return None
    if value < 0 and metric in _NEVER_NEGATIVE_MULTIPLES:
        return (f"[valuation sign] {metric} = {value:,.4g}x has a NEGATIVE denominator and has "
                f"been SUPPRESSED from the anchor line. The multiple is arithmetically real "
                f"but carries no information: say so in prose if it matters, never print it "
                f"as a multiple.")
    if lo <= abs(value) <= hi:
        return None
    return (f"[valuation unit check] {metric} = {value:,.4g}x is outside the plausible range "
            f"{lo}x-{hi}x and has been SUPPRESSED from the anchor line. This is almost always a "
            f"currency-subunit mismatch — a price in GBP against an EPS in pence yields a P/E "
            f"100x too small (measured on Investec). Verify the basis before quoting a multiple.")


def build_valuation_line(val: Dict[str, str],
                         order: Optional[List[Tuple[str, str]]] = None) -> str:
    order = valuation_order("default") if order is None else order
    _seen_labels = set()
    parts = []
    for k, lbl in order:
        if k == "Dividend_Yield" or not val.get(k) or lbl in _seen_labels:
            continue
        _seen_labels.add(lbl)
        parts.append(f"{val[k]} {lbl}")
        if len(parts) >= 3:
            break
    if not parts:
        return ""
    line = "Trades " + (" and ".join(parts) if len(parts) < 3
                        else ", ".join(parts[:-1]) + " and " + parts[-1])
    dy = val.get("Dividend_Yield")
    if dy:
        line += f"; {dy} dividend yield"
    return line + "."


def build_margin_row(cur_actuals: Dict[str, float], estimates: Dict[str, float],
                     prior_actuals: Dict[str, float],
                     names: Optional[Dict[str, str]] = None) -> Optional[Dict[str, str]]:
    rev_lt = resolve_fin_ltcode(FINANCIAL_METRICS[0], cur_actuals, estimates)
    eb_lt = resolve_fin_ltcode(FINANCIAL_METRICS[1], cur_actuals, estimates)
    if not rev_lt or not eb_lt:
        return None
    def marg(d: Dict[str, float]) -> Optional[float]:
        rev, eb = d.get(rev_lt), d.get(eb_lt)
        return (eb / rev * 100.0) if (rev and eb and rev != 0) else None
    a = marg(cur_actuals)
    p = marg(prior_actuals)
    rev_e, eb_e = estimates.get(rev_lt), estimates.get(eb_lt)
    e = (eb_e / rev_e * 100.0) if (rev_e and eb_e and rev_e != 0) else None
    _pname = (names or {}).get(eb_lt) or ""
    _base = re.sub(r"\s*\(\s*Ex\s*-?\s*1x[^)]*\)", "", _pname, flags=re.I).strip() or "EBITDA"
    _suffix = " (adj)" if is_adjusted_basis(eb_lt, _pname) else ""
    _label = (f"{_base} Margin{_suffix}" if _base.lower() != "ebitda"
              else f"EBITDA Margin{_suffix}")
    return build_metric_row(_label, "COMPUTED", a, e, p,
                            {"pct": True, "cur": "", "mag": "", "dec": 1})


if __name__ == "__main__":
    _OWNS_STDOUT = True
    if len(sys.argv) < 3:
        print("Usage: python run_math.py <input.json> <output_dir>", file=sys.stderr)
        sys.exit(2)
    try:
        sys.exit(main(sys.argv[1], sys.argv[2]))
    except ValueError as _e:
        json.dump({"status": "error", "message": "short_key_ambiguous - %s" % _e}, sys.stdout)
        sys.stdout.write("\n")
        sys.exit(1)
