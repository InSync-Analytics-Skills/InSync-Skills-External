#!/usr/bin/env python3
"""
fetch_plan.py - offline planner for the capital-allocation-strategy skill.

Given ONE ticker, it reads the company master (if one is supplied; none is bundled) and the sector registry and
prints (a) the resolved master row, (b) the matched sector profile (priority levers,
leverage metric, return framework, focus metrics), (c) the recommended fiscal periods
(5 annual years + latest quarter), and (d) the exact LTCODE fetch list to paste into
get_actuals. No network, no LLM math.

Usage:
    python fetch_plan.py TICKER

stdout: comma-joined LTCODE list (easy to paste into get_actuals).
stderr: the full human-readable plan.

Company master override: export INSYNC_COMPANY_MASTER=/abs/path/to/company_master.csv
"""
import csv
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

sys.path.insert(0, HERE)
from ltcodes import FETCH_LTCODES, SECTOR_LTCODES, fetch_codes, CF, BS, IS


def _master_path():
    return os.environ.get(
        "INSYNC_COMPANY_MASTER", os.path.join(ROOT, "common", "company_master.csv")
    )


def _sector_registry():
    with open(os.path.join(ROOT, "assets", "capital_allocation_sectors.json")) as f:
        return json.load(f)


def load_master_row(ticker):
    path = _master_path()
    if not os.path.exists(path):
        return None
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            if row.get("ticker", "").upper() == ticker.upper():
                return row
    return None


def match_sector(row, registry):
    industry = (row.get("industry") or "").strip() if row else ""
    classification = (row.get("classification") or "").strip() if row else ""
    for key, prof in registry.items():
        if key.startswith("_"):
            continue
        if classification and classification in prof.get("match_classifications", []):
            return key, prof
    for key, prof in registry.items():
        if key.startswith("_"):
            continue
        if classification and classification in (prof.get("exclude_classifications") or []):
            continue
        if industry and industry in prof.get("match_industries", []):
            return key, prof
    return "_default", registry["_default"]


_PERIOD_RX = re.compile(
    r"^\s*(?:R\d*-)?"
    r"(?:(?P<q>[1-4])Q(?P<qy>\d{2}|\d{4})"
    r"|(?P<qy2>\d{2}|\d{4})Q(?P<q2>[1-4])"
    r"|Q(?P<q3>[1-4])[-/](?P<qy3>\d{2}|\d{4})"
    r"|(?P<h>[12])H(?P<hy>\d{2}|\d{4})"
    r"|(?:FY)?(?P<y>\d{2}|\d{4}))"
    r"(?P<est>E)?\s*$", re.I)


def _yr4(y):
    y = int(y)
    return y + 2000 if y < 100 else y


def parse_period(token):
    m = _PERIOD_RX.match(str(token or ""))
    if not m:
        return None
    g = m.groupdict()
    if g["q"]:
        return (_yr4(g["qy"]), int(g["q"]))
    if g["q2"]:
        return (_yr4(g["qy2"]), int(g["q2"]))
    if g["q3"]:
        return (_yr4(g["qy3"]), int(g["q3"]))
    if g["h"]:
        return (_yr4(g["hy"]), int(g["h"]) * 2)
    if g["y"]:
        return (_yr4(g["y"]), None)
    return None


def recommended_periods(latest_quarter):
    parsed = parse_period(latest_quarter)
    if parsed is None:
        return {"annual": [], "quarter": latest_quarter or "",
                "note": ("could not read %r as a fiscal period, so no annual window is "
                         "recommended - supply the latest fiscal quarter as 4Q25 or 2025"
                         % (latest_quarter or "")) }
    yr = parsed[0]
    years = [str(y) for y in range(yr - 4, yr + 1)]
    return {"annual": years, "quarter": latest_quarter}


def _last8(lq):
    parsed = parse_period(lq)
    if parsed is None or parsed[1] is None:
        return []
    y, q = parsed[0], parsed[1]
    out = []
    for _ in range(8):
        out.append("%dQ%02d" % (q, y % 100))
        q -= 1
        if q == 0:
            q = 4
            y -= 1
    return out


