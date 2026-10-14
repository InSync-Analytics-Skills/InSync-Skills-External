"""
sector_router.py - fail-closed sector template selection.  (v3 architecture, step 1)

WHY THIS MODULE EXISTS
----------------------
A report must never carry another sector's fields - for example an insurer's report showing
    "Property acquisitions and development", "Memo: AFFO per share change",
    "Maintenance capex", "CET1 ratio"
which happens when one sector's path falls through to another's wording, or when a shared
financial-sector branch inserts bank rows for insurers too.

Two defences here:
  1. Routing is ORDERED and FAILS CLOSED.  An unrecognised sector produces no report at all,
     rather than a generic template dressed in the wrong sector's vocabulary.
  2. Every template declares forbidden_fields.  guard_output() scans the rendered text and a hit
     is a hard build failure.  That check catches every leaked field.

DESIGN: 9 base engines + a variant layer, not 20 bespoke templates.
A variant overrides metric selection, thresholds and vocabulary - never structure.  Twenty parallel
engines would be exactly the surface that lets one sector's fields leak into another.

ENERGY ROUTING
--------------
The feed does NOT distinguish energy sub-models.  Verified live: an E&P, an integrated major,
a refiner and a midstream operator all return
    industry="Oil and Gas"  subindustry="Oil and Gas Producers"
All four are identical, so a label-based router cannot pick E&P vs Integrated vs Refining vs
Midstream.  We therefore route on OBSERVED REPORTING BEHAVIOUR - which line items the company
actually reports - which is deterministic, auditable, and printed in the report so the reader can
check the choice.  An explicit analyst override always wins.
"""

from __future__ import annotations

import re

BLOCK_MESSAGE = "Sector-specific capital-allocation template could not be validated."

_WORD_CACHE = {}


def _contains(haystack, term):
    rx = _WORD_CACHE.get(term)
    if rx is None:
        rx = _WORD_CACHE[term] = re.compile(r"(?<![a-z0-9])%s(?![a-z0-9])"
                                            % re.escape(term).replace(r"\ ", r"\s+"))
    return bool(rx.search(haystack))


class RoutingError(RuntimeError):
    pass


class TemplateLeakageError(RuntimeError):
    pass



_T = dict

