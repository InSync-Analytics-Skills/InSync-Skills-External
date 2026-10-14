#!/usr/bin/env python3
"""
ltcodes.py - the single, authoritative capital-allocation LTCODE map for this skill.

Both fetch_plan.py and compute_capalloc.py import from here so the fetch list and the
math layer can never drift apart. Codes were verified live against the InSync MCP
(search_lineitem, filter='standard') on 2026-07-22. Do NOT invent codes; if a company
lacks a code the compute layer degrades that line to "n/a" rather than fabricating it.
"""

CF = {
    "CF_P_108": "Operating Cash Flow (OCF)",
    "CF_P_110": "Free Cash Flow (FCF)",
    "CF_P_109": "Capital Expenditure",
    "CF_P_101": "Share Repurchase ($)",
    "CF_P_102": "Share Repurchase (#)",
    "CF_P_103": "Average Repurchase Price",
    "CF_P_104": "Dividends Paid ($)",
    "CF_P_107": "Dividend Payout Ratio (%)",
    "CF_P_129": "Acquisitions (CFI)",
    "CF_P_130": "Purchases of Investments (CFI)",
    "CF_P_111": "Net Cash from Investing (CFI)",
    "CF_P_112": "Net Cash from Financing (CFF)",
    "CF_P_133": "Proceeds from Issuance of Debt (CFF)",
    "CF_P_106": "Change in Working Capital",
    "CF_P_135": "Net Change in Cash",
}

BS = {
    "BS_P_141": "Cash",
    "BS_P_101": "Cash & Cash Equivalents",
    "BS_P_114": "Total Debt (ex-op lease)",
    "BS_P_115": "Net Debt (ex-op lease)",
    "BS_P_118": "Marketable Securities / Short-Term Investments",
    "BS_P_108": "Total Debt (incl-op lease)",
    "BS_P_109": "Net Debt (incl-op lease)",
    "BS_P_107": "Long-Term Debt",
    "BS_P_106": "Short-Term Debt",
    "BS_P_113": "Total Equity",
    "BS_P_103": "Total Assets",
    "BS_P_127": "Intangible Assets, Net",
    "BS_P_126": "Goodwill, Net",
}

IS = {
    "IS_P_101": "Revenue",
    "IS_P_109": "EBIT",
    "IS_P_113": "EBITDA",
    "IS_P_114": "EBITDA (Adj)",
    "IS_P_123": "Net Income",
    "IS_P_124": "Net Income (Adj)",
    "IS_P_127": "EPS (GAAP)",
    "IS_P_128": "EPS (Adj)",
    "IS_P_125": "Diluted Shares (FDS)",
    "IS_P_250": "Basic Shares",
    "IS_P_141": "Interest Expense",
    "IS_P_121": "Effective Tax Rate (%)",
    "IS_P_135": "R&D",
    "IS_P_111": "D&A",
    "IS_P_151": "Stock-Based Comp",
}

ENERGY_VARIANT_MARKERS = {
    "KM_P_1220": "Total production (MMBoe) - upstream marker",
    "KM_P_1205": "Total average daily production - upstream marker",
    "KM_S_199":  "Total refinery throughput - refining marker",
    "KM_S_1103": "Refinery throughput / gasoline output - refining marker",
    "KM_S_1059": "Crude oil capacity utilisation - refining marker",
    "KM_S_1091": "Realised refining margins - refining marker",
    "SD_S_132":  "Downstream revenue - refining marker",
    "CF_S_126":  "Distributable cash flow - midstream marker",
    "CF_S_122":  "Maintenance capital expenditure - midstream marker",
}

try:
    import sector_router as _SR
    for _c in (list(_SR._PRODUCTION_CODES) + list(_SR._REFINING_CODES)
               + list(_SR._MIDSTREAM_CODES)):
        ENERGY_VARIANT_MARKERS.setdefault(
            _c, "Energy variant marker (from sector_router) - %s" % _c)