def main():
    if len(sys.argv) < 2:
        sys.exit("Usage: python fetch_plan.py TICKER")
    ticker = sys.argv[1].upper()
    registry = _sector_registry()
    row = load_master_row(ticker)

    if row is None:
        skey, prof = "_default", registry["_default"]
        print(",".join(fetch_codes(None)))
        sys.stderr.write(
            f"\n[fetch_plan] {ticker} NOT in company master.\n"
            f"  -> Resolve ticker_id, industry, latest_quarter via search_tickerid, then\n"
            f"     use the generic sector profile below and the full LTCODE list on stdout.\n"
            f"  Sector: {prof['label']} (generic fallback)\n"
            f"  Priority levers: {', '.join(prof['priority_levers'])}\n"
            f"  Leverage metric: {prof['leverage_metric']} | Return framework: {prof['roic_framework']}\n"
        )
        sys.exit(3 if os.path.exists(_master_path()) else 0)

    skey, prof = match_sector(row, registry)
    periods = recommended_periods(row.get("latest_quarter", ""))
    peers = [row.get(f"peer_{i}", "").strip() for i in (1, 2, 3)]
    peers = [p for p in peers if p]

    print(",".join(fetch_codes(skey)))

    e = sys.stderr.write
    e("\n" + "=" * 72 + "\n")
    e(f" CAPITAL ALLOCATION - FETCH PLAN for {ticker}\n")
    e("=" * 72 + "\n")
    e(f" Company     : {row.get('company_name')}\n")
    e(f" ticker_id   : {row.get('ticker_id')}\n")
    e(f" Industry    : {row.get('industry')}  |  Classification: {row.get('classification')}\n")
    e(f" Latest qtr  : {row.get('latest_quarter')}\n")
    e(f" Peers       : {', '.join(peers) if peers else '(none in master - AI-select 3-5)'}\n")
    e("-" * 72 + "\n")
    e(f" SECTOR PROFILE: {prof['label']}  (matched key: {skey})\n")
    e(f"   Priority levers  : {', '.join(prof['priority_levers'])}\n")
    e(f"   Leverage metric  : {prof['leverage_metric']}\n")
    e(f"   Return framework : {prof['roic_framework']}\n")
    if prof.get("focus_metrics"):
        e(f"   Focus metrics    : {', '.join(prof['focus_metrics'])}\n")
    if prof.get("suppress"):
        e(f"   Suppressed       : {', '.join(prof['suppress'])}\n")
    e(f"   Lens             : {prof['narrative_lens']}\n")
    e("-" * 72 + "\n")
    e(" RECOMMENDED FISCAL PERIODS (get_actuals):\n")
    e(f"   Annual (5y): {','.join(periods['annual']) or '(derive from search_tickerid)'}\n")
    e(f"   Latest qtr : {periods['quarter']}   <- for current balance sheet / leverage\n")
    e("-" * 72 + "\n")
    q8 = _last8(row.get("latest_quarter", ""))
    e("-" * 72 + "\n")
    e(" MONITORING & FORWARD FETCHES (for the new sections):\n")
    e(f"   Quarterly (8q, monitor + LTM shift): get_actuals ltcode=CF_P_108,CF_P_109,CF_P_101,CF_P_104\n")
    e(f"     fiscal_period={','.join(q8) if q8 else '<last 8 quarters>'}  -> raw_data.actuals_quarterly\n")
    e("   Forward guidance: get_guidance(latest=true) -> capital-allocation rows (capex, buyback/dividend posture, leverage target)\n")
    e("   Consensus: get_estimates ltcode=CF_P_109,CF_P_110,IS_P_128 fiscal_period=<FY+1>,<FY+2> -> forward_outlook.rows\n")
    e("   Management commentary: parse_document_content on the latest transcript -> 2-3 verbatim capital-allocation quotes\n")
    e("-" * 72 + "\n")
    _codes = fetch_codes(skey)
    _extra = SECTOR_LTCODES.get(skey) or []
    e(f" FETCH {len(_codes)} LTCODES (also on stdout, comma-joined)"
      + (f" - includes {len(_extra)} {skey} sector-module codes" if _extra else "") + ":\n")
    e(f"   Cash flow ({len(CF)}): {', '.join(CF)}\n")
    e(f"   Balance sheet ({len(BS)}): {', '.join(BS)}\n")
    e(f"   Income stmt ({len(IS)}): {', '.join(IS)}\n")
    e("=" * 72 + "\n")


if __name__ == "__main__":
    main()