TEMPLATES = {

    "BANKS": _T(
        engine="FINANCIAL", label="Banks",
        leverage=("CET1_RATIO", "LQ", "reported"),
        required=["ROE", "TOTAL_SHAREHOLDER_PAYOUT_RATIO"],
        requires_any=["BS_P_287", "BS_S_1000", "BS_P_289", "BS_P_278"],
        requires_label=("a regulatory-capital line - CET 1 capital ratio, standardized "
                        "(BS_P_287) or advanced (BS_S_1000), risk-weighted assets (BS_P_289) "
                        "or tier 1 capital ratio (BS_P_278). CET 1 capital alone (BS_P_276) is "
                        "not sufficient: it yields neither the ratio nor a denominator"),
        forbidden=["affo", "ffo per share", "capex intensity", "property acquisitions",
                   "property development", "maintenance capex", "combined ratio", "loss ratio",
                   "reinvestment rate"],
    ),
    "INSURANCE_PC": _T(
        engine="INSURANCE", label="P&C Insurance",
        leverage=("DEBT_TO_TOTAL_CAPITAL", "LQ", "reported"),
        required=["ROE", "DEBT_TO_TOTAL_CAPITAL", "TOTAL_SHAREHOLDER_PAYOUT_RATIO"],
        forbidden=["cet1", "rwa", "risk-weighted", "affo", "ffo per share",
                   "property acquisitions", "property development", "maintenance capex",
                   "same-store", "occupancy", "tangible book value per share",
                   "capex intensity", "reinvestment rate"],
    ),
    "INSURANCE_LIFE": _T(
        engine="INSURANCE", variant="life", label="Life Insurance",
        leverage=("DEBT_TO_TOTAL_CAPITAL", "LQ", "reported"),
        required=["ROE", "DEBT_TO_TOTAL_CAPITAL"],
        forbidden=["cet1", "rwa", "affo", "ffo per share", "property development",
                   "maintenance capex", "combined ratio", "capex intensity"],
    ),
    "INSURANCE_BROKER": _T(
        engine="ASSET_LIGHT_SERVICES", label="Insurance Brokers",
        leverage=("NET_DEBT_TO_EBITDA", "LFY", "adjusted"),
        required=["ROIC", "FCF"],
        forbidden=["cet1", "combined ratio", "loss ratio", "affo", "property development"],
    ),
    "FINANCIAL_SERVICES": _T(
        engine="ASSET_LIGHT_SERVICES", variant="financial_services",
        label="Asset Managers, Brokers and Specialty Finance",
        leverage=("NET_DEBT_TO_EBITDA", "LFY", "adjusted"),
        required=["ROIC", "FCF"],
        forbidden=["cet1", "rwa", "risk-weighted", "affo", "ffo per share", "combined ratio",
                   "loss ratio", "underwriting income", "property development",
                   "reserve replacement"],
    ),

    "REAL_ESTATE_OPERATING": _T(
        engine="REIT", variant="operating", label="Real Estate Owners and Developers",
        leverage=("NET_DEBT_TO_EBITDA", "LFY", "adjusted"),
        required=["NET_DEBT_TO_EBITDA"],
        forbidden=["cet1", "rwa", "affo", "ffo per share", "combined ratio", "loss ratio",
                   "underwriting income", "reserve replacement"],
    ),
    "REAL_ESTATE_SERVICES": _T(
        engine="ASSET_LIGHT_SERVICES", label="Real Estate Services",
        leverage=("NET_DEBT_TO_EBITDA", "LFY", "adjusted"),
        required=["ROIC", "FCF"],
        forbidden=["cet1", "rwa", "affo", "ffo per share", "property acquisitions",
                   "property development", "maintenance capex", "same-store", "occupancy",
                   "combined ratio", "reserve replacement"],
    ),

    "REIT": _T(
        engine="REIT", label="REITs",
        leverage=("NET_DEBT_TO_EBITDA", "LFY", "adjusted"),
        required=["CAPITAL_RETURNED"],
        forbidden=["cet1", "rwa", "combined ratio", "loss ratio", "underwriting income"],
    ),

    "HEALTHCARE_SERVICES": _T(
        engine="HEALTHCARE_SERVICES", label="Managed Care and Healthcare Services",
        leverage=("NET_DEBT_TO_EBITDA", "LFY", "adjusted"),
        required=["ROIC", "FCF", "NET_DEBT_TO_EBITDA"],
        forbidden=["cet1", "affo", "combined ratio", "property development",
                   "maintenance capex", "reserve replacement"],
    ),
    "PHARMA": _T(
        engine="R_AND_D_LED", label="Pharmaceuticals and Biotechnology",
        leverage=("NET_DEBT_TO_EBITDA", "LFY", "adjusted"),
        required=["ROIC", "FCF"],
        forbidden=["cet1", "affo", "combined ratio", "property development", "refinery throughput", "crude throughput"],
    ),

    "MEDTECH": _T(
        engine="R_AND_D_LED", variant="medtech", label="Medical Devices and Life Sciences",
        leverage=("NET_DEBT_TO_EBITDA", "LFY", "adjusted"),
        required=["ROIC", "FCF"],
        forbidden=["cet1", "affo", "combined ratio", "property development",
                   "refinery throughput", "crude throughput", "reserve replacement"],
    ),

    "INDUSTRIALS": _T(
        engine="INDUSTRIAL", label="Industrials",
        leverage=("NET_DEBT_TO_EBITDA", "LFY", "adjusted"),
        required=["ROIC", "FCF", "NET_DEBT_TO_EBITDA", "CAPEX_INTENSITY"],
        forbidden=["cet1", "affo", "combined ratio", "loss ratio", "reserve replacement",
                   "refinery throughput", "crude throughput", "distributable cash flow"],
    ),
    "TRANSPORT": _T(
        engine="INDUSTRIAL", variant="transport", label="Transportation",
        leverage=("NET_DEBT_TO_EBITDA", "LFY", "adjusted"),
        required=["ROIC", "FCF", "CAPEX_INTENSITY"],
        forbidden=["cet1", "affo", "combined ratio", "reserve replacement"],
    ),
    "MATERIALS": _T(
        engine="INDUSTRIAL", variant="materials", label="Materials, Chemicals and Mining",
        leverage=("NET_DEBT_TO_EBITDA", "LFY", "adjusted"),
        required=["ROIC", "FCF", "NET_DEBT_TO_EBITDA", "CAPEX_INTENSITY"],
        forbidden=["cet1", "affo", "combined ratio", "loss ratio", "reserve replacement",
                   "refinery throughput", "crude throughput", "distributable cash flow"],
    ),
    "CONSUMER": _T(
        engine="CONSUMER", label="Consumer and Retail",
        leverage=("NET_DEBT_TO_EBITDA", "LFY", "adjusted"),
        required=["ROIC", "FCF"],
        forbidden=["cet1", "affo", "combined ratio", "reserve replacement", "refinery throughput", "crude throughput"],
    ),

    "SOFTWARE": _T(
        engine="ASSET_LIGHT_RD", label="Software and Technology",
        leverage=("NET_DEBT_TO_EBITDA", "LFY", "adjusted"),
        required=["ROIC", "FCF"],
        forbidden=["cet1", "affo", "combined ratio", "reserve replacement", "refinery throughput", "crude throughput",
                   "property development", "maintenance capex"],
    ),
    "SEMIS": _T(
        engine="ASSET_LIGHT_RD", variant="semis", label="Semiconductors",
        leverage=("NET_DEBT_TO_EBITDA", "LFY", "adjusted"),
        required=["ROIC", "FCF", "CAPEX_INTENSITY"],
        forbidden=["cet1", "affo", "combined ratio", "reserve replacement"],
    ),

    "TECH_HARDWARE": _T(
        engine="ASSET_LIGHT_RD", variant="hardware", label="Technology Hardware",
        leverage=("NET_DEBT_TO_EBITDA", "LFY", "adjusted"),
        required=["ROIC", "FCF"],
        forbidden=["cet1", "affo", "combined ratio", "reserve replacement",
                   "property development", "maintenance capex"],
    ),
    "MEDIA": _T(
        engine="ASSET_LIGHT_RD", variant="media", label="Media and Entertainment",
        leverage=("NET_DEBT_TO_EBITDA", "LFY", "adjusted"),
        required=["FCF"],
        forbidden=["cet1", "affo", "combined ratio", "reserve replacement",
                   "refinery throughput", "crude throughput", "property development"],
    ),

    "UTILITIES": _T(
        engine="RATE_BASE", label="Electric and Gas Utilities",
        leverage=("NET_DEBT_TO_EBITDA", "LFY", "adjusted"),
        required=["NET_DEBT_TO_EBITDA", "CAPEX_INTENSITY"],
        forbidden=["cet1", "affo", "combined ratio", "reserve replacement", "refinery throughput", "crude throughput"],
    ),
    "TELECOM": _T(
        engine="NETWORK_CAPEX", label="Telecommunications",
        leverage=("NET_DEBT_TO_EBITDA", "LFY", "adjusted"),
        required=["NET_DEBT_TO_EBITDA", "CAPEX_INTENSITY", "FCF"],
        forbidden=["cet1", "affo", "combined ratio", "reserve replacement", "refinery throughput", "crude throughput"],
    ),

    "RENEWABLES": _T(
        engine="INDUSTRIAL", variant="renewables", label="Renewable Energy",
        leverage=("NET_DEBT_TO_EBITDA", "LFY", "adjusted"),
        required=["FCF", "CAPEX_INTENSITY"],
        forbidden=["cet1", "affo", "combined ratio", "reserve replacement",
                   "refinery throughput", "crude throughput", "distributable cash flow",
                   "finding and development"],
    ),
    "ENERGY_SERVICES": _T(
        engine="INDUSTRIAL", variant="oilfield_services", label="Energy Services and Equipment",
        leverage=("NET_DEBT_TO_EBITDA", "LFY", "adjusted"),
        required=["ROIC", "FCF"],
        forbidden=["cet1", "affo", "combined ratio", "reserve replacement",
                   "finding and development", "refinery throughput", "crude throughput",
                   "distributable cash flow", "distribution coverage", "capture rate"],
    ),
    "ENERGY_UPSTREAM": _T(
        engine="ENERGY", variant="upstream", label="Exploration and Production",
        leverage=("NET_DEBT_TO_EBITDA", "LFY", "adjusted"),
        required=["NET_DEBT_TO_EBITDA", "FCF", "REINVESTMENT_RATE"],
        forbidden=["cet1", "affo", "combined ratio", "distributable cash flow",
                   "distribution coverage", "refinery utilisation", "refinery utilization",
                   "crude throughput", "capture rate", "refinery turnaround", "planned turnaround"],
    ),
    "ENERGY_INTEGRATED": _T(
        engine="ENERGY", variant="integrated", label="Integrated Oil and Gas",
        leverage=("NET_DEBT_TO_EBITDA", "LFY", "adjusted"),
        required=["NET_DEBT_TO_EBITDA", "FCF"],
        forbidden=["cet1", "affo", "combined ratio", "distributable cash flow",
                   "distribution coverage", "capture rate"],
    ),
    "ENERGY_DOWNSTREAM": _T(
        engine="ENERGY", variant="downstream", label="Refining and Marketing",
        leverage=("NET_DEBT_TO_EBITDA", "LFY", "adjusted"),
        required=["NET_DEBT_TO_EBITDA", "FCF"],
        forbidden=["cet1", "affo", "combined ratio", "distributable cash flow",
                   "distribution coverage", "reserve replacement", "capture rate",
                   "finding and development"],
    ),
    "ENERGY_MIDSTREAM": _T(
        engine="ENERGY", variant="midstream", label="Midstream and Pipelines",
        leverage=("NET_DEBT_TO_EBITDA", "LFY", "adjusted"),
        required=["NET_DEBT_TO_EBITDA"],
        forbidden=["cet1", "combined ratio", "reserve replacement", "finding and development",
                   "refinery utilisation", "refinery utilization", "crude throughput",
                   "capture rate", "contracted capacity"],
    ),
}