except Exception as _e:
    raise ImportError(
        "ltcodes derives the energy fetch markers from sector_router and could not import it "
        "(%s). The fetch plan would be missing routing markers, so this fails rather than "
        "emitting a short list." % _e)

COMPANY_FALLBACKS = {
    "CF_S_1783": "Acquisition consideration - issuer-specific code, and issuers reporting no CF_P_129",
    "CF_S_151": "Businesses acquired - issuer-specific code, and issuers reporting no CF_P_129",
    "CF_S_240": "Acquisitions of businesses, net of cash acquired - issuer-specific code, and issuers "
                "reporting no CF_P_129",
}

ACQUISITION_LTCODES = ["CF_P_129", "CF_S_1783", "CF_S_151", "CF_S_240"]


def acquisition_codes(extra=None):
    out = list(ACQUISITION_LTCODES)
    if not extra:
        return out
    if isinstance(extra, str):
        extra = [extra]
    for code in extra:
        code = str(code).strip().upper()
        if not code:
            continue
        if not code.startswith("CF_"):
            raise ValueError(
                "acquisition code %r is not a cash-flow code. Acquisition consideration is a "
                "CF_ line; a BS_ or IS_ code here would put a balance or a margin into the cash "
                "allocation bridge. Re-run search_lineitem and take the CF_S_* row." % code)
        if code not in out:
            out.append(code)
    return out

FETCH_LTCODES = (list(CF.keys()) + list(BS.keys()) + list(IS.keys())
                 + [c for c in ENERGY_VARIANT_MARKERS if c not in CF and c not in BS and c not in IS]
                 + [c for c in COMPANY_FALLBACKS if c not in CF and c not in BS and c not in IS])

SECTOR_LTCODES = {
    "Banks": ["BS_P_287",
              "BS_S_1000",
              "BS_P_289",
              "BS_P_276",
              "BS_P_278",
              "BS_P_309",
              "BS_P_256",
              "RT_P_107",
              "RT_P_102"],
    "Insurance": ["SD_P_103",
                  "SD_P_108",
                  "SD_S_1051", "SD_S_1052", "SD_S_1042", "SD_S_1031",
                  "IS_S_825", "IS_S_439",
                  "BS_P_256", "RT_P_102"],
    "REIT": ["CF_P_113",
             "CF_P_114",
             "CF_P_202",
             "CF_P_203",
             "CF_P_206"],
}
SECTOR_NAMES = {
    "SD_P_108": "Accident Year Combined Ratio", "SD_S_1051": "Fixed Annuity",
    "SD_S_1052": "Variable Annuity", "SD_S_1042": "Annuities",
    "SD_S_1031": "Annuities - Policyholder Account Balances",
    "IS_S_825": "Policyholder Dividends", "IS_S_439": "Policyholder Liability Remeasurement",
    "BS_P_287": "CET 1 Capital Ratio", "BS_P_289": "Risk Weighted Assets",
    "BS_P_276": "CET 1 Capital", "BS_P_278": "Tier 1 Capital Ratio",
    "BS_P_309": "Tangible Common Equity", "BS_P_256": "BVPS - Tangible",
    "RT_P_107": "ROTCE", "RT_P_102": "ROE", "SD_P_103": "Combined Ratio",
    "CF_P_113": "FFO", "CF_P_114": "AFFO", "CF_P_202": "FFO/Share",
    "CF_P_203": "AFFO/Share", "CF_P_206": "AFFO Payout Ratio",
}


def fetch_codes(sector_key=None):
    out = list(FETCH_LTCODES)
    blocks = ([SECTOR_LTCODES[sector_key]] if sector_key in SECTOR_LTCODES
              else list(SECTOR_LTCODES.values()))
    for blk in blocks:
        for c in blk:
            if c not in out:
                out.append(c)
    return out

NAMES = {**CF, **BS, **IS, **ENERGY_VARIANT_MARKERS, **COMPANY_FALLBACKS, **SECTOR_NAMES}