GLOBAL_FORBIDDEN_UNLESS_DECLARED = {
    "cet1": {"BANKS"},
    "affo": {"REIT"},
    "combined ratio": {"INSURANCE_PC", "INSURANCE_LIFE"},
    "reserve replacement": {"ENERGY_UPSTREAM", "ENERGY_INTEGRATED"},
    "distributable cash flow": {"ENERGY_MIDSTREAM"},
    "maintenance capex": {"REIT", "ENERGY_MIDSTREAM", "ENERGY_DOWNSTREAM",
                          "ENERGY_INTEGRATED", "INDUSTRIALS", "UTILITIES", "TRANSPORT",
                          "TELECOM"},
    "refinery throughput": {"ENERGY_DOWNSTREAM", "ENERGY_INTEGRATED"},
    "crude throughput": {"ENERGY_DOWNSTREAM", "ENERGY_INTEGRATED"},
    "refinery turnaround": {"ENERGY_DOWNSTREAM", "ENERGY_INTEGRATED"},
    "planned turnaround": {"ENERGY_DOWNSTREAM", "ENERGY_INTEGRATED"},
}



_BY_SUBINDUSTRY = {
    "banks": "BANKS",
    "banking": "BANKS",
    "specialty finance": "FINANCIAL_SERVICES",
    "asset management": "FINANCIAL_SERVICES",
    "institutional financial services": "FINANCIAL_SERVICES",
    "insurance": "INSURANCE_PC",
    "insurance brokers": "INSURANCE_BROKER",
    "life insurance": "INSURANCE_LIFE",
    "reit": "REIT",
    "reits": "REIT",
    "real estate investment trusts": "REIT",
    "real estate owners and developers": "REAL_ESTATE_OPERATING",
    "real estate services": "REAL_ESTATE_SERVICES",
    "managed care": "HEALTHCARE_SERVICES",
    "healthcare services": "HEALTHCARE_SERVICES",
    "health care facilities and services": "HEALTHCARE_SERVICES",
    "pharmaceuticals": "PHARMA",
    "biotechnology": "PHARMA",
    "biotech and pharma": "PHARMA",
    "medical equipment and devices": "MEDTECH",
    "industrials": "INDUSTRIALS",
    "industrial products": "INDUSTRIALS",
    "industrial services": "INDUSTRIALS",
    "machinery": "INDUSTRIALS",
    "industrial gases": "INDUSTRIALS",
    "electrical equipment": "INDUSTRIALS",
    "diversified industrials": "INDUSTRIALS",
    "transportation equipment": "INDUSTRIALS",
    "industrial intermediate prod": "INDUSTRIALS",
    "engineering and construction": "INDUSTRIALS",
    "commercial support services": "INDUSTRIALS",
    "industrial support services": "INDUSTRIALS",
    "aerospace and defense": "INDUSTRIALS",
    "chemicals": "MATERIALS",
    "metals and mining": "MATERIALS",
    "steel": "MATERIALS",
    "construction materials": "MATERIALS",
    "containers and packaging": "MATERIALS",
    "materials": "MATERIALS",
    "transportation": "TRANSPORT",
    "transportation and logistics": "TRANSPORT",
    "railroads": "TRANSPORT",
    "airlines": "TRANSPORT",
    "software": "SOFTWARE",
    "software and services": "SOFTWARE",
    "software and tech services": "SOFTWARE",
    "technology services": "SOFTWARE",
    "technology": "SOFTWARE",
    "internet media and services": "SOFTWARE",
    "semiconductors": "SEMIS",
    "technology hardware": "TECH_HARDWARE",
    "entertainment content": "MEDIA",
    "cable and satellite": "MEDIA",
    "publishing and broadcasting": "MEDIA",
    "advertising and marketing": "MEDIA",
    "consumer staples": "CONSUMER",
    "consumer discretionary": "CONSUMER",
    "beverages": "CONSUMER",
    "food": "CONSUMER",
    "household products": "CONSUMER",
    "tobacco and cannabis": "CONSUMER",
    "automotive": "CONSUMER",
    "home construction": "CONSUMER",
    "apparel and textile products": "CONSUMER",
    "leisure products": "CONSUMER",
    "leisure facilities and services": "CONSUMER",
    "home and office products": "CONSUMER",
    "retail": "CONSUMER",
    "retail - discretionary": "CONSUMER",
    "retail - consumer staples": "CONSUMER",
    "e-commerce discretionary": "CONSUMER",
    "wholesale - discretionary": "CONSUMER",
    "wholesale - consumer staples": "CONSUMER",
    "consumer services": "CONSUMER",
    "utilities": "UTILITIES",
    "electric utilities": "UTILITIES",
    "gas and water utilities": "UTILITIES",
    "telecommunications": "TELECOM",
    "telecom": "TELECOM",
    "wireless telecommunication services": "TELECOM",
    "renewable energy": "RENEWABLES",
    "oil and gas services and equipment": "ENERGY_SERVICES",
    "oil and gas supply chain": "ENERGY_SERVICES",
}

_ENERGY_SUBINDUSTRIES = {
    "oil and gas", "oil and gas producers", "energy", "oil gas and consumable fuels",
    "oil and gas refining", "oil and gas midstream",
}

_PRODUCTION_CODES = ["KM_P_1220", "KM_P_1205", "KM_S_166", "KM_S_361"]
_REFINING_CODES = ["KM_S_199", "KM_S_1103", "KM_S_1059", "KM_S_1091", "KM_S_1107",
                   "KM_S_879", "KM_S_880", "KM_S_1113", "SD_S_1022", "SD_S_132",
                   "SD_S_255", "SD_S_203", "SD_S_142", "CF_S_253"]
_MIDSTREAM_CODES = ["CF_S_126", "CF_S_122"]

_PC_MARKERS = ["SD_P_103", "SD_P_108"]
_LIFE_MARKERS = ["SD_S_1051", "SD_S_1052", "SD_S_1042", "SD_S_1031",
                 "IS_S_825", "IS_S_439"]



def route(meta, available_ltcodes=None):
    codes = set(available_ltcodes or ())

    _explicit = (meta.get("template") or "").strip()
    _coarse = (meta.get("sector_key") or "").strip()
    if _explicit:
        key = _normalise_override(_explicit)
        if key not in TEMPLATES:
            raise RoutingError(
                "%s Requested template %r is not a validated template. Valid templates: %s"
                % (BLOCK_MESSAGE, _explicit, ", ".join(sorted(TEMPLATES))))
        return _describe(key, "analyst override",
                         "explicitly requested template %r" % _explicit)
    if _coarse and _normalise_override(_coarse) not in TEMPLATES:
        _coarse = ""
    sub = (meta.get("insync_subindustry") or meta.get("classification") or "").strip().lower()
    ind = (meta.get("industry") or "").strip().lower()

    for field, value in (("insync_subindustry", sub), ("industry", ind)):
        if value in _BY_SUBINDUSTRY:
            _key = _BY_SUBINDUSTRY[value]
            if _key == "INSURANCE_PC":
                return _route_insurance(codes, "%s = %r" % (field, value))
            if _key == "FINANCIAL_SERVICES":
                return _route_financial_services(codes, "%s = %r" % (field, value))
            _check_precondition(_key, codes, "%s = %r" % (field, value))
            return _describe(_key, "%s exact match" % field, "%s = %r" % (field, value))

    if sub in _ENERGY_SUBINDUSTRIES or ind in _ENERGY_SUBINDUSTRIES:
        return _route_energy(codes, sub or ind)

    if _coarse:
        _k = _normalise_override(_coarse)
        if (TEMPLATES[_k].get("engine") if isinstance(TEMPLATES[_k], dict)
                else getattr(TEMPLATES[_k], "engine", None)) == "ENERGY":
            return _route_energy(codes, "sector_key=%s" % _coarse)
        _check_precondition(_k, codes, "sector_key=%s" % _coarse)
        return _describe(_k, "sector_key fallback", "sector_key = %r" % _coarse)

    raise RoutingError(
        "%s No template matches subindustry %r or industry %r. Supply an explicit template "
        "override to proceed." % (BLOCK_MESSAGE, sub or "(blank)", ind or "(blank)"))


def _route_energy(codes, source_label):
    if not codes:
        raise RoutingError(
            "%s Energy sub-model cannot be determined because the reported line-item set was not "
            "supplied. The feed classifies E&P, integrated, refining and midstream companies "
            "identically as %r, so routing requires the reported LTCODEs or an explicit override."
            % (BLOCK_MESSAGE, source_label))
    mid = [c for c in _MIDSTREAM_CODES if c in codes]
    prod = [c for c in _PRODUCTION_CODES if c in codes]
    refi = [c for c in _REFINING_CODES if c in codes]
    if mid and not prod and not refi:
        return _describe("ENERGY_MIDSTREAM", "energy line-item routing",
                         "reports %s and no production or refining line"
                         % ", ".join(mid))
    if prod and refi:
        return _describe("ENERGY_INTEGRATED", "energy line-item routing",
                         "reports both production volumes (%s) and refining operations (%s)"
                         % (", ".join(prod), ", ".join(refi)))
    if prod:
        return _describe("ENERGY_UPSTREAM", "energy line-item routing",
                         "reports production volumes (%s) and no refining line" % ", ".join(prod))
    if refi:
        return _describe("ENERGY_DOWNSTREAM", "energy line-item routing",
                         "reports refining operations (%s) and no production line"
                         % ", ".join(refi))
    return _describe("ENERGY_INTEGRATED", "energy fallback",
                     "classifies as %r but reports no production, refining or midstream line, "
                     "so the sub-model could not be observed; the integrated template is used "
                     "as the broadest energy model" % source_label)


def _route_insurance(codes, source_label):
    if not codes:
        return _describe("INSURANCE_PC", "insurance default",
                         "no reported line items were supplied (%s), so the P&C convention is "
                         "assumed; supply the reported LTCODEs or an explicit template override "
                         "to distinguish a life company or a broker" % source_label)
    pc = [c for c in _PC_MARKERS if c in codes]
    life = [c for c in _LIFE_MARKERS if c in codes]
    if pc:
        return _describe("INSURANCE_PC", "insurance line-item routing",
                         "reports an underwriting result (%s)" % ", ".join(pc))
    if life:
        return _describe("INSURANCE_LIFE", "insurance line-item routing",
                         "reports policyholder and annuity balances (%s) and no combined ratio"
                         % ", ".join(life))
    return _describe("INSURANCE_BROKER", "insurance line-item routing",
                     "reports neither an underwriting result nor policyholder balances, which is "
                     "the reporting shape of a fee-earning intermediary rather than a carrier")


def _route_financial_services(codes, source_label):
    need = TEMPLATES["BANKS"]["requires_any"]
    reg = [c for c in need if c in codes]
    if reg:
        return _describe("BANKS", "financial line-item routing",
                         "%s and reports a regulatory-capital line (%s)"
                         % (source_label, ", ".join(reg)))
    return _describe("FINANCIAL_SERVICES", "financial line-item routing",
                     "%s and reports no regulatory-capital line (none of %s), the reporting "
                     "shape of a fee business or non-bank lender rather than a bank"
                     % (source_label, ", ".join(need)))


def _normalise_override(s):
    s = s.strip().upper().replace(" ", "_").replace("-", "_").replace("&", "")
    aliases = {
        "INSURANCE": "INSURANCE_PC", "PC_INSURANCE": "INSURANCE_PC",
        "PANDC": "INSURANCE_PC", "LIFE": "INSURANCE_LIFE",
        "BANK": "BANKS", "ENERGY": "ENERGY_INTEGRATED",
        "EP": "ENERGY_UPSTREAM", "UPSTREAM": "ENERGY_UPSTREAM",
        "REFINING": "ENERGY_DOWNSTREAM", "MIDSTREAM": "ENERGY_MIDSTREAM",
        "HEALTHCARE": "HEALTHCARE_SERVICES", "MANAGED_CARE": "HEALTHCARE_SERVICES",
        "TECH": "SOFTWARE", "INDUSTRIAL": "INDUSTRIALS",
    }
    return aliases.get(s, s)


def _check_precondition(key, codes, basis):
    t = TEMPLATES[key]
    need = t.get("requires_any")
    if not need:
        return
    if codes and any(c in codes for c in need):
        return
    raise RoutingError(
        "%s %s routing requires %s, and none of %s was returned for this company%s. A company "
        "that does not report it cannot be described by this template. Supply an explicit "
        "template override if the assignment is correct and the line item is simply absent."
        % (BLOCK_MESSAGE, t["label"], t.get("requires_label") or "a required line item",
           ", ".join(need),
           " (routing basis: %s)" % basis if basis else ""))


def _describe(key, basis, evidence):
    t = TEMPLATES[key]
    return {
        "template": key,
        "engine": t["engine"],
        "variant": t.get("variant"),
        "label": t["label"],
        "basis": basis,
        "evidence": evidence,
        "required": list(t["required"]),
        "forbidden": list(t["forbidden"]),
        "leverage": t["leverage"],
    }



def guard_output(text, routing, *, raise_on_hit=True):
    low = " ".join(str(text).lower().split())
    hits = []

    for term in routing.get("forbidden", ()):
        if _contains(low, term):
            hits.append({"term": term, "scope": "template",
                         "template": routing["template"]})

    for term, allowed in GLOBAL_FORBIDDEN_UNLESS_DECLARED.items():
        if _contains(low, term) and routing["template"] not in allowed:
            if not any(h["term"] == term for h in hits):
                hits.append({"term": term, "scope": "global",
                             "allowed_in": sorted(allowed)})

    if hits and raise_on_hit:
        detail = "; ".join(
            "%r (forbidden for template %s)" % (h["term"], routing["template"]) for h in hits)
        raise TemplateLeakageError(
            "Template leakage detected - report not saved. %s. "
            "A sector report must not contain another sector's vocabulary." % detail)
    return hits


def template_summary():
    return [{"template": k, "engine": v["engine"], "variant": v.get("variant") or "-",
             "label": v["label"], "required": ", ".join(v["required"]),
             "leverage": v["leverage"][0]}
            for k, v in sorted(TEMPLATES.items())]
